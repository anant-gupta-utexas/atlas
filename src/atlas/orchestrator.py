"""7-stage atlas pipeline orchestrator."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from atlas.stages import StageSpec

if TYPE_CHECKING:
    from atlas.cli_backend import SpanUsage
    from atlas.plumb_io import PlumbIO
    from atlas.state import StateStore
    from atlas.worktree import WorktreeManager

_ROUTING_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent / "tests" / "fixtures" / "routing_ground_truth.json"
)

# Per-stage subprocess timeouts (seconds), dev-pipeline tier-3 fallback.
# code_gen gets extra headroom. Retained as the final fallback when neither
# .atlas.toml nor the workflow YAML's timeout_s specifies one (§6.7).
_DEFAULT_TIMEOUT_S: dict[str, int] = {
    "research": 600,
    "prd_draft": 600,
    "trd_draft": 600,
    "tds_gen": 600,
    "plan_review": 600,
    "code_gen": 1800,
    "code_review": 600,
}

# Fallback for stage names absent from _DEFAULT_TIMEOUT_S (non-dev workflows).
_GLOBAL_FALLBACK_TIMEOUT_S = 600

_GATE_MAX_REASON_BYTES = 4096
_GATE_MAX_RETRIES = 3
_AWAITING_HOOK_MAX_ATTEMPTS = 3
_DEFAULT_COMMIT_WAIT_TIMEOUT_S = 1800  # 30 minutes


class RoutingDriftError(Exception):
    """Raised when STAGES does not match the routing fixture."""


class NoActiveRunError(Exception):
    """Raised by resume() when .atlas/current-run is absent."""


class AwaitingHookExceededError(Exception):
    """Raised when awaiting_hook repeats more than _AWAITING_HOOK_MAX_ATTEMPTS times."""


def resolve_timeout(stage: StageSpec, timeout_overrides: dict[str, int]) -> int:
    """Resolve a stage's subprocess timeout in priority order (§6.7):

    1. ``.atlas.toml`` per-stage override (highest priority).
    2. The workflow YAML's ``timeout_s`` field on the stage.
    3. The hardcoded dev-pipeline ``_DEFAULT_TIMEOUT_S`` table.
    4. ``_GLOBAL_FALLBACK_TIMEOUT_S``, for stage names absent from tier 3
       (non-dev workflows that omit ``timeout_s``).
    """
    if stage.name in timeout_overrides:
        return timeout_overrides[stage.name]
    if stage.timeout_s is not None:
        return stage.timeout_s
    return _DEFAULT_TIMEOUT_S.get(stage.name, _GLOBAL_FALLBACK_TIMEOUT_S)


def namespaced_metric(workflow_name: str, gate_label: str) -> str:
    """Return the metric name for a gate score, namespaced by workflow.

    The ``dev`` workflow preserves v1-era bare names (e.g. ``gate_research``)
    for backward compatibility; any other workflow gets ``<name>.<gate_label>``.
    """
    if workflow_name == "dev":
        return gate_label
    return f"{workflow_name}.{gate_label}"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunContext:
    run_id: str
    slug: str
    task: str
    repo_root: Path
    worktree_path: Path | None = None


@dataclass(frozen=True)
class GateDecision:
    label: str  # "approved" | "rejected"
    turn_count: int
    reason: str | None


@dataclass(frozen=True)
class StageOutcome:
    stage: StageSpec
    span_id: str
    status: str  # "success" | "failure" | "awaiting_hook" | "rejected"
    output_text: str
    error_type: str | None
    # Per-dispatch token/cost telemetry, when the backend reports any.
    # None for every non-CLI runner (LIB:/SHELL:), for backends that report
    # no usage (agy), and for attended runs, which never request the JSON
    # envelope. Populated by SubprocessStageRunner in loop mode and consumed
    # by Pipeline.step() to write spans.tokens / spans.attributes.
    usage: SpanUsage | None = None


@dataclass(frozen=True)
class RunResult:
    """Terminal outcome of a completed or paused run.

    Wraps RunContext (unchanged) with the status Pipeline already computes
    internally in run_to_completion()'s loop but previously discarded after
    writing it to plumb. Additive: every existing call site that only reads
    `ctx` fields continues to work via `.ctx`.
    """

    ctx: RunContext
    status: str  # "success" | "failure" | "paused"  (paused = awaiting_hook timeout)
    # Summed engine-reported cost for this run, or None when no stage reported
    # one (every attended run, and every all-Codex run — the Codex CLI emits no
    # cost figure at all). None and 0.0 are deliberately distinct: the loop's
    # budget must not treat "unknown" as "free".
    dollar_cost: float | None = None


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class GatePrompter(Protocol):
    def ask(self, *, stage: StageSpec, gate_index: int, output_text: str = "") -> GateDecision: ...


class StageRunner(Protocol):
    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome: ...


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    def __init__(
        self,
        *,
        repo_root: Path,
        state: StateStore,
        plumb: PlumbIO,
        runner: StageRunner,
        prompter: GatePrompter,
        stages: tuple[StageSpec, ...] | None = None,
        workflow_name: str = "dev",
        worktree: WorktreeManager | None = None,
        commit_wait_timeout_s: int = _DEFAULT_COMMIT_WAIT_TIMEOUT_S,
    ) -> None:
        self._repo_root = repo_root
        self._state = state
        self._plumb = plumb
        self._runner = runner
        self._prompter = prompter
        self._worktree = worktree
        self._commit_wait_timeout_s = commit_wait_timeout_s
        self._last_code_gen_span_id: str = ""
        # Run-level usage roll-up across every stage of this run. plumb
        # auto-fills run tokens from buffered spans at close, but NEVER
        # auto-fills dollar_cost (plumb v1.1 FR-USAGE-3a), so atlas has to
        # sum and write it explicitly. `_run_cost_seen` distinguishes "no
        # engine reported cost" (leave NULL) from "cost genuinely was $0".
        self._run_dollar_cost: float = 0.0
        self._run_cost_seen: bool = False
        # Latest RunContext as mutated inside step() (e.g. with worktree_path
        # after stage 5).  run_to_completion() reads this back so caller-owned
        # ctx in same-process flow does not drift from in-flight ctx.
        self._latest_ctx: RunContext | None = None

        if stages is None:
            from atlas.workflow_loader import resolve_workflow

            loaded = resolve_workflow(
                workflow_file=None, workflow_name=workflow_name, repo_root=repo_root
            )
            stages = loaded.stages
        self._stages = stages
        self._stage_by_name: dict[str, StageSpec] = {s.name: s for s in stages}
        self._workflow_name = workflow_name
        self._validate_routing_fixture()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self, *, task: str, slug: str) -> RunContext:
        """
        Create a new run. Writes tasks.md + .atlas/current-run.
        Does NOT execute any stage.
        """
        run_id = self._plumb.open_run(task=task)
        ctx = RunContext(run_id=run_id, slug=slug, task=task, repo_root=self._repo_root)
        self._state.create_tasks_md(ctx, stages=self._stages, workflow_name=self._workflow_name)
        self._state.write_current_run(run_id, slug)
        return ctx

    def resume(self) -> RunContext:
        """
        Resume an in-flight run from .atlas/current-run + tasks.md.

        Hands off to plumb via the documented child-run pattern: ``reopen_run``
        spawns a new run linked to the original by ``parent_run_id``, and the
        returned id becomes the active run id for all subsequent writes.  We
        persist the new active id back to ``.atlas/current-run`` and rewrite
        the tasks.md run_id comment so post-commit hook records also attribute
        to the active child run.
        """
        quad = self._state.read_current_run_with_worktree()
        if quad is None:
            raise NoActiveRunError("No active atlas run in this repo.")
        run_id, slug, worktree_path, code_gen_span_id = quad
        tasks_path = self._repo_root / "dev" / "active" / slug / "tasks.md"
        task = self._state.read_task_text(slug) or _parse_task_from_tasks_md(tasks_path)

        # Reload the workflow this run was started with — tasks.md is the
        # canonical source, not whatever stages this Pipeline was constructed
        # with (a fresh CLI invocation always constructs with the "dev"
        # default before resume() corrects it here).
        workflow_name = self._state.read_workflow_name(slug) or "dev"
        from atlas.workflow_loader import WorkflowNotFoundError, resolve_workflow

        try:
            loaded = resolve_workflow(
                workflow_file=None, workflow_name=workflow_name, repo_root=self._repo_root
            )
        except WorkflowNotFoundError as exc:
            raise WorkflowNotFoundError(f"Cannot resume run {run_id!r}: {exc}") from exc
        self._stages = loaded.stages
        self._stage_by_name = {s.name: s for s in loaded.stages}
        self._workflow_name = loaded.name

        # Validate state under the *original* run_id before any handoff.
        original_ctx = RunContext(
            run_id=run_id,
            slug=slug,
            task=task,
            repo_root=self._repo_root,
            worktree_path=worktree_path,
        )
        self._state.assert_consistent(original_ctx)

        # Rehydrate span_id so flush_pending_scores can attribute hook scores correctly.
        if code_gen_span_id:
            self._last_code_gen_span_id = code_gen_span_id

        # Child-run handoff. In stub mode this returns the same id; in real
        # mode it opens a child run whose parent_run_id links to the original.
        active_run_id = self._plumb.reopen_run(run_id)

        # If the handoff produced a new run id, propagate it into atlas state
        # so all subsequent reads/writes use the active id.
        if active_run_id != run_id:
            # Preserve the async-gate metric (line 5) across the rewrite — a
            # bare rewrite would truncate it, silently reverting non-dev
            # workflows' commit gate to the literal "gate_commit".
            async_gate_metric = self._state.read_async_gate_metric()
            self._state.update_run_id(slug, active_run_id)
            self._state.write_current_run(
                active_run_id,
                slug,
                worktree_path,
                code_gen_span_id=code_gen_span_id,
                async_gate_metric=async_gate_metric,
            )

        return RunContext(
            run_id=active_run_id,
            slug=slug,
            task=task,
            repo_root=self._repo_root,
            worktree_path=worktree_path,
        )

    def step(self, ctx: RunContext) -> StageOutcome | None:
        """
        Execute one stage + its gate.
        Returns StageOutcome, or None if the run is already complete.
        Idempotent if called after run close.
        """
        self._state.assert_consistent(ctx)

        # Drain any gate_commit scores written by the post-commit hook since
        # the last step. The hook can't open a plumb run handle itself, so it
        # buffers to .atlas/pending-scores.jsonl.
        pending = self._repo_root / ".atlas" / "pending-scores.jsonl"
        if pending.exists():
            self._plumb.flush_pending_scores(
                run_id=ctx.run_id,
                pending_path=pending,
                span_id=self._last_code_gen_span_id,
            )

        next_name = self._state.first_unchecked(ctx)
        if next_name is None:
            return None

        stage = self._stage_by_name[next_name]

        # An isolate stage runs inside a git worktree; create it before invoking the runner.
        # The path must outlive this step() call so later stages operate on the
        # generated code, not main. Persist it to .atlas/current-run.
        if stage.isolate and self._worktree is not None and ctx.worktree_path is None:
            worktree_path = self._worktree.create(slug=ctx.slug, run_id=ctx.run_id)
            ctx = RunContext(
                run_id=ctx.run_id,
                slug=ctx.slug,
                task=ctx.task,
                repo_root=ctx.repo_root,
                worktree_path=worktree_path,
            )
            self._state.write_current_run(ctx.run_id, ctx.slug, worktree_path)

        # Cache the (possibly-mutated) ctx so run_to_completion() can use the
        # post-worktree-creation context for stage 6 in same-process flow.
        self._latest_ctx = ctx

        # Measure runner runtime for real latency_ms telemetry.
        t0 = time.monotonic()
        outcome = self._runner.run(ctx=ctx, stage=stage)
        latency_ms = (time.monotonic() - t0) * 1000.0

        span_id = self._plumb.record_span(
            run_id=ctx.run_id,
            kind=stage.span_kind,
            name=stage.name,
            status=outcome.status if outcome.status != "rejected" else "failure",
            latency_ms=latency_ms,
            error_type=outcome.error_type,
            tokens=outcome.usage.tokens if outcome.usage is not None else None,
            attributes=outcome.usage.attributes if outcome.usage is not None else None,
        )
        self._accumulate_usage(outcome.usage)
        outcome = StageOutcome(
            stage=stage,
            span_id=span_id,
            status=outcome.status,
            output_text=outcome.output_text,
            error_type=outcome.error_type,
            usage=outcome.usage,
        )

        # NOTE: tasks.md checkbox is NOT marked here. We only check the box once
        # the gate decision is finalized (success / awaiting_hook / approved) so
        # that resume after a failure or rejection re-runs the same stage instead
        # of skipping past it.

        if outcome.status == "failure":
            return outcome

        if stage.gate_label is None:
            # No gate; advance directly
            self._state.check_box(ctx, stage.name)
            if stage.index < len(self._stages) - 1:
                next_stage = self._stages[stage.index + 1]
                self._state.update_current_block(
                    ctx,
                    phase=next_stage.name,
                    gate=f"none (entering {next_stage.name})",
                    next_action=f"run stage {next_stage.index} ({next_stage.name})",
                )
            else:
                self._state.update_current_block(
                    ctx,
                    phase=stage.name,
                    gate="none",
                    next_action="run complete",
                )
            return StageOutcome(
                stage=stage,
                span_id=span_id,
                status="success",
                output_text=outcome.output_text,
                error_type=None,
            )

        if stage.gate_is_async:
            # Written by post-commit hook; orchestrator returns awaiting_hook.
            # No score is written here. Remember the span_id so the next
            # step()'s flush can attribute hook scores to it. The stage's
            # *work* succeeded, so check the box; the hook score is a
            # separate, asynchronous concern.
            self._state.check_box(ctx, stage.name)
            self._last_code_gen_span_id = span_id
            self._state.write_current_run(
                ctx.run_id,
                ctx.slug,
                ctx.worktree_path,
                code_gen_span_id=span_id,
                async_gate_metric=namespaced_metric(self._workflow_name, stage.gate_label),
            )
            return StageOutcome(
                stage=stage,
                span_id=span_id,
                status="awaiting_hook",
                output_text=outcome.output_text,
                error_type=None,
            )

        assert stage.gate_index is not None
        decision = self._prompter.ask(
            stage=stage, gate_index=stage.gate_index, output_text=outcome.output_text
        )
        self._plumb.record_user_signal(
            run_id=ctx.run_id,
            span_id=span_id,
            metric=namespaced_metric(self._workflow_name, stage.gate_label),
            decision=decision,
        )

        if decision.label == "rejected":
            self._plumb.write_example(
                run_id=ctx.run_id,
                span_id=span_id,
                inputs=ctx.task,
                expected=None,
            )
            return StageOutcome(
                stage=stage,
                span_id=span_id,
                status="rejected",
                output_text=outcome.output_text,
                error_type=None,
            )

        # Approved — check the box and advance current block
        self._state.check_box(ctx, stage.name)
        if stage.index < len(self._stages) - 1:
            next_stage = self._stages[stage.index + 1]
            self._state.update_current_block(
                ctx,
                phase=next_stage.name,
                gate=stage.gate_label,
                next_action=f"run stage {next_stage.index} ({next_stage.name})",
            )
        else:
            self._state.update_current_block(
                ctx,
                phase=stage.name,
                gate=stage.gate_label,
                next_action="run complete",
            )

        return StageOutcome(
            stage=stage,
            span_id=span_id,
            status="success",
            output_text=outcome.output_text,
            error_type=None,
        )

    def run_to_completion(self, ctx: RunContext) -> RunResult:
        """
        Loop: step() until all 7 stages done OR a gate rejects OR a stage fails.

        On ``awaiting_hook`` (code_gen gate): block until pending-scores.jsonl
        contains a record for this run, then continue.  On timeout, return a
        ``RunResult(status="paused")`` so the user can ``atlas resume`` later.
        Raises ``AwaitingHookExceededError`` if awaiting_hook repeats more than
        ``_AWAITING_HOOK_MAX_ATTEMPTS`` times (indicates a loop in the plugin).

        Reads ``self._latest_ctx`` after each step so updates made inside
        step() (notably worktree_path after stage 5) propagate to subsequent
        stages in the same process.
        """
        awaiting_attempts = 0
        while True:
            outcome = self.step(ctx)
            # Pick up any mutated ctx (e.g. worktree_path) that step() set.
            if self._latest_ctx is not None:
                ctx = self._latest_ctx
            if outcome is None:
                self._flush_run_usage(run_id=ctx.run_id)
                self._plumb.close_run(run_id=ctx.run_id, status="success")
                self._state.delete_current_run()
                return RunResult(ctx=ctx, status="success", dollar_cost=self.run_dollar_cost)
            if outcome.status in ("failure", "rejected"):
                self._flush_run_usage(run_id=ctx.run_id)
                self._plumb.close_run(run_id=ctx.run_id, status="failure")
                self._state.delete_current_run()
                # A failed run still spent money — the budget must see it.
                return RunResult(ctx=ctx, status="failure", dollar_cost=self.run_dollar_cost)
            if outcome.status == "awaiting_hook":
                awaiting_attempts += 1
                if awaiting_attempts > _AWAITING_HOOK_MAX_ATTEMPTS:
                    raise AwaitingHookExceededError(
                        f"awaiting_hook repeated {awaiting_attempts} times; "
                        "possible plugin loop or missing commit. Aborting."
                    )
                if not self._wait_for_commit_score(
                    run_id=ctx.run_id,
                    timeout_s=self._commit_wait_timeout_s,
                ):
                    # Timed out waiting for the commit; leave the run open for
                    # resume. Deliberately no _flush_run_usage here: the run is
                    # still open and more stages may spend, and set_usage is
                    # last-call-wins, so the resumed process writes the total.
                    return RunResult(ctx=ctx, status="paused", dollar_cost=self.run_dollar_cost)
            # success: continue to next stage

    @property
    def run_dollar_cost(self) -> float | None:
        """Summed engine-reported cost so far, or None if nothing reported any."""
        return self._run_dollar_cost if self._run_cost_seen else None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _accumulate_usage(self, usage: SpanUsage | None) -> None:
        if usage is None or usage.dollar_cost is None:
            return
        self._run_dollar_cost += usage.dollar_cost
        self._run_cost_seen = True

    def _flush_run_usage(self, *, run_id: str) -> None:
        """Write the run-level cost roll-up to plumb before the run closes.

        Tokens are left to plumb, which auto-fills run-level `tokens_in`/
        `tokens_out` from the buffered spans at close time (v1.1 FR-USAGE-3).
        `dollar_cost` is never auto-filled, so it must be written here or it
        stays NULL forever.
        """
        if not self._run_cost_seen:
            return
        self._plumb.set_usage(run_id=run_id, dollar_cost=self._run_dollar_cost)

    def _wait_for_commit_score(
        self,
        *,
        run_id: str,
        timeout_s: int,
        poll_interval_s: float = 2.0,
    ) -> bool:
        """Block until pending-scores.jsonl contains a record for run_id, or timeout.

        Returns True if a matching record arrived, False on timeout.
        """
        pending = self._repo_root / ".atlas" / "pending-scores.jsonl"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if pending.exists():
                for line in pending.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            if json.loads(line).get("run_id") == run_id:
                                return True
                        except json.JSONDecodeError:
                            continue
            time.sleep(poll_interval_s)
        return False

    def _validate_routing_fixture(self) -> None:
        # routing_ground_truth.json only describes the dev pipeline.
        if self._workflow_name != "dev":
            return
        if not _ROUTING_FIXTURE_PATH.exists():
            raise RoutingDriftError(f"Routing fixture not found: {_ROUTING_FIXTURE_PATH}")
        rows = json.loads(_ROUTING_FIXTURE_PATH.read_text())
        if len(rows) != len(self._stages):
            raise RoutingDriftError(
                f"Fixture has {len(rows)} rows; workflow has {len(self._stages)} stages"
            )
        for spec, row in zip(self._stages, rows, strict=True):
            if (
                spec.tool != row["expected_tool"]
                or spec.span_kind != row["expected_span_kind"]
                or spec.name != row["stage_name"]
            ):
                raise RoutingDriftError(f"Stage {spec.index} drifted from fixture: {spec} vs {row}")


def _parse_task_from_tasks_md(path: Path) -> str:
    """Extract the original task description from tasks.md filename comment."""
    content = path.read_text()
    for line in content.splitlines():
        if line.startswith("# tasks —"):
            return line[len("# tasks —") :].strip()
    return path.parent.name


# ---------------------------------------------------------------------------
# SubprocessStageRunner (T4.1 + T4.3)
# ---------------------------------------------------------------------------


class SubprocessStageRunner:
    """
    Thin subprocess dispatcher — delegates argv construction and result parsing
    to a ``CliBackend`` strategy (ClaudeCodeBackend by default).

    All subprocess calls are list-form (no ``shell=True``).  Plugin names are
    validated against the allow-list in ``plugin_resolver`` before any
    subprocess is spawned (T4.3).
    """

    def __init__(
        self,
        *,
        timeout_overrides: dict[str, int] | None = None,
        command_overrides: dict[str, str] | None = None,
        model: str = "haiku",
        default_backend: str = "claude",
        loaded_workflow: object = None,  # LoadedWorkflow | None; typed as object to avoid cycle
        max_turns: int | None = None,
        loop_mode: bool = False,
    ) -> None:
        self._timeout_overrides = timeout_overrides or {}
        self._command_overrides = command_overrides or {}
        self._model = model
        self._default_backend = default_backend
        self._workflow = loaded_workflow
        # Per-run turn cap, passed through to the backend as --max-turns.
        # None (the default for `atlas run`) leaves the backend's own default
        # in place; the loop daemon sets it from cfg.loop.max_turns so an
        # unattended run can't spin indefinitely.
        self._max_turns = max_turns
        # Unattended dispatch: request the JSON envelope (the only way any
        # token/cost telemetry exists at all) and apply TRD-v3 §3.6's headless
        # permission profile. False for `atlas run`, which keeps attended argv
        # byte-identical to pre-L0 — see
        # test_dev_pipeline_unaffected_by_phase_l0.
        self._loop_mode = loop_mode

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        from atlas.cli_backend import (
            UnknownBackendError,
            UsageReporting,
            make_backend,
            resolve_backend,
        )
        from atlas.plugin_resolver import build_prompt, resolve  # local import to avoid cycles

        # T4.3 — allow-list check before any subprocess call
        plugin_cmd = resolve(stage.tool, overrides=self._command_overrides)

        timeout_s = resolve_timeout(stage, self._timeout_overrides)

        # Plugin slash-commands are workspace-scoped; run claude from the atlas
        # install root so local plugins resolve. Traverse up from __file__ to
        # find the repo root (the directory containing .git), skipping any
        # .claude/worktrees/<branch> intermediate directory in case this module
        # is loaded from a git worktree.
        atlas_root = _find_atlas_root()
        target_dir = ctx.worktree_path if ctx.worktree_path is not None else ctx.repo_root

        # tasks.md is the canonical state file the plugin should read for prior
        # stage outputs and the current phase/gate.
        tasks_md = ctx.repo_root / "dev" / "active" / ctx.slug / "tasks.md"
        context_hint = f"Context file: {tasks_md}\nWorking directory: {target_dir}"

        prompt = build_prompt(plugin_cmd, ctx.task, context_hint)

        # Resolve backend per TRD-v2 §3.4's 4-tier order.
        backend_name = resolve_backend(
            stage=stage,
            workflow=self._workflow,  # type: ignore[arg-type]
            config_default=self._default_backend,
        )
        try:
            backend = make_backend(backend_name)
        except UnknownBackendError as exc:
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=str(exc),
                error_type="unknown_backend",
            )

        preflight = backend.preflight()
        if preflight is not None:
            msg, error_type = preflight
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=msg,
                error_type=error_type,
            )

        add_dirs = [ctx.repo_root]
        if ctx.worktree_path is not None:
            add_dirs.append(ctx.worktree_path)

        extra_flags: dict[str, str] = {}
        if self._max_turns is not None:
            extra_flags["max_turns"] = str(self._max_turns)
        if self._loop_mode:
            # TRD-v3 §3.6: JSON telemetry + acceptEdits, never
            # --dangerously-skip-permissions. The --allowedTools allowlist is
            # deliberately NOT passed here — the TRD stores it in the target
            # repo's checked-in .claude/settings.json, which the CLI reads on
            # its own, so duplicating it into argv would create a second place
            # to keep in sync.
            extra_flags["telemetry"] = "json"
            extra_flags["permission_mode"] = "acceptEdits"

        argv = backend.build_argv(
            prompt=prompt,
            model=self._model,
            add_dirs=add_dirs,
            timeout_s=timeout_s,
            extra_flags=extra_flags,
        )

        try:
            result = subprocess.run(
                argv,
                cwd=str(atlas_root),
                capture_output=True,
                check=False,
                timeout=timeout_s,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text="",
                error_type="plugin_timeout",
            )

        status, output_text, error_type = backend.parse_result(
            result.stdout, result.stderr, result.returncode
        )
        # Usage is only present when the backend was asked for a machine-readable
        # envelope (loop mode) AND implements UsageReporting — agy does not.
        usage: SpanUsage | None = None
        if isinstance(backend, UsageReporting):
            usage = backend.span_usage(result.stdout)
        return StageOutcome(
            stage=stage,
            span_id="",
            status=status,
            output_text=output_text,
            error_type=error_type,
            usage=usage,
        )


# ---------------------------------------------------------------------------
# ClickPrompter (T4.2)
# ---------------------------------------------------------------------------


class AbortedError(Exception):
    """Raised by ClickPrompter when the user quits or gives too many bad inputs."""


class ClickPrompter:
    """
    Interactive gate prompter using ``input()`` (swappable for Typer/Click).

    Re-asks up to ``_GATE_MAX_RETRIES`` times on unparseable input, then
    aborts the run.  ``q`` / ``quit`` aborts immediately.  The reason is
    length-clamped to ``_GATE_MAX_REASON_BYTES`` bytes.
    """

    def ask(self, *, stage: StageSpec, gate_index: int, output_text: str = "") -> GateDecision:
        if output_text:
            print(f"\n{output_text}")

        prompt = (
            f"\nGate {gate_index} — {stage.name}\n"
            "Output reviewed. [a]pprove / [r]eject reason / q to quit: "
        )

        for attempt in range(_GATE_MAX_RETRIES):
            try:
                raw = input(prompt).strip()
            except EOFError:
                raw = "q"

            if raw.lower() in ("q", "quit"):
                raise AbortedError("User quit at gate.")

            if raw.lower() in ("a", "approve"):
                return GateDecision(label="approved", turn_count=attempt + 1, reason=None)

            if raw.lower().startswith("r"):
                reason_raw = raw[1:].strip() if len(raw) > 1 else ""
                if not reason_raw:
                    try:
                        reason_raw = input("  Reason: ").strip()
                    except EOFError:
                        reason_raw = ""
                reason = _clamp_reason(reason_raw)
                return GateDecision(label="rejected", turn_count=attempt + 1, reason=reason)

            print(f"  Unrecognised input {raw!r}. Expected a, r <reason>, or q.")

        raise AbortedError(
            f"Gate {gate_index}: {_GATE_MAX_RETRIES} unparseable inputs — aborting run."
        )


def _find_atlas_root() -> Path:
    """
    Find the main atlas repo root from the location of this module file.

    Handles the case where the module is loaded from a git worktree
    (.claude/worktrees/<branch>/src/atlas/orchestrator.py) by walking up
    until we find a real .git *directory* (not a worktree .git file).

    Raises RuntimeError if no git checkout is found; wheel installs are not
    supported in v1.
    """
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        git = parent / ".git"
        if git.is_dir():
            return parent
    raise RuntimeError(
        f"atlas must be installed in a git checkout; ran from {candidate}. "
        "Wheel installs (outside a git repo) are not supported in v1."
    )


def _clamp_reason(reason: str) -> str:
    """Truncate reason to _GATE_MAX_REASON_BYTES bytes; append a note if truncated."""
    encoded = reason.encode()
    if len(encoded) <= _GATE_MAX_REASON_BYTES:
        return reason
    truncated = encoded[:_GATE_MAX_REASON_BYTES].decode(errors="ignore")
    return truncated + " … [truncated]"


class AutoPrompter:
    """Non-interactive prompter that auto-approves all gates (for testing)."""

    def ask(self, *, stage: StageSpec, gate_index: int, output_text: str = "") -> GateDecision:
        print(f"\nGate {gate_index} — {stage.name} [AUTO-APPROVED]")
        return GateDecision(label="approved", turn_count=1, reason=None)
