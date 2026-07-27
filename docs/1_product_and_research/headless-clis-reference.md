---
title: Headless CLIs (Claude Code & Antigravity) — feature/flag reference + content-pipeline decision
status: reference
created: 2026-06-28
last_reviewed: 2026-06-28
tags: [content-pipeline, claude-code, antigravity, gemini-cli, headless, llm-backend]
---

# Headless CLIs as programmatic LLM backends — reference & decision

Two questions, answered in one doc:

1. **Reference** — the full headless / non-interactive feature and flag surface of
   **Claude Code** (`claude -p`) and **Antigravity CLI** (`agy -p`, formerly Gemini CLI).
2. **Decision** — should `content-pipeline` drive its `LLMClientPort` through one of these
   CLIs (or the Claude/Gemini *in-Chrome* assistants) instead of local OSS models?

**Bottom line up front:** Both CLIs *can* run headlessly with full system-prompt override.
But for `content-pipeline` the right layer is the **API**, not the CLI — and you already
built that seam (`anthropic` + `openrouter` adapters behind `LLMClientPort`). The browser
assistants are interactive-only and not automatable at all. Details and the full flag
tables below.

---

## Part A — The decision (content-pipeline specific)

### TL;DR table

| Question | Answer |
| --- | --- |
| Call Claude Code / Antigravity CLI programmatically via `-p`? | **Technically yes** for both — headless, stdin piping, JSON output. |
| More powerful than local OSS models? | **Yes** — Opus/Sonnet & Gemini 3 are well above Llama/Qwen-class for classify/research/PRD. |
| Override the system prompt? | **Yes, fully.** Claude: `--system-prompt` / `--system-prompt-file`. Antigravity: `GEMINI_SYSTEM_MD`. |
| *Should* I wire a CLI in as the `complete()` backend? | **No.** Wrong layer — use the **Claude API** (`anthropic_client.py`) or **OpenRouter** (already built). |
| Claude-in-Chrome / Gemini-in-Chrome for pipeline tasks? | **No.** Interactive-only browser assistants. No API, no CLI, no headless mode. |

### The reframe

`content-pipeline` already has the correct abstraction. From
`src/application/ports/llm.py`:

```python
class LLMClientPort(Protocol):
    backend_name: str
    model_name: str
    def complete(self, system: str, user: str,
                 response_model: type[BaseModel] | None = None,
                 max_tokens: int = 1024) -> BaseModel | str: ...
```

and `src/infrastructure/config/settings.py` already switches backends:

```python
llm_backend: Literal["anthropic", "openrouter", "ollama"] = "anthropic"
llm_model: str = "claude-sonnet-4-6"
```

So the project **already defaults to a frontier cloud model** (`claude-sonnet-4-6` via
`src/infrastructure/llm/anthropic_client.py`). Ollama is just one of three adapters. The
real question is narrow: *"instead of the Anthropic SDK inside `anthropic_client.py`,
should I shell out to `claude -p` / `agy -p`?"* — and that answer is **no**.

### Why a CLI is the wrong backend layer

The CLIs are **agentic harnesses** (agent loop, tool dispatch, permission system, session
files, context management). The port wants **one synchronous structured completion**.

1. **Impedance mismatch.** `complete()` takes separate `system`/`user` and returns a
   **validated Pydantic model** (`response_model`). The API SDKs give that natively;
   shelling out means serialize → spawn subprocess → parse `--output-format json` →
   re-extract text → re-validate. Re-implementing what `anthropic_client.py` already does,
   slower and across a process boundary.
2. **Latency & throughput.** Every call = process spawn + CLI start + harness init. The
   pipeline batch-classifies (see the `classifications` list handling in
   `ollama_client.py`), so subprocess-per-item is the worst case.
3. **Error semantics.** `OllamaClient` retries `429/500/502/503/504` via `tenacity`. CLIs
   surface **exit codes** (and for Claude, `system/api_retry` stream events), not HTTP
   status codes you can branch on cleanly inside `complete()`.
4. **Licensing (Claude).** Anthropic's Agent SDK docs state: *"Unless previously approved,
   Anthropic does not allow third party developers to offer claude.ai login or rate limits
   for their products … Please use the API key authentication methods … instead."* For a
   **personal** pipeline you run yourself, using your own subscription via the CLI is
   normal use. The moment it's framed as a distributable product/portfolio artifact, the
   supported path is **API-key auth** — i.e. `anthropic_client.py`.
5. **You lose nothing.** The whole point of `-p` (tools, edits, web search, multi-turn
   agency) is irrelevant to a `complete()` call. You'd strip the CLI
   (`--tools ""`, `--max-turns 1`, `--system-prompt`) until it's a worse API client.

### What to do instead

Use the existing backend switch:

```bash
# .env — already defaulted here
LLM_BACKEND=anthropic
LLM_MODEL=claude-sonnet-4-6        # claude-opus-4-8 for the Brown-lite PRD-writer stage
ANTHROPIC_API_KEY=sk-ant-...
```

- **Most powerful, cleanly** → `anthropic` backend (built). Opus 4.8 for PRD writer,
  Sonnet 4.6 for capture/classify. Structured-output native, retry-able, license-clean.
- **Model flexibility / A-B Gemini 3 vs Claude** → `openrouter` backend
  (`openrouter_client.py`, built). One OpenAI-compatible key exposes Gemini 3, Claude,
  Llama, etc. **This is how you'd use Gemini programmatically — not via `agy`.**
- **Keep `ollama`** for offline/cost-free local dev and CI smoke tests.

If you specifically want Gemini 3 quality in the pipeline: add it through **OpenRouter**
(or the Gemini API directly). Same model, proper API-key auth, no OAuth-in-CI problem,
no free-tier request cliff, structured output your port can validate.

### Why the browser assistants don't apply

Both confirmed **interactive-only**, no programmatic surface:

- **Claude in Chrome** — extension/side-panel; reads/clicks/fills/screenshots; has
  workflow recording + scheduled tasks but **no API/CLI**. Beta on Pro/Max/Team/Enterprise
  (Pro = Haiku 4.5 only; Max = Opus/Sonnet/Haiku).
- **Gemini in Chrome** — in-browser assistant; summarize/compare-tabs/auto-browse;
  **no API/CLI/headless**. Google AI Pro/Ultra, select regions.

These are for *a human browsing*. `content-pipeline` is unattended batch automation. If a
stage ever needs a logged-in, JS-heavy, API-less site, the right tool is **Playwright via
MCP** or the existing scrapers — not a chat extension you can't script.

---

## Part B — Claude Code headless reference (`claude -p`)

Claude Code's non-interactive mode is "the Agent SDK via the CLI." Add `-p` / `--print` to
any `claude` command to run once and exit. All CLI flags work with `-p`.

### Core non-interactive flags

| Flag | What it does |
| --- | --- |
| `--print`, `-p` | Print response without interactive mode; run once and exit. |
| `--bare` | Skip auto-discovery of hooks, skills, plugins, MCP servers, auto-memory, and `CLAUDE.md`. Only explicit flags take effect. **Recommended for scripts/CI**; will become the `-p` default in a future release. In bare mode, Anthropic auth must come from `ANTHROPIC_API_KEY` or an `apiKeyHelper` (no OAuth/keychain). |
| `--output-format` | `text` (default), `json`, or `stream-json`. |
| `--input-format` | `text` or `stream-json` (stream multiple input messages in). |
| `--model` | `opus` / `sonnet` / `haiku` / `fable` alias or full model ID. Overrides `model` setting and `ANTHROPIC_MODEL`. |
| `--max-turns` | Limit agentic turns (print mode only); errors when exceeded. No limit by default. |
| `--verbose` | Full turn-by-turn output; required for some `stream-json` features. |

### System-prompt control

| Flag | Behavior |
| --- | --- |
| `--system-prompt` | **Replace the entire system prompt** with custom text. |
| `--system-prompt-file` | Replace the default prompt with file contents. |
| `--append-system-prompt` | **Append** to the default prompt (keeps Claude Code identity, tools, safety). |
| `--append-system-prompt-file` | Append file contents to the default prompt. |
| `--exclude-dynamic-system-prompt-sections` | Move per-machine sections (cwd, env, memory paths, git flag) into the first user message for better prompt-cache reuse across machines. Only with the default prompt; ignored when `--system-prompt`/`--system-prompt-file` is set. |

`--system-prompt` and `--system-prompt-file` are **mutually exclusive**; append flags can
combine with either. **Rule of thumb:** *append* when Claude should stay a coding assistant
that also follows extra rules; *replace* when the identity/surface differs (a non-coding
agent in an unattended pipeline) — replacing drops all default tool guidance and **safety
instructions**, so you own whatever the task still needs.

### Tool & permission control

