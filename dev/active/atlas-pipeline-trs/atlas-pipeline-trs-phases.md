# Implementation Phases — `atlas.pipeline`

Companion to [`atlas-pipeline-trs-plan.md`](./atlas-pipeline-trs-plan.md).
Per-task progress is tracked in [`atlas-pipeline-trs-tasks.md`](./atlas-pipeline-trs-tasks.md).

Effort scale: S = ½ day, M = 1 day, L = 1.5 days, XL = 2+ days.

---

## Phase 1 — Skeleton + state machine (no plumb, no real plugins)

**Objective:** Walk all 7 stages with stub everything. Prove the gate loop, the routing fixture, and the resume protocol work in isolation.

**Tasks:**

* **T1.1 — Define stage table + enums** [Effort: S]
  - **Description:** Implement `src/atlas/stages.py` with `StageName`, `GateLabel`, `StageSpec`, `STAGES` tuple. Commit `tests/fixtures/routing_ground_truth.json` mirroring `STAGES`.
  - **Acceptance Criteria:**
    - [ ] `STAGES` has exactly 7 entries in the order from PRD.
    - [ ] Stage 3 has `gate_label=None`; gate 3 is on stage 4.
    - [ ] `tests/fixtures/routing_ground_truth.json` has 7 rows matching `STAGES`.
  - **Files to Create/Modify:**
    - `src/atlas/stages.py` — stage table.
    - `tests/fixtures/routing_ground_truth.json` — fixture.
  - **Dependencies:** —
  - **Testing Requirements:** Unit (`test_routing_fixture_match.py`).

* **T1.2 — `RunContext` + `GateDecision` + `StageOutcome` dataclasses** [Effort: S]
  - **Description:** Implement frozen dataclasses in `src/atlas/orchestrator.py`.
  - **Acceptance Criteria:**
    - [ ] All three are `@dataclass(frozen=True)`.
    - [ ] Full type annotations; `mypy src --strict` clean.
  - **Files to Create/Modify:**
    - `src/atlas/orchestrator.py` (initial).
  - **Dependencies:** —
  - **Testing Requirements:** Unit (smoke).

* **T1.3 — `StateStore` (tasks.md + .atlas/current-run, read/write/consistency)** [Effort: M]
  - **Description:** Atomic write for tasks.md (`.tmp` + rename). `assert_consistent()` raises on `run_id` mismatch.
  - **Acceptance Criteria:**
    - [ ] `create_tasks_md` produces a parseable `## current` block + 7 unchecked boxes.
    - [ ] `first_unchecked` returns the right `StageName`.
    - [ ] `assert_consistent` raises `StateInconsistencyError` with both `run_id` values in the message.
    - [ ] tasks.md writes are atomic (no partial files possible).
  - **Files to Create/Modify:**
    - `src/atlas/state.py`.
  - **Dependencies:** T1.2.
  - **Testing Requirements:** Unit (consistency, atomicity, parse round-trip).

* **T1.4 — `Pipeline` with stub `StageRunner` + `FakePrompter`** [Effort: M]
  - **Description:** `start`, `resume`, `step`, `run_to_completion` working end-to-end with stubs. No plumb yet — use a no-op `PlumbIO`.
  - **Acceptance Criteria:**
    - [ ] One `start` + 7 `step()` calls walks the full pipeline.
    - [ ] Gate-rejection path closes the run.
    - [ ] Resume after a simulated process restart finds the right unchecked box.
  - **Files to Create/Modify:**
    - `src/atlas/orchestrator.py` (full).
  - **Dependencies:** T1.1, T1.3.
  - **Testing Requirements:** Unit (`test_pipeline_step_advances_on_approve`, `test_pipeline_resume_after_compaction`, `test_pipeline_step_writes_example_on_reject` — example write stubbed for now).

**Phase Deliverables:**
- Walking pipeline against stubs.
- Routing fixture + assertion at construction time.
- Resume protocol verified.
- All Phase 1 unit tests passing.

---

## Phase 2 — plumb integration

**Objective:** Real spans, real scores, real run rows in plumb.

**Tasks:**

