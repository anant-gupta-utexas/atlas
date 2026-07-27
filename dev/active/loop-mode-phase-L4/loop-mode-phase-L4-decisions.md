# Pending Decisions & Clarifications — Loop Mode, Phase L4 TRS

Full text of all decisions flagged during this TRS's authoring. Split out from
`loop-mode-phase-L4-plan.md` to keep that file under the repo's 800-line cap
(matches L2/L3's own precedent). Normative, not optional reading — several of
these change which files T-L4.1/T-L4.2/T-L4.5/T-L4.7 touch.

> **✅ ALL 14 RESOLVED (maintainer, 2026-07-27).** Every recommendation below
> was accepted as written. The options and their trade-offs are kept in place
> rather than trimmed to the winner — the record of what was weighed is the
> point, matching TRD-v3's own "annotate, don't silently rewrite" discipline.
>
> **One decision changed between authoring and resolution: #14.** It shipped
> in the first draft with *"No recommendation given — a genuine design fork"*;
> a recommendation was subsequently worked out and accepted (**Option B**,
> `~/.atlas/loop-state.json`, **with a mandatory one-time migration of the
> existing file** — see that section for why abandoning it is not safe). The
> original no-recommendation framing is preserved inline there as history.
>
> Two resolutions carry an **operator-visible behavior change** that must be
> documented rather than discovered live — both are correct behavior that
> looks like a bug if unannounced (tracked in T-L4.11's acceptance criteria):
> - **#12** — raising `concurrency` makes the circuit breaker trip faster in
>   wall-clock time for the same `no_progress_limit`/`identical_error_limit`.
> - **#14** — `atlas loop status` becomes **user-wide**, not per-repo: the
>   same budget/breaker state is reported from any repo.

---

## 1. `[loop].repos` config shape: breaking table array vs. non-breaking parallel map

Today `[loop].repos = ["owner/repo", ...]` is a flat string list, and this
repo's own `.atlas.toml` uses it (`repos = ["anant-gupta-utexas/atlas"]`). L4
needs to pair each GitHub slug with a local checkout path.

- **Option A (recommended): breaking change to `[[loop.repo]]` table array**
  (`github` + `local_path` + `trusted_authors` per entry, see plan's Detailed
  Component Design). Clean data model — one source of truth per target, no
  way for a slug and a path list to silently drift out of index-alignment.
  Downside: every existing `.atlas.toml` with the old flat-list shape
  (including this repo's) needs migrating; `Config.load()` must decide
  whether to hard-fail with a clear message on the old shape or silently
  ignore it (silently ignoring is the wrong choice — a stale config that
  parses but does nothing is worse than a loud failure).
- **Option B: keep `repos: tuple[str, ...]` flat, add a parallel
  `local_paths: dict[str, str]`** mapping GitHub slug → local path (e.g.
  `local_paths = { "anant-gupta-utexas/atlas" = "/Users/.../atlas" }`).
  Non-breaking for the existing single-repo case if a sensible default (the
  process's own cwd, or the pre-L4 single `repo_root`) is assumed for any
  slug missing from the map. But two parallel lists that must stay
  consistent is a worse data shape long-term, and the "assume cwd for an
  unmapped slug" fallback is exactly the kind of implicit pairing that
  created this phase's core gap in the first place.

Recommend **Option A**. L4 is precisely the phase the TRD licenses to make
this change ("Add the plumb repo as a second target" already implies the
config needs to express two distinct local checkouts). This repo's own
`.atlas.toml` migration is folded into T-L4.1's acceptance criteria rather
than treated as a separate follow-up, since shipping L4 without updating the
one live config file that would break it is not a real deliverable.

---

## 2. Concurrency shape: widen `tick()` to a batch vs. N parallel `tick()` loops

Two ways to get `concurrency > 1` dispatches per interval:

- **Option A (recommended, as designed): `tick()` itself claims and
  dispatches a bounded batch** (up to `cfg.loop.concurrency` issues) via an
  internal thread pool, and `run_forever()` keeps its exact existing `while
  True: tick(); sleep()` shape. Matches TRD-v3 §12's explicit anti-framework
  mitigation ("the loop is a `while` over `tick()`; `tick()` is a linear
  state machine") — the loop's outer shape does not grow a second concurrency
  mechanism layered on top of the first.
- **Option B: `run_forever()` runs N independent `tick()`-calling loops**,
  each in its own thread/process, each with its own poll interval. Superficially
  simpler per-loop, but multiplies `LoopState`/breaker/budget coordination
  problems across N independent loops instead of one batch inside one loop
  — the process-global counters (`runs_today`, `dollars_today`,
  `breaker_open_until`) would need cross-loop synchronization regardless, so
  Option B doesn't actually avoid the thread-safety problem this TRS already
  has to solve for Option A; it just spreads it across more call sites.

Recommend **Option A**. It is also the smaller diff against the existing
`tick()`/`run_forever()` split, and keeps exactly one place
(`tick()`'s own post-pool body) responsible for `LoopState` mutation.

---

## 3. Does attended `atlas run` migrate to the keyed `current-run` path?

`state.py`'s new `write_current_run_keyed`/`list_current_runs`/
`delete_current_run_keyed` methods are additive. The legacy singleton
`write_current_run`/`read_current_run`/`read_current_run_with_worktree`/
`delete_current_run` methods remain, used today by both attended `atlas run`
and (pre-L4) the loop.

- **Option A (recommended): keep attended `atlas run` on the legacy singleton
  path forever; only loop-dispatched runs use the keyed path.** Attended mode
  is still genuinely single-run (v1/v2's own scope: "one `atlas run` per repo
  at a time"; PRD "Single concurrent run per repo" assumption) — there is no
  concurrency to support there, so migrating it would be a pure risk (any
  subtle behavior difference in the keyed path's read/write ordering) for
  zero benefit. This also preserves every phase's "attended-mode invariance"
  NFR by construction: the singleton code path is untouched, not merely
  tested to behave the same.
- **Option B: migrate everything to the keyed path** (attended runs write to
  `.atlas/runs/<run_id>/current-run` too, singleton path retired). Marginally
  simpler long-term (one code path, not two), but touches attended mode's
  state I/O for a phase whose exit criteria (§13 #11/#12) say nothing about
  attended runs at all — exactly the kind of scope creep TRD-v3's own
  Appendix A warns against ("if implementation finds `Pipeline` genuinely
  needs editing, that is a signal the design has drifted").

Recommend **Option A**.

---

## 4. Weekly report data source: direct `SQLiteStorageAdapter` import vs. shelling `plumb run stats`

TRD-v3 §14/§13 #12 name `plumb run stats` literally. Verified against
`plumb/cli.py:52-151` (2026-07-26): the CLI's JSON output
(`_RUN_STATS_COLUMNS`) never includes `dollar_cost`, and truncates `run_id`
to `s.run_id[:8]` in every row. The underlying `RunSummaryRow` the CLI itself
reads from (`plumb/core/entities.py:242-282`, via
`storage.list_runs_with_counts()`) carries the full data — the CLI's
formatting layer is what drops it, not the storage layer.

- **Option A (recommended): import `plumb.adapters.storage_sqlite.SQLiteStorageAdapter`
  directly**, constructed the same way `plumb/cli.py::_get_storage()` builds
  it internally (`plumb.config.get_settings()` + `ensure_data_dir()` +
  `SQLiteStorageAdapter(db_path, clock=...)`), and call
  `.list_runs_with_counts()` on it — full `run_id`/`dollar_cost`/
  `parent_run_id`/tokens. This mirrors L3's own Pending Decision #3
  resolution (library `JudgeAdapter` over the `plumb judge run` batch CLI)
  exactly: when the TRD names a CLI surface but the CLI's actual behavior
  doesn't fit the need, atlas verifies against source and uses the
  underlying library call instead, documenting the divergence rather than
  forcing the CLI to do something it wasn't built for.
- **Option B: shell `plumb run stats --format json --limit <n>` as a
  subprocess** (matching the `gh`-adapter pattern `queue_gh.py` uses for
  GitHub). Keeps atlas's plumb integration surface CLI-only in this one
  place, but is a strictly worse fit here: the CLI drops the two fields
  (`dollar_cost`, full `run_id`) this report structurally needs, so Option B
  would require *also* patching `plumb`'s CLI to add them — a cross-repo
  change this TRS cannot commit to (plumb is a separate project with its own
  release cadence), whereas Option A needs no plumb-side change at all.

Recommend **Option A**. Note for the maintainer: `_get_storage()` itself is a
private, underscore-prefixed helper inside `plumb/cli.py`, not a published
API — `loop_report.py` should replicate its two-line construction
(`get_settings()` + `SQLiteStorageAdapter(...)`) directly rather than
importing the private function, so a future plumb refactor of `_get_storage`
doesn't silently break atlas's import.

---

## 5. "Intervention rate" operational definition given available data

TRD-v3 §2's KPI defines it as "fraction of runs requiring a human nudge
beyond the standard PR review" — a broad, human-centric definition. Nothing
atlas or plumb durably records today directly answers "did a human have to
manually unstick this issue," because `atlas:blocked` → later manually
requeued → `atlas:ready` is a label-transition history GitHub itself doesn't
expose as a queryable timeline through the `gh` surfaces atlas already uses,
and atlas doesn't persist its own transition log.

- **Option A (recommended, narrower): `intervention_rate` = (lineages with
  more than one run, i.e. a self-heal retry fired) / (all terminal-status
  lineages)** — a **robot** intervention (the diagnosis-injected retry),
  not literally a human one. This is honestly narrower than TRD-v3 §2's
  prose, but it is the one definition fully computable from data atlas
  already writes (L3's `parent_run_id` child-run lineage), with no new
  tracking required. Flag the narrowing explicitly in the report's own
  output/docstring — the same "flagged rather than overclaimed" discipline
  L3's own Pending Decision #4 caveat used for `reasoning_output_tokens`.
- **Option B (broader, TRD-literal): track "did a human relabel this issue
  back to `atlas:ready` after it reached `atlas:blocked`"** as the
  intervention signal. Closer to the KPI's literal intent, but requires new
  durable tracking atlas does not have today — either a new local log of
  label transitions (a new atlas-owned state file, more surface than this
  phase's stated scope) or re-deriving it from GitHub's issue-timeline API
  (`gh issue view --json timeline` or similar), which is a new `queue_gh`
  capability not currently designed or scoped.

Recommend **Option A** for v3.3, with Option B flagged in BACKLOG.md as a
natural follow-up once/if a label-transition log exists for other reasons.

---

## 6. Engine discriminator for the report's per-engine split

The report must show `claude` cost and `codex` tokens **separately**, never
blended (TRD-v3 §13 #12's own text: "cross-engine comparison remains
tokens-only"). `RunSummaryRow` carries `orchestrator_model` (a model name
string like `haiku` or `gpt-5.1-codex`), not an explicit `engine` field.

- **Option A (recommended): add `"engine"` to the `attributes` dict already
  passed to `record_span()`** on the `code_gen` span (the same JSON column
  L1's raw-token-breakdown provenance and L3's judge-score-rationale-adjacent
  data already use) — additive, no plumb schema change, and an explicit
  string (`"claude"`/`"codex"`) rather than an inferred one.
- **Option B: infer engine from `orchestrator_model`'s string shape**
  (e.g., "if it doesn't start with a known Claude model prefix, assume
  codex"). Requires no new write, but is a **fragile heuristic**: it breaks
  the moment an operator names a `[backend.models] codex = "..."` entry that
  happens to collide with a Claude-style name, or the moment a third engine
  is added later, and it silently misclassifies rather than failing loudly.

Recommend **Option A**. It costs one more dict key on an already-existing
call site (`loop.py:255-262`) and produces a report that reads real data
instead of guessing.

---

## 7. Is a manual `atlas loop report` command sufficient for "weekly" cadence?

TRD-v3 §14 says "a recurring report"; §11/§8 state "no new hosted
infrastructure" and §12 explicitly forbids the loop growing "a scheduler, a
DAG engine."

- **Option A (recommended): `atlas loop report [--since 7d]` is a one-shot
  CLI command**, run by the operator by hand or via their own external
  scheduling (a personal `crontab` entry, a GitHub Action on a schedule
  trigger, a shell alias) — exactly the same shape `atlas loop status`
  already has. "Recurring" describes the operator's usage pattern, not new
  code inside atlas.
- **Option B: atlas itself schedules and emits the report** (e.g.
  `run_forever()` checks a wall-clock interval and calls
  `build_weekly_report()`/posts it somewhere automatically). This is new
  scheduler-shaped logic inside the "not a framework" loop — precisely the
  risk TRD-v3 §12 names and mitigates by keeping the loop a bare `while`.
  Also raises an unscoped question (post the report where? a GitHub issue
  comment? a file? stdout only?) that this TRS has not been asked to answer.

Recommend **Option A**. If the operator wants true automation later, that's
an external cron line invoking the CLI command this phase already ships —
not a reason to grow atlas's own daemon.

---

## 8. `LoopState` thread-safety under concurrency

`state.runs_today`/`dollars_today`/`consecutive_no_progress`/etc. and
`state.persist()` are mutated by plain (non-atomic) Python operations today,
safe only because `tick()` has always been single-threaded end to end.

- **Option A (recommended): pool workers return pure per-issue outcomes;
  only `tick()`'s own body, executed single-threaded after `as_completed()`
  drains the pool, mutates `state` and calls `state.persist()` exactly
  once.** No lock needed — the critical section is provably single-threaded
  by construction, not by discipline. Simplest to reason about and to test
  (T-L4.9 proves it empirically with a real pool, but the design doesn't
  *rely* on the test to be correct).
- **Option B: wrap every `LoopState` mutation (and `persist()`) in a
  `threading.Lock`**, allowing worker threads to mutate `state` directly as
  they finish. Works, but adds a lock to a codebase that has never needed
  one, increases the chance of a forgotten mutation site outside the lock
  (a classic source of the exact race this decision exists to prevent), and
  makes `state.persist()` potentially called once per worker instead of once
  per tick — more disk I/O for no benefit over Option A.

Recommend **Option A**.

---

## 9. `TickResult` widening for batch dispatch

`TickResult` is a `frozen` dataclass with singular fields
(`issue_number: int | None`, `lane: ... | None`, `pr_ref: PrRef | None`).
Batch dispatch produces zero-or-more per-issue outcomes per tick.

- **Option A (recommended): a new `BatchTickResult(results: list[TickResult])`
  wrapper.** `TickResult` itself is completely unchanged — every existing
  test/caller that pattern-matches on its fields keeps working once ported
  to read `batch.results[0]` (or asserts `len(batch.results) == 1`) for the
  concurrency=1 case, which is the regression baseline. Matches Appendix A's
  sanctioned precedent (`RunResult` widened additively, not `Pipeline`
  itself rewritten).
- **Option B: widen `TickResult` itself** to carry a list of per-issue
  fields instead of singular ones (`issue_numbers: list[int]`, etc.). This
  is a breaking change to every existing caller/test of `TickResult`,
  including `cli.py`'s own status-reporting code, for a benefit (one fewer
  dataclass) that doesn't outweigh the churn.

Recommend **Option A**.

---

## 10. Concurrency default/practical ceiling given `git worktree` lock contention

`git worktree add`/`remove` take git's own per-repo lock; running N of them
concurrently under the same `repo_root` serializes at the git level
regardless of atlas's own thread pool size. This TRS does not load-test or
tune a ceiling.

- **Option A (recommended): ship a conservative default (`concurrency = 1`
  or `2`), name the untuned ceiling explicitly in `docs/3_guides/core_concepts.md`
  or the `[loop]` config's own doc comment, and leave raising it to an
  operator willing to observe their own git-lock wait times.** No load
  testing, no benchmark suite, no auto-tuning — those are out of scope per
  the Phase Deliverables' explicit exclusion.
- **Option B: attempt to measure/tune a recommended ceiling as part of this
  phase** (e.g., a benchmark script, a documented "concurrency=N adds Xms of
  average lock wait" table). Real value, but materially larger scope than
  "more than one concurrent run... works" — the TRD's own bar — and this
  TRS's Testing Strategy already flags throughput/load testing as explicitly
  not a deliverable.

Recommend **Option A**. If the maintainer wants a load-tested ceiling, that's
a natural, separately-scoped follow-up once L4's mechanism ships.

---

## 11. `trusted_authors`: global across all targets, or per-target?

Today `LoopConfig.trusted_authors: tuple[str, ...]` is one global list,
consulted in `_pull_next_ready` regardless of which repo an issue came from
(fine when there was only ever one repo).

- **Option A (recommended): `RepoTarget.trusted_authors`, per-target.**
  Repo visibility/authorship (private vs. public, single- vs. multi-author)
  is a property of each repo independently — TRD-v3 §4 Security's own
  language already frames it that way ("if **any target repo** is public or
  multi-author"). A global list can't express "atlas is private/single-author
  but plumb is public," which is exactly the kind of case this decision
  needs to be right for before it matters, not after.
- **Option B: keep one global `[loop].trusted_authors` list**, applied
  identically to every target. Simpler config, but silently wrong the moment
  two targets have different visibility/authorship — a maintainer adding a
  public second repo could believe the allowlist protects it when it was
  tuned for the private first repo's author set (or vice versa: an
  allowlist meant only for the public repo would needlessly restrict a
  private, single-author one).

Recommend **Option A**.

---

## 12. Breaker's `consecutive_no_progress`: per batch/tick or per issue?

Today, exactly one dispatch per tick means "per tick" and "per issue" are the
same thing. At `concurrency > 1` they diverge.

- **Option A (recommended): increment once per failed issue** within the
  batch (so a tick with 3 dispatches, 2 failing, adds 2 to
  `consecutive_no_progress`, not 1). Preserves the breaker's actual intent —
  "the loop keeps failing" — proportionally to how much failing actually
  happened, rather than diluting a bad batch into a single tick-level
  data point.
- **Option B: increment once per tick regardless of batch size** (a tick
  with any failure at all counts as "one no-progress tick", same as today).
  Simpler, and leaves `no_progress_limit=3`'s existing tuning meaning
  unchanged in tick-count terms, but under-counts real failure volume at
  higher concurrency — 3 failed issues in one batch would look identical to
  1 failed issue in a `concurrency=1` tick, even though the former is a
  worse signal.

Recommend **Option A**, with the explicit operator-facing consequence named
in Security Considerations: raising `concurrency` makes the breaker trip
faster in wall-clock time for the same `no_progress_limit`/
`identical_error_limit` values, which is correct behavior, not a regression,
but worth knowing before tuning those limits at higher concurrency.

---

## 13. Proceed with this TRS while L3's T-L3.10/T-L3.11 are open?

L3 is code-complete; its own manual smoke (T-L3.10) and STATUS.md close-out
(T-L3.11) remain open, both needing a human operator session.

- **Option A (recommended, and what this TRS does): proceed.** This mirrors
  L3's own TRS's precedent exactly — it was authored and its tasks specified
  while L2's T-L2.13 was still open, naming the dependency explicitly (a
  table in its Overview & Scope) rather than re-scoping L2's manual check
  into L3's own task list or waiting for it to close. The same reasoning
  applies here: L4's *code* tasks (T-L4.1–T-L4.9) don't require T-L3.10/
  T-L3.11 to be closed to be correctly specified or even implemented; only
  L4's own manual smoke (T-L4.10) benefits from T-L3.10 having run first
  (see the plan's "Manual testing carried over" table).
- **Option B: block this TRS until T-L3.10/T-L3.11 close.** More
  conservative, but has no precedent in this project's own history (L0→L1,
  L1→L2, and L2→L3 all proceeded with the prior phase's manual checks open)
  and would stall L4's authoring for a human-operator-session dependency
  that has nothing to do with L4's own design questions.

Recommend **Option A** — already reflected in how this TRS is written.

---

## 14. Where does `.atlas/loop-state.json` live with more than one `repo_root`?

`loop_budget.py`'s `LoopState.load_or_init`/`persist` take a single
`repo_root: Path` and join it against `_LOOP_STATE_RELATIVE_PATH = Path(".atlas")
/ "loop-state.json"`. Budgets/breaker state is explicitly **process-global**
(one daemon, one set of daily caps, shared across every target) — but with
`RepoTarget`s pointing at N different local paths, there is no longer one
"the" `repo_root` to root this file under.

- **Option A: root it under the first-configured target's `local_path`**
  (`targets[0].local_path / ".atlas" / "loop-state.json"`). Minimal code
  change (still just "a `repo_root`", now derived rather than passed
  explicitly), but ties process-global state to an arbitrary ordering
  decision in the config file — reordering `[[loop.repo]]` entries would
  silently relocate (or orphan) the daemon's own budget history, which is a
  surprising and easy-to-trip footgun.
- **Option B (recommended): a dedicated daemon-home path independent of any
  target repo — specifically `~/.atlas/loop-state.json`.** `~/.atlas/` is
  **already** an established atlas-owned location (`config.py::Config.load`
  reads `~/.atlas/config.toml` as the user-wide config layer), so this
  introduces no new concept — it reuses the home atlas already has. It
  correctly reflects that this file describes the **daemon process**, not any
  one repo, matching the same reasoning that already makes budgets
  process-global rather than per-target; and it is immune to `[[loop.repo]]`
  reordering. Only one loop daemon runs at a time (concurrency is *within* a
  daemon, not across daemons), so a single user-wide file has no collision
  problem.
- **Option C: an explicit `[loop].state_path` config key.** Maximum control,
  but one more config concept to document and validate, covering no case
  Option B doesn't already cover.

> **RESOLVED 2026-07-27 — Option B.** The first draft of this TRS shipped
> this decision with *"No recommendation given — a genuine design fork with
> no majority-obvious answer"*, and asked the maintainer to pick before
> T-L4.5. That framing is kept here as the record; a recommendation was
> subsequently worked out and accepted.

**Two consequences that must be handled, not waved at:**

1. **Migrate the existing file — do not abandon it.** The daily counters
   (`runs_today`, `dollars_today`) are cheap to lose, but
   `synced_pr_outcomes` is the **idempotency guard** for PR-outcome scoring
   (`loop.py::sync_prior_prs`'s `dedupe_key` check). Dropping it means the
   next tick can re-score an already-synced merged PR — writing a duplicate
   `user_signal` score and re-relabeling a closed issue. TRD-v3 §4
   Reliability names idempotent sync as a requirement, so silently resetting
   this list would regress a shipped guarantee. T-L4.5 therefore carries a
   one-time migration (copy `<repo_root>/.atlas/loop-state.json` →
   `~/.atlas/loop-state.json` on first run if the destination is absent) as
   an acceptance criterion, not a follow-up.
2. **`atlas loop status` becomes user-wide, not per-repo.** Running it from
   any repo reports the same budget/breaker state. This is *correct* — the
   caps were always process-global, the per-repo file location just made
   them look otherwise — but it is a visible behavior change and must be
   documented (T-L4.11), not discovered.

This changes `LoopState.load_or_init`/`persist`'s call sites in both
`loop.py` and `cli.py::loop_status`, plus `loop_budget.py`'s
`_LOOP_STATE_RELATIVE_PATH` constant.
