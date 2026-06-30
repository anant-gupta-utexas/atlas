"""
Regression tests for findings in atlas-pipeline-trs-code-review.md (2026-05-01).

Each test pins one of the four behaviours fixed in that review pass:

1. worktree_path persists across step()/resume() so stage 6 (code_review)
   operates on the generated code, not main.
2. The post-commit hook writes a durable record to pending-scores.jsonl
   (resolved via --git-common-dir from inside a worktree), and the next
   orchestrator step() flushes it through the live plumb run handle.
3. SubprocessStageRunner passes a real context file (tasks.md) and the task
   description, not the tool identifier, to the plugin.
4. tasks.md checkbox stays unchecked when a stage fails or its gate rejects,
   so resume re-tries the same stage.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas.orchestrator import (
    GateDecision,
    Pipeline,
    RunContext,
    StageOutcome,
    SubprocessStageRunner,
)
from atlas.plumb_io import PlumbIO
from atlas.post_commit_hook import _main_repo_root
from atlas.stages import StageSpec
from atlas.state import StateStore
from atlas.workflow_loader import load_workflow_file

_DEV_YAML_PATH = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"
STAGES = load_workflow_file(_DEV_YAML_PATH).stages

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _CapturingRunner:
    """Captures the ctx passed to run() per stage; returns success."""

    def __init__(self) -> None:
        self.calls: list[RunContext] = []

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        self.calls.append(ctx)
        status = "awaiting_hook" if stage.name == "code_gen" else "success"
        return StageOutcome(
            stage=stage,
            span_id="",
            status=status,
            output_text="",
            error_type=None,
        )


class _ApprovePrompter:
    def ask(self, *, stage: StageSpec, gate_index: int, output_text: str = "") -> GateDecision:
        return GateDecision(label="approved", turn_count=1, reason=None)


class _StubWorktree:
    """Pretends to create a worktree path; does no git work."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self.created: list[Path] = []

    def create(self, *, slug: str, run_id: str) -> Path:
        path = self._repo_root / ".atlas" / "worktrees" / f"{slug}-{run_id[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.created.append(path)
        return path


# ---------------------------------------------------------------------------
# Finding 1: worktree_path persists across step() and resume()
# ---------------------------------------------------------------------------


def test_worktree_path_persists_to_current_run_file(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    plumb = PlumbIO(real=False)
    runner = _CapturingRunner()
    worktree = _StubWorktree(tmp_path)
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=runner,
        prompter=_ApprovePrompter(),
        worktree=worktree,  # type: ignore[arg-type]
    )

    ctx = pipeline.start(task="t", slug="my-task")
    for _ in range(5):
        pipeline.step(ctx)
    pipeline.step(ctx)  # stage 5: code_gen — creates worktree

    quad = state.read_current_run_with_worktree()
    assert quad is not None
    _, _, persisted, _ = quad
    assert persisted == worktree.created[0], (
        "worktree_path must be persisted to .atlas/current-run after code_gen"
    )


def test_resume_after_code_gen_carries_worktree_to_code_review(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    runner1 = _CapturingRunner()
    worktree = _StubWorktree(tmp_path)
    pipeline1 = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=PlumbIO(real=False),
        runner=runner1,
        prompter=_ApprovePrompter(),
        worktree=worktree,  # type: ignore[arg-type]
    )

    ctx = pipeline1.start(task="t", slug="my-task")
    for _ in range(6):  # stages 0-5 (5 returns awaiting_hook)
        pipeline1.step(ctx)

    # Simulate restart with a fresh Pipeline.
    runner2 = _CapturingRunner()
    pipeline2 = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=PlumbIO(real=False),
        runner=runner2,
        prompter=_ApprovePrompter(),
        worktree=worktree,  # type: ignore[arg-type]
    )
    ctx2 = pipeline2.resume()

    assert ctx2.worktree_path is not None, (
        "resume() must rehydrate worktree_path from .atlas/current-run"
    )

    pipeline2.step(ctx2)  # should be stage 6 (code_review)
    last_call = runner2.calls[-1]
    assert last_call.worktree_path == worktree.created[0], (
        "code_review must run with worktree_path set, not fall back to repo_root"
    )


# ---------------------------------------------------------------------------
# Finding 2: post-commit hook durability
# ---------------------------------------------------------------------------


def _init_main_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=main, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.t"], cwd=main, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=main, check=True, capture_output=True)
    (main / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=main, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=main, check=True, capture_output=True)

    wt = main / ".atlas" / "worktrees" / "feat-aaaaaaaa"
    wt.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "-b", "atlas/feat", str(wt), "main"],
        cwd=main,
        check=True,
        capture_output=True,
    )
    return main, wt


def test_main_repo_root_resolves_from_worktree(tmp_path: Path) -> None:
    main, wt = _init_main_and_worktree(tmp_path)
    # Run the helper from inside the worktree's cwd
    import os

    cwd = os.getcwd()
    try:
        os.chdir(wt)
        resolved = _main_repo_root()
    finally:
        os.chdir(cwd)
    assert resolved is not None
    assert resolved.resolve() == main.resolve(), (
        "_main_repo_root must return the main checkout, not the worktree"
    )


