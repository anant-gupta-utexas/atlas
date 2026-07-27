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

- [ ] **Re-enable CI triggers.** `.github/workflows/ci.yml` is
      `workflow_dispatch`-only on both `origin/main` (`dd299d8`) and the
      loop-mode branch, so no job runs on push or PR. Restoring
      `on: [push, pull_request]` is a one-line decision, deferred to the
      maintainer. *(found 2026-07-27)*

- [ ] **`origin/main` is ~45 commits behind the local loop-mode work.**
      `origin/main` sits at the pre-Phase-2 commit `bc4a6aa` plus two small
      commits; every Phase 2 / Phase 3 / L0 / L1 / L2 commit exists only on
      the local branch, now proposed as PR #6 (47 commits). Two consequences
      worth knowing before merging: the loop daemon bases its worktrees on
      **local `main`**, so the T-L2.13 smoke PRs were cut against that stale
      base; and the `LIB: ` YAML bug (from `53359e4`) is likewise absent from
      `origin/main`, which is why GitHub only started reporting "Invalid
      workflow file" once the branch was pushed. *(found 2026-07-27)*

- [ ] Tag `v2.2` in git — **created locally 2026-07-27** at `bc4a6aa`
      (annotated). **Not pushed** — run `git push origin v2.2` to publish.
      `pyproject.toml` reads `2.2.0` (Phase L0, T-L0.2).
