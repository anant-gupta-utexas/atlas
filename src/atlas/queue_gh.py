"""``gh`` CLI adapter — the sole point of contact with ``gh`` from atlas.

Every function here is a list-form ``subprocess.run`` call, timeout-wrapped,
raising :class:`GhCliError` on a non-zero exit, a timeout, or malformed JSON.
No ``shell=True`` anywhere; raw issue-body text never reaches a ``gh`` argv —
only the agent prompt sees it. ``loop.py`` never shells ``gh`` directly
(TRD-v3 §6) — enforced by the grep-based test in ``test_queue_gh.py``.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from atlas.deliverer import Deliverer, PrRef

_DEFAULT_TIMEOUT_S = 30


class GhCliError(Exception):
    """Raised when a `gh` invocation fails (non-zero exit) or times out."""


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: frozenset[str]
    repo: str
    author: str = ""


@dataclass(frozen=True)
class PrStatus:
    """One in-flight issue's PR state, as read by sync()."""

    issue: Issue
    outcome: Literal["merged", "closed_unmerged", "open"]
    pr_number: int | None


def _run_gh(argv: list[str], *, timeout_s: int) -> str:
    """Run a `gh ...` argv list; return stdout. Raises GhCliError on failure."""
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            timeout=timeout_s,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise GhCliError(f"gh call timed out after {timeout_s}s: {argv}") from exc
    except FileNotFoundError as exc:
        raise GhCliError("gh CLI not found on PATH") from exc

    if result.returncode != 0:
        raise GhCliError(f"gh call failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def _parse_json(stdout: str, *, context: str) -> object:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise GhCliError(f"gh returned malformed JSON for {context}: {exc}") from exc


def list_ready(repo: str, *, timeout_s: int = _DEFAULT_TIMEOUT_S) -> list[Issue]:
    """List open issues labeled `atlas:ready` in `repo`."""
    return _list_labeled(repo, "atlas:ready", timeout_s=timeout_s)


def list_labeled(repo: str, label: str, *, timeout_s: int = _DEFAULT_TIMEOUT_S) -> list[Issue]:
    """List open issues carrying an arbitrary label (used by reconcile_orphans)."""
    return _list_labeled(repo, label, timeout_s=timeout_s)


def _list_labeled(repo: str, label: str, *, timeout_s: int, state: str = "open") -> list[Issue]:
    """List issues carrying ``label``.

    ``state`` defaults to ``open`` — a closed issue must never be picked up
    as new work. ``sync()`` overrides it to ``all``; see its docstring for
    why a merged PR's issue is necessarily already closed.
    """
    argv = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--label",
        label,
        "--state",
        state,
        "--json",
        "number,title,body,labels,author",
    ]
    stdout = _run_gh(argv, timeout_s=timeout_s)
    rows = _parse_json(stdout, context="issue list")
    if not isinstance(rows, list):
        raise GhCliError(f"gh issue list returned non-array JSON: {stdout!r}")

    issues: list[Issue] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        labels = frozenset(
            str(lbl["name"])
            for lbl in row.get("labels", [])
            if isinstance(lbl, dict) and "name" in lbl
        )
        author_obj = row.get("author")
        author = (
            str(author_obj.get("login", ""))
            if isinstance(author_obj, dict)
            else str(author_obj or "")
        )
        issues.append(
            Issue(
                number=int(row["number"]),
                title=str(row.get("title", "")),
                body=str(row.get("body", "") or ""),
                labels=labels,
                repo=repo,
                author=author,
            )
        )
    return issues


def claim(issue: Issue, *, assignee: str, timeout_s: int = _DEFAULT_TIMEOUT_S) -> None:
    """Swap atlas:ready -> atlas:working and assign, in one combined `gh` call."""
    argv = [
        "gh",
        "issue",
        "edit",
        str(issue.number),
        "--repo",
        issue.repo,
        "--remove-label",
        "atlas:ready",
        "--add-label",
        "atlas:working",
        "--add-assignee",
        assignee,
    ]
    _run_gh(argv, timeout_s=timeout_s)


def deliver_pr(
    issue: Issue,
    *,
    branch: str,
    title: str,
    body: str,
    repo_root: Path,
    run_id: str,
    worktree_path: Path,
    deliverer: Deliverer,
) -> PrRef:
    """Thin pass-through to Deliverer.deliver() — queue_gh does not reimplement PR creation.

    DeliveryError propagates to loop.tick(), which comments on the issue and
    leaves it atlas:working for manual triage.
    """
    return deliverer.deliver(
        run_id=run_id,
        branch=branch,
        worktree_path=worktree_path,
        title=title,
        body=body,
    )


def comment(issue: Issue, *, body: str, timeout_s: int = _DEFAULT_TIMEOUT_S) -> None:
    """Post a comment on the issue (run_id + score summary, or a failure notice)."""
    argv = [
        "gh",
        "issue",
        "comment",
        str(issue.number),
        "--repo",
        issue.repo,
        "--body",
        body,
    ]
    _run_gh(argv, timeout_s=timeout_s)


