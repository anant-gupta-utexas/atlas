---
project: atlas
status: v3.1 shipped — loop mode L0/L1/L2 complete and verified live 2026-07-27; Phase L3 code-complete 2026-07-26, not yet verified live
last_updated: 2026-07-26
next_gate: Phase L3 (self-healing + routing) — code-complete; T-L3.10 manual smoke + T-L3.11 STATUS close-out pending a human operator session
blocked_on: null
---

# atlas — status

## What atlas is today

A local CLI agent orchestrator with two modes over one engine:

- **Attended** — `atlas run "<task>"` walks a YAML-defined workflow, stopping
  at human gates. The dev workflow is 7 stages / 6 gates; `job`/`job_cli` and
  any workflow you author run through the same machinery.
- **Unattended** — `atlas loop run` polls a GitHub Issues queue, triages each
  issue into one of two lanes, runs the pipeline in an isolated worktree on a
  selectable engine (`claude` or `codex`), and opens a PR. It never merges and
  never pushes `main`.

Both modes write every run into [plumb](https://github.com/anant-gupta-utexas/plumb)
as a typed span tree with tokens, cost, and gate scores. The premise is
unchanged from v1: **humans keep the pen on decisions, the agent does
everything in between, and both sides of the split are measured.** Loop mode
moves the gate from an inline `input()` to a PR review — asynchronous and
batchable, not absent.

Local suite: **484 passed, 1 xfailed, 95.29% coverage** (measured 2026-07-27).

## Shipped

| Release | What it is | State |
|---|---|---|
| **v2.2** | YAML workflow engine — multi-workflow loader, `RAW:`/`LIB:`/`SHELL:`/plugin-command dispatch, `CliBackend` strategy (`claude`/`agy`) | Complete. Tagged at `47027c3` (Phase 3 review). |
| **v3.0** | Measured baseline (Phases L0+L1) — Claude JSON telemetry → plumb, headless permission profile, `GhPrDeliverer`, `CodexBackend`, `loop_dev.yaml` | Complete, incl. both phases' manual off-CI checks. |
| **v3.1** | The loop daemon (Phase L2) — `queue_gh.py`, `loop.py`, `triage.py`, `loop_budget.py`, `[loop]` config, `atlas loop run/start/stop/status/attach` | Complete, incl. T-L2.13. TRD-v3 §13 #1–#8 all hold. `pyproject.toml` reads `3.1.0`. |

**On tags.** `v2.2` and `v3.1` exist **locally only** — push with
`git push origin v2.2 v3.1`. There is deliberately **no `v3.0` tag**, though
TRD-v3 §11 reserves one for the L0+L1 exit: no commit ever satisfied it. L0's
headline criterion (§13 #1, a live measured run) was *unimplementable* until
2026-07-27 — `parse_usage()` had no caller — so the L0/L1 code-complete point
was not a releasable state, and by the time it became one, L2 was in the same
tree. Tagging `v3.0` retroactively would assert a working measured baseline
that never shipped on its own. `v3.1` subsumes it.

Per-module coverage on the loop-mode surface: `loop.py` 90%, `queue_gh.py`
92%, `triage.py` 95%, `cli_backend.py` 99%, `config.py`/`deliverer.py` 100%.

## Verified against real systems (2026-07-27)

All five manual off-CI checks the phases carried — **T-L0.8, T-L0.9, T-L1.1,
T-L1.8, T-L2.13** — have been executed against the real
`anant-gupta-utexas/atlas` repo with real tokens. Evidence per criterion:

| TRD-v3 §13 | Criterion | Evidence |
|---|---|---|
| #1 | Live attended run, measured | `code_gen` span carried **161,200 real tokens**; run-level `dollar_cost` **`$0.1865061`**. |
| #2 | Attended-mode invariance | Telemetry is opt-in behind `atlas run --telemetry`; without it the attended argv is byte-identical to pre-L0. |
| #3 | `CodexBackend` dispatch | `loop_dev` completed on **both** `claude` and `codex`. Codex reports no cost at all, so its run-level `dollar_cost` is correctly **NULL**, not `0.0`. |
| #4 | Delivery primitive | Real PRs **#8** and **#11**. `main` was never pushed to and never force-pushed. |
| #5 | Zero-touch delivery *(headline)* | Issue **#7** → PR **#8** carrying `Closes #7` plus a `run_id` comment, with **zero keystrokes**. Merging it made the next tick write `user_signal=approved`, anchored to a real `pr_outcome`/`handoff` span, and relabel the issue `atlas:done`. |
| #6 | Two-lane routing | Issue **#10** (`wf:planned`) → PR **#11** containing exactly the three TRS triad files, with a `dev_docs_be` span and **no `code_gen` span** — the loop stopped for review as designed. |
| #7 | Budgets & breaker | `atlas loop status` reports real accumulated spend — **`$2.5822 / $5.00`** — where it previously printed "not tracked (cap NOT enforced)". |
| #8 | Crash recovery | `kill -9` mid-dispatch; restart reclaimed the issue and pruned the orphaned worktree. |

**What made #1 and #7 possible: plumb v1.1.** `RunHandle.set_usage()` landed,
so run-level `dollar_cost` is writable and `spans.tokens_in`/`tokens_out` are
durable. The **plumb P1-a deferral that TRD-v3 §3.6/§13 repeatedly carves out
is closed**, and `max_dollars_per_day` is a real budget rather than a
documented intention.

## What running the checks actually cost

Running them found **eight defects that 400+ green tests, `mypy --strict` and
a full code review had all missed**, because each lived on a path CI never
executed. The headline: **Phase L0's telemetry was never connected in
production** — `parse_usage()` had no caller, `StageOutcome` had no usage
field, `record_span()` was called with no `tokens=`, and nothing requested the
JSON envelope. §13 #1 was *unimplementable*, not merely unverified. The
envelope schema it parsed was also wrong, and the token rule recorded a real
159,896-token span as **50 tokens**.

Two shipped assumptions were reversed by measurement:

- **`claude -p --output-format json` emits a JSON array** of stream events
  terminated by a `type: "result"` element — not the single object `--help`
  describes. The old `startswith("{")` sniff routed every real envelope into
  the plain-text branch. Anthropic's token fields are **disjoint**: billed
  input is `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
- **Codex's `cached_input_tokens` is a subset of `input_tokens`, not an
  addend** (TRD-v3 §3.6's Pending Decision #4, settled by a cold/warm capture
  pair). atlas had it backwards and was inflating every Codex span's input by
  ~70–90%. The rule is now `openai_subset_fields_v2`; spans written under v1
  stay recomputable because the raw breakdown and the rule name were persisted
  to `spans.attributes`.

The through-line: **every failure was silent.** Runs reported `success` on all
spans while delivering nothing. Fixing observability first is what made the
rest findable. Full defect list and per-defect evidence:
[`dev/archive/loop-mode-phase-L2/loop-mode-phase-L2-tasks.md`](dev/archive/loop-mode-phase-L2/loop-mode-phase-L2-tasks.md)
(field-findings section). Phase-by-phase detail lives in the archived TRS
triads under [`dev/archive/`](dev/archive/) and in git log.

## Known gaps — read before trusting a number

- **Codex spend is invisible.** The Codex CLI emits no cost field, so a
  Codex-only day advances `max_runs_per_day` but not `max_dollars_per_day`.
  The runs cap is the load-bearing bound on that lane, not the backstop.
  `atlas loop status` says so at runtime rather than printing a confident
  `$0.00`.
- **`test_score_jobs_adapter_real_import_success` is still `xfail`.**
  content-pipeline decomposed `ScoreJobsUseCase`; re-targeting the adapter is
  `job`-workflow scope, not loop mode. It is a correct drift signal.
- **`origin/main` is far behind the loop-mode branch**, so the smoke PRs were
  cut against a stale base.
- **CI runs on `workflow_dispatch` only** — the suite is a local pre-commit
  gate today, not an on-push check.
- **plumb is still a local path dependency.** A versioned pin needs a
  `v1.1.0` tag in the plumb repo first.
- **T3.8 (`agy` manual smoke) is still open** — blocked only on credentials.

Full pending list: [`docs/1_product_and_research/BACKLOG.md`](docs/1_product_and_research/BACKLOG.md).

## Next

**Phase L3 — self-healing + routing (`v3.2`) — code-complete, not yet
verified live.** TRS in
[`dev/active/loop-mode-phase-L3/`](dev/active/loop-mode-phase-L3/). Shipped:
`judge_gate.py` (pre-PR task-completion scoring + failure-mode
classification via plumb's library `JudgeAdapter`) and `self_heal.py`
(diagnosis-injected single child-run retry via `parent_run_id`), wired into
`loop.py`'s `run_one_shot`/`run_planned_first_pass`/`tick()`. TRD-v3 §13 #9
and #10 are implemented and covered by unit + a dedicated retry-cap
integration test (T-L3.8), but — like every prior phase — code-complete is
not the same as verified: **T-L3.10** (manual smoke against a real repo,
needs a configured `PLUMB_JUDGE_PROVIDER`) and **T-L3.11** (this section's
own final close-out) are still open, tracked in
[`loop-mode-phase-L3-tasks.md`](dev/active/loop-mode-phase-L3/loop-mode-phase-L3-tasks.md).
Router v1 (score-informed engine/workflow routing) remains a named-but-
unimplemented stretch seam at `loop.py::_engine_for_issue` — see
[BACKLOG.md](docs/1_product_and_research/BACKLOG.md).

Local suite: **520 passed, 1 xfailed, 95% coverage** (measured 2026-07-26;
`judge_gate.py` 86%, `self_heal.py` 100%, `loop.py` 90%).

## Pointers

- Docs hub: [`docs/README.md`](docs/README.md)
- Backlog: [`docs/1_product_and_research/BACKLOG.md`](docs/1_product_and_research/BACKLOG.md)
- Loop-mode phase contract: [`docs/2_architecture/TRD-v3.md`](docs/2_architecture/TRD-v3.md)
- System design: [`docs/2_architecture/system_design.md`](docs/2_architecture/system_design.md)
- Workflow engine guide: [`docs/3_guides/yaml_workflow_engine.md`](docs/3_guides/yaml_workflow_engine.md)
- Backend selection: [`docs/3_guides/cli_backends.md`](docs/3_guides/cli_backends.md)
- Build history: [`dev/archive/`](dev/archive/) + git log
