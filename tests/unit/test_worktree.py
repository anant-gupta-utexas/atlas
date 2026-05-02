"""Unit tests for WorktreeManager (subprocess mocks)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas.worktree import WorktreeError, WorktreeManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLUG = "my-task"
_RUN_ID = "abcdef01" * 4  # 32 chars
_SHORT_ID = "abcdef01"


def _manager(tmp_path: Path) -> WorktreeManager:
    return WorktreeManager(tmp_path)


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# T3.1 — path containment
# ---------------------------------------------------------------------------


def test_create_path_is_always_under_worktrees_dir(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    expected_prefix = tmp_path / ".atlas" / "worktrees"

    with patch("atlas.worktree.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        path = mgr.create(slug=_SLUG, run_id=_RUN_ID)

    assert str(path).startswith(str(expected_prefix))


def test_create_path_encodes_slug_and_short_run_id(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)

    with patch("atlas.worktree.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        path = mgr.create(slug=_SLUG, run_id=_RUN_ID)

    assert path.name == f"{_SLUG}-{_SHORT_ID}"


def test_create_raises_on_path_collision(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    collision = tmp_path / ".atlas" / "worktrees" / f"{_SLUG}-{_SHORT_ID}"
    collision.mkdir(parents=True)

    with pytest.raises(WorktreeError, match="already exists"):
        mgr.create(slug=_SLUG, run_id=_RUN_ID)


def test_create_raises_on_dirty_repo(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)

    with patch("atlas.worktree.subprocess.run") as mock_run:
        # git status returns dirty output
        mock_run.return_value = _completed(stdout=" M some_file.py")
        with pytest.raises(WorktreeError, match="uncommitted changes to tracked files"):
            mgr.create(slug=_SLUG, run_id=_RUN_ID)


def test_create_raises_when_git_worktree_add_fails(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "status" in args:
            return _completed(stdout="")  # clean repo
        return _completed(returncode=1, stderr="some git error")

    with patch("atlas.worktree.subprocess.run", side_effect=side_effect):
        with pytest.raises(WorktreeError, match="git worktree add failed"):
            mgr.create(slug=_SLUG, run_id=_RUN_ID)


def test_create_uses_list_form_subprocess(tmp_path: Path) -> None:
    """subprocess.run must never be called with a string (no shell=True)."""
    mgr = _manager(tmp_path)

    calls: list[list[str]] = []

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        calls.append(args)
        if "status" in args:
            return _completed(stdout="")
        return _completed()

    with patch("atlas.worktree.subprocess.run", side_effect=side_effect):
        mgr.create(slug=_SLUG, run_id=_RUN_ID)

    for call_args in calls:
        assert isinstance(call_args, list), "subprocess.run must use list-form args"


# ---------------------------------------------------------------------------
# T3.1 — cleanup
# ---------------------------------------------------------------------------


def test_cleanup_calls_git_worktree_remove(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    worktree_path = tmp_path / ".atlas" / "worktrees" / f"{_SLUG}-{_SHORT_ID}"
    worktree_path.mkdir(parents=True)

    with patch("atlas.worktree.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        mgr.cleanup(worktree_path)

    calls = [list(c.args[0]) for c in mock_run.call_args_list]
    assert any("worktree" in c and "remove" in c for c in calls)


def test_cleanup_is_noop_when_path_absent(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    absent_path = tmp_path / ".atlas" / "worktrees" / "nonexistent"

    with patch("atlas.worktree.subprocess.run") as mock_run:
        mgr.cleanup(absent_path)  # should not raise
        mock_run.assert_not_called()


def test_cleanup_raises_on_remove_failure(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    worktree_path = tmp_path / ".atlas" / "worktrees" / f"{_SLUG}-{_SHORT_ID}"
    worktree_path.mkdir(parents=True)

    with patch("atlas.worktree.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=1, stderr="cannot remove")
        with pytest.raises(WorktreeError, match="git worktree remove failed"):
            mgr.cleanup(worktree_path)


def test_cleanup_does_not_touch_main(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    worktree_path = tmp_path / ".atlas" / "worktrees" / f"{_SLUG}-{_SHORT_ID}"
    worktree_path.mkdir(parents=True)

    with patch("atlas.worktree.subprocess.run") as mock_run:
        mock_run.return_value = _completed()
        mgr.cleanup(worktree_path)

    all_args = [c.args[0] for c in mock_run.call_args_list]
    for args in all_args:
        assert "checkout" not in args, "cleanup must never git checkout"
        assert "main" not in args, "cleanup must never reference main"


# ---------------------------------------------------------------------------
# T3.1 — path containment guard
# ---------------------------------------------------------------------------


def test_assert_path_contained_blocks_escape(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    escape_path = tmp_path / "outside"
    escape_path.mkdir()

    with pytest.raises(WorktreeError, match="not under"):
        mgr.cleanup(escape_path)


# ---------------------------------------------------------------------------
# T3.1 — merge_back
# ---------------------------------------------------------------------------


def test_merge_back_success(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    worktree_path = tmp_path / ".atlas" / "worktrees" / f"{_SLUG}-{_SHORT_ID}"
    worktree_path.mkdir(parents=True)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "--abbrev-ref" in args:
            return _completed(stdout="atlas/my-task-abcdef01")
        if "merge" in args:
            return _completed()
        if "commit" in args:
            return _completed()
        return _completed()

    with patch("atlas.worktree.subprocess.run", side_effect=side_effect):
        mgr.merge_back(worktree_path)  # should not raise


def test_merge_back_raises_when_branch_lookup_fails(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    worktree_path = tmp_path / ".atlas" / "worktrees" / f"{_SLUG}-{_SHORT_ID}"
    worktree_path.mkdir(parents=True)

    with patch("atlas.worktree.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=1, stderr="bad ref")
        with pytest.raises(WorktreeError, match="Could not determine worktree branch"):
            mgr.merge_back(worktree_path)


def test_merge_back_raises_on_merge_conflict(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    worktree_path = tmp_path / ".atlas" / "worktrees" / f"{_SLUG}-{_SHORT_ID}"
    worktree_path.mkdir(parents=True)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "--abbrev-ref" in args:
            return _completed(stdout="atlas/my-task-abcdef01")
        return _completed(returncode=1, stderr="CONFLICT")

    with patch("atlas.worktree.subprocess.run", side_effect=side_effect):
        with pytest.raises(WorktreeError, match="git merge --squash failed"):
            mgr.merge_back(worktree_path)


def test_merge_back_raises_on_commit_failure(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    worktree_path = tmp_path / ".atlas" / "worktrees" / f"{_SLUG}-{_SHORT_ID}"
    worktree_path.mkdir(parents=True)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "--abbrev-ref" in args:
            return _completed(stdout="atlas/my-task-abcdef01")
        if "merge" in args:
            return _completed()
        return _completed(returncode=1, stderr="nothing to commit")

    with patch("atlas.worktree.subprocess.run", side_effect=side_effect):
        with pytest.raises(WorktreeError, match="git commit failed"):
            mgr.merge_back(worktree_path)
