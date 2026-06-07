---
title: "atlas as a YAML-driven gated-workflow engine — analysis & v2 sketch"
status: analysis (not yet committed to v2 scope)
created: 2026-06-07
related:
  - dynamic-workflows (Claude Code feature, blog 2026-06-02)
  - plumb (measurement spine — sibling repo)
  - measured-orchestrator consumer (reuses atlas as a library)
---

# atlas as a YAML-driven gated-workflow engine

> Captured from a working session on 2026-06-07. Frames the difference between
> Claude Code's **dynamic workflows** and **atlas**, then sketches what it would
> take to generalize atlas from one frozen dev pipeline into a declarative
> engine that runs *multiple* human-gated, measured workflows defined in YAML —
> and what that change would surface at the **plumb** level.

---

## 1. The framing question

Two questions drove the session:

1. **Are dynamic workflows and atlas the same thing?** → No. They sit at
   different layers and optimize for opposite things.
2. **Could atlas run different YAML-defined workflows, each with their own
   gates, instead of only the hard-coded dev pipeline?** → Yes — and atlas is
   already ~70% there architecturally. The gate machinery is generic; only the
   *stage list* is hard-coded.

---

## 2. Dynamic workflows vs atlas — comparative analysis

### 2.1 What each one is

- **Dynamic workflows** (Claude Code, shipped ~2026-05): a **runtime primitive**.
  Claude writes a JavaScript harness *on the fly*, per task, using `agent()`,
  `parallel()`, `pipeline()`, `phase()`. Ephemeral, generic, no human gates, no
  durable measurement record. Topology is *inferred per task*.
- **atlas**: a **standalone Python CLI** — a fixed 7-stage state machine
  ("a state machine, not a framework"). `atlas run "<task>"` walks
  research → PRD → SDD+TRD → TDS → plan review → code → review, stops at six
  human gates, and writes every run as a typed span tree into plumb. Topology is
  *authored once and frozen in code*.

### 2.2 Side-by-side

| Dimension | Dynamic Workflows | atlas |
|---|---|---|
| Layer | Runtime primitive inside Claude Code | Standalone product / CLI |
| Who authors the structure | Claude, per task, at invocation | A human, once, frozen in code |
| Topology | Dynamic (fan-out, tournament, loop-until-done, classify-route) | Fixed linear 7-stage DAG |
| Lifespan | Ephemeral (resumable mid-run, not a durable artifact) | Durable — `tasks.md` is source of truth, survives compaction |
| Human role | Mostly out of the loop | Structurally *in* the loop: six hard gates |
| Measurement | None built in (token budget only) | The reason it exists — full plumb span tree |
| Concurrency | Massively parallel by design (~16 concurrent agents) | Strictly sequential, one run per repo |
| Isolation | Optional per-agent git worktrees | One worktree at exactly one stage (code gen) |
| Failure mode fought | Agentic laziness, self-preferential bias, goal drift *within one task* | State loss on session end; *absence of data* on where humans must intervene |
| Output | A synthesized result | A reviewed change **+ a measured dataset about the dev process** |

### 2.3 Where they genuinely overlap (why the comparison is fair)

1. **Both orchestrate multiple subagents with isolated context windows** rather
   than one long context. atlas's stages are "black boxes"; that's structurally
   the same insight as fan-out-and-synthesize.
2. **Both fight goal drift / lossy compaction.** The blog names goal drift after
   compaction as a core failure mode; atlas's `tasks.md` is a direct answer to
   the same problem. *Same disease, different cure* — atlas uses a durable file,
   workflows use deterministic JS control flow.
3. **Both use git worktrees as a parallelism/safety boundary.** Bun's Zig→Rust
   rewrite ("a subagent per fix in a worktree") mirrors atlas's "stage 5 runs in
   an isolated worktree; main is never touched."

### 2.4 The three sharp differences

