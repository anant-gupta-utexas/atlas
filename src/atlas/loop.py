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
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from atlas import judge_gate, queue_gh
from atlas.cli_backend import UnknownBackendError, UsageReporting, make_backend, resolve_model
from atlas.config import Config, LoopConfig, RepoTarget
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
    migrate_legacy_state_if_needed,
    record_tick_outcome,
)
from atlas.loop_budget import error_signature as _error_signature
from atlas.loop_budget import remember_synced_outcome as _remember_synced_outcome
from atlas.loop_budget import warn_on_unenforced_budget as _warn_on_unenforced_budget
from atlas.orchestrator import AbortedError, GateDecision, RunResult
from atlas.pipeline_factory import make_pipeline
from atlas.plugin_resolver import build_prompt, resolve
from atlas.plumb_io import PlumbIO
from atlas.queue_gh import GhCliError, Issue
from atlas.triage import TriageResult, triage
from atlas.worktree import WorktreeError, WorktreeManager

if TYPE_CHECKING:
    # Local-only at runtime (see tick()'s inline import) — self_heal.py
    # imports this module, so a module-level import here would cycle.
    from atlas.self_heal import SelfHealResult

_logger = logging.getLogger("atlas.loop")

_WORKFLOW_NAME = "loop_dev"


class AbortedRunError(Exception):
    """Raised when a loop_dev run completes with a non-success RunResult.status.

    ``worktree_path``, when set, points at the worktree the failed run left
    behind (never cleaned up by ``run_one_shot`` on any failure path — that
    has always been true, not new in Phase L3) so ``self_heal.handle_failure``
    can read its diff to build a diagnosis without re-deriving the path.

    ``run_id``, when set, is the plumb run_id of the failed dispatch itself —
    tick()'s self-heal wiring (T-L3.7) anchors the diagnosis/retry child run
    to this, not the earlier triage run_id.
    """

    def __init__(
        self, message: str, *, worktree_path: Path | None = None, run_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.worktree_path = worktree_path
        self.run_id = run_id


class JudgeGateFailedError(AbortedRunError):
    """Raised when judge_gate.score_diff() returns passed=False (T-L3.4).

    Carries the JudgeGateResult so self_heal.handle_failure can build the
    retry diagnosis without re-scoring. Inherits AbortedRunError's
    worktree_path/run_id so the same recovery path works for both.
    """

    def __init__(
        self,
        result: judge_gate.JudgeGateResult,
        *,
        worktree_path: Path | None,
        run_id: str | None,
    ) -> None:
        super().__init__(
            f"judge gate failed: value_numeric={result.value_numeric:.2f} "
            f"rationale={result.rationale!r}",
            worktree_path=worktree_path,
            run_id=run_id,
        )
        self.result = result


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


@dataclass(frozen=True)
class BatchTickResult:
    """Outcome of one tick() call under Phase L4 concurrent dispatch.

    ``results`` holds one ``TickResult`` per dispatched/failed issue this
    tick, in no particular order. At ``concurrency=1`` it has exactly 0 or 1
    elements — byte-identical to pre-L4 ``tick()``'s single ``TickResult``
    once a caller reads ``results[0]`` (Pending Decision #9: ``TickResult``
    itself is unchanged, not widened)."""

    results: list[TickResult]


@dataclass(frozen=True)
class _DispatchOutcome:
    """One worker's pure result — no `LoopState` access (Pending Decision #8).

    `tick()` folds every outcome into `state` single-threaded, after the pool
    that produced them has fully drained."""

    result: TickResult
    made_progress: bool
    error_signature: str | None
    cost: float


# ---------------------------------------------------------------------------
# Prompt construction (Decision #10)
# ---------------------------------------------------------------------------

_SCOPE_PREAMBLE = (
    "Scope this change strictly to the issue's stated acceptance criteria. "
    "Do not touch files outside that scope."
)


def build_issue_prompt(issue: Issue, *, diagnosis: str | None = None) -> str:
    """Title, body, an optional prior-failure diagnosis (T-L3.5), then the
    scope preamble — in that order. ``diagnosis`` sits between the issue's
    own text and the preamble so the preamble's scope constraint still
    applies last and is not diluted or overridden by anything above it
    (Security Considerations, TRD-v3 §14 Phase L3)."""
    parts = [issue.title, issue.body]
    if diagnosis is not None:
        parts.append(f"Prior attempt failed. Diagnosis: {diagnosis}\nAddress this specifically.")
    parts.append(_SCOPE_PREAMBLE)
    return "\n\n".join(parts)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "issue"


def _engine_for_issue(issue: Issue) -> str | None:
    # Router v1 seam: an explicit engine:* label always wins today. TRD-v3
    # §14 Phase L3 names a stretch goal — when no label is present, consult
    # plumb run stats for the engine/workflow that scores best for this
    # issue's task class and prefer it over cfg.default_backend — but this
    # is NOT implemented (Pending Decision #4: no defined "task class"
    # taxonomy exists yet). See docs/1_product_and_research/BACKLOG.md.
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


def run_one_shot(
    issue: Issue,
    cfg: Config,
    *,
    repo_root: Path,
    parent_run_id: str | None = None,
    diagnosis: str | None = None,
) -> tuple[PrRef, str, float]:
    """Dispatch one quick-lane run and deliver its PR.

    ``parent_run_id``/``diagnosis`` are additive, keyword-only (T-L3.5): when
    omitted, behavior is byte-identical to pre-L3. When ``parent_run_id`` is
    set, this call is a self-heal retry — the plumb child-run handoff
    (``reopen_run``) happens right after ``pipeline.start()`` and before
    ``run_to_completion()`` so every span the retry produces lands under the
    child run_id from the start, mirroring ``sync_prior_prs()``'s own
    reopen-then-write ordering. ``ctx.run_id`` (used for local bookkeeping —
    tasks.md, worktree naming) is unaffected by the handoff and stays the
    id this call's own ``pipeline.start()`` opened; the plumb-side active run
    changes underneath it, which is fine because every write after the
    handoff goes through ``pipeline.plumb``, not through ``ctx.run_id``.
    """
    engine = _engine_for_issue(issue)
    prompt_context = build_issue_prompt(issue, diagnosis=diagnosis)
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
    if parent_run_id is not None:
        pipeline.plumb.reopen_run(parent_run_id)
    try:
        result: RunResult = pipeline.run_to_completion(ctx)
    except AbortedError as exc:
        raise AbortedRunError(
            f"loop_dev run for issue #{issue.number} aborted: {exc}", run_id=ctx.run_id
        ) from exc

    if result.status != "success":
        raise AbortedRunError(
            f"loop_dev run {result.ctx.run_id} ended with status={result.status}",
            worktree_path=result.ctx.worktree_path,
            run_id=result.ctx.run_id,
        )

    if result.ctx.worktree_path is None:
        raise AbortedRunError(
            f"loop_dev run {result.ctx.run_id} succeeded but produced no worktree_path",
            run_id=result.ctx.run_id,
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

    # Pre-PR judge gate (T-L3.4, TRD-v3 §13 #10) — scored after the branch is
    # confirmed ahead of main (so the diff is non-empty by construction) and
    # before delivery. Fails OPEN when the judge is unconfigured (Pending
    # Decision #5): an operator who hasn't set PLUMB_JUDGE_PROVIDER gets L2's
    # un-gated behavior, not a stalled loop.
    diff_text = _read_worktree_diff(result.ctx.worktree_path)
    judge_span_id = pipeline.plumb.record_span(
        run_id=result.ctx.run_id,
        kind="llm",
        name="task_completion_gate",
        status="success",
        latency_ms=0.0,
        error_type=None,
        # Phase L4 (T-L4.7, Pending Decision #6): an explicit engine string,
        # not inferred from the model name — build_weekly_report's per-engine
        # cost/token split reads this rather than guessing from
        # orchestrator_model, which would misclassify the moment a custom
        # codex model name collides with a Claude-style one.
        attributes={"engine": engine or cfg.default_backend},
    )
    try:
        gate: judge_gate.JudgeGateResult | None = judge_gate.score_diff(
            diff_text=diff_text,
            run_id=result.ctx.run_id,
            span_id=judge_span_id,
            model=cfg.model,
        )
    except judge_gate.JudgeUnavailableError as exc:
        _logger.warning("judge gate unavailable, failing open (delivering anyway): %s", exc)
        gate = None

    if gate is not None and not gate.passed:
        # Worktree is deliberately NOT cleaned up here — self_heal needs the
        # diff on disk to build its diagnosis; cleanup happens after it's
        # done with it. run_one_shot has never cleaned up on any other
        # failure path either (orphan sweep handles it), so this adds no new
        # gap.
        raise JudgeGateFailedError(
            gate, worktree_path=result.ctx.worktree_path, run_id=result.ctx.run_id
        )

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


def _read_worktree_diff(worktree_path: Path, *, base_branch: str = "main") -> str:
    """Return ``git diff <base_branch>`` output for the judge gate (T-L3.4).

    Called after ``_assert_branch_has_commits`` has already confirmed the
    branch is ahead of ``base_branch``, so a non-empty diff is expected —
    but not re-asserted here; an empty result is passed to the judge as-is
    (Pending Decision #7 — not special-cased in this phase).
    """
    result = subprocess.run(
        ["git", "diff", base_branch],
        cwd=worktree_path,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git diff failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


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
    issue: Issue,
    cfg: Config,
    *,
    repo_root: Path,
    parent_run_id: str | None = None,
    diagnosis: str | None = None,
) -> tuple[PrRef, str, float]:
    """Planned lane, first-pass-only (Decision #2): produce the TRS triad via
    dev-docs-be, open a plan-only PR, stop. No code_gen dispatch this tick.

    Ordering mirrors the quick lane (Pipeline creates the worktree *before*
    the isolated stage runs): create the worktree, run dev-docs-be inside it,
    commit the triad, then deliver. Running dev-docs-be against ``repo_root``
    and creating the worktree afterwards would write the triad into the main
    working tree and push a branch with zero commits ahead of main.

    ``parent_run_id``/``diagnosis`` are additive, keyword-only (T-L3.6): this
    lane owns its ``PlumbIO`` directly (it bypasses ``Pipeline`` entirely),
    so the child-run handoff is a straight ``reopen_run`` instead of
    ``open_run`` — no ``pipeline.plumb`` indirection needed here.
    """
    prompt_context = build_issue_prompt(issue, diagnosis=diagnosis)

    plumb = PlumbIO(real=True)
    if parent_run_id is not None:
        run_id = plumb.reopen_run(parent_run_id)
    else:
        run_id = plumb.open_run(task=prompt_context)
    ctx_slug = _slugify(issue.title)
    repo_head_before = _head_sha(repo_root)

    engine = _engine_for_issue(issue) or cfg.default_backend
    try:
        backend = make_backend(engine)
    except UnknownBackendError as exc:
        plumb.close_run(run_id=run_id, status="failure")
        raise AbortedRunError(f"planned-lane dispatch failed: {exc}", run_id=run_id) from exc

    worktree = WorktreeManager(repo_root)
    try:
        wt_path = worktree.create(slug=ctx_slug, run_id=run_id)
    except WorktreeError:
        plumb.close_run(run_id=run_id, status="failure")
        raise

    argv = backend.build_argv(
        # Resolved, not hardcoded. `/dev-docs-be` is not a real slash command
        # — plugin_resolver maps the bare tool name to
        # `DEV-ESSENTIALS:dev-docs-be`, and the quick lane has always gone
        # through that mapping. This lane hardcoded the unresolved name, so
        # the agent received an unknown command, wrote no triad, and still
        # exited 0 (T-L2.13, 2026-07-27).
        prompt=build_prompt(
            resolve("dev-docs-be", overrides=cfg.plugin_commands),
            f"Detail this GitHub issue into a TRS triad under "
            f"dev/active/{ctx_slug}/. Issue:\n\n{prompt_context}",
            f"Working directory: {wt_path}",
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
        raise AbortedRunError(
            f"planned-lane dev-docs-be dispatch failed: {output_text}", run_id=run_id
        )

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
                # "handoff" not "deliver": plumb's SpanKind enum has no
                # deliver member, and this span records the outcome of the
                # handoff to a human reviewer. Passing an invalid kind raised
                # ValueError inside plumb and took down the whole tick.
                kind="handoff",
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


def tick(cfg: Config, state: LoopState, *, targets: Sequence[RepoTarget]) -> BatchTickResult:
    """Claim and dispatch up to ``cfg.loop.concurrency`` ready issues across
    ``targets`` (Phase L4). Claiming/triage is sequential (fast gh/plumb
    calls); dispatch itself runs in a bounded thread pool, and every
    ``LoopState`` mutation happens single-threaded, after the pool drains
    (Pending Decision #8) — see ``_dispatch_one``.
    """
    _reset_daily_counters_if_new_day(state)

    sync_results: list[queue_gh.PrStatus] = []
    for target in targets:
        try:
            sync_results += sync_prior_prs(target.github, state)
        except GhCliError as exc:
            _logger.warning("sync failed for repo=%s: %s", target.github, exc)
            continue

    made_progress_from_sync = len(sync_results) > 0

    if breaker_open(state, cfg.loop):
        state.last_tick_at = _now_iso()
        state.persist()
        return BatchTickResult(
            results=[
                TickResult(
                    action="breaker_open",
                    issue_number=None,
                    lane=None,
                    pr_ref=None,
                    detail=f"breaker open until {state.breaker_open_until}",
                )
            ]
        )

    if budget_exhausted(state, cfg.loop):
        state.last_tick_at = _now_iso()
        state.persist()
        return BatchTickResult(
            results=[
                TickResult(
                    action="budget_exhausted",
                    issue_number=None,
                    lane=None,
                    pr_ref=None,
                    detail="daily budget exhausted",
                )
            ]
        )

    batch = _pull_ready_batch(targets, cfg.loop, limit=cfg.loop.concurrency)
    if not batch:
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
        state.persist()
        return BatchTickResult(
            results=[
                TickResult(
                    action="idle",
                    issue_number=None,
                    lane=None,
                    pr_ref=None,
                    detail="no ready issue",
                )
            ]
        )

    try:
        assignee = current_gh_user()
    except GhCliError as exc:
        record_tick_outcome(
            state, cfg.loop, made_progress=False, error_signature=_error_signature(exc)
        )
        state.last_tick_at = _now_iso()
        state.persist()
        return BatchTickResult(
            results=[
                TickResult(
                    action="failed",
                    issue_number=batch[0][1].number,
                    lane=None,
                    pr_ref=None,
                    detail=f"could not resolve gh identity: {exc}",
                )
            ]
        )

    # Claim + triage sequentially (fast, individually-attributable gh/plumb
    # calls). A lost claim-race (T-L4.4) drops that one issue from dispatch
    # without failing the tick or relabeling it — another claimant already
    # owns it.
    dispatchable: list[tuple[RepoTarget, Issue, TriageResult, str]] = []
    for target, issue in batch:
        plumb = PlumbIO(real=True)
        run_id_for_triage = plumb.open_run(task=issue.title)
        triage_result = _triage_issue(issue, plumb=plumb, run_id=run_id_for_triage)
        plumb.close_run(run_id=run_id_for_triage, status="success")

        queue_gh.claim(issue, assignee=assignee)
        if not _claim_confirmed(issue, assignee):
            _logger.info(
                "lost claim race for issue #%s (repo=%s); skipping this tick",
                issue.number,
                target.github,
            )
            continue
        dispatchable.append((target, issue, triage_result, run_id_for_triage))

    if not dispatchable:
        state.last_tick_at = _now_iso()
        state.persist()
        return BatchTickResult(
            results=[
                TickResult(
                    action="idle",
                    issue_number=None,
                    lane=None,
                    pr_ref=None,
                    detail="all candidates lost the claim race",
                )
            ]
        )

    with ThreadPoolExecutor(max_workers=cfg.loop.concurrency) as pool:
        futures = [
            pool.submit(
                _dispatch_one,
                target,
                issue,
                cfg,
                triage_result=triage_result,
                run_id_for_triage=run_id_for_triage,
            )
            for target, issue, triage_result, run_id_for_triage in dispatchable
        ]
        outcomes = [future.result() for future in as_completed(futures)]

    # Single-threaded from here on — the only place tick() mutates `state`.
    for outcome in outcomes:
        record_tick_outcome(
            state,
            cfg.loop,
            made_progress=outcome.made_progress,
            error_signature=outcome.error_signature,
        )
        if outcome.made_progress:
            state.runs_today += 1
            state.dollars_today += outcome.cost
    state.last_tick_at = _now_iso()
    state.persist()
    return BatchTickResult(results=[outcome.result for outcome in outcomes])


def _dispatch_one(
    target: RepoTarget,
    issue: Issue,
    cfg: Config,
    *,
    triage_result: TriageResult,
    run_id_for_triage: str,
) -> _DispatchOutcome:
    """Dispatch one already-claimed issue to completion.

    Pure with respect to ``LoopState`` (Pending Decision #8, T-L4.5): takes no
    ``state`` parameter and never touches one — every real side effect here
    (dispatch, comment, relabel) is external (gh/plumb), not an in-memory
    mutation ``tick()`` needs to serialize. Safe to run inside a thread-pool
    worker; ``tick()`` folds the returned outcome into ``state`` afterwards,
    single-threaded.
    """
    repo_root = target.local_path
    try:
        if triage_result.lane == "quick":
            pr_ref, run_id, cost = run_one_shot(issue, cfg, repo_root=repo_root)
        else:
            pr_ref, run_id, cost = run_planned_first_pass(issue, cfg, repo_root=repo_root)

        queue_gh.comment(issue, body=_format_run_summary(run_id, pr_ref))
        return _DispatchOutcome(
            result=TickResult(
                action="dispatched",
                issue_number=issue.number,
                lane=triage_result.lane,
                pr_ref=pr_ref,
                detail="ok",
            ),
            made_progress=True,
            error_signature=None,
            cost=cost,
        )

    except AbortedRunError as exc:
        # Diagnosis-injected single retry (T-L3.7, TRD-v3 §13 #9) — replaces
        # L2's bare "leave atlas:working for manual triage" for this specific
        # exception type. DeliveryError/GhCliError/WorktreeError (below) are
        # infrastructure failures, not agent-diagnosable ones, and keep L2's
        # original handling unchanged.
        from atlas import self_heal  # local import: self_heal imports loop

        heal = self_heal.handle_failure(
            issue,
            exc,
            cfg,
            repo_root=repo_root,
            original_run_id=exc.run_id or run_id_for_triage,
            diff_text=_read_diff_for_diagnosis(exc),
            lane=triage_result.lane,
        )

        if heal.outcome == "retried_success":
            assert heal.pr_ref is not None and heal.run_id is not None
            # Exact same shape as a first-try success (operator-visible
            # parity) — comment + made_progress=True, no relabel (the issue
            # stays atlas:working until sync_prior_prs sees the PR merge,
            # same as any other successful dispatch).
            queue_gh.comment(issue, body=_format_run_summary(heal.run_id, heal.pr_ref))
            return _DispatchOutcome(
                result=TickResult(
                    action="dispatched",
                    issue_number=issue.number,
                    lane=triage_result.lane,
                    pr_ref=heal.pr_ref,
                    detail="ok (retried)",
                ),
                made_progress=True,
                error_signature=None,
                cost=heal.cost,
            )

        queue_gh.relabel(issue, state="blocked")
        queue_gh.comment(issue, body=_format_blocked_comment(exc, heal))
        return _DispatchOutcome(
            result=TickResult(
                action="failed",
                issue_number=issue.number,
                lane=triage_result.lane,
                pr_ref=None,
                detail=heal.detail,
            ),
            made_progress=False,
            error_signature=_error_signature(exc),
            cost=0.0,
        )

    except (DeliveryError, GhCliError, WorktreeError) as exc:
        queue_gh.comment(
            issue,
            body=f"loop_dev run failed: {exc}. Left atlas:working for manual triage.",
        )
        return _DispatchOutcome(
            result=TickResult(
                action="failed",
                issue_number=issue.number,
                lane=triage_result.lane,
                pr_ref=None,
                detail=str(exc),
            ),
            made_progress=False,
            error_signature=_error_signature(exc),
            cost=0.0,
        )


def _claim_confirmed(issue: Issue, assignee: str) -> bool:
    """Re-read `issue`'s assignees right after `queue_gh.claim()` to detect a
    lost claim-race (Phase L4, T-L4.4): two claimants both saw `atlas:ready`
    before either claimed.

    `gh issue edit --add-assignee` is additive, not exclusive, so a race
    leaves *both* callers' logins in the assignees list — checking simple
    membership can't tell them apart. The first assignee is the tie-break:
    whichever claim() call landed first names this caller as assignees[0].
    A transient read failure does not block dispatch (fails open, matching
    every other best-effort gh read in this module).
    """
    try:
        assignees = queue_gh.current_assignees(issue)
    except GhCliError as exc:
        _logger.warning("could not confirm claim for issue #%s: %s", issue.number, exc)
        return True
    return bool(assignees) and assignees[0] == assignee


def _pull_ready_batch(
    targets: Sequence[RepoTarget], loop_cfg: LoopConfig, *, limit: int
) -> list[tuple[RepoTarget, Issue]]:
    """Up to ``limit`` (RepoTarget, Issue) pairs across ``targets``, in target
    order then gh's own oldest-first order within each target (Decision #9,
    extended to multiple targets in Phase L4). Untrusted-author issues
    (checked per-target, Decision #11) are skipped, left atlas:ready, not
    treated as 'no ready issue' (Decision #16)."""
    batch: list[tuple[RepoTarget, Issue]] = []
    for target in targets:
        if len(batch) >= limit:
            break
        issues = queue_gh.list_ready(target.github)
        for issue in issues:
            if len(batch) >= limit:
                break
            if target.trusted_authors and issue.author not in target.trusted_authors:
                _logger.warning(
                    "skipping issue #%s (repo=%s): untrusted author %r",
                    issue.number,
                    target.github,
                    issue.author,
                )
                continue
            batch.append((target, issue))
    return batch


def _triage_issue(issue: Issue, *, plumb: PlumbIO, run_id: str) -> TriageResult:
    return triage(issue, plumb=plumb, run_id=run_id)


def _format_run_summary(run_id: str, pr_ref: PrRef) -> str:
    # No cost line: per-run cost is unavailable until plumb P1-a (TRD-v3
    # §3.6). Add one here when run_one_shot returns a real figure.
    return f"atlas loop dispatched this issue.\n\nplumb run_id: `{run_id}`\nPR: {pr_ref.url}"


def _read_diff_for_diagnosis(exc: AbortedRunError) -> str | None:
    """Best-effort diff read for self_heal's diagnosis (T-L3.7).

    None when the failed run left no worktree behind (a dispatch failure
    before code_gen ran, or the planned lane's own cleanup-on-failure) —
    self_heal falls back to the issue body in that case.
    """
    if exc.worktree_path is None:
        return None
    try:
        return _read_worktree_diff(exc.worktree_path)
    except WorktreeError as read_exc:
        _logger.warning("could not read worktree diff for diagnosis: %s", read_exc)
        return None


def _format_blocked_comment(exc: AbortedRunError, heal: SelfHealResult) -> str:
    """Comment body for atlas:blocked (T-L3.7) — names the failure mode and
    both run_ids where applicable (TRD-v3 §14 Security: "atlas:blocked is a
    fail-safe state, not a fail-silent one")."""
    lines = [f"atlas loop could not deliver this issue: {heal.detail}"]
    if exc.run_id is not None:
        lines.append(f"original plumb run_id: `{exc.run_id}`")
    if heal.run_id is not None:
        lines.append(f"retry plumb run_id: `{heal.run_id}`")
    if heal.classification is not None:
        lines.append(
            f"failure mode: `{heal.classification.mode}` — {heal.classification.rationale}"
        )
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# run_forever() + reconcile_orphans() (T-L2.8)
# ---------------------------------------------------------------------------


def run_forever(cfg: Config, *, targets: Sequence[RepoTarget]) -> None:
    _warn_on_unenforced_budget(cfg.loop, engine=cfg.default_backend)
    for target in targets:
        migrate_legacy_state_if_needed(target.local_path)
    state = LoopState.load_or_init()
    reconcile_orphans(cfg, targets=targets, at_startup=True)

    while True:
        # No outer breaker check: tick() handles the breaker itself (returns
        # action="breaker_open", updates last_tick_at, persists). Short-
        # circuiting here instead would freeze last_tick_at and log nothing,
        # so `atlas loop status` during a cooldown would look like a dead
        # daemon rather than one deliberately waiting.
        try:
            batch: BatchTickResult | None = tick(cfg, state, targets=targets)
        except Exception:
            _logger.exception("tick() raised unexpectedly")
            batch = None
        _log_tick(batch)
        time.sleep(cfg.loop.poll_interval_s)


def _log_tick(batch: BatchTickResult | None) -> None:
    if batch is None:
        _logger.warning("tick() failed with an unhandled exception; see traceback above")
        return
    # A failed dispatch must not scroll past at the same level as an idle one
    # — `detail` is where a dispatch or delivery failure explains itself, and
    # state.last_error_signature is not a durable record of it (a later idle
    # tick used to clear it).
    for result in batch.results:
        _logger.log(
            logging.WARNING if result.action == "failed" else logging.INFO,
            "tick: action=%s issue=%s lane=%s detail=%s",
            result.action,
            result.issue_number,
            result.lane,
            result.detail,
        )


def reconcile_orphans(
    cfg: Config, *, targets: Sequence[RepoTarget], at_startup: bool = False
) -> list[str]:
    """Reclaim issues and worktrees stranded by a crashed run, independently
    per target — a crash affecting one target's worktree does not touch
    another's.

    ``at_startup`` marks the daemon-boot call, where every ``current-run``
    pointer (singleton and keyed) is **by definition stale**: nothing can be
    in flight before the daemon's first tick. Without it, a hard crash
    (``kill -9``) leaves a file naming the dead run's worktree, and
    ``_sweep_orphaned_worktrees`` retains precisely the orphan it exists to
    prune — observed live in T-L2.13's crash drill on 2026-07-27, where the
    issue was correctly relabeled back to ``atlas:ready`` but its worktree
    survived every restart.
    """
    from atlas.state import StateStore

    reconciled: list[str] = []
    for target in targets:
        working_issues = queue_gh.list_labeled(target.github, "atlas:working")
        try:
            statuses = queue_gh.sync(target.github)
        except GhCliError as exc:
            _logger.warning("reconcile_orphans: sync failed for repo=%s: %s", target.github, exc)
            statuses = []

        linked_numbers = {s.issue.number for s in statuses}
        for issue in working_issues:
            if issue.number not in linked_numbers:
                queue_gh.relabel(issue, state="ready")
                reconciled.append(f"issue #{issue.number}")

        reconciled += _sweep_orphaned_worktrees(target.local_path, ignore_current_run=at_startup)
        if at_startup:
            # Every pointer is stale by construction here; leaving the
            # singleton would also make `atlas resume` offer to resume a run
            # that no longer exists.
            store = StateStore(target.local_path)
            try:
                store.delete_current_run()
                for run_id, _slug, _wt, _span in store.list_current_runs():
                    store.delete_current_run_keyed(run_id)
            except OSError as exc:
                _logger.warning(
                    "could not clear stale current-run state for %s: %s", target.github, exc
                )
    return reconciled


def _sweep_orphaned_worktrees(repo_root: Path, *, ignore_current_run: bool = False) -> list[str]:
    """Delete worktrees left behind by a crashed run, retaining every live one.

    ``ignore_current_run`` disables the retain-check entirely. Callers pass it
    at daemon startup, where no pointer can describe a live run — see
    ``reconcile_orphans``.

    The retain-check must be exact, because this deletes directories that may
    hold uncommitted agent work. Matching on re-slugified issue titles (the
    previous approach) was lossy twice over: _slugify truncates to 40 chars,
    so two similar titles collide and an orphan is retained forever; and a
    live run whose slug didn't match would have its work deleted. The
    run_id-keyed paths recorded in current-run (singleton and, since Phase
    L4, every ``.atlas/runs/<run_id>/current-run``) are unambiguous.
    """
    worktrees_dir = repo_root / ".atlas" / "worktrees"
    if not worktrees_dir.is_dir():
        return []

    from atlas.state import StateStore

    store = StateStore(repo_root)
    live: set[Path] = set()
    if not ignore_current_run:
        try:
            singleton = store.read_current_run_with_worktree()
            keyed = store.list_current_runs()
        except OSError as exc:
            # Fail safe: without readable state we cannot tell which
            # worktree is live, so sweep nothing rather than risk deleting it.
            _logger.warning("could not read current-run state; skipping worktree sweep: %s", exc)
            return []
        if singleton is not None and singleton[2] is not None:
            live.add(singleton[2].resolve())
        for _run_id, _slug, worktree_path, _span in keyed:
            if worktree_path is not None:
                live.add(worktree_path.resolve())

    swept: list[str] = []
    worktree_manager = WorktreeManager(repo_root)
    for worktree_dir in sorted(worktrees_dir.glob("*")):
        if not worktree_dir.is_dir():
            continue
        if worktree_dir.resolve() in live:
            continue
        try:
            worktree_manager.cleanup(worktree_dir)
            swept.append(f"worktree {worktree_dir.name}")
        except WorktreeError as exc:
            _logger.warning("cleanup failed for orphaned worktree %s: %s", worktree_dir, exc)
    return swept


__all__ = [
    "AbortedRunError",
    "BatchTickResult",
    "JudgeGateFailedError",
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
