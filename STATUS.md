---
project: atlas
status: v2.2 — Phase 3 (CLI backend dispatch) complete
last_updated: 2026-06-30
next_gate: tag v2.2 (user-discretionary)
blocked_on: null
---

# atlas — status

## Current

**v2.2 (Phase 3) is complete.** 239 tests pass at 95% coverage.

Shipped in v2.2: `CliBackend` Protocol, `ClaudeCodeBackend`, `AntigravityBackend`, 4-tier backend resolution, `Config.default_backend` from `.atlas.toml [backend] default`.

### YAML workflow engine (Phases 1–3, complete)

**Phase 3** — CLI backend dispatch. `CliBackend` Protocol extracted; `ClaudeCodeBackend` (byte-identical argv) and `AntigravityBackend` (`agy`, auth fail-closed on missing `GEMINI_API_KEY`) added. Backend resolved via 4-tier cascade: per-stage YAML > workflow default > `.atlas.toml` > hard `"claude"`. 239 tests, 95% coverage, 100% on `cli_backend.py`.

**Phase 2** — `job` workflow (content-pipeline integration). `LIB:` tool-string convention added; `LibraryStageRunner` maps a closed registry to content-pipeline adapters (`ingest_postings`, `score_fit`). `job-cli.yaml` shipped as the dependency-free `RAW:`-only variant. Missing content-pipeline install fails fast and names `job-cli` as the alternative. Gate output rendered via `score_jobs_report.render_report()`. 220 tests.

**Phase 1** — core loader and `StageSpec` data model. `dev.yaml` extracted from hardcoded `STAGES`; `workflow_loader.py` added; `stages.py` switched from `StrEnum` to dataclass-per-stage. `_DEFAULT_TIMEOUT_S` generalized into per-stage `timeout_s` with 4-tier resolution. Routing fixture scoped to `dev`-only. 193 tests.

### Pipeline (v1, complete)

**Phase 5 + T5.1 closure** — CLI (`atlas run`, `atlas resume`, `atlas status`, `atlas hook`), TOML config loader, post-commit hook. Closure fixes: parent_run_id handoff, original task text persistence, hook idempotency, real latency_ms measurement. 119 tests at 92% coverage. Manual E2E (2026-05-06): full 7-stage pipeline on throwaway Flask repo, all 5 TRD v1.0 acceptance criteria verified (PlumbIO stub mode).

**Phase 4** — `SubprocessStageRunner` (list-form argv, per-stage timeouts, capture_output), `ClickPrompter` (3× re-prompt, 4 KB clamp, `AbortedError`), `plugin_resolver.py` (7-tool mapping table). 18 new tests.

**Phase 3** — `WorktreeManager` (`create`, `merge_back`, `cleanup`), path containment, dirty-repo guard. Stage 5 hand-off creates worktree before `code_gen`; no main-branch commits.

**Phases 1–2** — `stages.py`, `orchestrator.py` (7-stage state machine, 6 human gates), `state.py`, `plumb_io.py` (stub/real mode). 34 unit tests.

## Module coverage

| Module | File | Status |
| --- | --- | --- |
| CLI entry point | `src/atlas/cli.py` | ✅ |
| Stage table + enums | `src/atlas/stages.py` | ✅ |
| State machine | `src/atlas/orchestrator.py` | ✅ |
| State store | `src/atlas/state.py` | ✅ |
| plumb wrapper | `src/atlas/plumb_io.py` | ✅ |
| Worktree manager | `src/atlas/worktree.py` | ✅ |
| Plugin resolver | `src/atlas/plugin_resolver.py` | ✅ |
| TOML config | `src/atlas/config.py` | ✅ |
| Post-commit hook | `src/atlas/post_commit_hook.py` | ✅ |
| CLI backend dispatch | `src/atlas/cli_backend.py` | ✅ |
| YAML workflow loader | `src/atlas/workflow_loader.py` | ✅ |

## Next

- Tag `v2.2` — all criteria met.
- Install plumb as a path dependency to unlock durable span/score writes.
- v1.1 backlog: log rotation, HTTP shell boundary, plumb v2 `add_example` on RunHandle.
- T3.8 manual smoke test: pending (requires `agy` binary + `GEMINI_API_KEY`).

## Pointers

- PRD: `docs/1_product_and_research/PRD.md`
- TRD: `docs/2_architecture/TRD.md`
- SDD: `docs/2_architecture/system_design.md`
- YAML workflow engine: `docs/3_guides/yaml_workflow_engine.md`
