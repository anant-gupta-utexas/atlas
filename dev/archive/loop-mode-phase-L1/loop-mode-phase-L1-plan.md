# TRS — Loop Mode, Phase L1: CodexBackend + loop workflow

**Source TRD:** [`docs/2_architecture/TRD-v3.md`](../../../docs/2_architecture/TRD-v3.md) §14 Phase L1
**Prior phase:** [`loop-mode-phase-L0`](../loop-mode-phase-L0/) — code-complete (T-L0.1–L0.7, L0.10, L0.11 done; T-L0.8/T-L0.9 are manual off-CI checks, not a blocker for L1 to start per STATUS.md 2026-07-22)

---

## Phase Summary

**Phase L1 — CodexBackend + loop workflow → completes `v3.0`**

> Goal (copied from TRD-v3 §14): *"A second engine and a loop-shaped workflow, both selectable per run."*

**Dependencies:** L0 (shipped/code-complete).
**Delivers:** part of PRD release `v3.0` — measured baseline + engines + delivery (TRD-v3 §11; the other part, L0, is already code-complete). L0 + L1 together satisfy the `v3.0` tag.
**Exit criteria (TRD-v3 §13):** item 3 — `CodexBackend` dispatch — plus `loop_dev` running end-to-end on both engines (manual smoke).

---

## Overview & Scope

L1 adds the second half of what `v3.0` needs before the loop daemon (L2) can exist: an alternate CLI backend (`codex exec`) implementing the same `CliBackend` Protocol as `ClaudeCodeBackend`/`AntigravityBackend`, and a new ungated workflow (`loop_dev.yaml`) shaped for unattended one-shot dispatch. Neither has a caller yet — `loop.py` (L2) is what will actually drive `Pipeline(loop_dev)` on a schedule. L1's job is to make both pieces *exist, be correct, and be manually provable* via `atlas run --workflow loop_dev --backend codex`, exactly as `cli.py::run` dispatches today.

