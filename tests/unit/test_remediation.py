"""Unit tests for T5.1 remediation fixes (T1–T11)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas.orchestrator import (
    AwaitingHookExceededError,
    GateDecision,
    Pipeline,
    RunContext,
    StageOutcome,
    _find_atlas_root,
)
from atlas.plumb_io import PlumbIO
from atlas.stages import StageSpec
from atlas.state import StateStore
from atlas.workflow_loader import load_workflow_file

_DEV_YAML_PATH = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"
STAGES = load_workflow_file(_DEV_YAML_PATH).stages

# ---------------------------------------------------------------------------
# Shared fakes (mirror test_pipeline.py)
# ---------------------------------------------------------------------------


class _FakeRunner:
    def __init__(self, outcomes: list[StageOutcome] | None = None) -> None:
        self._outcomes = list(outcomes or [])
        self._idx = 0

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        if self._outcomes and self._idx < len(self._outcomes):
            outcome = self._outcomes[self._idx]
            self._idx += 1
            return outcome
        return StageOutcome(
            stage=stage,
            span_id="",
            status="success",
            output_text=f"output of {stage.name}",
            error_type=None,
        )


class _FakePrompter:
    def ask(self, *, stage: StageSpec, gate_index: int) -> GateDecision:
        return GateDecision(label="approved", turn_count=1, reason=None)


def _make_pipeline(
    tmp_path: Path,
    *,
    runner: _FakeRunner | None = None,
    prompter: _FakePrompter | None = None,
    commit_wait_timeout_s: int = 0,
) -> tuple[Pipeline, PlumbIO, StateStore]:
    plumb = PlumbIO(real=False)
    state = StateStore(tmp_path)
    pipeline = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb,
        runner=runner or _FakeRunner(),
        prompter=prompter or _FakePrompter(),
        commit_wait_timeout_s=commit_wait_timeout_s,
    )
    return pipeline, plumb, state


# ---------------------------------------------------------------------------
# T1 — state: code_gen_span_id round-trip
# ---------------------------------------------------------------------------


def test_current_run_round_trip_with_span_id(tmp_path):
    store = StateStore(tmp_path)
    store.write_current_run("run123", "my-slug", code_gen_span_id="spanABC")
    result = store.read_current_run_with_worktree()
    assert result is not None
    run_id, slug, worktree_path, code_gen_span_id = result
    assert run_id == "run123"
    assert slug == "my-slug"
    assert worktree_path is None
    assert code_gen_span_id == "spanABC"


def test_current_run_round_trip_with_worktree_and_span_id(tmp_path):
    store = StateStore(tmp_path)
    wt = tmp_path / "worktree"
    store.write_current_run("run456", "slug2", worktree_path=wt, code_gen_span_id="spanXYZ")
    result = store.read_current_run_with_worktree()
    assert result is not None
    run_id, slug, worktree_path, code_gen_span_id = result
    assert worktree_path == wt
    assert code_gen_span_id == "spanXYZ"


def test_current_run_no_span_id_returns_none(tmp_path):
    store = StateStore(tmp_path)
    store.write_current_run("run789", "slug3")
    result = store.read_current_run_with_worktree()
    assert result is not None
    _, _, _, code_gen_span_id = result
    assert code_gen_span_id is None


def test_current_run_two_line_file_is_backward_compatible(tmp_path):
    """Old two-line current-run files should still parse correctly."""
    store = StateStore(tmp_path)
    atlas_dir = tmp_path / ".atlas"
    atlas_dir.mkdir()
    (atlas_dir / "current-run").write_text("run_old\nslug_old\n")
    result = store.read_current_run_with_worktree()
    assert result is not None
    run_id, slug, worktree_path, code_gen_span_id = result
    assert run_id == "run_old"
    assert slug == "slug_old"
    assert worktree_path is None
    assert code_gen_span_id is None


# ---------------------------------------------------------------------------
# P0-3 — resume rehydrates _last_code_gen_span_id
# ---------------------------------------------------------------------------


def test_resume_rehydrates_code_gen_span_id(tmp_path):
    pipeline, plumb, state = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="my task", slug="my-slug")

    # Run up through code_gen (stage 5) — awaiting_hook
    for _ in range(6):
        pipeline.step(ctx)

    # The span_id for code_gen should be persisted in current-run line 4.
    result = state.read_current_run_with_worktree()
    assert result is not None
    _, _, _, persisted_span_id = result
    assert persisted_span_id is not None
    assert len(persisted_span_id) > 0

    # Simulate fresh process (new Pipeline instance)
    plumb2 = PlumbIO(real=False)
    pipeline2 = Pipeline(
        repo_root=tmp_path,
        state=state,
        plumb=plumb2,
        runner=_FakeRunner(),
        prompter=_FakePrompter(),
        commit_wait_timeout_s=0,
    )
    pipeline2.resume()
    assert pipeline2._last_code_gen_span_id == persisted_span_id


# ---------------------------------------------------------------------------
# P0-2 — PlumbIO.reopen_run in stub mode
# ---------------------------------------------------------------------------


def test_reopen_run_stub_preserves_run_id(tmp_path):
    plumb = PlumbIO(real=False)
    original_run_id = plumb.open_run(task="test task")
    plumb._closed = True  # simulate earlier close

    returned = plumb.reopen_run(original_run_id)
    assert returned == original_run_id
    assert plumb._run_id == original_run_id
    assert plumb._closed is False


def test_reopen_run_allows_record_span_after_reopen(tmp_path):
    plumb = PlumbIO(real=False)
    run_id = plumb.open_run(task="test task")
    plumb._closed = True

    plumb.reopen_run(run_id)
    span_id = plumb.record_span(
        run_id=run_id,
        kind="agent",
        name="code_gen",
        status="success",
        latency_ms=0.0,
        error_type=None,
    )
    assert len(span_id) > 0
    assert any(s["run_id"] == run_id for s in plumb.spans)


# ---------------------------------------------------------------------------
# P0-1 — _wait_for_commit_score and blocking in run_to_completion
# ---------------------------------------------------------------------------


def test_wait_for_commit_score_returns_true_when_record_present(tmp_path):
    pipeline, _, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="test", slug="slug")

    atlas_dir = tmp_path / ".atlas"
    atlas_dir.mkdir(exist_ok=True)
    pending = atlas_dir / "pending-scores.jsonl"
    record = {"run_id": ctx.run_id, "metric": "gate_commit", "value_label": "approved"}
    pending.write_text(json.dumps(record) + "\n")

    result = pipeline._wait_for_commit_score(run_id=ctx.run_id, timeout_s=5, poll_interval_s=0.05)
    assert result is True


def test_wait_for_commit_score_returns_false_on_timeout(tmp_path):
    pipeline, _, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="test", slug="slug")

    result = pipeline._wait_for_commit_score(run_id=ctx.run_id, timeout_s=0, poll_interval_s=0.05)
    assert result is False


def test_wait_for_commit_score_ignores_different_run_id(tmp_path):
    pipeline, _, _ = _make_pipeline(tmp_path)
    ctx = pipeline.start(task="test", slug="slug")

    atlas_dir = tmp_path / ".atlas"
    atlas_dir.mkdir(exist_ok=True)
    pending = atlas_dir / "pending-scores.jsonl"
    record = {"run_id": "some-other-run-id", "metric": "gate_commit", "value_label": "approved"}
    pending.write_text(json.dumps(record) + "\n")

    result = pipeline._wait_for_commit_score(run_id=ctx.run_id, timeout_s=0, poll_interval_s=0.05)
    assert result is False


def test_run_to_completion_blocks_on_awaiting_hook_then_continues(tmp_path):
    """run_to_completion picks up a pending-scores record written while it polls."""
    import threading

    pipeline, plumb, state = _make_pipeline(tmp_path, commit_wait_timeout_s=5)
    atlas_dir = tmp_path / ".atlas"
    atlas_dir.mkdir(exist_ok=True)

    ctx = pipeline.start(task="test", slug="slug")

    # Write the pending-scores file from a background thread after a short delay,
    # simulating the post-commit hook firing while run_to_completion is waiting.
    def _write_score() -> None:
        time.sleep(0.15)
        record = {"run_id": ctx.run_id, "metric": "gate_commit", "value_label": "approved"}
        (atlas_dir / "pending-scores.jsonl").write_text(json.dumps(record) + "\n")

    # Patch poll_interval_s to 0.05 so the test finishes quickly.
    original_wait = pipeline._wait_for_commit_score

    def _fast_wait(*, run_id: str, timeout_s: int) -> bool:
        return original_wait(run_id=run_id, timeout_s=timeout_s, poll_interval_s=0.05)

    pipeline._wait_for_commit_score = _fast_wait  # type: ignore[method-assign]

    t = threading.Thread(target=_write_score, daemon=True)
    t.start()

    returned_ctx = pipeline.run_to_completion(ctx)
    t.join(timeout=2)

    assert returned_ctx.run_id == ctx.run_id
    # All 7 stages should complete and the run should be closed successfully.
    assert len(plumb.spans) == 7


def test_run_to_completion_awaiting_hook_timeout_returns_ctx(tmp_path):
    """On timeout (no pending-scores), run_to_completion returns ctx without closing the run."""
    pipeline, plumb, _ = _make_pipeline(tmp_path, commit_wait_timeout_s=0)
    ctx = pipeline.start(task="test", slug="slug")
    returned_ctx = pipeline.run_to_completion(ctx)
    assert returned_ctx.run_id == ctx.run_id
    # Run should NOT be closed (no close_run call changes _closed)
    assert not plumb._closed


# ---------------------------------------------------------------------------
# P1-2 — AwaitingHookExceededError after too many awaiting_hook attempts
# ---------------------------------------------------------------------------


def test_awaiting_hook_attempt_cap_raises(tmp_path):
    """If awaiting_hook repeats more than _AWAITING_HOOK_MAX_ATTEMPTS times, raise."""
    pipeline, plumb, _ = _make_pipeline(tmp_path, commit_wait_timeout_s=0)
    ctx = pipeline.start(task="test", slug="slug")

    fake_outcome = StageOutcome(
        stage=STAGES[5],
        span_id="span-fake",
        status="awaiting_hook",
        output_text="",
        error_type=None,
    )

    # Patch both step (always returns awaiting_hook) and _wait_for_commit_score
    # (always returns True, so the loop doesn't exit on timeout — it keeps going
    # until the attempt cap is hit).
    with (
        patch.object(pipeline, "step", return_value=fake_outcome),
        patch.object(pipeline, "_wait_for_commit_score", return_value=True),
    ):
        with pytest.raises(AwaitingHookExceededError):
            pipeline.run_to_completion(ctx)


# ---------------------------------------------------------------------------
# P1-1 — close_run logs on failure; no swallowed __exit__
# ---------------------------------------------------------------------------


def test_close_run_logs_on_exit_exception(tmp_path, caplog):
    import logging

    plumb = PlumbIO(real=False)
    plumb._real = True  # trick to exercise the real path
    mock_ctx = MagicMock()
    mock_ctx.__exit__ = MagicMock(side_effect=RuntimeError("plumb failure"))
    plumb._run_ctx = mock_ctx

    with caplog.at_level(logging.WARNING, logger="atlas.plumb"):
        plumb.close_run(run_id="test-run", status="success")

    assert any("close_run" in r.message for r in caplog.records)


def test_close_run_noop_when_already_closed(tmp_path):
    plumb = PlumbIO(real=False)
    plumb._closed = True
    plumb.close_run(run_id="run1", status="success")  # must not raise


# ---------------------------------------------------------------------------
# P1-3 — _find_atlas_root raises outside a git repo
# ---------------------------------------------------------------------------


def test_find_atlas_root_outside_git_raises(tmp_path, monkeypatch):
    """When no .git directory exists above __file__, _find_atlas_root must raise."""
    import atlas.orchestrator as orch_module

    fake_file = tmp_path / "fake" / "src" / "atlas" / "orchestrator.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.touch()

    monkeypatch.setattr(orch_module, "__file__", str(fake_file))

    with pytest.raises(RuntimeError, match="git checkout"):
        _find_atlas_root()


# ---------------------------------------------------------------------------
# P2-1 — hook install warns when interpreter is outside repo venv
# ---------------------------------------------------------------------------


def test_hook_install_warns_outside_repo_venv(tmp_path, capsys):
    from atlas.post_commit_hook import install

    (tmp_path / ".git" / "hooks").mkdir(parents=True)

    # Use a python path outside the repo's .venv
    with patch("sys.executable", "/usr/local/bin/python3"):
        install(tmp_path)

    captured = capsys.readouterr()
    assert "Warning" in captured.err


def test_hook_install_no_warning_inside_repo_venv(tmp_path, capsys):
    from atlas.post_commit_hook import install

    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()

    with patch("sys.executable", str(venv_python)):
        install(tmp_path)

    captured = capsys.readouterr()
    assert "Warning" not in captured.err


def test_hook_script_contains_fallback(tmp_path):
    from atlas.post_commit_hook import _hook_script

    script = _hook_script("/usr/local/bin/python3")
    assert "command -v" in script
    assert "python3 -m atlas.post_commit_hook" in script
    assert "Re-run" in script


# ---------------------------------------------------------------------------
# P2-2 — flush_pending_scores logs on different run_id
# ---------------------------------------------------------------------------


def test_flush_pending_scores_logs_different_run_id(tmp_path, caplog):
    import logging

    plumb = PlumbIO(real=False)
    plumb.open_run(task="task")

    pending = tmp_path / "pending-scores.jsonl"
    record = {"run_id": "other-run", "metric": "gate_commit", "value_label": "approved"}
    pending.write_text(json.dumps(record) + "\n")

    with caplog.at_level(logging.INFO, logger="atlas.plumb"):
        flushed = plumb.flush_pending_scores(
            run_id="active-run", pending_path=pending, span_id="span1"
        )

    assert flushed == 0
    assert any("other-run" in r.message for r in caplog.records)
    # Record must be kept
    assert pending.exists()
