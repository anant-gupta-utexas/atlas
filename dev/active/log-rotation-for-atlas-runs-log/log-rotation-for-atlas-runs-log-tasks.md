# Tasks — Log Rotation for `.atlas/runs/*.log`

Progress checklist. Source-of-truth for design is
[`log-rotation-for-atlas-runs-log-plan.md`](./log-rotation-for-atlas-runs-log-plan.md).

## Current

```
phase: not_started
gate:  none
next:  await plan review / Pending-Decisions sign-off before starting Task 1
```

## Tasks (flat — single self-contained unit, no sub-phases)

- [ ] **T1** — Add `log_retention_days` / `log_max_count` to `Config` dataclass + `load()` (`config.py`)
- [ ] **T2** — Implement `run_logging.py`: `open_run_log`, `close_run_log`, `rotate_run_logs`, `_safe_unlink`
- [ ] **T3** — Wire rotation + log-open/close into `cli.py` `run()` and `resume()`
- [ ] **T4** — Add `atlas log-prune` command (cut if Pending Decision #2 resolves against it)
- [ ] **T5** — Unit tests: `tests/unit/test_run_logging.py` (age eviction, count eviction, protect-current-run, missing dir, non-.log ignored, best-effort on OSError)
- [ ] **T6** — Unit tests: extend `tests/unit/test_config.py` for the two new keys (defaults + repo-over-user precedence)
- [ ] **T7** — Integration test: sequential-runs rotation demo (extend e2e happy-path test)
- [ ] **T8** — Docs: update TRD.md Logging paragraph, system_design.md on-disk-state table, STATUS.md backlog line

## Exit criteria (plan §"Phase Deliverables", copied for tracking)

- [ ] `.atlas/runs/<run_id>.log` writer with age+count rotation, wired into `atlas run` and `atlas resume`
- [ ] All new/extended tests passing
- [ ] `ruff check`, `ruff format --check`, `mypy src` green
- [ ] TRD.md / system_design.md / STATUS.md no longer say "no rotation in v1"

## Pending decisions (must resolve before/while implementing — see plan for full option tables)

- [ ] #1 — Confirm or adjust numeric defaults: `log_retention_days=14`, `log_max_count=500`
- [ ] #2 — Include `atlas log-prune` (T4) in this cut, or defer it?
- [ ] #3 — Confirm this phase is lifecycle/rotation infrastructure only (no subprocess-output capture into the log body) — follow-up issue for content capture, not bundled here
- [ ] #4 — Confirm the pre-existing `[plumb]`-table-vs-flat-key docs/code mismatch stays out of scope

_This TRS is a standalone operational backlog item (v1 TRD's own deferred "v1.1 backlog: log rotation" line, STATUS.md:96), not one of TRD-v2's four numbered Development Phases — it does not touch any YAML-workflow-engine seam._
