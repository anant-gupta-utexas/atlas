# Tasks — YAML Workflow Engine, Phase 3 TRS

Progress checklist. Source-of-truth for design is
[`yaml-workflow-engine-phase-3-plan.md`](./yaml-workflow-engine-phase-3-plan.md).
Reference notes live in
[`yaml-workflow-engine-phase-3-context.md`](./yaml-workflow-engine-phase-3-context.md).

## Current

```
phase: not-started
gate:  none
next:  T3.1 — sanity-check Phase 1 + Phase 2 seams
```

## Status — prerequisites resolved (2026-06-30), incl. Phase 2 review

Phase 1 and Phase 2 are both merged on `main`, **and the Phase 2 code review is
resolved** (commit `53359e4`, docs note `7cd350a`). This TRS is written against the
current shipped codebase (not a draft-ahead-of-implementation snapshot like Phase
2's banner described). The seams Phase 3 consumes are already in place:

- `StageSpec.backend: str | None` — added in Phase 1 (`stages.py`), currently parsed
  but inert.
- `LoadedWorkflow.default_backend: str | None` — added in Phase 1
  (`workflow_loader.py`), currently parsed but inert.
- `SubprocessStageRunner` — the single **`claude -p`** subprocess dispatcher
  (`orchestrator.py`; class starts line 535, argv block ~583–622), today hardcodes
  `claude -p ...`. Phase 3 refactors this to delegate argv + result-parsing to a
  `CliBackend` strategy.
- `CompositeStageRunner` — Phase 2's `LIB:` / `SHELL:` / `RAW:` / plugin-command
  dispatcher (`composite_runner.py`, 57 LoC), unchanged by Phase 3. Its constructor
  now takes a third `shell=` slot.
- `ShellStageRunner` — **new in the Phase 2 review** (`shell_runner.py`, 118 LoC).
  Dispatches `SHELL:` tools as a direct list-form subprocess. Separate path from
  `SubprocessStageRunner`; **unchanged by Phase 3.**
- `job.yaml`'s `tailor_materials.backend: claude` — already declared in Phase 2;
  Phase 3 is the phase that finally consumes it. (`job_cli.yaml`'s content-pipeline
  stages are now `SHELL:` → `ShellStageRunner`, not `RAW:` — Phase 3 leaves them.)

There is **no T3.0 hard verification gate** (unlike Phase 2's T2.0). T3.1 is a
lightweight grep-confirm + baseline test re-run, not a blocking checkpoint.
Post-review test baseline: **193 passing**.

### What the Phase 2 review changed that Phase 3 must respect (commit `53359e4`)

Phase 3 modifies **only** the `claude -p` path inside `SubprocessStageRunner`. The
review resolution added a parallel `SHELL:` path Phase 3 must not break:

- `ShellStageRunner` (`shell_runner.py`) + `CompositeStageRunner(shell=...)` slot +
  the `cli.py::_make_pipeline()` `ShellStageRunner` wiring → **preserve, don't remove**.
- `job_cli.yaml` `RAW:` → `SHELL:` switch → **leave as-is**.
- `LibraryStageRunner` ImportError narrowed (`library_adapter_error` vs
  `content_pipeline_not_installed`); adapters dropped the `src.` prefix → **leave as-is**.
- CI now has an active `test-job-extra` leg gated on the `CONTENT_PIPELINE_TOKEN`
  secret (self-skips when absent). This is a **Phase 2 open item**, not a Phase 3
  blocker — Phase 3 adds no content-pipeline stages. See Phase 2 tasks
  "Post-review follow-up". T3.9 only requires `mypy --strict src` stay green **both**
  with and without the `job` extra (the dual-leg posture the review established).

## Tasks (flat — Phase 3 only, no sub-phases)

- [ ] **T3.1** — Sanity-check Phase 1 + Phase 2 seams (grep `StageSpec.backend`, `LoadedWorkflow.default_backend`, single `SubprocessStageRunner` for the `claude -p` path; confirm the `ShellStageRunner`/`shell=` wiring from the Phase 2 review is present and left intact; full-suite baseline = **193 passing**)
- [ ] **T3.2** — Author `src/atlas/cli_backend.py` — `CliBackend` Protocol + `ClaudeCodeBackend` + `AntigravityBackend` + `resolve_backend()` + `make_backend()` + `UnknownBackendError`
- [ ] **T3.3** — Unit-test `cli_backend.py` (~20 tests; argv parity, parse_result by returncode + JSON shape, preflight env-var paths, resolve priority table); ≥ 85% coverage
- [ ] **T3.4** — Refactor `SubprocessStageRunner` to delegate to a `CliBackend` strategy (new `default_backend` + `loaded_workflow` kwargs; argv / parse_result calls replace hardcoded block)
- [ ] **T3.5** — Extend `Config` with `default_backend: str = "claude"` field reading `.atlas.toml [backend] default`; thread it + `loaded` through `_make_pipeline()`
- [ ] **T3.6** — Integration tests — `agy` dispatch end-to-end (mocked subprocess), mixed-backend workflow, dev-pipeline-unaffected proof, `job.tailor_materials` now dispatches via `ClaudeCodeBackend`
- [ ] **T3.7** — Document per-CLI auth + `agy` experimental status (`docs/3_guides/cli_backends.md`)
- [ ] **T3.8** — Manual smoke test against a real `agy` binary (off-CI; document result whether it succeeds or auth blocks)
- [ ] **T3.9** — CI green: `ruff check` + `ruff format --check` + `mypy --strict src` (both with and without `--extra job`, per the Phase 2 review's dual-leg posture) + coverage gates (≥ 80% repo-wide, ≥ 85% on `cli_backend.py`). No new CI job needed; the `CONTENT_PIPELINE_TOKEN` secret is a Phase-2 open item, not a Phase-3 blocker.
- [ ] **T3.10** — Update `STATUS.md`; flag `v2.2` tag (user-discretionary)

## Exit criteria (TRD-v2 §14 Phase 3 + §13 #7–8, copied for tracking)

- [ ] **§13 #7** — At least one stage dispatches to `AntigravityBackend` and produces a valid `StageOutcome` (mocked in CI; real dispatch in manual testing if auth allows)
- [ ] **§13 #8** — Backend resolution: per-stage override > workflow default > config default > hard default, verified by test (`test_resolve_backend_priority_order`, T3.3)
- [ ] **§14 exit #1** — Existing dev pipeline runs unchanged (`ClaudeCodeBackend` is the default; `test_dev_pipeline_unaffected_by_phase_3` asserts byte-identical argv, negative-asserts no `--bare` or `--output-format`)
- [ ] **§14 exit #2** — A workflow YAML with `backend: agy` on one stage dispatches correctly (mocked) — `test_agy_dispatch_end_to_end_mocked` (T3.6)
- [ ] **§14 exit #3** — Backend resolution order verified by test
- [ ] **§14 exit #4** — `agy` auth failure produces a clear error, not a silent hang — `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` (T3.4); CLI surfaces a user-readable `agy_missing_auth_env` message
- [ ] **NFR-5 / TRD-v2 §10** — `cli_backend.py` ≥ 85% coverage; full suite ≥ 80% (existing gate)
- [ ] **NFR-7 / TRD-v2 §10** — `ruff check`, `ruff format --check`, `mypy --strict src` green
- [ ] **FR-8 (regression safety)** — All Phase 1 + Phase 2 tests pass unchanged (post-review baseline **193 passing**, commit `53359e4`); `test_e2e_happy_path.py` passes unmodified; the `SHELL:`/`LIB:` job paths remain untouched

## Resolved decisions (see plan §12 / `context.md` Decisions table for full rationale)

All seven items below were settled at TRS authoring time (2026-06-30). User-skipped
clarifying questions resolved as plan §12's recommended defaults; #7 was identified
during drafting. None remain open; if any are overridden, plan + context + this
tasks file all need updating.

- [x] **#1 — `parse_result()` output format** → plain-text for `ClaudeCodeBackend` (FR-8 dev-pipeline byte-identity); JSON for `AntigravityBackend` (robust failure classification needs the JSON `error` field). Binding on T3.2 / T3.3.
- [x] **#2 — Do NOT add `--bare` to Claude argv** → keep `--no-session-persistence` only. `--bare` would skip DEV-ESSENTIALS plugin discovery the dev pipeline relies on. TRD-v2 §3.4's `--bare` recommendation is a research-note suggestion, not a hard contract. Binding on T3.2.
- [x] **#3 — `.atlas.toml [backend] default = "claude"` schema** → single key. Per-backend model subtables (`[backend.agy] model = ...`) explicitly deferred. Existing top-level `model` field serves Claude; `AntigravityBackend` carries its own default. Binding on T3.5.
- [x] **#4 — No `atlas run --backend <name>` CLI flag** → stay with §3.4's 4-tier resolution exactly. Non-breaking to add later. Binding on T3.5.
- [x] **#5 — `AntigravityBackend` default model = `"gemini-flash-lite"`** → matches Claude's `haiku` cost posture; fits the documented `agy` free-tier (~20 req/day on flash-lite per `headless-clis-reference.md` Part C). Binding on T3.2.
- [x] **#6 — Single new file `src/atlas/cli_backend.py`** → Protocol + both backends + helpers in one focused module. Matches Phase 2's `composite_runner.py` precedent. Target ≤ 200 LoC (NFR-4). Binding on T3.2.
- [x] **#7 — `backend` field NOT validated at YAML load time** → validation lives in `make_backend()` / dispatch time only. Phase 1's loader stays decoupled from `cli_backend.py`. Matches the existing `RAW:` posture (loader validates structure, not tool content). Binding on T3.2 (no `workflow_loader.py` changes).

## Notes for implementation

- **No upstream blocker.** Phase 1 and Phase 2 are merged on `main`. T3.1 is a sanity grep + baseline run, not a hard gate.
- **Refactor, not rewrite.** `SubprocessStageRunner.run()`'s subprocess invocation pattern (`subprocess.run(argv, cwd=..., capture_output=True, ...)`), `TimeoutExpired` handling, and the `plugin_resolver.resolve()` + `build_prompt()` flow are all **unchanged** in Phase 3 — only the argv-list construction and post-subprocess result handling move into the `CliBackend` strategy. ~30 net lines change in `orchestrator.py`.
- **FR-8 byte-identity is the load-bearing claim.** The single most important test in this phase is `test_claude_code_backend_argv_byte_identical_to_phase2`: a golden-string comparison of `ClaudeCodeBackend.build_argv(...)` against the literal argv list Phase 2's hardcoded path produced. If that fails, the refactor has drifted from intent and dev pipeline regression risk goes up sharply — fix the backend, do not "fix" the test.
- **Auth preflight is a security boundary, not a UX nicety.** `AntigravityBackend.preflight()` MUST NOT call `subprocess.run()` if `GEMINI_API_KEY` / `GOOGLE_API_KEY` are absent. The test `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` mocks `subprocess.run` to raise `AssertionError` if invoked — proving the env-var check fires **before** any process spawn. This addresses TRD-v2 §4 Security's "do not silently fall back to browser auth" explicitly.
- **`agy` is experimental.** TRD-v2 §5 / `headless-clis-reference.md` Part C document the contested-headless-auth status (Antigravity issue #78). T3.7's user-facing doc must say so. T3.8's manual run is best-effort; CI is mocked-only (§13 #7 verbatim).

## Implementation notes (post-hoc — filled in after work is done)

_To be appended as Phase 3 lands. Track: final LoC counts for `cli_backend.py` and the
`orchestrator.py` delta; coverage percentages achieved; whether T3.8's live `agy`
run succeeded or auth blocked it; any of the seven Resolved Decisions that needed
overriding in practice; final test count delta from Phase 2's baseline._
