# Tasks — Loop Mode, Phase L1 TRS

Progress checklist. Source-of-truth for design is
[`loop-mode-phase-L1-plan.md`](./loop-mode-phase-L1-plan.md).
Reference notes live in
[`loop-mode-phase-L1-context.md`](./loop-mode-phase-L1-context.md).

## Current

```
phase: engineering complete (T-L1.2-L1.7, L1.9, L1.10 done); manual off-CI checks open
gate:  none
next:  T-L1.1 (write-heavy Codex capture) + T-L1.8 (manual smoke, both engines) — off-CI, not run by this session
```

## Status — no blocking dependency

L0 is code-complete (T-L0.1–L0.7, L0.10, L0.11 done per `STATUS.md` 2026-07-22; T-L0.8/T-L0.9
are manual off-CI checks that don't block L1's engineering work — see context.md). TRD-v3 §14
lists L1's dependency as simply "L0."

## Tasks (flat — Phase L1 only, no sub-phases)

- [x] **T-L1.1** — Capture a **write-heavy** Codex run + pin `codex --version` — **DONE 2026-07-26** against `codex-cli 0.144.4`. All five sub-questions closed: (a) write-path event types are `item.started`/`item.completed` with `item_type` in {`command_execution`, `file_change`, `agent_message`}; (b) **no failure event type exists** — a sandbox-denied write exits `0` and still emits `turn.completed`, while a hard preflight failure exits non-zero with empty stdout; (c) `--add-dir` **is** writable under `--sandbox workspace-write` (Pending Decision #3 → honored as documented); (d) `reasoning_output_tokens > 0` observed (9/50/159); (e) **Pending Decision #4 RESOLVED — `cached_input_tokens` is a SUBSET of `input_tokens`, not an addend.** Cold/warm pair: input held flat 68719→69161 (+0.6%) while cached rose 48384→62464 (+29%); under the addend model input had to fall ~14k. atlas's assumption was backwards and inflated every Codex span's input ~70-90%; `codex_usage_to_tokens` is now `openai_subset_fields_v2`. Real captures checked in at `tests/fixtures/codex_jsonl/{write_heavy_real,sandbox_denied_real}.jsonl`; Part E of `headless-clis-reference.md` updated.
- [x] **T-L1.2** — `CodexBackend.build_argv` + `preflight`: register in `_KNOWN_BACKENDS`/`make_backend()`; `-C` = last `add_dirs` element, rest via `--add-dir`, plus `--model`/`--sandbox workspace-write`; no bypass flags ever; preflight fails closed on missing `OPENAI_API_KEY`/`$CODEX_HOME/auth.json` with **no subprocess spawned**
- [x] **T-L1.3** — `CodexBackend.parse_result` + `parse_usage` + `CodexUsageStats`: exit-code-driven status, `turn.completed` presence check, text joined from `item.completed`/`agent_message`, five-field usage with `total_cost_usd` always `None`; fixtures under `tests/fixtures/codex_jsonl/`
- [x] **T-L1.4** — `loop_dev.yaml`: ungated `plan → code_gen(isolate) → verify`, `span_kind: llm` for `code_gen` (Pending Decision #2 — resolved `llm`, per maintainer 2026-07-24), `default_backend: claude`
- [x] **T-L1.5** — Codex section (Part E) in `headless-clis-reference.md`: matches existing Part B/C depth; every unconfirmed schema claim explicitly flagged as unverified in the doc text itself; added Part F (3-way comparison table)
- [x] **T-L1.6** — `Pipeline.run_to_completion()` status surfacing: new `RunResult(ctx, status)` dataclass; widened the 3 existing return statements; `cli.py::run`/`resume` unaffected (bare-statement calls, grep-confirmed no other call site exists)
- [x] **T-L1.7** — Integration tests: mocked end-to-end Codex dispatch (`StageOutcome` → plumb span with `tokens`) + negative failure-status test + `RunResult` regression coverage
- [ ] **T-L1.8** — Manual smoke test (off-CI): `atlas run "<task>" --workflow loop_dev --backend claude` (must complete) and `--backend codex` (attempt; explicitly record skip-with-reason if auth unavailable). **NOT RUN this session** — real external dispatch, off-CI by design.
- [x] **T-L1.9** — Lint/type/coverage gate: `ruff check`, `ruff format --check`, `mypy --strict src` all clean; coverage 96.16% repo-wide (no regression below L0's 96%); `CodexBackend` 100%; `run_to_completion`'s changed lines covered
- [x] **T-L1.10** — Update `STATUS.md` with L1 completion

## Exit criteria (TRD-v3 §13 item 3 — copied for tracking)

- [ ] **§13 #3** — A `loop_dev` run under `engine:codex` produces a valid `StageOutcome` (mocked in CI via captured JSONL; real dispatch in manual testing if auth allows). `preflight` fails closed on missing auth with no subprocess spawned.
- [ ] **"`loop_dev` runs end-to-end on both engines (manual smoke)"** (TRD-v3 §14 L1 exit criteria, second clause)

## Resolved decisions (see plan's Resolved Decisions table for full rationale)

- [x] **#1 — Codex schema VERIFIED against `codex-cli 0.144.4`** (2026-07-24, real captured stream + `--help` read directly). Supersedes the original "design against best-known shape" decision. The assumed schema was wrong in 4 material ways — see plan. Binding on T-L1.2/T-L1.3.
- [x] **#2 — "Guardrail signs" = `StageOutcome.status`/`RunResult.status` propagation; no new type.** User-selected. Binding on T-L1.4/T-L1.6.
- [x] **#3 — `Pipeline.run_to_completion()` return type widens to `RunResult`, invoking Appendix A's "pause and reconcile" escape hatch explicitly.** Binding on T-L1.6. If you disagree with in-L1 scoping, the alternative is making this the first task of the L2 TRS instead — flagged, not silently assumed.
- [x] **#4 — `-C` gets the *last* `add_dirs` element; the rest are passed via `--add-dir`** *(revised 2026-07-24 — `--add-dir` exists on `codex exec`; the original "drop the extras" rule would have made Codex runs context-poorer than Claude runs)*. Binding on T-L1.2.
- [x] **#5 — `loop_dev.yaml` stays strictly one-shot-lane; no planned-lane scaffolding.** User-selected. Binding on T-L1.4.
- [x] **#6 — `CodexUsageStats` is a distinct dataclass, not shared with `UsageStats`.** Reinforced by verification: Codex has no cost field and two extra token fields, so the shapes genuinely differ. Binding on T-L1.3.
- [x] **#7 — TRD-v3 §3.3's `CodexBackend` contract table is factually wrong** (claims a `result` event + subtype failure signal that 0.144.4 does not emit). This TRS supersedes it; **the TRD should be corrected upstream**. Binding on T-L1.3 + a doc follow-up.
- [x] **#8 — Status is exit-code-only; `parse_result` never infers failure from event content.** The honest consequence (exit-0-but-useless run reports success) is documented and is what `verify` + the PR gate exist to catch. Binding on T-L1.3.
- [x] **#9 — `CodexUsageStats` keeps all four reported token fields**, with the `(in, out)` reduction as one documented function. Binding on T-L1.3.
- [x] **#10 — v3 measures tokens, not dollars, for cross-engine comparison** (maintainer, 2026-07-24). No price table in v3. If added later: dated config (`as_of`), computed at query time, labeled *estimated*, never stored alongside CLI-reported figures. Binding on L2/L4, recorded here so it isn't rediscovered.
- [x] **#11 — `span_kind: llm` for `loop_dev.code_gen`** (maintainer, 2026-07-24). Span kinds describe what the span *is*; role is carried by the stage name (`code_gen`), which is the exact filter for "code-writing stages" rather than the drift-prone `span_kind` proxy. Binding on T-L1.4.
- [x] **#12 — Per-span token storage answers exactly one question: total tokens billed.** Verified: `spans` has a single `tokens INTEGER` column and plumb sums the `(in, out)` tuple on write (`adapters/_schema.py:47`, `core/entities.py:123-127` — *"the in/out split is not durable"*). Binding on T-L1.3's reduction rule.

## Notes for implementation

- **The schema is verified — build against the plan's Data Structures section, not TRD-v3 §3.3.** The TRD describes a `result` event with a status subtype; `codex-cli 0.144.4` emits no such thing. Following the TRD literally produces a backend that parses nothing. T-L1.1 now covers only what the read-only sample couldn't: write-path event types, failure-path events, `--add-dir` writability, reasoning tokens.
- **`total_cost_usd` is permanently `None` for Codex — this is not a bug to fix.** Claude's CLI reports cost that plumb can't yet store (P1-a); Codex's CLI never reports it at all. **Decided: v3 is tokens-only; no price table anywhere in v3.**
- **Per-span tokens collapse to ONE integer in plumb** (`spans.tokens`; the `(in, out)` tuple is summed on write and the split is explicitly non-durable). Don't design as if the split survives — it doesn't. The reduction rule exists only to make that single number mean "total tokens billed for this span."
- **Pending Decision #4 is the one open item that can silently produce wrong numbers.** If `cached_input_tokens` turns out to be a subset rather than an addend, every Codex span is over-counted ~4×. `parse_usage` must log both raw values at debug level so any real run is diagnosable without a bespoke experiment.
- **The `CodexBackend.preflight()` no-subprocess-spawned test is the security-critical test of this phase**, mirroring L0's `Deliverer` push-safety test and Phase 3's `agy` auth-preflight test: assert the dangerous call never fires, not just that the return value looks right.
- **T-L1.6 touches `orchestrator.py`, which every prior phase (including L0) treated as strictly unchanged.** Read the plan's Resolved Decision #3 in full before starting — this is a deliberate, narrow, TRD-sanctioned exception (Appendix A's own "pause and reconcile" clause), not scope creep. Grep `run_to_completion` call sites *before* changing the signature so no caller is missed.
- **T-L1.8 is a real, off-CI action, same posture as L0's T-L0.8/T-L0.9.** The `--backend claude` leg has no auth risk (already proven working in L0) and should always be run. The `--backend codex` leg depends on whether Codex auth is actually available in the environment — if not, record the skip explicitly rather than leaving the checkbox ambiguous.
- **Fixture files are constructed, not necessarily live-captured**, unless T-L1.1 produces real output. If T-L1.1 does capture real output, replace the constructed fixtures with the real captures before T-L1.3 is marked done — closer to ground truth is strictly better here.

## Implementation notes (post-hoc — fill in after work is done)

- **T-L1.2/T-L1.3 landed together** — `CodexBackend`'s argv/preflight and
  parse_result/parse_usage were implemented as one cohesive class addition to
  `cli_backend.py`, since splitting them into separate commits would have
  left an uncompilable intermediate state. Both tasks' acceptance criteria
  are independently verified in `tests/unit/test_cli_backend.py` (75 tests
  covering `CodexBackend`, 100% line coverage on `cli_backend.py`).
- **Found and fixed a pre-existing bug in `Pipeline.step()` while writing
  T-L1.7's `RunResult` regression tests**: the ungated-stage branch
  unconditionally did `self._stages[stage.index + 1]`, which raises
  `IndexError` whenever the workflow's *last* stage has no gate.
  `dev.yaml`/`job.yaml` always gate their final stage, so this path was
  never exercised in CI until `loop_dev.yaml` — whose last stage (`verify`)
  is ungated by design (Resolved Decision #5) — hit it immediately. Fixed by
  mirroring the guard the *gated* branch already had (`if stage.index <
  len(self._stages) - 1`). This is in-scope for T-L1.4/T-L1.7, not scope
  creep: `loop_dev.yaml` is what surfaced the bug, and no `loop_dev` run
  (manual or automated) could ever complete without this fix.
- **T-L1.1 and T-L1.8 were not run this session** — both require a live
  external `codex`/`claude` CLI session (real subprocess dispatch, real
  auth), which is off-CI, off-agent-session work by design, matching L0's
  own T-L0.8/T-L0.9 posture. Everything downstream (T-L1.2/T-L1.3's
  `CodexBackend`, T-L1.5's doc) was built defensively around the schema gaps
  T-L1.1 would close — every such gap is explicitly flagged "UNVERIFIED" in
  `headless-clis-reference.md` Part E rather than silently assumed. A human
  with `codex`/`claude` CLI access needs to run these before Phase L1 is
  fully closed; until then `next_gate` in `STATUS.md` names both.
- **Coverage**: `CodexBackend` reached 100% (target was ≥85%); repo-wide
  landed at 96.16% (target: no regression below L0's 96%). `mypy --strict
  src`, `ruff check`, and `ruff format --check` all clean at phase close.
  Full suite: 301 passed, 1 xfail (the pre-existing L0 `score_jobs` xfail,
  unrelated to L1).
