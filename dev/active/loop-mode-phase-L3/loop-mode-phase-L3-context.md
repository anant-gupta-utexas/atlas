# Context — Loop Mode, Phase L3 TRS

Reference notes for anyone picking up this work cold.

## Status at TRS authoring time (2026-07-25)

> ⚠️ **Snapshot, not current state.** The `plugin_resolver` blocker described
> below was fixed later the same day (commit `48ee363`) — see the update at the
> end of this section before acting on anything here.

Per `STATUS.md`: v2.2 shipped; Loop Mode Phases L0, L1, and L2 are all
**code-complete**. L2 (the loop daemon itself) passed code review (2 Critical + 4
Important findings, all fixed in the same pass). **L2's own manual exit check,
T-L2.13, is currently blocked**: `atlas loop run`/`start` raises
`RoutingDriftError` because `plugin_resolver.resolve()` doesn't special-case
`RAW:`-prefixed tool strings despite its own docstring's claim that it does —
`loop_dev.yaml`'s `plan` and `code_gen` stages are both `RAW:`-prefixed. This is
a **live, unresolved bug**, not a documentation gap, and it sits directly in
Phase L3's critical path: L3's diagnosis-injected retry re-dispatches through the
exact same `run_one_shot()` → `make_pipeline()` → `loop_dev.yaml` path.

