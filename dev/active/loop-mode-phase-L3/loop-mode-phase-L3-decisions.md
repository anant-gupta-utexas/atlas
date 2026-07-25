# Pending Decisions & Clarifications — Loop Mode, Phase L3 TRS

Full text of all decisions flagged during this TRS's authoring. Split out from
`loop-mode-phase-L3-plan.md` to keep that file under the repo's 800-line cap
(matches the L2 TRS's own precedent — see
`dev/active/loop-mode-phase-L2/loop-mode-phase-L2-decisions.md`). Normative, not
optional reading — several of these change which files T-L3.1/T-L3.3 touch.

---

## 1. How to resolve the `plugin_resolver` `RAW:` blocker (T-L3.1) — before L3's manual checks, or in parallel with L3's code tasks?

`STATUS.md`'s `blocked_on` names this as unresolved: `plugin_resolver.resolve()`
does a literal dict lookup and doesn't special-case `RAW:`-prefixed tool strings
despite its own docstring's claim that it does, so `loop_dev.yaml`'s `plan` and
`code_gen` stages raise `RoutingDriftError` under a real `atlas loop run`. This is
a precondition for every task in this TRS that touches a live run (T-L3.10) and
for L2's own still-open T-L2.13.

- **Option A (recommended): fix `plugin_resolver.resolve()` to special-case
  `RAW:` properly, as T-L3.1, first task in this phase.** Matches the docstring's
  original intent (it already claims to special-case `RAW:`), is a small,
  well-scoped code fix, and permanently unblocks every future `RAW:`-based
  workflow, not just this one. Downside: touches a shared module
  (`plugin_resolver.py`) that other workflows also depend on — needs its own
  regression coverage beyond `loop_dev.yaml`.
- **Option B: ship a `.atlas.toml [plugin_commands]` workaround** covering
  `loop_dev.yaml`'s three stages, documented as loop-mode setup. Smaller,
  zero-risk to `plugin_resolver.py`'s other callers, but doesn't fix the
  underlying bug — every future `RAW:` workflow (e.g. a hypothetical L4 addition)
  hits the same gap again.

This TRS's task list (T-L3.1) is written to accommodate either; the maintainer
should pick before implementation starts since it changes which files T-L3.1
touches.

---

## 2. Does the planned lane get the same judge gate as the quick lane?

This TRS's design (Component Design, Error Handling table in the plan) says
**no** — a plan-only PR has no code diff to score for "task completion," only a
TRS triad.

- **Option A (as designed, recommended): no judge gate on planned-lane output at
  all**, only the retry-on-failure machinery (self_heal still applies if
  `dev-docs-be` itself fails to produce the triad).
- **Option B: a different judge metric** (e.g. "TRS completeness/quality")
  scoring the triad before the plan-only PR opens. Adds real value but is a new
  prompt + new metric this TRS did not scope or ask the TRD for — would need a
  TRD amendment or a follow-up decision to add cleanly, since TRD-v3 §13 #10 only
  names "a plumb judge score" singular, implicitly the code-diff one.

Recommend Option A for this phase (matches the TRD's literal exit bar), flag
Option B as a natural L4-or-later follow-up.

---

## 3. Judge invocation: library `JudgeAdapter` Protocol vs. `plumb judge run` CLI

TRD-v3 §6 names `plumb judge run` (the CLI) as the integration surface; this
TRS's design uses the library `JudgeAdapter` Protocol instead.

Verified by reading the sibling `plumb` repo's actual source
(`plumb/_cli_judge.py`, `plumb/adapters/__init__.py`): `plumb judge run` is a
**batch CLI command that scans already-persisted, un-scored runs** in the plumb
DB — it cannot score a diff synchronously mid-`tick()` before a run even finishes
being recorded, because the run has to exist and be queryable first. The Python
API it's built on (`plumb.adapters.get_judge_adapter(...).score(...)`) **is**
directly callable in-process, which is what a pre-PR gate actually needs.

This TRS's design uses the library API; it is functionally what TRD-v3 §13 #10
requires, but is a **narrower, more specific reading of §6's "plumb judge"
integration line than its literal phrasing** ("`plumb judge run` (v3.2) for the
pre-PR quality gate"). Flagging so the maintainer can confirm this reading is
correct rather than silently diverging from the TRD's exact words — recommend
confirming, since the alternative (shelling out to `plumb judge run` as a batch
pass after every single run) would add a process-spawn-per-run + polling-for-the-
score round trip this TRS's design avoids entirely.

---

## 4. Router v1 — include as a stretch task in this TRS's list, or omit entirely?

This TRS omits it from the committed task list (T-L3.9 only documents the seam)
per the phase-picker/nomenclature contract's instruction not to invent scope the
TRD itself marked "(stretch)" and left out of §13's numbered exit criteria.

If the maintainer wants it committed as a real task for this phase, say so and it
becomes T-L3.12 with its own acceptance criteria (a `plumb run stats`-backed
engine-preference lookup at `_engine_for_issue()` — non-trivial, since it needs a
defined "task class" taxonomy the codebase doesn't have yet).

---

## 5. Fail-open vs. fail-closed on `JudgeUnavailableError`

This TRS's design picks **fail-open for the gate** (an unconfigured judge doesn't
block every PR — matches "errors surface, don't hang," TRD-v3 §4 Usability) and
**fail-to-`not_retryable` for classification** (an unclassified failure should
not blind-retry).

Alternative: fail-closed on the gate too (block every PR until a judge is
configured) — safer in the sense that "no judge gate at all" silently regresses
to L2's un-gated behavior, which an operator might not notice for a while.

Recommend keeping fail-open on the gate as designed (matches the reliability
posture the rest of the loop already takes toward optional-but-valuable checks —
e.g. `queue_gh.preflight()` doesn't exist either, L2 Decision #7 chose "rely on
first-call failure" over a hard startup gate) but naming it explicitly here since
it's a real behavior choice, not an implementation detail.

---

## 6. Should the retry cap be enforced by control flow only, or also persisted as a per-issue counter in `LoopState`?

This TRS deliberately does NOT add a persisted counter — the cap is enforced by
`self_heal.handle_failure` never recursing and `tick()` calling it at most once
per dispatch.

A persisted counter would add defense-in-depth against a hypothetical future bug
that calls `handle_failure` twice, at the cost of a new `LoopState` field +
migration-adjacent concerns for an already-existing flat JSON file.

Recommend the control-flow-only design (simpler, and T-L3.8 directly tests the
invariant it depends on) unless the maintainer wants the extra persisted
belt-and-suspenders.

---

## 7. Empty/near-empty diffs reaching the judge gate

Error Handling (in the plan) flags this as "not special-cased... if this proves
noisy in practice, revisit." No decision needed now; named so a future bug report
about spurious judge-gate failures on trivial diffs isn't a surprise, and so
nobody assumes atlas special-cases this today when it doesn't.

---

## 8. Judge prompt file provisioning for the new `failure_mode` metric (T-L3.3)

Unverified in this TRS: whether plumb expects the **caller** (atlas) to ship a
`judge_prompts/failure_mode.md` file into `$PLUMB_DATA_DIR`, or whether plumb
bundles/packages prompt files itself for known metrics.

T-L3.3's acceptance criteria flags this as needing verification against plumb's
actual provisioning story before implementation — recommend a short spike reading
`plumb/_prompt_loader.py` and any existing `judge_prompts/*.md` files in the
sibling repo before committing to a file-location decision, rather than guessing
in this TRS.
