"""Unit tests for atlas.deliverer — GhPrDeliverer (T-L0.6).

The push-safety test is the security-critical test of this module, mirroring
Phase 3's auth-preflight test: assert the dangerous call (main / --force)
never fires, not just that the happy path looks right.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas.deliverer import DeliveryError, GhPrDeliverer, PrRef
from atlas.worktree import WorktreeError, WorktreeManager

_BRANCH = "atlas/my-task-abcdef01"
_RUN_ID = "run-1"


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _deliverer(tmp_path: Path) -> tuple[GhPrDeliverer, MagicMock]:
    worktree = MagicMock(spec=WorktreeManager)
    return GhPrDeliverer(repo_root=tmp_path, worktree=worktree), worktree


def _is_default_branch_probe(args: list[str]) -> bool:
    """True for the read-only `git symbolic-ref` default-branch detection call.

    Added by L1 code review finding L3. It is local, read-only, and fails open,
    so tests that only care about push/PR behavior treat it as background noise
    rather than asserting on it.
    """
    return args[:2] == ["git", "symbolic-ref"]


def _no_default_branch() -> MagicMock:
    """Response for the probe when origin/HEAD is unset (detection fails open)."""
    return _completed(returncode=1, stderr="fatal: ref refs/remotes/origin/HEAD is not a ref")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_deliver_happy_path_pushes_then_creates_pr_then_cleans_up(tmp_path: Path) -> None:
    deliverer, worktree = _deliverer(tmp_path)
    worktree_path = tmp_path / "wt"

    calls: list[list[str]] = []

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if _is_default_branch_probe(args):
            return _no_default_branch()
        calls.append(args)
        if args[:2] == ["git", "push"]:
            return _completed()
        if args[:3] == ["gh", "pr", "create"]:
            return _completed(stdout="https://github.com/acme/repo/pull/42\n")
        raise AssertionError(f"unexpected subprocess call: {args}")

    with patch("atlas.deliverer.subprocess.run", side_effect=side_effect):
        result = deliverer.deliver(
            run_id=_RUN_ID,
            branch=_BRANCH,
            worktree_path=worktree_path,
            title="t",
            body="b",
        )

    assert result == PrRef(number=42, url="https://github.com/acme/repo/pull/42")
    assert calls[0] == ["git", "push", "-u", "origin", _BRANCH]
    assert calls[1][:3] == ["gh", "pr", "create"]
    worktree.cleanup.assert_called_once_with(worktree_path)


# ---------------------------------------------------------------------------
# Load-bearing security test — never main, never --force
# ---------------------------------------------------------------------------


def test_deliver_never_pushes_main_or_force(tmp_path: Path) -> None:
    """Fake subprocess.run fails the test outright if invoked with 'main' or
    '--force' anywhere in argv, for every code path this test drives."""
    deliverer, worktree = _deliverer(tmp_path)
    worktree_path = tmp_path / "wt"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        assert "main" not in args, f"dangerous call: {args}"
        assert "--force" not in args, f"dangerous call: {args}"
        if _is_default_branch_probe(args):
            return _no_default_branch()
        if args[:2] == ["git", "push"]:
            return _completed()
        if args[:3] == ["gh", "pr", "create"]:
            return _completed(stdout="https://github.com/acme/repo/pull/1\n")
        raise AssertionError(f"unexpected subprocess call: {args}")

    with patch("atlas.deliverer.subprocess.run", side_effect=side_effect):
        deliverer.deliver(
            run_id=_RUN_ID,
            branch=_BRANCH,
            worktree_path=worktree_path,
            title="t",
            body="b",
        )


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "master",
        "trunk",
        "develop",
        "Main",
        "MASTER",
        "refs/heads/main",
        "refs/heads/master",
        "  main  ",
    ],
)
def test_deliver_rejects_every_protected_branch_spelling(tmp_path: Path, branch: str) -> None:
    """L1 code review finding L3 — load-bearing, do not loosen.

    `if branch == "main"` only covered one spelling of the hazard. A repo on
    `master`, or a caller passing a fully-qualified `refs/heads/main`, would
    have sailed past a guard that reads like it prevents trunk pushes. The
    hardcoded argv shape (no --force, explicit `-u origin <branch>`) is still
    the primary protection; this is the defense-in-depth layer, and it should
    cover the hazard it claims to cover.
    """
    deliverer, worktree = _deliverer(tmp_path)

    with patch("atlas.deliverer.subprocess.run") as mock_run:
        with pytest.raises(DeliveryError, match="protected branch"):
            deliverer.deliver(
                run_id=_RUN_ID,
                branch=branch,
                worktree_path=tmp_path / "wt",
                title="t",
                body="b",
            )
        mock_run.assert_not_called()
    worktree.cleanup.assert_not_called()


def test_deliver_rejects_repo_default_branch_even_if_unusually_named(tmp_path: Path) -> None:
    """A repo whose default branch isn't in the static set is still protected:
    origin/HEAD is consulted before pushing."""
    deliverer, worktree = _deliverer(tmp_path)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if _is_default_branch_probe(args):
            return _completed(stdout="origin/production\n")
        raise AssertionError(f"must not push after detecting default branch: {args}")

    with patch("atlas.deliverer.subprocess.run", side_effect=side_effect):
        with pytest.raises(DeliveryError, match="default branch"):
            deliverer.deliver(
                run_id=_RUN_ID,
                branch="production",
                worktree_path=tmp_path / "wt",
                title="t",
                body="b",
            )
    worktree.cleanup.assert_not_called()


def test_deliver_proceeds_when_default_branch_detection_fails(tmp_path: Path) -> None:
    """Detection is best-effort and must fail OPEN: a missing origin/HEAD is
    common in fresh clones and must not block delivery of a normal branch."""
    deliverer, worktree = _deliverer(tmp_path)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if _is_default_branch_probe(args):
            raise FileNotFoundError("git not found")
        if args[:2] == ["git", "push"]:
            return _completed()
        if args[:3] == ["gh", "pr", "create"]:
            return _completed(stdout="https://github.com/acme/repo/pull/5\n")
        raise AssertionError(f"unexpected subprocess call: {args}")

    with patch("atlas.deliverer.subprocess.run", side_effect=side_effect):
        result = deliverer.deliver(
            run_id=_RUN_ID,
            branch=_BRANCH,
            worktree_path=tmp_path / "wt",
            title="t",
            body="b",
        )
    assert result.number == 5


def test_deliver_rejects_empty_branch_name(tmp_path: Path) -> None:
    deliverer, _worktree = _deliverer(tmp_path)
    with patch("atlas.deliverer.subprocess.run") as mock_run:
        with pytest.raises(DeliveryError, match="empty branch name"):
            deliverer.deliver(
                run_id=_RUN_ID,
                branch="   ",
                worktree_path=tmp_path / "wt",
                title="t",
                body="b",
            )
        mock_run.assert_not_called()


def test_deliver_rejects_main_branch_before_any_subprocess_call(tmp_path: Path) -> None:
    deliverer, worktree = _deliverer(tmp_path)

    with patch("atlas.deliverer.subprocess.run") as mock_run:
        with pytest.raises(DeliveryError, match="main"):
            deliverer.deliver(
                run_id=_RUN_ID,
                branch="main",
                worktree_path=tmp_path / "wt",
                title="t",
                body="b",
            )
        mock_run.assert_not_called()
    worktree.cleanup.assert_not_called()


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_deliver_push_failure_raises_and_never_calls_gh_or_cleanup(tmp_path: Path) -> None:
    deliverer, worktree = _deliverer(tmp_path)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if _is_default_branch_probe(args):
            return _no_default_branch()
        if args[:2] == ["git", "push"]:
            return _completed(returncode=1, stderr="no remote")
        raise AssertionError(f"gh must never be called after a failed push: {args}")

    with patch("atlas.deliverer.subprocess.run", side_effect=side_effect):
        with pytest.raises(DeliveryError, match="git push failed"):
            deliverer.deliver(
                run_id=_RUN_ID,
                branch=_BRANCH,
                worktree_path=tmp_path / "wt",
                title="t",
                body="b",
            )
    worktree.cleanup.assert_not_called()


def test_deliver_pr_create_failure_raises_and_never_calls_cleanup(tmp_path: Path) -> None:
    deliverer, worktree = _deliverer(tmp_path)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if _is_default_branch_probe(args):
            return _no_default_branch()
        if args[:2] == ["git", "push"]:
            return _completed()
        if args[:3] == ["gh", "pr", "create"]:
            return _completed(returncode=1, stderr="no gh auth")
        raise AssertionError(f"unexpected subprocess call: {args}")

    with patch("atlas.deliverer.subprocess.run", side_effect=side_effect):
        with pytest.raises(DeliveryError, match="gh pr create failed"):
            deliverer.deliver(
                run_id=_RUN_ID,
                branch=_BRANCH,
                worktree_path=tmp_path / "wt",
                title="t",
                body="b",
            )
    worktree.cleanup.assert_not_called()


def test_deliver_malformed_pr_url_raises_instead_of_number_zero_sentinel(tmp_path: Path) -> None:
    """L1 code review finding L2 (T-L2.12): a gh pr create exit-0 with stdout
    that doesn't match the expected PR URL shape must raise DeliveryError,
    not silently return PrRef(number=0, ...) — a caller reading .number back
    (comment/label/close by PR number) would otherwise fail much later, via
    a confusing `gh api /pulls/0` 404, further from the actual cause."""
    deliverer, worktree = _deliverer(tmp_path)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if _is_default_branch_probe(args):
            return _no_default_branch()
        if args[:2] == ["git", "push"]:
            return _completed()
        if args[:3] == ["gh", "pr", "create"]:
            return _completed(stdout="not a pr url\n")
        raise AssertionError(f"unexpected subprocess call: {args}")

    with patch("atlas.deliverer.subprocess.run", side_effect=side_effect):
        with pytest.raises(DeliveryError, match="could not parse a PR number"):
            deliverer.deliver(
                run_id=_RUN_ID,
                branch=_BRANCH,
                worktree_path=tmp_path / "wt",
                title="t",
                body="b",
            )
    # The PR was already created by this point (gh pr create returned exit 0);
    # cleanup is skipped since deliver() can't confirm which PR it belongs to.
    worktree.cleanup.assert_not_called()


def test_deliver_gh_binary_missing_raises_clear_deliveryerror(tmp_path: Path) -> None:
    deliverer, worktree = _deliverer(tmp_path)

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if _is_default_branch_probe(args):
            return _no_default_branch()
        if args[:2] == ["git", "push"]:
            return _completed()
        raise FileNotFoundError("gh not found")

    with patch("atlas.deliverer.subprocess.run", side_effect=side_effect):
        with pytest.raises(DeliveryError, match="gh CLI not found"):
            deliverer.deliver(
                run_id=_RUN_ID,
                branch=_BRANCH,
                worktree_path=tmp_path / "wt",
                title="t",
                body="b",
            )
    worktree.cleanup.assert_not_called()


def test_deliver_git_binary_missing_raises_clear_deliveryerror(tmp_path: Path) -> None:
    deliverer, worktree = _deliverer(tmp_path)

    with patch("atlas.deliverer.subprocess.run", side_effect=FileNotFoundError("git not found")):
        with pytest.raises(DeliveryError, match="git CLI not found"):
            deliverer.deliver(
                run_id=_RUN_ID,
                branch=_BRANCH,
                worktree_path=tmp_path / "wt",
                title="t",
                body="b",
            )
    worktree.cleanup.assert_not_called()


def test_deliver_cleanup_failure_does_not_prevent_prref_return(tmp_path: Path) -> None:
    deliverer, worktree = _deliverer(tmp_path)
    worktree.cleanup.side_effect = WorktreeError("stale ref")

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if _is_default_branch_probe(args):
            return _no_default_branch()
        if args[:2] == ["git", "push"]:
            return _completed()
        if args[:3] == ["gh", "pr", "create"]:
            return _completed(stdout="https://github.com/acme/repo/pull/7\n")
        raise AssertionError(f"unexpected subprocess call: {args}")

    with patch("atlas.deliverer.subprocess.run", side_effect=side_effect):
        result = deliverer.deliver(
            run_id=_RUN_ID,
            branch=_BRANCH,
            worktree_path=tmp_path / "wt",
            title="t",
            body="b",
        )

    assert result == PrRef(number=7, url="https://github.com/acme/repo/pull/7")
    worktree.cleanup.assert_called_once()
