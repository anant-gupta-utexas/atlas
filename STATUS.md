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