1. **Generic primitive vs opinionated product.** The blog explicitly contrasts
   dynamic vs *static* workflows on this axis: static/hand-built harnesses are
   "more generic… need to work for all edge cases"; dynamic ones are
   "tailor-made… per invocation." atlas takes the *opposite* trade from dynamic
   workflows: narrow (dev only), fixed (7 stages), permanent (checked in, 119
   tests, 92% coverage). atlas buys reliability + legibility by giving up
   flexibility.
2. **Human-in-the-loop is a design axiom for atlas, an anti-goal for workflows.**
   atlas thesis: *"humans keep the pen on decisions, the agent does everything
   in between, and both sides of that split are measured."* The six gates *are*
   the product. Dynamic workflows run the other way — you fire one off precisely
   so you don't babysit 80 resumes. Workflows *minimize* human touches; atlas
   *quantifies* them.
3. **Measurement is atlas's reason to exist.** A dynamic workflow throws away its
   scaffolding when done (you can save the recipe, but not a run log). atlas's
   value *is* the durable span tree in plumb — typed spans, gate scores,
   rejection examples that become regression rows at zero marginal cost.
   *A dynamic workflow produces an answer; atlas produces an answer plus a
   dataset about how the answer was produced.*

### 2.5 The reframe that resolves the confusion

> **atlas is what you'd get if you took one *specific* dynamic workflow — "drive
> a task through gated stages" — and decided it was valuable enough to (a) freeze
> its topology, (b) make a human review every transition, and (c) instrument
> every step for after-the-fact analysis. Dynamic workflows are the layer you'd
> reach for to build the *next* atlas without writing Python.**

- atlas's `pipeline()`-equivalent runner *could* be reimplemented on dynamic
  workflows — but you'd still have to bolt on the three differentiators (gates,
  durable state, plumb). The blog primitives give none of those for free.
- atlas would be *bad* at the blog's headline use cases (resume ranking, naming
  tournaments, hypothesis panels) — those want dynamic topology and zero gates.

**Strategic implication:** atlas's *commodity* part (the sequential
orchestration runner) is now something Claude can author on the fly. atlas's
*defensible* part is gates + durable state + measurement — exactly what dynamic
workflows lack. The positioning to lean into is **"the measured, gated wrapper,"
not "a hand-rolled orchestrator competing with dynamic workflows on plumbing."**

---

## 3. The YAML-driven-workflows idea

The proposal: let atlas run *multiple* workflows, each defined in a YAML file,
each with its own stages and its own gates — instead of only the hard-coded dev
pipeline. e.g. a writing pipeline, a research pipeline, a finance-analysis
pipeline, an ops runbook — all human-gated and plumb-measured.

This is a **static-workflow generalization of atlas**, and it lands in a
genuinely different place from dynamic workflows: it keeps the two things
dynamic workflows *don't* give (human gates + measurement) while adding the one
thing v1 atlas lacks (more than one workflow shape). The two are
**complementary, not redundant**.

### 3.1 What's already generic in atlas (grounded in code)

`StageSpec` (`src/atlas/stages.py`) is already a pure data structure:

```python
@dataclass(frozen=True)
class StageSpec:
    index: int
    name: StageName
    span_kind: str          # "plan" | "verify" | "subagent"
    tool: str
    gate_label: GateLabel | None
    gate_index: int | None
```

A stage is *index + name + span kind + tool + optional gate*. Nothing about that
says "dev workflow." The orchestrator walks `STAGES` as a list, invokes `tool`
via `claude -p`, and stops wherever `gate_label is not None`. That loop is
already a **generic gated-pipeline runner**.

What is hard-coded is only the *content* of three things:

1. `STAGES` — the frozen tuple (`stages.py`).
2. `StageName` / `GateLabel` — `StrEnum`s with a fixed, closed member set.
3. `PLUGIN_COMMANDS` — the tool→command map (`plugin_resolver.py`), already
   overridable via `.atlas.toml [plugin_commands]`.

### 3.2 The refactor (atlas side)

