---
task: loop-mode-phase-L3
status: not started
phase: L3 (self-healing + routing)
delivers: v3.2
---

## current: phase=not_started, gate=none, next=T-L3.1

Detailed acceptance criteria for every task live in
[`loop-mode-phase-L3-tasks-detail.md`](./loop-mode-phase-L3-tasks-detail.md).
This file tracks checkbox-level progress only.

## Pre-work / blocking precondition

- [ ] **Pending Decision #1 resolved** — maintainer picks Option A (fix
      `plugin_resolver.resolve()`) or Option B (`.atlas.toml [plugin_commands]`
      workaround) before T-L3.1 starts.
- [ ] **Pending Decision #3 confirmed** — maintainer confirms the library
      `JudgeAdapter` API (not the `plumb judge run` CLI) is the correct reading
      of TRD-v3 §6 before T-L3.2 starts.
- [ ] **Pending Decision #8 spiked** — 15-minute read of
      `plumb/_prompt_loader.py` + existing `judge_prompts/*.md` files to settle
      prompt-file provisioning before T-L3.3 starts.

## Tasks

- [ ] T-L3.1 — Resolve or formally scope the `plugin_resolver` `RAW:` blocker
- [ ] T-L3.2 — `judge_gate.py`: pre-PR scoring
- [ ] T-L3.3 — `judge_gate.py`: failure-mode classification
- [ ] T-L3.4 — `loop.py`: wire the judge gate into `run_one_shot`
- [ ] T-L3.5 — `loop.py`: `parent_run_id` + `diagnosis` params on `run_one_shot`
- [ ] T-L3.6 — `self_heal.py`: the retry state machine
- [ ] T-L3.7 — `loop.py`: wire `self_heal` into `tick()`'s dispatch failure path
- [ ] T-L3.8 — Retry-cap invariant test (explicit, not incidental)
- [ ] T-L3.9 — Router v1 seam (named, not implemented) + cost-tracking note
- [ ] T-L3.10 — Manual smoke: judge gate + retry against a real repo (blocked on
      T-L3.1, T-L3.7, T-L3.8; also needs a configured `PLUMB_JUDGE_PROVIDER`)
- [ ] T-L3.11 — Update STATUS.md and close out the phase

## Exit criteria (TRD-v3 §13)

- [ ] #9 — Diagnosis-injected retry: verify/judge failure → plumb example →
      classified → one child-run retry with diagnosis injected → exhaustion →
      `atlas:blocked`.
- [ ] #10 — Pre-PR judge gate: a plumb judge score below threshold blocks
      delivery.

## Carried-forward open manual checks (not this phase's tasks, tracked for visibility)

- [ ] T-L0.8 — first live `atlas run` against real `claude` backend
- [ ] T-L0.9 — real `GhPrDeliverer.deliver()` against a scratch repo
- [ ] T-L1.1 — write-heavy Codex capture (cold/warm-cache token question)
- [ ] T-L1.8 — both-engines smoke (`--backend claude` / `--backend codex`)
- [ ] T-L2.13 — zero-touch delivery / planned-lane / crash-recovery smoke
      (blocked on the same `plugin_resolver` gap T-L3.1 resolves — closing
      T-L3.1 should unblock T-L2.13 too)
