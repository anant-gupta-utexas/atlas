# Technical Requirements Document (TRD) — v3

**Project:** atlas — autonomous, minimal-input development loop ("loop mode")
**Scope:** v3 (autonomy layer on top of the v2 workflow engine). Builds on the v1 and v2 TRDs; supersedes neither.
**Status:** Phases **L0, L1 and L2 are COMPLETE and verified against real systems (2026-07-27)** — §13 criteria #1–#8 all hold. Phases L3–L4 remain planning. This document stays a **phase contract**: met criteria are annotated, not deleted, and the places where reality diverged from the contract are marked ⚡ rather than quietly rewritten.
**Created:** 2026-07-21
**Last updated:** 2026-07-27 (L0–L2 closure pass)
**Grounds on:**

- [`loop-mode-design.md`](../1_product_and_research/loop-mode-design.md) — the source-of-truth design note (problem, locked decisions, two-lane routing, phases, risks). **Read this first.**
- [`headless-clis-reference.md`](../1_product_and_research/headless-clis-reference.md) — per-CLI flag/auth/quota reference (Claude Code, Antigravity; Codex section added in Phase L1).
- [`TRD-v2.md`](./TRD-v2.md) — the shipped v2 engine (YAML workflows, `CliBackend`, worktree isolation). v3 reuses this machinery; it does not modify the v2 contract.
- [`TRD.md`](./TRD.md) — v1 NFRs, integration contracts, success criteria (carry forward).
- [`system_design.md`](./system_design.md) — component architecture (loop-mode section appended alongside this TRD).

> **Relationship to v2.** TRD-v2 generalized the engine from one hardcoded dev pipeline to N YAML workflows with per-stage CLI backends. It stops explicitly short of "an HTTP shell, multi-tenancy, concurrent runs, a UI, or dynamic topology." v3 does **not** cross into dynamic topology or multi-tenancy — it adds the one deferred capability the loop needs: a **long-running driver** that pulls work from a queue and delivers PRs, reusing v2's `Pipeline` / `WorktreeManager` / `CliBackend` / `PlumbIO` unchanged.

---

## ⚡ Where reality diverged from this contract (L0–L2 closure, 2026-07-27)

Five load-bearing assumptions in this document were wrong. They are corrected
in place below; this table is the index so a reader who skims does not carry a
superseded claim away. Each was found by **running the manual off-CI checks**
(T-L0.8/T-L0.9/T-L1.1/T-L1.8/T-L2.13), not by testing — all eight defects the
field pass turned up survived 400+ green tests, `mypy --strict`, and a full
code review.

