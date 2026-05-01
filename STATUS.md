---
project: atlas
status: v1.0 implementation complete — pending real-plugin E2E run
phase: v1 — all 5 implementation phases done
last_updated: 2026-05-01
next_gate: T5.1 manual E2E run on Flask cache-middleware target (gated on real plugin install)
blocked_on: null
---

# atlas — status

## Current

All five implementation phases of the `atlas.pipeline` TRS are complete.
The codebase is feature-complete for v1.0. 82 tests (34 unit Phase 1 + 11
unit Phase 2 + 13 unit Phase 3 + 19 unit Phase 4 + 5 config/state + 3 E2E)
pass at 91% coverage. CI gates are configured in `.github/workflows/ci.yml`.

The only remaining step before the v1.0 tag is **T5.1**: a manual E2E run
on a throwaway Flask repo with real agent plugins installed.

## Recent (last 7 days)

- **Phase 5 complete** (2026-05-01):
  - `src/atlas/config.py` — TOML loader with user/repo merge.
  - `src/atlas/cli.py` — Typer CLI (`atlas run`, `atlas resume`, `atlas status`, `atlas hook`).
  - `src/atlas/post_commit_hook.py` — git hook that writes the `gate_commit` score.
  - `tests/e2e/test_e2e_happy_path.py` — 3 automated E2E tests (stub plugins) covering all 5 TRD success criteria.
  - `.github/workflows/ci.yml` — 4-job CI: unit+integration (coverage ≥80%), lint, mypy, E2E.
  - `pyproject.toml` updated to v1.0.0 with `atlas` entry point, dev extras, coverage/ruff config.

- **Phase 4 complete** (2026-05-01):
  - `src/atlas/orchestrator.py` — `SubprocessStageRunner` (list-form, per-stage timeouts, capture_output), `ClickPrompter` (re-prompt 3×, 4 KB clamp, `AbortedError`), allow-list check.
  - `src/atlas/plugin_resolver.py` — 7-tool mapping table + `resolve()` function.
  - `tests/unit/test_phase4.py` — 18 tests covering all §7 error scenarios.

- **Phase 3 complete** (2026-05-01):
  - `src/atlas/worktree.py` — `WorktreeManager` (`create`, `merge_back`, `cleanup`), path containment, dirty-repo guard.
  - Stage 5 hand-off: Pipeline creates worktree before invoking code_gen; no main-branch commits.
  - `tests/integration/test_main_branch_isolation.py` — 2 real-git-repo tests.
  - `tests/unit/test_worktree.py` — 15 unit tests (subprocess mocks).

- **Phases 1 + 2 complete** (2026-04-27 – 2026-05-01):
  - `src/atlas/stages.py`, `src/atlas/orchestrator.py`, `src/atlas/state.py`, `src/atlas/plumb_io.py`.
  - 34 unit tests for Phase 1 state machine; plumb wrapper with stub/real mode.

## v1 module coverage

| Module | File | Est. LoC | Status |
| --- | --- | --- | --- |
| CLI entry point | `src/atlas/cli.py` | 90 | ✅ Done |
| Stage table + enums | `src/atlas/stages.py` | 47 | ✅ Done |
| State machine | `src/atlas/orchestrator.py` | 446 | ✅ Done |
| State store | `src/atlas/state.py` | 155 | ✅ Done |
| plumb wrapper | `src/atlas/plumb_io.py` | 204 | ✅ Done |
| Worktree manager | `src/atlas/worktree.py` | 186 | ✅ Done |
| Plugin resolver | `src/atlas/plugin_resolver.py` | 35 | ✅ Done |
| TOML config | `src/atlas/config.py` | 68 | ✅ Done |
| Post-commit hook | `src/atlas/post_commit_hook.py` | 100 | ✅ Done |
| Routing fixture | `tests/fixtures/routing_ground_truth.json` | — | ✅ Done |
| CI workflow | `.github/workflows/ci.yml` | — | ✅ Done |

## Next

- **T5.1 manual E2E** — run `atlas run "add response-cache middleware"` on a throwaway
  Flask repo with real agent plugins installed. Verify all 5 TRD success criteria:
  1. One `runs` row, `status="success"`.
  2. 7 spans in expected order.
  3. 6 user-signal scores.
  4. `git log main` unchanged.
  5. Routing fixture passes.
- Once T5.1 is green, tag `v1.0`.

## Pointers

- PRD: `docs/1_product_and_research/PRD.md`
- TRD: `docs/2_architecture/TRD.md`
- SDD: `docs/2_architecture/system_design.md`
- **TRS (pipeline)**: `dev/active/atlas-pipeline-trs/`
  - [`atlas-pipeline-trs-plan.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-plan.md) — design contract
  - [`atlas-pipeline-trs-phases.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-phases.md) — 5 phases + decisions
  - [`atlas-pipeline-trs-tasks.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-tasks.md) — progress checklist
