"""The loop daemon — poll/dispatch/deliver/sync (TRD-v3 §3.5, Phase L2).

``tick()`` is a linear function; ``run_forever()`` is a ``while True`` loop
over it. No new orchestration framework — matches TRD-v3 §12's explicit
anti-framework risk mitigation. ``loop.py`` never shells ``gh`` directly;
every GitHub interaction goes through ``queue_gh.py`` (TRD-v3 §6, grep-
enforced by ``tests/unit/test_queue_gh.py::test_loop_module_never_shells_gh_directly``).
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from atlas import queue_gh
from atlas.cli_backend import UnknownBackendError, UsageReporting, make_backend, resolve_model
from atlas.config import Config, LoopConfig
from atlas.deliverer import DeliveryError, GhPrDeliverer, PrRef

# Re-exported so `from atlas.loop import LoopState, breaker_open, ...` keeps
# working after the budget/breaker split into loop_budget.py.
from atlas.loop_budget import (
    LoopState,
    _now_iso,
    _reset_daily_counters_if_new_day,
    _today,  # noqa: F401 — re-exported; tests and callers use loop._today()
    breaker_open,
    budget_exhausted,
    record_tick_outcome,
)
from atlas.loop_budget import error_signature as _error_signature
from atlas.loop_budget import remember_synced_outcome as _remember_synced_outcome
from atlas.loop_budget import warn_on_unenforced_budget as _warn_on_unenforced_budget
from atlas.orchestrator import AbortedError, GateDecision, RunResult
from atlas.pipeline_factory import make_pipeline
from atlas.plumb_io import PlumbIO
from atlas.queue_gh import GhCliError, Issue
from atlas.triage import TriageResult, triage
from atlas.worktree import WorktreeError, WorktreeManager

_logger = logging.getLogger("atlas.loop")

_WORKFLOW_NAME = "loop_dev"


class AbortedRunError(Exception):
    """Raised when a loop_dev run completes with a non-success RunResult.status."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickResult:
    """Outcome of one tick.

    ``action`` is the machine-readable field — count dispatches by it, never
    by string-matching ``detail``. ``"dispatched"`` means a run was dispatched
    and delivered; a run that failed (or never started) is ``"failed"``, so
    callers can't over-count dispatches.
    """

    action: Literal["idle", "dispatched", "failed", "synced", "breaker_open", "budget_exhausted"]
    issue_number: int | None
    lane: Literal["quick", "planned"] | None
    pr_ref: PrRef | None
    detail: str


# ---------------------------------------------------------------------------
# Prompt construction (Decision #10)
# ---------------------------------------------------------------------------

_SCOPE_PREAMBLE = (
    "Scope this change strictly to the issue's stated acceptance criteria. "
    "Do not touch files outside that scope."
)


def build_issue_prompt(issue: Issue) -> str:
    return f"{issue.title}\n\n{issue.body}\n\n{_SCOPE_PREAMBLE}"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "issue"


def _engine_for_issue(issue: Issue) -> str | None:
    if "engine:codex" in issue.labels:
        return "codex"
    if "engine:claude" in issue.labels:
        return "claude"
    return None


def current_gh_user() -> str:
    return queue_gh.current_user()


# ---------------------------------------------------------------------------
# Dispatch — quick lane
# ---------------------------------------------------------------------------


