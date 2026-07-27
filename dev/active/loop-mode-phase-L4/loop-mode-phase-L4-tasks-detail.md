# Tasks (detail) — Loop Mode, Phase L4 TRS

Full flat task list (T-L4.1–T-L4.11) for TRD-v3 Phase L4 (Scale-out). Split
out from `loop-mode-phase-L4-plan.md` to keep that file under the repo's
800-line cap (matches L2/L3's own precedent). Ordered by execution sequence;
cross-task dependencies captured via each task's `Dependencies` field, not by
nesting. Progress checkboxes live in `loop-mode-phase-L4-tasks.md`.

---

### T-L4.1 — Reshape `[loop].repos` → `RepoTarget`; migrate this repo's own config

**[Task Name]** `config.py`: `RepoTarget` dataclass + `[[loop.repo]]` TOML
parsing [Effort: M]

- **Description**: Replace `LoopConfig.repos: tuple[str, ...]` with
  `tuple[RepoTarget, ...]` (`github`, `local_path`, `trusted_authors` — see
  Pending Decision #1/#11). Update `_parse_loop_config` to read
  `[[loop.repo]]` table-array entries instead of a flat `repos = [...]`
  list. Migrate this repo's own `.atlas.toml` to the new shape as part of
  this task, not a follow-up.
- **Acceptance Criteria**:
    - [ ] `RepoTarget(github: str, local_path: Path, trusted_authors: tuple[str, ...] = ())`
      is importable from `atlas.config`.
    - [ ] `Config.load()` parses `[[loop.repo]]` entries into
      `LoopConfig.repos: tuple[RepoTarget, ...]`.
    - [ ] Loading a `.atlas.toml` with the **old** `[loop] repos = [...]`
      flat-string shape fails loudly with a clear migration message naming
      the new `[[loop.repo]]` shape — not a silent no-op (Pending Decision
      #1).
    - [ ] `local_path` is resolved to an absolute path and validated to exist
      and be a git repo (`.git` present) at `Config.load()` time, not lazily
      at first dispatch — fails loudly at startup per the plan's Error
      Handling table.
    - [ ] This repo's own `.atlas.toml` is migrated to `[[loop.repo]]` form
      and `atlas loop status`/`atlas loop run --verbose` still start cleanly
      against it.
    - [ ] `LoopConfig.__post_init__` no longer raises on `concurrency != 1`
      (folded in here or in T-L4.4 — see that task's note).
- **Files to Create/Modify**:
    - `src/atlas/config.py` — `RepoTarget`, `_parse_loop_config` rewrite
    - `.atlas.toml` — migrated to `[[loop.repo]]`
    - `tests/unit/test_config.py` — new `RepoTarget`/table-array parsing
      cases, old-shape rejection case
- **Dependencies**: None
- **Testing Requirements**: Unit

---

### T-L4.2 — Route `tick()`/`run_forever()`/`reconcile_orphans` per-target

**[Task Name]** `loop.py`: replace single `repo_root` with `targets: Sequence[RepoTarget]`
[Effort: L]

- **Description**: Widen `tick()`, `run_forever()`, `reconcile_orphans`,
  `_sweep_orphaned_worktrees`'s callers, `sync_prior_prs`'s call sites, and
  `_pull_next_ready` (renamed `_pull_ready_batch` in T-L4.5, but its
  per-target routing lands here first as a single-issue version to keep this
  task reviewable independent of concurrency) to iterate `RepoTarget`s
  instead of a flat `repo_root: Path` + `repos: list[str]` pair. Every
  dispatch call (`run_one_shot`, `run_planned_first_pass`,
  `WorktreeManager(...)`, `StateStore(...)`) receives the winning issue's own
  `RepoTarget.local_path`, not a single shared value.
- **Acceptance Criteria**:
    - [ ] `tick(cfg, state, *, targets: Sequence[RepoTarget])` — no more
      separate `repos`/`repo_root` parameters.
    - [ ] An issue pulled from `targets[1]` (the second configured repo)
      dispatches its worktree/pipeline/state operations against
      `targets[1].local_path`, verified by a test with two distinct fake
      local paths and asserting which one `WorktreeManager`/`StateStore`
      were constructed with.
    - [ ] `reconcile_orphans` sweeps worktrees and resets stale
      `atlas:working` labels independently per target — a crash affecting
      one target's worktree does not touch another target's.
    - [ ] `cli.py::loop_run`/`loop_status` updated to pass `cfg.loop.repos`
      (now `tuple[RepoTarget, ...]`) as `targets`.
    - [ ] Full existing single-repo test suite passes unmodified in behavior
      (a `targets` tuple of length 1 produces byte-identical dispatch to
      today's single-`repo_root` code).
- **Files to Create/Modify**:
    - `src/atlas/loop.py` — `tick`, `run_forever`, `reconcile_orphans`,
      `_sweep_orphaned_worktrees` callers, `_pull_next_ready`
    - `src/atlas/cli.py` — `loop_run`/`loop_status` call sites
    - `tests/unit/test_loop.py` — per-target routing cases
- **Dependencies**: T-L4.1
- **Testing Requirements**: Unit, Integration

---

### T-L4.3 — `state.py`: per-run-keyed `current-run`

**[Task Name]** `StateStore`: additive keyed methods for concurrent loop runs
[Effort: L]

- **Description**: Add `write_current_run_keyed`, `list_current_runs`,
  `delete_current_run_keyed` to `StateStore`, writing/reading
  `.atlas/runs/<run_id>/current-run` (same positional body shape as the
  existing singleton file). Wire `run_one_shot`/`run_planned_first_pass` to
  call the keyed methods when dispatched from the loop (reusing the existing
  `loop_mode=True` plumbing, per Appendix A's `orchestrator.py` note).
  `_sweep_orphaned_worktrees` reads `list_current_runs()` instead of the
  singleton `read_current_run_with_worktree()` for its retain-check.
- **Acceptance Criteria**:
    - [ ] `write_current_run_keyed`/`list_current_runs`/
      `delete_current_run_keyed` implemented and unit-tested independent of
      `loop.py`.
    - [ ] Attended `atlas run`'s existing singleton methods
      (`write_current_run`/`read_current_run`/`read_current_run_with_worktree`/
      `delete_current_run`) are **untouched** — same file, same signatures,
      same call sites (Pending Decision #3). A grep-based regression test
      (mirroring `test_queue_gh.py`'s "loop.py never shells gh directly"
      pattern) asserts attended-mode code paths (`cli.py::run`/`resume`)
      never call the new keyed methods.
    - [ ] Two concurrent loop dispatches, each writing its own keyed
      `current-run` file, do not clobber each other — verified with two
      real (not faked) `StateStore` instances against a shared `repo_root`
      in a test.
    - [ ] `_sweep_orphaned_worktrees` retains every worktree named by any
      live keyed run, sweeps everything else — a test with 2 live keyed runs
      and 1 orphaned worktree confirms only the orphan is swept.
    - [ ] `reconcile_orphans`'s `at_startup=True` path still clears/ignores
      keyed runs the same way it clears the singleton today (nothing
      survives a process restart, concurrency or not).
- **Files to Create/Modify**:
    - `src/atlas/state.py` — new keyed methods
    - `src/atlas/loop.py` — `run_one_shot`/`run_planned_first_pass`/
      `_sweep_orphaned_worktrees` wired to keyed methods
    - `tests/unit/test_state.py` — new keyed-method cases
    - `tests/unit/test_loop.py` — orphan-sweep-with-multiple-live-runs case
- **Dependencies**: T-L4.2
- **Testing Requirements**: Unit, Integration

---

### T-L4.4 — Lift `concurrency != 1` guard; claim-race re-check

**[Task Name]** `config.py`/`queue_gh.py`/`loop.py`: enable `concurrency > 1`,
add claim confirmation [Effort: M]

- **Description**: Change `LoopConfig.__post_init__` to accept any
  `concurrency >= 1` (was: raise unless exactly `1`). Add `_claim_confirmed()`
  in `loop.py`, re-reading an issue's assignee immediately after
  `queue_gh.claim()` to detect a lost claim-race (two claimants both saw
  `atlas:ready` before either claimed).
- **Acceptance Criteria**:
    - [ ] `LoopConfig(concurrency=2)` no longer raises; `LoopConfig(concurrency=0)`
      or negative still raises `ValueError`.
    - [ ] `_claim_confirmed(issue, assignee)` returns `False` when a re-read
      shows a different assignee than the one this caller just set (mocked
      `queue_gh` race scenario), `True` on a clean claim.
    - [ ] A caller that loses the claim race does **not** relabel the issue
      back to `atlas:ready` and does not raise — it is skipped for this
      tick, logged at INFO.
    - [ ] Existing single-claimant behavior (today's only tested case) is
      unaffected — `_claim_confirmed` always returns `True` when there is no
      race.
- **Files to Create/Modify**:
    - `src/atlas/config.py` — `__post_init__` guard change
    - `src/atlas/loop.py` — `_claim_confirmed`
    - `tests/unit/test_config.py` — `concurrency` bound cases
    - `tests/unit/test_loop.py` — claim-race cases
- **Dependencies**: T-L4.1
- **Testing Requirements**: Unit

---

### T-L4.5 — Concurrent dispatch: bounded thread pool + `BatchTickResult`

**[Task Name]** `loop.py`: batch claim/dispatch via `ThreadPoolExecutor`,
single-threaded `LoopState` mutation [Effort: L]

- **Description**: Implement `_pull_ready_batch` (widens `_pull_next_ready`
  to return up to `cfg.loop.concurrency` `(RepoTarget, Issue)` pairs across
  all targets), `_dispatch_one` (pure function, no `LoopState` access, runs
  inside a pool worker), and rewrite `tick()`'s dispatch section per the
  plan's Algorithm & Logic Design pseudocode: claim the batch (using
  T-L4.4's race check), dispatch via `ThreadPoolExecutor(max_workers=cfg.loop.concurrency)`,
  collect pure outcomes via `as_completed()`, then — single-threaded, after
  the pool drains — apply every outcome to `state` and call
  `state.persist()` exactly once. Introduce `BatchTickResult(results: list[TickResult])`;
  `TickResult` itself is unchanged (Decision #9).

  **Also relocates the loop-state file** ([Decision
  #14](./loop-mode-phase-L4-decisions.md), resolved 2026-07-27):
  `LoopState.load_or_init`/`persist` stop taking a `repo_root` and read/write
  `~/.atlas/loop-state.json` — the user-wide home `Config.load()` already
  reads `~/.atlas/config.toml` from. Process-global budget/breaker state does
  not belong under any one target's checkout. **This requires a one-time
  migration, not a fresh start** — see the acceptance criteria below for why
  abandoning the old file is not safe.
- **Acceptance Criteria**:
    - [ ] `tick(cfg, state, *, targets)` returns `BatchTickResult`, not a bare
      `TickResult`.
    - [ ] At `concurrency=1` with one target, `BatchTickResult.results` has
      exactly 0 or 1 elements, matching today's `tick()` behavior exactly —
      the full existing `test_loop.py` suite ported to read `results[0]`
      (or assert `len(results) <= 1`) passes unmodified in assertions
      beyond that unwrap.
    - [ ] At `concurrency=3` with 3 ready issues across 2 targets, all 3
      dispatch and `BatchTickResult.results` has 3 entries.
    - [ ] `_dispatch_one` never reads or writes `state`/`LoopState` — enforced
      by a test asserting the function's signature/body has no `state`
      parameter at all (not just "doesn't call `persist`").
    - [ ] `state.persist()` is called **exactly once** per `tick()` call
      regardless of batch size (0, 1, or N dispatches) — asserted via a
      call-count spy in a test.
    - [ ] A claim-race loss (T-L4.4) within a batch does not prevent the
      other, successfully-claimed issues in the same batch from dispatching.
    - [ ] `LoopState.load_or_init()`/`persist()` read/write
      `~/.atlas/loop-state.json`, no longer `<repo_root>/.atlas/loop-state.json`
      (Decision #14).
    - [ ] **One-time migration:** on first run, if `~/.atlas/loop-state.json`
      is absent and a legacy `<repo_root>/.atlas/loop-state.json` exists, the
      legacy file is copied to the new location before any tick mutates it.
      **The `synced_pr_outcomes` list must survive the move** — it is the
      idempotency guard for PR-outcome scoring (`sync_prior_prs`'s
      `dedupe_key`), and losing it lets the next tick re-score an
      already-synced merged PR, regressing TRD-v3 §4 Reliability's
      idempotent-sync guarantee. A test asserts a populated legacy
      `synced_pr_outcomes` is present in the migrated file, not just that
      *some* file was created.
    - [ ] Migration is idempotent — running it twice does not overwrite a
      newer `~/.atlas/loop-state.json` with stale legacy contents.
- **Files to Create/Modify**:
    - `src/atlas/loop.py` — `_pull_ready_batch`, `_dispatch_one`,
      `BatchTickResult`, `tick()` rewrite
    - `src/atlas/loop_budget.py` — `_LOOP_STATE_RELATIVE_PATH` → user-wide
      path; `LoopState.load_or_init`/`persist` signatures drop `repo_root`;
      one-time legacy migration (Decision #14)
    - `src/atlas/cli.py` — `loop_run`/`loop_status` updated for
      `BatchTickResult` and the new state-file location
    - `tests/unit/test_loop.py` — batch dispatch, single-persist-per-tick,
      pure-worker cases
    - `tests/unit/test_loop_budget.py` — migration cases (including the
      `synced_pr_outcomes` survival assertion and idempotency)
- **Dependencies**: T-L4.2, T-L4.4
- **Testing Requirements**: Unit, Integration

---

### T-L4.6 — Second target repo: plumb's own backlog + allowlist

**[Task Name]** Onboard the plumb repo as a loop target [Effort: S, partly
operator]

- **Description**: Add `[[loop.repo]]` entry for the plumb repo to this
  repo's `.atlas.toml` (built on T-L4.1's migration). Seed the plumb repo's
  GitHub Issues with at least one `atlas:ready`-labeled issue from its own
  BACKLOG.md. Check in a `.claude/settings.json` allowlist in the plumb repo
  checkout, mirroring this repo's own (per TRD-v3 §3.6/§7's existing
  per-target-repo pattern).
- **Acceptance Criteria**:
    - [ ] plumb repo added to `.atlas.toml`'s `[[loop.repo]]` list with a
      valid `local_path`.
    - [ ] At least one real `atlas:ready` issue exists in the plumb repo for
      T-L4.10's manual smoke to pick up.
    - [ ] `.claude/settings.json` checked into the plumb repo's own checkout,
      not this repo's.
    - [ ] Missing-allowlist fail-closed behavior (from T-L4.2/T-L4.5's
      per-target dispatch) verified against this target specifically before
      the allowlist is added (prove the fail-closed path fires), then again
      after (prove dispatch proceeds).
- **Files to Create/Modify**:
    - `.atlas.toml` — plumb repo entry
    - `/Users/anant/PersonalProjects/plumb/.claude/settings.json` — new,
      checked into the plumb repo
- **Dependencies**: T-L4.1
- **Testing Requirements**: Manual (operator sets up the second repo's
  allowlist and seeds an issue; no unit test exercises a real second
  checkout)

---

### T-L4.7 — `loop_report.py`: weekly cost/intervention aggregation

**[Task Name]** Implement `build_weekly_report()` over direct plumb storage
access [Effort: L]

- **Description**: New module per the plan's Detailed Component Design.
  Constructs `SQLiteStorageAdapter` the same way `plumb/cli.py::_get_storage()`
  does internally (not importing that private helper — replicating its
  two-line construction, per Pending Decision #4's note), calls
  `.list_runs_with_counts(since=..., limit=...)`, groups by
  `parent_run_id`-lineage root, cross-references `user_signal` scores for
  "landed", reads the new `spans.attributes["engine"]` tag (T-L4.7 also adds
  the write side of this tag to `run_one_shot`, per the plan's "New: `engine`
  span attribute" subsection) for the per-engine split, and computes
  `cost_per_landed_pr_claude`/`tokens_per_landed_pr_codex`/`intervention_rate`
  per Pending Decisions #5/#6.
- **Acceptance Criteria**:
    - [ ] `build_weekly_report(cfg, since=...)` returns a `WeeklyReport` with
      correct `landed_prs`/`total_runs` counts against a faked
      `list_runs_with_counts()` fixture with a mix of landed/rejected/in-flight
      lineages.
    - [ ] A lineage with 2 runs (an original + one self-heal retry) counts
      once toward `intervention_count`, not twice, and once toward
      `landed_prs` if the final run in the lineage has an approved
      `user_signal`.
    - [ ] `cost_per_landed_pr_claude` is `None` (not `0.0`, not a
      `ZeroDivisionError`) when zero claude-engine runs landed in the
      window.
    - [ ] `tokens_per_landed_pr_codex` is computed independently of
      `cost_per_landed_pr_claude` — a window with only codex landings
      produces `cost_per_landed_pr_claude=None` and a real
      `tokens_per_landed_pr_codex` tuple, never a blended number.
    - [ ] A run whose `code_gen` span has no `"engine"` attribute (pre-L4
      data) is excluded from both per-engine aggregates but still counted in
      `total_runs`.
    - [ ] `run_one_shot` writes `attributes["engine"]` on its existing
      `record_span()` call for the judge-gate span — verified by a test
      asserting the attribute is present and correct for both `claude` and
      `codex` dispatch.
    - [ ] `SQLiteStorageAdapter` is constructed directly (not via
      `plumb.cli._get_storage`, a private helper) — verified by the absence
      of any import from `plumb.cli` in `loop_report.py`.
- **Files to Create/Modify**:
    - `src/atlas/loop_report.py` — new
    - `src/atlas/loop.py` — `run_one_shot`'s `record_span` call gains the
      `"engine"` attribute key
    - `tests/unit/test_loop_report.py` — new
- **Dependencies**: None (independent of T-L4.1–T-L4.6; can start in
  parallel, though T-L4.10's manual smoke benefits from a real second-repo
  dataset to report over)
- **Testing Requirements**: Unit (faked `RunSummaryRow`/score/attribute data)

---

### T-L4.8 — `atlas loop report` CLI command

**[Task Name]** Wire `loop_report.py` into the `atlas loop` Typer sub-app
[Effort: S]

- **Description**: `atlas loop report [--since 7d] [--format text|json]` —
  calls `build_weekly_report(cfg, since=parsed)`, prints via
  `format_report()` (text, default) or `dataclasses.asdict` (json). Reuses
  the `_resolve_since`-shaped duration parsing already established as a
  pattern in plumb's own CLI (`--since 7d`/`2w`/ISO date), reimplemented
  locally in atlas rather than imported from plumb's CLI module (which is
  not a library import target — see Pending Decision #4's note about
  `_get_storage` being private; the same applies to plumb's `_time_utils`
  internals).
- **Acceptance Criteria**:
    - [ ] `atlas loop report` with no args defaults to a sensible window
      (e.g. 7 days) and prints a human-readable summary.
    - [ ] `atlas loop report --since 30d --format json` prints valid JSON
      matching `WeeklyReport`'s fields.
    - [ ] Invalid `--since` value fails with a clear CLI error (`typer.Exit(1)`),
      not a stack trace.
    - [ ] Command is discoverable under `atlas loop --help`.
- **Files to Create/Modify**:
    - `src/atlas/cli.py` — `loop_app.command("report")`
    - `tests/unit/test_cli.py` — new command test cases
- **Dependencies**: T-L4.7
- **Testing Requirements**: Unit (Typer `CliRunner`)

---

### T-L4.9 — Concurrency-safety invariant test

**[Task Name]** Explicit real-thread-pool test proving no lost `LoopState`
updates [Effort: S]

- **Description**: Matches T-L3.8's "explicit invariant test, not incidental"
  precedent. Runs `tick()` at `concurrency=3` against 3 fake dispatches with
  artificial, staggered `time.sleep()`-style delays inside a **real**
  `ThreadPoolExecutor` (not a mocked pool — the point is to prove correctness
  under actual OS-level thread interleaving, not just under a
  single-threaded stand-in that trivially can't race).
- **Acceptance Criteria**:
    - [ ] Test asserts `state.runs_today`/`state.dollars_today` land on the
      exact expected sum across all 3 dispatches — no lost increments.
    - [ ] Test asserts `state.persist()` (spied) is called exactly once for
      the whole batch, not once per worker.
    - [ ] Test asserts `record_tick_outcome` is called once per dispatched
      issue (3 times for 3 dispatches), each with that issue's own
      `made_progress`/`error_signature`, not a single call describing "the
      batch."
    - [ ] Test runs reliably (no flakiness from timing) across at least 20
      consecutive local runs before being considered acceptance-complete —
      a race-condition test that only sometimes catches the race is not
      trustworthy CI signal.
- **Files to Create/Modify**:
    - `tests/unit/test_loop.py` (or a new
      `tests/integration/test_loop_concurrency.py` if the real-thread-pool
      setup doesn't fit the existing unit-test fixture shape) — new test
- **Dependencies**: T-L4.5
- **Testing Requirements**: Unit/Integration (real `ThreadPoolExecutor`, no
  mocked concurrency primitive)

---

### T-L4.10 — Manual smoke: real second-repo dispatch + real concurrency=2 run

**[Task Name]** Live proof against the real plumb repo and real `gh`/CLI
backends [Effort: M, manual]

- **Description**: Matches the standing pattern every phase before this one
  has needed (T-L0.8, T-L0.9, T-L1.8, T-L2.13, T-L3.10) — code-complete is
  not verified until run for real. Requires a human operator session with:
  the plumb repo onboarded (T-L4.6), `gh auth` scoped to both repos, and
  `concurrency=2` configured.
- **Acceptance Criteria**:
    - [ ] A real `atlas loop run` tick dispatches an issue from the plumb
      repo target into a worktree under the plumb repo's own `local_path`
      (not the atlas checkout) and opens a real PR against the plumb repo.
    - [ ] A real tick with 2 simultaneously-ready issues (one per target, or
      two in the same target) at `concurrency=2` dispatches both in
      parallel — evidenced by overlapping start/end timestamps in
      `.atlas/runs/<run_id>.log` for both runs.
    - [ ] No claim-race double-dispatch observed (or, if a race is
      deliberately provoked, `_claim_confirmed` is observed skipping the
      loser cleanly).
    - [ ] `atlas loop report --since 1d` run after the above produces a
      report reflecting the real dispatches (non-zero `total_runs`, correct
      per-engine attribution if both `claude` and `codex` were exercised).
    - [ ] Findings (defects found live, same as every prior phase's field
      pass) documented in this file's checklist / a field-findings note,
      following L2/L3's own precedent of naming what CI couldn't have
      caught.
- **Files to Create/Modify**: None (this is a live-execution task; any
  defects found produce follow-up fixes filed against the specific task
  above they belong to, matching L2's "eight defects found by the field
  pass" precedent of fixing forward rather than retrofitting the task list)
- **Dependencies**: T-L4.6, T-L4.9 (all code must be in place first); benefits
  from T-L3.10 having run first (see plan's "Manual testing carried over"
  table)
- **Testing Requirements**: Manual (real repos, real `gh`, real CLI backends)

---

### T-L4.11 — Update STATUS.md, tag `v3.3`, close out the phase

**[Task Name]** Phase close-out [Effort: S]

- **Description**: Same shape as T-L3.11/L2's own close-out. Update
  `STATUS.md`'s `status`/`next_gate`/`blocked_on` frontmatter and body,
  recording what T-L4.10 found. Tag `v3.3` locally (matching the "tags exist
  locally only, pushed on request" convention `STATUS.md` already documents
  for `v2.2`/`v3.1`). Move this TRS's triad from `dev/active/` to
  `dev/archive/loop-mode-phase-L4/`, matching L0/L1/L2's own archival
  precedent.
- **Acceptance Criteria**:
    - [ ] `STATUS.md` reflects L4/`v3.3` as shipped, naming T-L4.10's actual
      findings (or "none found" if genuinely clean — but say so explicitly,
      don't omit the section).
    - [ ] `pyproject.toml` version bumped and a local `v3.3` tag created.
    - [ ] `dev/active/loop-mode-phase-L4/` moved to `dev/archive/loop-mode-phase-L4/`.
    - [ ] BACKLOG.md updated to remove the "Phase L4 — scale-out" entry (it
      is now shipped, not pending) and to add any follow-ups T-L4.10 or the
      Decisions surfaced (e.g. Decision #5's Option B — a label-transition
      log enabling the broader human-intervention metric; Decision #10's
      untuned concurrency ceiling; a Codex price table).
    - [ ] **The two operator-visible behavior changes are documented, not
      left to be discovered live** — both are correct behavior that reads as
      a bug if unannounced:
        - [ ] **Decision #12** — raising `[loop].concurrency` makes the
          circuit breaker trip faster in wall-clock time for the same
          `no_progress_limit`/`identical_error_limit`, because
          `consecutive_no_progress` increments per failed *issue*, not per
          tick. Documented where those config keys are explained
          (`docs/3_guides/core_concepts.md` and/or the `[loop]` block's own
          doc comment).
        - [ ] **Decision #14** — `atlas loop status` is now **user-wide**,
          not per-repo: the same budget/breaker state is reported from any
          repo, and the state file moved to `~/.atlas/loop-state.json`.
          Documented in the same place, plus a STATUS.md note since it
          changes where an operator looks for the file.
- **Files to Create/Modify**:
    - `STATUS.md`
    - `pyproject.toml`
    - `docs/1_product_and_research/BACKLOG.md`
    - `docs/3_guides/core_concepts.md` — the two operator-visible changes
    - `dev/archive/loop-mode-phase-L4/` (moved from `dev/active/`)
- **Dependencies**: T-L4.10
- **Testing Requirements**: None (documentation/release task)
