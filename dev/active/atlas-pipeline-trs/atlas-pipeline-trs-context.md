# Context — `atlas.pipeline` TRS

Reference notes for anyone picking up this work cold.

## Key files

### Source-of-truth docs (read first, in order)
- [`docs/1_product_and_research/PRD.md`](../../../docs/1_product_and_research/PRD.md) — product scope, FRs, gate semantics, user stories.
- [`docs/2_architecture/TRD.md`](../../../docs/2_architecture/TRD.md) — NFRs, integrations, success criteria, mandatory tests.
- [`docs/2_architecture/system_design.md`](../../../docs/2_architecture/system_design.md) — module diagram, data flow, trade-off log.

### TRS itself (this directory)
- [`atlas-pipeline-trs-plan.md`](./atlas-pipeline-trs-plan.md) — design (sections 1–11, appendix).
- [`atlas-pipeline-trs-phases.md`](./atlas-pipeline-trs-phases.md) — phased implementation plan + pending decisions.
- [`atlas-pipeline-trs-tasks.md`](./atlas-pipeline-trs-tasks.md) — checkbox progress.

### Code targets (do not exist yet)
- `src/atlas/orchestrator.py` — main subject.
- `src/atlas/stages.py` — static stage table.
- `src/atlas/state.py` — tasks.md + .atlas/current-run.
- `src/atlas/plumb_io.py` — plumb wrapper.
- `src/atlas/worktree.py` — git worktree.
- `tests/fixtures/routing_ground_truth.json` — release-blocker fixture.

## Decisions made (during this TRS)

| # | Decision | Rationale |
| - | --- | --- |
| 1 | TRS targets `atlas.pipeline` only (Q1 = B). | Pipeline is the only module with non-trivial logic; everything else is I/O at edges. Spec the seam, ship the rest cheaply. |
| 2 | Target the CLAUDE.md flat layout `src/atlas/*.py` (Q2 = A). | The Clean Architecture scaffold under `src/domain/...` would blow past the 300 LoC budget. CLAUDE.md is the source of truth on layout. |
| 3 | Use the provided plumb API reference verbatim (Q3 = #p). | Saves a derive-then-verify round trip. plumb's `add_span` buffers status at write time — wrapper redesigned around `record_span(...)` to fit. |
| 4 | Stage 3 has no gate of its own; gate 3 is on stage 4. | PRD §FR table is explicit: `gate_tds` attaches to `verify:plan_review`, not `plan:tds_gen`. The TRS makes this an explicit `gate_label=None` in the stage table. |
| 5 | Gate 4 (`gate_commit`) is hook-written, not orchestrator-written. | PRD + SDD both place this in the post-commit hook. Orchestrator returns `awaiting_hook` from stage 5 step. |
| 6 | `Pipeline` holds no run state; every method takes a `RunContext`. | Makes resume trivial — rebuild ctx from disk, call `step()`. No in-memory invariants to preserve across processes. |
| 7 | `add_span` is recorded *after* the stage completes (not opened on entry). | Matches plumb's buffer-once API. Status is known by the time atlas writes the span — there's no reason to open early. |

## Decisions still pending (see [`atlas-pipeline-trs-phases.md`](./atlas-pipeline-trs-phases.md) §"Pending Decisions")

- **D1 — Plugin command resolution.** Blocker for Phase 4. Recommendation: small mapping table in `plugin_resolver.py`.
- **D2 — `examples.expected_output` nullability.** Blocker for Phase 2 if plumb forbids null. Need plumb-author confirmation.
- **D3 — Per-stage timeout defaults.** Soft — we ship with guessed defaults, tune after Day-5 run.
- **D4 — Whether prompter is its own file.** Soft — judged during Phase 4 implementation.

## Integration points

| Direction | Surface | Failure mode | Test coverage |
| --- | --- | --- | --- |
| `atlas.pipeline` → plumb | Direct in-process Python (`plumb.run`, `add_span`, `add_score`, `Example`) | Plumb logs internally, never raises (per plumb API ref). Missing rows surface as "incomplete tree" downstream. | Integration test against in-memory plumb (`test_pipeline_writes_full_span_tree`). |
| `atlas.pipeline` → DEV-ESSENTIALS / DEV-BE-PYTHON plugins | `subprocess.run([...], capture_output=True, check=False, timeout=...)` | Non-zero exit = `error_type="plugin_nonzero_exit"`. Timeout = `error_type="plugin_timeout"`. Run halts. | Unit (`test_pipeline_step_handles_plugin_nonzero_exit`, `..._timeout`). |
| `atlas.pipeline` → git (worktree) | `subprocess.run(["git", "worktree", "add", ...])` | Dirty repo or path collision = `WorktreeError`. Halt before stage 5 span. | Integration (`test_main_branch_isolation`). |
| `atlas.pipeline` → `tasks.md` (atlas.state) | Atomic file write (`.tmp` + rename) | Partial write impossible by construction. Mismatched run_id raises `StateInconsistencyError`. | Unit (`test_pipeline_state_inconsistency_refuses`). |
| `atlas.hook` → plumb | Same in-process API, but in a separate post-commit subprocess. | Hook reads `.atlas/current-run` for run_id; on parse failure logs and continues (best-effort, per TRD §Risks). | Integration (`test_hook_idempotency`). |
| `atlas.cli` → `atlas.pipeline` | Plain Python calls (`Pipeline.start`, `run_to_completion`). | Exceptions surface as friendly CLI errors via Typer's exception handler. | Out of scope for this TRS. |

## Routing fixture format

```json
[
  {"stage_index": 0, "stage_name": "research",    "expected_tool": "consult-experts:research", "expected_span_kind": "plan"},
  {"stage_index": 1, "stage_name": "prd_draft",   "expected_tool": "consult-experts:pm",        "expected_span_kind": "plan"},
  {"stage_index": 2, "stage_name": "trd_draft",   "expected_tool": "consult-experts:tech-lead", "expected_span_kind": "plan"},
  {"stage_index": 3, "stage_name": "tds_gen",     "expected_tool": "dev-docs-be",               "expected_span_kind": "plan"},
  {"stage_index": 4, "stage_name": "plan_review", "expected_tool": "plan-reviewer",             "expected_span_kind": "verify"},
  {"stage_index": 5, "stage_name": "code_gen",    "expected_tool": "code-gen-agent",            "expected_span_kind": "subagent"},
  {"stage_index": 6, "stage_name": "code_review", "expected_tool": "code-review",               "expected_span_kind": "verify"}
]
```

Confirm `expected_tool` strings against the actual plugin command names during Phase 1 — these are best-guesses pending D1.

## Where the LoC budget goes (estimate)

| Module | Estimated LoC | Notes |
| --- | --- | --- |
| `orchestrator.py` | 110 | Pipeline class + protocols + main loop. |
| `state.py` | 60 | tasks.md format + atomic write + consistency check. |
| `plumb_io.py` | 30 | Wrapper over plumb; mostly straight-through calls. |
| `worktree.py` | 35 | git worktree subprocess wrapper. |
| `stages.py` | 25 | Stage table + enums (data, not logic). |
| `cli.py` | 25 | Typer entrypoints (out of scope here). |
| `config.py` | 20 | TOML merge + frozen Config. |
| `post_commit_hook.py` | 30 | Standalone script; runs in a separate process. |
| **Total** | **335** | Slightly over the ~300 target — D1 resolution may bring it back under. |

If the total trends past 350, Phase 4's prompter or runner gets dropped to a Should-Have and ships in v1.0.1.
