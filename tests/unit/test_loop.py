"""Unit tests for atlas.loop — budgets, breaker, LoopState persistence,
sync_prior_prs, reconcile_orphans, run_forever (T-L2.5 through T-L2.8).

tick()'s own dispatch-path tests live in test_loop_dispatch.py;
run_one_shot/judge-gate/field-finding tests live in test_loop_run_one_shot.py;
Phase L4's per-target/claim-race/batch-dispatch tests live in
test_loop_l4.py. Split across files to stay under this repo's 800-line cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from atlas import loop, loop_budget
from atlas.config import Config, LoopConfig, RepoTarget
from atlas.queue_gh import Issue, PrStatus

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


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ~/.atlas/loop-state.json at a tmp dir (Phase L4 relocation)."""
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


# ---------------------------------------------------------------------------
# budget_exhausted / breaker_open / record_tick_outcome
# ---------------------------------------------------------------------------


def test_budget_exhausted_runs() -> None:
    cfg = LoopConfig(max_runs_per_day=2, max_dollars_per_day=100.0)
    state = _state(runs_today=2, dollars_today=0.0)
    assert loop.budget_exhausted(state, cfg)


def test_budget_exhausted_dollars() -> None:
    cfg = LoopConfig(max_runs_per_day=100, max_dollars_per_day=1.0)
    state = _state(runs_today=0, dollars_today=1.0)
    assert loop.budget_exhausted(state, cfg)


def test_budget_not_exhausted_under_both_caps() -> None:
    cfg = LoopConfig(max_runs_per_day=5, max_dollars_per_day=5.0)
    state = _state(runs_today=1, dollars_today=1.0)
    assert not loop.budget_exhausted(state, cfg)


def test_breaker_closed_when_no_open_until() -> None:
    cfg = LoopConfig()
    state = _state(breaker_open_until=None)
    assert not loop.breaker_open(state, cfg)


def test_breaker_open_when_until_in_future() -> None:
    cfg = LoopConfig()
    future = (datetime.now(tz=UTC) + timedelta(minutes=10)).isoformat()
    state = _state(breaker_open_until=future)
    assert loop.breaker_open(state, cfg)


def test_breaker_closed_when_until_in_past() -> None:
    cfg = LoopConfig()
    past = (datetime.now(tz=UTC) - timedelta(minutes=10)).isoformat()
    state = _state(breaker_open_until=past)
    assert not loop.breaker_open(state, cfg)


def test_record_tick_outcome_progress_resets_counters() -> None:
    cfg = LoopConfig()
    state = _state(
        consecutive_no_progress=2, consecutive_identical_errors=3, last_error_signature="x"
    )
    loop.record_tick_outcome(state, cfg, made_progress=True, error_signature=None)
    assert state.consecutive_no_progress == 0
    assert state.consecutive_identical_errors == 0
    assert state.last_error_signature is None


def test_breaker_opens_on_no_progress_limit() -> None:
    cfg = LoopConfig(no_progress_limit=3, identical_error_limit=100)
    state = _state()
    for _ in range(3):
        loop.record_tick_outcome(state, cfg, made_progress=False, error_signature=None)
    assert loop.breaker_open(state, cfg)


def test_breaker_opens_on_identical_error_limit() -> None:
    cfg = LoopConfig(no_progress_limit=100, identical_error_limit=2)
    state = _state()
    loop.record_tick_outcome(state, cfg, made_progress=False, error_signature="E1")
    loop.record_tick_outcome(state, cfg, made_progress=False, error_signature="E1")
    assert loop.breaker_open(state, cfg)


def test_breaker_does_not_open_below_thresholds() -> None:
    cfg = LoopConfig(no_progress_limit=5, identical_error_limit=5)
    state = _state()
    loop.record_tick_outcome(state, cfg, made_progress=False, error_signature="E1")
    assert not loop.breaker_open(state, cfg)


def test_breaker_open_until_set_to_cooldown() -> None:
    cfg = LoopConfig(no_progress_limit=1, cooldown_min=30)
    state = _state()
    loop.record_tick_outcome(state, cfg, made_progress=False, error_signature=None)
    assert state.breaker_open_until is not None
    until = datetime.fromisoformat(state.breaker_open_until)
    delta = until - datetime.now(tz=UTC)
    assert timedelta(minutes=29) < delta <= timedelta(minutes=30)


