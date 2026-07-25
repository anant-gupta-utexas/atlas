# Tasks — Loop Mode, Phase L2 TRS

Progress checklist. Source-of-truth for design is
[`loop-mode-phase-L2-plan.md`](./loop-mode-phase-L2-plan.md); full task detail lives
in [`loop-mode-phase-L2-tasks-detail.md`](./loop-mode-phase-L2-tasks-detail.md);
Pending Decisions live in
[`loop-mode-phase-L2-decisions.md`](./loop-mode-phase-L2-decisions.md). Reference
notes live in [`loop-mode-phase-L2-context.md`](./loop-mode-phase-L2-context.md).

## Current

```
phase: not started — TRS authored, no implementation yet
gate:  none
next:  T-L2.1 (baseline verification + gh fixture capture)
```

## Status — no blocking dependency

L0 + L1 are code-complete (301 tests pass, 1 xfail, 96% coverage per STATUS.md
2026-07-24) and currently in code review (verdict: Approve, one Medium + four
Low/Nit findings, none blocking — see
[`loop-mode-phase-L1/loop-mode-code-review.md`](../loop-mode-phase-L1/loop-mode-code-review.md)).
TRD-v3 §14 lists L2's dependency as simply "L1." Per Decision #1 (decisions file),
L2 does not block on L1's own still-open manual checks (T-L1.1, T-L1.8) — but inherits
their risk for Codex-lane token-cost trust specifically.

## Tasks (flat — Phase L2 only, no sub-phases)

- [ ] **T-L2.1** — Baseline verification + `gh` fixture capture: re-confirm suite state; capture real `gh --json` output (issue list, pr view × 3 outcome states); pin `gh --version`
- [ ] **T-L2.2** — `queue_gh.py`: the `gh` adapter (`list_ready`/`claim`/`deliver_pr`/`comment`/`sync`/`relabel`), timeout-wrapped, list-form argv only, grep-enforced sole-`gh`-caller test
- [ ] **T-L2.3** — `[loop]` config: `LoopConfig` dataclass + `Config.loop` field + TOML parsing; `concurrency != 1` raises
- [ ] **T-L2.4** — `triage.py`: label-wins-else-classify router; both-labels-present → `planned`; unparseable classify → `planned`
- [ ] **T-L2.5** — `loop.py`: `tick()` core state machine (sync → breaker → budget → pull → trust-check → triage → claim → dispatch → comment → persist); `run_one_shot()`/`run_planned_first_pass()`/`build_issue_prompt()`; promote `cli.py::_make_pipeline` to shared `make_pipeline()`
- [ ] **T-L2.6** — `loop.py`: budgets + circuit breaker (`LoopState`, `budget_exhausted`, `breaker_open`, `record_tick_outcome`, day rollover)
- [ ] **T-L2.7** — `loop.py`: `sync_prior_prs()` + idempotent scoring via `PlumbIO.reopen_run()`; relabel + close-on-merge
- [ ] **T-L2.8** — `loop.py`: `run_forever()` + `reconcile_orphans()`; outer exception safety net
- [ ] **T-L2.9** — `atlas loop` CLI surface: `run`/`start`/`stop`/`status`/`attach` (tmux wrapper for the detached three)
- [ ] **T-L2.10** — Integration tests: full-tick + zero-touch smoke (faked `gh`/`Pipeline`)
- [ ] **T-L2.11** — Lint/type/coverage gate
- [ ] **T-L2.12** — `PrRef.number` fix (L1 code-review finding L2) + `trusted_authors` wiring checkpoint
- [ ] **T-L2.13** — Manual smoke tests (off-CI): zero-touch delivery, planned lane, crash recovery — real GitHub repo
- [ ] **T-L2.14** — Update `STATUS.md`

## Exit criteria (TRD-v3 §13 items 5–8 — copied for tracking)

- [ ] **§13 #5** — Zero-touch delivery (headline): one `atlas:ready` issue → `atlas loop start` → a PR appears (`Closes #n`) with a plumb `run_id` comment, zero keystrokes between labeling and reviewing; merging writes `user_signal` + closes the issue. Cost half requires plumb P1-a — L2 reports tokens, not dollars, until then.
- [ ] **§13 #6** — Two-lane routing works: `wf:quick` → one PR; `wf:planned` → plan-only PR (triad + Pending Decisions) and the loop stops.
- [ ] **§13 #7** — Budgets & breaker: per-day cost/run caps halt dispatch; breaker opens on no-progress/identical-error thresholds, resumes after cooldown.
- [ ] **§13 #8** — Crash recovery: killing the loop mid-run and restarting resets the stranded issue and prunes its worktree.

## Resolved decisions (see decisions file for full rationale — 18 total)

