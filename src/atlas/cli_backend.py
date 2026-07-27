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
class SpanUsage:
    """Backend-agnostic usage for one dispatch, ready for plumb.

    The runner converts each backend's own usage dataclass into this shape so
    ``StageOutcome`` — and therefore ``orchestrator``/``loop`` — never has to
    know which engine produced a span. ``tokens`` is the reduced ``(in, out)``
    pair plumb's ``record_span`` expects; ``attributes`` is the raw per-engine
    breakdown persisted alongside it so a reduction rule that later proves
    wrong is recomputable rather than silently corrupt (L1 code review M1).

    ``dollar_cost`` is ``None`` for engines that report no cost figure (Codex
    never does — see ``CodexUsageStats``). Only Claude populates it.
    """

    tokens: tuple[int, int]
    attributes: dict[str, object]
    dollar_cost: float | None


@runtime_checkable
class UsageReporting(Protocol):
    """Optional capability: a backend that can report usage for a dispatch.

    Deliberately a *second* Protocol rather than a fourth ``CliBackend``
    method (L0 Resolved Decision #1 keeps ``CliBackend`` at three members, and
    ``AntigravityBackend`` genuinely has no usage to report). The runner does
    an ``isinstance`` check, so adding usage to a backend is additive and
    mypy-checkable rather than a ``hasattr`` probe.
    """

    def span_usage(self, stdout: str) -> SpanUsage | None: ...


@dataclass(frozen=True)
class UsageStats:
    """Cost/token telemetry parsed from a `claude -p --output-format json` envelope.

    VERIFIED against Claude Code 2.1.220 (2026-07-26) — see
    ``_claude_result_event`` for the envelope shape, which is NOT what this
    class was originally designed against.

    The four token fields are **disjoint** in Anthropic's convention:
    ``input_tokens`` counts only uncached input, with cache hits/writes
    reported separately. Summing them is what ``claude_usage_to_tokens``
    does; reading ``input_tokens`` alone undercounts a warm-cache run by
    orders of magnitude (a real captured run reported ``input_tokens=2``
    against ``cache_read_input_tokens=19589``).
    """

    total_cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None


def _looks_like_json_envelope(stdout: str) -> bool:
    """True for both envelope shapes `claude -p --output-format json` may emit.

    Claude Code 2.1.220 emits a JSON **array** of stream events terminated by
    a ``type: "result"`` element, despite ``--help`` still describing the mode
    as "single result". Testing only for ``{`` (as this did before 2026-07-26)
    silently routed every real envelope into the plain-text branch, which is
    why no live run ever produced telemetry. Both shapes are accepted so a
    revert to a bare object on some future version keeps working.
    """
    return stdout.lstrip().startswith(("{", "["))