**What this means for L3, concretely:** every unit/integration test in this TRS's
task list can be written and pass in CI (they mock `subprocess`/`gh`/the
pipeline, same as L2's own tests do), but **no manual/live proof of Phase L3's
exit criteria is possible until T-L3.1 resolves the `plugin_resolver` gap.**
T-L3.1 is therefore the first task in this TRS's execution order specifically to
unblock T-L3.10 (and, as a side effect, L2's own still-open T-L2.13).

> **Update 2026-07-25 (after authoring): the blocker above is FIXED — commit
> `48ee363`.** It was resolved during the L2 session that unblocked T-L2.13,
> because the same one-line gap blocked both phases and there was no reason to
> hold the fix for L3 to begin. Pending Decision #1 resolved as Option A;
> T-L3.1 is closed; **T-L3.10 no longer depends on it** (only on
> `PLUMB_JUDGE_PROVIDER` being configured, plus T-L3.7/T-L3.8). `resolve()` now
> returns `RAW:` strings verbatim, and `loop_dev.yaml`'s `verify` stage uses the
> bare `verify` tool string rather than the literal `"/verify"` that
> `build_prompt` would have rendered as `//verify`. The paragraphs above are
> preserved as the authoring-time snapshot; read them as history, not current
> state. L2's T-L2.13 is likewise unblocked and now needs only a human operator
> — a runbook lives at the bottom of
> [`loop-mode-phase-L2-tasks.md`](../../archive/loop-mode-phase-L2/loop-mode-phase-L2-tasks.md).

**The user's framing for this request** — "previous phases have been
implemented but manual testing remains across phases" — is accurate and is
reflected throughout this TRS: see the plan's "Manual testing carried over from
L0/L1/L2" subsection under Overview & Scope, and the tasks file's "Carried-forward
open manual checks" section.

## Key files

### Source-of-truth docs (read first, in order)

- [`docs/2_architecture/TRD-v3.md`](../../../docs/2_architecture/TRD-v3.md) — the
  phase contract this TRS details. §13 items 9–10 (the binding exit criteria),
  §14 Phase L3 (engineering scope summary), §3 design-note cross-reference,
  Appendix A (seam inventory — `orchestrator.py` unchanged).
- [`docs/1_product_and_research/loop-mode-design.md`](../../../docs/1_product_and_research/loop-mode-design.md) —
  §5 Phase L3 section is the original source for the four failure modes
  (`flaky`/`wrong_approach`/`missing_context`/`infeasible`) and the "judge gate
  before the PR... threshold (default 0.7)" language this TRS's design
  implements literally.
- [`STATUS.md`](../../../STATUS.md) — **read the full Phase L2 entry.** The
  `plugin_resolver` blocker (now fixed — see the update above), the
  cost-extraction (`extract_cost`) known
  limitation, and the exact shipped module list (`loop.py`, `loop_budget.py`,
  `pipeline_factory.py`, `triage.py`, `queue_gh.py`) this TRS builds on top of
  are all documented there, not re-derived here.
- [`dev/archive/loop-mode-phase-L2/`](../../archive/loop-mode-phase-L2/) — the L2 TRS triad.
  This TRS follows its task-numbering convention (`T-L3.N`), its
  plan/decisions/tasks-detail file split (adopted here for the same 800-line-cap
  reason), and its "what this TRS does NOT cover" discipline.
- **Sibling `plumb` repo source** (path-installed dependency, confirmed present
  at `/Users/anant/PersonalProjects/plumb` at authoring time) — this TRS's judge
  integration design was verified directly against:
  - `plumb/_cli_judge.py` — the batch `plumb judge run` CLI (confirms it is
    NOT what a synchronous pre-PR gate should call — see Pending Decision #3).
  - `plumb/adapters/__init__.py:12` — `get_judge_adapter(settings, *,
    metric_name) -> JudgeAdapter`, the library API this TRS's design uses
    instead.
  - `plumb/core/ports.py:145` — the `JudgeAdapter` Protocol,
    `.score(*, metric_name, prompt, content, model, timeout_s=60.0) ->
    JudgeResult`.
  - `plumb/core/entities.py:222` — `JudgeResult` dataclass shape
    (`value_numeric`/`value_label` XOR, `rationale`, `tokens_in`/`tokens_out`,
    `latency_ms`).
  - `plumb/adapters/_judge_common.py` — confirms plumb's own adapters already
    retry transient errors internally (up to 3 attempts), so atlas's
    `judge_gate.py` should NOT add a second retry layer on top.
  - `plumb/_prompt_loader.py:73` — prompt files load from
    `$PLUMB_DATA_DIR/judge_prompts/` by convention; exact provisioning
    responsibility for a *new* metric (`failure_mode`) is flagged as Pending
    Decision #8, unresolved by this TRS.

### TRS itself (this directory)

- [`loop-mode-phase-L3-plan.md`](./loop-mode-phase-L3-plan.md) — design (Phase
  Summary through Performance Considerations), Phase Deliverables, a short
  pointer to Pending Decisions and to the tasks-detail file.
- [`loop-mode-phase-L3-decisions.md`](./loop-mode-phase-L3-decisions.md) — all 8
  Pending Decisions & Clarifications with full rationale, split into its own
  file per the L2 precedent.
- [`loop-mode-phase-L3-tasks-detail.md`](./loop-mode-phase-L3-tasks-detail.md) —
  the full flat task list (T-L3.1–T-L3.11) with acceptance criteria, files,
  dependencies, testing requirements. Also split out for file-size reasons.
- [`loop-mode-phase-L3-tasks.md`](./loop-mode-phase-L3-tasks.md) — checkbox
  progress tracking, including the carried-forward L0–L2 manual-check list.

### Code targets

**New:**
- `src/atlas/judge_gate.py` — `score_diff()` (pre-PR gate) + `classify_failure()`
  (failure-mode classification), both via `plumb.adapters.get_judge_adapter`
  (T-L3.2, T-L3.3). The sole point of contact with plumb's judge Python API.
- `src/atlas/self_heal.py` — `handle_failure()`, the diagnosis-injected
  single-retry state machine (T-L3.6). Orchestrates `write_example` →
  `classify_failure` → retry-or-block.
- `tests/fixtures/judge_responses/*.json` — captured/synthetic judge response
  fixtures (mirrors `tests/fixtures/gh_json/`'s existing convention).
- `tests/unit/test_judge_gate.py`, `test_self_heal.py` — new test files.
- `judge_prompts/failure_mode.md` (or equivalent — location TBD, Pending
  Decision #8) — new judge prompt file for the classification metric.

**Modified:**
- `src/atlas/loop.py` — `run_one_shot()` gains the judge-gate call (T-L3.4) and
  `parent_run_id`/`diagnosis` keyword params (T-L3.5); `tick()`'s dispatch
  failure branch is rewired to call `self_heal.handle_failure` instead of
  leaving the issue `atlas:working` with no forward progress (T-L3.7); new
  `JudgeGateFailedError` exception class.
- ~~`src/atlas/plugin_resolver.py` — possible `RAW:` special-case fix~~ —
  **done 2026-07-25 (Option A, commit `48ee363`)**; no L3 change needed. The
  `.atlas.toml [plugin_commands]` workaround (Option B) was not taken and is
  not needed. `docs/3_guides/yaml_workflow_engine.md` documents the result.
- `docs/1_product_and_research/BACKLOG.md` — Router v1 entry (T-L3.9).
- `tests/unit/test_loop.py`, `tests/integration/test_loop_e2e.py` — new cases
  for the judge gate and retry paths.
- `STATUS.md` — phase completion entry (T-L3.11).

**Unchanged (verify, don't touch):**
- `src/atlas/orchestrator.py` (`Pipeline`) — per Appendix A's standing rule. The
  retry is a **new** `Pipeline` run (child run via `reopen_run`), never a resume
  of the failed one.
- `src/atlas/queue_gh.py` — `relabel()`'s `Literal["done","rejected","blocked",
  "ready"]` signature already accepts `"blocked"`; L3 is simply its first
  caller. No signature or implementation change needed.
- `src/atlas/triage.py`, `src/atlas/pipeline_factory.py`, `src/atlas/deliverer.py`
  — reused as-is; L3 does not change lane routing, pipeline construction, or PR
  delivery mechanics, only what happens between a failed run and the next
  dispatch decision.
- `src/atlas/plumb_io.py` — `write_example()` and `reopen_run()` are already
  shipped and exactly match what this TRS's design needs; no new `PlumbIO`
  method required (extends L2 Decision #8's "no new PlumbIO method" precedent
  one phase further).

If implementation finds any "unchanged" file genuinely needs editing beyond
what's listed here, that's a signal the design has drifted from this TRS —
pause and reconcile before proceeding (same standing instruction Appendix A
gives for `Pipeline`, generalized).

## Decisions made (during this TRS's authoring)

Full text in
[`loop-mode-phase-L3-decisions.md`](./loop-mode-phase-L3-decisions.md). One-line
index reproduced in the plan file's Pending Decisions section — not duplicated
a third time here.

## Verified plumb surface used by this TRS (read 2026-07-25, against sibling repo source)

- **`plumb.adapters.get_judge_adapter(settings, *, metric_name) -> JudgeAdapter`**
  — confirmed present and importable; lazy-imports the actual LLM SDK so
  importing it doesn't pull in `anthropic`/`openai` eagerly (NFR-Perf-6 comment
  in `plumb/adapters/__init__.py:1`).
- **`JudgeAdapter.score(*, metric_name, prompt, content, model, timeout_s=60.0)
  -> JudgeResult`** — Protocol, `@runtime_checkable`.
- **`JudgeResult`** — frozen dataclass, `value_numeric`/`value_label` are XOR
  (exactly one set, enforced in `__post_init__` — raises `ValidationError`
  otherwise), plus `rationale`, `tokens_in`, `tokens_out`, `latency_ms`,
  `scorer_version`.
- **`plumb judge run` (the CLI, `plumb/_cli_judge.py`)** — confirmed to be a
  **batch pass over already-persisted, unscored runs** (`storage.
  list_runs_unscored_for_metric(...)`), NOT a synchronous single-diff scorer.
  This TRS's design deliberately does NOT call this CLI — see Pending Decision
  #3 for the full reasoning, since TRD-v3 §6 names it and this is a real,
  material divergence from the TRD's literal phrasing (though not from its
  intent).
- **`PlumbIO.write_example`** (`src/atlas/plumb_io.py:268`) — already shipped,
  writes directly through `plumb._storage_writer.write_example` (the same
  interim pattern plumb itself uses pending `RunHandle.add_example` in a future
  plumb version). No change needed for L3.
- **`PlumbIO.reopen_run`** (`src/atlas/plumb_io.py:80`) — already shipped,
  child-run handoff via `parent_run_id`. Already proven in production code by
  `sync_prior_prs()` (L2). L3's retry dispatch is its second production
  consumer.

## Integration points (new in L3)

| Direction | Surface | Failure mode | Test coverage |
| --- | --- | --- | --- |
| `loop.run_one_shot()` → `judge_gate.score_diff()` | In-process call, pre-delivery | `JudgeUnavailableError` → fail-open (deliver anyway); `passed=False` → `JudgeGateFailedError`, no delivery | Unit (T-L3.4) |
| `self_heal.handle_failure()` → `judge_gate.classify_failure()` | In-process call | `JudgeUnavailableError` → `not_retryable` (fail-to-safe) | Unit (T-L3.6) |
| `self_heal.handle_failure()` → `plumb_io.write_example()` | In-process call | Already best-effort/non-raising inside `PlumbIO` | Unit (T-L3.6) |
| `self_heal.handle_failure()` → `loop.run_one_shot(parent_run_id=..., diagnosis=...)` | In-process call, single retry | A second `AbortedRunError` → `retried_failed`, no further recursion | Unit + Integration (T-L3.6, T-L3.8) |
| `loop.tick()` → `queue_gh.relabel(issue, state="blocked")` | Typed call, first-ever use of `"blocked"` | N/A (relabel already handles `GhCliError` per L2) | Integration (T-L3.7) |
| `judge_gate.py` → `plumb.adapters.get_judge_adapter` | In-process import, sibling repo | Missing `PLUMB_JUDGE_PROVIDER` → `ValueError` inside plumb, caught and re-raised as `JudgeUnavailableError` | Unit (T-L3.2, T-L3.3) |

## Implementation notes (added 2026-07-26, after T-L3.2–T-L3.9 landed)

> Read this before touching `judge_gate.py`/`self_heal.py`/`loop.py` again —
> it's where the design docs above and the actual shipped code diverge.

**Status: code-complete.** T-L3.2 through T-L3.9 are implemented, unit- and
integration-tested, and clean (`pytest` 520 passed/1 xfailed, `ruff check`,
`mypy --strict src` all clean, coverage `judge_gate.py` 86%/`self_heal.py`
100%/`loop.py` 90%, total 95%). T-L3.10 (manual smoke) and T-L3.11 (STATUS.md
close-out) remain — both need a human operator session with a real repo and
a configured `PLUMB_JUDGE_PROVIDER`.

**Three things this TRS's design docs got wrong, found only by reading
plumb's actual source rather than trusting the prose summary:**

1. **`plumb/adapters/_judge_common.py::parse_reply` only accepts a verdict of
   `"pass"`, `"fail"`, or a bare number — never an arbitrary label.**
   `classify_failure`'s four-way mode (`flaky`/`wrong_approach`/
   `missing_context`/`infeasible`) can't ride `JudgeResult.value_label`
   directly, as the design's pseudocode assumed. Fix: `judge_prompts/
   failure_mode.md` always asks for `verdict: "fail"` and encodes the real
   mode as a leading token in `rationale` (`"<mode>: <explanation>"`),
   parsed back out by `judge_gate._parse_failure_mode`.
2. **Neither `Pipeline` nor the `LastOutcomeRunner` recorder
   `make_pipeline()` returns expose the `PlumbIO` instance** the design's
   pseudocode calls `reopen_run()` on (`plumb = recorder's PlumbIO
   instance`). Fixed with the one sanctioned exception to "Pipeline
   unchanged": a single read-only `Pipeline.plumb` property
   (`orchestrator.py`) returning the existing private `self._plumb` — no
   behavior change, confirmed with the user before making it.
3. **`PlumbIO(real=True)`'s default `task_id=""` breaks `write_example`
   in real mode** — plumb's `Example` entity requires a non-empty
   `task_id`, and that validation happens *outside* `write_example`'s own
   try/except, so it wasn't a graceful degradation, it was an uncaught
   crash. `self_heal.py` fixes this locally by constructing
   `PlumbIO(real=True, task_id=f"issue-{issue.number}")`. A parallel bug
   existed in `judge_gate._write_score` (`Score(...)` construction was
   also outside its own try/except) — fixed there directly since it's new
   L3 code, not a pre-existing gap. **`plumb_io.py`'s own `write_example`
   still has the `task_id=""` exposure for any *other* future caller that
   constructs `PlumbIO(real=True)` without a task_id — not fixed here,
   since `plumb_io.py` is explicitly on the "unchanged, reused as-is" list
   above; flag it if it bites a future phase.**

**One task-list gap, not a design error:** `run_planned_first_pass` needed
`parent_run_id`/`diagnosis` params too (for the planned-lane retry
`self_heal.handle_failure` dispatches through), but no numbered L3 task
named it explicitly — T-L3.5's title only says `run_one_shot`. Added as part
of T-L3.6's work since `self_heal.py` couldn't be built without it. Simpler
than the quick lane's version: the planned lane owns its own `PlumbIO`
directly (bypasses `Pipeline` entirely), so it's a straight `reopen_run`
instead of `open_run`, no `pipeline.plumb` indirection needed.

**A real bug an integration test caught before it could land:** the first
draft of T-L3.8's retry-cap test patched `atlas.judge_gate.classify_failure`
and `atlas.loop.run_one_shot`, which passed in isolation but failed when run
inside the full suite. Root cause: `self_heal.py` does `from atlas.judge_gate
import classify_failure` / `from atlas.loop import run_one_shot` — plain name
imports bind their own reference in `self_heal`'s namespace at first import,
which patching the *origin* module's attribute doesn't reach once any other
test has already imported `atlas.self_heal` (several do, at module level).
Fix: patch `atlas.self_heal.classify_failure` / `atlas.self_heal.run_one_shot`
directly — the same rule `test_self_heal.py`'s own tests already followed
correctly. Worth remembering for any future test that reaches through
`self_heal.py` into its re-exported names.

## Where this TRS's task list maps to TRD-v3 §14 Phase L3 scope bullets

| TRD-v3 §14 Phase L3 bullet | This TRS's task |
| --- | --- |
| "Pre-PR judge gate: `plumb judge` (haiku) over the diff → task-completion score; threshold (default 0.7) gates delivery" | T-L3.2, T-L3.4 |
| "Diagnosis-injected retry: `write_example`(origin_run_id) → judge classifies failure mode... → one child-run retry (`reopen_run` w/ `parent_run_id`) with diagnosis injected → else `atlas:blocked`" | T-L3.3, T-L3.5, T-L3.6, T-L3.7, T-L3.8 |
| "Router v1 (stretch): prefer the engine/workflow that scores better in plumb for the task class" | T-L3.9 (seam only, not implemented — see Pending Decision #4) |
| *(implicit — L2's own blocker must clear for L3 to be provable live)* | T-L3.1 |
| *(implicit — every phase closes with a STATUS.md update)* | T-L3.11 |
