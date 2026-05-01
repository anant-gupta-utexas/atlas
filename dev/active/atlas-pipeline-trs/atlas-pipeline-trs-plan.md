# TRS — `atlas.pipeline` (7-stage state machine)

**Project:** atlas — v1 local CLI
**Component:** `src/atlas/orchestrator.py` (the state machine) + its direct collaborators
**Status:** Draft, pre-implementation
**Last reviewed:** 2026-04-27
**Grounds on:** [PRD](../../../docs/1_product_and_research/PRD.md), [TRD](../../../docs/2_architecture/TRD.md), [System Design](../../../docs/2_architecture/system_design.md), plumb API reference (provided 2026-04-27).

> Implementation phases + pending decisions live in
> [`atlas-pipeline-trs-phases.md`](./atlas-pipeline-trs-phases.md).
> Per-task progress tracking lives in
> [`atlas-pipeline-trs-tasks.md`](./atlas-pipeline-trs-tasks.md).
> Decisions log + integration points live in
> [`atlas-pipeline-trs-context.md`](./atlas-pipeline-trs-context.md).

---

## 1. Overview & Scope

### In scope
The `atlas.pipeline` module is the orchestrator: a deterministic 7-stage state machine that walks every atlas run from research to reviewed code. This TRS specifies its full implementation contract — data structures, method signatures, gate loop pseudocode, error handling, and tests.

This TRS also covers `atlas.pipeline`'s *direct* collaborators where the contract between them is non-obvious:

- `atlas.state` — `tasks.md` reader/writer (state I/O surface).
- `atlas.plumb_io` — plumb wrapper (span/score writes).
- `atlas.config` — config object passed in at construction time.
- `atlas.worktree` — git worktree create/merge for stage 5.

### Out of scope
- `atlas.cli` (entry point — thin Typer wrapper, separate TRS if needed).
- `atlas.hook` (post-commit hook — runs in a separate process; the contract is "writes one or two `scores` rows referencing the active `run_id`").
- The plumb internals.
- Plugin authoring — atlas only invokes plugins by name.

### Why this scope
The pipeline is the only module with non-trivial state transitions, plumb writes, and gate I/O all wired together. Everything else in atlas is either I/O at the edges (CLI, hook) or a pass-through (config, plumb_io). Pinning down `atlas.pipeline` pins down ~70% of the LoC budget and 100% of the run-shape correctness criteria from the PRD.

---

## 2. Requirements Summary

### Functional (from PRD §"Functional Requirements")
- **FR-1** — Walks 7 stages in fixed order: `research`, `prd_draft`, `trd_draft`, `tds_gen`, `plan_review`, `code_gen`, `code_review`.
- **FR-2** — Pauses at six gates (one after each of stages 0–4 and one at run end). Each gate writes exactly one `scores` row with `scorer="user_signal"` and `value_label ∈ {"approved", "rejected"}`.
- **FR-3** — Each stage emits exactly one span of the correct kind (`plan` for stages 0–3, `verify` for stages 4 + 6, `subagent` for stage 5).
- **FR-4** — Stage 5 (`code_gen`) runs inside a `git worktree`. The pipeline never writes to `main` directly.
- **FR-5** — Pipeline state lives entirely in `dev/active/<slug>/tasks.md` + `.atlas/current-run`. A fresh process can resume from these two files alone.
- **FR-6** — Routing is validated against `tests/fixtures/routing_ground_truth.json` — dispatching to a tool not matching the fixture is a routing failure.
- **FR-7** — On gate rejection, the pipeline writes one `examples` row capturing the rejected artifact (per PRD §"Gate rejection path").

### Non-functional (from TRD §"NFRs")
- **NFR-1** — Pipeline + state + plumb_io combined ≤ ~200 LoC (leaving budget for CLI/hook/config inside the ~300 total).
- **NFR-2** — `atlas status` reads tasks.md in < 500 ms (handled by `atlas.state`, but the pipeline must not introduce unnecessary writes that bloat the file).
- **NFR-3** — All public functions in `atlas.pipeline` carry full type annotations; `mypy src --strict` passes.
- **NFR-4** — Crashed runs leave a well-formed (truncated) span tree; `runs.status` is set to `failure` via plumb's run-context-manager exit.

---

## 3. Detailed Component Design

### 3.1 Module structure

