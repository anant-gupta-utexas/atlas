"""Unit tests for the Pipeline state machine (Phase 1 + 2 coverage)."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.orchestrator import (
    GateDecision,
    NoActiveRunError,
    Pipeline,
    RunContext,
    StageOutcome,
)
from atlas.plumb_io import PlumbIO
from atlas.stages import StageSpec
from atlas.state import StateInconsistencyError, StateStore
from atlas.workflow_loader import load_workflow_file

_DEV_YAML_PATH = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"
STAGES = load_workflow_file(_DEV_YAML_PATH).stages

# ---------------------------------------------------------------------------
# Fakes / stubs
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Returns canned outcomes in order. Defaults to success."""

    def __init__(self, outcomes: list[StageOutcome] | None = None) -> None:
        self._outcomes = list(outcomes or [])
        self._idx = 0

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        if self._outcomes and self._idx < len(self._outcomes):
            outcome = self._outcomes[self._idx]
            self._idx += 1
            return outcome
        # Default: synthetic success outcome (plumb_io will assign real span_id)
        return StageOutcome(
            stage=stage,
            span_id="",
            status="success",
            output_text=f"output of {stage.name}",
            error_type=None,
        )


class _FakePrompter:
    """Returns scripted decisions in order. Defaults to approve."""

    def __init__(self, decisions: list[GateDecision] | None = None) -> None:
        self._decisions = list(decisions or [])
        self._idx = 0

    def ask(self, *, stage: StageSpec, gate_index: int) -> GateDecision:
        if self._decisions and self._idx < len(self._decisions):
            d = self._decisions[self._idx]
            self._idx += 1
            return d
        return GateDecision(label="approved", turn_count=1, reason=None)


def _make_pipeline(
    tmp_path: Path,
    *,
    runner: _FakeRunner | None = None,
    prompter: _FakePrompter | None = None,
    commit_wait_timeout_s: int = 0,
) -> tuple[Pipeline, PlumbIO, StateStore]:
    plumb = PlumbIO(real=False)
    state = StateStore(tmp_path)
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=runner or _FakeRunner(),
        prompter=prompter or _FakePrompter(),
        commit_wait_timeout_s=commit_wait_timeout_s,
    )
    return pipeline, plumb, state


# ---------------------------------------------------------------------------
# T1.4 — Basic walking
# ---------------------------------------------------------------------------


def test_one_start_plus_seven_steps_walks_full_pipeline(tmp_path):
    pipeline, plumb, state = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="do a thing", slug="test-task")

    completed = []
    for _ in range(7):
        outcome = pipeline.step(ctx)
        assert outcome is not None
        completed.append(outcome.stage.name)

    assert len(completed) == 7
    # All stages visited in order
    assert completed == [s.name for s in STAGES]

    # Next step should return None (done)
    assert pipeline.step(ctx) is None


def test_step_returns_none_when_all_stages_complete(tmp_path):
    pipeline, _, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="test", slug="slug")

    for _ in range(7):
        pipeline.step(ctx)

    assert pipeline.step(ctx) is None


def test_gate_rejection_closes_run_on_first_stage(tmp_path):
    reject_decision = GateDecision(label="rejected", turn_count=1, reason="not good")
    prompter = _FakePrompter(decisions=[reject_decision])
    pipeline, plumb, _ = _make_pipeline(tmp_path, prompter=prompter)

    ctx = pipeline.start(task="test task", slug="slug")
    outcome = pipeline.step(ctx)  # research → gate → rejected

    assert outcome is not None
    assert outcome.status == "rejected"


def test_gate_rejection_writes_examples_row(tmp_path):
    reject_decision = GateDecision(label="rejected", turn_count=2, reason="bad output")
    prompter = _FakePrompter(decisions=[reject_decision])
    pipeline, plumb, _ = _make_pipeline(tmp_path, prompter=prompter)

    ctx = pipeline.start(task="my task", slug="slug")
    pipeline.step(ctx)

    assert len(plumb.examples) == 1
    ex = plumb.examples[0]
    assert ex["expected_output_hash"] is None
    assert len(ex["inputs_hash"]) == 64  # SHA-256 hex


def test_rejection_inputs_hash_is_sha256_of_task(tmp_path):
    import hashlib

    task = "add cache middleware"
    reject_decision = GateDecision(label="rejected", turn_count=1, reason=None)
    prompter = _FakePrompter(decisions=[reject_decision])
    pipeline, plumb, _ = _make_pipeline(tmp_path, prompter=prompter)

    ctx = pipeline.start(task=task, slug="slug")
    pipeline.step(ctx)

    expected_hash = hashlib.sha256(task.encode()).hexdigest()
    assert plumb.examples[0]["inputs_hash"] == expected_hash


# ---------------------------------------------------------------------------
# T1.4 — Gate-approve produces spans + scores
# ---------------------------------------------------------------------------


def test_step_advance_on_approve_writes_user_signal_score(tmp_path):
    pipeline, plumb, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="task", slug="slug")

    outcome = pipeline.step(ctx)  # stage 0: research + gate approved
    assert outcome is not None
    assert outcome.status == "success"
    assert len(plumb.scores) == 1
    assert plumb.scores[0]["value_label"] == "approved"
    assert plumb.scores[0]["metric"] == "gate_research"


def test_step_writes_one_span_per_stage(tmp_path):
    pipeline, plumb, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="task", slug="slug")

    for _ in range(7):
        pipeline.step(ctx)

    assert len(plumb.spans) == 7