def run_one_shot(issue: Issue, cfg: Config, *, repo_root: Path) -> tuple[PrRef, str, float]:
    engine = _engine_for_issue(issue)
    prompt_context = build_issue_prompt(issue)
    # Pinned before dispatch so a post-run comparison can catch an agent that
    # committed into the operator's checkout instead of its worktree.
    repo_head_before = _head_sha(repo_root)

    pipeline, recorder = make_pipeline(
        repo_root,
        cfg,
        auto_approve=True,
        workflow=_WORKFLOW_NAME,
        backend_override=engine,
        max_turns=cfg.loop.max_turns,
        loop_mode=True,
    )
    ctx = pipeline.start(task=prompt_context, slug=_slugify(issue.title))
    try:
        result: RunResult = pipeline.run_to_completion(ctx)
    except AbortedError as exc:
        raise AbortedRunError(f"loop_dev run for issue #{issue.number} aborted: {exc}") from exc

    if result.status != "success":
        raise AbortedRunError(f"loop_dev run {result.ctx.run_id} ended with status={result.status}")

    if result.ctx.worktree_path is None:
        raise AbortedRunError(
            f"loop_dev run {result.ctx.run_id} succeeded but produced no worktree_path"
        )

    worktree = WorktreeManager(repo_root)

    # The loop_dev prompt asks code_gen to commit, but a prompt is not a
    # guarantee — sweep up anything the agent left uncommitted, then verify
    # the branch is actually ahead of main before delivering. Without both
    # steps a "successful" run silently delivers nothing (T-L2.13).
    _assert_main_checkout_untouched(repo_root, repo_head_before)
    _commit_all(
        result.ctx.worktree_path,
        message=f"atlas: {issue.title} (#{issue.number})",
        require_changes=False,
    )
    _assert_branch_has_commits(result.ctx.worktree_path)

    deliverer = GhPrDeliverer(repo_root=repo_root, worktree=worktree)
    branch = f"atlas/{ctx.slug}-{result.ctx.run_id[:8]}"
    pr_ref = queue_gh.deliver_pr(
        issue,
        branch=branch,
        title=f"{issue.title} (Closes #{issue.number})",
        body=_pr_body(issue, result.ctx.run_id),
        repo_root=repo_root,
        run_id=result.ctx.run_id,
        worktree_path=result.ctx.worktree_path,
        deliverer=deliverer,
    )
    return pr_ref, result.ctx.run_id, result.dollar_cost or 0.0


def _pr_body(issue: Issue, run_id: str) -> str:
    return f"Closes #{issue.number}\n\nplumb run_id: `{run_id}`"


def _commit_all(worktree_path: Path, *, message: str, require_changes: bool = True) -> None:
    """Stage and commit everything in ``worktree_path``.

    With ``require_changes`` (the default), raises WorktreeError when there is
    nothing staged — an empty branch would make `gh pr create` fail with "No
    commits between main and ..." much further from the cause.

    Pass ``require_changes=False`` when the agent may legitimately have
    committed its own work already, so an empty index means "nothing left
    over" rather than "the agent did nothing". Callers in that mode must
    follow up with ``_assert_branch_has_commits``, which is the check that
    actually protects delivery.
    """
    add = subprocess.run(
        ["git", "add", "-A"], cwd=worktree_path, capture_output=True, check=False, text=True
    )
    if add.returncode != 0:
        raise WorktreeError(f"git add failed (exit {add.returncode}): {add.stderr.strip()}")

    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=worktree_path,
        capture_output=True,
        check=False,
        text=True,
    )
    if staged.returncode == 0:
        if not require_changes:
            return
        raise WorktreeError(f"nothing to commit in {worktree_path}: agent produced no changes")

    commit = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=worktree_path,
        capture_output=True,
        check=False,
        text=True,
    )
    if commit.returncode != 0:
        raise WorktreeError(
            f"git commit failed (exit {commit.returncode}): {commit.stderr.strip()}"
        )


