# Tasks — `atlas.pipeline` TRS

Progress checklist. Source-of-truth for Phase scope is
`[atlas-pipeline-trs-phases.md](./atlas-pipeline-trs-phases.md)`.

## Current

```
phase: phase_5_implementation_complete
gate:  T5.1 manual E2E with real plugins (pending plugin install)
next:  tag v1.0 after T5.1 green
```

## Phase 1 — Skeleton + state machine

- [x] **T1.1** — `src/atlas/stages.py` + `tests/fixtures/routing_ground_truth.json`
- [x] **T1.2** — `RunContext` / `GateDecision` / `StageOutcome` dataclasses
- [x] **T1.3** — `StateStore` (tasks.md + .atlas/current-run + consistency check)
- [x] **T1.4** — `Pipeline` skeleton with stub runner + fake prompter
- [x] Unit tests: `test_routing_fixture_match` (5), `test_state_store` (11), `test_pipeline` (18) — 34 total, all passing

## Phase 2 — plumb integration

- [x] **T2.1** — `PlumbIO` wrapper (`record_span`, `record_user_signal`, `write_example`, `close_run`)
- [x] **T2.2** — Wire real `PlumbIO` into `Pipeline.step` (no-op stub when plumb absent; real when available)
- [x] **T2.3** — Examples row on rejection (sha256 hashes; `expected_output_hash=None`; covered in test_pipeline)
- [ ] Integration test: `test_pipeline_writes_full_span_tree` (requires plumb installed)
- **D2 resolved** — write examples with null expected_output_hash

## Phase 3 — Worktree + gate-4 hand-off

- [x] **T3.1** — `WorktreeManager` (`src/atlas/worktree.py`; create / merge_back / cleanup; path containment; dirty-repo check ignores untracked files)
- [x] **T3.2** — `Pipeline.step` creates worktree on stage 5 entry; returns `awaiting_hook` without writing `gate_commit` score
- [x] **T3.3** — `tests/integration/test_main_branch_isolation.py` (2 tests; real git repo; main log byte-identical before/after)
- [x] Unit tests: `test_worktree.py` (11 tests; subprocess mocks; list-form assertion; path containment; cleanup)
- Hook idempotency test in CI — deferred to Phase 5

## Phase 4 — Real plugin invocation + error paths

- **D1 resolved** — mapping table (C) + agent CLI invocation (A)
- [x] **T4.1** — `SubprocessStageRunner` (`src/atlas/orchestrator.py`; list-form, per-stage timeout, `capture_output=True`, `check=False`)
- [x] **T4.2** — `ClickPrompter` (inline in `orchestrator.py`; re-prompt 3x, `q` aborts, 4 KB reason clamp; `AbortedError` raised)
- [x] **T4.3** — `plugin_resolver.py` allow-list; `RoutingDriftError` raised before `subprocess.run`
- [x] Unit tests: `test_phase4.py` (18 tests; list-form assert, timeout, nonzero exit, allow-list, prompter re-prompt/abort/clamp)
- **D3 resolved** — timeouts: 600s plan stages, 1800s code_gen (in `_DEFAULT_TIMEOUT_S`)
- **D4 resolved** — ClickPrompter inline in `orchestrator.py` (~25 LoC)

## Phase 5 — End-to-end real run + release gates

- [x] **T5.1 (automated)** — `tests/e2e/test_e2e_happy_path.py` (3 tests; stub plugins; real git repo; all 5 TRD success criteria verified)
  - [x] 7 spans in expected order
  - [x] 5 orchestrator gate scores + 1 hook score = 6 total
  - [x] `git log main` unchanged across run
  - [x] Routing fixture passes (Pipeline construction validates)
  - [x] Resume protocol verified mid-run
  - [ ] **T5.1 manual** — real plugins on Flask target (pending plugin install; gates v1.0 tag)
- [x] **T5.2** — CI gates: `.github/workflows/ci.yml`
  - [x] `pytest --cov-fail-under=80` (91% achieved)
  - [x] `ruff check` + `ruff format --check` (lint job)
  - [x] `mypy src` (typecheck job)
  - [x] E2E job (runs on main + tags)
- [x] **T5.3** — `STATUS.md` updated; tag `v1.0` pending T5.1 manual gate
  - [x] `src/atlas/config.py` — TOML loader (user/repo merge)
  - [x] `src/atlas/cli.py` — Typer CLI (`atlas run`, `atlas resume`, `atlas status`, `atlas hook`)
  - [x] `src/atlas/post_commit_hook.py` — git hook writing `gate_commit` score
  - [x] `pyproject.toml` bumped to v1.0.0 with `[project.scripts]`, dev extras, coverage/ruff config

## Cross-phase

- Pin plumb commit SHA in `pyproject.toml` (Phase 2)
- Pin DEV-ESSENTIALS + DEV-BE-PYTHON commit SHAs in `pyproject.toml` (Phase 4)
- Total atlas LoC ≤ 350 (target ~300)
- `mypy src --strict` clean across all phases