1. **Loosen the enums to validated strings.** `StageName`/`GateLabel` become
   plain `str` instead of `StrEnum` members so workflow files can name their own
   stages/gates. atlas already treats them as strings everywhere (`StrEnum`), so
   this is mostly deletion.
2. **Load `STAGES` from YAML** instead of a literal tuple. A loader parses a
   workflow file into `tuple[StageSpec, ...]` — the exact type the orchestrator
   already consumes — assigning `index` and `gate_index` by enumeration.
3. **Select the workflow at run time:** `atlas run "<task>" --workflow <name>`.
   The built-in dev pipeline becomes just `dev.yaml`; it stays the default.
4. **Per-workflow tool routing.** `PLUGIN_COMMANDS` either moves into each YAML
   file or merges per-workflow. The `.atlas.toml [plugin_commands]` override
   plumbing already exists.

### 3.3 Example non-dev workflow YAML

```yaml
# ~/.atlas/workflows/blog-post.yaml
name: blog-post
stages:
  - name: outline
    span_kind: plan
    tool: consult-experts:writer
    gate: gate_outline        # human approves the outline
  - name: draft
    span_kind: plan
    tool: RAW:Write a full draft from this outline
    gate: null                # no gate — flows into fact-check
  - name: fact_check
    span_kind: verify
    tool: deep-research
    gate: gate_facts          # human approves the fact-check
  - name: edit
    span_kind: verify
    tool: code-review         # reused as a prose-review pass
    gate: gate_done
```

The gate *mechanism* is reused verbatim across workflows — only the gate
*names/positions* differ. Every gate still: stops, prompts the human, writes a
`user_signal` score, writes an `examples` row on rejection. **This is the
strongest argument the refactor is natural**: the gate machinery is already
workflow-agnostic.

### 3.4 Non-trivial tradeoffs (atlas side)

| Concern | Detail |
|---|---|
| **State file format** | `tasks.md` writes per-stage checkboxes and the resume protocol reads them back. Both must become **workflow-aware** — a `blog-post` run has different boxes than a `dev` run. The state store keys off stage names, so open-string stage names must round-trip cleanly. |
| **Worktree stage** | Stage 5 gets a worktree because it *writes code*. A blog-post workflow has no such stage. `worktree_stage` (currently a fixed index in `.atlas.toml`) must generalize to a per-stage boolean (`isolate: true`). |
| **The "300 LoC / state machine not a framework" vow** | atlas's `CLAUDE.md` explicitly forbids "a new file type (router module, **agent registry**)." A YAML workflow loader is exactly that kind of registry. This is a **deliberate v1→v2 philosophy relaxation**, not a tweak — call it out as a scope decision, don't sneak it in. |

### 3.5 Worked-example recommendation

Before touching code, **author one concrete non-dev workflow YAML end-to-end**
(writing or finance-analysis). If the YAML and the gates feel natural, the
abstraction is real. If it feels forced, that's a cheap lesson learned before a
refactor. (This is the "render candidates as concrete artifacts" discipline from
the ideation-loop skill.)

---

## 4. plumb-level changes this would surface

The plumb schema was reviewed directly (`plumb/core/entities.py`,
`plumb/adapters/_schema.py`, `SCHEMA_VERSION = 1`). The good news: **most of the
schema is already workflow-agnostic.** The places that assume "one dev pipeline"
are narrow and identifiable.

### 4.1 What already works unchanged

- **`runs` table.** `task_id` is a free-form `TEXT` string — a `blog-post` run
  just uses a different `task_id` namespace (e.g. `blog-post.<slug>`). No change.
  `parent_run_id`, model fields, token/cost fields are all workflow-neutral.
- **`scores` table.** `metric_name` is free-form `TEXT`; `scorer` already
  includes `'user_signal'` (what gates write). Gate scores from *any* workflow
  fit as-is.
- **`examples` table.** Gate-rejection promotion (`source = 'production_promotion'`,
  `origin_run_id`) is workflow-neutral. A rejected blog outline promotes to a
  regression example exactly like a rejected PRD.

