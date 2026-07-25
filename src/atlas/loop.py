"""The loop daemon — poll/dispatch/deliver/sync (TRD-v3 §3.5, Phase L2).

``tick()`` is a linear function; ``run_forever()`` is a ``while True`` loop
over it. No new orchestration framework — matches TRD-v3 §12's explicit
anti-framework risk mitigation. ``loop.py`` never shells ``gh`` directly;
every GitHub interaction goes through ``queue_gh.py`` (TRD-v3 §6, grep-
enforced by ``tests/unit/test_queue_gh.py::test_loop_module_never_shells_gh_directly``).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from atlas import queue_gh
from atlas.cli import make_pipeline
from atlas.cli_backend import UnknownBackendError, make_backend
from atlas.config import Config, LoopConfig
from atlas.deliverer import DeliveryError, GhPrDeliverer, PrRef
from atlas.orchestrator import AbortedError, GateDecision, RunResult
from atlas.plumb_io import PlumbIO
from atlas.queue_gh import GhCliError, Issue
from atlas.triage import TriageResult, triage
from atlas.worktree import WorktreeError, WorktreeManager

_logger = logging.getLogger("atlas.loop")

_LOOP_STATE_RELATIVE_PATH = Path(".atlas") / "loop-state.json"
_WORKFLOW_NAME = "loop_dev"


class AbortedRunError(Exception):
    """Raised when a loop_dev run completes with a non-success RunResult.status."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickResult:
    action: Literal["idle", "dispatched", "synced", "breaker_open", "budget_exhausted"]
    issue_number: int | None
    lane: Literal["quick", "planned"] | None
    pr_ref: PrRef | None
    detail: str


@dataclass
class LoopState:
    """Mutable, persisted-to-disk loop state — survives process restarts.

    Distinct from RunContext/RunResult (per-run) — this is per-loop-process
    (Decision #6). Persisted as .atlas/loop-state.json.
    """

    runs_today: int = 0
    dollars_today: float = 0.0
    day: str = ""
    consecutive_no_progress: int = 0
    consecutive_identical_errors: int = 0
    last_error_signature: str | None = None
    breaker_open_until: str | None = None
    last_tick_at: str | None = None
    synced_pr_outcomes: list[str] = field(default_factory=list)

    @classmethod
    def load_or_init(cls, repo_root: Path) -> LoopState:
        path = repo_root / _LOOP_STATE_RELATIVE_PATH
        if not path.exists():
            return cls(day=_today())
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                runs_today=int(raw.get("runs_today", 0)),
                dollars_today=float(raw.get("dollars_today", 0.0)),
                day=str(raw.get("day", _today())),
                consecutive_no_progress=int(raw.get("consecutive_no_progress", 0)),
                consecutive_identical_errors=int(raw.get("consecutive_identical_errors", 0)),
                last_error_signature=raw.get("last_error_signature"),
                breaker_open_until=raw.get("breaker_open_until"),
                last_tick_at=raw.get("last_tick_at"),
                synced_pr_outcomes=list(raw.get("synced_pr_outcomes", [])),
            )
        except (json.JSONDecodeError, ValueError, TypeError, KeyError):
            _logger.warning("loop-state.json corrupted at %s; initializing fresh state", path)
            return cls(day=_today())

    def persist(self, repo_root: Path) -> None:
        path = repo_root / _LOOP_STATE_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)


def _today() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# Budget / breaker
# ---------------------------------------------------------------------------


def _reset_daily_counters_if_new_day(state: LoopState) -> None:
    today = _today()
    if state.day != today:
        state.day = today
        state.runs_today = 0
        state.dollars_today = 0.0


def budget_exhausted(state: LoopState, cfg: LoopConfig) -> bool:
    return (
        state.runs_today >= cfg.max_runs_per_day or state.dollars_today >= cfg.max_dollars_per_day
    )


def breaker_open(state: LoopState, cfg: LoopConfig) -> bool:
    if state.breaker_open_until is None:
        return False
    try:
        until = datetime.fromisoformat(state.breaker_open_until)
    except ValueError:
        return False
    return datetime.now(tz=UTC) < until


def record_tick_outcome(
    state: LoopState, cfg: LoopConfig, *, made_progress: bool, error_signature: str | None
) -> None:
    if made_progress:
        state.consecutive_no_progress = 0
        state.consecutive_identical_errors = 0
        state.last_error_signature = None
        return

    state.consecutive_no_progress += 1

    if error_signature is not None and error_signature == state.last_error_signature:
        state.consecutive_identical_errors += 1
    else:
        state.consecutive_identical_errors = 1 if error_signature is not None else 0
    state.last_error_signature = error_signature

    if (
        state.consecutive_no_progress >= cfg.no_progress_limit
        or state.consecutive_identical_errors >= cfg.identical_error_limit
    ):
        deadline = datetime.now(tz=UTC).timestamp() + cfg.cooldown_min * 60
        state.breaker_open_until = datetime.fromtimestamp(deadline, tz=UTC).isoformat()


