# Context — Loop Mode, Phase L0 TRS

Reference notes for anyone picking up this work cold.

## Status — no blocking dependency, but two confirmed facts on the ground

Shipped v2.2 (per `STATUS.md`, "v2.2 — Phase 3 (CLI backend dispatch) complete") is the
declared dependency. At TRS authoring time (2026-07-21), running the full suite confirmed:

```
1 failed, 238 passed in 4.90s
FAILED tests/integration/test_job_adapters_real_import.py::test_score_jobs_adapter_real_import_success
  AttributeError: module 'application.use_cases' has no attribute 'score_jobs'
```

This is the exact drift TRD-v3 §14 Phase L0 calls out ("fix or `xfail` the content-pipeline
drift integration test so a green suite means green") — it is not hypothetical, it is the
current state of `main` as of this TRS's authoring. The `score_jobs` use case appears to have
moved/renamed on the content-pipeline side since atlas's adapter code was written.

Also confirmed: `pyproject.toml` currently declares `version = "1.0.0"` while `STATUS.md`,
git commit messages, and every doc reference the shipped state as **v2.2**. This is the
"version reconciliation" bullet in TRD-v3 §14 — also not hypothetical.

Neither of these blocks L0 from starting; they **are** two of L0's tasks (T-L0.2, T-L0.3).

## Key files

### Source-of-truth docs (read first, in order)
- [`docs/2_architecture/TRD-v3.md`](../../../docs/2_architecture/TRD-v3.md) — the phase
  contract this TRS details. §3.6 (telemetry/permissions), §3.7 (Deliverer), §14 Phase L0,
  §13 #1/#2/#4 (exit criteria), Appendix A (seam inventory) are the load-bearing sections.
- [`docs/1_product_and_research/loop-mode-design.md`](../../../docs/1_product_and_research/loop-mode-design.md)
  §5 Phase L0 — the source design note TRD-v3 §14 was written from; near-identical bullet
  list, useful for cross-checking intent.
- [`docs/1_product_and_research/headless-clis-reference.md`](../../../docs/1_product_and_research/headless-clis-reference.md)
  Part B — the exact `claude -p --output-format json` result schema this TRS's
  `parse_result`/`parse_usage` design implements: `subtype`, `result`, `total_cost_usd`,
  `usage.{input_tokens,output_tokens}`. T-L0.8's live run should append real-world findings
  back into this same doc (per TRD-v3 §14's own instruction: "Capture findings into
  `headless-clis-reference.md`").
- [`docs/2_architecture/TRD-v2.md`](../../../docs/2_architecture/TRD-v2.md) §3.4 — the
  `CliBackend` Protocol contract this phase extends without breaking (3-method Protocol;
  `parse_usage` is additive, not a Protocol member).
- Phase 3 TRS triad ([plan](../../archive/yaml-workflow-engine-phase-3/yaml-workflow-engine-phase-3-plan.md),
  [context](../../archive/yaml-workflow-engine-phase-3/yaml-workflow-engine-phase-3-context.md),
  [tasks](../../archive/yaml-workflow-engine-phase-3/yaml-workflow-engine-phase-3-tasks.md)) —
  the most recent completed phase; this TRS follows its file-naming, task-numbering, and
  "Resolved Decisions" conventions directly. Also the origin of the byte-identity golden-string
  test pattern (`test_claude_code_backend_argv_byte_identical_to_phase2`) that L0's telemetry
  work must not break.

### TRS itself (this directory)
- [`loop-mode-phase-L0-plan.md`](./loop-mode-phase-L0-plan.md) — full design + flat task list
  (T-L0.1–T-L0.11) + Pending Decisions.
- [`loop-mode-phase-L0-tasks.md`](./loop-mode-phase-L0-tasks.md) — checkbox progress tracking.

### Code targets

**New:**
- `src/atlas/deliverer.py` — `Deliverer` Protocol + `GhPrDeliverer` + `PrRef` + `DeliveryError`.
- `tests/unit/test_deliverer.py` — unit tests for the above (~10-12 tests estimated, matching
  the density of `test_worktree.py`/`test_cli_backend.py`).

**Modified:**
- `src/atlas/cli_backend.py` — `ClaudeCodeBackend.build_argv` gains conditional
  `--output-format json` + permission-profile flags (gated on `extra_flags` keys, absent by
  default so attended argv is unchanged); `parse_result` gains a JSON-envelope branch
  alongside the existing plain-text branch; new `parse_usage()` method + `UsageStats`
  dataclass. **Protocol itself (`CliBackend`) is unchanged** — `parse_usage` is an
  additional method on the concrete class, not a Protocol requirement.
- `src/atlas/plumb_io.py` — `record_span()` gains an optional `tokens: tuple[int, int] |
  None = None` kwarg, threaded to plumb's **confirmed** `RunHandle.add_span(..., tokens=(in,
  out))` (`plumb/api.py:264`) when present. **Spike is RESOLVED (maintainer, 2026-07-21):**
  plumb sums the tuple into a single `spans.tokens` column (in/out split lost until plumb
  v1.1); **run-level `runs.dollar_cost`/`tokens_in`/`tokens_out` are NOT writable** from the
  online `with run()` path (`finalize_run` at `plumb/storage_sqlite.py:431` sets none;
  `RunHandle` has no cost setter). So L0 writes per-span tokens and **defers run-level
  `dollar_cost` to plumb P1-a** (`set_usage` + `finalize_run` threading), tracked in BACKLOG.md.
  ⚠ **Tokens and dollars are not symmetric:** tokens have a span-level sink; `total_cost_usd`
  has **none at any level** below the run (no per-span cost column in v1.0.1 *or* v1.1 —
  `spans.attributes` is JSON and P1-a's `set_usage` is deliberately run-level). Do not hunt
  for a per-span cost sink; there isn't one.
  Note the kwarg is `tokens=(in, out)` — a bare tuple matching plumb — **not** `usage=UsageStats`;
  the caller decomposes `UsageStats` into the tuple and carries `total_cost_usd` separately
  (in-memory only).
- `pyproject.toml` — version bump `1.0.0` → `2.2.0`.
- `tests/integration/test_job_adapters_real_import.py` — fix or `xfail`-mark the drift test.
- `tests/unit/test_cli_backend.py` — new test cases for the telemetry/permission argv paths.
- `tests/integration/test_cli_backend_dispatch.py` — new loop-mode-dispatch + attended-invariance
  tests.
- `docs/1_product_and_research/headless-clis-reference.md` — append live-run findings
  (T-L0.8).
- `docs/1_product_and_research/BACKLOG.md` — tracking entry if T-L0.3 resolves as `xfail`
  rather than a direct fix.
- `STATUS.md` — phase completion entry.

**Unchanged (verify, don't touch):**
- `src/atlas/orchestrator.py` (`Pipeline`) — per TRD-v3 Appendix A's explicit note: "if
  implementation finds `Pipeline` genuinely needs editing, that is a signal the design has
  drifted — pause and reconcile." `Deliverer` is called by whatever harness runs it (a manual
  script/test in L0; `loop.py` in L2), never by `Pipeline` itself.
- `src/atlas/worktree.py` — `WorktreeManager.cleanup()` already exists, already idempotent
  (verified by reading the source: no-op if `worktree_path` doesn't exist). L0 only adds a new
  *caller* (`GhPrDeliverer`); `merge_back()` stays as dead code for now (TRD-v3 §3.7 notes it
  is "replaced" by `Deliverer`, but removing it is not in this TRS's scope — no other code
  path currently calls it, and deleting unrelated code is out of scope for L0).
- `src/atlas/cli.py` — **no `atlas loop` command in L0.** `_make_pipeline()`/`run`/`resume`
  are untouched; loop-mode `extra_flags` are never set by any existing CLI path in this
  phase. (The CLI surface for loop mode is Phase L2, TRD-v3 §3.8.)
- `src/atlas/config.py` — no `[loop]` section in L0 (that's Phase L2, TRD-v3 §7). No config
  schema changes at all in this phase.
- `src/atlas/workflows/*.yaml` — no new workflow in L0 (`loop_dev.yaml` is Phase L1).
- `src/atlas/composite_runner.py`, `library_runner.py`, `shell_runner.py`,
  `library_adapters/*` (except the possible T-L0.3 fix), `plugin_resolver.py`,
  `post_commit_hook.py`, `state.py`, `workflow_loader.py`, `stages.py` — all untouched.
- `tests/e2e/test_e2e_happy_path.py`, `tests/integration/test_main_branch_isolation.py` — run
  unmodified (regression proof); `test_main_branch_isolation.py`'s assertion style is the
  template T-L0.9's manual `git log main` check mirrors.
- `tests/fixtures/routing_ground_truth.json` — unchanged; L0 introduces no new dev-pipeline
  stages.

If implementation finds any "unchanged" file genuinely needs editing, that's a signal the
design has drifted from this TRS — pause and reconcile before proceeding.

## Decisions made (during this TRS)

| # | Decision | Rationale |
| - | --- | --- |
| 1 | `parse_usage()` is a **new method on `ClaudeCodeBackend`, not a `CliBackend` Protocol member**. | Keeps the Protocol at 3 methods for every backend (`AntigravityBackend`, future `CodexBackend` in L1) rather than forcing every backend to implement token/cost extraction even if their CLI doesn't expose it the same way. `SubprocessStageRunner` duck-types (`hasattr`/optional-Protocol check) before calling it. See plan §"Dependencies & Interfaces". |
| 2 | JSON-vs-plain-text detection in `parse_result`/`parse_usage` is **by stdout content sniffing, not a mode flag threaded through the method signature**. | `parse_result`'s signature is fixed by the `CliBackend` Protocol (`stdout, stderr, returncode`) shared with `AntigravityBackend`; adding a mode parameter would be backend-specific Protocol pollution. `SubprocessStageRunner` already knows it requested `telemetry=json` via the argv it built — it just calls `parse_usage` unconditionally and gets `None` back for attended (plain-text) dispatches. See plan §"Algorithm & Logic Design". |
| 3 | `Deliverer.deliver()` takes **`title`/`body` strings**, not TRD-v3 §3.7's literal `issue`/`scores` objects. | L0 has no `issue` (GitHub Issues queue) or `scores` (loop run-scoring summary) types — those are Phase L2 concepts. The caller (a manual harness in L0; `loop.py` in L2) composes `title`/`body` from whatever context it has. Flagged as **Pending Decision #1** in the plan in case the maintainer wants the wider signature locked now instead. |
| 4 | `record_span()` gains `tokens=(in, out)` as an **optional kwarg on the existing method**, matching plumb's real `add_span(tokens=...)` signature — not a `usage=UsageStats` kwarg, and not a new sibling `record_usage()` call. | Plumb's confirmed API (`plumb/api.py:264`) takes a bare `(in, out)` tuple and has no per-span cost field. One call site to keep in sync. Run-level `dollar_cost` is unreachable in plumb v1.0.1's online path → deferred to plumb P1-a (BACKLOG.md). Spike **resolved** (was Pending Decision #2). |
| 5 | Push safety (`GhPrDeliverer` never touches `main`, never `--force`) is enforced **by construction (hardcoded argv shape) plus a defensive branch-name assertion**, not by a runtime config flag. | Matches TRD-v3 §4 Security's explicit ask: "asserted never to push `main` or force-push" (§13 #4) — a config flag could be misconfigured; a hardcoded argv shape + assertion cannot silently drift. Mirrors Phase 3's `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` pattern of asserting the dangerous call *never fires*, not just checking a return value. |
| 6 | On `git push`/`gh pr create` failure, the worktree is **not** cleaned up. | Preserves the unpushed/un-PR'd work for manual recovery — matches the general atlas posture of "fail visibly, don't destroy state" (e.g. `WorktreeManager.cleanup()` itself never touches `main` and is already designed to be safe to retry). A cleanup-after-failure would make a failed delivery unrecoverable without re-running the whole stage. |
| 7 | T-L0.3 (content-pipeline drift test) defaults to **investigate-and-fix-if-trivial, `xfail` as fallback** — not an automatic `xfail`. | TRD-v3 §14's own phrasing is "fix **or** xfail," listing fix first. Whether a fix is reachable from atlas's side alone (vs. requiring a content-pipeline-side change) is unknown until investigated — flagged as **Pending Decision #3**. |

None of these contradict TRD-v3; where its text was silent on an implementation-level detail
(exact `Deliverer` signature vs. the wider one shown in §3.7's illustrative Protocol, plumb's
literal API shape, Protocol-membership of `parse_usage`), this TRS resolved them with the
rationale above and surfaced each as a Pending Decision where a plausible alternative exists.

## Integration points

| Direction | Surface | Failure mode | Test coverage |
| --- | --- | --- | --- |
| `ClaudeCodeBackend.build_argv()` → `extra_flags` dict | Conditional flag appension keyed on dict keys (`telemetry`, `permission_mode`, `allowed_tools`, `max_turns`) | N/A — pure computation | Unit: byte-identity test (no keys) + one test per flag combination (T-L0.4) |
| `ClaudeCodeBackend.parse_result()` → JSON envelope | Content-sniffs stdout; malformed JSON caught, never raises | `claude_unparseable_json` / `claude_<subtype>` error types | Unit: full subtype table + malformed-JSON case (T-L0.4) |
| `ClaudeCodeBackend.parse_usage()` → JSON envelope | Same content-sniff; missing keys → `None` fields, not `KeyError` | Returns `None` (plain-text) or `UsageStats` (JSON, possibly with `None` fields) | Unit (T-L0.4) |
| `SubprocessStageRunner` → `backend.parse_usage()` | Duck-typed optional call (only when backend supports it and loop-mode was requested) | N/A — absent method or `None` return both handled as "no telemetry available" | Integration (T-L0.7) |
| `PlumbIO.record_span(tokens=(in,out))` → `RunHandle.add_span(tokens=...)` | Additive kwarg; `None` preserves exact current behavior. Plumb sums the tuple into `spans.tokens`. Run-level cost NOT written (unreachable, deferred to plumb P1-a) | Confirmed signature (`plumb/api.py:264`) — **spike resolved** | Unit (stub-mode buffer assertions + negative-assert no run-level write, T-L0.5) |
| `GhPrDeliverer.deliver()` → `git push` | Hardcoded argv shape (`-u origin <branch>`, no `--force`, no `main` literal) | `DeliveryError` on non-zero exit; worktree preserved | Unit — happy path + failure path + **load-bearing security test** (T-L0.6) |
| `GhPrDeliverer.deliver()` → `gh pr create` | List-form subprocess, no shell interpolation of `title`/`body` | `DeliveryError` on non-zero exit or `FileNotFoundError` (gh missing); worktree preserved | Unit (T-L0.6) |
| `GhPrDeliverer.deliver()` → `WorktreeManager.cleanup()` | Called only after successful push + PR create; failure swallowed | Logged, does not fail `deliver()` | Unit (T-L0.6) |

## Where this TRS's task list maps to TRD-v3 §14 Phase L0 scope bullets

| TRD-v3 §14 Phase L0 bullet | This TRS's task |
| --- | --- |
| "Version reconciliation: bump `pyproject.toml` → `2.2.0`, tag `v2.2`; fix or `xfail` the content-pipeline drift integration test" | T-L0.2 (version bump) + T-L0.3 (drift test) |
| "First live attended run... confirm subprocess spawn + gate prompts + a plumb run with spans. Capture findings into `headless-clis-reference.md`" | T-L0.8 |
| "`ClaudeCodeBackend` loop-mode telemetry: `--output-format json`; `parse_result` surfaces `total_cost_usd` + `usage`; thread into plumb... Guard behind a per-run flag" | T-L0.4 (backend) + T-L0.5 (plumb threading) |
| "Headless permission profile: `--permission-mode acceptEdits` + curated `--allowedTools`... + `--max-turns`. No `--dangerously-skip-permissions`" | T-L0.4 (same argv-construction task; permission flags share the same `extra_flags`-gated mechanism as telemetry) |
| "`Deliverer` / `GhPrDeliverer`: push branch + `gh pr create` + `WorktreeManager.cleanup()`; replaces the dead `merge_back()` path" | T-L0.6 |

The remaining tasks (T-L0.1 baseline sanity-check, T-L0.7 integration proof, T-L0.9 manual
delivery smoke, T-L0.10 lint/type/coverage, T-L0.11 STATUS.md update) are standard hygiene
tasks following the precedent set by Phase 3 (T3.1, T3.6, T3.8, T3.9, T3.10) — they don't map
to individual TRD-v3 bullets but round out a complete, verifiable phase.

## TRD-v3 §13 exit criteria → tests/tasks that prove them

| TRD-v3 §13 exit criterion | Proving task/test |
| --- | --- |
| #1 (**amended in TRD-v3 itself, 2026-07-21**) "A live `atlas run` on the `claude` backend produces a plumb run whose `code_gen` span carries real `tokens`." Run-level `dollar_cost` / token roll-up is **explicitly not an L0 gate** — deferred to plumb P1-a (`set_usage`), verified at L2. | T-L0.8 (the live run, incl. one loop-mode dispatch proving `spans.tokens`) + T-L0.4/T-L0.5 (the telemetry plumbing). No open question — the TRD carries this wording now. |
| #2 "Attended-mode invariance. Full v2 suite green; `atlas run` unchanged." | T-L0.7's `test_dev_pipeline_unaffected_by_phase_l0` + T-L0.10's full-suite gate |
| #4 "Delivery primitive. The `Deliverer` pushes a branch + opens a PR for a completed run and calls `cleanup()`; asserted never to push `main` or force-push." | T-L0.6 (unit, incl. the load-bearing security test) + T-L0.9 (manual real-world proof) |

(§13 #3 — `CodexBackend` dispatch — is **not** an L0 exit criterion; it belongs to Phase L1
per TRD-v3 §14's own phase split. Not tracked here.)

## What this TRS deliberately does NOT cover

- **`CodexBackend`, `loop_dev.yaml`.** Explicitly Phase L1 (TRD-v3 §14).
- **`loop.py`, `queue_gh.py`, any polling/ticking, `atlas loop` CLI commands, `[loop]` config.**
  Explicitly Phase L2 (TRD-v3 §14, §3.5, §3.8, §7).
- **Any actual GitHub Issues read/write.** `Deliverer` opens a PR; it never reads or writes an
  Issue — that's `queue_gh.py`'s job, which doesn't exist until L2. The `run_id`/`branch`
  passed to `deliver()` in L0 come from a manual test harness, not a queue.
- **Self-healing, judge scoring, diagnosis-injected retries.** Phase L3 (TRD-v3 §14).
- **`Pipeline`/gate/worktree-creation code changes.** TRD-v3 Appendix A is explicit:
  `Pipeline` is unchanged by loop mode across all phases; L0 preserves this by construction
  (`Deliverer` lives entirely outside `Pipeline.run_to_completion()`).
- **Automating `git tag v2.2`.** Resolved (2026-07-21): T-L0.2 bumps the version string;
  creating/pushing the tag stays a **manual** maintainer action, matching Phase 3's T3.10
  precedent ("tag `v2.2` (user-discretionary)"). A future BACKLOG item may reconsider
  CI-automated tagging.
- **Run-level `dollar_cost` / `tokens_in` / `tokens_out` plumb writes.** Confirmed unreachable
  in plumb v1.0.1's online path (`finalize_run` sets none; no `RunHandle` cost setter) —
  deferred to plumb P1-a (`set_usage` + `finalize_run` threading), tracked in BACKLOG.md. L0
  writes per-span `tokens` only.
- **A starter `.claude/settings.json` allowlist for atlas's own repo.** Deferred to L2 per
  Pending Decision #4 — the required tool set is easier to pin down once `loop_dev.yaml` (L1)
  and the actual loop prompt shape (L2) exist.
