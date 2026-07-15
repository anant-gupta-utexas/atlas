---
project: atlas
status: v2.2 — Phase 3 (CLI backend dispatch) complete
last_updated: 2026-07-15
next_gate: tag v2.2 (user-discretionary)
blocked_on: null
---

# atlas — status

## Current

**v2.2 is complete.** 239 tests pass at 95% coverage.

Shipped: the v1 7-stage dev pipeline (6 human gates, git worktree boundary,
post-commit hook, full plumb span-tree integration) plus the v2 YAML
workflow engine — a multi-workflow loader (`dev`/`job`/`job_cli` + custom
YAML), `LIB:`/`SHELL:`/`RAW:`/plugin-command runner dispatch, and CLI
backend selection (`claude` / `agy`, 4-tier resolution). Full phase-by-phase
build history lives in git log and in
[`dev/archive/yaml-workflow-engine-phase-{1,2,3}/`](dev/archive/).

## Module coverage

| Module | File | Status |
| --- | --- | --- |
| CLI entry point | `src/atlas/cli.py` | ✅ |
| Stage table + StageSpec | `src/atlas/stages.py` | ✅ |
| State machine | `src/atlas/orchestrator.py` | ✅ |
| State store | `src/atlas/state.py` | ✅ |
| plumb wrapper | `src/atlas/plumb_io.py` | ✅ |
| Worktree manager | `src/atlas/worktree.py` | ✅ |
| Plugin resolver | `src/atlas/plugin_resolver.py` | ✅ |
| TOML config | `src/atlas/config.py` | ✅ |
| Post-commit hook | `src/atlas/post_commit_hook.py` | ✅ |
| CLI backend dispatch | `src/atlas/cli_backend.py` | ✅ |
| YAML workflow loader | `src/atlas/workflow_loader.py` | ✅ |
| Composite/library/shell runners | `composite_runner.py`, `library_runner.py`, `shell_runner.py` | ✅ |

## Next

See [`docs/1_product_and_research/BACKLOG.md`](docs/1_product_and_research/BACKLOG.md)
for the full pending list. Top items: tag `v2.2`, install plumb as a
versioned (not path) dependency, add the `CONTENT_PIPELINE_TOKEN` CI secret,
run the T3.8 `agy` manual smoke test.

## Pointers

- Docs hub: `docs/README.md`
- PRD: `docs/1_product_and_research/PRD.md`
- Backlog: `docs/1_product_and_research/BACKLOG.md`
- TRD (v1): `docs/2_architecture/TRD.md`
- TRD (v2): `docs/2_architecture/TRD-v2.md`
- System design: `docs/2_architecture/system_design.md`
- YAML workflow engine guide: `docs/3_guides/yaml_workflow_engine.md`
