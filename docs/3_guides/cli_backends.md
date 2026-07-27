# CLI Backends

Atlas can dispatch stages to different agentic CLIs. The active backend is selected per-stage via a 5-tier resolution order, and each CLI has its own auth requirements and its own model names.

## Backend resolution order

For each stage, atlas picks the backend in this priority order (first non-null wins):

1. **Explicit run-scoped override** — `atlas run --backend <name>`, or a loop issue's `engine:<name>` label. Highest priority.
2. **Per-stage `backend:` field** in the workflow YAML.
3. **Workflow-level `default_backend:`** field in the workflow YAML.
4. **`.atlas.toml` `[backend] default`** — project-wide config.
5. **Hard default: `"claude"`** — used when none of the above are set.

> **Changed 2026-07-26 — this used to be a 4-tier order with no override tier.** An override was folded into tier 4, so any workflow declaring `default_backend:` silently beat it. Every shipped loop workflow declares one (`loop_dev.yaml` says `claude`), which made two surfaces inert with no error at all: `atlas run --backend codex --workflow loop_dev` ran claude, and the loop daemon's `engine:*` label could never take effect. If you are reading an older doc that describes the 4-tier order — including TRD-v2 §3.4 — this page is the current one.
>
> One known stale string: `atlas run --backend --help` still says "a stage's own `backend:` field still wins." It does not; the override outranks it.

### Example: run-scoped override

```bash
atlas run "fix the flaky retry test" --backend codex
```

Beats everything in the YAML, for this run only. This is also how the loop applies an `engine:codex` issue label.

### Example: per-stage override

```yaml
# .atlas/workflows/my-workflow.yaml
name: my-workflow
default_backend: claude   # workflow default

stages:
  - name: research
    tool: /dev-docs-be
    gate_label: Gate — research
    # no backend: field → inherits workflow default_backend ("claude")

  - name: draft
    tool: RAW:Write a first draft.
    gate_label: Gate — draft
    backend: agy            # overrides workflow default for this stage only
```

### Example: project-wide default via `.atlas.toml`

```toml
# .atlas.toml
[backend]
default = "agy"   # all stages default to agy unless overridden
```

---

## Model selection is per-engine

**Model names are not portable between engines.** `Config.model` is one global string defaulting to `"haiku"` — a Claude name — and handing it to another engine is a hard failure, not a degraded default: `codex exec --model haiku` returns HTTP 400 (*"The 'haiku' model is not supported when using Codex with a ChatGPT account"*). Before this was fixed on 2026-07-26, every `--backend codex` run died in the plan stage with an opaque `codex_nonzero_exit`.

Set per-engine names in `.atlas.toml`:

```toml
[backend.models]
codex = "gpt-5.1-codex"
```

Resolution (`cli_backend.resolve_model()`):

1. An explicit `[backend.models]` entry for this engine, if configured.
2. `Config.model` for `claude` — preserves existing behavior and the byte-identical attended argv.
3. `""` for every other engine, which each backend reads as "use your own default": `CodexBackend` omits `--model` entirely, `AntigravityBackend` substitutes its `default_model`.

There is deliberately **no hardcoded cross-engine mapping table** — model lineups change faster than atlas releases, and guessing a wrong name just reproduces the same 400 with a different string. An unconfigured engine gets its CLI's own current default, which is always valid.

---

## Telemetry (`--telemetry`)

By default a backend is dispatched in its human-readable mode and atlas records no token or cost data for the run. Opt in per run:

```bash
atlas run "<task>" --telemetry
```

This requests the backend's JSON envelope, from which atlas records per-span tokens (plus the raw per-engine breakdown into `spans.attributes`) and, for `claude`, run-level `dollar_cost`. The loop always requests it.

It is **off by default on purpose**, for two independent reasons:

- It changes the dispatched argv. Without the flag, an attended `atlas run` is byte-identical to pre-loop-mode — which a regression test asserts.
- It is **deliberately separable from the permission mode.** Requesting measurement must never be a back door to `acceptEdits` or a wider tool allowlist, so a measured attended run has exactly the permissions an unmeasured one does.

Two facts worth knowing if you parse the output yourself:

- `claude -p --output-format json` emits a **JSON array** of stream events terminated by a `type: "result"` element — *not* the single object `--help` describes. Verified against Claude Code 2.1.220.
- Anthropic's token fields are **disjoint**: billed input is `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`. Reading `input_tokens` alone undercounts a warm-cache run by orders of magnitude. Codex is the opposite — `cached_input_tokens` is a **subset** of `input_tokens`.

---

## Backends

### `claude` (default)

Dispatches via `claude -p` (Claude Code CLI).

**Auth:** Set `ANTHROPIC_API_KEY` in your environment. The `claude` subprocess validates the key itself and returns a non-zero exit code on auth failure (surfaced as `error_type="plugin_nonzero_exit"`).

**argv shape:** `claude -p <prompt> --no-session-persistence --model <model> --add-dir <dir> ...`

**Notes:**
- `--bare` is intentionally NOT used — it would skip DEV-ESSENTIALS plugin discovery that the dev pipeline depends on.
- Output is plain text by default; `--telemetry` (or a loop run) switches it to the JSON envelope described above. `output_text` at the gate is the raw stdout either way.

---

### `codex` — OpenAI Codex CLI

Dispatches via `codex exec` (`codex-cli`, schema verified against 0.144.4).

**Auth:** `OPENAI_API_KEY`, or `$CODEX_HOME/auth.json` (default `~/.codex/auth.json`, written by `codex login`). `preflight()` fails closed with `codex_missing_auth` and **no subprocess spawned** if neither is present — same posture as `agy`.

