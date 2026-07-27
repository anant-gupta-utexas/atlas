---
task: loop-mode-phase-L4
status: in-progress
phase: L4 (scale-out)
delivers: v3.3
---

## current: phase=code_complete, gate=none, next=T-L4.10 (needs a human operator session; T-L4.1–T-L4.9 and T-L4.6's code/config piece are done, tests green, ruff+mypy clean)

Detailed acceptance criteria for every task live in
[`loop-mode-phase-L4-tasks-detail.md`](./loop-mode-phase-L4-tasks-detail.md).
This file tracks checkbox-level progress only.

## Pre-work / blocking preconditions

**None outstanding.** All 14 decisions in
[`loop-mode-phase-L4-decisions.md`](./loop-mode-phase-L4-decisions.md) were
resolved by the maintainer on 2026-07-27 — every recommendation accepted as
written. The three that gated task start are settled:

- [x] **Decision #1** — `[loop].repos` config shape → **breaking
      `[[loop.repo]]` table array** (Option A). T-L4.1 hard-fails on the old
      flat-string shape with a migration message rather than silently
      ignoring it.
- [x] **Decision #4** — weekly report data source → **direct
      `SQLiteStorageAdapter` import** (Option A), not shelling `plumb run
      stats` (which truncates `run_id` and drops `dollar_cost`, verified
      against source). Mirrors L3's own Decision #3 precedent.
- [x] **Decision #14** — loop-state file location → **`~/.atlas/loop-state.json`**
      (Option B), the user-wide home `Config.load()` already reads
      `~/.atlas/config.toml` from. **Carries a mandatory one-time migration**
      (T-L4.5): the legacy file's `synced_pr_outcomes` list is the PR-outcome
      idempotency guard and must survive the move. *This decision shipped in
      the TRS's first draft with no recommendation; one was worked out and
      accepted afterward.*

Decisions #2, #3, #5–#13 were likewise accepted as recommended; this TRS's
task list already assumes them throughout.

**Two resolutions carry operator-visible behavior changes** that T-L4.11 must
document — both are correct behavior that reads as a bug if unannounced:
**#12** (the breaker trips faster in wall-clock time as `concurrency` rises)
and **#14** (`atlas loop status` becomes user-wide, not per-repo).

## Tasks

- [x] T-L4.1 — Reshape `[loop].repos` → `RepoTarget`; migrate this repo's own
      `.atlas.toml`
- [x] T-L4.2 — Route `tick()`/`run_forever()`/`reconcile_orphans` per-target
      instead of a single `repo_root`
- [x] T-L4.3 — `state.py`: per-run-keyed `.atlas/runs/<run_id>/current-run` +
      `list_current_runs()`; attended `atlas run`'s singleton path untouched
      (also wired `Pipeline(loop_mode=...)` + `assert_consistent(keyed=...)`,
      a gap the TRS's own text didn't fully spell out but `step()`'s shared
      code path required)
- [x] T-L4.4 — Lift `LoopConfig.__post_init__`'s `concurrency != 1` guard;
      add claim-race re-check
- [x] T-L4.5 — Concurrent dispatch: bounded thread pool inside `tick()`;
      `LoopState` mutation stays single-threaded post-pool; `BatchTickResult`
      wrapper
- [~] T-L4.6 — Second target repo: plumb repo's own `atlas:ready` backlog +
      checked-in `.claude/settings.json` allowlist. **Code/config done**
      (plumb added to this repo's local `.atlas.toml` `[[loop.repo]]`, with
      `trusted_authors` set since plumb is a **public** repo — verified live,
      2026-07-27 — making that allowlist load-bearing, not theoretical).
      **Operator-owned and still open**: seeding a real `atlas:ready` issue
      on plumb and checking in `.claude/settings.json` there — both are
      live/cross-repo actions the maintainer chose to do by hand rather than
      have the agent do autonomously. Note for whoever picks this up: atlas's
      *own* checkout has no checked-in `.claude/settings.json` to mirror
      (only a gitignored `.local.json`), so the TRS's "mirror this repo's
      own" framing has no real template — one needs to be authored from
      TRD-v3 §3.6's `--allowedTools` description directly.
- [x] T-L4.7 — `loop_report.py`: `build_weekly_report()` over direct
      `SQLiteStorageAdapter` access; lineage/engine joins
- [x] T-L4.8 — `atlas loop report [--since] [--format]` CLI command
- [x] T-L4.9 — Concurrency-safety invariant test (explicit, real thread pool;
      `tests/integration/test_loop_concurrency.py`, 20 consecutive runs)
- [ ] T-L4.10 — Manual smoke: real second-repo dispatch + real
      `concurrency=2` run (needs a human operator session; blocked on
      T-L4.6's operator-owned pieces above)
- [ ] T-L4.11 — Update STATUS.md, tag `v3.3`, close out the phase

## Exit criteria (TRD-v3 §13)

- [~] #11 — Second repo + concurrency: the plumb repo runs as a second
      target; `concurrency > 1` works with per-run state keys. Code:
      T-L4.1–T-L4.5 done. Proven live: T-L4.10 (not yet run).
- [~] #12 — Weekly report: a cost-per-landed-PR + intervention-rate summary,
      tokens-only for Codex. Code: T-L4.7–T-L4.8 done. Proven live: T-L4.10
      (not yet run).

## Carried-forward open manual checks (not this phase's tasks, tracked for visibility)

- [ ] T-L3.10 — Manual smoke: judge gate + retry against a real repo (needs
      a human operator session with a configured `PLUMB_JUDGE_PROVIDER`) —
      code-complete since 2026-07-26, not yet proven live. Running this
      before or alongside T-L4.10 is the sensible order (see plan's
      "Manual testing carried over" table).
- [ ] T-L3.11 — Update STATUS.md and close out Phase L3 (depends on T-L3.10;
      cosmetic only, does not block any L4 code path)

All of L0/L1/L2's own manual checks (T-L0.8, T-L0.9, T-L1.1, T-L1.8, T-L2.13)
were executed and closed 2026-07-27 (STATUS.md) — no residual dependency from
those phases.
