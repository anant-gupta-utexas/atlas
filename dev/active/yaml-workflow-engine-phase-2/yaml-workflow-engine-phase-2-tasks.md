# Tasks — YAML Workflow Engine, Phase 2 TRS

Progress checklist. Source-of-truth for design is
[`yaml-workflow-engine-phase-2-plan.md`](./yaml-workflow-engine-phase-2-plan.md).

## Current

```
phase: complete
gate:  none
next:  all tasks done
```

## Note — T2.4 widened `GatePrompter.ask()` (a real, narrow `Pipeline` touch)

T2.3 verified zero diff to `Pipeline`'s own source; T2.4 required one. The
`GatePrompter` Protocol's `ask()` gained `output_text: str = ""`, and
`Pipeline.step()`'s single call site (`orchestrator.py`, the `self._prompter.ask(...)`
line) now passes `outcome.output_text` through. `ClickPrompter`/`AutoPrompter`
updated to match; `ClickPrompter` prints `output_text` (if non-empty) before the
gate prompt — additive only, dev-pipeline gates with empty/already-seen
`output_text` are unaffected (verified by
`test_click_prompter_silent_when_output_text_empty`). This was confirmed with
the maintainer as in-spirit with FR-7/Resolved-Decision-#1 ("don't restructure
Pipeline for library-stage dispatch"), not a violation of it — a one-parameter
addition to an existing call site, not a restructure. All 8 test-fake
`GatePrompter` implementations across the suite were updated to match the new
signature.

## ⚠️ Naming correction — `job-cli` → `job_cli` (2026-06-30)

The plan document uses `job-cli`/`job-cli.yaml` throughout, but Phase 1's `_NAME_RE =
^[a-z][a-z0-9_]*$` (in `stages.py`, enforced by `workflow_loader.py` for both the
top-level `name:` field and `resolve_workflow()`'s CLI-supplied name) rejects hyphens.
`job-cli` fails to load and `--workflow job-cli` fails to resolve. Resolved with the
maintainer (2026-06-30): **rename to `job_cli`** (underscore) everywhere — file is
`src/atlas/workflows/job_cli.yaml`, workflow `name: job_cli`, invoked as
`--workflow job_cli`, metrics namespaced `job_cli.gate_*`. Do not loosen `_NAME_RE`
(Phase-1-delivered code; widening it for one workflow's naming preference isn't
justified). All forward references in this plan/tasks file to `job-cli` should be
read as `job_cli`.

## ⚠️ Blocking dependency — RESOLVED 2026-06-30

Phase 1 ([`dev/active/yaml-workflow-engine-phase-1/`](../yaml-workflow-engine-phase-1/)) is
complete on branch `atlas/yaml-workflow-engine-phase-1` (commits `0515425`, `b5573ff`).
`src/atlas/stages.py` now has the Phase-1 `StageSpec` shape (no `StrEnum`). T2.0 verified
for real: 156 tests pass (153 unit/integration + 3 e2e, exceeding the 153+3 claimed in
Phase 1's tasks file), Phase 1's own tasks file shows `phase: complete` with all exit
criteria checked. Note: Phase 1 is not yet merged into `main` — this branch is stacked on
the Phase 1 branch, which is expected for continuing Phase 2 work before Phase 1 ships.

## Tasks (flat — Phase 2 only, no sub-phases)

- [x] **T2.0** — Verify Phase 1 exit criteria before starting any Phase 2 work (hard gate, no code)
- [x] **T2.1** — Author `job.yaml` + `job_cli.yaml` (matched Mode-A / Mode-B pair, 4 stages each)
- [x] **T2.2** — Implement `library_runner.py` (`LibraryStageRunner`) + `library_adapters/` (score_jobs, capture)
- [x] **T2.3** — Implement `CompositeStageRunner` (`LIB:`/`RAW:`/plugin-command dispatch; zero `Pipeline` changes)
- [x] **T2.4** — Render `score_fit`'s gate content end-to-end (confirm `output_text` reaches the gate prompt)
- [x] **T2.5** — Wire `_make_pipeline()` for `job.yaml` (`CompositeStageRunner` + `content_pipeline_not_installed` error naming `job-cli`)
- [x] **T2.6** — Add content-pipeline as an optional dependency (`pyproject.toml` extra)
- [x] **T2.7** — Document the two-variant choice (`job` vs `job-cli`)
- [x] **T2.8** — End-to-end test (both variants) + dev-pipeline regression re-run
- [x] **T2.9** — CI gate updates — job extra (with/without content-pipeline installed)
- [x] **T2.10** — Document the hub-and-spoke trigger model

## Exit criteria (TRD-v2 §14 Phase 2 + §13 #5–6, copied for tracking)

- [x] `atlas run "..." --workflow job` produces a complete span tree (4 spans) with 3 gate scores, distinct from dev runs
- [x] Dev and job runs coexist in the same plumb DB; metric names namespaced (`job.gate_shortlist`, `job.gate_materials`, `job.gate_done`)
- [x] Queries by workflow `task_id` prefix return the correct subset
- [x] Dev pipeline remains unaffected — regression suite (`test_e2e_happy_path.py` + full Phase 1 suite) green, unmodified
- [x] content-pipeline integration is optional — `--workflow job` fails cleanly (error names `job_cli`) without it installed; `--workflow job_cli` runs the full workflow dependency-free
- [x] `library_runner.py` ≥ 85% coverage; `library_adapters/` ≥ 80%; full suite ≥ 80% (existing gate)
- [x] `ruff check`, `ruff format --check`, `mypy src` green, with and without the `job` extra installed (pre-existing mypy errors in yaml/plumb stubs are unrelated to Phase 2 additions)

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

- [x] #1 — `CompositeStageRunner` placement → keep in `orchestrator.py`; split to a new file only if it exceeds **~500 lines** after Phase 1 (concrete trigger, check at T2.3).
      **Trigger hit:** `orchestrator.py` was 713 lines at T2.3 (well past 500), so
      `CompositeStageRunner` landed in new file `src/atlas/composite_runner.py`
      instead. `Pipeline`'s own diff in `orchestrator.py` is empty — FR-7 holds.
- [x] #2 — Automatic Mode-A→Mode-B fallback → **NO.** Ship `job-cli.yaml` as a second explicit workflow; error names it. "Explicit > implicit." (binds T2.1/T2.2/T2.5/T2.7)
- [x] #3 — `ingest_postings` partial-failure strictness → **keep atlas stricter.** Any source failure fails the stage; measured pipeline assumes complete upstream data (binds T2.2)
- [x] #4 — Real job-board credentials → manual prerequisite; tests mock at the use-case boundary; live runs are manual
- [x] #5 — `timeout_s` inert on `LIB:` stages → **accept the asymmetry.** In-process calls bounded by content-pipeline's own client timeouts; revisit only if a `LIB:` stage actually hangs (binds T2.1/T2.2)
