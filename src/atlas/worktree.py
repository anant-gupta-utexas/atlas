"""Git worktree lifecycle management for stage 5 (code_gen)."""

from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeError(Exception):
    """Raised on worktree create/merge/cleanup failures."""


class WorktreeManager:
    """
    Manages one git worktree per atlas run.

    The worktree lives at ``<repo_root>/.atlas/worktrees/<slug>-<short_run_id>``.
    Atlas never touches ``main`` directly.
    """

    _WORKTREES_DIR = Path(".atlas") / "worktrees"

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._worktrees_root = repo_root / self._WORKTREES_DIR

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def create(self, *, slug: str, run_id: str, base_branch: str = "main") -> Path:
        """
        Run ``git worktree add`` and return the new worktree path.

        Raises ``WorktreeError`` if the repo is dirty, the path already
        exists, or the git command fails.
        """
        short_id = run_id[:8]
        name = f"{slug}-{short_id}"
        worktree_path = self._worktrees_root / name

        self._assert_path_contained(worktree_path)

        if worktree_path.exists():
            raise WorktreeError(
                f"Worktree path already exists: {worktree_path}. "
                "Remove it manually or use a different run_id."
            )

        self._check_repo_clean()

        branch_name = f"atlas/{name}"
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path), base_branch],
            cwd=self._repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            raise WorktreeError(
                f"git worktree add failed (exit {result.returncode}): {result.stderr.strip()}"
            )

        return worktree_path

    def merge_back(self, worktree_path: Path, *, target_branch: str = "main") -> None:
        """
        Merge the worktree branch into ``target_branch`` via ``git merge --squash``,
        then commit.  Raises ``WorktreeError`` on conflict or git failure.
        """
        self._assert_path_contained(worktree_path)

        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            check=False,
            text=True,
        )
        if branch_result.returncode != 0:
            raise WorktreeError(
                f"Could not determine worktree branch: {branch_result.stderr.strip()}"
            )
        worktree_branch = branch_result.stdout.strip()

        # Squash-merge the worktree branch into target_branch
        merge_result = subprocess.run(
            ["git", "merge", "--squash", worktree_branch],
            cwd=self._repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
        if merge_result.returncode != 0:
            raise WorktreeError(
                f"git merge --squash failed (exit {merge_result.returncode}): "
                f"{merge_result.stderr.strip()}"
            )

        commit_result = subprocess.run(
            ["git", "commit", "-m", f"atlas: merge code_gen output from {worktree_branch}"],
            cwd=self._repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
        if commit_result.returncode != 0:
            raise WorktreeError(
                f"git commit failed (exit {commit_result.returncode}): "
                f"{commit_result.stderr.strip()}"
            )

    def cleanup(self, worktree_path: Path) -> None:
        """
        Remove the worktree directory and prune the worktree ref.
        Does NOT touch ``main``.  Safe to call even if the worktree was
        never created (path absent).
        """
        if not worktree_path.exists():
            return

        self._assert_path_contained(worktree_path)

        remove_result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=self._repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
        if remove_result.returncode != 0:
            raise WorktreeError(
                f"git worktree remove failed (exit {remove_result.returncode}): "
                f"{remove_result.stderr.strip()}"
            )

        # Prune stale refs
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=self._repo_root,
            capture_output=True,
            check=False,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _assert_path_contained(self, path: Path) -> None:
        """Raise WorktreeError if path escapes the designated worktrees dir."""
        try:
            path.resolve().relative_to(self._worktrees_root.resolve())
        except ValueError:
            raise WorktreeError(
                f"Worktree path {path} is not under {self._worktrees_root}. "
                "Refusing to operate outside the designated worktrees directory."
            )

    def _check_repo_clean(self) -> None:
        """
        Raise WorktreeError if tracked files have uncommitted modifications.

        Untracked files (``?? …``) are ignored — they don't conflict with
        worktree creation and would otherwise block every run that creates
        ``.atlas/`` or ``dev/`` directories inside the repo.
        """
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self._repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            raise WorktreeError(f"git status failed: {result.stderr.strip()}")

        # Filter out untracked-file lines (start with "??")
        dirty_lines = [line for line in result.stdout.splitlines() if not line.startswith("??")]
        if dirty_lines:
            raise WorktreeError(
                "Repository has uncommitted changes to tracked files; "
                "cannot create worktree.\n"
                "Dirty files:\n" + "\n".join(dirty_lines)
            )
