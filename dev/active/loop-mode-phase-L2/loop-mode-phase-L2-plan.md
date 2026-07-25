# TRS — Loop Mode, Phase L2: The loop daemon

**Source TRD:** [`docs/2_architecture/TRD-v3.md`](../../../docs/2_architecture/TRD-v3.md) §14 Phase L2
**Prior phases:** [`loop-mode-phase-L0`](../loop-mode-phase-L0/) and [`loop-mode-phase-L1`](../loop-mode-phase-L1/) — both code-complete, currently in code review (`loop-mode-code-review.md`, verdict: **Approve**, one Medium + four Low/Nit findings, none blocking). L2 is the first phase to actually *call* the primitives L0/L1 built.
**Pending Decisions:** split into [`loop-mode-phase-L2-decisions.md`](./loop-mode-phase-L2-decisions.md) to keep this file under the repo's file-size cap — read that file before starting implementation; it is normative, not optional background.

---

## Phase Summary

**Phase L2 — The loop daemon → delivers `v3.1`**

> Goal (copied from TRD-v3 §14): *"The poll-dispatch-deliver-sync loop — the core deliverable."*

**Dependencies:** L1 (code-complete; manual off-CI checks T-L1.1/T-L1.8 still open — see Decision #1 in the decisions file for how this TRS treats that).
**Delivers:** PRD release `v3.1` — the loop daemon end-to-end (TRD-v3 §11).
**Exit criteria (TRD-v3 §13):** items 5 (zero-touch delivery headline), 6 (two-lane routing), 7 (budgets & breaker), 8 (crash recovery).

---

## Overview & Scope

L0 built the primitives (telemetry, permission profile, `Deliverer`). L1 built the second engine and the ungated one-shot workflow. **Neither has a caller yet** — the L1 code review confirms this explicitly: *"`parse_usage`, `codex_usage_to_tokens`, `Deliverer`, and `record_span(tokens=...)` have zero production call sites... the thing to watch is L2: these primitives have been unit-tested but never integration-proven through the runner."* L2 is that driver. It is the phase where atlas stops being "a CLI a human runs" and becomes "a process that runs `atlas run` on the human's behalf, one GitHub issue at a time."

The deliverable is small and specific: a `gh` adapter (`queue_gh.py`), a driver module (`loop.py`) implementing `tick()`/`run_forever()`/`reconcile_orphans()`, a triage router (label-wins-else-classify), budget + circuit-breaker enforcement, a new `[loop]` config block, and a `atlas loop run|start|stop|status|attach` CLI surface (tmux wrapper for the detached forms). By the end of L2, filing a `wf:quick`-labeled issue and running `atlas loop start` produces a PR with zero further keystrokes — the headline smoke test (TRD-v3 §13 item 5).

**In scope:**
- `queue_gh.py` — the `gh` CLI adapter (`list_ready`, `claim`, `deliver_pr`, `comment`, `sync`, `relabel`), the *only* point of contact with `gh` (TRD-v3 §3.1).
- `loop.py` — `tick()` (sync-first → pull → triage → claim → dispatch → deliver → comment/relabel), `run_forever()`, `reconcile_orphans()`. One issue per tick, sequential (`concurrency=1`).
- Triage router: `wf:quick`/`wf:planned` label wins; else a single haiku classify call (`RAW:`-style, not agentic).
- `[loop]` config block added to `Config` (extends `config.py`, TRD-v3 §7).
- `atlas loop run|start|stop|status|attach` CLI command group in `cli.py` (tmux subprocess wrapper for `start`/`stop`/`attach`; `run` calls `run_forever()` directly).
- Budgets (`max_runs_per_day`, `max_dollars_per_day` from **in-memory** cost accumulation — not `runs.dollar_cost`, per TRD-v3 §3.6/§12) and a circuit breaker (`no_progress_limit`, `identical_error_limit`, `cooldown_min`).
- One-shot lane wiring: `loop.py` constructs and drives `Pipeline(loop_dev)` exactly as `cli.py::run` does, then calls `Deliverer.deliver()` on `RunResult.status == "success"`.
- Planned lane's **first-pass-only** behavior: produce the TRS triad via `dev-docs-be`, open a plan-only PR, **stop** (no `code_gen` this pass). Full planned-lane task-by-task implementation loop (subsequent passes picking up a committed TRS) is **out of scope** — see Decision #2.
- Idempotent sync via local dedupe (`issue+pr+outcome`) — the interim pattern until plumb v1.1's durable idempotent scoring.
- Tests: faked `gh`/`subprocess`/`time` — the full state machine, budget/breaker cutoffs, orphan reconciliation.

**Out of scope (see "What this TRS deliberately does NOT cover" below):** self-healing / diagnosis-injected retry, pre-PR judge gate, router v1 score-informed routing (all Phase L3); second repo, `concurrency > 1`, weekly report (Phase L4); a per-model price table (never in v3, per L1's Resolved Decision #10, carried forward); the planned lane's task-by-task subsequent-pass loop (Decision #2); fixing the L1 code review's open findings beyond what L2 itself must touch to avoid inheriting them silently (Decision #3).

---

## Requirements Summary

From TRD-v3 §14 Phase L2 engineering scope summary, decomposed:

1. `queue_gh.py` (§3.1): the `gh` adapter (`list_ready`/`claim`/`deliver_pr`/`comment`/`sync`/`relabel`).
2. `loop.py` (§3.5): `tick()` (sync-first → pull → triage → claim → dispatch → deliver → comment/relabel) + `run_forever()` + `reconcile_orphans()`. One issue per tick; sequential.
3. Triage router (§3.2): `wf:*` label wins, else haiku classify.
4. `[loop]` config (§7) — extend the frozen `Config`.
5. CLI (§3.8): `atlas loop run|start|stop|status|attach` (tmux wrapper for start/stop/attach).
6. Budgets + circuit breaker (§3.5, §5).
7. Tests: faked `gh`/`subprocess`/`time`; the full state machine + budget/breaker + orphan reconciliation.

Exit criteria (TRD-v3 §13 items 5–8, restated):
- **#5 Zero-touch delivery (headline).** One `atlas:ready` issue → `atlas loop start` → a PR appears (`Closes #n`) with a plumb `run_id` comment, zero keystrokes between labeling and reviewing. Merging it makes the next tick write a `user_signal` success and close the issue. **Cost half requires plumb P1-a** — until then L2 reports tokens, not dollars.
- **#6 Two-lane routing works.** A `wf:quick` issue yields one PR; a `wf:planned` issue yields a plan-only PR (triad + Pending Decisions) and the loop stops.
- **#7 Budgets & breaker.** Per-day cost/run caps halt dispatch; the breaker opens on no-progress/identical-error thresholds and resumes after cooldown.
- **#8 Crash recovery.** Killing the loop mid-run and restarting resets the stranded issue and prunes its worktree.

---

## Detailed Component Design

### Classes/Modules Structure

```
src/atlas/
├── queue_gh.py              # NEW — gh CLI adapter (§3.1)
├── loop.py                  # NEW — tick/run_forever/reconcile_orphans + triage + budgets + breaker (§3.5)
├── triage.py                 # NEW — label-wins-else-classify router (Decision #4: split out, not inlined)
├── config.py                # MODIFY — add LoopConfig + Config.loop field
├── cli.py                   # MODIFY — register `atlas loop` Typer sub-app
├── cli_backend.py           # UNCHANGED (L1 shipped CodexBackend; L2 only *selects* engine per label)
├── deliverer.py             # UNCHANGED (L0 shipped Deliverer/GhPrDeliverer; L2 is its first caller)
├── orchestrator.py          # UNCHANGED (L1 shipped RunResult; L2 is its first caller)
├── workflows/
│   └── loop_dev.yaml        # UNCHANGED (L1 shipped this; L2 is its first automated caller)
tests/unit/
├── test_queue_gh.py         # NEW — list/claim/deliver_pr/comment/sync/relabel, faked gh subprocess
├── test_loop.py             # NEW — tick() state machine, triage, budgets, breaker, reconcile_orphans
├── test_triage.py           # NEW — label-wins, classify-fallback
└── test_config.py           # MODIFY — [loop] section parsing
tests/integration/
└── test_loop_e2e.py         # NEW — one full tick, faked gh + faked Pipeline, asserts PR-shaped output
tests/fixtures/
└── gh_json/                  # NEW — captured `gh issue list --json ...` / `gh pr create` output shapes
    ├── issue_list.json
    ├── issue_list_empty.json
    ├── pr_view_merged.json
    ├── pr_view_closed.json
    └── pr_view_open.json
```

No new top-level orchestration framework — `loop.py` is a `while` loop over `tick()`, `tick()` is a linear function, matching TRD-v3 §12's explicit anti-framework risk mitigation.

### Method Signatures

```python
# queue_gh.py

@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: frozenset[str]
    repo: str


@dataclass(frozen=True)
class PrStatus:
    """One in-flight issue's PR state, as read by sync()."""
    issue: Issue
    outcome: Literal["merged", "closed_unmerged", "open"]
    pr_number: int | None


class GhCliError(Exception):
    """Raised when a `gh` invocation fails (non-zero exit) or times out."""


def list_ready(repo: str, *, timeout_s: int = 30) -> list[Issue]:
    # gh issue list --repo <repo> --label atlas:ready --state open --json number,title,body,labels

def claim(issue: Issue, *, assignee: str, timeout_s: int = 30) -> None:
    # gh issue edit <n> --repo <repo> --remove-label atlas:ready --add-label atlas:working
    #   --add-assignee <assignee>   (one combined call — Decision #5)

def deliver_pr(issue: Issue, *, branch: str, title: str, body: str, repo_root: Path) -> PrRef:
    # Thin pass-through to Deliverer.deliver() (L0) — queue_gh does NOT reimplement PR creation.

def comment(issue: Issue, *, body: str, timeout_s: int = 30) -> None:
    # gh issue comment <n> --repo <repo> --body <body>   (run_id + score summary)

def sync(repo: str, *, timeout_s: int = 30) -> list[PrStatus]:
    # For every atlas:working issue with a linked PR:
    #   read PR state via `gh pr view <pr> --json state,mergedAt`
    #   merged -> "merged"; closed (not merged) -> "closed_unmerged"; still open -> "open"

def relabel(issue: Issue, *, state: Literal["done", "rejected", "blocked", "ready"], timeout_s: int = 30) -> None:
    # -atlas:working +atlas:<state>. state="done" also closes the issue (Decision #12).
```

```python
# triage.py

@dataclass(frozen=True)
class TriageResult:
    lane: Literal["quick", "planned"]
    source: Literal["label", "classify"]
    rationale: str | None  # populated only when source == "classify"


def triage(issue: Issue, *, plumb: PlumbIO, run_id: str) -> TriageResult:
    # 1. wf:quick in issue.labels -> ("quick", "label", None)
    # 2. wf:planned in issue.labels -> ("planned", "label", None)
    # 3. both present -> ("planned", "label", None) + warning logged (collision resolution)
    # 4. neither present -> classify() (single haiku RAW: call via CliBackend directly, Decision #13)
    # 5. classify() result recorded as a plumb span (span_kind="plan", name="triage") per TRD-v3 §3.2
```

```python
# loop.py

@dataclass(frozen=True)
class TickResult:
    action: Literal["idle", "dispatched", "synced", "breaker_open", "budget_exhausted"]
    issue_number: int | None
    lane: Literal["quick", "planned"] | None
    pr_ref: PrRef | None
    detail: str


@dataclass
class LoopState:
    """Mutable, persisted-to-disk loop state — survives process restarts.

    Distinct from RunContext/RunResult (per-run) — this is per-loop-process.
    Persisted as .atlas/loop-state.json (Decision #6: JSON file, not a new
    plumb table — no plumb schema change permitted in v3.0-v3.2, TRD-v3 §13 #14).
    """
    runs_today: int
    dollars_today: float          # summed from in-process total_cost_usd (§3.6) — NOT runs.dollar_cost
    day: str                       # ISO date; resets counters on rollover
    consecutive_no_progress: int
    consecutive_identical_errors: int
    last_error_signature: str | None
    breaker_open_until: str | None  # ISO timestamp; None when closed
    last_tick_at: str | None
    synced_pr_outcomes: list[str]   # "{issue}:{pr}:{outcome}" dedupe keys


def tick(cfg: LoopConfig, state: LoopState, *, repos: list[str]) -> TickResult:
    # 1. sync_prior_prs(cfg, state)     — merged -> user_signal 1.0 + relabel done + close issue
    #                                      closed_unmerged -> user_signal 0.0 + relabel rejected
    #                                      idempotent via local dedupe (issue+pr+outcome)
    # 2. if breaker_open(state, cfg): return TickResult("breaker_open", ...)
    # 3. if budget_exhausted(state, cfg): return TickResult("budget_exhausted", ...)
    # 4. issue = pull_next_ready(repos)  — first match across repos; None -> TickResult("idle", ...)
    # 5. result = triage(issue, ...)     — label wins, else classify
    # 6. claim(issue, assignee=<self>)   — BEFORE any Pipeline construction (crash-safety ordering)
    # 7. dispatch:
    #      quick   -> run_one_shot(issue, engine, state)     -> Deliverer.deliver() on success
    #      planned -> run_planned_first_pass(issue, state)   -> plan-only PR -> STOP (no code_gen)
    # 8. comment(issue, body=<run_id + score summary>) — on success AND failure (Decision #14)
    # 9. update LoopState (runs_today, dollars_today, breaker counters); persist to disk
    # 10. return TickResult("dispatched", ...)

def run_forever(cfg: LoopConfig, *, repos: list[str]) -> None:
    state = LoopState.load_or_init(cfg)
    reconcile_orphans(cfg, repos=repos)
    while True:
        if breaker_open(state, cfg):
            time.sleep(cfg.poll_interval_s)   # re-check each interval, not one long sleep
            continue
        try:
            result = tick(cfg, state, repos=repos)
        except Exception:                      # last-resort safety net (Decision #18)
            _logger.exception("tick() raised unexpectedly")
            result = None
        _log_tick(result)
        time.sleep(cfg.poll_interval_s)

def reconcile_orphans(cfg: LoopConfig, *, repos: list[str]) -> list[str]:
    # atlas:working issue with NO linked PR -> relabel(state="ready")
    # .atlas/worktrees/* not matching a currently atlas:working issue -> WorktreeManager.cleanup()
    #   (best-effort; log on failure, don't crash startup)

def budget_exhausted(state: LoopState, cfg: LoopConfig) -> bool: ...
def breaker_open(state: LoopState, cfg: LoopConfig) -> bool: ...
def record_tick_outcome(state: LoopState, cfg: LoopConfig, *, made_progress: bool, error_signature: str | None) -> None:
    # made_progress=False -> consecutive_no_progress += 1
    # made_progress=True  -> both counters reset to 0
    # error_signature repeats identical_error_limit times consecutively -> open breaker
    # consecutive_no_progress hits no_progress_limit -> open breaker
    # opening the breaker sets breaker_open_until = now + cooldown_min
```

```python
# config.py — additions

@dataclass(frozen=True)
class LoopConfig:
    repos: tuple[str, ...] = ()
    poll_interval_s: int = 60
    max_runs_per_day: int = 20
    max_dollars_per_day: float = 10.0
    max_turns: int = 40
    no_progress_limit: int = 3
    identical_error_limit: int = 5
    cooldown_min: int = 30
    concurrency: int = 1          # frozen at 1 for v3.0-v3.2; validated in __post_init__
    trusted_authors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.concurrency != 1:
            raise ValueError("concurrency > 1 is not supported until Phase L4")


@dataclass(frozen=True)
class Config:
    # ... existing fields unchanged ...
    loop: LoopConfig = field(default_factory=LoopConfig)
    # Config.load() gains a [loop] section parse, same _deep_merge pattern as [backend] today.
```

```python
# cli.py — additions

loop_app = typer.Typer(name="loop", help="Run the autonomous loop driver.")
app.add_typer(loop_app, name="loop")

@loop_app.command("run")
def loop_run() -> None: ...        # run_forever() in this terminal (foreground, debugging)

@loop_app.command("start")
def loop_start() -> None: ...      # tmux new -d -s atlas-loop 'atlas loop run'

@loop_app.command("stop")
def loop_stop() -> None: ...       # tmux kill-session -t atlas-loop

@loop_app.command("status")
def loop_status() -> None: ...     # reads .atlas/loop-state.json; prints budgets/breaker/last-tick

@loop_app.command("attach")
def loop_attach() -> None: ...     # tmux attach -t atlas-loop  (os.execvp — replaces the process)
```

### Data Structures

**`.atlas/loop-state.json`** (new persisted file — Decision #6):

```json
{
  "runs_today": 3,
  "dollars_today": 1.42,
  "day": "2026-07-24",
  "consecutive_no_progress": 0,
  "consecutive_identical_errors": 0,
  "last_error_signature": null,
  "breaker_open_until": null,
  "last_tick_at": "2026-07-24T18:03:11Z",
  "synced_pr_outcomes": ["42:107:merged", "43:108:closed_unmerged"]
}
```

`synced_pr_outcomes` is the local dedupe set for idempotent sync (TRD-v3 §4 NFR Reliability) — a list of `"{issue}:{pr}:{outcome}"` strings; `sync_prior_prs()` skips writing a `user_signal` score for any tuple already present. This is the "interim: local dedupe by `issue+pr+outcome`" pattern the TRD names explicitly (§4).

**`[loop]` TOML config** — exactly TRD-v3 §7's schema, no deviation:

```toml
[loop]
repos = ["anant-gupta-utexas/atlas"]
poll_interval_s = 60
max_runs_per_day = 20
max_dollars_per_day = 10.0
max_turns = 40
no_progress_limit = 3
identical_error_limit = 5
cooldown_min = 30
concurrency = 1
# trusted_authors = [...]   # required only if repo is public/multi-author
```

---

## API Specifications

Not applicable in the network sense (TRD-v3 §1: no HTTP shell in v3). The real "API" surface is the `gh` CLI subprocess contract `queue_gh.py` wraps:

| Dimension | Contract |
|---|---|
| Invocation | List-form argv only, e.g. `["gh", "issue", "list", "--repo", repo, "--label", "atlas:ready", "--state", "open", "--json", "number,title,body,labels"]` — matches `Deliverer`'s existing `subprocess.run` trust boundary (no `shell=True`) |
| Request shape | Every `queue_gh` function takes typed Python args (`Issue`, `str`, `Path`); issue *body* text never reaches a `gh` argv — only ever the agent prompt |
| Response shape | `gh ... --json ...` → JSON to stdout, parsed with `json.loads`. `list_ready` expects an array; `sync`'s `gh pr view --json state,mergedAt` expects a single object |
| Status determination | Exit code: non-zero → `GhCliError`. A non-zero `gh` exit is a **recoverable tick failure** (TRD-v3 §4 NFR) — caught at the tick level, logged, issue left reclaimable, `TickResult` describes the failure rather than crashing `run_forever()` |
| Error handling | Every call wrapped in a timeout (`timeout_s`, default 30s); `subprocess.TimeoutExpired` re-raised as `GhCliError` so callers have one exception type |
| Auth | Relies on the operator's existing `gh auth login` session (TRD-v3 §5); never stores/reads/logs a token. No `preflight()`-style check (Decision #7) |
| Rate limiting | Not handled explicitly — `gh`'s own client-side backoff is relied upon; `poll_interval_s` (default 60s) keeps call volume low by construction |

---

## Database Design

Not applicable to atlas's own storage — no new SQLite/file schema beyond `.atlas/loop-state.json` (a plain JSON file). Per TRD-v3 §13 item 14: **"No plumb migration for v3.0–v3.2."** L2 does not touch plumb's schema.

**plumb write patterns L2 introduces** (all through the existing `PlumbIO` surface — no new `PlumbIO` method required, per Decision #8):

| Write | plumb call | When |
|---|---|---|
| Triage classification | `PlumbIO.record_span(kind="plan", name="triage", ...)` | Every classify-fallback triage (not label-wins — no LLM call happened) |
| One-shot run | `Pipeline(loop_dev).run_to_completion()` — existing L0/L1 span-writing path, unchanged | Every `wf:quick` dispatch |
| Planned first-pass run | `dev-docs-be` invocation (Decision #2's first-pass-only scope) | Every `wf:planned` dispatch, first pass only |
| PR-outcome score | `PlumbIO.record_user_signal(...)` via `reopen_run(run_id)` (Decision #8) | `sync()` finds a merged/closed PR |

### Data Access Patterns

`loop.py` never queries plumb directly for read access inside the loop (no `plumb run stats` call as a dispatch input — that's an operator-facing query, TRD-v3 §13 #12, and router v1 score-informed routing is explicitly L3). `LoopState` is the loop's own read/write store, entirely separate from plumb.

### Migration Strategy

None. No schema changes.

---

## Algorithm & Logic Design

### `tick()` — the core state machine

```
function tick(cfg, state, repos):
    reset_daily_counters_if_new_day(state)

    # 1. Sync first — always, even if breaker/budget would otherwise stop dispatch.
    #    Recording outcomes for already-delivered PRs is not "new work" and must
    #    never be blocked by a budget cap meant to bound NEW dispatch.
    sync_results = []
    for repo in repos:
        try:
            sync_results += sync_prior_prs(repo, state)
        except GhCliError as e:
            log_recoverable("sync failed for repo=%s: %s", repo, e)
            continue

    made_progress = len(sync_results) > 0

    if breaker_open(state, cfg):
        return TickResult("breaker_open", None, None, None, "breaker open until " + state.breaker_open_until)

    if budget_exhausted(state, cfg):
        return TickResult("budget_exhausted", None, None, None, "daily budget exhausted")

    # 2. Pull next ready issue (first repo in cfg.repos order with a ready issue — Decision #9)
    issue = pull_next_ready(repos)
    if issue is None:
        record_tick_outcome(state, cfg, made_progress=made_progress, error_signature=None)
        persist(state)
        return TickResult("idle", None, None, None, "no ready issue")

    if cfg.trusted_authors and issue.author not in cfg.trusted_authors:
        log_warning("skipping issue #%s: untrusted author", issue.number)   # Decision #16
        record_tick_outcome(state, cfg, made_progress=made_progress, error_signature=None)
        persist(state)
        return TickResult("idle", None, None, None, "untrusted author, skipped")

    # 3. Triage
    triage_result = triage(issue, plumb=..., run_id=...)

    # 4. Claim — before any work starts, so a crash mid-dispatch leaves the issue
    #    visibly atlas:working (reconcile_orphans on next startup un-claims it)
    claim(issue, assignee=current_gh_user())

    try:
        # 5. Dispatch
        if triage_result.lane == "quick":
            pr_ref, run_id, cost = run_one_shot(issue, cfg)
        else:  # planned
            pr_ref, run_id, cost = run_planned_first_pass(issue, cfg)

        # 6. Comment — issue stays atlas:working until sync() sees the PR's outcome
        #    ("working" means "a PR is open and needs review," per TRD-v3 §3.1's label table).
        comment(issue, body=format_run_summary(run_id, pr_ref, cost))

        record_tick_outcome(state, cfg, made_progress=True, error_signature=None)
        state.runs_today += 1
        state.dollars_today += cost.dollars_if_known_else_zero()
        persist(state)
        return TickResult("dispatched", issue.number, triage_result.lane, pr_ref, "ok")

    except (DeliveryError, GhCliError, AbortedError) as e:
        # Dispatch or delivery failed. Issue stays atlas:working (a human sees it via
        # the failure comment, Decision #14) — reconcile_orphans handles the
        # "no PR ever opened" case on next restart, not this tick.
        comment(issue, body=f"loop_dev run failed: {e}. Left atlas:working for manual triage.")
        error_sig = _error_signature(e)
        record_tick_outcome(state, cfg, made_progress=False, error_signature=error_sig)
        persist(state)
        return TickResult("dispatched", issue.number, triage_result.lane, None, f"failed: {e}")
```

**Why sync runs before the breaker/budget check, unconditionally:** a breaker or budget that also blocked score-writing for already-open PRs would mean a runaway-cost day silently stops recording outcomes for work that already happened — corrupting the exact measurement (`user_signal`, intervention rate) the budget cap exists to protect. Sync is read-then-write against GitHub's already-final state (merged/closed), not new dispatch; it costs no LLM tokens and opens no new run.

### `run_one_shot()` — the quick-lane dispatch

```
function run_one_shot(issue, cfg):
    engine = resolve_engine(issue.labels, cfg)     # engine:claude/engine:codex label, else cfg default
    prompt_context = build_issue_prompt(issue)      # title + body + scope preamble (Decision #10)

    pipeline, recorder = make_pipeline(               # shared with cli.py::run (Decision #11)
        repo_root=repo_root_for(issue.repo),
        cfg=cfg,
        workflow="loop_dev",
        backend_override=engine,
    )
    ctx = pipeline.start(task=prompt_context, slug=slugify(issue.title))
    result: RunResult = pipeline.run_to_completion(ctx)   # RunResult from L1's widened Pipeline API

    if result.status != "success":
        raise AbortedError(f"loop_dev run {result.ctx.run_id} ended with status={result.status}")

    deliverer = GhPrDeliverer(repo_root=repo_root_for(issue.repo), worktree=WorktreeManager(...))
    pr_ref = deliverer.deliver(
        run_id=result.ctx.run_id,
        branch=branch_name_for(result.ctx),
        worktree_path=result.ctx.worktree_path,
        title=f"{issue.title} (Closes #{issue.number})",
        body=pr_body(issue, result.ctx.run_id),
    )
    return pr_ref, result.ctx.run_id, extract_cost(recorder)
```

This is the literal answer to the TRD's own framing: *"the loop constructs and drives `Pipeline` instances exactly as `cli.py::run` does today."*

### `sync_prior_prs()` — idempotent outcome scoring

```
function sync_prior_prs(repo, state):
    statuses = queue_gh.sync(repo)
    results = []
    for s in statuses:
        if s.outcome == "open":
            continue

        dedupe_key = f"{s.issue.number}:{s.pr_number}:{s.outcome}"
        if dedupe_key in state.synced_pr_outcomes:
            continue

        label = "approved" if s.outcome == "merged" else "rejected"
        value = 1.0 if s.outcome == "merged" else 0.0
        run_id = extract_run_id_from_issue_comment(s.issue)   # parsed from comment() body at dispatch

        if run_id is not None:
            handle = plumb.reopen_run(run_id)          # Decision #8
            plumb.record_user_signal(run_id=run_id, span_id="", metric="user_signal",
                                      decision=GateDecision(label=label, turn_count=1, reason=None))
            plumb.close_run(run_id=run_id, status="success" if s.outcome == "merged" else "failure")

        queue_gh.relabel(s.issue, state="done" if s.outcome == "merged" else "rejected")
        state.synced_pr_outcomes.append(dedupe_key)
        results.append(s)
    return results
```

### `reconcile_orphans()` — startup crash recovery

```
function reconcile_orphans(cfg, repos):
    reconciled = []
    for repo in repos:
        working_issues = queue_gh.list_labeled(repo, "atlas:working")
        statuses = queue_gh.sync(repo)
        for issue in working_issues:
            if not any(s.issue.number == issue.number for s in statuses):
                queue_gh.relabel(issue, state="ready")
                reconciled.append(f"issue #{issue.number}")

    for worktree_dir in (repo_root / ".atlas" / "worktrees").glob("*"):
        if not is_worktree_for_active_issue(worktree_dir, repos):
            try:
                WorktreeManager(repo_root).cleanup(worktree_dir)
            except WorktreeError:
                log_warning("cleanup failed for orphaned worktree %s", worktree_dir)
            reconciled.append(f"worktree {worktree_dir.name}")

    return reconciled
```

### Triage classification (haiku, `RAW:`-style single-shot)

```
function classify(issue) -> TriageResult:
    prompt = (
        "Classify this GitHub issue as either 'quick' (a single small, well-scoped "
        "change completable in one pass) or 'planned' (large enough to need a design "
        "doc / task breakdown before implementation). Respond with exactly one word "
        "('quick' or 'planned') followed by a one-line rationale.\n\n"
        f"Title: {issue.title}\nBody: {issue.body}"
    )
    # Dispatched via CliBackend.build_argv/parse_result directly (Decision #13), NOT through
    # Pipeline/StageRunner — matches TRD-v3 §3.2's "RAW:-style single-shot, not an agentic run."
    lane, rationale = parse_classify_response(output_text)  # unparseable -> "planned" + warning
    return TriageResult(lane=lane, source="classify", rationale=rationale)
```

---

## Error Handling & Edge Cases

| Case | Handling |
|---|---|
| `gh` CLI not authenticated / not installed | First `queue_gh` call raises `GhCliError`; `tick()` catches it, logs, returns a `TickResult` — `run_forever()` keeps polling (TRD-v3 §4 NFR) |
| `gh issue list` returns malformed JSON | `json.JSONDecodeError` re-raised as `GhCliError` — same recoverable-tick-failure path |
| Two issues both `atlas:ready` across two repos | First repo (by `cfg.repos` order) with a non-empty result, first issue in `gh`'s default (oldest-first) order — Decision #9 |
| Issue has both `wf:quick` and `wf:planned` | Resolves to `planned` (the more conservative lane); logged as a warning |
| Classifier output doesn't parse to `quick`/`planned` | Defaults to `planned` (safer failure direction); logged as a warning |
| `loop_dev` run fails (`RunResult.status == "failure"`) | No `Deliverer.deliver()` call — no PR opens. Issue stays `atlas:working`, but a failure `comment()` is posted (Decision #14). **v3.1 has no self-healing** — a failed one-shot run is a dead end until a human re-labels or intervenes |
| Run succeeds but `Deliverer.deliver()` raises `DeliveryError` | Work exists in the worktree, no PR, no cleanup (matches `GhPrDeliverer`'s own behavior — cleanup only runs post-`gh pr create`). Issue stays `atlas:working`; `reconcile_orphans()` will NOT reclaim this since plumb spans exist but no PR to sync against — a real gap, flagged not silently accepted |
| Budget exhausted mid-day | `tick()` still runs `sync_prior_prs()` (always) but returns before pulling a new issue. `run_forever()` keeps polling at `poll_interval_s`; dispatch resumes automatically at midnight UTC rollover |
| Circuit breaker opens | `run_forever()` re-checks each `poll_interval_s` tick (not one long sleep) — keeps `atlas loop status` responsive and doesn't miss a manual `atlas loop stop` |
| `engine:codex` selected but Codex auth unavailable | `CodexBackend.preflight()` (L1) fails closed inside `run_to_completion()` — surfaces as `StageOutcome(status="failure", error_type="codex_missing_auth")`, propagates to `run_one_shot`'s `AbortedError` path. No loop-level special-casing needed |
| `loop.py` crashes mid-tick (after `claim()`, before `comment()`/delivery) | Issue is `atlas:working`, no PR exists. On next `atlas loop start`, `reconcile_orphans()` relabels back to `atlas:ready` — the crash-recovery guarantee TRD-v3 §4 NFR requires |
| `.atlas/loop-state.json` missing or corrupted | `LoopState.load_or_init()` treats missing as fresh state; corrupted (unparseable) is logged as a warning and treated the same — never crashes startup |
| Prompt injection via issue body (private repo assumption) | Per TRD-v3 §4/§5, v3's target repos are private, single-author. `trusted_authors` check runs only when the tuple is non-empty (Decision #16); default `()` means no check, matching the private-repo default. Asserted by a dedicated test so the gate is provably wired even though inactive by default |

**Retry strategy:** None inside `loop.py` for a failed one-shot run — self-healing (diagnosis-injected child-run retry) is explicitly Phase L3. A failed run is terminal for that tick; the issue remains `atlas:working` for human triage.

**Fallback:** Engine fallback is not automatic — `engine:codex` failing does not retry on `claude`, matching L1's opt-in-only design.

---

## Dependencies & Interfaces

| Dependency | Direction | Contract |
|---|---|---|
| `gh` CLI | `queue_gh.py` → subprocess | External; authenticated session assumed (TRD-v3 §5); every call timeout-wrapped |
| `queue_gh.py` | `loop.py` → adapter | `loop.py` **never** shells `gh` directly — TRD-v3 §6's boundary, enforced by construction (grep-checkable, Decision #15) |
| `Pipeline` / `RunResult` (L1) | `loop.py` → orchestrator | Constructed exactly as `cli.py::run` does (via shared `make_pipeline()`, Decision #11); first production consumer of L1's `RunResult` widening |
| `Deliverer` / `GhPrDeliverer` (L0) | `loop.py` → delivery | First production caller. Post-success side-effect only, per TRD-v3 §3.7 — never a `StageSpec`, never invoked by `Pipeline` itself |
| `CliBackend` / `CodexBackend` (L0/L1) | `loop.py` → engine selection | `engine:*` label resolves to a `backend` override at `Pipeline`/`SubprocessStageRunner` construction — the "highest practical precedence" tier TRD-v3 §3.3 describes. First production caller of `CodexBackend` outside tests |
| `Config` / `LoopConfig` | `loop.py` → config | `[loop]` section, extends the existing `_deep_merge` TOML pattern |
| `WorktreeManager` (existing) | `reconcile_orphans()` / `Deliverer` → worktree | `cleanup()` reused as-is; `reconcile_orphans` adds the startup sweep, `Deliverer` the per-run cleanup |
| `dev-docs-be` (agent skill) | `run_planned_first_pass()` → agent invocation | Same "invoke as a black box" boundary v1's PRD establishes for existing plugins — dispatched, not reimplemented |
| `PlumbIO` (existing) | `loop.py` → plumb | Reuses `record_span`/`record_user_signal`/`open_run`/`close_run`/`reopen_run` — no new method (Decision #8) |
| `tmux` | `atlas loop start/stop/attach` → subprocess | Observability convenience only (TRD-v3 §3.8); `atlas loop run` works without it |

---

## Security Considerations

- **`queue_gh.py` is the sole `gh` touchpoint** — enforced structurally (single import site, grep-verifiable per Decision #15) so TRD-v3 §6's boundary is provable, not just conventional.
- **List-form subprocess argv only**, matching every existing backend/adapter — no `shell=True` anywhere in `queue_gh.py` or `loop.py`.
- **PR-only delivery, unchanged from L0.** `loop.py` never calls `git push` or `gh pr merge`/`gh pr create` directly — it goes through `Deliverer`, which already carries the branch-safety assertion and never-force-push guarantee. L2 adds no new push surface.
- **Issue body → prompt is the load-bearing new risk this phase introduces** (TRD-v3 §4 Security). This is the first phase where that boundary is real. Mitigation: private single-author repo assumption is the v3 default; `trusted_authors` allowlist is implemented and tested but inactive by default, becoming load-bearing the moment a target repo is public/multi-author. This TRS does not sanitize issue body content beyond passing it as data into a `RAW:` prompt — the same trust level as any `atlas run "<task>"` argument today. That is a deliberate, TRD-matched scope boundary (§4 names the allowlist as the mitigation, not content sanitization).
- **`claim()`'s assignee is the operator's own `gh` identity** — the loop assigns to the authenticated user, since v3 has no separate loop identity.
- **Budget/breaker are safety mechanisms, not security mechanisms.** They bound runaway *cost*, not unauthorized *access*. The security boundary is `trusted_authors` + the permission profile (`acceptEdits` + allowlist + `--sandbox workspace-write`), both inherited unchanged from L0/L1.
- **`.atlas/loop-state.json` contains no secrets** — token counts, dates, issue/PR numbers only.

---

## Testing Strategy

### Unit tests (`tests/unit/test_queue_gh.py`)

| Test | Asserts |
|---|---|
| `test_list_ready_parses_gh_json` | `gh issue list --json ...` fixture → correct `list[Issue]` |
| `test_list_ready_empty` | Empty `gh` JSON array → `[]`, not an error |
| `test_list_ready_gh_failure_raises` | Non-zero `gh` exit → `GhCliError` |
| `test_claim_swaps_labels_and_assigns` | Exact `-atlas:ready +atlas:working --add-assignee` argv |
| `test_sync_merged_outcome` / `test_sync_closed_unmerged_outcome` / `test_sync_open_outcome` | Fixture PR states map correctly |
| `test_relabel_state_transitions` | Each state produces correct argv; `done` also closes the issue |
| `test_all_gh_calls_wrapped_in_timeout` | Every function passes `timeout=` to `subprocess.run` |
| `test_queue_gh_is_sole_gh_caller` | Grep-based: no `"gh"` subprocess invocation exists outside `queue_gh.py` (Decision #15) |

### Unit tests (`tests/unit/test_triage.py`)

| Test | Asserts |
|---|---|
| `test_triage_wf_quick_label_wins` / `test_triage_wf_planned_label_wins` | Label wins, classify never called |
| `test_triage_both_labels_planned_wins` | Collision → `planned`, warning logged |
| `test_triage_classify_fallback` | No label → classify() called once; span recorded |
| `test_triage_classify_unparseable_defaults_planned` | Bad output → `planned` + warning |

### Unit tests (`tests/unit/test_loop.py`)

| Test | Asserts |
|---|---|
| `test_tick_idle_no_ready_issue` | Empty across all repos → `"idle"`; sync still ran |
| `test_tick_sync_runs_before_budget_check` | Budget exhausted + mergeable prior PR → PR still scored/relabeled |
| `test_tick_dispatches_quick_lane` | `wf:quick` → `run_one_shot` + `Deliverer.deliver` called, `"dispatched"` |
| `test_tick_dispatches_planned_lane_stops_after_trs` | `wf:planned` → `dev-docs-be` invoked, plan-only PR, `code_gen` never runs (mock call count) |
| `test_tick_claims_before_dispatch` | `claim()` precedes any `Pipeline` construction |
| `test_tick_failed_run_no_pr_but_comments` | `status=="failure"` → no `deliver`, but a failure comment posted (Decision #14) |
| `test_tick_delivery_failure_leaves_issue_working` | `DeliveryError` → issue stays `atlas:working`, no crash |
| `test_sync_idempotent_on_repeat_tick` | Same merged PR synced twice → `record_user_signal` called once |
| `test_sync_merged_writes_success_signal_and_closes` / `test_sync_closed_writes_rejected_signal` | Correct score + relabel + (merged-only) close |
| `test_budget_exhausted_blocks_new_dispatch` | `runs_today >= max_runs_per_day` → no `claim()` |
| `test_budget_resets_on_day_rollover` | New day → counters reset before check |
| `test_breaker_opens_on_no_progress_limit` / `test_breaker_opens_on_identical_error_limit` | Either threshold opens the breaker |
| `test_breaker_resets_on_progress` / `test_breaker_closes_after_cooldown` | Progress resets counters; cooldown expiry resumes dispatch |
| `test_reconcile_orphans_resets_stale_working_issue` | No linked PR → relabeled `atlas:ready` |
| `test_reconcile_orphans_leaves_working_issue_with_open_pr` | Open PR → untouched |
| `test_reconcile_orphans_prunes_stale_worktrees` | Unmatched worktree → `cleanup()` called; failure logged not raised |
| `test_engine_label_selects_backend` | `engine:codex` → `Pipeline` built with `CodexBackend` |
| `test_loop_state_persists_across_calls` / `test_loop_state_missing_file_inits_fresh` / `test_loop_state_corrupted_file_inits_fresh_with_warning` | Persistence round-trips; degraded-start paths never crash |
| `test_trusted_authors_empty_means_no_check` / `test_trusted_authors_enforced_when_configured` | Gate inactive by default, active when configured (Decision #16) |
| `test_run_forever_survives_unexpected_tick_exception` | A bare `Exception` from `tick()` is caught, logged, loop continues (Decision #18) |

### Integration tests (`tests/integration/test_loop_e2e.py`)

| Test | Asserts |
|---|---|
| `test_one_shot_lane_end_to_end_faked` | Faked `gh` + faked `Pipeline`/backend dispatch → one full `tick()` → one PR, one plumb run, correct labels |
| `test_planned_lane_stops_after_plan_pr` | `wf:planned` → plan-only PR, triad files under `dev/active/<slug>/`, no `code_gen` span |
| `test_crash_recovery_full_cycle` | claim → simulated crash → `reconcile_orphans()` on fresh startup → issue back to `atlas:ready`, worktree pruned |
| `test_zero_touch_smoke_faked` | Literal TRD-v3 §13 #5 shape, fully faked: label → one `tick()` → PR with `Closes #n` + `run_id` comment, no other interaction |

### Manual smoke tests (off-CI, same posture as L0's T-L0.8/T-L0.9 and L1's T-L1.1/T-L1.8)

- The real headline test (TRD-v3 §13 #5): real `atlas:ready` + `wf:quick` issue → `atlas loop start` → real PR, zero further interaction; merge it → next tick writes `user_signal` + closes issue.
- A real `wf:planned` issue → real plan-only PR with the TRS triad and Pending Decisions in the PR body.
- A real crash-recovery drill: start, kill mid-run, restart, confirm reclaim.

### Mocking strategy

No live `gh` subprocess calls in CI. `queue_gh.py` tests mock `subprocess.run` returning captured/constructed fixtures under `tests/fixtures/gh_json/`, matching L0/L1's `CliBackend` fixture pattern. `loop.py` tests mock at the `queue_gh` function boundary (typed `Issue`/`PrStatus`, not raw `gh` JSON a second layer down). `time.sleep`/`time.time` mocked for breaker/cooldown tests.

### Coverage targets

- `loop.py`: 85%+ · `queue_gh.py`: 90%+ · `triage.py`: 85%+ · `config.py`'s `[loop]` additions: 90%+ (all per TRD-v3 §10). Existing modules: no regression below L1's exit coverage.

---

## Performance Considerations

- **Poll efficiency** (TRD-v3 §4 NFR): idle-path `tick()` is one `gh issue list` call per configured repo, no busy-wait, `poll_interval_s` (default 60s) between ticks.
- **Triage classifier cost**: a single haiku call per untriaged issue; counts toward `max_dollars_per_day` but not `max_runs_per_day` (Decision #17).
- **`gh` calls are network-bound** — every call timeout-wrapped (30s default) so a hung API call degrades one tick, not the whole process.
- **Sequential, `concurrency=1`** (TRD-v3 §5) — no parallelism to reason about; one issue dispatched (or one sync batch) per tick.
- **`LoopState` persistence** is a small JSON file rewritten once per tick — negligible I/O at `poll_interval_s` granularity.

---

## Tasks

See [`loop-mode-phase-L2-tasks.md`](./loop-mode-phase-L2-tasks.md) for the flat, ordered task list (T-L2.1–T-L2.14) with per-task acceptance criteria, files, dependencies, and testing requirements — split into its own file alongside this plan's design content to respect the repo's 800-line file cap. The task list is normative; treat it as this section's content, not a separate artifact.

---

## Phase Deliverables

- `queue_gh.py` — the `gh` adapter, the sole point of contact with `gh` from atlas.
- `loop.py` — `tick()`/`run_forever()`/`reconcile_orphans()`, budgets, circuit breaker, idempotent sync.
- `triage.py` — label-wins-else-classify router.
- `[loop]` config wired into `Config`.
- `atlas loop run|start|stop|status|attach` CLI surface.
- L0's `Deliverer` and L1's `CodexBackend`/`loop_dev.yaml`/`RunResult` get their first production callers.
- Tests passing: unit (`queue_gh`, `loop`, `triage`, `config`), integration (full-tick faked state machine, zero-touch smoke faked, crash recovery), manual smoke (zero-touch real, planned-lane real, crash-recovery real — off-CI).
- `docs/1_product_and_research/BACKLOG.md`, `STATUS.md` updated; L1 code review's `PrRef.number` finding closed.
- `v3.1` delivered per TRD-v3 §11 (pending manual proof, same "code-complete, manual verification pending" framing L0/L1 used).

---

## Pending Decisions & Clarifications

Eighteen decisions were surfaced while authoring this TRS — spanning phase-dependency posture, planned-lane scope, how much of the L1 code review to absorb, file layout, `LoopState` persistence, plumb re-attachment for sync scoring, prompt construction, and several smaller implementation calls. Full text with rationale and recommended options lives in [`loop-mode-phase-L2-decisions.md`](./loop-mode-phase-L2-decisions.md) — **read it before implementation starts**; several tasks above (T-L2.5, T-L2.7, T-L2.12) directly depend on how these resolve. Headline items requiring maintainer sign-off before or during implementation:

- **#1** — Whether L2 blocks on L1's open manual checks (recommended: no, but don't trust Codex-lane token data until T-L1.1 closes).
- **#2** — Planned-lane scope: first-pass-only (recommended) vs. the full task-by-task loop.
- **#8** — Reusing `PlumbIO.reopen_run()` for post-hoc PR-outcome scoring — a conceptual stretch worth explicit sign-off.
- **#11** — Extracting `cli.py::_make_pipeline` into a shared `make_pipeline()` vs. duplicating it in `loop.py`.
- **#13** — Driving `CliBackend` directly for the triage classifier, bypassing `SubprocessStageRunner` entirely — a new usage pattern.

---

## What this TRS deliberately does NOT cover

- **Self-healing** (diagnosis-injected child-run retry, `parent_run_id` lineage for retries, failure-mode classification) and **pre-PR judge gate** (`plumb judge run`). Both Phase L3 (TRD-v3 §14).
- **Router v1** (score-informed engine/workflow selection). Phase L3 (stretch goal even there).
- **Second repo, `concurrency > 1`, per-run state keys.** Phase L4 — `LoopConfig.concurrency` rejects anything but `1` specifically to keep this boundary hard.
- **Weekly `plumb run stats` report.** Phase L4.
- **A per-model price table / dollar-cost derivation for Codex runs.** Never in v3 (L1 Resolved Decision #10, reaffirmed) — a Codex-heavy day's `max_dollars_per_day` protection is bounded only by `max_runs_per_day` (TRD-v3 §12).
- **The planned lane's task-by-task subsequent-pass implementation loop.** See Decision #2 — L2 delivers first-pass-only.
- **Fixing every L1 code-review finding.** See Decision #3 — only `PrRef.number` (T-L2.12) is in scope.
- **A dedicated `queue_gh.preflight()` auth check.** See Decision #7.
- **Any change to `dev.yaml`, `job.yaml`, `job_cli.yaml`, `loop_dev.yaml`'s stage shape, or the workflow loader's schema.** L2 only *drives* `loop_dev.yaml`, never edits it.
- **Any change to `CliBackend`/`CodexBackend`/`ClaudeCodeBackend`'s own argv/parse logic.** L2 only *selects* an engine via label.
- **A new plumb schema, table, or `RunHandle` method.** Per TRD-v3 §13 #14 — Decision #8 concludes none is needed.
