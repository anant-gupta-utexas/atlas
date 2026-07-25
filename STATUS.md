---
project: atlas
status: v2.2 shipped; loop mode Phase L2 (loop daemon) — code complete, manual off-CI verification pending (unblocked 2026-07-25)
last_updated: 2026-07-25
next_gate: T-L2.13 (Phase L2 manual smoke) — unblocked, needs a human operator session; also still open — T-L0.8/T-L0.9 (Phase L0) + T-L1.1/T-L1.8 (Phase L1)
blocked_on: null
---

# atlas — status

## Current

**v2.2 is complete.** `pyproject.toml` reads `2.2.0`; `git tag v2.2` remains
a manual maintainer action (tracked in BACKLOG.md).

**Loop Mode Phase L2 (the loop daemon) is code-complete and code-reviewed: 424
tests pass, 1 xfail, 95.02% repo-wide coverage** (every individual module meets
its own T-L2.11 target — `queue_gh.py` 92%, `triage.py` 95%, `config.py` 100% —
the repo-wide figure sits below L1's 96% only because L2 added ~500 new
statements below the pre-L2 average; well above the CI floor of 80%). Completes
`v3.1` (the loop daemon itself).

The Phase L2 code review
([`loop-mode-phase-L2-code-review.md`](dev/active/loop-mode-phase-L2/loop-mode-phase-L2-code-review.md),
verdict **Approve with changes**) found 2 Critical, 4 Important and 5 Minor
issues; **all were fixed** in the same pass, along with both of its
architecture recommendations. The Criticals are worth knowing about:

- **The planned lane could not open a PR under any input.** `dev-docs-be` ran
  against `repo_root` with the worktree created *afterwards* and nothing ever
  committed, so delivery pushed a branch identical to `main`. Fixed by
  mirroring the quick lane's ordering (worktree first, agent runs inside it,
  triad committed, then deliver). Confirmed real: a pre-existing integration
  test flipped red once the ordering was fixed, because its fake agent wrote
  nothing to disk and nothing had checked for commits.
- **`sync_prior_prs()` wrote `user_signal` scores with `span_id=""`** — a
  dangling foreign key on the headline signal of the whole phase (§13 #5).
  Now anchored to a real `record_span` id. Its dedupe list was also unbounded
  in a file rewritten every tick; now capped at 500 entries.

Shipped this phase:

- `src/atlas/queue_gh.py` — the sole `gh` CLI adapter (`list_ready`/`claim`/
  `deliver_pr`/`comment`/`sync`/`relabel`/`current_user`/`find_run_id_comment`),
  list-form argv only, timeout-wrapped, grep-enforced as the only caller of
  `gh` in the loop path (`loop.py` never shells `gh` directly).
- `src/atlas/triage.py` — label-wins-else-classify two-lane router
  (`wf:quick`/`wf:planned`); both-labels-present resolves to `planned`;
  an unparseable classifier response also defaults to `planned`.
- `src/atlas/loop.py` (600 lines) — `tick()` (the core state machine: sync →
  breaker → budget → pull → trust-check → triage → claim → dispatch →
  comment → persist), `run_one_shot()`/`run_planned_first_pass()` (quick and
  planned-lane dispatch), `sync_prior_prs()` (idempotent PR-outcome
  scoring via `PlumbIO.reopen_run()`), `run_forever()` + `reconcile_orphans()`
  (crash recovery — an orphaned `atlas:working` issue is relabeled back to
  `atlas:ready` and its worktree pruned on the next startup, keyed on
  `.atlas/current-run`'s exact path so a live run is never swept).
- `src/atlas/loop_budget.py` — `LoopState` (a new flat `.atlas/loop-state.json`
  file), budgets and the circuit breaker. Split out of `loop.py` post-review so
  the driver stays readable as L3 adds self-healing; `loop.py` re-exports the
  public names, so `from atlas.loop import LoopState` still works.
- `src/atlas/pipeline_factory.py` — `make_pipeline()` (was `cli.py::_make_pipeline`;
  adds `backend_override` and `max_turns`) plus `LastOutcomeRunner`, so
  `cli.py::run`/`resume` and `loop.py`'s quick-lane dispatch share one
  construction path instead of two that could drift. Lives outside `cli.py` so
  `loop.py` no longer imports the CLI entry point — that cycle previously forced
  `cli.py`'s loop commands to import `loop` lazily inside function bodies.
- `atlas loop run|start|stop|status|attach` — `run` calls `run_forever()` in
  the foreground (no tmux dependency); `start`/`stop`/`attach` are thin tmux
  wrappers (`tmux new -d -s atlas-loop 'atlas loop run'` /
  `kill-session` / `attach`, the last via `os.execvp`); `status` reads
  `.atlas/loop-state.json` and reports budgets used, last tick, breaker state.
- `[loop]` config: `LoopConfig` + `Config.loop` + TOML `[loop]` parsing,
  exactly TRD-v3 §7's schema; `concurrency != 1` raises (frozen at 1 until
  Phase L4).
- **L1 code review finding closed**: `deliverer.py::_parse_pr_url` now
  raises `DeliveryError` on a malformed `gh pr create` URL instead of a
  `PrRef(number=0, ...)` sentinel — L2 is `PrRef`'s first real consumer, so
  this is where the L1 review's flagged gap actually mattered.
- **T-L2.13's blocker found and fixed (2026-07-25)**:
  `plugin_resolver.resolve()` did not special-case `RAW:`-prefixed tool
  strings despite its own docstring's claim that it did — a literal dict
  lookup, so `loop_dev.yaml`'s stages raised `RoutingDriftError` under a real
  `atlas loop run` unless `.atlas.toml` carried a `[plugin_commands]`
  override for each. `resolve()` now returns `RAW:` strings verbatim (they
  are literal prompts from the workflow YAML, not plugin names, so there is
  no third-party command to allow-list; an explicit override still wins).
  The separate `verify` stage carried a literal `"/verify"` tool string that
  `build_prompt` would have rendered as `//verify`; it is now the bare
  `verify`, mapped in `PLUGIN_COMMANDS` to `DEV-ESSENTIALS:verify` like the
  other dev-pipeline slash commands. The allow-list still rejects unknown
  non-`RAW:` tool strings before any subprocess spawns. `loop_dev` now
  dispatches with **no `.atlas.toml` workaround**, and
  `test_loop_e2e.py`'s override was removed so its tests exercise the real
  resolution path rather than passing regardless of it.
- **Known limitation carried forward, not a regression**: cost extraction
  (`extract_cost`) is unimplemented, so `run_one_shot()`'s `cost` is always
  `0.0` — `max_dollars_per_day` is mechanically wired and tested (the
  breaker/cooldown/runs-cap logic is correct) but never actually accumulates
  from real runs yet; only `max_runs_per_day` has teeth today. Post-review this
  is now **surfaced at runtime instead of only in docs**: `atlas loop status`
  prints "Dollars today: not tracked (cap $N NOT enforced — pending plumb
  P1-a)" rather than a confident `$0.00 / $N`, and `run_forever()` logs a
  startup WARNING when an operator has set a non-default cap. A spend control
  that silently does nothing is worse than no control at all.