- [x] **#1 — L2 doesn't block on L1's T-L1.1/T-L1.8**, but Codex-lane token data isn't trustworthy until T-L1.1 closes. Binding on T-L2.13's scope note.
- [x] **#2 — Planned lane is first-pass-only** (plan-only PR + stop; no task-by-task loop). Binding on T-L2.5.
- [x] **#3 — L2 closes only the `PrRef.number` L1-review finding.** Binding on T-L2.12.
- [x] **#4 — `triage.py` is a separate file.** Binding on T-L2.4.
- [x] **#5 — `claim()` is one combined `gh issue edit` call.** Binding on T-L2.2.
- [x] **#6 — `LoopState` is a new flat JSON file**, not folded into `StateStore`. Binding on T-L2.6.
- [x] **#7 — No `queue_gh.preflight()`.** Binding on T-L2.2.
- [x] **#8 — `sync_prior_prs()` reuses `PlumbIO.reopen_run()`; no new `PlumbIO` method.** Binding on T-L2.7.
- [x] **#9 — Multi-repo tie-breaking: `cfg.repos` order, then `gh`'s oldest-first.** Binding on T-L2.5 (inert until L4).
- [x] **#10 — `build_issue_prompt()` = title + body + scope preamble; no `context_hint` lookup.** Binding on T-L2.5.
- [x] **#11 — `cli.py::_make_pipeline` promoted to shared `make_pipeline()`.** Binding on T-L2.5.
- [x] **#12 — `relabel(state="done")` bundles `gh issue close`.** Binding on T-L2.2.
- [x] **#13 — Triage classifier dispatches via `CliBackend` directly**, bypassing `SubprocessStageRunner`. Binding on T-L2.4.
- [x] **#14 — `tick()` posts a comment on failure too, not just success.** Binding on T-L2.5.
- [x] **#15 — "`loop.py` never shells `gh` directly" is grep-enforced.** Binding on T-L2.2.
- [x] **#16 — Untrusted-author issues are skipped, not relabeled to an error state.** Binding on T-L2.5.
- [x] **#17 — Triage classifier cost counts toward `max_dollars_per_day`, not `max_runs_per_day`.** Binding on T-L2.6.
- [x] **#18 — `run_forever()` wraps `tick()` in a bare `except Exception` safety net.** Binding on T-L2.8.

*(All 18 marked "resolved" in the sense that this TRS commits to a recommended
option — several are maintainer-reviewable judgment calls, not closed investigations
the way L1's Codex-schema verification was. See the decisions file's "Headline items
requiring maintainer sign-off" callout in the plan for the five most consequential:
#1, #2, #8, #11, #13.)*

## Notes for implementation

- **This TRS's Pending Decisions are unusually numerous (18) because L2 is the first
  phase to actually wire L0/L1's primitives together** — nearly every wiring point
  (how sync re-attaches to plumb, how the classifier dispatches, how `_make_pipeline`
  gets shared, what a failed run's operator-visible signal is) had no precedent to
  follow in this codebase. Read the decisions file before writing code, not after —
  several tasks (T-L2.5, T-L2.7, T-L2.12) are underspecified in the plan's pseudocode
  without it.
- **T-L2.5 is the XL task and the one most likely to reveal a wrong assumption.**
  If `make_pipeline()`'s extraction from `cli.py::_make_pipeline` turns out messier
  than Decision #11 assumes (e.g. a hidden coupling to Typer's option-parsing), stop
  and reconcile rather than duplicating the construction logic in `loop.py` — the
  whole point of the shared function is that `loop.py` and `cli.py::run` can't drift.
- **The L1 code review is required reading, not optional context.** It flagged a
  Medium finding (Codex token cache semantics) that L2's Codex-lane dispatch directly
  inherits, plus the `PrRef.number` finding this TRS's T-L2.12 explicitly closes.
- **Budget/breaker correctness is security-adjacent even though Security
  Considerations frames it as a safety mechanism** — a broken breaker or budget cap
  is the difference between "the loop dispatched into an API error 40 times overnight"
  and "the loop caught it on attempt 5." Test this thoroughly; it's the one place a
  logic bug has an unbounded-cost blast radius.
- **Sync-before-breaker/budget-check is a deliberate ordering, not an oversight** —
  see the plan's Algorithm & Logic Design note on why. Don't "simplify" this during
  implementation by moving the sync call after the budget check.
- **No plumb schema change, ever, in this phase.** If implementation finds itself
  wanting a new `PlumbIO` method or a new table, that's a signal to stop and re-read
  Decision #8 — the intended answer is almost certainly "reuse `reopen_run`," not
  "add something to plumb."

## Implementation notes (post-hoc — fill in after work is done)

*(Empty — implementation has not started. Fill in per L0/L1's precedent: what
landed together vs. separately, any bugs found and fixed in-scope, what was and
wasn't run this session, final coverage numbers.)*
