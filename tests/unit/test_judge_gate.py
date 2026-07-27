"""Unit tests for atlas.judge_gate — pre-PR scoring + failure classification
(T-L3.2, T-L3.3). The JudgeAdapter Protocol is mocked at the judge_gate.py
boundary (a fake implementing .score()), matching how L2 already fakes
gh/subprocess/time at the queue_gh.py/loop.py boundary rather than mocking
the network."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from atlas import judge_gate

_RUN_ID = "r" * 32
_SPAN_ID = "s" * 32


@dataclass
class _FakeJudgeResult:
    metric_name: str
    scorer_version: str
    rationale: str
    tokens_in: int = 10
    tokens_out: int = 5
    latency_ms: float = 12.0
    value_numeric: float | None = None
    value_label: str | None = None


class _FakeAdapter:
    def __init__(self, result: _FakeJudgeResult, *, raises: Exception | None = None) -> None:
        self._result = result
        self._raises = raises
        self.calls: list[dict[str, object]] = []

    def score(self, **kwargs: object) -> _FakeJudgeResult:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._result


# ---------------------------------------------------------------------------
# score_diff
# ---------------------------------------------------------------------------


def test_score_diff_passes_above_threshold() -> None:
    adapter = _FakeAdapter(
        _FakeJudgeResult(
            metric_name="task_completion",
            scorer_version="anthropic:haiku:abc123",
            rationale="looks complete",
            value_numeric=0.9,
        )
    )
    with (
        patch("atlas.judge_gate._get_adapter", return_value=adapter),
        patch("atlas.judge_gate._write_score"),
    ):
        result = judge_gate.score_diff(
            diff_text="diff --git a b", run_id=_RUN_ID, span_id=_SPAN_ID, model="haiku"
        )
    assert result.passed is True
    assert result.value_numeric == pytest.approx(0.9)


def test_score_diff_fails_below_threshold() -> None:
    adapter = _FakeAdapter(
        _FakeJudgeResult(
            metric_name="task_completion",
            scorer_version="anthropic:haiku:abc123",
            rationale="incomplete",
            value_numeric=0.4,
        )
    )
    with (
        patch("atlas.judge_gate._get_adapter", return_value=adapter),
        patch("atlas.judge_gate._write_score"),
    ):
        result = judge_gate.score_diff(
            diff_text="diff --git a b",
            run_id=_RUN_ID,
            span_id=_SPAN_ID,
            model="haiku",
            threshold=0.7,
        )
    assert result.passed is False
    assert result.value_numeric == pytest.approx(0.4)


def test_score_diff_raises_when_provider_unset_no_call_attempted() -> None:
    """_get_adapter raising JudgeUnavailableError (mirrors get_judge_adapter's
    own ValueError on missing PLUMB_JUDGE_PROVIDER) must short-circuit before
    any adapter.score() call — CodexBackend.preflight()'s fail-closed-before-
    spawn pattern (TRD-v3 §3.3)."""
    with (
        patch(
            "atlas.judge_gate._get_adapter",
            side_effect=judge_gate.JudgeUnavailableError("PLUMB_JUDGE_PROVIDER not set"),
        ) as get_adapter_mock,
        patch("atlas.judge_gate._write_score") as write_mock,
    ):
        with pytest.raises(judge_gate.JudgeUnavailableError):
            judge_gate.score_diff(diff_text="diff", run_id=_RUN_ID, span_id=_SPAN_ID, model="haiku")
    get_adapter_mock.assert_called_once()
    write_mock.assert_not_called()


def test_score_diff_wraps_unexpected_adapter_exception() -> None:
    """A generic exception from adapter.score() (not JudgeUnavailableError)
    must still surface as JudgeUnavailableError — callers only ever handle
    one exception type from this boundary."""
    adapter = _FakeAdapter(
        _FakeJudgeResult(metric_name="task_completion", scorer_version="v1", rationale=""),
        raises=RuntimeError("network exploded"),
    )
    with patch("atlas.judge_gate._get_adapter", return_value=adapter):
        with pytest.raises(judge_gate.JudgeUnavailableError, match="network exploded"):
            judge_gate.score_diff(diff_text="diff", run_id=_RUN_ID, span_id=_SPAN_ID, model="haiku")


def test_write_score_real_path_never_raises() -> None:
    """_write_score's own body (not mocked here) must not propagate any
    storage failure — a lost score is a data-quality gap, not a control-flow
    failure."""
    adapter = _FakeAdapter(
        _FakeJudgeResult(
            metric_name="task_completion",
            scorer_version="anthropic:haiku:abc123",
            rationale="ok",
            value_numeric=0.85,
        )
    )
    with patch("atlas.judge_gate._get_adapter", return_value=adapter):
        result = judge_gate.score_diff(
            diff_text="diff", run_id=_RUN_ID, span_id=_SPAN_ID, model="haiku"
        )
    assert result.passed is True


def test_score_diff_raises_when_no_numeric_score_returned() -> None:
    """Adapter's own fail-open (`value_label="error"`) must surface as
    JudgeUnavailableError, not as a below-threshold score."""
    adapter = _FakeAdapter(
        _FakeJudgeResult(
            metric_name="task_completion",
            scorer_version="anthropic:haiku:abc123:error",
            rationale="timeout",
            value_label="error",
        )
    )
    with (
        patch("atlas.judge_gate._get_adapter", return_value=adapter),
        patch("atlas.judge_gate._write_score") as write_mock,
    ):
        with pytest.raises(judge_gate.JudgeUnavailableError):
            judge_gate.score_diff(diff_text="diff", run_id=_RUN_ID, span_id=_SPAN_ID, model="haiku")
    write_mock.assert_not_called()


def test_score_diff_writes_score_anchored_to_real_span() -> None:
    adapter = _FakeAdapter(
        _FakeJudgeResult(
            metric_name="task_completion",
            scorer_version="anthropic:haiku:abc123",
            rationale="ok",
            value_numeric=0.8,
        )
    )
    with (
        patch("atlas.judge_gate._get_adapter", return_value=adapter),
        patch("atlas.judge_gate._write_score") as write_mock,
    ):
        judge_gate.score_diff(diff_text="diff", run_id=_RUN_ID, span_id=_SPAN_ID, model="haiku")
    write_mock.assert_called_once()
    kwargs = write_mock.call_args.kwargs
    assert kwargs["run_id"] == _RUN_ID
    assert kwargs["span_id"] == _SPAN_ID
    assert kwargs["span_id"] != ""
    assert kwargs["metric_name"] == "task_completion"
    assert kwargs["value_numeric"] == pytest.approx(0.8)


def test_score_diff_passes_explicit_timeout_not_adapter_default() -> None:
    adapter = _FakeAdapter(
        _FakeJudgeResult(
            metric_name="task_completion", scorer_version="v1", rationale="ok", value_numeric=0.9
        )
    )
    with (
        patch("atlas.judge_gate._get_adapter", return_value=adapter),
        patch("atlas.judge_gate._write_score"),
    ):
        judge_gate.score_diff(
            diff_text="diff",
            run_id=_RUN_ID,
            span_id=_SPAN_ID,
            model="haiku",
            timeout_s=45.0,
        )
    assert adapter.calls[0]["timeout_s"] == 45.0


# ---------------------------------------------------------------------------
# classify_failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,retryable",
    [
        ("flaky", True),
        ("wrong_approach", True),
        ("missing_context", True),
        ("infeasible", False),
    ],
)
def test_classify_failure_maps_each_mode(mode: str, retryable: bool) -> None:
    adapter = _FakeAdapter(
        _FakeJudgeResult(
            metric_name="failure_mode",
            scorer_version="anthropic:haiku:def456",
            rationale=f"{mode}: some explanation",
            value_label="fail",
        )
    )
    with (
        patch("atlas.judge_gate._get_adapter", return_value=adapter),
        patch("atlas.judge_gate._write_score"),
    ):
        result = judge_gate.classify_failure(
            diff_text="diff",
            failure_context="verify failed",
            run_id=_RUN_ID,
            span_id=_SPAN_ID,
            model="haiku",
        )
    assert result.mode == mode
    assert result.retryable is retryable
    assert result.rationale == "some explanation"


def test_classify_failure_unparseable_defaults_to_wrong_approach(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _FakeAdapter(
        _FakeJudgeResult(
            metric_name="failure_mode",
            scorer_version="v1",
            rationale="I'm not sure what happened here",
            value_label="fail",
        )
    )
    with (
        patch("atlas.judge_gate._get_adapter", return_value=adapter),
        patch("atlas.judge_gate._write_score"),
        caplog.at_level(logging.WARNING, logger="atlas.judge_gate"),
    ):
        result = judge_gate.classify_failure(
            diff_text="diff",
            failure_context="ctx",
            run_id=_RUN_ID,
            span_id=_SPAN_ID,
            model="haiku",
        )
    assert result.mode == "wrong_approach"
    assert result.retryable is True
    assert any("unparseable" in r.message for r in caplog.records)


def test_classify_failure_wraps_unexpected_adapter_exception() -> None:
    adapter = _FakeAdapter(
        _FakeJudgeResult(metric_name="failure_mode", scorer_version="v1", rationale=""),
        raises=RuntimeError("network exploded"),
    )
    with patch("atlas.judge_gate._get_adapter", return_value=adapter):
        with pytest.raises(judge_gate.JudgeUnavailableError, match="network exploded"):
            judge_gate.classify_failure(
                diff_text="diff",
                failure_context="ctx",
                run_id=_RUN_ID,
                span_id=_SPAN_ID,
                model="haiku",
            )


def test_classify_failure_raises_when_provider_unset() -> None:
    with (
        patch(
            "atlas.judge_gate._get_adapter",
            side_effect=judge_gate.JudgeUnavailableError("PLUMB_JUDGE_PROVIDER not set"),
        ),
        patch("atlas.judge_gate._write_score") as write_mock,
    ):
        with pytest.raises(judge_gate.JudgeUnavailableError):
            judge_gate.classify_failure(
                diff_text="diff",
                failure_context="ctx",
                run_id=_RUN_ID,
                span_id=_SPAN_ID,
                model="haiku",
            )
    write_mock.assert_not_called()


def test_classify_failure_fail_open_label_raises_unavailable() -> None:
    adapter = _FakeAdapter(
        _FakeJudgeResult(
            metric_name="failure_mode",
            scorer_version="v1:error",
            rationale="adapter internal failure",
            value_label="error",
        )
    )
    with (
        patch("atlas.judge_gate._get_adapter", return_value=adapter),
        patch("atlas.judge_gate._write_score") as write_mock,
    ):
        with pytest.raises(judge_gate.JudgeUnavailableError):
            judge_gate.classify_failure(
                diff_text="diff",
                failure_context="ctx",
                run_id=_RUN_ID,
                span_id=_SPAN_ID,
                model="haiku",
            )
    write_mock.assert_not_called()
