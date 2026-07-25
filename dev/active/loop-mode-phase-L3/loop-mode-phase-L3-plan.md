# TRS — Loop Mode, Phase L3 (Self-healing + routing)

## Phase Summary

- **TRD phase:** Phase L3 — Self-healing + routing (`docs/2_architecture/TRD-v3.md` §14)
- **Delivers:** `v3.2` (TRD-v3 §11)
- **Goal (copied from TRD-v3 §14):** *"Rescue failures with diagnosis rather than
  blind retry; begin score-informed routing."*
- **Dependencies:** L2 (per TRD-v3 §14: "Dependencies: L2"). L2 is **code-complete**
  but its own manual exit check (T-L2.13) is currently **blocked** — see
  [Pending Decisions & Clarifications](./loop-mode-phase-L3-decisions.md) #1 and
  [Overview & Scope](#overview--scope) below. This TRS treats that as a live
  precondition, not background noise.
- **Exit criteria (TRD-v3 §13):**
  - **#9** Diagnosis-injected retry. A verify/judge failure is captured as a plumb
    example, classified, and retried **once** as a child run (`parent_run_id`) with
    the diagnosis injected; exhaustion → `atlas:blocked`.
  - **#10** Pre-PR judge gate. A plumb judge score below threshold blocks delivery.

Full Pending Decisions text lives in
[`loop-mode-phase-L3-decisions.md`](./loop-mode-phase-L3-decisions.md) (split out,
matching L2's own precedent, to keep this file under the repo's 800-line cap). Full
flat task list lives in
[`loop-mode-phase-L3-tasks-detail.md`](./loop-mode-phase-L3-tasks-detail.md) (same
reason). This file covers Phase Summary through Performance Considerations, plus
Phase Deliverables.

---

## Overview & Scope

### What L3 adds, precisely

Two new capabilities sit inside `run_one_shot()`'s existing success path
(`src/atlas/loop.py:113-152`), between `Pipeline.run_to_completion()` returning and
`queue_gh.deliver_pr()` being called:

1. **Pre-PR judge gate (§13 #10).** Before `deliver_pr()` is called, the diff
   produced by `code_gen` is scored by a plumb judge for task-completion. A score
   below a threshold (default `0.7`) **blocks delivery** — the run is treated as a
   failure for retry purposes (see #2), not silently merged anyway.
2. **Diagnosis-injected retry (§13 #9).** Any run that fails — either a
   `verify`-stage failure (`AbortedRunError`, unchanged from L2) or a judge-gate
   failure (new in L3) — is captured as a plumb `example` (`write_example`,
   already shipped in `plumb_io.py`), classified into one of four failure modes by
   a second judge call, and if the mode is retryable, re-dispatched **once** as a
   **child run** (`PlumbIO.reopen_run()`, already shipped) with the diagnosis
   injected into the prompt. A second failure (or a non-retryable classification)
   relabels the issue `atlas:blocked` instead of leaving it silently stuck on
   `atlas:working`.

Router v1 (engine/workflow preference from historical plumb scores) is explicitly
a **TRD-stretch item**, not a §13 exit-criterion — see
[Requirements Summary](#requirements-summary) for how this TRS scopes it out of the
committed task list while leaving a named seam for it.

### What L3 does NOT touch

- `queue_gh.py`, `triage.py`, `pipeline_factory.py`, `deliverer.py` (except the
  planned-lane retry wiring described in Task T-L3.6) — L2's adapters are reused
  as-is.
- The **planned lane's task-by-task implementation loop** (TRD-v3 §3.2: "Subsequent
  loop passes pick up the committed TRS and implement it task by task... yields
  multiple PRs per issue"). That machinery does not exist yet (L2 explicitly scoped
  the planned lane to first-pass-only, Decision #2 in
  `dev/active/loop-mode-phase-L2/loop-mode-phase-L2-decisions.md`). Retrying a
  **planned-lane** failure is therefore narrower in L3 than retrying a **quick-lane**
  failure — see Task T-L3.6 and Pending Decision #2.
- `Pipeline`/`orchestrator.py` — unchanged, per Appendix A's standing rule ("if
  implementation finds `Pipeline` genuinely needs editing, that is a signal the
  design has drifted — pause and reconcile"). The retry is a **new `Pipeline` run**
  (child run via `reopen_run`), not a resume of the failed one.
- `max_dollars_per_day` real enforcement — still blocked on plumb P1-a and
  `extract_cost` (STATUS.md "Known limitation carried forward"), unrelated to L3's
  scope. L3's own new judge-call cost is tracked the same way triage classifier
  cost already is (L2 Decision #17: counts toward `max_dollars_per_day`, not
  `max_runs_per_day` — mechanically wired, not yet enforceable end-to-end for the
  same pre-existing reason).

### Manual testing carried over from L0/L1/L2 (explicit acknowledgment)

Per the user's framing of this request: **L0, L1, and L2 are implemented in code,
but each phase's manual/off-CI exit checks remain open.** This TRS does not
re-scope those into L3 tasks (they belong to their own phases' TRSs), but it
depends on some of them being resolved before L3's own manual checks are
meaningful, and it surfaces the dependency explicitly rather than silently
assuming they're done:

| Open manual check | Phase | Blocks L3 how |
| --- | --- | --- |
| T-L0.8 (first live `atlas run` against real `claude` backend) | L0 | If never run, L3's judge-gate manual smoke (T-L3.10) is the *first* live run in the whole project — a bad place to discover an unrelated L0 gap. |
| T-L0.9 (real `GhPrDeliverer.deliver()` against a scratch repo) | L0 | Same — L3's retry PRs are delivered the same way. |
| T-L1.1 (write-heavy Codex capture — cold/warm-cache token question) | L1 | Only matters if L3 is smoke-tested under `engine:codex`; not required for the `claude`-only exit bar. |
| T-L1.8 (both-engines smoke) | L1 | Same as T-L1.1. |
| **T-L2.13 (zero-touch delivery / planned-lane / crash-recovery smoke)** | L2 | **Blocking.** T-L2.13 is itself blocked on the `plugin_resolver.resolve()` gap (STATUS.md `blocked_on`) — `loop_dev.yaml`'s `RAW:`-prefixed stages raise `RoutingDriftError` under a real `atlas loop run`. L3's retry re-dispatches through the exact same `run_one_shot()` → `loop_dev.yaml` path, so **this gap blocks live retry testing, not just the original run.** See Pending Decision #1. |

L3's own task list includes a task (T-L3.1) that resolves or formally re-scopes the
`plugin_resolver` blocker specifically because L3 cannot be manually proven without
it — everything else in the table above is named as context, not duplicated as an
L3 task.

---

## Requirements Summary

From TRD-v3 §14 Phase L3 engineering scope summary, decomposed:

1. **Pre-PR judge gate**: `plumb judge` (haiku) over the diff → task-completion
   score; threshold (default 0.7) gates delivery.
2. **Diagnosis-injected retry**: `write_example`(origin_run_id) → judge classifies
   failure mode (`flaky` / `wrong_approach` / `missing_context` / `infeasible`) →
   one child-run retry (`reopen_run` w/ `parent_run_id`) with diagnosis injected →
   else `atlas:blocked`.
3. **Router v1 (stretch)**: prefer the engine/workflow that scores better in plumb
   for the task class. **Not in this TRS's committed task list** — see
   [Pending Decisions #4](./loop-mode-phase-L3-decisions.md).

From TRD-v3 §13 (the binding exit bar, reproduced above): items #9 and #10 only.
Router v1 is TRD-labeled "(stretch)" in §14 and is absent from §13's numbered
list — the nomenclature contract in this command ("Task — owned by this command...
detail a phase into tasks") does not license inventing scope the TRD itself marked
optional as a committed, tested deliverable. It is named as a deferred task (T-L3.9,
explicitly out of the Phase Deliverables gate) so the seam is visible without
inflating this phase's committed surface.

---

## Detailed Component Design

### New module: `src/atlas/judge_gate.py`

Houses both judge calls (scoring and classification) as a single, small,
independently-testable unit — kept out of `loop.py` for the same reason L2 split
`triage.py` and `loop_budget.py` out (L2 Decision #4: "Testability + keeps `loop.py`
from growing past readable").

```python
"""Pre-PR judge gate + failure-mode classification (TRD-v3 §14 Phase L3).

Two distinct plumb judge calls, both via the *library* JudgeAdapter Protocol
(plumb.adapters.get_judge_adapter), NOT the `plumb judge run` batch CLI —
see Pending Decision #3 for why the TRD's phrasing needed disambiguating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FailureMode = Literal["flaky", "wrong_approach", "missing_context", "infeasible"]

_RETRYABLE_MODES: frozenset[FailureMode] = frozenset(
    {"flaky", "wrong_approach", "missing_context"}
)


@dataclass(frozen=True)
class JudgeGateResult:
    passed: bool
    value_numeric: float
    rationale: str
    scorer_version: str


@dataclass(frozen=True)
class FailureClassification:
    mode: FailureMode
    rationale: str
    retryable: bool  # mode in _RETRYABLE_MODES


class JudgeUnavailableError(Exception):
    """Raised when PLUMB_JUDGE_PROVIDER is unset or the adapter fails closed.

    Callers (loop.py) must fail OPEN on this — see Pending Decision #5 — not
    treat "judge unavailable" the same as "judge said no".
    """


def score_diff(
    *, diff_text: str, metric: str = "task_completion", model: str, threshold: float
) -> JudgeGateResult:
    """Score a diff for task-completion via plumb's JudgeAdapter.score().

    Raises JudgeUnavailableError if no judge provider is configured.
    """
    ...


def classify_failure(
    *, diff_text: str, failure_context: str, model: str
) -> FailureClassification:
    """Classify a failed run's failure mode via a second, differently-prompted
    judge call (metric="failure_mode", a new judge_prompts file — see T-L3.3).

    Unparseable / low-confidence classifier output defaults to
    mode="wrong_approach", retryable=True — the same "ambiguous defaults to the
    lane needing more human oversight, never silently drops the issue" posture
    triage.py already uses for its own unparseable-response default (L2's
    `_parse_classify_response` defaults to "planned").
    """
    ...
```

### Modified: `src/atlas/loop.py`

```python
def run_one_shot(
    issue: Issue, cfg: Config, *, repo_root: Path, parent_run_id: str | None = None,
    diagnosis: str | None = None,
) -> tuple[PrRef, str, float]:
    """Unchanged signature shape plus two new keyword-only params (T-L3.6):

    parent_run_id — when set, dispatch as a plumb child run via
      PlumbIO.reopen_run(parent_run_id) instead of open_run() (matches how
      sync_prior_prs() already reattaches — L2 Decision #8's pattern, reused
      not reinvented).
    diagnosis — when set, appended to build_issue_prompt()'s output as a
      "Prior attempt failed because: ..." section (T-L3.5).

    New post-success, pre-delivery step: after RunResult.status == "success" and
    worktree_path is confirmed (existing L2 checks, unchanged), call
    judge_gate.score_diff() over the worktree diff before calling deliver_pr().
    A below-threshold score raises JudgeGateFailedError (new, subclasses
    AbortedRunError so existing except-blocks in tick() keep working) instead of
    delivering.
    """


class JudgeGateFailedError(AbortedRunError):
    """Raised when judge_gate.score_diff() returns passed=False.

    Carries the JudgeGateResult so the retry path (T-L3.6) can build the
    diagnosis without re-running the judge.
    """
    def __init__(self, result: JudgeGateResult) -> None: ...
```

`tick()`'s dispatch block (`loop.py:350-458`, specifically the `run_one_shot`/
`run_planned_first_pass` call sites and their existing `except AbortedRunError`
handling) grows a **retry branch**:

```
dispatch (existing L2 shape, quick lane only — see Pending Decision #2):
    try:
        pr_ref, run_id, cost = run_one_shot(issue, cfg, repo_root=repo_root)
    except AbortedRunError as exc:
        # NEW in L3 — was: record_tick_outcome(...); relabel back to atlas:ready
        # (implicitly, via no relabel call — L2 left it atlas:working until a
        # human noticed); now:
        outcome = self_heal.handle_failure(issue, exc, cfg, repo_root=repo_root)
        # outcome is one of:
        #   retried_success(pr_ref, run_id)   -> deliver, comment, relabel same as success
        #   retried_failed                     -> relabel atlas:blocked, comment diagnosis
        #   not_retryable                      -> relabel atlas:blocked, comment diagnosis
```

### New module: `src/atlas/self_heal.py`

The orchestration glue between a failed `run_one_shot()`/`run_planned_first_pass()`
call and the retry-or-block decision. Kept separate from `loop.py` for the same
testability reason as `judge_gate.py` — `loop.py` is already 600 lines (L2's own
STATUS.md notes it was split once already, into `loop_budget.py`, "so the driver
stays readable as L3 adds self-healing" — this was anticipated in the L2 code
review).

```python
"""Diagnosis-injected single-retry (TRD-v3 §14 Phase L3, §13 #9)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from atlas.config import Config
from atlas.deliverer import PrRef
from atlas.judge_gate import FailureClassification, JudgeUnavailableError, classify_failure
from atlas.loop import AbortedRunError, JudgeGateFailedError, run_one_shot
from atlas.plumb_io import PlumbIO
from atlas.queue_gh import Issue

SelfHealOutcome = Literal["retried_success", "retried_failed", "not_retryable"]


@dataclass(frozen=True)
class SelfHealResult:
    outcome: SelfHealOutcome
    pr_ref: PrRef | None
    run_id: str | None
    classification: FailureClassification | None
    detail: str


def handle_failure(
    issue: Issue, exc: AbortedRunError, cfg: Config, *, repo_root: Path,
    original_run_id: str, diff_text: str | None,
) -> SelfHealResult:
    """One retry attempt max — enforced by NOT recursing; the caller (tick())
    only ever calls this once per tick, and a retried run that itself fails
    raises AbortedRunError again, which this function does NOT catch a second
    time (caller sees it as retried_failed, not a further self_heal call).

    Steps (TRD-v3 §14, §3 design note):
      1. write_example(origin_run_id=original_run_id, inputs=diff_text or
         issue body, expected=None) — capture the failure regardless of what
         happens next. A judge-unavailable failure below still gets this write;
         losing the example is worse than losing the retry.
      2. classify_failure(...) — may raise JudgeUnavailableError.
         - On JudgeUnavailableError: fail to not_retryable (Pending Decision #5)
           rather than silently retrying blind.
      3. If not classification.retryable: return not_retryable.
      4. Else: build the diagnosis string, call run_one_shot(..., parent_run_id=
         original_run_id, diagnosis=...) exactly once.
         - Success -> retried_success.
         - AbortedRunError (incl. a second JudgeGateFailedError) -> retried_failed.
           Does NOT recurse into handle_failure() again — that would violate the
           "cap at one retry" contract.
    """
```

### Data Structures (new, summarized)

| Type | Module | Shape |
| --- | --- | --- |
| `JudgeGateResult` | `judge_gate.py` | `passed: bool`, `value_numeric: float`, `rationale: str`, `scorer_version: str` |
| `FailureClassification` | `judge_gate.py` | `mode: FailureMode`, `rationale: str`, `retryable: bool` |
| `FailureMode` | `judge_gate.py` | `Literal["flaky","wrong_approach","missing_context","infeasible"]` |
| `SelfHealResult` | `self_heal.py` | `outcome`, `pr_ref: PrRef \| None`, `run_id: str \| None`, `classification`, `detail: str` |
| `JudgeGateFailedError` | `loop.py` | subclass of `AbortedRunError`, carries `JudgeGateResult` |
| `JudgeUnavailableError` | `judge_gate.py` | plain `Exception` |

---

## API Specifications

No new HTTP/RPC surface — atlas remains local-only (TRD-v3 carries v1's "no network
listener" NFR forward unchanged). "API" here means the **in-process Python
boundaries** this phase adds or crosses:

| Boundary | Direction | Contract |
| --- | --- | --- |
| `loop.py` → `judge_gate.py` | call | `score_diff(diff_text=..., model=..., threshold=...) -> JudgeGateResult`; raises `JudgeUnavailableError` |
| `self_heal.py` → `judge_gate.py` | call | `classify_failure(diff_text=..., failure_context=..., model=...) -> FailureClassification`; raises `JudgeUnavailableError` |
| `judge_gate.py` → `plumb.adapters.get_judge_adapter` | call | `get_judge_adapter(get_settings(), metric_name=...) -> JudgeAdapter`; `.score(metric_name, prompt, content, model, timeout_s=60.0) -> JudgeResult` — **verified against plumb source** (`plumb/adapters/__init__.py:12`, `plumb/core/ports.py:145`), not re-derived from the TRD's `plumb judge run` CLI phrasing. See Pending Decision #3. |
| `self_heal.py` → `plumb_io.PlumbIO.write_example` | call | Already shipped (`plumb_io.py:268`); `inputs=diff_text_or_issue_body`, `expected=None` (no "corrected" half exists at write time — mirrors the gate-rejection pattern from PRD §"Gate rejection path", which also writes `expected` in a later update, not at write time). |
| `self_heal.py` → `loop.run_one_shot(..., parent_run_id=..., diagnosis=...)` | call | New keyword-only params on an existing function — additive, not breaking (mirrors L1's `RunResult` widening precedent, Appendix A's one sanctioned exception pattern). |
| `tick()` → `queue_gh.relabel(issue, state="blocked")` | call | Already a valid `Literal` value in `queue_gh.relabel`'s signature (`Literal["done","rejected","blocked","ready"]`, TRD-v3 §3.1) — **unused by any L0-L2 code path today**; L3 is `"blocked"`'s first caller. |

**Rate limiting:** two new judge calls per failed run (score + classify), capped
at one retry — worst case per issue is 1 original run + 1 judge score + 1 judge
classify + 1 retry run + 1 judge score (if the retry also fails) = bounded, no
unbounded loop. Both judge calls' token cost rolls into `max_dollars_per_day`
tracking the same (currently-inert, pending `extract_cost`) way triage's does.

**Authentication:** inherits plumb's own judge-provider config
(`PLUMB_JUDGE_PROVIDER`, `PLUMB_JUDGE_ADAPTER` env vars, already documented in
`PLUMB_API_REFERENCE.md`) — atlas does not manage judge credentials itself, same
boundary discipline as `write_example`'s existing direct-adapter-import pattern.

---

## Database Design

No new atlas-owned persistent schema. Reuses existing sinks:

- **plumb `examples`** (via `write_example`, already shipped) — one row per
  failed run, `origin_run_id` = the failed run's id.
- **plumb `scores`** (via the judge adapter's own write path inside
  `_cli_judge.py`'s pattern — but L3 calls the adapter directly, not through that
  CLI command, so **L3 must write the `Score` row itself**, mirroring
  `_cli_judge.py:98-108`'s `Score(...)` construction — see T-L3.2's acceptance
  criteria). `scorer=ScorerKind.JUDGE`, `metric_name="task_completion"` for the
  gate score and `metric_name="failure_mode"` for the classification score,
  `span_id` anchored to the `code_gen` span (same anchoring discipline the L2
  code review enforced for `sync_prior_prs()`'s `user_signal` write — no dangling
  `span_id=""`).
- **plumb `runs`** — the retry is a **child run** via `reopen_run(parent_run_id=
  original_run_id)`, exactly the shape `sync_prior_prs()` already uses. No schema
  change; `parent_run_id` lineage already exists in plumb v1.0.1 (TRD-v3 §7 "plumb
  impact" table: "`parent_run_id` child runs — Works as-is").
- **`.atlas/loop-state.json`** (`LoopState`) — no new fields required for the
  minimum retry-once contract (the cap is enforced by control flow, not persisted
  counters — see T-L3.6's acceptance criteria and Pending Decision #6 for why a
  persisted per-issue retry counter is explicitly NOT added in this phase).

**Data Access Patterns:** all writes are single-row inserts via existing
`PlumbIO`/direct-adapter methods — no new query patterns, no new indexes.

**Migration Strategy:** none. TRD-v3 §13 item 14 ("No plumb migration for
v3.0–v3.2") holds for L3 (`v3.2`) — confirmed by this TRS design not adding any
new plumb-side column or table.

---

## Algorithm & Logic Design

### Pre-PR judge gate (pseudocode)

```
function run_one_shot_with_gate(issue, cfg, repo_root, parent_run_id=None, diagnosis=None):
    prompt = build_issue_prompt(issue)
    if diagnosis is not None:
        prompt += f"\n\nPrior attempt failed. Diagnosis: {diagnosis}\nAddress this specifically."

    pipeline, recorder = make_pipeline(..., backend_override=engine)
    ctx = pipeline.start(task=prompt, slug=slugify(issue.title))

    if parent_run_id is not None:
        # child-run handoff BEFORE pipeline runs, so all spans land under the
        # child run_id from the start (matches sync_prior_prs' reopen-then-write
        # ordering, not reopen-after-the-fact)
        plumb = recorder's PlumbIO instance
        child_run_id = plumb.reopen_run(parent_run_id)
        # ctx.run_id is already fixed by pipeline.start(); the child run_id
        # becomes the ACTIVE plumb run_id for subsequent writes (per
        # reopen_run's documented contract) while ctx.run_id stays the
        # orchestrator's own bookkeeping id — same split L2 already lives with
        # for sync_prior_prs (Pipeline itself is UNCHANGED per Appendix A and
        # has no parent_run_id parameter).

    result = pipeline.run_to_completion(ctx)
    if result.status != "success":
        raise AbortedRunError(...)   # existing L2 behavior, unchanged

    diff_text = read_worktree_diff(result.ctx.worktree_path)   # `git diff main`
    gate = judge_gate.score_diff(diff_text=diff_text, model=cfg.model, threshold=0.7)
    write judge Score row (scorer=JUDGE, metric=task_completion)

    if not gate.passed:
        raise JudgeGateFailedError(gate)   # NEW — blocks delivery, worktree left
                                            # for self_heal to build the diagnosis
                                            # from, cleaned up by self_heal's own
                                            # failure path (mirrors _cleanup_quietly)

    pr_ref = queue_gh.deliver_pr(...)      # unchanged
    return pr_ref, result.ctx.run_id, cost
```

### Diagnosis-injected retry (pseudocode)

```
function handle_failure(issue, exc, cfg, repo_root, original_run_id, diff_text):
    write_example(origin_run_id=original_run_id, inputs=diff_text or issue.body, expected=None)

    try:
        classification = judge_gate.classify_failure(
            diff_text=diff_text or "(no diff produced)",
            failure_context=str(exc),
            model=cfg.model,
        )
    except JudgeUnavailableError:
        return SelfHealResult(outcome="not_retryable", ..., detail="judge unavailable, failing to safe")

    write judge Score row (scorer=JUDGE, metric=failure_mode, value_label=classification.mode)

    if not classification.retryable:   # mode == "infeasible"
        return SelfHealResult(outcome="not_retryable", classification=classification, ...)

    diagnosis = f"{classification.mode}: {classification.rationale}"
    try:
        pr_ref, run_id, cost = run_one_shot(
            issue, cfg, repo_root=repo_root,
            parent_run_id=original_run_id, diagnosis=diagnosis,
        )
        return SelfHealResult(outcome="retried_success", pr_ref=pr_ref, run_id=run_id, ...)
    except AbortedRunError:
        # single retry exhausted — do NOT call handle_failure() again
        return SelfHealResult(outcome="retried_failed", classification=classification, ...)
```

### `tick()` integration (delta over L2's existing dispatch block)

```
# inside tick(), quick-lane branch (loop.py:350-458 region)
try:
    pr_ref, run_id, cost = run_one_shot(issue, cfg, repo_root=repo_root)
    ... existing success path: comment, relabel stays atlas:working until
    ... sync_prior_prs() sees the PR merge (unchanged L2 behavior — L3 does
    ... not change what "success" does)
except AbortedRunError as exc:
    diff_text = try_read_last_worktree_diff(exc)   # best-effort; None if no worktree
    heal = self_heal.handle_failure(
        issue, exc, cfg, repo_root=repo_root,
        original_run_id=<the run_id from the failed attempt>, diff_text=diff_text,
    )
    if heal.outcome == "retried_success":
        # deliver comment/relabel exactly as the plain success path does
        ...
    else:
        queue_gh.relabel(issue, state="blocked")
        queue_gh.comment(issue, body=f"Blocked: {heal.detail}\n\n{heal.classification}")
        record_tick_outcome(state, cfg.loop, made_progress=False, error_signature=...)
```

---

## Error Handling & Edge Cases

| Case | Handling |
| --- | --- |
| Judge provider not configured (`PLUMB_JUDGE_PROVIDER` unset) | `JudgeUnavailableError` raised by `judge_gate.py`. **Gate call site fails open** (treats as passed — Pending Decision #5) so a misconfigured judge doesn't silently stall every PR; **classify call site fails to `not_retryable`** (safer default — an unclassified failure should not blind-retry). Both paths log a WARNING naming the missing config, matching `_cli_judge.py`'s own `die(...)` message shape so the operator fix is the same either way. |
| Judge call times out or raises a transient error (`JudgeTransientError` inside plumb's own adapter, per `plumb/adapters/_judge_common.py`) | plumb's adapter already retries up to 3 times internally (confirmed in source, `_judge_common.py:116` docstring); atlas does not add its own retry wrapper — would double the backoff without benefit. A final failure surfaces as whatever exception plumb's adapter raises after exhausting its own retries; `judge_gate.py` catches broadly and re-raises as `JudgeUnavailableError` so `loop.py`/`self_heal.py` only ever handle one exception type from this boundary. |
| Retried run also fails (`AbortedRunError` or a second `JudgeGateFailedError`) | `self_heal.handle_failure()` does NOT recurse — enforced by not calling itself, and by `tick()` only calling `handle_failure` once per failed dispatch. Second failure → `outcome="retried_failed"` → `atlas:blocked`, both original and retry `run_id`s named in the PR/issue comment. |
| Classification returns `infeasible` | Not retryable by definition (`_RETRYABLE_MODES` excludes it) — issue goes straight to `atlas:blocked` without spending a retry run. |
| Classifier output unparseable | Defaults to `mode="wrong_approach"`, `retryable=True` — mirrors `triage._parse_classify_response`'s existing "ambiguous defaults toward more oversight" convention (defaults to `planned`, the lane a human is more likely to see sooner). One retry attempt is a bounded cost even when the classification is a guess. |
| No diff available to judge (e.g. `code_gen` succeeded with zero file changes — should be caught earlier by `_commit_all`'s "nothing to commit" check, but defense in depth) | `score_diff` on an empty diff returns a low score deterministically-ish (still a real judge call, but plumb's own judge prompt is expected to score "no changes" low) — not special-cased in atlas; if this proves noisy in practice, revisit in a follow-up, not blocking this TRS (see Pending Decision #7). |
| `write_example` itself fails (durable persistence error) | Already handled inside `PlumbIO.write_example` (`plumb_io.py:296-306`) — logs a warning, does not raise. `self_heal.handle_failure` proceeds to classification regardless; losing the example is a data-quality gap, not a control-flow failure. |
| Planned-lane failure (`run_planned_first_pass` raises `AbortedRunError`) | Retried through the **same** `self_heal.handle_failure()` but re-dispatches via `run_planned_first_pass(..., parent_run_id=..., diagnosis=...)` instead of `run_one_shot` — see T-L3.6's acceptance criteria and Pending Decision #2 for the narrower contract here (no judge gate on a plan-only PR — there's no code diff to score, only a TRS triad). |
| Worktree from the failed attempt still exists when building the diagnosis diff | `self_heal.handle_failure` reads the diff via the **same worktree path** the failed run left behind (not yet cleaned up — `_cleanup_quietly` is called by `run_one_shot`'s own failure branch only on non-judge-gate failures; a `JudgeGateFailedError` path must defer cleanup until after `self_heal` reads the diff — see T-L3.5's acceptance criteria for the exact ordering fix this requires in `run_one_shot`). |
| `atlas:blocked` issue re-enters the queue (operator relabels back to `atlas:ready` after fixing something manually) | Out of scope for L3's code — this is the existing manual escape hatch the label protocol already implies (TRD-v3 §3.1: labels are relabeled by "Operator" as well as "Loop"). No special first-attempt-vs-retry state is persisted per issue (Pending Decision #6), so a manually-requeued blocked issue gets a fresh one-retry budget, which is the desired behavior, not a bug. |

---

## Dependencies & Interfaces

| Dependency | Type | Notes |
| --- | --- | --- |
| `plumb.adapters.get_judge_adapter` / `plumb.core.ports.JudgeAdapter` | In-process Python import (sibling repo, path-installed per PRD "Dependencies") | **New** direct import — not previously used by atlas. Verified present in `plumb/adapters/__init__.py:12` and `plumb/core/ports.py:145` against the installed sibling repo. `PLUMB_JUDGE_PROVIDER`/`PLUMB_JUDGE_ADAPTER` env vars gate availability. |
| `plumb.core.entities.Score`, `ScorerKind.JUDGE` | In-process Python import | Already used elsewhere in plumb's own `_cli_judge.py`; atlas constructs `Score` rows the same way (T-L3.2). |
| `PlumbIO.write_example` / `PlumbIO.reopen_run` | Internal (`plumb_io.py`) | Already shipped in L0/L2 — no new `PlumbIO` method required (extends L2 Decision #8's precedent one phase further). |
| `queue_gh.relabel(issue, state="blocked")` | Internal (`queue_gh.py`) | Signature already accepts `"blocked"` (TRD-v3 §3.1) — L3 is the first caller. No `queue_gh.py` change needed. |
| `loop_dev.yaml` (`plan → code_gen[isolate] → verify`) | Internal (packaged workflow) | Reused unchanged for the retry dispatch — same workflow, new prompt content (the injected diagnosis). |
| `plugin_resolver.resolve()` | Internal (`src/atlas/plugin_resolver.py`) | **Live blocker** — does not special-case `RAW:`-prefixed tool strings (STATUS.md). `loop_dev.yaml`'s `plan`/`code_gen` stages are both `RAW:`-prefixed. Must be resolved (or formally worked around) before any retry — quick-lane or otherwise — can run for real. See T-L3.1, Pending Decision #1. |

---

## Security Considerations

- **Judge input is the agent's own diff/output, not raw issue text a second time**
  — the injection surface analyzed in TRD-v3 §4 Security ("Prompt injection via
  issue bodies") is unchanged by L3; the diagnosis text fed back into the retry
  prompt originates from a judge classifying **atlas's own prior output**, not new
  untrusted external input. It is still operator-repo-scoped by the same "private,
  single-author repos in v3" assumption (TRD-v3 §5) — no new trust boundary is
  crossed, but the diagnosis string IS interpolated into a prompt that later
  triggers a second live agent run, so `self_heal.py`'s diagnosis-building step
  must not blindly forward arbitrary judge `rationale` text without at least the
  same scope-preamble discipline `build_issue_prompt` already applies
  (`_SCOPE_PREAMBLE`, `loop.py:81-88`) — T-L3.5's acceptance criteria requires the
  retry prompt to still include the scope preamble, not just title+body+diagnosis.
- **Judge credentials never touch atlas.** `PLUMB_JUDGE_PROVIDER`/API keys are read
  by plumb's own `get_settings()`, never passed through atlas code or logged by it
  — mirrors the existing "atlas does not persist LLM provider keys" NFR (PRD
  Security section, carried forward by every TRD).
- **`atlas:blocked` is a fail-safe state, not a fail-silent one.** Every path that
  reaches `atlas:blocked` also posts a `queue_gh.comment()` naming the failure mode
  and both run_ids — matches TRD-v3 §4 Usability ("Errors surface, don't hang")
  extended from ticks to individual issues.
- **The retry is bounded by construction, not just by config.** No `[loop]` config
  key introduces a "max retries" the operator could misconfigure into an unbounded
  loop — the cap is `handle_failure` not recursing, a code-level invariant, tested
  directly (T-L3.8).

---

## Testing Strategy

Coverage targets follow L2's own established bar (STATUS.md: "every individual
module meets its own target... well above the CI floor of 80%"):

- **`judge_gate.py`: 90%+** — pure logic + two thin adapter-call wrappers; easy to
  hit high coverage with mocked `JudgeAdapter`.
- **`self_heal.py`: 85%+** — the retry state machine; every outcome branch
  (`retried_success`, `retried_failed`, `not_retryable`, judge-unavailable-fail-to-safe)
  exercised via fakes, matching L2's own `loop.py` bar and rationale.
- **`loop.py` delta (judge-gate integration + `tick()` retry branch): 85%+**,
  consistent with L2's existing `loop.py` target — not a new module, so no new
  target row, but the diff itself must clear the same bar the file already carries.

The full unit/integration test matrix, mocking strategy, and manual-test tasks are
detailed in [`loop-mode-phase-L3-tasks-detail.md`](./loop-mode-phase-L3-tasks-detail.md)
(T-L3.2, T-L3.3, T-L3.4, T-L3.5, T-L3.6, T-L3.7, T-L3.8, T-L3.10) — each task's own
Acceptance Criteria and Testing Requirements fields are the authoritative source, not
duplicated here.

**Mocking strategy:** the `JudgeAdapter` Protocol is mocked at the `judge_gate.py`
boundary (a fake implementing `.score()`), not at plumb's HTTP/SDK layer — matches
how L2 already fakes `gh`/`subprocess`/`time` at the `queue_gh.py`/`loop.py`
boundary rather than mocking the network. Fixture judge responses live alongside
the existing `tests/fixtures/gh_json/` convention, e.g.
`tests/fixtures/judge_responses/*.json`.

---

## Performance Considerations

- **Two extra judge calls per failed run, zero extra calls on the happy path**
  beyond the one new gate call every successful `code_gen` now incurs (this is the
  one genuinely new per-run cost L3 adds regardless of failure — every quick-lane
  success now pays for one `score_diff` call it didn't before). Budget this
  explicitly in T-L3.9's cost-tracking note; it is a real, load-bearing latency +
  cost addition to the happy path, not just the failure path.
- **Judge latency is added to the critical path before delivery** — `score_diff`
  blocks `deliver_pr()`. A slow/hanging judge call delays every PR, not just
  failed ones. plumb's `JudgeAdapter.score()` signature already carries a
  `timeout_s: float = 60.0` default (`ports.py:158`) — `judge_gate.py` should pass
  an explicit timeout consistent with `queue_gh`'s existing "wrap every external
  call in a timeout" NFR (TRD-v3 §4 Performance), not rely on the adapter's
  default silently.
- **No new polling or busy-wait** — judge calls are synchronous, in-line with the
  existing per-issue dispatch; `tick()`'s one-issue-per-tick cadence (TRD-v3 §3.5)
  is unaffected in shape, only in per-dispatch latency.
- **Retry doubles worst-case per-issue latency** (bounded at 2x by the single-retry
  cap) — acceptable given TRD-v3's "sequential in v3.0–v3.2" NFR already accepts
  one-issue-per-tick throughput as the baseline; L3 does not change the
  concurrency model (still `concurrency=1`, still Phase L4 territory to lift).

---

## Tasks

Full flat task list (T-L3.1–T-L3.11) with Acceptance Criteria, Files to
Create/Modify, Dependencies, and Testing Requirements lives in
[`loop-mode-phase-L3-tasks-detail.md`](./loop-mode-phase-L3-tasks-detail.md) —
split out to keep this file under the repo's 800-line cap (same reasoning as L2's
own TRS split). Progress checkboxes live in
[`loop-mode-phase-L3-tasks.md`](./loop-mode-phase-L3-tasks.md).

One-line index:

| # | Task | Effort |
| - | --- | --- |
| T-L3.1 | Resolve or formally scope the `plugin_resolver` `RAW:` blocker | S |
| T-L3.2 | `judge_gate.py`: pre-PR scoring | M |
| T-L3.3 | `judge_gate.py`: failure-mode classification | M |
| T-L3.4 | `loop.py`: wire the judge gate into `run_one_shot` | M |
| T-L3.5 | `loop.py`: `parent_run_id` + `diagnosis` params on `run_one_shot` | M |
| T-L3.6 | `self_heal.py`: the retry state machine | L |
| T-L3.7 | `loop.py`: wire `self_heal` into `tick()`'s dispatch failure path | M |
| T-L3.8 | Retry-cap invariant test (explicit, not incidental) | S |
| T-L3.9 | Router v1 seam (named, not implemented) + cost-tracking note | S |
| T-L3.10 | Manual smoke: judge gate + retry against a real repo | M (manual) |
| T-L3.11 | Update STATUS.md and close out the phase | S |

---

## Phase Deliverables

- Working `judge_gate.py` (pre-PR scoring + failure-mode classification) and
  `self_heal.py` (diagnosis-injected single retry), wired into `loop.py`'s
  `run_one_shot`/`tick()` dispatch path, delivering TRD-v3 `v3.2` (§11).
- Tests passing: all new unit tests (`test_judge_gate.py`, `test_self_heal.py`,
  `loop.py`/`self_heal.py` deltas in `test_loop.py`) and integration tests
  (`test_loop_e2e.py` additions) green in CI; full existing v1/v2/L0/L1/L2 suite
  still green (attended-mode + L2 invariance both carry forward unchanged, per
  every prior phase's own exit bar).
- Documentation updated: `STATUS.md` phase-close entry (T-L3.11), BACKLOG.md
  Router-v1 entry (T-L3.9), `yaml_workflow_engine.md` `plugin_resolver` fix note
  (T-L3.1).
- **Explicitly NOT a deliverable of this phase**: Router v1 (implemented code),
  the planned lane's task-by-task multi-PR loop (still L2-scoped as
  first-pass-only), and `max_dollars_per_day` real enforcement (blocked on
  `extract_cost` + plumb P1-a, pre-existing and unrelated to L3).

---

## Pending Decisions & Clarifications

See [`loop-mode-phase-L3-decisions.md`](./loop-mode-phase-L3-decisions.md) for the
full text of all 8 pending decisions (split out for the same file-size reason).
One-line index:

| # | Decision | Recommendation |
| - | --- | --- |
| 1 | How/when to resolve the `plugin_resolver` `RAW:` blocker | Fix `resolve()` properly (T-L3.1, Option A) |
| 2 | Does the planned lane get the same judge gate as the quick lane? | No — no code diff exists to score (Option A) |
| 3 | Judge invocation: library `JudgeAdapter` API vs. `plumb judge run` CLI | Library API — confirm this reading of TRD-v3 §6 |
| 4 | Include Router v1 as a committed task in this TRS? | No — name the seam only (T-L3.9) |
| 5 | Fail-open vs. fail-closed on `JudgeUnavailableError` | Fail-open on the gate, fail-to-safe on classification |
| 6 | Persist a retry counter in `LoopState`, or control-flow-only? | Control-flow-only |
| 7 | Empty/near-empty diffs reaching the judge gate | No decision needed now — named for later |
| 8 | Judge prompt file provisioning for the new `failure_mode` metric | Needs a short spike against plumb's actual prompt-loading convention before T-L3.3 starts |