- [ ] Consider CI-automated release tagging (create/push `git tag vX.Y` on
      merge to `main` when `pyproject.toml`'s version changes), instead of the
      current manual-tag process. Not built in Phase L0 — flagged as a future
      convenience, not a requirement. *(from Phase L0 TRS, T-L0.2)*
- [ ] Install plumb as a versioned dependency instead of a local path
      dependency, to unlock durable span/score writes outside the author's
      machine. *(from STATUS.md v1.1 backlog)* — **Blocked on plumb-side
      release work, checked 2026-07-27:** plumb is not published to any
      index, and its git tags stop at `v1.0.1` even though its
      `pyproject.toml` reads `1.1.0`. So the two candidate forms are both
      unavailable today: PyPI (`plumb==1.1.0`) needs a publish, and a git-ref
      pin (`plumb @ git+https://…@v1.1.0`) needs that tag to exist. **Tag
      plumb v1.1.0 first** — that is the actual next action, and it belongs
      in the plumb repo, not here. Note the swap also costs the editable
      install that makes local atlas+plumb co-development work, so it wants a
      dev-extra escape hatch.
- [ ] Add the `CONTENT_PIPELINE_TOKEN` repo secret so CI's `test-job-extra`
      leg actually exercises the real `LIB:` adapter-import path
      (`tests/integration/test_job_adapters_real_import.py`). Currently
      self-skips silently without it. *(from `.github/workflows/ci.yml`,
      `docs/4_testing/index.md`)* — **Maintainer-only:** needs a PAT with
      `repo` scope on `anant-gupta-utexas/content-pipeline`, created by hand
      and added via repo Settings → Secrets. Note this leg is currently moot
      anyway — see the CI-triggers item below.

## Loop mode (v3) — planning locked, not yet built

Autonomous, minimal-input development loop on top of the v2 engine. Design note:
[`loop-mode-design.md`](loop-mode-design.md); phase contract:
[`../2_architecture/TRD-v3.md`](../2_architecture/TRD-v3.md); architecture:
[`../2_architecture/system_design.md`](../2_architecture/system_design.md#loop-mode-v3).
Each phase below is detailed into its own per-phase TRS via `dev-docs-be`.

- [x] **Phase L0 — honest baseline** *(→ v3.0)* — **DONE 2026-07-27**, incl. T-L0.8/T-L0.9. Version reconcile + tag `v2.2`;
      first-ever live attended `atlas run` on the real `claude` backend;
      `ClaudeCodeBackend` loop-mode JSON telemetry (cost/tokens → plumb);
      headless permission profile; `Deliverer`/`GhPrDeliverer` (push branch +
      `gh pr create` + `cleanup()`, retiring the dead `merge_back()` path).
- [x] **Phase L1 — CodexBackend + `loop_dev.yaml`** *(→ v3.0)* — **DONE 2026-07-27**, incl. T-L1.1/T-L1.8. `codex exec
      --json` backend registered in `_KNOWN_BACKENDS`; ungated loop workflow
      (`plan → code_gen → verify`); Codex section added to
      `headless-clis-reference.md`.
- [x] **Phase L2 — the loop daemon** *(→ v3.1)* — **DONE 2026-07-27**, incl. T-L2.13. TRD-v3 §13 #1-#8 all proven live. `queue_gh.py` (GitHub Issues
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
- [x] **DONE (2026-07-25) — `GhPrDeliverer`'s branch-safety check is exact-match
      only.** Fixed both ways the entry proposed: `_PROTECTED_BRANCHES`
      (`main`/`master`/`trunk`/`develop`) compared after a `refs/heads/` strip,
      lowercase, and whitespace trim, *plus* a `git symbolic-ref` probe of
      `origin/HEAD` that catches an unusually-named default branch (e.g.
      `production`). The probe is local, read-only, and **fails open** — a
      missing `origin/HEAD` is common in fresh clones and must not block
      delivery. Covered by a 10-case parametrized rejection test, an
      unusual-default-branch test, a fail-open test, and an empty-branch test.
      *(L1 code review action #5)*

## `job` workflow — adapter re-targeting

- [ ] **Re-target `LIB:content_pipeline.score_jobs` adapter to the decomposed
      ingest/prep/score API.** `src/atlas/library_adapters/score_jobs_adapter.py`
      still imports content-pipeline's pre-split `ScoreJobsUseCase`, which no
      longer exists — content-pipeline decomposed it into
      `application/use_cases/score_jobs_ingest.py`,
      `score_jobs_prep.py`, and `score_jobs_score.py` (+ `score_merge.py`).
      `tests/integration/test_job_adapters_real_import.py::test_score_jobs_adapter_real_import_success`
      is `xfail(strict=False)`-marked pending this (Phase L0, T-L0.3) — it is a
      real, correct drift signal, not a broken test. Re-targeting means
      designing how the adapter composes ingest → prep → score; this is
      `job`-workflow scope, unrelated to loop mode. *(from Phase L0 TRS,
      T-L0.3, confirmed against `/Users/anant/PersonalProjects/content-pipeline`
      2026-07-21)*

## Manual verification pending

- [ ] **T3.8 — Antigravity (`agy`) manual smoke test.** Still open. Checked
      2026-07-27: the `agy` binary **is** installed (`~/.local/bin/agy`), so
      the only blocker left is credentials — neither `GEMINI_API_KEY` nor
      `GOOGLE_API_KEY` is set in the environment. Export one and the smoke is
      a single `atlas run --backend agy` away (`--backend` now exists, added
      2026-07-27). Until then `AntigravityBackend.preflight()` fails closed
      with no subprocess spawned, which is itself the tested behavior. `agy` dispatch is otherwise only exercised in CI via
      mocked subprocess calls. *(from STATUS.md, yaml_workflow_engine.md
      Phase 3 notes)*

## plumb-side ideas (not required for current atlas scope)

- [x] **DONE (plumb v1.1.0, adopted by atlas 2026-07-27) — plumb P1-a
      `RunHandle.set_usage()`.** plumb shipped `set_usage(tokens_in,
      tokens_out, dollar_cost)` plus split `spans.tokens_in`/`tokens_out`
      columns. atlas now writes run-level `dollar_cost` via
      `PlumbIO.set_usage()` and leaves the token fields unset so plumb
      auto-fills them from buffered spans. This is what made
      `max_dollars_per_day` a real budget rather than a documented intention
      — `atlas loop status` reports actual spend. Verified live:
      `$2.5822 / $5.00` across two loop runs.

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
