# Testing

## Overview

Atlas ships 239 tests at 95% coverage as of v2.2. CI is manual-only (`workflow_dispatch`) — this is a single-maintainer repo, so the suite is the local pre-commit gate rather than an on-push check. When run, it enforces the same quality gates: `pytest`, `ruff check`/`ruff format`, and `mypy --strict src`.

## Test organization

```
tests/
├── fixtures/
│   └── routing_ground_truth.json   # 7-row stage → tool/span_kind mapping (dev pipeline only)
├── unit/
│   ├── test_workflow_loader.py     # YAML parsing + validation + resolution order
│   ├── test_pipeline.py            # stage ordering, gate transitions, resume
│   ├── test_state_store.py         # tasks.md read/write, workflow field, first_unchecked
│   ├── test_cli_backend.py         # ClaudeCodeBackend/AntigravityBackend argv + parsing
│   ├── test_composite_runner.py    # LIB:/SHELL:/default dispatch
│   ├── test_shell_runner.py        # SHELL: allow-list, subprocess errors
│   ├── test_library_runner.py      # LIB: registry, not-installed, adapter exception
│   ├── test_library_adapters.py    # score_jobs_adapter + capture_adapter (mocked use-cases)
│   ├── test_non_dev_workflow.py    # synthetic non-dev workflow: namespaced metrics, resume
│   ├── test_config.py              # TOML layering, default_backend field
│   ├── test_routing_fixture_match.py   # dev.yaml matches routing_ground_truth.json
│   ├── test_phase4.py              # SubprocessStageRunner backend wiring
│   ├── test_worktree.py            # git worktree lifecycle
│   ├── test_remediation.py         # gate rejection + re-run
│   ├── test_review_fixes.py        # code review gate flow
│   └── test_t51_closure.py         # stage 5 → stage 6 transition
├── integration/
│   ├── test_job_workflow_e2e.py    # job + job_cli workflows end-to-end (mocked use-cases)
│   ├── test_cli_backend_dispatch.py  # agy dispatch, mixed-backend workflow, dev regression
│   ├── test_job_adapters_real_import.py  # real content-pipeline import (skipped if extra absent)
│   └── test_main_branch_isolation.py    # git worktree does not touch main
├── e2e/
│   └── test_e2e_happy_path.py      # full 7-stage dev run with stub plugins
└── conftest.py                     # shared fixtures (tmp dirs, mock plumb, stub runners)
```

E2E tests are excluded from the default `pytest` run (`--ignore=tests/e2e` in `pyproject.toml`). Run them explicitly: `pytest tests/e2e -m e2e`.

## Mandatory tests

These tests are release blockers. A failure blocks any version tag.

### 1. Dev-pipeline parity

`test_dev_pipeline_parity` in `test_workflow_loader.py` asserts that `load_workflow_file(dev_yaml_path)` produces a `StageSpec` tuple field-by-field identical to the old hardcoded `STAGES` tuple (7 stages, same tool/span_kind/gate_label/gate_index values, `isolate=True` only on `code_gen`, `gate_is_async=True` only on `gate_commit`, `timeout_s=None` on all 7). This is the regression guard for the engine generalization.

### 2. Routing ground-truth fixture

`test_routing_fixture_match.py` loads `dev.yaml` via `workflow_loader` and asserts 100% match against `tests/fixtures/routing_ground_truth.json` (7 rows, one per dev-pipeline stage: `stage_name` → `expected_tool` + `expected_span_kind`).

### 3. Main-branch isolation

`test_main_branch_isolation.py` simulates stage 5 (`code_gen`) executing inside a worktree, then asserts that `git log main` is unchanged from before the run to gate 4. Verifies that the `git worktree add` boundary holds.

### 4. Resume after compaction

Simulates a mid-run session end by writing an intermediate `tasks.md` and `.atlas/current-run` to disk, then starting a fresh process. Asserts atlas reads the first unchecked box and resumes correctly. Also covers the v2 case: the `workflow:` field in `tasks.md` is re-read and the workflow YAML is re-resolved.

### 5. Hook idempotency

Calls the post-commit score writer twice with the same commit SHA. Asserts plumb's `scores` table contains exactly one row per metric for that SHA. The test also covers the v2 behavior: non-dev workflow gate metric names are namespaced (`<workflow>.<gate_label>`).

### 6. Backend byte-identity

