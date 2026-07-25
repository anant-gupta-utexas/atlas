"""Per-CLI argv construction and result-parsing strategies for SubprocessStageRunner.

Trust boundary: backends never call subprocess themselves — they only build
argv lists and parse already-captured stdout/stderr/returncode.
SubprocessStageRunner owns the actual subprocess.run() call.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from atlas.stages import StageSpec
from atlas.workflow_loader import LoadedWorkflow

logger = logging.getLogger(__name__)

_KNOWN_BACKENDS: frozenset[str] = frozenset({"claude", "agy", "codex"})


@dataclass(frozen=True)
class UsageStats:
    """Cost/token telemetry parsed from a `claude -p --output-format json` envelope.

    total_cost_usd is surfaced in-memory only — plumb has no per-span or
    run-level sink reachable from the online run path in v1.0.1 (see
    docs/1_product_and_research/BACKLOG.md, plumb P1-a). input_tokens /
    output_tokens are threaded to PlumbIO.record_span(tokens=(in, out)).
    """

    total_cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None


def _looks_like_json_envelope(stdout: str) -> bool:
    return stdout.lstrip().startswith("{")


@runtime_checkable
class CliBackend(Protocol):
    name: str

    def build_argv(
        self,
        *,
        prompt: str,
        model: str,
        add_dirs: list[Path],
        timeout_s: int,
        extra_flags: dict[str, str],
    ) -> list[str]: ...

    def parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> tuple[str, str, str | None]:
        """Return (status, output_text, error_type).

        status: 'success' | 'failure'
        output_text: text to surface at the next gate (may be empty)
        error_type: None on success; an error_type string otherwise
        """
        ...

    def preflight(self) -> tuple[str, str | None] | None:
        """Optional pre-dispatch env-var check.

        Returns None if preflight passes; otherwise (error_message, error_type)
        for SubprocessStageRunner to surface as a failure StageOutcome.
        """
        ...


class ClaudeCodeBackend:
    name = "claude"

    def build_argv(
        self,
        *,
        prompt: str,
        model: str,
        add_dirs: list[Path],
        timeout_s: int,
        extra_flags: dict[str, str],
    ) -> list[str]:
        argv: list[str] = [
            "claude",
            "-p",
            prompt,
            "--no-session-persistence",
            "--model",
            model,
        ]
        for d in add_dirs:
            argv.extend(["--add-dir", str(d)])

        # Loop-mode-only flags — attended callers never set these keys, so
        # argv above stays byte-identical to today's output when extra_flags
        # is empty (test_claude_code_backend_argv_byte_identical_to_phase2).
        if extra_flags.get("telemetry") == "json":
            argv += ["--output-format", "json"]
        if extra_flags.get("permission_mode"):
            argv += ["--permission-mode", extra_flags["permission_mode"]]
        if extra_flags.get("allowed_tools"):
            argv += ["--allowedTools", extra_flags["allowed_tools"]]
        if extra_flags.get("max_turns"):
            argv += ["--max-turns", extra_flags["max_turns"]]

        return argv

    def parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> tuple[str, str, str | None]:
        if returncode != 0:
            return ("failure", stdout, "plugin_nonzero_exit")

        # Plain-text branch (attended, default) — unchanged.
        if not _looks_like_json_envelope(stdout):
            return ("success", stdout, None)

        # JSON branch (loop-mode `--output-format json`).
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return ("failure", stdout, "claude_unparseable_json")

        subtype = payload.get("subtype")
        if subtype != "success":
            return ("failure", payload.get("result") or stdout, f"claude_{subtype}")
        return ("success", payload.get("result") or "", None)

    def parse_usage(self, stdout: str) -> UsageStats | None:
        """Extract cost/token telemetry from a JSON-envelope stdout.

        Returns None for plain-text stdout (attended mode never calls this
        in practice, since it never requests telemetry). Not a CliBackend
        Protocol member — see Resolved Decision #1 in the Phase L0 TRS.
        """
        if not _looks_like_json_envelope(stdout):
            return None
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return None

        usage = payload.get("usage") or {}
        return UsageStats(
            total_cost_usd=payload.get("total_cost_usd"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    def preflight(self) -> tuple[str, str | None] | None:
        return None  # claude -p handles its own auth at subprocess level


class AntigravityBackend:
    name = "agy"

    def __init__(self, *, default_model: str = "gemini-flash-lite") -> None:
        self._default_model = default_model

    def build_argv(
        self,
        *,
        prompt: str,
        model: str,
        add_dirs: list[Path],
        timeout_s: int,
        extra_flags: dict[str, str],
    ) -> list[str]:
        effective_model = model if model else self._default_model
        argv: list[str] = [
            "agy",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            effective_model,
        ]
        for d in add_dirs:
            argv.extend(["--include-directories", str(d)])
        return argv

    def parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> tuple[str, str, str | None]:
        # agy documented exit codes: 0=success, 1=general, 42=input error, 53=turn limit
        if returncode == 42:
            return ("failure", stdout or stderr, "agy_input_error")
        if returncode == 53:
            return ("failure", stdout or stderr, "agy_turn_limit")
        if returncode != 0:
            return ("failure", stdout or stderr, "agy_general_error")

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return ("failure", stdout, "agy_unparseable_output")

        if payload.get("error"):
            err = payload["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return ("failure", msg, "agy_response_error")

        response = payload.get("response", "")
        if not isinstance(response, str):
            return ("failure", stdout, "agy_response_not_string")
        return ("success", response, None)

    def preflight(self) -> tuple[str, str | None] | None:
        # TRD-v2 §4 Security: validate key before any subprocess — do NOT fall
        # back to browser OAuth (documented failure mode on headless SSH sessions).
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            return (
                "Antigravity (agy) requires GEMINI_API_KEY or GOOGLE_API_KEY in the "
                "environment for headless dispatch. Browser OAuth fallback is "
                "intentionally disabled. See docs/3_guides/cli_backends.md.",
                "agy_missing_auth_env",
            )
        return None


@dataclass(frozen=True)
class CodexUsageStats:
    """Token telemetry parsed from a `codex exec --json` turn.completed event.

    VERIFIED against codex-cli 0.144.4. Note the asymmetry with Claude's
    UsageStats: Codex reports NO dollar figure at all, so there is deliberately
    no ``total_cost_usd`` field here (L1 code review finding N1 — a field that
    is structurally always None invites ``if usage.total_cost_usd:`` downstream,
    and the call-site symmetry it bought was notional: no caller consumes
    UsageStats and CodexUsageStats polymorphically). Engine A/B comparison in
    v3 is tokens-only — Resolved Decision #10. Codex additionally reports
    cached_input_tokens and reasoning_output_tokens, which Claude's envelope
    does not carry in the same shape — captured here because reasoning tokens
    are billable output on reasoning models and dropping them would
    understate usage.
    """

    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_output_tokens: int | None


class CodexBackend:
    name = "codex"

    def build_argv(
        self,
        *,
        prompt: str,
        model: str,
        add_dirs: list[Path],
        timeout_s: int,
        extra_flags: dict[str, str],
    ) -> list[str]:
        # SubprocessStageRunner seeds add_dirs as [repo_root] or
        # [repo_root, worktree_path] (orchestrator.py) — the worktree, when
        # present, is always last. -C/--cd sets the single working root;
        # --add-dir keeps every other directory writable alongside it, so
        # repo_root (e.g. dev/active/<slug>/tasks.md) stays reachable even
        # when code_gen isolates into a worktree.
        primary = add_dirs[-1] if add_dirs else Path.cwd()
        argv: list[str] = [
            "codex",
            "exec",
            prompt,
            "--json",
            "-C",
            str(primary),
            "--sandbox",
            "workspace-write",
        ]
        if model:
            argv += ["--model", model]
        for d in add_dirs:
            if d != primary:
                argv += ["--add-dir", str(d)]
        return argv

    def parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> tuple[str, str, str | None]:
        # The JSONL stream carries no status field — status is exit-code-only
        # (VERIFIED against codex-cli 0.144.4). See Resolved Decision #8.
        if returncode != 0:
            return ("failure", stdout or stderr, "codex_nonzero_exit")

        events: list[dict[str, object]] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        saw_turn_completed = any(e.get("type") == "turn.completed" for e in events)
        if not saw_turn_completed:
            return ("failure", stdout, "codex_no_turn_completed")

        messages: list[str] = []
        for e in events:
            if e.get("type") != "item.completed":
                continue
            item = e.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") != "agent_message":
                continue
            text = item.get("text")
            if text:
                messages.append(str(text))

        return ("success", "\n".join(messages), None)

    def parse_usage(self, stdout: str) -> CodexUsageStats | None:
        """Extract token telemetry from the terminal turn.completed event.

        Not a CliBackend Protocol member — same additive pattern as
        ClaudeCodeBackend.parse_usage (L0 Resolved Decision #1).
        """
        turn_completed: dict[str, object] | None = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "turn.completed":
                turn_completed = event

        if turn_completed is None:
            return None

        usage = turn_completed.get("usage")
        usage = usage if isinstance(usage, dict) else {}

        logger.debug(
            "codex usage: input_tokens=%r cached_input_tokens=%r "
            "output_tokens=%r reasoning_output_tokens=%r",
            usage.get("input_tokens"),
            usage.get("cached_input_tokens"),
            usage.get("output_tokens"),
            usage.get("reasoning_output_tokens"),
        )

        return CodexUsageStats(
            # No total_cost_usd — the Codex CLI reports no cost figure at all
            # (VERIFIED, 0.144.4). Not a gap to fill later; see the dataclass.
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cached_input_tokens=usage.get("cached_input_tokens"),
            reasoning_output_tokens=usage.get("reasoning_output_tokens"),
        )

    def preflight(self) -> tuple[str, str | None] | None:
        if os.environ.get("OPENAI_API_KEY"):
            return None
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            auth_path = Path(codex_home).expanduser() / "auth.json"
        else:
            auth_path = Path.home() / ".codex" / "auth.json"
        if auth_path.exists():
            return None
        return (
            "Codex (codex exec) requires OPENAI_API_KEY in the environment or a "
            "`codex login` session. See docs/3_guides/cli_backends.md.",
            "codex_missing_auth",
        )


# Bumped whenever the reduction rule below changes, so stored spans record
# which convention produced their `tokens` total (L1 code review finding M1).
CODEX_TOKEN_REDUCTION_RULE = "cached_input_as_addend_v1"


def codex_usage_to_tokens(usage: CodexUsageStats) -> tuple[int, int]:
    """Reduce CodexUsageStats to the (in, out) tuple plumb's record_span expects.

    Per Pending Decision #4, ``cached_input_tokens`` is **assumed** to be an
    addend to ``input_tokens`` rather than a subset of it. This is an
    inference from Anthropic's convention (Claude's ``input_tokens`` excludes
    its own cache fields), **not** a verified fact about OpenAI's schema —
    T-L1.1's cold/warm-cache capture pair is what settles it.

    If the assumption is backwards, every Codex span's input count is inflated
    (~78% on the `success.jsonl` fixture's real figures). That error is
    recoverable rather than silent: ``codex_usage_attributes()`` persists the
    raw four-field breakdown plus ``CODEX_TOKEN_REDUCTION_RULE`` into
    ``spans.attributes``, so history can be recomputed. Change the rule here
    and bump the constant — do not edit stored spans.
    """
    in_tokens = (usage.input_tokens or 0) + (usage.cached_input_tokens or 0)
    out_tokens = (usage.output_tokens or 0) + (usage.reasoning_output_tokens or 0)
    return (in_tokens, out_tokens)


def codex_usage_attributes(usage: CodexUsageStats) -> dict[str, object]:
    """Raw Codex token breakdown for durable storage in ``spans.attributes``.

    plumb collapses ``tokens=(in, out)`` into one summed ``spans.tokens``
    column, so neither the in/out split nor the cached/reasoning breakdown
    survives there. Persisting the raw fields — and the name of the reduction
    rule that produced the total — is what makes an incorrect Pending
    Decision #4 a recomputable error instead of permanently corrupt data
    (L1 code review finding M1).
    """
    return {
        "engine": "codex",
        "token_reduction_rule": CODEX_TOKEN_REDUCTION_RULE,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_output_tokens": usage.reasoning_output_tokens,
    }


def resolve_backend(
    *,
    stage: StageSpec,
    workflow: LoadedWorkflow | None,
    config_default: str | None,
) -> str:
    """4-tier backend resolution per TRD-v2 §3.4.

    1. Per-stage StageSpec.backend (highest priority)
    2. Workflow LoadedWorkflow.default_backend
    3. Config.default_backend (from .atlas.toml [backend] default)
    4. Hard default 'claude'
    """
    if stage.backend is not None:
        return stage.backend
    if workflow is not None and workflow.default_backend is not None:
        return workflow.default_backend
    if config_default is not None:
        return config_default
    return "claude"


class UnknownBackendError(Exception):
    """Raised when make_backend() receives a name not in _KNOWN_BACKENDS."""


def make_backend(name: str) -> CliBackend:
    """Construct a CliBackend by name; raise UnknownBackendError for unknown names."""
    if name == "claude":
        return ClaudeCodeBackend()
    if name == "agy":
        return AntigravityBackend()
    if name == "codex":
        return CodexBackend()
    raise UnknownBackendError(f"Unknown backend {name!r}. Allowed: {sorted(_KNOWN_BACKENDS)}")
