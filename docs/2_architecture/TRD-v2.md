# Technical Requirements Document (TRD) — v2

**Project:** atlas — YAML-driven gated-workflow engine
**Scope:** v2 (engine generalization + first non-dev workflow). Builds on v1 TRD; does not supersede it.
**Status:** Shipped (v2.0–v2.2, complete 2026-06-30). This TRD is now a historical planning record — for current schema, runner mechanics, and dispatch behavior, see [`docs/3_guides/yaml_workflow_engine.md`](../3_guides/yaml_workflow_engine.md) and [`docs/3_guides/cli_backends.md`](../3_guides/cli_backends.md), which reflect what actually shipped.
**Created:** 2026-06-29
**Grounds on:**

- [`yaml-driven-workflows-analysis.md`](../../dev/archive/yaml-workflow-engine-design-notes/yaml-driven-workflows-analysis.md) — abstract engine plan + plumb impact analysis (archived design note; decisions shipped)
- [`job-workflow-scope.md`](../../dev/archive/yaml-workflow-engine-design-notes/job-workflow-scope.md) — first worked-example + hub-and-spoke model (archived design note; decisions shipped)
- [`cli-backend-dispatch.md`](../../dev/archive/yaml-workflow-engine-design-notes/cli-backend-dispatch.md) — multi-CLI `StageRunner` spec + layer-ownership decision (archived design note; decisions shipped)
- [`headless-clis-reference.md`](../1_product_and_research/headless-clis-reference.md) — per-CLI flag/auth/quota reference
- [v1 TRD](./TRD.md) — existing NFRs, integration contracts, success criteria

> **Assumption:** The PRD's future-releases table (v1.1–v2) predates the YAML-workflow analysis. This TRD maps phases to the sequencing defined in the archived `yaml-driven-workflows-analysis.md` §7.4 and `job-workflow-scope.md` §3, which collectively constituted the de facto release plan for this scope. A formal PRD update should follow.

---

## 1. Executive Summary

Atlas v1 shipped a working 7-stage dev-workflow CLI: a linear state machine with six human gates, worktree isolation, and full plumb span-tree instrumentation. The pipeline topology, stage names, gate labels, and tool routing are all hard-coded.

Atlas v2 generalizes the engine so that **multiple workflows** — each defined in a YAML file, each with its own stages and gates — can run through the same gate machinery and plumb measurement infrastructure. The dev pipeline becomes `dev.yaml`, the default. A **job-automation workflow** (`job.yaml`) is the first non-dev instance, exercising content-pipeline's existing `score_jobs` surface and validating the abstraction. Alongside this, the `SubprocessStageRunner` gains a **CLI backend strategy** (`CliBackend`) so that individual stages can dispatch to different agentic CLIs (`claude -p`, `agy -p`) without changing the `Pipeline`/gate/plumb contracts.

The defensible core remains unchanged: **human gates + durable state + plumb measurement**. v2 extends that moat from one domain (dev) to any domain (writing, research, finance, ops, job automation) — without competing with Claude Code's dynamic workflows on orchestration plumbing.

**Scope boundary:** v2 does NOT include an HTTP shell, multi-tenancy, concurrent runs, a UI, or dynamic/LLM-decided topology. Those remain deferred.

---

## 2. Business Context & Objectives

### Strategic positioning

Claude Code's dynamic workflows (shipped ~2026-05) commoditize sequential agent orchestration. Atlas's commodity part — the linear runner — is now something Claude authors on the fly. Atlas's defensible part — gates, durable state, measurement — is exactly what dynamic workflows lack. v2 leans into this positioning by extending the gate+measurement discipline to *any* domain, not just dev.

See [`yaml-driven-workflows-analysis.md`](../../dev/archive/yaml-workflow-engine-design-notes/yaml-driven-workflows-analysis.md) §2.5 and §5 for the full competitive framing.

### Objectives

1. **Prove the abstraction is real.** Author `job.yaml` as a concrete artifact and run it end-to-end. If gates + measurement feel natural on a non-dev workflow, the generalization holds.
2. **Pay the engine cost once, get N workflows.** After v2, adding a new workflow is a YAML file + tool mapping — not a code change.
3. **Enable CLI backend flexibility.** Stages can dispatch to `claude -p` or `agy -p` (or future CLIs) based on workflow config, without touching `Pipeline`, gates, or plumb.
4. **Maintain v1 guarantees.** The dev pipeline must remain the default and pass all existing tests. v2 is additive.

### KPIs this build must make measurable

