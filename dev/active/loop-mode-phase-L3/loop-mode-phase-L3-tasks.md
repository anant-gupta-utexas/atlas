---
task: loop-mode-phase-L3
status: code-complete
phase: L3 (self-healing + routing)
delivers: v3.2
---

## current: phase=code_complete, gate=none, next=T-L3.10 (manual smoke, needs a human operator + PLUMB_JUDGE_PROVIDER)

Detailed acceptance criteria for every task live in
[`loop-mode-phase-L3-tasks-detail.md`](./loop-mode-phase-L3-tasks-detail.md).
This file tracks checkbox-level progress only.

## Pre-work / blocking precondition

- [x] **Pending Decision #1 resolved** — **Option A** (fix
      `plugin_resolver.resolve()`), its recommended option. Fixed 2026-07-25 in
      commit `48ee363` during the L2 session, since the same gap blocked
      T-L2.13. T-L3.1 is closed with it; see the Tasks list below.
- [x] **Pending Decision #3 confirmed** — verified directly against the
      sibling `plumb` repo source (`plumb/adapters/__init__.py`,
      `plumb/core/ports.py`): the library `JudgeAdapter` API is correct,
      exactly as designed. `plumb judge run` (the CLI) is confirmed to be a
      batch pass over already-persisted runs and cannot serve a synchronous
      pre-PR gate.
- [x] **Pending Decision #8 spiked** — verified against
      `plumb/_prompt_loader.py` + `plumb/docs/3_guides/getting_started.md` +
      `docs/3_guides/judge_prompts/routing_top1.md`: prompt files load from
      `$PLUMB_DATA_DIR/judge_prompts/<metric_name>.md`; plumb does not bundle
      them — the metric owner ships a doc-example file the operator copies
      over. Atlas's two files live at
      [`docs/3_guides/judge_prompts/task_completion.md`](../../../docs/3_guides/judge_prompts/task_completion.md)
      and
      [`docs/3_guides/judge_prompts/failure_mode.md`](../../../docs/3_guides/judge_prompts/failure_mode.md).
      **One correction the TRS's authoring pass missed**: plumb's judge reply
      parser (`plumb/adapters/_judge_common.py::parse_reply`) only accepts a
      verdict of `"pass"`, `"fail"`, or a bare number — never an arbitrary
      label — so `classify_failure`'s four-way mode can't ride `value_label`
      directly. `failure_mode.md` asks for `verdict: "fail"` always and
      encodes the mode as a leading token in `rationale`
      (`"<mode>: <explanation>"`), parsed back out in `judge_gate.py`.

## Tasks

- [x] T-L3.1 — Resolve or formally scope the `plugin_resolver` `RAW:` blocker
      (**closed early 2026-07-25, commit `48ee363`** — fixed during the L2
      session that unblocked T-L2.13; Option A. No L3 work remains)
- [x] T-L3.2 — `judge_gate.py`: pre-PR scoring
- [x] T-L3.3 — `judge_gate.py`: failure-mode classification
- [x] T-L3.4 — `loop.py`: wire the judge gate into `run_one_shot`
- [x] T-L3.5 — `loop.py`: `parent_run_id` + `diagnosis` params on `run_one_shot`
      (also extended to `run_planned_first_pass`, needed by self_heal's
      planned-lane retry — not itself a numbered L3 task, but required by
      T-L3.6's design)
- [x] T-L3.6 — `self_heal.py`: the retry state machine
- [x] T-L3.7 — `loop.py`: wire `self_heal` into `tick()`'s dispatch failure path
- [x] T-L3.8 — Retry-cap invariant test (explicit, not incidental)
- [x] T-L3.9 — Router v1 seam (named, not implemented) + cost-tracking note
- [ ] T-L3.10 — Manual smoke: judge gate + retry against a real repo (code
      side is done; needs a human operator session with a configured
      `PLUMB_JUDGE_PROVIDER` against a real repo — not run in this session)
- [ ] T-L3.11 — Update STATUS.md and close out the phase (depends on T-L3.10)

One additional non-numbered change, needed by T-L3.5/T-L3.6's design and
called out explicitly rather than silently: `orchestrator.py::Pipeline`
gained a single read-only `plumb` property (returns the existing private
`self._plumb`) so `run_one_shot` can call `reopen_run()` on the same
`PlumbIO` instance the Pipeline writes through for the child-run handoff.
No behavior change — `Pipeline`/`orchestrator.py` are otherwise unchanged,
per Appendix A's standing rule.

Full test suite (520 passed, 1 xfailed), `ruff check`, and `mypy --strict
src` are all clean as of this pass. Total coverage 95%; `judge_gate.py` 86%,
`self_heal.py` 100%, `loop.py` 90%. Implementation notes (including two real
bugs an integration test caught before landing) are in
[`loop-mode-phase-L3-context.md`](./loop-mode-phase-L3-context.md#implementation-notes-added-2026-07-26-after-t-l32t-l39-landed).

## Exit criteria (TRD-v3 §13)

- [x] #9 — Diagnosis-injected retry: verify/judge failure → plumb example →
      classified → one child-run retry with diagnosis injected → exhaustion →
      `atlas:blocked`. **Code-complete, unit/integration-tested
      (T-L3.8's dedicated invariant test); not yet proven live (T-L3.10).**
- [x] #10 — Pre-PR judge gate: a plumb judge score below threshold blocks
      delivery. **Code-complete, unit-tested; not yet proven live (T-L3.10).**

## Carried-forward open manual checks (not this phase's tasks, tracked for visibility)

- [ ] T-L0.8 — first live `atlas run` against real `claude` backend
- [ ] T-L0.9 — real `GhPrDeliverer.deliver()` against a scratch repo
- [ ] T-L1.1 — write-heavy Codex capture (cold/warm-cache token question)
- [ ] T-L1.8 — both-engines smoke (`--backend claude` / `--backend codex`)
- [ ] T-L2.13 — zero-touch delivery / planned-lane / crash-recovery smoke
      (**no longer blocked** — the shared `plugin_resolver` gap was fixed
      2026-07-25; needs only a human operator session. An operator runbook
      lives at the bottom of
      [`loop-mode-phase-L2-tasks.md`](../../archive/loop-mode-phase-L2/loop-mode-phase-L2-tasks.md))
