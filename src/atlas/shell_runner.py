"""Dispatches SHELL:-prefixed stage tools to a direct CLI subprocess.

This is the runner that makes ``job_cli.yaml`` a genuine dependency-free
CLI-dispatch path (review finding #2 / TRS §3.7): ``SHELL:content-pipeline
capture ...`` runs the ``content-pipeline`` executable directly, rather than
routing the string through ``claude -p`` the way ``RAW:`` does.

Trust boundary: a ``SHELL:`` command is executed with ``shell=False`` (list
form, no shell interpolation) and its first token must be in the closed
``_ALLOWED_COMMANDS`` allow-list. A workflow YAML is trusted input, but the
allow-list keeps ``SHELL:`` from becoming an arbitrary-command escape hatch —
it can only ever invoke content-pipeline's CLI.
"""

from __future__ import annotations

import logging
import shlex
import subprocess

from atlas.orchestrator import RunContext, StageOutcome, resolve_timeout
from atlas.stages import StageSpec

logger = logging.getLogger(__name__)

# Closed allow-list: the first token of a SHELL: command must be one of these.
_ALLOWED_COMMANDS: frozenset[str] = frozenset({"content-pipeline"})


class ShellStageRunner:
    """Runs SHELL:<argv> stage tools as a direct, list-form subprocess.

    Never raises: FileNotFoundError (binary absent), TimeoutExpired, and
    non-zero exits all map to StageOutcome(status="failure", ...) so NFR-2
    (no unhandled exceptions from a stage) holds. Honors StageSpec.timeout_s
    via resolve_timeout, unlike LibraryStageRunner's in-process calls.
    """

    def __init__(self, *, timeout_overrides: dict[str, int] | None = None) -> None:
        self._timeout_overrides = timeout_overrides or {}

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        command = stage.tool.removeprefix("SHELL:").strip()
        argv = shlex.split(command)

        if not argv:
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=f"empty SHELL: command in stage {stage.name!r}",
                error_type="shell_command_invalid",
            )

        if argv[0] not in _ALLOWED_COMMANDS:
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=(
                    f"SHELL: command {argv[0]!r} is not in the allow-list "
                    f"{sorted(_ALLOWED_COMMANDS)}"
                ),
                error_type="shell_command_not_allowed",
            )

        timeout_s = resolve_timeout(stage, self._timeout_overrides)
        target_dir = ctx.worktree_path if ctx.worktree_path is not None else ctx.repo_root

        try:
            result = subprocess.run(
                argv,
                cwd=str(target_dir),
                capture_output=True,
                check=False,
                timeout=timeout_s,
                text=True,
                shell=False,
            )
        except FileNotFoundError:
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=(
                    f"{argv[0]!r} not found on PATH. "
                    "Install content-pipeline or use --workflow job (in-process LIB: dispatch)."
                ),
                error_type="shell_command_not_found",
            )
        except subprocess.TimeoutExpired:
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text="",
                error_type="shell_timeout",
            )

        if result.returncode != 0:
            logger.warning(
                "SHELL: stage %s exited %d: %s", stage.name, result.returncode, result.stderr[:200]
            )
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=result.stdout or result.stderr,
                error_type="shell_nonzero_exit",
            )

        return StageOutcome(
            stage=stage,
            span_id="",
            status="success",
            output_text=result.stdout,
            error_type=None,
        )
