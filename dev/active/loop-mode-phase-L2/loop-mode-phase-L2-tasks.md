# Tasks — Loop Mode, Phase L2 TRS

Progress checklist. Source-of-truth for design is
[`loop-mode-phase-L2-plan.md`](./loop-mode-phase-L2-plan.md); full task detail lives
in [`loop-mode-phase-L2-tasks-detail.md`](./loop-mode-phase-L2-tasks-detail.md);
Pending Decisions live in
[`loop-mode-phase-L2-decisions.md`](./loop-mode-phase-L2-decisions.md). Reference
notes live in [`loop-mode-phase-L2-context.md`](./loop-mode-phase-L2-context.md).

## Current

```
phase: in progress — T-L2.1 through T-L2.8 code-complete + tested; T-L2.9 in progress
gate:  none
next:  T-L2.9 (atlas loop CLI surface) — resume at the exact point noted below
```

### Resume point (2026-07-25)

Stopped mid-T-L2.9, right after re-reading `cli.py`'s existing command pattern
(the `hook` command, `_slugify` helper) to match style before writing the
`loop_app` Typer sub-app. **Nothing has been written into `cli.py` for T-L2.9
yet** — the `loop_app` sub-app, `loop run/start/stop/status/attach` commands
do not exist. Next action: add them per the plan's Detailed Component Design
(`loop-mode-phase-L2-plan.md` "cli.py — additions" section) and T-L2.9's
acceptance criteria in `loop-mode-phase-L2-tasks-detail.md`.

**Load-bearing implementation note not yet in the plan text:** `loop.py`
imports `make_pipeline` from `cli.py` (module-level `from atlas.cli import
make_pipeline`). This means `cli.py` must NOT import `atlas.loop` at module
level — it would create a circular import. Import `atlas.loop` lazily inside
each `loop_run`/`loop_start`/`loop_stop`/`loop_status`/`loop_attach` command
function body (same lazy-import pattern `cli.py` already uses for
`atlas.post_commit_hook` in the `hook` command, `cli.py:295`).

Everything through T-L2.8 is implemented, tested, and passing (381 passed,
1 xfailed as of this checkpoint — up from the L0/L1 baseline of 301/1). See
the Implementation notes section at the bottom of this file for the full
per-task rundown of what shipped and what's left.

## Status — no blocking dependency

L0 + L1 are code-complete (301 tests pass, 1 xfail, 96% coverage per STATUS.md
2026-07-24) and currently in code review (verdict: Approve, one Medium + four
Low/Nit findings, none blocking — see
[`loop-mode-phase-L1/loop-mode-code-review.md`](../loop-mode-phase-L1/loop-mode-code-review.md)).
TRD-v3 §14 lists L2's dependency as simply "L1." Per Decision #1 (decisions file),
L2 does not block on L1's own still-open manual checks (T-L1.1, T-L1.8) — but inherits
their risk for Codex-lane token-cost trust specifically.

## Tasks (flat — Phase L2 only, no sub-phases)

- [x] **T-L2.1** — Baseline verification + `gh` fixture capture: re-confirm suite state; capture real `gh --json` output (issue list, pr view × 3 outcome states); pin `gh --version`
- [x] **T-L2.2** — `queue_gh.py`: the `gh` adapter (`list_ready`/`claim`/`deliver_pr`/`comment`/`sync`/`relabel`), timeout-wrapped, list-form argv only, grep-enforced sole-`gh`-caller test
- [x] **T-L2.3** — `[loop]` config: `LoopConfig` dataclass + `Config.loop` field + TOML parsing; `concurrency != 1` raises
- [x] **T-L2.4** — `triage.py`: label-wins-else-classify router; both-labels-present → `planned`; unparseable classify → `planned`
- [x] **T-L2.5** — `loop.py`: `tick()` core state machine (sync → breaker → budget → pull → trust-check → triage → claim → dispatch → comment → persist); `run_one_shot()`/`run_planned_first_pass()`/`build_issue_prompt()`; promote `cli.py::_make_pipeline` to shared `make_pipeline()`
- [x] **T-L2.6** — `loop.py`: budgets + circuit breaker (`LoopState`, `budget_exhausted`, `breaker_open`, `record_tick_outcome`, day rollover)
- [x] **T-L2.7** — `loop.py`: `sync_prior_prs()` + idempotent scoring via `PlumbIO.reopen_run()`; relabel + close-on-merge
- [x] **T-L2.8** — `loop.py`: `run_forever()` + `reconcile_orphans()`; outer exception safety net
- [ ] **T-L2.9** — `atlas loop` CLI surface: `run`/`start`/`stop`/`status`/`attach` (tmux wrapper for the detached three) — **IN PROGRESS, not started writing code yet; see Resume point above**
- [ ] **T-L2.10** — Integration tests: full-tick + zero-touch smoke (faked `gh`/`Pipeline`)
- [ ] **T-L2.11** — Lint/type/coverage gate
- [ ] **T-L2.12** — `PrRef.number` fix (L1 code-review finding L2) + `trusted_authors` wiring checkpoint — **note: `trusted_authors` enforcement itself is already implemented + tested (`loop._pull_next_ready`, `test_trusted_authors_*` in test_loop.py) as part of T-L2.5/T-L2.6; T-L2.12's remaining scope is (a) the `PrRef.number==0` sentinel fix in `deliverer.py` and (b) the BACKLOG.md bookkeeping**
- [ ] **T-L2.13** — Manual smoke tests (off-CI): zero-touch delivery, planned lane, crash recovery — real GitHub repo. Cannot be run autonomously; needs a human operator session per T-L2.13's own scope (off-CI, real systems)
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