def _assert_branch_has_commits(worktree_path: Path, *, base_branch: str = "main") -> None:
    """Fail loudly if the worktree branch is not ahead of ``base_branch``.

    This is the check that actually protects delivery. `gh pr create` on a
    branch identical to main fails with "No commits between main and ...",
    an error that names neither the run nor the reason.

    Observed live (T-L2.13, 2026-07-27): the quick lane's agent edited
    `.gitignore` correctly, the pipeline reported success across all three
    spans, and delivery still produced nothing — because the agent never ran
    `git commit` and nothing verified that it had. The L2 code review's C1
    fix closed exactly this hole in the *planned* lane; the quick lane, which
    relies on the `loop_dev.yaml` prompt politely asking the agent to commit,
    kept it.
    """
    result = subprocess.run(
        ["git", "rev-list", "--count", f"{base_branch}..HEAD"],
        cwd=worktree_path,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(
            f"git rev-list failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    if result.stdout.strip() == "0":
        raise WorktreeError(
            f"branch in {worktree_path} has no commits ahead of {base_branch}; "
            "refusing to open an empty PR"
        )


def _head_sha(repo_path: Path) -> str | None:
    """Current HEAD sha, or None if it can't be read (never raises)."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _assert_main_checkout_untouched(repo_root: Path, before_sha: str | None) -> None:
    """Fail if the unattended agent committed into the operator's own checkout.

    The worktree is a *directory* boundary, not a filesystem sandbox (TRD-v3
    §3.6) — the agent is handed ``--add-dir repo_root`` so it can read
    ``dev/active/<slug>/tasks.md``, and nothing physically stops it writing
    there instead.

    On 2026-07-27 that stopped being theoretical: during T-L2.13's first live
    zero-touch run, the agent committed ``fix(config): add .atlas.toml to
    .gitignore`` straight onto the operator's checked-out feature branch while
    leaving the worktree's copy uncommitted. An unattended daemon silently
    rewriting the branch a human has checked out is the single worst failure
    mode available to this design, and it produced no warning at all.

    This does not *prevent* the escape (only a real sandbox could), but it
    converts a silent corruption into a loud, attributable failure the
    operator sees on the very next tick.
    """
    if before_sha is None:
        return
    after_sha = _head_sha(repo_root)
    if after_sha is not None and after_sha != before_sha:
        raise WorktreeError(
            f"agent committed into the primary checkout at {repo_root} "
            f"(HEAD moved {before_sha[:8]} -> {after_sha[:8]}); it should only "
            "write inside its worktree. Inspect and reset that commit before rerunning."
        )


def _cleanup_quietly(worktree: WorktreeManager, worktree_path: Path) -> None:
    """Best-effort worktree cleanup on a failure path — never masks the original error."""
    try:
        worktree.cleanup(worktree_path)
    except WorktreeError as exc:
        _logger.warning("cleanup failed for %s: %s", worktree_path, exc)


def run_planned_first_pass(
    issue: Issue, cfg: Config, *, repo_root: Path
) -> tuple[PrRef, str, float]:
    """Planned lane, first-pass-only (Decision #2): produce the TRS triad via
    dev-docs-be, open a plan-only PR, stop. No code_gen dispatch this tick.

    Ordering mirrors the quick lane (Pipeline creates the worktree *before*
    the isolated stage runs): create the worktree, run dev-docs-be inside it,
    commit the triad, then deliver. Running dev-docs-be against ``repo_root``
    and creating the worktree afterwards would write the triad into the main
    working tree and push a branch with zero commits ahead of main.
    """
    prompt_context = build_issue_prompt(issue)

    plumb = PlumbIO(real=True)
    run_id = plumb.open_run(task=prompt_context)
    ctx_slug = _slugify(issue.title)
    repo_head_before = _head_sha(repo_root)

    engine = _engine_for_issue(issue) or cfg.default_backend
    try:
        backend = make_backend(engine)
    except UnknownBackendError as exc:
        plumb.close_run(run_id=run_id, status="failure")
        raise AbortedRunError(f"planned-lane dispatch failed: {exc}") from exc

    worktree = WorktreeManager(repo_root)
    try:
        wt_path = worktree.create(slug=ctx_slug, run_id=run_id)
    except WorktreeError:
        plumb.close_run(run_id=run_id, status="failure")
        raise

    argv = backend.build_argv(
        prompt=(
            f"/dev-docs-be Detail this GitHub issue into a TRS triad under "
            f"dev/active/{ctx_slug}/. Issue:\n\n{prompt_context}"
        ),
        # cfg.model is a Claude model name and is not portable across engines
        # (`codex exec --model haiku` is an HTTP 400) — see resolve_model.
        model=resolve_model(
            backend_name=engine,
            config_model=cfg.model,
            backend_models=cfg.backend_models,
        ),
        add_dirs=[wt_path],
        timeout_s=1800,
        # Same unattended profile SubprocessStageRunner applies in loop mode —
        # this lane bypasses the Pipeline, so it has to set them itself or the
        # planned lane would be the one dispatch path with no telemetry.
        extra_flags={
            "max_turns": str(cfg.loop.max_turns),
            "telemetry": "json",
            "permission_mode": "acceptEdits",
        },
    )
    t0 = time.monotonic()
    result_proc = subprocess.run(
        argv, cwd=str(wt_path), capture_output=True, check=False, timeout=1800, text=True
    )
    latency_ms = (time.monotonic() - t0) * 1000.0
    status, output_text, error_type = backend.parse_result(
        result_proc.stdout, result_proc.stderr, result_proc.returncode
    )
    usage = backend.span_usage(result_proc.stdout) if isinstance(backend, UsageReporting) else None
    plumb.record_span(
        run_id=run_id,
        kind="plan",
        name="dev_docs_be",
        status=status,
        latency_ms=latency_ms,
        error_type=error_type,
        tokens=usage.tokens if usage is not None else None,
        attributes=usage.attributes if usage is not None else None,
    )
    plan_cost = usage.dollar_cost if usage is not None else None
    if plan_cost is not None:
        plumb.set_usage(run_id=run_id, dollar_cost=plan_cost)

    if status != "success":
        plumb.close_run(run_id=run_id, status="failure")
        _cleanup_quietly(worktree, wt_path)
        raise AbortedRunError(f"planned-lane dev-docs-be dispatch failed: {output_text}")

    try:
        # require_changes=False for the same reason as the quick lane: if
        # dev-docs-be ever commits its own triad, an empty index means
        # "nothing left over", not "the agent did nothing". The
        # ahead-of-main assertion below is the real guard either way.
        _assert_main_checkout_untouched(repo_root, repo_head_before)
        _commit_all(
            wt_path,
            message=f"docs(plan): TRS triad for #{issue.number}",
            require_changes=False,
        )
        _assert_branch_has_commits(wt_path)
    except WorktreeError:
        plumb.close_run(run_id=run_id, status="failure")
        _cleanup_quietly(worktree, wt_path)
        raise

    plumb.close_run(run_id=run_id, status="success")

    branch = f"atlas/{ctx_slug}-{run_id[:8]}"
    deliverer = GhPrDeliverer(repo_root=repo_root, worktree=worktree)
    pr_ref = queue_gh.deliver_pr(
        issue,
        branch=branch,
        title=f"[plan] {issue.title} (#{issue.number})",
        body=(
            f"Plan-only PR for #{issue.number} — TRS triad under "
            f"`dev/active/{ctx_slug}/`. See Pending Decisions in the triad "
            f"before implementation.\n\nplumb run_id: `{run_id}`"
        ),
        repo_root=repo_root,
        run_id=run_id,
        worktree_path=wt_path,
        deliverer=deliverer,
    )
    return pr_ref, run_id, plan_cost or 0.0


# ---------------------------------------------------------------------------
# sync_prior_prs — idempotent outcome scoring (T-L2.7)
# ---------------------------------------------------------------------------


def sync_prior_prs(repo: str, state: LoopState) -> list[queue_gh.PrStatus]:
    statuses = queue_gh.sync(repo)
    results: list[queue_gh.PrStatus] = []

    for s in statuses:
        if s.outcome == "open":
            continue

        dedupe_key = f"{s.issue.number}:{s.pr_number}:{s.outcome}"
        if dedupe_key in state.synced_pr_outcomes:
            continue

        label = "approved" if s.outcome == "merged" else "rejected"
        run_id = queue_gh.find_run_id_comment(s.issue)

        if run_id is not None:
            plumb = PlumbIO(real=True)
            active_run_id = plumb.reopen_run(run_id)
            # Anchor the score to a real span. Every other record_user_signal
            # call site passes a span_id from record_span; an empty string is a
            # dangling foreign key into plumb's scores.span_id.
            span_id = plumb.record_span(
                run_id=active_run_id,
                kind="deliver",
                name="pr_outcome",
                status="success" if s.outcome == "merged" else "failure",
                latency_ms=0.0,
                error_type=None,
            )
            plumb.record_user_signal(
                run_id=active_run_id,
                span_id=span_id,
                metric="user_signal",
                decision=GateDecision(label=label, turn_count=1, reason=None),
            )
            plumb.close_run(
                run_id=active_run_id, status="success" if s.outcome == "merged" else "failure"
            )

        queue_gh.relabel(s.issue, state="done" if s.outcome == "merged" else "rejected")
        _remember_synced_outcome(state, dedupe_key)
        results.append(s)

    return results


# ---------------------------------------------------------------------------
# tick() — the core state machine (T-L2.5)
# ---------------------------------------------------------------------------


def tick(cfg: Config, state: LoopState, *, repos: list[str], repo_root: Path) -> TickResult:
    _reset_daily_counters_if_new_day(state)

    sync_results: list[queue_gh.PrStatus] = []
    for repo in repos:
        try:
            sync_results += sync_prior_prs(repo, state)
        except GhCliError as exc:
            _logger.warning("sync failed for repo=%s: %s", repo, exc)
            continue

    made_progress_from_sync = len(sync_results) > 0

    if breaker_open(state, cfg.loop):
        state.last_tick_at = _now_iso()
        state.persist(repo_root)
        return TickResult(
            action="breaker_open",
            issue_number=None,
            lane=None,
            pr_ref=None,
            detail=f"breaker open until {state.breaker_open_until}",
        )

    if budget_exhausted(state, cfg.loop):
        state.last_tick_at = _now_iso()
        state.persist(repo_root)
        return TickResult(
            action="budget_exhausted",
            issue_number=None,
            lane=None,
            pr_ref=None,
            detail="daily budget exhausted",
        )

    issue = _pull_next_ready(repos, cfg.loop)
    if issue is None:
        # An empty queue is NOT a failure, and must not feed the breaker.
        # This previously called record_tick_outcome(made_progress=False),
        # so a perfectly healthy idle loop incremented
        # consecutive_no_progress every tick and opened its own breaker after
        # no_progress_limit ticks — 90 seconds at the default 30s poll.
        # Worse, an idle tick also reset last_error_signature to None, so the
        # reason a *real* dispatch failure had just occurred was erased before
        # any human could read it (observed live, T-L2.13, 2026-07-27).
        # The breaker exists to stop a loop that keeps failing, not one with
        # nothing to do.
        if made_progress_from_sync:
            record_tick_outcome(state, cfg.loop, made_progress=True, error_signature=None)
        state.last_tick_at = _now_iso()
        state.persist(repo_root)
        return TickResult(
            action="idle", issue_number=None, lane=None, pr_ref=None, detail="no ready issue"
        )

    plumb = PlumbIO(real=True)
    run_id_for_triage = plumb.open_run(task=issue.title)
    triage_result = _triage_issue(issue, plumb=plumb, run_id=run_id_for_triage)
    plumb.close_run(run_id=run_id_for_triage, status="success")

    try:
        assignee = current_gh_user()
    except GhCliError as exc:
        record_tick_outcome(
            state, cfg.loop, made_progress=False, error_signature=_error_signature(exc)
        )
        state.last_tick_at = _now_iso()
        state.persist(repo_root)
        return TickResult(
            action="failed",
            issue_number=issue.number,
            lane=triage_result.lane,
            pr_ref=None,
            detail=f"could not resolve gh identity: {exc}",
        )

    queue_gh.claim(issue, assignee=assignee)

    try:
        if triage_result.lane == "quick":
            pr_ref, run_id, cost = run_one_shot(issue, cfg, repo_root=repo_root)
        else:
            pr_ref, run_id, cost = run_planned_first_pass(issue, cfg, repo_root=repo_root)

        queue_gh.comment(issue, body=_format_run_summary(run_id, pr_ref))

        record_tick_outcome(state, cfg.loop, made_progress=True, error_signature=None)
        state.runs_today += 1
        state.dollars_today += cost
        state.last_tick_at = _now_iso()
        state.persist(repo_root)
        return TickResult(
            action="dispatched",
            issue_number=issue.number,
            lane=triage_result.lane,
            pr_ref=pr_ref,
            detail="ok",
        )

    except (DeliveryError, GhCliError, AbortedRunError, WorktreeError) as exc:
        queue_gh.comment(
            issue,
            body=f"loop_dev run failed: {exc}. Left atlas:working for manual triage.",
        )
        error_sig = _error_signature(exc)
        record_tick_outcome(state, cfg.loop, made_progress=False, error_signature=error_sig)
        state.last_tick_at = _now_iso()
        state.persist(repo_root)
        return TickResult(
            action="failed",
            issue_number=issue.number,
            lane=triage_result.lane,
            pr_ref=None,
            detail=str(exc),
        )


def _pull_next_ready(repos: list[str], loop_cfg: LoopConfig) -> Issue | None:
    """First repo in cfg.repos order with a ready issue; first issue in gh's
    own default (oldest-first) order (Decision #9). Untrusted-author issues
    are skipped, left atlas:ready, not treated as 'no ready issue' (Decision #16)."""
    for repo in repos:
        issues = queue_gh.list_ready(repo)
        for issue in issues:
            if loop_cfg.trusted_authors and issue.author not in loop_cfg.trusted_authors:
                _logger.warning(
                    "skipping issue #%s: untrusted author %r", issue.number, issue.author
                )
                continue
            return issue
    return None


def _triage_issue(issue: Issue, *, plumb: PlumbIO, run_id: str) -> TriageResult:
    return triage(issue, plumb=plumb, run_id=run_id)


def _format_run_summary(run_id: str, pr_ref: PrRef) -> str:
    # No cost line: per-run cost is unavailable until plumb P1-a (TRD-v3
    # §3.6). Add one here when run_one_shot returns a real figure.
    return f"atlas loop dispatched this issue.\n\nplumb run_id: `{run_id}`\nPR: {pr_ref.url}"


# ---------------------------------------------------------------------------
# run_forever() + reconcile_orphans() (T-L2.8)
# ---------------------------------------------------------------------------


def run_forever(cfg: Config, *, repos: list[str], repo_root: Path) -> None:
    _warn_on_unenforced_budget(cfg.loop, engine=cfg.default_backend)
    state = LoopState.load_or_init(repo_root)
    reconcile_orphans(cfg, repos=repos, repo_root=repo_root)

    while True:
        # No outer breaker check: tick() handles the breaker itself (returns
        # action="breaker_open", updates last_tick_at, persists). Short-
        # circuiting here instead would freeze last_tick_at and log nothing,
        # so `atlas loop status` during a cooldown would look like a dead
        # daemon rather than one deliberately waiting.
        try:
            result: TickResult | None = tick(cfg, state, repos=repos, repo_root=repo_root)
        except Exception:
            _logger.exception("tick() raised unexpectedly")
            result = None
        _log_tick(result)
        time.sleep(cfg.loop.poll_interval_s)


def _log_tick(result: TickResult | None) -> None:
    if result is None:
        _logger.warning("tick() failed with an unhandled exception; see traceback above")
        return
    # A failed tick must not scroll past at the same level as an idle one —
    # `detail` is where a dispatch or delivery failure explains itself, and
    # state.last_error_signature is not a durable record of it (a later idle
    # tick used to clear it).
    _logger.log(
        logging.WARNING if result.action == "failed" else logging.INFO,
        "tick: action=%s issue=%s lane=%s detail=%s",
        result.action,
        result.issue_number,
        result.lane,
        result.detail,
    )


def reconcile_orphans(cfg: Config, *, repos: list[str], repo_root: Path) -> list[str]:
    reconciled: list[str] = []
    for repo in repos:
        working_issues = queue_gh.list_labeled(repo, "atlas:working")
        try:
            statuses = queue_gh.sync(repo)
        except GhCliError as exc:
            _logger.warning("reconcile_orphans: sync failed for repo=%s: %s", repo, exc)
            statuses = []

        linked_numbers = {s.issue.number for s in statuses}
        for issue in working_issues:
            if issue.number not in linked_numbers:
                queue_gh.relabel(issue, state="ready")
                reconciled.append(f"issue #{issue.number}")

    reconciled += _sweep_orphaned_worktrees(repo_root)
    return reconciled


def _sweep_orphaned_worktrees(repo_root: Path) -> list[str]:
    """Delete worktrees left behind by a crashed run, retaining the live one.

    The retain-check must be exact, because this deletes directories that may
    hold uncommitted agent work. Matching on re-slugified issue titles (the
    previous approach) was lossy twice over: _slugify truncates to 40 chars,
    so two similar titles collide and an orphan is retained forever; and a
    live run whose slug didn't match would have its work deleted. The
    run_id-keyed path recorded in .atlas/current-run is unambiguous.
    """
    worktrees_dir = repo_root / ".atlas" / "worktrees"
    if not worktrees_dir.is_dir():
        return []

    from atlas.state import StateStore

    try:
        current = StateStore(repo_root).read_current_run_with_worktree()
    except OSError as exc:
        # Fail safe: without a readable state file we cannot tell which
        # worktree is live, so sweep nothing rather than risk deleting it.
        _logger.warning("could not read .atlas/current-run; skipping worktree sweep: %s", exc)
        return []

    live: Path | None = None
    if current is not None and current[2] is not None:
        live = current[2].resolve()

    swept: list[str] = []
    worktree_manager = WorktreeManager(repo_root)
    for worktree_dir in sorted(worktrees_dir.glob("*")):
        if not worktree_dir.is_dir():
            continue
        if live is not None and worktree_dir.resolve() == live:
            continue
        try:
            worktree_manager.cleanup(worktree_dir)
            swept.append(f"worktree {worktree_dir.name}")
        except WorktreeError as exc:
            _logger.warning("cleanup failed for orphaned worktree %s: %s", worktree_dir, exc)
    return swept


__all__ = [
    "AbortedRunError",
    # Re-exported from loop_budget for backwards compatibility.
    "LoopState",
    "TickResult",
    "breaker_open",
    "budget_exhausted",
    "build_issue_prompt",
    "reconcile_orphans",
    "record_tick_outcome",
    "run_forever",
    "run_one_shot",
    "run_planned_first_pass",
    "sync_prior_prs",
    "tick",
]
