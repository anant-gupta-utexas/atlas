---
project: atlas
status: building
phase: v1 (Week 4) — design complete, implementation planning phase
last_updated: 2026-05-01
next_gate: Phase 1 — CLI skeleton + 7-stage state machine
blocked_on: null
---

# atlas — status

## Current

TRS (Technical Requirements Specification) for `atlas.pipeline` finalized.
All four design decisions (D1–D4) resolved. Implementation plan locked across
5 phases with effort estimates and cross-phase dependencies documented. Ready
to start Phase 1: skeleton state machine.

## Recent (last 7 days)

- **TRS v1 complete** (2026-04-27 – 2026-05-01):
  - Design contract: method signatures, plumb API bindings, gate loop pseudocode,
    error matrix, security/performance budgets.
  - 5 implementation phases: skeleton → plumb → worktree → real plugins → E2E
    (target ~335 LoC, slightly over 300 but acceptable for v1).
  - Decision log: D1 (plugin resolver table + agent CLI), D2 (examples with null
    expected_output_hash), D3 (600s/1800s timeouts), D4 (inline prompter).
- SDD finalized (2026-04-24): resolved all four PRD open questions, Mermaid
  diagrams, trade-offs and risks.
- TRD approved (Tech Lead pass 2026-04-24): atlas↔plumb boundary, plugin
  lifecycle, state-consistency contract, `runs.kind` deferral.

## Next

- **Phase 1** (Day 1–2): CLI skeleton + state machine stub
  - T1.1: stage table + routing fixture
  - T1.2: RunContext/GateDecision/StageOutcome dataclasses
  - T1.3: StateStore (tasks.md + .atlas/current-run)
  - T1.4: Pipeline with stub runner + fake prompter
  - Unit tests: routing match, step advance, resume, rejection path
- **Phase 2** (Day 3–4): plumb integration
  - Real PlumbIO wrapper, span/score writes, examples row on rejection
  - Integration test: full span tree shape
- **Phase 3** (Day 5): worktree + gate-4 hand-off
  - WorktreeManager, main-branch isolation test
- **Phase 4** (Day 6–7): real plugin invocation + error paths
  - SubprocessStageRunner, ClickPrompter, allow-list validation
  - All §7 error scenarios covered
- **Phase 5** (Day 8): E2E + release gates
  - Real run on throwaway target; coverage/lint gates; v1.0 tag

## v1 module coverage

The TRS covers `atlas.pipeline` and its collaborators. The three remaining
modules (`cli`, `config`, `hook`) are owned within the same TRS phases but
were scoped out of the design sections. Full picture:

| Module | File | Est. LoC | TRS coverage | Status |
| --- | --- | --- | --- | --- |
| CLI entry point | `src/atlas/cli.py` | 25 | Out of scope (thin Typer wrapper) | Not started |
| Stage table + enums | `src/atlas/stages.py` | 25 | T1.1 | Not started |
| State machine | `src/atlas/orchestrator.py` | 110 | T1.2, T1.4, T2.2, T3.2, T4.1, T4.2, T4.3 | Not started |
| State store | `src/atlas/state.py` | 60 | T1.3 | Not started |
| plumb wrapper | `src/atlas/plumb_io.py` | 30 | T2.1, T2.3 | Not started |
| Worktree manager | `src/atlas/worktree.py` | 35 | T3.1 | Not started |
| Plugin resolver | `src/atlas/plugin_resolver.py` | 15 | T4.3 (D1 decision) | Not started |
| TOML config | `src/atlas/config.py` | 20 | Out of scope (load + freeze only) | Not started |
| Post-commit hook | `src/atlas/post_commit_hook.py` | 30 | Out of scope (separate process) | Not started |
| Routing fixture | `tests/fixtures/routing_ground_truth.json` | — | T1.1 | Not started |
| CI workflow | `.github/workflows/ci.yml` | — | T5.2 | Not started |
| **Total** | | **~350** | | |

All modules are v1 blockers. `cli.py`, `config.py`, and `post_commit_hook.py`
have no TRS but are small enough to implement directly against the SDD +
TRD specs. Flag for a quick spec pass before Phase 4 if they grow unexpectedly.

## Blocked / waiting

- Nothing blocked. Phase 1 ready to start.

## Pointers

- PRD: `docs/1_product_and_research/PRD.md`
- TRD: `docs/2_architecture/TRD.md`
- SDD: `docs/2_architecture/system_design.md`
- **TRS (pipeline)**: `dev/active/atlas-pipeline-trs/`
  - [`atlas-pipeline-trs-plan.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-plan.md) — design contract
  - [`atlas-pipeline-trs-phases.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-phases.md) — 5 phases + decisions
  - [`atlas-pipeline-trs-context.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-context.md) — decisions log
  - [`atlas-pipeline-trs-tasks.md`](dev/active/atlas-pipeline-trs/atlas-pipeline-trs-tasks.md) — progress checklist
- Plumb API reference: `docs/1_product_and_research/PLUMB_API_REFERENCE.md`
