# Context — Loop Mode, Phase L2 TRS

Reference notes for anyone picking up this work cold.

## Status — L0 + L1 code-complete, in code review; L2 has no blocking dependency

Per `STATUS.md` (2026-07-24): v2.2 shipped; Loop Mode Phase L0 ("honest baseline") and
Phase L1 (`CodexBackend` + `loop_dev.yaml`) are both **code-complete**: 301 tests pass,
1 xfail, 96% coverage. Both phases are currently **in code review**
(`dev/active/loop-mode-phase-L1/loop-mode-code-review.md`, verdict: **Approve**, one
Medium + four Low/Nit findings, none blocking). TRD-v3 §14 lists L2's dependency as
simply "L1." Per the precedent L0→L1 already set (L0's T-L0.8/T-L0.9 manual off-CI
checks didn't block L1's engineering work), **this TRS does not block L2 on L1's own
open manual checks** (T-L1.1 write-heavy Codex capture, T-L1.8 both-engines smoke) —
see [Decision #1](./loop-mode-phase-L2-decisions.md) for the one place that posture has
a real consequence (Codex-lane token-cost trust).

**What's different about L2 vs. L0/L1:** L0 built primitives, L1 built a second engine
+ a loop-shaped workflow — **neither had a caller**. L2 is the first phase that
actually *drives* them. The L1 code review says this explicitly: *"the thing to watch
is L2: these primitives have been unit-tested but never integration-proven through the
runner."* Read that review in full before starting — it's short, and its "what's
notably good" section names the patterns (byte-identity invariants, fail-closed
preflight, security tests that assert the dangerous call never fires) this TRS expects
L2's own code to keep following.

## Key files

### Source-of-truth docs (read first, in order)

- [`docs/2_architecture/TRD-v3.md`](../../../docs/2_architecture/TRD-v3.md) — the
  phase contract this TRS details. §3.1 (`queue_gh.py` adapter surface + label
  protocol), §3.2 (two-lane routing), §3.5 (the loop driver sketch), §3.6 (headless
  telemetry — L2 is where `runs.dollar_cost` still being unwritable pre-plumb-P1-a
  actually bites, since budgets need it), §3.7 (`Deliverer`), §3.8 (CLI surface), §7
  (`[loop]` config schema — copied verbatim into this TRS), §14 Phase L2 (engineering
  scope + exit criteria), §13 items 5–8 (the exit criteria), Appendix A (seam
  inventory).
- [`docs/1_product_and_research/loop-mode-design.md`](../../../docs/1_product_and_research/loop-mode-design.md) —
  source design note; cross-check for intent if TRD-v3 phrasing is ambiguous.
- [`dev/active/loop-mode-phase-L1/loop-mode-code-review.md`](../loop-mode-phase-L1/loop-mode-code-review.md) —
  **read this in full.** The Medium finding (M1, Codex token cache-semantics) and the
  four Low/Nit findings (worktree-vs-sandbox cwd, `PrRef.number` sentinel, branch-safety
  exact-match, `total_cost_usd` always-`None` field) all inform this TRS's Pending
  Decisions #1 and #3. L2 closes exactly one of them (`PrRef.number`, T-L2.12) and
  explicitly does not silently absorb or silently ignore the rest.
- Phase L0 and L1 TRS triads
  ([L0 plan](../loop-mode-phase-L0/loop-mode-phase-L0-plan.md) /
  [L0 context](../loop-mode-phase-L0/loop-mode-phase-L0-context.md) /
  [L0 tasks](../loop-mode-phase-L0/loop-mode-phase-L0-tasks.md),
  [L1 plan](../loop-mode-phase-L1/loop-mode-phase-L1-plan.md) /
  [L1 context](../loop-mode-phase-L1/loop-mode-phase-L1-context.md) /
  [L1 tasks](../loop-mode-phase-L1/loop-mode-phase-L1-tasks.md)) — this TRS follows
  their task-numbering (`T-L2.N`), Resolved-Decisions-as-a-separate-artifact
  convention (here, split further into a dedicated decisions file because L2's list
  ran to 18 items), and "what this TRS deliberately does NOT cover" section directly.

### TRS itself (this directory)

- [`loop-mode-phase-L2-plan.md`](./loop-mode-phase-L2-plan.md) — design (Phase
  Summary through Performance Considerations), Phase Deliverables, a short pointer to
  Pending Decisions.
