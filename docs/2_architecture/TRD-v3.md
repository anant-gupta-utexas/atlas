# Technical Requirements Document (TRD) — v3

**Project:** atlas — autonomous, minimal-input development loop ("loop mode")
**Scope:** v3 (autonomy layer on top of the v2 workflow engine). Builds on the v1 and v2 TRDs; supersedes neither.
**Status:** Planning (pre-implementation). Phases L0–L4 below feed the per-phase TRS workflow.
**Created:** 2026-07-21
**Grounds on:**

- [`loop-mode-design.md`](../1_product_and_research/loop-mode-design.md) — the source-of-truth design note (problem, locked decisions, two-lane routing, phases, risks). **Read this first.**
- [`headless-clis-reference.md`](../1_product_and_research/headless-clis-reference.md) — per-CLI flag/auth/quota reference (Claude Code, Antigravity; Codex section added in Phase L1).
- [`TRD-v2.md`](./TRD-v2.md) — the shipped v2 engine (YAML workflows, `CliBackend`, worktree isolation). v3 reuses this machinery; it does not modify the v2 contract.
- [`TRD.md`](./TRD.md) — v1 NFRs, integration contracts, success criteria (carry forward).
- [`system_design.md`](./system_design.md) — component architecture (loop-mode section appended alongside this TRD).

> **Relationship to v2.** TRD-v2 generalized the engine from one hardcoded dev pipeline to N YAML workflows with per-stage CLI backends. It stops explicitly short of "an HTTP shell, multi-tenancy, concurrent runs, a UI, or dynamic topology." v3 does **not** cross into dynamic topology or multi-tenancy — it adds the one deferred capability the loop needs: a **long-running driver** that pulls work from a queue and delivers PRs, reusing v2's `Pipeline` / `WorktreeManager` / `CliBackend` / `PlumbIO` unchanged.

---

## 1. Executive Summary

Atlas v1/v2 is a **single-run, operator-present** orchestrator: `atlas run "<task>"` walks one workflow to completion, pausing at human gates. v3 adds **loop mode** — a long-running driver that lets the operator's involvement collapse to two points: **filing a ticket** and **reviewing a PR**.

The loop polls a per-repo GitHub Issues queue, triages each issue into one of two lanes, runs the existing pipeline in an isolated worktree with a selectable engine (`claude` or `codex`), and opens a PR. It never pushes to `main`, never merges. When the operator merges or closes the PR, the next loop pass records that outcome as a plumb `user_signal` score. Later phases add self-healing (diagnosis-injected child-run retries) and score-informed routing.

This is "loop engineering" — designing the outer feedback loop rather than prompting the agent by hand. The defensible core is unchanged: **human gate (now the PR) + durable state + plumb measurement.** v3 moves the gate from an inline `input()` prompt to a PR review, which is asynchronous and batchable.

**Scope boundary:** v3 does NOT include an HTTP shell, a web UI, multi-tenant queues, or auto-merge. The loop is a single local process (a detached tmux session for observability), sequential in v3.0–v3.2 (`concurrency=1`), with multi-run concurrency deferred to v3.3.

### What v2 already provides (reused verbatim)

| Capability | v2 component | v3 use |
|---|---|---|
| YAML workflows, `tuple[StageSpec, ...]` | `workflow_loader.py`, `workflows/*.yaml` | `loop_dev.yaml` is a new workflow; no loader change |
| State machine + gates + async-gate path | `Pipeline` (`orchestrator.py`) | Driven per issue; unchanged in shape |
| CLI backend strategy + 4-tier resolution | `CliBackend`, `_KNOWN_BACKENDS` (`cli_backend.py`) | `CodexBackend` registered; engine chosen per `engine:*` label |
| Worktree isolation | `WorktreeManager` (`worktree.py`) | One worktree per run; `cleanup()` finally wired |
| Measurement, child runs, examples | `PlumbIO` (`plumb_io.py`), plumb `run`/`add_span`/`add_score`/`parent_run_id`/judge | Run scoring, PR-outcome scoring, self-healing lineage |
| Compaction-safe run state | `StateStore` + `tasks.md` (`state.py`) | Per-run working memory; unchanged |

---

## 2. Business Context & Objectives

### Strategic positioning

The v2 TRD positioned atlas against Claude Code's dynamic workflows: the commodity part (linear orchestration) is now free; the defensible part (gates + durable state + measurement) is what dynamic workflows lack. v3 extends that thesis along the **autonomy axis**: the industry ("loop engineering", long-running agent harnesses) is converging on agents that keep working unattended, but throughput-only loops are silent on cost and quality. atlas's loop is **measured by construction** — every autonomous run is a plumb span tree with cost, tokens, and a task-completion signal — and **gated by construction** — nothing merges without a human PR review.

### Objectives

1. **Collapse operator input to file-ticket + review-PR.** Between those two points, the loop runs with zero keystrokes.
2. **Reuse the v2 spine; add only the driver.** New code is a queue adapter, a loop module, a delivery hook, and one backend — not a new orchestration framework.
3. **Two engines, measured comparison.** `claude` and `codex` both dispatch from day one so plumb can compare cost/quality per task class.
4. **Drive the operator's own TRS discipline autonomously.** Large issues get a plan-only PR (TRS triad + surfaced decisions) that stops for review — the loop performs the same per-phase workflow the operator does by hand.
5. **Preserve all v1/v2 guarantees.** Attended `atlas run` is unchanged. Loop behavior is opt-in via a new command and config section.

### KPIs this build must make measurable

- **Zero-touch delivery.** A labeled issue produces a PR with no human input between labeling and review (the headline smoke test).
- **Cost-per-landed-PR.** Total `dollar_cost` across runs / issues reaching a merged PR — queryable from plumb **once plumb P1-a (`set_usage`) lands**; until then only token counts are durable (§3.6).
- **Intervention rate.** Fraction of runs requiring a human nudge beyond the standard PR review.
- **Engine A/B.** The same task class run under `claude` vs `codex`, compared in `plumb run stats`.
- **Self-healing lift (v3.2).** Fraction of first-attempt failures rescued by a diagnosis-injected retry vs. blind failure.

---

## 3. Functional Requirements

### 3.1 Work queue — GitHub Issues per repo

The queue is GitHub Issues, accessed via the authenticated `gh` CLI (JSON output). A thin adapter (`queue_gh.py`) is the single point of contact with `gh`; the loop never shells `gh` directly.

**Label protocol (per repo):**

