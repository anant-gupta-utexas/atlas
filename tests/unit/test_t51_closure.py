"""Regression tests for the T5.1-closure fixes (2026-05-06).

Each test pins one of the open findings called out in
``dev/active/atlas-pipeline-trs/T5.1-implementation-findings.md`` /
``atlas-pipeline-trs-review-findings-2026-05-06.md``:

- P0  resume aligns to plumb child-run handoff and propagates active run id
- P1  original task text persists across resume (not slug header)
- P1  rejection examples are durably written through plumb storage adapter
- P2  post-commit hook dedupes (run_id, commit_sha, metric) replays
- P2  Pipeline.step() emits real latency_ms (not the 0.0 placeholder)
- P2  run_to_completion() picks up worktree_path mutation from step()
- P2  GateDecision.reason flows through PlumbIO.record_user_signal into
       RunHandle.add_score(rationale=...)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from atlas.orchestrator import (
    GateDecision,
    Pipeline,
    RunContext,
    StageOutcome,
)
from atlas.plumb_io import PlumbIO
from atlas.stages import STAGES, StageName, StageSpec
from atlas.state import StateStore

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _CapturingRunner:
    def __init__(self) -> None:
        self.calls: list[RunContext] = []

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        self.calls.append(ctx)
        status = "awaiting_hook" if stage.name == StageName.CODE_GEN else "success"
        return StageOutcome(
            stage=stage,
            span_id="",
            status=status,
            output_text="",
            error_type=None,
        )


class _ApprovePrompter:
    def ask(self, *, stage: StageSpec, gate_index: int) -> GateDecision:
        return GateDecision(label="approved", turn_count=1, reason=None)


class _StubWorktree:
    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self.created: list[Path] = []

    def create(self, *, slug: str, run_id: str) -> Path:
        path = self._repo_root / ".atlas" / "worktrees" / f"{slug}-{run_id[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.created.append(path)
        return path


# ---------------------------------------------------------------------------
# P0 — resume child-run handoff propagates active run id
# ---------------------------------------------------------------------------


def test_reopen_run_real_mode_uses_parent_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In real mode, reopen_run must call plumb_run with parent_run_id and
    return the new (child) run id, not the original."""
    plumb = PlumbIO(real=False)
    plumb._real = True

    fake_handle = MagicMock()
    fake_handle.run_id = "child-run-id-2"
    fake_ctx = MagicMock()
    fake_ctx.__enter__ = MagicMock(return_value=fake_handle)

    captured: dict[str, object] = {}

    def fake_run(**kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return fake_ctx

    import atlas.plumb_io as plumb_io

    # plumb_run only exists when plumb is importable; inject a stub either way.
    monkeypatch.setattr(plumb_io, "plumb_run", fake_run, raising=False)
    returned = plumb.reopen_run("parent-run-id-1")

    assert returned == "child-run-id-2", "reopen_run must return the child id"
    assert captured.get("parent_run_id") == "parent-run-id-1"
    assert captured.get("kind") == "online"
    assert plumb._parent_run_id == "parent-run-id-1"


def test_resume_propagates_new_active_run_id_into_state(tmp_path: Path) -> None:
    """When the handoff returns a new run id, atlas state must be updated so
    subsequent step()s consistently use the active (child) run id."""
    state = StateStore(tmp_path)
    plumb = PlumbIO(real=False)
    runner = _CapturingRunner()
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=runner,
        prompter=_ApprovePrompter(),
        worktree=_StubWorktree(tmp_path),  # type: ignore[arg-type]
    )

    _ = pipeline.start(task="my real task", slug="t1")

    # Simulate the handoff by stubbing reopen_run to return a different id.
    new_id = "newchild" + "0" * 24
    plumb.reopen_run = lambda run_id: new_id  # type: ignore[assignment]

    resumed = pipeline.resume()

    assert resumed.run_id == new_id, "resume must propagate the new run id"
    # tasks.md run_id comment should now reference the child id.
    tasks_md = (tmp_path / "dev" / "active" / "t1" / "tasks.md").read_text()
    assert new_id in tasks_md, "tasks.md must be rewritten with the new run_id"
    # .atlas/current-run line 1 must be the new id.
    persisted = state.read_current_run_with_worktree()
    assert persisted is not None and persisted[0] == new_id


# ---------------------------------------------------------------------------
# P1 — original task text persists and is restored on resume
# ---------------------------------------------------------------------------