- [`loop-mode-phase-L2-decisions.md`](./loop-mode-phase-L2-decisions.md) — **all 18**
  Pending Decisions & Clarifications with full rationale, split into its own file to
  keep the plan under the repo's 800-line file cap. Normative, not optional reading.
- [`loop-mode-phase-L2-tasks-detail.md`](./loop-mode-phase-L2-tasks-detail.md) — the
  full flat task list (T-L2.1–T-L2.14) with acceptance criteria, files, dependencies,
  testing requirements. Also split out for the same file-size reason.
- [`loop-mode-phase-L2-tasks.md`](./loop-mode-phase-L2-tasks.md) — checkbox progress
  tracking.

### Code targets

**New:**
- `src/atlas/queue_gh.py` — the `gh` CLI adapter (T-L2.2). The sole point of contact
  with `gh` from atlas (grep-enforced, T-L2.2's acceptance criteria).
- `src/atlas/loop.py` — `tick()`/`run_forever()`/`reconcile_orphans()`, `LoopState`,
  budgets, circuit breaker (T-L2.5–T-L2.8). The core deliverable.
- `src/atlas/triage.py` — label-wins-else-classify router (T-L2.4).
- `tests/fixtures/gh_json/*.json` — captured `gh --json` output shapes (T-L2.1).
- `tests/unit/test_queue_gh.py`, `test_loop.py`, `test_triage.py` — new test files.
- `tests/integration/test_loop_e2e.py` — new integration test file.

**Modified:**
- `src/atlas/config.py` — `LoopConfig` dataclass, `Config.loop` field, `[loop]` TOML
  parsing (T-L2.3).
- `src/atlas/cli.py` — `_make_pipeline` promoted to a shared `make_pipeline()` with a
  `backend_override` param (Decision #11, T-L2.5); new `atlas loop` Typer sub-app
  (T-L2.9).
- `src/atlas/deliverer.py` — possible `PrRef.number` fix, depending on which of the
  L1 review's two recommended options is chosen (T-L2.12).
- `docs/1_product_and_research/BACKLOG.md` — close the `PrRef.number` action item;
  confirm the rest of the L1 review's open items remain tracked.
- `tests/unit/test_config.py` — `[loop]` section parsing tests.
- `STATUS.md` — phase completion entry (T-L2.14).

**Unchanged (verify, don't touch):**
- `src/atlas/orchestrator.py` (`Pipeline`, `RunResult`) — L1 shipped `RunResult`; L2
  is its first production consumer, but consumes it exactly as designed, no further
  widening needed.
- `src/atlas/cli_backend.py` (`CliBackend`, `ClaudeCodeBackend`, `CodexBackend`) — L2
  only *selects* an engine via the `engine:*` label at `Pipeline`/`SubprocessStageRunner`
  construction time; no change to argv-building or parsing logic in either backend.
- `src/atlas/workflows/loop_dev.yaml` — L1 shipped this; L2 is its first automated
  (non-manual-smoke) caller.
- `src/atlas/worktree.py` — `WorktreeManager.cleanup()` reused as-is by both
  `Deliverer` (per-run) and `reconcile_orphans()` (startup sweep) — two call sites,
  same method, no method-signature change.
- `src/atlas/plumb_io.py` (`PlumbIO`) — per Decision #8, `reopen_run()` already
  covers the "re-attach to a run from a prior tick/process and write a score" need;
  no new `PlumbIO` method required.
- `src/atlas/state.py` (`StateStore`) — `LoopState` is deliberately a *separate*
  persistence mechanism (`.atlas/loop-state.json`, Decision #6), not folded into
  `StateStore`'s per-run `tasks.md`/`.atlas/current-run` conventions.

If implementation finds any "unchanged" file genuinely needs editing beyond what's
listed here, that's a signal the design has drifted from this TRS — pause and
reconcile before proceeding.

## Decisions made (during this TRS's authoring)

Full text in [`loop-mode-phase-L2-decisions.md`](./loop-mode-phase-L2-decisions.md).
One-line index:

| # | Decision | One-line why |
| - | --- | --- |
| 1 | L2 doesn't block on L1's T-L1.1/T-L1.8, but don't trust Codex-lane token data until T-L1.1 closes | Matches L0→L1 precedent; the risk is data-trust, not code-readiness |
| 2 | Planned lane is first-pass-only in L2 (plan-only PR + stop) | Matches TRD-v3 §13 #6's literal exit bar; the task-by-task loop needs undesigned machinery |
| 3 | L2 closes only the `PrRef.number` L1-review finding, not all four remaining | Only that one is newly, directly depended on by L2 code |
| 4 | `triage.py` is a separate file, not inlined into `loop.py` | Testability + keeps `loop.py` from growing past readable |
| 5 | `claim()` is one combined `gh issue edit` call | Reduces the crash window between label-swap and assignment |
| 6 | `LoopState` is a new flat JSON file, not folded into `StateStore` | Per-loop-process state is categorically different from per-run state |
| 7 | No `queue_gh.preflight()` — rely on first-call failure + recoverable-tick handling | Simpler; arguably better UX than a startup fail-closed |
| 8 | `sync_prior_prs()` reuses `PlumbIO.reopen_run()` for post-hoc scoring; no new `PlumbIO` method | Structurally identical to how `Pipeline.resume()` already reattaches |
| 9 | Multi-repo tie-breaking: first-match in `cfg.repos` order, then `gh`'s own oldest-first order | Inert until L4 (v3's `repos` list is length 1); named for L4's TRS author |
| 10 | `build_issue_prompt()` = title + body + a short scope preamble; no `context_hint` lookup | No such mechanism exists in the codebase yet; TRD's phrase is under-specified |
| 11 | `cli.py::_make_pipeline` promoted to a shared `make_pipeline()` | Avoids `loop.py` duplicating and silently drifting from `cli.py::run`'s construction |
| 12 | `relabel(state="done")` bundles the `gh issue close` call | A split would leave an unreachable-by-`reconcile_orphans` half-done state on crash |
| 13 | Triage classifier dispatches via `CliBackend` directly, bypassing `SubprocessStageRunner` | Matches "not an agentic run" literally; avoids a throwaway single-stage workflow |
| 14 | `tick()` posts a comment on failure too, not just success | Without it, a failed run is invisible to the operator beyond a stale label |
| 15 | "`loop.py` never shells `gh` directly" is enforced by a grep-based test | Converts an architectural guarantee into something CI actually checks |
| 16 | Untrusted-author issues are skipped (left `atlas:ready`), not relabeled to an error state | Avoids looking like a self-healing exhaustion state that doesn't exist until L3 |
| 17 | Triage classifier cost counts toward `max_dollars_per_day`, not `max_runs_per_day` | Keeps the dollar cap honest about all LLM spend; the run cap is about code-changing runs |
| 18 | `run_forever()` wraps `tick()` in a bare `except Exception` safety net | An unattended daemon should never die from one tick's unhandled bug |

## Verified plumb/config surface used by this TRS (read 2026-07-24)

Confirmed by reading the actual source (not re-deriving from the TRD sketch alone):

- **`PlumbIO`** (`src/atlas/plumb_io.py`) already exposes everything L2's sync path
  needs: `open_run`, `close_run`, `reopen_run` (child-run handoff via `parent_run_id`,
  currently used by `Pipeline.resume()`), `record_span`, `record_user_signal`,
  `write_example`. No new method required — see Decision #8.
- **`Pipeline.__init__`** (`src/atlas/orchestrator.py:146`) takes `repo_root`,
  `state`, `plumb`, `runner`, `prompter`, `stages`, `workflow_name`, `worktree`,
  `commit_wait_timeout_s` — all keyword-only. `loop.py`'s `make_pipeline()` must
  supply the same shape `cli.py::_make_pipeline` does today (confirmed by reading
  `cli.py:85-129`).
- **`RunResult`** (`orchestrator.py:114`) — `ctx: RunContext`, `status: str` (`"success"
  | "failure" | "paused"`). `Pipeline.run_to_completion()` returns this (L1's
  widening). This is what `run_one_shot()` gates delivery on.
- **`Deliverer`/`GhPrDeliverer`** (`src/atlas/deliverer.py`) — `deliver(run_id, branch,
  worktree_path, title, body) -> PrRef`. Refuses `branch == "main"` by raising
  `DeliveryError`. `PrRef(number: int, url: str)` — `number` is `0` on a malformed
  `gh pr create` URL parse (`_parse_pr_url`, line 110-114) — this is the finding
  T-L2.12 closes.
- **`Config`** (`src/atlas/config.py`) — frozen dataclass, `Config.load(repo_root)`
  merges `~/.atlas/config.toml` then `<repo_root>/.atlas.toml` via `_deep_merge`. No
  `[loop]` section exists yet; T-L2.3 adds it following the exact pattern the existing
  `[backend]` section (→ `default_backend`) already uses.
- **`cli.py::_make_pipeline`** (`cli.py:85-129`) — private, constructs
  `SubprocessStageRunner` + `CompositeStageRunner` (+ optional `LibraryStageRunner`/
  `ShellStageRunner`) + `Pipeline`, reading `cfg.default_backend` for engine selection
  with no override parameter today. T-L2.5 promotes this to a shared, importable
  `make_pipeline()` with a `backend_override: str | None = None` param (Decision #11).

## Integration points

| Direction | Surface | Failure mode | Test coverage |
| --- | --- | --- | --- |
| `queue_gh.list_ready/claim/comment/sync/relabel()` → `gh` subprocess | List-form argv, timeout-wrapped | `GhCliError` (non-zero exit, timeout, malformed JSON) | Unit (T-L2.2) |
| `loop.tick()` → `queue_gh` | Typed calls only, never raw `gh` output | Caught `GhCliError` → recoverable `TickResult` | Unit + Integration (T-L2.5, T-L2.10) |
| `loop.run_one_shot()` → `make_pipeline()`/`Pipeline.run_to_completion()` | Mirrors `cli.py::run` exactly | `RunResult.status != "success"` → `AbortedError`, no delivery | Unit (T-L2.5), Integration (T-L2.10) |
| `loop.run_one_shot()` → `Deliverer.deliver()` | Post-success side effect only | `DeliveryError` caught at tick level | Unit (T-L2.5), Integration (T-L2.10) |
| `loop.sync_prior_prs()` → `PlumbIO.reopen_run()`/`record_user_signal()`/`close_run()` | Re-attach-and-score pattern (Decision #8) | Best-effort; a plumb write failure is logged, does not block relabeling | Unit + Integration (T-L2.7) |
| `loop.reconcile_orphans()` → `queue_gh.sync()` + `WorktreeManager.cleanup()` | Startup-only sweep | Cleanup failure logged, not raised | Unit + Integration (T-L2.8, `test_crash_recovery_full_cycle`) |
| `triage.classify()` → `CliBackend.build_argv/parse_result` (direct, not via `SubprocessStageRunner`) | Single haiku call | Unparseable output → `planned` default | Unit (T-L2.4) |
| `cli.loop_app` → `tmux` subprocess | `start`/`stop`/`attach` only | Missing `tmux` → clear error, `run` unaffected | Unit (T-L2.9) |

## Where this TRS's task list maps to TRD-v3 §14 Phase L2 scope bullets

| TRD-v3 §14 Phase L2 bullet | This TRS's task |
| --- | --- |
| "`queue_gh.py`... the `gh` adapter (list/claim/deliver_pr/comment/sync/relabel)" | T-L2.1 (fixture capture) + T-L2.2 |
| "`loop.py`... `tick()`... `run_forever()`... `reconcile_orphans()`. One issue per tick; sequential" | T-L2.5, T-L2.6, T-L2.7, T-L2.8 |
| "Triage router... `wf:*` label wins, else haiku classify" | T-L2.4 |
| "`[loop]` config... extend the frozen `Config`" | T-L2.3 |
| "CLI... `atlas loop run\|start\|stop\|status\|attach` (tmux wrapper for start/stop/attach)" | T-L2.9 |
| "Budgets + circuit breaker" | T-L2.6 |
| "Tests: faked `gh`/`subprocess`/`time`; the full state machine + budget/breaker + orphan reconciliation" | T-L2.10 (integration) + each unit-test table across T-L2.2–T-L2.8 |

T-L2.12 (`PrRef.number` fix + `trusted_authors` checkpoint) does not map to an explicit
TRD-v3 §14 L2 bullet — it's this TRS's own follow-through on the L1 code review
(Decision #3). T-L2.11 (lint/type/coverage) and T-L2.14 (STATUS.md) are standard
hygiene tasks following L0/L1's own T-L0.10/T-L0.11 and T-L1.9/T-L1.10 precedent.

## TRD-v3 §13 exit criterion → tests/tasks that prove it

| TRD-v3 §13 exit criterion | Proving task/test |
| --- | --- |
| Item 5: "Zero-touch delivery (headline)... zero keystrokes between labeling and reviewing... requires plumb P1-a for the cost half" | T-L2.10 (`test_zero_touch_smoke_faked`, CI-safe) + T-L2.13 (real, off-CI) — cost half explicitly reports tokens not dollars per §3.6, unchanged by L2 |
| Item 6: "Two-lane routing works" | T-L2.4 (triage unit tests) + T-L2.10 (`test_one_shot_lane_end_to_end_faked`, `test_planned_lane_stops_after_plan_pr`) + T-L2.13 (real planned-lane smoke) |
| Item 7: "Budgets & breaker" | T-L2.6 (full unit table) |
| Item 8: "Crash recovery" | T-L2.8 + T-L2.10 (`test_crash_recovery_full_cycle`) + T-L2.13 (real crash drill) |

## What this TRS deliberately does NOT cover

See the plan's own "What this TRS deliberately does NOT cover" section for the full
list (self-healing/judge gate/router v1 — L3; second repo/concurrency/weekly report —
L4; a per-model price table — never in v3; the planned lane's task-by-task loop — see
Decision #2; three of the four remaining L1 code-review findings — see Decision #3;
`queue_gh.preflight()` — see Decision #7; any change to workflow YAML files, the
workflow loader, or backend argv/parse logic; any new plumb schema/table/method).

## T-L2.1 findings (2026-07-25)

- Suite re-confirmed: 301 passed, 1 xfailed, at TRS-authoring-consistent state (no
  coverage regression check run yet — deferred to T-L2.11).
- `gh --version`: **2.96.0** (`gh version 2.96.0 (2026-07-02)`), authenticated as
  `anant-gupta-utexas` via keyring token (scopes: gist, read:org, repo, workflow).
- Real fixture capture against `anant-gupta-utexas/atlas` itself (scratch issue #4,
  scratch PR #5 — both created, captured, then closed/deleted/branch-removed):
  - `gh issue list --json number,title,body,labels` → `tests/fixtures/gh_json/issue_list.json`
    (real shape: `id`/`description`/`color` present on each label object — `Issue`
    dataclass parsing must only read `name` off each label, ignoring the rest).
  - Empty case (`--label` with no matches) → `[]`, captured as `issue_list_empty.json`.
  - `gh pr view --json state,mergedAt,number,url` captured for all three outcomes:
    `MERGED` (`mergedAt` populated, real PR #3), `OPEN` (`mergedAt: null`, scratch PR #5
    pre-close), `CLOSED` unmerged (`mergedAt: null`, same PR #5 post-`gh pr close`).
  - `gh issue edit --remove-label --add-label --add-assignee` (combined call, Decision
    #5) verified against the scratch issue: one call successfully swapped labels AND
    assigned in a single invocation — confirms Decision #5's assumption holds against
    the real CLI, not just the docs.
- Label set created on the real repo for ongoing loop use (not scratch — kept):
  `atlas:ready`, `atlas:working`, `atlas:done`, `atlas:rejected`, `atlas:blocked`,
  `wf:quick`, `wf:planned`, `engine:codex`, `engine:claude` — per TRD-v3 §3.1's label
  table. These did not exist before this task; T-L2.13's real smoke test depends on
  them existing.
- Schema-drift risk note: `gh issue list --json ...labels` occasionally lagged by ~1-2s
  after a label mutation before a subsequent `--label`-filtered list call reflected it
  (search-index propagation delay, not a `gh` bug) — observed once during capture.
  Not a correctness issue for the loop (poll-interval-based, not immediate-consistency
  dependent) but worth knowing if a fast unit/integration test ever hits real `gh`
  instead of a fixture (it won't, per the mocking strategy — noted for completeness).

## Open threads carried from L1

- **L1's T-L1.1 (write-heavy Codex capture) and T-L1.8 (both-engines manual smoke)**
  remain open. Per Decision #1, L2 proceeds without blocking on them, but Codex-lane
  `loop_dev` dispatch in L2 inherits the ~4×-token-miscount risk M1 flagged until
  T-L1.1 closes. Whoever picks up T-L2.13 (real smoke tests) should check whether
  T-L1.1 has closed in the meantime and fold in a cache-semantics sanity check if not.
- **The L1 code review's cwd-vs-`--sandbox` ambiguity (L1 finding L1)** — whether
  Codex's `-C <worktree>` genuinely wins over `SubprocessStageRunner`'s
  `cwd=atlas_root`. Still needs T-L1.8's manual smoke check specifically. Not touched
  by L2; flagged again here so it isn't lost between phases.
- **The branch-safety exact-match nit (L1 finding L3, `deliverer.py:62`)** —
  `if branch == "main"` doesn't cover `master`/`refs/heads/main`/other default
  branches. Not touched by L2 (Decision #3); still worth a BACKLOG line for whichever
  phase picks it up.
