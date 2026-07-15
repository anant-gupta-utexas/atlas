# TRS — YAML Workflow Engine, Phase 2 (Job Workflow End-to-End)

**Project:** atlas — v2 YAML-driven gated-workflow engine
**Component:** `src/atlas/workflows/job.yaml` (new) + `library_runner.py` (new) + `plumb_adapter.py`, `cli.py` (extended)
**Status:** Draft, pre-implementation
**Last reviewed:** 2026-06-29
**Grounds on:** [TRD-v2](../../../docs/2_architecture/TRD-v2.md) §3.8, §6, §10, §14 (Phase 2); [`job-workflow-scope.md`](../yaml-workflow-engine-design-notes/job-workflow-scope.md) §2–3; [v1 TRD](../../../docs/2_architecture/TRD.md); [PRD](../../../docs/1_product_and_research/PRD.md); Phase 1 TRS ([plan](../yaml-workflow-engine-phase-1/yaml-workflow-engine-phase-1-plan.md), [context](../yaml-workflow-engine-phase-1/yaml-workflow-engine-phase-1-context.md))

> This TRS details exactly one TRD phase — Phase 2 — into a flat task list. It does not re-plan releases (PRD-owned) or re-sequence phases (TRD-owned). Phase 3 (CLI backend dispatch) and Phase 4 (second-brain trigger skill) are out of scope here and get their own TRS when picked up.

---

## ⚠️ Blocking dependency — read first

**Phase 1 has not been implemented yet.** As of this TRS's authoring date (2026-06-29), `src/atlas/stages.py` still defines `StageName`/`GateLabel` as `StrEnum`s with a hardcoded `STAGES` tuple; `src/atlas/workflow_loader.py` does not exist; `src/atlas/workflows/dev.yaml` does not exist; `Pipeline.__init__` does not accept a `stages` parameter; metric-name namespacing does not exist. Every task in this TRS assumes those Phase 1 artifacts exist exactly as specified in the [Phase 1 TRS](../yaml-workflow-engine-phase-1/yaml-workflow-engine-phase-1-plan.md).

**This TRS is written against the Phase-1-complete codebase, per TRD-v2's own phase sequencing (Phase 2 "Dependencies: Phase 1").** Per the user's explicit instruction (2026-06-29), this is a deliberate choice — write Phase 2 as the contract specifies, flag the gap once, here, rather than redesigning Phase 2 around today's pre-Phase-1 code shape. **T2.0 below is a hard gate: no other Phase 2 task may start until Phase 1 has merged and its exit criteria (TRD-v2 §13 #1–4) are independently verified.**

---

## Phase Summary

**TRD phase:** Phase 2 — Job workflow end-to-end (TRD-v2 §14).
**PRD release(s) delivered:** None directly — same situation as Phase 1. TRD-v2's preamble notes the PRD's future-releases table predates the YAML-workflow analysis. TRD-v2 §11 tags this phase **v2.1 — job workflow end-to-end**.
**Goal (verbatim from TRD-v2 §14):** "Author `job.yaml`, run it end-to-end, and validate that the multi-workflow engine produces correct span trees and gate scores for a non-dev domain."

---

## 1. Overview & Scope

### In scope

Everything TRD-v2 §14 Phase 2's engineering-scope bullets and §3.8 specify:

- Author `src/atlas/workflows/job.yaml` (in-package, shipped workflow) per TRD-v2 §3.8's four stages: `ingest_postings`, `score_fit`, `tailor_materials`, `emit_package`.
- Integrate `content-pipeline` as an optional editable dependency (`pip install -e ../content-pipeline`) for `ingest_postings` and `score_fit` — **Mode A (library import)**, per the resolved decision below.
- New `StageRunner` implementation, `LibraryStageRunner`, that dispatches Mode-A stages to content-pipeline use-cases in-process; when content-pipeline isn't installed, a `LIB:` stage fails cleanly with a clear error naming the `job-cli` dependency-free alternative (Resolved Decision #2 — **no automatic runtime fallback**).
- A second shipped workflow, `job-cli.yaml` (`name: job-cli`) — the Mode-B subprocess variant that runs without content-pipeline as a Python dependency (Resolved Decision #2, §3.7).
- End-to-end test: `atlas run --workflow job` produces a complete span tree (4 spans, 3 gate scores).
- Verify metric-name namespacing (`job.gate_shortlist`, `job.gate_materials`, `job.gate_done`) lands correctly in plumb.
- Verify cross-workflow coexistence: dev + job runs in the same plumb DB, queryable by `task_id` prefix.
- Document the hub-and-spoke trigger model (second-brain → ai-workx skill → atlas → content-pipeline → plumb) — a short addition to the README or a new guide doc.

### Resolved decision: Mode A is the default (`job.yaml`); Mode B is a separate explicit variant (`job-cli.yaml`)

TRD-v2 §3.8's example `job.yaml` shows `ingest_postings`/`score_fit` as `RAW:content-pipeline ...` shell stages (Mode B), but TRD-v2 §6's integration-requirements table and §14 Phase 2's own scope bullet both call for content-pipeline as an **optional editable dependency** consumed **in-process**. Confirmed with the user (2026-06-29): **the default `job.yaml` implements Mode A** (in-process, `LIB:`), not the literal Mode-B YAML shown in §3.8's illustrative example — §3.8's block is illustrative shorthand (the same pattern Phase 1's TRS found for `gate_is_async`/`default_backend` being absent from §3.1's example), and Mode A is what produces the "structured results" §6 and `job-workflow-scope.md` §2 both call the **preferred** mode. So `job.yaml`'s `tool` field uses the new `LIB:<use_case_ref>` convention (§3.2) for `ingest_postings`/`score_fit`.

**Mode B is still provided — as a distinct shipped workflow, not as the default and not as automatic fallback.** Resolved Decision #2 (2026-06-30): `job-cli.yaml` (`name: job-cli`, §3.7) is the literal Mode-B variant from §3.8, shipped alongside `job.yaml` for environments without content-pipeline installed. The user picks between them explicitly (`--workflow job` vs `--workflow job-cli`); there is no runtime import-check-and-switch. "Explicit > implicit" — see §6.4.

### Out of scope (deferred to later phases / later TRS)

- `CliBackend` Protocol, `ClaudeCodeBackend`, `AntigravityBackend` (Phase 3). `tailor_materials` continues to dispatch via the existing `SubprocessStageRunner` → `claude -p` path (Mode C), unchanged from Phase 1's behavior — `job.yaml`'s `backend: claude` field is parsed (Phase 1) but not yet dispatched on a per-stage basis (still Phase 3).
- The second-brain ai-workx trigger skill itself (Phase 4 — out of TRD-v2 scope entirely; this TRS only documents the model, doesn't build the skill).
- Any plumb schema change (TRD-v2 §7, §12 #3 — unchanged in Phase 2).
- The Mode-B path when content-pipeline is *not* installed is **in scope** (TRD-v2 §14 exit criteria: "atlas runs without it installed, falling back to CLI dispatch") — delivered as the shipped `job-cli.yaml` variant (§3.7), **not** as automatic runtime fallback inside `LibraryStageRunner` (Resolved Decision #2; see §6.4).
- Replacing `tailor_materials`/`emit_package` with anything other than today's Mode C / `RAW:` mechanism — both stay exactly as Phase 1 already supports them.
- Real scraper/source configuration for `ingest_postings` (job-board credentials, ATS configs, IMAP/Gmail wiring). This TRS wires `CaptureUseCase` generically; populating real `SourceConfig` entries in the user's content-pipeline install is a one-time user setup step, documented but not automated here.

### Why this scope

Phase 1 makes "a new workflow is a YAML file, not a code change" *structurally* true. Phase 2 is the only phase that tests whether that claim survives contact with a real non-dev domain. The TRD-v2 author's own framing (§2 Objective 1) is explicit: "If gates + measurement feel natural on a non-dev workflow, the generalization holds." Everything in this TRS exists to produce that one falsifiable artifact — a `job.yaml` run with a correct span tree — plus the one genuinely new runner (`LibraryStageRunner`) needed to do it the *preferred* way (Mode A) rather than the cheaper-but-lossy way (Mode B).

---

## 2. Requirements Summary

### Functional (from TRD-v2 §3.8, §6, §14, mapped to FR IDs for traceability)

- **FR-1** (§3.8) — `job.yaml` defines 4 stages: `ingest_postings` (tool, no gate), `score_fit` (verify, `gate_shortlist`), `tailor_materials` (subagent, `gate_materials`), `emit_package` (tool, `gate_done`). Loads via Phase 1's `workflow_loader.py` with zero loader changes required.
- **FR-2** (§6) — content-pipeline integrates as `pip install -e ../content-pipeline`, optional. atlas's `pyproject.toml` declares it as an optional dependency group (e.g. `[project.optional-dependencies] job = [...]`), not a hard dependency — installing atlas's core package must not require content-pipeline.
- **FR-3** (§3.8, Resolved Decision #1 & #2) — In `job.yaml`, `ingest_postings` and `score_fit` dispatch via `LibraryStageRunner` (Mode A). The Mode-B path is the *separate* shipped `job-cli.yaml` workflow (all `RAW:`, `SubprocessStageRunner`), which the user selects explicitly with `--workflow job-cli`. There is **no** automatic runtime switch between the two — if a `LIB:` stage's content-pipeline import fails, the stage fails with an error naming `job-cli`.
- **FR-4** (§14) — End-to-end: `atlas run "<task>" --workflow job` produces a span tree with exactly 4 spans (`tool:ingest_postings`, `verify:score_fit`, `subagent:tailor_materials`, `tool:emit_package`) and 3 `scorer='user_signal'` gate scores.
- **FR-5** (§14, §3.7) — Gate scores are namespaced: `job.gate_shortlist`, `job.gate_materials`, `job.gate_done` (via Phase 1's `namespaced_metric()`, zero changes needed — this is the proof-of-life for that function).
- **FR-6** (§14) — Dev and job runs coexist in the same plumb DB (`~/.plumb/plumb.db`), queryable by `task_id` prefix (`dev.<slug>` vs `job.<slug>`).
- **FR-7** (§14) — Dev pipeline regression suite remains green; Phase 2 adds zero changes to `dev.yaml`, `orchestrator.py`'s core `step()` logic, or any Phase-1-delivered file beyond what's strictly needed to wire in `LibraryStageRunner` as an additional `StageRunner` implementation.
- **FR-8** (§14, Resolved Decision #2) — content-pipeline integration is optional at the *atlas-as-a-whole* level: with content-pipeline not installed, `--workflow job` fails cleanly (clear error → `job-cli`), and `--workflow job-cli` runs the full 4-stage workflow successfully via subprocess dispatch. "atlas runs without content-pipeline installed" is satisfied by the `job-cli` variant existing and working, not by `--workflow job` degrading.
- **FR-9** (resolved decision, gate content) — `score_fit`'s `StageOutcome.output_text` includes a rendered report (via content-pipeline's `score_jobs_report.render_report()`) so the `gate_shortlist` prompt shows the human a real shortlist (scored entries grouped GREEN/YELLOW/RED, top picks) rather than a bare pass/fail.

### Non-functional (from TRD-v2 §4, §5, applied to Phase 2's surface)

- **NFR-1** — No regression on Phase 1 NFRs (YAML load < 50ms, workflow resolution < 100ms, `atlas status` < 500ms, hook < 1s).
- **NFR-2** — `LibraryStageRunner` failures (content-pipeline import error, use-case exception, missing settings/profile files) surface as `StageOutcome(status="failure", error_type=...)`, never an unhandled exception that crashes the orchestrator loop.
- **NFR-3** — content-pipeline as optional dependency: `import` of content-pipeline modules happens lazily, inside `LibraryStageRunner`, not at `atlas` package import time — mirrors the existing `_build_llm_client`-style lazy-import pattern already used in content-pipeline's own CLI.
- **NFR-4** — `mypy src` and `ruff check`/`ruff format` pass. `LibraryStageRunner` is typed against content-pipeline's use-case classes only inside `TYPE_CHECKING` blocks or via a runtime-optional import pattern, so `mypy src` does not hard-fail when content-pipeline isn't installed in the dev environment running the check (use `if TYPE_CHECKING: from src.application.use_cases... import ScoreJobsUseCase` plus a try/except at runtime — see §3.4).
- **NFR-5** — No plumb schema change (TRD-v2 §7, §12 #3 — carried unchanged).
- **NFR-6** — LoC budget: TRD-v2 §5 caps total engine code (orchestrator + loader + backends + state) at ≤ ~600 lines, set in Phase 1. `LibraryStageRunner` is a new file outside that budget's named components (it's a runner, like `SubprocessStageRunner`, not "engine" in the loader/orchestrator/state sense) — but should stay lean: target ≤ 150 lines, consistent with `SubprocessStageRunner`'s current footprint (~90 lines).

---

## 3. Detailed Component Design

### 3.1 Module structure (post–Phase 2)

```
src/atlas/
├── __init__.py
├── cli.py                  # unchanged signature; --workflow job now resolves to a real workflow
├── orchestrator.py         # unchanged from Phase 1 — Pipeline/step() logic untouched
├── workflow_loader.py       # unchanged from Phase 1 — loads job.yaml with zero loader code changes
├── stages.py                 # unchanged from Phase 1
├── state.py                   # unchanged from Phase 1
├── plugin_resolver.py         # unchanged — job.yaml's tool fields are LIB:/RAW: prefixed, bypass PLUGIN_COMMANDS entirely
├── plumb_adapter.py            # unchanged from Phase 1 — namespaced_metric() consumed as-is
├── post_commit_hook.py          # unchanged — job.yaml has no gate_is_async stage (no worktree, no async hook gate)
├── library_runner.py             # NEW — LibraryStageRunner (Mode A dispatch to content-pipeline)
├── worktree.py                    # unchanged — job.yaml sets isolate: false on every stage
├── config.py                       # unchanged
└── workflows/
    ├── dev.yaml                    # unchanged (Phase 1)
    ├── job.yaml                     # NEW — the worked example (Mode A, library; requires content-pipeline)
    └── job-cli.yaml                  # NEW — Mode B variant (subprocess; no content-pipeline dependency)
```

`library_runner.py` is the only new code module. `workflows/job.yaml` and `workflows/job-cli.yaml` are new data files, packaged the same way `dev.yaml` is (Phase 1's T1.1 already proved the wheel-packaging path for `workflows/*.yaml`, so no new packaging task is needed here — see Dependencies). The two YAML variants are the maintainer-resolved answer to "what happens without content-pipeline installed" (Resolved Decision #2): `job.yaml` is the library-backed default; `job-cli.yaml` is the dependency-free subprocess alternative, shipped in-package so the error message can name it as a real, resolvable file (§3.7, §6.4).

### 3.2 `job.yaml` (the worked example, finalized)

```yaml
# src/atlas/workflows/job.yaml
name: job
default_backend: claude
stages:
  - name: ingest_postings
    span_kind: tool
    tool: "LIB:content_pipeline.capture"
    gate: null
    isolate: false
  - name: score_fit
    span_kind: verify
    tool: "LIB:content_pipeline.score_jobs"
    gate: gate_shortlist
    isolate: false
  - name: tailor_materials
    span_kind: subagent
    tool: "RAW:Tailor application materials for each shortlisted role. Read the shortlist report from the previous stage's output and draft a tailored CV + cover letter per role marked APPLY-STRONG or MONITOR."
    gate: gate_materials
    isolate: false
    backend: claude
    timeout_s: 1800
  - name: emit_package
    span_kind: tool
    tool: "RAW:Assemble the application package from the tailored materials and write it to docs/01_professional/job_applications/<role-slug>/."
    gate: gate_done
    isolate: false
```

**`timeout_s` on the `RAW:` stages (and deliberately not on the `LIB:` stages).** Phase 1's Resolved Decision #5 (committed 2026-06-29, plan §6.7) pulled the `_DEFAULT_TIMEOUT_S` generalization forward: `StageSpec` now carries a per-stage `timeout_s: int | None` field, and `SubprocessStageRunner.run()` resolves a stage's timeout via `resolve_timeout()` (`.atlas.toml` override → `stage.timeout_s` → `_DEFAULT_TIMEOUT_S` by stage name → `_GLOBAL_FALLBACK_TIMEOUT_S`). This matters for `job.yaml`:

- **`tailor_materials` sets `timeout_s: 1800`** — it's a `RAW:` → `SubprocessStageRunner` → `claude -p` stage drafting CV+cover-letter pairs across multiple roles; the agentic call can run long. Its stage name (`tailor_materials`) is *not* in `_DEFAULT_TIMEOUT_S` (that dict is keyed by dev-pipeline stage names only), so without an explicit `timeout_s` it would inherit the 600s `_GLOBAL_FALLBACK_TIMEOUT_S` — plausibly too short for a multi-role agentic draft. Setting `1800` (matching dev's `code_gen` headroom) is the deliberate choice. `emit_package` omits `timeout_s` and accepts the 600s fallback (file assembly is fast).
- **`ingest_postings` and `score_fit` deliberately omit `timeout_s`** — they are `LIB:` stages dispatched by `LibraryStageRunner` *in-process*, not via subprocess. `resolve_timeout()` / the `subprocess.run(timeout=...)` mechanism does **not** apply to in-process calls — there is no subprocess to time out. Their real-world latency (live scraping, LLM batch scoring) is bounded by content-pipeline's own internal timeouts (HTTP client timeouts, the LLM client's `max_tokens`/request timeout), not by atlas's stage timeout. Setting `timeout_s` on a `LIB:` stage would be inert and misleading, so `job.yaml` omits it there. This asymmetry — `timeout_s` is honored for `RAW:`/subprocess stages, silently inert for `LIB:`/in-process stages — is documented in §3.4's `LibraryStageRunner` docstring and called out in Resolved Decision #5.

**`LIB:<ref>` tool-string convention.** Mirrors `RAW:` (TRD-v2 §3.5: "Tools prefixed with `RAW:` bypass plugin resolution and are passed directly as the prompt text"). `LIB:<ref>` is a sibling convention this TRS introduces: it bypasses `plugin_resolver.resolve()` entirely (same as `RAW:`) and instead is dispatched by `LibraryStageRunner`, which maps `<ref>` (`content_pipeline.capture`, `content_pipeline.score_jobs`) to a small internal registry of use-case adapters (§3.4). This keeps the `tool` field's resolution semantics consistent: a prefix decides *which runner subsystem* interprets the rest of the string, exactly as `RAW:` already does for `SubprocessStageRunner`.

**Why `tailor_materials` and `emit_package` stay `RAW:`.** Both are genuine judgment/assembly steps with no content-pipeline use-case backing them (content-pipeline owns *scoring* and *capture*, not *drafting* or *file assembly* — confirmed by reading `src/application/use_cases/` in content-pipeline: no `tailor` or `emit_package` use case exists, nor should one — that's atlas/agent judgment territory, not a deterministic content-pipeline op). This matches `job-workflow-scope.md` §2's own mode table: Mode C for judgment, Mode A only for "deterministic content-pipeline op."

### 3.3 `StageRunner` dispatch — how `Pipeline` picks the right runner per stage

**Problem:** Phase 1's `Pipeline.__init__` takes a single `runner: StageRunner`. `job.yaml` needs two runners — `LibraryStageRunner` for `LIB:`-prefixed stages, `SubprocessStageRunner` for `RAW:`-prefixed stages. `dev.yaml`'s stages are all plugin-slash-command tools, handled entirely by `SubprocessStageRunner` (unchanged).

**Resolution:** introduce a small dispatching wrapper, `CompositeStageRunner`, satisfying the same `StageRunner` Protocol (`run(*, ctx: RunContext, stage: StageSpec) -> StageOutcome`) so `Pipeline` itself needs **zero changes** — it still calls `self._runner.run(ctx=ctx, stage=stage)` exactly as today.

```python
# src/atlas/orchestrator.py — addition, not a Pipeline change
class CompositeStageRunner:
    """Dispatches each stage to the runner matching its tool-string prefix.

    Satisfies the StageRunner Protocol; Pipeline is unaware this wrapping
    exists. Falls through to `default` (SubprocessStageRunner) for any
    tool string without a recognized prefix — preserves dev.yaml's
    plugin-slash-command behavior unchanged (those strings have neither
    a RAW: nor a LIB: prefix; plugin_resolver.resolve() still owns them).
    """

    def __init__(self, *, default: StageRunner, library: StageRunner | None = None) -> None:
        self._default = default
        self._library = library

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        if stage.tool.startswith("LIB:"):
            if self._library is None:
                return StageOutcome(
                    stage=stage, span_id="", status="failure", output_text="",
                    error_type="library_runner_unavailable",
                )
            return self._library.run(ctx=ctx, stage=stage)
        return self._default.run(ctx=ctx, stage=stage)  # RAW: and plugin-command stages
```

**Why a wrapper, not a `Pipeline` constructor change.** TRD-v2 §6 states the `CliBackend ↔ Pipeline` boundary explicitly: "`Pipeline` sees only the `StageRunner` Protocol and `StageOutcome` — it does not know which CLI was used." The same boundary principle applies here: *which runner* handles a stage is a `StageRunner`-internal concern, not a `Pipeline` concern. `CompositeStageRunner` is the seam; `Pipeline`'s contract is untouched, so Phase 2 introduces zero risk of a dev-pipeline regression in `orchestrator.py`'s core logic (FR-7).

### 3.4 `library_runner.py` — `LibraryStageRunner`

```python
# src/atlas/library_runner.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from atlas.orchestrator import RunContext, StageOutcome
from atlas.stages import StageSpec

if TYPE_CHECKING:
    from src.application.use_cases.capture import CaptureRunResult
    from src.application.use_cases.score_jobs import ScoreJobsRunResult

logger = logging.getLogger(__name__)


class ContentPipelineUnavailableError(Exception):
    """Raised when a LIB: stage is dispatched but content-pipeline is not importable."""


class _UseCaseAdapter(Protocol):
    def invoke(self, *, ctx: RunContext) -> StageOutcome: ...


_REGISTRY: dict[str, str] = {
    "content_pipeline.capture": "atlas.library_adapters.capture_adapter.invoke",
    "content_pipeline.score_jobs": "atlas.library_adapters.score_jobs_adapter.invoke",
}


class LibraryStageRunner:
    """Dispatches LIB:-prefixed stage tools to content-pipeline use-cases in-process.

    Lazily imports content-pipeline modules per-call (NFR-3) so atlas's core
    package import never requires content-pipeline to be installed. Each
    registry entry is a thin adapter function (atlas.library_adapters.*) that
    owns the use-case construction (settings, ports) — kept out of this class
    to bound its size (NFR-6) and isolate per-use-case wiring churn.

    NOTE on StageSpec.timeout_s: this runner makes IN-PROCESS calls, not
    subprocesses, so it does NOT enforce stage.timeout_s — there is no
    subprocess.run(timeout=...) to apply it to. A LIB: stage's effective
    latency bound comes from content-pipeline's own internal client timeouts.
    timeout_s is honored only by SubprocessStageRunner (RAW:/plugin stages).
    Setting timeout_s on a LIB: stage is therefore inert (Resolved Decision #5).
    """

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        ref = stage.tool.removeprefix("LIB:").strip()
        adapter_path = _REGISTRY.get(ref)
        if adapter_path is None:
            return StageOutcome(
                stage=stage, span_id="", status="failure", output_text="",
                error_type="library_ref_unknown",
            )
        try:
            adapter = _import_adapter(adapter_path)
        except ImportError as exc:
            logger.warning("content-pipeline not importable for %s: %s", ref, exc)
            return StageOutcome(
                stage=stage, span_id="", status="failure", output_text="",
                error_type="content_pipeline_not_installed",
            )
        try:
            return adapter(ctx=ctx)
        except Exception as exc:  # noqa: BLE001 — use-case errors must not crash the orchestrator
            logger.error("LibraryStageRunner adapter %s failed: %s", ref, exc)
            return StageOutcome(
                stage=stage, span_id="", status="failure", output_text=str(exc),
                error_type="library_adapter_error",
            )


def _import_adapter(dotted_path: str):
    """importlib-based dynamic import; raises ImportError if content-pipeline (or
    the adapter module itself) is not on the path."""
    import importlib

    module_path, func_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, func_name)
```

### 3.5 Adapter functions — `atlas/library_adapters/`

Two thin functions, one per content-pipeline use-case, each owning its own settings/port wiring so `LibraryStageRunner` stays generic:

```python
# src/atlas/library_adapters/score_jobs_adapter.py
from __future__ import annotations

from atlas.orchestrator import RunContext, StageOutcome


def invoke(*, ctx: RunContext) -> StageOutcome:
    """Construct ScoreJobsUseCase from content-pipeline Settings and run_pending()."""
    from src.application.use_cases.score_jobs import ScoreJobsUseCase
    from src.infrastructure.cli.cmd_score_jobs import _build_llm_client, _load_profile_text, _load_prompt
    from src.infrastructure.cli.score_jobs_report import render_report
    from src.infrastructure.config.settings import Settings
    from src.infrastructure.storage.access_failures_log import AccessFailuresLog
    from src.infrastructure.storage.archive import FilesystemArchive
    from src.infrastructure.storage.meta_store import CapturesMetaStore

    settings = Settings()  # reads content-pipeline's own env/config, unrelated to .atlas.toml
    prompt_text = _load_prompt(settings.score_jobs_prompt_path)
    profile_text = _load_profile_text(settings.job_profile_path)
    llm_client = _build_llm_client(settings)
    meta_store = CapturesMetaStore(meta_path=settings.captures_meta_path)
    archive_reader = FilesystemArchive(settings.archive_root)

    use_case = ScoreJobsUseCase(
        llm_client=llm_client, meta_store=meta_store, archive_reader=archive_reader,
        profile_text=profile_text, prompt_text=prompt_text,
    )
    result = use_case.run_pending()

    ats_failures = AccessFailuresLog(settings.access_failures_log_path).read()
    report = render_report(meta_store.read_all(), ats_failures=ats_failures)

    status = "failure" if result.failed else "success"
    return StageOutcome(
        stage=None,  # filled in by caller — see Note below
        span_id="", status=status, output_text=report,
        error_type="score_jobs_failed" if result.failed else None,
    )
```

> **Note on `stage=None`:** `StageOutcome` is `frozen` and requires a `stage` field. The adapter doesn't have direct access to the `StageSpec` in this sketch — **fix applied in implementation (T2.2):** adapters take `stage: StageSpec` as an explicit parameter (`invoke(*, ctx: RunContext, stage: StageSpec) -> StageOutcome`), matching `LibraryStageRunner.run()`'s own signature, and `LibraryStageRunner` passes `stage=stage` through. This sketch's omission is corrected before implementation; flagging here so the task's acceptance criteria can check for it directly.

```python
# src/atlas/library_adapters/capture_adapter.py
from __future__ import annotations

from atlas.orchestrator import RunContext, StageOutcome


def invoke(*, ctx: RunContext, stage) -> StageOutcome:
    """Construct CaptureUseCase with the dispatcher registered for job-board
    sources only (ats_boards, rss, generic, web_search if configured) and
    call run_all() across the user's configured SourceConfig list."""
    from src.application.dispatcher import CrawlerDispatcher
    from src.application.use_cases.capture import CaptureUseCase
    from src.infrastructure.config.loader import ConfigLoader
    from src.infrastructure.config.settings import Settings
    from src.infrastructure.scrapers.ats_boards import AtsBoardScraper
    from src.infrastructure.scrapers.generic import GenericScraper
    from src.infrastructure.scrapers.rss import RssScraper
    from src.infrastructure.storage.archive import FilesystemArchive
    from src.infrastructure.storage.captures_md import CapturesMdAppender
    from src.infrastructure.storage.meta_store import CapturesMetaStore

    settings = Settings()
    dispatcher = CrawlerDispatcher()
    dispatcher.register("rss", RssScraper)
    dispatcher.register("generic", GenericScraper)
    dispatcher.register("ats_boards", AtsBoardScraper)
    # Gmail/IMAP/web_search/LinkedIn registration intentionally omitted —
    # those require credential wiring (settings.gmail_*, settings.imap_*)
    # that is a one-time user setup concern, not this adapter's job (§ scope
    # note: "real scraper/source configuration ... documented but not
    # automated here").

    archive = FilesystemArchive(archive_root=settings.archive_root)
    meta_store = CapturesMetaStore(meta_path=settings.captures_meta_path)
    captures_writer = CapturesMdAppender(captures_path=settings.captures_path)

    use_case = CaptureUseCase(
        dispatcher=dispatcher, archive=archive, meta_store=meta_store,
        captures_writer=captures_writer, captures_meta_path=settings.captures_meta_path,
    )

    loader = ConfigLoader()
    configs = [c for c in loader.load() if c.type in ("rss", "generic", "ats_boards")]
    results = use_case.run_all(configs)

    any_failed = any(r.failed for r in results)
    summary = "\n".join(
        f"{r.source_id}: fetched={r.items_fetched} new={r.items_new} dupe={r.items_dupe} errors={r.errors}"
        for r in results
    )
    return StageOutcome(
        stage=stage, span_id="", status="failure" if any_failed else "success",
        output_text=summary, error_type="capture_source_failed" if any_failed else None,
    )
```

**Why adapters live in `atlas/library_adapters/`, not inline in `library_runner.py`.** Keeps `library_runner.py` itself small and use-case-agnostic (NFR-6); each adapter's settings/port wiring is content-pipeline-version-sensitive and isolated so a content-pipeline API change touches one adapter file, not the dispatch core. This mirrors content-pipeline's *own* internal pattern (`cmd_capture.py` / `cmd_score_jobs.py` each own their wiring; the use-case classes themselves stay pure).

### 3.6 `gate_shortlist` content (resolved decision)

`score_jobs_adapter.invoke()` returns `output_text=report` where `report` is `render_report()`'s Markdown output (GREEN/YELLOW/RED sections, Top Picks, stats). `Pipeline.step()` already passes `outcome.output_text` through unchanged to the `StageOutcome` it returns; the existing `ClickPrompter`/CLI display layer is responsible for surfacing `output_text` at the gate prompt — **confirm in T2.2 that the CLI's gate-prompt rendering path actually prints `output_text`** (Phase 1 dev-pipeline stages relied on subprocess stdout being visible to the user directly in the terminal during the `claude -p` call itself, not via `output_text` redisplay — this is a **new requirement Phase 2 introduces**: Mode A stages have no subprocess stdout for the user to have already seen, so `output_text` becoming gate-visible is now load-bearing, not cosmetic).

### 3.7 `job-cli.yaml` — the dependency-free Mode-B variant (Resolved Decision #2)

Shipped alongside `job.yaml` as a second in-package workflow. Identical stages, gates, and `span_kind`s — the only difference is `ingest_postings`/`score_fit` use `RAW:` subprocess dispatch to content-pipeline's CLI instead of `LIB:` in-process calls, so the workflow runs with **zero content-pipeline Python import** (the `content-pipeline` console script must be on `PATH`, but the package need not be `pip install -e`'d into atlas's environment).

```yaml
# src/atlas/workflows/job-cli.yaml
name: job-cli
default_backend: claude
stages:
  - name: ingest_postings
    span_kind: tool
    tool: "RAW:content-pipeline capture --source job-boards"
    gate: null
    isolate: false
  - name: score_fit
    span_kind: verify
    tool: "RAW:content-pipeline score-jobs --pending"
    gate: gate_shortlist
    isolate: false
    timeout_s: 1800        # NOW honored — this is a RAW:/SubprocessStageRunner stage (cf. job.yaml's LIB: variant where timeout_s is inert)
  - name: tailor_materials
    span_kind: subagent
    tool: "RAW:Tailor application materials for each shortlisted role. Read the shortlist report from the previous stage's output and draft a tailored CV + cover letter per role marked APPLY-STRONG or MONITOR."
    gate: gate_materials
    isolate: false
    backend: claude
    timeout_s: 1800
  - name: emit_package
    span_kind: tool
    tool: "RAW:Assemble the application package from the tailored materials and write it to docs/01_professional/job_applications/<role-slug>/."
    gate: gate_done
    isolate: false
```

**Differences from `job.yaml`, and their consequences:**

- **`name: job-cli`** — distinct workflow name → distinct `task_id` prefix (`job-cli.<slug>`) and distinct namespaced gate metrics (`job-cli.gate_shortlist`, …). This means `job` and `job-cli` runs are *separately* queryable in plumb, not merged — a deliberate, minor consequence the user should know (a run done via the CLI variant won't aggregate with library-variant runs under the same metric name). If unified metrics across both variants are ever wanted, that's a future naming-convention decision, not Phase 2 work.
- **`score_fit`'s gate content is thinner.** The `LIB:` variant gets a rendered `render_report()` shortlist in `output_text` (§3.6) because `score_jobs_adapter` calls `render_report()` directly. The `RAW:content-pipeline score-jobs --pending` subprocess instead emits content-pipeline's *own* stdout (which `cmd_score_jobs.run_score_jobs()` already prints a report to — see content-pipeline `cmd_score_jobs.py:106-112`). So the human still sees a shortlist at `gate_shortlist`, but it's content-pipeline's CLI-formatted report surfaced as subprocess stdout, not atlas re-rendering it via `output_text`. Functionally equivalent gate experience; mechanically different path. Acceptable — both variants give the human a real shortlist to approve.
- **`timeout_s: 1800` on `score_fit` IS honored here** (unlike `job.yaml`'s `LIB:` `score_fit`, where it's inert per Resolved Decision #5) — because `job-cli.yaml`'s `score_fit` is a `RAW:`/`SubprocessStageRunner` stage with a real subprocess to time out. This is the one place the Mode A/B distinction changes `timeout_s` semantics, and it's correct: subprocess scoring *should* have a stage timeout; in-process scoring is bounded by the client's own timeout instead.

`job-cli.yaml` needs no `LibraryStageRunner`, no `CompositeStageRunner` library wiring, no content-pipeline optional-dependency install — it runs entirely through Phase 1's existing `SubprocessStageRunner` via the `RAW:` path. It is, in effect, the workflow `job-workflow-scope.md` §3 and TRD-v2 §3.8 literally sketched; `job.yaml` (the `LIB:` variant) is the Mode-A *optimization* of it.

---

## 4. API Specifications

No network API (unchanged from v1/Phase 1). Surface is the `job.yaml` workflow file + the new `LIB:` tool-string convention + the `library_runner.py`/`library_adapters/` module API (internal only, no CLI flag changes from Phase 1).

### 4.1 CLI surface

No new flags. Phase 1's `--workflow`/`--workflow-file` already cover workflow selection:

```
atlas run "<task>" --workflow job
```

resolves `job.yaml` from the built-in `src/atlas/workflows/` search-path tier (Phase 1's `resolve_workflow()`, unchanged).

### 4.2 Error surface (extends Phase 1's table, TRD-v2 §4 Usability)

| Condition | Behavior |
|---|---|
| `LIB:` stage dispatched, content-pipeline not installed | `StageOutcome(status="failure", error_type="content_pipeline_not_installed")`. CLI surfaces this as a stage failure with a clear message that (a) names the missing dependency + the `uv sync --extra job` / `pip install -e ../content-pipeline` remedy, **and (b) names `job-cli.yaml` as the dependency-free alternative** (`atlas run "<task>" --workflow job-cli`). **Not** a silent fallback to Mode B — explicit error pointing at the explicit alternative (Resolved Decision #2; see §3.7, §6.4). |
| `LIB:<unknown-ref>` (typo in `job.yaml` or a future workflow) | `StageOutcome(status="failure", error_type="library_ref_unknown")`. |
| content-pipeline use-case raises (e.g. missing `JOB_PROFILE_PATH`, malformed `ScoreJobsRunResult`) | Caught by `LibraryStageRunner.run()`'s broad `except Exception`, surfaced as `error_type="library_adapter_error"` with `output_text=str(exc)` — never an unhandled traceback reaching the orchestrator loop (NFR-2). |
| `CompositeStageRunner` sees a `LIB:` stage but was constructed with `library=None` | `StageOutcome(status="failure", error_type="library_runner_unavailable")` — this is the `_make_pipeline()`-time wiring check; see T2.5. |

---

## 5. Database Design

Unchanged from Phase 1: atlas owns no schema; all writes go through plumb's Python API. Phase 2's only DB-relevant behavior is **what `task_id` prefix and `metric_name` strings** get written — both already handled by Phase 1's `namespaced_metric()` and the existing `task_id` convention (`<workflow>.<slug>`, per TRD-v2 §7's `runs.task_id` row). No new plumb-adapter code in Phase 2.

---

## 6. Algorithm & Logic Design

### 6.1 `CompositeStageRunner.run()` dispatch (pseudocode — see §3.3 for the real implementation)

```
function run(ctx, stage) -> StageOutcome:
    if stage.tool starts with "LIB:":
        if library_runner is None: return failure("library_runner_unavailable")
        return library_runner.run(ctx=ctx, stage=stage)
    return default_runner.run(ctx=ctx, stage=stage)   # handles RAW: and plugin-command tools, unchanged
```

### 6.2 `LibraryStageRunner.run()` dispatch (pseudocode — see §3.4)

```
function run(ctx, stage) -> StageOutcome:
    ref = strip "LIB:" prefix from stage.tool
    adapter_path = REGISTRY.get(ref)
    if adapter_path is None: return failure("library_ref_unknown")
    try:
        adapter = dynamic_import(adapter_path)
    except ImportError:
        return failure("content_pipeline_not_installed")
    try:
        return adapter(ctx=ctx, stage=stage)
    except Exception as exc:
        return failure("library_adapter_error", output_text=str(exc))
```

### 6.3 `_make_pipeline()` wiring (cli.py, extends Phase 1's resolution)

```
function _make_pipeline(workflow, ...):
    loaded = resolve_workflow(...)             # Phase 1, unchanged
    default_runner = SubprocessStageRunner(...)  # Phase 1, unchanged
    library_runner = None
    if any(stage.tool.startswith("LIB:") for stage in loaded.stages):
        try:
            import atlas.library_runner  # confirms the module itself imports;
                                           # does NOT confirm content-pipeline is installed
            library_runner = atlas.library_runner.LibraryStageRunner()
        except ImportError:
            library_runner = None  # CompositeStageRunner surfaces the failure per-stage instead
    runner = CompositeStageRunner(default=default_runner, library=library_runner)
    return Pipeline(..., runner=runner, stages=loaded.stages, workflow_name=loaded.name)
```

Note: `library_runner.py` itself has no content-pipeline import at module level (NFR-3), so `import atlas.library_runner` always succeeds regardless of whether content-pipeline is installed — the actual "is content-pipeline importable" check happens lazily, per-stage, inside `LibraryStageRunner.run()` (§6.2). The `library_runner = None` branch above is therefore effectively dead in practice (atlas's own `library_runner.py` always imports cleanly) but is retained as a defensive guard in case a future atlas packaging change makes `library_runner.py` itself conditionally absent.

### 6.4 Why a missing content-pipeline install is `failure` + a named alternative, not silent Mode-B fallback

TRD-v2 §14 Phase 2 exit criteria says: "content-pipeline integration is optional (atlas runs without it installed, falling back to CLI dispatch)." **Maintainer-resolved reading (Resolved Decision #2, 2026-06-30):** "falling back to CLI dispatch" means **two shipped YAML variants the user chooses between**, not automatic runtime import-check-and-switch. The maintainer's words: *"Runtime import-check-and-silently-switch is more magic than this system should have. Explicit > implicit."*

- `job.yaml` (`name: job`) — Mode A, `LIB:` in-process, requires content-pipeline (§3.2).
- `job-cli.yaml` (`name: job-cli`) — Mode B, `RAW:` subprocess, no content-pipeline Python dependency (§3.7).

Both ship in-package (`src/atlas/workflows/`). When content-pipeline is not installed and the user runs `--workflow job`, the `LIB:` stage fails with `content_pipeline_not_installed` and an error message that **names `job-cli.yaml`** as the dependency-free path to run (`--workflow job-cli`). This is strictly better than the original "documented snippet" plan: because `job-cli.yaml` is a real shipped file resolvable through Phase 1's built-in search-path tier, the error can point at a `--workflow job-cli` invocation that *works immediately*, with no copy-paste-a-YAML-into-`~/.atlas/workflows/` step.

Why not *automatic* runtime fallback (silently retrying a `LIB:` stage as a `RAW:` shell-out): it would require `LibraryStageRunner` to know the equivalent CLI invocation for every registry entry (doubling the registry's surface) and would hide a meaningful environment difference (library vs. subprocess execution, which produce differently-namespaced plumb runs — see §3.7) behind a silent switch. The maintainer explicitly rejected that magic. The two-variant design keeps the choice visible and the measurement honest.

---

## 7. Error Handling & Edge Cases

| Case | Handling |
|---|---|
| content-pipeline not `pip install -e`'d | `LIB:` stage → `StageOutcome(status="failure", error_type="content_pipeline_not_installed")`. Run halts at that stage (consistent with Phase 1's existing `failure` handling in `Pipeline.step()` / `run_to_completion()` — no special-casing needed). The user-facing error names `--workflow job-cli` as the dependency-free alternative (§3.7, §6.4). |
| `JOB_PROFILE_PATH` / `Settings` env vars unset (content-pipeline's own config) | `score_jobs_adapter.invoke()` lets `_load_profile_text()`'s existing `FileNotFoundError` propagate up to `LibraryStageRunner`'s broad `except Exception`, surfaced as `library_adapter_error`. |
| `ScoreJobsUseCase.run_pending()` returns `items_pending=0` (nothing to score) | Not an error — `StageOutcome(status="success", output_text=<empty-stats report>)`. The `gate_shortlist` gate still fires; human sees "0 pending, 0 scored" and can approve trivially or reject to signal "ingest didn't actually capture anything new." |
| `CaptureUseCase.run_all()` partially fails (one source errors, others succeed) | `any_failed` aggregates across sources; if any source failed, `ingest_postings` stage reports `status="failure"`. This is stricter than content-pipeline's own CLI (which returns a process exit code but still writes whatever succeeded) — **deliberate**: `ingest_postings` has no gate, so a silent partial failure would flow straight into `score_fit` scoring a possibly-incomplete capture set with no human checkpoint to catch it. Resolved by the maintainer (2026-06-30) as binding: keep atlas stricter — a measured pipeline assumes complete upstream data. "Partial success is fine" was explicitly rejected. |
| `tailor_materials` / `emit_package` (unchanged `RAW:`/Mode-C stages) fail | Identical to any Phase-1 `RAW:`/`SubprocessStageRunner` failure — `plugin_timeout` / `plugin_nonzero_exit`, unchanged code path, no Phase 2 logic involved. |
| `LIB:<ref>` not in `_REGISTRY` (typo, or a future workflow author copies `job.yaml` and edits a tool string wrong) | `WorkflowValidationError` is **not** raised at load time — Phase 1's loader has no knowledge of `LIB:`/`RAW:` semantics (TRD-v2 §3.1 loader contract only validates `span_kind`, name format/uniqueness, gate uniqueness — not tool-string content). The error surfaces at **dispatch** time instead, as `library_ref_unknown`. This is consistent with `RAW:` stages today, which also aren't validated for prompt sanity at load time. |
| Two `job` runs started concurrently in the same repo | Same v1/Phase-1 constraint carried forward: "one `atlas run` per repo at a time; behavior undefined otherwise" (PRD Assumptions). Phase 2 introduces no new concurrency surface. |

**Retry/fallback strategy:** None of these are transient. Consistent with Phase 1's stance — load/dispatch-time errors are deterministic, no retry logic.

---

## 8. Dependencies & Interfaces

| Dependency | Direction | Notes |
|---|---|---|
| **Phase 1 (T1.1–T1.13, all)** | hard, blocking | See "Blocking dependency" banner at the top of this TRS. T2.0 gates on it. |
| content-pipeline (optional) | `atlas → content-pipeline`, never reverse (unchanged rule, TRD-v2 §6, `job-workflow-scope.md` §2) | `pip install -e ../content-pipeline`. atlas's `pyproject.toml` gets a new `[project.optional-dependencies] job = ["content-pipeline @ file://../content-pipeline"]`-style entry (exact syntax confirmed in T2.6) — **not** a core dependency. |
| `library_runner.py` → `atlas.orchestrator` | internal | Imports `RunContext`, `StageOutcome` (no new types needed — reuses Phase 1's `StageOutcome` shape as-is). |
| `library_adapters/*` → content-pipeline | internal, lazy | Each adapter does its own `from src.application.use_cases... import ...` inside the function body, never at module level (NFR-3). |
| `cli.py` → `library_runner.py` | internal | `_make_pipeline()` constructs `CompositeStageRunner` per §6.3. |
| `orchestrator.py` → `CompositeStageRunner` | internal, additive only | `Pipeline` itself is unmodified; `CompositeStageRunner` is a new class in the same module (or a new file — see Resolved Decision #1) satisfying the existing `StageRunner` Protocol. |

---

## 9. Security Considerations

Carried from TRD-v2 §4 Security and Phase 1's §9, applied to Phase 2's surface:

- **No new subprocess surface.** `LibraryStageRunner` makes zero subprocess calls — it's in-process Python. The only subprocess surface in `job.yaml` is `tailor_materials`/`emit_package`'s existing `RAW:` → `SubprocessStageRunner` → `claude -p` path, already covered by Phase 1's security model (workflow-author-is-the-user trust boundary).
- **`LIB:<ref>` registry is a closed allow-list** (`_REGISTRY` dict, §3.4), not a dynamic/arbitrary dotted-path execution from YAML content. A `job.yaml` cannot specify `LIB:os.system` and have it execute — only the two hardcoded registry entries (`content_pipeline.capture`, `content_pipeline.score_jobs`) resolve to anything; any other `LIB:<ref>` is `library_ref_unknown`. This is a **stronger** trust boundary than `RAW:`'s "equivalent to the user typing a command" — `LIB:` stages can only ever invoke the two specific, code-reviewed adapter functions shipped with atlas, regardless of what string appears in the YAML.
- **content-pipeline's own credential handling is out of scope here.** `Settings()` reads content-pipeline's own env vars (`ANTHROPIC_API_KEY`, `JOB_PROFILE_PATH`, Gmail/IMAP creds if configured) — atlas does not read, log, or persist these; it only triggers content-pipeline's existing, already-reviewed credential-loading path.
- **`output_text` surfaced at the `gate_shortlist` prompt may contain job-posting content** (company names, role titles, the human's own profile-match reasoning) — this is expected and desired (it's the whole point of the gate), but worth noting it's the first Phase 2 stage whose gate content includes potentially identifying real-world data (vs. dev pipeline's code/PRD text). No new exposure beyond what already prints to the user's own terminal.

---

## 10. Testing Strategy

Per TRD-v2 §10's coverage carry-forward (Phase 2 doesn't define new module-specific coverage targets in TRD-v2; this TRS sets `library_runner.py` to the same 85%+ bar TRD-v2 §10 sets for `cli_backend.py`, since both are dispatch-strategy modules of comparable risk profile).

### Unit tests (new file: `tests/unit/test_library_runner.py`)

| Test | Validates |
|---|---|
| `test_library_runner_unknown_ref` | `LIB:not_a_real_ref` → `StageOutcome(status="failure", error_type="library_ref_unknown")`. |
| `test_library_runner_content_pipeline_not_installed` | Mocks `_import_adapter` to raise `ImportError` → `error_type="content_pipeline_not_installed"`. |
| `test_library_runner_adapter_exception_caught` | Mocks a registered adapter to raise `RuntimeError` → `error_type="library_adapter_error"`, `output_text` contains the exception message, no exception propagates out of `run()`. |
| `test_library_runner_success_passthrough` | Mocks a registered adapter to return a `StageOutcome(status="success", ...)` → returned unchanged. |
| `test_library_runner_ignores_timeout_s` | A `LIB:` stage with `timeout_s: 1` (a value that would trip a real subprocess timeout) dispatches via a mocked slow adapter and completes normally — confirms `LibraryStageRunner` does not enforce `stage.timeout_s` for in-process calls (Decision #7 / Resolved Decision #5). |
| `test_composite_runner_dispatches_lib_prefix` | `CompositeStageRunner.run()` with a `LIB:`-prefixed stage calls the library runner, not the default. |
| `test_composite_runner_dispatches_raw_and_plugin_to_default` | `RAW:`-prefixed and plain plugin-command stages both call the default (`SubprocessStageRunner`-shaped mock), not the library runner. |
| `test_composite_runner_library_none_surfaces_failure` | `library=None` + a `LIB:` stage → `error_type="library_runner_unavailable"`, no `AttributeError`/crash. |

### Unit tests (new file: `tests/unit/test_library_adapters.py`)

| Test | Validates |
|---|---|
| `test_score_jobs_adapter_success` | Mocks `ScoreJobsUseCase.run_pending()` to return a non-failed `ScoreJobsRunResult`; mocks `render_report()`; asserts `StageOutcome(status="success", output_text=<report>)`. |
| `test_score_jobs_adapter_failure` | Mocks `run_pending()` to return `failed=True`; asserts `status="failure"`, `error_type="score_jobs_failed"`. |
| `test_score_jobs_adapter_zero_pending` | Mocks `run_pending()` to return `items_pending=0`; asserts `status="success"` (not an error — see §7 edge case table). |
| `test_capture_adapter_all_sources_succeed` | Mocks `CaptureUseCase.run_all()` to return all-non-failed results; asserts `status="success"`. |
| `test_capture_adapter_partial_failure` | One mocked `CaptureRunResult.failed=True` among several → `status="failure"` (deliberate strict behavior, §7). |

### Integration test (new file: `tests/integration/test_job_workflow_e2e.py`)

| Test | Validates |
|---|---|
| `test_job_workflow_produces_correct_span_tree` | `atlas run "<seed>" --workflow job` (content-pipeline mocked at the use-case boundary, not the orchestrator boundary — i.e., mock `ScoreJobsUseCase`/`CaptureUseCase` themselves, not `LibraryStageRunner`) produces exactly 4 spans in order: `tool:ingest_postings`, `verify:score_fit`, `subagent:tailor_materials`, `tool:emit_package`. |
| `test_job_workflow_gate_scores_namespaced` | 3 `scorer='user_signal'` rows with `metric_name` ∈ `{job.gate_shortlist, job.gate_materials, job.gate_done}`. |
| `test_job_and_dev_coexist_in_same_db` | A `dev` run + a `job` run against the same plumb DB path; query by `task_id` prefix returns the correct, non-overlapping subset for each. |
| `test_job_workflow_content_pipeline_not_installed_fails_cleanly` | With content-pipeline modules made unimportable (e.g. `sys.modules` patched to raise on `src.application...` imports), `ingest_postings` fails with `content_pipeline_not_installed`, run halts, no crash, **and the surfaced error message contains the string `job-cli`** (the named alternative — Resolved Decision #2). |
| `test_job_cli_workflow_runs_without_content_pipeline` | `atlas run "<seed>" --workflow job-cli` (content-pipeline modules unimportable, but the `content-pipeline` CLI mocked as a stub on `PATH`) completes its 4 stages via the `RAW:`/`SubprocessStageRunner` path with zero `LibraryStageRunner` involvement — proves the dependency-free variant is genuinely runnable, not just documented. |
| `test_job_cli_metrics_namespaced_separately` | `job-cli` gate scores write `metric_name` ∈ `{job-cli.gate_shortlist, job-cli.gate_materials, job-cli.gate_done}` — distinct from `job.*` (§3.7's separate-namespacing consequence is asserted, not just noted). |

### Updated existing tests

| File | Change |
|---|---|
| `tests/unit/test_workflow_loader.py` | Add `test_load_job_yaml_via_loader` — confirms `job.yaml` loads through Phase 1's existing loader with zero loader code changes (proves FR-1's "zero loader changes required" claim). No new validation logic needed since `job.yaml` uses only fields Phase 1 already supports. |
| `tests/unit/test_pipeline.py` | No changes expected — `Pipeline` itself is untouched by Phase 2 (FR-7). If `CompositeStageRunner` lives in `orchestrator.py` (Resolved Decision #1), its own unit tests go in a new `test_composite_runner` section of this file or a dedicated file — see T2.2. |
| `tests/e2e/test_e2e_happy_path.py` | No changes — this remains the dev-pipeline-only e2e proof; Phase 2 doesn't touch it (regression check only, T2.8). |

### Mocking strategy

- Library-runner unit tests mock at the `_import_adapter` / adapter-function boundary — no real content-pipeline calls.
- Integration test mocks at the content-pipeline *use-case* boundary (`ScoreJobsUseCase`, `CaptureUseCase` themselves are test doubles), proving atlas's wiring is correct without needing real LLM calls, real job-board scraping, or real `Settings()` env vars — consistent with Phase 1's "mock the subprocess, not the real CLI" approach for `AntigravityBackend` (TRD-v2 §12 risk-mitigation row).
- No real `claude -p` subprocess calls in CI for `tailor_materials`/`emit_package` — same stub-plugin pattern Phase 1's `test_e2e_happy_path.py` already uses for `SubprocessStageRunner`.

### Coverage target

`library_runner.py` ≥ 85% (mirrors TRD-v2 §10's `cli_backend.py` target — comparable dispatch-strategy risk profile). `library_adapters/*.py` — no hard percentage set in TRD-v2; this TRS sets 80% (matches the existing repo-wide CI floor) since adapters are thin wiring code, not validation-heavy like the loader.

---

## 11. Performance Considerations

- **No NFR-1/NFR-2 impact** — `LibraryStageRunner`/`CompositeStageRunner` add one `str.startswith()` check per stage dispatch; negligible versus Phase 1's existing < 50ms/100ms budgets, which `job.yaml`'s load/resolve path inherits unchanged.
- **`ingest_postings` and `score_fit` real-world latency is dominated by network I/O (scraping, LLM batch calls)** — not a Phase 2 atlas-engine performance concern; this is content-pipeline's own existing latency profile, unchanged by being called in-process vs. via CLI subprocess (arguably *faster* in-process, since Mode A avoids subprocess spawn overhead — a minor, unmeasured win from choosing Mode A over Mode B, not a tracked NFR).
- **No caching needed.** Each `LIB:` stage dispatch happens once per pipeline run, not in a hot loop — same reasoning as Phase 1's loader perf section.

---

## Tasks

Flat list, ordered by execution sequence. Cross-task dependencies captured via `Dependencies`.

* **[T2.0] Verify Phase 1 exit criteria before starting any Phase 2 work** [Effort: S]
  - **Description**: Hard gate. Confirm Phase 1's TRS (T1.1–T1.13) has merged and TRD-v2 §13 #1–4 (dev-pipeline parity, loader correctness, workflow-aware state, routing fixture stability) all independently pass on the current `main`. This is a verification task, not new code — it exists because this TRS is written against a not-yet-real Phase 1 codebase (see banner at top).
  - **Acceptance Criteria**:
    - [ ] `src/atlas/workflow_loader.py` exists and `tests/unit/test_workflow_loader.py` passes.
    - [ ] `src/atlas/workflows/dev.yaml` exists; `test_dev_pipeline_parity` passes.
    - [ ] `Pipeline.__init__` accepts `stages`/`workflow_name` kwargs (grep-confirm signature).
    - [ ] `StageName`/`GateLabel` no longer exist anywhere in `src/`/`tests/` (Phase 1's T1.11 grep-zero criterion, re-verified).
    - [ ] `StageSpec` has **10 fields** including `timeout_s: int | None` (Phase 1 commit `a70029b` / Resolved Decision #5); `_ALLOWED_STAGE_KEYS` includes `gate_is_async` + `timeout_s`; `SubprocessStageRunner` resolves timeouts via `resolve_timeout()` (Phase 1 §6.7). Phase 2's `job.yaml` (T2.1) and the `timeout_s`-inert behavior of `LibraryStageRunner` (T2.2) both depend on this being in place.
    - [ ] Full Phase 1 test suite green on `main` (or the integration branch Phase 2 will build on).
  - **Files to Create/Modify**: None — verification only.
  - **Dependencies**: Phase 1 (T1.1–T1.13, all)
  - **Testing Requirements**: Full existing suite re-run, no new tests

* **[T2.1] Author `job.yaml` + `job-cli.yaml` (the matched Mode-A / Mode-B pair)** [Effort: S]
  - **Description**: Write `src/atlas/workflows/job.yaml` per §3.2 (`LIB:` for `ingest_postings`/`score_fit`, `RAW:` for `tailor_materials`/`emit_package`; `timeout_s: 1800` on `tailor_materials` only). Write `src/atlas/workflows/job-cli.yaml` per §3.7 (the dependency-free Mode-B variant: `name: job-cli`, all stages `RAW:`, `timeout_s: 1800` on both `score_fit` and `tailor_materials` since they're now subprocess stages). Confirm both load cleanly through Phase 1's `workflow_loader.load_workflow_file()` with zero loader changes. (Resolved Decision #2: `job-cli.yaml` is a shipped artifact, not a doc snippet.)
  - **Acceptance Criteria**:
    - [ ] `load_workflow_file(job_yaml_path)` returns a `LoadedWorkflow` with `name="job"`, 4 stages, correct `gate_index` enumeration (0, 1, 2 — `ingest_postings` has no gate).
    - [ ] `load_workflow_file(job_cli_yaml_path)` returns a `LoadedWorkflow` with `name="job-cli"`, 4 stages, same gate enumeration; every stage's `tool` is `RAW:`-prefixed (no `LIB:`).
    - [ ] `span_kind` values (`tool`, `verify`, `subagent`, `tool`) validate against Phase 1's `SPAN_KINDS` frozenset for both files with no loader changes.
    - [ ] Gate labels (`gate_shortlist`, `gate_materials`, `gate_done`) are unique within each file.
    - [ ] `job.yaml`: `tailor_materials.timeout_s == 1800`; the other three stages have `timeout_s is None` (§3.2 — `LIB:` stages omit it because it's inert in-process; `emit_package` accepts the 600s fallback).
    - [ ] `job-cli.yaml`: `score_fit.timeout_s == 1800` **and** `tailor_materials.timeout_s == 1800` (both honored here — `RAW:`/subprocess stages, §3.7).
    - [ ] Both files are readable from an installed wheel (same packaging path Phase 1's T1.1 proved for `dev.yaml`).
  - **Files to Create/Modify**:
    - `src/atlas/workflows/job.yaml` - new
    - `src/atlas/workflows/job-cli.yaml` - new
    - `tests/unit/test_workflow_loader.py` - add `test_load_job_yaml_via_loader`, `test_load_job_cli_yaml_via_loader`
  - **Dependencies**: T2.0
  - **Testing Requirements**: Unit

* **[T2.2] Implement `library_runner.py` — `LibraryStageRunner` + `library_adapters/`** [Effort: L]
  - **Description**: Implement `LibraryStageRunner` per §3.4 and the two adapters per §3.5, including the `stage` parameter fix noted in §3.5's callout (adapters take `stage: StageSpec` explicitly, not `stage=None`). Wire the `_REGISTRY` closed allow-list. Ensure no content-pipeline import happens at module level anywhere in `library_runner.py` or `library_adapters/__init__.py` (NFR-3 — grep-verify no top-level `from src....` imports).
  - **Acceptance Criteria**:
    - [ ] All `test_library_runner_*` unit tests (§10) pass (including `test_library_runner_ignores_timeout_s`).
    - [ ] All `test_*_adapter_*` unit tests (§10) pass.
    - [ ] `grep -n "^from src\.\|^import src\." src/atlas/library_runner.py src/atlas/library_adapters/*.py` returns zero hits (confirms lazy-import discipline).
    - [ ] `mypy src` passes with content-pipeline modules referenced only inside `TYPE_CHECKING` blocks or function-local imports.
    - [ ] `LibraryStageRunner` does **not** reference `stage.timeout_s` anywhere (it's inert for in-process calls, Resolved Decision #5) — the runner's docstring documents this; `test_library_runner_ignores_timeout_s` (§10) confirms a `LIB:` stage with a tiny `timeout_s` is not enforced.
  - **Files to Create/Modify**:
    - `src/atlas/library_runner.py` - new
    - `src/atlas/library_adapters/__init__.py` - new
    - `src/atlas/library_adapters/score_jobs_adapter.py` - new
    - `src/atlas/library_adapters/capture_adapter.py` - new
    - `tests/unit/test_library_runner.py` - new
    - `tests/unit/test_library_adapters.py` - new
  - **Dependencies**: T2.0
  - **Testing Requirements**: Unit, ≥ 85% coverage on `library_runner.py`, ≥ 80% on `library_adapters/`

* **[T2.3] Implement `CompositeStageRunner`** [Effort: M]
  - **Description**: Add `CompositeStageRunner` per §3.3, satisfying the `StageRunner` Protocol unchanged. Decide placement (`orchestrator.py` vs. a new `composite_runner.py`) per Resolved Decision #1 — default to `orchestrator.py` alongside `SubprocessStageRunner` unless the LoC budget (NFR-6 / TRD-v2 §5's ~600-line engine cap, which `orchestrator.py` counts against) is already tight, in which case use a new file.
  - **Acceptance Criteria**:
    - [ ] All 3 `test_composite_runner_*` unit tests (§10) pass.
    - [ ] `CompositeStageRunner` satisfies `StageRunner` Protocol (type-checked via `mypy`, not just duck-typed).
    - [ ] `Pipeline`'s own source (`orchestrator.py`'s `Pipeline` class) has zero diff from Phase 1's final state — confirmed by a diff/grep check, proving FR-7's "zero changes to Pipeline" claim.
  - **Files to Create/Modify**:
    - `src/atlas/orchestrator.py` - add `CompositeStageRunner` (or new file, per decision above)
    - `tests/unit/test_pipeline.py` or new `tests/unit/test_composite_runner.py` - new tests
  - **Dependencies**: T2.2
  - **Testing Requirements**: Unit

* **[T2.4] Render `score_fit`'s gate content end-to-end** [Effort: S]
  - **Description**: Confirm the CLI's gate-prompt display path (`ClickPrompter.ask()` or its caller in `cli.py`) actually surfaces `StageOutcome.output_text` to the user at `gate_shortlist`, per §3.6's flagged new requirement (Phase 1 dev stages relied on visible subprocess stdout; Mode A stages have none). If the current display path doesn't print `output_text` before prompting, extend it minimally — this is the one place Phase 2 may need a small `cli.py` change beyond pure wiring.
  - **Acceptance Criteria**:
    - [ ] A manual or scripted run shows the rendered shortlist report (GREEN/YELLOW/RED sections) printed to the terminal immediately before the `gate_shortlist` approve/reject prompt.
    - [ ] Dev-pipeline gate prompts are unaffected (same content shown as before — this change is additive, not a reformat of the existing prompt).
  - **Files to Create/Modify**:
    - `src/atlas/cli.py` - gate-prompt display path, only if a gap is found
  - **Dependencies**: T2.2
  - **Testing Requirements**: Integration (extends `test_job_workflow_e2e.py`)

* **[T2.5] Wire `_make_pipeline()` for `job.yaml`** [Effort: M]
  - **Description**: Implement §6.3's `_make_pipeline()` extension: construct `CompositeStageRunner` with `library=LibraryStageRunner()` whenever any resolved stage's `tool` starts with `LIB:`. No new CLI flags — this is pure internal wiring triggered by `--workflow job` resolving to a YAML containing `LIB:` stages. Also wire the `content_pipeline_not_installed` user-facing error message so it **names `job-cli` as the alternative** (Resolved Decision #2, §6.4) — the message must contain a runnable `atlas run "<task>" --workflow job-cli` suggestion, surfaced at the point the CLI catches the stage failure (same `typer.echo(..., err=True)` path as Phase 1's other error handling).
  - **Acceptance Criteria**:
    - [ ] `atlas run "<task>" --workflow job` (content-pipeline mocked) successfully constructs a `Pipeline` with a `CompositeStageRunner` wired correctly.
    - [ ] `atlas run "<task>" --workflow dev` continues to construct a `Pipeline` with the plain `SubprocessStageRunner` as `runner` (or a `CompositeStageRunner` with `library=None` that never triggers, since `dev.yaml` has no `LIB:` stages) — either is acceptable as long as dev-pipeline behavior is byte-identical to Phase 1.
    - [ ] `atlas run "<task>" --workflow job-cli` constructs a `Pipeline` whose `CompositeStageRunner` never needs the library runner (all stages `RAW:`) — runs with content-pipeline uninstalled.
    - [ ] When a `LIB:` stage fails with `content_pipeline_not_installed`, the message printed to the user contains the substring `job-cli` and a `--workflow job-cli` invocation (asserted by `test_job_workflow_content_pipeline_not_installed_fails_cleanly`, §10).
  - **Files to Create/Modify**:
    - `src/atlas/cli.py` - `_make_pipeline()` + the `content_pipeline_not_installed` error message
  - **Dependencies**: T2.2, T2.3
  - **Testing Requirements**: Integration

* **[T2.6] Add content-pipeline as an optional dependency** [Effort: S]
  - **Description**: Add `[project.optional-dependencies] job = [...]` (or equivalent `uv`-compatible shape) to `pyproject.toml`, pointing at the local `../content-pipeline` path per TRD-v2 §6's "editable install" pattern. Confirm `uv sync` (core, no extras) does **not** pull in content-pipeline, and `uv sync --extra job` does.
  - **Acceptance Criteria**:
    - [ ] `uv sync` (no extras) succeeds without content-pipeline installed; `atlas run --workflow dev` still works.
    - [ ] `uv sync --extra job` installs content-pipeline editable; `python -c "from src.application.use_cases.score_jobs import ScoreJobsUseCase"` succeeds afterward.
    - [ ] `pyproject.toml`'s core `[project.dependencies]` list is unchanged (no new hard dependency).
  - **Files to Create/Modify**:
    - `pyproject.toml` - add `[project.optional-dependencies] job`
  - **Dependencies**: None (can run in parallel with T2.1–T2.5)
  - **Testing Requirements**: CI green (one job with the extra installed, one without — see T2.9)

* **[T2.7] Document the two-variant choice (`job` vs `job-cli`)** [Effort: S]
  - **Description**: Per Resolved Decision #2 / §3.7 / §6.4, document — in a short README section or guide doc — that two shipped workflows exist: `job` (Mode A, library, requires `uv sync --extra job`) and `job-cli` (Mode B, subprocess, dependency-free). The authoring of `job-cli.yaml` itself moved to T2.1 (it's now a shipped artifact, not a doc snippet); this task is the *user-facing explanation* of when to pick which, and the note that the `content_pipeline_not_installed` error auto-points at `job-cli`. No code — documentation only.
  - **Acceptance Criteria**:
    - [ ] Doc names both shipped workflows and states the one-line difference (library vs subprocess; content-pipeline required vs not).
    - [ ] Doc explains *why* two variants exist instead of automatic fallback (explicit > implicit — Resolved Decision #2), linking back to TRD-v2 §3.8 and `job-workflow-scope.md` §2.
    - [ ] Doc notes the consequence that `job` and `job-cli` runs are namespaced separately in plumb (§3.7) — a user running both won't see merged metrics.
  - **Files to Create/Modify**:
    - `README.md` or `docs/3_guides/job_workflow.md` - new section/doc
  - **Dependencies**: T2.1
  - **Testing Requirements**: None (docs)

* **[T2.8] End-to-end job-workflow test + dev-pipeline regression re-run** [Effort: M]
  - **Description**: Implement all integration tests in §10's "Integration test" table (the 4 `job`-variant tests + the 2 `job-cli`-variant tests added per Resolved Decision #2). Re-run `tests/e2e/test_e2e_happy_path.py` unmodified to confirm zero dev-pipeline regression from Phase 2's additions (FR-7).
  - **Acceptance Criteria**:
    - [ ] All `test_job_workflow_e2e.py` tests pass: span-tree shape, metric namespacing, dev/job coexistence, content-pipeline-not-installed failure path **(incl. the error naming `job-cli`)**, `job-cli` runs dependency-free, and `job-cli` metrics namespaced separately.
    - [ ] `test_e2e_happy_path.py` passes unmodified — zero file changes required (same proof-bar Phase 1's T1.13 set).
  - **Files to Create/Modify**:
    - `tests/integration/test_job_workflow_e2e.py` - new
  - **Dependencies**: T2.1, T2.2, T2.3, T2.4, T2.5
  - **Testing Requirements**: Integration + E2E regression

* **[T2.9] CI gate updates — job extra** [Effort: S]
  - **Description**: Update `.github/workflows/ci.yml` (or equivalent) so the job-workflow test suite runs in a job/step that installs the `job` extra (T2.6), while the core suite continues running without it — proving FR-8's "atlas runs without content-pipeline installed" claim is CI-enforced, not just documented.
  - **Acceptance Criteria**:
    - [ ] CI has (at minimum) one job/step that runs the full suite *without* the `job` extra and asserts `test_job_workflow_content_pipeline_not_installed_fails_cleanly` passes.
    - [ ] CI has a job/step that runs *with* the `job` extra installed and asserts the rest of `test_job_workflow_e2e.py` passes.
  - **Files to Create/Modify**:
    - `.github/workflows/ci.yml` - add/extend job-extra test matrix entry
  - **Dependencies**: T2.6, T2.8
  - **Testing Requirements**: CI green on both legs

* **[T2.10] Document the hub-and-spoke trigger model** [Effort: S]
  - **Description**: Per TRD-v2 §14 Phase 2's scope bullet, add a short doc (README section or new guide) describing the second-brain → ai-workx skill → atlas → content-pipeline → plumb flow, per `job-workflow-scope.md` §1's diagram. Explicitly note the ai-workx trigger skill itself is Phase 4, out of scope here — this task documents the *model*, not the skill.
  - **Acceptance Criteria**:
    - [ ] Doc includes (or links to) the hub-and-spoke diagram from `job-workflow-scope.md` §1.
    - [ ] Doc explicitly states the trigger skill is future work (Phase 4), avoiding implying it already exists.
  - **Files to Create/Modify**:
    - `README.md` or `docs/3_guides/job_workflow.md` - new section (may combine with T2.7's doc)
  - **Dependencies**: T2.1
  - **Testing Requirements**: None (docs)

---

## Phase Deliverables

- `src/atlas/workflows/job.yaml` ships as a built-in workflow, loadable via `atlas run --workflow job` with zero `workflow_loader.py` changes.
- `LibraryStageRunner` + `library_adapters/` dispatch `ingest_postings`/`score_fit` to content-pipeline's `CaptureUseCase`/`ScoreJobsUseCase` in-process (Mode A), with content-pipeline as a genuinely optional dependency (`[project.optional-dependencies] job`).
- `CompositeStageRunner` routes `LIB:`/`RAW:`/plugin-command stages to the correct runner with zero changes to `Pipeline` itself.
- A complete `job.yaml` run produces a 4-span tree and 3 namespaced gate scores (`job.gate_shortlist`, `job.gate_materials`, `job.gate_done`), coexisting in the same plumb DB as `dev` runs, queryable by `task_id` prefix.
- Dev-pipeline regression suite (`test_e2e_happy_path.py` + full Phase 1 suite) passes unmodified.
- `library_runner.py` ≥ 85% coverage; `library_adapters/` ≥ 80%; full suite ≥ 80% (unchanged CI floor).
- `ruff check`, `ruff format --check`, `mypy src` all green, including with the `job` optional extra both installed and not installed.
- Tests passing: full `pytest` suite (unit + integration; e2e run explicitly, matching Phase 1's `--ignore=tests/e2e` default convention).
- Documentation: hub-and-spoke trigger model documented; Mode-B content-pipeline-free alternative documented.

---

## Resolved Decisions & Clarifications

All five items below were resolved by the maintainer (also the TRD-v2 author) on 2026-06-30. None remain open. Each is now a binding constraint on implementation, not an assumption.

1. **`CompositeStageRunner` placement → (a) `orchestrator.py`, with a concrete split trigger. RESOLVED.** Keep `CompositeStageRunner` in `orchestrator.py` next to `SubprocessStageRunner`. **Split to a new `composite_runner.py` only if `orchestrator.py` exceeds ~500 lines after Phase 1 has landed** — check the line count at T2.3 and split then if over. This is a sharper trigger than the original "close to the ~600-line engine budget" — `orchestrator.py` itself crossing 500 lines is the concrete signal. Binding on T2.3.
2. **Automatic Mode-A → Mode-B runtime fallback → (a) NO auto-fallback; ship a second YAML artifact instead. RESOLVED.** The maintainer's TRD-v2 intent was **two YAML variants**, not runtime import-check-and-switch: `job.yaml` (Mode A, library, requires content-pipeline) and **`job-cli.yaml`** (Mode B, subprocess, no content-pipeline dependency). Runtime "import-check-and-silently-switch" is more magic than this system should have — **explicit > implicit.** When content-pipeline is not installed and the user runs `--workflow job`, the `LIB:` stage fails with a clear error **naming `job-cli.yaml` as the dependency-free alternative**. This upgrades the prior "documented variant" plan: `job-cli.yaml` becomes a **shipped in-package file** (T2.7), not just a doc snippet. See §3.7 (new) and §6.4 (revised). Binding on T2.1, T2.2, T2.7.
3. **`ingest_postings` partial-failure strictness → keep atlas stricter than content-pipeline's CLI. RESOLVED.** `ingest_postings` fails the whole stage if *any* configured source errors. Maintainer rationale (binding): **atlas is a measured pipeline where every gate decision assumes complete upstream data.** There is no gate after `ingest_postings` to catch a partial capture, so silent data loss would propagate undetected through `score_fit` (scoring an incomplete set) and every downstream gate. content-pipeline's looser CLI behavior (log failures, exit 0 unless all sources fail) is fine for interactive use, but atlas fails the stage and lets the user re-run. `capture_adapter.py`'s `any_failed` aggregation stays as specified in §3.5/§7. Binding on T2.2.
4. **Real `SourceConfig` / job-board credentials → (a) manual prerequisite; tests mock at the use-case boundary. RESOLVED.** The end-to-end tests (T2.8) mock at the use-case boundary so Phase 2 doesn't block on real scraper credentials. A genuinely live run (real scraping, real LLM scoring) is a manual verification step outside CI — same posture as Phase 1's `AntigravityBackend` "real dispatch in manual testing" note (TRD-v2 §13 #7). No change from the prior default.
5. **`timeout_s` inert for `LIB:`/in-process stages → (a) accept the asymmetry. RESOLVED.** `LibraryStageRunner` makes in-process calls with no subprocess to time out, so `StageSpec.timeout_s` is silently ignored for `LIB:` stages (see §3.2, §3.4). In-process content-pipeline calls are bounded by their own HTTP/LLM client timeouts, which is where a scraping/LLM timeout *belongs*. `job.yaml` sets `timeout_s` only on the `RAW:` stage that needs it (`tailor_materials: 1800`) and omits it on the two `LIB:` stages. Wrapping in-process calls in a `concurrent.futures`/signal-based timeout adds complexity for a problem that doesn't exist yet — **revisit only if a `LIB:` stage actually hangs in practice.** Binding on T2.1, T2.2.
