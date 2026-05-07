---
project: atlas
status: v1.0 — T5.1 manual E2E complete, all 5 TRD criteria verified
phase: v1 — COMPLETE
last_updated: 2026-05-06
next_gate: tag v1.0
blocked_on: null
---

# atlas — status

## Current

**v1.0 is complete.** All five implementation phases done, all P0/P1/P2 findings
closed, and T5.1 manual E2E passed. 119 tests pass at 92.15% coverage.

**T5.1 manual E2E results (2026-05-06):**
- Target: throwaway Flask repo at `/tmp/flask-cache-e2e`
- Task: `atlas run "add response-cache middleware" --auto-approve`
- Model: `haiku` (new default; configurable via `.atlas.toml [model]`)
- All 7 stages completed successfully (research → code_review)
- All 5 TRD v1.0 acceptance criteria verified:
  1. ✅ Run closed with `status='success'`; tasks.md all 7 boxes checked
  2. ✅ 7 spans recorded in correct stage order
  3. ✅ 6 user-signal scores (gates 0–5 all resolved)
  4. ✅ `git log main` unchanged; code committed only to worktree branch
  5. ✅ Routing fixture: 6/6 fixture tests pass, `_validate_routing_fixture()` clean

**Note on plumb integration:** plumb is not yet installed as a path dependency,
so this run used PlumbIO stub mode. Span/score data was recorded in-memory and
validated via tasks.md / process exit code. Install plumb to unlock durable DB
writes — no atlas code changes needed.

## Recent (last 7 days)

- **T5.1 manual E2E complete + Haiku default** (2026-05-06):
  - Full 7-stage pipeline ran end-to-end on throwaway Flask repo.
  - All 5 TRD v1.0 acceptance criteria verified (see Current section).
  - Added `model` config field (default `"haiku"`) to `Config` and `SubprocessStageRunner`.
  - `claude --model haiku` now passed to every stage subprocess; overrideable via `.atlas.toml [model]`.

- **T5.1 closure fixes complete** (2026-05-06):
  - P0: Resume child-run handoff with parent_run_id tracking and active run id propagation
  - P1: Original task text persistence (base64 in tasks.md) and rehydration on resume
  - P1: Durable rejection example persistence via plumb._storage_writer.write_example()
  - P2: Hook idempotency dedupe on (run_id, commit_sha, metric)
  - P2: Real latency_ms measurement (time.monotonic) in Pipeline.step()
  - P2: Same-process context drift fix via Pipeline._latest_ctx and run_to_completion() update
  - P2: Rationale threading to plumb add_score(rationale=...)
  - Added 8 comprehensive unit tests (test_t51_closure.py); 119 total tests pass at 92.13% coverage.
  - Commit: 0546620

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

- **Tag `v1.0`** — all criteria met; cut the release tag.
- **Install plumb** as a path dependency to unlock durable span/score writes in real mode.
- **v1.1 backlog**: log rotation, HTTP shell boundary, plumb v2 `add_example` on RunHandle.

## Pointers

- PRD: `docs/1_product_and_research/PRD.md`
- TRD: `docs/2_architecture/TRD.md`
- SDD: `docs/2_architecture/system_design.md`
- **TRS (pipeline)**: `dev/active/atlas-pipeline-trs/`
  - [`atlas-pipeline-trs-plan.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-plan.md) — design contract
  - [`atlas-pipeline-trs-phases.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-phases.md) — 5 phases + decisions
  - [`atlas-pipeline-trs-tasks.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-tasks.md) — progress checklist