def test_original_task_text_persists_to_tasks_md(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    ctx = RunContext(
        run_id="aa" * 16,
        slug="t1",
        task="Add response-cache middleware to the Flask repo",
        repo_root=tmp_path,
    )
    state.create_tasks_md(ctx)

    assert state.read_task_text("t1") == ctx.task


def test_resume_rehydrates_original_task_not_slug(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=PlumbIO(real=False),
        runner=_CapturingRunner(),
        prompter=_ApprovePrompter(),
        worktree=_StubWorktree(tmp_path),  # type: ignore[arg-type]
    )

    original_task = "implement: feature with multi-line\ndescription text"
    pipeline.start(task=original_task, slug="my-slug")

    resumed = pipeline.resume()

    assert resumed.task == original_task, "resume must restore the original task text, not the slug"
    assert resumed.task != "my-slug"


# ---------------------------------------------------------------------------
# P1 — rejection examples persist through plumb storage adapter in real mode
# ---------------------------------------------------------------------------


def test_write_example_real_mode_calls_plumb_storage_writer() -> None:
    import atlas.plumb_io as plumb_io

    if not plumb_io._PLUMB_AVAILABLE:
        pytest.skip("plumb not importable in this env")

    plumb = PlumbIO(real=False)
    plumb._real = True

    fake_writer = MagicMock()
    fake_module = MagicMock()
    fake_module._storage_writer = fake_writer
    original = plumb_io._plumb_module
    try:
        plumb_io._plumb_module = fake_module
        plumb.write_example(
            run_id="aa" * 16,
            span_id="bb" * 16,
            inputs="rejected output payload",
            expected=None,
        )
    finally:
        plumb_io._plumb_module = original

    assert fake_writer.write_example.called, (
        "real-mode write_example must invoke plumb's storage writer"
    )
    written = fake_writer.write_example.call_args.args[0]
    assert written.origin_run_id == "aa" * 16


# ---------------------------------------------------------------------------
# P2 — hook idempotency: replay does not duplicate gate_commit records
# ---------------------------------------------------------------------------


def _init_main_repo_with_commit(tmp_path: Path) -> Path:
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
    return main


def test_hook_replay_does_not_duplicate_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = _init_main_repo_with_commit(tmp_path)
    (main / ".atlas").mkdir(exist_ok=True)
    (main / ".atlas" / "current-run").write_text("run-xyz\nslug\n")

    monkeypatch.chdir(main)
    from atlas.post_commit_hook import run as hook_run

    with pytest.raises(SystemExit):
        hook_run()
    with pytest.raises(SystemExit):
        hook_run()  # replay for the same HEAD commit

    pending = main / ".atlas" / "pending-scores.jsonl"
    lines = [ln for ln in pending.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, f"hook replay for the same commit must dedupe; got {len(lines)} records"


# ---------------------------------------------------------------------------
# P2 — Pipeline.step() emits real latency_ms
# ---------------------------------------------------------------------------


def test_step_records_real_latency_ms(tmp_path: Path) -> None:
    """step() should record a positive latency_ms reflecting actual runner runtime."""
    import time as time_mod

    class _SlowRunner:
        def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
            time_mod.sleep(0.01)
            return StageOutcome(
                stage=stage,
                span_id="",
                status="success",
                output_text="",
                error_type=None,
            )

    state = StateStore(tmp_path)
    plumb = PlumbIO(real=False)
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=_SlowRunner(),
        prompter=_ApprovePrompter(),
    )
    ctx = pipeline.start(task="t", slug="t1")
    pipeline.step(ctx)

    assert plumb.spans, "step() should have recorded a span"
    latency = plumb.spans[0]["latency_ms"]
    assert latency > 0.0, f"latency_ms must be > 0, got {latency}"


# ---------------------------------------------------------------------------
# P2 — run_to_completion uses worktree-bearing ctx for stage 6
# ---------------------------------------------------------------------------


def test_run_to_completion_uses_worktree_for_code_review(tmp_path: Path) -> None:
    """Stage 6 (code_review) must run with worktree_path set even when
    run_to_completion is invoked from a single same-process loop."""
    state = StateStore(tmp_path)
    runner = _CapturingRunner()
    worktree = _StubWorktree(tmp_path)
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=PlumbIO(real=False),
        runner=runner,
        prompter=_ApprovePrompter(),
        worktree=worktree,  # type: ignore[arg-type]
        commit_wait_timeout_s=2,
    )
    ctx = pipeline.start(task="t", slug="t1")

    # Drive stages 0-5 (5 returns awaiting_hook, satisfied by writing the
    # hook record before each wait).  We arrange for the hook record to
    # already be present so _wait_for_commit_score returns True immediately.
    atlas_dir = tmp_path / ".atlas"
    atlas_dir.mkdir(exist_ok=True)

    # Pre-write a permanent pending-scores record. flush_pending_scores
    # consumes it on the *next* step (stage 6), so we leave it in place
    # until then.
    record = {"run_id": ctx.run_id, "metric": "gate_commit", "value_label": "approved"}
    (atlas_dir / "pending-scores.jsonl").write_text(json.dumps(record) + "\n")

    pipeline.run_to_completion(ctx)

    # The last call into the runner must have been stage 6 (code_review).
    last_call = runner.calls[-1]
    last_stage = STAGES[6]
    assert last_stage.name == StageName.CODE_REVIEW
    assert last_call.worktree_path == worktree.created[0], (
        "code_review must run with the worktree_path set, not fall back to repo_root"
    )


# ---------------------------------------------------------------------------
# P2 — rationale flows through to plumb add_score(rationale=...)
# ---------------------------------------------------------------------------


def test_record_user_signal_forwards_rationale_in_real_mode() -> None:
    plumb = PlumbIO(real=False)
    plumb._real = True
    fake_handle = MagicMock()
    fake_handle.add_score = MagicMock(return_value="score-id")
    plumb._run_handle = fake_handle

    decision = GateDecision(label="approved", turn_count=1, reason="LGTM, looks good to me")
    plumb.record_user_signal(
        run_id="aa" * 16,
        span_id="bb" * 16,
        metric="gate_research",
        decision=decision,
    )

    assert fake_handle.add_score.called
    kwargs = fake_handle.add_score.call_args.kwargs
    assert kwargs.get("rationale") == "LGTM, looks good to me", (
        "decision.reason must be forwarded as rationale to plumb.add_score"
    )
    assert kwargs.get("value_label") == "approved"
