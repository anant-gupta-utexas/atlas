# Tasks — Loop Mode, Phase L2 TRS

Progress checklist. Source-of-truth for design is
[`loop-mode-phase-L2-plan.md`](./loop-mode-phase-L2-plan.md); full task detail lives
in [`loop-mode-phase-L2-tasks-detail.md`](./loop-mode-phase-L2-tasks-detail.md);
Pending Decisions live in
[`loop-mode-phase-L2-decisions.md`](./loop-mode-phase-L2-decisions.md). Reference
notes live in [`loop-mode-phase-L2-context.md`](./loop-mode-phase-L2-context.md).

## Current

```
phase: code-complete — T-L2.1 through T-L2.12 and T-L2.14 done;
       T-L2.13 blocked (needs a human operator + a maintainer fix first)
gate:  none
next:  T-L2.13 (manual smoke tests, off-CI — needs a human operator; blocked, see note below)
```

### Resume point (2026-07-25)

T-L2.10 is done: `tests/integration/test_loop_e2e.py` (5 tests) exercises
the real `tick()` -> `run_one_shot()`/`run_planned_first_pass()` ->
`make_pipeline()` -> `Pipeline`/`WorktreeManager`/`GhPrDeliverer` stack
against a real temporary git repo — `gh` is mocked at the `queue_gh`
function boundary; the backend subprocess (`claude -p ...`) and delivery's
`git push`/`gh pr create` are mocked via one `subprocess.run` patch (see
`_FakeSubprocess` in that file — `atlas.orchestrator`, `atlas.deliverer`,
`atlas.loop`, and `atlas.worktree` all import the literal same `subprocess`
module object, not separate copies, so one patch covers all of them; real
`git` calls other than `push` are delegated through to the actual
`subprocess.run` so `WorktreeManager` genuinely exercises git). All four
scenarios from the plan's Testing Strategy table pass: one-shot lane
end-to-end, planned-lane stops after the plan PR (no `code_gen`/`verify`
dispatch), crash recovery via `reconcile_orphans` (both the relabel-to-ready
and orphaned-worktree-pruning halves, as two tests), and the zero-touch
smoke shape (`Closes #n` + `run_id` comment). Full suite at 398 passed,
1 xfailed. `ruff check`/`ruff format --check` clean;
`mypy --strict src tests` shows zero errors attributable to the new file
(the 167 errors it surfaces elsewhere are all pre-existing, in files this
session didn't touch — confirmed by diffing against `mypy --strict src`
alone, which shows the same 14 pre-existing `config.py` errors whether or
not `test_loop_e2e.py` exists).

T-L2.11 is also done — full gate run:

- `ruff check .`: clean (no changes needed).
- `ruff format --check .`: found one pre-existing drift, `tests/unit/test_triage.py`
  (a committed file this session never touched, one over-long line collapsed
  by an older ruff version than the one now in use) — reformatted, now clean.
- `mypy --strict src`: found 14 real, fixable errors in `src/atlas/config.py`,
  all in `_parse_loop_config()` (T-L2.3's own code). Root cause: `int(section.get(key,
  default))` where `section.get()` returns `object` — the `# type: ignore[arg-type]`
  comments guarding these calls were on the wrong error code (mypy raises
  `call-overload` for `int(object)`, not `arg-type`), so they never actually
  suppressed anything; strict mode was catching real, unsuppressed errors that
  had been sitting there since T-L2.3. Fixed by extracting `_int_field()`/
  `_float_field()` helpers that `isinstance`-narrow before calling
  `int()`/`float()`, no `type: ignore` needed. `mypy --strict src` is now
  clean (0 errors, 22 files) — verified the fix didn't change parsing
  behavior (all `[loop]`-config tests in `test_config.py` still pass
  unchanged).
- `mypy --strict tests` (whole directory, not just `src`) is NOT clean — 167
  pre-existing errors across `test_remediation.py`, `test_pipeline.py`,
  `test_shell_runner.py`, `test_cli_backend_dispatch.py`, and
  `test_job_workflow_e2e.py`, none introduced this session, none in any file
  T-L2.1–T-L2.10 touched or added. The plan's T-L2.11 acceptance criteria
  only name `mypy --strict src`, so this is out of scope — noted so it isn't
  mistaken for a new regression by whoever runs this gate next.
