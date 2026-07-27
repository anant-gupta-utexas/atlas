"""Unit tests for atlas.loop.run_one_shot — cost extraction, the judge gate
(T-L3.4), parent_run_id/diagnosis (T-L3.5), and the T-L2.13 field findings
(commit sweep, main-checkout guard, orphan reconcile at startup).

Split out of test_loop.py to stay under this repo's 800-line cap. tick()'s
own dispatch-path tests live in test_loop_dispatch.py; Phase L4 per-target/
claim-race/batch-dispatch tests live in test_loop_l4.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas import judge_gate, loop
from atlas.config import Config, LoopConfig, RepoTarget
from atlas.deliverer import PrRef
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


# ---------------------------------------------------------------------------
# Cost extraction (2026-07-26) — run_one_shot must report real spend
# ---------------------------------------------------------------------------


def test_run_one_shot_returns_engine_reported_cost(tmp_path: Path) -> None:
    from atlas.orchestrator import RunContext, RunResult

    issue = _issue(labels=frozenset({"wf:quick"}))
    ctx = RunContext(run_id="r" * 32, slug="fix-bug", task="t", repo_root=tmp_path)
    finished = RunContext(
        run_id=ctx.run_id,
        slug=ctx.slug,
        task=ctx.task,
        repo_root=tmp_path,
        worktree_path=tmp_path / "wt",
    )

    pipeline = MagicMock()
    pipeline.start.return_value = ctx
    pipeline.run_to_completion.return_value = RunResult(
        ctx=finished, status="success", dollar_cost=1.75
    )

    with (
        patch("atlas.loop.make_pipeline", return_value=(pipeline, MagicMock())),
        patch("atlas.loop.WorktreeManager"),
        patch("atlas.loop.GhPrDeliverer"),
        patch("atlas.loop._commit_all"),
        patch("atlas.loop._assert_branch_has_commits"),
        patch("atlas.loop._read_worktree_diff", return_value="diff --git a b"),
        patch("atlas.queue_gh.deliver_pr", return_value=PrRef(number=7, url="u")),
    ):
        _pr, _run_id, cost = loop.run_one_shot(issue, _cfg(tmp_path), repo_root=tmp_path)

    assert cost == pytest.approx(1.75)


def test_run_one_shot_reports_zero_when_engine_reports_no_cost(tmp_path: Path) -> None:
    """Codex reports no cost — the run must still complete, contributing 0.0."""
    from atlas.orchestrator import RunContext, RunResult

    issue = _issue(labels=frozenset({"wf:quick"}))
    ctx = RunContext(run_id="s" * 32, slug="fix-bug", task="t", repo_root=tmp_path)
    finished = RunContext(
        run_id=ctx.run_id,
        slug=ctx.slug,
        task=ctx.task,
        repo_root=tmp_path,
        worktree_path=tmp_path / "wt",
    )

    pipeline = MagicMock()
    pipeline.start.return_value = ctx
    pipeline.run_to_completion.return_value = RunResult(
        ctx=finished, status="success", dollar_cost=None
    )

    with (
        patch("atlas.loop.make_pipeline", return_value=(pipeline, MagicMock())),
        patch("atlas.loop.WorktreeManager"),
        patch("atlas.loop.GhPrDeliverer"),
        patch("atlas.loop._commit_all"),
        patch("atlas.loop._assert_branch_has_commits"),
        patch("atlas.loop._read_worktree_diff", return_value="diff --git a b"),
        patch("atlas.queue_gh.deliver_pr", return_value=PrRef(number=8, url="u")),
    ):
        _pr, _run_id, cost = loop.run_one_shot(issue, _cfg(tmp_path), repo_root=tmp_path)

    assert cost == 0.0


def test_run_one_shot_dispatches_in_loop_mode(tmp_path: Path) -> None:
    """The quick lane must request the telemetry/permission profile.

    Without loop_mode=True the dispatch gets no JSON envelope, so cost and
    tokens are both unavailable — the exact gap that made §13 #1 unprovable.
    """
    from atlas.orchestrator import RunContext, RunResult

    issue = _issue(labels=frozenset({"wf:quick"}))
    ctx = RunContext(run_id="t" * 32, slug="fix-bug", task="t", repo_root=tmp_path)
    pipeline = MagicMock()
    pipeline.start.return_value = ctx
    pipeline.run_to_completion.return_value = RunResult(
        ctx=RunContext(
            run_id=ctx.run_id,
            slug=ctx.slug,
            task=ctx.task,
            repo_root=tmp_path,
            worktree_path=tmp_path / "wt",
        ),
        status="success",
        dollar_cost=0.1,
    )

    with (
        patch("atlas.loop.make_pipeline", return_value=(pipeline, MagicMock())) as mk,
        patch("atlas.loop.WorktreeManager"),
        patch("atlas.loop.GhPrDeliverer"),
        patch("atlas.loop._commit_all"),
        patch("atlas.loop._assert_branch_has_commits"),
        patch("atlas.loop._read_worktree_diff", return_value="diff --git a b"),
        patch("atlas.queue_gh.deliver_pr", return_value=PrRef(number=9, url="u")),
    ):
        loop.run_one_shot(issue, _cfg(tmp_path), repo_root=tmp_path)

    assert mk.call_args.kwargs["loop_mode"] is True


# ---------------------------------------------------------------------------
# Judge gate (T-L3.4) + parent_run_id/diagnosis (T-L3.5)
# ---------------------------------------------------------------------------


def _pipeline_mock(tmp_path: Path, *, run_id: str = "u" * 32) -> tuple[MagicMock, object]:
    from atlas.orchestrator import RunContext, RunResult

    ctx = RunContext(run_id=run_id, slug="fix-bug", task="t", repo_root=tmp_path)
    finished = RunContext(
        run_id=run_id,
        slug=ctx.slug,
        task=ctx.task,
        repo_root=tmp_path,
        worktree_path=tmp_path / "wt",
    )
    pipeline = MagicMock()
    pipeline.start.return_value = ctx
    pipeline.run_to_completion.return_value = RunResult(
        ctx=finished, status="success", dollar_cost=0.1
    )
    return pipeline, ctx


def test_judge_gate_below_threshold_blocks_delivery_not_cleanup(tmp_path: Path) -> None:
    issue = _issue(labels=frozenset({"wf:quick"}))
    pipeline, _ctx = _pipeline_mock(tmp_path)
    gate_result = judge_gate.JudgeGateResult(
        passed=False, value_numeric=0.3, rationale="incomplete", scorer_version="v1"
    )

    with (
        patch("atlas.loop.make_pipeline", return_value=(pipeline, MagicMock())),
        patch("atlas.loop.WorktreeManager") as wt_mgr_cls,
        patch("atlas.loop.GhPrDeliverer"),
        patch("atlas.loop._commit_all"),
        patch("atlas.loop._assert_branch_has_commits"),
        patch("atlas.loop._read_worktree_diff", return_value="diff --git a b"),
        patch("atlas.judge_gate.score_diff", return_value=gate_result),
        patch("atlas.queue_gh.deliver_pr") as deliver_mock,
    ):
        with pytest.raises(loop.JudgeGateFailedError) as excinfo:
            loop.run_one_shot(issue, _cfg(tmp_path), repo_root=tmp_path)

    deliver_mock.assert_not_called()
    assert excinfo.value.result is gate_result
    # Worktree cleanup is never called on this path — self_heal still needs
    # the diff on disk.
    wt_mgr_cls.return_value.cleanup.assert_not_called()


def test_judge_gate_passing_score_delivers_as_before(tmp_path: Path) -> None:
    issue = _issue(labels=frozenset({"wf:quick"}))
    pipeline, _ctx = _pipeline_mock(tmp_path)
    gate_result = judge_gate.JudgeGateResult(
        passed=True, value_numeric=0.9, rationale="good", scorer_version="v1"
    )

    with (
        patch("atlas.loop.make_pipeline", return_value=(pipeline, MagicMock())),
        patch("atlas.loop.WorktreeManager"),
        patch("atlas.loop.GhPrDeliverer"),
        patch("atlas.loop._commit_all"),
        patch("atlas.loop._assert_branch_has_commits"),
        patch("atlas.loop._read_worktree_diff", return_value="diff --git a b"),
        patch("atlas.judge_gate.score_diff", return_value=gate_result) as score_mock,
        patch("atlas.queue_gh.deliver_pr", return_value=PrRef(number=10, url="u")) as deliver_mock,
    ):
        pr_ref, _run_id, _cost = loop.run_one_shot(issue, _cfg(tmp_path), repo_root=tmp_path)

    score_mock.assert_called_once()
    deliver_mock.assert_called_once()
    assert pr_ref.number == 10


def test_judge_gate_span_carries_engine_attribute(tmp_path: Path) -> None:
    """T-L4.7 write side: the judge-gate span must carry an explicit `engine`
    attribute for both claude and codex dispatch (Pending Decision #6)."""
    issue = _issue(labels=frozenset({"wf:quick", "engine:codex"}))
    pipeline, _ctx = _pipeline_mock(tmp_path)
    gate_result = judge_gate.JudgeGateResult(
        passed=True, value_numeric=0.9, rationale="good", scorer_version="v1"
    )

    with (
        patch("atlas.loop.make_pipeline", return_value=(pipeline, MagicMock())),
        patch("atlas.loop.WorktreeManager"),
        patch("atlas.loop.GhPrDeliverer"),
        patch("atlas.loop._commit_all"),
        patch("atlas.loop._assert_branch_has_commits"),
        patch("atlas.loop._read_worktree_diff", return_value="diff --git a b"),
        patch("atlas.judge_gate.score_diff", return_value=gate_result),
        patch("atlas.queue_gh.deliver_pr", return_value=PrRef(number=10, url="u")),
    ):
        loop.run_one_shot(issue, _cfg(tmp_path), repo_root=tmp_path)

    span_call = pipeline.plumb.record_span.call_args
    assert span_call.kwargs["attributes"] == {"engine": "codex"}


def test_parent_run_id_triggers_reopen_run(tmp_path: Path) -> None:
    issue = _issue(labels=frozenset({"wf:quick"}))
    pipeline, _ctx = _pipeline_mock(tmp_path)

    with (
        patch("atlas.loop.make_pipeline", return_value=(pipeline, MagicMock())),
        patch("atlas.loop.WorktreeManager"),
        patch("atlas.loop.GhPrDeliverer"),
        patch("atlas.loop._commit_all"),
        patch("atlas.loop._assert_branch_has_commits"),
        patch("atlas.loop._read_worktree_diff", return_value="diff --git a b"),
        patch(
            "atlas.judge_gate.score_diff",
            return_value=judge_gate.JudgeGateResult(
                passed=True, value_numeric=0.9, rationale="ok", scorer_version="v1"
            ),
        ),
        patch("atlas.queue_gh.deliver_pr", return_value=PrRef(number=11, url="u")),
    ):
        loop.run_one_shot(issue, _cfg(tmp_path), repo_root=tmp_path, parent_run_id="p" * 32)

    pipeline.plumb.reopen_run.assert_called_once_with("p" * 32)
    # reopen_run must happen before run_to_completion (child-run handoff
    # BEFORE the pipeline runs, so every span lands under the child).
    call_names = [c[0] for c in pipeline.mock_calls]
    assert call_names.index("plumb.reopen_run") < call_names.index("run_to_completion")


def test_no_parent_run_id_never_calls_reopen_run(tmp_path: Path) -> None:
    """Regression guard: the pre-L3 call shape must not touch reopen_run at all."""
    issue = _issue(labels=frozenset({"wf:quick"}))
    pipeline, _ctx = _pipeline_mock(tmp_path)

    with (
        patch("atlas.loop.make_pipeline", return_value=(pipeline, MagicMock())),
        patch("atlas.loop.WorktreeManager"),
        patch("atlas.loop.GhPrDeliverer"),
        patch("atlas.loop._commit_all"),
        patch("atlas.loop._assert_branch_has_commits"),
        patch("atlas.loop._read_worktree_diff", return_value="diff --git a b"),
        patch(
            "atlas.judge_gate.score_diff",
            return_value=judge_gate.JudgeGateResult(
                passed=True, value_numeric=0.9, rationale="ok", scorer_version="v1"
            ),
        ),
        patch("atlas.queue_gh.deliver_pr", return_value=PrRef(number=12, url="u")),
    ):
        loop.run_one_shot(issue, _cfg(tmp_path), repo_root=tmp_path)

    pipeline.plumb.reopen_run.assert_not_called()


def test_diagnosis_appears_after_issue_body_and_before_scope_preamble() -> None:
    issue = _issue()
    prompt = loop.build_issue_prompt(issue, diagnosis="wrong_approach: tried X, needed Y")
    body_idx = prompt.index(issue.body)
    diag_idx = prompt.index("wrong_approach: tried X, needed Y")
    preamble_idx = prompt.index(loop._SCOPE_PREAMBLE)
    assert body_idx < diag_idx < preamble_idx


def test_no_diagnosis_prompt_byte_identical_to_pre_l3() -> None:
    issue = _issue()
    assert loop.build_issue_prompt(issue) == (
        f"{issue.title}\n\n{issue.body}\n\n{loop._SCOPE_PREAMBLE}"
    )


# ---------------------------------------------------------------------------
# T-L2.13 field findings (2026-07-27) — bugs the faked e2e tests missed
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> Path:
    import subprocess as sp

    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        sp.run(["git", *args], cwd=path, capture_output=True, check=True)
    (path / "seed.txt").write_text("seed\n")
    sp.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    sp.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True)
    return path


