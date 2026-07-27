"""Unit tests for atlas.self_heal — the diagnosis-injected single-retry
state machine (T-L3.6). Fakes PlumbIO/judge_gate/run_one_shot/
run_planned_first_pass at the same module boundaries loop.py's own tests
already use."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from atlas import judge_gate, self_heal
from atlas.config import Config, LoopConfig
from atlas.deliverer import PrRef
from atlas.loop import AbortedRunError
from atlas.queue_gh import Issue

_REPO = "anant-gupta-utexas/atlas"
_ORIGINAL_RUN_ID = "o" * 32


def _cfg(tmp_path: Path) -> Config:
    return Config(
        repo_root=tmp_path, plumb_db_path=tmp_path / "plumb.db", loop=LoopConfig(repos=(_REPO,))
    )


def _issue() -> Issue:
    return Issue(number=1, title="Fix bug", body="details", labels=frozenset(), repo=_REPO)


def _classification(mode: str, retryable: bool) -> judge_gate.FailureClassification:
    return judge_gate.FailureClassification(mode=mode, rationale="because", retryable=retryable)  # type: ignore[arg-type]


def test_write_example_called_exactly_once(tmp_path: Path) -> None:
    with (
        patch("atlas.self_heal.PlumbIO") as plumb_cls,
        patch(
            "atlas.self_heal.classify_failure",
            return_value=_classification("infeasible", False),
        ),
    ):
        plumb_instance = plumb_cls.return_value
        self_heal.handle_failure(
            _issue(),
            AbortedRunError("boom"),
            _cfg(tmp_path),
            repo_root=tmp_path,
            original_run_id=_ORIGINAL_RUN_ID,
            diff_text="some diff",
            lane="quick",
        )
    plumb_instance.write_example.assert_called_once()


def test_judge_unavailable_on_classify_is_not_retryable_no_retry_call(tmp_path: Path) -> None:
    with (
        patch("atlas.self_heal.PlumbIO"),
        patch(
            "atlas.self_heal.classify_failure",
            side_effect=judge_gate.JudgeUnavailableError("no provider"),
        ) as classify_mock,
        patch("atlas.self_heal.run_one_shot") as run_mock,
    ):
        result = self_heal.handle_failure(
            _issue(),
            AbortedRunError("boom"),
            _cfg(tmp_path),
            repo_root=tmp_path,
            original_run_id=_ORIGINAL_RUN_ID,
            diff_text="diff",
            lane="quick",
        )
    classify_mock.assert_called_once()
    run_mock.assert_not_called()
    assert result.outcome == "not_retryable"
    assert result.classification is None


def test_infeasible_mode_not_retryable_no_retry_call(tmp_path: Path) -> None:
    with (
        patch("atlas.self_heal.PlumbIO"),
        patch(
            "atlas.self_heal.classify_failure",
            return_value=_classification("infeasible", False),
        ),
        patch("atlas.self_heal.run_one_shot") as run_mock,
    ):
        result = self_heal.handle_failure(
            _issue(),
            AbortedRunError("boom"),
            _cfg(tmp_path),
            repo_root=tmp_path,
            original_run_id=_ORIGINAL_RUN_ID,
            diff_text="diff",
            lane="quick",
        )
    run_mock.assert_not_called()
    assert result.outcome == "not_retryable"
    assert result.classification is not None
    assert result.classification.mode == "infeasible"


@pytest.mark.parametrize("mode", ["flaky", "wrong_approach", "missing_context"])
def test_retryable_mode_calls_run_one_shot_once_with_parent_and_diagnosis(
    tmp_path: Path, mode: str
) -> None:
    pr_ref = PrRef(number=5, url="https://example.com/pulls/5")
    with (
        patch("atlas.self_heal.PlumbIO"),
        patch("atlas.self_heal.classify_failure", return_value=_classification(mode, True)),
        patch(
            "atlas.self_heal.run_one_shot", return_value=(pr_ref, "child-run-id", 0.5)
        ) as run_mock,
    ):
        result = self_heal.handle_failure(
            _issue(),
            AbortedRunError("boom"),
            _cfg(tmp_path),
            repo_root=tmp_path,
            original_run_id=_ORIGINAL_RUN_ID,
            diff_text="diff",
            lane="quick",
        )
    run_mock.assert_called_once()
    kwargs = run_mock.call_args.kwargs
    assert kwargs["parent_run_id"] == _ORIGINAL_RUN_ID
    assert mode in kwargs["diagnosis"]
    assert result.outcome == "retried_success"
    assert result.pr_ref == pr_ref
    assert result.run_id == "child-run-id"


def test_retry_failure_returns_retried_failed_does_not_recurse(tmp_path: Path) -> None:
    with (
        patch("atlas.self_heal.PlumbIO"),
        patch(
            "atlas.self_heal.classify_failure", return_value=_classification("flaky", True)
        ),
        patch(
            "atlas.self_heal.run_one_shot", side_effect=AbortedRunError("second failure")
        ) as run_mock,
    ):
        result = self_heal.handle_failure(
            _issue(),
            AbortedRunError("boom"),
            _cfg(tmp_path),
            repo_root=tmp_path,
            original_run_id=_ORIGINAL_RUN_ID,
            diff_text="diff",
            lane="quick",
        )
    run_mock.assert_called_once()
    assert result.outcome == "retried_failed"


def test_planned_lane_retries_via_run_planned_first_pass_never_scores_diff(
    tmp_path: Path,
) -> None:
    pr_ref = PrRef(number=6, url="https://example.com/pulls/6")
    with (
        patch("atlas.self_heal.PlumbIO"),
        patch(
            "atlas.self_heal.classify_failure",
            return_value=_classification("missing_context", True),
        ),
        patch("atlas.self_heal.run_one_shot") as quick_mock,
        patch(
            "atlas.self_heal.run_planned_first_pass",
            return_value=(pr_ref, "child-run-id", 0.2),
        ) as planned_mock,
        patch("atlas.judge_gate.score_diff") as score_mock,
    ):
        result = self_heal.handle_failure(
            _issue(),
            AbortedRunError("boom"),
            _cfg(tmp_path),
            repo_root=tmp_path,
            original_run_id=_ORIGINAL_RUN_ID,
            diff_text=None,
            lane="planned",
        )
    planned_mock.assert_called_once()
    quick_mock.assert_not_called()
    score_mock.assert_not_called()
    assert result.outcome == "retried_success"


def test_diff_text_none_falls_back_to_issue_body_for_example(tmp_path: Path) -> None:
    issue = _issue()
    with (
        patch("atlas.self_heal.PlumbIO") as plumb_cls,
        patch(
            "atlas.self_heal.classify_failure",
            return_value=_classification("infeasible", False),
        ),
    ):
        plumb_instance = plumb_cls.return_value
        self_heal.handle_failure(
            issue,
            AbortedRunError("boom"),
            _cfg(tmp_path),
            repo_root=tmp_path,
            original_run_id=_ORIGINAL_RUN_ID,
            diff_text=None,
            lane="quick",
        )
    kwargs = plumb_instance.write_example.call_args.kwargs
    assert kwargs["inputs"] == issue.body
