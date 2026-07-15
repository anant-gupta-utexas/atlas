---
title: atlas — job-automation workflow scope (first non-dev YAML workflow) + hub-and-spoke model
status: design-note
created: 2026-06-28
last_reviewed: 2026-06-28
related:
  - yaml-driven-workflows-analysis.md   # the abstract engine plan this instantiates
  - cli-backend-dispatch.md             # how atlas dispatches to claude -p / agy -p
  - ../content-pipeline/headless-clis-reference.md
tags: [atlas, content-pipeline, job-automation, yaml-workflows, second-brain, architecture]
---

# Job-automation workflow in atlas — scope & hub-and-spoke model

Answers three linked questions: (1) does the "second-brain is my surface, everything
else is tooling" model hold? (2) can atlas consume content-pipeline's tools directly?
(3) what's the scope of adding a **job** workflow alongside the dev workflow?

**Key reframe:** the job workflow is not a greenfield ask — it is the **first
worked-example non-dev YAML workflow** that
[`yaml-driven-workflows-analysis.md`](./yaml-driven-workflows-analysis.md) §3.5 says to
author *before* committing the engine generalization. Decision (2026-06-28): pursue the
**YAML-driven** engine path, not a Python-coded registry.

---

## 1. The hub-and-spoke model (validated, with one correction)

Mental model: **second-brain = the surface I interact with; atlas = workflow engine;
content-pipeline = tools; ai-workx = plugins; plumb = measurement.** This holds.

