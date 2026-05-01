# Tasks — `atlas.pipeline` TRS

Progress checklist. Source-of-truth for Phase scope is
[`atlas-pipeline-trs-phases.md`](./atlas-pipeline-trs-phases.md).

## Current

```
phase: not_started
gate:  none
next:  resolve D1 (plugin command resolution) before Phase 4; Phase 1 unblocked
```

## Phase 1 — Skeleton + state machine

- [ ] **T1.1** — `src/atlas/stages.py` + `tests/fixtures/routing_ground_truth.json`
- [ ] **T1.2** — `RunContext` / `GateDecision` / `StageOutcome` dataclasses
- [ ] **T1.3** — `StateStore` (tasks.md + .atlas/current-run + consistency check)
- [ ] **T1.4** — `Pipeline` skeleton with stub runner + fake prompter
- [ ] Unit tests: `test_routing_fixture_match`, `test_pipeline_step_advances_on_approve`, `test_pipeline_resume_after_compaction`, `test_pipeline_step_writes_example_on_reject`

## Phase 2 — plumb integration

- [ ] **T2.1** — `PlumbIO` wrapper (`record_span`, `record_user_signal`, `write_example`, `close_run`)
- [ ] **T2.2** — Wire real `PlumbIO` into `Pipeline.step`
- [ ] **T2.3** — Examples row on rejection (sha256 hashes per plumb ID format)
- [ ] Integration test: `test_pipeline_writes_full_span_tree`
- [ ] Resolve **D2** before this phase ships

## Phase 3 — Worktree + gate-4 hand-off

- [ ] **T3.1** — `WorktreeManager` (create / merge_back / cleanup; path containment)
- [ ] **T3.2** — `Pipeline.step` returns `awaiting_hook` for stage 5
- [ ] **T3.3** — `test_main_branch_isolation` integration test
- [ ] Hook idempotency test in CI

## Phase 4 — Real plugin invocation + error paths

- [x] **D1 resolved** — mapping table (C) + agent CLI invocation (A)
- [ ] **T4.1** — `SubprocessStageRunner` (list-form, timeout, capture)
- [ ] **T4.2** — `ClickPrompter` (re-prompt 3x, `q` aborts, 4 KB clamp)
- [ ] **T4.3** — Subprocess argument allow-list (`RoutingDriftError`)
- [ ] Unit tests for every plan §7 error scenario
- [ ] **D3 resolved** (per-stage timeout defaults committed to `.atlas.toml.example`)

## Phase 5 — End-to-end real run + release gates

- [ ] **T5.1** — E2E run on Flask cache-middleware target
  - [ ] 1 `runs` row with `status="success"`
  - [ ] 7 spans in expected order
  - [ ] 6 user-signal scores
  - [ ] `git log main` unchanged across run
  - [ ] Routing fixture passes
  - [ ] Resume-mid-run verified
- [ ] **T5.2** — CI gates: `pytest --cov-fail-under=80`, `ruff check`, `ruff format --check`, `mypy src`
- [ ] **T5.3** — Tag `v1.0`; update `STATUS.md`

## Cross-phase

- [ ] Pin plumb commit SHA in `pyproject.toml` (Phase 2)
- [ ] Pin DEV-ESSENTIALS + DEV-BE-PYTHON commit SHAs in `pyproject.toml` (Phase 4)
- [ ] Total atlas LoC ≤ 350 (target ~300)
- [ ] `mypy src --strict` clean across all phases
