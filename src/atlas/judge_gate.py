"""Pre-PR judge gate + failure-mode classification (TRD-v3 §14 Phase L3).

Two distinct plumb judge calls, both via the *library* ``JudgeAdapter``
Protocol (``plumb.adapters.get_judge_adapter``), NOT the `plumb judge run`
batch CLI — that CLI only scores already-persisted, un-scored runs in a
batch pass (confirmed against `plumb/_cli_judge.py`); this module needs a
synchronous score mid-``run_one_shot()``, before the run's PR ever opens.

plumb's judge reply contract (`plumb/adapters/_judge_common.py::parse_reply`)
only recognizes a verdict of `"pass"`, `"fail"`, or a bare number — never an
arbitrary label. `score_diff` uses the numeric form (a task-completion score
in [0, 1]). `classify_failure` has four failure modes to report, which that
verdict shape cannot carry directly, so its prompt is written to always
return `verdict: "fail"` and encode the mode as a leading token in
`rationale` (`"<mode>: <explanation>"`), parsed back out by
`_parse_failure_mode` — with the same "unparseable defaults toward more
oversight" convention `triage._parse_classify_response` already uses.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

_logger = logging.getLogger("atlas.judge_gate")

FailureMode = Literal["flaky", "wrong_approach", "missing_context", "infeasible"]

_RETRYABLE_MODES: frozenset[str] = frozenset({"flaky", "wrong_approach", "missing_context"})

_DEFAULT_TIMEOUT_S = 60.0
_TASK_COMPLETION_METRIC = "task_completion"
_FAILURE_MODE_METRIC = "failure_mode"


@dataclass(frozen=True)
class JudgeGateResult:
    passed: bool
    value_numeric: float
    rationale: str
    scorer_version: str


@dataclass(frozen=True)
class FailureClassification:
    mode: FailureMode
    rationale: str
    retryable: bool


class JudgeUnavailableError(Exception):
    """Raised when PLUMB_JUDGE_PROVIDER is unset/misconfigured, the prompt
    file for the metric is missing, plumb itself is not installed, or the
    adapter's own internal retries were exhausted and it fell back to its
    fail-open ``value_label="error"`` reply.

    Callers decide fail-open vs. fail-to-safe themselves (Pending Decision
    #5) — this module never makes that call on their behalf.
    """


def _get_adapter(metric_name: str) -> Any:
    """Instantiate the configured JudgeAdapter for *metric_name*.

    Imports are local (not module-level) so importing ``judge_gate`` never
    pulls in ``anthropic``/``openai`` or requires plumb to be installed —
    mirrors ``get_judge_adapter``'s own NFR-Perf-6 lazy-import discipline.
    """
    try:
        from plumb.adapters import get_judge_adapter
        from plumb.config import get_settings
    except ModuleNotFoundError as exc:
        raise JudgeUnavailableError(f"plumb is not installed: {exc}") from exc

    try:
        return get_judge_adapter(get_settings(), metric_name=metric_name)
    except (ValueError, FileNotFoundError) as exc:
        raise JudgeUnavailableError(str(exc)) from exc


def _write_score(
    *,
    run_id: str,
    span_id: str,
    metric_name: str,
    value_numeric: float | None,
    value_label: str | None,
    rationale: str,
    scorer_version: str,
) -> None:
    """Write a `scores` row directly through plumb's storage writer.

    There is no open ``RunHandle`` at this point in ``run_one_shot`` (the
    run already closed before the judge gate runs), so this cannot go
    through ``RunHandle.add_score`` — it uses the same interim direct-writer
    path ``PlumbIO.write_example`` already relies on, and the same `Score(...)`
    construction `plumb/_cli_judge.py:98-108` uses for its own batch writes.
    Never raises: a lost score is a data-quality gap, not a control-flow
    failure (matches `PlumbIO.write_example`'s own swallow-and-warn posture).
    """
    from plumb.api import _storage_writer
    from plumb.core.entities import Score, ScorerKind

    try:
        # Score(...) construction itself validates run_id/span_id shape and
        # raises on a mismatch — that validation must be inside this guard
        # too, not just the write call, or "never raises" would be false.
        score = Score(
            score_id=uuid.uuid4().hex,
            run_id=run_id,
            metric_name=metric_name,
            scorer=ScorerKind.JUDGE,
            scorer_version=scorer_version,
            scored_at=datetime.now(UTC),
            span_id=span_id,
            value_numeric=value_numeric,
            value_label=value_label,
            rationale=rationale,
        )
        _storage_writer.write_score(score)
    except Exception:
        _logger.warning(
            "judge_gate: durable score write failed for run_id=%s metric=%s", run_id, metric_name
        )


def score_diff(
    *,
    diff_text: str,
    run_id: str,
    span_id: str,
    model: str,
    threshold: float = 0.7,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> JudgeGateResult:
    """Score *diff_text* for task-completion via plumb's JudgeAdapter.score().

    ``run_id``/``span_id`` anchor the written `scores` row to the real
    `code_gen` span that produced the diff — never a dangling ``span_id=""``
    (the discipline the L2 code review enforced for `sync_prior_prs`).

    Raises ``JudgeUnavailableError`` if no judge provider is configured, the
    `judge_prompts/task_completion.md` file is missing, or the adapter could
    not produce a numeric score (including its own internal fail-open path)
    — callers must fail OPEN on this per Pending Decision #5, not treat
    "judge unavailable" the same as "judge said no".
    """
    adapter = _get_adapter(_TASK_COMPLETION_METRIC)
    try:
        result = adapter.score(
            metric_name=_TASK_COMPLETION_METRIC,
            prompt="",
            content=diff_text,
            model=model,
            timeout_s=timeout_s,
        )
    except JudgeUnavailableError:
        raise
    except Exception as exc:
        raise JudgeUnavailableError(f"judge call failed: {exc}") from exc

    if result.value_numeric is None:
        # judge_prompts/task_completion.md asks for a numeric verdict; a
        # label reply (including the adapter's own "error" fail-open label)
        # means the judge did not honor that contract. Surfacing this as
        # "unavailable" — not "score of 0" — lets the caller apply its own
        # fail-open policy instead of silently blocking every PR.
        raise JudgeUnavailableError(
            f"judge returned no numeric score (value_label={result.value_label!r}): "
            f"{result.rationale}"
        )

    _write_score(
        run_id=run_id,
        span_id=span_id,
        metric_name=_TASK_COMPLETION_METRIC,
        value_numeric=result.value_numeric,
        value_label=None,
        rationale=result.rationale,
        scorer_version=result.scorer_version,
    )

    return JudgeGateResult(
        passed=result.value_numeric >= threshold,
        value_numeric=result.value_numeric,
        rationale=result.rationale,
        scorer_version=result.scorer_version,
    )


def classify_failure(
    *,
    diff_text: str,
    failure_context: str,
    run_id: str,
    span_id: str,
    model: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> FailureClassification:
    """Classify a failed run's failure mode via a second, differently-
    prompted judge call (`judge_prompts/failure_mode.md`, metric
    `failure_mode`).

    Unparseable/unexpected judge output defaults to
    `mode="wrong_approach"`, `retryable=True`, logged at WARNING — the same
    "ambiguous defaults to the lane needing more human oversight, never
    silently drops the issue" posture `triage._parse_classify_response`
    already uses for its own unparseable-response default.

    Raises ``JudgeUnavailableError`` identically to `score_diff` — callers
    must fail to `not_retryable` on this (Pending Decision #5), not blind-retry.
    """
    adapter = _get_adapter(_FAILURE_MODE_METRIC)
    content = f"Failure context:\n{failure_context}\n\nDiff (if any):\n{diff_text}"
    try:
        result = adapter.score(
            metric_name=_FAILURE_MODE_METRIC,
            prompt="",
            content=content,
            model=model,
            timeout_s=timeout_s,
        )
    except JudgeUnavailableError:
        raise
    except Exception as exc:
        raise JudgeUnavailableError(f"judge call failed: {exc}") from exc

    if result.value_label == "error":
        raise JudgeUnavailableError(f"judge failed open: {result.rationale}")

    mode, rationale = _parse_failure_mode(result.rationale)

    _write_score(
        run_id=run_id,
        span_id=span_id,
        metric_name=_FAILURE_MODE_METRIC,
        value_numeric=None,
        value_label=mode,
        rationale=rationale,
        scorer_version=result.scorer_version,
    )

    return FailureClassification(mode=mode, rationale=rationale, retryable=mode in _RETRYABLE_MODES)


def _parse_failure_mode(rationale: str) -> tuple[FailureMode, str]:
    stripped = rationale.strip()
    first_token, _, rest = stripped.partition(":")
    candidate = first_token.strip().lower()
    detail = rest.strip() or stripped

    if candidate == "flaky":
        return "flaky", detail
    if candidate == "wrong_approach":
        return "wrong_approach", detail
    if candidate == "missing_context":
        return "missing_context", detail
    if candidate == "infeasible":
        return "infeasible", detail

    _logger.warning(
        "judge_gate: unparseable failure-mode rationale %r; defaulting to wrong_approach",
        stripped[:200],
    )
    return "wrong_approach", f"unparseable classifier rationale: {stripped[:200]}"


__all__ = [
    "FailureClassification",
    "FailureMode",
    "JudgeGateResult",
    "JudgeUnavailableError",
    "classify_failure",
    "score_diff",
]
