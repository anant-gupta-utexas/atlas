"""Delivery primitive: push a run's worktree branch and open a PR.

Trust boundary: never pushes ``main``, never force-pushes, never merges.
Push safety is enforced by construction (hardcoded argv shape) plus a
defensive branch-name assertion — not by a runtime flag.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from atlas.worktree import WorktreeError, WorktreeManager

_logger = logging.getLogger("atlas.deliverer")

_PR_URL_RE = re.compile(r"https://\S+/pull/(\d+)")

# Branch names atlas refuses to push, compared after stripping any
# ``refs/heads/`` prefix and lowercasing (L1 code review finding L3). The
# real protection is the hardcoded argv shape — no ``--force``, explicit
# ``-u origin <branch>`` — so this is defense-in-depth; it covers every
# common spelling of "the shared trunk" rather than only ``main``.
_PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master", "trunk", "develop"})


def _normalize_branch(branch: str) -> str:
    """Strip a ``refs/heads/`` prefix and lowercase, for protected-branch comparison."""
    stripped = branch.strip()
    if stripped.startswith("refs/heads/"):
        stripped = stripped[len("refs/heads/") :]
    return stripped.lower()


class DeliveryError(Exception):
    """Raised on git push / gh pr create failure, or an unsafe branch name."""


@dataclass(frozen=True)
class PrRef:
    number: int
    url: str


class Deliverer(Protocol):
    def deliver(
        self,
        *,
        run_id: str,
        branch: str,
        worktree_path: Path,
        title: str,
        body: str,
    ) -> PrRef: ...


class GhPrDeliverer:
    """Pushes a worktree branch, opens a PR via `gh`, then cleans up the worktree."""

    def __init__(self, *, repo_root: Path, worktree: WorktreeManager) -> None:
        self._repo_root = repo_root
        self._worktree = worktree

    def _detect_default_branch(self) -> str | None:
        """Return the repo's normalized default branch, or None if undeterminable.

        Reads ``origin/HEAD``'s symbolic ref, which is purely local (no
        network). Returns None on any failure — a missing ``origin/HEAD`` is
        common in fresh clones and must not block delivery. This is an
        *additional* guard layered on ``_PROTECTED_BRANCHES``; the static set
        still catches the common spellings when detection is unavailable.
        """
        try:
            result = subprocess.run(
                ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                cwd=self._repo_root,
                capture_output=True,
                check=False,
                text=True,
            )
        except (FileNotFoundError, OSError):
            return None
        if result.returncode != 0:
            return None
        # e.g. "origin/main" -> "main"
        ref = result.stdout.strip()
        return _normalize_branch(ref.split("/", 1)[1]) if "/" in ref else None

    def deliver(
        self,
        *,
        run_id: str,
        branch: str,
        worktree_path: Path,
        title: str,
        body: str,
    ) -> PrRef:
        normalized = _normalize_branch(branch)
        if not normalized:
            raise DeliveryError(f"Refusing to deliver run {run_id}: empty branch name.")
        if normalized in _PROTECTED_BRANCHES:
            raise DeliveryError(
                f"Refusing to deliver run {run_id}: branch {branch!r} is a protected "
                f"branch ({normalized!r}). Deliverer never pushes or opens a PR from "
                "a shared trunk."
            )
        default_branch = self._detect_default_branch()
        if default_branch is not None and normalized == default_branch:
            raise DeliveryError(
                f"Refusing to deliver run {run_id}: branch {branch!r} is this repo's "
                f"default branch ({default_branch!r}). Deliverer never pushes or "
                "opens a PR from a shared trunk."
            )

        try:
            push = subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=worktree_path,
                capture_output=True,
                check=False,
                text=True,
            )
        except FileNotFoundError as exc:
            raise DeliveryError("git CLI not found on PATH") from exc

        if push.returncode != 0:
            raise DeliveryError(f"git push failed (exit {push.returncode}): {push.stderr.strip()}")

        try:
            pr = subprocess.run(
                ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body],
                cwd=self._repo_root,
                capture_output=True,
                check=False,
                text=True,
            )
        except FileNotFoundError as exc:
            raise DeliveryError("gh CLI not found on PATH") from exc

        if pr.returncode != 0:
            raise DeliveryError(f"gh pr create failed (exit {pr.returncode}): {pr.stderr.strip()}")

        pr_ref = _parse_pr_url(pr.stdout)

        try:
            self._worktree.cleanup(worktree_path)
        except WorktreeError:
            _logger.warning(
                "deliver: cleanup failed for run_id=%s worktree_path=%s (PR already exists)",
                run_id,
                worktree_path,
            )

        return pr_ref


def _parse_pr_url(stdout: str) -> PrRef:
    """Parse `gh pr create`'s stdout URL into a PrRef.

    Raises DeliveryError rather than sentinel-ing to number=0 on a match
    failure (L1 code review finding L2, T-L2.12): 0 is not a valid PR number
    anywhere, and a caller reading it back (comment/label/close by PR number)
    would fail much later, further from the cause, via a confusing
    `gh api /pulls/0` 404. The PR itself was already created successfully by
    this point (gh pr create returned exit 0) — a malformed URL means gh's
    output format changed, not that delivery failed outright, but atlas has
    no way to safely resume without a parsed PR number, so failing loudly
    here (caught by loop.tick()'s existing DeliveryError handler, which
    leaves the issue atlas:working for manual triage) is the safer default.
    """
    url = stdout.strip().splitlines()[-1].strip() if stdout.strip() else ""
    match = _PR_URL_RE.search(url)
    if match is None:
        raise DeliveryError(f"could not parse a PR number out of `gh pr create` output: {stdout!r}")
    return PrRef(number=int(match.group(1)), url=url)
