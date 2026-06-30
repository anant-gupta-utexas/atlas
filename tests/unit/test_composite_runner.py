"""Unit tests for atlas.composite_runner.CompositeStageRunner (T2.3)."""

from __future__ import annotations

from pathlib import Path

from atlas.composite_runner import CompositeStageRunner
from atlas.orchestrator import RunContext, StageOutcome
from atlas.stages import StageSpec

_CTX = RunContext(
    run_id="r1", slug="acme-swe", task="score acme swe role", repo_root=Path("/tmp/repo")
)


class _RecordingRunner:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[StageSpec] = []

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        self.calls.append(stage)
        return StageOutcome(
            stage=stage,
            span_id="",
            status="success",
            output_text=f"handled by {self.name}",
            error_type=None,
        )


def _stage(tool: str, name: str = "some_stage") -> StageSpec:
    return StageSpec(
        index=0,
        name=name,
        span_kind="tool",
        tool=tool,
        gate_label=None,
        gate_index=None,
    )


def test_composite_runner_dispatches_lib_prefix() -> None:
    default = _RecordingRunner("default")
    library = _RecordingRunner("library")
    runner = CompositeStageRunner(default=default, library=library)
    stage = _stage("LIB:content_pipeline.score_jobs")

    outcome = runner.run(ctx=_CTX, stage=stage)

    assert outcome.output_text == "handled by library"
    assert library.calls == [stage]
    assert default.calls == []


def test_composite_runner_dispatches_raw_and_plugin_to_default() -> None:
    default = _RecordingRunner("default")
    library = _RecordingRunner("library")
    runner = CompositeStageRunner(default=default, library=library)

    raw_stage = _stage("RAW:do the thing", name="raw_stage")
    plugin_stage = _stage("consult-experts:research", name="plugin_stage")

    runner.run(ctx=_CTX, stage=raw_stage)
    runner.run(ctx=_CTX, stage=plugin_stage)

    assert default.calls == [raw_stage, plugin_stage]
    assert library.calls == []


def test_composite_runner_library_none_surfaces_failure() -> None:
    default = _RecordingRunner("default")
    runner = CompositeStageRunner(default=default, library=None)
    stage = _stage("LIB:content_pipeline.capture")

    outcome = runner.run(ctx=_CTX, stage=stage)

    assert outcome.status == "failure"
    assert outcome.error_type == "library_runner_unavailable"
    assert default.calls == []
