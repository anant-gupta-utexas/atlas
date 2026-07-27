"""Non-dev workflow gate-metric namespacing (covers C1 + I1).

The dev pipeline masks every namespacing bug because ``namespaced_metric("dev",
x) == x``. These tests drive a *synthetic* non-dev workflow through both a
synchronous gate and the asynchronous (commit) gate so that the
``<workflow>.<gate_label>`` namespacing — and its survival across a run-id-
changing resume — is actually exercised.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from atlas.orchestrator import Pipeline, RunContext, StageOutcome
from atlas.plumb_io import PlumbIO
from atlas.stages import StageSpec
from atlas.state import StateStore
from atlas.workflow_loader import load_workflow_file

# A minimal non-dev workflow: one synchronous gate, then an async (commit) gate.
# Stage names are deliberately absent from _DEFAULT_TIMEOUT_S so this also
# exercises the _GLOBAL_FALLBACK_TIMEOUT_S path implicitly.
_JOB_YAML = textwrap.dedent(
    """\
    name: job

    stages:
      - name: score_fit
        span_kind: llm
        tool: RAW:score the fit
        gate: gate_shortlist

      - name: ship_offer
        span_kind: subagent
        tool: RAW:ship the offer
        gate: gate_shipped
        gate_is_async: true
    """
)


def _write_job_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "job.yaml"
    path.write_text(_JOB_YAML)
    return path


class _FakeRunner:
    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        return StageOutcome(
            stage=stage,
            span_id="",
            status="success",
            output_text=f"output of {stage.name}",
            error_type=None,
        )


class _FakePrompter:
    def ask(self, *, stage: StageSpec, gate_index: int, output_text: str = ""):
        from atlas.orchestrator import GateDecision

        return GateDecision(label="approved", turn_count=1, reason=None)


def _make_pipeline(tmp_path: Path, stages, plumb: PlumbIO | None = None):
    plumb = plumb or PlumbIO(real=False)
    state = StateStore(tmp_path)
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=_FakeRunner(),
        prompter=_FakePrompter(),
        stages=stages,
        workflow_name="job",
    )
    return pipeline, plumb, state


def test_sync_gate_metric_is_namespaced_for_non_dev_workflow(tmp_path):
    stages = load_workflow_file(_write_job_yaml(tmp_path)).stages
    pipeline, plumb, _ = _make_pipeline(tmp_path, stages)

    ctx = pipeline.start(task="hire someone", slug="job-run")
    outcome = pipeline.step(ctx)  # score_fit + synchronous gate

    assert outcome is not None and outcome.status == "success"
    assert len(plumb.scores) == 1
    assert plumb.scores[0]["metric"] == "job.gate_shortlist"


def test_async_gate_metric_is_namespaced_in_current_run(tmp_path):
    stages = load_workflow_file(_write_job_yaml(tmp_path)).stages
    pipeline, _, state = _make_pipeline(tmp_path, stages)

    ctx = pipeline.start(task="hire someone", slug="job-run")
    pipeline.step(ctx)  # score_fit (sync gate)
    outcome = pipeline.step(ctx)  # ship_offer (async gate)

    assert outcome is not None and outcome.status == "awaiting_hook"
    # C1: line 5 carries the *namespaced* metric, not the bare label.
    assert state.read_async_gate_metric() == "job.gate_shipped"


def test_resume_preserves_namespaced_async_metric_when_run_id_changes(tmp_path):
    """I1 — a run-id-changing resume must not truncate the async-gate metric."""
    job_yaml = _write_job_yaml(tmp_path)
    stages = load_workflow_file(job_yaml).stages

    # Place the workflow where resolve_workflow() can find it on resume:
    # repo .atlas/workflows/job.yaml is the highest-priority search location.
    workflows_dir = tmp_path / ".atlas" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "job.yaml").write_text(_JOB_YAML)

    pipeline, _, state = _make_pipeline(tmp_path, stages)
    ctx = pipeline.start(task="hire someone", slug="job-run")
    pipeline.step(ctx)  # score_fit
    pipeline.step(ctx)  # ship_offer → async gate, line 5 written
    assert state.read_async_gate_metric() == "job.gate_shipped"

    # Fresh process: a real plumb backend's reopen_run() returns a NEW run id,
    # triggering the current-run rewrite path. Stub a run-id-changing reopen.
    plumb2 = PlumbIO(real=False)
    plumb2.reopen_run = lambda run_id: run_id + "-child"  # type: ignore[method-assign]
    pipeline2 = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb2,
        runner=_FakeRunner(),
        prompter=_FakePrompter(),
        stages=stages,
        workflow_name="job",
    )

    pipeline2.resume()

    # The metric must survive the rewrite — not silently revert to "gate_commit".
    assert state.read_async_gate_metric() == "job.gate_shipped"