def test_assert_branch_has_commits_rejects_a_branch_identical_to_main(tmp_path: Path) -> None:
    """The guard that stops an empty PR reaching `gh pr create`.

    Live T-L2.13 failure: the agent edited the file correctly, the pipeline
    reported success on all three spans, and delivery produced nothing —
    because the agent never committed and nothing checked.
    """
    from atlas.worktree import WorktreeError

    repo = _init_git_repo(tmp_path / "r")

    with pytest.raises(WorktreeError, match="no commits ahead of main"):
        loop._assert_branch_has_commits(repo)


def test_assert_branch_has_commits_passes_when_ahead(tmp_path: Path) -> None:
    import subprocess as sp

    repo = _init_git_repo(tmp_path / "r")
    sp.run(["git", "checkout", "-q", "-b", "atlas/work"], cwd=repo, capture_output=True, check=True)
    (repo / "new.txt").write_text("x\n")
    sp.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    sp.run(["git", "commit", "-m", "work"], cwd=repo, capture_output=True, check=True)

    loop._assert_branch_has_commits(repo)  # must not raise


def test_commit_all_is_a_noop_when_agent_already_committed(tmp_path: Path) -> None:
    """require_changes=False distinguishes 'nothing left over' from 'did nothing'.

    The quick-lane agent is asked to commit its own work. If it does, a clean
    index means success, not failure — the ahead-of-main assertion is what
    decides.
    """
    repo = _init_git_repo(tmp_path / "r")
    loop._commit_all(repo, message="sweep", require_changes=False)  # must not raise


