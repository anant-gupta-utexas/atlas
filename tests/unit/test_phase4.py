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
from atlas.workflow_loader import load_workflow_file

_DEV_YAML_PATH = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"
STAGES = load_workflow_file(_DEV_YAML_PATH).stages

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CODE_GEN_STAGE = next(s for s in STAGES if s.name == "code_gen")
_RESEARCH_STAGE = next(s for s in STAGES if s.name == "research")


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
    from atlas.stages import StageSpec

    bad_stage = StageSpec(
        index=0,
        name="research",
        span_kind="plan",
        tool="not-in-allow-list",
        gate_label="gate_research",
        gate_index=0,
    )
    runner = SubprocessStageRunner()
    ctx = _ctx(tmp_path)

    from atlas.orchestrator import RoutingDriftError

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        with pytest.raises(RoutingDriftError):
            runner.run(ctx=ctx, stage=bad_stage)
        mock_run.assert_not_called()


def test_resolve_passes_raw_tool_strings_through_verbatim() -> None:
    """RAW: strings are literal prompts from workflow YAML, not plugin names,
    so there is no third-party command to allow-list. Regression guard for the
    docstring/code mismatch that blocked `atlas run --workflow loop_dev`."""
    from atlas.plugin_resolver import build_prompt, resolve

    tool = "RAW:Implement the change per the acceptance criteria."
    assert resolve(tool) == tool
    # ...and build_prompt strips the same prefix downstream, so the round trip
    # yields the literal prompt rather than a slash command.
    assert build_prompt(resolve(tool), "task", "hint").startswith(
        "Implement the change per the acceptance criteria.\n\n"
    )


def test_resolve_raw_tool_string_still_honors_explicit_override() -> None:
    """The RAW: bypass must not shadow an explicit .atlas.toml override."""
    from atlas.plugin_resolver import resolve

    tool = "RAW:do the thing"
    assert resolve(tool, overrides={tool: "custom-plugin"}) == "custom-plugin"


def test_loop_dev_workflow_stages_all_resolve_without_overrides() -> None:
    """Every loop_dev.yaml stage must dispatch with no [plugin_commands] block
    in .atlas.toml — the precondition for T-L2.13's manual smoke test."""
    from atlas.plugin_resolver import resolve

    loop_dev_yaml = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "loop_dev.yaml"
    stages = load_workflow_file(loop_dev_yaml).stages

    assert [s.name for s in stages] == ["plan", "code_gen", "verify"]
    for stage in stages:
        resolve(stage.tool)  # must not raise RoutingDriftError


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


def test_click_prompter_prints_output_text_before_prompt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompter = ClickPrompter()
    stage = _RESEARCH_STAGE
    report = "## Shortlist\nGREEN: acme-swe (score=9)\nYELLOW: foo-corp (score=5)"

    with patch("builtins.input", return_value="a"):
        prompter.ask(stage=stage, gate_index=0, output_text=report)

    captured = capsys.readouterr()
    assert report in captured.out


def test_click_prompter_silent_when_output_text_empty(capsys: pytest.CaptureFixture[str]) -> None:
    prompter = ClickPrompter()
    stage = _RESEARCH_STAGE

    with patch("builtins.input", return_value="a"):
        prompter.ask(stage=stage, gate_index=0)

    captured = capsys.readouterr()
    assert "## Shortlist" not in captured.out


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


# ---------------------------------------------------------------------------
# T3.4 — Backend wiring in SubprocessStageRunner
# ---------------------------------------------------------------------------


def test_subprocess_runner_uses_claude_by_default(tmp_path: Path) -> None:
    runner = SubprocessStageRunner()
    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        runner.run(ctx=_ctx(tmp_path), stage=_RESEARCH_STAGE)
    argv = mock_run.call_args.args[0]
    assert argv[0] == "claude"


def test_subprocess_runner_respects_stage_backend_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.stages import StageSpec

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    agy_stage = StageSpec(
        index=0,
        name="research",
        span_kind="plan",
        tool=_RESEARCH_STAGE.tool,
        gate_label=_RESEARCH_STAGE.gate_label,
        gate_index=_RESEARCH_STAGE.gate_index,
        backend="agy",
    )
    runner = SubprocessStageRunner()
    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout='{"response": "ok", "stats": {}}')
        runner.run(ctx=_ctx(tmp_path), stage=agy_stage)
    argv = mock_run.call_args.args[0]
    assert argv[0] == "agy"
    assert "--include-directories" in argv
    assert "--add-dir" not in argv


def test_subprocess_runner_respects_workflow_default_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.workflow_loader import LoadedWorkflow

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    wf = LoadedWorkflow(name="test", default_backend="agy", stages=())
    runner = SubprocessStageRunner(loaded_workflow=wf)
    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout='{"response": "ok", "stats": {}}')
        runner.run(ctx=_ctx(tmp_path), stage=_RESEARCH_STAGE)
    argv = mock_run.call_args.args[0]
    assert argv[0] == "agy"


def test_subprocess_runner_unknown_backend_returns_failure(tmp_path: Path) -> None:
    from atlas.stages import StageSpec

    bad_stage = StageSpec(
        index=0,
        name="research",
        span_kind="plan",
        tool=_RESEARCH_STAGE.tool,
        gate_label=_RESEARCH_STAGE.gate_label,
        gate_index=_RESEARCH_STAGE.gate_index,
        backend="opus",
    )
    runner = SubprocessStageRunner()
    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        outcome = runner.run(ctx=_ctx(tmp_path), stage=bad_stage)
    assert outcome.status == "failure"
    assert outcome.error_type == "unknown_backend"
    mock_run.assert_not_called()


def test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security: subprocess.run MUST NOT be called when agy auth env vars are absent."""
    from atlas.stages import StageSpec

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    agy_stage = StageSpec(
        index=0,
        name="research",
        span_kind="plan",
        tool=_RESEARCH_STAGE.tool,
        gate_label=_RESEARCH_STAGE.gate_label,
        gate_index=_RESEARCH_STAGE.gate_index,
        backend="agy",
    )
    runner = SubprocessStageRunner()
    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.side_effect = AssertionError("subprocess.run must not be called")
        outcome = runner.run(ctx=_ctx(tmp_path), stage=agy_stage)
    assert outcome.status == "failure"
    assert outcome.error_type == "agy_missing_auth_env"
    mock_run.assert_not_called()
