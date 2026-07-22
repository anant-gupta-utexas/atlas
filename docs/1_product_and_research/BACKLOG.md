---
title: atlas — backlog
status: living document
last_updated: 2026-07-15
---

# Backlog

The single source of pending/future work for atlas. When a design note or
TRD gets archived, forward-looking ideas that haven't been built move here
instead of disappearing with the doc. Checked items stay checked briefly
for context, then get deleted — git history is the permanent record.

Grouped by theme, not by origin doc. Each item notes where it came from.

---

## Release / process

- [ ] Tag `v2.2` in git — all v2.2 acceptance criteria are met (per `STATUS.md`).
- [ ] Install plumb as a versioned dependency instead of a local path
      dependency, to unlock durable span/score writes outside the author's
      machine. *(from STATUS.md v1.1 backlog)*
- [ ] Add the `CONTENT_PIPELINE_TOKEN` repo secret so CI's `test-job-extra`
      leg actually exercises the real `LIB:` adapter-import path
      (`tests/integration/test_job_adapters_real_import.py`). Currently
      self-skips silently without it. *(from `.github/workflows/ci.yml`,
      `docs/4_testing/index.md`)*

## Loop mode (v3) — planning locked, not yet built

Autonomous, minimal-input development loop on top of the v2 engine. Design note:
[`loop-mode-design.md`](loop-mode-design.md); phase contract:
[`../2_architecture/TRD-v3.md`](../2_architecture/TRD-v3.md); architecture:
[`../2_architecture/system_design.md`](../2_architecture/system_design.md#loop-mode-v3).
Each phase below is detailed into its own per-phase TRS via `dev-docs-be`.

- [ ] **Phase L0 — honest baseline** *(→ v3.0)*. Version reconcile + tag `v2.2`;
      first-ever live attended `atlas run` on the real `claude` backend;
      `ClaudeCodeBackend` loop-mode JSON telemetry (cost/tokens → plumb);
      headless permission profile; `Deliverer`/`GhPrDeliverer` (push branch +
      `gh pr create` + `cleanup()`, retiring the dead `merge_back()` path).
- [ ] **Phase L1 — CodexBackend + `loop_dev.yaml`** *(→ v3.0)*. `codex exec
      --json` backend registered in `_KNOWN_BACKENDS`; ungated loop workflow
      (`plan → code_gen → verify`); Codex section added to
      `headless-clis-reference.md`.
- [ ] **Phase L2 — the loop daemon** *(→ v3.1)*. `queue_gh.py` (GitHub Issues
      adapter) + `loop.py` (tick / run_forever / reconcile_orphans + two-lane
      triage + budgets + circuit breaker); `[loop]` config; `atlas loop
      run|start|stop|status|attach` (tmux for observability only).
- [ ] **Phase L3 — self-healing + routing** *(→ v3.2)*. Pre-PR plumb judge gate;
      diagnosis-injected single child-run retry (`parent_run_id`); failed runs
      → plumb examples; score-informed engine/workflow routing (stretch).
- [ ] **Phase L4 — scale-out** *(→ v3.3)*. Second target repo (plumb backlog);
      `concurrency > 1` (lift the `.atlas/current-run` single-run assumption via
      per-run state keys); weekly cost-per-landed-PR report.

## v1.1-era carryforward (pre-YAML-engine backlog, still open)

- [ ] **Log rotation.** `.atlas/runs/<run_id>.log` has no rotation; logs
      accumulate until manually cleaned. Land a policy once disk usage is a
      real problem. *(from STATUS.md, TRD.md §Deployment)*
- [ ] **HTTP shell + mobile trigger.** A thin FastAPI/Flask layer around the
      CLI so a mobile shortcut can trigger `atlas run`. Adds auth, request
      validation, a small queue. This is also the natural point to revisit
      the atlas ↔ plumb boundary as IPC rather than direct in-process calls
      (request lifetimes and plumb writes have different failure semantics).
      *(from PRD.md future-releases table, system_design.md, TRD.md)*
- [ ] **plumb v2 `add_example` on `RunHandle`.** Durable span/score writes
      need a first-class way to add an `examples` row mid-run rather than
      the current gate-rejection-only path. *(from STATUS.md v1.1 backlog)*
- [ ] **Bounded auto-retry in the worktree (v1.2-era idea).** Stage 5 retries
      `/verify` failures automatically with a hard iteration cap. This is
      where paired `examples` rows (failed span → passing span) start
      appearing at zero marginal authoring cost. *(from system_design.md
      Future Considerations)*
- [ ] **`/dev-resume` slash command.** Replaces the `CLAUDE.md`
      resume-instruction paragraph once drift from manual re-briefing is
      felt twice. *(from PRD.md, system_design.md)*
- [ ] **`runs.kind` schema column.** Currently `runs.kind` is absent; if a
      genuinely new run kind beyond named workflows appears, add a single
      column + backfill existing rows to `"dev_workflow"`. Largely superseded
      by the v2 workflow-namespacing convention (`<workflow>.<gate_label>` in
      `metric_name`, `task_id` prefixing) — revisit only if that convention
      proves insufficient. *(from system_design.md, TRD.md Resolved Decisions)*

## Manual verification pending

- [ ] **T3.8 — Antigravity (`agy`) manual smoke test.** Not yet attempted;
      requires the `agy` binary installed and a working `GEMINI_API_KEY` /
      `GOOGLE_API_KEY`. `agy` dispatch is otherwise only exercised in CI via
      mocked subprocess calls. *(from STATUS.md, yaml_workflow_engine.md
      Phase 3 notes)*

## plumb-side ideas (not required for current atlas scope)

- [ ] **`runs.workflow` provenance column.** Today "which workflow produced
      this run" is only recoverable via `task_id` prefix convention
      (`job.<slug>` vs `dev.<slug>`). A first-class `runs.workflow TEXT`
      column would make cross-workflow analysis queries first-class instead
      of string-prefix parsing. Only worth it if cross-workflow analysis
      becomes a headline use case — this bumps plumb's `SCHEMA_VERSION`.
      *(extracted from archived `yaml-driven-workflows-analysis.md` §4.4)*
- [ ] **`spans.attributes` JSON column (plumb-side proposal).** Would give
      per-workflow context (`{workflow, stage, gate}`) a durable structured
      home, potentially subsuming both the metric-namespacing convention and
      the `runs.workflow` column above. Proposed in plumb's own
      `deferred-features.md` / `phase-2-prioritization.md` (2026-06-07) —
      not an atlas-owned decision, but worth tracking here since it would
      change how atlas namespaces metrics if it lands. *(extracted from
      archived `yaml-driven-workflows-analysis.md` §7.3)*
- [ ] **Widen plumb's `spans.kind` CHECK constraint.** Only if a real
      workflow genuinely cannot express a stage as one of the existing six
      kinds (`llm`, `tool`, `subagent`, `handoff`, `plan`, `verify`) — so far
      every shipped workflow (`dev`, `job`, `job_cli`) fits within the six.
      Treat as unlikely to be needed; the loader-side validation approach has
      held. *(extracted from archived `yaml-driven-workflows-analysis.md` §4.2)*

## Workflow engine — not yet built

- [ ] **Second-brain trigger skill (TRD-v2 Phase 4).** An ai-workx skill
      (new `job-pipeline` skill, or extended `chief-of-staff`) invoked from a
      second-brain vault session that shells `atlas run --workflow job ...`
      and routes results back as markdown to
      `docs/01_professional/job_applications/`. Explicitly out of scope for
      the atlas repo — the skill itself lives in ai-workx, not here. Atlas's
      job here is limited to staying a clean CLI target for that skill to
      shell out to. *(from TRD-v2.md §14 Phase 4, archived
      `job-workflow-scope.md` §3 Phase 3)*
- [ ] **Model selection within a backend via `.atlas.toml`.** Currently the
      `agy` backend's default model (`gemini-flash-lite`) is only overridable
      per-stage or globally via the YAML `backend:` field — there's no
      `.atlas.toml` knob for it yet, unlike the `[models]` table atlas
      already has for the dev pipeline. *(from `docs/3_guides/cli_backends.md`)*
- [ ] **`orchestrator.py` file-size split.** At 746 LoC it is over the
      400/800-line file guidance in `CLAUDE.md`, flagged during the v2.2
      guide write-up as a future split candidate (mirrors the Phase 2 split
      that produced `composite_runner.py`). *(from
      `docs/3_guides/core_concepts.md` §Size and scope)*