# ---------------------------------------------------------------------------
# T1.4 — Stage 3 has no gate
# ---------------------------------------------------------------------------


def test_stage_3_does_not_write_score(tmp_path):
    pipeline, plumb, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="task", slug="slug")

    for i in range(4):  # stages 0-3
        scores_before_step = list(plumb.scores)
        outcome = pipeline.step(ctx)
        assert outcome is not None
        if i == 3:  # tds_gen — no gate
            assert len(plumb.scores) == len(scores_before_step), "Stage 3 should not write a score"


# ---------------------------------------------------------------------------
# T1.4 — Stage 5 returns awaiting_hook
# ---------------------------------------------------------------------------


def test_stage_5_returns_awaiting_hook(tmp_path):
    pipeline, plumb, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="task", slug="slug")

    for _ in range(5):  # stages 0-4
        pipeline.step(ctx)

    outcome = pipeline.step(ctx)  # stage 5: code_gen
    assert outcome is not None
    assert outcome.status == "awaiting_hook"


def test_stage_5_does_not_write_gate_commit_score(tmp_path):
    pipeline, plumb, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="task", slug="slug")

    for _ in range(5):
        pipeline.step(ctx)

    scores_before = list(plumb.scores)
    pipeline.step(ctx)  # code_gen — no user-signal score

    new_scores = plumb.scores[len(scores_before) :]
    gate_commit_scores = [s for s in new_scores if s.get("metric") == "gate_commit"]
    assert len(gate_commit_scores) == 0


# ---------------------------------------------------------------------------
# T1.4 — Plugin failure halts run
# ---------------------------------------------------------------------------


def test_plugin_nonzero_exit_halts_run(tmp_path):
    stage = STAGES[0]
    fail_outcome = StageOutcome(
        stage=stage,
        span_id="",
        status="failure",
        output_text="",
        error_type="plugin_nonzero_exit",
    )
    runner = _FakeRunner(outcomes=[fail_outcome])
    pipeline, plumb, _ = _make_pipeline(tmp_path, runner=runner)

    ctx = pipeline.start(task="task", slug="slug")
    outcome = pipeline.step(ctx)

    assert outcome is not None
    assert outcome.status == "failure"
    assert outcome.error_type == "plugin_nonzero_exit"


# ---------------------------------------------------------------------------
# T1.4 — Resume after simulated restart
# ---------------------------------------------------------------------------


def test_resume_finds_first_unchecked_box(tmp_path):
    # Phase 1: run stages 0 and 1, then "restart"
    pipeline, plumb, state = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="my task", slug="my-task")
    pipeline.step(ctx)  # research → approved
    pipeline.step(ctx)  # prd_draft → approved

    # Simulate fresh process: build a new Pipeline instance
    plumb2 = PlumbIO(real=False)
    pipeline2 = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb2,
        runner=_FakeRunner(),
        prompter=_FakePrompter(),
    )

    ctx2 = pipeline2.resume()
    assert ctx2.run_id == ctx.run_id

    next_unchecked = state.first_unchecked(ctx2)
    assert next_unchecked == "trd_draft"


def test_resume_raises_when_no_active_run(tmp_path):
    pipeline, _, _ = _make_pipeline(tmp_path)
    with pytest.raises(NoActiveRunError):
        pipeline.resume()


# ---------------------------------------------------------------------------
# T1.4 — State inconsistency is refused
# ---------------------------------------------------------------------------


def test_state_inconsistency_refuses_step(tmp_path):
    pipeline, plumb, state = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="task", slug="slug")

    # Corrupt .atlas/current-run to a different run_id
    state.write_current_run("deadbeef" * 4, ctx.slug)

    with pytest.raises(StateInconsistencyError):
        pipeline.step(ctx)


# ---------------------------------------------------------------------------
# T1.4 — Idempotent after run close
# ---------------------------------------------------------------------------


def test_step_after_completion_is_idempotent(tmp_path):
    pipeline, plumb, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="task", slug="slug")

    for _ in range(7):
        pipeline.step(ctx)

    # After all 7 stages, step returns None and does not write anything extra
    spans_before = len(plumb.spans)
    result = pipeline.step(ctx)
    assert result is None
    assert len(plumb.spans) == spans_before


# ---------------------------------------------------------------------------
# T1.4 — run_to_completion happy path
# ---------------------------------------------------------------------------


def test_run_to_completion_happy_path(tmp_path):
    pipeline, plumb, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="task", slug="slug")

    # run_to_completion will stop at awaiting_hook (stage 5)
    returned_ctx = pipeline.run_to_completion(ctx)
    assert returned_ctx.run_id == ctx.run_id

    # 5 spans for stages 0-4 (stage 5 is awaiting_hook)
    # Actually: stages 0-4 run (5 spans), stage 5 returns awaiting_hook
    span_count = len(plumb.spans)
    assert span_count == 6  # stages 0-5


def test_run_to_completion_on_rejection_closes_with_failure(tmp_path):
    reject_decision = GateDecision(label="rejected", turn_count=1, reason="bad")
    prompter = _FakePrompter(decisions=[reject_decision])
    pipeline, plumb, _ = _make_pipeline(tmp_path, prompter=prompter)

    ctx = pipeline.start(task="task", slug="slug")
    pipeline.run_to_completion(ctx)

    # Examples row written on rejection
    assert len(plumb.examples) == 1