* **T2.1 — `PlumbIO` wrapper** [Effort: M]
  - **Description:** Open the run as a context manager (per plumb API ref); expose `record_span`, `record_user_signal`, `write_example`. Hold the `RunHandle` as state. Adjust to plumb's "buffer once with status" model (per plan §5.2 note).
  - **Acceptance Criteria:**
    - [ ] `record_span` produces a `spans` row with the right `kind`, `status`, `latency_ms`.
    - [ ] `record_user_signal` produces a `scores` row with `scorer="user_signal"`, `value_label`, `span_id`.
    - [ ] `write_example` produces an `examples` row referencing `origin_run_id` + `origin_span_id`.
    - [ ] On orchestrator exception, the `with` exit sets `runs.status="failure"`.
  - **Files to Create/Modify:**
    - `src/atlas/plumb_io.py`.
    - `pyproject.toml` (pin plumb commit SHA).
  - **Dependencies:** Phase 1.
  - **Testing Requirements:** Unit (against in-memory plumb).

* **T2.2 — Wire plumb into `Pipeline.step`** [Effort: S]
  - **Description:** Replace the stub `PlumbIO` with the real one. Verify `add_span` timing — span must not be buffered until the stage's outcome is known.
  - **Acceptance Criteria:**
    - [ ] One full happy-path run produces exactly 7 spans + 5 user-signal scores (gate 4 + final gate counted separately; see plan §5.2).
    - [ ] Run row closes with `status="success"`.
  - **Files to Create/Modify:**
    - `src/atlas/orchestrator.py`.
  - **Dependencies:** T2.1.
  - **Testing Requirements:** Integration (`test_pipeline_writes_full_span_tree`).

* **T2.3 — Examples row on rejection** [Effort: S]
  - **Description:** On reject, build an `Example(...)` and write it via plumb's storage adapter (per plumb API ref §"Recording Examples").
  - **Acceptance Criteria:**
    - [ ] `examples` row written with `origin_run_id`, `origin_span_id`, `inputs_hash`, `expected_output_hash`.
    - [ ] Hashes are 64-char lowercase hex SHA256.
  - **Files to Create/Modify:**
    - `src/atlas/plumb_io.py`.
    - `src/atlas/orchestrator.py`.
  - **Dependencies:** T2.2.
  - **Testing Requirements:** Unit + integration.

**Phase Deliverables:**
- A run produces a real span tree in `~/.plumb/plumb.db`.
- Rejection produces an `examples` row.
- Integration test asserting full span/score shape passes.

---

## Phase 3 — Worktree + gate-4 hand-off

**Objective:** Stage 5 runs inside a worktree; gate 4 is written by the hook (not the orchestrator); main is never touched.

**Tasks:**

* **T3.1 — `WorktreeManager`** [Effort: M]
  - **Description:** `git worktree add` under `.atlas/worktrees/<slug>-<short_run_id>`. Path-containment assertion. `merge_back` and `cleanup` for run completion / abort.
  - **Acceptance Criteria:**
    - [ ] Worktree path is always under `repo_root/.atlas/worktrees/`.
    - [ ] On dirty repo or path collision, raises `WorktreeError`.
    - [ ] `cleanup` removes the worktree without touching `main`.
  - **Files to Create/Modify:**
    - `src/atlas/worktree.py`.
  - **Dependencies:** Phase 1.
  - **Testing Requirements:** Unit (subprocess mocks) + integration (real git).

* **T3.2 — `Pipeline.step` hand-off for stage 5** [Effort: S]
  - **Description:** When entering stage 5, create the worktree and return `awaiting_hook`. Don't write `gate_commit` ourselves.
  - **Acceptance Criteria:**
    - [ ] Stage 5 step does not write a `gate_commit` user_signal score.
    - [ ] `step()` re-entered after the hook fires advances correctly.
  - **Files to Create/Modify:**
    - `src/atlas/orchestrator.py`.
  - **Dependencies:** T3.1.
  - **Testing Requirements:** Unit.

* **T3.3 — Main-branch isolation test** [Effort: S]
  - **Description:** Integration test: capture `git log main` before `Pipeline.start`, run through gate 3, inspect `git log main` after — must be byte-identical (TRD §Mandatory tests).
  - **Acceptance Criteria:**
    - [ ] Test exists in CI.
    - [ ] Test fails if a regression sneaks a commit onto main.
  - **Files to Create/Modify:**
    - `tests/integration/test_main_branch_isolation.py`.
  - **Dependencies:** T3.2.
  - **Testing Requirements:** Integration.

