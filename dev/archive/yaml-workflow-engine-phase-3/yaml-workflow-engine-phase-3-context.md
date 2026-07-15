# Context — YAML Workflow Engine, Phase 3 TRS

Reference notes for anyone picking up this work cold.

## Status — no blocking dependency

Phases 1 and 2 are **merged on `main`** as of 2026-06-30 (unlike Phase 2's draft-against-pre-Phase-1
situation). T3.1 is a sanity grep + suite re-run, not a hard gate. The seams Phase 3 needs
(`StageSpec.backend`, `LoadedWorkflow.default_backend`, `SubprocessStageRunner` as the single
**`claude -p`** subprocess dispatcher, `CompositeStageRunner` wrapping it) are all present and
exercised by the existing test suite (**193 passing** at the post-review baseline).

### ⚠ Phase 2 review resolution changed the surface Phase 3 builds on (commit `53359e4`)

This TRS was first drafted against Phase 2's *pre-review* state. The Phase 2 code review landed
four fixes (commit `53359e4`, docs note in `7cd350a`) that Phase 3 must respect but does **not**
modify:

1. **New `ShellStageRunner`** (`src/atlas/shell_runner.py`, 118 LoC) — dispatches `SHELL:`-prefixed
   tools as a direct list-form subprocess (`shell=False`, closed allow-list `{content-pipeline}`,
   honors `timeout_s`, never raises). This is a *separate* dispatch path from `SubprocessStageRunner`
   (which stays the `claude -p` path). Phase 3 refactors only the latter.
2. **`CompositeStageRunner` gained a `shell=` slot** — now `CompositeStageRunner(default=..., library=...,
   shell=...)` (57 LoC, up from 41). `cli.py::_make_pipeline()` already wires a `ShellStageRunner`
   when any stage is `SHELL:`. Phase 3 must **preserve** this wiring, only adding `default_backend=`
   and `loaded_workflow=` to the `SubprocessStageRunner(...)` construction.
3. **`job_cli.yaml`'s two content-pipeline stages switched `RAW:` → `SHELL:`** — they now run the
   `content-pipeline` CLI as a real subprocess instead of routing the string through `claude -p`.
   So the `job.*` (LIB:, in-process) vs `job_cli.*` (SHELL:, subprocess CLI) metric split is now a
   genuine in-process-vs-subprocess comparison. Phase 3 leaves `job_cli.yaml` alone.
4. **`LibraryStageRunner` ImportError mapping narrowed** — only an ImportError naming a content-pipeline
   top-level package (`application`/`infrastructure`/`domain`) yields `content_pipeline_not_installed`;
   an atlas-adapter or unrelated ImportError now surfaces as `library_adapter_error`. Adapters dropped
   the `src.` import prefix (content-pipeline's src-layout maps to bare top-level names). Phase 3 leaves
   `library_runner.py` and the adapters alone.

**CI note:** `.github/workflows/ci.yml` now has an active `test-job-extra` leg guarded on a
`CONTENT_PIPELINE_TOKEN` repo secret (checks out the private content-pipeline, runs the real-import
suite; self-skips when the secret is absent). This is a **Phase 2 open item**, not a Phase 3 blocker —
Phase 3 adds no content-pipeline stages. See Phase 2 tasks "Post-review follow-up".

## Key files

### Source-of-truth docs (read first, in order)
- [`docs/2_architecture/TRD-v2.md`](../../../docs/2_architecture/TRD-v2.md) — the phase contract
  this TRS details. §3.4, §4 (Security), §6, §10, §13 #7–8, §14 (Phase 3), §11 (release tag
  v2.2) are the load-bearing sections.
- [`docs/1_product_and_research/cli-backend-dispatch.md`](../yaml-workflow-engine-design-notes/cli-backend-dispatch.md)
  — the architecture decision pinning **atlas** as the owner of headless-CLI subprocess dispatch
  (vs content-pipeline staying API-only). Sets the `CliBackend`-as-strategy direction this TRS
  implements verbatim.
- [`docs/1_product_and_research/headless-clis-reference.md`](../../../docs/1_product_and_research/headless-clis-reference.md)
  — the per-CLI flag/auth/quota reference. Part B (Claude `claude -p`) and Part C
  (Antigravity `agy -p`) are the implementation specs `ClaudeCodeBackend.build_argv()` and
  `AntigravityBackend.build_argv()`/`parse_result()` ground against. Part D is the
  per-dimension comparison table.
