"""Unit tests for Phase L4 (scale-out): per-target routing (T-L4.2), keyed
current-run state (T-L4.3), the claim-race guard (T-L4.4), batch dispatch and
the loop-state relocation/migration (T-L4.5).

The T-L4.9 real-thread-pool concurrency-safety invariant test lives in
tests/integration/test_loop_concurrency.py (a real ThreadPoolExecutor doesn't
fit this file's mocked-dispatch shape).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from atlas import loop, loop_budget
from atlas.config import Config, LoopConfig, RepoTarget
from atlas.deliverer import PrRef
from atlas.queue_gh import GhCliError, Issue

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
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def _tick(
    cfg: Config, state: loop.LoopState, targets: tuple[RepoTarget, ...]
) -> loop.BatchTickResult:
    return loop.tick(cfg, state, targets=targets)


# ---------------------------------------------------------------------------
# T-L4.5 — one-time legacy loop-state.json migration
# ---------------------------------------------------------------------------


def test_migrate_legacy_state_copies_synced_pr_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    home = _home(tmp_path, monkeypatch)
    repo_root = tmp_path / "repo"
    legacy_path = repo_root / ".atlas" / "loop-state.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps({"day": "2026-01-01", "synced_pr_outcomes": ["1:2:merged", "3:4:merged"]})
    )

    loop_budget.migrate_legacy_state_if_needed(repo_root)

    new_path = home / ".atlas" / "loop-state.json"
    assert new_path.exists()
    migrated = json.loads(new_path.read_text())
    assert migrated["synced_pr_outcomes"] == ["1:2:merged", "3:4:merged"]


def test_migrate_legacy_state_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    home = _home(tmp_path, monkeypatch)
    repo_root = tmp_path / "repo"
    legacy_path = repo_root / ".atlas" / "loop-state.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(json.dumps({"day": "2000-01-01", "synced_pr_outcomes": ["stale"]}))

    new_path = home / ".atlas" / "loop-state.json"
    new_path.parent.mkdir(parents=True)
    new_path.write_text(json.dumps({"day": "2026-07-27", "synced_pr_outcomes": ["fresh"]}))

    loop_budget.migrate_legacy_state_if_needed(repo_root)

    assert json.loads(new_path.read_text())["synced_pr_outcomes"] == ["fresh"]


def test_migrate_legacy_state_noop_when_no_legacy_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    loop_budget.migrate_legacy_state_if_needed(repo_root)

    assert not (home / ".atlas" / "loop-state.json").exists()


# ---------------------------------------------------------------------------
# T-L4.2 — per-target routing
# ---------------------------------------------------------------------------


def test_pull_ready_batch_routes_across_multiple_targets(tmp_path: Path) -> None:
    target_a = _target(tmp_path / "a", github="org/a")
    target_b = _target(tmp_path / "b", github="org/b")
    issue_a = _issue(number=1)
    issue_b = _issue(number=2)

    def fake_list_ready(repo: str) -> list[Issue]:
        return [issue_a] if repo == "org/a" else [issue_b]

    with patch("atlas.queue_gh.list_ready", side_effect=fake_list_ready):
        result = loop._pull_ready_batch((target_a, target_b), LoopConfig(), limit=2)

    assert result == [(target_a, issue_a), (target_b, issue_b)]


def test_pull_ready_batch_respects_limit_across_targets(tmp_path: Path) -> None:
    target_a = _target(tmp_path / "a", github="org/a")
    target_b = _target(tmp_path / "b", github="org/b")
    issues_a = [_issue(number=1), _issue(number=2)]
    issues_b = [_issue(number=3)]

    def fake_list_ready(repo: str) -> list[Issue]:
        return issues_a if repo == "org/a" else issues_b

    with patch("atlas.queue_gh.list_ready", side_effect=fake_list_ready):
        result = loop._pull_ready_batch((target_a, target_b), LoopConfig(), limit=1)

    assert result == [(target_a, issues_a[0])]


def test_an_issue_from_the_second_target_dispatches_against_its_own_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The winning issue's own RepoTarget.local_path reaches WorktreeManager/
    StateStore/run_one_shot — not a single shared repo_root (T-L4.2)."""
    _home(tmp_path, monkeypatch)
    target_a = _target(tmp_path / "a")
    target_b = _target(tmp_path / "b", github="org/b")
    cfg = _cfg(tmp_path, repos=(target_a, target_b))
    state = _state()
    issue = _issue(labels=frozenset({"wf:quick"}))
    pr_ref = PrRef(number=1, url="u")

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target_b, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot", return_value=(pr_ref, "r1", 0.0)) as run_mock,
        patch("atlas.queue_gh.comment"),
    ):
        _tick(cfg, state, cfg.loop.repos)

    assert run_mock.call_args.kwargs["repo_root"] == target_b.local_path


