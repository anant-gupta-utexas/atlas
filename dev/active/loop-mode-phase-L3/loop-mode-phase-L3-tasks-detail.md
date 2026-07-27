# Tasks (detail) — Loop Mode, Phase L3 TRS

Full flat task list (T-L3.1–T-L3.11) for TRD-v3 Phase L3 (Self-healing + routing).
Split out from `loop-mode-phase-L3-plan.md` to keep that file under the repo's
800-line cap (matches L2's own precedent). Ordered by execution sequence;
cross-task dependencies captured via each task's `Dependencies` field, not by
nesting. Progress checkboxes live in `loop-mode-phase-L3-tasks.md`.

---

### T-L3.1 — Resolve or formally scope the `plugin_resolver` `RAW:` blocker

**[Task Name]** Resolve `plugin_resolver.resolve()`'s missing `RAW:` special-case
[Effort: S] — **DONE (closed early, 2026-07-25, commit `48ee363`)**

> **Closed before L3 started.** This task was written while the blocker was open;
> it was fixed during the L2 session that unblocked T-L2.13, since the same
> one-line gap blocked both phases. Pending Decision #1 resolved as **Option A**
> (fix `resolve()`), which was already its recommended option. No L3 work
> remains here — kept for provenance rather than deleted. **T-L3.10 no longer
> depends on this task**, only on `PLUMB_JUDGE_PROVIDER` being configured.

- **Description**: `resolve()` did a literal dict lookup and didn't special-case
  `RAW:`-prefixed tool strings despite its own docstring's claim that it did, so
  `loop_dev.yaml`'s `plan` and `code_gen` stages raised `RoutingDriftError` under
  a real `atlas loop run`. This was a **precondition for every other task in this
  TRS that touches a live run** (T-L3.10) and for L2's T-L2.13.
- **Resolution**: Option A. `resolve()` now returns `RAW:` strings verbatim — the
  text after `RAW:` is a literal prompt authored in the workflow YAML, not a
  plugin name, so there is no third-party command to allow-list. The allow-list's
  security property is unchanged: unknown *non*-`RAW:` tool strings still raise
  before any subprocess spawns. An explicit `.atlas.toml` override still wins.
  A second, latent bug surfaced once the first was fixed: the `verify` stage
  carried a literal `"/verify"` tool string, which `build_prompt` would have
  rendered as `//verify` (it prepends the slash itself) — now the bare `verify`,
  mapped in `PLUGIN_COMMANDS` to `DEV-ESSENTIALS:verify`. Fixing only the `RAW:`
  half would have turned a clean error into a malformed prompt found mid-smoke.
- **Acceptance Criteria**:
    - [x] `loop_dev.yaml`'s three stages (`plan`, `code_gen`, `verify`) resolve
      without `RoutingDriftError` — pinned by
      `test_loop_dev_workflow_stages_all_resolve_without_overrides`.
    - [x] The fix is documented in `docs/3_guides/yaml_workflow_engine.md`
      (tool-string conventions section), including the no-leading-slash pitfall.
    - [x] STATUS.md's `blocked_on` is now `null`.
- **Files Modified** (actual):
    - `src/atlas/plugin_resolver.py` — `RAW:` pass-through + `verify` mapping
    - `src/atlas/workflows/loop_dev.yaml` — `"/verify"` → `"verify"`
    - `docs/3_guides/yaml_workflow_engine.md` — documented both conventions
    - `tests/unit/test_phase4.py` — 3 regression tests (co-located with the
      existing T4.3 allow-list test rather than a new
      `tests/unit/test_plugin_resolver.py`, so the bypass and the allow-list
      rejection it must not weaken sit side by side)
    - `tests/integration/test_loop_e2e.py` — removed the now-obsolete
      `[plugin_commands]` workaround so those tests exercise the real path
    - `tests/unit/test_workflow_loader.py` — updated for the bare `verify`
- **Dependencies**: None
- **Testing Requirements**: Unit — 427 passed, 1 xfailed; ruff + `mypy --strict src` clean

---

### T-L3.2 — `judge_gate.py`: pre-PR scoring

**[Task Name]** Implement `judge_gate.score_diff()` [Effort: M]

- **Description**: New module per the plan's Detailed Component Design section.
  Wraps `plumb.adapters.get_judge_adapter(get_settings(), metric_name="task_completion")`
  → `.score(...)`, constructs and writes the `Score` row (`scorer=ScorerKind.JUDGE`),
  applies the configurable threshold (default `0.7`, matching TRD-v3 §14's stated
  default), raises `JudgeUnavailableError` on missing provider config.
- **Acceptance Criteria**:
    - [ ] `score_diff(diff_text=..., model=..., threshold=0.7)` returns
      `JudgeGateResult(passed=True, ...)` when the mocked adapter returns
      `value_numeric >= threshold`.
    - [ ] Returns `passed=False` when below threshold.
    - [ ] Raises `JudgeUnavailableError` when `PLUMB_JUDGE_PROVIDER` is unset
      (mocked `get_settings`), with **no** subprocess/network call attempted —
      mirrors `CodexBackend.preflight()`'s fail-closed-before-spawn pattern
      (TRD-v3 §3.3).
    - [ ] Writes a `Score` row with `span_id` anchored to a real span (not `""`)
      — same discipline the L2 code review enforced for `sync_prior_prs`.
    - [ ] Explicit `timeout_s` passed to `.score()`, not the adapter default.
- **Files to Create/Modify**:
    - `src/atlas/judge_gate.py` — new
    - `tests/unit/test_judge_gate.py` — new
- **Dependencies**: None (independent of T-L3.1; can start in parallel)
- **Testing Requirements**: Unit (mocked `JudgeAdapter`)

---

### T-L3.3 — `judge_gate.py`: failure-mode classification

**[Task Name]** Implement `judge_gate.classify_failure()` + judge prompt file
[Effort: M]

- **Description**: Second judge call, `metric_name="failure_mode"`, a **new**
  judge prompt file (plumb loads prompts by metric name via
  `plumb._prompt_loader.load_prompt` — confirmed in sibling repo source) asking
  the judge to pick one of the four TRD-named modes
  (`flaky`/`wrong_approach`/`missing_context`/`infeasible`) with a rationale.
  Parses the judge's `value_label` into `FailureMode`; unparseable → defaults to
  `wrong_approach`/`retryable=True` (mirrors `triage.py`'s existing
  ambiguity-default convention). **Before writing code, resolve
  [Pending Decision #8](./loop-mode-phase-L3-decisions.md#8-judge-prompt-file-provisioning-for-the-new-failure_mode-metric-t-l33)**
  (where plumb expects judge prompt files to live).
- **Acceptance Criteria**:
    - [ ] `classify_failure(diff_text=..., failure_context=..., model=...)` maps
      each of the four judge `value_label` strings to the correct
      `FailureClassification.mode` and `retryable` flag (`infeasible` →
      `retryable=False`; the other three → `True`).
    - [ ] Unparseable/unexpected `value_label` → `mode="wrong_approach"`,
      `retryable=True`, logged at WARNING (not silently swallowed).
    - [ ] Raises `JudgeUnavailableError` on missing judge provider config,
      identically to `score_diff`.
    - [ ] A `judge_prompts/failure_mode.md` (or equivalent, per whatever
      Pending Decision #8's spike concludes about plumb's actual prompt-file
      provisioning convention) is added and documented as a new atlas-owned
      data file the operator must have in place.
- **Files to Create/Modify**:
    - `src/atlas/judge_gate.py` — extends T-L3.2's module
    - `tests/unit/test_judge_gate.py` — extends T-L3.2's test file
    - judge prompt file (location per Pending Decision #8's resolution)
- **Dependencies**: T-L3.2 (shares the module + judge-adapter plumbing)
- **Testing Requirements**: Unit (mocked `JudgeAdapter`, one test per mode)

---

### T-L3.4 — `loop.py`: wire the judge gate into `run_one_shot`

**[Task Name]** Insert the pre-PR judge gate before `deliver_pr()` [Effort: M]

- **Description**: Per the plan's Algorithm & Logic Design (first pseudocode
  block). Read the worktree diff (`git diff main`, mirroring `_commit_all`'s
  existing subprocess-call style), call `judge_gate.score_diff`, raise
  `JudgeGateFailedError` (new, subclasses `AbortedRunError`) on failure **before**
  `queue_gh.deliver_pr()` is reached. Must NOT clean up the worktree on a
  judge-gate failure (unlike other `AbortedRunError` paths) — `self_heal` needs
  the diff to build the diagnosis; cleanup happens after `self_heal` finishes
  with it (see T-L3.5).
- **Acceptance Criteria**:
    - [ ] A `RunResult.status == "success"` run with a judge score below
      threshold does **not** call `deliver_pr` (call-count assertion).
    - [ ] The same run WITH a passing judge score delivers exactly as before
      (regression guard — L2's existing `run_one_shot` tests must still pass
      unmodified in shape, only gaining the new judge-call mock).
    - [ ] `JudgeGateFailedError` carries the `JudgeGateResult` for the caller to
      inspect without re-scoring.
    - [ ] Worktree is NOT cleaned up on `JudgeGateFailedError` (cleanup deferred
      to whoever consumes the diagnosis — verified by an explicit test asserting
      `WorktreeManager.cleanup` is not called on this path).
- **Files to Create/Modify**:
    - `src/atlas/loop.py` — `run_one_shot`, new `JudgeGateFailedError`
    - `tests/unit/test_loop.py` — new cases
- **Dependencies**: T-L3.2
- **Testing Requirements**: Unit

---

### T-L3.5 — `loop.py`: `parent_run_id` + `diagnosis` params on `run_one_shot`

**[Task Name]** Add retry-dispatch parameters to `run_one_shot` [Effort: M]

- **Description**: Additive keyword-only params (per Appendix A's one sanctioned
  precedent — L1's `RunResult` widening). `parent_run_id` triggers
  `PlumbIO.reopen_run()` instead of `open_run()` before the pipeline starts.
  `diagnosis`, when set, is appended to `build_issue_prompt()`'s output — but
  **must preserve the existing `_SCOPE_PREAMBLE`** (Security Considerations in
  the plan) rather than being prepended/replacing it.
- **Acceptance Criteria**:
    - [ ] `run_one_shot(issue, cfg, repo_root=..., parent_run_id=X, diagnosis=Y)`
      calls `PlumbIO.reopen_run(X)` instead of `open_run()`.
    - [ ] The dispatched prompt contains the issue title+body, the diagnosis
      text, AND `_SCOPE_PREAMBLE` (all three, in that order — asserted by
      string-containment test, not just "diagnosis appears somewhere").
    - [ ] Calling `run_one_shot` with no `parent_run_id`/`diagnosis` (existing L2
      call sites, unmodified) behaves byte-identically to pre-L3 — a regression
      test using the exact L2 test fixtures.
    - [ ] The child-run reconciliation between `ctx.run_id` (Pipeline's own
      bookkeeping id, unaffected by `reopen_run`) and the active plumb run_id
      (which changes after `reopen_run`) is resolved without breaking any span
      write — spans recorded during a retried run land under the **child**
      run_id in plumb, not the parent's.
- **Files to Create/Modify**:
    - `src/atlas/loop.py`
    - `tests/unit/test_loop.py`
- **Dependencies**: T-L3.4 (touches the same function)
- **Testing Requirements**: Unit

---

### T-L3.6 — `self_heal.py`: the retry state machine

**[Task Name]** Implement `self_heal.handle_failure()` [Effort: L]

- **Description**: Per the plan's Algorithm & Logic Design (second pseudocode
  block). Orchestrates: `write_example` → `classify_failure` → retryable check →
  single `run_one_shot` (or `run_planned_first_pass`, for the planned lane —
  narrower contract, no judge gate on that path since a plan-only PR has no code
  diff) retry → outcome. Must not recurse.
- **Acceptance Criteria**:
    - [ ] `write_example` is called exactly once per `handle_failure` invocation,
      regardless of downstream outcome.
    - [ ] `classify_failure` is called exactly once; a `JudgeUnavailableError`
      from it results in `outcome="not_retryable"` (fail-to-safe, per
      [Pending Decision #5](./loop-mode-phase-L3-decisions.md#5-fail-open-vs-fail-closed-on-judgeunavailableerror)'s
      classify-side default) WITHOUT calling `run_one_shot`.
    - [ ] `mode="infeasible"` results in `outcome="not_retryable"` WITHOUT
      calling `run_one_shot` (call-count assertion).
    - [ ] Any retryable mode results in exactly one `run_one_shot`/
      `run_planned_first_pass` call with `parent_run_id` and `diagnosis` set.
    - [ ] A second failure on the retried run does NOT trigger a second
      `handle_failure` call anywhere in the codebase — enforced by the calling
      convention in `tick()` (T-L3.7), verified by an integration test asserting
      total run count for a doubly-failing issue is exactly 2.
    - [ ] Planned-lane retries never call `judge_gate.score_diff` (no code diff
      exists at that point) — asserted by call-count.
- **Files to Create/Modify**:
    - `src/atlas/self_heal.py` — new
    - `tests/unit/test_self_heal.py` — new
- **Dependencies**: T-L3.2, T-L3.3, T-L3.5
- **Testing Requirements**: Unit

---

### T-L3.7 — `loop.py`: wire `self_heal` into `tick()`'s dispatch failure path

**[Task Name]** Retry-or-block branch in `tick()` [Effort: M]

- **Description**: Per the plan's Algorithm & Logic Design (third pseudocode
  block). Replaces L2's existing bare `except AbortedRunError` handling (which
  left the issue `atlas:working` with no forward progress) with a call into
  `self_heal.handle_failure`, then branches on `SelfHealResult.outcome` to either
  deliver-as-success or relabel `atlas:blocked` with a comment.
- **Acceptance Criteria**:
    - [ ] `retried_success` outcome results in the exact same `TickResult`/label/
      comment shape as a first-try success (operator-visible parity).
    - [ ] `not_retryable`/`retried_failed` outcomes call
      `queue_gh.relabel(issue, state="blocked")` exactly once, and
      `queue_gh.comment(...)` with a body naming the failure mode and both
      run_ids where applicable.
    - [ ] `record_tick_outcome` is still called on every branch (existing L2
      budget/breaker bookkeeping must not regress — a retry-triggering failure
      still counts toward `identical_error_limit`/`no_progress_limit` the same
      way a plain failure did in L2, since the *tick* still didn't land a PR
      unless the retry succeeded).
- **Files to Create/Modify**:
    - `src/atlas/loop.py`
    - `tests/unit/test_loop.py`
    - `tests/integration/test_loop_e2e.py`
- **Dependencies**: T-L3.6
- **Testing Requirements**: Unit + Integration

---

### T-L3.8 — Retry-cap invariant test (explicit, not incidental)

**[Task Name]** Dedicated test proving the retry cap can't be bypassed [Effort: S]

- **Description**: TRD-v3 §13 #9 says "retried **once**" — this is a safety
  property (see the plan's Security Considerations: "bounded by construction, not
  just config"), so it gets its own named test rather than relying on incidental
  coverage from T-L3.6/T-L3.7's tests. Mirrors L2's own precedent of dedicated
  safety tests (e.g. `Deliverer` "never pushes main" test) for properties that
  matter beyond ordinary correctness.
- **Acceptance Criteria**:
    - [ ] A test drives `tick()` through a doubly-failing issue end-to-end
      (faked `gh`/pipeline) and asserts the **total** number of
      `run_one_shot`/`run_planned_first_pass` invocations is exactly 2 (1
      original + 1 retry), never 3+, regardless of how many times `tick()` is
      subsequently called on the same (now-`atlas:blocked`) issue.
    - [ ] A second `tick()` call on an already-`atlas:blocked` issue does not
      re-dispatch it (blocked issues are excluded from `_pull_next_ready`'s
      `atlas:ready`-label query by construction — asserted, not assumed).
- **Files to Create/Modify**:
    - `tests/integration/test_loop_e2e.py`
- **Dependencies**: T-L3.7
- **Testing Requirements**: Integration

---

### T-L3.9 — Router v1 seam (named, not implemented) + cost-tracking note

**[Task Name]** Document the router-v1 seam and judge-call cost accounting
[Effort: S]

- **Description**: Per the plan's Requirements Summary, Router v1 ("prefer the
  engine/workflow that scores better in plumb for the task class") is
  TRD-labeled stretch and excluded from this phase's committed acceptance
  criteria (see
  [Pending Decision #4](./loop-mode-phase-L3-decisions.md#4-router-v1--include-as-a-stretch-task-in-this-trss-list-or-omit-entirely)).
  This task is deliberately small: name the seam in code comments/docs (e.g. a
  `# Router v1 seam: _engine_for_issue could consult plumb run stats here —
  TRD-v3 §14 Phase L3 stretch, not implemented` comment at
  `loop.py::_engine_for_issue`) so a future phase doesn't have to rediscover
  where it plugs in, and document that the two new judge calls' token cost is
  intended to count toward `max_dollars_per_day` (mirroring L2 Decision #17 for
  the triage classifier) even though — like the triage classifier's cost today —
  this remains mechanically inert until `extract_cost` is implemented (unrelated,
  pre-existing gap, not this TRS's to fix).
- **Acceptance Criteria**:
    - [ ] A code comment at the router seam names Router v1 explicitly and links
      to TRD-v3 §14.
    - [ ] `docs/1_product_and_research/BACKLOG.md` gets a Router v1 entry (if not
      already present) distinct from the `extract_cost` entry, so the two known
      gaps aren't conflated.
    - [ ] This TRS's plan explicitly states Router v1 is NOT part of Phase
      Deliverables (self-referential acceptance criterion — confirms scope
      discipline was followed, not just stated).
- **Files to Create/Modify**:
    - `src/atlas/loop.py` — comment only
    - `docs/1_product_and_research/BACKLOG.md`
- **Dependencies**: None
- **Testing Requirements**: None (documentation-only task)

---

### T-L3.10 — Manual smoke: judge gate + retry against a real repo

**[Task Name]** Live judge-gate and retry smoke test [Effort: M, manual/off-CI]

- **Description**: Per the user's explicit framing — manual testing remains
  across phases, and this phase's own exit criteria (§13 #9, #10) are stated as
  live behaviors ("A plumb judge score below threshold blocks delivery"), not
  just unit-tested logic. Requires T-L3.1 resolved (real `atlas loop run` must
  work at all) and a configured `PLUMB_JUDGE_PROVIDER`. Deliberately engineer one
  issue that should fail the judge gate (e.g. an acceptance criterion the agent
  can't satisfy) and confirm: no PR opens on the first attempt, a retry fires,
  the PR comment names both run_ids, and — separately — an issue engineered to
  fail twice ends up `atlas:blocked` with a human-readable comment.
- **Acceptance Criteria**:
    - [ ] One real issue: judge gate blocks the first attempt, retry succeeds,
      PR opens, comment shows both run_ids and the failure-mode rationale.
    - [ ] One real issue: both attempts fail (or are engineered to be
      `infeasible`), issue relabeled `atlas:blocked`, comment present, no PR
      opened.
    - [ ] plumb DB inspected post-hoc: `examples` row present with correct
      `origin_run_id`; `scores` rows for both `task_completion` and
      `failure_mode` metrics present with `scorer=judge` and non-dangling
      `span_id`; child run's `parent_run_id` correctly points at the original.
    - [ ] Findings (what worked, what didn't, any schema surprises analogous to
      L1's Codex-schema corrections) captured in this TRS's context file or a
      follow-up STATUS.md entry.
- **Files to Create/Modify**: None (manual verification; findings recorded in
  `dev/active/loop-mode-phase-L3/loop-mode-phase-L3-context.md` and/or
  `STATUS.md`)
- **Dependencies**: T-L3.1, T-L3.7, T-L3.8
- **Testing Requirements**: E2E (manual, off-CI, real GitHub repo + real judge
  provider)

---

### T-L3.11 — Update STATUS.md and close out the phase

**[Task Name]** Phase completion entry [Effort: S]

- **Description**: Standard phase-close task, matching every prior phase's own
  T-L0.x/T-L1.x/T-L2.14 pattern — update `STATUS.md`'s Current/Next sections,
  test counts, coverage figures, and explicitly carry forward any manual checks
  still open at L3's close (mirroring how L2's STATUS.md entry explicitly named
  L0/L1's still-open manual checks rather than letting them go silent).
- **Acceptance Criteria**:
    - [ ] `STATUS.md` "Current" section describes L3 as code-complete with real
      test/coverage numbers (not placeholders).
    - [ ] `STATUS.md` "Next" section points at Phase L4 (scale-out) and restates
      any manual checks from L0–L3 still open at this point.
    - [ ] `dev/active/loop-mode-phase-L3/loop-mode-phase-L3-tasks.md` checkboxes
      all reflect actual completion state.
- **Files to Create/Modify**:
    - `STATUS.md`
    - `dev/active/loop-mode-phase-L3/loop-mode-phase-L3-tasks.md`
- **Dependencies**: T-L3.10
- **Testing Requirements**: None