**Session checkpoint 2026-07-25** — T-L2.1 through T-L2.8 code-complete and
tested (381 passed, 1 xfailed, up from L0/L1's 301/1 baseline). T-L2.9 not
yet started (see "Resume point" under Current, above, for the exact next
step and a load-bearing circular-import note). T-L2.10 through T-L2.14 not
started.

### What landed, by task

- **T-L2.1**: Suite baseline re-confirmed (301/1 at start). Real `gh --json`
  fixtures captured against the actual `anant-gupta-utexas/atlas` repo (not
  synthesized) — a scratch issue (#4) and scratch PR (#5) were created,
  captured in all three `pr view` outcome states (open/merged/closed-unmerged
  — merged came from real PR #3, open+closed from #5 pre/post `gh pr close`),
  then cleaned up (issue deleted, branch deleted, PR left closed in repo
  history — closing a PR isn't reversible via API, only deletion of the
  branch was possible). `gh --version` pinned: **2.96.0**. Also created the
  **permanent** label set the loop needs going forward (`atlas:ready`,
  `atlas:working`, `atlas:done`, `atlas:rejected`, `atlas:blocked`,
  `wf:quick`, `wf:planned`, `engine:codex`, `engine:claude`) on the real repo
  — these are NOT scratch, they stay. Full findings in
  `loop-mode-phase-L2-context.md`'s "T-L2.1 findings" section.
- **T-L2.2**: `queue_gh.py` implemented with all six functions from the plan
  plus two NOT in the original method-signature sketch, added because the
  grep-enforced "loop.py never shells gh directly" test (Decision #15)
  caught two raw `gh` calls I'd initially written inline in `loop.py`:
  - `queue_gh.current_user()` — `gh api user --jq .login`, used by
    `tick()`/`claim()` to resolve the assignee.
  - `queue_gh.find_run_id_comment(issue)` — greps an issue's comments for
    the `plumb run_id: \`<id>\`` pattern `comment()` writes at dispatch time;
    used by `sync_prior_prs()` to recover which plumb run a merged/closed PR
    corresponds to.
  Both are exactly the kind of thing Decision #15 exists to catch — the grep
  test did its job on the first real run against `loop.py`.
  Also implemented (not in the original signature list, needed for
  `reconcile_orphans`): `list_labeled(repo, label)` — generic labeled-issue
  listing, `list_ready` is now a thin wrapper calling it with
  `"atlas:ready"`.
- **T-L2.3**: `LoopConfig` + `Config.loop` + TOML `[loop]` parsing, exact
  schema from the plan. `concurrency != 1` raises in `__post_init__` (tested
  both via direct `LoopConfig()` construction and via `Config.load()` with a
  `[loop]` TOML section).
- **T-L2.4**: `triage.py` implements label-wins-else-classify exactly per
  Decision #13 — classify dispatches via `CliBackend.build_argv`/
  `parse_result` directly (imported from `cli_backend.py`), NOT through
  `SubprocessStageRunner`. Records a plumb span (`kind="plan", name="triage"`)
  only on the classify path, never on label-wins (matches the plan's spec —
  "no LLM call happened" on label-wins, so no span).
- **T-L2.5 through T-L2.8**: all four landed together in `loop.py` (630
  lines) since they're tightly coupled — `tick()` calls into
  `sync_prior_prs()`, budget/breaker functions, and `run_forever()`/
  `reconcile_orphans()` all share `LoopState`. `make_pipeline()` promoted
  from `cli.py::_make_pipeline` (dropped the underscore, added
  `backend_override: str | None = None` param) — both `cli.py::run` and
  `cli.py::resume` updated to call the shared version; `loop.py`'s
  `run_one_shot()` calls it with `backend_override=` set from the issue's
  `engine:*` label (`_engine_for_issue()`).

### Implementation decisions made beyond the plan's literal pseudocode

These aren't scope deviations — they're places the plan's pseudocode was
necessarily incomplete (it's pseudocode) and a concrete choice had to be
made. Flagging them here per the TRS's own "don't silently drift" norm:

1. **`run_one_shot()`'s `cost` return value is currently always `0.0`.** The
   plan's pseudocode calls `extract_cost(recorder)` but no such function
   exists yet anywhere in the codebase — L1's `UsageStats`/`CodexUsageStats`
   are parsed by the backends but never threaded back through
   `SubprocessStageRunner`/`Pipeline`/`RunResult` to a caller. Wiring that
   through is arguably its own task (not listed in T-L2.5's acceptance
   criteria, which only requires `RunResult.status == "success"` gating,
   not cost extraction). Current behavior: `dollars_today` never actually
   accumulates from quick-lane runs, which means **`max_dollars_per_day`
   is currently inert** — only `max_runs_per_day` has teeth. This is a real
   gap worth a maintainer decision: either (a) accept it for L2 and note it
   as a known limitation in STATUS.md (budgets exit criterion §13 #7 is
   about the breaker/cap *mechanism* existing and firing correctly, which
   is tested and works for the runs-cap; or (b) wire `extract_cost` before
   calling T-L2.6 fully done. I left T-L2.6 marked complete because the
   mechanism (both caps, breaker, cooldown) is implemented and tested
   correctly — the gap is only in one input (`cost`) always being zero for
   the dollar half, not in the budget logic itself. **Flag this to the user
   before T-L2.11 (coverage/lint gate) or T-L2.14 (STATUS.md) — STATUS.md
   should say "dollar budget inert pending cost-extraction wiring" rather
   than implying full budget enforcement.**
2. **`run_planned_first_pass()` dispatches `/dev-docs-be` via a raw
   `backend.build_argv()`/`subprocess.run()` call**, not through
   `SubprocessStageRunner` or `Pipeline` — there's no existing "invoke one
   slash-command as a single dispatch outside the 7-stage pipeline"
   primitive to reuse (the closest is `triage.py`'s classify path, which
   this mirrors). This matches Decision #2's first-pass-only framing but is
   a second instance of the same "direct CliBackend dispatch" pattern
   Decision #13 introduced for triage — worth noting as a small emerging
   pattern (two call sites now bypass `SubprocessStageRunner` for one-shot
   dispatches) rather than something either TRS explicitly named as
   reusable.
3. **`sync()` in `queue_gh.py` resolves an issue's linked PR via
   `gh issue view --json closedByPullRequestsReferences`**, not via parsing
   `Closes #n` out of PR bodies as the plan's prose loosely suggested. This
   is a real, verified `gh` JSON field (confirmed via `gh issue view --json`
   during T-L2.1's capture work) and is more robust than text-parsing PR
   bodies. Flagging because it's an implementation-level resolution of an
   underspecified point, not a re-derivation of something the plan pinned
   down explicitly.
4. **`current_gh_user()` in `loop.py` is now a one-line wrapper around
   `queue_gh.current_user()`** (see queue_gh notes above) rather than the
   plan's implied "loop.py calls some current-user helper" — moved into
   `queue_gh.py` entirely once the grep test caught the original inline
   version.

### Known gaps / follow-ups for whoever picks this up next

- **Cost extraction (`extract_cost`) is unimplemented** — see point 1 above.
  This is the single most important thing to resolve before claiming T-L2.6
  or the §13 #7 budget exit criterion are *fully* proven, as opposed to
  "the breaker/runs-cap mechanism is proven, dollar-cap is not yet
  exercised end-to-end."
- **T-L2.9 (CLI surface) not started** — see "Resume point" above for the
  exact next step and the circular-import constraint that must be respected
  (`cli.py` must lazy-import `atlas.loop`, never at module level).
- **T-L2.10 (integration tests)**, **T-L2.11 (lint/type/coverage gate — unit
  tests have been running mypy --strict + ruff clean per-file as they were
  written, but the full-repo coverage-target check per module has not been
  run)**, **T-L2.12's `PrRef.number` fix** (the `trusted_authors` half of
  T-L2.12 is done — see the tasks-list note above), **T-L2.13 (manual
  smoke, needs a human)**, and **T-L2.14 (STATUS.md)** are all not started.
- Full test count at this checkpoint: **381 passed, 1 xfailed** (new files:
  `test_queue_gh.py` 25, `test_triage.py` 9, `test_loop.py` 41, plus 6 new
  `[loop]`-config tests added to `test_config.py` — 81 new tests total over
  the L0/L1 baseline of 301/1).