def test_budget_resets_on_day_rollover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path, max_runs_per_day=1)
    state = _state(runs_today=1, dollars_today=5.0, day="2000-01-01")
    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[]),
    ):
        batch = _tick(cfg, state, cfg.loop.repos)
    assert _first(batch).action == "idle"
    assert state.runs_today == 0
    assert state.dollars_today == 0.0


# ---------------------------------------------------------------------------
# LoopState persistence (Phase L4: user-wide ~/.atlas/loop-state.json)
# ---------------------------------------------------------------------------


def test_loop_state_persists_across_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home(tmp_path, monkeypatch)
    state = loop.LoopState(day="2026-01-01", runs_today=3)
    state.persist()
    loaded = loop.LoopState.load_or_init()
    assert loaded.runs_today == 3
    assert loaded.day == "2026-01-01"


def test_loop_state_missing_file_inits_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(tmp_path, monkeypatch)
    state = loop.LoopState.load_or_init()
    assert state.runs_today == 0
    assert state.day == loop._today()


def test_loop_state_corrupted_file_inits_fresh_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    path = home / ".atlas" / "loop-state.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json{{{")
    state = loop.LoopState.load_or_init()
    assert state.runs_today == 0


def test_loop_state_load_trims_oversized_dedupe_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-oversized state file from before the bound was introduced
    gets trimmed on load rather than persisting forever."""
    import json

    home = _home(tmp_path, monkeypatch)
    state_path = home / ".atlas" / "loop-state.json"
    state_path.parent.mkdir(parents=True)
    oversized = [f"k-{i}" for i in range(loop_budget._MAX_SYNCED_OUTCOMES + 250)]
    state_path.write_text(json.dumps({"day": loop._today(), "synced_pr_outcomes": oversized}))

    loaded = loop.LoopState.load_or_init()

    assert len(loaded.synced_pr_outcomes) == loop_budget._MAX_SYNCED_OUTCOMES
    assert loaded.synced_pr_outcomes[-1] == oversized[-1]  # newest retained


# ---------------------------------------------------------------------------
# sync_prior_prs
# ---------------------------------------------------------------------------


def test_sync_idempotent_on_repeat_tick() -> None:
    issue = _issue()
    status = PrStatus(issue=issue, outcome="merged", pr_number=9)
    state = _state()

    with (
        patch("atlas.queue_gh.sync", return_value=[status]),
        patch("atlas.queue_gh.find_run_id_comment", return_value="run-abc"),
        patch("atlas.queue_gh.relabel") as relabel_mock,
        patch("atlas.loop.PlumbIO") as plumb_cls,
    ):
        plumb_instance = plumb_cls.return_value
        plumb_instance.reopen_run.return_value = "run-abc"

        results1 = loop.sync_prior_prs(_REPO, state)
        results2 = loop.sync_prior_prs(_REPO, state)

    assert len(results1) == 1
    assert len(results2) == 0  # dedupe on second call
    assert plumb_instance.record_user_signal.call_count == 1
    assert relabel_mock.call_count == 1


def test_sync_merged_writes_success_signal_and_closes() -> None:
    issue = _issue()
    status = PrStatus(issue=issue, outcome="merged", pr_number=9)
    state = _state()

    with (
        patch("atlas.queue_gh.sync", return_value=[status]),
        patch("atlas.queue_gh.find_run_id_comment", return_value="run-abc"),
        patch("atlas.queue_gh.relabel") as relabel_mock,
        patch("atlas.loop.PlumbIO") as plumb_cls,
    ):
        plumb_instance = plumb_cls.return_value
        plumb_instance.reopen_run.return_value = "run-abc"
        loop.sync_prior_prs(_REPO, state)

    relabel_mock.assert_called_once_with(issue, state="done")
    decision = plumb_instance.record_user_signal.call_args.kwargs["decision"]
    assert decision.label == "approved"
    plumb_instance.close_run.assert_called_once_with(run_id="run-abc", status="success")


def test_sync_closed_writes_rejected_signal() -> None:
    issue = _issue()
    status = PrStatus(issue=issue, outcome="closed_unmerged", pr_number=10)
    state = _state()

    with (
        patch("atlas.queue_gh.sync", return_value=[status]),
        patch("atlas.queue_gh.find_run_id_comment", return_value="run-xyz"),
        patch("atlas.queue_gh.relabel") as relabel_mock,
        patch("atlas.loop.PlumbIO") as plumb_cls,
    ):
        plumb_instance = plumb_cls.return_value
        plumb_instance.reopen_run.return_value = "run-xyz"
        loop.sync_prior_prs(_REPO, state)

    relabel_mock.assert_called_once_with(issue, state="rejected")
    decision = plumb_instance.record_user_signal.call_args.kwargs["decision"]
    assert decision.label == "rejected"
    plumb_instance.close_run.assert_called_once_with(run_id="run-xyz", status="failure")


def test_sync_score_is_anchored_to_a_real_span() -> None:
    """C2 regression: the user_signal score must carry a span_id from a real
    record_span call, not an empty-string sentinel.

    Uses a genuine stub-mode PlumbIO (not a MagicMock) so the recorded rows
    are real dicts — a MagicMock would happily return a truthy mock for
    record_span and hide the defect.
    """
    from atlas.plumb_io import PlumbIO

    issue = _issue()
    status = PrStatus(issue=issue, outcome="merged", pr_number=9)
    state = _state()
    real_stub = PlumbIO(real=False)

    with (
        patch("atlas.queue_gh.sync", return_value=[status]),
        patch("atlas.queue_gh.find_run_id_comment", return_value="run-abc"),
        patch("atlas.queue_gh.relabel"),
        patch("atlas.loop.PlumbIO", return_value=real_stub),
    ):
        loop.sync_prior_prs(_REPO, state)

    assert len(real_stub.scores) == 1
    score = real_stub.scores[0]
    assert score["span_id"], "user_signal score has a falsy span_id"
    assert score["span_id"] in {s["span_id"] for s in real_stub.spans}
    assert score["value_label"] == "approved"


def test_sync_dedupe_list_is_bounded() -> None:
    """C2 regression: loop-state.json is rewritten every tick, so the dedupe
    list must not grow without limit in a long-running daemon."""
    state = _state()
    state.synced_pr_outcomes = [f"old-{i}" for i in range(loop_budget._MAX_SYNCED_OUTCOMES)]

    loop_budget.remember_synced_outcome(state, "newest")

    assert len(state.synced_pr_outcomes) == loop_budget._MAX_SYNCED_OUTCOMES
    assert state.synced_pr_outcomes[-1] == "newest"
    assert "old-0" not in state.synced_pr_outcomes  # oldest evicted


def test_sync_open_outcome_skipped() -> None:
    issue = _issue()
    status = PrStatus(issue=issue, outcome="open", pr_number=11)
    state = _state()

    with (
        patch("atlas.queue_gh.sync", return_value=[status]),
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        results = loop.sync_prior_prs(_REPO, state)

    assert results == []
    relabel_mock.assert_not_called()


def test_sync_no_run_id_still_relabels() -> None:
    issue = _issue()
    status = PrStatus(issue=issue, outcome="merged", pr_number=9)
    state = _state()

    with (
        patch("atlas.queue_gh.sync", return_value=[status]),
        patch("atlas.queue_gh.find_run_id_comment", return_value=None),
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        results = loop.sync_prior_prs(_REPO, state)

    assert len(results) == 1
    relabel_mock.assert_called_once_with(issue, state="done")


# ---------------------------------------------------------------------------
# reconcile_orphans
# ---------------------------------------------------------------------------


def test_reconcile_orphans_resets_stale_working_issue(tmp_path: Path) -> None:
    targets = _targets(tmp_path)
    working_issue = _issue(number=5)

    with (
        patch("atlas.queue_gh.list_labeled", return_value=[working_issue]),
        patch("atlas.queue_gh.sync", return_value=[]),
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        reconciled = loop.reconcile_orphans(_cfg(tmp_path), targets=targets)

    relabel_mock.assert_called_once_with(working_issue, state="ready")
    assert "issue #5" in reconciled


def test_reconcile_orphans_leaves_working_issue_with_open_pr(tmp_path: Path) -> None:
    targets = _targets(tmp_path)
    working_issue = _issue(number=5)
    status = PrStatus(issue=working_issue, outcome="open", pr_number=1)

    with (
        patch("atlas.queue_gh.list_labeled", return_value=[working_issue]),
        patch("atlas.queue_gh.sync", return_value=[status]),
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        reconciled = loop.reconcile_orphans(_cfg(tmp_path), targets=targets)

    relabel_mock.assert_not_called()
    assert reconciled == []


def test_reconcile_orphans_prunes_stale_worktrees(tmp_path: Path) -> None:
    targets = _targets(tmp_path)
    worktrees_dir = tmp_path / ".atlas" / "worktrees"
    stale = worktrees_dir / "old-issue-abcdef01"
    stale.mkdir(parents=True)

    with (
        patch("atlas.queue_gh.list_labeled", return_value=[]),
        patch("atlas.queue_gh.sync", return_value=[]),
        patch("atlas.loop.WorktreeManager") as wm_cls,
    ):
        wm_instance = wm_cls.return_value
        reconciled = loop.reconcile_orphans(_cfg(tmp_path), targets=targets)

    wm_instance.cleanup.assert_called_once_with(stale)
    assert f"worktree {stale.name}" in reconciled


def test_reconcile_orphans_cleanup_failure_logged_not_raised(tmp_path: Path) -> None:
    from atlas.worktree import WorktreeError

    targets = _targets(tmp_path)
    worktrees_dir = tmp_path / ".atlas" / "worktrees"
    stale = worktrees_dir / "old-issue-abcdef01"
    stale.mkdir(parents=True)

    with (
        patch("atlas.queue_gh.list_labeled", return_value=[]),
        patch("atlas.queue_gh.sync", return_value=[]),
        patch("atlas.loop.WorktreeManager") as wm_cls,
    ):
        wm_instance = wm_cls.return_value
        wm_instance.cleanup.side_effect = WorktreeError("stale ref")
        reconciled = loop.reconcile_orphans(_cfg(tmp_path), targets=targets)

    assert reconciled == []  # cleanup failed, not counted as reconciled


# ---------------------------------------------------------------------------
# run_forever — exception safety net
# ---------------------------------------------------------------------------


def test_run_forever_survives_unexpected_tick_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)

    call_count = {"n": 0}

    def fake_sleep(seconds: float) -> None:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise KeyboardInterrupt  # escape the while True loop after 2 iterations

    with (
        patch("atlas.loop.reconcile_orphans", return_value=[]),
        patch("atlas.loop_budget.migrate_legacy_state_if_needed"),
        patch("atlas.loop.LoopState.load_or_init", return_value=loop.LoopState(day=loop._today())),
        patch("atlas.loop.tick", side_effect=RuntimeError("boom")),
        patch("atlas.loop.time.sleep", side_effect=fake_sleep),
    ):
        with pytest.raises(KeyboardInterrupt):
            loop.run_forever(cfg, targets=cfg.loop.repos)

    assert call_count["n"] >= 2  # loop continued after tick() raised


def test_run_forever_calls_reconcile_orphans_once_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)

    with (
        patch("atlas.loop.reconcile_orphans", return_value=[]) as reconcile_mock,
        patch("atlas.loop_budget.migrate_legacy_state_if_needed"),
        patch("atlas.loop.LoopState.load_or_init", return_value=loop.LoopState(day=loop._today())),
        patch("atlas.loop.tick", return_value=None),
        patch("atlas.loop.time.sleep", side_effect=KeyboardInterrupt),
    ):
        with pytest.raises(KeyboardInterrupt):
            loop.run_forever(cfg, targets=cfg.loop.repos)

    reconcile_mock.assert_called_once()


def test_run_forever_breaker_open_dispatches_nothing_but_still_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An open breaker must not dispatch — but run_forever still calls tick(),
    which reports action="breaker_open" and refreshes last_tick_at (m5).

    Previously run_forever short-circuited before tick(), which froze
    last_tick_at and logged nothing, making a cooling-down daemon look dead
    in `atlas loop status`.
    """
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    future = (datetime.now(tz=UTC) + timedelta(minutes=10)).isoformat()
    state = loop.LoopState(day=loop._today(), breaker_open_until=future)

    with (
        patch("atlas.loop.reconcile_orphans", return_value=[]),
        patch("atlas.loop_budget.migrate_legacy_state_if_needed"),
        patch("atlas.loop.LoopState.load_or_init", return_value=state),
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch") as pull_mock,
        patch("atlas.loop.run_one_shot") as run_mock,
        patch("atlas.loop.time.sleep", side_effect=KeyboardInterrupt),
    ):
        with pytest.raises(KeyboardInterrupt):
            loop.run_forever(cfg, targets=cfg.loop.repos)

    # tick() ran, but bailed at the breaker check before pulling or dispatching.
    pull_mock.assert_not_called()
    run_mock.assert_not_called()
    assert state.last_tick_at is not None


# ---------------------------------------------------------------------------
# build_issue_prompt (Decision #10)
# ---------------------------------------------------------------------------


def test_build_issue_prompt_includes_title_body_and_scope_preamble() -> None:
    issue = _issue()
    prompt = loop.build_issue_prompt(issue)
    assert issue.title in prompt
    assert issue.body in prompt
    assert "scope" in prompt.lower()