- **Multi-workflow run completeness.** A `job.yaml` run produces a well-formed span tree with the correct number of spans/gates, distinct from dev-pipeline runs.
- **Cross-workflow gate score completeness.** Gate scores from both `dev` and `job` workflows coexist in plumb, queryable by workflow namespace.
- **Dev-pipeline regression.** All v1 acceptance criteria continue to pass after the engine generalization (zero regressions).
- **CLI backend dispatch.** At least one stage successfully dispatches to a non-Claude-Code backend (`agy -p`) and produces a valid `StageOutcome`.

---

## 3. Functional Requirements

### 3.1 YAML workflow definition

A workflow is a YAML file that defines an ordered sequence of stages, each with:

```yaml
# ~/.atlas/workflows/<name>.yaml  or  .atlas/workflows/<name>.yaml
name: <workflow-name>            # unique identifier, used as task_id prefix
stages:
  - name: <stage-name>           # free-form string (validated: [a-z][a-z0-9_]*)
    span_kind: <kind>            # MUST be one of plumb's closed set: llm, tool, subagent, handoff, plan, verify
    tool: <tool-reference>       # plugin slash-command OR "RAW:<prompt>" for inline prompts
    gate: <gate-label> | null    # free-form string or null (no gate)
    isolate: true | false        # default false; if true, stage runs in a git worktree
    backend: claude | agy | null # default null (inherits workflow/global default)
```