```
src/atlas/
├── __init__.py
├── cli.py               # Typer entrypoint (out of scope here)
├── orchestrator.py      # ◀── this TRS
├── state.py             # tasks.md + .atlas/current-run
├── plumb_io.py          # plumb wrapper
├── worktree.py          # git worktree create/merge
├── config.py            # TOML loader
├── post_commit_hook.py  # hook script (separate process)
└── stages.py            # static stage→tool mapping table
```

Single responsibility per file. `orchestrator.py` is the only file that imports from all the others — that's the seam.

### 3.2 Data structures

All frozen dataclasses (NFR-3 + TRD's "frozen dataclasses already give half the type-coverage value for free").

```python
# src/atlas/stages.py
from dataclasses import dataclass
from enum import Enum

class StageName(str, Enum):
    RESEARCH    = "research"
    PRD_DRAFT   = "prd_draft"
    TRD_DRAFT   = "trd_draft"
    TDS_GEN     = "tds_gen"
    PLAN_REVIEW = "plan_review"
    CODE_GEN    = "code_gen"
    CODE_REVIEW = "code_review"

class GateLabel(str, Enum):
    GATE_RESEARCH       = "gate_research"
    GATE_PRD            = "gate_prd"
    GATE_TRD            = "gate_trd"
    GATE_TDS            = "gate_tds"
    GATE_COMMIT         = "gate_commit"          # written by hook, not orchestrator
    GATE_PHASE_COMPLETE = "gate_phase_complete"

@dataclass(frozen=True)
class StageSpec:
    index: int                       # 0–6
    name: StageName
    span_kind: str                   # "plan" | "verify" | "subagent"
    tool: str                        # plugin command/agent name to invoke
    gate_label: GateLabel | None     # None for stage 3 (its output is reviewed by stage 4)
    gate_index: int | None           # 0–5; None where gate_label is None

STAGES: tuple[StageSpec, ...] = (
    StageSpec(0, StageName.RESEARCH,    "plan",     "consult-experts:research", GateLabel.GATE_RESEARCH,       0),
    StageSpec(1, StageName.PRD_DRAFT,   "plan",     "consult-experts:pm",        GateLabel.GATE_PRD,            1),
    StageSpec(2, StageName.TRD_DRAFT,   "plan",     "consult-experts:tech-lead", GateLabel.GATE_TRD,            2),
    StageSpec(3, StageName.TDS_GEN,     "plan",     "dev-docs-be",               None,                          None),
    StageSpec(4, StageName.PLAN_REVIEW, "verify",   "plan-reviewer",             GateLabel.GATE_TDS,            3),
    StageSpec(5, StageName.CODE_GEN,    "subagent", "code-gen-agent",            GateLabel.GATE_COMMIT,         4),  # written by hook
    StageSpec(6, StageName.CODE_REVIEW, "verify",   "code-review",               GateLabel.GATE_PHASE_COMPLETE, 5),
)
```

> **Note on stages 3 & 4.** Per the PRD, `gate_tds` is attached to the `verify:plan_review` span, *not* to `plan:tds_gen`. So stage 3 has no gate of its own — its output is reviewed by stage 4 and gate 3 fires at the end of stage 4. The fixture and routing test must reflect this.

```python
# src/atlas/orchestrator.py
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass(frozen=True)
class RunContext:
    run_id: str                # 32-char hex (plumb-issued)
    slug: str                  # tasks.md directory name
    task: str                  # original task description
    repo_root: Path            # project root (where .atlas.toml lives)
    worktree_path: Path | None # set on stage 5 entry; None before

@dataclass(frozen=True)
class GateDecision:
    label: str                 # "approved" | "rejected"
    turn_count: int            # number of agent turns used inside this stage
    reason: str | None         # free-form, written to scores.rationale

@dataclass(frozen=True)
class StageOutcome:
    stage: StageSpec
    span_id: str               # plumb-issued
    status: str                # "success" | "failure" | "awaiting_hook" | "rejected"
    output_text: str           # captured stdout (used for examples row on rejection)
    error_type: str | None     # populated on failure
```

### 3.3 Public interface (the orchestrator)

```python
# src/atlas/orchestrator.py

class GatePrompter(Protocol):
    """User-facing gate I/O. Injected so tests can substitute a fake."""
    def ask(self, *, stage: StageSpec, gate_index: int) -> GateDecision: ...

class StageRunner(Protocol):
    """Invokes a single stage's tool and returns the outcome."""
    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome: ...

class Pipeline:
    def __init__(
        self,
        *,
        config: Config,
        state: StateStore,           # atlas.state
        plumb: PlumbIO,              # atlas.plumb_io
        worktree: WorktreeManager,   # atlas.worktree
        runner: StageRunner,         # default: SubprocessStageRunner
        prompter: GatePrompter,      # default: ClickPrompter
    ) -> None: ...

    def start(self, task: str) -> RunContext:
        """
        New run. Inserts plumb run row, creates tasks.md + .atlas/current-run,
        returns the RunContext. Does NOT execute any stage.
        """

    def resume(self) -> RunContext:
        """
        Resume an in-flight run. Reads .atlas/current-run + tasks.md,
        validates state-consistency contract (TRD §Data Requirements),
        returns a RunContext positioned at the first unchecked box.
        """

    def step(self, ctx: RunContext) -> StageOutcome | None:
        """
        Execute one stage + its gate. Returns the StageOutcome, or None if
        the run is already complete. Idempotent if called after run close.
        """

    def run_to_completion(self, ctx: RunContext) -> RunContext:
        """
        Loop: step() until all 7 stages done OR a gate rejects OR a stage fails.
        On rejection, writes the examples row, marks run failure, returns.
        """
```

`Pipeline` is the only stateful class; it holds collaborators by reference but no run state — every call takes a `RunContext`. This makes resume trivial: rebuild `RunContext` from disk, call `step()`.

### 3.4 Collaborator contracts

Just signatures, not full implementations — those modules get their own deeper specs if they grow non-trivial.

```python
# src/atlas/state.py
class StateStore:
    def __init__(self, repo_root: Path) -> None: ...

    def create_tasks_md(self, ctx: RunContext) -> None:
        """Writes the initial tasks.md with the ## current block + 7 unchecked boxes."""

    def write_current_run(self, run_id: str, slug: str) -> None:
        """Writes .atlas/current-run."""

    def read_current_run(self) -> tuple[str, str] | None:
        """Returns (run_id, slug) or None if no active run."""

    def update_current_block(self, ctx: RunContext, *, phase: StageName, gate_index: int | None, next_action: str) -> None: ...

    def check_box(self, ctx: RunContext, stage: StageName) -> None: ...

    def first_unchecked(self, ctx: RunContext) -> StageName | None: ...

    def assert_consistent(self, ctx: RunContext) -> None:
        """Raises StateInconsistencyError if .atlas/current-run.run_id != tasks.md header run_id."""

# src/atlas/plumb_io.py
class PlumbIO:
    def open_run(self, *, task: str, git_sha: str) -> str:           # returns run_id
    def record_span(self, *, run_id: str, kind: str, name: str, status: str, latency_ms: float, error_type: str | None) -> str
    def record_user_signal(self, *, run_id: str, span_id: str, metric: GateLabel, decision: GateDecision) -> None
    def write_example(self, *, run_id: str, span_id: str, inputs: str, expected: str | None) -> None
    def close_run(self, *, run_id: str, status: str) -> None

# src/atlas/worktree.py
class WorktreeManager:
    def create(self, ctx: RunContext) -> Path:
        """git worktree add .atlas/worktrees/<slug>-<short_run_id> <branch>; returns path."""

    def merge_back(self, ctx: RunContext) -> None: ...
    def cleanup(self, ctx: RunContext) -> None: ...
```

---

## 4. API Specifications

The pipeline has **no network API** (per PRD §"Scalability"). The "API" is the CLI surface and three internal Python protocols above.

### CLI ↔ pipeline

| CLI command | Pipeline call | User I/O |
| --- | --- | --- |
| `atlas run "<task>"` | `Pipeline.start(task)` then `Pipeline.run_to_completion(ctx)` | One gate prompt per stage: `Approve / Reject [reason?]` |
| `atlas status` | `state.read_current_run()` + read `tasks.md` `## current` block | Prints the block, exits |
| (no CLI command) | Hook subprocess writes gate 4 score directly via `plumb_io` | — |

### Gate prompt shape (the only user-facing "endpoint")

```
[atlas] Gate 2 — TRD finalized?
        Stage: trd_draft   Span: plan:trd_draft   Turn count: 4
        [a]pprove  [r]eject  (q to quit)
> a
        Reason (optional, single line, blank to skip):
> looks good
[atlas] gate_trd: approved (turn_count=4) → advancing to stage 3
```

Inputs:
- `a` / `approved` / `y` → `GateDecision(label="approved", ...)`
- `r` / `rejected` / `n` → `GateDecision(label="rejected", ...)`
- `q` → SIGINT-equivalent: orchestrator marks run aborted via `r.abort("user_quit")`.

Errors:
- Empty / unparseable input → re-prompt up to 3 times, then abort with `error_type="gate_input_unparseable"`.
- Pipeline never accepts auto-approval flags in v1 (would defeat the gate's purpose).

---

## 5. Database Design

### 5.1 Atlas-owned schema
**None.** Atlas owns flat files (`.atlas.toml`, `.atlas/current-run`, `tasks.md`, `.atlas/runs/<run_id>.log`). All structured data goes through plumb (TRD §"Data Requirements").

### 5.2 Plumb tables touched (read-only listing)

Atlas writes via plumb's Python API (never SQLite directly). Below is what the pipeline writes per run, per the plumb API reference:

| When | API call | Resulting row(s) |
| --- | --- | --- |
| `Pipeline.start()` | `plumb.run(task_id=task, kind="online", git_sha=...)` (entered as ctx mgr) | 1 `runs` row, status starts `pending`, transitions to `success`/`failure`/`aborted` on exit |
| Stage entry | `r.add_span(SpanKind.PLAN/VERIFY/SUBAGENT, name=stage.name, status=..., latency_ms=...)` | 1 `spans` row |
| Stage failure | `r.add_span(..., status=SpanStatus.FAILURE, error_type=...)` | 1 `spans` row with failure status |
| Gate approve/reject | `r.add_score(metric=gate.value, scorer=ScorerKind.USER_SIGNAL, value_label=decision.label, span_id=span_id)` | 1 `scores` row |
| Gate reject (extra) | `Example(...)` written via storage adapter (per plumb ref §"Recording Examples") | 1 `examples` row |
| Run close | (handled by `with` exit) | `runs.status` finalized, `end_ts` set |

> **Plumb interaction note.** `add_span` in plumb buffers the span and *its status is set at add time*, not via a separate close call. The orchestrator must therefore time the stage, decide success/failure, and only then call `add_span`. This inverts the naive "open/close span" pattern. The `PlumbIO` wrapper exposes a single `record_span(...)` call to keep the orchestrator's intent ("record this span with this outcome") readable.

### 5.3 Indexes / migrations
None for v1 — atlas owns no schema, and plumb's schema is plumb's responsibility (TRD §"Data Requirements", §"Resolved decisions" #4).

---

## 6. Algorithm & Logic Design

### 6.1 The main loop (pseudocode)

```
function run_to_completion(ctx):
    while True:
        outcome = step(ctx)
        if outcome is None:           # all 7 stages done
            close_run(ctx, status="success")
            return ctx
        if outcome.status in ("failure", "rejected"):
            close_run(ctx, status="failure")
            return ctx
        if outcome.status == "awaiting_hook":
            # gate 4 is hook-driven; the orchestrator returns to the user.
            # Resume happens on the next `atlas run` invocation.
            return ctx

function step(ctx):
    state.assert_consistent(ctx)
    next_stage_name = state.first_unchecked(ctx)
    if next_stage_name is None:
        return None                   # all done
    stage = STAGES[index_of(next_stage_name)]

    span_id, latency_ms, output_text, status, error_type = invoke_stage(ctx, stage)
    plumb.record_span(kind=stage.span_kind, name=stage.name, status=status,
                      latency_ms=latency_ms, error_type=error_type)
    state.check_box(ctx, stage.name)

    if status == "failure":
        return StageOutcome(stage, span_id, "failure", output_text, error_type)

    # gate handling
    if stage.gate_label is None:
        # stage 3 — no gate; advance to stage 4
        state.update_current_block(ctx, phase=next_stage_after(stage), gate_index=None, next_action=...)
        return StageOutcome(stage, span_id, "success", output_text, None)

    if stage.gate_label == GateLabel.GATE_COMMIT:
        # gate 4 — written by post-commit hook, not the orchestrator
        worktree.create(ctx)
        return StageOutcome(stage, span_id, "awaiting_hook", output_text, None)

    decision = prompter.ask(stage=stage, gate_index=stage.gate_index)
    plumb.record_user_signal(span_id=span_id, metric=stage.gate_label.value, decision=decision)

    if decision.label == "rejected":
        plumb.write_example(run_id=ctx.run_id, span_id=span_id,
                            inputs=ctx.task, expected=output_text)
        return StageOutcome(stage, span_id, "rejected", output_text, None)

    # approved — advance
    state.update_current_block(ctx, phase=next_stage_after(stage), gate_index=stage.gate_index + 1, next_action=...)
    return StageOutcome(stage, span_id, "success", output_text, None)
```

### 6.2 `invoke_stage` (subprocess + timing)

```
function invoke_stage(ctx, stage):
    cwd = ctx.worktree_path if stage.name == CODE_GEN else ctx.repo_root
    # routing assertion: dispatch table must match the fixture
    assert stage.tool == ROUTING_FIXTURE[stage.index].tool

    t0 = monotonic()
    proc = subprocess.run([resolve_plugin_command(stage.tool), ...stage_args(ctx, stage)],
                          cwd=cwd, capture_output=True, check=False, timeout=STAGE_TIMEOUT_S)
    t1 = monotonic()

    if proc.returncode != 0 or t1 - t0 > STAGE_TIMEOUT_S:
        return ("", (t1-t0)*1000, proc.stderr, "failure",
                "plugin_nonzero_exit" if proc.returncode != 0 else "plugin_timeout")

    return (new_span_id, (t1-t0)*1000, proc.stdout, "success", None)
```

`STAGE_TIMEOUT_S` is configurable per-stage via `.atlas.toml` (default 600s for plan stages, 1800s for code_gen). Stage 5's "timeout" is naturally bounded by the user — the orchestrator hands off to the worktree and the hook drives the next transition.

### 6.3 Routing-fixture validation

```
ROUTING_FIXTURE: list of {stage_index, stage_name, expected_tool, expected_span_kind}
                 loaded once from tests/fixtures/routing_ground_truth.json

# Pre-flight check at Pipeline.__init__:
for spec, row in zip(STAGES, ROUTING_FIXTURE, strict=True):
    if (spec.tool, spec.span_kind, spec.name) != (row.expected_tool, row.expected_span_kind, row.stage_name):
        raise RoutingDriftError(f"Stage {spec.index} drifted from fixture: {spec} vs {row}")
```

A drift here is a **release blocker** (TRD §"Success Criteria" #4), so failing fast at pipeline construction is the right cost.

### 6.4 Resume protocol

```
function resume():
    pair = state.read_current_run()
    if pair is None:
        raise NoActiveRunError()
    run_id, slug = pair
    ctx = build_run_context(run_id, slug, repo_root)
    state.assert_consistent(ctx)
    return ctx
```

The orchestrator does not need to track "what stage was I on" in memory — `state.first_unchecked(ctx)` provides that on every `step()` call. Resume is pointer-driven, not memory-driven.

---

## 7. Error Handling & Edge Cases

| Scenario | Detection | Handling | Visible to user as |
| --- | --- | --- | --- |
| Plugin exits non-zero | `proc.returncode != 0` | `record_span(status="failure", error_type="plugin_nonzero_exit")`; halt; mark run `failure` via `with` exit | "Stage X failed: plugin exited 1; stderr: ..." |
| Plugin times out | `subprocess.TimeoutExpired` raised by `proc.run` with `timeout=` | Same as above with `error_type="plugin_timeout"` | "Stage X timed out after Ns" |
| Gate prompt unparseable input | `prompter.ask()` re-asks up to 3 times | `r.abort("gate_input_unparseable")`; run closes with status `aborted` | "Gate input not understood — aborting run." |
| User quits at gate (`q`) | `prompter.ask()` returns sentinel | `r.abort("user_quit")`; close worktree if present | "Run aborted by user." |
| `.atlas/current-run` missing on `resume()` | `read_current_run() is None` | Raise `NoActiveRunError`; CLI surfaces friendly message | "No active atlas run in this repo." |
| `run_id` mismatch between `.atlas/current-run` and `tasks.md` header | `state.assert_consistent()` | Raise `StateInconsistencyError` naming both values; orchestrator never auto-fixes (TRD §Data Requirements) | "State mismatch: .atlas/current-run says R1; tasks.md header says R2. Resolve manually." |
| Gate rejected | `decision.label == "rejected"` | Write `examples` row + close run `failure`; do not auto-restart | "Gate rejected — run closed. Restart with `atlas run` for a fresh attempt." |
| Worktree create fails (path exists, dirty repo) | `worktree.create()` raises `WorktreeError` | Halt before stage 5 span; record span `failure` with `error_type="worktree_create_failed"` | "Could not create worktree: <reason>" |
| plumb write fails internally | plumb logs internally; never raises (per plumb API ref §"Error Handling") | Orchestrator continues; the missing row is detectable downstream as "incomplete tree" | (silent — surfaced when user queries plumb later) |
| Hook never advances state for gate 4 | `awaiting_hook` outcome; `step()` re-entered via `atlas run` resumes and detects the unchecked box already moved by the hook | Idempotent: repeat invocations are safe | (transparent) |

### Retry strategy
None within v1. PRD explicitly defers bounded auto-retry to v1.2. A failed stage means the run closes `failure` and the user re-invokes `atlas run`. A rejected gate means the same.

### Fallback strategy
Hook parser is best-effort (TRD §Risks). If parse fails, log and continue — do not block the run. The user can re-derive scores from plumb queries later.

---

## 8. Dependencies & Interfaces

### Direct module dependencies
- `atlas.state` — file I/O for tasks.md / current-run.
- `atlas.plumb_io` — wraps `plumb.run`, `r.add_span`, `r.add_score`, `Example` writes.
- `atlas.worktree` — `git worktree` subprocess wrapper.
- `atlas.config` — provides per-stage timeouts and tool names (overridable).
- `atlas.stages` — static stage table + routing constants.

### External (Python) dependencies
- `plumb` — direct in-process API. Pinned commit SHA in `pyproject.toml`.
- `typer >= 0.12` — CLI (entry point only; the orchestrator itself is plain Python).
- `tomllib` (stdlib) — config parsing.

### External (system) dependencies
- `git >= 2.5` — worktree.
- `DEV-ESSENTIALS`, `DEV-BE-PYTHON` plugins — invoked by name via `subprocess`. Pinned commit SHAs in `pyproject.toml`.

### Interface stability contract
`Pipeline.start / resume / step / run_to_completion` are the public API of this module. Any change is a major-version bump for atlas. The collaborator protocols (`StateStore`, `PlumbIO`, `WorktreeManager`, `GatePrompter`, `StageRunner`) are internal — refactor freely.

---

## 9. Security Considerations

The pipeline runs locally with the user's shell privileges (TRD §Security). Specific concerns inside the orchestrator:

- **Subprocess argument construction.** All plugin invocations go through `subprocess.run([...], cwd=...)` with a list (never shell=True). Stage args derived from config are validated against an allow-list of known plugin names before invocation.
- **Path containment for stage 5.** `ctx.worktree_path` must be under `ctx.repo_root / ".atlas" / "worktrees"`. Pipeline asserts this on creation; if `worktree.create()` returns a path outside that root, halt with `error_type="worktree_path_escape"`.
- **No secret handling.** LLM API keys live in env vars and are passed to plugins via the inherited environment; the orchestrator never reads, copies, or logs them. `subprocess.run` inherits env by default — verify no debug logging in `invoke_stage` ever stringifies `os.environ`.
- **Gate prompt input.** User input is captured as a string and stored as `scores.rationale` (free-form). No interpretation; no eval. Length-clamp to 4 KB to avoid an absurdly long line breaking tasks.md formatting.
- **tasks.md write atomicity.** Use atomic write (write to `tasks.md.tmp`, rename) so a crash mid-write cannot leave an unparseable file — the resume protocol depends on a parseable header.

---

## 10. Testing Strategy

Coverage target for `atlas.pipeline`: **80%+** (TRD §QA Requirements). Coverage target for `atlas.state`: **90%+** because correctness on it gates the resume protocol.

### Unit tests (`tests/unit/`)

| Test | What it asserts |
| --- | --- |
| `test_routing_fixture_match.py` | `STAGES` and `routing_ground_truth.json` agree row-for-row (TRD §Mandatory tests). **Release blocker** if it fails. |
| `test_pipeline_step_advances_on_approve.py` | `step()` after gate-approve writes one user_signal score, advances `## current`, ticks the box. |
| `test_pipeline_step_writes_example_on_reject.py` | Reject path writes one `examples` row + closes run `failure`. |
| `test_pipeline_step_handles_plugin_nonzero_exit.py` | Non-zero plugin exit closes the span with `failure`, halts run. |
| `test_pipeline_step_handles_plugin_timeout.py` | `subprocess.TimeoutExpired` → span `failure` with `error_type="plugin_timeout"`. |
| `test_pipeline_resume_after_compaction.py` | Fresh `Pipeline` + fresh `RunContext` reads tasks.md, finds first unchecked box, continues. |
| `test_pipeline_state_inconsistency_refuses.py` | Mismatched run_id → `StateInconsistencyError` naming both values. |
| `test_pipeline_idempotent_after_close.py` | `step()` after run close returns `None` and writes nothing. |
| `test_pipeline_gate_4_handed_off_to_hook.py` | Stage 5 returns `awaiting_hook`; orchestrator does not write `gate_commit` itself. |
| `test_subprocess_arg_list_form.py` | Pipeline always uses list-form `subprocess.run`, never `shell=True`. |
| `test_worktree_path_containment.py` | If `worktree.create()` returns a path outside `.atlas/worktrees/`, pipeline halts. |

### Integration tests (`tests/integration/`)

| Test | Spans real subsystems |
| --- | --- |
| `test_pipeline_writes_full_span_tree.py` | Real plumb (in-memory SQLite); stub plugins; one full happy-path run produces 7 spans + 6 scores. |
| `test_main_branch_isolation.py` | Real git repo; stage 5 commits in worktree; assert `git log main` unchanged from start to gate 4. |
| `test_hook_idempotency.py` | Two commits on the same SHA produce no duplicate scores. |

### E2E (`tests/e2e/`)

One test, run manually before tagging v1.0: a real `atlas run` against a throwaway Flask repo. Asserts criteria 1–5 from TRD §Success Criteria.

### Mocking strategy

- Plugins → fake `StageRunner` returning canned `StageOutcome`s. Real plugins only at E2E.
- plumb → real plumb against `:memory:` SQLite (no mocking — plumb is in-process and cheap).
- git → temp directory + real git for integration; subprocess mocks acceptable for unit tests of `worktree.py`.
- Gate prompt → `FakePrompter` returning scripted decisions.

### Test data

- `tests/fixtures/routing_ground_truth.json` — 7-row source of truth.
- `tests/fixtures/sample_tasks.md` — pre-built tasks.md at gates 0/3/5 for resume-protocol tests.
- `tests/fixtures/plugin_outputs/` — canned stdout from each plugin for hook-parser tests.

---

## 11. Performance Considerations

The pipeline itself is human-bounded (NFR-2 carve-out). Performance concerns inside the orchestrator:

- **`Pipeline.__init__` cost.** Loading the routing fixture, opening plumb, validating config — must complete in < 100 ms cold so `atlas status` (which constructs a Pipeline-less reader) and `atlas run` start feel instant. Concrete budget: file I/O only, no plumb DB writes at construction.
- **`step()` overhead.** All atlas-side work (state I/O, plumb writes) must add < 50 ms per stage on top of the plugin's own runtime. Plugin runtime dominates by 3–6 orders of magnitude, so this rarely matters — but it sets the bar for what `atlas.state` can do (no fancy markdown parsing).
- **No caching.** Every `step()` re-reads tasks.md. Caching is a v1.1 concern at best. The 500ms `atlas status` budget already covers a cold read.
- **Logs are append-only at `.atlas/runs/<run_id>.log`.** No rotation in v1; tracked as a v1.1 backlog item (TRD §Deployment).
- **Monitoring.** Per-stage latency is captured as `latency_ms` on the span. Plumb queries derive p50/p95 — no separate metric pipeline.

---

## Appendix — Cross-references

- [PRD §"Functional Requirements"](../../../docs/1_product_and_research/PRD.md#functional-requirements) — FR-1 … FR-7 source.
- [TRD §"Quality Assurance Requirements"](../../../docs/2_architecture/TRD.md#quality-assurance-requirements) — release-blocker tests this TRS implements.
- [SDD §"System Components & Services"](../../../docs/2_architecture/system_design.md#system-components--services) — module diagram this TRS slots into.
- plumb API reference — provided 2026-04-27, used for §3.4, §5.2, §6.1.
