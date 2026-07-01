"""Per-CLI argv construction and result-parsing strategies for SubprocessStageRunner.

Trust boundary: backends never call subprocess themselves — they only build
argv lists and parse already-captured stdout/stderr/returncode.
SubprocessStageRunner owns the actual subprocess.run() call.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from atlas.stages import StageSpec
from atlas.workflow_loader import LoadedWorkflow

_KNOWN_BACKENDS: frozenset[str] = frozenset({"claude", "agy"})


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
        return argv

    def parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> tuple[str, str, str | None]:
        if returncode != 0:
            return ("failure", stdout, "plugin_nonzero_exit")
        return ("success", stdout, None)

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
    raise UnknownBackendError(f"Unknown backend {name!r}. Allowed: {sorted(_KNOWN_BACKENDS)}")
