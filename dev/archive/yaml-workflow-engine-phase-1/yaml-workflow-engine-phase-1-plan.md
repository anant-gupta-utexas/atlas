# TRS — YAML Workflow Engine, Phase 1 (Engine Generalization)

**Project:** atlas — v2 YAML-driven gated-workflow engine
**Component:** `workflow_loader.py` (new) + `stages.py`, `orchestrator.py`, `state.py`, `plugin_resolver.py`, `cli.py`, `plumb_adapter.py` (refactored)
**Status:** Draft, pre-implementation
**Last reviewed:** 2026-06-29
**Grounds on:** [TRD-v2](../../../docs/2_architecture/TRD-v2.md) §3, §10, §14 (Phase 1), Appendix A; [v1 TRD](../../../docs/2_architecture/TRD.md); [PRD](../../../docs/1_product_and_research/PRD.md)

> This TRS details exactly one TRD phase — Phase 1 — into a flat task list. It does not re-plan releases (PRD-owned) or re-sequence phases (TRD-owned). Phase 2 (job workflow) and Phase 3 (CLI backend dispatch) are out of scope here and get their own TRS when picked up.

---

## Phase Summary

**TRD phase:** Phase 1 — Engine generalization (TRD-v2 §14).
**PRD release(s) delivered:** None directly — the TRD-v2 preamble notes the PRD's future-releases table predates the YAML-workflow analysis and "a formal PRD update should follow." Phase 1 ships no new user-facing release; it is the foundation Phase 2 (job workflow, v2.1) and Phase 3 (CLI backend, v2.2) build on. Per TRD-v2 §11, this phase's tag is **v2.0 — engine generalization + dev-pipeline parity**.
**Goal (verbatim from TRD-v2 §14):** "Generalize atlas from a single hardcoded dev pipeline to a YAML-driven multi-workflow engine, with full backward compatibility." No user-facing new workflow yet — the dev pipeline runs identically, just loaded from YAML.

---

## 1. Overview & Scope

### In scope

This TRS covers every seam TRD-v2 Appendix A lists as required for Phase 1:

- New module `workflow_loader.py` — YAML → `tuple[StageSpec, ...]` with validation.
- `dev.yaml` — the v1 hardcoded `STAGES` tuple extracted to a shipped, in-package YAML file.
- `stages.py` — `StageName`/`GateLabel` loosened from `StrEnum` to validated `str`; `StageSpec` gains `isolate`, `gate_is_async`, `backend` fields.
- `orchestrator.py` — three hardcoded conditionals refactored to data-driven `StageSpec` properties; `_validate_routing_fixture()` made dev-workflow-only.
- `cli.py` — `--workflow` / `--workflow-file` flags; workflow resolution wired into `_make_pipeline()`.
- `state.py` — `create_tasks_md()` accepts a `tuple[StageSpec, ...]`; `## current` block gains a `workflow` field; `first_unchecked()` returns `str`.
- `plugin_resolver.py` — tool resolution merges YAML `tool` field with `.atlas.toml` overrides; `PLUGIN_COMMANDS` retained as dev-pipeline-only defaults.
- `plumb_adapter.py` (new, or extension of `plumb_io.py`) — metric-name namespacing (`<workflow>.<gate_label>` for non-dev workflows; v1-era names preserved for dev).
- `post_commit_hook.py` — `metric = "gate_commit"` parameterized from the `gate_is_async` stage's `gate_label` (TRD-v2 Appendix A).

### Out of scope (deferred to later phases / later TRS)

- `job.yaml` and any content-pipeline integration (Phase 2).
- `CliBackend` Protocol, `ClaudeCodeBackend`, `AntigravityBackend` (Phase 3). Phase 1's `backend: str | None` field on `StageSpec` is added and *threaded through* (so Phase 3 has a stable seam) but **not consumed** — `SubprocessStageRunner` continues to always invoke `claude -p` in Phase 1, ignoring the field's value.
- The second-brain trigger skill (Phase 4 — out of TRD-v2 scope entirely).
- Any plumb schema change. TRD-v2 §7 and §12 #3 are explicit: no `SCHEMA_VERSION` bump.

### Why this scope

Phase 1 is the only phase that touches the engine's load-bearing seam (how a pipeline's shape gets from disk into `Pipeline`). Every other Phase 1 file change (`cli.py`, `state.py`, `plugin_resolver.py`, hook) exists only to consume `workflow_loader.py`'s output instead of the hardcoded `STAGES` import. Pinning this down first is what makes Phase 2 "a YAML file, not a code change" (TRD-v2 §2 Objective 2).

---

## 2. Requirements Summary

### Functional (from TRD-v2 §3, mapped to FR IDs for traceability)

- **FR-1** (§3.1) — A workflow YAML parses into `tuple[StageSpec, ...]`, identical to what `Pipeline` already consumes from the hardcoded tuple.
- **FR-2** (§3.1) — Loader validates: `span_kind` ∈ plumb's six (`llm`, `tool`, `subagent`, `handoff`, `plan`, `verify`); stage `name` uniqueness; `gate` label uniqueness; `name` matches `[a-z][a-z0-9_]*`; if `isolate: true`, git available + repo clean.
- **FR-3** (§3.2) — Workflow resolution order: `--workflow-file <path>` > `--workflow <name>` (search `.atlas/workflows/` → `~/.atlas/workflows/` → built-in) > default `dev`.
- **FR-4** (§3.3) — `StageName`/`GateLabel` become validated `str`, not `StrEnum`. Three hardcoded conditionals replaced by `StageSpec.isolate`, `StageSpec.gate_is_async`, and the already-data-driven `gate_label is None`.
- **FR-5** (§3.5) — Tool resolution order: `.atlas.toml [plugin_commands.<tool>]` > YAML `tool` field > `PLUGIN_COMMANDS` (dev-only fallback).
- **FR-6** (§3.6) — `create_tasks_md()` takes a `tuple[StageSpec, ...]` parameter; `## current` block includes `workflow: <name>`; `first_unchecked()` returns `str`; resume re-reads `workflow` from `tasks.md` and reloads the matching YAML.
- **FR-7** (§3.7) — Metric names namespaced `<workflow>.<gate_label>` for non-dev workflows; v1-era bare names (`gate_research`, etc.) preserved for `dev`.
- **FR-8** (§5 Reliability) — `dev.yaml` loaded through the YAML loader produces a `StageSpec` tuple behaviorally identical to v1's hardcoded `STAGES` (modulo new boolean fields defaulting to match v1 behavior: `isolate=True` only for `code_gen`, `gate_is_async=True` only for the gate-4/`gate_commit` stage).

### Non-functional (from TRD-v2 §4)