**Correction:** second-brain is **markdown-only by hard constraint** (vault `CLAUDE.md`:
plain markdown, no build systems/frontend; it's the private *data layer*). So second-brain
is the **trigger surface + data/output store**, but cannot *contain* orchestration code.
The trigger is a **skill** (lives in ai-workx), not vault markdown, because markdown can't
execute.

```
You ──(NL / slash command)──▶ Claude Code session in second-brain
                                      │
                                      ▼  invokes
                        ai-workx skill ("run job workflow")
                                      │  shells out to
                                      ▼
                 atlas  ──run --workflow job "<seed>"──┐
                          │                            │
        consumes as library│                          │ dispatches judgment stages
                            ▼                          ▼  via claude -p / agy -p
                  content-pipeline use-cases     (CliBackend, see cli-backend-dispatch.md)
                            │                          │
                       plumb spans ◀──── instrumentation┘
                            │
                  outputs written back to
                            ▼
        second-brain  docs/01_professional/job_applications/
```

Net: **you talk to second-brain → a skill triggers atlas → atlas drives content-pipeline
tools + agentic CLIs → plumb measures → results land back in second-brain.** Exactly the
model described; the only nuance is the executable trigger lives in ai-workx.

---

## 2. Can atlas consume content-pipeline directly? Yes — three modes.

content-pipeline is clean-architecture: use-cases are plain classes with
constructor-injected ports (e.g. `ScoreJobsUseCase(llm_client, meta_store, archive_reader,
...)`, `src/application/use_cases/score_jobs.py`), shipped as a `content-pipeline` console
script (`pyproject.toml [project.scripts]`). Three consumption modes:

| Mode | How | Use when | Notes |
| --- | --- | --- | --- |
| **A. Library import** | `pip install -e ../content-pipeline`; atlas calls `ScoreJobsUseCase(...).run_pending()` in-process | Stage is a deterministic content-pipeline op (capture, classify, score_jobs, research) | **Preferred.** Structured results, no subprocess. |
| **B. CLI subprocess** | atlas runs `content-pipeline score-jobs ...` via `SubprocessStageRunner` | Want process isolation, or the op is already a polished CLI command | Reuses existing dispatch; loses structured return |
| **C. Agentic CLI** | atlas runs `claude -p` / `agy -p` for judgment steps | Step needs an *agent*, not a deterministic function (tailor a cover letter, decide fit) | The `CliBackend` work — see [`cli-backend-dispatch.md`](./cli-backend-dispatch.md) |

**A real job workflow mixes all three:** deterministic scrape/score = Mode A; judgment
(draft tailored materials, apply/skip) = Mode C. That heterogeneity is the whole point of
atlas being a pipeline of typed stages.

**Dependency direction (unchanged rule):** `atlas → content-pipeline`. content-pipeline
never imports atlas. content-pipeline already has the job-relevant surface today:
`score_jobs` use-case, `JobScore` entity, `cmd_score_jobs`, `score_jobs_report`.

---

## 3. Scope of adding a job workflow

### Reality check: atlas is single-workflow today

`Pipeline` imports the one module-level `STAGES` tuple (`orchestrator.py:12`), indexes it
positionally (`STAGES[stage.index + 1]`), and `_validate_routing_fixture()` raises
`RoutingDriftError` if anything drifts from the dev pipeline. **There is exactly one
workflow, hard-wired.** So "add a second workflow" requires generalizing the engine first —
it is not a config change today.

### The work, in phases

The analysis doc already specs the engine generalization (§3.2 refactor, §3.4 tradeoffs,
§4 plumb impact). This note sequences the *job* instance on top of it.

**Phase 0 — Worked-example YAML (gating go/no-go, ~half a session, no code).**
Author `job.yaml` end-to-end as a concrete artifact *before* touching atlas code (the §3.5
discipline). Proposed stages:

```yaml
# ~/.atlas/workflows/job.yaml
name: job
stages:
  - name: ingest_postings    # Mode A: content-pipeline capture/scrapers
    span_kind: tool
    tool: RAW:content-pipeline capture --source job-boards
    gate: null
  - name: score_fit          # Mode A: ScoreJobsUseCase against rubric
    span_kind: verify
    tool: RAW:content-pipeline score-jobs --pending
    gate: gate_shortlist     # human approves the shortlist
  - name: tailor_materials   # Mode C: agentic — draft tailored CV/cover per role
    span_kind: subagent
    tool: RAW:Tailor application materials for each shortlisted role
    gate: gate_materials     # human approves drafts
  - name: emit_package       # write to docs/01_professional/job_applications/
    span_kind: tool
    tool: RAW:Assemble the application package
    gate: gate_done
```

If the YAML + gates feel natural, the abstraction is real; if forced, cheap lesson before a
refactor. **Decision gate:** only proceed to Phase 1 if this feels clean.

**Phase 1 — Engine generalization (the bulk; the deferred v2 scope).**
Take these together as one deliberate scope decision (from analysis §3.2 / §3.4):
1. Loosen `StageName` / `GateLabel` from `StrEnum` to validated strings.
2. Load `STAGES` from YAML into `tuple[StageSpec, ...]` (loader assigns `index`,
   `gate_index`).
3. `atlas run --workflow <name>`; the dev pipeline becomes the default `dev.yaml`.
4. Make `_validate_routing_fixture` per-workflow (or dev-only).
5. Generalize `worktree_stage` (fixed index) → per-stage `isolate: true` (job has no code
   stage, so no worktree).
6. Per-workflow tool routing; validate every `span_kind` against plumb's **closed six**
   (`llm/tool/subagent/handoff/plan/verify`) **at the loader** — no plumb change.
7. Explicitly relax atlas's "300 LoC / no registry" vow — this is the v1→v2 philosophy
   shift, not a tweak.

**Phase 2 — content-pipeline as a library (small).**
`pip install -e ../content-pipeline` into atlas; replace `RAW:content-pipeline ...` shell
stages with direct use-case calls (Mode A) where structured results matter.

**Phase 3 — second-brain trigger (small, in ai-workx).**
A thin skill (new `job-pipeline`, or extend `chief-of-staff`) invoked from a vault session;
shells `atlas run --workflow job ...`, reports results back as markdown, outputs route to
`docs/01_professional/job_applications/`.

### plumb impact (from analysis §4 — mostly already generic)

- `runs.task_id` free text → namespace as `job.<slug>`. No schema change.
- `scores` / `user_signal` (gate scores) and `examples` (rejection→regression) are
  workflow-neutral. A rejected shortlist promotes exactly like a rejected PRD.
- **Only hard constraint:** `spans.kind` is a closed set — map job stages onto the six at
  the loader (done in Phase 1 step 6). No plumb migration for v1.
- Agree the `<workflow>.<gate>.<scorer>` metric-name convention before the first job run.
  (A proposed plumb `spans.attributes` JSON column, if it lands, subsumes this — see
  analysis §7.3.)

---

## 4. Net answer

- **Model holds:** second-brain is your surface + data store; atlas/content-pipeline/
  ai-workx/plumb are associated tooling. The executable trigger is an ai-workx skill, not
  vault markdown.
- **atlas can consume content-pipeline directly** (library preferred, CLI/agentic as
  needed); `atlas → content-pipeline`, never the reverse.
- **Scope is dominated by Phase 1** (generalize atlas to multi-workflow / YAML). That's the
  deferred v2 work from the analysis doc. Once paid **once**, *every* future workflow (job,
  finance, content) is just a YAML stage list + a runner mapping. Pay the engine cost once,
  get N workflows.
- **Do Phase 0 first** — author `job.yaml` as a concrete artifact; it's the cheap go/no-go
  for the whole engine generalization.

### Sequencing flag

The analysis doc deferred YAML-driven workflows **past the symphony-measured / shipwright
hackathon**, which consumes atlas as a *library* and does not need the engine generalized
(§7). Confirm that hackathon has shipped (or is no longer the priority) before starting
Phase 1 here — otherwise Phase 0 (author `job.yaml`) is the only part to do now.

## Cross-references

- Engine plan this instantiates: [`yaml-driven-workflows-analysis.md`](./yaml-driven-workflows-analysis.md)
- CLI dispatch (Mode C): [`cli-backend-dispatch.md`](./cli-backend-dispatch.md)
- content-pipeline job surface: `src/application/use_cases/score_jobs.py`, `src/domain/entities/job_score.py`, `src/infrastructure/cli/cmd_score_jobs.py`
- Job-application outputs land in: [`../../01_professional/job_applications/`](../../01_professional/job_applications/)
- atlas runner seam: `src/atlas/orchestrator.py` (`StageRunner`, `SubprocessStageRunner`, `Pipeline`)