def test_hook_writes_pending_score_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    main, wt = _init_main_and_worktree(tmp_path)
    (main / ".atlas").mkdir(exist_ok=True)
    (main / ".atlas" / "current-run").write_text("run-abc\nfeat\n")

    # Make a commit in the worktree so HEAD has a sha
    (wt / "x.txt").write_text("y\n")
    subprocess.run(["git", "add", "."], cwd=wt, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.t", "-c", "user.name=t", "commit", "-m", "c"],
        cwd=wt,
        check=True,
        capture_output=True,
    )

    monkeypatch.chdir(wt)
    from atlas.post_commit_hook import run as hook_run

    with pytest.raises(SystemExit):
        hook_run()

    pending = main / ".atlas" / "pending-scores.jsonl"
    assert pending.exists(), "hook must write pending-scores.jsonl in the main repo"
    line = pending.read_text().strip()
    rec = json.loads(line)
    assert rec["run_id"] == "run-abc"
    assert rec["metric"] == "gate_commit"
    assert rec["value_label"] == "approved"


def test_orchestrator_flush_drains_pending_into_plumb(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    plumb = PlumbIO(real=False)
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=_CapturingRunner(),
        prompter=_ApprovePrompter(),
        worktree=_StubWorktree(tmp_path),  # type: ignore[arg-type]
    )

    ctx = pipeline.start(task="t", slug="task")
    for _ in range(6):  # walk through code_gen → awaiting_hook
        pipeline.step(ctx)

    # Hook fires after code_gen commit
    pending = tmp_path / ".atlas" / "pending-scores.jsonl"
    pending.parent.mkdir(exist_ok=True)
    pending.write_text(
        json.dumps(
            {
                "run_id": ctx.run_id,
                "metric": "gate_commit",
                "value_label": "approved",
                "rationale": "commit deadbeef",
            }
        )
        + "\n"
    )

    # Next step (stage 6: code_review) should drain the file
    pipeline.step(ctx)

    gate_commit = [s for s in plumb.scores if s.get("metric") == "gate_commit"]
    assert len(gate_commit) == 1, (
        f"flush must record gate_commit through plumb; got scores={plumb.scores}"
    )
    assert not pending.exists(), "pending-scores.jsonl must be removed after drain"


# ---------------------------------------------------------------------------
# Finding 3: plugin context payload
# ---------------------------------------------------------------------------


def test_runner_passes_tasks_md_path_as_context(tmp_path: Path) -> None:
    runner = SubprocessStageRunner()
    research = next(s for s in STAGES if s.name == "research")
    ctx = RunContext(
        run_id="a" * 32,
        slug="my-task",
        task="add cache middleware",
        repo_root=tmp_path,
    )

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = ""
    completed.stderr = ""

    with patch("atlas.orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = completed
        runner.run(ctx=ctx, stage=research)

    args = mock_run.call_args.args[0]
    # Prompt (second arg after -p) must reference tasks.md and the task description
    assert "-p" in args
    prompt = args[args.index("-p") + 1]
    assert "tasks.md" in prompt, f"prompt should reference tasks.md path, got {prompt!r}"
    assert "my-task" in prompt, "prompt should include the slug directory"
    assert "add cache middleware" in prompt, "task description must be in the prompt"
    # And NOT the bare tool identifier as the context value
    assert research.tool not in prompt.split("\n")[0], (
        "regression: tool name must not be the sole prompt content"
    )


# ---------------------------------------------------------------------------
# Finding 4: checkbox only marked on success/approval
# ---------------------------------------------------------------------------


def _stage_box_checked(state: StateStore, ctx: RunContext, stage_name: str) -> bool:
    path = tmp_path_for(state, ctx)
    content = path.read_text()
    pattern = re.compile(rf"^- \[(.)\] {re.escape(stage_name)}$", re.MULTILINE)
    m = pattern.search(content)
    assert m, f"stage {stage_name} not found in tasks.md"
    return m.group(1) == "x"


def tasks_md_path(state: StateStore, ctx: RunContext) -> Path:
    return ctx.repo_root / "dev" / "active" / ctx.slug / "tasks.md"


def tmp_path_for(state: StateStore, ctx: RunContext) -> Path:
    return tasks_md_path(state, ctx)


def test_failed_stage_leaves_box_unchecked(tmp_path: Path) -> None:
    state = StateStore(tmp_path)

    class _FailRunner:
        def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text="",
                error_type="plugin_nonzero_exit",
            )

    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=PlumbIO(real=False),
        runner=_FailRunner(),
        prompter=_ApprovePrompter(),
    )

    ctx = pipeline.start(task="t", slug="task")
    outcome = pipeline.step(ctx)
    assert outcome is not None and outcome.status == "failure"

    assert not _stage_box_checked(state, ctx, "research"), (
        "failed stage must NOT be marked complete in tasks.md"
    )
    # And resume should pick up the same stage
    assert state.first_unchecked(ctx) == "research"


def test_rejected_gate_leaves_box_unchecked(tmp_path: Path) -> None:
    state = StateStore(tmp_path)

    class _RejectPrompter:
        def ask(self, *, stage: StageSpec, gate_index: int, output_text: str = "") -> GateDecision:
            return GateDecision(label="rejected", turn_count=1, reason="no")

    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=PlumbIO(real=False),
        runner=_CapturingRunner(),
        prompter=_RejectPrompter(),
    )

    ctx = pipeline.start(task="t", slug="task")
    outcome = pipeline.step(ctx)
    assert outcome is not None and outcome.status == "rejected"

    assert not _stage_box_checked(state, ctx, "research"), (
        "rejected stage must NOT be marked complete; user can re-run after fixing"
    )
    assert state.first_unchecked(ctx) == "research"


def test_approved_stage_marks_box_checked(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=PlumbIO(real=False),
        runner=_CapturingRunner(),
        prompter=_ApprovePrompter(),
    )
    ctx = pipeline.start(task="t", slug="task")
    pipeline.step(ctx)
    assert _stage_box_checked(state, ctx, "research")