| Flag | What it does |
| --- | --- |
| `--allowedTools`, `--allowed-tools` | Tools that run without a permission prompt. Supports rule syntax, e.g. `"Bash(git diff *)"` (note the space before `*` for prefix matching). |
| `--disallowedTools`, `--disallowed-tools` | Deny rules. Bare name removes a tool from context (`"Edit"`, `"*"`, `"mcp__*"`); scoped rule (`Bash(rm *)`) denies only matching calls. |
| `--tools` | Restrict built-in tools: `""` disables all, `"default"` all, or `"Bash,Edit,Read"`. Does not affect MCP tools. |
| `--permission-mode` | `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, or `bypassPermissions`. `dontAsk` = locked-down CI (denies anything not in allow rules / read-only set). `acceptEdits` = auto-approve writes + `mkdir/touch/mv/cp`. |
| `--permission-prompt-tool` | Delegate permission decisions to an MCP tool for fully unattended runs. |
| `--dangerously-skip-permissions` | Skip prompts (= `--permission-mode bypassPermissions`). |

### Context, config & MCP

| Flag | What it does |
| --- | --- |
| `--settings <file-or-json>` | Load settings (and `apiKeyHelper`) explicitly. |
| `--mcp-config <file-or-json>` | Load MCP servers. |
| `--strict-mcp-config` | Only use MCP servers from `--mcp-config` (ignore discovered ones). |
| `--add-dir <path>` | Add directories to the workspace. |
| `--agents <json>` | Define custom subagents inline. |
| `--plugin-dir <path>` / `--plugin-url <url>` | Load a plugin explicitly (needed under `--bare`). |
| `--json-schema '<schema>'` | With `--output-format json`, force output to a JSON Schema; result lands in `structured_output`. |

### Session / multi-turn

| Flag | What it does |
| --- | --- |
| `--continue` | Continue the most recent conversation (same project dir). |
| `--resume <session_id>` | Continue a specific session by ID. |
| `--session-id <id>` | Set/choose a session ID. |
| `--fork-session` | Branch a session to explore an alternative without mutating the original. |

Session lookup is scoped to the current project dir and its git worktrees — run resume from
the same directory.

### `--output-format json` result schema

`claude -p --output-format json` returns a single result object. Key fields:

| Field | Type | Notes |
| --- | --- | --- |
| `subtype` | string | `success`, `error_during_execution`, `error_max_turns`, `error_max_budget_usd`, `error_max_structured_output_retries`. |
| `result` | string \| null | Final assistant text on success; `null` on error subtypes. |
| `structured_output` | any | Populated when `--json-schema` is used. |
| `is_error` | bool | True if ended in error. |
| `session_id` | string | For `--resume`. |
| `num_turns` | int | Agentic turns completed. |
| `duration_ms` / `duration_api_ms` | int | Total / API-only duration. |
| `total_cost_usd` | float \| null | Estimated spend for the invocation (+ per-model breakdown). |
| `usage` | object | `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`. |
| `model_usage` | object \| null | Per-model usage breakdown. |
| `permission_denials`, `errors`, `api_error_status`, `uuid` | — | Diagnostics. |

### `--output-format stream-json` events

Newline-delimited JSON, one event per line. Message/event types:

- **`system` / `init`** — first event; session metadata: model, tools, MCP servers,
  loaded `plugins` (each `name`+`path`) and `plugin_errors` (each `plugin`/`type`/`message`
  — use to fail CI when a plugin didn't load).
- **`system` / `api_retry`** — emitted before a retryable API retry. Fields: `attempt`,
  `max_retries`, `retry_delay_ms`, `error_status`, `error` (category:
  `authentication_failed`, `oauth_org_not_allowed`, `billing_error`, `rate_limit`,
  `overloaded`, `invalid_request`, `model_not_found`, `server_error`,
  `max_output_tokens`, `unknown`), `uuid`, `session_id`.
- **`system` / `plugin_install`** — only when `CLAUDE_CODE_SYNC_PLUGIN_INSTALL` is set;
  `status` ∈ `started`/`installed`/`failed`/`completed`.
- **`assistant`** — Claude's response: `content` blocks, `model`, optional `usage`.
- **`user`** — user/tool-result messages during multi-turn.
- **`result`** — final outcome with aggregated stats (same fields as the json schema above).
- **`stream_event`** — token deltas when `--include-partial-messages` is set
  (filter `.event.delta.type == "text_delta"`).

Related stream flags: `--include-partial-messages`, `--include-hook-events`,
`--prompt-suggestions`, `--replay-user-messages` (all require `--output-format stream-json`
+ `--verbose`).

### Operational behaviors worth knowing

- **stdin piping**: non-interactive mode reads stdin — `cat x | claude -p "..." > out.txt`.
  Piped stdin is **capped at 10 MB** (v2.1.128+); larger inputs → write to a file and
  reference the path.
- **Background tasks at exit**: a background Bash task is killed ~5 s after the final result
  + stdin close. Background subagents/workflows are waited on (capped 10 min by default;
  tune with `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`, `0` = no cap).
- **Skills/commands in `-p`**: include `/skill-name` in the prompt; it expands before
  running. Interactive-dialog commands (`/login`) are unavailable. `/config key=value`
  works to set a setting inline (e.g. `/config thinking=false`).

### Auth & key env vars

- `ANTHROPIC_API_KEY` — primary (required under `--bare`).
- Provider routing: `CLAUDE_CODE_USE_BEDROCK=1`, `CLAUDE_CODE_USE_VERTEX=1`,
  `CLAUDE_CODE_USE_FOUNDRY=1`, `CLAUDE_CODE_USE_ANTHROPIC_AWS=1` (+ provider creds).
- `ANTHROPIC_MODEL` — default model (overridden by `--model`).
- `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` — background-agent wait cap.
- `CLAUDE_CODE_SYNC_PLUGIN_INSTALL` — emit `plugin_install` events.

### Example commands

```bash
# One-off question
claude -p "What does the auth module do?"

