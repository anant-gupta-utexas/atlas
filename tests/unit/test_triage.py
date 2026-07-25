"""Unit tests for atlas.triage — label-wins-else-classify router (T-L2.4)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from atlas.plumb_io import PlumbIO
from atlas.queue_gh import Issue
from atlas.triage import triage

_REPO = "anant-gupta-utexas/atlas"


def _issue(labels: frozenset[str], body: str = "some body") -> Issue:
    return Issue(number=1, title="Title", body=body, labels=labels, repo=_REPO)


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# Label-wins
# ---------------------------------------------------------------------------


def test_triage_wf_quick_label_wins() -> None:
    plumb = PlumbIO(real=False)
    with patch("atlas.triage.subprocess.run") as mock_run:
        result = triage(_issue(frozenset({"wf:quick"})), plumb=plumb, run_id="r1")
    mock_run.assert_not_called()
    assert result.lane == "quick"
    assert result.source == "label"


def test_triage_wf_planned_label_wins() -> None:
    plumb = PlumbIO(real=False)
    with patch("atlas.triage.subprocess.run") as mock_run:
        result = triage(_issue(frozenset({"wf:planned"})), plumb=plumb, run_id="r1")
    mock_run.assert_not_called()
    assert result.lane == "planned"
    assert result.source == "label"


def test_triage_both_labels_planned_wins() -> None:
    plumb = PlumbIO(real=False)
    with patch("atlas.triage.subprocess.run") as mock_run:
        result = triage(_issue(frozenset({"wf:quick", "wf:planned"})), plumb=plumb, run_id="r1")
    mock_run.assert_not_called()
    assert result.lane == "planned"
    assert result.source == "label"


# ---------------------------------------------------------------------------
# Classify fallback
# ---------------------------------------------------------------------------


def test_triage_classify_fallback_invokes_backend_once() -> None:
    plumb = PlumbIO(real=False)
    with patch(
        "atlas.triage.subprocess.run",
        return_value=_completed(stdout="quick this is a small fix"),
    ) as mock_run:
        result = triage(_issue(frozenset()), plumb=plumb, run_id="r1")

    mock_run.assert_called_once()
    assert result.lane == "quick"
    assert result.source == "classify"
    assert len(plumb.spans) == 1
    assert plumb.spans[0]["kind"] == "plan"
    assert plumb.spans[0]["name"] == "triage"


def test_triage_classify_planned_response() -> None:
    plumb = PlumbIO(real=False)
    with patch(
        "atlas.triage.subprocess.run",
        return_value=_completed(stdout="planned needs a design doc"),
    ):
        result = triage(_issue(frozenset()), plumb=plumb, run_id="r1")
    assert result.lane == "planned"
    assert result.source == "classify"


def test_triage_classify_unparseable_defaults_planned() -> None:
    plumb = PlumbIO(real=False)
    with patch(
        "atlas.triage.subprocess.run",
        return_value=_completed(stdout="I cannot decide"),
    ):
        result = triage(_issue(frozenset()), plumb=plumb, run_id="r1")
    assert result.lane == "planned"
    assert result.source == "classify"
    assert result.rationale is not None


def test_triage_classify_nonzero_exit_defaults_planned() -> None:
    plumb = PlumbIO(real=False)
    with patch(
        "atlas.triage.subprocess.run",
        return_value=_completed(returncode=1, stderr="boom"),
    ):
        result = triage(_issue(frozenset()), plumb=plumb, run_id="r1")
    assert result.lane == "planned"
    assert result.source == "classify"


def test_triage_classify_timeout_defaults_planned() -> None:
    import subprocess as sp

    plumb = PlumbIO(real=False)
    with patch(
        "atlas.triage.subprocess.run",
        side_effect=sp.TimeoutExpired(cmd=["claude"], timeout=120),
    ):
        result = triage(_issue(frozenset()), plumb=plumb, run_id="r1")
    assert result.lane == "planned"
    assert result.source == "classify"


def test_triage_classify_empty_response_defaults_planned() -> None:
    plumb = PlumbIO(real=False)
    with patch("atlas.triage.subprocess.run", return_value=_completed(stdout="")):
        result = triage(_issue(frozenset()), plumb=plumb, run_id="r1")
    assert result.lane == "planned"