def test_commit_all_still_raises_when_changes_required(tmp_path: Path) -> None:
    from atlas.worktree import WorktreeError

    repo = _init_git_repo(tmp_path / "r")
    with pytest.raises(WorktreeError, match="nothing to commit"):
        loop._commit_all(repo, message="sweep", require_changes=True)


def test_commit_all_sweeps_uncommitted_agent_work(tmp_path: Path) -> None:
    """The exact live failure: agent edits a file but never runs git commit."""
    import subprocess as sp

    repo = _init_git_repo(tmp_path / "r")
    sp.run(["git", "checkout", "-q", "-b", "atlas/work"], cwd=repo, capture_output=True, check=True)
    # The agent edits a file and stops there — no git commit.
    (repo / "seed.txt").write_text("edited by agent\n")

    loop._commit_all(repo, message="atlas: sweep", require_changes=False)
    loop._assert_branch_has_commits(repo)  # swept up, now ahead of main


def test_assert_main_checkout_untouched_detects_agent_commit(tmp_path: Path) -> None:
    """The most serious T-L2.13 finding, now loud instead of silent.

    On 2026-07-27 the unattended agent committed
    `fix(config): add .atlas.toml to .gitignore` directly onto the operator's
    checked-out feature branch, outside its worktree, while leaving the
    worktree's own copy uncommitted. The worktree is a directory boundary,
    not a sandbox — the agent is handed `--add-dir repo_root` so it can read
    tasks.md, and nothing physically stops it writing there.
    """
    import subprocess as sp

    from atlas.worktree import WorktreeError

    repo = _init_git_repo(tmp_path / "r")
    before = loop._head_sha(repo)

    # Simulate the agent committing into the primary checkout.
    (repo / "seed.txt").write_text("agent wrote here\n")
    sp.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    sp.run(["git", "commit", "-m", "agent escape"], cwd=repo, capture_output=True, check=True)

    with pytest.raises(WorktreeError, match="committed into the primary checkout"):
        loop._assert_main_checkout_untouched(repo, before)