def _error_signature(exc: Exception) -> str:
    return f"{type(exc).__name__}:{exc}"


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

    pipeline, recorder = make_pipeline(
        repo_root,
        cfg,
        auto_approve=True,
        workflow=_WORKFLOW_NAME,
        backend_override=engine,
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
    return pr_ref, result.ctx.run_id, 0.0


def _pr_body(issue: Issue, run_id: str) -> str:
    return f"Closes #{issue.number}\n\nplumb run_id: `{run_id}`"


def run_planned_first_pass(
    issue: Issue, cfg: Config, *, repo_root: Path
) -> tuple[PrRef, str, float]:
    """Planned lane, first-pass-only (Decision #2): produce the TRS triad via
    dev-docs-be, open a plan-only PR, stop. No code_gen dispatch this tick."""
    prompt_context = build_issue_prompt(issue)

    plumb = PlumbIO(real=True)
    run_id = plumb.open_run(task=prompt_context)
    ctx_slug = _slugify(issue.title)

    t0 = time.monotonic()
    try:
        backend = make_backend(_engine_for_issue(issue) or cfg.default_backend)
    except UnknownBackendError as exc:
        plumb.close_run(run_id=run_id, status="failure")
        raise AbortedRunError(f"planned-lane dispatch failed: {exc}") from exc

    argv = backend.build_argv(
        prompt=(
            f"/dev-docs-be Detail this GitHub issue into a TRS triad under "
            f"dev/active/{ctx_slug}/. Issue:\n\n{prompt_context}"
        ),
        model=cfg.model,
        add_dirs=[repo_root],
        timeout_s=1800,
        extra_flags={},
    )
    result_proc = subprocess.run(
        argv, cwd=str(repo_root), capture_output=True, check=False, timeout=1800, text=True
    )
    latency_ms = (time.monotonic() - t0) * 1000.0
    status, output_text, error_type = backend.parse_result(
        result_proc.stdout, result_proc.stderr, result_proc.returncode
    )
    plumb.record_span(
        run_id=run_id,
        kind="plan",
        name="dev_docs_be",
        status=status,
        latency_ms=latency_ms,
        error_type=error_type,
    )

    if status != "success":
        plumb.close_run(run_id=run_id, status="failure")
        raise AbortedRunError(f"planned-lane dev-docs-be dispatch failed: {output_text}")

    plumb.close_run(run_id=run_id, status="success")

    worktree = WorktreeManager(repo_root)
    wt_path = worktree.create(slug=ctx_slug, run_id=run_id)
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
    return pr_ref, run_id, 0.0


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
            plumb.record_user_signal(
                run_id=active_run_id,
                span_id="",
                metric="user_signal",
                decision=GateDecision(label=label, turn_count=1, reason=None),
            )
            plumb.close_run(
                run_id=active_run_id, status="success" if s.outcome == "merged" else "failure"
            )

        queue_gh.relabel(s.issue, state="done" if s.outcome == "merged" else "rejected")
        state.synced_pr_outcomes.append(dedupe_key)
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
        record_tick_outcome(
            state, cfg.loop, made_progress=made_progress_from_sync, error_signature=None
        )
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
            action="dispatched",
            issue_number=issue.number,
            lane=triage_result.lane,
            pr_ref=None,
            detail=f"failed: could not resolve gh identity: {exc}",
        )

    queue_gh.claim(issue, assignee=assignee)

    try:
        if triage_result.lane == "quick":
            pr_ref, run_id, cost = run_one_shot(issue, cfg, repo_root=repo_root)
        else:
            pr_ref, run_id, cost = run_planned_first_pass(issue, cfg, repo_root=repo_root)

        queue_gh.comment(issue, body=_format_run_summary(run_id, pr_ref, cost))

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
            action="dispatched",
            issue_number=issue.number,
            lane=triage_result.lane,
            pr_ref=None,
            detail=f"failed: {exc}",
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


def _format_run_summary(run_id: str, pr_ref: PrRef, cost: float) -> str:
    return f"atlas loop dispatched this issue.\n\nplumb run_id: `{run_id}`\nPR: {pr_ref.url}"


# ---------------------------------------------------------------------------
# run_forever() + reconcile_orphans() (T-L2.8)
# ---------------------------------------------------------------------------


def run_forever(cfg: Config, *, repos: list[str], repo_root: Path) -> None:
    state = LoopState.load_or_init(repo_root)
    reconcile_orphans(cfg, repos=repos, repo_root=repo_root)

    while True:
        if breaker_open(state, cfg.loop):
            time.sleep(cfg.loop.poll_interval_s)
            continue
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
    _logger.info(
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

    worktrees_dir = repo_root / ".atlas" / "worktrees"
    if worktrees_dir.is_dir():
        active_issue_slugs = _active_issue_slugs(repos)
        worktree_manager = WorktreeManager(repo_root)
        for worktree_dir in worktrees_dir.glob("*"):
            if not worktree_dir.is_dir():
                continue
            if _is_worktree_for_active_issue(worktree_dir, active_issue_slugs):
                continue
            try:
                worktree_manager.cleanup(worktree_dir)
                reconciled.append(f"worktree {worktree_dir.name}")
            except WorktreeError as exc:
                _logger.warning("cleanup failed for orphaned worktree %s: %s", worktree_dir, exc)

    return reconciled


def _active_issue_slugs(repos: list[str]) -> set[str]:
    slugs: set[str] = set()
    for repo in repos:
        for issue in queue_gh.list_labeled(repo, "atlas:working"):
            slugs.add(_slugify(issue.title))
    return slugs


def _is_worktree_for_active_issue(worktree_dir: Path, active_slugs: set[str]) -> bool:
    name = worktree_dir.name
    return any(name.startswith(f"{slug}-") for slug in active_slugs)


__all__ = [
    "AbortedRunError",
    "LoopState",
    "TickResult",
    "budget_exhausted",
    "breaker_open",
    "build_issue_prompt",
    "record_tick_outcome",
    "reconcile_orphans",
    "run_forever",
    "run_one_shot",
    "run_planned_first_pass",
    "sync_prior_prs",
    "tick",
]
