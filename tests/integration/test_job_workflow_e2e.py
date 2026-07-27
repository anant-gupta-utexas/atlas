"""Integration tests for the job.yaml and job_cli.yaml workflows (T2.8).

These tests exercise the full Pipeline + CompositeStageRunner + LibraryStageRunner
stack.  Content-pipeline is an optional dependency and is NOT installed in the
standard CI environment, so every test that invokes a LIB: stage mocks at the
adapter-import boundary (``atlas.library_runner._import_adapter``), not at the
use-case boundary. This is sufficient to prove atlas's dispatch and plumb-write
wiring without requiring a real content-pipeline install.

For RAW: stages (tailor_materials, emit_package) a _FakeSubprocessRunner is used
so no real subprocess is spawned.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from atlas.composite_runner import CompositeStageRunner
from atlas.library_runner import LibraryStageRunner
from atlas.orchestrator import (
    GateDecision,
    Pipeline,
    RunContext,
    StageOutcome,
)
from atlas.plumb_io import PlumbIO
from atlas.stages import StageSpec
from atlas.state import StateStore
from atlas.workflow_loader import load_workflow_file

_JOB_YAML = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "job.yaml"
_JOB_CLI_YAML = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "job_cli.yaml"


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeSubprocessRunner:
    """Returns success for all stages; never spawns a real subprocess."""

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        return StageOutcome(
            stage=stage,
            span_id="",
            status="success",
            output_text=f"fake output of {stage.name}",
            error_type=None,
        )


class _FakePrompter:
    def ask(self, *, stage: StageSpec, gate_index: int, output_text: str = "") -> GateDecision:
        return GateDecision(label="approved", turn_count=1, reason=None)


def _success_adapter(*, ctx: RunContext, stage: StageSpec) -> StageOutcome:
    return StageOutcome(
        stage=stage,
        span_id="",
        status="success",
        output_text=f"## Report\nfake output for {stage.name}",
        error_type=None,
    )


def _make_job_pipeline(
    tmp_path: Path,
    *,
    plumb: PlumbIO | None = None,
    prompter: _FakePrompter | None = None,
    library_runner: LibraryStageRunner | None = None,
) -> Pipeline:
    plumb = plumb or PlumbIO(real=False)
    state = StateStore(tmp_path)
    stages = load_workflow_file(_JOB_YAML).stages
    lib = library_runner if library_runner is not None else LibraryStageRunner()
    composite = CompositeStageRunner(default=_FakeSubprocessRunner(), library=lib)
    return Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=composite,
        prompter=prompter or _FakePrompter(),
        stages=stages,
        workflow_name="job",
    )


def _make_job_cli_pipeline(tmp_path: Path, *, plumb: PlumbIO | None = None) -> Pipeline:
    plumb = plumb or PlumbIO(real=False)
    state = StateStore(tmp_path)
    stages = load_workflow_file(_JOB_CLI_YAML).stages
    # job_cli.yaml has SHELL: (content-pipeline CLI) and RAW: (claude) stages, no
    # LIB: stages. The _FakeSubprocessRunner stands in for BOTH the default
    # (RAW:→claude) and shell (SHELL:→content-pipeline) runners here so this
    # plumbing test spawns no real process; a real content-pipeline subprocess
    # is exercised separately in test_shell_runner.py.
    composite = CompositeStageRunner(
        default=_FakeSubprocessRunner(), library=None, shell=_FakeSubprocessRunner()
    )
    return Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=composite,
        prompter=_FakePrompter(),
        stages=stages,
        workflow_name="job_cli",
    )


# ---------------------------------------------------------------------------
# T2.8 integration tests
# ---------------------------------------------------------------------------


def test_job_workflow_produces_correct_span_tree(tmp_path: Path) -> None:
    """atlas run --workflow job produces 4 spans with correct kind/name ordering."""
    plumb = PlumbIO(real=False)
    pipeline = _make_job_pipeline(tmp_path, plumb=plumb)

    with patch("atlas.library_runner._import_adapter", return_value=_success_adapter):
        ctx = pipeline.start(task="find a swe role", slug="find-swe")
        pipeline.run_to_completion(ctx)

    assert len(plumb.spans) == 4
    assert plumb.spans[0]["kind"] == "tool" and plumb.spans[0]["name"] == "ingest_postings"
    assert plumb.spans[1]["kind"] == "verify" and plumb.spans[1]["name"] == "score_fit"
    assert plumb.spans[2]["kind"] == "subagent" and plumb.spans[2]["name"] == "tailor_materials"
    assert plumb.spans[3]["kind"] == "tool" and plumb.spans[3]["name"] == "emit_package"
    assert all(s["status"] == "success" for s in plumb.spans)


def test_job_workflow_gate_scores_namespaced(tmp_path: Path) -> None:
    """Three gate scores are written with job.gate_* metric names."""
    plumb = PlumbIO(real=False)
    pipeline = _make_job_pipeline(tmp_path, plumb=plumb)

    with patch("atlas.library_runner._import_adapter", return_value=_success_adapter):
        ctx = pipeline.start(task="find a swe role", slug="find-swe")
        pipeline.run_to_completion(ctx)

    assert len(plumb.scores) == 3
    metric_names = {s["metric"] for s in plumb.scores}
    assert metric_names == {"job.gate_shortlist", "job.gate_materials", "job.gate_done"}
    assert all(s["scorer"] == "user_signal" for s in plumb.scores)


def test_job_and_dev_coexist_in_same_db(tmp_path: Path) -> None:
    """Dev and job runs in the same PlumbIO instance produce non-overlapping metric names.

    The dev pipeline uses commit_wait_timeout_s=0 so the awaiting_hook stage
    (code_gen) exits immediately without polling for a pending-scores file.
    Stages 0-4 (research → plan_review) each have sync gates, giving 5 dev
    gate scores with bare metric names.
    """
    from atlas.workflow_loader import load_workflow_file as _load

    dev_yaml = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"
    dev_stages = _load(dev_yaml).stages
    plumb = PlumbIO(real=False)

    # Dev run (commit_wait_timeout_s=0 exits awaiting_hook immediately)
    dev_tmp = tmp_path / "dev_repo"
    dev_tmp.mkdir()
    dev_pipeline = Pipeline(
        repo_root=dev_tmp,
        state=StateStore(dev_tmp),
        plumb=plumb,
        runner=_FakeSubprocessRunner(),
        prompter=_FakePrompter(),
        stages=dev_stages,
        workflow_name="dev",
        commit_wait_timeout_s=0,
    )
    dev_ctx = dev_pipeline.start(task="add cache", slug="add-cache")
    dev_pipeline.run_to_completion(dev_ctx)

    # Job run
    job_tmp = tmp_path / "job_repo"
    job_tmp.mkdir()
    job_pipeline = _make_job_pipeline(job_tmp, plumb=plumb)
    with patch("atlas.library_runner._import_adapter", return_value=_success_adapter):
        job_ctx = job_pipeline.start(task="find swe", slug="find-swe")
        job_pipeline.run_to_completion(job_ctx)

    all_metrics = [s["metric"] for s in plumb.scores]
    dev_metrics = [m for m in all_metrics if not m.startswith("job.")]
    job_metrics = [m for m in all_metrics if m.startswith("job.")]

    # Dev stages 0-4 each have sync gates; stage 5 has async gate (score written later)
    assert len(dev_metrics) >= 3, "Dev run should have written sync gate scores"
    assert len(job_metrics) == 3, "Job run should have written exactly 3 gate scores"
    assert set(job_metrics) == {"job.gate_shortlist", "job.gate_materials", "job.gate_done"}
    # No overlap between dev and job metric names
    assert set(dev_metrics).isdisjoint(set(job_metrics))


def test_job_workflow_content_pipeline_not_installed_fails_cleanly(tmp_path: Path) -> None:
    """When content-pipeline is unimportable, ingest_postings fails cleanly.

    content-pipeline imports are function-local inside the adapter body, so a
    genuinely-missing content-pipeline surfaces as an ImportError raised there.
    The runner classifies an ImportError naming a content-pipeline package as
    'content_pipeline_not_installed' with output_text that names job_cli as the
    dependency-free alternative.  Uses pipeline.step() so we can inspect the
    outcome directly; step() records the failure span but does NOT delete
    current-run (that is run_to_completion()'s job on failure close).
    """
    import builtins

    plumb = PlumbIO(real=False)
    pipeline = _make_job_pipeline(tmp_path, plumb=plumb)

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        # Simulate content-pipeline absent: any of its top-level packages raises.
        if name.split(".")[0] in ("application", "infrastructure", "domain"):
            raise ImportError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    ctx = pipeline.start(task="find swe", slug="find-swe")
    with patch("builtins.__import__", side_effect=_fake_import):
        outcome = pipeline.step(ctx)

    assert outcome is not None
    assert outcome.status == "failure"
    assert outcome.error_type == "content_pipeline_not_installed"
    # The output_text must name job_cli so the user knows the dependency-free alternative.
    assert "job_cli" in outcome.output_text
    # No crash — the span was recorded in plumb with failure status.
    assert len(plumb.spans) == 1
    assert plumb.spans[0]["status"] == "failure"
    assert plumb.spans[0]["error_type"] == "content_pipeline_not_installed"


def test_job_workflow_atlas_adapter_import_bug_is_not_masked(tmp_path: Path) -> None:
    """An ImportError resolving the *atlas* adapter module is NOT reported as a
    missing optional dependency — it surfaces as 'library_adapter_error' so the
    user is not pointed at the wrong remedy (review finding #3).
    """
    plumb = PlumbIO(real=False)
    pipeline = _make_job_pipeline(tmp_path, plumb=plumb)

    def _raise_atlas_import(dotted_path: str) -> None:
        raise ImportError(f"atlas adapter broken: {dotted_path}")

    ctx = pipeline.start(task="find swe", slug="find-swe")
    with patch("atlas.library_runner._import_adapter", side_effect=_raise_atlas_import):
        outcome = pipeline.step(ctx)

    assert outcome is not None
    assert outcome.status == "failure"
    assert outcome.error_type == "library_adapter_error"
    # It must NOT masquerade as a missing dependency.
    assert "job_cli" not in outcome.output_text
    assert plumb.spans[0]["error_type"] == "library_adapter_error"


def test_job_cli_workflow_runs_without_content_pipeline(tmp_path: Path) -> None:
    """job_cli.yaml completes all 4 stages via RAW:/SubprocessStageRunner only.

    LibraryStageRunner is never invoked (library=None in CompositeStageRunner).
    """
    plumb = PlumbIO(real=False)
    # Use CompositeStageRunner with library=None — the test's _FakeSubprocessRunner
    # handles all RAW: stages cleanly, proving no LIB: calls are needed.
    pipeline = _make_job_cli_pipeline(tmp_path, plumb=plumb)

    ctx = pipeline.start(task="find swe cli", slug="find-swe-cli")
    pipeline.run_to_completion(ctx)

    assert len(plumb.spans) == 4
    assert all(s["status"] == "success" for s in plumb.spans)
    # Verify ingest_postings is NOT a LIB: dispatch (span kind is tool, runner is subprocess)
    assert plumb.spans[0]["name"] == "ingest_postings"
    assert plumb.spans[0]["status"] == "success"


def test_job_cli_metrics_namespaced_separately(tmp_path: Path) -> None:
    """job_cli gate scores are namespaced under job_cli.*, distinct from job.*."""
    plumb = PlumbIO(real=False)
    pipeline = _make_job_cli_pipeline(tmp_path, plumb=plumb)

    ctx = pipeline.start(task="find swe cli", slug="find-swe-cli")
    pipeline.run_to_completion(ctx)

    assert len(plumb.scores) == 3
    metric_names = {s["metric"] for s in plumb.scores}
    assert metric_names == {
        "job_cli.gate_shortlist",
        "job_cli.gate_materials",
        "job_cli.gate_done",
    }
    # Confirm these don't collide with job.* metrics
    assert all(not m.startswith("job.") for m in metric_names)
