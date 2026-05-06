"""Unit tests for Phase 4: SubprocessStageRunner, ClickPrompter, plugin allow-list."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas.orchestrator import (
    _GATE_MAX_REASON_BYTES,
    AbortedError,
    ClickPrompter,
    RunContext,
    SubprocessStageRunner,
    _clamp_reason,
)
from atlas.stages import STAGES, StageName

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CODE_GEN_STAGE = next(s for s in STAGES if s.name == StageName.CODE_GEN)
_RESEARCH_STAGE = next(s for s in STAGES if s.name == StageName.RESEARCH)


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(
        run_id="a" * 32,
        slug="test-task",
        task="add cache middleware",
        repo_root=tmp_path,
    )


def _completed(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# T4.1 — SubprocessStageRunner
# ---------------------------------------------------------------------------


def test_runner_uses_list_form_subprocess(tmp_path: Path) -> None:
    runner = SubprocessStageRunner()
    ctx = _ctx(tmp_path)

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        runner.run(ctx=ctx, stage=_RESEARCH_STAGE)

    call_args = mock_run.call_args
    assert isinstance(call_args.args[0], list), "subprocess.run must use list-form args"


def test_runner_nonzero_exit_returns_failure_with_plugin_nonzero_exit(tmp_path: Path) -> None:
    runner = SubprocessStageRunner()
    ctx = _ctx(tmp_path)

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=1, stderr="plugin crashed")
        outcome = runner.run(ctx=ctx, stage=_RESEARCH_STAGE)

    assert outcome.status == "failure"
    assert outcome.error_type == "plugin_nonzero_exit"


def test_runner_timeout_returns_failure_with_plugin_timeout(tmp_path: Path) -> None:
    runner = SubprocessStageRunner()
    ctx = _ctx(tmp_path)

    with patch(
        "atlas.orchestrator.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 600)
    ):
        outcome = runner.run(ctx=ctx, stage=_RESEARCH_STAGE)

    assert outcome.status == "failure"
    assert outcome.error_type == "plugin_timeout"


def test_runner_success_returns_success_outcome(tmp_path: Path) -> None:
    runner = SubprocessStageRunner()
    ctx = _ctx(tmp_path)

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="stage output")
        outcome = runner.run(ctx=ctx, stage=_RESEARCH_STAGE)

    assert outcome.status == "success"
    assert outcome.output_text == "stage output"
    assert outcome.error_type is None


def test_runner_code_gen_passes_worktree_via_add_dir(tmp_path: Path) -> None:
    runner = SubprocessStageRunner()
    worktree = tmp_path / ".atlas" / "worktrees" / "test-task-aaaaaaaa"
    worktree.mkdir(parents=True)
    ctx = RunContext(
        run_id="a" * 32,
        slug="test-task",
        task="task",
        repo_root=tmp_path,
        worktree_path=worktree,
    )

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        runner.run(ctx=ctx, stage=_CODE_GEN_STAGE)

    args = mock_run.call_args.args[0]
    # Worktree must be accessible via --add-dir, not cwd
    add_dir_values = [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]
    assert str(worktree) in add_dir_values, (
        f"worktree path must appear after --add-dir, got: {add_dir_values}"
    )


def test_runner_applies_per_stage_timeout(tmp_path: Path) -> None:
    runner = SubprocessStageRunner()
    ctx = _ctx(tmp_path)

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        runner.run(ctx=ctx, stage=_CODE_GEN_STAGE)

    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs.get("timeout") == 1800  # code_gen default


def test_runner_timeout_override_respected(tmp_path: Path) -> None:
    runner = SubprocessStageRunner(timeout_overrides={"code_gen": 300})
    ctx = _ctx(tmp_path)

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        runner.run(ctx=ctx, stage=_CODE_GEN_STAGE)

    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs.get("timeout") == 300


# ---------------------------------------------------------------------------
# T4.3 — Allow-list (RoutingDriftError before subprocess.run)
# ---------------------------------------------------------------------------


def test_unknown_plugin_raises_routing_drift_error_before_subprocess(tmp_path: Path) -> None:
    from atlas.stages import GateLabel, StageSpec

    bad_stage = StageSpec(
        index=0,
        name=StageName.RESEARCH,
        span_kind="plan",
        tool="not-in-allow-list",
        gate_label=GateLabel.GATE_RESEARCH,
        gate_index=0,
    )
    runner = SubprocessStageRunner()
    ctx = _ctx(tmp_path)

    from atlas.orchestrator import RoutingDriftError

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        with pytest.raises(RoutingDriftError):
            runner.run(ctx=ctx, stage=bad_stage)
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# T4.2 — ClickPrompter
# ---------------------------------------------------------------------------


def test_click_prompter_approve(tmp_path: Path) -> None:
    prompter = ClickPrompter()
    stage = _RESEARCH_STAGE

    with patch("builtins.input", return_value="a"):
        decision = prompter.ask(stage=stage, gate_index=0)

    assert decision.label == "approved"
    assert decision.turn_count == 1
    assert decision.reason is None


def test_click_prompter_reject_with_inline_reason(tmp_path: Path) -> None:
    prompter = ClickPrompter()
    stage = _RESEARCH_STAGE

    with patch("builtins.input", return_value="r not thorough enough"):
        decision = prompter.ask(stage=stage, gate_index=0)

    assert decision.label == "rejected"
    assert decision.reason == "not thorough enough"


def test_click_prompter_reject_prompts_for_reason_when_bare_r(tmp_path: Path) -> None:
    prompter = ClickPrompter()
    stage = _RESEARCH_STAGE

    inputs = iter(["r", "missing section on auth"])
    with patch("builtins.input", side_effect=inputs):
        decision = prompter.ask(stage=stage, gate_index=0)

    assert decision.label == "rejected"
    assert decision.reason == "missing section on auth"


def test_click_prompter_quit_raises_aborted_error(tmp_path: Path) -> None:
    prompter = ClickPrompter()
    stage = _RESEARCH_STAGE

    with patch("builtins.input", return_value="q"):
        with pytest.raises(AbortedError, match="quit"):
            prompter.ask(stage=stage, gate_index=0)


def test_click_prompter_three_bad_inputs_raises_aborted_error() -> None:
    prompter = ClickPrompter()
    stage = _RESEARCH_STAGE

    with patch("builtins.input", return_value="???"):
        with pytest.raises(AbortedError, match="unparseable"):
            prompter.ask(stage=stage, gate_index=0)


def test_click_prompter_aborts_on_eof() -> None:
    prompter = ClickPrompter()
    stage = _RESEARCH_STAGE

    with patch("builtins.input", side_effect=EOFError):
        with pytest.raises(AbortedError):
            prompter.ask(stage=stage, gate_index=0)


def test_click_prompter_reason_clamped_to_4kb() -> None:
    big_reason = "x" * 10_000
    clamped = _clamp_reason(big_reason)
    assert len(clamped.encode()) <= _GATE_MAX_REASON_BYTES + 50  # small buffer for " … [truncated]"
    assert "[truncated]" in clamped


def test_click_prompter_short_reason_not_truncated() -> None:
    short = "fix the intro section"
    assert _clamp_reason(short) == short


def test_click_prompter_turn_count_increments_on_retry() -> None:
    prompter = ClickPrompter()
    stage = _RESEARCH_STAGE

    # 2 bad inputs then approve
    inputs = iter(["???", "???", "a"])
    with patch("builtins.input", side_effect=inputs):
        decision = prompter.ask(stage=stage, gate_index=0)

    assert decision.label == "approved"
    assert decision.turn_count == 3