### 4.2 What needs attention — `spans.kind` is a CLOSED set

This is the one hard schema constraint. `spans.kind` has a `CHECK` clause:

```sql
kind TEXT NOT NULL CHECK (kind IN ('llm', 'tool', 'subagent', 'handoff', 'plan', 'verify'))
```

and the mirror `SpanKind` StrEnum: `{llm, tool, subagent, handoff, plan, verify}`.

**Implication:** atlas stages may be *open-named*, but their `span_kind` must
map onto this **closed set**. A YAML workflow that wants a stage kind outside
those six (say, `research` or `edit` as a first-class span kind) would either:

- **(Recommended) Constrain at the atlas loader.** Validate every YAML
  `span_kind` against plumb's six allowed values *before* the run starts. Cheap,
  no plumb change, keeps the schema's CHECK as the single source of truth. Map
  domain stages onto the existing kinds (`plan` / `verify` / `subagent` / `tool`).
  This is almost certainly the right call — the six kinds are *semantic
  categories* (planning vs verifying vs delegating), not dev-specific labels, and
  most workflow stages fit one of them.
- **(Only if forced) Widen plumb's CHECK + `SpanKind` enum + bump
  `SCHEMA_VERSION`.** Adding a span kind is a schema migration: edit the DDL
  CHECK, add the enum member, increment `SCHEMA_VERSION` (currently `1`), and
  provide a migration path for existing DBs. Do this *only* if a real workflow
  genuinely cannot express a stage as one of the six — which the analysis
  suggests is unlikely.

### 4.3 Metric-name conventions (a convention gap, not a schema gap)

plumb's "ten v1 metrics" and the intervention-rate framing are implicitly
dev-pipeline-shaped (`intervention rate`, `routing top-1`, `pass^3`). With
multiple workflows writing scores into one DB, **metric names need a namespace
convention** so cross-workflow queries stay clean — e.g.
`<workflow>.<gate>.user_signal`, or a `workflow` dimension carried in
`task_id`'s prefix. This is a **naming convention to agree on**, not a schema
change (`metric_name` is free `TEXT`). Document it before the first non-dev
workflow writes scores, or the metrics layer gets muddy fast.

### 4.4 Optional: a `workflow` provenance field

