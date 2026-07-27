# TRS — Loop Mode, Phase L4 (Scale-out)

## Phase Summary

- **TRD phase:** Phase L4 — Scale-out (`docs/2_architecture/TRD-v3.md` §14)
- **Delivers:** `v3.3` (TRD-v3 §11)
- **Goal (copied from TRD-v3 §14):** *"More than one repo, more than one
  concurrent run, and a recurring report."*
- **Dependencies:** L3 (per TRD-v3 §14: "Dependencies: L3"). **L3's code is
  complete** — T-L3.2 through T-L3.9 are implemented, unit- and
  integration-tested (520 passed, 1 xfailed; `judge_gate.py` 86%,
  `self_heal.py` 100%, `loop.py` 90%; `ruff`/`mypy --strict` clean). **T-L3.10**
  (manual smoke against a real repo with a configured `PLUMB_JUDGE_PROVIDER`)
  and **T-L3.11** (STATUS.md close-out) remain open — both need a human
  operator session, not a code fix
  (`dev/active/loop-mode-phase-L3/loop-mode-phase-L3-tasks.md`). This mirrors
  how L3's own TRS was authored while L2's T-L2.13 was still open: the
  dependency is named here, not re-scoped into this phase's tasks, and this
  TRS proceeds rather than blocking on it — see [Overview &
  Scope](#overview--scope) and
  [Pending Decision #13](./loop-mode-phase-L4-decisions.md).
- **Exit criteria (TRD-v3 §13):**
  - **#11** Second repo + concurrency. The plumb repo runs as a second target;
    `concurrency > 1` works with per-run state keys.
  - **#12** Weekly report. `plumb run stats` yields a cost-per-landed-PR +
    intervention-rate summary. Cross-engine comparison remains **tokens-only**
    for Codex (no per-model price table in v3 scope).

Full Pending Decisions text lives in
[`loop-mode-phase-L4-decisions.md`](./loop-mode-phase-L4-decisions.md) (split
out, matching L2/L3's own precedent, to keep this file under the repo's
800-line cap). Full flat task list lives in
[`loop-mode-phase-L4-tasks-detail.md`](./loop-mode-phase-L4-tasks-detail.md)
(same reason). This file covers Phase Summary through Performance
Considerations, plus Phase Deliverables.

---

## Overview & Scope

### What L4 adds, precisely

Three capabilities, each touching a gap the TRD's own Appendix A already
flags (`state.py`: "Modify (L4 only) ... Untouched in v3.0–v3.2"):

1. **A second target repo (§13 #11, first half).** `[loop].repos` today is a
   flat `tuple[str, ...]` of GitHub `owner/repo` slugs consumed only by
   `queue_gh` calls (`list_ready`, `sync`, `list_labeled`). Every **dispatch**
   call — `run_one_shot`, `run_planned_first_pass`, `WorktreeManager(repo_root)`,
   `StateStore(repo_root)`, `reconcile_orphans` — takes a single
   `repo_root: Path`, passed once from `cli.py::loop_run` (`cli.py:285`).
   **Configuring a second GitHub repo today would poll it correctly but
   dispatch its issues against the wrong local checkout** — the pairing
   between "which GitHub repo" and "which local clone" doesn't exist as data;
   it's implicit, because there has only ever been one of each. This is a
   live gap, not a hypothetical one, and L4 must close it before "the plumb
   repo runs as a second target" means anything.
2. **Concurrency > 1 (§13 #11, second half).** `LoopConfig.__post_init__`
   (`config.py:24`) hard-raises if `concurrency != 1`. `.atlas/current-run`
   (`state.py`) is a **singleton** file holding one run's `(run_id, slug,
   worktree_path, code_gen_span_id, async_gate_metric)` tuple, and
   `_sweep_orphaned_worktrees`'s orphan-retain check (`loop.py:969-1013`)
   reads that one file to decide which one worktree is "live". `tick()`
   itself claims and dispatches exactly one issue per call. All three must
   change for "`concurrency > 1` works with per-run state keys" to be true.
3. **A recurring cost-per-landed-PR + intervention-rate report (§13 #12).**
   Verified against the sibling `plumb` repo's CLI source (`plumb/cli.py:109-151`,
   2026-07-26): **`plumb run stats`'s JSON output truncates `run_id` to 8 hex
   characters and never surfaces `dollar_cost` at all** — `_RUN_STATS_COLUMNS`
   is `[run_id, task_id, kind, status, start_ts, duration_ms, span_count,
   score_count]`, and the row dict builds `"run_id": s.run_id[:8]`. The
   underlying `RunSummaryRow` the CLI reads from (`plumb/core/entities.py:242-282`)
   **does** carry full `run_id`, `dollar_cost`, `tokens_in`/`tokens_out`, and
   `parent_run_id` — the CLI simply never prints them. So `plumb run stats`,
   read literally as TRD-v3 §14 names it, **cannot produce this report** — a
   full run_id is needed to cross-reference plumb's own
   `user_signal`/`parent_run_id` lineage, and `dollar_cost` never reaches the
   CLI's output. This is the same shape of TRD-phrasing-vs-source gap L3's
   Pending Decision #3 found for `plumb judge run`, resolved the same way:
   **read plumb's storage layer directly**, not its batch CLI (see Detailed
   Component Design).

### What L4 does NOT touch

- `judge_gate.py`, `self_heal.py` — reused as-is, per issue, per repo.
  Concurrency changes how many issues run *in parallel*, not the retry
  semantics of any one of them; T-L3.8's one-retry-per-issue cap is unaffected.
- The planned lane's task-by-task multi-PR implementation loop — still out of
  scope, unchanged from L2/L3's explicit carve-out
  (`dev/archive/loop-mode-phase-L2/loop-mode-phase-L2-decisions.md` #2).
- A per-model Codex price table. TRD-v3 §3.6/§13 #12 are explicit this is
  **not** v3 scope — the report's cost dimension covers `claude` only;
  `codex` stays tokens-only. This phase reports honestly across that gap,
  it does not close it.
- Any hosted infrastructure, scheduler, or cron-like daemon logic inside
  atlas. TRD-v3 §12's anti-framework risk ("no scheduler, no DAG engine ...
  if it grows one, it has drifted from scope") applies to "weekly" exactly as
  it did to "loop": the report is a CLI command an operator runs (by hand or
  via *their own* external cron), not a new internal timer ([Pending
  Decision #7](./loop-mode-phase-L4-decisions.md)).
- `Pipeline`/`orchestrator.py` — unchanged, per Appendix A's standing rule.
  Concurrency is expressed above the `Pipeline` boundary (multiple
  independent `Pipeline` instances, one per concurrent dispatch); nothing in
  `orchestrator.py` is process-global today except through `state.py`, which
  this TRS does change.

### Manual testing carried over from L0–L3 (explicit acknowledgment)

Per the discipline L3's TRS applied to L0–L2's open manual checks: this TRS
does not re-scope L3's open items into L4 tasks, but names the dependency so
a reader doesn't assume L3 is closed.

| Open manual check | Phase | Blocks L4 how |
| --- | --- | --- |
| T-L3.10 (judge gate + retry smoke, needs `PLUMB_JUDGE_PROVIDER`) | L3 | L4's own smoke (T-L4.10) dispatches through the same `run_one_shot`/judge-gate path, now against two repos and at `concurrency>1` — if T-L3.10 never ran, T-L4.10 is the first live proof of *both* phases' exit criteria at once, a worse place to discover an L3 gap. |
| T-L3.11 (STATUS.md close-out) | L3 | Cosmetic only — no L4 code-path dependency. Named for completeness. |
| T-L0.8/T-L0.9/T-L1.1/T-L1.8/T-L2.13 | L0–L2 | All executed and closed 2026-07-27 (STATUS.md); no residual dependency. |

Running T-L3.10 before or alongside T-L4.10 is the sensible order — cheaper
to isolate an L3 gap than entangle it with two new L4 variables at once.

---

## Requirements Summary

From TRD-v3 §14 Phase L4 engineering scope summary, decomposed:

1. **Second repo**: add the plumb repo as a second target (its own backlog →
   issues), with its own local checkout and `.claude/settings.json` allowlist.
2. **Concurrency > 1**: lift the `.atlas/current-run` single-run assumption
   via per-run state keys (Appendix A); bound by a pool at `[loop].concurrency`.
3. **Weekly report**: `plumb run stats` (read: plumb's storage layer, per
   Overview & Scope) → a cost-per-landed-PR + intervention-rate report. Cost
   dimension covers `claude` only; `codex` stays tokens-only.

From TRD-v3 §13 (the binding exit bar): items #11 and #12 only.

---

## Detailed Component Design

### Modified: `src/atlas/config.py` — `LoopConfig.repos` reshaped

Today: `repos: tuple[str, ...] = ()` (GitHub slugs, gh-polling only) and
`__post_init__` hard-raises unless `concurrency == 1`.

New shape (breaking config change — [Pending Decision
#1](./loop-mode-phase-L4-decisions.md)):

```python
@dataclass(frozen=True)
class RepoTarget:
    """One loop target: a GitHub repo paired with the local clone dispatch/
    worktree/state operations run against. This pairing did not exist as data
    before L4 — there was only ever one implicit pair."""
    github: str                              # "owner/repo", passed to queue_gh
    local_path: Path                         # must exist and be a git repo
    trusted_authors: tuple[str, ...] = ()    # per-target (Pending Decision #11)

@dataclass(frozen=True)
class LoopConfig:
    repos: tuple[RepoTarget, ...] = ()
    concurrency: int = 1
    ...
    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")
```

TOML shape:

```toml
[loop]
concurrency = 2

[[loop.repo]]
github = "anant-gupta-utexas/atlas"
local_path = "/Users/anant/PersonalProjects/atlas"

[[loop.repo]]
github = "anant-gupta-utexas/plumb"
local_path = "/Users/anant/PersonalProjects/plumb"
```

This repo's own `.atlas.toml` (checked at authoring time: `repos =
["anant-gupta-utexas/atlas"]`, a flat string list) needs migrating as part of
this phase's rollout — T-L4.1's acceptance criteria, not left implicit.

### Modified: `src/atlas/loop.py` — per-target dispatch, batch tick

`tick()` widens from a single `repo_root` to a sequence of targets and, per
[Pending Decision #2](./loop-mode-phase-L4-decisions.md), claims and
dispatches a **batch** (not one issue) bounded by `cfg.loop.concurrency`:

```python
def tick(cfg: Config, state: LoopState, *, targets: Sequence[RepoTarget]) -> BatchTickResult:
    """run_forever() keeps its existing `while True: tick(); sleep()` shape —
    TRD-v3 §12's anti-framework mitigation still holds; only tick()'s own
    internals grow a bounded pool."""
```

`_pull_next_ready` widens to `_pull_ready_batch`, returning up to
`cfg.loop.concurrency` `(RepoTarget, Issue)` pairs across all targets
(same per-target `trusted_authors` skip-and-continue as today's Decision #16).

Claiming grows a **race check**: `queue_gh.claim()` is a label swap plus
assignee set, not compare-and-swap. Two concurrent claimants (two pool
workers, or two loop processes against the same repo) can both see an issue
as `atlas:ready` before either claims it:

```python
def _claim_confirmed(issue: Issue, assignee: str) -> bool:
    """Re-read the issue's assignee right after claim(). A caller that lost
    the race returns False; the issue is left atlas:working (NOT relabeled
    back to atlas:ready — the winner is actively working it). A race is
    expected traffic under concurrency, not a crash."""
```

Dispatch (`run_one_shot`/`run_planned_first_pass`) is unchanged in its own
body — only its `repo_root` argument now comes from the winning issue's own
`RepoTarget.local_path`.

**`LoopState` thread-safety is a new problem, not present at concurrency=1.**
`state.runs_today += 1`, `record_tick_outcome(...)`, `state.persist(...)`
today assume single-threaded, sequential mutation. Resolved by design, not a
lock ([Pending Decision #8](./loop-mode-phase-L4-decisions.md)): pool workers
return **pure** per-issue outcomes; only `tick()`'s own single-threaded body,
after `as_completed()` drains the pool, applies every outcome to `state` and
calls `state.persist()` **once** per tick:

```python
def _dispatch_one(target: RepoTarget, issue: Issue, cfg: Config) -> _DispatchOutcome:
    """Runs inside a pool worker. Does NOT touch LoopState — returns a pure
    result for tick()'s own body to apply after the pool drains."""

def tick(cfg, state, *, targets) -> BatchTickResult:
    ...
    with ThreadPoolExecutor(max_workers=cfg.loop.concurrency) as pool:
        futures = [pool.submit(_dispatch_one, t, i, cfg) for t, i in claimed]
        outcomes = [f.result() for f in as_completed(futures)]
    for outcome in outcomes:                       # single-threaded from here
        record_tick_outcome(state, cfg.loop, made_progress=outcome.made_progress,
                             error_signature=outcome.error_signature)
        state.runs_today += outcome.runs_delta
        state.dollars_today += outcome.cost
    state.last_tick_at = _now_iso()
    state.persist(...)                              # exactly once
    return BatchTickResult(results=[o.tick_result for o in outcomes])
```

### New dataclass: `BatchTickResult`

```python
@dataclass(frozen=True)
class BatchTickResult:
    """Wraps one TickResult per dispatched issue this tick (Pending Decision
    #9). TickResult itself is UNCHANGED — additive wrapper, matching Appendix
    A's sanctioned RunResult-widening precedent. At concurrency=1 this always
    wraps zero or one TickResult, so existing callers/tests port by
    unwrapping results[0] rather than being rewritten."""
    results: list[TickResult]
```

### Modified: `src/atlas/state.py` — per-run-keyed `current-run`

Today, `StateStore._current_run_path = repo_root / ".atlas" / "current-run"`
is a **single file**. New, additive (keyed) methods, loop-only:

```python
class StateStore:
    def write_current_run_keyed(self, run_id: str, slug: str, worktree_path: Path | None, ...) -> None:
        """Writes .atlas/runs/<run_id>/current-run — same positional body as
        write_current_run, keyed by run_id so concurrent runs don't clobber
        each other."""

    def list_current_runs(self) -> list[tuple[str, str, Path | None, str | None]]:
        """All currently-live keyed runs (glob .atlas/runs/*/current-run).
        Replaces the singleton read for the loop's orphan-sweep use case
        ONLY; attended atlas run's own read_current_run()/
        read_current_run_with_worktree() are UNTOUCHED (Pending Decision #3)
        — they keep reading the legacy singleton path, preserving
        attended-mode byte-identity."""

    def delete_current_run_keyed(self, run_id: str) -> None: ...
```

`_sweep_orphaned_worktrees` (`loop.py:969-1013`) changes its retain-check
from "the one worktree named in the one file" to "any worktree named in any
currently-live keyed run":

```python
def _sweep_orphaned_worktrees(repo_root: Path, *, ignore_current_run: bool = False) -> list[str]:
    live_paths = set()
    if not ignore_current_run:
        for _run_id, _slug, wt_path, _span_id in StateStore(repo_root).list_current_runs():
            if wt_path is not None:
                live_paths.add(wt_path.resolve())
    # sweep every worktree dir NOT in live_paths (unchanged loop body otherwise)
```

`reconcile_orphans` (`loop.py:927-966`) loops over `targets` (one `repo_root`
per target) instead of a single shared one; its `at_startup` semantics
(".atlas/current-run is by-definition stale at boot" — nothing survives a
process restart) are unaffected by concurrency.

### New module: `src/atlas/loop_report.py`

```python
"""Weekly cost-per-landed-PR + intervention-rate report (TRD-v3 §14 Phase L4,
§13 #12).

Reads plumb's storage layer DIRECTLY — the same SQLiteStorageAdapter +
plumb.config.get_settings()/ensure_data_dir() construction plumb/cli.py's own
private _get_storage() helper uses internally (verified against source,
plumb/cli.py:67-74) — NOT the `plumb run stats` CLI, which truncates run_id
to 8 hex chars and never surfaces dollar_cost (Overview & Scope). Mirrors
L3's own Pending Decision #3 (library JudgeAdapter over the `plumb judge run`
batch CLI) exactly.
"""

@dataclass(frozen=True)
class WeeklyReport:
    since: datetime
    total_runs: int
    landed_prs: int                              # runs whose issue's PR merged
    cost_per_landed_pr_claude: float | None       # None if 0 claude landings
    tokens_per_landed_pr_codex: tuple[int, int] | None  # None if 0 codex landings
    intervention_rate: float                      # Pending Decision #5
    intervention_count: int
    terminal_run_count: int                        # denominator

def build_weekly_report(cfg: Config, *, since: datetime) -> WeeklyReport:
    """
    1. storage.list_runs_with_counts(since=since, limit=<generous>) ->
       list[RunSummaryRow] — full run_id/dollar_cost/parent_run_id/tokens,
       NOT the CLI's truncated/cost-less rows.
    2. "landed" = a run reachable via parent_run_id lineage (a self-heal
       retry is the same logical dispatch as its parent) with an approved
       user_signal score anywhere in that lineage — cross-referenced against
       plumb's scores table (sync_prior_prs already wrote it; no new gh call).
    3. engine split via spans.attributes["engine"] (new tag, below), NOT
       string-matched off orchestrator_model (Pending Decision #6).
    4. cost_per_landed_pr_claude = mean(dollar_cost) over claude-engine
       landed runs; None (not 0.0, not ZeroDivisionError) if that count is 0
       — same "NULL is the honest value" discipline TRD-v3 applies to Codex
       cost (§3.3).
    5. tokens_per_landed_pr_codex: same shape, summed (tokens_in, tokens_out)
       over codex-engine landed runs, divided by landed count.
    6. intervention_rate = intervention_count / terminal_run_count, using the
       NARROW definition in Pending Decision #5 (fraction of dispatches
       needing self_heal's retry, i.e. lineages with >1 run among all
       terminal-status lineages) — not a broader "human had to manually
       unstick this" proxy, which isn't durably recorded today.
    """

def format_report(report: WeeklyReport) -> str:
    """Human-readable text, reusable by the CLI command and (optionally) a
    PR/issue comment body — same plain-text convention as
    loop.py::_format_run_summary."""
```

### New: `engine` span attribute (no plumb schema change)

`run_one_shot`'s existing `pipeline.plumb.record_span(..., attributes=...)`
call (`loop.py:255-262`) gains one more key in the `attributes` dict already
written for token-breakdown provenance: `attributes["engine"] = engine or
cfg.default_backend`. Rides the already-shipped `spans.attributes` JSON
column (plumb v1.1) — additive, no migration, extending TRD-v3 §13 #14's "no
plumb migration" invariant one phase further.

### New CLI command: `atlas loop report`

```bash
atlas loop report [--since 7d] [--format text|json]
```

Prints a `WeeklyReport` via `format_report()` or `dataclasses.asdict`. Weekly
is an operator cadence, not code atlas schedules itself — see [Pending
Decision #7](./loop-mode-phase-L4-decisions.md).

---

## API Specifications

No new HTTP/RPC surface — atlas remains local-only. "API" means the
in-process Python boundaries this phase adds or changes:

| Boundary | Direction | Contract |
| --- | --- | --- |
| `cli.py::loop_run` → `loop.tick()`/`run_forever()` | call | `run_forever(cfg, targets=cfg.loop.repos)` — widened from `repos: list[str], repo_root: Path`. Breaking change to this internal signature only; `cli.py` is the sole first-party caller (T-L4.2). |
| `loop.py::tick()` → `_dispatch_one()` (pool) | call, N ≤ `cfg.loop.concurrency` per tick | Pure function — no `LoopState` mutation inside a worker (Pending Decision #8). |
| `loop_report.py` → `plumb.adapters.storage_sqlite.SQLiteStorageAdapter` / `plumb.config.get_settings`/`ensure_data_dir` | direct import, bypassing `plumb run stats` CLI | `.list_runs_with_counts(since=..., limit=...) -> list[RunSummaryRow]` — verified against `plumb/core/ports.py`'s `StorageReader` Protocol and `plumb/core/entities.py:242` (full `run_id`, `dollar_cost`, `tokens_in/out`, `parent_run_id`, `status`, `span_count`, `score_count`). |
| `state.py::StateStore` → `.atlas/runs/<run_id>/current-run` | file I/O, new keyed path | Additive — legacy singleton path untouched for attended `atlas run` (Pending Decision #3). |
| `run_one_shot()` → `record_span(..., attributes={"engine": ...})` | call, existing call site, one more dict key | Additive — no new `PlumbIO` method (extends L2/L3's "no new `PlumbIO` method" precedent). |

**Rate limiting:** unchanged shape, wider fan-out. `cfg.loop.concurrency`
dispatches run in parallel, each with its own judge calls (L3) and `gh` calls
(per-target); total per-tick load scales linearly with concurrency, bounded
by the same daily budget/breaker (see Security Considerations for its
semantics under concurrency).

**Authentication:** unchanged — `gh auth` (per target's own `github` slug),
Codex/Claude CLI auth, `PLUMB_JUDGE_PROVIDER`. A second target needs its own
`gh` scope (covered by an existing session with access to both repos) and its
own checked-in `.claude/settings.json` allowlist.

---

## Database Design

**No new plumb schema.** TRD-v3 §13 #14 ("No plumb migration for v3.0–v3.2")
is scoped through `v3.2`; L4 (`v3.3`) adds none either — the `engine`
discriminator rides the already-shipped `spans.attributes` JSON column, and
the report reads existing `runs`/`scores` rows through the existing
`StorageReader` port.

**New atlas-owned local files:**

| File / config | Purpose | Lifecycle |
| --- | --- | --- |
| `.atlas/runs/<run_id>/current-run` | Per-run state key (loop-dispatched runs only); replaces the singleton for the orphan-sweep retain-check under concurrency | Written on dispatch start, deleted on end/cleanup — same lifecycle, keyed |
| `.atlas.toml [[loop.repo]]` table array | Replaces `[loop] repos = [...]` (breaking, Decision #1) | User-authored; this repo's own config migrates in T-L4.1 |
| Second target's `.claude/settings.json` | Loop-run allowlist for the plumb repo checkout | User-authored, checked into that repo — same pattern, now exercised twice |
| `~/.atlas/loop-state.json` | **Moved** from `<repo_root>/.atlas/loop-state.json` (Decision #14) — process-global budget/breaker state no longer belongs under any one target's checkout | Migrated once on first run (the `synced_pr_outcomes` idempotency guard must survive the move — T-L4.5); rewritten every tick thereafter |

**Data Access Patterns:** `loop_report.py`'s one new read is a single bounded
`list_runs_with_counts(since=..., limit=...)` call — the same query shape
`plumb run stats` already makes, read at the storage-port level instead of
through the CLI's row-formatting layer. No new indexes needed.

**Migration Strategy:** none plumb-side. Atlas-side, `.atlas.toml`'s `[loop]
repos` **must** migrate for any repo with an existing flat-string list (this
repo included) — `Config.load()` should either hard-fail on the old shape
with a clear message, or accept both shapes during a transition window (see
[Pending Decision #1](./loop-mode-phase-L4-decisions.md)).

---

## Algorithm & Logic Design

### Batched tick with bounded concurrency (pseudocode)

```
function tick(cfg, state, targets):
    reset_daily_counters_if_new_day(state)
    for target in targets: sync_prior_prs(target.github, state)   # unchanged shape, over RepoTarget now
    if breaker_open or budget_exhausted: return early (wrapped in a single-result BatchTickResult)

    batch = _pull_ready_batch(targets, cfg.loop, limit=cfg.loop.concurrency)
    if not batch: return idle (wrapped)

    claimed = []
    for target, issue in batch:
        queue_gh.claim(issue, assignee=current_gh_user())
        if _claim_confirmed(issue, assignee): claimed.append((target, issue))
        # else: lost the race — leave atlas:working, do not touch it further

    if not claimed: return idle-ish (detail="lost every claim race")

    with ThreadPoolExecutor(max_workers=cfg.loop.concurrency) as pool:
        futures = [pool.submit(_dispatch_one, t, i, cfg) for t, i in claimed]
        outcomes = [f.result() for f in as_completed(futures)]   # pure; no LoopState touch inside

    for outcome in outcomes:                                      # single-threaded from here — see
        record_tick_outcome(state, cfg.loop, ...)                  # Component Design for why this
        state.runs_today += outcome.runs_delta                     # ordering removes the need for a lock
        state.dollars_today += outcome.cost
    state.last_tick_at = _now_iso()
    state.persist()                    # ~/.atlas/loop-state.json (Decision #14)
    return BatchTickResult(results=[o.tick_result for o in outcomes])
```

This surfaces one more thing not named in the plan text above:
**`.atlas/loop-state.json` is itself rooted at a single `repo_root` today**
(`loop_budget.py`'s `_LOOP_STATE_RELATIVE_PATH`, joined against whatever
`repo_root` is passed to `persist`/`load_or_init`). With multiple targets
there is no longer one obvious `repo_root` to root the **process-global**
budget/breaker state under.

**Resolved ([Decision #14](./loop-mode-phase-L4-decisions.md), 2026-07-27):
it moves to `~/.atlas/loop-state.json`** — the user-wide home
`Config.load()` already reads `~/.atlas/config.toml` from, so no new concept
is introduced. Budgets were always process-global; the per-repo file location
merely made them look per-repo. Two consequences are load-bearing and carried
into the task list rather than left as prose:

- **The existing file must be migrated, not abandoned** (T-L4.5). Its
  `synced_pr_outcomes` list is the idempotency guard for PR-outcome scoring
  (`sync_prior_prs`'s `dedupe_key`); losing it lets the next tick re-score an
  already-synced merged PR, regressing TRD-v3 §4 Reliability's idempotent-sync
  guarantee. Daily counters are cheap to lose; this list is not.
- **`atlas loop status` becomes user-wide, not per-repo** (T-L4.11 documents
  it) — correct behavior that looks like a bug if unannounced.

### Weekly report aggregation (pseudocode)

```
function build_weekly_report(cfg, since):
    storage = SQLiteStorageAdapter(plumb_db_path, clock=RealClock())
    summaries = storage.list_runs_with_counts(since=since, limit=10_000)  # full RunSummaryRow

    lineages = group_by_root_run_id(summaries)   # dict[root_run_id, list[RunSummaryRow]]
    landed = intervened = terminal = 0
    claude_costs, codex_tokens = [], [0, 0]

    for root_id, runs in lineages.items():
        if any(r.status not in ("success", "failure") for r in runs):
            continue   # still in flight — excluded from both numerator and denominator
        terminal += 1
        if len(runs) > 1: intervened += 1        # a retry happened somewhere (Pending Decision #5)

        if lineage_has_approved_user_signal(root_id):   # plumb scores query
            landed += 1
            final = runs[-1]
            engine = read_engine_attribute(final)         # spans.attributes["engine"]
            if engine == "claude" and final.dollar_cost is not None:
                claude_costs.append(final.dollar_cost)
            elif engine == "codex":
                codex_tokens[0] += final.tokens_in or 0
                codex_tokens[1] += final.tokens_out or 0

    return WeeklyReport(
        since=since, total_runs=len(summaries), landed_prs=landed,
        cost_per_landed_pr_claude=mean(claude_costs) if claude_costs else None,
        tokens_per_landed_pr_codex=tuple(t / landed for t in codex_tokens)
            if landed and codex_tokens != [0, 0] else None,
        intervention_rate=(intervened / terminal) if terminal else 0.0,
        intervention_count=intervened, terminal_run_count=terminal,
    )
```

---

## Error Handling & Edge Cases

| Case | Handling |
| --- | --- |
| Two claimants race the same issue | `_claim_confirmed()` re-reads the assignee right after `claim()`; the loser skips silently, logged at INFO (expected traffic under concurrency, not an error). |
| `git worktree add`/`remove` contention at concurrency > 1 | Both take git's own per-repo lock and serialize; not parallelized by this design. Practical ceiling before lock-wait dominates is untuned ([Pending Decision #10](./loop-mode-phase-L4-decisions.md)) — named, not fixed. |
| Second target has no `.claude/settings.json` yet | Fail closed, per-target, before first dispatch — mirrors `CodexBackend.preflight()`'s discipline, applied to a missing allowlist instead of missing auth. |
| A configured `RepoTarget.local_path` doesn't exist or isn't a git repo | Fail loudly at `Config.load()`/`atlas loop run` startup, not a silent per-tick skip. |
| `LoopState` touched by more than one thread | Prevented by design (workers return pure outcomes; only `tick()`'s single-threaded body mutates/persists) — a dedicated test (T-L4.9) proves it under real interleaving rather than trusting the design on paper. |
| `.atlas/runs/<run_id>/current-run` orphaned mid-dispatch, one of several live | `_sweep_orphaned_worktrees` reads all `.atlas/runs/*/current-run` via `list_current_runs()`, retains every worktree any names, sweeps the rest. |
| Weekly report window has zero landed PRs (either engine) | Reports `None` ("no data"), not `0.0` and not a crash — matching TRD-v3's "`NULL` is honest, `0.0` is not" discipline for Codex cost. |
| A run's `code_gen` span predates this phase (no `engine` attribute) | Excluded from the per-engine split, counted in `total_runs` only — a real boundary in what's newly tagged, not a bug. |
| A retry's `parent_run_id` falls outside the `--since` window | Treated as its own root for aggregation (best-effort) — `--since` is a convenience filter, not a lineage-completeness guarantee; flagged as a known imprecision at the report's edges. |

---

## Dependencies & Interfaces

| Dependency | Type | Notes |
| --- | --- | --- |
| `plumb.adapters.storage_sqlite.SQLiteStorageAdapter`, `plumb.config.get_settings`/`ensure_data_dir` | In-process import (sibling repo, path-installed) | **New** — mirrors `plumb/cli.py::_get_storage()`'s own construction (verified against source) rather than shelling `plumb run stats`, because the CLI drops `dollar_cost` and truncates `run_id`. |
| `plumb.core.entities.RunSummaryRow` | In-process import | Read-only projection; no write path. |
| `concurrent.futures.ThreadPoolExecutor` | stdlib | New — the only concurrency primitive this phase introduces. Compatible with CLAUDE.md's "Sync-only in v1 (no async/await)" rule because dispatch is subprocess-bound (blocks on I/O, releases the GIL), not CPU-bound — the same reasoning that already lets `judge_gate`'s synchronous calls sit inline in v1/v2/L3's sync codebase. |
| `queue_gh.claim()` | Internal | No signature change — L4 adds a caller-side re-read (`_claim_confirmed`), expressible with existing adapter surface, not a new `queue_gh` method. |
| `[[loop.repo]]` config table array | `.atlas.toml`/`~/.atlas/config.toml` | Breaking shape change (Pending Decision #1); this repo's own config migrates in T-L4.1. |

---

## Security Considerations

- **Per-target allowlists, now actually plural.** TRD-v3 §3.6/§7 specify a
  singular "target repo `.claude/settings.json`" because there was only ever
  one. L4 exercises this requirement's plural form for real — a missing
  allowlist on the second target must fail closed for **that target only**.
- **The "directory boundary, not a filesystem sandbox" risk (TRD-v3 §3.5) has
  a larger blast radius.** `_assert_main_checkout_untouched` compares one
  before/after HEAD sha for one `repo_root`; with N concurrent dispatches
  across up to 2 `repo_root`s, it must run **per-target** — an agent
  dispatched against the plumb checkout committing into the operator's atlas
  checkout (or vice versa) is exactly T-L2.13's live failure mode, now with a
  second checkout to get wrong.
- **Prompt injection (TRD-v3 §4), now per-target.** "Private, single-author"
  is a per-repo property — if the plumb repo (or any future target) ever
  goes public/multi-author, **that target's own** `trusted_authors` allowlist
  becomes mandatory, hence `RepoTarget.trusted_authors` rather than one
  global field ([Pending Decision #11](./loop-mode-phase-L4-decisions.md)).
- **Budgets are process-global by design, unchanged in meaning.**
  `max_dollars_per_day`/`max_runs_per_day` are shared `LoopState` counters
  across all targets and dispatches — concurrency changes how fast the cap is
  spent, not what it means.
- **The breaker's practical sensitivity changes at concurrency > 1, not its
  code.** Incrementing `consecutive_no_progress` once per failed **issue**
  within a batch (recommended, [Pending Decision
  #12](./loop-mode-phase-L4-decisions.md)) means a single bad tick at
  `concurrency=3` can trip the breaker in one pass where it used to take
  three. Not a bug — an operator raising `concurrency` should know the
  breaker fires faster in wall-clock terms.

---

## Testing Strategy

- **`loop_report.py`: 90%+** — pure aggregation over faked `RunSummaryRow`
  lists + faked score/attribute lookups; no real plumb DB needed for unit
  tests.
- **`loop.py`/`config.py`/`state.py` deltas: 85%+**, matching each file's
  existing bar — every new branch (claim-race loss, per-target routing,
  keyed current-run read/write, single-threaded-mutation ordering) via fakes.
- **A dedicated concurrency-safety invariant test (T-L4.9)**, matching
  T-L3.8's "explicit, not incidental" precedent: run `tick()` at
  `concurrency=3` with three fake dispatches and staggered delays via a
  **real** `ThreadPoolExecutor` (not mocked — the point is real interleaving),
  assert `state.runs_today`/`dollars_today` land on the exact expected sum
  with no lost updates and `state.persist()` called exactly once.
- **Full existing v1/v2/L0–L3 suite must stay green with `concurrency=1` + a
  single-target config as the regression baseline** — the exact
  configuration every existing test already exercises. A `tick()` call in
  that shape must produce a `BatchTickResult` wrapping exactly the same
  single `TickResult` today's tests already assert on (`results[0]`).
- **Manual smoke (T-L4.10)**: a real second-repo dispatch against the
  plumb repo's own backlog, and a real `concurrency=2` run racing two issues,
  to prove claim-race handling and thread-safety hold outside of fakes.
  Needs a human operator session, same as T-L3.10/T-L2.13/T-L1.8/T-L0.8.

**Mocking strategy:** `SQLiteStorageAdapter` is faked at the `loop_report.py`
boundary (a fake `.list_runs_with_counts()`), not at plumb's SQLite layer —
matches how L3 fakes the `JudgeAdapter` Protocol rather than plumb's storage
internals.

---

## Performance Considerations

- **Concurrency's entire point is throughput** — up to `cfg.loop.concurrency`
  issues dispatch in parallel per tick. Daily budget/breaker caps remain the
  actual spend ceiling; concurrency changes how fast it's reached, not what
  it is.
- **`git worktree` operations serialize per-repo regardless of concurrency**
  — concurrency parallelizes agent-CLI subprocess time, not git operations
  themselves. The practical ceiling before lock-wait dominates is unknown and
  untuned this phase ([Pending Decision #10](./loop-mode-phase-L4-decisions.md)).
- **The weekly report is a single bounded query, run on demand** — no
  polling, no busy-wait, no interval atlas drives itself; `atlas loop report`
  is invoked manually (or via the operator's own cron), matching `atlas loop
  status`'s existing on-demand shape.
- **Multi-repo polling is not new load, just now exercised for real.**
  `tick()`'s sync/pull loops already iterate `for repo in repos`
  (`loop.py:660-666`, `:826-839`) — L4 changes what's inside that loop
  (`RepoTarget` instead of a plain string), not its shape or cost.

---

## Tasks

Full flat task list (T-L4.1–T-L4.11) with Acceptance Criteria, Files to
Create/Modify, Dependencies, and Testing Requirements lives in
[`loop-mode-phase-L4-tasks-detail.md`](./loop-mode-phase-L4-tasks-detail.md)
— split out to keep this file under the repo's 800-line cap. Progress
checkboxes live in
[`loop-mode-phase-L4-tasks.md`](./loop-mode-phase-L4-tasks.md).

One-line index:

| # | Task | Effort |
| - | --- | --- |
| T-L4.1 | Reshape `[loop].repos` → `RepoTarget` (github + local_path + trusted_authors); migrate this repo's own `.atlas.toml` | M |
| T-L4.2 | Route `tick()`/`run_forever()`/`reconcile_orphans` per-target instead of a single `repo_root` | L |
| T-L4.3 | `state.py`: per-run-keyed `.atlas/runs/<run_id>/current-run` + `list_current_runs()`; attended `atlas run`'s singleton path stays untouched | L |
| T-L4.4 | Lift `LoopConfig.__post_init__`'s `concurrency != 1` guard; add claim-race re-check | M |
| T-L4.5 | Concurrent dispatch: bounded thread pool inside `tick()`; `LoopState` mutation stays single-threaded post-pool; `BatchTickResult` wrapper | L |
| T-L4.6 | Second target repo: plumb repo's own `atlas:ready` backlog + checked-in `.claude/settings.json` allowlist | S (partly operator) |
| T-L4.7 | `loop_report.py`: `build_weekly_report()` over direct `SQLiteStorageAdapter` access; lineage/engine joins | L |
| T-L4.8 | `atlas loop report [--since] [--format]` CLI command | S |
| T-L4.9 | Concurrency-safety invariant test (explicit, real thread pool) | S |
| T-L4.10 | Manual smoke: real second-repo dispatch + real `concurrency=2` run | M (manual) |
| T-L4.11 | Update STATUS.md, tag `v3.3`, close out the phase | S |

---

## Phase Deliverables

- Working multi-repo, `concurrency > 1`-capable loop daemon (`RepoTarget`
  config, per-target dispatch/state/worktree routing, bounded thread-pool
  batch dispatch with race-safe claiming and single-threaded `LoopState`
  mutation) plus `loop_report.py` (`atlas loop report`), delivering TRD-v3
  `v3.3` (§11).
- Tests passing: new unit tests for `loop_report.py` and config/state/loop
  deltas, plus a dedicated real-thread-pool concurrency-safety invariant
  test, all green in CI; full existing v1/v2/L0–L3 suite still green with
  `concurrency=1` + single-target config as the regression baseline.
- Documentation updated: `STATUS.md` phase-close entry (T-L4.11); a
  `.atlas.toml` migration note for the breaking `[loop].repos` shape change
  (BACKLOG.md or a short migration addendum); `system_design.md`/
  `docs/3_guides/core_concepts.md` updated if implementation finds either doc
  makes a claim this phase supersedes.
- **Explicitly NOT a deliverable of this phase**: a Codex per-model price
  table (cost stays tokens-only for that engine); any scheduler/cron wiring
  for "weekly" cadence inside atlas itself (operator-driven, Pending Decision
  #7); load-testing or tuning `[loop].concurrency` beyond "it works and is
  race-safe" — no throughput benchmarking is in scope.

---

## Pending Decisions & Clarifications

**✅ All 14 resolved (maintainer, 2026-07-27)** — every recommendation was
accepted as written. See
[`loop-mode-phase-L4-decisions.md`](./loop-mode-phase-L4-decisions.md) for the
full text, options weighed, and rationale (kept in place rather than trimmed
to the winner). One-line index of what was decided:

| # | Decision | Resolution |
| - | --- | --- |
| 1 | `[loop].repos` config shape: breaking `[[loop.repo]]` table array vs. non-breaking parallel `local_paths` map | ✅ Breaking table array (Option A) |
| 2 | Concurrency shape: widen `tick()` to claim+dispatch a batch vs. N parallel `tick()` loops in `run_forever()` | ✅ Widen `tick()` (Option A) |
| 3 | Does attended `atlas run` migrate to the keyed `current-run` path? | ✅ No — keep untouched, additive-only |
| 4 | Weekly report data source: direct `SQLiteStorageAdapter` import vs. shelling `plumb run stats` | ✅ Direct import (Option A) |
| 5 | "Intervention rate" operational definition given available data | ✅ Narrow: fraction of dispatches needing a self-heal retry; narrowing labeled in the report's own output |
| 6 | Engine discriminator for the report's per-engine split | ✅ New `spans.attributes["engine"]` tag (Option A) — never inferred from model name |
| 7 | Is a manual `atlas loop report` command sufficient for "weekly" cadence? | ✅ Yes — no scheduler, matches §12 |
| 8 | `LoopState` thread-safety under concurrency | ✅ Workers return pure outcomes; `tick()` body mutates/persists once. No lock |
| 9 | `TickResult` widening for batch dispatch | ✅ New `BatchTickResult` wrapper (additive) |
| 10 | Concurrency default/practical ceiling given `git worktree` lock contention | ✅ Not tuned this phase — conservative default, gap named in docs |
| 11 | `trusted_authors`: global or per-target? | ✅ Per-target (`RepoTarget.trusted_authors`) |
| 12 | Breaker's `consecutive_no_progress`: per batch/tick or per issue? | ✅ Per issue — **operator-visible:** breaker trips faster at higher concurrency (T-L4.11 documents it) |
| 13 | Proceed with this TRS while L3's T-L3.10/T-L3.11 are open? | ✅ Yes — name the dependency, don't block (L3's own precedent) |
| 14 | Where does `.atlas/loop-state.json` (process-global budget/breaker state) live with more than one `repo_root`? | ✅ `~/.atlas/loop-state.json` (Option B), **with a mandatory one-time migration** of the existing file — its `synced_pr_outcomes` list is the PR-outcome idempotency guard. **Operator-visible:** `atlas loop status` becomes user-wide (T-L4.11 documents it). *Changed from the first draft's "no recommendation given" — see the decisions file.* |