| # | This TRD said | Reality | Fixed in |
|---|---|---|---|
| 1 | Run-level `dollar_cost` is **unwritable** — "blocked on plumb P1-a" (§3.6, §5, §7, §13 #1/#5/#12) | **P1-a is CLOSED.** plumb v1.1 shipped `RunHandle.set_usage()`; atlas writes run-level `dollar_cost` today, and `spans.tokens_in`/`tokens_out` are durable columns. The "in/out split is not durable" claim is obsolete. | §3.6, §5, §7 |
| 2 | Codex's `cached_input_tokens` is *probably* an addend to `input_tokens` (§3.6 Pending Decision #4) | **Subset, not addend** — measured with a cold/warm capture pair. atlas had been inflating every Codex span's input by ~70–90%. Rule is now `openai_subset_fields_v2`. | §3.3, §3.6 |
| 3 | Engine resolves through the **4-tier** cascade with the loop's `engine:*` label injected as "highest practical precedence" (§3.3) | The override sat *below* the workflow `default_backend`, and every loop workflow declares one — so `engine:*` **and** `atlas run --backend` were both silently discarded. Resolution is now genuinely **5-tier** with the override on top. | §3.3 |
| 4 | Claude's JSON telemetry mode emits an object whose `subtype` maps to status (§3.6) | Claude Code 2.1.220 emits a **JSON array** of stream events terminated by a `type: "result"` element. Anthropic's token fields are **disjoint** — billed input is `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`. | §3.6 |
| 5 | §13 #1 ("live run carries real tokens") and §13 #2 ("attended argv unchanged") were in tension — nothing could request the envelope without changing attended behavior | Resolved by a separate opt-in flag: **`atlas run --telemetry`**, deliberately independent of the `acceptEdits` permission mode. | §3.6, §3.8 |

One further correction the contract never anticipated: `Config.model` is a
single global string defaulting to `"haiku"`, a **Claude** name, and it was
handed to every engine. `codex exec --model haiku` is an HTTP 400, so every
`--backend codex` run died in the plan stage. Model names are engine-specific
— see §3.3's `[backend.models]` note.

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
| CLI backend strategy + tiered resolution | `CliBackend`, `_KNOWN_BACKENDS` (`cli_backend.py`) | `CodexBackend` registered; engine chosen per `engine:*` label. ⚡ Not reused verbatim after all — the cascade gained a **tier-1 override** so the label can actually win (§3.3) |
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
- **Cost-per-landed-PR.** Total `dollar_cost` across runs / issues reaching a merged PR — queryable from plumb. ✅ **Unblocked 2026-07-27:** plumb v1.1's `set_usage()` landed and atlas writes run-level `dollar_cost` (§3.6). Applies to `claude` runs only — Codex reports no cost figure at any layer, so that lane stays tokens-only.
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

Engine per run resolves through a **5-tier** cascade. The loop injects the backend from an `engine:*` label as a per-run override at pipeline construction; `atlas run --backend X` uses the same tier.

1. **`override`** — an explicit, run-scoped human instruction (`atlas run --backend X`, or a loop issue's `engine:X` label)
2. Per-stage `StageSpec.backend`
3. Workflow `default_backend`
4. `.atlas.toml [backend] default`
5. Hard default `claude`

> **⚡ Correction (2026-07-26).** This section originally specified the v2 **4-tier** cascade with the override folded into tier 4, described as "highest practical precedence." It was not. Every shipped loop workflow declares `default_backend:` — `loop_dev.yaml` says `claude` — so tier 3 beat the override and **both** surfaces were inert with no error: `atlas run --backend codex --workflow loop_dev` ran claude (confirmed live; the run's spans came back stamped `engine: claude`), and the loop's `engine:*` label could never take effect, making §13 #3 unreachable by design. A silently-discarded explicit instruction is worse than overriding a YAML default the operator can see and edit, so the override was lifted to tier 1. Note the `atlas run --backend` help text still claims a stage's own `backend:` field wins — that string is stale relative to the implemented order.

**Model names are engine-specific (added 2026-07-27).** `Config.model` is one global string defaulting to `"haiku"` — a Claude name — and passing it to another engine is a hard failure, not a degraded default (`codex exec --model haiku` → HTTP 400). Per-engine names live in a new `.atlas.toml` section:

```toml
[backend.models]
codex = "gpt-5.1-codex"
```

`cli_backend.resolve_model()` takes the configured entry if present, `Config.model` for `claude` (preserving the byte-identical attended argv), and `""` for any other engine — which each backend reads as "use your own CLI default", always a valid name. Deliberately **not** a hardcoded cross-engine mapping table: model lineups change faster than atlas releases, and guessing a wrong name reproduces the same 400 with a different string.

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
| Model selection | `-m/--model <MODEL>` — **omitted entirely** unless `[backend.models] codex` is set, so codex uses its own default. Never hand it `Config.model` (a Claude name → HTTP 400). |
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
| `claude` | Yes (`total_cost_usd`) | ✅ **Yes** — plumb v1.1's `set_usage()` (adopted 2026-07-27) | **Resolved.** A real run wrote `dollar_cost = $0.1865061`. |
| `codex` | **No — never emitted** | N/A | Requires atlas to *derive* cost from tokens × a per-model price table |

The sink half is fixed; the *source* half is not, and never will be by plumb. **`dollar_cost` for Codex runs is unobtainable, not merely unstored** — a Codex run's `runs.dollar_cost` is correctly `NULL`, which is the honest value and deliberately not `0.0`. Until a price table exists (not in v3 scope), **cross-engine comparison is tokens-only**. The operational consequence is in §12's runaway-cost risk row: a Codex-heavy day advances only `max_runs_per_day`.

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
    tool: "verify"          # bare, NOT "/verify" — see below
    gate: null
    isolate: false
```

Quality is enforced by `verify` + the downstream PR review, not by inline gates. `code_gen` keeps `isolate: true` so it runs in a worktree.

Two tool-string details the sketch above got wrong, both found by running it:

- **`"/verify"` renders as `//verify`.** `build_prompt` adds the leading slash, so a plugin-command tool string must be bare. It is now `verify`, mapped in `PLUGIN_COMMANDS` like the other dev-pipeline slash commands.
- **`plugin_resolver.resolve()` did not special-case `RAW:` strings** despite its docstring claiming it did — it was a literal dict lookup, so every `RAW:` stage above raised `RoutingDriftError` under a real `atlas loop run` unless `.atlas.toml` carried a `[plugin_commands]` override for each. `resolve()` now returns `RAW:` strings verbatim (they are literal prompts from the workflow YAML, not plugin names, so there is nothing to allow-list); the allow-list still rejects unknown non-`RAW:` tool strings before any subprocess spawns.

The shipped `code_gen` prompt also explicitly instructs the agent to **commit** — the quick lane originally relied on it doing so unprompted, and it didn't, so the branch matched `main` and delivery produced nothing.

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

**⚡ Dispatch cwd differs between modes (added 2026-07-27).** Unattended dispatch runs with `cwd` set to the **worktree**; attended `atlas run` keeps the atlas install root, which is what plugin/skill discovery resolves against. The contract did not distinguish them, and running the loop from the checkout is how the field pass found an unattended agent **committing into the operator's own checked-out branch** — the worktree is a directory boundary, not a filesystem sandbox, and an agent handed the wrong cwd will happily write outside it. That case is now detected and reported rather than silently delivering an empty branch.

**Observability is load-bearing, not a nicety.** `atlas loop run` configures logging (and gained `--verbose`). Before it did, the daemon logged nothing at all — which is why the other defects in the field pass were invisible: runs reported `success` on every span while delivering nothing. Fixing observability first is what made the rest findable.

### 3.6 Headless telemetry & permission profile (loop runs)

Loop runs require two things attended runs don't:

**Telemetry.** `ClaudeCodeBackend.build_argv` adds `--output-format json`; `parse_result` maps the terminal `result` element's `subtype` → status and surfaces `total_cost_usd` + `usage`. **Guarded behind a per-run flag** so attended `dev` runs keep human-readable stdout — the v2 Phase-3 decision that Claude stays plain-text for gate-parity holds for attended mode only.

> **⚡ Envelope shape corrected (verified against Claude Code 2.1.220, 2026-07-26).** `claude -p --output-format json` emits a **JSON array** of stream events (`system` / `assistant` / `rate_limit_event` / …) terminated by a `type: "result"` element carrying `subtype`, `result`, `total_cost_usd` and `usage` — **not** the single object `--help` still describes and this TRD originally assumed. The parser's `startswith("{")` sniff therefore routed every real envelope into the plain-text branch, which is why no live run ever produced telemetry. Both shapes are now accepted (the last `result` element wins, mirroring `CodexBackend`'s `turn.completed` handling); a well-formed array with no `result` element fails loudly as `claude_no_result_event` rather than reporting a phantom success.
>
> **Anthropic's token fields are disjoint.** Billed input is `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`; `input_tokens` alone counts only *uncached* input. Reading it alone recorded a real 159,896-token `code_gen` span as **50 tokens**. A captured run reported `input_tokens=2` against `cache_read_input_tokens=19589` — the failure mode is orders of magnitude, not a rounding error.

**How telemetry is requested (added 2026-07-26).** `atlas run --telemetry` opts a single attended run into the JSON envelope; the loop always requests it. The flag is **deliberately separable from the `acceptEdits` permission mode** below, so measuring an attended run never silently widens what the agent is allowed to do. Off by default because it changes the dispatched argv, and §13 #2 requires the attended argv stay byte-identical to pre-L0 without it. This is what resolved the standing tension between §13 #1 and §13 #2.

**Where that telemetry actually lands (re-verified against plumb v1.1, 2026-07-27):**

| Signal | Sink | Status |
|---|---|---|
| `usage` token fields | **Span-level** — `RunHandle.add_span(tokens=(in, out))`, threaded via `plumb_io.py` | ✅ **Works.** plumb v1.1 split the storage into durable `spans.tokens_in` / `spans.tokens_out` columns. |
| Raw per-engine token breakdown | **`spans.attributes`** (JSON), alongside the name of the reduction rule that produced the `(in, out)` pair | ✅ **Works.** This is what makes a wrong reduction rule a *recomputable* error rather than permanently corrupt data — and it earned its keep when Pending Decision #4 flipped (below). |
| `total_cost_usd` | **Run-level** — `RunHandle.set_usage(dollar_cost=…)` via `PlumbIO.set_usage()` | ✅ **Works.** There is still no per-span cost column and there will not be one; `set_usage` is deliberately run-level. |

> **⚡ The plumb P1-a deferral is CLOSED (2026-07-27).** This section previously stated that `runs.tokens_in`/`tokens_out`/`dollar_cost` "exist in plumb's schema but are not written by the online `with run()` path", that `RunHandle` exposes no cost setter, and that a live run therefore produces `dollar_cost = NULL`. **All three are obsolete.** plumb v1.1.0 shipped `RunHandle.set_usage(tokens_in, tokens_out, dollar_cost)`; atlas writes `dollar_cost` and leaves the token fields unset so plumb auto-fills them from buffered spans at close (last-call-wins per field; `dollar_cost` is never auto-filled, so it is the one field atlas must write itself). A live `claude` run wrote `$0.1865061`. Every "blocked on plumb P1-a" carve-out elsewhere in this document (§2, §5, §7, §12, §13 #1/#5/#12) is superseded by this row — the remaining Codex cost gap is a *source* problem, not a sink problem.
>
> The claim that "the in/out split is not durable" is likewise obsolete: plumb v1.1 stores the split.

**Per-engine asymmetry (verified 2026-07-24).** The table above describes `claude`. `codex` differs at the *source*, not just the sink:

| | `claude` | `codex` |
|---|---|---|
| Cost emitted by CLI | `total_cost_usd` | **Nothing** — no cost field exists |
| Path to durable cost | ✅ shipped — `set_usage()` (plumb v1.1) | still blocked: needs a per-model price table atlas does not have |
| Uncached input tokens | `input_tokens` | — |
| Cache-read tokens | `cache_read_input_tokens` | `cached_input_tokens` |
| Cache-**write** tokens | `cache_creation_input_tokens` | *(not reported)* |
| Output tokens | `output_tokens` | `output_tokens` |
| Reasoning tokens | *(not reported — folded into output)* | `reasoning_output_tokens` |

**Neither CLI's token schema is a superset of the other**, which is why the two backends keep separate usage dataclasses rather than sharing one.

**How this collapses at the span level.** Each backend reduces its own usage dataclass to the `(in, out)` pair `add_span` takes. plumb v1.1 stores that pair in durable `spans.tokens_in`/`tokens_out` columns (the pre-v1.1 single summed `tokens` column, and the "the in/out split is not durable" note this section used to cite, are both superseded). The two rules are **not the same shape**, because the two vendors' conventions are opposites:

```
claude  (cache_fields_disjoint_addends_v1):
          in  = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
          out = output_tokens

codex   (openai_subset_fields_v2):
          in  = input_tokens          # cached_input_tokens ⊆ input_tokens
          out = output_tokens         # reasoning_output_tokens ⊆ output_tokens
```

> **⚡ Pending Decision #4 is RESOLVED (T-L1.1, 2026-07-26) — and resolved the *other* way.** This section previously flagged whether Codex's `cached_input_tokens` is an *addend* to `input_tokens` (Anthropic's convention) or a *subset* of it as open, noting the captured sample fit both readings. atlas shipped the addend assumption; it was **wrong**, and it had been inflating every Codex span's input by ~70–90%.
>
> Settled by direct measurement — a cold/warm capture pair on `codex-cli 0.144.4`, same prompt and directory, back to back: `input_tokens` held flat (68,719 → 69,161, +0.6%) while `cached_input_tokens` rose 29% (48,384 → 62,464). Under the addend model `input_tokens` had to *fall* by ~14k as more of the prompt became cacheable. It did not. `input_tokens` is the whole prompt; `cached_input_tokens` is the served-from-cache portion of it — matching OpenAI's documented `prompt_tokens_details.cached_tokens ⊆ prompt_tokens`.
>
> The rule name is now `openai_subset_fields_v2`. **Spans written under the old `cached_input_as_addend_v1` rule remain recomputable** because the raw four-field breakdown and the rule's name were persisted to `spans.attributes` — the L1 code review's M1 mechanism earning its keep. Change the rule, bump the constant; never edit stored spans.
>
> One honest caveat: `reasoning_output_tokens ⊆ output_tokens` is **convention plus consistency with the measured cached result, not an independent measurement**. A run with `output_tokens=206` / `reasoning=50` against a ~46-token visible message fits either model arithmetically, since tool-call arguments are also billed output. Flagged rather than overclaimed.

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
atlas loop run [--verbose]     # foreground (debugging); runs run_forever in this terminal
atlas loop start               # tmux new -d -s atlas-loop 'atlas loop run'   (detached)
atlas loop stop                # tmux kill-session -t atlas-loop
atlas loop status              # budgets used, last tick, in-flight issue, breaker state
atlas loop attach              # tmux attach -t atlas-loop
```

**Also added to the attended command (2026-07-26), both shipped in L2's closure pass:**

```bash
atlas run "<task>" --backend codex     # tier-1 engine override (§3.3)
atlas run "<task>" --telemetry         # request the JSON envelope (§3.6); off by default
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
- **⚡ plumb v1.1 is adopted; the v1.0.1 carve-out is gone.** This bullet previously read "plumb v1.0.1 is sufficient — with one carve-out", the carve-out being that run-level `dollar_cost` was unwritable from the online `with run()` path and therefore a genuine P1-a dependency blocking cost-per-landed-PR and `max_dollars_per_day`. **plumb v1.1.0 shipped `RunHandle.set_usage()` and atlas adopted it 2026-07-27**, so both are live: `atlas loop status` reports real accumulated spend. Remaining plumb v1.1 niceties (durable `rationale`, first-class `add_example`, idempotent scoring) improve the self-healing and sync paths but were never hard blockers — the interim private-API / local-dedupe patterns from v2 carry those gaps. **Caveat:** plumb is still consumed as a **local path dependency**; a versioned pin needs a `v1.1.0` tag in the plumb repo, which does not exist yet (BACKLOG).
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
max_dollars_per_day = 10.0                # LIVE since 2026-07-27 (plumb v1.1 set_usage).
                                          # claude-reported only — codex runs advance the
                                          # runs cap but not this one (§3.6, §12)
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
| `spans.tokens_in` / `tokens_out` (per-span) | Works | Populate from Claude/Codex JSON `usage` via `add_span(tokens=(in, out))`. **plumb v1.1 stores the split durably** — the earlier "persists summed" note is superseded (§3.6) |
| `spans.attributes` (per-span JSON) | Works | Persist the raw per-engine token breakdown + the reduction-rule name, so a rule that later proves wrong is recomputable (§3.6) |
| `runs.tokens_in` / `tokens_out` / `dollar_cost` | ✅ **Writable** — plumb v1.1's `RunHandle.set_usage()` | atlas writes `dollar_cost` and leaves the token fields unset so plumb auto-fills them from buffered spans. **The "blocked on plumb P1-a" verdict this row used to carry is closed** (§3.6) |
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
| **Claude JSON telemetry** | `ClaudeCodeBackend` parses `total_cost_usd` + `usage` from the **array** envelope (§3.6); the disjoint input fields are summed, not read singly; tokens reach plumb as `add_span(tokens=(in, out))` and cost as `set_usage(dollar_cost=…)`. Attended mode without `--telemetry` stays plain-text (byte-identity preserved). |
| **End-to-end telemetry chain** | *(added post-L2 field pass)* A dispatch that reports usage must produce a span with non-zero tokens **and** a run with a non-`NULL` `dollar_cost`. The original phase shipped every link of this chain except the connections between them — `parse_usage()` had no caller, `StageOutcome` had no usage field, `record_span()` was called with no `tokens=`, and nothing set the telemetry flag. Unit-testing the links individually is what let that pass. |
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
| Runaway cost / infinite loop | High | Medium | Dual bound: per-day cost cap **and** no-progress circuit breaker. Both required; neither alone is sufficient. **Gap (a) is CLOSED (2026-07-27):** `max_dollars_per_day` accumulates real spend (plumb v1.1 `set_usage` + the L0 telemetry chain); `atlas loop status` reported `$2.5822 / $5.00` across two live loop runs, where it previously printed "not tracked (cap NOT enforced)". **Gap (b) stands and is permanent:** the Codex CLI returns no cost figure at all (§3.3), so a Codex-heavy day is bounded only by `max_runs_per_day`. **On the Codex lane `max_runs_per_day` is therefore the load-bearing cap, not the backstop**, and must be set conservatively enough to bound spend on its own. The runtime string names the asymmetry rather than hiding it. |
| Prompt injection via issue body | High | Low (private repo) → High (if public) | Private single-author assumption in v3; `trusted_authors` allowlist becomes mandatory the moment a repo is public/multi-author (§4). |
| Codex headless auth blocks `engine:codex` | Medium | **Low (was Medium)** | Auth path verified 2026-07-24 (`$CODEX_HOME/auth.json` present after `codex login`; `OPENAI_API_KEY` also accepted). Opt-in; default `claude`; `preflight()` fails closed with a clear error; Claude-only fallback is a config change, not a rewrite. |
| Codex CLI upgrade silently changes the undocumented JSONL schema | Medium | Medium | The event schema is undocumented and unversioned — a `codex` upgrade can change it without notice. `parse_result` is written to fail **loudly** (`codex_no_turn_completed`) rather than silently mis-parse; the observed version is pinned in `headless-clis-reference.md`; fixtures are real captures, so a schema change surfaces as a test failure on the next capture refresh, not as corrupted telemetry. |
| Cost-per-landed-PR is undefined for `codex` runs | Medium | **High (certain — unmitigated, by decision)** | Codex emits no cost figure at all (§3.3, §3.6). Claude's half is now resolved (`set_usage`, live); Codex's is not, and cannot be by plumb — it requires deriving dollars from tokens × a per-model price table, which atlas does not have and which is **not in v3 scope**. §13 #12's report is therefore **dollars for Claude, tokens-per-landed-PR for Codex**. A Codex run's `runs.dollar_cost` stays `NULL` rather than `0.0`, so the gap reads as missing data instead of free work. |
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

> **Status (2026-07-27): #1–#8 all MET**, verified live against the real
> `anant-gupta-utexas/atlas` repo with real tokens — not mocked, not inferred
> from tests. Criteria are annotated rather than deleted: this is a contract,
> and the record of what it originally demanded (including where it demanded
> the wrong thing) is the point. #9–#12 remain open. Evidence below is the
> summary; the full field log is in
> [`dev/archive/loop-mode-phase-L2/loop-mode-phase-L2-tasks.md`](../../dev/archive/loop-mode-phase-L2/loop-mode-phase-L2-tasks.md).

### v3.0 — Measured baseline + engines + delivery (Phases L0+L1 exit) — ✅ MET
1. ✅ **Live attended run, measured.** A live `atlas run "<task>"` on the `claude` backend produces a plumb run whose `code_gen` span carries real `tokens` from the backend JSON. **Verified (T-L0.8):** `code_gen` carried **161,200 real tokens**, and run-level `dollar_cost` came back **`$0.1865061`**.
   > ⚡ This criterion originally deferred run-level `dollar_cost` to plumb P1-a and explicitly excluded it from the L0 gate. **plumb v1.1 landed and atlas adopted it**, so the deferral is void and the dollar figure is part of the evidence above. The criterion was also **unimplementable as written** until the field pass: nothing in production requested the JSON envelope, so no live run could ever have carried tokens (see §3.6 and the divergence table at the top).
2. ✅ **Attended-mode invariance.** Full v2 suite green; `atlas run` unchanged. Preserved by making telemetry opt-in behind `--telemetry` — without the flag the attended argv is byte-identical to pre-L0.
   > ⚡ #1 and #2 were in **direct tension** as written: #1 needed the JSON envelope, #2 forbade changing the attended argv, and the contract named no mechanism to satisfy both. The `--telemetry` flag is that mechanism, added during the field pass rather than designed up front.
3. ✅ **`CodexBackend` dispatch.** `preflight` fails closed on missing auth with no subprocess spawned. **Verified (T-L1.8):** `loop_dev` completed under **both** `claude` and `codex` — real dispatch, not mocked. Codex's run-level `dollar_cost` is correctly `NULL` (that CLI reports no cost), not `0.0`.
   > ⚡ Unreachable as originally specified: the `engine:*` override sat below the workflow `default_backend`, so a `loop_dev` run under `engine:codex` ran claude. Fixed by the 5-tier order (§3.3). A second blocker surfaced immediately after: `Config.model`'s Claude name was handed to codex, and `codex exec --model haiku` is an HTTP 400 — every codex run died in the plan stage until `[backend.models]` landed.
4. ✅ **Delivery primitive.** The `Deliverer` pushes a branch + opens a PR and calls `cleanup()`. **Verified (T-L0.9, T-L2.13):** real PRs **#8** and **#11**; `main` never pushed to, never force-pushed.

### v3.1 — The loop daemon (Phase L2 exit) — ✅ MET
5. ✅ **Zero-touch delivery (headline).** **Verified (T-L2.13):** issue **#7** → PR **#8** carrying `Closes #7` plus a plumb `run_id` comment, with **zero keystrokes between labeling and reviewing**. Merging it made the next tick write `user_signal=approved` — anchored to a real `pr_outcome`/`handoff` span, not a dangling `span_id` — and relabel the issue `atlas:done`.
   > ⚡ The "requires plumb P1-a for the cost half" caveat is void: `dollar_cost` is written. Two defects had to be fixed before this criterion was even observable — `sync()` listed only *open* issues, but atlas's own `Closes #n` closes the issue on merge, making the merged outcome structurally unobservable; and fixing that immediately exposed `kind="deliver"`, which is not a valid plumb `SpanKind` and crashed every tick.
6. ✅ **Two-lane routing works.** **Verified (T-L2.13):** issue **#10** (`wf:planned`) → PR **#11** containing exactly the three TRS triad files, with a `dev_docs_be` span and **no `code_gen` span** — the loop stopped for review as designed.
7. ✅ **Budgets & breaker.** **Verified (T-L2.13):** `atlas loop status` reports real accumulated spend, **`$2.5822 / $5.00`**, where it previously printed "not tracked (cap NOT enforced)". Caveat that does not go away: Codex runs advance only the runs cap (§3.3, §12).
8. ✅ **Crash recovery.** **Verified (T-L2.13):** `kill -9` mid-dispatch; restart reclaimed the issue and pruned the orphaned worktree.

### v3.2 — Self-healing + routing (Phase L3 exit)
9. **Diagnosis-injected retry.** A verify/judge failure is captured as a plumb example, classified, and retried **once** as a child run (`parent_run_id`) with the diagnosis injected; exhaustion → `atlas:blocked`.
10. **Pre-PR judge gate.** A plumb judge score below threshold blocks delivery.

### v3.3 — Scale-out (Phase L4 exit)
11. **Second repo + concurrency.** The plumb repo runs as a second target; `concurrency > 1` works with per-run state keys.
12. **Weekly report.** `plumb run stats` yields a cost-per-landed-PR + intervention-rate summary. The plumb P1-a prerequisite this criterion carried is **satisfied** (§3.6), so the cost dimension is available for `claude` runs. **For `codex` runs it is not, and P1-a was never the blocker** — that CLI emits no cost figure at all (§3.3), so dollars would have to be derived from tokens × a per-model price table that does not exist in v3. Cross-engine comparison remains **tokens-only** unless that table is built.

### Cross-cutting
13. **LoC discipline.** Loop-mode code (loop + queue adapter + deliverer + CodexBackend) stays small — a state machine, not a framework. Target ≤ ~500 lines net across the new modules.
14. **No plumb migration for v3.0–v3.2.** plumb `SCHEMA_VERSION` unchanged; v1.1 improvements are additive when they land.

---

## 14. Development Phases

> Each phase below is written to become one per-phase TRS via `dev-docs-be`. Phases are tagged to the v3.x releases in §11. Effort tags are rough. Paths under `src/atlas/`.

### Phase L0 — Honest baseline  → delivers part of `v3.0` — ✅ **COMPLETE (2026-07-27)**

**Goal:** Make the existing single-run path real for the first time, and add the telemetry + permission + delivery primitives the loop depends on. **No loop yet.**

**Dependencies:** Shipped v2.2.

> **Closure note.** L0 was declared code-complete well before its two manual checks ran, and the gap mattered: when T-L0.8 finally executed, **the telemetry chain this phase's whole purpose was to build had never been connected in production.** Every part existed and was unit-tested; none of them called each other. Treat "code-complete with manual checks outstanding" as *unverified*, not *nearly done*.

**Engineering scope summary:**
- Version reconciliation: bump `pyproject.toml` → `2.2.0`, tag `v2.2`; fix or `xfail` the content-pipeline drift integration test so a green suite means green. **Resolved (2026-07-21, verified against the sibling repo): `xfail` is correct here, and this is not a judgment call.** content-pipeline **decomposed** `ScoreJobsUseCase` into a three-stage pipeline (`application/use_cases/score_jobs_{ingest,prep,score}.py`, plus `score_merge.py`); no `ScoreJobsUseCase` class exists anywhere in that repo. So `LIB:content_pipeline.score_jobs`'s adapter targets a **superseded API** — not a rename (no one-line fix) and not unreachable (not a true xfail-forever). Re-targeting it means designing how the adapter composes ingest → prep → score, which is **`job`-workflow scope, unrelated to loop mode**. L0 marks it `xfail(strict=False)` with a reason string naming the three replacement modules + a BACKLOG entry, keeping L0's suite honest-green without smuggling an unrelated redesign into the loop's first phase.
- **First live attended run** (has never happened): `atlas run "<small task>" --workflow dev` against the real `claude` backend; confirm subprocess spawn + gate prompts + a plumb run with spans. Capture findings into `headless-clis-reference.md`.
- `ClaudeCodeBackend` loop-mode telemetry (§3.6): `--output-format json`; `parse_result` surfaces `total_cost_usd` + `usage`; thread into plumb via `plumb_io.py`. Guard behind a per-run flag (attended stays plain-text; the flag shipped as `atlas run --telemetry`). ⚡ **The v1.0.1-era note here — "tokens land at the span level; `total_cost_usd` has no durable sink until plumb P1-a; L0 reports tokens, not dollars" — is superseded.** plumb v1.1's `set_usage()` makes `runs.dollar_cost` writable, and L0's closure verified a real one (§3.6, §13 #1).
- Headless permission profile (§3.6): `--permission-mode acceptEdits` + curated `--allowedTools` (target repo `.claude/settings.json`) + `--max-turns`. No `--dangerously-skip-permissions`.
- `Deliverer` / `GhPrDeliverer` (§3.7): push branch + `gh pr create` + `WorktreeManager.cleanup()`; replaces the dead `merge_back()` path. Exercised manually in L0; the loop calls it in L2.

**Exit criteria:** §13 items 1, 2, 4. — ✅ all met; manual checks T-L0.8 (live measured run) and T-L0.9 (real `GhPrDeliverer.deliver()`) both executed. TRS archived at [`dev/archive/loop-mode-phase-L0/`](../../dev/archive/loop-mode-phase-L0/).

---

### Phase L1 — CodexBackend + loop workflow  → completes `v3.0` — ✅ **COMPLETE (2026-07-27)**

**Goal:** A second engine and a loop-shaped workflow, both selectable per run.

**Dependencies:** L0.

**Engineering scope summary:**
- `CodexBackend` (§3.3) per the v2 `CliBackend` Protocol; register in `_KNOWN_BACKENDS` / `make_backend()`. `build_argv` = `codex exec … --json -C <worktree> --sandbox workspace-write --model <m> [--add-dir <repo_root>]`; `parse_result` derives status from the **exit code** (the stream has no status field), asserts a `turn.completed` event, and joins agent text from `item.completed`/`agent_message` events; `preflight` fails closed on missing auth (`OPENAI_API_KEY` or `$CODEX_HOME/auth.json`) with no subprocess spawned. **Schema verified against `codex-cli 0.144.4` — see §3.3's correction box.**
- `loop_dev.yaml` (§3.4): ungated `plan → code_gen(isolate) → verify`, distinct from `dev.yaml`. Finalize guardrail "signs".
- Add a Codex section to `headless-clis-reference.md`.
- Tests: `CodexBackend` argv/parse (captured JSONL fixtures) + preflight; `loop_dev` in the loader tests. Manual smoke: `atlas run "<task>" --workflow loop_dev --backend codex`.

**Exit criteria:** §13 item 3; `loop_dev` runs end-to-end on both engines (manual smoke). — ✅ met. T-L1.1's write-heavy cold/warm capture **reversed** the phase's shipped cached-token assumption (§3.6); T-L1.8 ran `loop_dev` live on both engines, and surfaced the `[backend.models]` gap that had been killing every codex run. TRS archived at [`dev/archive/loop-mode-phase-L1/`](../../dev/archive/loop-mode-phase-L1/).

---

### Phase L2 — The loop daemon  → delivers `v3.1` — ✅ **COMPLETE (2026-07-27)**

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

**Exit criteria:** §13 items 5, 6, 7, 8. — ✅ all met, proven live by T-L2.13 against the real repo. That run found **eight defects invisible to CI**, plus two more while proving the fixes; the field-findings section of [`dev/archive/loop-mode-phase-L2/loop-mode-phase-L2-tasks.md`](../../dev/archive/loop-mode-phase-L2/loop-mode-phase-L2-tasks.md) is the authoritative record. TRS archived at [`dev/archive/loop-mode-phase-L2/`](../../dev/archive/loop-mode-phase-L2/).

---

### Phase L3 — Self-healing + routing  → delivers `v3.2` — **NEXT** (TRS written, not implemented)

**Goal:** Rescue failures with diagnosis rather than blind retry; begin score-informed routing.

**Dependencies:** L2 — ✅ satisfied. Phase TRS lives in [`dev/active/loop-mode-phase-L3/`](../../dev/active/loop-mode-phase-L3/).

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
- Weekly `plumb run stats` → a cost-per-landed-PR + intervention-rate report. The plumb-side prerequisite is satisfied (§3.6); the cost dimension covers `claude` runs and is structurally unavailable for `codex`.

**Exit criteria:** §13 items 11, 12.

---

## Appendix A — Codebase seam inventory (loop mode)

Grounded in the current v2.2 source. "New" modules are the only substantial additions; everything else is a small, localized edit.

| File | Action | Change |
|---|---|---|
| `cli_backend.py` | Modify | Add `CodexBackend`; extend `_KNOWN_BACKENDS` / `make_backend()`. Add `--output-format json` (telemetry flag) to `ClaudeCodeBackend`; surface `total_cost_usd` + `usage` in `parse_result`. **Also grew** (post-field-pass): `resolve_backend`'s tier-1 override, `resolve_model()` + `[backend.models]`, a `SpanUsage`/`UsageReporting` seam so `StageOutcome` can carry usage engine-agnostically, and the two named token-reduction rules (§3.6). |
| `plumb_io.py` | Modify | Thread backend `usage` into the **span** write as `add_span(tokens=(in, out))`, plus the raw breakdown into `spans.attributes`. ⚡ **Run-level write is now in scope**: `PlumbIO.set_usage()` writes `runs.dollar_cost` via plumb v1.1 and leaves the token fields for plumb to auto-fill. The "not written / unwritable pre-P1-a" instruction in this row is superseded. |
| `worktree.py` | Wire | `cleanup()` finally called (by the `Deliverer`); `merge_back()` retired for loop mode. |
| `workflows/loop_dev.yaml` | New | The ungated one-shot workflow (§3.4). |
| `queue_gh.py` | New | GitHub Issues adapter (§3.1). |
| `loop.py` | New | The loop driver (§3.5): tick/run_forever/reconcile_orphans + triage + budgets + breaker. |
| `deliverer.py` (or in `loop.py`) | New | `Deliverer` Protocol + `GhPrDeliverer` (§3.7). |
| `config.py` | Modify | Add the frozen `[loop]` config block (§7). |
| `cli.py` | Modify | Register the `atlas loop` command group (§3.8). **Also grew:** `atlas run --backend` / `--telemetry`; `atlas loop run --verbose` and its logging setup. |
| `state.py` | Modify (L4 only) | Per-run state keys to lift the single-run assumption for `concurrency > 1`. Untouched in v3.0–v3.2. |
| `pipeline_factory.py` | New *(not in the original inventory)* | `make_pipeline()` + `LastOutcomeRunner`, lifted out of `cli.py::_make_pipeline` so `cli.py::run`/`resume` and the loop's quick-lane dispatch share one construction path rather than two that could drift. Sits outside `cli.py` so `loop.py` need not import the CLI entry point. |
| `loop_budget.py` | New *(not in the original inventory)* | `LoopState` (`.atlas/loop-state.json`), budgets, circuit breaker — split out of `loop.py` post-review to keep the driver readable as L3 adds self-healing. `loop.py` re-exports the public names. |
| `triage.py` | New *(named as part of `loop.py` in the original inventory)* | The two-lane router (§3.2), its own module. |
| `orchestrator.py` (`Pipeline`) | ⚡ **Changed — three sanctioned exceptions** | The row originally read **Unchanged** ("verify, don't touch"). Three edits were needed and taken deliberately: (a) `run_to_completion()`'s return type widened `RunContext` → `RunResult(ctx, status)`, additive since both `cli.py` call sites discarded the value; (b) `SubprocessStageRunner` gained `backend_override` / `backend_models` / `max_turns` / usage plumbing; (c) a `loop_cwd_is_worktree` flag, because unattended dispatch must run inside the worktree (§3.5). None changes the pipeline's *shape* — no new stage type, no routing change, no gate-semantics change. |

If implementation finds `Pipeline` genuinely needs editing, that is a signal the design has drifted from this TRD — pause and reconcile. That check fired three times and was each time resolved as additive rather than structural; the file has since grown to ~900 LoC, over the 400/800-line guidance in `CLAUDE.md`, and a split is a tracked backlog item.

## Appendix B — Cross-references

- Design note (source of truth): [`loop-mode-design.md`](../1_product_and_research/loop-mode-design.md)
- Headless CLI reference: [`headless-clis-reference.md`](../1_product_and_research/headless-clis-reference.md)
- plumb API reference: [`PLUMB_API_REFERENCE.md`](../1_product_and_research/PLUMB_API_REFERENCE.md)
- Backlog: [`BACKLOG.md`](../1_product_and_research/BACKLOG.md)
- v2 TRD (engine): [`TRD-v2.md`](./TRD-v2.md)
- v1 TRD: [`TRD.md`](./TRD.md)
- System design (loop-mode section): [`system_design.md`](./system_design.md)
- Engine seams reused: `src/atlas/{orchestrator.py,cli_backend.py,worktree.py,plumb_io.py,workflow_loader.py,state.py,config.py}`