Today, "which workflow produced this run" is only recoverable from a `task_id`
prefix convention. If cross-workflow analysis becomes central (e.g. "intervention
rate for *writing* workflows vs *dev* workflows"), a first-class
`runs.workflow TEXT` column would make those queries first-class instead of
string-prefix-parsing. **Not required for v1 of YAML workflows** — `task_id`
prefixing covers it — but worth noting as the natural next schema add if
multi-workflow analysis becomes a headline use case. Would be a
`SCHEMA_VERSION`-bumping migration.

### 4.5 plumb-change summary

| plumb concern | Verdict | Action |
|---|---|---|
| `runs.task_id` (free text) | ✅ works as-is | Namespace per workflow (`<workflow>.<slug>`) |
| `scores` / `user_signal` scorer | ✅ works as-is | Gate scores from any workflow fit |
| `examples` promotion | ✅ works as-is | Rejection→regression is workflow-neutral |
| `spans.kind` CHECK (closed set) | ⚠️ constraint | **Validate YAML `span_kind` against the six allowed kinds at the atlas loader.** Avoid widening plumb. |
| metric-name namespacing | ⚠️ convention | Agree a `<workflow>.<gate>` naming convention *before* first non-dev run |
| `runs.workflow` provenance | ◽ optional | Add only if cross-workflow analysis becomes central; bumps `SCHEMA_VERSION` |

**Bottom line at plumb level:** the *only* hard constraint is the closed
`spans.kind` set, and the clean fix lives in atlas (validate/map at the loader),
not in plumb. Everything else is either already-generic or a naming convention.
A plumb schema migration is *avoidable* for v1 of YAML workflows.

---

## 5. Strategic note for atlas positioning

For a measured, self-healing orchestrator built on plumb + atlas (a consumer
that reuses atlas as a library): the existence of dynamic workflows means atlas
should *not* position itself as a hand-rolled orchestrator competing on
plumbing. Its
defensible core is **gates + durable state + plumb measurement**. YAML-driven
workflows extend that moat to *any* domain (writing, research, finance, ops),
not just dev — which is a stronger product story than "another way to chain
agents." The orchestration runner is now a commodity Claude can author on the
fly; the measurement-and-gate discipline is not.

---

## 6. Open decisions (not yet made)

- [ ] Commit YAML-driven workflows to atlas v2 scope, or keep as analysis?
- [ ] Author one worked-example non-dev workflow YAML first (recommended) before any code.
- [ ] Accept the `span_kind`-maps-to-six-kinds constraint, or is a real workflow blocked by it?
- [ ] Agree the metric-name namespacing convention (`<workflow>.<gate>.<scorer>`?).
- [ ] Decide whether `runs.workflow` provenance is worth a `SCHEMA_VERSION` bump now or deferred.
- [ ] Reconcile with atlas's "300 LoC / no registry" vow — explicit v2 relaxation.

---

## 7. Phase-2 prioritization

> Added 2026-06-07. Sequences this YAML-workflow analysis against near-term
> work that consumes atlas as a *library* (its `SubprocessStageRunner`,
> `WorktreeManager`, `Pipeline`, and the `write_example`-on-gate-rejection
> shape at `orchestrator.py:319`) rather than as a workflow engine.

### 7.1 The framing the near-term work forces

When atlas is reused as a library — a caller wraps the stage runner and worktree
manager to drive its own per-task workers — it does **not** need the workflow
topology generalized. A per-task worker is a thin wrapper over the runner, not a
new YAML-defined pipeline. So **YAML-driven workflows are out of scope for that
near-term work** and should stay analysis-only until it ships.

### 7.2 Priority call

| Item | Verdict | Why |
|---|---|---|
| YAML-driven workflows (this doc, §3) | **Defer** | atlas is consumed as a library; the workflow engine isn't on the critical path. Authoring one worked-example non-dev YAML (§3.5) is the gating step *before* any code — do that first. |
| `worktree_stage` → per-stage `isolate: true` (§3.4) | **Stretch / back-pocket** | The one atlas generalization a library-consumer might touch, if a task-worker is expressed as a tiny atlas workflow. Bonus only; don't pull it forward. |
| Loosen `StageName`/`GateLabel` enums to strings (§3.2) | **Defer** | Mechanical, but pointless until a second workflow shape actually exists. |
| The "300 LoC / no registry" vow relaxation (§3.4) | **Decide later** | A deliberate v1→v2 philosophy shift, not a tweak. Don't sneak it in under time pressure. |

### 7.3 Dependency on the measurement layer

The measurement-layer concerns this doc raised (§4) interact with that layer's
own roadmap:

- The closed `spans.kind` set (§4.2) — **no measurement-layer change needed**;
  validate/map at the atlas loader. Unchanged conclusion.
- Metric-name namespacing (§4.3) and `runs.workflow` provenance (§4.4) — both
  partly subsumed by a proposed `spans.attributes` JSON column on the
  measurement layer. If that lands, per-workflow context
  (`{workflow, stage, gate}`) gets a durable home without a `task_id`-prefix
  convention or a dedicated provenance column. **Prefer the attributes column
  over the `runs.workflow` add.**

### 7.4 Net Phase-2 ordering for atlas

1. Support the near-term library-consumer with no atlas code change required.
2. Then: author one worked-example non-dev workflow YAML (§3.5) — the cheap
   go/no-go test for the whole abstraction.
3. Only if that feels natural: commit YAML-driven workflows to atlas v2, taking
   the enum-loosening + loader + `isolate` + vow-relaxation together as one
   deliberate scope decision.
