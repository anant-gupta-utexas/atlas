# Task Detail — Phase L2 TRS

Full flat task list (T-L2.1–T-L2.14) with per-task acceptance criteria, files,
dependencies, and testing requirements. Referenced from
[`loop-mode-phase-L2-plan.md`](./loop-mode-phase-L2-plan.md)'s **Tasks**
section — split out to keep the plan file under the repo's 800-line cap.
This file is normative TRS content, not a separate artifact; treat it exactly
as if it were inline in the plan. Progress checkboxes live in
[`loop-mode-phase-L2-tasks.md`](./loop-mode-phase-L2-tasks.md).

---

* **T-L2.1 — Baseline verification + `gh` fixture capture** [Effort: S]
  - **Description**: Confirm L0+L1's shipped state (301 tests, 1 xfail, 96% coverage per STATUS.md) still holds at TRS-authoring time by re-running the suite. Capture real `gh issue list --json ...` / `gh pr view --json state,mergedAt` / `gh pr create` output shapes against a real (scratch or the atlas repo itself) issue/PR, to ground `tests/fixtures/gh_json/*` in real schema rather than an assumed one — the exact lesson L1's Resolved Decision #1 (Codex schema verification) teaches applied preemptively here instead of discovered mid-implementation.
  - **Acceptance Criteria**:
    - [ ] Full suite re-confirmed passing at the same pass/xfail/coverage numbers STATUS.md claims (or deltas explained)
    - [ ] Real captured `gh --json` output for `issue list`, `pr view` (all three outcome states: open/merged/closed-unmerged), saved as fixtures
    - [ ] `gh --version` recorded in context.md (schema drift risk, same posture as L1's Codex version pin)
  - **Files to Create/Modify**:
    - `tests/fixtures/gh_json/*.json` - captured shapes
    - `dev/active/loop-mode-phase-L2/loop-mode-phase-L2-context.md` - findings
  - **Dependencies**: none
  - **Testing Requirements**: N/A (capture task; feeds T-L2.2's fixtures)

* **T-L2.2 — `queue_gh.py`: the `gh` adapter** [Effort: L]
  - **Description**: Implement `list_ready`, `claim`, `deliver_pr` (thin pass-through to `Deliverer`), `comment`, `sync`, `relabel` per the plan's Detailed Component Design. Every call list-form argv, timeout-wrapped, raising `GhCliError` on failure. `Issue`/`PrStatus` dataclasses. Include the grep-based "sole `gh` caller" test (Decision #15).
  - **Acceptance Criteria**:
    - [ ] All six functions implemented; every `subprocess.run` call passes `timeout=`
    - [ ] `list_ready` parses real captured JSON (T-L2.1) into `list[Issue]`
    - [ ] `sync` correctly maps `MERGED`/`CLOSED`(unmerged)/`OPEN` PR states to the three `outcome` values
    - [ ] `relabel("done")` both swaps labels and closes the issue; other states only swap labels
    - [ ] No `shell=True` anywhere; no raw issue-body content ever interpolated into a `gh` argv string
    - [ ] A grep/AST-based test confirms no `gh` subprocess invocation exists outside this module
    - [ ] `mypy --strict src` passes
  - **Files to Create/Modify**:
    - `src/atlas/queue_gh.py` - new adapter
  - **Dependencies**: T-L2.1
  - **Testing Requirements**: Unit (`tests/unit/test_queue_gh.py`, full table in plan's Testing Strategy)

* **T-L2.3 — `[loop]` config** [Effort: S]
  - **Description**: Add `LoopConfig` dataclass and `Config.loop` field per Detailed Component Design; extend `Config.load()`'s TOML parsing to read a `[loop]` section using the existing `_deep_merge` pattern. `concurrency != 1` raises at construction (frozen at 1 for v3.0-v3.2, TRD-v3 §5).
  - **Acceptance Criteria**:
    - [ ] `LoopConfig` matches TRD-v3 §7's schema exactly (field names, defaults)
    - [ ] `Config.load()` with no `[loop]` section produces `LoopConfig()` defaults (no crash on absent section)
    - [ ] `Config.load()` with a `[loop]` section correctly overrides defaults
    - [ ] `LoopConfig(concurrency=2)` raises `ValueError` at construction
    - [ ] `trusted_authors` parses as a tuple of strings; absent → `()`
  - **Files to Create/Modify**:
    - `src/atlas/config.py` - `LoopConfig`, `Config.loop` field, TOML parsing
    - `tests/unit/test_config.py` - `[loop]` section parsing tests
  - **Dependencies**: none
  - **Testing Requirements**: Unit

* **T-L2.4 — `triage.py`: label-wins-else-classify router** [Effort: M]
  - **Description**: Implement `triage()` per Algorithm & Logic Design — `wf:quick`/`wf:planned` label check first (both-present resolves to `planned`), else a single haiku classify call dispatched directly via `CliBackend` (Decision #13), recorded as a plumb span. Unparseable classifier output defaults to `planned`.
  - **Acceptance Criteria**:
    - [ ] Label-wins path never invokes the classifier (asserted via mock call count)
    - [ ] Both-labels-present resolves to `planned` with a logged warning
    - [ ] Classify-fallback path invokes exactly one backend call, records a plumb span (`kind="plan"`, `name="triage"`)
    - [ ] Unparseable classifier output → `planned` + warning, never raises
  - **Files to Create/Modify**:
    - `src/atlas/triage.py` - new module
    - `tests/unit/test_triage.py`
  - **Dependencies**: T-L2.1 (for realistic classify prompt/response shape, if captured)
  - **Testing Requirements**: Unit

* **T-L2.5 — `loop.py`: `tick()` core state machine** [Effort: XL]
  - **Description**: The central deliverable. Implement `tick()` exactly per Algorithm & Logic Design: sync-first (unconditional) → breaker check → budget check → pull next ready issue → trusted-author check → triage → claim → dispatch (quick/planned) → comment (success and failure, Decision #14) → persist state. `run_one_shot()` constructs `Pipeline(loop_dev)` via a shared `make_pipeline()` extracted from `cli.py::_make_pipeline` (Decision #11), consumes `RunResult`, calls `Deliverer.deliver()` on success. `run_planned_first_pass()` invokes `dev-docs-be`, opens a plan-only PR, stops (Decision #2's first-pass-only scope). `build_issue_prompt()` per Decision #10.
  - **Acceptance Criteria**:
    - [ ] `tick()` implements every branch in the Algorithm & Logic Design pseudocode
    - [ ] Sync runs even when breaker is open or budget is exhausted
    - [ ] `claim()` happens before any `Pipeline` construction (crash-safety ordering)
    - [ ] `wf:quick` → exactly one PR (`Closes #n`), `RunResult.status == "success"` required before `Deliverer.deliver()` is called
    - [ ] `wf:planned` → a plan-only PR containing the `dev/active/<slug>/` triad with Pending Decisions surfaced in the PR body; **no `code_gen` dispatch this tick**
    - [ ] A failed `loop_dev` run never calls `Deliverer.deliver()`; issue stays `atlas:working`; a failure comment is posted
    - [ ] A `DeliveryError` from `Deliverer.deliver()` is caught at the tick level, logged, does not crash `run_forever()`
    - [ ] `cli.py::_make_pipeline` is promoted to a shared `make_pipeline()` with a `backend_override` param; `cli.py::run`/`resume` updated to call the shared version (regression-proof)
  - **Files to Create/Modify**:
    - `src/atlas/loop.py` - `tick()`, `run_one_shot()`, `run_planned_first_pass()`, `build_issue_prompt()`
    - `src/atlas/cli.py` - `_make_pipeline` → shared `make_pipeline()` (Decision #11)
  - **Dependencies**: T-L2.2, T-L2.3, T-L2.4
  - **Testing Requirements**: Unit (`tests/unit/test_loop.py`'s tick/dispatch tests), regression tests on `cli.py::run`/`resume`

* **T-L2.6 — `loop.py`: budgets + circuit breaker** [Effort: M]
  - **Description**: Implement `LoopState`, `budget_exhausted()`, `breaker_open()`, `record_tick_outcome()` per Detailed Component Design. In-memory (not `runs.dollar_cost`) cost accumulation per TRD-v3 §3.6/§12; triage classifier cost counted toward `dollars_today` but not `runs_today` (Decision #17). Day-rollover counter reset. Breaker opens on `no_progress_limit` consecutive no-progress ticks OR `identical_error_limit` consecutive identical `error_signature`s; closes after `cooldown_min`.
  - **Acceptance Criteria**:
    - [ ] `budget_exhausted` true once `runs_today >= max_runs_per_day` OR `dollars_today >= max_dollars_per_day`
    - [ ] Day rollover resets both counters before the check
    - [ ] Breaker opens on either threshold independently; resets both counters on any tick with `made_progress=True`
    - [ ] `breaker_open_until` computed as `now + cooldown_min`; `run_forever` does not dispatch while open but does keep polling/sleeping at `poll_interval_s`
    - [ ] `LoopState` persists to `.atlas/loop-state.json`; missing/corrupted file inits fresh with a warning, never crashes
  - **Files to Create/Modify**:
    - `src/atlas/loop.py` - `LoopState`, budget/breaker functions
  - **Dependencies**: T-L2.5
  - **Testing Requirements**: Unit (budget/breaker table in plan's Testing Strategy)

* **T-L2.7 — `loop.py`: `sync_prior_prs()` + idempotent scoring** [Effort: M]
  - **Description**: Implement the sync path — `queue_gh.sync()` per repo, map merged→1.0/closed→0.0 `user_signal` writes via `PlumbIO.reopen_run()` + `record_user_signal()` + `close_run()` (Decision #8), local dedupe via `synced_pr_outcomes`, relabel + close-on-merge (Decision #12). Also closes the L1 code-review `PrRef.number` finding (see T-L2.12) since this is the first real consumer of `PrRef.number` for run-id correlation.
  - **Acceptance Criteria**:
    - [ ] Merged PR → `user_signal=1.0`, issue relabeled `atlas:done`, issue closed
    - [ ] Closed-unmerged PR → `user_signal=0.0`, issue relabeled `atlas:rejected`, issue left open (per TRD-v3 §3.1's label table — only `atlas:done` implies closure)
    - [ ] Re-running `sync_prior_prs` on an already-scored outcome is a no-op (dedupe key check)
    - [ ] `run_id` correctly recovered from the issue's `comment()` body written at dispatch time
  - **Files to Create/Modify**:
    - `src/atlas/loop.py` - `sync_prior_prs()`
  - **Dependencies**: T-L2.2, T-L2.5
  - **Testing Requirements**: Unit (sync table in plan's Testing Strategy), Integration

* **T-L2.8 — `loop.py`: `run_forever()` + `reconcile_orphans()`** [Effort: M]
  - **Description**: The outer `while True` loop (breaker-aware sleep, `poll_interval_s` cadence, bare-`Exception` safety net per Decision #18) and startup crash recovery — reset stale `atlas:working` issues with no linked PR back to `atlas:ready`, prune stale `.atlas/worktrees/*`.
  - **Acceptance Criteria**:
    - [ ] `run_forever` calls `reconcile_orphans` exactly once, at startup, before the first `tick()`
    - [ ] A stale `atlas:working` issue with no PR → relabeled `atlas:ready`
    - [ ] A `atlas:working` issue with an open PR → left untouched
    - [ ] A stale worktree with no matching active issue → `WorktreeManager.cleanup()` called; failure logged, not raised
    - [ ] `run_forever` never raises out of the loop on a single tick's exception (outer `except Exception`, logged, loop continues)
  - **Files to Create/Modify**:
    - `src/atlas/loop.py` - `run_forever()`, `reconcile_orphans()`
  - **Dependencies**: T-L2.5, T-L2.6, T-L2.7
  - **Testing Requirements**: Unit + Integration (`test_crash_recovery_full_cycle`)

* **T-L2.9 — `atlas loop` CLI surface** [Effort: M]
  - **Description**: Register the `atlas loop` Typer sub-app in `cli.py` with `run`/`start`/`stop`/`status`/`attach` per Detailed Component Design. `start`/`stop`/`attach` are thin `tmux` subprocess wrappers; `run` calls `run_forever()` in-process; `status` reads `.atlas/loop-state.json` and prints a human-readable summary (budgets used, last tick, in-flight issue, breaker state — TRD-v3 §4 NFR Usability).
  - **Acceptance Criteria**:
    - [ ] `atlas loop run` calls `run_forever(cfg, repos=cfg.loop.repos)` with no tmux dependency
    - [ ] `atlas loop start`/`stop`/`attach` produce the exact `tmux new -d -s atlas-loop 'atlas loop run'` / `tmux kill-session -t atlas-loop` / `tmux attach -t atlas-loop` invocations
    - [ ] A missing `tmux` binary produces a clear error for `start`/`stop`/`attach` only (`FileNotFoundError` caught, `typer.Exit(1)` with a message); `run` is unaffected
    - [ ] `atlas loop status` with no `.atlas/loop-state.json` reports "loop has not run yet" rather than crashing
    - [ ] `atlas loop status` with a populated state file reports runs/dollars used vs. budget, last tick time, breaker state (open/closed + until-when)
  - **Files to Create/Modify**:
    - `src/atlas/cli.py` - `loop_app` Typer sub-app registration
  - **Dependencies**: T-L2.5, T-L2.6, T-L2.8
  - **Testing Requirements**: Unit (Typer `CliRunner` invocation tests, `tmux` subprocess mocked)

* **T-L2.10 — Integration tests: full-tick + zero-touch smoke (faked)** [Effort: L]
  - **Description**: `tests/integration/test_loop_e2e.py` — the full state machine exercised end-to-end with faked `gh`/`Pipeline`/backend dispatch, per Testing Strategy's integration table. This is the CI-safe proof of TRD-v3 §13 #5/#6 (the manual, off-CI real-world proof is T-L2.13).
  - **Acceptance Criteria**:
    - [ ] `test_one_shot_lane_end_to_end_faked` passes: one PR, one plumb run, correct final labels
    - [ ] `test_planned_lane_stops_after_plan_pr` passes: plan-only PR, triad files exist, no `code_gen` span
    - [ ] `test_crash_recovery_full_cycle` passes: claim → simulated crash → `reconcile_orphans` on restart → issue back to `atlas:ready`
    - [ ] `test_zero_touch_smoke_faked` passes: label → one `tick()` → PR with `Closes #n` + `run_id` comment, no other interaction simulated
  - **Files to Create/Modify**:
    - `tests/integration/test_loop_e2e.py` - new
  - **Dependencies**: T-L2.5, T-L2.6, T-L2.7, T-L2.8
  - **Testing Requirements**: Integration

* **T-L2.11 — Lint/type/coverage gate** [Effort: S]
  - **Description**: `ruff check`, `ruff format --check`, `mypy --strict src`, coverage check (repo-wide no regression below L1's 96%; `loop.py` ≥85%, `queue_gh.py` ≥90%, `triage.py` ≥85%, `config.py`'s new lines ≥90%, per TRD-v3 §10).
  - **Acceptance Criteria**:
    - [ ] `ruff check` and `ruff format --check` clean
    - [ ] `mypy --strict src` clean
    - [ ] Coverage targets met per module
  - **Files to Create/Modify**: none (verification task)
  - **Dependencies**: T-L2.2 through T-L2.10
  - **Testing Requirements**: N/A (CI gate)

* **T-L2.12 — `PrRef.number` fix + `trusted_authors` wiring checkpoint** [Effort: S]
  - **Description**: Two small, scoped items surfaced by the L1 code review (`loop-mode-code-review.md`) that L2 is the first phase positioned to close, since L2 is `PrRef`'s first real consumer: (a) L1 review finding L2 — `PrRef.number == 0` sentinel on a malformed `gh pr create` URL parse; L2's `sync_prior_prs`/`comment` are the first code to actually consume `PrRef.number` for something consequential (issue commenting, run_id correlation) — decide and implement per the L1 review's recommendation (raise `DeliveryError` in `deliverer.py`, or accept `int | None` and handle `None` explicitly in `loop.py`). (b) Confirm `trusted_authors` enforcement (this TRS's own new surface) is wired at the `claim()`/dispatch boundary, not just present in config — a dedicated test closes the loop the TRD's §4 Security section opened.
  - **Acceptance Criteria**:
    - [ ] `PrRef(number=0)` sentinel resolved one of the two ways the L1 review recommended; a test pins the chosen behavior
    - [ ] `trusted_authors` enforcement test exists and passes (already covered by T-L2.5/T-L2.6's test list — this task is the explicit checkpoint tying it back to the L1 review's ask)
    - [ ] BACKLOG.md updated to remove the now-closed L1-review action item; any items L2 does NOT close (e.g. L1's cache-semantics capture, the branch-safety exact-match nit) remain or are re-confirmed as still-open
  - **Files to Create/Modify**:
    - `src/atlas/deliverer.py` - `PrRef.number` fix (if that branch is chosen)
    - `src/atlas/loop.py` - `None`-handling (if that branch is chosen instead)
    - `docs/1_product_and_research/BACKLOG.md` - close/carry-forward bookkeeping
  - **Dependencies**: T-L2.7 (first real `PrRef.number` consumer)
  - **Testing Requirements**: Unit

* **T-L2.13 — Manual smoke tests: zero-touch delivery + planned lane + crash recovery (real systems)** [Effort: M]
  - **Description**: Off-CI, real external systems, same posture as L0's T-L0.8/T-L0.9 and L1's T-L1.1/T-L1.8. The three manual smoke tests from Testing Strategy, run against the real atlas GitHub repo.
  - **Acceptance Criteria**:
    - [ ] Zero-touch smoke (TRD-v3 §13 #5 literal proof): real `atlas:ready`+`wf:quick` issue → `atlas loop start` → real PR appears, zero further interaction; merge it → next tick writes `user_signal` + closes issue
    - [ ] Planned-lane smoke: real `wf:planned` issue → real plan-only PR with triad + Pending Decisions in the PR body
    - [ ] Crash-recovery drill: kill the loop mid-dispatch, restart, confirm reclaim
    - [ ] Any live-run findings folded back into context.md / BACKLOG.md
  - **Files to Create/Modify**: none (manual verification; may produce follow-up edits)
  - **Dependencies**: T-L2.9, T-L2.10, T-L2.11
  - **Testing Requirements**: E2E (manual, off-CI)

* **T-L2.14 — Update `STATUS.md`** [Effort: S]
  - **Description**: Record L2 completion (or code-complete/manual-pending status, matching L0/L1's precedent if T-L2.13 hasn't run yet), module coverage table update, "Next" pointed at Phase L3.
  - **Acceptance Criteria**:
    - [ ] `STATUS.md` reflects L2 status with the same density/style as L0/L1's entries
    - [ ] "Next" names Phase L3 as the immediate follow-up
    - [ ] `v3.1` delivery status recorded per TRD-v3 §11
  - **Files to Create/Modify**:
    - `STATUS.md` - phase completion entry
  - **Dependencies**: T-L2.11
  - **Testing Requirements**: N/A (documentation)
