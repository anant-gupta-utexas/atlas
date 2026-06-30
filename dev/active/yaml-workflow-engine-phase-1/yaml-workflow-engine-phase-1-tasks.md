# Tasks — YAML Workflow Engine, Phase 1 TRS

Progress checklist. Source-of-truth for design is
[`yaml-workflow-engine-phase-1-plan.md`](./yaml-workflow-engine-phase-1-plan.md).

## Current

```
phase: not_started
gate:  none
next:  start T1.1 + T1.12 (all 4 decisions resolved 2026-06-29)
```

## Tasks (flat — Phase 1 only, no sub-phases)

- [ ] **T1.1** — Extract `dev.yaml` + define `StageSpec` v2 shape (`stages.py`, `workflows/dev.yaml`)
- [ ] **T1.2** — Implement `workflow_loader.py` parsing + validation (`load_workflow_file`)
- [ ] **T1.3** — Implement `resolve_workflow()` search-path resolution
- [ ] **T1.4** — Dev-pipeline parity test (`test_dev_pipeline_parity`)
- [ ] **T1.5** — Refactor `orchestrator.py`: data-driven conditionals + stages-as-constructor-arg
- [ ] **T1.6** — Update `state.py`: workflow-aware `tasks.md`
- [ ] **T1.7** — Wire `resume()` to reload workflow from `tasks.md`
- [ ] **T1.8** — Update `cli.py`: `--workflow` / `--workflow-file` flags
- [ ] **T1.9** — Update `plugin_resolver.py`: doc clarity on resolution order
- [ ] **T1.10** — Parameterize `post_commit_hook.py` metric from `gate_is_async` stage
- [ ] **T1.11** — Sweep: delete dead `StageName`/`GateLabel` references repo-wide
- [ ] **T1.12** — Add `pyyaml` dependency + CI gate check (can start anytime, in parallel)
- [ ] **T1.13** — End-to-end parity re-run (`test_e2e_happy_path.py`, unmodified)

## Exit criteria (TRD-v2 §14 Phase 1, copied for tracking)

- [ ] All v1 acceptance criteria pass (zero regressions)
- [ ] `atlas run "<task>"` (no `--workflow` flag) loads `dev.yaml`, behaves identically to v1
- [ ] `atlas run "<task>" --workflow dev` explicitly loads `dev.yaml`, same result
- [ ] Loader tests validate acceptance and rejection of valid/invalid YAMLs
- [ ] Routing fixture test passes against the YAML-loaded dev pipeline
- [ ] `workflow_loader.py` ≥ 90% coverage; full suite ≥ 80% (existing gate)
- [ ] `ruff check`, `ruff format --check`, `mypy src` green

## Resolved decisions (2026-06-29 — see plan "Resolved Decisions" for full detail)

- [x] #1 — Hatchling packaging → (a) trust default, verify in T1.1; explicit config only if needed
- [x] #2 — `gate_is_async` as explicit YAML stage key → (a) CONFIRMED by TRD-v2 author (example YAML is illustrative, not exhaustive)
- [x] #3 — Both `--workflow` and `--workflow-file` → (a) silent priority for Phase 1
- [x] #4 — `default_backend` validation → (a) parse but don't validate; Phase 3 owns the allow-list

## Still open (non-blocking for Phase 1 exit)

- [ ] Appendix A gap — `_DEFAULT_TIMEOUT_S` YAML-driven generalization not in this Phase 1 task list; flag for next TRD/TRS pass to confirm Phase 2 / Phase 3 / later cleanup (see context.md)
