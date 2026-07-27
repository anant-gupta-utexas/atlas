"""Unit tests for atlas.library_runner (LibraryStageRunner dispatch behavior)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from atlas.library_runner import LibraryStageRunner, _import_adapter
from atlas.orchestrator import RunContext, StageOutcome
from atlas.stages import StageSpec

_CTX = RunContext(
    run_id="r1", slug="acme-swe", task="score acme swe role", repo_root=Path("/tmp/repo")
)


def _lib_stage(tool: str = "LIB:content_pipeline.score_jobs") -> StageSpec:
    return StageSpec(
        index=1,
        name="score_fit",
        span_kind="verify",
        tool=tool,
        gate_label="gate_shortlist",
        gate_index=0,
    )


def test_library_runner_unknown_ref() -> None:
    runner = LibraryStageRunner()
    stage = _lib_stage("LIB:not_a_real_ref")

    outcome = runner.run(ctx=_CTX, stage=stage)

    assert outcome.status == "failure"
    assert outcome.error_type == "library_ref_unknown"
    assert outcome.stage is stage


def test_library_runner_content_pipeline_not_installed() -> None:
    """A content-pipeline ImportError raised from inside the adapter body (its
    imports are function-local) is classified as content_pipeline_not_installed
    with the install hint — the genuine "extra not installed" case.
    """
    runner = LibraryStageRunner()
    stage = _lib_stage()

    def _cp_missing(*, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        raise ImportError("No module named 'application.dispatcher'", name="application.dispatcher")

    with patch("atlas.library_runner._import_adapter", return_value=_cp_missing):
        outcome = runner.run(ctx=_CTX, stage=stage)

    assert outcome.status == "failure"
    assert outcome.error_type == "content_pipeline_not_installed"
    assert "job_cli" in outcome.output_text


def test_library_runner_atlas_adapter_import_error_not_masked() -> None:
    """An ImportError resolving the atlas adapter module itself is a real
    atlas-side bug — it surfaces as library_adapter_error, NOT as a missing
    optional dependency (review finding #3).
    """
    runner = LibraryStageRunner()
    stage = _lib_stage()

    with patch("atlas.library_runner._import_adapter", side_effect=ImportError("no module")):
        outcome = runner.run(ctx=_CTX, stage=stage)

    assert outcome.status == "failure"
    assert outcome.error_type == "library_adapter_error"
    assert "job_cli" not in outcome.output_text


def test_library_runner_non_content_pipeline_import_error_not_masked() -> None:
    """An ImportError for an unrelated module raised deep in the adapter body is
    a real bug, not a missing content-pipeline — stays library_adapter_error.
    """
    runner = LibraryStageRunner()
    stage = _lib_stage()

    def _unrelated_missing(*, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        raise ImportError("No module named 'some_typo_dep'", name="some_typo_dep")

    with patch("atlas.library_runner._import_adapter", return_value=_unrelated_missing):
        outcome = runner.run(ctx=_CTX, stage=stage)

    assert outcome.status == "failure"
    assert outcome.error_type == "library_adapter_error"
    assert "job_cli" not in outcome.output_text


def test_library_runner_adapter_exception_caught() -> None:
    runner = LibraryStageRunner()
    stage = _lib_stage()

    def _boom(*, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        raise RuntimeError("settings file missing")

    with patch("atlas.library_runner._import_adapter", return_value=_boom):
        outcome = runner.run(ctx=_CTX, stage=stage)

    assert outcome.status == "failure"
    assert outcome.error_type == "library_adapter_error"
    assert "settings file missing" in outcome.output_text


def test_library_runner_success_passthrough() -> None:
    runner = LibraryStageRunner()
    stage = _lib_stage()
    expected = StageOutcome(
        stage=stage, span_id="", status="success", output_text="report text", error_type=None
    )

    def _fake_adapter(*, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        return expected

    with patch("atlas.library_runner._import_adapter", return_value=_fake_adapter):
        outcome = runner.run(ctx=_CTX, stage=stage)

    assert outcome is expected


def test_library_runner_ignores_timeout_s() -> None:
    """A LIB: stage's timeout_s is never read by LibraryStageRunner — it has no
    subprocess to bound. A small timeout_s on a "slow" mocked adapter still
    completes successfully, proving the value is inert for in-process calls."""
    runner = LibraryStageRunner()
    stage = StageSpec(
        index=1,
        name="score_fit",
        span_kind="verify",
        tool="LIB:content_pipeline.score_jobs",
        gate_label="gate_shortlist",
        gate_index=0,
        timeout_s=1,
    )

    def _slow_adapter(*, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        return StageOutcome(
            stage=stage, span_id="", status="success", output_text="done", error_type=None
        )

    with patch("atlas.library_runner._import_adapter", return_value=_slow_adapter):
        outcome = runner.run(ctx=_CTX, stage=stage)

    assert outcome.status == "success"


def test_import_adapter_resolves_real_dotted_path() -> None:
    """_import_adapter itself (not mocked) against a real importable module —
    proves the importlib.import_module + getattr wiring works end to end."""
    fn = _import_adapter("atlas.library_adapters.score_jobs_adapter.invoke")

    from atlas.library_adapters.score_jobs_adapter import invoke

    assert fn is invoke


def test_import_adapter_raises_import_error_for_missing_module() -> None:
    with pytest.raises(ImportError):
        _import_adapter("atlas.library_adapters.not_a_real_module.invoke")
