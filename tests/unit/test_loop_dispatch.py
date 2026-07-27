"""Unit tests for atlas.loop's tick() dispatch path — idle/dispatch/trust,
self-heal retry branches, and trusted_authors enforcement (T-L2.5, T-L3.7).

Split out of test_loop.py to stay under this repo's 800-line cap. Budget/
breaker primitives, sync_prior_prs, reconcile_orphans, and run_forever live
in test_loop.py; run_one_shot/judge-gate/field-finding tests live in
test_loop_run_one_shot.py; Phase L4 per-target/claim-race/batch-dispatch
tests live in test_loop_l4.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from atlas import loop
from atlas.config import Config, LoopConfig, RepoTarget
from atlas.deliverer import DeliveryError, PrRef
from atlas.queue_gh import Issue

_REPO = "anant-gupta-utexas/atlas"


def _target(
    local_path: Path, github: str = _REPO, trusted_authors: tuple[str, ...] = ()
) -> RepoTarget:
    return RepoTarget(github=github, local_path=local_path, trusted_authors=trusted_authors)


def _targets(local_path: Path, **kwargs: object) -> tuple[RepoTarget, ...]:
    return (_target(local_path, **kwargs),)  # type: ignore[arg-type]


def _cfg(tmp_path: Path, **loop_kwargs: object) -> Config:
    loop_kwargs.setdefault("repos", _targets(tmp_path))
    loop_cfg = LoopConfig(**loop_kwargs)  # type: ignore[arg-type]
    return Config(repo_root=tmp_path, plumb_db_path=tmp_path / "plumb.db", loop=loop_cfg)


def _issue(number: int = 1, labels: frozenset[str] = frozenset(), author: str = "a") -> Issue:
    return Issue(
        number=number, title="Fix bug", body="details", labels=labels, repo=_REPO, author=author
    )


def _state(**kwargs: object) -> loop.LoopState:
    kwargs.setdefault("day", loop._today())
    return loop.LoopState(**kwargs)  # type: ignore[arg-type]


def _home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def _first(batch: loop.BatchTickResult) -> loop.TickResult:
    assert len(batch.results) == 1
    return batch.results[0]


def _tick(
    cfg: Config, state: loop.LoopState, targets: tuple[RepoTarget, ...]
) -> loop.BatchTickResult:
    return loop.tick(cfg, state, targets=targets)


def test_tick_idle_no_ready_issue_sync_still_ran(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import MagicMock

    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    sync_mock = MagicMock(return_value=[])
    with (
        patch("atlas.loop.sync_prior_prs", sync_mock),
        patch("atlas.loop._pull_ready_batch", return_value=[]),
    ):
        batch = _tick(cfg, state, cfg.loop.repos)
    assert _first(batch).action == "idle"
    sync_mock.assert_called_once()


def test_tick_breaker_open_returns_early(tmp_path: Path, monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    future = (datetime.now(tz=UTC) + timedelta(minutes=10)).isoformat()
    state = _state(breaker_open_until=future)
    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch") as pull_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)
    assert _first(batch).action == "breaker_open"
    pull_mock.assert_not_called()


def test_tick_budget_exhausted_returns_early(tmp_path: Path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path, max_runs_per_day=1)
    state = _state(runs_today=1)
    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch") as pull_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)
    assert _first(batch).action == "budget_exhausted"
    pull_mock.assert_not_called()


def test_tick_dispatches_quick_lane(tmp_path: Path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:quick"}))
    pr_ref = PrRef(number=42, url="https://example/pull/42")

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim") as claim_mock,
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot", return_value=(pr_ref, "run-1", 0.5)) as run_mock,
        patch("atlas.loop.run_planned_first_pass") as planned_mock,
        patch("atlas.queue_gh.comment") as comment_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    claim_mock.assert_called_once()
    run_mock.assert_called_once()
    planned_mock.assert_not_called()
    comment_mock.assert_called_once()
    result = _first(batch)
    assert result.action == "dispatched"
    assert result.lane == "quick"
    assert result.pr_ref == pr_ref
    assert state.runs_today == 1
    assert state.dollars_today == 0.5


def test_tick_dispatches_planned_lane_stops_after_trs(tmp_path: Path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:planned"}))
    pr_ref = PrRef(number=43, url="https://example/pull/43")

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot") as quick_mock,
        patch(
            "atlas.loop.run_planned_first_pass", return_value=(pr_ref, "run-2", 0.2)
        ) as planned_mock,
        patch("atlas.queue_gh.comment"),
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    planned_mock.assert_called_once()
    quick_mock.assert_not_called()
    result = _first(batch)
    assert result.lane == "planned"
    assert result.action == "dispatched"


def test_tick_claims_before_dispatch(tmp_path: Path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:quick"}))
    call_order: list[str] = []

    def claim_side_effect(*args: object, **kwargs: object) -> None:
        call_order.append("claim")

    def run_side_effect(*args: object, **kwargs: object) -> tuple[PrRef, str, float]:
        call_order.append("dispatch")
        return PrRef(number=1, url="u"), "r1", 0.0

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim", side_effect=claim_side_effect),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot", side_effect=run_side_effect),
        patch("atlas.queue_gh.comment"),
    ):
        _tick(cfg, state, cfg.loop.repos)

    assert call_order == ["claim", "dispatch"]


def test_tick_failed_run_no_pr_but_comments(tmp_path: Path, monkeypatch) -> None:
    """AbortedRunError now routes through self_heal (T-L3.7); with no judge
    provider configured, classify_failure fails to not_retryable (Pending
    Decision #5) and the issue is relabeled atlas:blocked rather than left
    silently atlas:working (L2's old behavior for this exception type)."""
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:quick"}))

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot", side_effect=loop.AbortedRunError("boom")),
        patch("atlas.queue_gh.comment") as comment_mock,
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    result = _first(batch)
    # action is "failed", not "dispatched" — nothing was delivered, and
    # counting dispatches by action must not over-count (I1).
    assert result.action == "failed"
    assert result.pr_ref is None
    assert "judge unavailable" in result.detail
    relabel_mock.assert_called_once_with(issue, state="blocked")
    comment_mock.assert_called_once()
    assert "judge unavailable" in comment_mock.call_args.kwargs["body"]


def test_tick_delivery_failure_leaves_issue_working_no_crash(tmp_path: Path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:quick"}))

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot", side_effect=DeliveryError("push failed")),
        patch("atlas.queue_gh.comment") as comment_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    result = _first(batch)
    assert result.action == "failed"
    assert result.pr_ref is None
    comment_mock.assert_called_once()


def test_tick_retried_success_matches_first_try_success_shape(tmp_path: Path, monkeypatch) -> None:
    """retried_success must produce the same TickResult/label/comment shape
    as a first-try success (T-L3.7 acceptance: operator-visible parity) —
    action='dispatched', no relabel call, a run-summary comment."""
    import pytest

    import atlas.self_heal as self_heal_module

    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:quick"}))
    pr_ref = PrRef(number=42, url="https://example.com/pulls/42")
    heal_result = self_heal_module.SelfHealResult(
        outcome="retried_success",
        pr_ref=pr_ref,
        run_id="child-run",
        classification=None,
        detail="retried",
        cost=0.75,
    )

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot", side_effect=loop.AbortedRunError("boom")),
        patch("atlas.self_heal.handle_failure", return_value=heal_result),
        patch("atlas.queue_gh.comment") as comment_mock,
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    result = _first(batch)
    assert result.action == "dispatched"
    assert result.pr_ref == pr_ref
    relabel_mock.assert_not_called()
    comment_mock.assert_called_once()
    assert "child-run" in comment_mock.call_args.kwargs["body"]
    assert state.dollars_today == pytest.approx(0.75)
    assert state.runs_today == 1


def test_tick_blocked_comment_names_mode_and_both_run_ids(tmp_path: Path, monkeypatch) -> None:
    import atlas.self_heal as self_heal_module
    from atlas.judge_gate import FailureClassification

    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:quick"}))
    classification = FailureClassification(
        mode="wrong_approach", rationale="tried the wrong file", retryable=True
    )
    heal_result = self_heal_module.SelfHealResult(
        outcome="retried_failed",
        pr_ref=None,
        run_id="retry-run-id",
        classification=classification,
        detail="retry failed: still broken",
    )

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch(
            "atlas.loop.run_one_shot",
            side_effect=loop.AbortedRunError("boom", run_id="original-run-id"),
        ),
        patch("atlas.self_heal.handle_failure", return_value=heal_result),
        patch("atlas.queue_gh.comment") as comment_mock,
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    result = _first(batch)
    assert result.action == "failed"
    relabel_mock.assert_called_once_with(issue, state="blocked")
    comment_mock.assert_called_once()
    body = comment_mock.call_args.kwargs["body"]
    assert "wrong_approach" in body
    assert "original-run-id" in body
    assert "retry-run-id" in body


def test_tick_records_outcome_on_retry_branches(tmp_path: Path, monkeypatch) -> None:
    """record_tick_outcome must fire on every self-heal branch — existing
    L2 budget/breaker bookkeeping must not regress (T-L3.7 acceptance)."""
    import atlas.self_heal as self_heal_module

    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path, no_progress_limit=1, identical_error_limit=100)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:quick"}))
    heal_result = self_heal_module.SelfHealResult(
        outcome="not_retryable",
        pr_ref=None,
        run_id=None,
        classification=None,
        detail="infeasible",
    )

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot", side_effect=loop.AbortedRunError("boom")),
        patch("atlas.self_heal.handle_failure", return_value=heal_result),
        patch("atlas.queue_gh.comment"),
        patch("atlas.queue_gh.relabel"),
    ):
        _tick(cfg, state, cfg.loop.repos)

    # no_progress_limit=1 means a single made_progress=False call opens the
    # breaker — proves record_tick_outcome actually ran on this branch.
    assert loop.breaker_open(state, cfg.loop)


def test_trusted_authors_empty_means_no_check(tmp_path: Path) -> None:
    target = _target(tmp_path, trusted_authors=())
    issue = _issue(author="anyone")
    with patch("atlas.queue_gh.list_ready", return_value=[issue]):
        result = loop._pull_ready_batch((target,), LoopConfig(), limit=1)
    assert result == [(target, issue)]


def test_trusted_authors_enforced_when_configured(tmp_path: Path) -> None:
    target = _target(tmp_path, trusted_authors=("trusted-user",))
    untrusted = _issue(number=1, author="random")
    trusted = _issue(number=2, author="trusted-user")
    with patch("atlas.queue_gh.list_ready", return_value=[untrusted, trusted]):
        result = loop._pull_ready_batch((target,), LoopConfig(), limit=1)
    assert result == [(target, trusted)]


def test_trusted_authors_all_untrusted_returns_none(tmp_path: Path) -> None:
    target = _target(tmp_path, trusted_authors=("trusted-user",))
    untrusted = _issue(number=1, author="random")
    with patch("atlas.queue_gh.list_ready", return_value=[untrusted]):
        result = loop._pull_ready_batch((target,), LoopConfig(), limit=1)
    assert result == []


def test_trusted_authors_enforced_at_tick_claim_boundary(tmp_path: Path, monkeypatch) -> None:
    """T-L2.12 checkpoint: trusted_authors enforcement must be wired into the
    actual tick()/claim()/dispatch path, not just present as a config field
    and a helper-level unit test (_pull_ready_batch above) — this is the test
    the TRD's §4 Security section asks for. An untrusted-author issue must
    never reach claim() or a dispatch function; it must be silently skipped
    (idle tick), per Decision #16 (skipped, not relabeled to an error state).
    _pull_ready_batch is intentionally NOT mocked here — it's the real
    enforcement point tick() calls into."""
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path, repos=_targets(tmp_path, trusted_authors=("trusted-user",)))
    state = _state()
    untrusted = _issue(number=1, author="random", labels=frozenset({"wf:quick"}))

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.queue_gh.list_ready", return_value=[untrusted]),
        patch("atlas.loop.current_gh_user") as current_user_mock,
        patch("atlas.queue_gh.claim") as claim_mock,
        patch("atlas.loop.run_one_shot") as run_one_shot_mock,
        patch("atlas.loop.run_planned_first_pass") as run_planned_mock,
        patch("atlas.queue_gh.comment") as comment_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    result = _first(batch)
    assert result.action == "idle"
    assert result.issue_number is None
    current_user_mock.assert_not_called()
    claim_mock.assert_not_called()
    run_one_shot_mock.assert_not_called()
    run_planned_mock.assert_not_called()
    comment_mock.assert_not_called()


