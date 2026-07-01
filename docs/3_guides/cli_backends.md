# CLI Backends

Atlas can dispatch stages to different agentic CLIs. The active backend is selected per-stage via a 4-tier resolution order, and each CLI has its own auth requirements.

## Backend resolution order (TRD-v2 §3.4)

For each stage, atlas picks the backend in this priority order (first non-null wins):

1. **Per-stage `backend:` field** in the workflow YAML — highest priority.
2. **Workflow-level `default_backend:`** field in the workflow YAML.
3. **`.atlas.toml` `[backend] default`** — project-wide config.
4. **Hard default: `"claude"`** — used when none of the above are set.

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

## Backends

### `claude` (default)

Dispatches via `claude -p` (Claude Code CLI).

**Auth:** Set `ANTHROPIC_API_KEY` in your environment. The `claude` subprocess validates the key itself and returns a non-zero exit code on auth failure (surfaced as `error_type="plugin_nonzero_exit"`).

**argv shape:** `claude -p <prompt> --no-session-persistence --model <model> --add-dir <dir> ...`

**Notes:**
- `--bare` is intentionally NOT used — it would skip DEV-ESSENTIALS plugin discovery that the dev pipeline depends on.
- Output is plain text (not JSON). `output_text` at the gate is the raw stdout.

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

**Default model:** `gemini-flash-lite` — matches Claude's `haiku` cost posture and fits the free-tier allowance (~20 req/day on flash-lite). Override per-stage or globally via the YAML `backend:` field (model selection within a backend is not yet configurable in `.atlas.toml`; that is deferred post-Phase 3).

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
| `plugin_nonzero_exit` | `claude -p` exited non-zero (auth failure, plugin crash, etc.) |
| `plugin_timeout` | Either backend's subprocess exceeded `timeout_s` |
| `unknown_backend` | Stage `backend:` field names a backend not in `{"claude", "agy"}` |

## See also

- [cli-backend-dispatch.md](../1_product_and_research/cli-backend-dispatch.md) — design rationale and per-CLI flag table
- [headless-clis-reference.md](../1_product_and_research/headless-clis-reference.md) — Part B (claude headless) and Part C (agy headless, auth status)
- [TRD-v2.md](../2_architecture/TRD-v2.md) §3.4 (backend resolution), §4 (security), §5 (agy experimental status)