# CI-safe, deterministic: bare + pre-approve Read only
claude --bare -p "Summarize this file" --allowedTools "Read"

# Pipe a build log, get the root cause to a file
cat build-error.txt | claude -p 'explain the root cause' > output.txt

# Structured output against a schema (lands in .structured_output)
claude -p "Extract main function names from auth.py" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}' \
  | jq '.structured_output'

# Replace the system prompt entirely (pure-LLM, no agent harness)
echo "$ARTICLE" | claude -p --system-prompt-file ./prompts/classify.md \
  --output-format json --tools "" --max-turns 1 "Classify this content"

# Multi-turn via session id
sid=$(claude -p "Start a review" --output-format json | jq -r '.session_id')
claude -p "Now focus on DB queries" --resume "$sid"
```

---

## Part C — Antigravity CLI headless reference (`agy -p`, formerly Gemini CLI)

> **Migration alert (as of 2026-06):** Gemini CLI is being retired and replaced by
> **Antigravity CLI**. The command is renamed `gemini` → **`agy`**. Gemini CLI stopped
> serving free/Pro/Ultra requests on **2026-06-18**. Automation that calls the old
> `gemini` binary breaks. Most published Gemini-CLI flag/env docs still apply to `agy`,
> but verify against `agy --help` because the surface is in flux.

### Core non-interactive flags

| Flag | What it does |
| --- | --- |
| `-p`, `--prompt` | Prompt text; **forces non-interactive mode**. Appended to stdin if stdin is also provided. |
| `-i`, `--prompt-interactive` | Run the prompt, then drop into interactive mode (not for CI). |
| `-m`, `--model` | Model to use (see model notes below). |
| `-o`, `--output-format` | `text`, `json`, or `stream-json` (JSONL). |
| `--approval-mode` | `default`, `auto_edit`, `yolo`, or `plan`. `yolo` auto-approves all actions. |
| `-y`, `--yolo` | *Deprecated* — use `--approval-mode=yolo`. |
| `-s`, `--sandbox` | Run tool execution in a sandbox. |
| `-e`, `--extensions` | Extensions to load (comma-separated or repeated). |
| `--allowed-mcp-server-names` | Allow specific MCP servers. |
| `--allowed-tools` | *Deprecated* — tools allowed without confirmation. |
| `--include-directories` | Add directories to the workspace. |
| `-d`, `--debug` | Verbose debug logging. |

### System-prompt override

| Mechanism | Behavior |
| --- | --- |
| `GEMINI_SYSTEM_MD` | Env var enabling **full replacement** of the built-in system prompt. Values: `true`/`1` → read `./.gemini/system.md`; an absolute/relative/`~` path → that file; `false`/`0` → built-in. **Full replacement, not a merge** — none of the original safety/tool instructions apply unless you re-include them. |
| `GEMINI_WRITE_SYSTEM_MD` | Export the current default prompt before overriding: `GEMINI_WRITE_SYSTEM_MD=1 agy` (or a path). Review built-in safety rules first. |
| Variable substitution | Custom prompt files support `${AgentSkills}`, `${SubAgents}`, `${AvailableTools}`, and tool-specific patterns like `${write_file_ToolName}`. |
| Persistence | Store the var in `.gemini/.env`. Missing file → error `missing system prompt file '<path>'`. |

### `--output-format json` schema

Single JSON object:

- **`response`** (string) — the model's final answer.
- **`stats`** (object) — token usage and API latency metrics.
- **`error`** (object, optional) — present if the request failed.

### `--output-format stream-json` (JSONL) events

- **`init`** — session metadata (session ID, model).
- **`message`** — user/assistant message chunks.
- **`tool_use`** — tool call requests with arguments.
- **`tool_result`** — output from executed tools.
- **`error`** — non-fatal warnings / system errors.
- **`result`** — final outcome with aggregated stats and per-model token breakdowns.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | General error or API failure. |
| `42` | Input error (invalid prompt or arguments). |
| `53` | Turn limit exceeded. |

### Authentication — the headless gotcha

- **Antigravity default = browser OAuth (Google Sign-In).** On a desktop it auto-opens the
  browser; over SSH it prints an authorization URL to paste. This is a **real obstacle for
  headless servers/CI** — the opposite of what unattended automation wants. (Gemini CLI
  authenticated headlessly more easily; Antigravity flips this.)
- **API-key env vars (documented for Gemini CLI; status on `agy` is contested):**
  - `GEMINI_API_KEY` — Google AI Studio key (preferred for explicit intent).
  - `GOOGLE_API_KEY` — Google Cloud key. **If both are set, `GOOGLE_API_KEY` wins.**
  - `GOOGLE_GENAI_USE_VERTEXAI=true` — switch to Vertex AI; then set `GOOGLE_CLOUD_PROJECT`
    + `GOOGLE_CLOUD_LOCATION` and configure ADC (e.g. `GOOGLE_APPLICATION_CREDENTIALS`
    service account).
  - In non-interactive mode the CLI uses cached creds if present, else **exits with an
    error** if no suitable env vars are found.
  - ⚠️ There is an **open issue requesting Gemini-API-key (AI Studio) auth for headless
    environments on the Antigravity CLI** — treat headless API-key auth on `agy` as
    not-yet-guaranteed and verify before depending on it in CI.

### Free-tier quota cliff

- Gemini CLI: ~**1,000 requests/day** (daily reset).
- Antigravity free tier: reported ~**20 requests/day on flash-lite**, with a **weekly
  compute-based cap** instead of daily. Heavy/batch automation will exhaust this fast —
  another reason to use the **Gemini API / OpenRouter** for `content-pipeline` rather than
  the CLI.

### Example commands

```bash
# Headless one-shot
agy -p "Write a poem about TypeScript"

