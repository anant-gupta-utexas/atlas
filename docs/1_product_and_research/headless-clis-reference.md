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

## Cross-references

- Project README: [`README.md`](./README.md)
- Repo: <https://github.com/anant-gupta-utexas/content-pipeline>
- LLM port & adapters: `src/application/ports/llm.py`, `src/infrastructure/llm/{anthropic,openrouter,ollama}_client.py`
- **Layer-ownership decision** (atlas owns CLI-subprocess dispatch; this project stays API-only) and the `CliBackend` implementation spec that consumes Parts B–D above: [`../atlas/cli-backend-dispatch.md`](../atlas/cli-backend-dispatch.md)
