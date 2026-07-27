# Tasks — YAML Workflow Engine, Phase 1 TRS

Progress checklist. Source-of-truth for design is
[`yaml-workflow-engine-phase-1-plan.md`](./yaml-workflow-engine-phase-1-plan.md).

## Current

```
phase: complete
gate:  none
next:  Phase 1 implementation done; ready for review / Phase 2 kickoff
```

## Tasks (flat — Phase 1 only, no sub-phases)

- [x] **T1.1** — Extract `dev.yaml` + define `StageSpec` v2 shape (4 new fields incl. `timeout_s`) (`stages.py`, `workflows/dev.yaml`)
- [x] **T1.2** — Implement `workflow_loader.py` parsing + validation (`load_workflow_file`)
- [x] **T1.3** — Implement `resolve_workflow()` search-path resolution
- [x] **T1.4** — Dev-pipeline parity test (`test_dev_pipeline_parity`)
- [x] **T1.5** — Refactor `orchestrator.py`: data-driven conditionals + stages-as-constructor-arg
- [x] **T1.6** — Update `state.py`: workflow-aware `tasks.md`
- [x] **T1.7** — Wire `resume()` to reload workflow from `tasks.md`
- [x] **T1.8** — Update `cli.py`: `--workflow` / `--workflow-file` flags
- [x] **T1.9** — Update `plugin_resolver.py`: doc clarity on resolution order
- [x] **T1.10** — Parameterize `post_commit_hook.py` metric from `gate_is_async` stage
- [x] **T1.11** — Sweep: delete dead `StageName`/`GateLabel` references repo-wide
- [x] **T1.12** — Add `pyyaml` dependency + CI gate check (can start anytime, in parallel)
- [x] **T1.13** — End-to-end parity re-run (`test_e2e_happy_path.py`, unmodified)

## Exit criteria (TRD-v2 §14 Phase 1, copied for tracking)

- [x] All v1 acceptance criteria pass (zero regressions) — 153 unit/integration + 3 e2e tests pass
- [x] `atlas run "<task>"` (no `--workflow` flag) loads `dev.yaml`, behaves identically to v1
- [x] `atlas run "<task>" --workflow dev` explicitly loads `dev.yaml`, same result
- [x] Loader tests validate acceptance and rejection of valid/invalid YAMLs
- [x] Routing fixture test passes against the YAML-loaded dev pipeline
- [x] `workflow_loader.py` ≥ 90% coverage (100% achieved); full suite ≥ 80% (92.75% achieved)
- [x] `ruff check`, `ruff format --check`, `mypy src` green

## Resolved decisions (2026-06-29 — see plan "Resolved Decisions" for full detail)

- [x] #1 — Hatchling packaging → (a) trust default, verify in T1.1; explicit config only if needed.
      Verified: built a wheel, confirmed `atlas/workflows/dev.yaml` is included. No pyproject.toml
      force-include needed.
- [x] #2 — `gate_is_async` as explicit YAML stage key → (a) CONFIRMED by TRD-v2 author (example YAML is illustrative, not exhaustive)
- [x] #3 — Both `--workflow` and `--workflow-file` → (a) silent priority for Phase 1
- [x] #4 — `default_backend` validation → (a) parse but don't validate; Phase 3 owns the allow-list
- [x] #5 — `_DEFAULT_TIMEOUT_S` generalization → PULL INTO Phase 1: per-stage `timeout_s` YAML field, 4-tier resolution, `_DEFAULT_TIMEOUT_S` retained as fallback (T1.1/T1.2/T1.4/T1.5; plan §6.7)

_No open items remain — the Appendix A inventory is fully accounted for in Phase 1._

## Implementation notes (post-hoc, 2026-06-30)

- New files: `src/atlas/workflow_loader.py`, `src/atlas/workflows/dev.yaml`,
  `tests/unit/test_workflow_loader.py`.
- `StageName`/`GateLabel` `StrEnum`s fully deleted from `stages.py`; `StageSpec.name`/
  `gate_label` are now plain `str` / `str | None`. Verified zero repo-wide references remain.
- `Pipeline.__init__` accepts `stages: tuple[StageSpec, ...] | None = None` (defaults to
  resolving `dev` when omitted, e.g. for tests that construct `Pipeline` directly) and
  `workflow_name: str = "dev"`.
- `Pipeline.resume()` re-resolves the workflow from the `workflow:` field in `tasks.md`
  via `resolve_workflow()`, raising `WorkflowNotFoundError` (not falling back to `dev`)
  if the original workflow YAML is missing.
- `state.py`: `create_tasks_md()` now requires a `stages` kwarg; `## current` block gained
  a `workflow: <name>` line; `first_unchecked()` returns `str | None` with no validation
  against a closed set (NFR-6 — any checkbox label is valid by construction). One existing
  test (`test_first_unchecked_skips_unknown_stage_name`) was renamed/inverted to
  `test_first_unchecked_returns_any_unchecked_label` to reflect this intentional behavior
  change.
- `cli.py`: `run` command gained `--workflow`/`-w` and `--workflow-file` options;
  `atlas --help` lists built-in workflow names (currently just `dev`); both `run` and
  `resume` catch `WorkflowNotFoundError`/`WorkflowValidationError` and print a clean
  message (no traceback) before exiting 1. Manually smoke-tested.
- Final verification: 153 unit/integration tests + 3 e2e tests green; coverage 92.75%
  overall (`workflow_loader.py` 100%); `ruff check`, `ruff format --check`, `mypy --strict`
  all clean.
