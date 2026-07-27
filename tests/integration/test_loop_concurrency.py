"""Concurrency-safety invariant test for Phase L4's batch dispatch (T-L4.9).

Matches T-L3.8's own precedent: an explicit, dedicated test for a safety
property, not incidental coverage. Uses a **real** ``ThreadPoolExecutor``
(``tick()``'s own, not a mocked stand-in) with staggered per-worker delays so
actual OS-level thread interleaving occurs — the point is to prove
correctness under real concurrency, not merely under a single-threaded
stand-in that trivially can't race.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from atlas import loop
from atlas.config import Config, LoopConfig, RepoTarget
from atlas.deliverer import PrRef
from atlas.queue_gh import Issue

_REPO = "org/repo"


def _cfg(tmp_path: Path, *, concurrency: int) -> Config:
    target = RepoTarget(github=_REPO, local_path=tmp_path)
    return Config(
        repo_root=tmp_path,
        plumb_db_path=tmp_path / "plumb.db",
        loop=LoopConfig(repos=(target,), concurrency=concurrency),
    )


def _issue(number: int) -> Issue:
    return Issue(
        number=number,
        title=f"issue {number}",
        body="body",
        labels=frozenset({"wf:quick"}),
        repo=_REPO,
        author="anant",
    )


def _run_once(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, concurrency=3)
    state = loop.LoopState(day=loop._today())
    target = cfg.loop.repos[0]
    issues = [_issue(1), _issue(2), _issue(3)]
    batch = [(target, issue) for issue in issues]

    # Staggered, out-of-order-completing delays: worker 1 finishes last even
    # though it starts first, so as_completed()'s ordering genuinely differs
    # from submission order — a real interleaving, not an artifact of GIL
    # scheduling happening to preserve submission order.
    delays = {1: 0.03, 2: 0.01, 3: 0.02}

    def fake_run_one_shot(
        issue: Issue, _cfg: Config, *, repo_root: Path, **kwargs: object
    ) -> tuple[PrRef, str, float]:
        time.sleep(delays[issue.number])
        return PrRef(number=issue.number, url=f"u{issue.number}"), f"run-{issue.number}", 1.0

    real_record_tick_outcome = loop.record_tick_outcome
    outcome_calls: list[tuple[bool, str | None]] = []

    def spy_record_tick_outcome(
        state: loop.LoopState, cfg: LoopConfig, *, made_progress: bool, error_signature: str | None
    ) -> None:
        outcome_calls.append((made_progress, error_signature))
        real_record_tick_outcome(
            state, cfg, made_progress=made_progress, error_signature=error_signature
        )

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=batch),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot", side_effect=fake_run_one_shot),
        patch("atlas.queue_gh.comment"),
        patch("atlas.loop.record_tick_outcome", side_effect=spy_record_tick_outcome),
        patch.object(loop.LoopState, "persist", autospec=True) as persist_mock,
    ):
        batch_result = loop.tick(cfg, state, targets=cfg.loop.repos)

    # No lost dispatches: all 3 issues produced a result.
    assert len(batch_result.results) == 3
    assert {r.issue_number for r in batch_result.results} == {1, 2, 3}

    # No lost increments: the exact expected sum, not a partial one a race
    # would produce (e.g. two workers reading-then-writing the same counter).
    assert state.runs_today == 3
    assert state.dollars_today == pytest.approx(3.0)

    # state.persist() happens exactly once per tick, regardless of batch size
    # — proves the single-threaded aggregation tail, not per-worker I/O.
    assert persist_mock.call_count == 1

    # record_tick_outcome fires once per dispatched issue, each with that
    # issue's own outcome — not a single call describing "the batch".
    assert len(outcome_calls) == 3
    assert all(made_progress for made_progress, _ in outcome_calls)


def test_concurrent_dispatch_no_lost_state_updates_across_20_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    # 20 consecutive runs, fresh state each time — a race-condition test that
    # only sometimes catches the race is not trustworthy CI signal.
    for i in range(20):
        run_dir = tmp_path / f"run-{i}"
        run_dir.mkdir()
        _run_once(run_dir)
