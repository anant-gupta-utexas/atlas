# Context — Loop Mode, Phase L4 TRS

Reference notes for anyone picking up this work cold.

## Status at TRS authoring time (2026-07-27)

Per `STATUS.md`: v3.1 shipped and verified live; Phase L3 (self-healing +
routing, `v3.2`) is **code-complete** (T-L3.2–T-L3.9, 520 passed/1 xfailed,
`ruff`/`mypy --strict` clean, `judge_gate.py` 86%/`self_heal.py` 100%/
`loop.py` 90% coverage) but its own manual smoke (T-L3.10) and STATUS.md
close-out (T-L3.11) are open, both needing a human operator session with a
configured `PLUMB_JUDGE_PROVIDER` — **not a code gap**.

**This TRS proceeds against that state deliberately**, mirroring exactly how
L3's own TRS was authored while L2's T-L2.13 was open (see
`dev/active/loop-mode-phase-L3/loop-mode-phase-L3-plan.md`'s own "Manual
testing carried over" section) — the pattern is now established across
three consecutive phases (L1→L2, L2→L3, L3→L4), not a one-off judgment call.
See [Pending Decision #13](./loop-mode-phase-L4-decisions.md).

## Key files

### Source-of-truth docs (read first, in order)

- [`docs/2_architecture/TRD-v3.md`](../../../docs/2_architecture/TRD-v3.md) —
  the phase contract this TRS details. §13 items 11–12 (the binding exit
  criteria), §14 Phase L4 (engineering scope summary — three bullets: second
  repo, concurrency, weekly report), Appendix A (seam inventory —
  `state.py`: "Modify (L4 only) ... Untouched in v3.0–v3.2").
- [`docs/1_product_and_research/loop-mode-design.md`](../../../docs/1_product_and_research/loop-mode-design.md) —
  §5 Phase L4 section: "Add the plumb repo as a second target... raise
  `concurrency > 1`... weekly `plumb run stats` → an external report."
  Shorter and less corrected-in-place than the TRD for this phase (L4 wasn't
  reached by the L0–L2 field pass), so it carries less authoritative weight
  than TRD-v3 where the two differ — TRD-v3 wins per this doc's own §"Scope
  of this doc vs. the TRD" note.
- [`STATUS.md`](../../../STATUS.md) — Phase L2/L3 entries, the exact shipped
  module list (`loop.py`, `loop_budget.py`, `pipeline_factory.py`,
  `triage.py`, `queue_gh.py`, `judge_gate.py`, `self_heal.py`) this TRS
  builds on top of, and the "tags exist locally only" convention T-L4.11
  follows for `v3.3`.
- [`dev/active/loop-mode-phase-L3/`](../loop-mode-phase-L3/) — the L3 TRS
  triad. This TRS follows its task-numbering convention (`T-L4.N`), its
  plan/decisions/tasks-detail/tasks file split (adopted here for the same
  800-line-cap reason), and its "what this TRS does NOT cover" discipline.
- **Sibling `plumb` repo source** (path-installed dependency, confirmed
  present at `/Users/anant/PersonalProjects/plumb` at authoring time) — this
  TRS's weekly-report design was verified directly against:
  - `plumb/cli.py:52-74` — `_get_storage()`, the CLI's own (private)
    `SQLiteStorageAdapter` construction, which `loop_report.py` replicates
    rather than importing.
  - `plumb/cli.py:109-151` — `run_stats()`, confirming the JSON output
    truncates `run_id` to 8 chars (`s.run_id[:8]`) and never includes
    `dollar_cost` in `_RUN_STATS_COLUMNS`/the row dict — the reason this TRS
    does **not** shell `plumb run stats` (Pending Decision #4).
  - `plumb/core/entities.py:242-282` — `RunSummaryRow`, the underlying
    dataclass `storage.list_runs_with_counts()` returns, confirming it
    **does** carry full `run_id`, `dollar_cost`, `tokens_in`/`tokens_out`,
    `parent_run_id` — the CLI's formatting layer drops them, the storage
    layer doesn't.
  - `plumb/core/ports.py:97` — `StorageReader.list_runs_with_counts(since=...,
    task_id=..., kind=..., limit=...)` — the Protocol `loop_report.py` calls.
  - `plumb/core/stats.py` — pure McNemar/Benjamini-Hochberg statistical
    helpers, unrelated to this phase; confirmed there is no existing
    cost-per-PR/intervention-rate aggregation anywhere in plumb to reuse —
    this TRS's aggregation logic in `loop_report.py` is genuinely new, not a
    thin wrapper over an existing plumb function.

### TRS itself (this directory)

- [`loop-mode-phase-L4-plan.md`](./loop-mode-phase-L4-plan.md) — design
  (Phase Summary through Performance Considerations), Phase Deliverables, a
  short pointer to Pending Decisions and the tasks-detail file.
- [`loop-mode-phase-L4-decisions.md`](./loop-mode-phase-L4-decisions.md) —
  all 14 Pending Decisions & Clarifications with full rationale.
- [`loop-mode-phase-L4-tasks-detail.md`](./loop-mode-phase-L4-tasks-detail.md) —
  the full flat task list (T-L4.1–T-L4.11) with acceptance criteria, files,
  dependencies, testing requirements.
- [`loop-mode-phase-L4-tasks.md`](./loop-mode-phase-L4-tasks.md) — checkbox
  progress tracking, including the carried-forward L3 manual-check list and
  the decision-resolution record (all 14 settled 2026-07-27 — nothing gates
  T-L4.1).

### Code targets

**New:**

- `src/atlas/loop_report.py` — `build_weekly_report()` (direct
  `SQLiteStorageAdapter` access), `format_report()`, `WeeklyReport` dataclass
  (T-L4.7).
- `tests/unit/test_loop_report.py` — new test file.
- A new `tests/integration/test_loop_concurrency.py` (or a section of
  `test_loop.py` — see T-L4.9's note) for the real-thread-pool invariant
  test.
- `/Users/anant/PersonalProjects/plumb/.claude/settings.json` — second
  target's checked-in allowlist (T-L4.6), lives in the **plumb** repo, not
  this one.

**Modified:**

- `src/atlas/config.py` — `RepoTarget` dataclass; `LoopConfig.repos` reshaped
  to `tuple[RepoTarget, ...]`; `[[loop.repo]]` TOML parsing; lifted
  `concurrency != 1` guard (T-L4.1, T-L4.4).
- `src/atlas/loop.py` — per-target dispatch routing (T-L4.2); `_claim_confirmed`
  (T-L4.4); `_pull_ready_batch`/`_dispatch_one`/`BatchTickResult`/rewritten
  `tick()` (T-L4.5); `record_span(..., attributes={"engine": ...})` on the
  existing judge-gate span call site (T-L4.7).
- `src/atlas/state.py` — new keyed `StateStore` methods, additive; legacy
  singleton methods untouched (T-L4.3).
- `src/atlas/loop_budget.py` — `_LOOP_STATE_RELATIVE_PATH` → user-wide
  `~/.atlas/loop-state.json`; `LoopState.load_or_init`/`persist` drop their
  `repo_root` parameter; one-time legacy-file migration preserving
  `synced_pr_outcomes` (Decision #14, T-L4.5).
- `src/atlas/cli.py` — `loop_run`/`loop_status` updated for `targets`/
  `BatchTickResult`; new `atlas loop report` command (T-L4.2, T-L4.5, T-L4.8).
- `.atlas.toml` — migrated to `[[loop.repo]]`; plumb repo entry added
  (T-L4.1, T-L4.6).
- `STATUS.md`, `pyproject.toml`, `docs/1_product_and_research/BACKLOG.md` —
  phase close-out (T-L4.11).

**Unchanged (verify, don't touch):**

- `src/atlas/orchestrator.py` (`Pipeline`) — per Appendix A's standing rule.
  Concurrency is expressed above the `Pipeline` boundary; each concurrent
  dispatch constructs its own independent `Pipeline` instance exactly as
  today's single-dispatch code does.
- `src/atlas/judge_gate.py`, `src/atlas/self_heal.py` — reused as-is, per
  issue, per repo. Not touched by concurrency or multi-repo routing.
- `src/atlas/triage.py`, `src/atlas/pipeline_factory.py`,
  `src/atlas/deliverer.py`, `src/atlas/queue_gh.py`'s own method signatures
  — reused as-is; L4 adds a caller-side claim-race re-check (T-L4.4) without
  changing `queue_gh.claim()`'s own signature or implementation.
- `src/atlas/plumb_io.py` — no new method; the `engine` tag rides the
  existing `attributes` parameter on `record_span()`.

If implementation finds any "unchanged" file genuinely needs editing beyond
what's listed here, that's a signal the design has drifted from this TRS —
pause and reconcile, per Appendix A's standing instruction, generalized.

## Decisions made (during this TRS's authoring)

Full text in
[`loop-mode-phase-L4-decisions.md`](./loop-mode-phase-L4-decisions.md). One-line
index reproduced in the plan file's Pending Decisions section — not
duplicated a third time here.

**All 14 were resolved by the maintainer on 2026-07-27**, every
recommendation accepted as written; nothing blocks T-L4.1. The options and
trade-offs are preserved in the decisions file rather than trimmed to the
winner, matching TRD-v3's own "annotate, don't silently rewrite" discipline.

Two things a reader picking this up cold should know:

1. **Decision #14 changed between authoring and resolution.** It shipped in
   the first draft as *"no recommendation given — a genuine design fork"*;
   the accepted answer is `~/.atlas/loop-state.json` (Option B) **with a
   mandatory one-time migration** of the legacy per-repo file, because its
   `synced_pr_outcomes` list is the PR-outcome idempotency guard
   (`sync_prior_prs`'s `dedupe_key`). Abandoning it would regress TRD-v3 §4
   Reliability's idempotent-sync guarantee — a shipped behavior, not a
   nice-to-have.
2. **Two resolutions are operator-visible** and are T-L4.11 acceptance
   criteria rather than prose: **#12** (breaker trips faster in wall-clock
   time as `concurrency` rises, since `consecutive_no_progress` counts per
   failed *issue*) and **#14** (`atlas loop status` becomes user-wide, not
   per-repo). Both are correct behavior that reads as a bug if unannounced.

## Verified plumb/config surface used by this TRS (read 2026-07-27, against sibling repo + this repo's own source)

- **`plumb.adapters.storage_sqlite.SQLiteStorageAdapter`** — confirmed
  importable; constructed with `(db_path, clock=...)`, used as a context
  manager (`with storage: ...`), matching `plumb/cli.py::_get_storage()`'s
  own usage.
- **`plumb.config.get_settings()`/`ensure_data_dir()`** — confirmed present,
  used by `_get_storage()` to locate `plumb.db`; `loop_report.py` calls
  these directly rather than the private `_get_storage()` function itself.
- **`storage.list_runs_with_counts(since=..., task_id=..., kind=..., limit=...)
  -> list[RunSummaryRow]`** — confirmed in `plumb/core/ports.py`'s
  `StorageReader` Protocol; `RunSummaryRow`'s full field list confirmed in
  `plumb/core/entities.py:242-282`.
- **`plumb/cli.py`'s `run_stats()` CLI command** — confirmed to truncate
  `run_id` and drop `dollar_cost` in its JSON/table/csv output — the
  concrete, source-verified reason this TRS bypasses it (Pending Decision #4).
- **This repo's own `config.py`/`state.py`/`loop.py`/`loop_budget.py`
  (`src/atlas/`)** — read in full at authoring time; every code-location
  reference in the plan/decisions/tasks-detail files above (line numbers,
  method names) is verified against the actual source as it stood
  2026-07-27, not inferred from the TRD's prose alone. This includes
  confirming `LoopConfig.__post_init__`'s hard `concurrency != 1` guard,
  `StateStore`'s singleton `.atlas/current-run` file, and `tick()`'s
  single-issue-per-call shape — all three are real, present-tense gaps this
  TRS closes, not hypothetical ones.

## Integration points (new in L4)

| Direction | Surface | Failure mode | Test coverage |
| --- | --- | --- | --- |
| `loop.py::tick()` → `loop.py::_dispatch_one()` (thread pool) | In-process call, N ≤ concurrency per tick | Worker exception surfaces via `future.result()`; caught per-outcome, does not crash the batch | Unit + real-pool integration (T-L4.5, T-L4.9) |
| `loop.py::_claim_confirmed()` → `queue_gh` (re-read) | In-process call | Lost race → skip, no relabel, no raise | Unit (T-L4.4) |
| `state.py::StateStore.list_current_runs()` → `.atlas/runs/*/current-run` | File I/O, glob read | Missing/corrupt file → fail-safe skip (mirrors today's singleton corruption handling), not a sweep-everything crash | Unit (T-L4.3) |
| `loop_report.py` → `plumb.adapters.storage_sqlite.SQLiteStorageAdapter` | In-process import, sibling repo | Missing/misconfigured `plumb_db_path` → surfaces as whatever `SQLiteStorageAdapter`'s own constructor raises, not swallowed | Unit (T-L4.7) |
| `run_one_shot()` → `record_span(attributes={"engine": ...})` | In-process call, existing call site | N/A — additive dict key, no new failure mode | Unit (T-L4.7) |
| `cli.py::loop report` → `loop_report.build_weekly_report()` | Typer call | Invalid `--since` → `typer.Exit(1)` with a clear message | Unit (T-L4.8) |

## Where this TRS's task list maps to TRD-v3 §14 Phase L4 scope bullets

| TRD-v3 §14 Phase L4 bullet | This TRS's task |
| --- | --- |
| "Add the plumb repo as a second target (its own backlog → issues)" | T-L4.1, T-L4.2, T-L4.6 |
| "Concurrency > 1: lift the `.atlas/current-run` single-run assumption via per-run state keys (Appendix A); bound by a semaphore at `[loop].concurrency`" | T-L4.3, T-L4.4, T-L4.5, T-L4.9 |
| "Weekly `plumb run stats` → a cost-per-landed-PR + intervention-rate report" | T-L4.7, T-L4.8 |
| *(implicit — every phase closes with a STATUS.md update + live proof)* | T-L4.10, T-L4.11 |
