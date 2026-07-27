# atlas documentation

atlas is a local-CLI agent orchestrator that walks a human-gated, YAML-defined
workflow (dev pipeline by default) and logs every run as a typed span tree
into [plumb](https://github.com/anant-gupta-utexas/plumb). This is the entry
point into the docs — start here, then follow into whichever domain area
matches what you're trying to do.

Current shipped state: the v1 pipeline (7-stage dev workflow, 6 human gates),
the v2 YAML workflow engine (v2.0–v2.2: multi-workflow loader, `job`/`job_cli`
content-pipeline integration, CLI backend dispatch), and **loop mode through
Phase L2 (v3.0 + v3.1)** — `CodexBackend`, `loop_dev.yaml`, PR delivery, and
the `atlas loop` daemon, all verified live against a real GitHub repo on
2026-07-27. 484 tests, 1 xfail, 95% coverage. Next is Phase L3 (self-healing +
routing). See [`STATUS.md`](../STATUS.md) for the snapshot and
[`BACKLOG.md`](1_product_and_research/BACKLOG.md) for what's pending.

---

## Domain areas

- **1_product_and_research/** — *When to read:* you need to know **why**
  atlas exists, what's in scope for a given release, or what's still
  pending. Product context lives here: the
  [PRD](1_product_and_research/PRD.md), the living
  [BACKLOG](1_product_and_research/BACKLOG.md), and reusable reference
  ([PLUMB_API_REFERENCE](1_product_and_research/PLUMB_API_REFERENCE.md),
  [headless-clis-reference](1_product_and_research/headless-clis-reference.md)).
- **2_architecture/** — *When to read:* you need the **system-level how** —
  component shape, data flow, the plumb span-tree contract, NFRs, trade-offs.
  Start with [system_design.md](2_architecture/system_design.md); read this
  before touching `src/atlas/` internals or reasoning about a cross-cutting
  change.
- **3_guides/** — *When to read:* you're actually running atlas, writing a
  new workflow YAML, or debugging a stage dispatch failure. Start with
  [core_concepts.md](3_guides/core_concepts.md). These are the living,
  task-oriented references — kept in sync with shipped behavior, unlike the
  architecture docs which freeze at design time.
- **4_testing/** — *When to read:* you're adding a test, wondering what's a
  release blocker, or setting up CI locally. See
  [4_testing/index.md](4_testing/index.md).

---

## Cross-cutting: loop mode (v3)

The autonomous loop is the newest cross-cutting area — it has a design note, a
phase contract, an architecture section, two guides that changed because of
it, and a body of field evidence. Entry points, in reading order:

- **[2_architecture/TRD-v3.md](2_architecture/TRD-v3.md)** — *When to read:*
  you want the phase contract and the current shipped semantics of engines,
  telemetry, budgets, and delivery. **Start with its "Where reality diverged
  from this contract" table** — five load-bearing design assumptions were
  wrong, and that table is the index of what changed.
- **[2_architecture/system_design.md § Loop mode](2_architecture/system_design.md#loop-mode-v3)**
  — *When to read:* you want the component-level picture (`loop.py`,
  `queue_gh.py`, `triage.py`, `deliverer.py`, `loop_budget.py`) and what is
  reused verbatim from v1/v2.
- **[3_guides/cli_backends.md](3_guides/cli_backends.md)** — *When to read:*
  you're choosing or debugging an engine. Backend resolution, per-engine
  model names, telemetry, and the `codex` backend all live here.
- **Phase records, archived and frozen:**
  [`dev/archive/loop-mode-phase-L0/`](../dev/archive/loop-mode-phase-L0/),
  [`loop-mode-phase-L1/`](../dev/archive/loop-mode-phase-L1/),
  [`loop-mode-phase-L2/`](../dev/archive/loop-mode-phase-L2/). These are
  **completed** phases, not active work. The single highest-value document in
  the set is the **field-findings section** at the end of
  [`loop-mode-phase-L2-tasks.md`](../dev/archive/loop-mode-phase-L2/loop-mode-phase-L2-tasks.md)
  — eight defects that survived 400+ green tests, `mypy --strict`, and a full
  code review, because each lived on a path CI never executed. Read it before
  trusting "code-complete" as a synonym for "works".
- **[dev/active/loop-mode-phase-L3/](../dev/active/loop-mode-phase-L3/)** —
  the one loop-mode TRS that *is* active: written, not implemented.
- **[1_product_and_research/loop-mode-design.md](1_product_and_research/loop-mode-design.md)**
  — the originating design note (2026-07-21). A frozen research artifact;
  where it and TRD-v3 disagree, TRD-v3 wins.

---

## Cross-cutting: the YAML workflow engine

The v2 YAML workflow engine touches all four domains — it has a product
rationale (why generalize past one hardcoded pipeline), an architecture
(the `StageRunner`/`CliBackend` seam), a guide (the full schema reference),
and a test suite. If you're trying to understand the engine as a whole
rather than one facet of it, the guide is the right single entry point:

- **[3_guides/yaml_workflow_engine.md](3_guides/yaml_workflow_engine.md)** —
  *When to read:* this is the current, comprehensive reference for the
  engine — schema, tool-string conventions (`RAW:`/`LIB:`/`SHELL:`), runner
  dispatch chain, backend selection, workflow resolution order, and
  phase-by-phase build history. Treat this as the source of truth over the
  design-time TRD-v2 for anything about *current mechanics*.
- **[2_architecture/TRD-v2.md](2_architecture/TRD-v2.md)** — the design
  document that specified the engine before it was built (now a historical
  planning record — see its status banner).
- **[1_product_and_research/BACKLOG.md](1_product_and_research/BACKLOG.md)**
  — forward-looking engine ideas not yet built (second-brain trigger skill,
  plumb schema ideas, per-backend model config).

---

## Quick links by task

| I want to... | Read |
|---|---|
| Run atlas for the first time | [3_guides/getting_started.md](3_guides/getting_started.md) |
| Understand gates, stages, the state file | [3_guides/core_concepts.md](3_guides/core_concepts.md) |
| Write a custom workflow YAML | [3_guides/yaml_workflow_engine.md](3_guides/yaml_workflow_engine.md#writing-a-custom-workflow) |
| Run the job-search workflow | [3_guides/job_workflow.md](3_guides/job_workflow.md) |
| Pick / debug a CLI backend (claude / codex / agy) | [3_guides/cli_backends.md](3_guides/cli_backends.md) |
| Set a per-engine model, or record cost/tokens | [3_guides/cli_backends.md](3_guides/cli_backends.md#model-selection-is-per-engine) |
| Run the autonomous loop | [2_architecture/TRD-v3.md](2_architecture/TRD-v3.md#38-cli-surface) §3.8, then `[loop]` config in [3_guides/getting_started.md](3_guides/getting_started.md#configuration) |
| Understand the system architecture | [2_architecture/system_design.md](2_architecture/system_design.md) |
| Know what's shipped vs. pending | [../STATUS.md](../STATUS.md), [1_product_and_research/BACKLOG.md](1_product_and_research/BACKLOG.md) |
| Add or run tests | [4_testing/index.md](4_testing/index.md) |
| Integrate with plumb from another project | [1_product_and_research/PLUMB_API_REFERENCE.md](1_product_and_research/PLUMB_API_REFERENCE.md) |
| See why atlas owns CLI dispatch, not content-pipeline | [2_architecture/TRD-v2.md](2_architecture/TRD-v2.md#34-cli-backend-dispatch-clibackend-strategy) (design rationale is archived — see [dev/archive/yaml-workflow-engine-design-notes/](../dev/archive/yaml-workflow-engine-design-notes/)) |

---

## Repo conventions

- **`docs/`** — evergreen: the single source of truth for what atlas *is*,
  numbered by domain (`1_product_and_research` → `2_architecture` →
  `3_guides` → `4_testing`).
- **`dev/active/`** — work-in-progress technical designs for features
  currently being built. Empty when nothing is in flight. Currently holds
  **only** the Phase L3 TRS; loop-mode L0–L2 have been archived.
- **`dev/archive/`** — historical record of completed features: TDS/plan/
  context/tasks documents, code reviews, and superseded design notes that
  grounded now-shipped work. Not evergreen — reflects what was true when
  written, not necessarily current behavior. The one exception worth reading
  for its own sake is the L2 field-findings log, linked above.
- **[`CLAUDE.md`](../CLAUDE.md)** — repo-wide conventions (300 LoC ethos,
  file-based state, coding style) plus the doc map used by agent sessions.