# Pipe context via stdin
cat error.log | agy -p "Explain why this failed"
git diff | agy -p "Write a commit message for these changes"

# JSON output, extract the answer
agy --output-format json -p "Summarize this" | jq -r '.response'

# Full system-prompt override
GEMINI_SYSTEM_MD=./prompts/classify.md agy -p "Classify this content" --output-format json
```

---

## Part D — Claude Code vs Antigravity headless: quick comparison

| Dimension | Claude Code (`claude -p`) | Antigravity (`agy -p`) |
| --- | --- | --- |
| Headless trigger | `-p` / `--print` | `-p` / `--prompt` (or non-TTY) |
| Output formats | `text` / `json` / `stream-json` | `text` / `json` / `stream-json` (JSONL) |
| Schema-forced output | `--json-schema` → `structured_output` | not documented (prompt for JSON) |
| System prompt: replace | `--system-prompt` / `--system-prompt-file` | `GEMINI_SYSTEM_MD` (full replace) |
| System prompt: append | `--append-system-prompt[-file]` | not native (append manually in file) |
| Tool gating | `--allowedTools` / `--disallowedTools` / `--tools` | `--allowed-tools` (deprecated) / `--allowed-mcp-server-names` |
| Permission/autonomy | `--permission-mode` (incl. `bypassPermissions`), `--permission-prompt-tool` | `--approval-mode` (`default`/`auto_edit`/`yolo`/`plan`) |
| Multi-turn / sessions | `--continue` / `--resume` / `--session-id` / `--fork-session` | not documented in headless ref |
| CI determinism | `--bare` (skip local discovery) | sandbox via `-s`; no bare-equivalent documented |
| Cost telemetry | `total_cost_usd` + per-model in JSON | `stats` (tokens + latency) |
| Headless auth | `ANTHROPIC_API_KEY` (clean, required under `--bare`) | **browser OAuth by default**; API-key headless contested |
| Free-tier batch fit | API billed per token (no request/day cliff) | ~20 req/day free + weekly cap |
| Retry signal | `system/api_retry` events w/ error categories | exit codes only |
| stdin cap | 10 MB | not documented |

**Net for `content-pipeline`:** Claude Code is the more automation-mature CLI (bare mode,
schema output, session control, clean key auth, retry events). But neither belongs *inside*
`complete()` — use the **API** for both vendors (Anthropic SDK for Claude; OpenRouter or
Gemini API for Gemini).

---

## Part E — Codex CLI headless reference (`codex exec`)

> **Verification status (updated 2026-07-26, `codex-cli 0.144.4`).** T-L1.1's write-heavy
> capture has now been run, closing the gaps this Part previously carried. Live captures are
> checked in at `tests/fixtures/codex_jsonl/write_heavy_real.jsonl` and
> `sandbox_denied_real.jsonl`. Four things changed from the 2026-07-24 draft:
>
> 1. **Write-path event types are VERIFIED** — `item.started`/`item.completed` carry
>    `item_type` values `command_execution`, `file_change`, and `agent_message`.
> 2. **No failure event type exists.** A sandbox-blocked write still exits `0` and emits
>    `turn.completed`; the agent simply narrates that it couldn't comply.
> 3. **`--add-dir` IS writable** under `--sandbox workspace-write` (Pending Decision #3).
> 4. **`cached_input_tokens` is a SUBSET of `input_tokens`, not an addend** (Pending
>    Decision #4). atlas's prior assumption was backwards and inflated every Codex span's
>    input by ~70-90%. Fixed; see the token-usage table below.

### Core non-interactive flags

**VERIFIED** — all present in `codex exec --help` (0.144.4):

| Flag | What it does |
| --- | --- |
| `exec <prompt>` | Non-interactive one-shot execution; prompt is a positional string argument (also accepts stdin, not used by atlas). |
| `--json` | Emit JSONL events to stdout instead of human-readable text. |
| `-C`, `--cd <DIR>` | Set the single working root for the run. |
| `-s`, `--sandbox <MODE>` | `read-only`, `workspace-write`, or `danger-full-access`. Confines writes to the working root passed via `-C`. |
| `--add-dir <DIR>` | Repeatable. *"Additional directories that should be writable alongside the primary workspace."* Whether the sandbox actually honors this for `workspace-write` is **UNVERIFIED** by execution — see the sandbox caveat below. |
| `-m`, `--model <MODEL>` | Model to use. |
| `--skip-git-repo-check` | Not used by atlas — atlas always dispatches inside a git repo/worktree. |
| `--ephemeral` | Not used by atlas. |
| `--output-schema <FILE>` | Force structured output against a JSON Schema. Not used by atlas in L1 (no structured-output need). |
| `-o`, `--output-last-message <FILE>` | Write the final assistant message to a file. Not used by atlas (output is read from the JSONL stream instead). |

**Bypass flags that exist and must never be used** (VERIFIED present, banned by atlas policy —
the Codex analogue of Claude's `--dangerously-skip-permissions`, TRD-v3 §3.6):
`--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`.

### `--json` output schema — JSONL event stream

**VERIFIED.** A real captured run of a trivial read-only prompt produced exactly this stream:

```jsonl
{"type":"thread.started","thread_id":"019f96b7-e404-7673-8853-2938007f2629"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hi"}}
{"type":"turn.completed","usage":{"input_tokens":16668,"cached_input_tokens":13056,"output_tokens":5,"reasoning_output_tokens":0}}
```

Observed event types:

| Event `type` | Fields | Notes |
| --- | --- | --- |
| `thread.started` | `thread_id` | First event; session identifier. |
| `turn.started` | — | No payload observed. |
| `item.completed` | `item.{id,type,text}` | Agent output lives here, **not** on the terminal event. `item.type == "agent_message"` holds prose; other `item.type` values (e.g. file-edit or command-execution items) are **UNVERIFIED** — not observed in the read-only sample, expected from a write-heavy run (T-L1.1). |
| `turn.completed` | `usage` only | The terminal event. Carries **no status field and no text** — success/failure is exit-code-only (see below). |

**This differs materially from both Claude's envelope (Part B) and the schema this doc
originally assumed for Codex.** Do not reason from Claude's `result`-event shape when working
with Codex:

| Assumed (pre-verification) | Actual (VERIFIED) |
| --- | --- |
| Terminal event `type: "result"` with `status` + `text` | Terminal event is `type: "turn.completed"`, carrying **only** `usage` |
| Failure detectable from a `result` subtype | **No status field anywhere in the stream** — failure is exit-code-only |
| Agent output text on the terminal event | Output lives in separate `item.completed` events (`item.type == "agent_message"`) |
| `total_cost_usd` present (as with Claude) | **No cost field at all** — four token fields instead |

### Status determination

**VERIFIED exit-code-only, including the failure path (2026-07-26).** The event stream
carries no status field, so:

- `returncode != 0` → failure.
- `returncode == 0` + a `turn.completed` event present → success.
- `returncode == 0` with **no** `turn.completed` (truncated/interrupted stream) → treated as
  failure by atlas (`codex_no_turn_completed`), even though the exit code was clean.
- **No distinct failure-path event type exists.** T-L1.1 deliberately triggered a sandbox
  denial (`--sandbox read-only`, asked to write a file): the write was correctly refused,
  yet the run exited `0` and emitted a normal `turn.completed`. There is no `turn.failed`,
  no error event, nothing to branch on — so `parse_result` correctly does not look for one.
- A **hard** failure (preflight, e.g. an untrusted directory) exits non-zero with **empty
  stdout** and the message on stderr — hence the exit-code branch must come first.

A clean exit with a completed turn is reported as `success` even if the agent's actual work
failed (tests still red, task not accomplished) — Codex's JSONL gives atlas no way to
distinguish "did nothing useful" from "did the task," which is why loop-mode workflows always
follow Codex dispatch with a separate `verify` stage rather than trusting exit code alone.

### Token usage fields

**VERIFIED.** `turn.completed.usage` carries exactly four fields, no dollar figure:

| Field | Notes |
| --- | --- |
| `input_tokens` | **Total** prompt tokens — the whole prompt, cached portion included. |
| `cached_input_tokens` | **VERIFIED (2026-07-26) to be a SUBSET of `input_tokens`**, i.e. the served-from-cache portion of it — matching OpenAI's documented convention (`prompt_tokens_details.cached_tokens` ⊆ `prompt_tokens`) and **opposite to Anthropic's**. Do not add it to `input_tokens`. |
| `output_tokens` | Total output tokens, including tool-call arguments and (see below) reasoning. |
| `reasoning_output_tokens` | Reasoning-model output tokens. `> 0` now **observed** (9, 50, and 159 across captured runs), closing that gap. Treated as a **subset** of `output_tokens` on OpenAI's convention (`completion_tokens_details.reasoning_tokens` ⊆ `completion_tokens`) — this one is convention plus consistency with the measured cache result, **not an independent measurement**: a run with `output_tokens=206`/`reasoning=50` against a ~46-token visible message fits either model arithmetically, because tool-call arguments are billed output too. |

#### How the cache question was settled

Same prompt, same directory, two runs back to back on `codex-cli 0.144.4`:

| Run | `input_tokens` | `cached_input_tokens` |
| --- | --- | --- |
| A (colder) | 68719 | 48384 |
| B (warmer) | 69161 | 62464 |

`input_tokens` held flat (+0.6%) while `cached_input_tokens` rose 29%. Under the addend model
`input_tokens` had to *fall* by ~14k as more of the prompt became cacheable — it did not.
Therefore `input_tokens` is the total and `cached_input_tokens` is a portion of it.

atlas's reduction rule is now `openai_subset_fields_v2` (`cli_backend.codex_usage_to_tokens`):
`(input_tokens, output_tokens)`, adding neither sub-field. Spans written under the superseded
`cached_input_as_addend_v1` rule remain recomputable, because
`codex_usage_attributes()` persists the raw four-field breakdown *and* the rule name into
`spans.attributes` — the L1 code review's finding M1 mechanism doing exactly the job it was
added for.

**No cost field exists anywhere in the schema.** Codex does not report a dollar figure at all
— this is a data-availability gap, not a plumb-storage gap (contrast with Claude's
`total_cost_usd`, which the CLI *does* report — see Part B — but which plumb can't yet persist,
BACKLOG.md P1-a). Cross-engine cost comparison is therefore **tokens-only**; no price table is
implemented anywhere in atlas's v3 loop-mode arc.

### Exit codes

**UNVERIFIED beyond the binary success/failure split.** Only `0` (success) and non-zero
(failure) were exercised by the read-only capture; Codex's documented exit-code taxonomy (if
one exists beyond 0/non-zero, analogous to `agy`'s `42`/`53` in Part C) has not been confirmed.

### Authentication

**VERIFIED.** `OPENAI_API_KEY` env var (checked first), or a `codex login` session file at
`$CODEX_HOME/auth.json` (default `~/.codex/auth.json` when `CODEX_HOME` is unset — the path was
confirmed present on this machine after `codex login`). `--ignore-user-config`'s own help text
states *"auth still uses `CODEX_HOME`"*, so a preflight check must honor the env var rather than
hardcoding `~/.codex`. Fails closed with no subprocess spawned if neither is present — same
posture as `AntigravityBackend.preflight()` (Part C).

### Sandbox — writability of `--add-dir` paths

**VERIFIED writable (2026-07-26).** A real write attempt under `--sandbox workspace-write`
with `-C <main>` and `--add-dir <extra>` created files in **both** directories. Pending
Decision #3 resolves to "honored as documented" — the contemplated fallback (`-C <worktree>`
only, dropping the extra `--add-dir` entries) is not needed, so atlas keeps passing
`repo_root` via `--add-dir` and Codex runs stay as context-rich as Claude runs.

The sandbox does still enforce its boundary: under `--sandbox read-only`, a write attempt was
refused with *"operation not permitted"* and no file appeared.

### Example commands

```bash
# Headless one-shot, JSONL output, workspace-write sandbox
codex exec "Add a hello.py that prints hi" --json -C ./my-repo --sandbox workspace-write

# Read-only exploration (no edits, no commands run)
codex exec "Explain what auth.py does" --json -C ./my-repo --sandbox read-only

# Extra writable directory alongside the primary workspace
codex exec "Implement the change" --json -C ./worktree --sandbox workspace-write \
  --add-dir ./my-repo --model gpt-5-codex
```

---

## Part F — Claude Code vs Antigravity vs Codex: quick comparison

| Dimension | Claude Code (`claude -p`) | Antigravity (`agy -p`) | Codex (`codex exec`) |
| --- | --- | --- | --- |
| Headless trigger | `-p` / `--print` | `-p` / `--prompt` (or non-TTY) | `exec` subcommand |
| Output formats | `text` / `json` / `stream-json` | `text` / `json` / `stream-json` (JSONL) | `text` / `--json` (JSONL) |
| Terminal event | `result` (status + text + stats) | `result` (JSONL) | `turn.completed` (**usage only** — no status, no text) |
| Status signal | `subtype` field | exit codes only | **exit code only** — no status field anywhere |
| Output text location | terminal `result.result` field | terminal `result.response` field | separate `item.completed` events (`item.type == "agent_message"`) |
| Cost telemetry | `total_cost_usd` + per-model breakdown | `stats` (tokens + latency) | **none** — tokens only (`input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`) |
| Sandbox / confinement | worktree (atlas-managed) only | worktree (atlas-managed) only | `--sandbox {read-only,workspace-write,danger-full-access}` (Codex-native) **+** worktree |
| Dangerous bypass flag | `--dangerously-skip-permissions` (banned) | none documented | `--dangerously-bypass-approvals-and-sandbox` / `--dangerously-bypass-hook-trust` (banned) |
| Headless auth | `ANTHROPIC_API_KEY` | browser OAuth by default; API-key headless contested | `OPENAI_API_KEY` or `codex login` session (`$CODEX_HOME/auth.json`) |
| Extra directories | `--add-dir` (verified writable) | `--include-directories` | `--add-dir` (writability **unverified** under `workspace-write`) |

**Net for atlas loop mode:** Codex's JSONL schema is the least self-describing of the three —
no status field, no cost field, output text on a different event type than the terminal event.
`CodexBackend` compensates by treating the `verify` stage (not backend exit-code parsing) as
the actual quality gate — the same posture atlas already takes toward Antigravity's coarser
exit-code taxonomy, just pushed one step further because Codex's stream carries even less
signal than `agy`'s.

---

## Sources

- Claude Code headless guide — <https://code.claude.com/docs/en/headless>
- Claude Code CLI reference — <https://code.claude.com/docs/en/cli-reference>
- Claude Agent SDK overview (+ third-party auth constraint) — <https://code.claude.com/docs/en/agent-sdk>
- Claude Agent SDK Python (ResultMessage schema) — <https://code.claude.com/docs/en/agent-sdk/python>
- Claude in Chrome — <https://support.claude.com/en/articles/12012173-get-started-with-claude-in-chrome>
- Gemini CLI → Antigravity transition (Google Developers Blog) — <https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/>
- Gemini CLI → Antigravity transition (GitHub discussion #27274) — <https://github.com/google-gemini/gemini-cli/discussions/27274>
- Gemini CLI headless reference — <https://geminicli.com/docs/cli/headless/>
- Gemini CLI / Antigravity CLI flag reference — <https://geminicli.com/docs/cli/cli-reference/>
- Gemini CLI system-prompt override (`GEMINI_SYSTEM_MD`) — <https://geminicli.com/docs/cli/system-prompt/>
- Gemini CLI automation tutorial — <https://geminicli.com/docs/cli/tutorials/automation/>
- Gemini CLI authentication / env vars — <https://geminicli.com/docs/get-started/authentication/>
- Antigravity CLI headless API-key auth request (issue #78) — <https://github.com/google-antigravity/antigravity-cli/issues/78>
- The New Stack — Gemini CLI vs Antigravity — <https://thenewstack.io/gemini-cli-antigravity-replacement/>
- amux — Gemini CLI → Antigravity migration guide — <https://amux.io/guides/gemini-cli-to-antigravity-cli/>
- Gemini in Chrome — <https://gemini.google/overview/gemini-in-chrome/>
- Codex CLI (`codex exec`) schema — verified directly against `codex-cli 0.144.4` output and
  `codex exec --help` (real capture, 2026-07-24); see
  `dev/active/loop-mode-phase-L1/loop-mode-phase-L1-context.md` for the raw sample and
  verification log. No public URL captured for this source — pin to the version string if
  cross-checking later.

## Cross-references

- Project README: [`README.md`](./README.md)
- Repo: <https://github.com/anant-gupta-utexas/content-pipeline>
- LLM port & adapters: `src/application/ports/llm.py`, `src/infrastructure/llm/{anthropic,openrouter,ollama}_client.py`
- **Layer-ownership decision** (atlas owns CLI-subprocess dispatch; this project stays API-only) and the `CliBackend` implementation spec that consumes Parts B–D above: [`../atlas/cli-backend-dispatch.md`](../atlas/cli-backend-dispatch.md)