def test_assert_main_checkout_untouched_passes_when_head_unmoved(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "r")
    before = loop._head_sha(repo)
    # Uncommitted noise in the primary checkout is not an escape — only a
    # moved HEAD is. Being stricter would false-positive on every operator
    # who has edits in flight while the daemon runs.
    (repo / "seed.txt").write_text("operator edit\n")

    loop._assert_main_checkout_untouched(repo, before)  # must not raise


def test_assert_main_checkout_untouched_is_a_noop_without_a_baseline(tmp_path: Path) -> None:
    """A repo whose HEAD couldn't be read must not block delivery."""
    loop._assert_main_checkout_untouched(tmp_path, None)


def test_startup_reconcile_prunes_the_worktree_current_run_still_names(tmp_path: Path) -> None:
    """After kill -9, .atlas/current-run names the DEAD run's worktree.

    _sweep_orphaned_worktrees treats that path as live and retains it — so
    the sweep skipped precisely the orphan it exists to prune. Observed in
    T-L2.13's crash drill: the issue was correctly relabeled back to
    atlas:ready, but the worktree survived every restart.

    At daemon startup no run can be in flight, so every pointer is stale by
    construction.
    """
    from atlas.state import StateStore
    from atlas.worktree import WorktreeManager

    repo = _init_git_repo(tmp_path / "r")
    wt = WorktreeManager(repo).create(slug="stranded", run_id="dead0000deadbeef")
    StateStore(repo).write_current_run("dead0000deadbeef", "stranded", wt)
    assert wt.exists()

    targets = _targets(repo)
    with (
        patch("atlas.queue_gh.list_labeled", return_value=[]),
        patch("atlas.queue_gh.sync", return_value=[]),
    ):
        # Mid-run semantics: the pointer is honored, the worktree retained.
        loop.reconcile_orphans(_cfg(repo), targets=targets)
        assert wt.exists(), "a live run's worktree must never be swept"

        # Startup semantics: the pointer is stale, so the orphan goes.
        reconciled = loop.reconcile_orphans(_cfg(repo), targets=targets, at_startup=True)

    assert not wt.exists()
    assert any("stranded" in item for item in reconciled)
    assert not (repo / ".atlas" / "current-run").exists()


def test_planned_lane_resolves_the_dev_docs_be_command(tmp_path: Path) -> None:
    """`/dev-docs-be` is not a real slash command.

    plugin_resolver maps the bare tool name to `DEV-ESSENTIALS:dev-docs-be`;
    the quick lane has always gone through that mapping, but the planned lane
    hardcoded the unresolved name. The agent got an unknown command, wrote no
    triad, and still exited 0 — so the run "succeeded" and delivered nothing
    (T-L2.13, 2026-07-27).
    """
    from atlas.plugin_resolver import build_prompt, resolve

    prompt = build_prompt(resolve("dev-docs-be"), "task", "hint")
    assert prompt.startswith("/DEV-ESSENTIALS:dev-docs-be ")
    assert not prompt.startswith("/dev-docs-be ")