def test_engine_label_selects_backend(tmp_path: Path) -> None:
    issue = _issue(labels=frozenset({"engine:codex"}))
    assert loop._engine_for_issue(issue) == "codex"

    issue2 = _issue(labels=frozenset({"engine:claude"}))
    assert loop._engine_for_issue(issue2) == "claude"

    issue3 = _issue(labels=frozenset())
    assert loop._engine_for_issue(issue3) is None


def test_tick_handles_current_gh_user_failure(tmp_path: Path, monkeypatch) -> None:
    """Closes the L2 known gap: loop.py's current_gh_user()-raises branch.

    Flagged untested in the Phase L2 TRS implementation notes.
    """
    from atlas.queue_gh import GhCliError

    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:quick"}))

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", side_effect=GhCliError("gh not authed")),
        patch("atlas.queue_gh.claim") as claim_mock,
        patch("atlas.loop.run_one_shot") as run_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    result = _first(batch)
    assert result.action == "failed"
    # Must fail *before* claiming or dispatching — a claim we can't attribute
    # would strand the issue in atlas:working.
    claim_mock.assert_not_called()
    run_mock.assert_not_called()
    assert state.consecutive_no_progress == 1


def test_idle_tick_does_not_feed_the_circuit_breaker(tmp_path: Path, monkeypatch) -> None:
    """An empty queue is not a failure.

    The idle path used to call record_tick_outcome(made_progress=False), so a
    perfectly healthy loop with nothing to do incremented
    consecutive_no_progress every tick and opened its own breaker after
    no_progress_limit ticks — 90 seconds at a 30s poll. Observed live during
    T-L2.13: the daemon halted itself while the queue was simply empty.
    """
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path, no_progress_limit=3)
    state = _state()

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[]),
    ):
        for _ in range(5):
            batch = _tick(cfg, state, cfg.loop.repos)

    assert _first(batch).action == "idle"
    assert state.consecutive_no_progress == 0
    assert state.breaker_open_until is None