- Phase 1 TRS ([plan](../yaml-workflow-engine-phase-1/yaml-workflow-engine-phase-1-plan.md),
  [context](../yaml-workflow-engine-phase-1/yaml-workflow-engine-phase-1-context.md),
  [tasks](../yaml-workflow-engine-phase-1/yaml-workflow-engine-phase-1-tasks.md)) — established
  `StageSpec.backend`/`LoadedWorkflow.default_backend` as parsed-but-inert fields (Phase 1
  Resolved Decision #4: "parse but don't validate; Phase 3 owns the allow-list"). Phase 3 is
  where that allow-list (`_KNOWN_BACKENDS`) lands.
- Phase 2 TRS ([plan](../yaml-workflow-engine-phase-2/yaml-workflow-engine-phase-2-plan.md),
  [context](../yaml-workflow-engine-phase-2/yaml-workflow-engine-phase-2-context.md),
  [tasks](../yaml-workflow-engine-phase-2/yaml-workflow-engine-phase-2-tasks.md)) — added
  `job.yaml` with `tailor_materials.backend: claude` that Phase 2 deliberately left inert
  ("`tailor_materials`'s `backend: claude` field is parsed but not dispatched on a per-stage
  basis"). Phase 3 finally consumes it. **Read the Phase 2 tasks "Post-review follow-up" section**
  (resolved 2026-06-30, commit `53359e4`): it added `ShellStageRunner`, the `CompositeStageRunner`
  `shell=` slot, the `job_cli.yaml` `RAW:` → `SHELL:` switch, and the narrowed `LibraryStageRunner`
  ImportError mapping — the surface Phase 3 now builds against (summarized under "Phase 2 review
  resolution" above).
- v1 TRD: [`docs/2_architecture/TRD.md`](../../../docs/2_architecture/TRD.md) — NFRs / integrations
  that carry forward unchanged.

### TRS itself (this directory)
- [`yaml-workflow-engine-phase-3-plan.md`](./yaml-workflow-engine-phase-3-plan.md) — design
  (sections 1–12) + flat task list (T3.1–T3.10) + appendix.
- [`yaml-workflow-engine-phase-3-tasks.md`](./yaml-workflow-engine-phase-3-tasks.md) — checkbox
  progress tracking.

### Code targets

**New:**
- `src/atlas/cli_backend.py` — `CliBackend` Protocol + `ClaudeCodeBackend` + `AntigravityBackend`
  + `resolve_backend()` + `make_backend()` + `UnknownBackendError` + `_KNOWN_BACKENDS`. ≤ 200
  lines total (NFR-4).
- `tests/unit/test_cli_backend.py` — ~20 unit tests covering argv / parse_result / preflight /
  resolve / factory.
- `tests/integration/test_cli_backend_dispatch.py` — 4 integration tests covering end-to-end
  agy dispatch, mixed-backend workflow, dev-pipeline regression, and Phase 2's
  `tailor_materials.backend: claude` consumption.
- `docs/3_guides/cli_backends.md` — per-CLI auth + 4-tier resolution + `agy` experimental-status
  doc.

**Modified:**
- `src/atlas/orchestrator.py` — `SubprocessStageRunner.__init__` gains `default_backend` and
  `loaded_workflow` kwargs; `SubprocessStageRunner.run()` replaces its hardcoded
  `["claude", "-p", ...]` block (~lines 583–622; class starts at 535) with `backend.build_argv()` /
  `backend.preflight()` / `backend.parse_result()`. Net ~30 lines added; subprocess invocation
  pattern, timeout handling, and `plugin_resolver`/`build_prompt` flow are unchanged. Only the
  `claude -p` path — `ShellStageRunner`'s subprocess path is separate and untouched.
- `src/atlas/config.py` — `Config.default_backend: str = "claude"` field; `Config.load()`
  reads `[backend] default` from `.atlas.toml`.
- `src/atlas/cli.py` — `_make_pipeline()` passes `default_backend=cfg.default_backend` and
  `loaded_workflow=loaded` into `SubprocessStageRunner(...)`. ~2-line change. **Do not remove** the
  pre-existing `ShellStageRunner` import + `shell=shell` wiring added by the Phase 2 review.
- `tests/unit/test_phase4.py` (or `test_subprocess_runner.py` if that's where Phase 1 put the
  runner tests) — +5 tests for backend wiring + the load-bearing
  `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` security test.
- `tests/unit/test_config.py` — +2 tests for `[backend] default` parsing.
- `STATUS.md` — Phase 3 completion entry.

**Unchanged (verify, don't touch):**
- `src/atlas/workflow_loader.py` — Resolved Decision #7: loader does not validate `backend`
  field. Phase 1's loader knows nothing about `_KNOWN_BACKENDS`.
- `src/atlas/stages.py` — `StageSpec.backend` was added in Phase 1; Phase 3 only consumes it.
- `src/atlas/state.py` — no state-file shape changes.
- `src/atlas/composite_runner.py`, `library_runner.py`, `shell_runner.py`, `library_adapters/*` —
  Phase 2 work (incl. the review resolution in commit `53359e4`) unchanged. The `shell=` slot on
  `CompositeStageRunner` and the whole `ShellStageRunner` stay as-is.
- `src/atlas/plugin_resolver.py`, `plumb_io.py`, `post_commit_hook.py`, `worktree.py` — all
  unchanged.
- `src/atlas/workflows/*.yaml` — no YAML changes; existing `tailor_materials.backend: claude`
  in `job.yaml` already correct; `job_cli.yaml`'s `SHELL:` content-pipeline stages stay routed
  to `ShellStageRunner`.
- `tests/e2e/test_e2e_happy_path.py` — runs unmodified (regression proof).
- `tests/fixtures/routing_ground_truth.json` — dev pipeline unchanged.

If implementation finds any "unchanged" file genuinely needs editing, that's a signal the
design has drifted from this TRS — pause and reconcile before proceeding.

## Decisions made (during this TRS)

The seven items below are this TRS's binding design choices. Items #1–6 were presented to the
maintainer as multiple-choice clarifying questions before drafting; the maintainer **skipped
the question set without selecting**, so this TRS proceeded with each item's *recommended
default*. They remain settable — if the maintainer wants to override, the plan's
`§12 Pending Decisions & Clarifications` section enumerates each alternative with the
single-file impact of each swap. Item #7 was identified during drafting.

| # | Decision | Rationale |
| - | --- | --- |
| 1 | `parse_result()` output format → **plain-text for Claude; JSON for Antigravity**. | Claude's stdout is what Phase 1's dev-pipeline parity claim depends on (DEV-ESSENTIALS plugins print human-readable text that `gate_*` prompts surface). Switching Claude to JSON breaks FR-8 byte-identity. Antigravity, by contrast, *needs* JSON: plain-text mode offers no error envelope (just returncode + stderr), so distinguishing auth-failure vs rate-limit vs turn-limit requires the `error` field. See plan §12 #1 for the swap path. |
| 2 | Do NOT add `--bare` to Claude argv. | `--bare` skips auto-discovery of plugins/skills/`CLAUDE.md`/MCP — but the dev pipeline depends on DEV-ESSENTIALS plugin discovery (`dev-docs-be`, `code-review`, `consult-experts`). The TRD-v2 §3.4 table listing `--bare` is a research-note recommendation from `headless-clis-reference.md` Part B; not a hard contract. `--no-session-persistence` stays as the only "CI determinism" flag for Claude. See plan §12 #2. |
| 3 | `.atlas.toml` schema → **`[backend] default = "claude"`**, single key. | Smallest schema delta; matches TRD-v2 §3.4 wording exactly. Per-backend model subtables (e.g. `[backend.agy] model = ...`) explicitly deferred — existing top-level `model` field serves Claude; `AntigravityBackend` carries its own default. See plan §12 #3. |
| 4 | **No `atlas run --backend <name>` CLI flag in Phase 3.** | Stay with §3.4's 4-tier resolution exactly (per-stage → workflow default → config → hard default). Adding a flag later is non-breaking. Phase 1 / 2 both held the line on flag additions. See plan §12 #4. |
| 5 | `AntigravityBackend` default model → `"gemini-flash-lite"`. | Matches Claude's `haiku` cost-efficient default. Fits agy's documented free-tier allowance (~20 req/day on `gemini-flash-lite` per `headless-clis-reference.md` Part C). Per-stage / config overrides remain available. See plan §12 #5. |
| 6 | Backend module layout → **single new file `src/atlas/cli_backend.py`** (Protocol + both backends + helpers). | Matches Phase 2's `composite_runner.py` precedent (one focused module per dispatch strategy). Total ≤ 200 lines; clean coverage target. Split into `backends/` subpackage when a third backend lands. See plan §12 #6. |
| 7 | **`backend` field NOT validated at YAML load time.** | Loader knows nothing about `_KNOWN_BACKENDS`; unknown backend name fails at dispatch (`make_backend()` → `UnknownBackendError`), not at load. Matches existing `RAW:` posture (loader validates structure, not content). Keeps `workflow_loader.py` decoupled from `cli_backend.py`. Phase 1 deliberately left this coupling out (Phase 1 Resolved Decision #4 — "Phase 3 may define the allow-list differently anyway"). |

None of these contradict TRD-v2; where its text was ambiguous (`--bare` literalism vs
practical dev-pipeline impact, JSON-output-for-both-backends vs per-CLI choice, `[backend]`
schema shape, CLI-flag presence, agy default model, where backends live), this TRS resolved
them with the maintainer-style defaults documented above.

## Integration points

| Direction | Surface | Failure mode | Test coverage |
| --- | --- | --- | --- |
| `cli_backend.py::resolve_backend()` → `StageSpec` / `LoadedWorkflow` / `Config` | Pure 4-tier function (no I/O) | N/A — pure computation, no failure mode | Unit (`test_resolve_backend_priority_order` — table-driven 16-case matrix). |
| `cli_backend.py::make_backend()` → `_KNOWN_BACKENDS` allow-list | `make_backend(name)` raises `UnknownBackendError` for any name ∉ `{"claude", "agy"}` | Caller (`SubprocessStageRunner.run()`) catches and returns `StageOutcome(status="failure", error_type="unknown_backend")` | Unit (`test_make_backend_unknown_name_raises`). |
| `AntigravityBackend.preflight()` → `os.environ` | Reads `GEMINI_API_KEY` / `GOOGLE_API_KEY` exactly once per `run()` call | Either set → `None`; neither set → `(error_msg, "agy_missing_auth_env")` tuple | Unit (3 tests covering both-unset, gemini-set, google-set); integration (`test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` — load-bearing: asserts `subprocess.run` NEVER called). |
| `AntigravityBackend.parse_result()` → `json.loads(stdout)` | Parses the documented `agy --output-format json` envelope (`response`, `stats`, optional `error`) | `json.JSONDecodeError` → `agy_unparseable_output`; non-string `response` → `agy_response_not_string`; non-empty `error` field → `agy_response_error` | Unit (8-row decision-table coverage per plan §6.3). |
| `SubprocessStageRunner.run()` → `backend.build_argv()` / `backend.parse_result()` | Delegates argv + result-parsing to the resolved backend | Backend exceptions are NOT caught here — Protocol contract is "pure computation, < 1ms"; if a backend raises, it's a code bug, not a runtime case | Unit (5 new runner-level tests covering backend wiring); integration (4 dispatch-flow tests). |
| `Config.load()` → `.atlas.toml [backend] default` | TOML section read; falls back to `"claude"` if section absent or malformed | Malformed (e.g. `backend = "claude"` as top-level string) → safe `"claude"` default, no crash | Unit (`test_config_default_backend_from_toml` + `test_config_default_backend_fallback`). |
| `cli.py::_make_pipeline()` → `SubprocessStageRunner(default_backend=..., loaded_workflow=...)` | Threads `cfg.default_backend` + resolved `LoadedWorkflow` into runner constructor | N/A — pure wiring | Integration (`test_agy_dispatch_end_to_end_mocked` exercises the full path). |
| plumb (gate scores) | Unchanged from Phase 2: `namespaced_metric()` continues to work | N/A — Phase 3 introduces no new metric path | E2E regression (`test_e2e_happy_path.py` unmodified). |

## Where this TRS's task list maps to TRD-v2 §14 Phase 3 scope bullets

| TRD-v2 §14 Phase 3 bullet | This TRS's task |
| --- | --- |
| "Define `CliBackend` Protocol (§3.4)" | T3.2 |
| "Implement `ClaudeCodeBackend` — extract existing `SubprocessStageRunner` argv/parse logic into this class" | T3.2 (Protocol + class) + T3.4 (extraction from `SubprocessStageRunner.run()`) |
| "Implement `AntigravityBackend` — build argv per `agy -p` flag surface, parse JSON result per `agy` schema, validate headless auth env vars" | T3.2 (`build_argv`/`parse_result`/`preflight`); T3.3 (unit tests covering all branches) |
| "Refactor `SubprocessStageRunner` to accept a `CliBackend` (default: `ClaudeCodeBackend`)" | T3.4 |
| "Backend resolution logic: per-stage YAML → workflow default → `.atlas.toml [backend]` → hard default `claude`" | T3.2 (`resolve_backend()` helper) + T3.5 (`Config` field + `_make_pipeline()` wiring) |
| "Unit tests for both backends (argv construction, result parsing)" | T3.3 |
| "Integration test: at least one stage dispatches to `AntigravityBackend` with mocked subprocess" | T3.6 (`test_agy_dispatch_end_to_end_mocked`) |
| "Document per-CLI auth requirements and the experimental status of `agy` support" | T3.7 |

The remaining tasks (T3.1 sanity-check, T3.8 manual smoke, T3.9 lint/type, T3.10 status update)
are this TRS's standard hygiene tasks; they don't map to specific TRD-v2 bullets but follow the
precedent set by Phase 1 (T1.12 deps + CI, T1.13 e2e re-run) and Phase 2 (T2.0 verification,
T2.8 e2e + regression, T2.9 CI matrix).

## TRD-v2 §13 Phase 3 exit criteria → tests that prove them

| TRD-v2 §13 exit criterion | Proving test(s) |
| --- | --- |
| #7 "Multi-backend dispatch. At least one stage dispatches to `AntigravityBackend` and produces a valid `StageOutcome` (mocked in CI; real dispatch in manual testing if auth allows)." | `test_agy_dispatch_end_to_end_mocked` (T3.6); T3.8 manual smoke |
| #8 "Backend resolution. Per-stage override → workflow default → config default → hard default, verified by test." | `test_resolve_backend_priority_order` (T3.3) + `test_subprocess_runner_respects_stage_backend_field` / `test_subprocess_runner_respects_workflow_default_backend` (T3.4) |
| §14 Phase 3 "agy auth failure produces a clear error, not a silent hang." | `test_antigravity_backend_preflight_no_env` (T3.3) + `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` (T3.4) — the second asserts `subprocess.run` is NEVER called when auth is missing (load-bearing security test) |
| §14 Phase 3 "Existing dev pipeline runs unchanged (ClaudeCodeBackend is the default)." | `test_claude_code_backend_argv_byte_identical_to_phase2` (T3.3) + `test_dev_pipeline_unaffected_by_phase_3` (T3.6) + `test_e2e_happy_path.py` unmodified (T3.6 / T3.9) |
| §13 cross-cutting #9 "LoC budget — engine code ≤ ~600 lines total" | NFR-4 in plan; `cli_backend.py` ≤ 200 lines guardrail in T3.2's acceptance criteria |
| §13 cross-cutting #10 "No plumb migration." | Phase 3 introduces no plumb schema changes (plan §5); existing tests prove no regression |

## What this TRS deliberately does NOT cover

- **The ai-workx second-brain trigger skill itself** (Phase 4, explicitly out of TRD-v2 scope).
- **A real `agy -p` subprocess invocation in CI.** TRD-v2 §13 #7's wording is explicit: "mocked
  in CI; real dispatch in manual testing if auth allows." T3.8 covers the manual side as a
  best-effort smoke test that's not CI-gated.
- **Per-backend model knobs in `.atlas.toml`** (e.g. `[backend.claude] model = "haiku"`,
  `[backend.agy] model = "gemini-2.5-pro"`). The existing top-level `model` field serves
  Claude; `AntigravityBackend` carries its own constructor default. Per-stage overrides via
  YAML `backend:` + a future `model:` field (if added) would be the natural extension; YAGNI
  for Phase 3.
- **Browser-OAuth or interactive-auth flows for `agy`.** The maintainer-binding stance is
  fail-closed on missing API-key env vars (TRD-v2 §4 Security; Resolved Decision parallel to
  Phase 2's Resolved Decision #2 — "explicit > implicit"). Whether `agy` would eventually
  support a programmatic-API-key auth path (issue #78 contested) is an external concern; atlas
  just needs the env vars present.
- **A third backend (`codex`, etc.).** The Protocol + factory + allow-list pattern Phase 3
  establishes is the explicit extension point — adding a third backend later is a
  one-PR change to `cli_backend.py` (new class + extend `_KNOWN_BACKENDS` + extend
  `make_backend()`), with **zero** changes elsewhere by construction.
- **`Pipeline` or gate / worktree / plumb code.** TRD-v2 §6 is explicit on this boundary:
  "`Pipeline` sees only the `StageRunner` Protocol and `StageOutcome` — it does not know which
  CLI was used. Gates, worktrees, plumb instrumentation are all untouched." Phase 3 preserves
  this invariant by construction — `CliBackend` is internal to `SubprocessStageRunner`.