**argv shape:** `codex exec <prompt> --json -C <primary-dir> --sandbox workspace-write [--model <model>] [--add-dir <other-dir>...]`

`-C` sets the single working root (the worktree, when a stage isolates); every other directory is kept writable via `--add-dir`, so `dev/active/<slug>/tasks.md` under the repo root stays reachable. `--model` is **omitted entirely** unless `[backend.models] codex` is set — see Model selection above.

**Never** `--dangerously-bypass-approvals-and-sandbox` or `--dangerously-bypass-hook-trust`. `--sandbox workspace-write` is the confinement.

**Output:** a JSONL event stream. Three things about it are unlike the other backends and unlike what the design docs originally assumed:

- **Success/failure is exit-code-only.** There is no status field anywhere in the stream.
- The terminal event is **`turn.completed`**, and it carries only `usage`. Its absence is reported loudly as `codex_no_turn_completed` rather than mis-parsed as success.
- Agent output text lives in a *different* event type — `item.completed` where `item.type == "agent_message"` — so extraction is a two-pass scan.

**Cost:** none. The Codex CLI emits no cost field at any layer, so a Codex run's run-level `dollar_cost` is `NULL`, not `0.0`. Practical consequence for loop mode: Codex runs advance `max_runs_per_day` but not `max_dollars_per_day`, so on that lane the run count is the load-bearing spend bound.

**Schema stability:** the JSONL event schema is undocumented and unversioned, so a `codex` upgrade can change it silently. Fixtures are real captures; a schema change should surface as a test failure on the next capture refresh, not as corrupted telemetry.

---

### `agy` — Antigravity (experimental)

Dispatches via `agy -p` (Antigravity CLI, wrapping Gemini models).

> **Experimental.** Antigravity's headless API-key auth is contested (issue #78 in the Antigravity repo). As of 2026-06-18, the upstream Gemini CLI was retired and `agy` is its successor. Headless auth may not work in all environments — see [headless-clis-reference.md](../1_product_and_research/headless-clis-reference.md) Part C for the latest status.

**Auth:** Set `GEMINI_API_KEY` **or** `GOOGLE_API_KEY` in your environment before running atlas. If neither variable is set, atlas will refuse to spawn the subprocess and return a clear error (`error_type="agy_missing_auth_env"`) rather than silently opening a browser OAuth flow or hanging on an SSH session.

```sh
export GEMINI_API_KEY="your-key-here"
atlas run "..." --workflow my-workflow
```

**argv shape:** `agy -p <prompt> --output-format json --model <model> --include-directories <dir> ...`

Note the flag difference vs claude: `--include-directories` (not `--add-dir`), repeated per directory.

**Output:** `agy` is invoked with `--output-format json`. Atlas parses the `response` field on success and the `error` field on failure. Exit codes 42 (input error) and 53 (turn limit exceeded) are mapped to distinct `error_type` values for actionable log messages.

**Default model:** `gemini-flash-lite` — matches Claude's `haiku` cost posture and fits the free-tier allowance (~20 req/day on flash-lite). Override it with an `[backend.models] agy = "..."` entry (see Model selection above; the note that once stood here saying model selection "is not yet configurable in `.atlas.toml`" was true until 2026-07-26).

---

## Error types reference

| `error_type` | Condition |
|---|---|
| `agy_missing_auth_env` | `GEMINI_API_KEY` and `GOOGLE_API_KEY` both unset — subprocess not spawned |
| `agy_general_error` | `agy` exited with code 1 (API failure / network error) |
| `agy_input_error` | `agy` exited with code 42 (bad input / prompt too long) |
| `agy_turn_limit` | `agy` exited with code 53 (turn limit exceeded) |
| `agy_unparseable_output` | `agy` exited 0 but stdout is not valid JSON |
| `agy_response_error` | `agy` exited 0 but the JSON envelope contains a non-empty `error` field |
| `agy_response_not_string` | JSON `response` field is not a string |
| `codex_missing_auth` | Neither `OPENAI_API_KEY` nor `$CODEX_HOME/auth.json` present — subprocess not spawned |
| `codex_nonzero_exit` | `codex exec` exited non-zero (status is exit-code-only for this backend) |
| `codex_no_turn_completed` | `codex exec` exited 0 but the stream carries no terminal `turn.completed` event |
| `plugin_nonzero_exit` | `claude -p` exited non-zero (auth failure, plugin crash, etc.) |
| `claude_unparseable_json` | Telemetry requested but stdout is not valid JSON |
| `claude_no_result_event` | Well-formed JSON envelope with no terminal `result` element — a truncated or interrupted stream |
| `plugin_timeout` | Any backend's subprocess exceeded `timeout_s` |
| `unknown_backend` | Stage `backend:` field (or `--backend`) names a backend not in `{"claude", "agy", "codex"}` |

## See also

- [cli-backend-dispatch.md](../../dev/archive/yaml-workflow-engine-design-notes/cli-backend-dispatch.md) — design rationale and per-CLI flag table (archived design note; decision shipped in v2.2)
- [headless-clis-reference.md](../1_product_and_research/headless-clis-reference.md) — Part B (claude headless), Part C (agy headless, auth status), Part E (codex headless) and Part F (3-way comparison)
- [TRD-v3.md](../2_architecture/TRD-v3.md) §3.3 (engine selection, `CodexBackend`) and §3.6 (telemetry, permission profile) — current
- [TRD-v2.md](../2_architecture/TRD-v2.md) §3.4 (backend resolution), §4 (security), §5 (agy experimental status) — design-time; its §3.4 predates the override tier
