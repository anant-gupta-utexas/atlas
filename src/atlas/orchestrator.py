"""7-stage atlas pipeline orchestrator."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from atlas.stages import STAGE_BY_NAME, STAGES, GateLabel, StageName, StageSpec

if TYPE_CHECKING:
    from atlas.plumb_io import PlumbIO
    from atlas.state import StateStore

_ROUTING_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent / "tests" / "fixtures" / "routing_ground_truth.json"
)


class RoutingDriftError(Exception):
    """Raised when STAGES does not match the routing fixture."""


class NoActiveRunError(Exception):
    """Raised by resume() when .atlas/current-run is absent."""


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunContext:
    run_id: str
    slug: str
    task: str
    repo_root: Path
    worktree_path: Path | None = None


@dataclass(frozen=True)
class GateDecision:
    label: str  # "approved" | "rejected"
    turn_count: int
    reason: str | None


@dataclass(frozen=True)
class StageOutcome:
    stage: StageSpec
    span_id: str
    status: str  # "success" | "failure" | "awaiting_hook" | "rejected"
    output_text: str
    error_type: str | None


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class GatePrompter(Protocol):
    def ask(self, *, stage: StageSpec, gate_index: int) -> GateDecision: ...


class StageRunner(Protocol):
    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome: ...


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    def __init__(
        self,
        *,
        repo_root: Path,
        state: "StateStore",
        plumb: "PlumbIO",
        runner: StageRunner,
        prompter: GatePrompter,
    ) -> None:
        self._repo_root = repo_root
        self._state = state
        self._plumb = plumb
        self._runner = runner
        self._prompter = prompter
        self._validate_routing_fixture()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self, *, task: str, slug: str) -> RunContext:
        """
        Create a new run. Writes tasks.md + .atlas/current-run.
        Does NOT execute any stage.
        """
        run_id = self._plumb.open_run(task=task)
        ctx = RunContext(run_id=run_id, slug=slug, task=task, repo_root=self._repo_root)
        self._state.create_tasks_md(ctx)
        self._state.write_current_run(run_id, slug)
        return ctx

    def resume(self) -> RunContext:
        """
        Resume an in-flight run from .atlas/current-run + tasks.md.
        Validates state consistency before returning the RunContext.
        """
        pair = self._state.read_current_run()
        if pair is None:
            raise NoActiveRunError("No active atlas run in this repo.")
        run_id, slug = pair
        tasks_path = self._repo_root / "dev" / "active" / slug / "tasks.md"
        task = _parse_task_from_tasks_md(tasks_path)
        ctx = RunContext(run_id=run_id, slug=slug, task=task, repo_root=self._repo_root)
        self._state.assert_consistent(ctx)
        return ctx

    def step(self, ctx: RunContext) -> StageOutcome | None:
        """
        Execute one stage + its gate.
        Returns StageOutcome, or None if the run is already complete.
        Idempotent if called after run close.
        """
        self._state.assert_consistent(ctx)

        next_name = self._state.first_unchecked(ctx)
        if next_name is None:
            return None

        stage = STAGE_BY_NAME[next_name]
        outcome = self._runner.run(ctx=ctx, stage=stage)

        span_id = self._plumb.record_span(
            run_id=ctx.run_id,
            kind=stage.span_kind,
            name=stage.name.value,
            status=outcome.status if outcome.status != "rejected" else "failure",
            latency_ms=0.0,
            error_type=outcome.error_type,
        )
        outcome = StageOutcome(
            stage=stage,
            span_id=span_id,
            status=outcome.status,
            output_text=outcome.output_text,
            error_type=outcome.error_type,
        )

        self._state.check_box(ctx, stage.name)

        if outcome.status == "failure":
            return outcome

        if stage.gate_label is None:
            # Stage 3 — no gate; advance directly
            next_stage = STAGES[stage.index + 1]
            self._state.update_current_block(
                ctx,
                phase=next_stage.name,
                gate=f"none (entering {next_stage.name.value})",
                next_action=f"run stage {next_stage.index} ({next_stage.name.value})",
            )
            return StageOutcome(
                stage=stage,
                span_id=span_id,
                status="success",
                output_text=outcome.output_text,
                error_type=None,
            )

        if stage.gate_label == GateLabel.GATE_COMMIT:
            # Gate 4 — written by post-commit hook; orchestrator returns awaiting_hook
            return StageOutcome(
                stage=stage,
                span_id=span_id,
                status="awaiting_hook",
                output_text=outcome.output_text,
                error_type=None,
            )

        assert stage.gate_index is not None
        decision = self._prompter.ask(stage=stage, gate_index=stage.gate_index)
        self._plumb.record_user_signal(
            run_id=ctx.run_id,
            span_id=span_id,
            metric=stage.gate_label.value,
            decision=decision,
        )

        if decision.label == "rejected":
            self._plumb.write_example(
                run_id=ctx.run_id,
                span_id=span_id,
                inputs=ctx.task,
                expected=None,
            )
            return StageOutcome(
                stage=stage,
                span_id=span_id,
                status="rejected",
                output_text=outcome.output_text,
                error_type=None,
            )

        # Approved — advance current block
        if stage.index < len(STAGES) - 1:
            next_stage = STAGES[stage.index + 1]
            self._state.update_current_block(
                ctx,
                phase=next_stage.name,
                gate=stage.gate_label.value,
                next_action=f"run stage {next_stage.index} ({next_stage.name.value})",
            )
        else:
            self._state.update_current_block(
                ctx,
                phase=stage.name,
                gate=stage.gate_label.value,
                next_action="run complete",
            )

        return StageOutcome(
            stage=stage,
            span_id=span_id,
            status="success",
            output_text=outcome.output_text,
            error_type=None,
        )

    def run_to_completion(self, ctx: RunContext) -> RunContext:
        """
        Loop: step() until all 7 stages done OR a gate rejects OR a stage fails.
        """
        while True:
            outcome = self.step(ctx)
            if outcome is None:
                self._plumb.close_run(run_id=ctx.run_id, status="success")
                self._state.delete_current_run()
                return ctx
            if outcome.status in ("failure", "rejected"):
                self._plumb.close_run(run_id=ctx.run_id, status="failure")
                self._state.delete_current_run()
                return ctx
            if outcome.status == "awaiting_hook":
                return ctx

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_routing_fixture(self) -> None:
        if not _ROUTING_FIXTURE_PATH.exists():
            raise RoutingDriftError(
                f"Routing fixture not found: {_ROUTING_FIXTURE_PATH}"
            )
        rows = json.loads(_ROUTING_FIXTURE_PATH.read_text())
        if len(rows) != len(STAGES):
            raise RoutingDriftError(
                f"Fixture has {len(rows)} rows; STAGES has {len(STAGES)}"
            )
        for spec, row in zip(STAGES, rows, strict=True):
            if (
                spec.tool != row["expected_tool"]
                or spec.span_kind != row["expected_span_kind"]
                or spec.name.value != row["stage_name"]
            ):
                raise RoutingDriftError(
                    f"Stage {spec.index} drifted from fixture: {spec} vs {row}"
                )


def _parse_task_from_tasks_md(path: Path) -> str:
    """Extract the original task description from tasks.md filename comment."""
    content = path.read_text()
    for line in content.splitlines():
        if line.startswith("# tasks —"):
            return line[len("# tasks —"):].strip()
    return path.parent.name