**Loader contract:**
- Parse YAML into `tuple[StageSpec, ...]` — the exact type `Pipeline` already consumes.
- Assign `index` and `gate_index` by enumeration (same logic as v1's hardcoded tuple).
- Validate every `span_kind` against plumb's six allowed values (`llm`, `tool`, `subagent`, `handoff`, `plan`, `verify`) **at load time** — reject unknown kinds before a run starts.
- Validate `name` uniqueness within the workflow.
- Validate `gate` uniqueness (no two stages share a gate label).
- If `isolate: true`, validate that git is available and the repo is clean (same check as v1's worktree guard).

**The dev pipeline becomes `dev.yaml`:**
The current hardcoded `STAGES` tuple is extracted to `dev.yaml` (or an equivalent in-package default). It remains the default when `--workflow` is not specified. All existing tests continue to exercise the dev pipeline path.

### 3.2 Workflow selection at runtime

```bash
atlas run "<task>" --workflow <name>    # explicit workflow
atlas run "<task>"                      # default → dev
atlas run "<task>" --workflow-file ./path/to/custom.yaml  # one-off YAML
```

**Resolution order:**
1. `--workflow-file <path>` — literal path, highest priority.
2. `--workflow <name>` — searches: `.atlas/workflows/<name>.yaml` → `~/.atlas/workflows/<name>.yaml` → built-in package workflows.
3. No flag — default `dev`.

### 3.3 Loosen StageName / GateLabel from StrEnum to validated strings

**Current state:** `StageName` and `GateLabel` are `StrEnum`s with fixed, closed member sets. The orchestrator, state store, and tests reference these enum values directly.

**Target state:** Stage names and gate labels become plain `str` values, validated at YAML load time against the `[a-z][a-z0-9_]*` pattern. Code that references specific enum members (e.g., `StageName.CODE_GEN`) is refactored to use `StageSpec` properties instead:

| v1 hardcoded check | v2 data-driven replacement |
|---|---|
| `if stage.name == StageName.CODE_GEN` (worktree creation) | `if stage.isolate` (new `StageSpec.isolate: bool` field) |
| `if stage.gate_label == GateLabel.GATE_COMMIT` (async hook flow) | `if stage.gate_is_async` (new `StageSpec.gate_is_async: bool` field) |
| `if stage.gate_label is None` (skip gate) | Unchanged — `gate_label is None` is already data-driven |

**New `StageSpec` fields:**

```python
@dataclass(frozen=True)
class StageSpec:
    index: int
    name: str                    # was StageName (StrEnum)
    span_kind: str               # constrained to plumb's six
    tool: str
    gate_label: str | None       # was GateLabel (StrEnum) | None
    gate_index: int | None
    isolate: bool = False        # new: run in git worktree
    gate_is_async: bool = False  # new: gate written by external hook, not orchestrator
    backend: str | None = None   # new: CLI backend override (null = workflow/global default)
```

### 3.4 CLI backend dispatch (`CliBackend` strategy)

**Decision:** atlas owns headless-CLI subprocess dispatch. See [`cli-backend-dispatch.md`](../../dev/archive/yaml-workflow-engine-design-notes/cli-backend-dispatch.md).

**Implementation:** Make the backend CLI a strategy on `SubprocessStageRunner`:

```python
class CliBackend(Protocol):
    name: str
    def build_argv(self, *, prompt: str, model: str, add_dirs: list[Path],
                   timeout_s: int, extra_flags: dict[str, str]) -> list[str]: ...
    def parse_result(self, stdout: str, stderr: str, returncode: int) -> StageOutcome: ...

class ClaudeCodeBackend:    # claude -p ...
class AntigravityBackend:   # agy -p ...
```

**Per-CLI contract differences** (from [`headless-clis-reference.md`](../1_product_and_research/headless-clis-reference.md)):

| Dimension | `ClaudeCodeBackend` | `AntigravityBackend` |
|---|---|---|
| Command | `claude` | `agy` |
| Workspace dir flag | `--add-dir` | `--include-directories` |
| Session control | `--no-session-persistence` | not documented |
| Output format | `--output-format json` → `result` / `structured_output` | `--output-format json` → `response` / `stats` |
| Failure signal | returncode + `system/api_retry` events | exit codes (`0/1/42/53`) |
| CI determinism | `--bare` | sandbox via `-s` |
| Auth (headless) | `ANTHROPIC_API_KEY` (clean) | browser OAuth by default; API-key contested |

**Backend resolution order:**
1. Per-stage `backend` field in YAML (highest priority).
2. Workflow-level `default_backend` in YAML.
3. `.atlas.toml` → `[backend] default = "claude"`.
4. Hard default: `claude`.

### 3.5 Per-workflow tool routing

**Current state:** `PLUGIN_COMMANDS` is a hardcoded dict in `plugin_resolver.py` mapping 7 tool names to slash-command strings.

**Target state:** Tool-to-command mapping moves into the workflow YAML (via the `tool` field on each stage). The `PLUGIN_COMMANDS` dict is retained only as the default for `dev.yaml` stages. `.atlas.toml [plugin_commands]` continues to serve as a per-repo override layer.

**Resolution order for a given stage's tool:**
1. `.atlas.toml [plugin_commands.<tool>]` — per-repo override (existing mechanism).
2. The `tool` field in the workflow YAML — the primary source.
3. Built-in `PLUGIN_COMMANDS` dict — fallback for dev pipeline stages only.

Tools prefixed with `RAW:` bypass plugin resolution and are passed directly as the prompt text (existing mechanism, unchanged).

### 3.6 Workflow-aware state file

**Current state:** `create_tasks_md()` generates checkboxes from the hardcoded `STAGES` tuple and sets initial phase to `research`.

**Target state:**
- `create_tasks_md()` accepts a `tuple[StageSpec, ...]` (from the loaded workflow) and generates checkboxes matching those stages.
- The `## current` block includes a `workflow: <name>` field.
- `first_unchecked()` returns `str` (not `StageName`), matching stage names from the loaded workflow.
- On resume, the workflow name is read from `tasks.md` and the corresponding YAML is re-loaded to reconstruct the `StageSpec` tuple.

### 3.7 Metric-name namespacing convention

Before the first non-dev workflow writes scores, agree and enforce:

```
<workflow>.<gate_label>    # e.g., "job.gate_shortlist", "dev.gate_research"
```

as the `metric_name` in `scores` rows. This is a **naming convention** enforced at the atlas layer (in `plumb_adapter.py`), not a plumb schema change. The `metric_name` field is free-form `TEXT`.

For the dev pipeline, v1-era metric names (`gate_research`, `gate_prd`, etc.) are preserved as-is for backward compatibility. New workflows use the namespaced form from their first run.

### 3.8 Job workflow (`job.yaml`) — first non-dev instance

The concrete worked-example from [`job-workflow-scope.md`](../../dev/archive/yaml-workflow-engine-design-notes/job-workflow-scope.md) §3:

```yaml
name: job
default_backend: claude
stages:
  - name: ingest_postings
    span_kind: tool
    tool: "RAW:content-pipeline capture --source job-boards"
    gate: null
    isolate: false
  - name: score_fit
    span_kind: verify
    tool: "RAW:content-pipeline score-jobs --pending"
    gate: gate_shortlist
    isolate: false
  - name: tailor_materials
    span_kind: subagent
    tool: "RAW:Tailor application materials for each shortlisted role"
    gate: gate_materials
    isolate: false
    backend: claude
  - name: emit_package
    span_kind: tool
    tool: "RAW:Assemble the application package and write to docs/01_professional/job_applications/"
    gate: gate_done
    isolate: false
```

**Consumption modes** (mixed within one workflow):
- **Mode A (library import):** `ingest_postings` and `score_fit` call content-pipeline use-cases in-process via `pip install -e ../content-pipeline`. Requires a new `StageRunner` implementation (`LibraryStageRunner`) or `RAW:` dispatch to the content-pipeline CLI.
- **Mode C (agentic CLI):** `tailor_materials` dispatches to `claude -p` for judgment.
- **Mode B (CLI subprocess):** Fallback for Mode A stages if process isolation is preferred.

**Phase 2 optimization:** Replace `RAW:content-pipeline ...` shell invocations with direct use-case calls (Mode A) where structured results matter. This is deferred to Phase 2 (§14).

---

## 4. Non-Functional Requirements (NFRs)

### Performance

- **YAML load time:** < 50 ms for a workflow file with ≤ 20 stages. Measured at the loader, not including disk I/O.
- **Workflow resolution:** < 100 ms to resolve `--workflow <name>` through the search path.
- **No regression on v1 targets:** `atlas status` < 500 ms, post-commit hook < 1 s.
- **`CliBackend.build_argv()` and `parse_result()`:** Pure computation, < 1 ms each.

### Security

All v1 security requirements carry forward. Additionally:

- **YAML loading:** Use `yaml.safe_load()` only — never `yaml.load()` with arbitrary constructors. Reject YAML files with unknown top-level keys.
- **Workflow file trust boundary:** Workflow YAML files can reference arbitrary tool strings (including `RAW:` prompts). This is equivalent to the user typing a command — the trust model is "the workflow author is the user." Document this explicitly.
- **Antigravity auth caveat:** `agy -p` defaults to browser OAuth, which is unsuitable for headless/CI. The `AntigravityBackend` must validate that `GEMINI_API_KEY` or `GOOGLE_API_KEY` is set before attempting dispatch, and fail with a clear error message if not. Do not silently fall back to browser auth.

### Reliability

- **Backward compatibility:** All v1 acceptance criteria pass without modification after the engine generalization. The dev pipeline runs identically whether loaded from the hardcoded tuple (removed) or from `dev.yaml`.
- **Unknown stage names on resume:** `first_unchecked()` already skips unknown stage names via try/except. This behavior is preserved; a workflow YAML that has been edited between run start and resume does not crash — it logs a warning and skips stages that no longer exist.
- **Atomic YAML load:** The workflow YAML is loaded once at `Pipeline` construction and frozen for the duration of the run. Mid-run edits to the YAML file do not affect a running pipeline.

### Usability

- **`atlas --help`** lists available workflows (discovered via the search path).
- **`atlas status`** includes the workflow name in its output.
- **Error on unknown workflow:** `atlas run --workflow nonexistent` exits non-zero with a message naming the search path locations checked.
- **Error on invalid YAML:** Surface the specific validation failure (unknown span_kind, duplicate stage name, etc.) with the offending line, not a raw traceback.

---

## 5. System Constraints & Assumptions

All v1 constraints carry forward. Additionally:

- **PyYAML dependency:** v2 adds `pyyaml >= 6.0` (or uses `ruamel.yaml` if round-trip preservation matters — unlikely for v2). Added to `pyproject.toml [project.dependencies]`.
- **v1 → v2 philosophy relaxation:** v1's "300 LoC / no registry / no new file type" vow is **deliberately relaxed** for v2. The YAML loader is a new module (`workflow_loader.py`), and the `CliBackend` strategy is a new abstraction. This is a conscious scope decision, not scope creep — called out explicitly per [`yaml-driven-workflows-analysis.md`](../../dev/archive/yaml-workflow-engine-design-notes/yaml-driven-workflows-analysis.md) §3.4.
- **LoC target revised:** v2 target is ≤ ~600 lines across the engine (orchestrator + loader + backends + state). Individual files stay under the 400-line guideline from `CLAUDE.md`.
- **content-pipeline as optional dependency:** In Phase 2, `content-pipeline` is added as an editable path dependency (`pip install -e ../content-pipeline`). It is optional — the job workflow falls back to `RAW:` CLI dispatch if content-pipeline is not installed.
- **Antigravity CLI stability caveat:** `agy` is in flux (Gemini CLI retired 2026-06-18; headless API-key auth is contested — issue #78). The `AntigravityBackend` is implemented but treated as experimental until auth stabilizes.

---

## 6. Integration Requirements

All v1 integrations carry forward. New integrations:

| Integration | Surface | Version / shape | Owner |
|---|---|---|---|
| content-pipeline | Python API (use-case classes) — optional in-process calls | path install; editable (`pip install -e ../content-pipeline`) | sibling repo |
| Antigravity CLI | Subprocess (`agy -p`) via `AntigravityBackend` | latest stable; auth via `GEMINI_API_KEY` env | external CLI |
| PyYAML | `yaml.safe_load()` for workflow parsing | `>= 6.0`, pinned in `pyproject.toml` | PyPI |

**atlas ↔ content-pipeline boundary.** Same principle as atlas ↔ plumb in v1: direct in-process Python calls, no IPC. `atlas → content-pipeline`, never the reverse. content-pipeline use-cases are plain classes with constructor-injected ports — atlas instantiates them with the required adapters at the `_make_pipeline()` assembly point.

**CliBackend ↔ Pipeline boundary.** `CliBackend` is internal to `SubprocessStageRunner`. `Pipeline` sees only the `StageRunner` Protocol and `StageOutcome` — it does not know which CLI was used. Gates, worktrees, plumb instrumentation are all untouched.

---

## 7. Data Requirements

### Atlas-owned flat files (additions to v1)

| File | Purpose | Lifecycle |
|---|---|---|
| `.atlas/workflows/<name>.yaml` | Per-repo workflow definitions | User-authored, checked into repo |
| `~/.atlas/workflows/<name>.yaml` | User-wide workflow definitions | User-authored |
| Built-in `dev.yaml` (in-package) | Default dev pipeline | Shipped with atlas |

### State file changes

The `tasks.md` format gains a `workflow` field in the `## current` block:

```markdown
## current
```text
run_id:   <uuid>
workflow: job
phase:    score_fit
gate:     2
next:     run stage 1 (score_fit)
```

### plumb impact (from analysis §4)

| plumb concern | Verdict | Action |
|---|---|---|
| `runs.task_id` (free text) | Works as-is | Namespace per workflow: `<workflow>.<slug>` |
| `scores` / `user_signal` scorer | Works as-is | Gate scores from any workflow fit |
| `examples` promotion | Works as-is | Rejection→regression is workflow-neutral |
| `spans.kind` CHECK (closed set of 6) | Constraint | **Validate YAML `span_kind` against the six allowed kinds at the loader.** No plumb change. |
| Metric-name namespacing | Convention | `<workflow>.<gate_label>` enforced at atlas layer (§3.7) |
| `runs.workflow` provenance column | Deferred | Not required for v2. `task_id` prefix covers it. Revisit if cross-workflow analysis becomes central; would bump plumb `SCHEMA_VERSION`. |

**No plumb schema migration is required for v2.**

---

## 8. Infrastructure & Environment Requirements

Same as v1 (local laptop, GitHub Actions CI). No new hosted infrastructure.

**CI additions:**
- Workflow loader unit tests (valid/invalid YAML parsing).
- `CliBackend` unit tests (argv construction, result parsing for both backends).
- Dev-pipeline regression suite (all existing tests must pass with `dev.yaml` as the source instead of hardcoded `STAGES`).

---

## 9. Compliance & Regulatory Requirements

None. Same as v1.

---

## 10. Quality Assurance Requirements

### Coverage targets

- **`workflow_loader.py`:** 90%+ (critical new module — malformed YAML must be caught).
- **`cli_backend.py`:** 85%+ (argv construction and result parsing for both backends).
- **Existing modules:** Coverage targets unchanged from v1.

### Mandatory tests

All v1 mandatory tests carry forward. New tests:

| Test | What it validates |
|---|---|
| **Workflow loader — valid YAML** | A well-formed YAML file produces the expected `tuple[StageSpec, ...]` with correct index/gate_index assignment. |
| **Workflow loader — invalid span_kind** | A YAML with `span_kind: "research"` (not in plumb's six) is rejected at load time with a clear error. |
| **Workflow loader — duplicate stage name** | Rejected at load time. |
| **Workflow loader — duplicate gate label** | Rejected at load time. |
| **Workflow loader — name validation** | Stage names matching `[a-z][a-z0-9_]*` pass; others rejected. |
| **Dev-pipeline parity** | `dev.yaml` loaded through the YAML loader produces a `StageSpec` tuple identical to the v1 hardcoded `STAGES` (modulo the new boolean fields defaulting to match v1 behavior). |
| **Routing fixture parity** | `routing_ground_truth.json` still validates against the dev pipeline whether loaded from YAML or hardcoded. |
| **Workflow-aware state file** | `create_tasks_md()` with a non-dev workflow generates checkboxes matching that workflow's stages. `first_unchecked()` returns the correct stage name. |
| **CliBackend — ClaudeCode argv** | `ClaudeCodeBackend.build_argv()` produces the expected `["claude", "-p", ...]` list. |
| **CliBackend — Antigravity argv** | `AntigravityBackend.build_argv()` produces the expected `["agy", "-p", ...]` list. |
| **CliBackend — parse_result** | Both backends correctly extract success/failure from their respective JSON output schemas. |
| **Multi-workflow isolation** | Two runs (dev + job) in the same repo produce distinct span trees with non-overlapping `task_id` prefixes. |
| **Backend resolution** | Per-stage override > workflow default > config default > hard default. |

### Linters

Same as v1: `ruff check`, `ruff format`, `mypy src`. All CI gates.

---

## 11. Deployment & Operations Requirements

Same as v1. No deployed surface. The repo is the artifact.

**Release tags:**
- v2.0 — engine generalization + dev-pipeline parity (Phase 1 exit).
- v2.1 — job workflow end-to-end (Phase 2 exit).
- v2.2 — CLI backend dispatch with Antigravity (Phase 3 exit).

---

## 12. Dependencies & Risks

### New dependencies

| Dependency | Type | Risk |
|---|---|---|
| `pyyaml >= 6.0` | PyPI package | Stable, widely used. Low risk. |
| content-pipeline (optional) | Sibling repo, editable install | Same author. Coupling risk mitigated by optional-dependency design — atlas falls back to CLI dispatch if not installed. |
| Antigravity CLI (`agy`) | External CLI binary | **Medium risk.** In flux post-Gemini-CLI retirement. Headless API-key auth is contested (issue #78). Mitigated by treating as experimental and defaulting to Claude Code. |

### Risks

| Risk | Impact | Probability | Mitigation |
|---|---|---|---|
| YAML loader becomes a framework (schema validation, inheritance, templating) | High | Medium | Loader validates the six fields defined in §3.1, nothing more. No YAML inheritance, no templating, no conditional stages. If a workflow needs dynamic topology, it's a dynamic-workflow use case, not an atlas use case. |
| The `isolate` / `gate_is_async` booleans don't cover a future workflow's needs | Medium | Low | These two booleans replace exactly two v1 `if` branches. If a third behavioral flag is needed, add it to `StageSpec` — the cost is one field + one conditional, not a redesign. |
| Antigravity auth breaks headless dispatch | Medium | High | Default to Claude Code. `AntigravityBackend` is behind an explicit `backend: agy` opt-in. CI tests mock the subprocess, not the real CLI. |
| Dev-pipeline regression during refactor | High | Medium | All v1 tests run against both the hardcoded path (deleted last) and the YAML-loaded path (added first). Migration is: add YAML path → verify parity → delete hardcoded path. |
| content-pipeline API churn breaks library-import stages | Medium | Medium | content-pipeline use-cases are constructor-injected ports. Atlas pins to a specific content-pipeline commit SHA in `pyproject.toml` (same pattern as plumb). |

### Resolved decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | **YAML-driven, not Python-coded registry.** | The workflow YAML is a data file the user authors; a Python registry requires code changes per workflow. YAML aligns with atlas's "stages are black boxes" principle. Decision recorded 2026-06-28 in `job-workflow-scope.md`. |
| 2 | **atlas owns CLI subprocess dispatch.** | `SubprocessStageRunner` already exists. content-pipeline stays API-only. See `cli-backend-dispatch.md`. |
| 3 | **No plumb schema migration for v2.** | `spans.kind` constraint is handled by validating at the atlas loader. Metric-name namespacing is a convention, not a schema change. `runs.workflow` column deferred. |
| 4 | **v1 "300 LoC / no registry" vow is relaxed for v2.** | Explicit scope decision, not scope creep. The YAML loader is the registry; the `CliBackend` is the strategy. Both are deliberate v1→v2 philosophy shifts. |
| 5 | **Dev pipeline backward compatibility via `dev.yaml`.** | The hardcoded `STAGES` tuple is extracted to `dev.yaml`. All v1 tests continue to pass. The tuple is deleted only after YAML-loaded parity is proven. |

---

## 13. Success Criteria & Acceptance Criteria

v2 ships when all of the following hold:

### Engine generalization (Phase 1 exit gate)

1. **Dev-pipeline parity.** All v1 acceptance criteria pass with the dev pipeline loaded from `dev.yaml` instead of the hardcoded `STAGES` tuple. Zero regressions.
2. **YAML loader correctness.** A valid workflow YAML loads into the expected `StageSpec` tuple. Invalid YAMLs (bad span_kind, duplicate names, etc.) are rejected with clear errors.
3. **Workflow-aware state.** `tasks.md` includes `workflow: <name>` and generates stage-specific checkboxes. Resume correctly re-loads the workflow.
4. **Routing fixture stability.** `routing_ground_truth.json` validates against the YAML-loaded dev pipeline.

### Job workflow (Phase 2 exit gate)

5. **Job workflow end-to-end.** `atlas run "..." --workflow job` produces a complete span tree with the expected spans and gate scores, distinct from dev runs.
6. **Cross-workflow coexistence.** Dev and job runs coexist in the same plumb DB. Metric names are namespaced (`job.gate_shortlist`, `dev.gate_research`). Queries by workflow prefix return the correct subset.

### CLI backend dispatch (Phase 3 exit gate)

7. **Multi-backend dispatch.** At least one stage dispatches to `AntigravityBackend` and produces a valid `StageOutcome` (mocked in CI; real dispatch in manual testing if auth allows).
8. **Backend resolution.** Per-stage override → workflow default → config default → hard default, verified by test.

### Cross-cutting

9. **LoC budget.** Engine code (orchestrator + loader + backends + state) stays ≤ ~600 lines total.
10. **No plumb migration.** plumb `SCHEMA_VERSION` is unchanged.

---

## 14. Development Phases

### Phase 1 — Engine generalization

**Goal:** Generalize atlas from a single hardcoded dev pipeline to a YAML-driven multi-workflow engine, with full backward compatibility.

**Delivers:** Foundation for all subsequent phases. No user-facing new workflow yet — the dev pipeline runs identically, just loaded from YAML.

**Dependencies:** None (builds on shipped v1).

**Engineering scope summary:**
- New module `workflow_loader.py`: YAML parser → `tuple[StageSpec, ...]` with validation (span_kind against plumb's six, name uniqueness, gate uniqueness, name format).
- Extract hardcoded `STAGES` tuple to `dev.yaml` (in-package default).
- Loosen `StageName` / `GateLabel` from `StrEnum` to validated `str`. Delete the enum modules.
- Add `isolate: bool`, `gate_is_async: bool`, `backend: str | None` fields to `StageSpec`.
- Refactor three hardcoded conditionals in `orchestrator.step()` to use `StageSpec` properties (`isolate`, `gate_is_async`, `gate_label is None`).
- Update `cli.py` to accept `--workflow` / `--workflow-file` flags and wire workflow resolution into `_make_pipeline()`.
- Update `state.py`: `create_tasks_md()` accepts workflow's `StageSpec` tuple; `## current` block includes `workflow` field; `first_unchecked()` returns `str`.
- Update `plugin_resolver.py`: tool resolution merges YAML `tool` field with `.atlas.toml` overrides. `PLUGIN_COMMANDS` retained as dev-pipeline defaults only.
- Update `_validate_routing_fixture()` to be dev-workflow-only (or per-workflow fixture if other workflows provide one).
- Add metric-name namespacing logic to `plumb_adapter.py` (prefix with `<workflow>.` for non-dev workflows).
- All v1 tests updated and passing. New loader/state/resolver tests added.

**Exit criteria:**
- All v1 acceptance criteria pass (zero regressions).
- `atlas run "<task>"` (no `--workflow` flag) loads `dev.yaml` and behaves identically to v1.
- `atlas run "<task>" --workflow dev` explicitly loads `dev.yaml` with same result.
- Loader tests validate acceptance and rejection of valid/invalid YAMLs.
- Routing fixture test passes against the YAML-loaded dev pipeline.

---

### Phase 2 — Job workflow end-to-end

**Goal:** Author `job.yaml`, run it end-to-end, and validate that the multi-workflow engine produces correct span trees and gate scores for a non-dev domain.

**Dependencies:** Phase 1.

**Engineering scope summary:**
- Author `job.yaml` (see §3.8) as a shipped workflow (in-package or `~/.atlas/workflows/`).
- Integrate content-pipeline as an optional editable dependency for Mode A stages (`ingest_postings`, `score_fit`). If not installed, fall back to `RAW:` CLI dispatch (Mode B).
- End-to-end test: `atlas run --workflow job` produces a complete span tree with the expected 4 spans and 3 gate scores.
- Verify metric-name namespacing: `job.gate_shortlist`, `job.gate_materials`, `job.gate_done` in plumb.
- Verify cross-workflow coexistence: dev + job runs in the same DB, queryable by prefix.
- Document the hub-and-spoke trigger model (second-brain → ai-workx skill → atlas → content-pipeline → plumb) in the README or a guide.

**Exit criteria:**
- `job.yaml` run produces a well-formed span tree distinct from dev runs.
- Gate scores are namespaced and queryable.
- Dev pipeline remains unaffected (regression suite green).
- content-pipeline integration is optional (atlas runs without it installed, falling back to CLI dispatch).

---

### Phase 3 — CLI backend dispatch

**Goal:** Enable per-stage dispatch to different agentic CLIs (`claude -p`, `agy -p`), selectable via workflow YAML or config.

**Dependencies:** Phase 1. (Independent of Phase 2 — can run in parallel.)

**Engineering scope summary:**
- Define `CliBackend` Protocol (§3.4).
- Implement `ClaudeCodeBackend` — extract existing `SubprocessStageRunner` argv/parse logic into this class.
- Implement `AntigravityBackend` — build argv per `agy -p` flag surface, parse JSON result per `agy` schema, validate headless auth env vars.
- Refactor `SubprocessStageRunner` to accept a `CliBackend` (default: `ClaudeCodeBackend`).
- Backend resolution logic: per-stage YAML → workflow default → `.atlas.toml [backend]` → hard default `claude`.
- Unit tests for both backends (argv construction, result parsing).
- Integration test: at least one stage dispatches to `AntigravityBackend` with mocked subprocess.
- Document per-CLI auth requirements and the experimental status of `agy` support.

**Exit criteria:**
- Existing dev pipeline runs unchanged (ClaudeCodeBackend is the default).
- A workflow YAML with `backend: agy` on one stage dispatches correctly (mocked).
- Backend resolution order verified by test.
- `agy` auth failure produces a clear error, not a silent hang.

---

### Phase 4 — Second-brain trigger skill (future, not scoped in this TRD)

**Goal:** An ai-workx skill invoked from a second-brain vault session that shells `atlas run --workflow job ...` and routes results back as markdown.

**Dependencies:** Phase 2 + Phase 3.

**Engineering scope summary:** Out of scope for this TRD. Noted here for sequencing context. Scope lives in ai-workx, not atlas.

---

## Appendix A — Codebase seam inventory

Comprehensive list of hardcoded references that Phase 1 must address, grounded in the current source:

| File | Line(s) | Hardcoded reference | v2 action |
|---|---|---|---|
| `stages.py` | all | `StageName(StrEnum)`, `GateLabel(StrEnum)`, `STAGES` tuple, `STAGE_BY_NAME` dict | Replace with YAML loader output. Delete enums. |
| `orchestrator.py` | `_DEFAULT_TIMEOUT_S` | 7 stage-name string keys | Move into `dev.yaml` or config; loader merges. |
| `orchestrator.py` | `step()` | `if stage.name == StageName.CODE_GEN` | Replace with `if stage.isolate` |
| `orchestrator.py` | `step()` | `if stage.gate_label == GateLabel.GATE_COMMIT` | Replace with `if stage.gate_is_async` |
| `orchestrator.py` | `_validate_routing_fixture()` | Hardcoded path to `routing_ground_truth.json` | Make dev-workflow-only or per-workflow. |
| `plugin_resolver.py` | `PLUGIN_COMMANDS` | 7 tool-to-command entries | Retain as dev-pipeline defaults; merge with YAML tool field. |
| `state.py` | `create_tasks_md()` | Imports `STAGES`; hardcodes initial phase as `research` | Accept `tuple[StageSpec, ...]` parameter; initial phase = first stage name. |
| `state.py` | `first_unchecked()` | Returns `StageName(name)` | Return `str`. |
| `post_commit_hook.py` | `run()` | `metric = "gate_commit"` | Parameterize from `StageSpec.gate_label` of the `gate_is_async` stage. |

## Appendix B — Cross-references

- Engine plan: [`yaml-driven-workflows-analysis.md`](../../dev/archive/yaml-workflow-engine-design-notes/yaml-driven-workflows-analysis.md)
- Job workflow scope: [`job-workflow-scope.md`](../../dev/archive/yaml-workflow-engine-design-notes/job-workflow-scope.md)
- CLI dispatch decision: [`cli-backend-dispatch.md`](../../dev/archive/yaml-workflow-engine-design-notes/cli-backend-dispatch.md)
- Headless CLI reference: [`headless-clis-reference.md`](../1_product_and_research/headless-clis-reference.md)
- v1 TRD: [`TRD.md`](./TRD.md)
- v1 System Design: [`system_design.md`](./system_design.md)
- v1 PRD: [`PRD.md`](../1_product_and_research/PRD.md)
- atlas runner seam: `src/atlas/orchestrator.py` (`StageRunner`, `SubprocessStageRunner`, `Pipeline`)
- Hardcoded stage definitions: `src/atlas/stages.py` (`StageName`, `GateLabel`, `STAGES`)
- Plugin routing: `src/atlas/plugin_resolver.py` (`PLUGIN_COMMANDS`, `resolve()`)
- State management: `src/atlas/state.py` (`StateStore`, `create_tasks_md`, `first_unchecked`)
