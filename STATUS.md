---
project: atlas
status: v2.2 shipped; loop mode Phase L0 (honest baseline) — code complete, manual off-CI verification pending
last_updated: 2026-07-22
next_gate: T-L0.8 (first live attended run) + T-L0.9 (manual delivery smoke test)
blocked_on: null
---

# atlas — status

## Current

**v2.2 is complete.** `pyproject.toml` now reads `2.2.0` (was drifted at
`1.0.0`); `git tag v2.2` remains a manual maintainer action (tracked in
BACKLOG.md).

**Loop Mode Phase L0 ("honest baseline") is code-complete: 271 tests pass, 1
xfail, at 96% coverage.** Adds the telemetry, permission, and delivery
primitives the v3 loop depends on — no loop or `atlas loop` command yet
(that's L1/L2). Shipped this phase:

- `ClaudeCodeBackend` gains an opt-in JSON telemetry path (`--output-format
  json` + a headless permission profile), gated on `extra_flags` and absent
  by default — attended `atlas run` argv is byte-identical to pre-L0.
  `parse_result` handles the JSON envelope; a new `parse_usage()` method +
  `UsageStats` dataclass extract `total_cost_usd`/token counts.
- `PlumbIO.record_span()` gains an optional `tokens: tuple[int, int] | None`
  kwarg threaded to plumb's `RunHandle.add_span(tokens=(in, out))`. Run-level
  `dollar_cost`/token roll-up is **not** written — confirmed unreachable from
  plumb v1.0.1's online run path, deferred to plumb P1-a (BACKLOG.md).
- New `src/atlas/deliverer.py`: `Deliverer` Protocol + `GhPrDeliverer` — push
  branch → `gh pr create` → `WorktreeManager.cleanup()`. Never touches
  `main`, never force-pushes (enforced by construction + a dedicated
  security test).
- `test_score_jobs_adapter_real_import_success` is `xfail(strict=False)`
  (content-pipeline decomposed `ScoreJobsUseCase`; re-targeting is
  `job`-workflow scope — tracked in BACKLOG.md), so the suite is genuinely
  green rather than silently red.

**Not yet done (off-CI, manual, real external systems):** T-L0.8 (first live
`atlas run` against the real `claude` backend, confirming `spans.tokens` from
a real JSON envelope) and T-L0.9 (a real `GhPrDeliverer.deliver()` against a
scratch GitHub repo). These are the phase's two manual exit-criteria checks
and have not been executed yet — everything else in the Phase L0 TRS
([`dev/active/loop-mode-phase-L0/`](dev/active/loop-mode-phase-L0/)) is done.

Prior to L0: the v1 7-stage dev pipeline (6 human gates, git worktree
boundary, post-commit hook, full plumb span-tree integration) plus the v2
YAML workflow engine — a multi-workflow loader (`dev`/`job`/`job_cli` +
custom YAML), `LIB:`/`SHELL:`/`RAW:`/plugin-command runner dispatch, and CLI
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
| plumb wrapper | `src/atlas/plumb_io.py` | ✅ (+ per-span `tokens`, L0) |
| Worktree manager | `src/atlas/worktree.py` | ✅ |
| Plugin resolver | `src/atlas/plugin_resolver.py` | ✅ |
| TOML config | `src/atlas/config.py` | ✅ |
| Post-commit hook | `src/atlas/post_commit_hook.py` | ✅ |
| CLI backend dispatch | `src/atlas/cli_backend.py` | ✅ (+ loop-mode telemetry, L0) |
| YAML workflow loader | `src/atlas/workflow_loader.py` | ✅ |
| Composite/library/shell runners | `composite_runner.py`, `library_runner.py`, `shell_runner.py` | ✅ |
| Delivery primitive (new, L0) | `src/atlas/deliverer.py` | ✅ |

## Next

See [`docs/1_product_and_research/BACKLOG.md`](docs/1_product_and_research/BACKLOG.md)
for the full pending list. Immediate: T-L0.8 (live attended run) and T-L0.9
(manual delivery smoke test) to close out Phase L0, then Phase L1
(`CodexBackend` + `loop_dev.yaml`). Also open: tag `v2.2`, install plumb as a
versioned (not path) dependency, add the `CONTENT_PIPELINE_TOKEN` CI secret,
run the T3.8 `agy` manual smoke test, re-target the `score_jobs` adapter
(`job`-workflow scope).

## Pointers

- Docs hub: `docs/README.md`
- PRD: `docs/1_product_and_research/PRD.md`
- Backlog: `docs/1_product_and_research/BACKLOG.md`
- TRD (v1): `docs/2_architecture/TRD.md`
- TRD (v2): `docs/2_architecture/TRD-v2.md`
- System design: `docs/2_architecture/system_design.md`
- YAML workflow engine guide: `docs/3_guides/yaml_workflow_engine.md`
