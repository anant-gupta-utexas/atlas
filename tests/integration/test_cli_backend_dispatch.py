"""Integration tests for CLI backend dispatch — T3.6.

Verifies that agy and claude backends are dispatched correctly through the full
Pipeline + SubprocessStageRunner + CliBackend stack. subprocess.run is mocked
throughout — no real CLI binaries are spawned in CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

from atlas.cli_backend import ClaudeCodeBackend, CodexBackend, UsageStats, codex_usage_to_tokens
from atlas.orchestrator import (
    GateDecision,
    Pipeline,
    RunContext,
    StageOutcome,
    SubprocessStageRunner,
)
from atlas.plumb_io import PlumbIO
from atlas.stages import StageSpec
from atlas.state import StateStore
from atlas.workflow_loader import LoadedWorkflow, load_workflow_file

_DEV_YAML = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"
_JOB_YAML = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "job.yaml"
_LOOP_DEV_YAML = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "loop_dev.yaml"
_CODEX_FIXTURES = Path(__file__).parents[1] / "fixtures" / "codex_jsonl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePrompter:
    def ask(self, *, stage: StageSpec, gate_index: int, output_text: str = "") -> GateDecision:
        return GateDecision(label="approved", turn_count=1, reason=None)


def _completed(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> MagicMock:
    m = MagicMock(spec=CompletedProcess)
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _stage(name: str = "research", backend: str | None = None) -> StageSpec:
    stages = load_workflow_file(_DEV_YAML).stages
    base = next(s for s in stages if s.name == name)
    return StageSpec(
        index=base.index,
        name=base.name,
        span_kind=base.span_kind,
        tool=base.tool,
        gate_label=base.gate_label,
        gate_index=base.gate_index,
        isolate=base.isolate,
        gate_is_async=base.gate_is_async,
        backend=backend,
        timeout_s=base.timeout_s,
    )


# ---------------------------------------------------------------------------
# test_agy_dispatch_end_to_end_mocked (FR-9 / §13 #7)
# ---------------------------------------------------------------------------


def test_agy_dispatch_end_to_end_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stage with backend: agy dispatches agy and produces correct StageOutcome."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    agy_stage = _stage("research", backend="agy")
    agy_response = json.dumps({"response": "agy-said-this", "stats": {}})

    wf = LoadedWorkflow(name="agy-test", default_backend=None, stages=(agy_stage,))
    runner = SubprocessStageRunner(model="haiku", default_backend="claude", loaded_workflow=wf)

    ctx = RunContext(
        run_id="a" * 32,
        slug="test-agy",
        task="test agy dispatch",
        repo_root=tmp_path,
    )

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout=agy_response)
        outcome = runner.run(ctx=ctx, stage=agy_stage)

    # agy binary was invoked, not claude
    argv = mock_run.call_args.args[0]
    assert argv[0] == "agy"
    assert "--include-directories" in argv
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"

    # Parsed correctly
    assert outcome.status == "success"
    assert outcome.output_text == "agy-said-this"
    assert outcome.error_type is None


# ---------------------------------------------------------------------------
# test_mixed_backend_workflow
# ---------------------------------------------------------------------------


def test_mixed_backend_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 1 backend: claude, Stage 2 backend: agy — each gets the right binary."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    claude_stage = _stage("research", backend="claude")
    agy_stage = _stage("prd_draft", backend="agy")

    wf = LoadedWorkflow(name="mixed-test", default_backend=None, stages=(claude_stage, agy_stage))
    runner = SubprocessStageRunner(model="haiku", default_backend="claude", loaded_workflow=wf)

    ctx = RunContext(
        run_id="b" * 32,
        slug="test-mixed",
        task="mixed backends",
        repo_root=tmp_path,
    )

    recorded_argvs: list[list[str]] = []

    def _fake_run(argv: list[str], **_kw: object) -> MagicMock:
        recorded_argvs.append(argv)
        if argv[0] == "agy":
            return _completed(stdout=json.dumps({"response": "agy-output", "stats": {}}))
        return _completed(stdout="claude-output")

    with patch("atlas.orchestrator.subprocess.run", side_effect=_fake_run):
        outcome1 = runner.run(ctx=ctx, stage=claude_stage)
        outcome2 = runner.run(ctx=ctx, stage=agy_stage)

    assert recorded_argvs[0][0] == "claude"
    assert recorded_argvs[1][0] == "agy"
    assert outcome1.status == "success"
    assert outcome1.output_text == "claude-output"
    assert outcome2.status == "success"
    assert outcome2.output_text == "agy-output"


# ---------------------------------------------------------------------------
# test_dev_pipeline_unaffected_by_phase_3 (FR-8 / §14 exit #1)
# ---------------------------------------------------------------------------


def test_dev_pipeline_unaffected_by_phase_3(tmp_path: Path) -> None:
    """dev.yaml stages still dispatch to claude with byte-identical argv.

    --bare and --output-format must NOT appear (Resolved Decisions #1 and #2).
    """
    dev_wf = load_workflow_file(_DEV_YAML)
    research = next(s for s in dev_wf.stages if s.name == "research")

    runner = SubprocessStageRunner(
        model="haiku",
        default_backend="claude",
        loaded_workflow=dev_wf,
    )
    ctx = RunContext(
        run_id="c" * 32,
        slug="test-dev",
        task="add cache middleware",
        repo_root=tmp_path,
    )

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="research output")
        outcome = runner.run(ctx=ctx, stage=research)

    argv = mock_run.call_args.args[0]

    # Must dispatch to claude, not agy
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    # Phase 3 must NOT add --bare or --output-format
    assert "--bare" not in argv, "--bare must not appear (Resolved Decision #2)"
    assert "--output-format" not in argv, "--output-format must not appear (Resolved Decision #1)"
    # Phase 2 flags must still be present
    assert "--no-session-persistence" in argv
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "haiku"
    assert "--add-dir" in argv

    assert outcome.status == "success"
    assert outcome.output_text == "research output"


# ---------------------------------------------------------------------------
# test_job_workflow_tailor_materials_dispatches_via_claude_backend (FR-8)
# ---------------------------------------------------------------------------


def test_job_workflow_tailor_materials_dispatches_via_claude_backend(
    tmp_path: Path,
) -> None:
    """tailor_materials.backend: claude (parsed-but-inert in Phase 2) is now consumed.

    Verifies the field is set and that resolve_backend() returns 'claude', and
    that SubprocessStageRunner dispatches to ClaudeCodeBackend (producing Phase 2
    argv shape) when the tool is registered via command_overrides.
    """
    from atlas.cli_backend import resolve_backend

    job_wf = load_workflow_file(_JOB_YAML)
    tailor = next(s for s in job_wf.stages if s.name == "tailor_materials")

    # The field is set in the YAML and parsed correctly.
    assert tailor.backend == "claude", "job.yaml tailor_materials must have backend: claude"

    # resolve_backend returns 'claude' for this stage (per-stage tier 1 wins).
    resolved = resolve_backend(stage=tailor, workflow=job_wf, config_default=None)
    assert resolved == "claude"

    # Run via SubprocessStageRunner with command_overrides to bypass plugin
    # allow-list (the RAW: tool string is not in PLUGIN_COMMANDS).
    runner = SubprocessStageRunner(
        model="haiku",
        default_backend="claude",
        loaded_workflow=job_wf,
        command_overrides={tailor.tool: "tailor-plugin"},
    )
    ctx = RunContext(
        run_id="d" * 32,
        slug="test-job",
        task="tailor job materials",
        repo_root=tmp_path,
    )

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="tailor output")
        outcome = runner.run(ctx=ctx, stage=tailor)

    argv = mock_run.call_args.args[0]
    # Backend: claude → must use claude binary with Phase 2 argv shape
    assert argv[0] == "claude"
    assert "--no-session-persistence" in argv
    assert "--model" in argv
    assert "--bare" not in argv
    assert "--output-format" not in argv
    assert outcome.status == "success"


# ---------------------------------------------------------------------------
# test_dev_pipeline_unaffected_by_phase_l0 (T-L0.7 / TRD-v3 §13 #2)
# ---------------------------------------------------------------------------


def test_dev_pipeline_unaffected_by_phase_l0(tmp_path: Path) -> None:
    """Attended dev-pipeline dispatch is provably unaffected by Phase L0.

    SubprocessStageRunner.run() never sets extra_flags in L0 — no CLI surface
    exists yet to request loop-mode telemetry/permission flags (that's L2's
    `atlas loop` command). This proves the byte-identical-argv claim end to
    end, not just at the ClaudeCodeBackend.build_argv unit-test level.
    """
    dev_wf = load_workflow_file(_DEV_YAML)
    research = next(s for s in dev_wf.stages if s.name == "research")

    runner = SubprocessStageRunner(model="haiku", default_backend="claude", loaded_workflow=dev_wf)
    ctx = RunContext(
        run_id="e" * 32,
        slug="test-dev-l0",
        task="add cache middleware",
        repo_root=tmp_path,
    )

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="research output")
        outcome = runner.run(ctx=ctx, stage=research)

    argv = mock_run.call_args.args[0]
    assert argv[0] == "claude"
    assert "--output-format" not in argv
    assert "--permission-mode" not in argv
    assert "--allowedTools" not in argv
    assert "--max-turns" not in argv
    assert "--dangerously-skip-permissions" not in argv
    assert outcome.status == "success"
    assert outcome.output_text == "research output"


# ---------------------------------------------------------------------------
# test_claude_backend_loop_mode_telemetry_end_to_end (T-L0.7 / TRD-v3 §13 #1)
# ---------------------------------------------------------------------------


def test_claude_backend_loop_mode_telemetry_end_to_end(tmp_path: Path) -> None:
    """Loop-mode dispatch (mocked subprocess) -> UsageStats -> PlumbIO.record_span.

    No `atlas loop` command exists in L0 (that's L2), so this test drives the
    telemetry pieces directly the way a future loop-mode call site would,
    proving build_argv/parse_result/parse_usage/record_span compose correctly
    end to end. total_cost_usd is asserted present in-memory but never
    written to plumb (deferred to plumb P1-a — see BACKLOG.md).
    """
    backend = ClaudeCodeBackend()

    argv = backend.build_argv(
        prompt="do the task",
        model="haiku",
        add_dirs=[tmp_path],
        timeout_s=60,
        extra_flags={
            "telemetry": "json",
            "permission_mode": "acceptEdits",
            "allowed_tools": "Bash(git *),Edit",
            "max_turns": "10",
        },
    )
    assert "--output-format" in argv
    assert "--dangerously-skip-permissions" not in argv

    json_envelope = json.dumps(
        {
            "subtype": "success",
            "result": "task complete",
            "total_cost_usd": 0.042,
            "usage": {"input_tokens": 1234, "output_tokens": 567},
        }
    )

    # Mocked subprocess result — no real `claude` binary spawned.
    result = _completed(stdout=json_envelope)

    status, output_text, error_type = backend.parse_result(
        result.stdout, result.stderr, result.returncode
    )
    assert status == "success"
    assert output_text == "task complete"
    assert error_type is None

    usage = backend.parse_usage(result.stdout)
    assert usage == UsageStats(total_cost_usd=0.042, input_tokens=1234, output_tokens=567)

    plumb = PlumbIO(real=False)
    run_id = plumb.open_run(task="loop-mode-test")
    assert usage is not None
    plumb.record_span(
        run_id=run_id,
        kind="code_gen",
        name="code_gen",
        status="success",
        latency_ms=100.0,
        error_type=None,
        tokens=(usage.input_tokens, usage.output_tokens),
    )

    assert plumb.spans[-1]["tokens"] == (1234, 567)
    # total_cost_usd stays in-memory only — no plumb write for it (plumb P1-a).
    assert not any("dollar_cost" in span for span in plumb.spans)


# ---------------------------------------------------------------------------
# test_codex_dispatch_end_to_end_mocked (T-L1.7 / TRD-v3 §13 #3)
# ---------------------------------------------------------------------------


def test_codex_dispatch_end_to_end_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stage with backend: codex dispatches codex and produces correct StageOutcome.

    Also proves build_argv/parse_result/parse_usage/record_span compose
    end-to-end, same pattern as test_claude_backend_loop_mode_telemetry_end_to_end
    (no Pipeline.step() wiring for tokens exists yet — that's a future caller).
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    codex_stage = _stage("research", backend="codex")
    codex_stdout = (_CODEX_FIXTURES / "success.jsonl").read_text(encoding="utf-8")

    wf = LoadedWorkflow(name="codex-test", default_backend=None, stages=(codex_stage,))
    runner = SubprocessStageRunner(
        model="gpt-5-codex", default_backend="claude", loaded_workflow=wf
    )

    ctx = RunContext(
        run_id="f" * 32,
        slug="test-codex",
        task="test codex dispatch",
        repo_root=tmp_path,
    )

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout=codex_stdout)
        outcome = runner.run(ctx=ctx, stage=codex_stage)

    argv = mock_run.call_args.args[0]
    assert argv[0] == "codex"
    assert argv[1] == "exec"
    assert "--sandbox" in argv
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv

    assert outcome.status == "success"
    assert outcome.output_text == "hi"
    assert outcome.error_type is None

    backend = CodexBackend()
    usage = backend.parse_usage(codex_stdout)
    assert usage is not None
    in_tokens, out_tokens = codex_usage_to_tokens(usage)

    plumb = PlumbIO(real=False)
    run_id = plumb.open_run(task="codex-dispatch-test")
    plumb.record_span(
        run_id=run_id,
        kind="code_gen",
        name="code_gen",
        status="success",
        latency_ms=100.0,
        error_type=None,
        tokens=(in_tokens, out_tokens),
    )
    assert plumb.spans[-1]["tokens"] == (in_tokens, out_tokens)


def test_codex_dispatch_failure_status_no_pr_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonzero-exit Codex run surfaces StageOutcome.status == 'failure' cleanly."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    codex_stage = _stage("research", backend="codex")

    wf = LoadedWorkflow(name="codex-fail-test", default_backend=None, stages=(codex_stage,))
    runner = SubprocessStageRunner(
        model="gpt-5-codex", default_backend="claude", loaded_workflow=wf
    )

    ctx = RunContext(
        run_id="g" * 32,
        slug="test-codex-fail",
        task="test codex failure",
        repo_root=tmp_path,
    )

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=1, stdout="", stderr="boom")
        outcome = runner.run(ctx=ctx, stage=codex_stage)

    assert outcome.status == "failure"
    assert outcome.error_type == "codex_nonzero_exit"


# ---------------------------------------------------------------------------
# RunResult regression (T-L1.6 / T-L1.7)
# ---------------------------------------------------------------------------


class _FakeGateRunner:
    """Returns canned outcomes in order; used to script Pipeline stage results."""

    def __init__(self, outcomes: list[StageOutcome]) -> None:
        self._outcomes = list(outcomes)
        self._idx = 0

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        outcome = self._outcomes[self._idx]
        self._idx += 1
        return outcome


def _loop_dev_pipeline(tmp_path: Path, runner: object) -> Pipeline:
    state = StateStore(tmp_path)
    plumb = PlumbIO(real=False)
    stages = load_workflow_file(_LOOP_DEV_YAML).stages
    return Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=runner,  # type: ignore[arg-type]
        prompter=_FakePrompter(),
        stages=stages,
        workflow_name="loop_dev",
    )


def test_run_to_completion_returns_run_result(tmp_path: Path) -> None:
    stages = load_workflow_file(_LOOP_DEV_YAML).stages
    success_outcomes = [
        StageOutcome(stage=s, span_id="", status="success", output_text="ok", error_type=None)
        for s in stages
    ]
    pipeline = _loop_dev_pipeline(tmp_path, _FakeGateRunner(success_outcomes))
    ctx = pipeline.start(task="loop dev test", slug="loop-dev-test")
    result = pipeline.run_to_completion(ctx)
    assert result.status == "success"
    assert result.ctx.run_id == ctx.run_id


def test_run_to_completion_returns_failure_run_result(tmp_path: Path) -> None:
    stages = load_workflow_file(_LOOP_DEV_YAML).stages
    outcomes = [
        StageOutcome(
            stage=stages[0], span_id="", status="success", output_text="ok", error_type=None
        ),
        StageOutcome(
            stage=stages[1],
            span_id="",
            status="failure",
            output_text="verify failed",
            error_type="codex_nonzero_exit",
        ),
    ]
    pipeline = _loop_dev_pipeline(tmp_path, _FakeGateRunner(outcomes))
    ctx = pipeline.start(task="loop dev failing test", slug="loop-dev-fail-test")
    result = pipeline.run_to_completion(ctx)
    assert result.status == "failure"
    assert result.ctx.run_id == ctx.run_id


def test_cli_run_and_resume_updated_for_run_result(tmp_path: Path) -> None:
    """cli.py::run/resume discard run_to_completion()'s return value already —
    this pins that the RunResult widening doesn't break that call shape."""
    stages = load_workflow_file(_LOOP_DEV_YAML).stages
    success_outcomes = [
        StageOutcome(stage=s, span_id="", status="success", output_text="ok", error_type=None)
        for s in stages
    ]
    pipeline = _loop_dev_pipeline(tmp_path, _FakeGateRunner(success_outcomes))
    ctx = pipeline.start(task="cli regression test", slug="cli-regression-test")

    # Mirrors cli.py::run's bare-statement call (no assignment).
    pipeline.run_to_completion(ctx)
