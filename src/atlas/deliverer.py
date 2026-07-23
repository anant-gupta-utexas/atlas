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

    def deliver(
        self,
        *,
        run_id: str,
        branch: str,
        worktree_path: Path,
        title: str,
        body: str,
    ) -> PrRef:
        if branch == "main":
            raise DeliveryError(
                f"Refusing to deliver run {run_id}: branch is 'main'. "
                "Deliverer never pushes or opens a PR from main."
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
    url = stdout.strip().splitlines()[-1].strip() if stdout.strip() else ""
    match = _PR_URL_RE.search(url)
    number = int(match.group(1)) if match else 0
    return PrRef(number=number, url=url)
