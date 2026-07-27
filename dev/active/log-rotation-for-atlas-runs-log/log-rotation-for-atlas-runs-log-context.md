# Context — Log Rotation for `.atlas/runs/*.log`

Source of truth for design: [`log-rotation-for-atlas-runs-log-plan.md`](./log-rotation-for-atlas-runs-log-plan.md).

## Origin

GitHub issue "Log rotation for .atlas/runs/*.log" (BACKLOG). Planning-only pass per issue instructions — no implementation in this lane.

## Key finding that shapes the whole plan

**The run-scoped log writer doesn't exist in code yet.** `.atlas/runs/<run_id>.log` is documented (TRD.md:194-197, system_design.md's on-disk-state table, STATUS.md:96) but never implemented — no `FileHandler`, no `basicConfig`, nothing writes to it. The only `logging.getLogger` call anywhere in `src/atlas` is `plumb_io.py:13` (`"atlas.plumb"`), with no handler attached, so it falls through to stderr's `lastResort`. This TRS therefore scaffolds a minimal writer (open/rotate/close lifecycle only, no subprocess-output instrumentation) alongside the rotation policy — rotation logic against a nonexistent file would be untestable otherwise.

## Key files (existing, read during planning)

- `src/atlas/config.py` (71 lines) — flat-TOML-key `Config` dataclass; `load()` merges `~/.atlas/config.toml` over repo `.atlas.toml`. New keys `log_retention_days`/`log_max_count` follow the existing flat-scalar pattern used by `model` — **not** the nested `[plumb]`-table shape shown in aspirational docs (PRD.md:213-225, getting_started.md), which `config.py` never actually parses (confirmed: it reads a flat `plumb_db_path` key, config.py:27,53). Pre-existing docs/code mismatch — not fixed by this TRS (Pending Decision #4).
- `src/atlas/state.py` — `StateStore.read_current_run()` (state.py:114-119) is the sole interface rotation needs, to determine which `run_id`'s log file is protected from deletion. `.atlas/current-run` format: `run_id\nslug\n[worktree_path]\n[code_gen_span_id]`. No changes needed here.
- `src/atlas/cli.py` — `run()` (61-94) and `resume()` (97-126) are the two touchpoints where rotation + log-open/close get wired in. `_make_pipeline()` (41-58) builds `PlumbIO`/`StateStore`/`WorktreeManager`/`SubprocessStageRunner` — rotation needs `StateStore` constructed slightly earlier than today's flow, since it must read `current-run` *before* `pipeline.start()`/`resume()` can mutate it.
- `src/atlas/orchestrator.py` — `Pipeline.start()` (130-139), `.resume()` (141-194), `.run_to_completion()` (359+). Resume can mint a **new child `run_id`** via `plumb.reopen_run()` (orchestrator.py:175), rewriting `.atlas/current-run` and `tasks.md`'s run_id comment (`state.update_run_id()`, orchestrator.py:180). This is why the parent run's log becomes unprotected post-resume — documented as intentional in the plan, not a bug.
- `src/atlas/plumb_io.py` — `_make_id()` (312-314) generates the 32-char lowercase hex `run_id` in stub mode (`secrets.token_hex(16)`); real mode uses plumb's own opaque id. No timestamp embedded in the id itself — rotation must use file `mtime`, not anything parsed from the filename.

## Decisions made (with rationale — see plan §6 for full detail)

- **Retention policy: age-based primary (default 14 days) + count ceiling (default 500) as a safety net.** Rejected pure count-based (doesn't track recency, vulnerable to burst-eviction of recent logs) and pure size-based (requires summing file sizes across the directory for marginal benefit at solo-dev run volumes; revisit only if subprocess-output instrumentation later makes files large).
- **Rotation trigger: on run start** (both `atlas run` and `atlas resume`). Daemon-tick was ruled out architecturally — atlas has no background process anywhere (sync-only, no async, no HTTP shell even in TRD-v2 scope) and adding one solely for rotation would violate the project's "state machine, not a framework" constraint. A separate `atlas log-prune` command is added as a Should-have manual escape hatch, not the primary trigger.
- **Resume interaction:** rotation protects whatever `run_id` is in `.atlas/current-run` at the moment it runs (read *before* `pipeline.resume()` can rewrite it via a child-run handoff). Once resume mints a new child id, the parent id's log is no longer protected and ages out normally on a future rotation pass — deliberate, since the log file is a debugging aid, not the system of record (plumb owns that).
- **Config shape:** flat top-level scalar keys, matching the code's actual (not aspirational) TOML parsing pattern.

## Open items — see plan's "Pending Decisions & Clarifications" for full option comparisons

1. Are the numeric defaults (14 days / 500 count) right, or should they be tuned?
2. Include `atlas log-prune` in this first cut, or defer it?
3. This TRS is lifecycle/rotation infrastructure only — it does not make the log files contain anything meaningful yet (no subprocess-output capture). Confirm that's an acceptable phase boundary, with content-capture as a follow-up issue.
4. The pre-existing `[plumb]`-table-vs-flat-key docs/code mismatch is left alone — confirm that's fine to defer as a separate cleanup.

## Integration points for implementation (when this TRS is picked up)

- `src/atlas/run_logging.py` — new module (`open_run_log`, `close_run_log`, `rotate_run_logs`, `_safe_unlink`).
- `src/atlas/config.py` — two new dataclass fields + load() parsing lines.
- `src/atlas/cli.py` — `run()`/`resume()` wiring + new `log-prune` command.
- No changes needed to `orchestrator.py`, `state.py`, `plumb_io.py`, or anything in the TRD-v2 YAML-workflow-engine seam.

## Not touched (explicitly out of scope)

- `SubprocessStageRunner`, `post_commit_hook.py` — no subprocess-output-into-log instrumentation in this phase.
- `workflow_loader.py`, `dev.yaml`, TRD-v2 Appendix A seams — unrelated initiative.
- Any concurrency/locking — v1/v2 are both single-run-per-repo by explicit assumption.
