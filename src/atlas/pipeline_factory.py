"""Shared Pipeline construction — used by both `atlas run` and the loop daemon.

Lives outside ``cli.py`` so ``loop.py`` doesn't have to import the CLI entry
point to build a ``Pipeline`` (which forced ``cli.py``'s loop commands to
import ``loop`` lazily inside function bodies to dodge a circular import).
The dependency now runs one way: ``cli.py`` -> ``pipeline_factory`` and
``loop.py`` -> ``pipeline_factory``.
"""

from __future__ import annotations

from pathlib import Path

from atlas.composite_runner import CompositeStageRunner
from atlas.config import Config
from atlas.library_runner import LibraryStageRunner
from atlas.orchestrator import (
    AutoPrompter,
    ClickPrompter,
    Pipeline,
    RunContext,
    StageOutcome,
    SubprocessStageRunner,
)
from atlas.plumb_io import PlumbIO
from atlas.shell_runner import ShellStageRunner
from atlas.stages import StageSpec
from atlas.state import StateStore
from atlas.workflow_loader import resolve_workflow
from atlas.worktree import WorktreeManager


class LastOutcomeRunner:
    """Records the last StageOutcome from each step so callers can inspect it.

    Wraps CompositeStageRunner without touching Pipeline — the pipeline
    only sees the StageRunner Protocol.  After run_to_completion() the CLI
    checks ``last.error_type`` to surface actionable error messages.
    """

    def __init__(self, inner: CompositeStageRunner) -> None:
        self._inner = inner
        self.last: StageOutcome | None = None

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        outcome = self._inner.run(ctx=ctx, stage=stage)
        self.last = outcome
        return outcome


def make_pipeline(
    repo_root: Path,
    cfg: Config,
    *,
    auto_approve: bool = False,
    workflow: str | None = None,
    workflow_file: Path | None = None,
    backend_override: str | None = None,
    max_turns: int | None = None,
) -> tuple[Pipeline, LastOutcomeRunner]:
    """Construct a Pipeline + recorder exactly as `atlas run` does.

    Shared by cli.py::run/resume and loop.py (Decision #11) so the two
    construction paths cannot silently drift. ``backend_override``, when
    given, takes priority over ``cfg.default_backend`` (but still below a
    stage's own ``backend`` field or the workflow's ``default_backend`` —
    the existing 4-tier order in `cli_backend.resolve_backend`) — used by
    loop.py to honor an issue's `engine:*` label.

    ``max_turns`` caps agent turns per stage. ``atlas run`` leaves it None
    (a human is watching); the loop daemon passes ``cfg.loop.max_turns`` so
    an unattended run can't spin indefinitely.
    """
    loaded = resolve_workflow(
        workflow_file=workflow_file, workflow_name=workflow, repo_root=repo_root
    )
    plumb = PlumbIO(real=True)
    state = StateStore(repo_root)
    worktree = WorktreeManager(repo_root)
    default_runner = SubprocessStageRunner(
        timeout_overrides=cfg.timeout_overrides,
        command_overrides=cfg.plugin_commands,
        model=cfg.model,
        default_backend=backend_override or cfg.default_backend,
        loaded_workflow=loaded,
        max_turns=max_turns,
    )
    # Construct LibraryStageRunner only when the loaded workflow uses LIB: stages.
    # CompositeStageRunner is always used so dev.yaml's plain plugin-command
    # stages (no LIB:/RAW: prefix) still fall through to SubprocessStageRunner.
    library: LibraryStageRunner | None = None
    if any(s.tool.startswith("LIB:") for s in loaded.stages):
        library = LibraryStageRunner()
    # ShellStageRunner handles SHELL: stages (direct CLI dispatch, e.g. job_cli).
    shell: ShellStageRunner | None = None
    if any(s.tool.startswith("SHELL:") for s in loaded.stages):
        shell = ShellStageRunner(timeout_overrides=cfg.timeout_overrides)
    composite = CompositeStageRunner(default=default_runner, library=library, shell=shell)
    recorder = LastOutcomeRunner(composite)
    prompter: ClickPrompter | AutoPrompter = AutoPrompter() if auto_approve else ClickPrompter()
    pipeline = Pipeline(
        repo_root=repo_root,
        state=state,
        plumb=plumb,
        runner=recorder,
        prompter=prompter,
        stages=loaded.stages,
        workflow_name=loaded.name,
        worktree=worktree,
    )
    return pipeline, recorder


__all__ = ["LastOutcomeRunner", "make_pipeline"]