**Phase Deliverables:**
- Stage 5 runs in a worktree.
- Gate 4 is hook-written.
- Main-branch isolation enforced by CI.

---

## Phase 4 — Real plugin invocation + error paths

**Objective:** Replace stub `StageRunner` with one that actually invokes plugins via subprocess, and harden every error path enumerated in plan §7.

**Tasks:**

* **T4.1 — `SubprocessStageRunner`** [Effort: M]
  - **Description:** List-form `subprocess.run(..., capture_output=True, check=False, timeout=...)`. Per-stage timeout from config (default 600s plan, 1800s code_gen).
  - **Acceptance Criteria:**
    - [ ] All `subprocess.run` calls are list-form (test asserts).
    - [ ] Non-zero exit → `StageOutcome.status="failure"`, `error_type="plugin_nonzero_exit"`.
    - [ ] `TimeoutExpired` → `error_type="plugin_timeout"`.
  - **Files to Create/Modify:**
    - `src/atlas/orchestrator.py` (or new `src/atlas/runner.py` if it grows).
  - **Dependencies:** Phase 3.
  - **Testing Requirements:** Unit.

* **T4.2 — Gate prompt re-prompt + abort** [Effort: S]
  - **Description:** `ClickPrompter` (Typer-based) that re-asks 3x on unparseable input then aborts via `r.abort("gate_input_unparseable")`. Length-clamp reason to 4 KB.
  - **Acceptance Criteria:**
    - [ ] 3rd unparseable input aborts run.
    - [ ] `q` aborts cleanly.
    - [ ] Reason longer than 4 KB is truncated and the truncation is recorded in `rationale`.
  - **Files to Create/Modify:**
    - `src/atlas/orchestrator.py` (or a `prompter.py`).
  - **Dependencies:** Phase 1.
  - **Testing Requirements:** Unit.

* **T4.3 — Subprocess argument allow-list** [Effort: S]
  - **Description:** Resolve plugin names against an allow-list before invocation; reject anything that isn't in the routing fixture.
  - **Acceptance Criteria:**
    - [ ] Unknown plugin name raises `RoutingDriftError` before `subprocess.run` is called.
  - **Files to Create/Modify:**
    - `src/atlas/orchestrator.py`.
  - **Dependencies:** T4.1.
  - **Testing Requirements:** Unit.

**Phase Deliverables:**
- Real plugin invocation working.
- Every plan §7 error scenario covered by a test.
- All plan §10 unit tests passing.

---

## Phase 5 — End-to-end real run + release gates

**Objective:** Day-5 in TRD: one real run on a Flask cache-middleware target. All five §"Success Criteria" hold.

**Tasks:**

* **T5.1 — E2E happy path** [Effort: L]
  - **Description:** Run `atlas run "add response-cache middleware"` on a throwaway Flask repo. Verify all 5 TRD success criteria.
  - **Acceptance Criteria:**
    - [ ] One `runs` row, status `success`.
    - [ ] 7 spans in the expected order.
    - [ ] 6 user-signal scores.
    - [ ] `git log main` unchanged across run.
    - [ ] Routing fixture passes.
    - [ ] Resume protocol verified mid-run.
  - **Files to Create/Modify:** none (the test target is the system itself).
  - **Dependencies:** Phases 1–4 complete.
  - **Testing Requirements:** E2E (manual, gated on the v1.0 tag).

* **T5.2 — Coverage + lint gates** [Effort: S]
  - **Description:** `pytest --cov=src.atlas --cov-fail-under=80`, `ruff check`, `ruff format --check`, `mypy src` all CI-required.
  - **Acceptance Criteria:**
    - [ ] CI fails if coverage drops below 80% on `atlas.pipeline`.
    - [ ] CI fails on any ruff or mypy violation.
  - **Files to Create/Modify:**
    - `.github/workflows/ci.yml` (or equivalent).
    - `pyproject.toml` (ruff/mypy config).
  - **Dependencies:** Phase 4.
  - **Testing Requirements:** N/A (CI infra).