| Label | Meaning | Written by |
|---|---|---|
| `atlas:ready` | Eligible for the loop to pick up | Operator (when filing) |
| `atlas:working` | Claimed; a run is in flight or a PR is open | Loop (on claim) |
| `atlas:blocked` | Retryable path exhausted; needs human attention | Loop (self-healing exit) |
| `atlas:rejected` | The delivered PR was closed unmerged | Loop (on PR-close sync) |
| `atlas:done` | The delivering PR merged (issue closed) | Loop (on PR-merge sync) |
| `wf:quick` / `wf:planned` | Explicit lane override (see §3.2) | Operator (optional) |
| `engine:claude` / `engine:codex` | Explicit engine override | Operator (optional) |

**Adapter surface (`queue_gh.py`):**

```python
def list_ready(repo: str) -> list[Issue]                 # gh issue list --label atlas:ready --json ...
def claim(issue: Issue) -> None                          # -atlas:ready +atlas:working, assign self
def deliver_pr(issue: Issue, branch: str, body: str) -> PrRef   # gh pr create --head <branch>
def comment(issue_or_pr: Ref, body: str) -> None         # run_id + score summary
def sync(repo: str) -> list[SyncResult]                  # read PR state for atlas:working issues
def relabel(issue: Issue, state: Literal["done","rejected","blocked","ready"]) -> None
```

`Issue` carries `number`, `title`, `body`, `labels`. `sync()` returns, per in-flight issue, whether its PR is `merged` / `closed_unmerged` / `open`.

### 3.2 Two-lane routing (hybrid)

Each issue is triaged into one of two lanes. The router **is** the v2 workflow-selection seam (`wf:*` label → workflow YAML), with an added classifier fallback:

- **Explicit label wins.** `wf:quick` → one-shot lane; `wf:planned` → planned lane.
- **Else classify.** An unlabeled issue is passed to a fast triage step (haiku, single structured call) that reads title + body and returns `quick` or `planned` with a one-line rationale (recorded on the plumb run as a span). The classifier is a `RAW:`-style single-shot, not an agentic run.

