"""7-stage atlas pipeline orchestrator."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from atlas.stages import STAGE_BY_NAME, STAGES, GateLabel, StageName, StageSpec

if TYPE_CHECKING:
    from atlas.plumb_io import PlumbIO
    from atlas.state import StateStore
    from atlas.worktree import WorktreeManager

_ROUTING_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent / "tests" / "fixtures" / "routing_ground_truth.json"
)

# Per-stage subprocess timeouts (seconds). code_gen gets extra headroom.
_DEFAULT_TIMEOUT_S: dict[str, int] = {
    "research": 600,
    "prd_draft": 600,
    "trd_draft": 600,
    "tds_gen": 600,
    "plan_review": 600,
    "code_gen": 1800,
    "code_review": 600,
}

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


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class GatePrompter(Protocol):
    def ask(self, *, stage: StageSpec, gate_index: int) -> GateDecision: ...


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
        self._state.create_tasks_md(ctx)
        self._state.write_current_run(run_id, slug)
        return ctx

    def resume(self) -> RunContext:
        """
        Resume an in-flight run from .atlas/current-run + tasks.md.
        Validates state consistency before returning the RunContext.
        """
        quad = self._state.read_current_run_with_worktree()
        if quad is None:
            raise NoActiveRunError("No active atlas run in this repo.")
        run_id, slug, worktree_path, code_gen_span_id = quad
        tasks_path = self._repo_root / "dev" / "active" / slug / "tasks.md"
        task = _parse_task_from_tasks_md(tasks_path)
        ctx = RunContext(
            run_id=run_id,
            slug=slug,
            task=task,
            repo_root=self._repo_root,
            worktree_path=worktree_path,
        )
        self._state.assert_consistent(ctx)
        # Rehydrate span_id so flush_pending_scores can attribute hook scores correctly.
        if code_gen_span_id:
            self._last_code_gen_span_id = code_gen_span_id
        self._plumb.reopen_run(run_id)
        return ctx

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

        stage = STAGE_BY_NAME[next_name]

        # Stage 5 (code_gen) runs inside a git worktree; create it before invoking the runner.
        # The path must outlive this step() call so stage 6 (code_review) operates on the
        # generated code, not main. Persist it to .atlas/current-run.
        if (
            stage.name == StageName.CODE_GEN
            and self._worktree is not None
            and ctx.worktree_path is None
        ):
            worktree_path = self._worktree.create(slug=ctx.slug, run_id=ctx.run_id)
            ctx = RunContext(
                run_id=ctx.run_id,
                slug=ctx.slug,
                task=ctx.task,
                repo_root=ctx.repo_root,
                worktree_path=worktree_path,
            )
            self._state.write_current_run(ctx.run_id, ctx.slug, worktree_path)

        outcome = self._runner.run(ctx=ctx, stage=stage)

        span_id = self._plumb.record_span(
            run_id=ctx.run_id,
            kind=stage.span_kind,
            name=stage.name.value,
            status=outcome.status if outcome.status != "rejected" else "failure",
            latency_ms=0.0,
            error_type=outcome.error_type,
        )
        outcome = StageOutcome(
            stage=stage,
            span_id=span_id,
            status=outcome.status,
            output_text=outcome.output_text,
            error_type=outcome.error_type,
        )

        # NOTE: tasks.md checkbox is NOT marked here. We only check the box once
        # the gate decision is finalized (success / awaiting_hook / approved) so
        # that resume after a failure or rejection re-runs the same stage instead
        # of skipping past it.

        if outcome.status == "failure":
            return outcome

        if stage.gate_label is None:
            # Stage 3 — no gate; advance directly
            self._state.check_box(ctx, stage.name)
            next_stage = STAGES[stage.index + 1]
            self._state.update_current_block(
                ctx,
                phase=next_stage.name,
                gate=f"none (entering {next_stage.name.value})",
                next_action=f"run stage {next_stage.index} ({next_stage.name.value})",
            )
            return StageOutcome(
                stage=stage,
                span_id=span_id,
                status="success",
                output_text=outcome.output_text,
                error_type=None,
            )

        if stage.gate_label == GateLabel.GATE_COMMIT:
            # Gate 4 — written by post-commit hook; orchestrator returns awaiting_hook.
            # No gate_commit user_signal score is written here. Remember the
            # span_id so the next step()'s flush can attribute hook scores to it.
            # The stage's *work* succeeded, so check the box; the hook score is
            # a separate, asynchronous concern.
            self._state.check_box(ctx, stage.name)
            self._last_code_gen_span_id = span_id
            self._state.write_current_run(
                ctx.run_id, ctx.slug, ctx.worktree_path, code_gen_span_id=span_id
            )
            return StageOutcome(
                stage=stage,
                span_id=span_id,
                status="awaiting_hook",
                output_text=outcome.output_text,
                error_type=None,
            )

        assert stage.gate_index is not None
        decision = self._prompter.ask(stage=stage, gate_index=stage.gate_index)
        self._plumb.record_user_signal(
            run_id=ctx.run_id,
            span_id=span_id,
            metric=stage.gate_label.value,
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
        if stage.index < len(STAGES) - 1:
            next_stage = STAGES[stage.index + 1]
            self._state.update_current_block(
                ctx,
                phase=next_stage.name,
                gate=stage.gate_label.value,
                next_action=f"run stage {next_stage.index} ({next_stage.name.value})",
            )
        else:
            self._state.update_current_block(
                ctx,
                phase=stage.name,
                gate=stage.gate_label.value,
                next_action="run complete",
            )

        return StageOutcome(
            stage=stage,
            span_id=span_id,
            status="success",
            output_text=outcome.output_text,
            error_type=None,
        )

    def run_to_completion(self, ctx: RunContext) -> RunContext:
        """
        Loop: step() until all 7 stages done OR a gate rejects OR a stage fails.

        On ``awaiting_hook`` (code_gen gate): block until pending-scores.jsonl
        contains a record for this run, then continue.  On timeout, return the
        ctx so the user can ``atlas resume`` later.  Raises
        ``AwaitingHookExceededError`` if awaiting_hook repeats more than
        ``_AWAITING_HOOK_MAX_ATTEMPTS`` times (indicates a loop in the plugin).
        """
        awaiting_attempts = 0
        while True:
            outcome = self.step(ctx)
            if outcome is None:
                self._plumb.close_run(run_id=ctx.run_id, status="success")
                self._state.delete_current_run()
                return ctx
            if outcome.status in ("failure", "rejected"):
                self._plumb.close_run(run_id=ctx.run_id, status="failure")
                self._state.delete_current_run()
                return ctx
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
                    # Timed out waiting for the commit; leave the run open for resume.
                    return ctx
            # success: continue to next stage

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
        if not _ROUTING_FIXTURE_PATH.exists():
            raise RoutingDriftError(f"Routing fixture not found: {_ROUTING_FIXTURE_PATH}")
        rows = json.loads(_ROUTING_FIXTURE_PATH.read_text())
        if len(rows) != len(STAGES):
            raise RoutingDriftError(f"Fixture has {len(rows)} rows; STAGES has {len(STAGES)}")
        for spec, row in zip(STAGES, rows, strict=True):
            if (
                spec.tool != row["expected_tool"]
                or spec.span_kind != row["expected_span_kind"]
                or spec.name.value != row["stage_name"]
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
    Invokes plugins via ``claude -p "/<plugin> <task>" --no-session-persistence``.

    All subprocess calls are list-form (no ``shell=True``).  Plugin names are
    validated against the allow-list in ``plugin_resolver`` before any
    subprocess is spawned (T4.3).
    """

    def __init__(
        self,
        *,
        timeout_overrides: dict[str, int] | None = None,
        command_overrides: dict[str, str] | None = None,
    ) -> None:
        self._timeout_overrides = timeout_overrides or {}
        self._command_overrides = command_overrides or {}

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        from atlas.plugin_resolver import build_prompt, resolve  # local import to avoid cycles

        # T4.3 — allow-list check before any subprocess call
        plugin_cmd = resolve(stage.tool, overrides=self._command_overrides)

        timeout_s = self._timeout_overrides.get(
            stage.name.value, _DEFAULT_TIMEOUT_S[stage.name.value]
        )

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

        add_dirs = [str(ctx.repo_root)]
        if ctx.worktree_path is not None:
            add_dirs.append(str(ctx.worktree_path))

        try:
            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    prompt,
                    "--no-session-persistence",
                    *[arg for d in add_dirs for arg in ("--add-dir", d)],
                ],
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

        if result.returncode != 0:
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=result.stdout,
                error_type="plugin_nonzero_exit",
            )

        return StageOutcome(
            stage=stage,
            span_id="",
            status="success",
            output_text=result.stdout,
            error_type=None,
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

    def ask(self, *, stage: StageSpec, gate_index: int) -> GateDecision:
        prompt = (
            f"\nGate {gate_index} — {stage.name.value}\n"
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

    def ask(self, *, stage: StageSpec, gate_index: int) -> GateDecision:
        print(f"\nGate {gate_index} — {stage.name.value} [AUTO-APPROVED]")
        return GateDecision(label="approved", turn_count=1, reason=None)
