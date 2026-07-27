"""Diagnosis-injected single-retry (TRD-v3 §14 Phase L3, §13 #9).

Orchestrates: ``write_example`` -> ``classify_failure`` -> retryable check ->
a single ``run_one_shot``/``run_planned_first_pass`` retry -> outcome. Never
recurses — ``tick()`` (T-L3.7) calls ``handle_failure`` at most once per
failed dispatch, and a second failure on the retried run is NOT fed back
into ``handle_failure`` again. That is the entire enforcement mechanism for
TRD-v3 §13 #9's "retried once" cap (Pending Decision #6: control-flow-only,
no persisted counter) — see T-L3.8's dedicated invariant test.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from atlas.config import Config
from atlas.deliverer import PrRef
from atlas.judge_gate import FailureClassification, JudgeUnavailableError, classify_failure
from atlas.loop import AbortedRunError, run_one_shot, run_planned_first_pass
from atlas.plumb_io import PlumbIO
from atlas.queue_gh import Issue

_logger = logging.getLogger("atlas.self_heal")

SelfHealOutcome = Literal["retried_success", "retried_failed", "not_retryable"]

_CLASSIFY_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class SelfHealResult:
    outcome: SelfHealOutcome
    pr_ref: PrRef | None
    run_id: str | None
    classification: FailureClassification | None
    detail: str
    cost: float = 0.0


def handle_failure(
    issue: Issue,
    exc: AbortedRunError,
    cfg: Config,
    *,
    repo_root: Path,
    original_run_id: str,
    diff_text: str | None,
    lane: Literal["quick", "planned"],
) -> SelfHealResult:
    """One retry attempt max — see the module docstring.

    ``diff_text`` is the failed run's worktree diff when one is available
    (``AbortedRunError.worktree_path`` was set and readable) — None for
    failures with no diff (e.g. the planned lane, or a dispatch failure
    before any worktree existed). ``lane`` selects the retry dispatcher:
    the quick lane's ``run_one_shot`` (which re-runs the judge gate) or the
    planned lane's ``run_planned_first_pass`` (which never does — Pending
    Decision #2, no code diff exists on a plan-only PR to score).
    """
    # task_id must be non-empty: plumb's Example entity validates it, and
    # that validation happens outside write_example's own try/except, so an
    # empty task_id would raise past this function entirely rather than
    # degrade gracefully.
    plumb = PlumbIO(real=True, task_id=f"issue-{issue.number}")
    diagnosis_run_id = plumb.reopen_run(original_run_id)
    span_id = plumb.record_span(
        run_id=diagnosis_run_id,
        kind="handoff",
        name="self_heal_diagnosis",
        status="failure",
        latency_ms=0.0,
        error_type=type(exc).__name__,
    )

    # Capture the failure regardless of what happens next — losing the
    # example is worse than losing the retry (write_example never raises).
    plumb.write_example(
        run_id=diagnosis_run_id,
        span_id=span_id,
        inputs=diff_text if diff_text is not None else issue.body,
        expected=None,
    )

    try:
        classification = classify_failure(
            diff_text=diff_text or "(no diff produced)",
            failure_context=str(exc),
            run_id=diagnosis_run_id,
            span_id=span_id,
            model=cfg.model,
            timeout_s=_CLASSIFY_TIMEOUT_S,
        )
    except JudgeUnavailableError as unavailable_exc:
        # Fail-to-safe (Pending Decision #5) — an unclassified failure must
        # not blind-retry.
        _logger.warning(
            "self_heal: judge unavailable for issue #%s; failing to not_retryable: %s",
            issue.number,
            unavailable_exc,
        )
        plumb.close_run(run_id=diagnosis_run_id, status="failure")
        return SelfHealResult(
            outcome="not_retryable",
            pr_ref=None,
            run_id=None,
            classification=None,
            detail=f"judge unavailable, failing to safe: {unavailable_exc}",
        )

    if not classification.retryable:
        plumb.close_run(run_id=diagnosis_run_id, status="failure")
        return SelfHealResult(
            outcome="not_retryable",
            pr_ref=None,
            run_id=None,
            classification=classification,
            detail=f"{classification.mode}: {classification.rationale}",
        )

    plumb.close_run(run_id=diagnosis_run_id, status="success")

    diagnosis = f"{classification.mode}: {classification.rationale}"
    retry = run_one_shot if lane == "quick" else run_planned_first_pass
    try:
        pr_ref, run_id, cost = retry(
            issue,
            cfg,
            repo_root=repo_root,
            parent_run_id=original_run_id,
            diagnosis=diagnosis,
        )
    except AbortedRunError as retry_exc:
        # Single retry exhausted — do NOT call handle_failure() again here;
        # tick() (T-L3.7) must not either. This is the entire retry cap.
        return SelfHealResult(
            outcome="retried_failed",
            pr_ref=None,
            run_id=None,
            classification=classification,
            detail=f"retry failed: {retry_exc}",
        )

    return SelfHealResult(
        outcome="retried_success",
        pr_ref=pr_ref,
        run_id=run_id,
        classification=classification,
        detail=f"retry succeeded after diagnosing {classification.mode}",
        cost=cost,
    )


__all__ = ["SelfHealOutcome", "SelfHealResult", "handle_failure"]
