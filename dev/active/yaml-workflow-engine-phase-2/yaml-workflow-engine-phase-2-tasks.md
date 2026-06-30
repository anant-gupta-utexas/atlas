# Tasks — YAML Workflow Engine, Phase 2 TRS

Progress checklist. Source-of-truth for design is
[`yaml-workflow-engine-phase-2-plan.md`](./yaml-workflow-engine-phase-2-plan.md).

## Current

```
phase: blocked_on_phase_1
gate:  none
next:  do not start T2.1 until T2.0 confirms Phase 1 has merged
```

## ⚠️ Blocking dependency

Phase 1 ([`dev/active/yaml-workflow-engine-phase-1/`](../yaml-workflow-engine-phase-1/)) has
**not shipped yet** as of 2026-06-29 (`src/atlas/stages.py` is still the pre-Phase-1
StrEnum shape). This TRS is written against the Phase-1-complete contract per explicit user
instruction. **T2.0 is a hard gate — verify Phase 1's exit criteria for real before
starting T2.1.**

## Tasks (flat — Phase 2 only, no sub-phases)

- [ ] **T2.0** — Verify Phase 1 exit criteria before starting any Phase 2 work (hard gate, no code)
- [ ] **T2.1** — Author `job.yaml` + `job-cli.yaml` (matched Mode-A / Mode-B pair, 4 stages each)
- [ ] **T2.2** — Implement `library_runner.py` (`LibraryStageRunner`) + `library_adapters/` (score_jobs, capture)
- [ ] **T2.3** — Implement `CompositeStageRunner` (`LIB:`/`RAW:`/plugin-command dispatch; zero `Pipeline` changes)
- [ ] **T2.4** — Render `score_fit`'s gate content end-to-end (confirm `output_text` reaches the gate prompt)
- [ ] **T2.5** — Wire `_make_pipeline()` for `job.yaml` (`CompositeStageRunner` + `content_pipeline_not_installed` error naming `job-cli`)
- [ ] **T2.6** — Add content-pipeline as an optional dependency (`pyproject.toml` extra)
- [ ] **T2.7** — Document the two-variant choice (`job` vs `job-cli`)
- [ ] **T2.8** — End-to-end test (both variants) + dev-pipeline regression re-run
- [ ] **T2.9** — CI gate updates — job extra (with/without content-pipeline installed)
- [ ] **T2.10** — Document the hub-and-spoke trigger model

## Exit criteria (TRD-v2 §14 Phase 2 + §13 #5–6, copied for tracking)

- [ ] `atlas run "..." --workflow job` produces a complete span tree (4 spans) with 3 gate scores, distinct from dev runs
- [ ] Dev and job runs coexist in the same plumb DB; metric names namespaced (`job.gate_shortlist`, `job.gate_materials`, `job.gate_done`)
- [ ] Queries by workflow `task_id` prefix return the correct subset
- [ ] Dev pipeline remains unaffected — regression suite (`test_e2e_happy_path.py` + full Phase 1 suite) green, unmodified
- [ ] content-pipeline integration is optional — `--workflow job` fails cleanly (error names `job-cli`) without it installed; `--workflow job-cli` runs the full workflow dependency-free
- [ ] `library_runner.py` ≥ 85% coverage; `library_adapters/` ≥ 80%; full suite ≥ 80% (existing gate)
- [ ] `ruff check`, `ruff format --check`, `mypy src` green, with and without the `job` extra installed

## Design decisions (see plan "Decisions made" / context.md for full detail)

- [x] #1 — Mode A (library) is the `job.yaml` default, not literal `RAW:` Mode-B shell — CONFIRMED by user 2026-06-29
- [x] #2 — `LIB:<ref>` closed-registry tool-string convention, sibling to `RAW:`
- [x] #3 — `gate_shortlist` shows a rendered shortlist report (`render_report()`), not bare pass/fail — CONFIRMED by user 2026-06-29
- [x] #4 — Missing content-pipeline → `LIB:` stage fails outright with error naming `job-cli`; Mode-B is the **shipped `job-cli.yaml`** workflow, NOT automatic runtime fallback — CONFIRMED by maintainer 2026-06-30
- [x] #5 — `ingest_postings` fails the whole stage on any source error (stricter than content-pipeline's CLI; measured pipeline assumes complete upstream data) — CONFIRMED by maintainer 2026-06-30
- [x] #6 — `CompositeStageRunner` is a new wrapper satisfying `StageRunner`; `Pipeline` itself gets zero changes
- [x] #7 — Incorporates Phase 1 commit `a70029b` (`timeout_s` generalization): `job.yaml` sets `timeout_s: 1800` on `tailor_materials` (RAW:/subprocess), omits it on `LIB:` stages (inert in-process)

## Plan-level "Resolved Decisions & Clarifications" — ALL SETTLED (maintainer, 2026-06-30)

No decisions remain open. The five items below were the plan's "Pending Decisions"; all are now resolved and binding.

- [x] #1 — `CompositeStageRunner` placement → keep in `orchestrator.py`; split to a new file only if it exceeds **~500 lines** after Phase 1 (concrete trigger, check at T2.3)
- [x] #2 — Automatic Mode-A→Mode-B fallback → **NO.** Ship `job-cli.yaml` as a second explicit workflow; error names it. "Explicit > implicit." (binds T2.1/T2.2/T2.5/T2.7)
- [x] #3 — `ingest_postings` partial-failure strictness → **keep atlas stricter.** Any source failure fails the stage; measured pipeline assumes complete upstream data (binds T2.2)
- [x] #4 — Real job-board credentials → manual prerequisite; tests mock at the use-case boundary; live runs are manual
- [x] #5 — `timeout_s` inert on `LIB:` stages → **accept the asymmetry.** In-process calls bounded by content-pipeline's own client timeouts; revisit only if a `LIB:` stage actually hangs (binds T2.1/T2.2)
