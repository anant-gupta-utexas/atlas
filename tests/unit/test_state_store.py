"""Unit tests for atlas.state.StateStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.orchestrator import RunContext
from atlas.state import StateInconsistencyError, StateStore
from atlas.workflow_loader import load_workflow_file

_DEV_YAML_PATH = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"
STAGES = load_workflow_file(_DEV_YAML_PATH).stages


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
    store.create_tasks_md(ctx, stages=STAGES)

    path = tmp_path / "dev" / "active" / ctx.slug / "tasks.md"
    assert path.exists()
    content = path.read_text()
    assert "<!-- run_id:" in content
    assert ctx.run_id in content


def test_create_tasks_md_has_seven_unchecked_boxes(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)

    path = tmp_path / "dev" / "active" / ctx.slug / "tasks.md"
    content = path.read_text()
    unchecked = content.count("- [ ]")
    assert unchecked == 7, f"Expected 7 unchecked boxes, found {unchecked}"


def test_create_tasks_md_has_current_block(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)

    path = tmp_path / "dev" / "active" / ctx.slug / "tasks.md"
    content = path.read_text()
    assert "## current" in content
    assert "phase:" in content


def test_create_tasks_md_includes_workflow_field(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES, workflow_name="dev")

    assert store.read_workflow_name(ctx.slug) == "dev"


def test_create_tasks_md_workflow_field_defaults_to_dev(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)

    assert store.read_workflow_name(ctx.slug) == "dev"


def test_create_tasks_md_custom_workflow_name(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES, workflow_name="job")

    assert store.read_workflow_name(ctx.slug) == "job"


def test_update_current_block_preserves_workflow_field(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES, workflow_name="job")

    store.update_current_block(ctx, phase="prd_draft", gate="gate_research", next_action="x")
    assert store.read_workflow_name(ctx.slug) == "job"


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
# Phase L4 (T-L4.3) — per-run-keyed current-run
# ---------------------------------------------------------------------------


def test_write_and_list_current_runs_keyed(tmp_path):
    store = _make_store(tmp_path)
    store.write_current_run_keyed("run-a", "slug-a")
    store.write_current_run_keyed("run-b", "slug-b")

    runs = store.list_current_runs()
    assert {(r[0], r[1]) for r in runs} == {("run-a", "slug-a"), ("run-b", "slug-b")}


def test_list_current_runs_empty_when_no_runs_dir(tmp_path):
    store = _make_store(tmp_path)
    assert store.list_current_runs() == []


def test_delete_current_run_keyed_removes_only_that_run(tmp_path):
    store = _make_store(tmp_path)
    store.write_current_run_keyed("run-a", "slug-a")
    store.write_current_run_keyed("run-b", "slug-b")

    store.delete_current_run_keyed("run-a")

    runs = store.list_current_runs()
    assert [r[0] for r in runs] == ["run-b"]


def test_delete_current_run_keyed_is_noop_when_absent(tmp_path):
    store = _make_store(tmp_path)
    store.delete_current_run_keyed("nope")  # must not raise


def test_keyed_current_run_carries_worktree_and_span(tmp_path):
    store = _make_store(tmp_path)
    wt = tmp_path / "wt"
    store.write_current_run_keyed("run-a", "slug-a", wt, code_gen_span_id="span-1")

    runs = store.list_current_runs()
    assert runs == [("run-a", "slug-a", wt, "span-1")]


def test_attended_singleton_untouched_by_keyed_writes(tmp_path):
    """Decision #3: the singleton path and the keyed path are independent —
    writing a keyed run must not create or alter .atlas/current-run."""
    store = _make_store(tmp_path)
    store.write_current_run_keyed("run-a", "slug-a")

    assert store.read_current_run() is None


def test_assert_consistent_keyed_passes_on_valid_state(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)
    store.write_current_run_keyed(ctx.run_id, ctx.slug)

    store.assert_consistent(ctx, keyed=True)  # must not raise


def test_assert_consistent_keyed_raises_when_keyed_file_absent(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)
    # No write_current_run_keyed call, and no singleton either — the keyed
    # check must not fall back to the singleton file.
    store.write_current_run(ctx.run_id, ctx.slug)

    with pytest.raises(StateInconsistencyError):
        store.assert_consistent(ctx, keyed=True)


# ---------------------------------------------------------------------------
# first_unchecked
# ---------------------------------------------------------------------------


def test_first_unchecked_returns_research_on_new_run(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)

    assert store.first_unchecked(ctx) == "research"


def test_first_unchecked_advances_after_check_box(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)

    store.check_box(ctx, "research")
    assert store.first_unchecked(ctx) == "prd_draft"


def test_first_unchecked_returns_none_when_all_checked(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)

    for stage in STAGES:
        store.check_box(ctx, stage.name)

    assert store.first_unchecked(ctx) is None


# ---------------------------------------------------------------------------
# assert_consistent
# ---------------------------------------------------------------------------


def test_assert_consistent_passes_on_valid_state(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)
    store.write_current_run(ctx.run_id, ctx.slug)

    store.assert_consistent(ctx)  # must not raise


def test_assert_consistent_raises_on_run_id_mismatch(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path, run_id="aabbccdd" * 4)
    store.create_tasks_md(ctx, stages=STAGES)

    other_run_id = "11223344" * 4
    store.write_current_run(other_run_id, ctx.slug)

    with pytest.raises(StateInconsistencyError) as exc_info:
        store.assert_consistent(ctx)

    msg = str(exc_info.value)
    assert "aabbccdd" * 4 in msg or other_run_id in msg


def test_assert_consistent_message_contains_both_run_ids(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path, run_id="aabbccdd" * 4)
    store.create_tasks_md(ctx, stages=STAGES)

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
    store.create_tasks_md(ctx, stages=STAGES)

    tmp_file = tmp_path / "dev" / "active" / ctx.slug / "tasks.md.tmp"
    assert not tmp_file.exists(), ".tmp file should not remain after atomic write"


# ---------------------------------------------------------------------------
# Edge cases — coverage for uncovered branches
# ---------------------------------------------------------------------------


def test_read_current_run_returns_none_when_file_absent(tmp_path):
    store = _make_store(tmp_path)
    assert store.read_current_run() is None


def test_read_current_run_returns_none_when_file_has_one_line(tmp_path):
    store = _make_store(tmp_path)
    atlas_dir = tmp_path / ".atlas"
    atlas_dir.mkdir()
    (atlas_dir / "current-run").write_text("only-one-line\n")
    assert store.read_current_run() is None


def test_delete_current_run_is_noop_when_absent(tmp_path):
    store = _make_store(tmp_path)
    store.delete_current_run()  # should not raise


def test_first_unchecked_returns_any_unchecked_label(tmp_path):
    """first_unchecked is now purely structural (str, not a validated enum):
    it returns whatever checkbox label appears first, without validating it
    against a closed stage-name set (NFR-6) — any label is valid by
    construction since it only ever reads back what create_tasks_md wrote."""
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)

    path = tmp_path / "dev" / "active" / ctx.slug / "tasks.md"
    content = path.read_text()
    content = content.replace("- [ ] research", "- [ ] unknown_stage\n- [ ] research")
    path.write_text(content)

    result = store.first_unchecked(ctx)
    assert result == "unknown_stage"


def test_assert_consistent_raises_when_tasks_md_has_no_run_id_comment(tmp_path):
    store = _make_store(tmp_path)
    ctx = _make_ctx(tmp_path)
    store.create_tasks_md(ctx, stages=STAGES)
    store.write_current_run(ctx.run_id, ctx.slug)

    # Remove the run_id comment from tasks.md
    path = tmp_path / "dev" / "active" / ctx.slug / "tasks.md"
    content = path.read_text()
    content = "\n".join(line for line in content.splitlines() if "run_id:" not in line)
    path.write_text(content)

    with pytest.raises(StateInconsistencyError, match="no run_id comment"):
        store.assert_consistent(ctx)
