"""Integration tests for the loop daemon (T-L2.10).

Full-tick + zero-touch smoke, exercised end-to-end through the real
``tick()`` -> ``run_one_shot``/``run_planned_first_pass`` -> ``make_pipeline``
-> ``Pipeline``/``WorktreeManager``/``GhPrDeliverer`` stack, against a real
temporary git repo. ``gh`` (via ``queue_gh``) is mocked at the typed function
boundary; the backend subprocess (``claude -p ...``) and delivery's
``git push``/``gh pr create`` are mocked via a single ``subprocess.run``
patch (see ``_FakeSubprocess`` — orchestrator/deliverer/worktree all import
the same ``subprocess`` module object). No live network or CLI binaries.
This is the CI-safe proof of TRD-v3 §13 #5/#6; the real-world manual proof
is T-L2.13.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from atlas import loop
from atlas.config import Config, LoopConfig
from atlas.queue_gh import Issue

_REPO = "anant-gupta-utexas/atlas"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=path)
    _git("config", "user.email", "test@atlas.local", cwd=path)
    _git("config", "user.name", "Atlas Test", cwd=path)
    (path / "README.md").write_text("# test repo\n")
    _git("add", "README.md", cwd=path)
    _git("commit", "-m", "initial commit", cwd=path)
    return path


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not available")


def _cfg(repo_root: Path, **loop_kwargs: object) -> Config:
    """No [plugin_commands] override: loop_dev.yaml's stages resolve natively —
    its two RAW: stages bypass the allow-list in plugin_resolver.resolve(), and
    its verify stage is in PLUGIN_COMMANDS. Passing an override here would make
    these tests pass regardless of that, masking a regression."""
    loop_cfg = LoopConfig(repos=(_REPO,), **loop_kwargs)  # type: ignore[arg-type]
    return Config(
        repo_root=repo_root,
        plumb_db_path=repo_root / "plumb.db",
        loop=loop_cfg,
    )


def _issue(
    number: int = 1,
    title: str = "Fix the thing",
    body: str = "Do the thing per acceptance criteria.",
    labels: frozenset[str] = frozenset({"wf:quick"}),
    author: str = "anant",
) -> Issue:
    return Issue(number=number, title=title, body=body, labels=labels, repo=_REPO, author=author)


def _completed(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> MagicMock:
    m = MagicMock(spec=CompletedProcess)
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class _FakeSubprocess:
    """Fakes the one shared `subprocess` module used by atlas.orchestrator
    (backend dispatch: claude -p ...), atlas.deliverer (git push / gh pr
    create), and atlas.worktree (git worktree add/remove, git status) — all
    three modules literally share one imported `subprocess` object, so a
    single routing fake patched once covers all of them. Only `git push` and
    `gh pr create` are faked (no real push/PR); every other `git ...` call
    (worktree add/remove, status) is delegated to the real subprocess.run so
    WorktreeManager genuinely exercises git against the temp repo."""

    def __init__(self, pr_number: int = 42, backend_stdout: str = "ok") -> None:
        self.pr_number = pr_number
        self.backend_stdout = backend_stdout
        self.calls: list[list[str]] = []
        # Captured before `subprocess.run` gets patched out — `patch(...)`
        # replaces the `run` attribute on this exact module object (loop.py,
        # orchestrator.py, deliverer.py, and worktree.py all import the same
        # `subprocess` module, not a copy), so calling `subprocess.run` from
        # inside __call__ would recurse into this fake instead of real git.
        self._real_subprocess_run = subprocess.run

    def __call__(self, argv: list[str], **kwargs: Any) -> MagicMock | CompletedProcess[str]:
        self.calls.append(argv)
        if argv[:2] == ["git", "push"]:
            return _completed(stdout="")
        if argv[:2] == ["gh", "pr"]:
            return _completed(stdout=f"https://github.com/{_REPO}/pull/{self.pr_number}\n")
        if argv[0] == "claude":
            return _completed(stdout=self.backend_stdout)
        if argv[0] == "git":
            return self._real_subprocess_run(argv, **kwargs)
        raise AssertionError(f"unexpected subprocess call: {argv}")


# ---------------------------------------------------------------------------
# test_one_shot_lane_end_to_end_faked
# ---------------------------------------------------------------------------


def test_one_shot_lane_end_to_end_faked(tmp_path: Path) -> None:
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root)
    state = loop.LoopState(day=loop._today())
    issue = _issue(labels=frozenset({"wf:quick"}))
    fake_subprocess = _FakeSubprocess(pr_number=42)

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.queue_gh.list_ready", return_value=[issue]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim") as claim_mock,
        patch("atlas.orchestrator.subprocess.run", side_effect=fake_subprocess),
        patch("atlas.queue_gh.comment") as comment_mock,
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        result = loop.tick(cfg, state, repos=[_REPO], repo_root=repo_root)

    assert result.action == "dispatched"
    assert result.lane == "quick"
    assert result.pr_ref is not None
    assert result.pr_ref.number == 42
    assert result.pr_ref.url == f"https://github.com/{_REPO}/pull/42"

    claim_mock.assert_called_once()
    relabel_mock.assert_not_called()  # sync/relabel happens on a later tick, not this one

    # One plumb run was recorded for the loop_dev pipeline.
    assert state.runs_today == 1

    # The PR was actually pushed and created (not just simulated in Python).
    push_calls = [c for c in fake_subprocess.calls if c[:2] == ["git", "push"]]
    pr_calls = [c for c in fake_subprocess.calls if c[:2] == ["gh", "pr"]]
    assert len(push_calls) == 1
    assert len(pr_calls) == 1

    # comment() posts the run_id + PR back onto the issue.
    comment_mock.assert_called_once()
    comment_body = comment_mock.call_args.kwargs["body"]
    assert "plumb run_id" in comment_body
    assert "https://github.com" in comment_body

    # Worktree was cleaned up after delivery (no leftover .atlas/worktrees entries).
    worktrees_dir = repo_root / ".atlas" / "worktrees"
    if worktrees_dir.exists():
        assert list(worktrees_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# test_planned_lane_stops_after_plan_pr
# ---------------------------------------------------------------------------


def test_planned_lane_stops_after_plan_pr(tmp_path: Path) -> None:
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root)
    state = loop.LoopState(day=loop._today())
    issue = _issue(number=7, title="Redesign the queue", labels=frozenset({"wf:planned"}))
    # Writes a triad into the backend's cwd, as a real dev-docs-be run would;
    # the planned lane commits it before delivering, so an inert fake would
    # (correctly) fail at commit time with "nothing to commit".
    # Since planned-lane dispatches only dev-docs-be (never plan/code_gen/
    # verify), any "claude" call this tick is that one call.
    fake_subprocess = _TriadWritingSubprocess(pr_number=43)

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.queue_gh.list_ready", return_value=[issue]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.loop.subprocess.run", side_effect=fake_subprocess),
        patch("atlas.queue_gh.comment") as comment_mock,
    ):
        result = loop.tick(cfg, state, repos=[_REPO], repo_root=repo_root)

    assert result.action == "dispatched"
    assert result.lane == "planned"
    assert result.pr_ref is not None
    assert result.pr_ref.number == 43

    # Exactly one backend call happened this tick (dev-docs-be) — no
    # plan/code_gen/verify dispatch, since Pipeline is never invoked
    # (Decision #2: planned lane is first-pass-only, dev-docs-be then stop).
    claude_calls = [c for c in fake_subprocess.calls if c[0] == "claude"]
    assert len(claude_calls) == 1
    assert "dev-docs-be" in claude_calls[0][2]

    pr_create_call = next(c for c in fake_subprocess.calls if c[:2] == ["gh", "pr"])
    body_arg = pr_create_call[pr_create_call.index("--body") + 1]
    assert "Plan-only PR" in body_arg
    assert "plumb run_id" in body_arg

    comment_mock.assert_called_once()


class _TriadWritingSubprocess(_FakeSubprocess):
    """_FakeSubprocess, but the faked `claude` call also writes a TRS triad
    into the cwd it was invoked with — the way a real dev-docs-be run would.

    This is what makes the planned lane's commit assertable: without files on
    disk there is nothing to `git add`, and the empty-branch bug the ordering
    fix addresses would be invisible.
    """

    def __init__(self, pr_number: int = 43) -> None:
        super().__init__(pr_number=pr_number, backend_stdout="Wrote the TRS triad.")
        self.backend_cwds: list[str] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> MagicMock | CompletedProcess[str]:
        if argv[0] == "claude":
            cwd = str(kwargs.get("cwd", ""))
            self.backend_cwds.append(cwd)
            triad_dir = Path(cwd) / "dev" / "active" / "redesign-the-queue"
            triad_dir.mkdir(parents=True, exist_ok=True)
            (triad_dir / "plan.md").write_text("# plan\n")
            (triad_dir / "tasks.md").write_text("# tasks\n")
        return super().__call__(argv, **kwargs)


def test_planned_lane_commits_triad_before_delivering(tmp_path: Path) -> None:
    """C1 regression: the planned lane must run dev-docs-be *inside* the
    worktree and commit the triad, so the delivered branch is ahead of main.

    The pre-fix ordering ran dev-docs-be against repo_root and created an
    empty worktree afterwards, so `git push` sent a branch identical to main
    and `gh pr create` would fail with "No commits between main and ...".
    """
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root)
    issue = _issue(number=7, title="Redesign the queue", labels=frozenset({"wf:planned"}))
    fake_subprocess = _TriadWritingSubprocess(pr_number=43)

    # Capture the branch name at the delivery boundary, before GhPrDeliverer
    # cleans the worktree up. Delivery itself stays real (git push / gh pr
    # create are faked inside _FakeSubprocess), so the branch is genuinely
    # pushed from a genuinely committed worktree.
    captured: dict[str, str] = {}
    real_deliver_pr = loop.queue_gh.deliver_pr

    def _capture_deliver_pr(*args: Any, **kwargs: Any) -> Any:
        captured["branch"] = kwargs["branch"]
        return real_deliver_pr(*args, **kwargs)

    with (
        patch("atlas.loop.subprocess.run", side_effect=fake_subprocess),
        patch("atlas.loop.queue_gh.deliver_pr", side_effect=_capture_deliver_pr),
    ):
        pr_ref, _run_id, _cost = loop.run_planned_first_pass(issue, cfg, repo_root=repo_root)

    assert pr_ref.number == 43

    # dev-docs-be ran inside the worktree, not the main working tree.
    assert len(fake_subprocess.backend_cwds) == 1
    backend_cwd = Path(fake_subprocess.backend_cwds[0])
    assert backend_cwd != repo_root
    assert ".atlas/worktrees" in str(backend_cwd)

    # The delivered branch has a real commit ahead of main — the actual C1 bug.
    branch = captured["branch"]
    ahead = subprocess.run(
        ["git", "rev-list", "--count", f"main..{branch}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert int(ahead.stdout.strip()) >= 1, "delivered branch has no commits ahead of main"

    # The triad is actually in that commit, and never landed in the main tree.
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", branch],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "dev/active/redesign-the-queue/plan.md" in tree.stdout
    assert not (repo_root / "dev" / "active" / "redesign-the-queue").exists()


def test_planned_lane_raises_when_agent_produces_nothing(tmp_path: Path) -> None:
    """An agent that writes no files must fail loudly at commit time rather
    than pushing an empty branch and failing at `gh pr create`."""
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root)
    issue = _issue(number=8, title="Redesign the queue", labels=frozenset({"wf:planned"}))
    # Plain _FakeSubprocess: the faked `claude` writes nothing to disk.
    fake_subprocess = _FakeSubprocess(pr_number=44, backend_stdout="I did nothing.")

    from atlas.worktree import WorktreeError

    with (
        patch("atlas.loop.subprocess.run", side_effect=fake_subprocess),
        pytest.raises(WorktreeError, match="nothing to commit"),
    ):
        loop.run_planned_first_pass(issue, cfg, repo_root=repo_root)

    # No PR was attempted, and the worktree was cleaned up on the failure path.
    assert [c for c in fake_subprocess.calls if c[:2] == ["gh", "pr"]] == []
    worktrees_dir = repo_root / ".atlas" / "worktrees"
    if worktrees_dir.exists():
        assert list(worktrees_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# test_crash_recovery_full_cycle
# ---------------------------------------------------------------------------


def test_crash_recovery_full_cycle(tmp_path: Path) -> None:
    """claim() lands (issue -> atlas:working), the run then 'crashes' (no PR
    ever opens), and reconcile_orphans() on the next startup puts the issue
    back to atlas:ready and prunes any orphaned worktree."""
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root)

    working_issue = _issue(number=9, labels=frozenset({"atlas:working"}))

    # sync() finds no linked PR for the stranded issue (the run crashed before
    # delivery), so reconcile_orphans relabels it back to atlas:ready.
    with (
        patch("atlas.queue_gh.list_labeled", return_value=[working_issue]),
        patch("atlas.queue_gh.sync", return_value=[]),
        patch("atlas.queue_gh.relabel") as relabel_mock,
    ):
        reconciled = loop.reconcile_orphans(cfg, repos=[_REPO], repo_root=repo_root)

    relabel_mock.assert_called_once_with(working_issue, state="ready")
    assert any("issue #9" in item for item in reconciled)


def test_crash_recovery_prunes_orphaned_worktree(tmp_path: Path) -> None:
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root)

    from atlas.worktree import WorktreeManager

    worktree_mgr = WorktreeManager(repo_root)
    orphan_path = worktree_mgr.create(slug="stale-issue", run_id="deadbeef00000000")

    with (
        patch("atlas.queue_gh.list_labeled", return_value=[]),
        patch("atlas.queue_gh.sync", return_value=[]),
    ):
        reconciled = loop.reconcile_orphans(cfg, repos=[_REPO], repo_root=repo_root)

    assert not orphan_path.exists()
    assert any("worktree" in item for item in reconciled)


def test_reconcile_retains_live_worktree_and_prunes_colliding_orphan(tmp_path: Path) -> None:
    """I4 regression: the retain-check keys on .atlas/current-run's exact
    worktree path, not on a re-slugified issue title.

    _slugify truncates to 40 chars, so two long, similar issue titles produce
    the same slug. Under the old title-slug matching an orphan sharing a live
    issue's slug was retained forever, and — worse — a live run whose slug
    didn't match had its uncommitted work deleted.
    """
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root)

    from atlas.state import StateStore
    from atlas.worktree import WorktreeManager

    worktree_mgr = WorktreeManager(repo_root)
    # Same 40-char-truncated slug, different runs: one live, one orphaned.
    slug = loop._slugify("Refactor the authentication middleware layer for OAuth")
    assert slug == loop._slugify("Refactor the authentication middleware layer for SAML")

    live_path = worktree_mgr.create(slug=slug, run_id="1111111111111111")
    orphan_path = worktree_mgr.create(slug=slug, run_id="2222222222222222")

    StateStore(repo_root).write_current_run("1111111111111111", slug, live_path)

    with (
        patch("atlas.queue_gh.list_labeled", return_value=[]),
        patch("atlas.queue_gh.sync", return_value=[]),
    ):
        reconciled = loop.reconcile_orphans(cfg, repos=[_REPO], repo_root=repo_root)

    assert live_path.exists(), "live run's worktree was deleted"
    assert not orphan_path.exists(), "colliding orphan was retained"
    assert any(orphan_path.name in item for item in reconciled)


def test_reconcile_sweeps_nothing_when_current_run_unreadable(tmp_path: Path) -> None:
    """Fail-safe: if .atlas/current-run can't be read we cannot tell which
    worktree is live, so sweep nothing rather than risk deleting it."""
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root)

    from atlas.worktree import WorktreeManager

    wt_path = WorktreeManager(repo_root).create(slug="some-issue", run_id="3333333333333333")

    with (
        patch("atlas.queue_gh.list_labeled", return_value=[]),
        patch("atlas.queue_gh.sync", return_value=[]),
        patch(
            "atlas.state.StateStore.read_current_run_with_worktree",
            side_effect=OSError("disk error"),
        ),
    ):
        reconciled = loop.reconcile_orphans(cfg, repos=[_REPO], repo_root=repo_root)

    assert wt_path.exists()
    assert not any("worktree" in item for item in reconciled)


def test_loop_passes_max_turns_to_the_backend(tmp_path: Path) -> None:
    """I3 regression: cfg.loop.max_turns must reach the backend argv as
    --max-turns. It was parsed and documented but never threaded through, so
    an unattended run had no turn cap at all."""
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root, max_turns=7)
    state = loop.LoopState(day=loop._today())
    issue = _issue(labels=frozenset({"wf:quick"}))
    fake_subprocess = _FakeSubprocess(pr_number=77)

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.queue_gh.list_ready", return_value=[issue]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.orchestrator.subprocess.run", side_effect=fake_subprocess),
        patch("atlas.queue_gh.comment"),
    ):
        loop.tick(cfg, state, repos=[_REPO], repo_root=repo_root)

    claude_calls = [c for c in fake_subprocess.calls if c[0] == "claude"]
    assert claude_calls, "no backend call was made"
    for argv in claude_calls:
        assert "--max-turns" in argv
        assert argv[argv.index("--max-turns") + 1] == "7"


def test_atlas_run_leaves_max_turns_unset(tmp_path: Path) -> None:
    """`atlas run` has a human watching, so it keeps the backend's own default
    — make_pipeline's max_turns is opt-in, not a behavior change for stage 1-7."""
    from atlas.cli import make_pipeline

    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root, max_turns=7)
    _pipeline, _recorder = make_pipeline(repo_root, cfg, workflow="loop_dev")
    # No max_turns argument passed -> runner leaves it None.
    from atlas.orchestrator import SubprocessStageRunner

    runner = SubprocessStageRunner(model=cfg.model)
    assert runner._max_turns is None


