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

from atlas.orchestrator import (
    GateDecision,
    RunContext,
    SubprocessStageRunner,
)
from atlas.stages import StageSpec
from atlas.workflow_loader import LoadedWorkflow, load_workflow_file

_DEV_YAML = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"
_JOB_YAML = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "job.yaml"


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
