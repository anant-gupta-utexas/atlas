# Context — YAML Workflow Engine, Phase 1 TRS

Reference notes for anyone picking up this work cold.

## Key files

### Source-of-truth docs (read first, in order)
- [`docs/2_architecture/TRD-v2.md`](../../../docs/2_architecture/TRD-v2.md) — the phase contract this TRS details. §3.1–3.7, §10, §14 (Phase 1), Appendix A are the load-bearing sections.
- [`docs/2_architecture/TRD.md`](../../../docs/2_architecture/TRD.md) — v1 NFRs/integrations that carry forward unchanged.
- [`docs/1_product_and_research/PRD.md`](../../../docs/1_product_and_research/PRD.md) — v1 product scope. Note: predates v2 entirely; no v2 PRD exists yet (TRD-v2's own preamble flags this).
- [`docs/1_product_and_research/yaml-driven-workflows-analysis.md`](../yaml-workflow-engine-design-notes/yaml-driven-workflows-analysis.md) — engine plan + plumb impact analysis grounding TRD-v2.
- [`docs/1_product_and_research/job-workflow-scope.md`](../yaml-workflow-engine-design-notes/job-workflow-scope.md) — first worked example (Phase 2, not this TRS, but explains *why* the loader's shape is what it is).

### TRS itself (this directory)
- [`yaml-workflow-engine-phase-1-plan.md`](./yaml-workflow-engine-phase-1-plan.md) — design (sections 1–11) + flat task list (T1.1–T1.13).
- [`yaml-workflow-engine-phase-1-tasks.md`](./yaml-workflow-engine-phase-1-tasks.md) — checkbox progress tracking.

### Code targets

**New:**
- `src/atlas/workflow_loader.py` — YAML → `tuple[StageSpec, ...]`, the new module.
- `src/atlas/workflows/dev.yaml` — extracted from the old hardcoded `STAGES` tuple.
- `tests/unit/test_workflow_loader.py` — new test file.

**Modified:**
- `src/atlas/stages.py` — `StageName`/`GateLabel` `StrEnum`s deleted; `StageSpec` gains `isolate`, `gate_is_async`, `backend`.
- `src/atlas/orchestrator.py` — `Pipeline` takes `stages`/`workflow_name` at construction; 3 hardcoded conditionals → data-driven; routing-fixture validation dev-only; metric namespacing applied.
- `src/atlas/state.py` — `create_tasks_md`/`first_unchecked`/`write_current_run` workflow-aware.
- `src/atlas/cli.py` — `--workflow`/`--workflow-file` flags.
- `src/atlas/plugin_resolver.py` — doc clarity only (no behavior change expected).
- `src/atlas/post_commit_hook.py` — metric parameterized from `.atlas/current-run` line 5.
- `pyproject.toml` — add `pyyaml>=6.0`.

### Existing reference implementation (for the patterns this TRS extends)
- [`dev/archive/atlas-pipeline-trs/`](../../archive/atlas-pipeline-trs/) — the v1 TRS for the same orchestrator. Format/depth precedent for this TRS. v1's `STAGES`/`StageName`/`GateLabel` design (now being generalized) lives there in full historical context.

## Decisions made (during this TRS)

| # | Decision | Rationale |
| - | --- | --- |
| 1 | `gate_is_async` is a YAML stage key (`true`/`false`, default `false`). **CONFIRMED by the TRD-v2 author 2026-06-29** — §3.1's example YAML is illustrative, not exhaustive. | §3.3 requires the `StageSpec` field; §3.4 requires a data-driven way to set it; it's a per-stage property parallel to its sibling `isolate`. No longer an inference — settled. See plan "Resolved Decisions" #2. |
| 2 | `isolate: true` validation is split: loader checks "git on PATH" at load time; `WorktreeManager` keeps the existing "repo is clean" check at worktree-creation time. | Avoids a TOCTOU bug — the repo could go from clean to dirty between `Pipeline` construction and stage-5 execution if other stages ran first. Re-validating cleanliness at load time would be a stale check. |
| 3 | `post_commit_hook.py`'s metric parameterization uses a 5th line in `.atlas/current-run`, not a YAML read inside the hook subprocess. | The hook runs in a separate process with no `Pipeline`/`StageSpec` access; keeping it YAML-free preserves v1's "thin, dependency-free, best-effort" hook design philosophy. |
| 4 | `PLUGIN_COMMANDS` requires no behavioral change in Phase 1 — only a docstring update. | `resolve()`'s existing precedence (`overrides` > `PLUGIN_COMMANDS`) already matches §3.5's resolution order once "the YAML `tool` field" is understood as *already being* `StageSpec.tool` (the primary source), not a fourth lookup table. |
| 5 | `default_backend` and per-stage `backend` are parsed and stored in Phase 1 but never validated or consumed. | `CliBackend` doesn't exist until Phase 3; validating a backend allow-list now would be speculative work outside this phase's stated scope, and Phase 3 may define the allow-list differently anyway. |
| 6 | `_validate_routing_fixture()` becomes a no-op for any `workflow_name != "dev"`, rather than being deleted or made per-workflow. | `routing_ground_truth.json` only describes the 7-stage dev pipeline (TRD-v2 §14: "Make dev-workflow-only or per-workflow fixture"). Per-workflow fixtures are not in Phase 1 scope — Phase 2's `job.yaml` ships without one. |

Decisions 2–6 are this TRS's own design choices where TRD-v2's text left room; decision 1 was confirmed directly by the TRD-v2 author. None contradict TRD-v2. All five originally-open questions (hatchling packaging, `gate_is_async` schema, both-flags precedence, `default_backend` validation, and the `_DEFAULT_TIMEOUT_S` generalization) were resolved on 2026-06-29 — see the plan's "Resolved Decisions" section. There are no remaining open items.

## Integration points

| Direction | Surface | Failure mode | Test coverage |
| --- | --- | --- | --- |
| `workflow_loader.py` → filesystem (`.atlas/workflows/`, `~/.atlas/workflows/`, built-in) | `Path.exists()` + `yaml.safe_load(path.read_text())` | Missing file → `WorkflowNotFoundError` (lists all paths checked). Malformed YAML → `WorkflowValidationError` (names the field). | Unit (`test_workflow_loader.py`, full table in plan §10). |
| `orchestrator.py` → `workflow_loader.py` | `Pipeline.__init__` now takes `stages: tuple[StageSpec, ...]` directly (resolved upstream by `cli.py`/`resume()`), not a module import | N/A — `Pipeline` itself never calls the loader; callers do. | Unit (`test_pipeline.py`, construction call sites updated). |
| `cli.py` → `workflow_loader.py` | `_make_pipeline()` calls `resolve_workflow(...)` before constructing `Pipeline` | `WorkflowNotFoundError`/`WorkflowValidationError` caught, `typer.echo(..., err=True)`, `typer.Exit(1)` — same pattern as existing `RoutingDriftError` handling. | Integration (CLI invocation tests). |
| `Pipeline.resume()` → `workflow_loader.py` | Reads `workflow:` from `tasks.md`, calls `resolve_workflow(workflow_name=...)` | Edited/deleted YAML between start and resume → clear caught error, not a crash; does NOT silently fall back to `dev`. | Unit + Integration (resume tests). |
| `post_commit_hook.py` → `.atlas/current-run` | Reads line 5 (optional) for the async-gate metric name | Missing line 5 → falls back to literal `"gate_commit"` (backward compatible). | Unit + Integration (hook tests). |
| `orchestrator.py` → `plumb_adapter.py` (metric namespacing) | `namespaced_metric(workflow_name, gate_label)` wraps the existing `record_user_signal(metric=...)` call | N/A — pure string function, no failure mode. | Unit (new test asserting `dev` stays bare, synthetic non-dev workflow gets prefixed). |

## Routing fixture — unchanged in Phase 1

`tests/fixtures/routing_ground_truth.json` (7 rows, one per dev-pipeline stage) is **not modified** by Phase 1. It continues to validate only the `dev` workflow; `_validate_routing_fixture()` is made dev-only (Decision #6 above) rather than the fixture itself being generalized. Confirm the fixture's `stage_name`/`expected_tool`/`expected_span_kind` values still match `dev.yaml` exactly once T1.1 extracts it — this is what T1.4's parity test and the existing `test_routing_fixture_match.py` (updated to load via the YAML path) jointly prove.

## Where this TRS's task list maps to TRD-v2 Appendix A

| Appendix A row | This TRS's task |
| --- | --- |
| `stages.py` — delete enums, replace with loader output | T1.1 |
| `orchestrator.py::_DEFAULT_TIMEOUT_S` (7 stage-name string keys) | T1.1 (`StageSpec.timeout_s`) + T1.2 (loader parses it) + T1.5 (`resolve_timeout` helper). Resolved Decision #5 pulls this into Phase 1: per-stage `timeout_s` YAML field, resolved `.atlas.toml override` > `stage.timeout_s` > `_DEFAULT_TIMEOUT_S` > `_GLOBAL_FALLBACK_TIMEOUT_S`. `_DEFAULT_TIMEOUT_S` is retained as the tier-3 fallback (not deleted); `dev.yaml` omits `timeout_s` so the dev pipeline inherits v1 timeouts exactly. See plan §6.7. |
| `orchestrator.py::step()` — `if stage.name == StageName.CODE_GEN` | T1.5 |
| `orchestrator.py::step()` — `if stage.gate_label == GateLabel.GATE_COMMIT` | T1.5 |
| `orchestrator.py::_validate_routing_fixture()` | T1.5 |
| `plugin_resolver.py::PLUGIN_COMMANDS` | T1.9 |
| `state.py::create_tasks_md()` | T1.6 |
| `state.py::first_unchecked()` | T1.6 |
| `post_commit_hook.py::run()` — `metric = "gate_commit"` | T1.10 |

**Note on `_DEFAULT_TIMEOUT_S` (Resolved Decision #5):** originally flagged here as a deferred gap; the maintainer decided 2026-06-29 to pull the generalization into Phase 1. It is now spread across T1.1/T1.2/T1.4/T1.5 (no standalone task — the change lives entirely in files those tasks already touch: `stages.py`, `workflow_loader.py`, `orchestrator.py`). `_DEFAULT_TIMEOUT_S` survives as the fallback tier, so dev-pipeline parity (FR-8) is unaffected. This was the last open item; the Appendix A inventory is now fully accounted for in Phase 1.