def _is_parseable_json(stdout: str) -> bool:
    try:
        json.loads(stdout)
    except json.JSONDecodeError:
        return False
    return True


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _claude_result_event(stdout: str) -> dict[str, object] | None:
    """Return the terminal ``result`` object from a Claude JSON envelope.

    Handles both observed shapes:

    * **array** (VERIFIED, Claude Code 2.1.220) — a list of stream events
      (``system``/``assistant``/``rate_limit_event``/...) whose last
      ``type: "result"`` element carries ``subtype``, ``result``,
      ``total_cost_usd`` and ``usage``. The *last* such element wins, mirroring
      ``CodexBackend.parse_usage``'s handling of ``turn.completed``.
    * **bare object** — the shape ``--help`` documents and this module
      originally assumed. Kept so the parser is not version-locked.

    Returns ``None`` for unparseable JSON or an array with no ``result``
    element; callers distinguish those two cases themselves.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None

    if isinstance(payload, dict):
        return payload

    if isinstance(payload, list):
        found: dict[str, object] | None = None
        for event in payload:
            if isinstance(event, dict) and event.get("type") == "result":
                found = event
        return found

    return None


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
        if not _is_parseable_json(stdout):
            return ("failure", stdout, "claude_unparseable_json")

        event = _claude_result_event(stdout)
        if event is None:
            # Well-formed JSON, but no terminal `result` element — a truncated
            # or interrupted stream. Mirrors CodexBackend's turn.completed
            # presence check rather than reporting a phantom success.
            return ("failure", stdout, "claude_no_result_event")

        subtype = event.get("subtype")
        result_text = event.get("result")
        if subtype != "success":
            return ("failure", str(result_text) if result_text else stdout, f"claude_{subtype}")
        return ("success", str(result_text) if result_text else "", None)

    def parse_usage(self, stdout: str) -> UsageStats | None:
        """Extract cost/token telemetry from a JSON-envelope stdout.

        Returns None for plain-text stdout (attended mode never calls this
        in practice, since it never requests telemetry). Not a CliBackend
        Protocol member — see Resolved Decision #1 in the Phase L0 TRS.
        """
        if not _looks_like_json_envelope(stdout):
            return None

        event = _claude_result_event(stdout)
        if event is None:
            return None

        raw_usage = event.get("usage")
        usage: dict[str, object] = raw_usage if isinstance(raw_usage, dict) else {}

        logger.debug(
            "claude usage: input_tokens=%r cache_creation_input_tokens=%r "
            "cache_read_input_tokens=%r output_tokens=%r total_cost_usd=%r",
            usage.get("input_tokens"),
            usage.get("cache_creation_input_tokens"),
            usage.get("cache_read_input_tokens"),
            usage.get("output_tokens"),
            event.get("total_cost_usd"),
        )

        return UsageStats(
            total_cost_usd=_as_float(event.get("total_cost_usd")),
            input_tokens=_as_int(usage.get("input_tokens")),
            output_tokens=_as_int(usage.get("output_tokens")),
            cache_creation_input_tokens=_as_int(usage.get("cache_creation_input_tokens")),
            cache_read_input_tokens=_as_int(usage.get("cache_read_input_tokens")),
        )

    def span_usage(self, stdout: str) -> SpanUsage | None:
        usage = self.parse_usage(stdout)
        if usage is None:
            return None
        return SpanUsage(
            tokens=claude_usage_to_tokens(usage),
            attributes=claude_usage_attributes(usage),
            dollar_cost=usage.total_cost_usd,
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

    def span_usage(self, stdout: str) -> SpanUsage | None:
        usage = self.parse_usage(stdout)
        if usage is None:
            return None
        return SpanUsage(
            tokens=codex_usage_to_tokens(usage),
            attributes=codex_usage_attributes(usage),
            # Structurally always None — the Codex CLI reports no cost figure
            # (VERIFIED, 0.144.4). v3 compares engines on tokens, not dollars.
            dollar_cost=None,
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
CLAUDE_TOKEN_REDUCTION_RULE = "cache_fields_disjoint_addends_v1"


def claude_usage_to_tokens(usage: UsageStats) -> tuple[int, int]:
    """Reduce UsageStats to the (in, out) tuple plumb's record_span expects.

    Unlike Codex's equivalent, this rule is **verified, not assumed**:
    Anthropic's usage fields are disjoint, so total billed input is
    ``input_tokens + cache_creation_input_tokens + cache_read_input_tokens``.
    A real captured run (2026-07-26, Claude Code 2.1.220) reported
    ``input_tokens=2`` alongside ``cache_read_input_tokens=19589`` — taking
    ``input_tokens`` alone, as this module's original design did, would have
    recorded 2 tokens for a ~31.5k-token dispatch.

    Same recoverability guarantee as Codex: ``claude_usage_attributes()``
    persists the raw breakdown plus ``CLAUDE_TOKEN_REDUCTION_RULE`` into
    ``spans.attributes``. Change the rule here and bump the constant — do not
    edit stored spans.
    """
    in_tokens = (
        (usage.input_tokens or 0)
        + (usage.cache_creation_input_tokens or 0)
        + (usage.cache_read_input_tokens or 0)
    )
    return (in_tokens, usage.output_tokens or 0)


def claude_usage_attributes(usage: UsageStats) -> dict[str, object]:
    """Raw Claude token breakdown for durable storage in ``spans.attributes``.

    Mirrors ``codex_usage_attributes``. Includes ``total_cost_usd`` because
    plumb stores dollars only at run level — the per-stage split is otherwise
    unrecoverable once several spans roll up into one run total.
    """
    return {
        "engine": "claude",
        "token_reduction_rule": CLAUDE_TOKEN_REDUCTION_RULE,
        "input_tokens": usage.input_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "output_tokens": usage.output_tokens,
        "total_cost_usd": usage.total_cost_usd,
    }


CODEX_TOKEN_REDUCTION_RULE = "openai_subset_fields_v2"


def codex_usage_to_tokens(usage: CodexUsageStats) -> tuple[int, int]:
    """Reduce CodexUsageStats to the (in, out) tuple plumb's record_span expects.

    **Pending Decision #4 is RESOLVED (T-L1.1, 2026-07-26): the sub-fields are
    subsets, not addends.** The prior rule
    (``cached_input_as_addend_v1``) assumed Anthropic's convention and was
    wrong — it inflated every Codex span's input by ~70-90%.

    Measured directly with a cold/warm capture pair on codex-cli 0.144.4,
    same prompt and directory, run back to back::

        run A (colder):  input_tokens=68719  cached_input_tokens=48384
        run B (warmer):  input_tokens=69161  cached_input_tokens=62464

    ``input_tokens`` held flat (+0.6%) while ``cached_input_tokens`` rose 29%.
    Under the addend model ``input_tokens`` had to fall by ~14k as more of the
    prompt became cacheable; it did not. ``input_tokens`` is therefore the
    whole prompt and ``cached_input_tokens`` is the served-from-cache portion
    of it — matching OpenAI's documented convention
    (``prompt_tokens_details.cached_tokens`` ⊆ ``prompt_tokens``), which is
    the opposite of Anthropic's.

    ``reasoning_output_tokens`` is treated as a subset of ``output_tokens`` on
    the same OpenAI convention (``completion_tokens_details.reasoning_tokens``
    ⊆ ``completion_tokens``). That one is **convention plus consistency with
    the measured cached result, not an independent measurement** — a run with
    ``output_tokens=206``/``reasoning=50`` against a ~46-token visible message
    fits either model arithmetically, since tool-call arguments are also
    billed output. Flagged rather than overclaimed.

    The raw four-field breakdown and this rule's name are persisted to
    ``spans.attributes`` by ``codex_usage_attributes()``, so spans written
    under the old v1 rule stay recomputable. Change the rule here and bump
    the constant — do not edit stored spans.
    """
    return (usage.input_tokens or 0, usage.output_tokens or 0)


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
    override: str | None = None,
) -> str:
    """5-tier backend resolution (TRD-v2 §3.4, plus an explicit override tier).

    1. ``override`` — an explicit, run-scoped human instruction: ``atlas run
       --backend X`` or a loop issue's ``engine:X`` label
    2. Per-stage StageSpec.backend
    3. Workflow LoadedWorkflow.default_backend
    4. Config.default_backend (from .atlas.toml [backend] default)
    5. Hard default 'claude'

    **Why override sits above the workflow default (added 2026-07-26).**
    Originally an override was folded into tier 4, so any workflow declaring
    `default_backend:` silently beat it. Every shipped loop workflow declares
    one — `loop_dev.yaml` says `default_backend: claude` — which made two
    surfaces inert without any error:

    * ``atlas run --backend codex --workflow loop_dev`` ran claude. Confirmed
      live on 2026-07-26: the run's spans came back stamped ``engine: claude``.
    * The loop daemon's ``engine:*`` issue label, which `run_one_shot` passes
      as `backend_override`, could **never** take effect — so TRD-v3 §13 #3
      ("a `loop_dev` run under `engine:codex`") was unreachable by design.

    A silently-discarded explicit instruction is the worst of the options
    here: worse than overriding a YAML default the operator can see and edit.
    """
    if override is not None:
        return override
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