def test_reconcile_orphans_is_independent_per_target(tmp_path: Path) -> None:
    """A crash affecting one target's worktree must not touch another's."""
    target_a = _target(tmp_path / "a")
    target_b = _target(tmp_path / "b", github="org/b")
    worktrees_a = target_a.local_path / ".atlas" / "worktrees"
    stale_a = worktrees_a / "stale-a"
    stale_a.mkdir(parents=True)
    worktrees_b = target_b.local_path / ".atlas" / "worktrees"
    worktrees_b.mkdir(parents=True)

    with (
        patch("atlas.queue_gh.list_labeled", return_value=[]),
        patch("atlas.queue_gh.sync", return_value=[]),
        patch("atlas.loop.WorktreeManager") as wm_cls,
    ):
        wm_instance = wm_cls.return_value
        reconciled = loop.reconcile_orphans(_cfg(tmp_path), targets=(target_a, target_b))

    wm_instance.cleanup.assert_called_once_with(stale_a)
    assert any("stale-a" in item for item in reconciled)


def test_startup_reconcile_clears_keyed_current_run_files(tmp_path: Path) -> None:
    """A keyed .atlas/runs/<run_id>/current-run left by a crashed concurrent
    dispatch is also stale at startup and must be cleared, the same as the
    singleton (T-L4.3)."""
    from atlas.state import StateStore
    from atlas.worktree import WorktreeManager

    repo = tmp_path / "r"
    repo.mkdir()
    import subprocess as sp

    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        sp.run(["git", *args], cwd=repo, capture_output=True, check=True)
    (repo / "seed.txt").write_text("seed\n")
    sp.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    sp.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

    wt = WorktreeManager(repo).create(slug="stranded", run_id="dead0000deadbeef")
    store = StateStore(repo)
    store.write_current_run_keyed("dead0000deadbeef", "stranded", wt)
    assert store.list_current_runs() != []

    targets = _targets(repo)
    with (
        patch("atlas.queue_gh.list_labeled", return_value=[]),
        patch("atlas.queue_gh.sync", return_value=[]),
    ):
        loop.reconcile_orphans(_cfg(repo), targets=targets, at_startup=True)

    assert store.list_current_runs() == []
    assert not wt.exists()


# ---------------------------------------------------------------------------
# T-L4.4 — claim-race detection
# ---------------------------------------------------------------------------


def test_claim_confirmed_true_on_clean_claim() -> None:
    issue = _issue()
    with patch("atlas.queue_gh.current_assignees", return_value=["anant"]):
        assert loop._claim_confirmed(issue, "anant") is True


def test_claim_confirmed_false_when_another_claimant_won_the_race() -> None:
    issue = _issue()
    with patch("atlas.queue_gh.current_assignees", return_value=["other-caller"]):
        assert loop._claim_confirmed(issue, "anant") is False


def test_claim_confirmed_fails_open_on_transient_read_error() -> None:
    issue = _issue()
    with patch("atlas.queue_gh.current_assignees", side_effect=GhCliError("timeout")):
        assert loop._claim_confirmed(issue, "anant") is True


def test_lost_claim_race_is_skipped_not_relabeled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(tmp_path, monkeypatch)
    cfg = _cfg(tmp_path)
    state = _state()
    target = cfg.loop.repos[0]
    issue = _issue(labels=frozenset({"wf:quick"}))

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.loop._pull_ready_batch", return_value=[(target, issue)]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim") as claim_mock,
        patch("atlas.loop._claim_confirmed", return_value=False),
        patch("atlas.loop.run_one_shot") as run_mock,
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    claim_mock.assert_called_once()
    run_mock.assert_not_called()
    relabel_mock.assert_not_called()
    assert batch.results[0].action == "idle"


