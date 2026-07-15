# atlas documentation

atlas is a local-CLI agent orchestrator that walks a human-gated, YAML-defined
workflow (dev pipeline by default) and logs every run as a typed span tree
into [plumb](https://github.com/anant-gupta-utexas/plumb). This is the entry
point into the docs — start here, then follow into whichever domain area
matches what you're trying to do.

Current shipped state: v1 pipeline (7-stage dev workflow, 6 human gates) plus
the v2 YAML workflow engine (v2.0–v2.2: multi-workflow loader, `job`/`job_cli`
content-pipeline integration, CLI backend dispatch for `claude`/`agy`). 239
tests, 95% coverage. See [`STATUS.md`](../STATUS.md) for the current snapshot
and [`BACKLOG.md`](1_product_and_research/BACKLOG.md) for what's next.

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
| Pick / debug a CLI backend (claude vs agy) | [3_guides/cli_backends.md](3_guides/cli_backends.md) |
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
  currently being built. Empty when nothing is in flight.
- **`dev/archive/`** — historical record of completed features: TDS/plan/
  context/tasks documents, code reviews, and superseded design notes that
  grounded now-shipped work. Not evergreen — reflects what was true when
  written, not necessarily current behavior.
- **[`CLAUDE.md`](../CLAUDE.md)** — repo-wide conventions (300 LoC ethos,
  file-based state, coding style) plus the doc map used by agent sessions.