- **NFR-1** — YAML load time < 50 ms for ≤ 20 stages (loader only, excludes disk I/O).
- **NFR-2** — `--workflow <name>` resolution < 100 ms through the search path.
- **NFR-3** — No regression on v1 targets: `atlas status` < 500 ms, post-commit hook < 1 s.
- **NFR-4** — `yaml.safe_load()` only; reject unknown top-level YAML keys.
- **NFR-5** — Workflow YAML is loaded once at `Pipeline` construction and frozen for the run; mid-run edits don't affect a running pipeline.
- **NFR-6** — Unknown stage names on resume: log a warning and skip (existing `first_unchecked()` try/except behavior preserved, now operating on `str` instead of an enum).
- **NFR-7** — `mypy src` and `ruff check`/`ruff format` pass (carried from v1 CI gates, TRD-v2 §10).
- **NFR-8** — Engine code (orchestrator + loader + backends + state) stays ≤ ~600 lines total (TRD-v2 §5, §13 #9). Phase 1 owns orchestrator + loader + state; Phase 3 adds backends later — Phase 1's own budget is the ~600 minus whatever Phase 3 needs, so Phase 1 should target keeping `workflow_loader.py` lean (TRD-v2 estimates the whole engine, not per-phase, so this TRS treats ≤ 600 as a soft ceiling to watch, not a hard per-file gate).

---

## 3. Detailed Component Design

### 3.1 Module structure (post–Phase 1)

```
src/atlas/
├── __init__.py
├── cli.py                 # + --workflow / --workflow-file flags
├── orchestrator.py        # 3 conditionals data-driven; STAGES import removed
├── workflow_loader.py      # NEW — YAML → tuple[StageSpec, ...]
├── stages.py               # StageName/GateLabel → str; StageSpec gains 3 fields
├── state.py                 # create_tasks_md/first_unchecked workflow-aware
├── plugin_resolver.py       # tool resolution merges YAML tool field
├── plumb_adapter.py         # NEW (or plumb_io.py extended) — metric namespacing
├── post_commit_hook.py      # metric parameterized from gate_is_async stage
├── worktree.py               # unchanged
├── config.py                  # unchanged (already merges .atlas.toml)
└── workflows/
    └── dev.yaml             # NEW — in-package default, extracted from old STAGES
```

`workflow_loader.py` is the only new module with logic; `workflows/dev.yaml` is a new data file (packaged via `[tool.hatch.build.targets.wheel] packages = ["src/atlas"]`, which already includes non-`.py` files under the package directory — confirm `MANIFEST`/hatchling include rules during T1.1, see Resolved Decision #1).

### 3.2 Data structures

```python
# src/atlas/stages.py
from __future__ import annotations

import re
from dataclasses import dataclass

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# plumb's closed set (TRD-v2 §3.1, §7) — validated against at load time.
SPAN_KINDS: frozenset[str] = frozenset({"llm", "tool", "subagent", "handoff", "plan", "verify"})


@dataclass(frozen=True)
class StageSpec:
    index: int
    name: str                    # was StageName (StrEnum); validated [a-z][a-z0-9_]*
    span_kind: str                # constrained to SPAN_KINDS
    tool: str
    gate_label: str | None        # was GateLabel (StrEnum) | None
    gate_index: int | None
    isolate: bool = False         # NEW — replaces `if stage.name == StageName.CODE_GEN`
    gate_is_async: bool = False   # NEW — replaces `if stage.gate_label == GateLabel.GATE_COMMIT`
    backend: str | None = None    # NEW — threaded through; unused until Phase 3
    timeout_s: int | None = None  # NEW — per-stage subprocess timeout; None → orchestrator default (Decision #5)


# StageName / GateLabel StrEnums are DELETED in this phase (TRD-v2 §3.3).
# Any remaining import of `atlas.stages.StageName` or `GateLabel` is a Phase 1
# regression — grep both names to zero before exit (see Task list, T1.9).
```

```python
# src/atlas/workflow_loader.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atlas.stages import SPAN_KINDS, StageSpec

_ALLOWED_TOP_LEVEL_KEYS = {"name", "default_backend", "stages"}
_ALLOWED_STAGE_KEYS = {
    "name", "span_kind", "tool", "gate", "isolate", "gate_is_async", "backend", "timeout_s",
}


class WorkflowValidationError(Exception):
    """Raised on any YAML schema/content violation. Carries a human-readable,
    line-aware message — never a raw traceback (TRD-v2 §4 Usability)."""


@dataclass(frozen=True)
class LoadedWorkflow:
    name: str
    default_backend: str | None
    stages: tuple[StageSpec, ...]


def load_workflow_file(path: Path) -> LoadedWorkflow:
    """Parse one workflow YAML file into a LoadedWorkflow.

    Uses yaml.safe_load() only (NFR-4). Raises WorkflowValidationError on:
    unknown top-level/stage keys, invalid span_kind, duplicate stage name,
    duplicate gate label, stage name not matching [a-z][a-z0-9_]*, isolate=true
    with git unavailable/repo dirty (checked lazily — see §6.3).
    """


def resolve_workflow(
    *, workflow_file: Path | None, workflow_name: str | None, repo_root: Path
) -> LoadedWorkflow:
    """Implements the §3.2 resolution order:
    1. workflow_file (literal path) — highest priority.
    2. workflow_name — search .atlas/workflows/<name>.yaml → ~/.atlas/workflows/<name>.yaml
       → built-in src/atlas/workflows/<name>.yaml.
    3. Neither given — default "dev" via step 2's search path.
    Raises WorkflowNotFoundError naming every location checked.
    """


class WorkflowNotFoundError(Exception):
    """Raised by resolve_workflow when no candidate path exists."""
```

**Why `LoadedWorkflow` instead of returning a bare tuple.** `Pipeline` ultimately wants `tuple[StageSpec, ...]` (matches v1's contract per TRD-v2 §3.1 loader contract). But `cli.py` also needs the workflow `name` (for the `## current` block's `workflow:` field, FR-6) and `default_backend` (consumed by Phase 3, threaded now for the same reason `StageSpec.backend` is threaded now). A thin wrapper dataclass avoids `Pipeline` taking three loosely-related positional args.

### 3.3 `Pipeline` constructor change

```python
# src/atlas/orchestrator.py — diff in shape, not full rewrite
class Pipeline:
    def __init__(
        self,
        *,
        repo_root: Path,
        state: StateStore,
        plumb: PlumbIO,
        runner: StageRunner,
        prompter: GatePrompter,
        stages: tuple[StageSpec, ...],     # NEW — replaces module-level STAGES import
        workflow_name: str = "dev",         # NEW — for tasks.md `workflow:` field + metric namespacing
        worktree: WorktreeManager | None = None,
        commit_wait_timeout_s: int = _DEFAULT_COMMIT_WAIT_TIMEOUT_S,
    ) -> None:
        ...
        self._stages = stages
        self._stage_by_name: dict[str, StageSpec] = {s.name: s for s in stages}
        self._workflow_name = workflow_name
        self._validate_routing_fixture()  # now dev-workflow-only; see §6.4
```

All internal references to the module-level `STAGES` / `STAGE_BY_NAME` constants (`stages.py`) become references to `self._stages` / `self._stage_by_name`. All references to `STAGES[stage.index + 1]` become `self._stages[stage.index + 1]`.

### 3.4 The three data-driven conditional replacements (Appendix A, verbatim mapping)

| Location | v1 (current) | Phase 1 (target) |
|---|---|---|
| `orchestrator.py::step()` — worktree creation | `if stage.name == StageName.CODE_GEN and self._worktree is not None and ctx.worktree_path is None:` | `if stage.isolate and self._worktree is not None and ctx.worktree_path is None:` |
| `orchestrator.py::step()` — async-hook gate | `if stage.gate_label == GateLabel.GATE_COMMIT:` | `if stage.gate_is_async:` |
| `orchestrator.py::step()` — no-gate stage | `if stage.gate_label is None:` | unchanged — already data-driven |

`dev.yaml`'s `code_gen` stage sets `isolate: true`; its gate-4 stage (the one currently tagged `GateLabel.GATE_COMMIT`) sets `gate_is_async: true`. This is the parity contract for FR-8.

---

## 4. API Specifications

No network API (unchanged from v1). The surface is the CLI flags and the loader's two public functions.

### 4.1 CLI surface additions

```
atlas run "<task>" [--workflow <name>] [--workflow-file <path>] [--slug ...] [--auto-approve]
atlas resume [--auto-approve]   # unchanged signature; workflow re-read from tasks.md
atlas status                     # unchanged signature; output gains a `workflow:` line
```

| Flag | Type | Default | Resolution priority |
|---|---|---|---|
| `--workflow-file` | `Path` | unset | 1 (highest) |
| `--workflow` | `str` | unset | 2 |
| (neither) | — | `"dev"` | 3 |

Mutually exclusive: if both `--workflow` and `--workflow-file` are passed, `--workflow-file` wins silently per §3.2's stated order (no error — TRD-v2 doesn't ask for one, and "highest priority" already disambiguates). Settled as Resolved Decision #3 (silent priority for Phase 1; promote to a hard usage error only if it causes real confusion).

### 4.2 Error surface (TRD-v2 §4 Usability)

| Condition | Behavior |
|---|---|
| `--workflow nonexistent` | Exit non-zero. Message lists every path checked: `.atlas/workflows/nonexistent.yaml`, `~/.atlas/workflows/nonexistent.yaml`, `<pkg>/workflows/nonexistent.yaml`. |
| Malformed YAML (unknown span_kind, dup name, dup gate, bad name format, unknown top-level/stage key) | Exit non-zero. Message names the offending field + value — no raw traceback, no Python stack frame in user-facing output. `WorkflowValidationError.__str__` is the message; `cli.py` catches it and `typer.echo(str(exc), err=True)`. |
| `isolate: true` stage, dirty repo or no git | Same pattern as v1's existing worktree guard (`WorktreeManager` already raises `WorktreeError`); loader validates git *availability*, `WorktreeManager` still validates repo-clean state at worktree-creation time (lazy, not at load time — see §6.3 for why). |

---

## 5. Database Design

Unchanged from v1: atlas owns no schema. All structured writes go through plumb's Python API (TRD-v2 §7: "No plumb schema migration is required for v2"). The only Phase 1–relevant change is **what string** atlas passes as `metric_name` — covered in §6.5 (metric namespacing), not a schema concern.

---

## 6. Algorithm & Logic Design

### 6.1 `load_workflow_file` (pseudocode)

```
function load_workflow_file(path) -> LoadedWorkflow:
    raw = yaml.safe_load(path.read_text())   # NEVER yaml.load()
    if raw is None or not isinstance(raw, dict):
        raise WorkflowValidationError(f"{path}: empty or non-mapping YAML document")

    unknown_top = set(raw.keys()) - _ALLOWED_TOP_LEVEL_KEYS
    if unknown_top:
        raise WorkflowValidationError(f"{path}: unknown top-level key(s): {sorted(unknown_top)}")

    name = raw.get("name")
    if not name or not _NAME_RE.match(name):
        raise WorkflowValidationError(f"{path}: workflow 'name' missing or invalid: {name!r}")

    default_backend = raw.get("default_backend")  # str | None, unvalidated in Phase 1 (Phase 3 concern)

    raw_stages = raw.get("stages")
    if not raw_stages or not isinstance(raw_stages, list):
        raise WorkflowValidationError(f"{path}: 'stages' must be a non-empty list")

    seen_names: set[str] = set()
    seen_gates: set[str] = set()
    stages: list[StageSpec] = []
    gate_idx = 0

    for i, raw_stage in enumerate(raw_stages):
        unknown_stage_keys = set(raw_stage.keys()) - _ALLOWED_STAGE_KEYS
        if unknown_stage_keys:
            raise WorkflowValidationError(f"{path}: stage[{i}] unknown key(s): {sorted(unknown_stage_keys)}")

        stage_name = raw_stage.get("name")
        if not stage_name or not _NAME_RE.match(stage_name):
            raise WorkflowValidationError(f"{path}: stage[{i}] invalid name: {stage_name!r}")
        if stage_name in seen_names:
            raise WorkflowValidationError(f"{path}: duplicate stage name {stage_name!r}")
        seen_names.add(stage_name)

        span_kind = raw_stage.get("span_kind")
        if span_kind not in SPAN_KINDS:
            raise WorkflowValidationError(
                f"{path}: stage[{i}] {stage_name!r} span_kind {span_kind!r} "
                f"not one of {sorted(SPAN_KINDS)}"
            )

        tool = raw_stage.get("tool")
        if not tool:
            raise WorkflowValidationError(f"{path}: stage[{i}] {stage_name!r} missing 'tool'")

        gate_label = raw_stage.get("gate")  # may be absent or YAML null -> None
        gate_index: int | None = None
        if gate_label is not None:
            if gate_label in seen_gates:
                raise WorkflowValidationError(f"{path}: duplicate gate label {gate_label!r}")
            seen_gates.add(gate_label)
            gate_index = gate_idx
            gate_idx += 1

        isolate = bool(raw_stage.get("isolate", False))
        backend = raw_stage.get("backend")  # str | None; unvalidated in Phase 1 (Resolved Decision #4)

        # gate_is_async IS an allowed YAML stage key, defaulting to false
        # (Resolved Decision #2 — confirmed by the TRD-v2 author: §3.1's example
        # YAML is illustrative, not exhaustive). A stage sets it true to declare
        # its gate is written asynchronously by the post-commit hook, not the
        # orchestrator. dev.yaml's gate-4 (gate_commit) stage sets it true.
        gate_is_async = bool(raw_stage.get("gate_is_async", False))

        # timeout_s: optional per-stage subprocess timeout (Resolved Decision #5).
        # None → the orchestrator falls back to _DEFAULT_TIMEOUT_S for this stage.
        timeout_s = raw_stage.get("timeout_s")
        if timeout_s is not None and (not isinstance(timeout_s, int) or timeout_s <= 0):
            raise WorkflowValidationError(
                f"{path}: stage[{i}] {stage_name!r} timeout_s must be a positive int, got {timeout_s!r}"
            )

        stages.append(StageSpec(
            index=i, name=stage_name, span_kind=span_kind, tool=tool,
            gate_label=gate_label, gate_index=gate_index,
            isolate=isolate, gate_is_async=gate_is_async, backend=backend,
            timeout_s=timeout_s,
        ))

    return LoadedWorkflow(name=name, default_backend=default_backend, stages=tuple(stages))
```

> **Resolved with the TRD-v2 author (2026-06-29):** §3.1's example YAML omits both `gate_is_async` and `default_backend`, yet §3.3/§3.4 require both — the author confirmed the example block is illustrative, not exhaustive. `gate_is_async: true | false` is an allowed stage key (default `false`). The same TRS pass added `timeout_s` per Resolved Decision #5 (pulling the Appendix A `_DEFAULT_TIMEOUT_S` generalization into Phase 1). See the "Resolved Decisions" section for both.

### 6.2 `resolve_workflow` (pseudocode)

```
function resolve_workflow(workflow_file, workflow_name, repo_root) -> LoadedWorkflow:
    if workflow_file is not None:
        if not workflow_file.exists():
            raise WorkflowNotFoundError(f"--workflow-file path not found: {workflow_file}")
        return load_workflow_file(workflow_file)

    name = workflow_name or "dev"
    candidates = [
        repo_root / ".atlas" / "workflows" / f"{name}.yaml",
        Path.home() / ".atlas" / "workflows" / f"{name}.yaml",
        _PACKAGE_WORKFLOWS_DIR / f"{name}.yaml",   # importlib.resources or __file__-relative
    ]
    for c in candidates:
        if c.exists():
            return load_workflow_file(c)
    raise WorkflowNotFoundError(
        f"Workflow {name!r} not found. Checked: " + ", ".join(str(c) for c in candidates)
    )
```

### 6.3 Why `isolate: true` git/clean validation is split (load time vs. worktree-creation time)

TRD-v2 §3.1 says: *"If `isolate: true`, validate that git is available and the repo is clean (same check as v1's worktree guard)."* But `WorktreeManager` (unchanged, out of scope) already performs the repo-clean check lazily, at `create()` time, because the repo could become dirty between `Pipeline` construction and stage execution (other stages may run first). Re-validating "is git available" at *load* time (cheap, static) while leaving "is the repo currently clean" to `WorktreeManager.create()` (must be checked at the moment of truth) avoids a stale-check TOCTOU bug. The loader's `isolate` validation in Phase 1 is therefore: **git binary on PATH** only. Repo-clean stays exactly where v1 already checks it.

### 6.4 `_validate_routing_fixture()` becomes dev-only

```
function _validate_routing_fixture(self):
    if self._workflow_name != "dev":
        return   # routing_ground_truth.json only describes the dev pipeline
    # ... existing logic, unchanged, but reads self._stages instead of STAGES
```

### 6.5 Metric-name namespacing (plumb_adapter.py)

```
function namespaced_metric(workflow_name: str, gate_label: str) -> str:
    if workflow_name == "dev":
        return gate_label                       # v1-era bare names preserved
    return f"{workflow_name}.{gate_label}"        # e.g. "job.gate_shortlist"
```

Called at the one site in `orchestrator.py::step()` that currently does `metric=stage.gate_label.value` (now `metric=stage.gate_label`, already a `str`) — wrap it: `metric=namespaced_metric(self._workflow_name, stage.gate_label)`. This is a **convention enforced at the atlas layer**, not a plumb schema change (TRD-v2 §3.7, §7).

### 6.6 `post_commit_hook.py` metric parameterization

v1 hardcodes `metric = "gate_commit"`. Phase 1 must parameterize this from the `gate_is_async` stage's `gate_label`, per Appendix A. The hook runs in a **separate process** with no access to `Pipeline` or the loaded `StageSpec` tuple — it only reads `.atlas/current-run`. Resolution: extend `.atlas/current-run`'s file format (already a `state.py`-owned multi-line flat file — see `write_current_run` in `state.py:99`) with a 5th line carrying the async-gate's metric name, written once at `Pipeline` construction / stage-5-entry time. The hook reads line 5 if present, else falls back to the literal `"gate_commit"` (preserves dev-pipeline behavior with zero `state.py` callers needing to change for the common case).

```
# state.py — write_current_run gains an optional async_gate_metric param
def write_current_run(self, run_id, slug, worktree_path=None, code_gen_span_id=None,
                        async_gate_metric: str | None = None) -> None:
    ...
    body += f"{async_gate_metric or 'gate_commit'}\n"   # line 5, always written once line 4 exists
```

```
# post_commit_hook.py::run()
lines = current_run_path.read_text().splitlines()
run_id = lines[0].strip()
metric = lines[4].strip() if len(lines) >= 5 and lines[4].strip() else "gate_commit"
```

This keeps the hook dependency-free (no YAML parsing in the hook subprocess — consistent with v1's design philosophy that the hook is a thin, best-effort parser, not a full atlas runtime).

### 6.7 Per-stage timeout resolution (Resolved Decision #5 — Appendix A `_DEFAULT_TIMEOUT_S` generalization)

TRD-v2 Appendix A lists `orchestrator.py::_DEFAULT_TIMEOUT_S` (7 stage-name string keys) as a v2 seam to generalize: "move into `dev.yaml` or config; loader merges." Decision #5 pulls this into Phase 1. The timeout for a stage now resolves in priority order:

```
function resolve_timeout(stage: StageSpec, timeout_overrides: dict[str, int]) -> int:
    # 1. .atlas.toml per-stage override (existing mechanism, highest priority)
    if stage.name in timeout_overrides:
        return timeout_overrides[stage.name]
    # 2. workflow YAML `timeout_s` field on the stage (NEW)
    if stage.timeout_s is not None:
        return stage.timeout_s
    # 3. orchestrator hard default by stage name (existing _DEFAULT_TIMEOUT_S)
    return _DEFAULT_TIMEOUT_S.get(stage.name, _GLOBAL_FALLBACK_TIMEOUT_S)
```

**Why `_DEFAULT_TIMEOUT_S` is retained, not deleted.** It moves from "the only source of timeouts" to "the final fallback when neither config nor YAML specifies one." This preserves v1 behavior exactly for any stage that omits `timeout_s` — including all of `dev.yaml`'s stages, which **do not** set `timeout_s` (they inherit the v1 defaults via fallback, keeping FR-8 parity intact). The one robustness improvement over v1: `_DEFAULT_TIMEOUT_S` is keyed by hardcoded dev-stage names, so a non-dev workflow whose stage names aren't in the dict previously had no defined behavior — `resolve_timeout` now uses `_GLOBAL_FALLBACK_TIMEOUT_S` (a new module constant, e.g. 600s) for any stage name absent from `_DEFAULT_TIMEOUT_S`, so non-dev workflows that omit `timeout_s` get a sane default instead of a `KeyError`.

**Call-site change.** `SubprocessStageRunner.run()` currently does `self._timeout_overrides.get(stage.name.value, _DEFAULT_TIMEOUT_S[stage.name.value])` (orchestrator.py:487-489). It becomes a call to `resolve_timeout(stage, self._timeout_overrides)` — folding the YAML field into the existing two-level (override → default) lookup as a new middle tier. `stage.name` is already a plain `str` post-T1.5, so the `.value` accesses drop out.

**Dev-pipeline parity note.** `dev.yaml` deliberately ships with **no** `timeout_s` fields. This is intentional: it proves the fallback path (tier 3) reproduces v1 timeouts exactly, and keeps the parity test (T1.4) asserting `timeout_s is None` on all 7 dev stages. A separate loader test (`test_load_stage_timeout_s`) covers the tier-2 path with a synthetic workflow that *does* set `timeout_s`.

---

## 7. Error Handling & Edge Cases

| Case | Handling |
|---|---|
| YAML file has unknown top-level key | `WorkflowValidationError` at load time, message names the key. |
| YAML file has unknown stage key | Same, names stage index + key. |
| `span_kind` not in plumb's six | `WorkflowValidationError`, names stage + offending value + the valid set. |
| Two stages share a `name` | `WorkflowValidationError`, names both. |
| Two stages share a `gate` label | `WorkflowValidationError`, names both. |
| Stage `name` fails `[a-z][a-z0-9_]*` | `WorkflowValidationError`, names stage index + value. |
| `--workflow <name>` resolves to nothing on any search path | `WorkflowNotFoundError`, lists all 3 candidate paths. |
| `--workflow-file <path>` doesn't exist | `WorkflowNotFoundError`, names the literal path. |
| `isolate: true` but `git` not on PATH | `WorkflowValidationError` at load time (cheap static check, §6.3). |
| `timeout_s` present but not a positive int (e.g. `0`, `-5`, `"600"`, `1.5`) | `WorkflowValidationError` at load time, names stage + offending value (§6.1). |
| Stage name absent from `_DEFAULT_TIMEOUT_S` and no `timeout_s`/override given (a non-dev workflow stage) | Not an error — `resolve_timeout` returns `_GLOBAL_FALLBACK_TIMEOUT_S` (§6.7). v1 would have raised `KeyError`; this is a robustness improvement. |
| Resume: `tasks.md` names a workflow whose YAML has since been edited/removed | `first_unchecked()` already skips unknown stage names via try/except (NFR-6); extend the except clause to also catch `WorkflowNotFoundError` at the `resolve_workflow()` call inside `resume()`, log a warning, and surface a clear CLI error rather than crash mid-resume. Resume does **not** silently fall back to `dev`. |
| Resume: workflow YAML edited between two `step()` calls in the same process | NFR-5 — loaded once at `Pipeline` construction, frozen. Mid-run file edits are inert until the next fresh `Pipeline` construction (i.e., next CLI invocation). |
| `dev.yaml` missing from the installed package (corrupted install) | Same `WorkflowNotFoundError` path as any other missing workflow — no special-cased fallback to the old hardcoded tuple (TRD-v2 §14 says the hardcoded tuple is deleted only after YAML-loaded parity is proven, i.e., it's gone by Phase 1 exit, not kept as a runtime fallback). |

**Retry/fallback strategy:** None of these are transient failures — all are deterministic load-time/resolve-time errors. No retry logic is appropriate (consistent with v1's existing pattern: gate input retries 3x because it's interactive user error; YAML errors are not).

---

## 8. Dependencies & Interfaces

| Dependency | Direction | Notes |
|---|---|---|
| `pyyaml >= 6.0` | new, `pyproject.toml [project.dependencies]` | `yaml.safe_load()` only (NFR-4). |
| `workflow_loader.py` → `stages.py` | internal | Imports `StageSpec`, `SPAN_KINDS`. |
| `orchestrator.py` → `workflow_loader.py` | internal | `Pipeline` no longer imports `STAGES`/`STAGE_BY_NAME` from `stages.py`; receives `stages: tuple[StageSpec, ...]` at construction (resolved upstream by `cli.py`). |
| `cli.py` → `workflow_loader.py` | internal | `_make_pipeline()` calls `resolve_workflow(...)` before constructing `Pipeline`. |
| `state.py` → `workflow_loader.py` | internal | `resume()` reads `workflow:` from `tasks.md`, calls `resolve_workflow(workflow_name=..., ...)` to reconstruct the `StageSpec` tuple. |
| `plugin_resolver.py` → workflow YAML `tool` field | internal | `resolve()` signature gains the per-stage `tool` value already embedded in `StageSpec.tool` (no separate lookup — the YAML `tool` field *is* `StageSpec.tool`, already the primary source per §3.5 resolution order item 2; `PLUGIN_COMMANDS` becomes the item-3 fallback, used only when a workflow's `tool` string isn't itself a directly-invokable command — `RAW:`-prefixed tools bypass resolution entirely, unchanged from v1). |

---

## 9. Security Considerations

Carried from TRD-v2 §4 Security, applied concretely to Phase 1's surface:

- **`yaml.safe_load()` exclusively.** No `yaml.load()`, no custom constructors, no `!!python/object` tags. Enforced by code review + a dedicated test (`test_loader_rejects_unsafe_yaml_tags`).
- **Unknown top-level/stage keys rejected**, not silently ignored — prevents a workflow author from smuggling in a future-reserved key that a later atlas version might interpret unexpectedly (defense in depth, not a current threat).
- **Workflow file trust boundary** (TRD-v2 §4): a workflow YAML's `tool: "RAW:<prompt>"` is equivalent to the user typing a command. Phase 1 does not add any sandboxing — this is documented behavior, not a gap. The `README`/docstring on `workflow_loader.py` states this explicitly per TRD-v2's instruction to "Document this explicitly."
- **No new subprocess surface in Phase 1.** `SubprocessStageRunner` is unchanged (still only invokes `claude -p`); the `backend` field is parsed and stored but not dispatched on, so there is no new code path that shells out differently based on YAML content yet.
- **Path handling in `resolve_workflow`.** `--workflow <name>` is interpolated into a filename (`f"{name}.yaml"`). `name` is validated against `_NAME_RE` (`[a-z][a-z0-9_]*`) *before* path construction in the CLI-args case — but note `--workflow` arrives from `sys.argv`, not from a YAML body, so it must be validated independently of `load_workflow_file`'s internal validation (which only runs after a file is found). Add an explicit `_NAME_RE` check on `workflow_name` at the top of `resolve_workflow()` to prevent path traversal via e.g. `--workflow ../../etc/passwd` before any path join happens.

---

## 10. Testing Strategy

Per TRD-v2 §10 coverage targets: `workflow_loader.py` ≥ 90%, existing modules unchanged targets, overall `pytest --cov-fail-under=80` (from `pyproject.toml`, carried forward).

### Unit tests (new file: `tests/unit/test_workflow_loader.py`)

| Test | Validates |
|---|---|
| `test_load_valid_workflow_yaml` | Well-formed YAML → expected `tuple[StageSpec, ...]`, correct `index`/`gate_index` enumeration. |
| `test_load_rejects_invalid_span_kind` | `span_kind: "research"` → `WorkflowValidationError`. |
| `test_load_rejects_duplicate_stage_name` | Two stages named `score_fit` → `WorkflowValidationError`. |
| `test_load_rejects_duplicate_gate_label` | Two stages with `gate: gate_done` → `WorkflowValidationError`. |
| `test_load_rejects_bad_name_format` | Stage name `Score-Fit` (uppercase, hyphen) → rejected; `score_fit2` accepted. |
| `test_load_rejects_unknown_top_level_key` | `extra_field: foo` at top level → rejected. |
| `test_load_rejects_unknown_stage_key` | `retries: 3` on a stage → rejected. |
| `test_loader_rejects_unsafe_yaml_tags` | A YAML doc using `!!python/object:os.system` → rejected (proves `safe_load` is actually used, not `load`). |
| `test_load_isolate_requires_git_on_path` | `isolate: true` with `git` missing from `PATH` (mocked) → `WorkflowValidationError`. |
| `test_load_stage_timeout_s` | A stage with `timeout_s: 900` parses to `StageSpec.timeout_s == 900`; a stage omitting it parses to `timeout_s is None`. |
| `test_load_rejects_bad_timeout_s` | `timeout_s: 0`, `-5`, `"600"`, and `1.5` each → `WorkflowValidationError` (positive int required). |
| `test_resolve_timeout_priority` | `.atlas.toml` override > YAML `timeout_s` > `_DEFAULT_TIMEOUT_S` > `_GLOBAL_FALLBACK_TIMEOUT_S` for an unknown stage name (§6.7). |
| `test_dev_pipeline_parity` | `dev.yaml` loaded via `load_workflow_file` produces a `StageSpec` tuple equal to a hand-written expected tuple matching v1's old `STAGES` (7 stages, same tool/span_kind/gate_label/gate_index values, `isolate=True` only on `code_gen`, `gate_is_async=True` only on the gate-4 stage, **`timeout_s is None` on all 7 stages** — dev pipeline inherits v1 timeouts via the §6.7 tier-3 fallback). |
| `test_resolve_workflow_priority_order` | `--workflow-file` beats `--workflow`; `.atlas/workflows/` beats `~/.atlas/workflows/` beats built-in. |
| `test_resolve_workflow_not_found_lists_all_paths` | Error message contains all 3 checked paths. |
| `test_resolve_workflow_rejects_path_traversal_name` | `workflow_name="../../etc/passwd"` → rejected before any filesystem path join. |

### Updated existing tests

| File | Change |
|---|---|
| `tests/unit/test_routing_fixture_match.py` | Now loads the dev pipeline via `workflow_loader.load_workflow_file(dev_yaml_path)` instead of importing `STAGES`; assertions unchanged (still validates against `routing_ground_truth.json`). |
| `tests/unit/test_state_store.py` | `create_tasks_md` tests pass an explicit `stages` tuple fixture instead of relying on the module-level import; new assertions on the `workflow:` line in the `## current` block. |
| `tests/unit/test_pipeline.py` | `Pipeline(...)` construction in every test gains `stages=...` and `workflow_name=...` kwargs; conditional-replacement tests (`isolate`/`gate_is_async`) added or adjusted to construct `StageSpec` directly with the new fields rather than relying on `StageName`/`GateLabel` enum members (which are deleted). |
| `tests/unit/test_phase4.py`, `test_worktree.py`, `test_remediation.py`, `test_review_fixes.py`, `test_t51_closure.py` | Grep for `StageName`/`GateLabel` imports; update any direct references to plain strings. |
| `tests/integration/test_main_branch_isolation.py` | Unaffected in behavior; update `Pipeline(...)` construction call sites for the new required kwargs. |
| `tests/e2e/test_e2e_happy_path.py` | Re-run unmodified (loads via `cli.py`/`_make_pipeline()`, which now resolves `dev` by default) — this is the actual parity proof end-to-end. |

### Mocking strategy

- `test_load_isolate_requires_git_on_path` mocks `shutil.which("git")` to return `None`.
- No subprocess mocking needed in `workflow_loader.py` tests — pure parsing/validation logic, no I/O beyond reading the YAML file itself (use `tmp_path` fixtures for file-based tests, in-memory `yaml.safe_load(io.StringIO(...))`-equivalent string fixtures where a real file isn't needed).

### Coverage target

`workflow_loader.py` ≥ 90% (TRD-v2 §10) — every `WorkflowValidationError` raise site needs a covering test (the table in §7 above is the checklist).

---

## 11. Performance Considerations

- **YAML load time < 50 ms for ≤ 20 stages** (NFR-1): `yaml.safe_load()` on a small document is sub-millisecond in practice; the 50ms budget is generous. No caching needed in Phase 1 — `Pipeline` construction happens once per CLI invocation, not in a hot loop. Add a perf-smoke test (`test_loader_perf_smoke`, not part of the 90% coverage requirement, marked `@pytest.mark.perf` or similar if the project later wants a perf-specific marker — Phase 1 can just assert wall-clock under a generous bound in CI without a special marker, consistent with v1's "spot-checked, not continuously gated" approach to perf, per v1 TRD §Performance).
- **Workflow resolution < 100 ms** (NFR-2): three `Path.exists()` calls plus one `load_workflow_file()` call — dominated by the YAML parse itself, well under budget.
- **No regression on `atlas status` < 500 ms / hook < 1 s** (NFR-3): `atlas status` doesn't invoke the loader at all in Phase 1 (it only reads `tasks.md`'s `## current` block, unchanged read path — the `workflow:` field is just one more line printed). The hook's new line-5 read is a single extra `.splitlines()` index access — negligible.

---

## Tasks

Flat list, ordered by execution sequence. Cross-task dependencies captured via `Dependencies`.

* **[T1.1] Extract `dev.yaml` and define `StageSpec` v2 shape** [Effort: M]
  - **Description**: Add `isolate`, `gate_is_async`, `backend`, `timeout_s` fields to `StageSpec` in `stages.py`; delete `StageName`/`GateLabel` `StrEnum`s and replace with `SPAN_KINDS` frozenset + `_NAME_RE` pattern. Hand-author `src/atlas/workflows/dev.yaml` encoding the exact v1 `STAGES` tuple (7 stages, same tool strings, gate labels, `isolate: true` on `code_gen` only, `gate_is_async: true` on the `gate_commit`-equivalent stage, **no `timeout_s` on any stage** — dev inherits v1 timeouts via the §6.7 fallback). Confirm hatchling packages the new `workflows/` data directory (add to `[tool.hatch.build.targets.wheel]` include rules if needed).
  - **Acceptance Criteria**:
    - [ ] `StageSpec` has 10 fields total (6 original + 4 new: `isolate`, `gate_is_async`, `backend`, `timeout_s`), all with correct types/defaults.
    - [ ] `StageName`/`GateLabel` no longer exist in `stages.py`.
    - [ ] `src/atlas/workflows/dev.yaml` exists, parses as valid YAML, contains exactly 7 stages.
    - [ ] `dev.yaml`'s `code_gen` stage has `isolate: true`; no other stage does.
    - [ ] `dev.yaml`'s gate-4 stage has `gate_is_async: true`; no other stage does.
    - [ ] No `dev.yaml` stage sets `timeout_s` (parity via §6.7 tier-3 fallback).
    - [ ] `uv run python -c "import importlib.resources; ..."` (or equivalent) confirms `dev.yaml` is readable from an installed wheel, not just from source checkout.
  - **Files to Create/Modify**:
    - `src/atlas/stages.py` - StageSpec v2 shape; delete StrEnums
    - `src/atlas/workflows/dev.yaml` - new, extracted from old STAGES tuple
    - `pyproject.toml` - confirm/add wheel package-data include for `workflows/*.yaml`
  - **Dependencies**: None
  - **Testing Requirements**: Unit (covered by T1.4's parity test, written against this output)

* **[T1.2] Implement `workflow_loader.py` — parsing + validation** [Effort: L]
  - **Description**: Implement `load_workflow_file()` per §6.1 pseudocode: `yaml.safe_load()` only, validate top-level keys, per-stage keys (including `gate_is_async` and `timeout_s` in `_ALLOWED_STAGE_KEYS`), `span_kind` membership, name format/uniqueness, gate-label uniqueness, `isolate` → git-on-PATH check, `timeout_s` → positive-int check. Raise `WorkflowValidationError` with a message naming the file path + offending field/value for every failure mode in §7's table.
  - **Acceptance Criteria**:
    - [ ] All unit tests in §10's "Unit tests" table for `load_workflow_file` pass (including `test_load_stage_timeout_s` and `test_load_rejects_bad_timeout_s`).
    - [ ] No raw traceback reaches a caller for any malformed-input case — every failure path raises `WorkflowValidationError` with a descriptive `str()`.
    - [ ] `yaml.load()` (unsafe) does not appear anywhere in the module — grep-verified.
    - [ ] `_ALLOWED_STAGE_KEYS` includes `gate_is_async` and `timeout_s` (a workflow setting either is not rejected as an unknown key).
  - **Files to Create/Modify**:
    - `src/atlas/workflow_loader.py` - new module
    - `tests/unit/test_workflow_loader.py` - new test file (parsing/validation tests only; resolution tests in T1.3)
  - **Dependencies**: T1.1
  - **Testing Requirements**: Unit, ≥ 90% coverage on this module

* **[T1.3] Implement `resolve_workflow()` — search-path resolution** [Effort: M]
  - **Description**: Implement `resolve_workflow()` per §6.2 pseudocode: `--workflow-file` > `--workflow <name>` search path (`.atlas/workflows/` → `~/.atlas/workflows/` → built-in `src/atlas/workflows/`) > default `dev`. Validate `workflow_name` against `_NAME_RE` before any path join (§9 security note — path traversal guard). Raise `WorkflowNotFoundError` naming every checked path on miss.
  - **Acceptance Criteria**:
    - [ ] `test_resolve_workflow_priority_order` passes (3-way priority order verified).
    - [ ] `test_resolve_workflow_not_found_lists_all_paths` passes.
    - [ ] `test_resolve_workflow_rejects_path_traversal_name` passes — a `workflow_name` containing `/`, `..`, or failing `_NAME_RE` is rejected before filesystem access.
    - [ ] Resolution completes in < 100 ms (NFR-2), spot-checked in a perf-smoke test.
  - **Files to Create/Modify**:
    - `src/atlas/workflow_loader.py` - add `resolve_workflow()`, `WorkflowNotFoundError`
    - `tests/unit/test_workflow_loader.py` - add resolution tests
  - **Dependencies**: T1.2
  - **Testing Requirements**: Unit

* **[T1.4] Dev-pipeline parity test** [Effort: S]
  - **Description**: Write `test_dev_pipeline_parity` asserting `load_workflow_file(dev_yaml_path)` produces a `StageSpec` tuple equal (field-by-field) to a hand-written expected tuple matching v1's old hardcoded `STAGES`. This is the single test that proves T1.1 + T1.2 together satisfy FR-8.
  - **Acceptance Criteria**:
    - [ ] Test compares all 10 `StageSpec` fields per stage, for all 7 stages (asserts `timeout_s is None` on every dev stage — §6.7 parity).
    - [ ] Test fails if `dev.yaml` is edited to drift from the original `STAGES` shape (regression guard).
  - **Files to Create/Modify**:
    - `tests/unit/test_workflow_loader.py` - add `test_dev_pipeline_parity`
  - **Dependencies**: T1.1, T1.2
  - **Testing Requirements**: Unit

* **[T1.5] Refactor `orchestrator.py` — data-driven conditionals + stages-as-constructor-arg** [Effort: L]
  - **Description**: Remove the `from atlas.stages import STAGE_BY_NAME, STAGES, GateLabel, StageName` import. Add `stages: tuple[StageSpec, ...]` and `workflow_name: str = "dev"` to `Pipeline.__init__`; build `self._stages`/`self._stage_by_name` from the constructor arg. Replace `if stage.name == StageName.CODE_GEN` with `if stage.isolate`; replace `if stage.gate_label == GateLabel.GATE_COMMIT` with `if stage.gate_is_async`. Replace all `STAGES[...]` indexing with `self._stages[...]`. Make `_validate_routing_fixture()` a no-op when `self._workflow_name != "dev"` (§6.4). Apply metric namespacing (§6.5) at the `record_user_signal` call site. **Add per-stage timeout resolution (§6.7, Resolved Decision #5):** introduce a `_GLOBAL_FALLBACK_TIMEOUT_S` module constant and a `resolve_timeout(stage, timeout_overrides)` helper implementing the `.atlas.toml` override > `stage.timeout_s` > `_DEFAULT_TIMEOUT_S` > global-fallback priority; replace `SubprocessStageRunner.run()`'s current `self._timeout_overrides.get(stage.name.value, _DEFAULT_TIMEOUT_S[stage.name.value])` (orchestrator.py:487-489) with a call to it. Retain `_DEFAULT_TIMEOUT_S` as the tier-3 fallback — do not delete it.
  - **Acceptance Criteria**:
    - [ ] No reference to module-level `STAGES`/`STAGE_BY_NAME`/`StageName`/`GateLabel` remains in `orchestrator.py` (grep-verified).
    - [ ] `if stage.isolate` and `if stage.gate_is_async` replace the two hardcoded equality checks.
    - [ ] `_validate_routing_fixture()` is skipped for any `workflow_name != "dev"`.
    - [ ] Gate scores for `workflow_name == "dev"` write bare metric names (`gate_research`, etc. — unchanged from v1); a synthetic non-dev workflow in a test writes `<name>.<gate_label>`.
    - [ ] `resolve_timeout` honors the 4-tier priority; `test_resolve_timeout_priority` passes. A non-dev stage name absent from `_DEFAULT_TIMEOUT_S` returns `_GLOBAL_FALLBACK_TIMEOUT_S`, not a `KeyError`.
    - [ ] `dev.yaml` stages (no `timeout_s`) resolve to the exact v1 `_DEFAULT_TIMEOUT_S` values (parity — verified against the v1 dict).
    - [ ] All existing `test_pipeline.py` / `test_phase4.py` timeout-related tests pass (or are updated) with no behavioral change for the dev pipeline.
  - **Files to Create/Modify**:
    - `src/atlas/orchestrator.py` - constructor + 3 conditionals + routing-fixture guard + metric namespacing + `resolve_timeout` helper + `_GLOBAL_FALLBACK_TIMEOUT_S`
  - **Dependencies**: T1.1, T1.2
  - **Testing Requirements**: Unit (existing `test_pipeline.py` + `test_phase4.py` suites, updated; new `test_resolve_timeout_priority`)

* **[T1.6] Update `state.py` — workflow-aware `tasks.md`** [Effort: M]
  - **Description**: `create_tasks_md()` accepts a `stages: tuple[StageSpec, ...]` parameter instead of importing `STAGES`; generates checkboxes from it. `## current` block template gains a `workflow: <name>` line. `first_unchecked()` returns `str` instead of `StageName(name)`; the existing try/except around enum construction is removed (plain string return needs no validation against a closed set — any non-empty checkbox label is valid by construction, since it only ever reads back what `create_tasks_md` wrote). `write_current_run()` gains the optional `async_gate_metric` parameter and writes it as line 5 (§6.6).
  - **Acceptance Criteria**:
    - [ ] `create_tasks_md(ctx, stages=...)` generates one checkbox per stage in the passed tuple, in order.
    - [ ] The `## current` code block includes a `workflow: <name>` line, positioned consistently (e.g. after `next:`).
    - [ ] `first_unchecked()` returns `str | None`, not `StageName | None`.
    - [ ] `write_current_run()` writes a 5th line with the async-gate metric name when provided; omits/defaults gracefully when not (backward compatible with files written by `read_current_run_with_worktree()`'s existing 2/3/4-line handling).
    - [ ] `read_current_run_with_worktree()`-equivalent reader (or a new accessor) can retrieve the workflow name from `tasks.md` for resume (FR-6's "resume re-reads workflow name").
  - **Files to Create/Modify**:
    - `src/atlas/state.py` - `create_tasks_md`, `first_unchecked`, `write_current_run`, `_TASKS_MD_HEADER` template, regex constants
  - **Dependencies**: T1.1
  - **Testing Requirements**: Unit (`test_state_store.py` updated + new workflow-field assertions)

* **[T1.7] Wire `resume()` to reload the workflow from `tasks.md`** [Effort: M]
  - **Description**: `Pipeline.resume()` currently rebuilds `RunContext` from `.atlas/current-run` + `tasks.md` without re-deriving the stage table (v1 always used the module-level `STAGES`). Phase 1 must read the `workflow:` field from `tasks.md`, call `resolve_workflow(workflow_name=..., repo_root=...)`, and use the resulting `stages` tuple for the rest of the resumed run. On `WorkflowNotFoundError` during resume (edited/deleted YAML), surface a clear CLI error rather than crashing — per §7's edge-case row, do not silently fall back to `dev`.
  - **Acceptance Criteria**:
    - [ ] `resume()` on a `job`-workflow run (synthetic test fixture workflow, not the real `job.yaml` from Phase 2) reconstructs the correct non-dev `stages` tuple.
    - [ ] `resume()` on a run whose workflow YAML has been deleted between start and resume raises a clear, caught error (test asserts the error message names the missing workflow), not an unhandled exception.
    - [ ] Existing `dev`-workflow resume tests pass unchanged in behavior.
  - **Files to Create/Modify**:
    - `src/atlas/orchestrator.py` - `Pipeline.resume()`
    - `src/atlas/state.py` - add a workflow-name reader if not already covered by T1.6
  - **Dependencies**: T1.3, T1.5, T1.6
  - **Testing Requirements**: Unit + Integration (extends `test_pipeline.py` resume tests)

* **[T1.8] Update `cli.py` — `--workflow` / `--workflow-file` flags** [Effort: M]
  - **Description**: Add `--workflow` and `--workflow-file` options to the `run` command (per §4.1 table). Wire `resolve_workflow()` into `_make_pipeline()`, passing the resolved `stages` tuple and `workflow_name` into `Pipeline(...)`. On `WorkflowNotFoundError`/`WorkflowValidationError`, catch and `typer.echo(str(exc), err=True); raise typer.Exit(1)` (matching the existing `RoutingDriftError` handling pattern already in `cli.py`). `atlas --help` lists available workflows discovered via the search path (TRD-v2 §4 Usability). `atlas status` output gains the `workflow:` line (already covered by T1.6's `## current` block change — `status` just echoes the block, no separate `status` code change needed beyond what T1.6 produces).
  - **Acceptance Criteria**:
    - [ ] `atlas run "<task>"` (no flag) behaves identically to v1 (loads `dev`).
    - [ ] `atlas run "<task>" --workflow dev` explicitly loads `dev.yaml`, same result.
    - [ ] `atlas run "<task>" --workflow nonexistent` exits non-zero, message lists all 3 checked paths.
    - [ ] `atlas run "<task>" --workflow-file ./custom.yaml` loads the literal path, bypassing the search.
    - [ ] `atlas --help` output names available workflows (at minimum `dev`, discovered from the built-in `workflows/` directory).
    - [ ] Malformed YAML passed via either flag surfaces `WorkflowValidationError`'s message, not a traceback.
  - **Files to Create/Modify**:
    - `src/atlas/cli.py` - `run` command options, `_make_pipeline()`, `--help` workflow listing
  - **Dependencies**: T1.3, T1.5
  - **Testing Requirements**: Integration (CLI invocation tests, extending existing `test_e2e_happy_path.py` patterns or a new `tests/integration/test_cli_workflow_flags.py`)

* **[T1.9] Update `plugin_resolver.py` — tool resolution merge order** [Effort: S]
  - **Description**: Confirm/adjust `resolve()`'s precedence to match §3.5: `.atlas.toml [plugin_commands.<tool>]` overrides > YAML `tool` field (i.e., `StageSpec.tool` itself, already resolved by the loader) > `PLUGIN_COMMANDS` dict fallback (dev-pipeline stages only). In practice this requires no behavior change to `resolve()`'s existing signature (`overrides` already wins; `PLUGIN_COMMANDS` is already the fallback) — the only Phase 1 change is documentation/comment clarity that `PLUGIN_COMMANDS` is now explicitly "dev-pipeline defaults only," since non-dev workflows' `tool` strings are expected to either be `RAW:`-prefixed (bypassing resolution) or match a `.atlas.toml` override, not `PLUGIN_COMMANDS`.
  - **Acceptance Criteria**:
    - [ ] `resolve()` behavior is unchanged for `dev.yaml` stages (regression-free).
    - [ ] A tool string not in `PLUGIN_COMMANDS` and not in `.atlas.toml` overrides and not `RAW:`-prefixed still raises `RoutingDriftError` with the existing message (proves non-dev workflows aren't silently broken by the missing fallback — they're expected to use `RAW:` or config overrides).
    - [ ] Module docstring/comment updated to state `PLUGIN_COMMANDS` is dev-pipeline-only.
  - **Files to Create/Modify**:
    - `src/atlas/plugin_resolver.py` - docstring/comment clarity; confirm no behavior regression
  - **Dependencies**: None (can run in parallel with T1.1–T1.8)
  - **Testing Requirements**: Unit (existing `plugin_resolver` tests re-verified, no new test required unless a gap is found)

* **[T1.10] Parameterize `post_commit_hook.py` metric from `gate_is_async` stage** [Effort: S]
  - **Description**: Implement §6.6: hook reads an optional 5th line from `.atlas/current-run` as the metric name, defaulting to `"gate_commit"` when absent (backward compatible with pre-Phase-1 state files and with `dev.yaml`, whose async-gate metric *is* `gate_commit`). `Pipeline.step()`'s worktree-creation branch (already modified in T1.5 to check `stage.isolate`) must also pass the async-gate's `gate_label` through to `state.write_current_run(..., async_gate_metric=stage.gate_label)` at the point it currently calls `write_current_run` for the `gate_is_async` stage (orchestrator.py:298-300 in the current source — the call inside the `if stage.gate_label == GateLabel.GATE_COMMIT:` block, now `if stage.gate_is_async:`).
  - **Acceptance Criteria**:
    - [ ] Hook reads line 5 when present, uses it as `metric`; falls back to `"gate_commit"` when absent or empty.
    - [ ] A synthetic non-dev workflow whose async-gate stage has `gate: "gate_shipped"` produces a hook-written score with `metric == "gate_shipped"`, not `"gate_commit"`.
    - [ ] `dev.yaml`'s existing behavior (`metric == "gate_commit"`) is unchanged — verified by a regression test.
    - [ ] Idempotency dedupe logic (`_already_recorded`) keys on `(run_id, commit_sha, metric)` — already metric-aware in v1, confirm no change needed there.
  - **Files to Create/Modify**:
    - `src/atlas/post_commit_hook.py` - `run()` reads line 5
    - `src/atlas/state.py` - `write_current_run()` writes line 5 (shared with T1.6)
    - `src/atlas/orchestrator.py` - pass `stage.gate_label` as `async_gate_metric` at the `gate_is_async` write_current_run call site
  - **Dependencies**: T1.5, T1.6
  - **Testing Requirements**: Unit + Integration (extends hook idempotency tests)

* **[T1.11] Sweep: delete dead `StageName`/`GateLabel` references repo-wide** [Effort: S]
  - **Description**: Grep the full `src/` and `tests/` trees for `StageName` and `GateLabel` (both as imports and as usages, e.g. `StageName.CODE_GEN`, `.value` accesses that assumed enum membership). Every remaining hit outside `stages.py`'s now-deleted definitions is a Phase 1 regression. Update all test fixtures that constructed `StageSpec(..., StageName.X, ...)` to use plain lowercase strings instead.
  - **Acceptance Criteria**:
    - [ ] `grep -rn "StageName\|GateLabel" src/ tests/` returns zero hits.
    - [ ] Full test suite (`pytest`) passes after the sweep.
    - [ ] `mypy src` passes (no orphaned enum-typed annotations left behind).
  - **Files to Create/Modify**:
    - `tests/unit/test_phase4.py`, `tests/unit/test_worktree.py`, `tests/unit/test_remediation.py`, `tests/unit/test_review_fixes.py`, `tests/unit/test_t51_closure.py`, `tests/integration/test_main_branch_isolation.py` - replace any enum references with plain strings
  - **Dependencies**: T1.5, T1.6, T1.7, T1.8, T1.9, T1.10 (run last — sweeps up whatever the above tasks leave behind)
  - **Testing Requirements**: Full suite re-run (unit + integration; e2e marked separately per `pyproject.toml`'s `--ignore=tests/e2e` default)

* **[T1.12] Add `pyyaml` dependency + CI gate updates** [Effort: S]
  - **Description**: Add `pyyaml >= 6.0` to `pyproject.toml [project.dependencies]`. Confirm `ruff`/`mypy` configs don't need changes (pyyaml ships type stubs via `types-PyYAML` if `mypy --strict` complains — add to `[project.optional-dependencies].dev` if needed). Update `.github/workflows/ci.yml` if it pins dependency lists explicitly (check first — `pyproject.toml`-driven installs likely need no CI file change).
  - **Acceptance Criteria**:
    - [ ] `uv sync` installs `pyyaml` cleanly.
    - [ ] `mypy src` passes with `pyyaml` imported in `workflow_loader.py` (add `types-PyYAML` to dev deps if strict mode flags missing stubs).
    - [ ] CI green on a PR touching only this dependency addition (sanity check before the full Phase 1 PR lands).
  - **Files to Create/Modify**:
    - `pyproject.toml` - add `pyyaml>=6.0` to dependencies; possibly `types-PyYAML` to dev deps
  - **Dependencies**: None (can run first, in parallel with T1.1)
  - **Testing Requirements**: CI green

* **[T1.13] End-to-end parity re-run** [Effort: S]
  - **Description**: Re-run `tests/e2e/test_e2e_happy_path.py` (currently using stub plugins + a real git repo) unmodified against the new `cli.py`-driven default-workflow resolution path, to prove the full v1 acceptance criteria (TRD-v2 §13 #1) hold end-to-end, not just at the unit level. This is the Phase 1 exit gate's primary evidence.
  - **Acceptance Criteria**:
    - [ ] All existing assertions in `test_e2e_happy_path.py` pass unmodified: 7 spans in order, 5 orchestrator gate scores + 1 hook score, `git log main` unchanged, routing fixture validated, resume mid-run verified.
    - [ ] No test file changes required in `test_e2e_happy_path.py` itself (proves the CLI-level contract is unchanged) — if a change *is* required, it must be limited to construction/setup boilerplate, not assertions.
  - **Files to Create/Modify**:
    - None expected (verification task); `tests/e2e/test_e2e_happy_path.py` only if unavoidable
  - **Dependencies**: T1.1–T1.12 (all)
  - **Testing Requirements**: E2E (`pytest tests/e2e -m e2e` or equivalent — note `pyproject.toml`'s default `addopts` ignores `tests/e2e`, so this must be run explicitly)

---

## Phase Deliverables

- Working YAML-driven engine: `workflow_loader.py` loads `dev.yaml` into the exact `StageSpec` shape `Pipeline` already consumes; `atlas run "<task>"` (no flag) and `atlas run "<task>" --workflow dev` both behave identically to v1.
- `StageName`/`GateLabel` `StrEnum`s deleted; zero references remain (T1.11's grep-zero criterion).
- `tasks.md` is workflow-aware (`workflow:` field; resume reloads the correct YAML).
- Metric-name namespacing convention implemented and dev-pipeline-backward-compatible.
- All v1 acceptance criteria (PRD + v1 TRD) pass with zero regressions, proven by T1.13's unmodified e2e re-run.
- `workflow_loader.py` ≥ 90% test coverage; full suite ≥ 80% (existing CI gate, unchanged threshold).
- `ruff check`, `ruff format --check`, `mypy src` all green.
- Tests passing: full `pytest` suite (unit + integration; e2e run explicitly per T1.13).
- Documentation updated: `workflow_loader.py` module docstring states the YAML trust-boundary explicitly (§9); this TRS itself stands as the implementation record until folded into a post-Phase-1 doc update pass.

---

## Resolved Decisions

All five open questions were resolved with the maintainer (also the TRD-v2 author) on 2026-06-29. Each is now a settled constraint on implementation, not an open assumption.

1. **Hatchling packaging of `src/atlas/workflows/dev.yaml` → (a) trust default, verify post-hoc.** `[tool.hatch.build.targets.wheel] packages = ["src/atlas"]` should include non-`.py` files under that tree by hatchling's default behavior. T1.1 builds a wheel and inspects its contents to confirm; only if the default *doesn't* ship `dev.yaml` does an explicit `[tool.hatch.build.targets.wheel.force-include]` entry get added. No `pyproject.toml` churn until proven necessary. **Rationale:** this is a verifiable fact, not a judgment call — the build either includes it or it doesn't, and T1.1's acceptance criterion already gates on reading `dev.yaml` from an installed wheel.

2. **`gate_is_async` as a YAML-exposed key → (a) explicit YAML stage key. CONFIRMED BY TRD-v2 AUTHOR.** `gate_is_async: true | false` is an allowed stage-level YAML key, defaulting to `false`, parallel to its sibling `isolate`. The TRD-v2 author confirmed on 2026-06-29 that §3.1's example YAML block is **illustrative, not exhaustive** — `gate_is_async` (like `default_backend`, also absent from the example but required by §3.4) belongs in the schema. **Rationale:** `gate_is_async` is a genuine per-stage property of *how a gate gets scored* (orchestrator-written vs. hook-written-asynchronously), exactly parallel to `isolate` being a per-stage property of *where a stage runs* — §3.3 treats them as siblings. The rejected alternatives: (b) deriving it from a workflow-level `async_gate:` field adds indirection nothing motivates; (c) hardcoding `gate_is_async = (gate_label == "gate_commit")` would pass every Phase 1 test (because `dev.yaml`'s async gate *is* `gate_commit`) yet silently break the first Phase 2 job-workflow gate under a different label — reintroducing the exact hardcoding §3.4 exists to remove. This decision is now binding on T1.1 (`StageSpec` + `dev.yaml`), T1.2 (loader accepts the key), and T1.4 (parity test asserts it).

3. **Both `--workflow` and `--workflow-file` passed → (a) silent priority for Phase 1.** `--workflow-file` wins per §3.2's resolution order; passing both is not a usage error. **Rationale:** single-user tool, and "resolution order" already deterministically defines the both-passed case. Promote to a `typer` mutually-exclusive usage error (option b) only if it ever causes real confusion in practice — tracked as a Should-Have polish item, not Phase 1 work.

4. **`default_backend` validation in Phase 1 → (a) parse but don't validate.** Phase 1 stores `LoadedWorkflow.default_backend` (and per-stage `StageSpec.backend`) as an opaque `str | None`, threading it through untouched; no allow-list check. **Rationale:** validation belongs where the authority lives — Phase 3 defines which backends exist (`CliBackend`, `ClaudeCodeBackend`, `AntigravityBackend`), so Phase 3 validates them in one place. Hardcoding `{"claude", "agy"}` in the Phase 1 loader would spread that knowledge across two phases for no benefit, since nothing consumes the field until Phase 3. A typo like `backend: claud` is harmless until Phase 3's dispatch path, where Phase 3's validation will catch it at the moment it first matters.

5. **Appendix A `_DEFAULT_TIMEOUT_S` generalization → pull into Phase 1.** TRD-v2 Appendix A lists `orchestrator.py::_DEFAULT_TIMEOUT_S` (7 stage-name string keys) as a v2 seam to generalize: "move into `dev.yaml` or config; loader merges." Originally this TRS deferred it (no §14 Phase 1 bullet mentions it, no §13 exit criterion depends on it). The maintainer chose on 2026-06-29 to **pull it into Phase 1** rather than leave a v2 seam un-generalized after the engine phase. Implementation: add an optional per-stage `timeout_s: int | None` field to `StageSpec` and as an allowed YAML key; the orchestrator resolves a stage's timeout in priority order `.atlas.toml override` > `stage.timeout_s` > `_DEFAULT_TIMEOUT_S` > `_GLOBAL_FALLBACK_TIMEOUT_S` (§6.7). **Rationale:** Appendix A explicitly names this seam, and generalizing it now (while the loader and `StageSpec` are already being touched) is cheaper than a later pass that re-opens the same files. `_DEFAULT_TIMEOUT_S` is **retained** as the tier-3 fallback, not deleted, so dev-pipeline parity (FR-8) is exact — `dev.yaml` ships with no `timeout_s` fields and inherits v1 timeouts via fallback. A bonus robustness fix falls out: non-dev workflows whose stage names aren't in `_DEFAULT_TIMEOUT_S` now get `_GLOBAL_FALLBACK_TIMEOUT_S` instead of the `KeyError` v1 would have raised. Binding on T1.1 (`StageSpec` field + `dev.yaml` omits it), T1.2 (loader parses/validates `timeout_s`), T1.4 (parity asserts `timeout_s is None`), and T1.5 (`resolve_timeout` helper + call-site).