- `cfg.loop.max_turns` was parsed and documented but never reached a backend
  (a runaway-cost guard that did nothing); it is now threaded through
  `make_pipeline(max_turns=...)` into both lanes. `atlas run` still leaves it
  unset — a human is watching there.

**Not yet done (off-CI, manual, real external systems):** T-L2.13 (zero-touch
delivery, planned-lane, and crash-recovery smoke tests against the real
GitHub repo) — no longer blocked (see the `plugin_resolver` fix above); it
now needs only a human operator session, since it drives real GitHub and
spends real tokens. Everything else in the Phase L2 TRS
([`dev/active/loop-mode-phase-L2/`](dev/active/loop-mode-phase-L2/)) is done.

---

**Loop Mode Phase L1 (`CodexBackend` + `loop_dev.yaml`) is code-complete: 301
tests pass, 1 xfail, at 96% coverage.** Completes `v3.0` (measured baseline +
engines + delivery) alongside L0. Shipped this phase:

- `CodexBackend` in `cli_backend.py` — a second `CliBackend` implementation
  dispatching `codex exec --json -C <dir> --sandbox workspace-write`.
  Schema **verified** against real `codex-cli 0.144.4` output (not designed
  against the TRD's assumed shape, which turned out wrong in four material
  ways — no `result` event, no status field, no cost field, output text on a
  separate `item.completed` event). Status is exit-code-only; `preflight()`
  fails closed on missing `OPENAI_API_KEY`/`$CODEX_HOME/auth.json` with no
  subprocess spawned (mirrors `AntigravityBackend`'s L0/Phase-3 pattern).
  Registered in `_KNOWN_BACKENDS`/`make_backend()` (`frozenset({"claude",
  "agy", "codex"})`).
- `CodexUsageStats` — a distinct 5-field dataclass (`total_cost_usd` always
  `None`; Codex reports tokens only). `codex_usage_to_tokens()` documents the
  one open question from this phase: whether `cached_input_tokens` is an
  addend or a subset of `input_tokens` (assumed addend, matching Anthropic's
  own convention — unconfirmed by execution, logged at debug level on every
  real run so it's diagnosable).
- `src/atlas/workflows/loop_dev.yaml` — new ungated 3-stage workflow
  (`plan → code_gen[isolate] → verify`), `default_backend: claude`. Quality
  enforcement is entirely `StageOutcome`/`RunResult.status`-driven (no gate,
  no new "guardrail" type).
- `Pipeline.run_to_completion()` return type widens `RunContext` → new
  `RunResult(ctx, status)` — the one deliberate, TRD-sanctioned exception to
  Appendix A's "`orchestrator.py` unchanged" row (both `cli.py` call sites
  already discarded the return value, so this is additive not breaking).
  Both `cli.py::run`/`resume` call sites unaffected (bare-statement calls).
- **Found and fixed in-scope**: `Pipeline.step()`'s ungated-stage branch
  unconditionally indexed `self._stages[stage.index + 1]`, which crashes
  `IndexError` when the **last** stage in a workflow has no gate.
  `dev.yaml`/`job.yaml` always gate their final stage, so this path was
  never exercised until `loop_dev.yaml` (whose last stage, `verify`, is
  ungated) — now guarded the same way the gated branch already was.
- New "Part E — Codex CLI headless reference" + "Part F" 3-way comparison
  table in `headless-clis-reference.md`, with every unconfirmed schema claim
  explicitly flagged (write-path event types, failure-path event existence,
  `--add-dir` writability under `--sandbox workspace-write`, and the
  cached-token addend/subset question all remain open pending a write-heavy
  live capture — T-L1.1).

**Not yet done (off-CI, manual, real external systems):** T-L1.1 (a
write-heavy Codex capture — file edits, a deliberately-failed run, a
cold/warm-cache pair to settle the cached-token question) and T-L1.8
(`atlas run --workflow loop_dev` against both `--backend claude` and
`--backend codex`, the latter contingent on Codex auth being available).
Everything else in the Phase L1 TRS
([`dev/active/loop-mode-phase-L1/`](dev/active/loop-mode-phase-L1/)) is done.

---

**Loop Mode Phase L0 ("honest baseline") is code-complete: 271 tests pass, 1
xfail, at 96% coverage.** Adds the telemetry, permission, and delivery
primitives the v3 loop depends on — no loop or `atlas loop` command yet
(that's L1/L2). Shipped this phase:

- `ClaudeCodeBackend` gains an opt-in JSON telemetry path (`--output-format
  json` + a headless permission profile), gated on `extra_flags` and absent
  by default — attended `atlas run` argv is byte-identical to pre-L0.
  `parse_result` handles the JSON envelope; a new `parse_usage()` method +
  `UsageStats` dataclass extract `total_cost_usd`/token counts.
- `PlumbIO.record_span()` gains an optional `tokens: tuple[int, int] | None`
  kwarg threaded to plumb's `RunHandle.add_span(tokens=(in, out))`. Run-level
  `dollar_cost`/token roll-up is **not** written — confirmed unreachable from
  plumb v1.0.1's online run path, deferred to plumb P1-a (BACKLOG.md).
- New `src/atlas/deliverer.py`: `Deliverer` Protocol + `GhPrDeliverer` — push
  branch → `gh pr create` → `WorktreeManager.cleanup()`. Never touches
  `main`, never force-pushes (enforced by construction + a dedicated
  security test).
- `test_score_jobs_adapter_real_import_success` is `xfail(strict=False)`
  (content-pipeline decomposed `ScoreJobsUseCase`; re-targeting is
  `job`-workflow scope — tracked in BACKLOG.md), so the suite is genuinely
  green rather than silently red.

**Not yet done (off-CI, manual, real external systems):** T-L0.8 (first live
`atlas run` against the real `claude` backend, confirming `spans.tokens` from
a real JSON envelope) and T-L0.9 (a real `GhPrDeliverer.deliver()` against a
scratch GitHub repo). These are the phase's two manual exit-criteria checks
and have not been executed yet — everything else in the Phase L0 TRS
([`dev/active/loop-mode-phase-L0/`](dev/active/loop-mode-phase-L0/)) is done.

Prior to L0: the v1 7-stage dev pipeline (6 human gates, git worktree
boundary, post-commit hook, full plumb span-tree integration) plus the v2
YAML workflow engine — a multi-workflow loader (`dev`/`job`/`job_cli` +
custom YAML), `LIB:`/`SHELL:`/`RAW:`/plugin-command runner dispatch, and CLI
backend selection (`claude` / `agy`, 4-tier resolution). Full phase-by-phase
build history lives in git log and in
[`dev/archive/yaml-workflow-engine-phase-{1,2,3}/`](dev/archive/).

## Next

See [`docs/1_product_and_research/BACKLOG.md`](docs/1_product_and_research/BACKLOG.md)
for the full pending list. Immediate: run T-L2.13's manual smoke tests (the
`plugin_resolver.resolve()` gap that blocked them is fixed — see Current,
above) — then **Phase L3** (self-healing + routing: pre-PR plumb judge gate,
diagnosis-injected single-retry, failed runs → plumb examples, score-informed
routing). The manual off-CI checks carried by L0/L1 (T-L0.8/T-L0.9,
T-L1.1/T-L1.8) remain open alongside L2's. Also open: tag `v2.2`, install
plumb as a versioned (not path) dependency, add the `CONTENT_PIPELINE_TOKEN`
CI secret, run the T3.8 `agy` manual smoke test, re-target the `score_jobs`
adapter (`job`-workflow scope), wire `extract_cost` so `max_dollars_per_day`
actually accumulates, extend `GhPrDeliverer`'s branch-safety check beyond
exact `"main"`.

## Pointers

- Docs hub: `docs/README.md`
- PRD: `docs/1_product_and_research/PRD.md`
- Backlog: `docs/1_product_and_research/BACKLOG.md`
- TRD (v1): `docs/2_architecture/TRD.md`
- TRD (v2): `docs/2_architecture/TRD-v2.md`
- System design: `docs/2_architecture/system_design.md`
- YAML workflow engine guide: `docs/3_guides/yaml_workflow_engine.md`