def sync(repo: str, *, timeout_s: int = _DEFAULT_TIMEOUT_S) -> list[PrStatus]:
    """For every atlas:working issue with a linked PR, read the PR's outcome.

    Two gh calls per issue: `gh issue view --json
    closedByPullRequestsReferences` to find the linked PR (GitHub resolves the
    link from the PR body's `Closes #<n>`), then `gh pr view --json
    state,mergedAt,number` for its outcome. Issues with no linked PR yet are
    omitted from the result entirely.

    **`state="all"` is load-bearing, not defensive.** atlas writes
    `Closes #<n>` into every PR body, so GitHub closes the issue the instant
    the PR merges — before atlas's next tick can look. Listing only open
    issues therefore made the merged case *structurally unobservable*: the
    `user_signal` score was never written and the issue sat on
    `atlas:working` forever, which is exactly what TRD-v3 §13 #5's second
    half ("merging makes the next tick write a user_signal and close the
    issue") requires. Confirmed live during T-L2.13 on 2026-07-27. The
    `atlas:working` label is the real filter here; issue state is not.
    """
    working = _list_labeled(repo, "atlas:working", timeout_s=timeout_s, state="all")
    statuses: list[PrStatus] = []
    for issue in working:
        pr_number = _find_linked_pr_number(issue, timeout_s=timeout_s)
        if pr_number is None:
            continue
        outcome, resolved_number = _pr_view_outcome(repo, pr_number, timeout_s=timeout_s)
        statuses.append(PrStatus(issue=issue, outcome=outcome, pr_number=resolved_number))
    return statuses


def _find_linked_pr_number(issue: Issue, *, timeout_s: int) -> int | None:
    """Return the number of the PR GitHub has linked to this issue, if any.

    Reads `closedByPullRequestsReferences` — GitHub populates it from the
    PR body's `Closes #<n>`, which loop.run_one_shot always writes. Returns
    None (rather than raising) when the field is absent, empty, or the gh
    call fails: the caller treats "no linked PR yet" as nothing to sync.
    """
    argv = [
        "gh",
        "issue",
        "view",
        str(issue.number),
        "--repo",
        issue.repo,
        "--json",
        "closedByPullRequestsReferences",
    ]
    try:
        stdout = _run_gh(argv, timeout_s=timeout_s)
    except GhCliError:
        return None
    payload = _parse_json(stdout, context="issue view (linked PRs)")
    if not isinstance(payload, dict):
        return None
    refs = payload.get("closedByPullRequestsReferences")
    if not isinstance(refs, list) or not refs:
        return None
    first = refs[0]
    if not isinstance(first, dict) or "number" not in first:
        return None
    return int(first["number"])


def _pr_view_outcome(
    repo: str, pr_number: int, *, timeout_s: int
) -> tuple[Literal["merged", "closed_unmerged", "open"], int]:
    argv = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        repo,
        "--json",
        "state,mergedAt,number",
    ]
    stdout = _run_gh(argv, timeout_s=timeout_s)
    payload = _parse_json(stdout, context="pr view")
    if not isinstance(payload, dict):
        raise GhCliError(f"gh pr view returned non-object JSON: {stdout!r}")

    state = str(payload.get("state", ""))
    merged_at = payload.get("mergedAt")
    number = int(payload.get("number", pr_number))

    if state == "MERGED" or merged_at:
        return "merged", number
    if state == "CLOSED":
        return "closed_unmerged", number
    return "open", number


def current_user(*, timeout_s: int = _DEFAULT_TIMEOUT_S) -> str:
    """Return the authenticated gh user's login (used as claim() assignee)."""
    argv = ["gh", "api", "user", "--jq", ".login"]
    stdout = _run_gh(argv, timeout_s=timeout_s)
    return stdout.strip()


_RUN_ID_COMMENT_RE = re.compile(r"plumb run_id:\s*`([0-9a-fA-F-]+)`")


def find_run_id_comment(issue: Issue, *, timeout_s: int = _DEFAULT_TIMEOUT_S) -> str | None:
    """Recover the plumb run_id recorded in a prior comment() call on this issue."""
    argv = [
        "gh",
        "issue",
        "view",
        str(issue.number),
        "--repo",
        issue.repo,
        "--json",
        "comments",
    ]
    try:
        stdout = _run_gh(argv, timeout_s=timeout_s)
    except GhCliError:
        return None
    payload = _parse_json(stdout, context="issue view (comments)")
    if not isinstance(payload, dict):
        return None
    comments = payload.get("comments", [])
    if not isinstance(comments, list):
        return None
    for c in reversed(comments):
        if not isinstance(c, dict):
            continue
        body = c.get("body", "")
        m = _RUN_ID_COMMENT_RE.search(str(body))
        if m:
            return m.group(1)
    return None


def relabel(
    issue: Issue,
    *,
    state: Literal["done", "rejected", "blocked", "ready"],
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> None:
    """Swap `atlas:working` -> `atlas:<state>`. state="done" also closes the issue."""
    argv = [
        "gh",
        "issue",
        "edit",
        str(issue.number),
        "--repo",
        issue.repo,
        "--remove-label",
        "atlas:working",
        "--add-label",
        f"atlas:{state}",
    ]
    _run_gh(argv, timeout_s=timeout_s)

    if state == "done":
        close_argv = ["gh", "issue", "close", str(issue.number), "--repo", issue.repo]
        _run_gh(close_argv, timeout_s=timeout_s)