**One-shot lane** (`wf:quick` → `loop_dev.yaml`): the whole issue is one work item (Ralph's "one item per loop"). Runs `plan → code_gen[isolate] → verify`, then delivers a single PR (`Closes #n`).

**Planned lane** (`wf:planned`): the loop does **not** one-shot. Its first pass produces the planning artifact via the per-phase TRS authoring step (`dev-docs-be`), opens a **plan-only PR** containing just the `dev/active/<slug>/` triad (`-plan.md` / `-context.md` / `-tasks.md`) with the TRS's "Pending Decisions & Clarifications" surfaced in the PR body, then **stops**. Subsequent loop passes pick up the committed TRS and implement it **task by task**, each task its own worktree run + `/code-review`. This yields **multiple PRs per issue** (`Refs #n` on task PRs, `Closes #n` on the last); the issue closes when the last merges.

> **Design note.** The planned lane is the loop driving the operator's normal per-phase TRS discipline autonomously, escalating decisions **as a PR review** rather than an interactive prompt. Whether a planned issue pauses after the TRS (chosen default) vs. auto-proceeds is fixed to **pause** in v3; a per-issue override label is a deferred nicety.

### 3.3 Engine selection & `CodexBackend`

Engine per run resolves through the existing 4-tier cascade (per-stage `StageSpec.backend` → workflow `default_backend` → `.atlas.toml [backend]` → hard default `claude`), with the loop injecting the backend from an `engine:*` label (highest practical precedence, applied as a per-run override at pipeline construction).

**`CodexBackend`** implements the v2 `CliBackend` Protocol (`cli_backend.py`), registered in `_KNOWN_BACKENDS` / `make_backend()`:

```python
class CodexBackend:            # codex exec ...
    name = "codex"
    def build_argv(self, *, prompt, model, add_dirs, timeout_s, extra_flags) -> list[str]:
        # ["codex", "exec", prompt, "--json", "-C", <primary-dir>,
        #  "--sandbox", "workspace-write", "--model", <model>,
        #  "--add-dir", <each-additional-dir>...]
    def parse_result(self, stdout, stderr, returncode) -> StageOutcome:
        # exit-code-driven status; assert a `turn.completed` event exists;
        # output text joined from `item.completed` events whose
        # item.type == "agent_message"  (there is NO status field in the stream)
    def parse_usage(self, stdout) -> CodexUsageStats | None:
        # `turn.completed`.usage → four token fields; NO cost field exists
    def preflight(self) -> str | None:
        # verify auth (OPENAI_API_KEY / $CODEX_HOME/auth.json); fail closed
        # with a typed error, no subprocess spawned, no silent hang
```

**Verified event schema (`codex-cli 0.144.4`, captured 2026-07-24).** A real read-only run emits:

```jsonl
{"type":"thread.started","thread_id":"019f96b7-..."}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hi"}}
{"type":"turn.completed","usage":{"input_tokens":16668,"cached_input_tokens":13056,"output_tokens":5,"reasoning_output_tokens":0}}
```

> **⚠ Correction (2026-07-24).** This section previously described a final **`result`** event carrying *"status + stats"* and a *"`result` event subtype"* failure signal, written before Codex had ever been exercised in this stack (§12 flags it as *"never exercised in this stack before"*). **Codex 0.144.4 emits no such event.** Four corrections, all load-bearing for `parse_result`:
> 1. The terminal event is **`turn.completed`**, and it carries **only `usage`** — no status, no text.
> 2. There is **no status field anywhere in the stream**; success/failure is **exit-code-only**.
> 3. Agent output text lives in **`item.completed`** events where `item.type == "agent_message"` (field `item.text`) — a *different* event type from the terminal one, so extraction is a two-pass scan.
> 4. There is **no `total_cost_usd`** (or any cost field). Codex reports four token counts: `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`.
>
> Implementation detail and the resulting parse algorithm live in the Phase L1 TRS ([`dev/archive/loop-mode-phase-L1/`](../../dev/archive/loop-mode-phase-L1/loop-mode-phase-L1-plan.md)).

**Per-CLI contract (extends TRD-v2 §3.4 table):**

| Dimension | `CodexBackend` |
|---|---|
| Command | `codex exec` |
| Workspace dir | `-C <worktree>` (also satisfies codex's git-repo requirement); `--add-dir <repo_root>` keeps repo context readable/writable alongside it |
| Model selection | `-m/--model <MODEL>` |
| Output format | `--json` → JSONL event stream; terminal **`turn.completed`** event carries `usage` only |
| Agent output text | `item.completed` events with `item.type == "agent_message"` → `item.text` (joined across events) |
| Autonomy/sandbox | `--sandbox workspace-write` (edits confined to the worktree). **Never** `--dangerously-bypass-approvals-and-sandbox` / `--dangerously-bypass-hook-trust` |
| Failure signal | **Exit code only** — the event stream carries no status field |
| Cost telemetry | **None reported by the CLI** (see §3.6 asymmetry note) |
| Token telemetry | `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens` |
| Auth (headless) | `OPENAI_API_KEY` or `$CODEX_HOME/auth.json` (default `~/.codex/auth.json`, written by `codex login`); validated in `preflight()` |

**Consequence for the engine A/B objective (§2 KPIs, §13 #12).** Cost comparison between engines is **not symmetric and not merely blocked on plumb**:

| Engine | Cost reported by CLI? | Durable sink? | Net |
|---|---|---|---|
| `claude` | Yes (`total_cost_usd`) | No — blocked on plumb P1-a (§3.6) | Recoverable when plumb v1.1 lands |
| `codex` | **No — never emitted** | N/A | Requires atlas to *derive* cost from tokens × a per-model price table |

Until such a price table exists (not in v3 scope), **cross-engine comparison is tokens-only**. `dollar_cost` for Codex runs is unobtainable, not merely unstored. Resolve in the L2 TRS (which owns cost-per-landed-PR) — see §12's risk row.

`ClaudeCodeBackend` gains `--output-format json` in loop mode (see §3.6). `AntigravityBackend` (`agy`) remains experimental and is not used by the loop.

### 3.4 `loop_dev.yaml` — the one-shot workflow

A new packaged workflow (`src/atlas/workflows/loop_dev.yaml`), **ungated**, distinct from the 7-gate attended `dev.yaml`:

```yaml
name: loop_dev
default_backend: claude
stages:
  - name: plan
    span_kind: plan
    tool: "RAW:Read the issue and the repo; produce a short plan for this one change."
    gate: null
    isolate: false
  - name: code_gen
    span_kind: llm
    tool: "RAW:Implement the change to satisfy the issue's acceptance criteria."
    gate: null
    isolate: true
  - name: verify
    span_kind: verify
    tool: "/verify"
    gate: null
    isolate: false
```

Quality is enforced by `verify` + the downstream PR review, not by inline gates. `code_gen` keeps `isolate: true` so it runs in a worktree. (Exact tool strings and guardrail "signs" are finalized in the Phase L1 TRS.)

### 3.5 The loop driver (`loop.py`)

A new module implementing the poll-dispatch-deliver-sync cycle. **`Pipeline` is unaware of the loop** — the loop constructs and drives `Pipeline` instances exactly as `cli.py::run` does today.

```python
def tick(cfg: LoopConfig) -> TickResult:
    # 1. sync prior PRs first: merged → user_signal 1.0 + relabel done + close; closed → 0.0 + relabel rejected
    # 2. pull next atlas:ready issue (across cfg.repos); if none → idle
    # 3. triage lane (wf:* wins, else classify)
    # 4. claim (label swap + assign)
    # 5. build prompt: issue title+body + guardrail signs + existing context_hint
    # 6. dispatch:
    #      one-shot → run Pipeline(loop_dev) to completion → Deliverer.deliver()
    #      planned  → run dev-docs-be → plan-only PR → STOP (no implementation this pass)
    # 7. comment run_id + score summary; relabel

def run_forever(cfg: LoopConfig) -> None:
    reconcile_orphans(cfg)          # startup: reset stale atlas:working w/o open PR; prune stale worktrees
    while True:
        if budget_exhausted(cfg) or breaker_open(cfg): sleep/stop
        tick(cfg)
        sleep(cfg.poll_interval_s)
```

**One issue per tick** (Ralph's one-item rule). v3 is **sequential** (`concurrency=1`), so the v2 single-run assumption (`.atlas/current-run` holds one run) is not violated. Concurrency > 1 is deferred to §14 Phase L4 and requires per-run state keys.

### 3.6 Headless telemetry & permission profile (loop runs)

Loop runs require two things attended runs don't:

**Telemetry.** `ClaudeCodeBackend.build_argv` adds `--output-format json` for loop runs; `parse_result` maps `subtype` → status and surfaces `total_cost_usd` + `usage` (`input_tokens` / `output_tokens`). **Guard behind a per-run flag** so attended `dev` runs keep human-readable stdout — the v2 Phase-3 decision that Claude stays plain-text for gate-parity holds for attended mode only.

**Where that telemetry actually lands (verified against plumb v1.0.1 — tokens and dollars are *not* symmetric):**

| Signal | Sink | Status |
|---|---|---|
| `usage.input_tokens` / `output_tokens` | **Span-level** — `RunHandle.add_span(tokens=(in, out))` (`plumb/api.py:264`), threaded via `plumb_io.py` | **Works today.** Persists *summed* into `spans.tokens`; the in/out split is lost at the DB layer until plumb v1.1. |
| `total_cost_usd` | **Run-level only** — there is **no per-span cost column** in plumb (not in v1.0.1, and not in v1.1: `spans.attributes` is JSON, and P1-a's `set_usage` is deliberately run-level) | **Blocked on plumb P1-a.** Parsed and surfaced in-memory (logs, PR-comment body) but has **no durable sink** until `set_usage` + `finalize_run` threading land. |

The `runs.tokens_in` / `tokens_out` / `dollar_cost` columns exist in plumb's schema, but **the online `with run()` path does not write them** — `finalize_run` (`plumb/storage_sqlite.py:431`, `_FINALIZE_RUN` SQL) sets none of them and `RunHandle` exposes no cost/usage setter. A live run today therefore produces a `runs` row with `dollar_cost = NULL`. Do **not** design against those columns before plumb P1-a; do **not** go looking for a per-span cost sink, as none exists.

**Per-engine asymmetry (verified 2026-07-24).** The table above describes `claude`. `codex` differs at the *source*, not just the sink:

| | `claude` | `codex` |
|---|---|---|
| Cost emitted by CLI | `total_cost_usd` | **Nothing** — no cost field exists |
| Path to durable cost | plumb P1-a (`set_usage`) | plumb P1-a **plus** a per-model price table atlas does not have |
| Uncached input tokens | `input_tokens` | — |
| Cache-read tokens | `cache_read_input_tokens` | `cached_input_tokens` |
| Cache-**write** tokens | `cache_creation_input_tokens` | *(not reported)* |
| Output tokens | `output_tokens` | `output_tokens` |
| Reasoning tokens | *(not reported — folded into output)* | `reasoning_output_tokens` |

**Neither CLI's token schema is a superset of the other**, which is why the two backends keep separate usage dataclasses rather than sharing one.

**How this collapses at the span level.** plumb's `spans` table has a **single `tokens INTEGER` column** (`plumb/adapters/_schema.py:47`); `add_span(tokens=(in, out))` sums the pair on write, and `Span`'s docstring states the in/out split *"is not durable"* (`plumb/core/entities.py:123-127`). So per-span token storage answers only one question — **total tokens billed for this span** — and every backend's usage fields reduce to that sum:

```
tokens = (uncached input + all cache fields) + (output + reasoning)
```

⚠ **One open ambiguity, tracked in the L1 TRS (Pending Decision #4):** whether Codex's `cached_input_tokens` is an *addend* to `input_tokens` (Anthropic's convention) or a *subset breakdown* of it. The captured sample fits both readings; choosing wrong mis-states Codex spans by ~4×. Settled by a cold-cache/warm-cache capture pair in L1.

**Decision (maintainer, 2026-07-24): v3 measures tokens, not dollars, for cross-engine comparison.** No per-model price table is built in v3. If cost synthesis is added later, the durable shape is prices in a **dated** config (`as_of`), cost computed at **query time not write time** (so a corrected table retroactively fixes history), and derived figures labeled *estimated* — never written into the same column as a CLI-reported figure.

**Permissions.** Loop runs use a non-interactive profile: **not** `--bare` (the pipeline needs plugin/skill discovery), but `--permission-mode acceptEdits` + a curated `--allowedTools` allowlist (stored in the *target repo's* `.claude/settings.json`, checked in) + a `--max-turns` cap. **No `--dangerously-skip-permissions`** — the worktree is a directory boundary, not a filesystem sandbox. `CodexBackend` uses `--sandbox workspace-write` for the equivalent confinement.

### 3.7 Delivery — branch → PR (`Deliverer`)

Delivery is a **post-success side-effect**, implemented as an injected collaborator (like `GatePrompter`), **not** a `StageSpec`. Keeping it out of the workflow YAML means attended workflows are unaffected and the delivery step is not a measured pipeline stage.

```python
class Deliverer(Protocol):
    def deliver(self, *, run_id, issue, worktree_path, branch, scores) -> PrRef: ...

class GhPrDeliverer:   # push branch; gh pr create --head <branch> --body <links + run_id + scores>; then WorktreeManager.cleanup()
```

The `Deliverer` replaces the dead `WorktreeManager.merge_back()` path. It **pushes a branch and opens a PR** — it never merges, never pushes `main`, never force-pushes.

### 3.8 CLI surface

```bash
atlas loop run                 # foreground (debugging); runs run_forever in this terminal
atlas loop start               # tmux new -d -s atlas-loop 'atlas loop run'   (detached)
atlas loop stop                # tmux kill-session -t atlas-loop
atlas loop status              # budgets used, last tick, in-flight issue, breaker state
atlas loop attach              # tmux attach -t atlas-loop
```

tmux is **observability only** — control is the CLI + files; the loop is never driven by tmux send-keys. Per-run logs continue to `.atlas/runs/<run_id>.log` for tailing.

---

## 4. Non-Functional Requirements (NFRs)

All v1/v2 NFRs carry forward. Additionally:

### Performance
- **Poll efficiency.** `tick()` idle path (no ready issue) is one `gh issue list` per configured repo per interval — no busy-wait. Interval configurable (`poll_interval_s`, default 60s).
- **`Deliverer.deliver()` / `queue_gh` calls** are network-bound; wrap each `gh` invocation in a timeout and treat a non-zero `gh` exit as a recoverable tick failure (log, leave the issue reclaimable), not a crash.
- **Triage classifier** is a single fast-model call; budget it as part of per-run cost.

### Security
- **Permission profile, not YOLO.** §3.6 — allowlist + `acceptEdits` + `--max-turns`; codex `--sandbox workspace-write`; never `bypassPermissions`.
- **PR-only delivery.** The loop never pushes `main`, never merges, never force-pushes. Enforced in `GhPrDeliverer` (branch-scoped push only) and asserted by test.
- **Prompt injection via issue bodies.** Issue title/body become part of the agent prompt. In a **private, single-author repo** (the v3 target) this is equivalent to the operator typing the command. **If any target repo is public or multi-author, the loop MUST require an allowlisted issue author before dispatch** (config `[loop].trusted_authors`), or sanitize the body. This is a hard requirement gated on repo visibility, called out explicitly.
- **`gh` auth scope.** The loop relies on the operator's existing `gh` session; it does not store or log tokens.

### Reliability
- **Crash recovery.** On startup, `reconcile_orphans()` resets any `atlas:working` issue with no open PR back to `atlas:ready` and prunes stale `.atlas/worktrees/*`. A crash mid-run must never strand an issue.
- **Budgets & circuit breaker.** The loop halts (cooldown, then resume) when a per-day budget is hit or the breaker trips (see §3.5, §5). A runaway loop must be bounded by both a **cost cap** and a **no-progress detector**.
- **Idempotent sync.** `sync()` writing a `user_signal` score for a merged/closed PR must be idempotent — re-running a tick does not double-score. (Interim: local dedupe by `issue+pr+outcome`, mirroring the existing pending-scores dedupe; durable once plumb v1.1 lands idempotent scoring.)
- **Attended-mode invariance.** `atlas run` (no loop) behaves identically to v2. All v2 tests pass unchanged.

### Usability
- **`atlas loop status`** reports enough to answer "what is the loop doing and is it healthy?" without attaching.
- **Errors surface, don't hang.** A missing engine auth, a `gh` failure, or a breaker-open state produces a clear log line and a non-zero `atlas loop status`, never a silent stall.

---

## 5. System Constraints & Assumptions

All v1/v2 constraints carry forward. Additionally:

- **`gh` CLI required and authenticated.** The loop assumes a working `gh auth` session with issue + PR scope on the target repos.
- **Codex CLI required for `engine:codex`.** `codex exec` must be installed and authenticated (`OPENAI_API_KEY` or `codex login`). `CodexBackend.preflight()` fails closed if not.
- **tmux required for `atlas loop start/attach`** (the detached-session convenience). `atlas loop run` works without tmux.
- **Sequential in v3.0–v3.2.** `concurrency=1`; the v2 single-run-per-repo state model is preserved. Concurrency > 1 (Phase L4) is the one place that requires lifting the `.atlas/current-run` single-run assumption.
- **plumb v1.0.1 is sufficient for v3.0–v3.2 — with one carve-out.** Child runs and per-span token writes work today. The run-level cost/token *columns* exist but are **not writable** from the online `with run()` path, so **run-level `dollar_cost` is a genuine plumb P1-a dependency**, not an interim-pattern gap: it blocks the cost half of cost-per-landed-PR (§3.6, §13 #1/#5/#12) and the `max_dollars_per_day` budget cap (§12). Everything else in plumb v1.1 (durable `rationale`, first-class `add_example`, idempotent scoring) improves the self-healing and sync paths but is not a hard blocker — the interim private-API / local-dedupe patterns from v2 carry those gaps.
- **Private, single-author target repos in v3.** The prompt-injection mitigation (§4 Security) is deferred *only* under this assumption and becomes mandatory the moment it no longer holds.

---

## 6. Integration Requirements

All v1/v2 integrations carry forward. New integrations:

| Integration | Surface | Shape | Owner |
|---|---|---|---|
| GitHub Issues / PRs | `gh` CLI subprocess (JSON) via `queue_gh.py` | authenticated `gh` session; issue + PR scope | external CLI |
| Codex CLI | Subprocess (`codex exec --json`) via `CodexBackend` | auth via `OPENAI_API_KEY` / `codex login` | external CLI |
| tmux | Subprocess (`tmux new/kill/attach`) for `atlas loop start/stop/attach` | observability wrapper only | external CLI |
| plumb judge | `plumb judge run` (v3.2) for the pre-PR quality gate + failure-mode classification | existing plumb adapter | sibling repo |

**Boundary guarantees (extend v2 §6):**
- **`queue_gh` ↔ loop.** The loop depends only on the `queue_gh` adapter surface (§3.1), never on raw `gh` output. A future swap to a graph-based tracker (e.g. beads) is a new adapter behind the same interface.
- **`CodexBackend` ↔ Pipeline.** Same invariant as every backend: internal to `SubprocessStageRunner`; `Pipeline` sees only `StageRunner` + `StageOutcome`. Gates, worktrees, plumb are untouched.
- **`Deliverer` ↔ Pipeline.** The `Deliverer` is invoked by the loop **after** a successful `run_to_completion`, not by `Pipeline`. Attended runs never construct a `Deliverer`.

---

## 7. Data Requirements

### New atlas-owned state (additions to v1/v2)

| File / config | Purpose | Lifecycle |
|---|---|---|
| `.atlas.toml [loop]` / `~/.atlas/config.toml [loop]` | Loop configuration (§ below) | User-authored |
| target repo `.claude/settings.json` | Loop-run tool allowlist (per target repo) | User-authored, checked into that repo |
| `.atlas/runs/<run_id>.log` | Per-run log (existing; now the primary tmux-tail surface) | Append-only; rotation still a backlog item |

**`[loop]` config schema** (extends the frozen `Config`):

```toml
[loop]
repos = ["anant-gupta-utexas/atlas"]     # target repos (v3.0: atlas builds atlas)
poll_interval_s = 60
max_runs_per_day = 20
max_dollars_per_day = 10.0                # pre-P1-a: summed from in-process total_cost_usd,
                                          # NOT runs.dollar_cost (unwritable — §3.6, §12)
max_turns = 40                            # per-run agent turn cap
no_progress_limit = 3                     # breaker: consecutive no-progress ticks
identical_error_limit = 5                 # breaker: consecutive identical errors
cooldown_min = 30                         # breaker cooldown before resume
concurrency = 1                           # v3.0–v3.2 fixed at 1
# trusted_authors = [...]                 # REQUIRED if any repo is public/multi-author (§4 Security)
```

### plumb impact

**No plumb schema migration required for v3.0–v3.2.**

| plumb concern | Verdict | Action |
|---|---|---|
| `spans.tokens` (per-span) | Works as-is | Populate from Claude/Codex JSON `usage` via `add_span(tokens=(in, out))` — persists summed (§3.6) |
| `runs.tokens_in` / `tokens_out` / `dollar_cost` | **Columns exist but are unwritable** from the online `with run()` path (`finalize_run` sets none; no `RunHandle` cost setter) | **Blocked on plumb P1-a (`set_usage` + finalize threading).** Do not design against these before then (§3.6) |
| `user_signal` scorer | Works as-is | PR merged → 1.0; closed-unmerged → 0.0 (§3.1 sync) |
| `parent_run_id` child runs | Works as-is | Self-healing retry lineage (v3.2) |
| `examples` (`origin_run_id`) | Works as-is | Failed-run capture (v3.2); interim private-API write from v2 carries until plumb v1.1 |
| judge scoring (`plumb judge run`) | Works as-is | Pre-PR quality gate + failure-mode classify (v3.2) |
| idempotent scoring | Deferred (plumb v1.1) | Interim local dedupe on `issue+pr+outcome` |

---

## 8. Infrastructure & Environment Requirements

Same as v1/v2 (local laptop). No new hosted infrastructure. The loop is a local process (optionally inside a detached tmux session). CI additions:

- `queue_gh.py` unit tests (faked `gh` subprocess; label-transition assertions).
- `CodexBackend` unit tests (argv construction; JSONL `parse_result` against captured fixtures; `preflight` auth-missing).
- `loop.py` unit tests (faked `gh` / `subprocess` / `time`; the triage → claim → dispatch → deliver → sync state machine; budget + breaker cutoffs; `reconcile_orphans`).
- `Deliverer` test asserting **branch-scoped push only** (never `main`, never force).

---

## 9. Compliance & Regulatory Requirements

None. Same as v1/v2.

---

## 10. Quality Assurance Requirements

### Coverage targets
- **`loop.py`:** 85%+ (the driver; every lane + budget/breaker branch exercised via fakes).
- **`queue_gh.py`:** 90%+ (label transitions + sync outcome mapping — the correctness surface).
- **`CodexBackend`:** 85%+ (argv + parse + preflight).
- **Existing modules:** unchanged from v1/v2.

### Mandatory tests (new)

| Test | What it validates |
|---|---|
| **Queue — list/claim** | `list_ready` parses `gh` JSON; `claim` swaps `atlas:ready`→`atlas:working` + assigns. |
| **Queue — sync outcomes** | Merged PR → `done` + `user_signal` 1.0; closed-unmerged → `rejected` + 0.0; open → no-op. Idempotent on re-tick. |
| **Router — label wins** | `wf:quick` / `wf:planned` label overrides the classifier. |
| **Router — classify fallback** | Unlabeled issue → classifier picks a lane; result recorded as a span. |
| **One-shot lane** | `loop_dev` runs to completion → `Deliverer` opens exactly one PR (`Closes #n`). |
| **Planned lane stop** | `dev-docs-be` runs → plan-only PR with triad + Pending Decisions → loop STOPS (no code_gen this pass). |
| **CodexBackend argv/parse** | `build_argv` produces the `codex exec --json -C … --sandbox workspace-write` list; `parse_result` extracts status/stats from JSONL; `preflight` fails closed on missing auth (asserts no subprocess spawned). |
| **Claude JSON telemetry** | Loop-mode `ClaudeCodeBackend` parses `total_cost_usd` + `usage`; tokens reach plumb as `add_span(tokens=(in, out))`; `total_cost_usd` is surfaced in-memory only (no durable sink pre-P1-a — §3.6). Attended mode stays plain-text (byte-identity preserved). |
| **Deliverer safety** | Pushes a branch + `gh pr create`; **never** pushes `main`, **never** `--force`. |
| **Budget cutoff** | `max_runs_per_day` / `max_dollars_per_day` halt dispatch. |
| **Circuit breaker** | `no_progress_limit` / `identical_error_limit` open the breaker → cooldown → resume. |
| **Orphan reconciliation** | Startup resets stale `atlas:working` (no open PR) → `atlas:ready`; prunes stale worktrees. |
| **Attended-mode invariance** | Full v2 suite passes; `atlas run` unaffected by loop-mode additions. |

### Linters
Same as v1/v2: `ruff check`, `ruff format`, `mypy src` — all CI gates.

---

## 11. Deployment & Operations Requirements

Same as v1/v2 — no deployed surface; the repo is the artifact. The loop runs on the operator's laptop (optionally in a detached tmux session).

**Release tags (loop mode):**
- **v3.0** — measured single-run baseline + `CodexBackend` + `loop_dev.yaml` + `Deliverer` (Phases L0+L1 exit).
- **v3.1** — the loop daemon end-to-end (Phase L2 exit).
- **v3.2** — self-healing + score-informed routing (Phase L3 exit).
- **v3.3** — scale-out: second repo + concurrency > 1 + weekly report (Phase L4 exit).

---

## 12. Dependencies & Risks

### New dependencies

| Dependency | Type | Risk |
|---|---|---|
| `gh` CLI | External CLI | Low — stable, already authenticated. Network-bound; failures handled as recoverable ticks. |
| Codex CLI (`codex exec`) | External CLI | **Low–Medium (downgraded 2026-07-24)** — schema and flag surface now **verified against `codex-cli 0.144.4`** (§3.3); auth path (`$CODEX_HOME/auth.json`) confirmed present. Residual risk is version drift (the JSONL schema is undocumented and unversioned, so a CLI upgrade can silently change event shapes — mitigated by pinning the observed version in `headless-clis-reference.md` and by `parse_result` failing loudly with `codex_no_turn_completed` rather than mis-parsing). Still opt-in via `engine:codex`; the loop defaults to `claude`. |
| tmux | External CLI | Low — observability only; `atlas loop run` works without it. |

### Risks

| Risk | Impact | Probability | Mitigation |
|---|---|---|---|
| Loop mode becomes a framework (schedulers, DAGs, plugin system) | High | Medium | The loop is a `while` over `tick()`; `tick()` is a linear state machine. New code = queue adapter + loop + deliverer + one backend. No scheduler, no DAG engine. If it grows one, it has drifted from scope — same vow as v1/v2. |
| Runaway cost / infinite loop | High | Medium | Dual bound: per-day cost cap **and** no-progress circuit breaker. Both required; neither alone is sufficient. ⚠ **Two independent gaps, not one.** (a) `max_dollars_per_day` cannot read `runs.dollar_cost` until plumb P1-a (that column is never written — §3.6); until then L2 must accumulate the **in-memory `total_cost_usd`** each backend returns, persisted across restarts. (b) **That fallback does not work for `codex` at all** — the Codex CLI returns no cost figure (§3.3, verified 2026-07-24), so a Codex-heavy day is bounded only by `max_runs_per_day`. **`max_runs_per_day` is therefore the load-bearing cap, not the backstop**, and must be set conservatively enough to bound spend on its own. Resolve in the L2 TRS. |
| Prompt injection via issue body | High | Low (private repo) → High (if public) | Private single-author assumption in v3; `trusted_authors` allowlist becomes mandatory the moment a repo is public/multi-author (§4). |
| Codex headless auth blocks `engine:codex` | Medium | **Low (was Medium)** | Auth path verified 2026-07-24 (`$CODEX_HOME/auth.json` present after `codex login`; `OPENAI_API_KEY` also accepted). Opt-in; default `claude`; `preflight()` fails closed with a clear error; Claude-only fallback is a config change, not a rewrite. |
| Codex CLI upgrade silently changes the undocumented JSONL schema | Medium | Medium | The event schema is undocumented and unversioned — a `codex` upgrade can change it without notice. `parse_result` is written to fail **loudly** (`codex_no_turn_completed`) rather than silently mis-parse; the observed version is pinned in `headless-clis-reference.md`; fixtures are real captures, so a schema change surfaces as a test failure on the next capture refresh, not as corrupted telemetry. |
| Cost-per-landed-PR is undefined for `codex` runs | Medium | **High (certain, pre-mitigation)** | Codex emits no cost figure at all (§3.3, §3.6) — unlike Claude, where cost exists but lacks a sink. Cross-engine cost comparison therefore requires deriving dollars from tokens × a per-model price table, which atlas does not have and which is **not in v3 scope**. Until then §13 #12's report is **tokens-per-landed-PR** for Codex and (post-P1-a) dollars for Claude. Resolve in the L2 TRS. |
| Crash strands an `atlas:working` issue | Medium | Medium | `reconcile_orphans()` on startup resets stale labels + prunes worktrees. |
| Double-scoring on sync re-tick | Low | Medium | Idempotent sync via local dedupe (`issue+pr+outcome`); durable once plumb v1.1 lands idempotent scoring. |
| Planned-lane multi-PR bookkeeping confuses issue state | Medium | Low | `Refs #n` on task PRs, `Closes #n` only on the last; issue closes on final merge; `atlas loop status` shows in-flight planned issues. |
| `.atlas/current-run` single-run assumption blocks concurrency | Medium | Low (deferred) | v3.0–v3.2 fixed at `concurrency=1`; Phase L4 lifts it with per-run state keys — a scoped change, flagged in Appendix A. |

### Resolved decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | **Queue = GitHub Issues, not a markdown board.** | Issue → branch → PR → merge is one service; `Closes #n` links automatically; merge status is the quality feedback channel. Supersedes the shipwright-design markdown control plane (chosen there for demo-safety). A graph tracker (beads) is a later swap behind `queue_gh`. |
| 2 | **Loop lives in atlas, not a separate repo.** | Reuses `Pipeline` / `WorktreeManager` / `CliBackend` / `PlumbIO` in-process rather than wrapping atlas from outside. Supersedes the shipwright "separate public repo" decision. |
| 3 | **Autonomy stops at the PR.** | Agent opens a PR; the operator merges. No auto-merge in v3. The PR review is the attestation gate — asynchronous and batchable, unlike an inline `input()` prompt. |
| 4 | **Both engines from day one.** | `claude` + `codex` dispatch from v3.0 so plumb compares cost/quality per task class. `agy` stays experimental (browser-OAuth auth). |
| 5 | **Delivery is a post-success hook, not a stage.** | A `StageSpec` would put delivery in the workflow YAML and measure it as a pipeline stage; it is neither. An injected `Deliverer` keeps attended workflows unaffected. |
| 6 | **Two lanes, hybrid routing; planned lane pauses after the TRS.** | Small vs. large issues need different paths; the planned lane drives the operator's own TRS discipline and escalates decisions as a PR review. Pause-after-TRS is the default (auto-proceed deferred). |
| 7 | **Sequential in v3.0–v3.2.** | Preserves the v2 single-run state model; concurrency is the only piece that forces a state-key change, so it is isolated to Phase L4. |
| 8 | **Claude JSON output is loop-only.** | Attended `dev` runs keep plain-text stdout for gate-parity (v2 Phase-3 decision); loop runs need JSON for cost/token telemetry. Gated by a per-run flag. |

---

## 13. Success Criteria & Acceptance Criteria

v3 milestones ship when the following hold.

### v3.0 — Measured baseline + engines + delivery (Phases L0+L1 exit)
1. **Live attended run, measured.** A live `atlas run "<task>"` on the `claude` backend produces a plumb run whose `code_gen` span carries real `tokens` from the backend JSON (the first-ever live run — see L0). **Run-level `dollar_cost` (and the run-level token roll-up) is deferred to plumb P1-a (`set_usage`) and is NOT an L0 exit gate** — it becomes real when plumb v1.1 lands, and is verified at L2 (cost-per-landed-PR). See §3.6 for why tokens and dollars are not symmetric here.
2. **Attended-mode invariance.** Full v2 suite green; `atlas run` unchanged.
3. **`CodexBackend` dispatch.** A `loop_dev` run under `engine:codex` produces a valid `StageOutcome` (mocked in CI via captured JSONL; real dispatch in manual testing if auth allows). `preflight` fails closed on missing auth with no subprocess spawned.
4. **Delivery primitive.** The `Deliverer` pushes a branch + opens a PR for a completed run and calls `cleanup()`; asserted never to push `main` or force-push.

### v3.1 — The loop daemon (Phase L2 exit)
5. **Zero-touch delivery (headline).** One `atlas:ready` issue in the atlas repo → `atlas loop start` → a PR appears (`Closes #n`) with a plumb `run_id` comment, with **zero keystrokes between labeling and reviewing**. Merging it makes the next tick write a `user_signal` success and close the issue. **Requires plumb P1-a** for the cost half of the story: run-level `dollar_cost` (and therefore cost-per-landed-PR) is unwritable until `set_usage` + `finalize_run` threading land — until then L2 reports tokens, not dollars (§3.6).
6. **Two-lane routing works.** A `wf:quick` issue yields one PR; a `wf:planned` issue yields a plan-only PR (triad + Pending Decisions) and the loop stops.
7. **Budgets & breaker.** Per-day cost/run caps halt dispatch; the breaker opens on no-progress/identical-error thresholds and resumes after cooldown.
8. **Crash recovery.** Killing the loop mid-run and restarting resets the stranded issue and prunes its worktree.

### v3.2 — Self-healing + routing (Phase L3 exit)
9. **Diagnosis-injected retry.** A verify/judge failure is captured as a plumb example, classified, and retried **once** as a child run (`parent_run_id`) with the diagnosis injected; exhaustion → `atlas:blocked`.
10. **Pre-PR judge gate.** A plumb judge score below threshold blocks delivery.

### v3.3 — Scale-out (Phase L4 exit)
11. **Second repo + concurrency.** The plumb repo runs as a second target; `concurrency > 1` works with per-run state keys.
12. **Weekly report.** `plumb run stats` yields a cost-per-landed-PR + intervention-rate summary. **Requires plumb P1-a** for the cost dimension (§3.6); pre-P1-a the report is tokens-per-landed-PR + intervention rate. **For `codex` runs the cost dimension requires more than P1-a** — the Codex CLI emits no cost figure at all (§3.3), so dollars must be derived from tokens × a per-model price table that does not exist in v3. Cross-engine comparison is therefore **tokens-only** unless that table is built.

### Cross-cutting
13. **LoC discipline.** Loop-mode code (loop + queue adapter + deliverer + CodexBackend) stays small — a state machine, not a framework. Target ≤ ~500 lines net across the new modules.
14. **No plumb migration for v3.0–v3.2.** plumb `SCHEMA_VERSION` unchanged; v1.1 improvements are additive when they land.

---

## 14. Development Phases

> Each phase below is written to become one per-phase TRS via `dev-docs-be`. Phases are tagged to the v3.x releases in §11. Effort tags are rough. Paths under `src/atlas/`.

### Phase L0 — Honest baseline  → delivers part of `v3.0`

**Goal:** Make the existing single-run path real for the first time, and add the telemetry + permission + delivery primitives the loop depends on. **No loop yet.**

**Dependencies:** Shipped v2.2.

**Engineering scope summary:**
- Version reconciliation: bump `pyproject.toml` → `2.2.0`, tag `v2.2`; fix or `xfail` the content-pipeline drift integration test so a green suite means green. **Resolved (2026-07-21, verified against the sibling repo): `xfail` is correct here, and this is not a judgment call.** content-pipeline **decomposed** `ScoreJobsUseCase` into a three-stage pipeline (`application/use_cases/score_jobs_{ingest,prep,score}.py`, plus `score_merge.py`); no `ScoreJobsUseCase` class exists anywhere in that repo. So `LIB:content_pipeline.score_jobs`'s adapter targets a **superseded API** — not a rename (no one-line fix) and not unreachable (not a true xfail-forever). Re-targeting it means designing how the adapter composes ingest → prep → score, which is **`job`-workflow scope, unrelated to loop mode**. L0 marks it `xfail(strict=False)` with a reason string naming the three replacement modules + a BACKLOG entry, keeping L0's suite honest-green without smuggling an unrelated redesign into the loop's first phase.
- **First live attended run** (has never happened): `atlas run "<small task>" --workflow dev` against the real `claude` backend; confirm subprocess spawn + gate prompts + a plumb run with spans. Capture findings into `headless-clis-reference.md`.
- `ClaudeCodeBackend` loop-mode telemetry (§3.6): `--output-format json`; `parse_result` surfaces `total_cost_usd` + `usage`; thread into plumb via `plumb_io.py`. Guard behind a per-run flag (attended stays plain-text). **Note (verified against plumb v1.0.1):** tokens land at the **span** level via `add_span(tokens=(in, out))`; `total_cost_usd` has **no durable sink** until plumb P1-a and is in-memory only. L0 reports tokens, not dollars — do not build against `runs.dollar_cost` here (§3.6, §13 #1).
- Headless permission profile (§3.6): `--permission-mode acceptEdits` + curated `--allowedTools` (target repo `.claude/settings.json`) + `--max-turns`. No `--dangerously-skip-permissions`.
- `Deliverer` / `GhPrDeliverer` (§3.7): push branch + `gh pr create` + `WorktreeManager.cleanup()`; replaces the dead `merge_back()` path. Exercised manually in L0; the loop calls it in L2.

**Exit criteria:** §13 items 1, 2, 4.

---

### Phase L1 — CodexBackend + loop workflow  → completes `v3.0`

**Goal:** A second engine and a loop-shaped workflow, both selectable per run.

**Dependencies:** L0.

**Engineering scope summary:**
- `CodexBackend` (§3.3) per the v2 `CliBackend` Protocol; register in `_KNOWN_BACKENDS` / `make_backend()`. `build_argv` = `codex exec … --json -C <worktree> --sandbox workspace-write --model <m> [--add-dir <repo_root>]`; `parse_result` derives status from the **exit code** (the stream has no status field), asserts a `turn.completed` event, and joins agent text from `item.completed`/`agent_message` events; `preflight` fails closed on missing auth (`OPENAI_API_KEY` or `$CODEX_HOME/auth.json`) with no subprocess spawned. **Schema verified against `codex-cli 0.144.4` — see §3.3's correction box.**
- `loop_dev.yaml` (§3.4): ungated `plan → code_gen(isolate) → verify`, distinct from `dev.yaml`. Finalize guardrail "signs".
- Add a Codex section to `headless-clis-reference.md`.
- Tests: `CodexBackend` argv/parse (captured JSONL fixtures) + preflight; `loop_dev` in the loader tests. Manual smoke: `atlas run "<task>" --workflow loop_dev --backend codex`.

**Exit criteria:** §13 item 3; `loop_dev` runs end-to-end on both engines (manual smoke).

---

### Phase L2 — The loop daemon  → delivers `v3.1`

**Goal:** The poll-dispatch-deliver-sync loop — the core deliverable.

**Dependencies:** L1.

**Engineering scope summary:**
- `queue_gh.py` (§3.1): the `gh` adapter (list/claim/deliver_pr/comment/sync/relabel).
- `loop.py` (§3.5): `tick()` (sync-first → pull → triage → claim → dispatch → deliver → comment/relabel) + `run_forever()` + `reconcile_orphans()`. One issue per tick; sequential.
- Triage router (§3.2): `wf:*` label wins, else haiku classify.
- `[loop]` config (§7) — extend the frozen `Config`.
- CLI (§3.8): `atlas loop run|start|stop|status|attach` (tmux wrapper for start/stop/attach).
- Budgets + circuit breaker (§3.5, §5).
- Tests: faked `gh`/`subprocess`/`time`; the full state machine + budget/breaker + orphan reconciliation.

**Exit criteria:** §13 items 5, 6, 7, 8.

---

### Phase L3 — Self-healing + routing  → delivers `v3.2`

**Goal:** Rescue failures with diagnosis rather than blind retry; begin score-informed routing.

**Dependencies:** L2.

**Engineering scope summary:**
- Pre-PR judge gate: `plumb judge` (haiku) over the diff → task-completion score; threshold (default 0.7) gates delivery.
- Diagnosis-injected retry: `write_example`(origin_run_id) → judge classifies failure mode (`flaky` / `wrong_approach` / `missing_context` / `infeasible`) → one child-run retry (`reopen_run` w/ `parent_run_id`) with diagnosis injected → else `atlas:blocked`.
- Router v1 (stretch): prefer the engine/workflow that scores better in plumb for the task class.

**Exit criteria:** §13 items 9, 10.

---

### Phase L4 — Scale-out  → delivers `v3.3`

**Goal:** More than one repo, more than one concurrent run, and a recurring report.

**Dependencies:** L3.

**Engineering scope summary:**
- Add the plumb repo as a second target (its own backlog → issues).
- Concurrency > 1: lift the `.atlas/current-run` single-run assumption via per-run state keys (Appendix A); bound by a semaphore at `[loop].concurrency`.
- Weekly `plumb run stats` → a cost-per-landed-PR + intervention-rate report (cost dimension requires plumb P1-a — §3.6).

**Exit criteria:** §13 items 11, 12.

---

## Appendix A — Codebase seam inventory (loop mode)

Grounded in the current v2.2 source. "New" modules are the only substantial additions; everything else is a small, localized edit.

| File | Action | Change |
|---|---|---|
| `cli_backend.py` | Modify | Add `CodexBackend`; extend `_KNOWN_BACKENDS` / `make_backend()`. Add `--output-format json` (loop-mode flag) to `ClaudeCodeBackend`; surface `total_cost_usd` + `usage` in `parse_result`. |
| `plumb_io.py` | Modify | Thread backend `usage` into the **span** write as `add_span(tokens=(in, out))`. Run-level `tokens_in`/`tokens_out`/`dollar_cost` are **not** written (unwritable pre-P1-a — §3.6). |
| `worktree.py` | Wire | `cleanup()` finally called (by the `Deliverer`); `merge_back()` retired for loop mode. |
| `workflows/loop_dev.yaml` | New | The ungated one-shot workflow (§3.4). |
| `queue_gh.py` | New | GitHub Issues adapter (§3.1). |
| `loop.py` | New | The loop driver (§3.5): tick/run_forever/reconcile_orphans + triage + budgets + breaker. |
| `deliverer.py` (or in `loop.py`) | New | `Deliverer` Protocol + `GhPrDeliverer` (§3.7). |
| `config.py` | Modify | Add the frozen `[loop]` config block (§7). |
| `cli.py` | Modify | Register the `atlas loop` command group (§3.8). |
| `state.py` | Modify (L4 only) | Per-run state keys to lift the single-run assumption for `concurrency > 1`. Untouched in v3.0–v3.2. |
| `orchestrator.py` (`Pipeline`) | **Unchanged** | The loop constructs/drives `Pipeline` exactly as `cli.py::run` does. No pipeline shape change — verify, don't touch. |

If implementation finds `Pipeline` genuinely needs editing, that is a signal the design has drifted from this TRD — pause and reconcile.

## Appendix B — Cross-references

- Design note (source of truth): [`loop-mode-design.md`](../1_product_and_research/loop-mode-design.md)
- Headless CLI reference: [`headless-clis-reference.md`](../1_product_and_research/headless-clis-reference.md)
- plumb API reference: [`PLUMB_API_REFERENCE.md`](../1_product_and_research/PLUMB_API_REFERENCE.md)
- Backlog: [`BACKLOG.md`](../1_product_and_research/BACKLOG.md)
- v2 TRD (engine): [`TRD-v2.md`](./TRD-v2.md)
- v1 TRD: [`TRD.md`](./TRD.md)
- System design (loop-mode section): [`system_design.md`](./system_design.md)
- Engine seams reused: `src/atlas/{orchestrator.py,cli_backend.py,worktree.py,plumb_io.py,workflow_loader.py,state.py,config.py}`