* **T5.3 — Tag v1.0** [Effort: S]
  - **Description:** All success criteria green → tag.
  - **Acceptance Criteria:**
    - [ ] Tag pushed.
    - [ ] STATUS.md updated.
  - **Files to Create/Modify:**
    - `STATUS.md`.
  - **Dependencies:** T5.1, T5.2.
  - **Testing Requirements:** N/A.

**Phase Deliverables:**
- v1.0 shipped.
- All TRD success criteria verified on a real run.

---

## Pending Decisions & Clarifications

These came up during this TRS and need user input before the relevant phase starts. Listed in order of urgency.

### D1 — Plugin command resolution shape (Phase 4 blocker)

The orchestrator needs a way to map a `StageSpec.tool` string (e.g. `"consult-experts:pm"`) to an actual subprocess argv. The PRD/TRD don't specify how plugins are invoked at the shell.

- **Option A — Slash-command shell wrapper.** Atlas invokes `claude --slash consult-experts:pm "<task>"` (or whatever the agent CLI exposes). *Pros:* aligns with how the user would invoke them manually. *Cons:* tight coupling to a specific agent CLI; tool resolution becomes "whatever that CLI does."
- **Option B — Direct plugin entry-point invocation.** Each plugin exposes a command in `~/.claude/plugins/<name>/bin/<entry>`; atlas invokes that directly. *Pros:* fewer layers; testable. *Cons:* requires plugin authors to expose entry points; not how DEV-ESSENTIALS ships today.
- **Option C — A small `atlas/plugin_resolver.py` mapping table.** Hard-code the four plugin commands atlas needs in a config-overridable dict. *Pros:* simplest, ~10 LoC. *Cons:* drift if plugin shapes change (mitigated by routing fixture).

**Recommendation:** Option C. It matches the "state machine, not a framework" line and keeps the LoC budget intact. Option A is a v1.1 thing once the agent CLI surface stabilizes.

### D2 — `examples.expected_output` semantics on rejection (Phase 2)

Per PRD §"Gate rejection path", the rejected artifact is the *input* half of a future paired example, with the corrected output filled in on the next approval. Plumb's `Example` type takes `inputs_hash` + `expected_output_hash` at write time.

- **Option A — Write `examples` row with `expected_output_hash=None` on rejection; backfill on next approval.** Requires plumb to allow null `expected_output_hash` (the plumb API ref doesn't specify nullability).
- **Option B — Write `examples` row only on the *corrected* approval, capturing both halves at once.** Simpler; loses the "rejection captured at zero marginal cost" property the PRD wants.
- **Option C — Write two examples rows: rejection (input only) and approval (input + corrected output), linked via a v1.1 column.** Speculative, kicks the can.

**Recommendation:** Confirm with plumb's author whether `expected_output_hash` can be null in the schema. If yes, A. If no, B (and revisit at v1.1).

### D3 — Per-stage timeout defaults (Phase 4)

The orchestrator's stage timeouts are in `.atlas.toml`. Defaults proposed: 600s for plan stages, 1800s for code_gen. Code_gen timeout in particular is a guess.

- **Option A — Use the proposed defaults; let the user tune via config.**
- **Option B — Disable timeout for code_gen entirely; rely on the user quitting via `q`.**
- **Option C — Make all stage timeouts user-configurable with no defaults; require the user to set them in `.atlas.toml` before first run.**

**Recommendation:** A. The numbers are guesses but they bound the worst case (a runaway plugin loop). Real-world adjustment after the Day-5 run.

### D4 — Whether to ship Phase 4's `prompter.py` as a separate file (Phase 4)

If `ClickPrompter` + abort handling exceeds ~30 LoC inside `orchestrator.py`, it should split into `src/atlas/prompter.py`. Mentioned because it bumps file count and would normally trigger CLAUDE.md's "new file type is a design-review trigger."

- **Option A — Keep in `orchestrator.py`** if it fits; split only if it grows past ~30 LoC.
- **Option B — Split now** for testability symmetry with the other collaborators.

**Recommendation:** A — judge during implementation. The CLAUDE.md rule targets *new conceptual surfaces*, not pure refactor.
