# Testing

## Overview

Atlas ships 82 tests at 91% coverage. CI runs on every push and PR;
`main` is never green without all three quality gates passing:
`pytest`, `ruff check`/`ruff format`, and `mypy src`.

## Test organization

```
tests/
├── fixtures/
│   └── routing_ground_truth.json   # 7-row stage → tool mapping
├── unit/
│   ├── test_state_machine.py       # 7-stage ordering, gate transitions
│   ├── test_span_tree.py           # span emission shape per stage
│   ├── test_hook_parser.py         # post-commit stdout → scores
│   ├── test_config.py              # TOML layering, validation
│   └── test_cli.py                 # CLI entry points (thin)
└── conftest.py                     # shared fixtures (tmp dirs, mock plumb)
```

No integration or E2E test directories in CI. One manual E2E run against
a real throwaway repo is done once per release (see "E2E validation" below).

## Mandatory tests

These five test scenarios are release blockers. A failing fixture test
blocks the `v1.0` tag even if all other tests pass.

### 1. Routing ground-truth fixture

`tests/fixtures/routing_ground_truth.json` commits the expected
stage → tool mapping for all 7 stages. The test loads the live
`atlas.pipeline` routing table and asserts 100% match row-by-row.

This is a deterministic sanity baseline in v1. When different
orchestrator models are swapped in via `.atlas.toml`, it becomes a
real routing-accuracy measurement.

### 2. Main-branch isolation

Simulates Stage 5 executing inside a worktree, then asserts that
`git log main` is unchanged from before the run to gate 4. Verifies
that the `git worktree add` boundary holds.

### 3. Resume after compaction

Simulates a mid-run session end by dropping an intermediate `tasks.md`
and `.atlas/current-run` to disk, then starting a fresh process. Asserts
that atlas reads the first unchecked box and offers to resume — no
re-briefing.

### 4. Hook idempotency

Calls the post-commit score writer twice with the same commit SHA.
Asserts that plumb's `scores` table contains exactly one row per metric
for that SHA — no duplicates.

### 5. State consistency contract

Writes a `tasks.md` whose embedded `run_id` differs from
`.atlas/current-run`. Asserts that both `atlas run` and `atlas status`
exit non-zero and print a recovery hint naming both values.

## Coverage targets

| Module | Target | Rationale |
|--------|--------|-----------|
| `atlas.pipeline` | 80%+ | Core state machine; most logic lives here |
| `atlas.state` | 80%+ | State file read/write; critical for resume |
| `atlas.hook` | 80%+ | Score writer; idempotency tested explicitly |
| `atlas.cli` | lower acceptable | Thin entry point; tested via integration |
| `atlas.plumb_io` | lower acceptable | Mostly pass-through to plumb |

Overall project target: 80%+. Current: 91%.

## Running tests

```bash
# All tests
pytest

# With coverage report
pytest --cov=src --cov-report=term-missing

# Specific module
pytest tests/unit/test_state_machine.py

# Quality gates (must all pass before merging)
ruff check .
ruff format --check .
mypy src/
```

## Mocking strategy

Agent plugins (`DEV-ESSENTIALS`, `DEV-BE-PYTHON`) are invoked via
`subprocess.run`. In tests, mock at the `subprocess.run` boundary — do
not shell out to real plugins. Provide realistic stdout and a zero exit
code for success cases; non-zero exit codes for failure paths.

Plumb writes are mocked in unit tests via a lightweight in-memory
adapter. The `conftest.py` fixture provides `mock_plumb` which captures
span and score writes without touching SQLite.

## E2E validation

One manual E2E run per release against a throwaway Flask feature:

```bash
atlas run "add response-cache middleware to this Flask repo"
```

Acceptance criteria:
- One `runs` row closed with `status='success'`.
- Seven typed spans in the expected order.
- Six `user_signal` scores, each linked to the correct span.
- `git log main` unchanged from run start to gate 4.
- At least one `examples` row written from a gate rejection.

Verified via plumb queries after the run:

```bash
plumb run stats
plumb example list
```

## CI configuration

GitHub Actions on push + PR:

1. `pytest` — all unit tests plus the routing fixture.
2. `ruff check .` — linting.
3. `ruff format --check .` — formatting.
4. `mypy src/` — type checking.

All four must be green before merging to `main`. No deployment step.