# ---------------------------------------------------------------------------
# test_zero_touch_smoke_faked
# ---------------------------------------------------------------------------


def test_zero_touch_smoke_faked(tmp_path: Path) -> None:
    """TRD-v3 §13 #5 literal shape: a labeled issue in, one tick(), a PR with
    Closes #n + a run_id comment out, with zero interaction beyond the label."""
    repo_root = _init_repo(tmp_path / "repo")
    cfg = _cfg(repo_root)
    state = loop.LoopState(day=loop._today())
    issue = _issue(number=55, title="Add a health check endpoint", labels=frozenset({"wf:quick"}))
    fake_subprocess = _FakeSubprocess(pr_number=101)

    with (
        patch("atlas.loop.sync_prior_prs", return_value=[]),
        patch("atlas.queue_gh.list_ready", return_value=[issue]),
        patch("atlas.loop.current_gh_user", return_value="anant"),
        patch("atlas.queue_gh.claim"),
        patch("atlas.orchestrator.subprocess.run", side_effect=fake_subprocess),
        patch("atlas.queue_gh.comment") as comment_mock,
    ):
        result = loop.tick(cfg, state, repos=[_REPO], repo_root=repo_root)

    assert result.action == "dispatched"
    assert result.pr_ref is not None

    pr_create_call = next(c for c in fake_subprocess.calls if c[:2] == ["gh", "pr"])
    title_arg = pr_create_call[pr_create_call.index("--title") + 1]
    body_arg = pr_create_call[pr_create_call.index("--body") + 1]
    assert "Closes #55" in title_arg
    assert "plumb run_id" in body_arg

    comment_body = comment_mock.call_args.kwargs["body"]
    assert "plumb run_id" in comment_body