def test_idle_tick_preserves_a_prior_error_signature(tmp_path: Path, monkeypatch) -> None:
    """A later idle tick must not erase why the previous tick failed.

    record_tick_outcome(error_signature=None) resets last_error_signature, so
    the reason a real dispatch failure had just occurred was destroyed by the
    next empty poll — leaving an opened breaker and no explanation anywhere.
    """
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    state.last_error_signature = "DeliveryError:no commits ahead of main"
    state.consecutive_no_progress = 1

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[]),
    ):
        _tick(cfg, state, cfg.loop.repos)

    assert state.last_error_signature == "DeliveryError:no commits ahead of main"


def test_sync_progress_on_an_idle_tick_still_resets_counters(tmp_path: Path, monkeypatch) -> None:
    """Real progress from sync_prior_prs must still clear the breaker counters."""
    from atlas.queue_gh import PrStatus

    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state(consecutive_no_progress=2)
    merged = [PrStatus(issue=_issue(), outcome="merged", pr_number=9)]

    with (
        patch("atlas.loop.sync_prior_prs", return_value=merged),
        patch("atlas.loop._pull_ready_batch", return_value=[]),
    ):
        _tick(cfg, state, cfg.loop.repos)

    assert state.consecutive_no_progress == 0


def test_budget_trips_on_dollars_once_cost_is_real(tmp_path: Path, monkeypatch) -> None:
    """The dollar cap now has teeth — previously unreachable with 0.0 costs."""
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path, max_runs_per_day=100, max_dollars_per_day=2.0)
    state = _state(runs_today=1, dollars_today=2.5)

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch") as pull_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    assert _first(batch).action == "budget_exhausted"
    pull_mock.assert_not_called()
