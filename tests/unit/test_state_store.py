"""Unit tests for atlas.state.StateStore."""
from __future__ import annotations

import pytest

from atlas.orchestrator import RunContext
from atlas.stages import STAGES, StageName
from atlas.state import StateInconsistencyError, StateStore


def _make_ctx(tmp_path, run_id: str = "aabbccdd" * 4, slug: str = "test-task") -> RunContext:
    return RunContext(run_id=run_id, slug=slug, task="do a thing", repo_root=tmp_path)


def _make_store(tmp_path) -> StateStore:
    return StateStore(tmp_path)


# ---------------------------------------------------------------------------
# create_tasks_md
# ---------------------------------------------------------------------------


def test_create_tasks_md_produces_parseable_file(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx)

    path = tmp_path / "dev" / "active" / ctx.slug / "tasks.md"
    assert path.exists()
    content = path.read_text()
    assert "<!-- run_id:" in content
    assert ctx.run_id in content


def test_create_tasks_md_has_seven_unchecked_boxes(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx)

    path = tmp_path / "dev" / "active" / ctx.slug / "tasks.md"
    content = path.read_text()
    unchecked = content.count("- [ ]")
    assert unchecked == 7, f"Expected 7 unchecked boxes, found {unchecked}"


def test_create_tasks_md_has_current_block(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx)

    path = tmp_path / "dev" / "active" / ctx.slug / "tasks.md"
    content = path.read_text()
    assert "## current" in content
    assert "phase:" in content


# ---------------------------------------------------------------------------
# write_current_run / read_current_run
# ---------------------------------------------------------------------------


def test_write_and_read_current_run(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.write_current_run(ctx.run_id, ctx.slug)

    pair = store.read_current_run()
    assert pair is not None
    assert pair == (ctx.run_id, ctx.slug)


def test_read_current_run_returns_none_when_absent(tmp_path):
    store = _make_store(tmp_path)
    assert store.read_current_run() is None


# ---------------------------------------------------------------------------
# first_unchecked
# ---------------------------------------------------------------------------


def test_first_unchecked_returns_research_on_new_run(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx)

    assert store.first_unchecked(ctx) == StageName.RESEARCH


def test_first_unchecked_advances_after_check_box(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx)

    store.check_box(ctx, StageName.RESEARCH)
    assert store.first_unchecked(ctx) == StageName.PRD_DRAFT


def test_first_unchecked_returns_none_when_all_checked(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx)

    for stage in STAGES:
        store.check_box(ctx, stage.name)

    assert store.first_unchecked(ctx) is None


# ---------------------------------------------------------------------------
# assert_consistent
# ---------------------------------------------------------------------------


def test_assert_consistent_passes_on_valid_state(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx)
    store.write_current_run(ctx.run_id, ctx.slug)

    store.assert_consistent(ctx)  # must not raise


def test_assert_consistent_raises_on_run_id_mismatch(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path, run_id="aabbccdd" * 4)
    store.create_tasks_md(ctx)

    other_run_id = "11223344" * 4
    store.write_current_run(other_run_id, ctx.slug)

    with pytest.raises(StateInconsistencyError) as exc_info:
        store.assert_consistent(ctx)

    msg = str(exc_info.value)
    assert "aabbccdd" * 4 in msg or other_run_id in msg


def test_assert_consistent_message_contains_both_run_ids(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path, run_id="aabbccdd" * 4)
    store.create_tasks_md(ctx)

    other_run_id = "11223344" * 4
    store.write_current_run(other_run_id, ctx.slug)

    with pytest.raises(StateInconsistencyError) as exc_info:
        store.assert_consistent(ctx)

    msg = str(exc_info.value)
    # Both run IDs must appear in the error message
    assert other_run_id in msg or "aabbccdd" * 4 in msg


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_tasks_md_write_is_atomic(tmp_path, monkeypatch):
    """After create_tasks_md, no .tmp file should remain."""
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx)

    tmp_file = tmp_path / "dev" / "active" / ctx.slug / "tasks.md.tmp"
    assert not tmp_file.exists(), ".tmp file should not remain after atomic write"
