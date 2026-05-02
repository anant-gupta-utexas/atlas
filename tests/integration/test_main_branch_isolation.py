"""
Integration test: T3.3 — main branch must be byte-identical before and after
an atlas pipeline run through gate 3 (plan_review stage).

This test uses a real (temporary) git repo, a real WorktreeManager, and a
stubbed Pipeline to verify that no commits land on main as a side-effect of
the worktree hand-off.

Skip when git is unavailable (CI without git configured).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from atlas.orchestrator import (
    GateDecision,
    Pipeline,
    RunContext,
    StageOutcome,
)
from atlas.plumb_io import PlumbIO
from atlas.stages import StageSpec
from atlas.state import StateStore
from atlas.worktree import WorktreeManager

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _git_log(repo: Path) -> str:
    """Return the full git log for HEAD branch."""
    result = _git("log", "--oneline", "HEAD", cwd=repo)
    return result.stdout


def _init_repo(path: Path) -> Path:
    """Initialise a minimal git repo with an initial commit on main."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=path)
    _git("config", "user.email", "test@atlas.local", cwd=path)
    _git("config", "user.name", "Atlas Test", cwd=path)
    readme = path / "README.md"
    readme.write_text("# test repo\n")
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


# ---------------------------------------------------------------------------
# Fake runner that approves everything through stage 4 (plan_review)
# ---------------------------------------------------------------------------


class _FakeRunner:
    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        return StageOutcome(
            stage=stage,
            span_id="",
            status="success",
            output_text=f"output of {stage.name.value}",
            error_type=None,
        )


class _FakePrompter:
    def ask(self, *, stage: StageSpec, gate_index: int) -> GateDecision:
        return GateDecision(label="approved", turn_count=1, reason=None)


# ---------------------------------------------------------------------------
# The isolation test
# ---------------------------------------------------------------------------


def test_main_branch_log_is_identical_after_pipeline_run_through_gate3(
    tmp_path: Path,
) -> None:
    """
    TRD §Mandatory tests: git log main must be byte-identical before and after
    an atlas pipeline run that proceeds up to (and including) the plan_review
    stage (gate 3).

    The pipeline must NOT create any commits on main.
    """
    repo = _init_repo(tmp_path / "repo")
    log_before = _git_log(repo)

    plumb = PlumbIO(real=False)
    state = StateStore(repo)
    worktree_mgr = WorktreeManager(repo)

    pipeline = Pipeline(
        repo_root=repo,
        state=state,
        plumb=plumb,
        runner=_FakeRunner(),
        prompter=_FakePrompter(),
        worktree=worktree_mgr,
    )

    ctx = pipeline.start(task="add cache middleware", slug="cache-middleware")

    # Run stages 0–4 (research → prd_draft → trd_draft → tds_gen → plan_review)
    # Stage 5 (code_gen) triggers worktree creation; we stop before it to keep
    # the test focused on the "no main commit" invariant through gate 3.
    for _ in range(5):
        outcome = pipeline.step(ctx)
        assert outcome is not None
        assert outcome.status in ("success", "awaiting_hook")
        if outcome.status == "awaiting_hook":
            break

    log_after = _git_log(repo)

    assert log_before == log_after, (
        "git log main changed during atlas pipeline run!\n"
        f"Before:\n{log_before}\nAfter:\n{log_after}"
    )


def test_worktree_is_created_under_atlas_worktrees_dir(tmp_path: Path) -> None:
    """
    When stage 5 (code_gen) is entered with a real WorktreeManager, the
    worktree must be under .atlas/worktrees/ and main must be unchanged.
    """
    repo = _init_repo(tmp_path / "repo")
    log_before = _git_log(repo)

    plumb = PlumbIO(real=False)
    state = StateStore(repo)
    worktree_mgr = WorktreeManager(repo)

    pipeline = Pipeline(
        repo_root=repo,
        state=state,
        plumb=plumb,
        runner=_FakeRunner(),
        prompter=_FakePrompter(),
        worktree=worktree_mgr,
    )

    ctx = pipeline.start(task="add cache middleware", slug="cache-middleware")

    # Step through stages 0–5 (stage 5 triggers worktree creation)
    for _ in range(6):
        outcome = pipeline.step(ctx)
        if outcome is None:
            break
        if outcome.status == "awaiting_hook":
            # Stage 5 completed — worktree should now exist
            worktrees_dir = repo / ".atlas" / "worktrees"
            assert worktrees_dir.exists(), ".atlas/worktrees/ was not created"
            created = list(worktrees_dir.iterdir())
            assert len(created) == 1, f"Expected exactly 1 worktree, got: {created}"
            wt_path = created[0]
            assert wt_path.is_dir()
            assert "cache-middleware" in wt_path.name
            break

    log_after = _git_log(repo)
    assert log_before == log_after, (
        "git log main changed after code_gen worktree creation!\n"
        f"Before:\n{log_before}\nAfter:\n{log_after}"
    )