**In scope:**
- `CodexBackend` class in `cli_backend.py`, registered in `_KNOWN_BACKENDS` / `make_backend()`.
- `loop_dev.yaml` — ungated 3-stage workflow (`plan → code_gen[isolate] → verify`).
- A **Codex Part** appended to `headless-clis-reference.md` (the doc explicitly has no Codex section yet — confirmed empty on read).
- A small, scoped fix to `Pipeline.run_to_completion()` so the terminal run status is programmatically visible to a caller outside the CLI (needed for the "guardrail signs" design — see below; not currently exposed, confirmed by reading `orchestrator.py`).
- Tests: `CodexBackend` argv/parse/preflight (captured-fixture based, no live network calls in CI); `loop_dev.yaml` loader tests; the `run_to_completion` status-surfacing change.
- Manual smoke test (off-CI, like L0's T-L0.8/T-L0.9 pattern): `atlas run "<task>" --workflow loop_dev --backend codex` and the same on `--backend claude`.

**Out of scope (see "What this TRS deliberately does NOT cover" below):** `loop.py`, `queue_gh.py`, `atlas loop` CLI, `[loop]` config, `Deliverer` wiring into any automatic caller, the planned lane, self-healing, judge scoring.

---

## Requirements Summary

From TRD-v3 §14 Phase L1 engineering scope summary, decomposed:

1. `CodexBackend` per the v2 `CliBackend` Protocol (§3.3); registered in `_KNOWN_BACKENDS` / `make_backend()`.
2. `build_argv` = `codex exec … --json -C <worktree> --sandbox workspace-write`.
3. `parse_result` consumes JSONL → final `result` event.
4. `preflight` fails closed on missing auth (no subprocess spawned).
5. `loop_dev.yaml` — ungated `plan → code_gen(isolate) → verify`, distinct from `dev.yaml`. "Finalize guardrail signs" (TRD-v3's own open phrasing — resolved below).
6. Codex section added to `headless-clis-reference.md`.
7. Tests: `CodexBackend` argv/parse (captured JSONL fixtures) + preflight; `loop_dev` in the loader tests.
8. Manual smoke: `atlas run "<task>" --workflow loop_dev --backend codex` (and `--backend claude`, since L1's exit criteria requires "both engines").

Exit criteria (TRD-v3 §13 item 3, restated): *"A `loop_dev` run under `engine:codex` produces a valid `StageOutcome` (mocked in CI via captured JSONL; real dispatch in manual testing if auth allows). `preflight` fails closed on missing auth with no subprocess spawned."* Plus: `loop_dev` runs end-to-end on both engines (manual smoke).

---

## Detailed Component Design

### Classes/Modules Structure

```
src/atlas/
├── cli_backend.py          # MODIFY — add CodexBackend, CodexUsageStats;
│                            #          register in _KNOWN_BACKENDS/make_backend()
├── orchestrator.py          # MODIFY (scoped) — Pipeline.run_to_completion()
│                            #          returns a terminal-status-bearing result
├── workflows/
│   └── loop_dev.yaml        # NEW — the ungated one-shot workflow
docs/1_product_and_research/
└── headless-clis-reference.md   # MODIFY — new "Part E — Codex CLI headless
                                  #          reference" section
tests/unit/
├── test_cli_backend.py      # MODIFY — CodexBackend argv/parse/preflight cases
└── test_workflow_loader.py  # MODIFY — loop_dev.yaml loads + validates
tests/integration/
└── test_cli_backend_dispatch.py  # MODIFY — CodexBackend dispatch fixture test
                                    #          + Pipeline status-surfacing test
tests/fixtures/
└── codex_jsonl/              # NEW — JSONL event-stream fixtures
    ├── success.jsonl         # the real captured 0.144.4 sample (verbatim)
    ├── multi_message.jsonl   # two agent_message items → join behavior
    ├── truncated.jsonl       # events but no turn.completed
    └── malformed.txt         # pure garbage → no turn.completed path
```

No new top-level module. This matches Appendix A's seam inventory exactly (`cli_backend.py` modify, `workflows/loop_dev.yaml` new) plus one addition this TRS makes explicit: `orchestrator.py` needs a narrow, additive change (see Pending Decision / Resolved Decision below) — Appendix A lists `orchestrator.py` (`Pipeline`) as **"Unchanged"** for the whole v3 arc, with the explicit caveat *"if implementation finds `Pipeline` genuinely needs editing, that is a signal the design has drifted from this TRD — pause and reconcile."* This TRS treats that caveat literally: see **Resolved Decision #3** for why this is judged a narrow, TRD-consistent exception rather than drift.

### Method Signatures

```python
# cli_backend.py

@dataclass(frozen=True)
class CodexUsageStats:
    """Token telemetry parsed from a `codex exec --json` turn.completed event.

    VERIFIED against codex-cli 0.144.4. Note the asymmetry with Claude's
    UsageStats: Codex reports NO dollar figure at all, so total_cost_usd is
    always None here (it exists on the dataclass only for call-site symmetry
    with UsageStats). Codex additionally reports cached_input_tokens and
    reasoning_output_tokens, which Claude's envelope does not carry in the
    same shape — captured here because reasoning tokens are billable output
    on reasoning models and dropping them would understate usage.
    """
    total_cost_usd: float | None   # ALWAYS None for Codex — CLI reports no cost
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_output_tokens: int | None


class CodexBackend:
    name = "codex"

    def build_argv(
        self,
        *,
        prompt: str,
        model: str,
        add_dirs: list[Path],
        timeout_s: int,
        extra_flags: dict[str, str],
    ) -> list[str]: ...
        # ["codex", "exec", prompt, "--json", "-C", <primary-dir>,
        #  "--sandbox", "workspace-write", "--model", model,
        #  "--add-dir", <each-additional-dir>...]
        # All four flags VERIFIED present in `codex exec --help` (0.144.4):
        # -C/--cd, --sandbox {read-only|workspace-write|danger-full-access},
        # -m/--model, --add-dir (repeatable, "additional directories that
        # should be writable alongside the primary workspace"), --json.

    def parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> tuple[str, str, str | None]: ...
        # Exit-code-driven status (the JSONL carries NO status field);
        # output text assembled from item.completed/agent_message events.
        # Never raises on malformed input.

    def parse_usage(self, stdout: str) -> CodexUsageStats | None: ...
        # Scans for the terminal `turn.completed` event's `usage` object.
        # Same additive-method pattern as ClaudeCodeBackend.parse_usage
        # (L0 Resolved Decision #1) — NOT a CliBackend Protocol member.

    def preflight(self) -> tuple[str, str | None] | None: ...
        # Checks OPENAI_API_KEY env var OR $CODEX_HOME/auth.json (default
        # ~/.codex/auth.json — VERIFIED present on this machine after
        # `codex login`). Fails closed with ("...", "codex_missing_auth").
        # No subprocess is spawned (env var + file existence check only).


def make_backend(name: str) -> CliBackend:
    # MODIFY: add `if name == "codex": return CodexBackend()` branch.
    # _KNOWN_BACKENDS becomes frozenset({"claude", "agy", "codex"}).
```

```python
# orchestrator.py — scoped addition (Resolved Decision #3)

@dataclass(frozen=True)
class RunResult:
    """Terminal outcome of a completed or paused run.

    Wraps the existing RunContext (unchanged) with the status
    Pipeline already computes internally in run_to_completion()'s loop but
    previously discarded after writing it to plumb. Additive: every existing
    call site that only reads `ctx` fields continues to work via `.ctx`.
    """
    ctx: RunContext
    status: str  # "success" | "failure" | "paused"  (paused = awaiting_hook timeout)

class Pipeline:
    def run_to_completion(self, ctx: RunContext) -> RunResult: ...
        # BEHAVIOR CHANGE: return type widens from RunContext to RunResult.
        # See Resolved Decision #3 for why this is in-scope for L1 and how
        # existing callers (cli.py::run, cli.py::resume) are updated.
```

### Data Structures

**`loop_dev.yaml`** (validated by the existing `workflow_loader.py` schema — no loader changes needed; confirmed by reading `_ALLOWED_TOP_LEVEL_KEYS`/`_ALLOWED_STAGE_KEYS`, both already a superset of what this file uses):

```yaml
name: loop_dev
default_backend: claude
stages:
  - name: plan
    span_kind: plan
    tool: "RAW:Read the issue and the repo; produce a short plan for this one change. Keep the plan to the acceptance criteria only — do not expand scope."
    isolate: false

  - name: code_gen
    span_kind: llm
    tool: "RAW:Implement the change to satisfy the issue's acceptance criteria. Commit your work with a descriptive message. Do not touch files outside the stated scope."
    isolate: true

  - name: verify
    span_kind: verify
    tool: "/verify"
    isolate: false
```

Notes on this shape, resolving TRD-v3's illustrative sketch (§3.4) into something loadable today:
- **No `gate:` key on any stage** — `gate_label` defaults to `None` via `workflow_loader.py`'s existing `raw_stage.get("gate")` (absent → `None`), which is exactly "ungated." The TRD's YAML sketch wrote `gate: null` explicitly; `workflow_loader.py`'s schema treats an *absent* key identically (confirmed by reading the loader — `gate_label = raw_stage.get("gate")`), so both are valid. This TRS omits the key entirely for brevity, matching `job.yaml`'s style more closely than `dev.yaml`'s (checked: `job.yaml`/`job_cli.yaml` also omit `gate` on ungated stages — confirmed convention).
- **`span_kind: llm` for `code_gen`**, not `subagent` (which `dev.yaml`'s `code_gen` uses). **Decided 2026-07-24 (maintainer).** `loop_dev`'s `code_gen` is a single `RAW:` prompt dispatch, not a named subagent invocation, and span kinds describe *what the span is* rather than *what role it plays* — the role is already carried by the stage name (`code_gen`, identical across both workflows). Querying "all code-writing stages" should filter on `name`, which is exact, not on `span_kind`, which would be a proxy that drifts as soon as another workflow dispatches code-gen differently.
- **`tool: "/verify"`** for the verify stage, per TRD-v3's own sketch and matching `dev.yaml`'s pattern is *not* directly reused (dev.yaml's `code_review` stage uses `tool: code-review`, a plugin-command, not `/verify` directly) — but TRD-v3 §3.4's YAML block is explicit (`tool: "/verify"`), so this TRS follows the TRD literally rather than dev.yaml's precedent.
- **Guardrail signs, resolved:** `loop_dev` has no gate, so quality enforcement is entirely `StageOutcome.status`-driven. `Pipeline.run_to_completion()` (widened per the Resolved Decision above) surfaces the terminal stage's status via `RunResult.status`. The loop-mode caller (a manual test harness in L1; `loop.py` in L2) checks `result.status == "success"` before invoking `Deliverer.deliver()` (L0's primitive, not wired to any caller yet). A `"failure"` status (e.g. `/verify` fails) is a hard stop — no PR opens, no code_gen output is silently delivered. This is the literal answer to TRD-v3 §14 L1's "finalize guardrail 'signs'" phrasing: the "sign" *is* `StageOutcome.status`/`RunResult.status`; no new type or mechanism is introduced.

**Codex JSONL fixtures** (`tests/fixtures/codex_jsonl/`) — **VERIFIED against `codex-cli 0.144.4`** (real captured output, supplied by the maintainer 2026-07-24; Pending Decision #1 is CLOSED):

```jsonl
{"type":"thread.started","thread_id":"019f96b7-e404-7673-8853-2938007f2629"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hi"}}
{"type":"turn.completed","usage":{"input_tokens":16668,"cached_input_tokens":13056,"output_tokens":5,"reasoning_output_tokens":0}}
```

**This schema differs materially from TRD-v3 §3.3's sketch and from Claude's JSON envelope. Four confirmed facts drive the whole `parse_result`/`parse_usage` design:**

1. **There is no `result` event.** The terminal event is `type: "turn.completed"`. TRD-v3 §3.3's phrase *"final `result` event carries status + stats"* describes a schema Codex 0.144.4 does not emit — the TRD is wrong on this point and this TRS supersedes it (see Resolved Decision #7).
2. **The terminal event carries no status and no text** — only `usage`. Success/failure must be determined from the **exit code**, not from event content.
3. **Agent output text lives in `item.completed` events** where `item.type == "agent_message"` (field: `item.text`), not in the terminal event. Extracting output requires a *second* scan over a *different* event type.
4. **There is no `total_cost_usd` field anywhere.** Codex reports tokens only. `usage` carries four fields: `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`.

Fact #4 is the consequential one for measurement: `CodexUsageStats.total_cost_usd` is **always `None`** for this backend — not "unwritable pending plumb P1-a" (Claude's situation, where the CLI *does* report a dollar figure that plumb can't yet store), but genuinely **never reported by the CLI**. Engine A/B cost comparison (TRD-v3 §2 KPIs, §13 #12) can therefore only compare Claude-vs-Codex on **tokens**, and any future dollar comparison requires atlas computing cost from token counts × a per-model price table — which does not exist and is not in v3 scope. Flagged in Pending Decision #1 (reframed) for the L2/L4 TRS author, since it changes what "cost-per-landed-PR" can mean for Codex runs.

---

## API Specifications

Not applicable in the network sense — L1 has no HTTP surface (TRD-v3 §1 scope boundary: no HTTP shell). The relevant "API" is the `codex exec` subprocess CLI contract, which is the actual specification surface for this phase:

All rows below are **verified against `codex-cli 0.144.4`** (`codex exec --help` + a real captured JSONL run), except where marked inferred.

| Dimension | Contract |
|---|---|
| Invocation | `codex exec <prompt> --json -C <workdir> --sandbox workspace-write [--model M] [--add-dir D]...` (list-form argv, no `shell=True` — same trust boundary as `ClaudeCodeBackend`, per `cli_backend.py`'s module docstring) |
| Request shape | Prompt is a single positional string argument, built by the existing `plugin_resolver.build_prompt()` — no change to prompt construction, only to how the resulting string reaches `codex`'s argv. (`codex exec` also accepts a prompt on stdin; atlas uses the positional form, matching how it dispatches every other backend.) |
| Response shape | JSONL to stdout. Observed event types: `thread.started` (carries `thread_id`), `turn.started`, `item.completed` (carries `item.{id,type,text}`; `item.type == "agent_message"` holds agent output), `turn.completed` (carries **only** `usage`). **No `result` event, no status field, no cost field.** |
| Status determination | **Exit code only.** The event stream carries no success/failure marker, so `returncode != 0` → failure; `returncode == 0` + a `turn.completed` present → success; `returncode == 0` with no `turn.completed` → `codex_no_turn_completed` (truncated stream) |
| Error handling | Non-zero exit → `("failure", stdout or stderr, "codex_nonzero_exit")`. Malformed JSON lines are skipped silently and scanning continues. Never raises. Mirrors `AntigravityBackend.parse_result`'s defensive posture; the line-by-line loop (vs. `ClaudeCodeBackend`'s single-object `json.loads`) is a genuine schema difference, not a style choice |
| Auth | `OPENAI_API_KEY` env var (checked first) or `$CODEX_HOME/auth.json`, default `~/.codex/auth.json` — **path verified present on this machine** after `codex login`. `preflight()` fails closed, no subprocess spawned, matching `AntigravityBackend.preflight()`'s exact pattern. Note `$CODEX_HOME` override: `--ignore-user-config`'s help text states *"auth still uses `CODEX_HOME`"*, so honor the env var rather than hardcoding `~/.codex` |
| Rate limiting | Not handled by `CodexBackend` — out of scope per TRD-v3 (no retry/backoff logic anywhere in the `CliBackend` Protocol; `SubprocessStageRunner` treats any backend failure as a terminal `StageOutcome`, same as Claude/agy today) |
| Flags deliberately NOT used | `--dangerously-bypass-approvals-and-sandbox` (the Codex analogue of Claude's `--dangerously-skip-permissions`, which TRD-v3 §3.6 bans), `--dangerously-bypass-hook-trust`, `--skip-git-repo-check` (atlas always dispatches inside a git repo/worktree), `--ephemeral`, `--oss`/`--local-provider`, `--output-schema` (no structured-output need in L1) |

---

## Database Design

Not applicable. L1 introduces no new persistent storage. `loop_dev.yaml` is a static file, not a database row. `CodexUsageStats`/tokens thread into plumb via the **existing** L0 path (`PlumbIO.record_span(tokens=(in, out))`) — no schema change, no new plumb call shape. Per TRD-v3 §13 item 14: "No plumb migration for v3.0–v3.2." L1 does not touch this.

---

## Algorithm & Logic Design

### `CodexBackend.build_argv`

```
function build_argv(prompt, model, add_dirs, timeout_s, extra_flags):
    primary = add_dirs[-1] if add_dirs else Path.cwd()   # worktree when isolate=true,
                                                          # else repo_root — matches
                                                          # SubprocessStageRunner's
                                                          # [repo_root] or
                                                          # [repo_root, worktree_path]
                                                          # (orchestrator.py:612-614)
    argv = ["codex", "exec", prompt, "--json",
            "-C", str(primary),
            "--sandbox", "workspace-write"]
    if model:
        argv += ["--model", model]      # -m/--model VERIFIED in 0.144.4 --help
    for d in add_dirs:
        if d != primary:
            argv += ["--add-dir", str(d)]   # keeps repo_root readable/writable
                                             # alongside the worktree
    return argv
```

**Why `-C` gets the last element and the rest become `--add-dir`:** `SubprocessStageRunner.run()` builds `add_dirs = [ctx.repo_root]`, then appends `ctx.worktree_path` if set (confirmed by reading `orchestrator.py:612-614`) — so the worktree, when present, is always last. `-C/--cd` sets the agent's *working root* (singular), and `--add-dir` supplies *"additional directories that should be writable alongside the primary workspace"* (both verbatim from `codex exec --help`, 0.144.4). That maps cleanly onto atlas's model: the worktree is where work happens; `repo_root` stays reachable so the agent can read `dev/active/<slug>/tasks.md` and other repo context, exactly as Claude's multi-`--add-dir` dispatch already allows. **This supersedes Resolved Decision #4's original "drop the extras" rule**, which was written when `--add-dir` was believed not to exist.

> **Sandbox caveat.** `--sandbox workspace-write` confines writes to the *primary workspace*. Whether directories added via `--add-dir` are also writable under that policy is stated by `--help` ("should be writable alongside the primary workspace") but **not verified by execution** in this TRS. T-L1.8's manual smoke test should confirm the agent can actually write to the worktree while `repo_root` is passed via `--add-dir`; if the sandbox proves more restrictive than the help text implies, fall back to passing only `-C <worktree>` (the original Decision #4 behavior) and record the finding.

### `CodexBackend.parse_result`

**Status is exit-code-driven — the JSONL carries no status field** (verified). Output text is assembled from `agent_message` items, which are a *different event type* from the terminal event:

```
function parse_result(stdout, stderr, returncode):
    if returncode != 0:
        return ("failure", stdout or stderr, "codex_nonzero_exit")

    events = <parse each non-empty line; skip malformed lines silently>

    saw_turn_completed = any(e.get("type") == "turn.completed" for e in events)
    if not saw_turn_completed:
        # exit 0 but no terminal event → truncated/interrupted stream
        return ("failure", stdout, "codex_no_turn_completed")

    # Agent text lives in item.completed events with item.type == agent_message.
    messages = [e["item"]["text"]
                for e in events
                if e.get("type") == "item.completed"
                and (e.get("item") or {}).get("type") == "agent_message"
                and (e.get("item") or {}).get("text")]

    return ("success", "\n".join(messages), None)
```

Design notes:
- **`codex_no_turn_completed`** replaces the assumed `codex_unparseable_output` error type: with exit-code-driven status, "stdout didn't parse" is no longer the meaningful failure — "the turn never completed despite a zero exit" is. A stream of pure garbage with exit 0 also lands here (no `turn.completed` found), so the malformed case is still covered by one branch rather than two.
- **Joining all `agent_message` texts** rather than taking the last: a multi-turn run emits several. Concatenation preserves the full narrative for the next gate / PR body. Empty result (agent produced only tool calls, no message) yields `""` with `"success"` — matching `ClaudeCodeBackend`'s `payload.get("result") or ""` behavior for the same edge.
- **No `f"codex_{status}"` branch** — there is no status field to interpolate. Failure typing comes from the exit code alone; richer Codex-side failure taxonomy (if 0.144.4 emits e.g. `turn.failed`) is not designed for here because it was not observed in the captured sample. If T-L1.8's smoke test surfaces an error-path event type, add a branch then rather than guessing its name now.

### `CodexBackend.parse_usage`

```
function parse_usage(stdout):
    turn_completed = <last event with type == "turn.completed"; None if absent>
    if turn_completed is None:
        return None
    usage = turn_completed.get("usage") or {}
    return CodexUsageStats(
        total_cost_usd=None,        # Codex reports no cost figure — VERIFIED
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cached_input_tokens=usage.get("cached_input_tokens"),
        reasoning_output_tokens=usage.get("reasoning_output_tokens"),
    )
```

**Token decomposition for plumb — verified constraint (2026-07-24).** Read plumb's source before designing this: `spans` has **one `tokens INTEGER` column**, not two (`plumb/adapters/_schema.py:47`). `Span`'s own docstring (`plumb/core/entities.py:123-127`) is explicit:

> *"The DB schema has a single `tokens` column. On write, `tokens_in + tokens_out` is summed and stored. On read, the sum is surfaced as `tokens_in`; `tokens_out` is always `None`. The in/out split is informational at the entity layer — it is not durable."*

So the real question is **not** "which two of Codex's four fields map to `(in, out)`" — the tuple is summed immediately. It is: **what single integer should represent this span?** The only interpretation that survives the collapse is **total tokens billed for the span**, so the decomposition exists purely to feed that sum correctly:

```
in  = input_tokens + cached_input_tokens          # see caveat below
out = output_tokens + reasoning_output_tokens
```

**Caveat — the subset-vs-addend question is unresolved and load-bearing.** The captured sample (`input_tokens: 16668`, `cached_input_tokens: 13056`) is consistent with *either* reading: cached-is-a-subset-of-input (add nothing) or cached-is-a-separate-addend (add both). Anthropic's convention is **addend** — Claude's `input_tokens` excludes `cache_read_input_tokens`/`cache_creation_input_tokens` — and this TRS assumes Codex follows suit, but that is an inference across vendors, not an observation. **Getting it backwards double-counts ~13k tokens on a 16k-token span** — a ~4× error on the exact metric the engine A/B comparison rests on.

**Resolution (T-L1.1):** capture a second run with a *different* cache profile (e.g. a cold-cache first run where `cached_input_tokens` should be ~0, versus a warm repeat). If `input_tokens` stays roughly constant while `cached_input_tokens` varies, they're addends; if `input_tokens` tracks the sum, cached is a subset. Until then, `parse_usage` **logs** both raw numbers at debug level so the ambiguity is diagnosable from any real run rather than requiring a bespoke experiment later.

**Cross-engine field alignment** (why a shared `UsageStats` type would have been wrong — Resolved Decision #6):

| Concept | Claude (`usage`) | Codex (`turn.completed.usage`) |
|---|---|---|
| Uncached input | `input_tokens` | — |
| Cache read | `cache_read_input_tokens` | `cached_input_tokens` |
| Cache **write** | `cache_creation_input_tokens` | *(not reported)* |
| Output | `output_tokens` | `output_tokens` |
| Reasoning | *(not reported — folded into output)* | `reasoning_output_tokens` |

Neither CLI is a superset of the other. Both collapse to one integer in plumb regardless, so the divergence costs nothing today — but it is the concrete reason the two backends keep separate `UsageStats`/`CodexUsageStats` types rather than sharing one.

### `CodexBackend.preflight`

```
function preflight():
    if OPENAI_API_KEY in environ:
        return None
    if (~/.codex/auth.json).exists():  # assumed session-marker path — Pending Decision #1
        return None
    return ("Codex (codex exec) requires OPENAI_API_KEY in the environment or a "
             "`codex login` session. See docs/3_guides/cli_backends.md.",
             "codex_missing_auth")
```

Mirrors `AntigravityBackend.preflight()`'s exact shape (env-var check, fail-closed message + typed error, no subprocess spawned) — same pattern, new backend.

### `Pipeline.run_to_completion` — status surfacing (Resolved Decision #3)

```
function run_to_completion(ctx):
    while True:
        outcome = step(ctx)
        ctx = latest_ctx or ctx
        if outcome is None:
            close_run(status="success")
            return RunResult(ctx=ctx, status="success")
        if outcome.status in ("failure", "rejected"):
            close_run(status="failure")
            return RunResult(ctx=ctx, status="failure")
        if outcome.status == "awaiting_hook":
            ... # unchanged retry logic
            if timed out:
                return RunResult(ctx=ctx, status="paused")
        # success: continue loop (unchanged)
```

Only the two `return ctx` statements and the implicit final-loop return become `return RunResult(ctx=ctx, status=...)`. No change to the loop's internal control flow, gate handling, or `awaiting_hook` retry logic.

---

## Error Handling & Edge Cases

| Case | Handling |
|---|---|
| `codex` binary not on `PATH` | `subprocess.run` raises `FileNotFoundError` — caught by `SubprocessStageRunner`'s existing exception handling (same path `AntigravityBackend` dispatch already exercises; confirmed no backend-specific handling needed beyond what exists) |
| `OPENAI_API_KEY` unset, no `auth.json` | `preflight()` returns the fail-closed tuple; `SubprocessStageRunner.run()` returns a `StageOutcome(status="failure", error_type="codex_missing_auth")` **before** any subprocess call — asserted by test (mirrors `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess`, L0/Phase-3 precedent) |
| JSONL stream has no `turn.completed` event (process killed mid-stream, truncated output) | `parse_result` returns `("failure", stdout, "codex_no_turn_completed")` — the existing `subprocess.TimeoutExpired` path in `orchestrator.py` (~line 633) already handles the timeout case upstream of `parse_result` ever being called; this branch covers the case where the process exited 0 but never finished a turn |
| Some JSONL lines are malformed, others valid | Skip-and-continue per line; never raises. Same defensive posture as `ClaudeCodeBackend.parse_result`'s single-object `try/except JSONDecodeError` — the multi-line case just needs it per-line |
| `turn.completed` present but zero `agent_message` items (agent only ran tools, produced no prose) | `("success", "", None)` — empty output text is not an error. Matches `ClaudeCodeBackend`'s `payload.get("result") or ""` handling of the same edge |
| `codex exec` exits 0 but the work actually failed (agent gave up, tests still red) | **Not detectable at the backend layer** — Codex's JSONL carries no status field, so `parse_result` reports success on any clean exit. This is precisely why `loop_dev.yaml`'s `verify` stage exists and why "guardrail signs" are `StageOutcome`-status-driven (Resolved Decision #2): quality enforcement lives in the `verify` stage + PR review, not in backend exit-code interpretation. Worth stating explicitly because it is a real behavioral difference from `ClaudeCodeBackend`, which *does* get a `subtype` it can fail on |
| `add_dirs` is empty (should not happen given `SubprocessStageRunner` always seeds `[ctx.repo_root]`, but defensive) | Falls back to `str(Path.cwd())` rather than raising — matches the defensive style of the rest of `cli_backend.py`, which never raises inside `build_argv` |
| Two `isolate: true` stages back-to-back producing two different worktree paths in `add_dirs` | Not applicable to `loop_dev.yaml` (only `code_gen` isolates) — noted as a non-issue for this specific workflow, not a general guarantee |
| `run_to_completion`'s new `RunResult` return breaks an existing caller that only expects `RunContext` | Both known call sites (`cli.py::run`, `cli.py::resume`) are updated in this same phase (T-L1.6) — grep-verified no third call site exists (`grep -rn "run_to_completion" src/`) |

**Retry strategy:** None at the `CodexBackend` level — same as `ClaudeCodeBackend`/`AntigravityBackend` today. Retry/backoff is explicitly out of scope for the `CliBackend` Protocol in all three implementations; `SubprocessStageRunner` treats every backend failure as terminal for that stage. (Self-healing retry is Phase L3, operates at the `Pipeline`/loop level via `parent_run_id`, not inside a backend.)

**Fallback:** None — `engine:codex` is opt-in (TRD-v3 Resolved Decision #4, §12 dependency table: *"if L1 auth is fiddly, ship the loop on Claude first and land Codex a beat later"*). If Codex dispatch fails, the caller (a human running the manual smoke test in L1; `loop.py` choosing per-issue engine in L2) sees a clear failure — L1 does not implement automatic engine fallback.

---

## Dependencies & Interfaces

| Dependency | Direction | Contract |
|---|---|---|
| `codex` CLI binary | `CodexBackend` → subprocess | External; `preflight()` gates on auth only, not binary presence (mirrors existing backends — none of them check `shutil.which()`) |
| `CliBackend` Protocol | `CodexBackend` implements | 3-method Protocol (`build_argv`, `parse_result`, `preflight`) — **unchanged**, per L0's own precedent of keeping `parse_usage` additive-not-Protocol (Resolved Decision reused, not re-litigated) |
| `_KNOWN_BACKENDS` / `make_backend()` | `cli_backend.py` internal | `frozenset({"claude", "agy", "codex"})`; new `if name == "codex"` branch |
| `workflow_loader.py` | `loop_dev.yaml` → loader | **No change needed** — confirmed the existing schema (`_ALLOWED_TOP_LEVEL_KEYS`, `_ALLOWED_STAGE_KEYS`, `SPAN_KINDS`) already accepts everything `loop_dev.yaml` uses |
| `resolve_workflow()` | CLI `--workflow loop_dev` → loader | **No change needed** — `loop_dev` resolves via the existing package-workflows-dir fallback (`_PACKAGE_WORKFLOWS_DIR / "loop_dev.yaml"`), same path `job.yaml`/`job_cli.yaml` use today |
| `Pipeline.run_to_completion()` | `cli.py::run`/`resume` → orchestrator | **Return type widens** `RunContext` → `RunResult` (Resolved Decision #3); both call sites updated in T-L1.6 |
| `plugin_resolver.build_prompt()` | `SubprocessStageRunner` → prompt construction | **Unchanged** — `RAW:` prefix handling already exists and is exercised by `dev.yaml`'s `tds_gen` stage; `loop_dev.yaml`'s `plan`/`code_gen` stages use the same mechanism |
| `PlumbIO.record_span(tokens=...)` | `CodexBackend.parse_usage()` → plumb | **Unchanged** — same L0-built kwarg, same `tuple[int,int] | None` shape; `CodexUsageStats` decomposes into the tuple identically to how `UsageStats` does today |

---

## Security Considerations

- **List-form subprocess argv only** — `CodexBackend.build_argv` returns a `list[str]`; no `shell=True`, no string interpolation into a shell command. Matches the module docstring's stated trust boundary and every existing backend.
- **`--sandbox workspace-write`** confines Codex's edits to the working directory passed via `-C` — the functional equivalent of the worktree boundary `ClaudeCodeBackend` relies on via `isolate: true` + `WorktreeManager`. This is Codex's own sandboxing, not atlas's; atlas's worktree isolation (`git worktree add`) is the primary boundary, `--sandbox` is defense-in-depth (matches TRD-v3 §3.6: *"the worktree is a directory boundary, not a filesystem sandbox"* — same caveat applies here: `--sandbox workspace-write` is Codex's own confinement, not a substitute for the worktree).
- **No `--dangerously-skip-permissions`-equivalent** for Codex in this design — TRD-v3 §3.6 names this explicitly as a hard "never" for Claude; `CodexBackend` has no analogous bypass flag in its `build_argv` at all, so there's nothing to accidentally enable.
- **Auth never logged.** `preflight()`'s failure message names the *env var*, never its value. No `OPENAI_API_KEY` value appears in any log line, error message, or plumb span — matches `AntigravityBackend.preflight()`'s existing pattern exactly.
- **`RAW:` prompt trust boundary** (from `plugin_resolver.py`'s and `workflow_loader.py`'s own docstrings): `loop_dev.yaml`'s `RAW:`-prefixed tool strings are trusted input — the workflow file itself, not runtime user input. This is unchanged by L1; flagged here only because `loop_dev.yaml` is the first workflow to use `RAW:` for its *primary* work stages (not just a fallback path) rather than a single stage as in `dev.yaml`. The **issue-body-as-prompt** injection risk TRD-v3 §4 Security flags is an **L2 concern** (that's when GitHub issue bodies first enter a prompt) — L1's manual smoke test supplies the task description directly, same trust level as any `atlas run "<task>"` invocation today.

---

## Testing Strategy

### Unit tests (`tests/unit/test_cli_backend.py`)

| Test | Asserts |
|---|---|
| `test_codex_backend_build_argv_shape` | Exact argv list: `["codex", "exec", <prompt>, "--json", "-C", <dir>, "--sandbox", "workspace-write", "--model", <model>]` |
| `test_codex_backend_build_argv_uses_worktree_as_primary` | `add_dirs=[repo_root, worktree]` → `-C` value is `worktree`; `repo_root` appears as `--add-dir` (Resolved Decision #4, as revised) |
| `test_codex_backend_build_argv_single_dir_no_add_dir` | `add_dirs=[repo_root]` (no isolate) → `-C` is `repo_root` and **no** `--add-dir` flag is emitted |
| `test_codex_backend_build_argv_never_bypasses_sandbox` | Asserts `--dangerously-bypass-approvals-and-sandbox` and `--dangerously-bypass-hook-trust` never appear in argv for any input — the argv-level analogue of TRD-v3 §3.6's ban on `--dangerously-skip-permissions` |
| `test_codex_backend_parse_result_success` | Fixture `success.jsonl` (the real captured sample) → `("success", "hi", None)` |
| `test_codex_backend_parse_result_joins_multiple_agent_messages` | Two `agent_message` items → both texts present in output, newline-joined |
| `test_codex_backend_parse_result_nonzero_exit` | `returncode=1` → `("failure", ..., "codex_nonzero_exit")`, JSONL body ignored |
| `test_codex_backend_parse_result_no_turn_completed` | Fixture `truncated.jsonl` (events but no `turn.completed`) → `("failure", stdout, "codex_no_turn_completed")`, never raises |
| `test_codex_backend_parse_result_malformed_stream` | Fixture `malformed.txt` (pure garbage) → `("failure", stdout, "codex_no_turn_completed")`, never raises |
| `test_codex_backend_parse_result_skips_bad_lines` | One malformed line + a valid `turn.completed` + an `agent_message` → still parses correctly |
| `test_codex_backend_parse_result_tool_only_run_is_success` | `turn.completed` present, zero `agent_message` items → `("success", "", None)` |
| `test_codex_backend_parse_usage_success` | Fixture `success.jsonl` → `CodexUsageStats(total_cost_usd=None, input_tokens=16668, output_tokens=5, cached_input_tokens=13056, reasoning_output_tokens=0)` |
| `test_codex_backend_parse_usage_cost_is_always_none` | Explicit assertion that `total_cost_usd is None` even on a fully successful run — pins the "Codex reports no cost" fact so a future refactor can't silently invent one |
| `test_codex_backend_parse_usage_no_turn_completed` | `malformed.txt` → `None`, no exception |
| `test_codex_backend_usage_to_plumb_tokens` | The reduction rule: `in == input_tokens + cached_input_tokens`, `out == output_tokens + reasoning_output_tokens` — i.e. the summed `spans.tokens` value equals **total tokens billed** (Pending Decision #4 — if the subset/addend call flips, this test is the single place it changes) |
| `test_codex_backend_preflight_missing_auth` | No `OPENAI_API_KEY`, no auth file (monkeypatched `CODEX_HOME` → empty tmp dir) → fail-closed tuple, **and asserts no subprocess spawned** (mock `subprocess.run`, assert `not called`) — the load-bearing security test, mirrors `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` |
| `test_codex_backend_preflight_env_var_present` | `OPENAI_API_KEY` set → `None` (passes) |
| `test_codex_backend_preflight_auth_file_present` | No env var but `$CODEX_HOME/auth.json` exists → `None` (passes) — covers the `codex login` session path |
| `test_make_backend_codex` | `make_backend("codex")` returns a `CodexBackend` instance |
| `test_known_backends_includes_codex` | `_KNOWN_BACKENDS == frozenset({"claude", "agy", "codex"})` |

### Unit tests (`tests/unit/test_workflow_loader.py`)

| Test | Asserts |
|---|---|
| `test_load_loop_dev_workflow` | `resolve_workflow(workflow_name="loop_dev", ...)` loads successfully; `name == "loop_dev"`; 3 stages; all `gate_label is None`; `code_gen.isolate is True`; `plan.isolate is False`; `verify.isolate is False` |
| `test_loop_dev_default_backend_is_claude` | `loaded.default_backend == "claude"` |
| `test_loop_dev_tool_strings_are_raw_or_slash` | `plan.tool.startswith("RAW:")`, `code_gen.tool.startswith("RAW:")`, `verify.tool == "/verify"` |

### Integration tests (`tests/integration/test_cli_backend_dispatch.py`)

| Test | Asserts |
|---|---|
| `test_codex_dispatch_end_to_end_mocked` | Full `SubprocessStageRunner.run()` path with `subprocess.run` mocked to return a captured `success.jsonl` fixture as stdout; asserts the resulting `StageOutcome.status == "success"` and a span is recorded with `tokens` populated |
| `test_run_to_completion_returns_run_result` | `Pipeline.run_to_completion()` on a stubbed all-success workflow → returns `RunResult(status="success")`; on a stubbed failing stage → `RunResult(status="failure")` |
| `test_cli_run_and_resume_updated_for_run_result` | `cli.py::run`/`resume` still function correctly against the new `RunResult` return type (regression proof for the Resolved Decision #3 change) |

### Manual smoke tests (off-CI, same pattern as L0's T-L0.8/T-L0.9)

- `atlas run "<small task>" --workflow loop_dev --backend codex` against a real `codex exec` session (if auth is available) — confirms real dispatch, captures actual JSONL output to reconcile against Pending Decision #1's assumed schema.
- `atlas run "<small task>" --workflow loop_dev --backend claude` — confirms the same workflow file dispatches correctly on the already-proven Claude backend, satisfying "runs end-to-end on both engines."

### Mocking strategy

No live `codex` or `claude` subprocess calls in CI — all `CodexBackend` tests use `unittest.mock.patch("subprocess.run")` returning captured/constructed JSONL fixtures, matching the existing `test_cli_backend.py` and `test_cli_backend_dispatch.py` patterns exactly (confirmed these files already follow this style for `AntigravityBackend`).

### Coverage targets

- `CodexBackend`: 85%+ (matches TRD-v3 §10's stated target for this exact component).
- `cli_backend.py` overall: no regression below L0's exit state (96% repo-wide per STATUS.md).
- `orchestrator.py`'s changed lines (`run_to_completion`'s three return statements + `RunResult`): 100% — small, security/correctness-adjacent surface.

---

## Performance Considerations

No new latency-sensitive path. `CodexBackend` dispatch is a single subprocess call per stage, same cost profile as `ClaudeCodeBackend`/`AntigravityBackend`. `--sandbox workspace-write` may add Codex-internal overhead, but that's outside atlas's measurement boundary (span `start_ts`/`end_ts` already captures whatever the subprocess takes, same as today). No caching, no batching — none is needed at this scale (one stage dispatch at a time, sequential, matching TRD-v3 §5's `concurrency=1` constraint that persists through L1).

---

## Tasks

* **T-L1.1 — Capture a write-heavy Codex run + pin the CLI version** [Effort: S]
  - **Description**: The read-only schema is already **verified** (`codex-cli 0.144.4`, sample captured 2026-07-24 — see Data Structures). What that sample does *not* cover is a run that actually **edits files and runs commands** under `--sandbox workspace-write`, which is the only mode `loop_dev`'s `code_gen` stage will ever use. Run a small real task in a scratch git repo (e.g. "create hello.py and run it") with the exact argv this TRS specifies, and capture: (a) the `item.completed` event types emitted for file edits / command execution, (b) whether any failure-path event type exists (`turn.failed`?) by deliberately failing a run, (c) whether `--add-dir` directories are genuinely writable under `workspace-write` (Pending Decision #3), (d) `reasoning_output_tokens` on a reasoning-capable model (Pending Decision #4). Record the installed version so a future drift is detectable.
  - **Acceptance Criteria**:
    - [ ] A write-heavy JSONL capture saved as a fixture; new `item.type` values (beyond `agent_message`) recorded in context.md
    - [ ] A deliberately-failed run captured; if a failure event type exists, `parse_result` gains a branch for it in T-L1.3; if failure is exit-code-only, that's recorded as confirmed rather than assumed
    - [ ] Pending Decision #3 (`--add-dir` writability) resolved yes/no with evidence
    - [ ] **Pending Decision #4 (cached-tokens subset vs addend) settled with a cold-cache/warm-cache capture pair** — run the same prompt twice in a fresh scratch repo and compare: `input_tokens` roughly constant while `cached_input_tokens` grows ⇒ addends (current assumption holds); `input_tokens` tracking the sum ⇒ subset (flip the rule in the one place it lives)
    - [ ] A reasoning-capable model run captured so `reasoning_output_tokens > 0` is observed at least once (the read-only sample had `0`, leaving the additive output rule untested)
    - [ ] `codex --version` recorded in context.md + `headless-clis-reference.md` so schema claims are version-pinned
  - **Files to Create/Modify**:
    - `tests/fixtures/codex_jsonl/` - additional captured fixtures
    - `dev/active/loop-mode-phase-L1/loop-mode-phase-L1-context.md` - findings
  - **Dependencies**: none
  - **Testing Requirements**: N/A (capture task; output feeds T-L1.3's fixtures)

* **T-L1.2 — `CodexBackend.build_argv` + `preflight`** [Effort: M]
  - **Description**: Implement `CodexBackend` class in `cli_backend.py` with `build_argv` (per Algorithm & Logic Design: `-C` = last add_dir, `--add-dir` for the rest, `--model`, `--sandbox workspace-write`) and `preflight` (`OPENAI_API_KEY` or `$CODEX_HOME/auth.json`, fail-closed). Register in `_KNOWN_BACKENDS` and `make_backend()`.
  - **Acceptance Criteria**:
    - [ ] `build_argv` produces the exact argv shape from Algorithm & Logic Design; `-C` gets the last `add_dirs` element, others become `--add-dir`
    - [ ] No bypass/dangerous flag ever appears in argv (asserted by test)
    - [ ] `preflight()` fails closed with `codex_missing_auth` when neither env var nor auth file is present, and **no subprocess is spawned**; honors `$CODEX_HOME` rather than hardcoding `~/.codex`
    - [ ] `make_backend("codex")` returns a `CodexBackend`; `_KNOWN_BACKENDS` includes `"codex"`
    - [ ] `mypy --strict src` passes on the new code
  - **Files to Create/Modify**:
    - `src/atlas/cli_backend.py` - add `CodexBackend`, extend `_KNOWN_BACKENDS`/`make_backend()`
  - **Dependencies**: none (schema already verified; T-L1.1 only refines failure/write-path details that land in T-L1.3)
  - **Testing Requirements**: Unit (`test_codex_backend_build_argv_*`, `test_codex_backend_preflight_*`, `test_make_backend_codex`, `test_known_backends_includes_codex`)

* **T-L1.3 — `CodexBackend.parse_result` + `parse_usage` + `CodexUsageStats`** [Effort: M]
  - **Description**: Implement the two-pass JSONL parse per Algorithm & Logic Design: exit-code-driven status, `turn.completed` presence check, output text joined from `item.completed`/`agent_message` events, `usage` extracted from the terminal event. Never raises; skips malformed lines. Add `CodexUsageStats` (five fields incl. `cached_input_tokens`/`reasoning_output_tokens`) and the documented `(in, out)` decomposition for plumb.
  - **Acceptance Criteria**:
    - [ ] `parse_result` on the real captured `success.jsonl` returns `("success", "hi", None)`
    - [ ] `parse_result` on `returncode != 0` returns `codex_nonzero_exit` without inspecting stdout
    - [ ] `parse_result` with no `turn.completed` returns `codex_no_turn_completed`, never raises
    - [ ] `parse_result` joins multiple `agent_message` texts; a tool-only run yields `("success", "", None)`
    - [ ] `parse_usage` returns all five fields with `total_cost_usd is None` (pinned by its own test); `None` when no terminal event
    - [ ] The `(in, out)` decomposition matches Pending Decision #4's rule and lives in exactly one place
    - [ ] If T-L1.1 found a failure-path event type, a branch for it exists; if not, exit-code-only failure is recorded as *confirmed*
  - **Files to Create/Modify**:
    - `src/atlas/cli_backend.py` - `parse_result`, `parse_usage`, `CodexUsageStats`
    - `tests/fixtures/codex_jsonl/success.jsonl` - the real captured sample, verbatim
    - `tests/fixtures/codex_jsonl/multi_message.jsonl`, `truncated.jsonl`, `malformed.txt` - new fixtures
  - **Dependencies**: T-L1.1, T-L1.2
  - **Testing Requirements**: Unit (`test_codex_backend_parse_result_*`, `test_codex_backend_parse_usage_*`, `test_codex_backend_usage_to_plumb_tokens`)

* **T-L1.4 — `loop_dev.yaml`** [Effort: S]
  - **Description**: Author the packaged workflow file per the Data Structures section above (`plan → code_gen[isolate] → verify`, no gates, `span_kind: llm` for `code_gen`).
  - **Acceptance Criteria**:
    - [ ] File loads via `resolve_workflow(workflow_name="loop_dev", ...)` with no loader changes required
    - [ ] All 3 stages have `gate_label is None`
    - [ ] `code_gen.isolate is True`; `plan.isolate is False`; `verify.isolate is False`
    - [ ] `default_backend == "claude"`
  - **Files to Create/Modify**:
    - `src/atlas/workflows/loop_dev.yaml` - new workflow
  - **Dependencies**: none (independent of CodexBackend work)
  - **Testing Requirements**: Unit (`test_load_loop_dev_workflow`, `test_loop_dev_default_backend_is_claude`, `test_loop_dev_tool_strings_are_raw_or_slash`)

* **T-L1.5 — Codex section in `headless-clis-reference.md`** [Effort: S]
  - **Description**: Append a new "Part E — Codex CLI headless reference (`codex exec`)" section following the existing Part B (Claude)/Part C (Antigravity) structure and depth: core flags, `--json` output schema, auth, exit codes, example commands. Populate with whatever was actually confirmed in T-L1.1; explicitly mark any unconfirmed field as "assumed, unverified" rather than presenting it as fact.
  - **Acceptance Criteria**:
    - [ ] New Part E section exists, matching the doc's existing per-CLI structure (flags table, output schema, auth, exit codes, examples)
    - [ ] Every schema claim not confirmed by T-L1.1 is explicitly flagged as unverified in the doc text itself (not just in this TRS)
    - [ ] Part D's comparison table (Claude vs Antigravity) gains a Codex column, or a new Part F comparison is added — author's judgment on which reads better, documented in context.md
  - **Files to Create/Modify**:
    - `docs/1_product_and_research/headless-clis-reference.md` - new Part E (+ Part D/F table update)
  - **Dependencies**: T-L1.1
  - **Testing Requirements**: none (documentation)

* **T-L1.6 — `Pipeline.run_to_completion()` status surfacing** [Effort: M]
  - **Description**: Widen `run_to_completion`'s return type from `RunContext` to a new `RunResult(ctx, status)` per Resolved Decision #3. Update the two existing call sites (`cli.py::run`, `cli.py::resume`) to use `.ctx` where they need the context and ignore or use `.status` as appropriate. Grep-confirm no other call site exists before starting.
  - **Acceptance Criteria**:
    - [ ] `RunResult` dataclass added to `orchestrator.py`; `run_to_completion` returns it in all three exit paths (`success`, `failure`/`rejected`, `paused`/awaiting_hook timeout)
    - [ ] `cli.py::run` and `cli.py::resume` updated and behave identically from the CLI user's perspective (no output/exit-code change — this is an internal API widening, not a CLI behavior change)
    - [ ] `grep -rn "run_to_completion" src/` confirms exactly these call sites were updated, none missed
    - [ ] Full existing test suite (pre-L1 baseline) still passes — this is a signature change, so every direct caller in tests must be checked too
  - **Files to Create/Modify**:
    - `src/atlas/orchestrator.py` - `RunResult`, `run_to_completion` return-path changes
    - `src/atlas/cli.py` - update the two call sites
    - Any test file directly calling `run_to_completion` and asserting on its return value (grep first; update accordingly)
  - **Dependencies**: none (independent of CodexBackend/loop_dev work, but needed before T-L1.7's integration test)
  - **Testing Requirements**: Unit + Integration (`test_run_to_completion_returns_run_result`, `test_cli_run_and_resume_updated_for_run_result`)

* **T-L1.7 — Integration tests: Codex dispatch end-to-end (mocked)** [Effort: M]
  - **Description**: Full `SubprocessStageRunner.run()` path exercised with `subprocess.run` mocked to return the `success.jsonl` fixture; asserts the complete chain from `StageSpec` → `CodexBackend` → `StageOutcome` → plumb span with `tokens` populated.
  - **Acceptance Criteria**:
    - [ ] `test_codex_dispatch_end_to_end_mocked` passes: `StageOutcome.status == "success"`, span recorded with the fixture's token counts
    - [ ] A parallel negative test confirms a failure-status fixture produces `StageOutcome.status == "failure"` with the right `error_type`, and that no PR-adjacent code path is exercised (there is none yet in L1 — this just confirms the failure surfaces cleanly)
  - **Files to Create/Modify**:
    - `tests/integration/test_cli_backend_dispatch.py` - new Codex dispatch tests
  - **Dependencies**: T-L1.2, T-L1.3, T-L1.6
  - **Testing Requirements**: Integration

* **T-L1.8 — Manual smoke test: both engines on `loop_dev`** [Effort: S]
  - **Description**: Off-CI, real external systems, same posture as L0's T-L0.8/T-L0.9. Run `atlas run "<small real task>" --workflow loop_dev --backend claude` and, if Codex auth is available, `--backend codex`. Confirm real subprocess dispatch, a real plumb run with 3 spans (`plan`, `code_gen`, `verify`), and (for the Codex leg, if run) that the live JSONL matches or updates T-L1.1's captured schema.
  - **Acceptance Criteria**:
    - [ ] `--backend claude` leg: real run completes, plumb run has exactly 3 spans matching `loop_dev`'s 3 stages, no gate prompts appear (ungated)
    - [ ] `--backend codex` leg: attempted; if auth unavailable, explicitly recorded as skipped (not silently omitted) with the reason
    - [ ] Any live-run findings that update Pending Decision #1 are folded back into `headless-clis-reference.md` (T-L1.5) and this TRS's context.md
  - **Files to Create/Modify**: none (manual verification; may produce follow-up edits to T-L1.5's doc output)
  - **Dependencies**: T-L1.2, T-L1.3, T-L1.4, T-L1.6, T-L1.7
  - **Testing Requirements**: E2E (manual, off-CI)

* **T-L1.9 — Lint/type/coverage gate** [Effort: S]
  - **Description**: `ruff check`, `ruff format --check`, `mypy --strict src`, coverage check (repo-wide no regression below L0's 96%; `CodexBackend`-specific lines ≥ 85% per TRD-v3 §10).
  - **Acceptance Criteria**:
    - [ ] `ruff check` and `ruff format --check` clean
    - [ ] `mypy --strict src` clean
    - [ ] Coverage: repo-wide ≥ prior baseline; `CodexBackend` lines ≥ 85%; `run_to_completion`'s changed lines ≥ 85%
  - **Files to Create/Modify**: none (verification task)
  - **Dependencies**: T-L1.2, T-L1.3, T-L1.4, T-L1.6, T-L1.7
  - **Testing Requirements**: N/A (CI gate)

* **T-L1.10 — Update `STATUS.md`** [Effort: S]
  - **Description**: Record L1 completion, module coverage table update (`cli_backend.py` gains Codex; note `orchestrator.py`'s `RunResult` widening), and point "Next" at Phase L2.
  - **Acceptance Criteria**:
    - [ ] `STATUS.md` reflects L1 completion with the same density/style as its L0 entry
    - [ ] "Next" section names Phase L2 as the immediate follow-up
  - **Files to Create/Modify**:
    - `STATUS.md` - phase completion entry
  - **Dependencies**: T-L1.9
  - **Testing Requirements**: N/A (documentation)

---

## Phase Deliverables

- `CodexBackend` implementing the `CliBackend` Protocol, registered and dispatchable via `--backend codex` on any workflow.
- `loop_dev.yaml` — a working, ungated, isolate-aware 3-stage workflow, loadable via `--workflow loop_dev`.
- `Pipeline.run_to_completion()` surfaces terminal run status programmatically (`RunResult`), closing the gap that would otherwise block L2's loop from knowing whether a `loop_dev` run succeeded.
- A Codex section in `headless-clis-reference.md`, populated with confirmed (or explicitly flagged as unconfirmed) schema details.
- Tests passing: unit (`CodexBackend` argv/parse/preflight, `loop_dev` loader), integration (mocked end-to-end dispatch, `RunResult` regression), manual smoke (both engines, off-CI).
- `docs/1_product_and_research/headless-clis-reference.md`, `STATUS.md` updated.
- `v3.0` fully delivered (L0 + L1 combined) per TRD-v3 §11.

---

## Pending Decisions & Clarifications

1. **~~Codex JSONL schema unverified~~ — CLOSED 2026-07-24** (verified against `codex-cli 0.144.4`). **~~Cost measurement approach~~ — DECIDED 2026-07-24 (maintainer): tokens-only comparison for now; cost synthesis can be added later.** Codex emits no dollar figure at all, so `CodexUsageStats.total_cost_usd` is permanently `None` for this backend — Claude's gap is a *storage* problem (plumb P1-a), Codex's is a *data-availability* problem. Engine A/B is therefore **tokens-only** in v3. **No price table is built in L1, L2, or v3.** If cost synthesis is ever added, the durable shape is: prices in a **dated config file** (`as_of` field), cost computed at **query time, not write time** (so a corrected table retroactively fixes history), and every derived figure labeled *estimated* — never stored in the same column as a CLI-reported figure. Recorded so the L2/L4 author inherits the constraint and the "don't store computed dollars" guardrail rather than rediscovering both.
2. **~~`span_kind: llm` vs `subagent`~~ — DECIDED 2026-07-24 (maintainer): `llm`.** Span kinds describe *what the span is* (a raw-prompt LLM dispatch), not *what role it plays* — role is already carried by the stage name, which is `code_gen` in both `dev.yaml` and `loop_dev.yaml`. A future "all code-writing stages" query should filter on `name == "code_gen"` (exact) rather than `span_kind == "subagent"` (a proxy that drifts the moment another workflow dispatches code-gen differently). Binding on T-L1.4; no further input needed.
3. **`--add-dir` writability under `--sandbox workspace-write` is documented but unexecuted.** `codex exec --help` says `--add-dir` supplies *"additional directories that should be writable alongside the primary workspace"*, so the design passes `repo_root` that way while `-C` points at the worktree. Whether the sandbox honors this in practice is unverified. **Resolution:** T-L1.8's smoke test confirms it; if the sandbox is stricter than the help text, fall back to `-C <worktree>` only. Low-risk either way (the fallback is one line), but a silent failure here would look like "the agent can't read tasks.md," which is confusing to debug — hence the explicit flag.
4. **Are Codex's `cached_input_tokens` an addend or a subset of `input_tokens`? — OPEN, and the only remaining item that can silently corrupt a metric.** *(Reframed 2026-07-24 after reading plumb's source: the original framing — "which two of four fields become `(in, out)`" — was wrong, because plumb sums the tuple into a **single `tokens` column** (`adapters/_schema.py:47`; `core/entities.py:123-127` states the in/out split "is not durable"). The real question is what single integer represents the span, and the only stable answer is **total tokens billed**.)* That makes the subset-vs-addend question decisive: the sample (`input_tokens: 16668`, `cached_input_tokens: 13056`) fits both readings, and **choosing wrong double-counts ~13k on a 16k-token span (~4× error)**. Anthropic's convention is *addend* (Claude's `input_tokens` excludes both cache fields), and this TRS assumes Codex matches — a cross-vendor inference, not an observation. **Options:** (a) **(recommended)** ship the addend rule, have `parse_usage` log both raw numbers at debug level so any real run is diagnosable, and settle it in T-L1.1 by capturing a cold-cache vs warm-cache pair — if `input_tokens` holds steady while `cached_input_tokens` varies, they're addends; if `input_tokens` tracks the sum, cached is a subset; (b) ship the subset rule (under-counts rather than over-counts if wrong — arguably the safer error direction for a budget cap, though L1 has no cap consumer). Not blocking T-L1.2; blocking the *interpretation* of any Codex token number.

---

## Resolved Decisions (made during this TRS's authoring)

| # | Decision | Rationale |
|---|---|---|
| 1 | **Codex schema: VERIFIED against 0.144.4, design written against real output.** (Originally "design against best-known shape, verify in T-L1.1" — superseded 2026-07-24 when the maintainer supplied a real captured stream and `codex --help` was read directly.) | The verification found the assumed schema wrong in four material ways (no `result` event, no status field, no cost field, output text in a different event type), which would have produced a `CodexBackend` that failed on every real run. This is the concrete payoff of L0's "don't design against unverified APIs" lesson — applied *before* implementation rather than discovered during it. |
| 7 | **TRD-v3 §3.3's `CodexBackend` contract table is factually wrong and this TRS supersedes it.** | §3.3 states *"Output format: `--json` → JSONL event stream; final `result` event carries status + stats"* and *"Failure signal: exit codes + `result` event subtype."* Verified: there is no `result` event and no subtype; the terminal event is `turn.completed` and carries only `usage`. The TRD was written pre-verification (it says as much: Codex is *"never exercised in this stack before"*). Following it literally would produce a backend that parses nothing. **Action: TRD-v3 §3.3's table should be corrected upstream** — flagged for the maintainer rather than silently diverged from, matching how L0 handled its own §13 #1 amendment (the TRD was edited, not just contradicted in the TRS). |
| 2 | **"Guardrail signs" = `StageOutcome.status` / `RunResult.status` propagation; no new type.** | User-selected option. Reuses the existing, already-tested `StageOutcome` contract. A gate-free workflow's quality enforcement is just "did every stage report success" — introducing a parallel "guardrail" concept would duplicate what `status` already means, which is exactly the kind of scope growth TRD-v3 §12's "loop mode becomes a framework" risk warns against. |
| 3 | **`Pipeline.run_to_completion()` return type widens to `RunResult`, despite Appendix A listing `orchestrator.py`/`Pipeline` as "Unchanged" across all v3 phases.** | Appendix A's own text supplies the escape hatch: *"If implementation finds `Pipeline` genuinely needs editing, that is a signal the design has drifted from this TRD — pause and reconcile."* This TRS treats that literally — reconciling here, in writing, rather than silently deviating during implementation. The need is real and narrow: `cli.py::run` today calls `run_to_completion` and **discards the return value** (confirmed by reading `cli.py:191,238` — no assignment), because no caller has ever needed to know pass/fail programmatically; human gate prompts made status visually obvious. L1's manual smoke test (T-L1.8) and every future automated caller (`loop.py` in L2) *do* need this — "guardrail signs" (Decision #2) is meaningless without it. The change is additive at the field level (`RunResult.ctx` preserves every existing `RunContext` access pattern) and touches exactly the three `return` statements already present in the method — no new control flow, no new stage-handling logic, nothing that resembles the scheduler/DAG/framework drift Appendix A's note is actually guarding against. This is judged in-scope for L1 rather than deferred to L2, because L2's `loop.py` needs a `Pipeline` that already reports status — building `loop.py` against a `Pipeline` that still silently swallows its own outcome would mean L2 re-opens `orchestrator.py` anyway, just later and under more time pressure. If the user disagrees with this scoping call, the alternative is to defer this exact change to be the *first* task of the L2 TRS instead — flagged here for visibility since it's the one place this TRS extends past TRD-v3 Appendix A's literal "Unchanged" row. |
| 4 | **`-C` gets the *last* `add_dirs` element (the worktree when present); every other element is passed via `--add-dir`.** *(Revised 2026-07-24 — the original decision dropped the extra directories because `--add-dir` was believed not to exist on `codex exec`. It does.)* | `SubprocessStageRunner` seeds `add_dirs` as `[repo_root]` or `[repo_root, worktree_path]` (`orchestrator.py:612-614`) — worktree is always last when present. `-C/--cd` sets the working root; `--add-dir` adds writable directories alongside it (both verbatim from `--help`). Keeping `repo_root` reachable matters concretely: the agent reads `dev/active/<slug>/tasks.md` from there, and Claude's dispatch already passes both. Dropping it would have made Codex runs silently context-poorer than Claude runs — a difference that would corrupt the engine A/B comparison rather than just inconvenience the agent. |
| 8 | **Status is derived from the exit code alone; `parse_result` does not attempt to infer failure from event content.** | Verified: the stream carries no status field. Inventing one (e.g. treating a missing `agent_message` as failure) would encode a guess as a contract. The honest consequence — a Codex run that exits 0 while having accomplished nothing reports `success` — is stated plainly in the Error Handling table and is exactly what `loop_dev`'s `verify` stage and the PR gate exist to catch (Resolved Decision #2). Better a known, documented gap than a fabricated signal. |
| 9 | **`CodexUsageStats` carries all four reported token fields, not just the two plumb can store.** | Plumb takes `(in, out)`; Codex reports four numbers. Discarding `cached_input_tokens`/`reasoning_output_tokens` at parse time would make the decomposition rule (Pending Decision #4) unrevisable without re-running dispatch. Keeping them on the dataclass costs nothing, keeps the decomposition a *single documented function* of parsed data, and leaves the door open for plumb v1.1's richer token schema without a re-parse. |
| 5 | **`loop_dev.yaml` stays strictly one-shot-lane — no planned-lane scaffolding.** | User-selected option, matching TRD-v3 §14 L1's engineering scope summary verbatim (lists only `CodexBackend` + `loop_dev.yaml` + docs + tests) and its exit criteria (§13 item 3 only). The planned lane's PR-authoring flow structurally depends on `queue_gh.py` and `loop.py` (both L2) — a placeholder file now would be untested dead code, which is precisely the "loop mode becomes a framework" risk TRD-v3 §12's Risks table names as High/Medium. |
| 6 | **`CodexUsageStats` is a distinct dataclass, not a shared base with `UsageStats`.** | The two backends' JSONL schemas are independently sourced (Claude's is confirmed via `headless-clis-reference.md` Part B; Codex's is assumed pending T-L1.1). Introducing a shared base type now would be designing for a commonality that hasn't been confirmed to exist — if a third backend later shows the same three fields aren't universal, unwinding a premature shared type costs more than two small dataclasses cost today. Both decompose into the same `PlumbIO.record_span(tokens=(in,out))` call shape regardless, so there's no duplication at the point that matters. |

---

## What this TRS deliberately does NOT cover

- **`loop.py`, `queue_gh.py`, `atlas loop` CLI, `[loop]` config, budgets/breaker, `reconcile_orphans`.** All explicitly Phase L2 (TRD-v3 §14).
- **Any automatic caller of `Deliverer`.** L0 built the primitive; L1 does not wire it to `loop_dev` or to anything automatic — `Deliverer.deliver()` remains callable only from a manual test harness until L2's `loop.py` exists. `loop_dev.yaml`'s `verify` stage is the last stage in L1's world; nothing opens a PR automatically yet.
- **The planned lane, `dev-docs-be`-as-a-loop-stage, plan-only PRs.** Phase L2 (TRD-v3 §3.2, §14). See Resolved Decision #5.
- **Triage classification (`wf:quick`/`wf:planned` label routing, haiku classify fallback).** Phase L2 (TRD-v3 §3.2, §14) — there's no queue to read labels from yet.
- **Self-healing, judge scoring, diagnosis-injected retries, router v1.** Phase L3 (TRD-v3 §14).
- **`gh` CLI integration of any kind.** Not touched in L1 — `queue_gh.py` doesn't exist until L2, and `Deliverer`/`GhPrDeliverer` (which does call `gh`) was L0's scope, already shipped, not re-touched here.
- **Prompt-injection mitigation (`trusted_authors`).** TRD-v3 §4 Security ties this to issue bodies entering a prompt — that only happens once L2's queue reads real GitHub issues. L1's manual smoke test supplies trusted, human-typed task strings.
- **Any change to `dev.yaml`, `job.yaml`, `job_cli.yaml`, or their loader validation rules.** `loop_dev.yaml` is additive; the existing workflows and the loader's validation schema are unchanged (confirmed: no new YAML key `loop_dev.yaml` needs falls outside `_ALLOWED_STAGE_KEYS`/`_ALLOWED_TOP_LEVEL_KEYS`).
- **Model-selection flag for `codex exec`** (e.g. a hypothetical `--model` equivalent). Deliberately omitted from `build_argv` pending schema verification (T-L1.1) rather than guessing a flag name — see Algorithm & Logic Design.