def test_concurrency_config_bounds() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        LoopConfig(concurrency=0)
    LoopConfig(concurrency=3)  # must not raise


# ---------------------------------------------------------------------------
# T-L4.5 — batch dispatch invariants
# ---------------------------------------------------------------------------


def test_batch_tick_result_has_one_entry_per_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(tmp_path, monkeypatch)
    target_a = _target(tmp_path / "a", github="org/a")
    target_b = _target(tmp_path / "b", github="org/b")
    cfg = _cfg(tmp_path, repos=(target_a, target_b), concurrency=2)
    state = _state()
    issue_a = _issue(number=1, labels=frozenset({"wf:quick"}))
    issue_b = _issue(number=2, labels=frozenset({"wf:quick"}))
    pr_a = PrRef(number=1, url="u1")
    pr_b = PrRef(number=2, url="u2")

    def fake_run_one_shot(issue: Issue, _cfg: Config, *, repo_root: Path, **kwargs: object):
        return (pr_a, "r1", 1.0) if issue.number == 1 else (pr_b, "r2", 2.0)

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch(
            "atlas.loop._pull_ready_batch",
            return_value=[(target_a, issue_a), (target_b, issue_b)],
        ),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch("atlas.loop.run_one_shot", side_effect=fake_run_one_shot),
        patch("atlas.queue_gh.comment"),
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    assert len(batch.results) == 2
    assert {r.issue_number for r in batch.results} == {1, 2}
    assert state.runs_today == 2
    assert state.dollars_today == pytest.approx(3.0)


def test_state_persist_called_exactly_once_per_tick_regardless_of_batch_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(tmp_path, monkeypatch)
    target_a = _target(tmp_path / "a", github="org/a")
    target_b = _target(tmp_path / "b", github="org/b")
    cfg = _cfg(tmp_path, repos=(target_a, target_b), concurrency=2)
    state = _state()
    issue_a = _issue(number=1, labels=frozenset({"wf:quick"}))
    issue_b = _issue(number=2, labels=frozenset({"wf:quick"}))

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch(
            "atlas.loop._pull_ready_batch",
            return_value=[(target_a, issue_a), (target_b, issue_b)],
        ),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", return_value=True),
        patch(
            "atlas.loop.run_one_shot",
            return_value=(PrRef(number=1, url="u"), "r1", 0.0),
        ),
        patch("atlas.queue_gh.comment"),
        patch.object(loop.LoopState, "persist", autospec=True) as persist_mock,
    ):
        _tick(cfg, state, cfg.loop.repos)

    assert persist_mock.call_count == 1


def test_dispatch_one_signature_has_no_state_parameter() -> None:
    """_dispatch_one must be pure w.r.t. LoopState (Pending Decision #8) —
    enforced structurally, not just by behavior."""
    import inspect

    params = inspect.signature(loop._dispatch_one).parameters
    assert "state" not in params


def test_claim_race_loss_within_a_batch_does_not_block_other_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(tmp_path, monkeypatch)
    target_a = _target(tmp_path / "a", github="org/a")
    target_b = _target(tmp_path / "b", github="org/b")
    cfg = _cfg(tmp_path, repos=(target_a, target_b), concurrency=2)
    state = _state()
    issue_a = _issue(number=1, labels=frozenset({"wf:quick"}))
    issue_b = _issue(number=2, labels=frozenset({"wf:quick"}))
    pr_b = PrRef(number=2, url="u2")

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch(
            "atlas.loop._pull_ready_batch",
            return_value=[(target_a, issue_a), (target_b, issue_b)],
        ),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop._claim_confirmed", side_effect=[False, True]),
        patch("atlas.loop.run_one_shot", return_value=(pr_b, "r2", 0.0)) as run_mock,
        patch("atlas.queue_gh.comment"),
    ):
        batch = _tick(cfg, state, cfg.loop.repos)

    run_mock.assert_called_once()
    assert len(batch.results) == 1
    assert batch.results[0].issue_number == 2
