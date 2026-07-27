"""Dispatches each stage to the runner matching its tool-string prefix.

New file rather than living in orchestrator.py: orchestrator.py is already
past the ~500-line split trigger (Phase-2 plan, Resolved Decision #1) by the
time this lands, so CompositeStageRunner gets its own module instead of
growing that file further.
"""

from __future__ import annotations

from atlas.orchestrator import RunContext, StageOutcome, StageRunner
from atlas.stages import StageSpec


class CompositeStageRunner:
    """Dispatches each stage to the runner matching its tool-string prefix.

    Satisfies the StageRunner Protocol; Pipeline is unaware this wrapping
    exists. Falls through to `default` (SubprocessStageRunner) for any tool
    string without a recognized prefix — preserves dev.yaml's plugin-slash-
    command behavior unchanged (those strings have neither a RAW: nor a
    LIB: prefix; plugin_resolver.resolve() still owns them).
    """

    def __init__(
        self,
        *,
        default: StageRunner,
        library: StageRunner | None = None,
        shell: StageRunner | None = None,
    ) -> None:
        self._default = default
        self._library = library
        self._shell = shell

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        if stage.tool.startswith("LIB:"):
            if self._library is None:
                return StageOutcome(
                    stage=stage,
                    span_id="",
                    status="failure",
                    output_text="",
                    error_type="library_runner_unavailable",
                )
            return self._library.run(ctx=ctx, stage=stage)
        if stage.tool.startswith("SHELL:"):
            if self._shell is None:
                return StageOutcome(
                    stage=stage,
                    span_id="",
                    status="failure",
                    output_text="",
                    error_type="shell_runner_unavailable",
                )
            return self._shell.run(ctx=ctx, stage=stage)
        return self._default.run(ctx=ctx, stage=stage)  # RAW: and plugin-command stages