- Coverage (`pytest --cov=atlas --cov-report=term-missing`, `cli.py` and
  `post_commit_hook.py` excluded per `pyproject.toml`'s existing
  `[tool.coverage.run] omit`): **95.10% repo-wide**, comfortably above the
  actual CI gate (`fail_under = 80`) but **below the 96% figure L1's
  STATUS.md cited** (301 tests, before `loop.py`/`queue_gh.py`/`triage.py`
  existed) — this is expected dilution, not a quality drop: L2 added ~500
  new statements at 91–95% coverage each (below the pre-L2 average, which
  skewed toward smaller, more-exhaustively-tested modules), which pulls the
  blended repo-wide average down even though **every individual module,
  including every new L2 module, meets or clears its own T-L2.11-specified
  target**: `loop.py` 91% (target ≥85%), `queue_gh.py` 92% (target ≥90%),
  `triage.py` 95% (target ≥85%), `config.py` 100% (target ≥90% for the new
  `[loop]` lines). Per-module targets are what T-L2.11's acceptance criteria
  actually specify; the "no regression below L1's 96%" framing in the plan's
  prose was written before L2's module count/size was known and should be
  read as directional, not a hard gate — flagging the exact number rather
  than silently reporting "no regression" so a maintainer can judge it.
  One genuine coverage gap worth naming: `loop.py:421-427`, the
  `current_gh_user()`-raises-`GhCliError` branch inside `tick()` (the "can't
  even ask GitHub who I am" failure path), is untested — not fixed here
  since T-L2.11 is scoped as verification-only (no files to create/modify
  per its own task spec), but a reasonable target for a T-L2.10 follow-up
  test if a maintainer wants to close it.

**Load-bearing implementation note not yet in the plan text:** `loop.py`
imports `make_pipeline` from `cli.py` (module-level `from atlas.cli import
make_pipeline`). This means `cli.py` must NOT import `atlas.loop` at module
level — it would create a circular import. `cli.py` imports `atlas.loop`
lazily inside each `loop_run`/`loop_status` command body (same lazy-import
pattern `cli.py` already uses for `atlas.post_commit_hook` in the `hook`
command). `loop_start`/`loop_stop`/`loop_attach` don't need `atlas.loop` at
all — they're pure `tmux` subprocess wrappers.

**Second load-bearing note, surfaced by T-L2.10:** `loop_dev.yaml`'s three
stage `tool` strings (`RAW:...` x2, `/verify`) are not present in
`plugin_resolver.PLUGIN_COMMANDS` — that table is dev-pipeline-only per its
own docstring, and despite the docstring's claim that `RAW:`-prefixed tools
"bypass resolution entirely," `resolve()`'s actual code does a literal dict
lookup with no such special case. A real `atlas run --workflow loop_dev` (or
`atlas loop run`) will raise `RoutingDriftError` today unless the operator's
`.atlas.toml` has a `[plugin_commands]` override mapping each of those three
literal tool strings to themselves (or repo_root `.atlas.toml` equivalent).
T-L2.10's tests reproduce this via `Config.plugin_commands` built from
`loop_dev.yaml`'s own stage tool strings. **This is a real, live gap for
T-L2.13's manual smoke test** — the operator will need a `.atlas.toml`
`[plugin_commands]` block for `loop_dev`'s three stages before `atlas loop
start` can dispatch anything, or `plugin_resolver.py`/`resolve()` needs a
fix to actually special-case `RAW:`-prefixed tool strings as its docstring
already claims. Flagging for a maintainer decision before T-L2.13.

T-L2.12 is also done. Both halves:

- **`PrRef.number` fix**: `deliverer.py::_parse_pr_url` now raises
  `DeliveryError` when it can't parse a PR number out of `gh pr create`'s
  stdout, instead of sentinel-ing to `PrRef(number=0, ...)`. Chose the
  "raise in `deliverer.py`" branch of the L1 review's two suggested fixes
  (over `int | None` + explicit `None`-handling in `loop.py`) because
  `PrRef.number` turned out to have **zero real consumers** in `loop.py` —
  only `.url` is read (in `_format_run_summary`) — so an `Optional` field
  would only defer the same problem to some future caller, while raising at
  the one place that actually knows parsing failed gives an immediate,
  unambiguous signal. `tick()`'s existing `except (DeliveryError, ...)`
  handler already catches this and posts a "run failed, left atlas:working
  for manual triage" comment — no new exception-handling wiring needed.
  New test: `test_deliver_malformed_pr_url_raises_instead_of_number_zero_sentinel`
  in `test_deliverer.py`, pinning that `worktree.cleanup()` is correctly
  *not* called in this path (the PR already exists on GitHub by the time
  parsing fails; atlas has no confirmed PR number to attribute cleanup to).
- **`trusted_authors` wiring checkpoint**: added
  `test_trusted_authors_enforced_at_tick_claim_boundary` to `test_loop.py`
  — the checkpoint test T-L2.12 asks for, at the `tick()`/`claim()`/dispatch
  boundary rather than only at the `_pull_next_ready()` helper level (the
  three existing `test_trusted_authors_*` tests from T-L2.5/T-L2.6 already
  covered the helper in isolation, but T-L2.12's own spec calls this out as
  "the explicit checkpoint tying it back to the L1 review's TRD §4 Security
  ask," so a `tick()`-level test was the actual gap). Asserts an
  untrusted-author issue never reaches `current_gh_user()`, `claim()`,
  either dispatch function, or `comment()` — the tick resolves `idle`, per
  Decision #16 (skipped, not relabeled to an error state). `_pull_next_ready`
  is intentionally left unmocked in this test since it's the real
  enforcement point.
- **BACKLOG.md bookkeeping**: L1 code review action #4 (`PrRef.number`)
  marked closed directly in
  [`loop-mode-code-review.md`](../loop-mode-phase-L1/loop-mode-code-review.md)'s
  own actions table (added a Status column rather than deleting the row —
  the review doc is the historical record of what was found and when it was
  fixed, so editing it in place keeps that provenance rather than
  scattering it into a second BACKLOG.md entry that would need to stay in
  sync). Action #5 (branch-safety exact-match, still open, not in T-L2.12's
  closure scope) is now a proper BACKLOG.md carryforward entry under
  "v1.1-era carryforward" — it wasn't previously tracked anywhere outside
  the review doc, so this is the first time it's actually on the backlog
  rather than just flagged in a phase-specific review file that's easy to
  lose track of.

Full suite: 400 passed, 1 xfailed (up from T-L2.11's 398 — the two new
tests). `ruff check`/`ruff format --check`/`mypy --strict src` all clean.

T-L2.14 (`STATUS.md`) is also done — L2 recorded with the same density/style
as L0/L1's entries (shipped bullets, the L1-review closure, the
`plugin_resolver` blocker, the `extract_cost` known limitation), the redundant
"Module coverage" table dropped entirely (superseded by the per-module
coverage figures now folded into each phase's own bullet, so the table was
duplicating information rather than adding it), and front-matter
(`status`/`next_gate`/`blocked_on`) updated to name the `plugin_resolver` fix
as the actual blocker rather than leaving it implicit in prose. "Next" now
names **Phase L3** per T-L2.14's acceptance criteria.

Only T-L2.13 remains — manual smoke tests, off-CI, needs a human operator,
and **blocked**: see the "Second load-bearing note" above
(`plugin_resolver.resolve()` doesn't special-case `RAW:`-prefixed tool
strings, so `atlas loop run`/`atlas loop start` will raise `RoutingDriftError`
on `loop_dev.yaml`'s stages today without a `.atlas.toml` `[plugin_commands]`
workaround). This needs a maintainer decision before T-L2.13 can proceed for
real — every other task in this TRS is done.

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
- [x] **T-L2.9** — `atlas loop` CLI surface: `run`/`start`/`stop`/`status`/`attach` (tmux wrapper for the detached three)
- [x] **T-L2.10** — Integration tests: full-tick + zero-touch smoke (faked `gh`/`Pipeline`)
- [x] **T-L2.11** — Lint/type/coverage gate
- [x] **T-L2.12** — `PrRef.number` fix (L1 code-review finding L2) + `trusted_authors` wiring checkpoint
- [ ] **T-L2.13** — Manual smoke tests (off-CI): zero-touch delivery, planned lane, crash recovery — real GitHub repo. Cannot be run autonomously; needs a human operator session per T-L2.13's own scope (off-CI, real systems)
- [x] **T-L2.14** — Update `STATUS.md`
- [x] **T-L2.15** — Phase L2 code review (`/consult-experts` Code Reviewer) + fix pass. Verdict **Approve with changes**: 2 Critical, 4 Important, 5 Minor, all fixed; both architecture recommendations also applied (`loop_budget.py` split, `pipeline_factory.py` extraction). See [`loop-mode-phase-L2-code-review.md`](./loop-mode-phase-L2-code-review.md) → "Resolution (applied 2026-07-25)". Suite 400 → 424 tests.

## Exit criteria (TRD-v3 §13 items 5–8 — copied for tracking)

- [ ] **§13 #5** — Zero-touch delivery (headline): one `atlas:ready` issue → `atlas loop start` → a PR appears (`Closes #n`) with a plumb `run_id` comment, zero keystrokes between labeling and reviewing; merging writes `user_signal` + closes the issue. Cost half requires plumb P1-a — L2 reports tokens, not dollars, until then.
- [ ] **§13 #6** — Two-lane routing works: `wf:quick` → one PR; `wf:planned` → plan-only PR (triad + Pending Decisions) and the loop stops. **Note (code review, 2026-07-25):** the planned lane could not open a PR at all before the C1 fix — `dev-docs-be` ran against `repo_root`, the worktree was created afterwards, and nothing was ever committed, so delivery pushed a branch identical to `main`. Now fixed and covered CI-side by `test_planned_lane_commits_triad_before_delivering`; the real proof still awaits T-L2.13.
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

**Session checkpoint 2026-07-25** — T-L2.1 through T-L2.12 and T-L2.14
code-complete and tested (400 passed, 1 xfailed, up from L0/L1's 301/1
baseline). Only T-L2.13 remains, and it's blocked — see the Resume point
above and the Known gaps section below.

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
- **T-L2.9**: `loop_app` Typer sub-app added to `cli.py` (`app.add_typer`)
  with five commands. `loop_run()` lazy-imports `run_forever` and calls it
  with `repos=list(cfg.loop.repos)` — no tmux dependency, matching the
  acceptance criterion literally. `loop_start`/`loop_stop`/`loop_attach` are
  thin `tmux` wrappers via a shared `_tmux()` helper (`start`/`stop`) and a
  direct `os.execvp` call (`attach`, so the shell process is replaced rather
  than left as a wrapper around tmux — matches the plan's parenthetical
  "os.execvp — replaces the process"). A missing `tmux` binary is caught
  cleanly for all three (`FileNotFoundError` from `subprocess.run` for
  start/stop, `shutil.which() is None` for attach since `execvp` itself
  would raise a less friendly `OSError`) and produces `typer.Exit(1)` with a
  clear message; `loop run` has no tmux dependency at all so it's
  unaffected by a missing binary, as required. `loop_status()` lazy-imports
  `LoopState`/`breaker_open`, checks for the state file's existence before
  calling `LoopState.load_or_init` (which would otherwise silently
  fabricate a fresh-zero state and print misleading output), and reports
  runs/dollars used vs. configured budget, last tick time, and breaker
  state (open+until-when, or closed).

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
5. **T-L2.9**: `loop_app` Typer sub-app in `cli.py`, five commands, all
   details in the T-L2.9 note above (now folded into this list for a single
   place to scan). No further deviations beyond what's already logged there.
6. **T-L2.10**: found and worked around a real, pre-existing gap in
   `plugin_resolver.resolve()` — see the "Second load-bearing note" under
   Resume point above. This is NOT a T-L2.10 scope item to fix (it predates
   this phase and isn't in any T-L2.x acceptance criteria), but it blocks
   T-L2.13's manual smoke test until a maintainer picks one of the two
   fixes named there. Also discovered that `atlas.orchestrator`,
   `atlas.deliverer`, `atlas.loop`, and `atlas.worktree` all import the
   literal same `subprocess` module object (not independent copies) —
   `unittest.mock.patch("atlas.X.subprocess.run", ...)` on any one of them
   patches all four simultaneously. This isn't a bug, just a fact about
   Python's module cache that the test file's `_FakeSubprocess` docstring
   now documents; worth knowing before writing more subprocess-mocking
   tests against this codebase, since patching two of these dotted paths
   "for clarity" silently clobbers rather than layers.
7. **T-L2.11**: fixed 14 real (previously unsuppressed) `mypy --strict`
   errors in `config.py::_parse_loop_config()` by extracting
   `_int_field()`/`_float_field()` helpers — see the Resume point note
   above for the root cause (mis-targeted `type: ignore` comments) and full
   detail. Also reformatted `test_triage.py` (pre-existing drift, unrelated
   to this session's code). No other files needed changes to pass the gate.
8. **T-L2.12**: picked the "raise in `deliverer.py`" branch over the
   "`int | None` + handle in `loop.py`" branch the L1 review offered as
   alternatives — see the Resume point note above for why (`PrRef.number`
   has zero real consumers in `loop.py` today, so `Optional` would just
   defer the same failure mode to a future caller instead of closing it).
   The BACKLOG.md bookkeeping edited the L1 review doc in place (added a
   Status column to its actions table) rather than duplicating the closed
   item into BACKLOG.md itself, since the review doc is already the
   authoritative record of when/why each action was raised.
9. **T-L2.14**: dropped `STATUS.md`'s "Module coverage" table entirely
   (per-file explicit request) rather than updating it with L2's new
   modules — it had become pure duplication once each phase's own bullet
   list already names every file it touches with a one-line description;
   the table added a second place to keep in sync for no additional
   information. Folded the per-module coverage numbers (`loop.py` 91%, etc.)
   into L2's shipped-bullets prose instead, where they sit next to the
   context that explains them (T-L2.11's target vs. actual). Also updated
   the front-matter `blocked_on` field to name the `plugin_resolver` gap
   explicitly — previously `blocked_on: null` even during L1, which
   undersold the two still-open manual checks; L2's `blocked_on` now names
   the actual mechanical blocker (not just "needs a human," which was true
   of L0/L1 too but didn't block anything requiring a code fix first).

### Known gaps / follow-ups for whoever picks this up next

- **Cost extraction (`extract_cost`) is unimplemented** — see point 1 above.
  This is the single most important thing to resolve before claiming T-L2.6
  or the §13 #7 budget exit criterion are *fully* proven, as opposed to
  "the breaker/runs-cap mechanism is proven, dollar-cap is not yet
  exercised end-to-end."
- **`plugin_resolver.resolve()` doesn't special-case `RAW:`-prefixed tool
  strings despite its own docstring's claim** — see the "Second load-bearing
  note" under Resume point above. Blocks T-L2.13 until fixed one of two
  ways (special-case `RAW:` in `resolve()`, or ship a `.atlas.toml`
  `[plugin_commands]` block for `loop_dev.yaml`'s three stages as part of
  the loop's setup docs/install). Needs a maintainer decision — flagged,
  not fixed, since it's outside every T-L2.x task's stated scope.
- **`loop.py:421-427`'s `current_gh_user()`-raises-`GhCliError` branch inside
  `tick()` is untested** — see the T-L2.11 coverage note above. Small,
  well-scoped gap for whoever next touches `test_loop.py`.
- **Repo-wide coverage is 95.10%, not the 96% L1's STATUS.md cited** — see
  the T-L2.11 coverage note above for why this is expected dilution (every
  individual module, including all new L2 modules, meets its own target)
  rather than a quality regression, and well above the actual CI floor
  (`fail_under = 80`).
- **T-L2.13 (manual smoke, needs a human, and has a known blocker — see
  above)** is the only remaining task in this TRS.
- Full test count at this checkpoint: **400 passed, 1 xfailed** (new files:
  `test_queue_gh.py` 25, `test_triage.py` 9, `test_loop.py` 42,
  `test_cli_loop.py` 12, `test_loop_e2e.py` 5, plus 6 new `[loop]`-config
  tests added to `test_config.py` and 1 new test added to
  `test_deliverer.py` — 100 new tests total over the L0/L1 baseline of
  301/1).