`test_claude_code_backend_argv_byte_identical_to_phase2` asserts that `ClaudeCodeBackend.build_argv()` output is byte-identical to the hardcoded argv list from before Phase 3's refactor. If the backend abstraction drifts from the exact subprocess call dev pipeline needs, this fails.

### 7. Auth preflight security test

`test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` mocks `subprocess.run` to raise `AssertionError` if invoked, then runs an `agy` stage with no API key env vars set. Asserts `agy_missing_auth_env` is returned without ever spawning a subprocess. This is the load-bearing test for the security boundary: no browser OAuth fallback on headless sessions.

### 8. Non-dev workflow namespacing

`test_non_dev_workflow.py` drives a synthetic `job` workflow through a sync gate and the async hook gate. Asserts that `job.gate_shortlist` (not `gate_shortlist`) and `job.gate_shipped` (not `gate_shipped`) are the metric names written to plumb. Verified to fail against pre-fix code, making these genuine regression guards, not tautologies.

## Coverage targets

| Module | Target | Achieved (v2.2) |
|---|---|---|
| `workflow_loader.py` | ≥ 90% | 100% |
| `cli_backend.py` | ≥ 85% | 100% |
| `library_runner.py` | ≥ 85% | ≥ 85% |
| `library_adapters/` | ≥ 80% | ≥ 80% |
| `orchestrator.py` | ≥ 80% | ≥ 80% |
| `state.py` | ≥ 80% | ≥ 80% |
| Repo-wide | ≥ 80% | 95% |

## Running tests

```bash
# All unit + integration tests (default)
pytest

# With coverage report
pytest --cov=src --cov-report=term-missing

# Specific test file
pytest tests/unit/test_workflow_loader.py

# E2E tests (excluded from default run; run explicitly)
pytest tests/e2e -m e2e

# Job-extra real-import tests (requires uv sync --extra job)
pytest tests/integration/test_job_adapters_real_import.py

# Quality gates (must all pass before merging)
ruff check .
ruff format --check .
mypy --strict src/
```

## Mocking strategy

**`SubprocessStageRunner` and backends:** Mock at the `subprocess.run` boundary. Do not shell out to `claude` or `agy` in tests. Provide realistic stdout and a zero exit code for success cases; non-zero for failure paths.

**`LibraryStageRunner` adapters:** Mock at the content-pipeline use-case class boundary (`ScoreJobsUseCase`, `CaptureUseCase`), not at `_import_adapter`. This ensures the real adapter module body runs and catches future API mismatches between the adapter and content-pipeline.

**`ShellStageRunner`:** Mock at `subprocess.run`, or place a stub binary on `PATH` in the test fixture.

**Plumb writes:** Use `PlumbIO(real=False)` — an in-memory adapter that captures span and score writes without touching SQLite. Available via the `mock_plumb` fixture in `conftest.py`.

**Workflow files:** Use `tmp_path` fixtures to write YAML files in-test; or load real built-in workflows from `src/atlas/workflows/` when testing parity (not mocking).

## CI configuration

GitHub Actions on push and PR:

1. `pytest` — all unit + integration tests (E2E excluded by default).
2. `ruff check .` — linting.
3. `ruff format --check .` — formatting.
4. `mypy --strict src/` — type checking.

Two CI legs for the job extra:
- **`test` job** — `uv sync --extra dev` (no content-pipeline). Exercises the `content_pipeline_not_installed` failure path.
- **`test-job-extra` job** — checks out content-pipeline from the private repo (requires `CONTENT_PIPELINE_TOKEN` repo secret) and runs `uv sync --extra dev --extra job`. This job self-skips with a GitHub notice when the secret is absent. **Until the secret is added, the real `LIB:` adapter-import path is not exercised in CI.**

## E2E validation

One `test_e2e_happy_path.py` run per release against a throwaway feature. Must pass completely unmodified — any change to this file signals a dev-pipeline contract change.

Acceptance criteria:
- One `runs` row closed with `status='success'`.
- 7 typed spans in order with correct `span_kind` values.
- 5 orchestrator-written gate scores + 1 hook-written `gate_commit` score.
- `git log main` unchanged from run start to gate 4 (worktree boundary holds).
- Resume mid-run reconstructs the correct stage from `tasks.md`.

Verified via plumb queries after the run:

```bash
plumb run stats
plumb example list
```
