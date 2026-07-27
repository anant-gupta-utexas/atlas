# YAML Workflow Engine

Atlas v2 generalizes from a single hardcoded 7-stage dev pipeline to a YAML-driven multi-workflow engine. The engine was built across three phases (v2.0–v2.2, implemented 2026-06-30) and is the foundation for running any gated workflow — dev, job automation, research, or custom — through the same gate machinery and plumb measurement infrastructure.

## Table of contents

- [Why the engine was built](#why-the-engine-was-built)
- [Architecture overview](#architecture-overview)
- [YAML schema reference](#yaml-schema-reference)
- [Tool-string conventions](#tool-string-conventions)
- [Runner dispatch chain](#runner-dispatch-chain)
- [Backend selection](#backend-selection)
- [Workflow resolution order](#workflow-resolution-order)
- [Writing a custom workflow](#writing-a-custom-workflow)
- [Built-in workflows](#built-in-workflows)
- [Phase-by-phase build history](#phase-by-phase-build-history)
- [Testing the workflow engine](#testing-the-workflow-engine)

---

## Why the engine was built

Atlas v1 hardcoded the 7-stage dev pipeline in Python (`stages.py`, `STAGES` tuple, `StageName`/`GateLabel` enums). Adding a second workflow — like a job-automation pipeline — would require code changes, not config. Atlas v2 removes that constraint:

> "After v2, adding a new workflow is a YAML file plus a tool mapping — not a code change." — TRD-v2 §2

The engine also decouples *which CLI a stage dispatches to* from the pipeline logic. Stages can route to `claude -p`, `agy -p`, content-pipeline in-process, or the content-pipeline CLI subprocess — all without touching `Pipeline`, gates, or plumb writes.

---

## Architecture overview

The workflow engine spans five modules:

```
workflow_loader.py   ─ YAML → tuple[StageSpec, ...] with validation
stages.py            ─ StageSpec dataclass (10 fields)
composite_runner.py  ─ CompositeStageRunner: prefix-dispatch to the right runner
shell_runner.py      ─ ShellStageRunner: SHELL: → direct list-form subprocess
library_runner.py    ─ LibraryStageRunner: LIB: → in-process Python adapter
cli_backend.py       ─ CliBackend Protocol + ClaudeCodeBackend + AntigravityBackend
                       + CodexBackend (v3), backend/model resolution, usage parsing
```

`orchestrator.py` contains `SubprocessStageRunner` (the `RAW:` and plugin-command runner) and the `Pipeline` class. `Pipeline` is deliberately unaware of runners and backends — it sees only the `StageRunner` Protocol surface.

### Data flow

```
atlas run "<task>" --workflow <name>
        │
        ▼
cli.py: resolve_workflow()
        │  reads .atlas/workflows/<name>.yaml
        │  → ~/.atlas/workflows/<name>.yaml
        │  → src/atlas/workflows/<name>.yaml
        ▼
workflow_loader.py: load_workflow_file()
        │  yaml.safe_load() → validate → build tuple[StageSpec, ...]
        ▼
cli.py: _make_pipeline()
        │  constructs SubprocessStageRunner (with resolved backend config)
        │  constructs LibraryStageRunner (only if LIB: stages present)
        │  constructs ShellStageRunner (only if SHELL: stages present)
        │  wraps all in CompositeStageRunner
        ▼
Pipeline.__init__(stages=..., workflow_name=..., runner=composite, ...)
        │
        ▼ for each stage:
CompositeStageRunner.run(ctx, stage)
        │  stage.tool.startswith("LIB:")  → LibraryStageRunner
        │  stage.tool.startswith("SHELL:") → ShellStageRunner
        │  else                            → SubprocessStageRunner
        │
        ▼ SubprocessStageRunner:
cli_backend.py: resolve_backend(stage, workflow, config_default)
        │  per-stage backend: > workflow default_backend: > .atlas.toml > "claude"
        ▼
make_backend(name) → ClaudeCodeBackend | AntigravityBackend
        │  backend.preflight()  ← auth check (agy only)
        │  backend.build_argv() ← constructs subprocess argv
        ▼
subprocess.run(argv, ...)
        │
        ▼
backend.parse_result(stdout, stderr, returncode) → (status, output_text, error_type)
        ▼
StageOutcome → Pipeline gate / plumb span write
```

---

## YAML schema reference

A workflow YAML lives at one of the search-path locations (see [Workflow resolution order](#workflow-resolution-order)). It must be a plain YAML mapping with exactly these top-level keys:

```yaml
name: <workflow-name>          # required; [a-z][a-z0-9_]* (no hyphens)
default_backend: claude | agy  # optional; workflow-level backend default
stages:                        # required; ordered list of stage mappings
  - ...
```

Unknown top-level keys are rejected at load time with a named error.

### Stage fields

Each stage entry supports exactly these keys:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | yes | — | Stage identifier. Must match `[a-z][a-z0-9_]*`. Unique within the workflow. |
| `span_kind` | string | yes | — | plumb span kind. Must be one of: `llm`, `tool`, `subagent`, `handoff`, `plan`, `verify`. |
| `tool` | string | yes | — | Tool reference. See [Tool-string conventions](#tool-string-conventions). |
| `gate` | string or null | no | null | Gate label. Unique within the workflow. null means no gate after this stage. |
| `isolate` | bool | no | false | If true, stage runs inside a `git worktree add` directory. git must be on PATH. |
| `gate_is_async` | bool | no | false | If true, the gate score is written by the post-commit hook asynchronously, not by the orchestrator inline. |
| `backend` | string | no | null | Per-stage backend override. `claude` or `agy`. null inherits from `default_backend`. |
| `timeout_s` | positive int | no | null | Subprocess timeout in seconds. null → `_DEFAULT_TIMEOUT_S` fallback by stage name, then `_GLOBAL_FALLBACK_TIMEOUT_S` (600s). Inert for `LIB:` stages. |

Unknown stage keys are rejected at load time.

### Validation rules enforced at load time

- `span_kind` must be in `{"llm", "tool", "subagent", "handoff", "plan", "verify"}`.
- Stage `name` must match `[a-z][a-z0-9_]*`. Duplicate names are rejected.
- Gate labels must be unique within the workflow.
- `timeout_s` must be a positive integer (not float, not string, not zero).
- `isolate: true` requires `git` to be on `PATH` (checked at load time; repo-clean check is deferred to worktree creation time to avoid TOCTOU).
- `backend` values are NOT validated at load time — validation happens at dispatch time in `make_backend()`.

### What is NOT validated at load time

- The `backend` field value (unknown name fails at runtime as `unknown_backend`).
- The content of `RAW:` prompt strings (treated as trusted user input).
- The content of `SHELL:` command strings (first token is validated against a closed allow-list at dispatch time, not load time).
- The content of `LIB:` references (unknown refs fail at dispatch as `library_ref_unknown`).

---

## Tool-string conventions

The `tool` field determines which runner subsystem handles a stage. Atlas recognizes three prefix conventions:

### Plugin slash-commands (no prefix)

```yaml
tool: code-review
tool: consult-experts:pm
tool: dev-docs-be
```

Dispatched by `SubprocessStageRunner` → `plugin_resolver.resolve()` → `claude -p /<tool> <task>`. These are the tool strings the dev workflow uses. `PLUGIN_COMMANDS` in `plugin_resolver.py` provides fallback mappings; per-stage overrides come from `.atlas.toml [plugin_commands]`.

**Write the tool string without a leading slash.** `build_prompt()` adds the `/` itself, so a `tool: "/verify"` renders as `//verify` and the command silently fails to match. Use `tool: verify` and let `PLUGIN_COMMANDS` map it to its namespaced form (`DEV-ESSENTIALS:verify`). A tool string in this form that is absent from both `PLUGIN_COMMANDS` and `.atlas.toml [plugin_commands]` raises `RoutingDriftError` before any subprocess spawns — that allow-list check is a deliberate security boundary, not an inconvenience to route around.

### `RAW:` — inline prompt to claude (or configured backend)

```yaml
tool: "RAW:Draft a tailored CV and cover letter for each shortlisted role."
```

The string after `RAW:` is passed as the prompt directly to the configured backend (`claude -p` or `agy -p`). No plugin resolution. The `RAW:` prefix is stripped; the remaining text plus the task description becomes the full prompt.

This convention is for judgment/drafting stages that do not map to a named plugin command.

`plugin_resolver.resolve()` returns `RAW:` strings verbatim rather than looking them up: the text after `RAW:` is a literal prompt authored in the YAML, not a plugin or slash-command name, so there is no third-party command to validate against the allow-list. **A `RAW:` stage therefore needs no `PLUGIN_COMMANDS` entry and no `.atlas.toml [plugin_commands]` block.** An explicit `[plugin_commands]` override still wins if a repo deliberately redirects a `RAW:` stage.

> Fixed 2026-07-25. `resolve()` previously did a plain dict lookup with no `RAW:` special case despite documenting this bypass, so every `RAW:` stage raised `RoutingDriftError` under a real run unless `.atlas.toml` mapped each literal prompt string to itself. This blocked `atlas loop run` on `loop_dev.yaml` (Phase L2 T-L2.13) and affected `job.yaml`/`job_cli.yaml` equally. Regression tests live in `tests/unit/test_phase4.py`.

### `LIB:` — in-process Python adapter

```yaml
tool: "LIB:content_pipeline.capture"
tool: "LIB:content_pipeline.score_jobs"
```

Dispatched in-process by `LibraryStageRunner`. The string after `LIB:` is a registry key looked up against `_REGISTRY` in `library_runner.py` — a closed allow-list mapping to atlas adapter functions in `atlas/library_adapters/`. Arbitrary dotted-path execution from YAML content is not supported; only registry entries resolve.

`timeout_s` is inert for `LIB:` stages because there is no subprocess to time out. In-process latency is bounded by the library's own client timeouts.

If content-pipeline is not installed and a `LIB:` stage dispatches, the stage fails with `error_type="content_pipeline_not_installed"` and the error message names `--workflow job_cli` as the dependency-free alternative.

### `SHELL:` — direct CLI subprocess

```yaml
tool: "SHELL:content-pipeline capture --source job-boards"
tool: "SHELL:content-pipeline score-jobs --pending"
```

Dispatched by `ShellStageRunner` as a list-form subprocess (`shell=False`). The first token must be in `_ALLOWED_COMMANDS` (currently `{"content-pipeline"}`). This is the mechanism for `job_cli.yaml`'s dependency-free path — it runs the `content-pipeline` CLI directly without routing through `claude -p`.

Unlike `RAW:` (which sends the string as a prompt to Claude), `SHELL:` actually executes the command. Non-zero exit codes, `FileNotFoundError`, and `TimeoutExpired` all map to `StageOutcome(status="failure", ...)` — the runner never raises.

`timeout_s` is honored by `ShellStageRunner` (unlike `LIB:` stages).

---

## Runner dispatch chain

`CompositeStageRunner` implements the `StageRunner` Protocol and wraps three specific runners. `Pipeline` sees only the Protocol surface and is unaware of the dispatch logic:

```
CompositeStageRunner.run(ctx, stage)
├── stage.tool.startswith("LIB:")   → LibraryStageRunner.run()
├── stage.tool.startswith("SHELL:") → ShellStageRunner.run()
└── else                            → SubprocessStageRunner.run()
                                         (handles RAW: and plugin-command tools)
```

Runners are wired in `pipeline_factory.py::make_pipeline()` (moved out of `cli.py::_make_pipeline` in v3.1 so `atlas run`/`resume` and the loop's dispatch share one construction path instead of two that could drift). `LibraryStageRunner` is only instantiated when the resolved workflow has at least one `LIB:` stage. `ShellStageRunner` is only instantiated when there is at least one `SHELL:` stage. Dev workflow runs never instantiate either, so their code paths are never touched during a default `atlas run`.

### Error types from runners

| error_type | Runner | Condition |
|---|---|---|
| `library_runner_unavailable` | CompositeStageRunner | `LIB:` stage dispatched but `library=None` |
| `shell_runner_unavailable` | CompositeStageRunner | `SHELL:` stage dispatched but `shell=None` |
| `library_ref_unknown` | LibraryStageRunner | `LIB:<ref>` not in `_REGISTRY` |
| `content_pipeline_not_installed` | LibraryStageRunner | Import of a content-pipeline top-level package (`application`/`infrastructure`/`domain`) failed inside an adapter body |
| `library_adapter_error` | LibraryStageRunner | Atlas adapter module not importable, or use-case raised an exception |
| `shell_command_invalid` | ShellStageRunner | Empty string after stripping `SHELL:` |
| `shell_command_not_allowed` | ShellStageRunner | First token not in `_ALLOWED_COMMANDS` |
| `shell_command_not_found` | ShellStageRunner | Binary not on `PATH` (`FileNotFoundError`) |
| `shell_nonzero_exit` | ShellStageRunner | Subprocess exited non-zero |
| `shell_timeout` | ShellStageRunner | `TimeoutExpired` |
| `plugin_nonzero_exit` | SubprocessStageRunner | `claude -p` exited non-zero |
| `plugin_timeout` | SubprocessStageRunner | Subprocess timed out |
| `unknown_backend` | SubprocessStageRunner | resolved backend name not in `{"claude", "agy", "codex"}` |

---

## Backend selection

Applies only to stages dispatched by `SubprocessStageRunner` (plugin-command and `RAW:` stages). `LIB:` and `SHELL:` stages ignore the backend field.

Resolution is a **5-tier** cascade, first non-null wins:

1. **Explicit run-scoped override** — `atlas run --backend <name>`, or a loop issue's `engine:<name>` label.
2. `StageSpec.backend` — per-stage YAML field.
3. `LoadedWorkflow.default_backend` — workflow-level YAML field.
4. `Config.default_backend` — `.atlas.toml [backend] default`.
5. Hard default: `"claude"`.

> **Tier 1 was added 2026-07-26.** The override previously sat *below* the workflow `default_backend`, so any workflow declaring one silently beat it — which made both `--backend` and the loop's `engine:*` label inert with no error. If you are looking at an older description of a 4-tier order, this is the one that matches the code.

Available backends: `"claude"`, `"codex"`, and the experimental `"agy"`. An unknown backend name surfaces as `error_type="unknown_backend"` at dispatch time (not at load time — the loader stores the value but does not validate it).

Model names are resolved **separately and per-engine** (`[backend.models]` in `.atlas.toml`); they are not interchangeable across engines. For that, plus auth requirements, argv shapes, telemetry, and error types, see [cli_backends.md](cli_backends.md).

---

## Workflow resolution order

When `atlas run` is invoked, the workflow is resolved in this order (first match wins):

1. `--workflow-file <path>` — literal path to a YAML file, highest priority.
2. `--workflow <name>` — searches three locations in order:
   - `.atlas/workflows/<name>.yaml` (repo-local overrides)
   - `~/.atlas/workflows/<name>.yaml` (user-wide overrides)
   - `src/atlas/workflows/<name>.yaml` (in-package built-in)
3. Neither flag given — defaults to `dev` via the step 2 search path.

The `name` argument to `--workflow` must match `[a-z][a-z0-9_]*`. Path traversal attempts (e.g. `--workflow ../../etc/passwd`) are rejected before any filesystem access.

On `atlas resume`, the workflow name is re-read from the `workflow:` field in `tasks.md` and re-resolved via the same search path. If the original YAML has been edited or deleted between start and resume, atlas raises `WorkflowNotFoundError` and exits rather than silently falling back to dev.

---

## Writing a custom workflow

### Minimal example

```yaml
# .atlas/workflows/my-workflow.yaml
name: my_workflow

stages:
  - name: research
    span_kind: plan
    tool: "RAW:Research the topic and produce a structured summary."
    gate: gate_research

  - name: draft
    span_kind: subagent
    tool: "RAW:Write a first draft based on the research summary."
    gate: gate_draft

  - name: review
    span_kind: verify
    tool: "RAW:Review the draft and produce a final polished version."
    gate: gate_complete
```

Run it with:

```bash
atlas run "write a proposal for X" --workflow my_workflow
```

### Naming rules

- Workflow `name` and stage `name` both must match `[a-z][a-z0-9_]*`: lowercase letters, digits, underscores. No hyphens. Start with a letter.
- Gate labels must be unique within the workflow. No other constraint on their content.
- Stage names must be unique within the workflow.

### Using `isolate` for code-generation stages

```yaml
stages:
  - name: code_gen
    span_kind: subagent
    tool: code-gen-agent
    gate: gate_commit
    isolate: true
    gate_is_async: true
```

`isolate: true` causes the stage to run inside a `git worktree add` directory. The repo must be clean when the worktree is created. `gate_is_async: true` signals that the gate score is written by the post-commit hook (installed via `atlas hook install`), not by the orchestrator.

### Setting per-stage timeouts

```yaml
stages:
  - name: long_running_stage
    span_kind: subagent
    tool: "RAW:Do something slow."
    gate: gate_done
    timeout_s: 1800    # 30 minutes
```

`timeout_s` applies to subprocess stages only. `LIB:` stages are bounded by their library's own timeouts.

### Timeout resolution priority

For each stage's subprocess call, the effective timeout is resolved in this order:

1. `.atlas.toml [timeout_overrides]` by stage name (highest priority).
2. `stage.timeout_s` from the YAML.
3. `_DEFAULT_TIMEOUT_S` by stage name (a dict in `orchestrator.py` covering the 7 dev-pipeline stage names).
4. `_GLOBAL_FALLBACK_TIMEOUT_S` = 600s (for any stage name not in the above dict).

Dev-pipeline stages do not set `timeout_s` in `dev.yaml` — they inherit their historical timeouts from tier 3.

### Mixing backends within a workflow

```yaml
name: mixed_workflow
default_backend: claude

stages:
  - name: research
    span_kind: plan
    tool: "RAW:Research the topic."
    gate: gate_research
    # no backend: → inherits default_backend "claude"

  - name: cheap_draft
    span_kind: subagent
    tool: "RAW:Produce a cheap first pass."
    gate: gate_draft
    backend: agy         # per-stage override to Antigravity/Gemini
```

### Metric namespacing

Gate score metric names are namespaced by workflow:
- `dev` workflow: bare names (`gate_research`, `gate_prd`, etc.) — backward-compatible with v1.
- Any other workflow: `<workflow_name>.<gate_label>` (e.g. `my_workflow.gate_research`).

This means dev and non-dev runs coexist in the same plumb DB without metric collision, queryable by `task_id` prefix (`dev.<slug>` vs `my_workflow.<slug>`).

---

## Built-in workflows

Four workflows ship in `src/atlas/workflows/`:

### `dev` (default)

The original 7-stage software-development pipeline. Loaded by default when `--workflow` is not specified.

| Stage | span_kind | Tool | Gate | Notes |
|---|---|---|---|---|
| `research` | plan | `consult-experts:research` | `gate_research` | |
| `prd_draft` | plan | `consult-experts:pm` | `gate_prd` | |
| `trd_draft` | plan | `consult-experts:tech-lead` | `gate_trd` | |
| `tds_gen` | plan | `dev-docs-be` | — | No gate |
| `plan_review` | verify | `plan-reviewer` | `gate_tds` | |
| `code_gen` | subagent | `code-gen-agent` | `gate_commit` | `isolate: true`, `gate_is_async: true` |
| `code_review` | verify | `code-review` | `gate_phase_complete` | |

### `job` — job-search pipeline (Mode A)

A 4-stage job-search workflow that integrates content-pipeline in-process via `LIB:` stages. Requires content-pipeline installed (`uv sync --extra job`). See [job_workflow.md](job_workflow.md) for full documentation.

### `job_cli` — job-search pipeline (Mode B)

The same 4 stages as `job`, but `ingest_postings` and `score_fit` dispatch via `SHELL:` to the content-pipeline CLI subprocess. No Python dependency on content-pipeline — the CLI binary must be on `PATH`. See [job_workflow.md](job_workflow.md) for full documentation.

### `loop_dev` — the unattended one-shot workflow (v3)

Added in loop mode Phase L1. Three stages, **ungated**: `plan → code_gen[isolate] → verify`, with `default_backend: claude`. Quality is enforced by the `verify` stage plus the downstream PR review rather than by inline gates, so it is not meaningful to run attended without `--auto-approve`-style expectations — it exists for the loop daemon, which dispatches it for every `wf:quick` issue.

Two schema details it is the first workflow to exercise, both of which surfaced real bugs:

- Its last stage is **ungated**, which `dev.yaml` and `job.yaml` never are. `Pipeline.step()`'s ungated branch unconditionally indexed `self._stages[stage.index + 1]` and raised `IndexError` on the final stage; now guarded the same way the gated branch already was.
- Its `plan` and `code_gen` tools are `RAW:` strings, and `plugin_resolver.resolve()` did not special-case them despite its docstring saying it did — every stage raised `RoutingDriftError` under a real `atlas loop run` until `resolve()` was fixed to return `RAW:` strings verbatim.

---

## Phase-by-phase build history

The engine was built in three phases, each tagged independently. This section summarizes what each phase shipped.

### Phase 1 — Engine generalization (v2.0, 2026-06-30)

**Goal:** Make the dev pipeline YAML-driven with full backward compatibility. No new user-facing workflow yet.

**What shipped:**
- `workflow_loader.py` — new module, `load_workflow_file()` + `resolve_workflow()` with all validation.
- `src/atlas/workflows/dev.yaml` — the v1 hardcoded `STAGES` tuple extracted to YAML.
- `StageSpec` gained 4 new fields: `isolate`, `gate_is_async`, `backend`, `timeout_s`.
- `StageName`/`GateLabel` `StrEnum`s fully deleted. Stage names and gate labels are now validated `str`.
- `orchestrator.py`: three hardcoded conditionals replaced by data-driven `StageSpec` fields. `Pipeline.__init__` accepts `stages: tuple[StageSpec, ...]` and `workflow_name: str`.
- `cli.py`: `--workflow`/`--workflow-file` flags added.
- `state.py`: `tasks.md` gained a `workflow: <name>` field in the `## current` block; `resume()` re-resolves the workflow from this field.
- `plumb_io.py`: metric names namespaced as `<workflow>.<gate_label>` for non-dev workflows; dev keeps bare names for backward compatibility.
- `post_commit_hook.py`: metric name parameterized from `.atlas/current-run` line 5 (written at `gate_is_async` stage entry) rather than hardcoded `"gate_commit"`.
- Per-stage timeout resolution: 4-tier cascade (config override > YAML `timeout_s` > `_DEFAULT_TIMEOUT_S` > `_GLOBAL_FALLBACK_TIMEOUT_S`).

**Test counts after Phase 1:** 156 passing (153 unit/integration + 3 e2e). `workflow_loader.py` 100% coverage; repo-wide 92.75%.

**Key design decisions:**
- `backend` and `default_backend` are parsed and stored but not consumed in Phase 1 — a clean forward seam for Phase 3.
- The loader validates structure, not backend names — validation of `backend` values deferred to Phase 3's dispatch layer.
- The post-commit hook stays dependency-free (no YAML parsing in the hook subprocess; the metric name travels via `.atlas/current-run` line 5).

### Phase 2 — Job workflow end-to-end (v2.1, 2026-06-30)

**Goal:** Author `job.yaml`, run it end-to-end, validate that multi-workflow produces correct span trees and gate scores.

**What shipped:**
- `src/atlas/workflows/job.yaml` — 4-stage job-search workflow with `LIB:` stages for in-process content-pipeline dispatch.
- `src/atlas/workflows/job_cli.yaml` — dependency-free Mode B variant using `SHELL:` stages (renamed from `job-cli` because `_NAME_RE` rejects hyphens).
- `library_runner.py` — `LibraryStageRunner`, dispatches `LIB:` tools to content-pipeline use-cases in-process via a closed `_REGISTRY` allow-list.
- `library_adapters/capture_adapter.py` and `score_jobs_adapter.py` — thin per-use-case wiring functions.
- `composite_runner.py` — `CompositeStageRunner` (split from `orchestrator.py` because `orchestrator.py` exceeded the 500-line split trigger). Dispatches by tool-string prefix.
- `shell_runner.py` — `ShellStageRunner` (added during Phase 2 code review). Runs `SHELL:`-prefixed tools as direct list-form subprocesses with a `{content-pipeline}` allow-list and `shell=False`.
- `Pipeline.step()` gained `output_text` propagation to the gate prompt (Phase 2 T2.4).

**Test counts after Phase 2 (post-review):** 193 passing.

**Phase 2 code review findings addressed:**
- Phase 2 initially had `RAW:` for `job_cli.yaml`'s content-pipeline stages, which routed them through `claude -p` rather than actually invoking the CLI. The review added `ShellStageRunner` and switched `job_cli.yaml` to `SHELL:`.
- `LibraryStageRunner`'s `ImportError` handling was narrowed: only an `ImportError` naming a content-pipeline top-level package (`application`/`infrastructure`/`domain`) yields `content_pipeline_not_installed`; other `ImportError`s surface as `library_adapter_error`.
- Adapter imports dropped the `src.` prefix (content-pipeline's src-layout maps to bare top-level names).

### Phase 3 — CLI backend dispatch (v2.2, 2026-06-30)

**Goal:** Make `SubprocessStageRunner` support multiple agentic CLIs via a `CliBackend` strategy, consuming the `backend` fields that Phase 1 threaded through as inert.

**What shipped:**
- `cli_backend.py` — `CliBackend` Protocol + `ClaudeCodeBackend` + `AntigravityBackend` + `resolve_backend()` + `make_backend()` + `UnknownBackendError` + `_KNOWN_BACKENDS`. 192 LoC, 100% coverage.
- `SubprocessStageRunner` refactored to accept `default_backend` and `loaded_workflow` kwargs; its hardcoded `claude -p` argv block replaced by `backend.build_argv()` / `backend.preflight()` / `backend.parse_result()`.
- `Config` gained `default_backend: str = "claude"` reading `.atlas.toml [backend] default`.
- `cli.py::_make_pipeline()` threads `cfg.default_backend` and the loaded workflow into `SubprocessStageRunner`.
- `docs/3_guides/cli_backends.md` — per-CLI auth and experimental status documentation.

**Test counts after Phase 3:** 239 passing (+46 from Phase 2 baseline). `cli_backend.py` 100% coverage; repo-wide 95%.

**Key design decisions:**
- `ClaudeCodeBackend.build_argv()` output is byte-identical to Phase 2's hardcoded argv list — FR-8 dev-pipeline parity preserved exactly.
- `--bare` is intentionally NOT included in the Claude argv — it would break DEV-ESSENTIALS plugin discovery that the dev pipeline depends on.
- `AntigravityBackend.preflight()` checks for `GEMINI_API_KEY`/`GOOGLE_API_KEY` before spawning any subprocess. If neither env var is set, the stage fails with `error_type="agy_missing_auth_env"` and the subprocess is never spawned (security boundary: no browser OAuth fallback on headless sessions).
- `agy` is experimental — headless API-key auth is contested upstream (Antigravity issue #78). T3.8 manual smoke test is not yet attempted.
- The `backend` field is validated at dispatch time in `make_backend()`, not at YAML load time. The loader is deliberately decoupled from the backend allow-list.

---

## Testing the workflow engine

### Test file locations

| File | What it tests |
|---|---|
| `tests/unit/test_workflow_loader.py` | `load_workflow_file()` and `resolve_workflow()` — all validation paths, priority order, path traversal guard, `dev.yaml` parity |
| `tests/unit/test_library_runner.py` | `LibraryStageRunner` dispatch: unknown ref, not-installed, adapter exception, success passthrough, `timeout_s` ignored |
| `tests/unit/test_composite_runner.py` | `CompositeStageRunner` prefix dispatch: `LIB:`, `SHELL:`, default fallthrough, `library=None` failure |
| `tests/unit/test_shell_runner.py` | `ShellStageRunner`: allow-list enforcement, `FileNotFoundError`, `TimeoutExpired`, non-zero exit, success |
| `tests/unit/test_cli_backend.py` | `ClaudeCodeBackend` argv + JSON-envelope parsing + usage reduction, `AntigravityBackend` argv + JSON parsing + preflight, `CodexBackend` argv + JSONL parsing + preflight, `resolve_backend()` 5-tier table, `resolve_model()` per-engine table, `make_backend()` unknown name |
| `tests/unit/test_non_dev_workflow.py` | Synthetic non-dev workflow through sync gate and async gate; asserts namespaced metrics survive hook + run-id-changing resume |
| `tests/unit/test_library_adapters.py` | `score_jobs_adapter` and `capture_adapter` with mocked use-case classes |
| `tests/integration/test_job_workflow_e2e.py` | Full `job` workflow: span-tree shape, namespaced gate scores, dev/job coexistence, not-installed failure path names `job_cli`, `job_cli` variant runs dependency-free |
| `tests/integration/test_cli_backend_dispatch.py` | `agy` dispatch end-to-end (mocked subprocess), mixed-backend workflow, dev-pipeline unaffected, `job.yaml` `tailor_materials` dispatches via Claude backend |
| `tests/integration/test_job_adapters_real_import.py` | Real adapter import against installed content-pipeline (skipped when the `job` extra is absent) |
| `tests/e2e/test_e2e_happy_path.py` | Full dev-pipeline regression proof (runs unmodified after every phase) |

### Coverage targets (post-Phase 3)

| Module | Target | Achieved |
|---|---|---|
| `workflow_loader.py` | ≥ 90% | 100% |
| `cli_backend.py` | ≥ 85% | 100% |
| `library_runner.py` | ≥ 85% | ≥ 85% |
| `library_adapters/` | ≥ 80% | ≥ 80% |
| Repo-wide | ≥ 80% | 95% |

### Key regression tests

**`test_dev_pipeline_parity`** — asserts that `load_workflow_file(dev_yaml_path)` produces a `StageSpec` tuple field-by-field identical to the old hardcoded `STAGES` tuple. If `dev.yaml` drifts from v1 behavior, this fails.

**`test_claude_code_backend_argv_byte_identical_to_phase2`** — asserts `ClaudeCodeBackend.build_argv()` output is byte-identical to the hardcoded argv list from Phase 2. If the backend refactor changes the Claude invocation, this fails.

**`test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess`** — sets `subprocess.run` to raise `AssertionError` if called, then runs an `agy` stage with no API key env vars. Asserts `agy_missing_auth_env` is returned and `subprocess.run` is never invoked. This is the load-bearing security test for the auth preflight boundary.

**`test_e2e_happy_path.py`** — runs the full 7-stage dev pipeline with stub plugins. Must pass unmodified after every phase; any change to this file means the dev pipeline contract has changed.

### Mocking strategy

- **`LIB:` adapter tests**: mock at the use-case class boundary (`ScoreJobsUseCase`, `CaptureUseCase`), not at `_import_adapter`. This ensures the real adapter import path runs and catches API mismatches.
- **`SHELL:` tests**: use a stub binary on `PATH` or mock `subprocess.run` directly.
- **`SubprocessStageRunner` tests**: mock `subprocess.run`. Never shell out to `claude` or `agy` in unit tests.
- **Plumb tests**: use the in-memory `PlumbIO(real=False)` adapter — no SQLite writes in unit tests.
- **CI note**: `tests/integration/test_job_adapters_real_import.py` requires the `job` extra installed and is guarded by the `CONTENT_PIPELINE_TOKEN` repo secret in CI. It self-skips when the secret is absent.
