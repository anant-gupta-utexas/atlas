# Context — YAML Workflow Engine, Phase 2 TRS

Reference notes for anyone picking up this work cold.

## ⚠️ Read this first

**Phase 1 is not implemented yet.** As of 2026-06-29, `src/atlas/stages.py` is still the
old `StrEnum`/hardcoded-`STAGES` shape. This Phase 2 TRS is written *as if* Phase 1's TRS
(T1.1–T1.13) has already shipped, per explicit user instruction — see the plan's banner.
**[T2.0](./yaml-workflow-engine-phase-2-plan.md#tasks) is a hard verification gate: do not
start T2.1+ until Phase 1 has actually merged.**

## Key files

### Source-of-truth docs (read first, in order)
- [`docs/2_architecture/TRD-v2.md`](../../../docs/2_architecture/TRD-v2.md) — the phase contract this TRS details. §3.8, §6, §10, §14 (Phase 2) are the load-bearing sections.
- [`docs/1_product_and_research/job-workflow-scope.md`](../yaml-workflow-engine-design-notes/job-workflow-scope.md) — the original worked-example design note this TRS implements. §2 (consumption modes), §3 (phased scope) are the core sections.
- [`dev/active/yaml-workflow-engine-phase-1/`](../yaml-workflow-engine-phase-1/) — Phase 1's TRS (plan + context + tasks). Phase 2 depends on every artifact it specifies. **Updated 2026-06-29 (commit `a70029b`):** Resolved Decision #5 pulled the `_DEFAULT_TIMEOUT_S` generalization into Phase 1 — `StageSpec` now has **10 fields** (added `timeout_s: int | None`), `_ALLOWED_STAGE_KEYS` now includes `gate_is_async` + `timeout_s`, and `SubprocessStageRunner` resolves timeouts via a 4-tier `resolve_timeout()` (plan §6.7). Phase 2 incorporated this — see Decision #7 below.
- [`docs/1_product_and_research/PRD.md`](../../../docs/1_product_and_research/PRD.md) — v1 product scope; still no v2 PRD exists.
- [`docs/1_product_and_research/cli-backend-dispatch.md`](../yaml-workflow-engine-design-notes/cli-backend-dispatch.md) — relevant only for Phase 3 (`tailor_materials`'s `backend: claude` field is parsed but not dispatched on in Phase 2).

### TRS itself (this directory)
- [`yaml-workflow-engine-phase-2-plan.md`](./yaml-workflow-engine-phase-2-plan.md) — design (sections 1–11) + flat task list (T2.0–T2.10).
- [`yaml-workflow-engine-phase-2-tasks.md`](./yaml-workflow-engine-phase-2-tasks.md) — checkbox progress tracking.

### Code targets

**New:**
- `src/atlas/workflows/job.yaml` — the worked-example workflow (4 stages, Mode A / `LIB:`).
- `src/atlas/workflows/job-cli.yaml` — the dependency-free Mode-B variant (4 stages, all `RAW:`); shipped so the `content_pipeline_not_installed` error can name it (Resolved Decision #2).
- `src/atlas/library_runner.py` — `LibraryStageRunner`, dispatches `LIB:`-prefixed tools to content-pipeline use-cases in-process.
- `src/atlas/library_adapters/score_jobs_adapter.py`, `capture_adapter.py` — thin per-use-case wiring functions.
- `tests/unit/test_library_runner.py`, `tests/unit/test_library_adapters.py`, `tests/integration/test_job_workflow_e2e.py` — new test files.

**Modified:**
- `src/atlas/orchestrator.py` — add `CompositeStageRunner` (dispatches `LIB:`/`RAW:`/plugin-command stages to the right runner). `Pipeline` itself is **unmodified** — this is the FR-7 regression-safety claim.
- `src/atlas/cli.py` — `_make_pipeline()` wires `CompositeStageRunner` when a resolved workflow has any `LIB:` stage; wires the `content_pipeline_not_installed` error to name `--workflow job-cli` (Resolved Decision #2); possibly extends the gate-prompt display path to surface `output_text` (T2.4).
- `pyproject.toml` — add `[project.optional-dependencies] job` pointing at `../content-pipeline`.

**Unchanged (verify, don't touch):**
- `workflow_loader.py`, `stages.py`, `state.py`, `plugin_resolver.py`, `plumb_adapter.py`, `post_commit_hook.py`, `worktree.py` — Phase 2 deliberately requires zero changes to any of these. If implementation discovers one of them *does* need a change, that's a signal the design has drifted from this TRS — stop and reconcile before proceeding.

### content-pipeline surface this TRS grounds against (sibling repo, read-only from atlas's perspective)
- `src/application/use_cases/score_jobs.py` — `ScoreJobsUseCase`, `ScoreJobsRunResult`. Constructor needs `llm_client`, `meta_store`, `archive_reader`, `profile_text`, `prompt_text`.
- `src/application/use_cases/capture.py` — `CaptureUseCase`, `CaptureRunResult`. Constructor needs `dispatcher`, `archive`, `meta_store`, `captures_writer`.
- `src/infrastructure/cli/cmd_score_jobs.py` — reference wiring pattern (`_load_prompt`, `_load_profile_text`, `_build_llm_client`) that `score_jobs_adapter.py` reuses.
- `src/infrastructure/cli/cmd_capture.py` — reference wiring pattern for dispatcher registration that `capture_adapter.py` partially reuses (job-board-relevant scrapers only: `rss`, `generic`, `ats_boards`).
- `src/infrastructure/cli/score_jobs_report.py` — `render_report()`, reused verbatim for the `gate_shortlist` content (§3.6 of the plan).
- `src/domain/entities/job_score.py` — `JobScore` (the scored-entry shape), not directly touched by atlas code but informs what `render_report()` shows.
- **No `tailor` or `emit_package` use-case exists in content-pipeline** — confirmed by reading `src/application/use_cases/`. This is why `tailor_materials`/`emit_package` stay `RAW:`/Mode C in `job.yaml`, not `LIB:`.

## Decisions made (during this TRS)

| # | Decision | Rationale |
| - | --- | --- |
| 1 | Phase 2 implements **Mode A (library import)** for `ingest_postings`/`score_fit`, not the literal `RAW:content-pipeline ...` Mode-B shell shown in TRD-v2 §3.8's example YAML. **Confirmed by the user, 2026-06-29.** | TRD-v2 §6's integration table and §14 Phase 2's scope bullet both call for content-pipeline as an in-process optional dependency; §3.8's YAML is illustrative shorthand (same pattern as Phase 1's `gate_is_async` resolution — example blocks aren't exhaustive). Mode A is also what `job-workflow-scope.md` §2 explicitly calls "Preferred." |
| 2 | New `LIB:<ref>` tool-string convention, sibling to `RAW:`. A small closed allow-list (`_REGISTRY` dict in `library_runner.py`) maps `<ref>` to adapter functions — **not** arbitrary dotted-path execution from YAML content. | Mirrors `RAW:`'s existing prefix-dispatch semantics (TRD-v2 §3.5) while keeping the trust boundary *tighter* than `RAW:` — a `job.yaml` can only ever invoke the two specific, code-reviewed adapters shipped with atlas, regardless of what string appears in the file. See plan §9 Security. |
| 3 | `gate_shortlist`'s prompt content is a rendered shortlist report (via content-pipeline's existing `score_jobs_report.render_report()`), not a bare pass/fail. **Confirmed by the user, 2026-06-29.** | Keeps the gate meaningful — the human approves *what they can see*, not a black box. Surfaced via `StageOutcome.output_text`, which is new load-bearing behavior for Mode A stages (Phase 1 dev stages relied on visible subprocess stdout; Mode A has none) — flagged as T2.4. |
| 4 | A missing content-pipeline install makes a `LIB:` stage **fail outright** (`content_pipeline_not_installed`) with an error **naming `job-cli` as the dependency-free alternative** — no silent runtime fallback. Mode B is a **shipped in-package workflow, `job-cli.yaml`** (`name: job-cli`, all `RAW:`), not a user-authored override. **Confirmed by the maintainer, 2026-06-30** — TRD-v2 intent was two YAML variants; "runtime import-check-and-silently-switch is more magic than this system should have. Explicit > implicit." | Automatic fallback would require `LibraryStageRunner` to know the CLI-equivalent of every registry entry *and* would hide a meaningful environment difference (library vs subprocess runs are namespaced separately in plumb) behind a silent switch. Shipping `job-cli.yaml` in-package (vs. the earlier "document a snippet" plan) lets the error point at a `--workflow job-cli` invocation that works immediately. See plan §3.7, §6.4, Resolved Decision #2. |
| 5 | `ingest_postings` fails the whole stage if *any* configured scrape source errors — stricter than content-pipeline's own CLI, which logs failures but exits 0 unless all sources fail. **Confirmed by the maintainer, 2026-06-30.** | `ingest_postings` has no gate (TRD-v2 §3.8). atlas is a *measured pipeline where every gate decision assumes complete upstream data* — a silent partial capture would flow into `score_fit` (scoring an incomplete set) and every downstream gate undetected. content-pipeline's looser CLI behavior is fine for interactive use; atlas fails the stage and the user re-runs. Binding, not a flagged option. |
| 6 | `CompositeStageRunner` is a new `StageRunner`-Protocol-satisfying wrapper; `Pipeline.__init__` and `Pipeline.step()` get **zero changes**. | TRD-v2 §6 already establishes the principle for `CliBackend ↔ Pipeline`: "Pipeline sees only the StageRunner Protocol... it does not know which CLI was used." Same boundary applies to "which runner handles a stage" — keeping this out of `Pipeline` is what makes FR-7 (zero dev-pipeline regression risk) true by construction, not just by testing. |
| 7 | **Incorporates Phase 1's commit `a70029b` (Resolved Decision #5, `timeout_s` generalization).** `job.yaml` sets `timeout_s: 1800` on `tailor_materials` (a `RAW:`/subprocess stage that runs long) and omits it on `ingest_postings`/`score_fit` (`LIB:`/in-process stages where `timeout_s` is inert). | The `timeout_s` field landed in Phase 1 *after* this TRS's first draft. Two consequences for Phase 2: (1) `tailor_materials`' stage name isn't in `_DEFAULT_TIMEOUT_S`, so without an explicit `timeout_s` it would inherit the 600s `_GLOBAL_FALLBACK_TIMEOUT_S` — too short for a multi-role agentic draft; (2) `LibraryStageRunner` makes in-process calls with no subprocess to time out, so `timeout_s` on a `LIB:` stage is silently inert — documented in the runner's docstring and flagged as Resolved Decision #5. |

None of these contradict TRD-v2; where its text was ambiguous (Mode A vs B, gate content, fallback behavior, partial-failure strictness, `timeout_s` semantics), the maintainer resolved them directly — decisions #1/#3 on 2026-06-29, decisions #4/#5/#7 and the five plan-level "Resolved Decisions" on 2026-06-30. **No decisions remain open** (see the plan's "Resolved Decisions & Clarifications" section — all five items there are settled and binding).

## Integration points

| Direction | Surface | Failure mode | Test coverage |
| --- | --- | --- | --- |
| `library_runner.py` → content-pipeline (lazy import) | `importlib.import_module()` inside `_import_adapter()`, never at module load time | `ImportError` → `StageOutcome(status="failure", error_type="content_pipeline_not_installed")` | Unit (`test_library_runner_content_pipeline_not_installed`). |
| `library_adapters/score_jobs_adapter.py` → `ScoreJobsUseCase` | Constructs use-case from content-pipeline `Settings()` + its own env vars | Missing env/profile files → `FileNotFoundError` propagates to `LibraryStageRunner`'s broad catch → `library_adapter_error` | Unit (mocked use-case). |
| `library_adapters/capture_adapter.py` → `CaptureUseCase` | Registers job-relevant scrapers only (`rss`, `generic`, `ats_boards`) | Any source failure → whole stage `failure` (deliberate strict behavior, Decision #5) | Unit (mocked use-case). |
| `orchestrator.py::CompositeStageRunner` → `LibraryStageRunner` / `SubprocessStageRunner` | `stage.tool` prefix dispatch (`LIB:` vs everything else) | `library=None` + `LIB:` stage → `library_runner_unavailable`, never a crash | Unit (`test_composite_runner_*`). |
| `cli.py::_make_pipeline()` → `CompositeStageRunner` | Constructs `library=LibraryStageRunner()` only when the resolved workflow has any `LIB:` stage | N/A — pure wiring | Integration (`test_job_workflow_e2e.py`). |
| plumb (gate scores) | Reuses Phase 1's `namespaced_metric()` unchanged — `job.gate_shortlist`, `job.gate_materials`, `job.gate_done` | N/A — no new failure mode introduced by Phase 2 | Integration (`test_job_workflow_gate_scores_namespaced`). |

## Where this TRS's task list maps to TRD-v2 §14 Phase 2 scope bullets

| TRD-v2 §14 Phase 2 bullet | This TRS's task |
| --- | --- |
| "Author `job.yaml` ... as a shipped workflow" | T2.1 |
| "Integrate content-pipeline as an optional editable dependency for Mode A stages ... If not installed, fall back to RAW: CLI dispatch (Mode B)" | T2.2 (library runner + adapters), T2.6 (optional dependency), T2.7 (Mode-B documented alternative — see Decision #4 for why this is documentation, not automatic runtime fallback) |
| "End-to-end test: ... 4 spans and 3 gate scores" | T2.8 |
| "Verify metric-name namespacing" | T2.8 (`test_job_workflow_gate_scores_namespaced`) |
| "Verify cross-workflow coexistence" | T2.8 (`test_job_and_dev_coexist_in_same_db`) |
| "Document the hub-and-spoke trigger model" | T2.10 |

No Appendix-A-style gap table is needed for Phase 2 — TRD-v2's Appendix A is scoped entirely to Phase 1 seams; Phase 2 introduces no comparable hardcoded-reference inventory of its own (it's additive new code, not a refactor of existing hardcoding).

## What this TRS deliberately does NOT cover

- The ai-workx second-brain trigger skill itself (Phase 4, explicitly out of TRD-v2 scope).
- `CliBackend`/`ClaudeCodeBackend`/`AntigravityBackend` (Phase 3) — `tailor_materials`'s `backend: claude` field is inert in Phase 2, same as Phase 1 left it.
- Real job-board scraper credential setup (Gmail/IMAP/LinkedIn auth) — a one-time user prerequisite, documented but not automated.
- Automatic Mode-A → Mode-B runtime fallback (Resolved Decision #2) — **explicitly rejected by the maintainer.** Mode B is the separate shipped `job-cli.yaml` the user selects by name; atlas never silently switches runners. ("Explicit > implicit.")
- Enforcing `timeout_s` on `LIB:`/in-process stages (Decision #7/Resolved Decision #5) — `LibraryStageRunner` honors content-pipeline's own internal client timeouts, not `StageSpec.timeout_s`; wrapping in-process LLM/scrape calls in a thread/signal-based timeout is deferred unless a real need surfaces.
