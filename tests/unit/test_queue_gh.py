"""Unit tests for atlas.queue_gh — the `gh` CLI adapter (T-L2.2)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas.deliverer import DeliveryError, PrRef
from atlas.queue_gh import (
    GhCliError,
    Issue,
    claim,
    comment,
    current_user,
    deliver_pr,
    find_run_id_comment,
    list_ready,
    relabel,
    sync,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "gh_json"
_REPO = "anant-gupta-utexas/atlas"


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# list_ready
# ---------------------------------------------------------------------------


def test_list_ready_parses_gh_json() -> None:
    fixture = (_FIXTURES / "issue_list.json").read_text()
    with patch("atlas.queue_gh.subprocess.run", return_value=_completed(stdout=fixture)):
        issues = list_ready(_REPO)

    assert len(issues) == 1
    assert issues[0].number == 4
    assert issues[0].title == "[scratch] atlas loop L2 fixture capture"
    assert issues[0].labels == frozenset({"atlas:ready", "wf:quick"})
    assert issues[0].repo == _REPO


def test_list_ready_empty() -> None:
    fixture = (_FIXTURES / "issue_list_empty.json").read_text()
    with patch("atlas.queue_gh.subprocess.run", return_value=_completed(stdout=fixture)):
        issues = list_ready(_REPO)
    assert issues == []


def test_list_ready_gh_failure_raises() -> None:
    with patch(
        "atlas.queue_gh.subprocess.run",
        return_value=_completed(returncode=1, stderr="not authenticated"),
    ):
        with pytest.raises(GhCliError, match="not authenticated"):
            list_ready(_REPO)


def test_list_ready_malformed_json_raises() -> None:
    with patch("atlas.queue_gh.subprocess.run", return_value=_completed(stdout="not json")):
        with pytest.raises(GhCliError, match="malformed JSON"):
            list_ready(_REPO)


def test_list_ready_argv_shape() -> None:
    calls: list[list[str]] = []

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        calls.append(argv)
        return _completed(stdout="[]")

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        list_ready(_REPO)

    assert calls[0] == [
        "gh",
        "issue",
        "list",
        "--repo",
        _REPO,
        "--label",
        "atlas:ready",
        "--state",
        "open",
        "--json",
        "number,title,body,labels,author",
    ]


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


def test_claim_swaps_labels_and_assigns() -> None:
    issue = Issue(number=4, title="t", body="b", labels=frozenset({"atlas:ready"}), repo=_REPO)
    calls: list[list[str]] = []

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        calls.append(argv)
        return _completed()

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        claim(issue, assignee="anant-gupta-utexas")

    assert len(calls) == 1
    assert calls[0] == [
        "gh",
        "issue",
        "edit",
        "4",
        "--repo",
        _REPO,
        "--remove-label",
        "atlas:ready",
        "--add-label",
        "atlas:working",
        "--add-assignee",
        "anant-gupta-utexas",
    ]


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


def _issue_view_stdout(pr_number: int | None) -> str:
    if pr_number is None:
        return json.dumps({"closedByPullRequestsReferences": []})
    return json.dumps({"closedByPullRequestsReferences": [{"number": pr_number}]})


def test_sync_merged_outcome() -> None:
    working_issue = json.dumps(
        [{"number": 4, "title": "t", "body": "b", "labels": [], "author": {"login": "a"}}]
    )
    pr_view = (_FIXTURES / "pr_view_merged.json").read_text()

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        if argv[:3] == ["gh", "issue", "list"]:
            return _completed(stdout=working_issue)
        if argv[:3] == ["gh", "issue", "view"]:
            return _completed(stdout=_issue_view_stdout(3))
        if argv[:3] == ["gh", "pr", "view"]:
            return _completed(stdout=pr_view)
        raise AssertionError(f"unexpected call: {argv}")

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        statuses = sync(_REPO)

    assert len(statuses) == 1
    assert statuses[0].outcome == "merged"
    assert statuses[0].pr_number == 3


def test_sync_closed_unmerged_outcome() -> None:
    working_issue = json.dumps(
        [{"number": 5, "title": "t", "body": "b", "labels": [], "author": {"login": "a"}}]
    )
    pr_view = (_FIXTURES / "pr_view_closed.json").read_text()

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        if argv[:3] == ["gh", "issue", "list"]:
            return _completed(stdout=working_issue)
        if argv[:3] == ["gh", "issue", "view"]:
            return _completed(stdout=_issue_view_stdout(5))
        if argv[:3] == ["gh", "pr", "view"]:
            return _completed(stdout=pr_view)
        raise AssertionError(f"unexpected call: {argv}")

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        statuses = sync(_REPO)

    assert statuses[0].outcome == "closed_unmerged"


def test_sync_open_outcome() -> None:
    working_issue = json.dumps(
        [{"number": 5, "title": "t", "body": "b", "labels": [], "author": {"login": "a"}}]
    )
    pr_view = (_FIXTURES / "pr_view_open.json").read_text()

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        if argv[:3] == ["gh", "issue", "list"]:
            return _completed(stdout=working_issue)
        if argv[:3] == ["gh", "issue", "view"]:
            return _completed(stdout=_issue_view_stdout(5))
        if argv[:3] == ["gh", "pr", "view"]:
            return _completed(stdout=pr_view)
        raise AssertionError(f"unexpected call: {argv}")

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        statuses = sync(_REPO)

    assert statuses[0].outcome == "open"


def test_sync_no_linked_pr_skips_issue() -> None:
    working_issue = json.dumps(
        [{"number": 6, "title": "t", "body": "b", "labels": [], "author": {"login": "a"}}]
    )

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        if argv[:3] == ["gh", "issue", "list"]:
            return _completed(stdout=working_issue)
        if argv[:3] == ["gh", "issue", "view"]:
            return _completed(stdout=_issue_view_stdout(None))
        raise AssertionError(f"unexpected call: {argv}")

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        statuses = sync(_REPO)

    assert statuses == []


# ---------------------------------------------------------------------------
# relabel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["rejected", "blocked", "ready"])
def test_relabel_state_transitions_no_close(state: str) -> None:
    issue = Issue(number=4, title="t", body="b", labels=frozenset({"atlas:working"}), repo=_REPO)
    calls: list[list[str]] = []

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        calls.append(argv)
        return _completed()

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        relabel(issue, state=state)  # type: ignore[arg-type]

    assert len(calls) == 1
    assert calls[0][-1] == f"atlas:{state}"


def test_relabel_done_swaps_labels_and_closes_issue() -> None:
    issue = Issue(number=4, title="t", body="b", labels=frozenset({"atlas:working"}), repo=_REPO)
    calls: list[list[str]] = []

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        calls.append(argv)
        return _completed()

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        relabel(issue, state="done")

    assert len(calls) == 2
    assert calls[0][-1] == "atlas:done"
    assert calls[1] == ["gh", "issue", "close", "4", "--repo", _REPO]


# ---------------------------------------------------------------------------
# comment
# ---------------------------------------------------------------------------


def test_comment_argv_shape() -> None:
    issue = Issue(number=4, title="t", body="b", labels=frozenset(), repo=_REPO)
    calls: list[list[str]] = []

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        calls.append(argv)
        return _completed()

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        comment(issue, body="run_id=abc123")

    assert calls[0] == ["gh", "issue", "comment", "4", "--repo", _REPO, "--body", "run_id=abc123"]


# ---------------------------------------------------------------------------
# deliver_pr — thin pass-through
# ---------------------------------------------------------------------------


def test_deliver_pr_delegates_to_deliverer(tmp_path: Path) -> None:
    issue = Issue(number=4, title="t", body="b", labels=frozenset(), repo=_REPO)
    mock_deliverer = MagicMock()
    mock_deliverer.deliver.return_value = PrRef(number=9, url="https://example/pull/9")

    result = deliver_pr(
        issue,
        branch="atlas/x-abc123",
        title="Title",
        body="Body",
        repo_root=tmp_path,
        run_id="run-1",
        worktree_path=tmp_path / "wt",
        deliverer=mock_deliverer,
    )

    assert result == PrRef(number=9, url="https://example/pull/9")
    mock_deliverer.deliver.assert_called_once_with(
        run_id="run-1",
        branch="atlas/x-abc123",
        worktree_path=tmp_path / "wt",
        title="Title",
        body="Body",
    )


def test_deliver_pr_propagates_delivery_error(tmp_path: Path) -> None:
    issue = Issue(number=4, title="t", body="b", labels=frozenset(), repo=_REPO)
    mock_deliverer = MagicMock()
    mock_deliverer.deliver.side_effect = DeliveryError("boom")

    with pytest.raises(DeliveryError, match="boom"):
        deliver_pr(
            issue,
            branch="atlas/x-abc123",
            title="Title",
            body="Body",
            repo_root=tmp_path,
            run_id="run-1",
            worktree_path=tmp_path / "wt",
            deliverer=mock_deliverer,
        )


# ---------------------------------------------------------------------------
# Timeout wrapping
# ---------------------------------------------------------------------------


def test_all_gh_calls_wrapped_in_timeout() -> None:
    calls: list[dict[str, object]] = []

    def side_effect(argv: list[str], **kwargs: object) -> MagicMock:
        calls.append(kwargs)
        if argv[:3] == ["gh", "issue", "list"]:
            return _completed(stdout="[]")
        return _completed(stdout="{}")

    with patch("atlas.queue_gh.subprocess.run", side_effect=side_effect):
        list_ready(_REPO)

    assert calls
    for kwargs in calls:
        assert "timeout" in kwargs


def test_gh_timeout_raises_ghclierror() -> None:
    import subprocess as sp

    with patch(
        "atlas.queue_gh.subprocess.run",
        side_effect=sp.TimeoutExpired(cmd=["gh"], timeout=30),
    ):
        with pytest.raises(GhCliError, match="timed out"):
            list_ready(_REPO)


def test_gh_binary_missing_raises_ghclierror() -> None:
    with patch("atlas.queue_gh.subprocess.run", side_effect=FileNotFoundError("gh not found")):
        with pytest.raises(GhCliError, match="not found"):
            list_ready(_REPO)


# ---------------------------------------------------------------------------
# current_user / find_run_id_comment
# ---------------------------------------------------------------------------


def test_current_user_returns_login() -> None:
    with patch(
        "atlas.queue_gh.subprocess.run", return_value=_completed(stdout="anant-gupta-utexas\n")
    ):
        assert current_user() == "anant-gupta-utexas"


def test_find_run_id_comment_extracts_run_id() -> None:
    issue = Issue(number=4, title="t", body="b", labels=frozenset(), repo=_REPO)
    stdout = json.dumps(
        {"comments": [{"body": "atlas loop dispatched this issue.\n\nplumb run_id: `abc-123`"}]}
    )
    with patch("atlas.queue_gh.subprocess.run", return_value=_completed(stdout=stdout)):
        assert find_run_id_comment(issue) == "abc-123"


def test_find_run_id_comment_no_match_returns_none() -> None:
    issue = Issue(number=4, title="t", body="b", labels=frozenset(), repo=_REPO)
    stdout = json.dumps({"comments": [{"body": "unrelated comment"}]})
    with patch("atlas.queue_gh.subprocess.run", return_value=_completed(stdout=stdout)):
        assert find_run_id_comment(issue) is None


def test_find_run_id_comment_gh_failure_returns_none() -> None:
    issue = Issue(number=4, title="t", body="b", labels=frozenset(), repo=_REPO)
    with patch(
        "atlas.queue_gh.subprocess.run", return_value=_completed(returncode=1, stderr="boom")
    ):
        assert find_run_id_comment(issue) is None


# ---------------------------------------------------------------------------
# Sole-gh-caller boundary test (Decision #15)
# ---------------------------------------------------------------------------


def test_loop_module_never_shells_gh_directly() -> None:
    """loop.py must never construct a `gh` subprocess call itself — it goes
    through queue_gh.py exclusively (TRD-v3 §6). Grep-based, not mock-based,
    so it catches the boundary even if a future edit adds a raw call."""
    loop_path = Path(__file__).parent.parent.parent / "src" / "atlas" / "loop.py"
    if not loop_path.exists():
        pytest.skip("loop.py not yet implemented")
    content = loop_path.read_text()
    # Match subprocess.run/Popen/call/check_call/check_output invocations
    # whose argv list starts with "gh" — the pattern any raw gh shell-out
    # would use. queue_gh.* calls (imported functions) are fine.
    forbidden = re.compile(r'subprocess\.(run|Popen|call|check_call|check_output)\(\s*\[\s*"gh"')
    assert not forbidden.search(content), "loop.py must not shell out to gh directly"
