# Plan Review — Atlas Pipeline TRS ("add cache middleware" task target)

**Review date:** 2026-05-05
**Reviewer:** Senior Technical Plan Reviewer
**Plan reviewed:** `dev/active/atlas-pipeline-trs/atlas-pipeline-trs-plan.md` (+ phases, tasks, context documents)
**Implementation reviewed:** All files in `src/atlas/` plus `tests/`
**Review scope:** The complete atlas pipeline TRS, its implementation status, and the pending T5.1 manual E2E run against the "add response-cache middleware" Flask target.

---

## 1. Executive Summary

The atlas pipeline TRS is well-structured and architecturally sound. The plan's scope discipline (state machine, not a framework), single-responsibility modules, and phased delivery are genuinely good engineering decisions. Phases 1–4 have been implemented and CI is green.

However, three issues in the current implementation are severe enough to block the T5.1 manual E2E run from producing a valid result. Two of these were previously identified in the existing code-review document (`atlas-pipeline-trs-code-review.md`) and are marked as critical there but have not yet been addressed in committed code — the git diff confirms the working-tree changes are only to the plugin invocation mechanism, not the state propagation or hook durability bugs. Additionally, the plugin invocation change introduced by the working-tree diff adds a new critical correctness problem of its own.

Recommended action: address the three critical issues before running T5.1 manual. All three are localized fixes (< 10 LoC each). The remaining findings are medium-severity or lower and do not block the E2E gate.

---

## 2. Critical Issues

### C1 — Worktree path not propagated to stage 6 (code_review runs on main)

**Location:** `src/atlas/orchestrator.py`, `Pipeline.step()`, lines 177–190

**Problem:** During stage 5 (code_gen), `Pipeline.step()` creates a new local `RunContext` with `worktree_path` set and persists it to `.atlas/current-run` via `write_current_run(ctx.run_id, ctx.slug, worktree_path)`. However, the updated context is **not returned to the caller** — `step()` returns `StageOutcome`, not `RunContext`. The caller's `ctx` variable in `run_to_completion` is never updated. When stage 6 runs in the next `step()` call, the `ctx` passed in still has `worktree_path=None`, so `SubprocessStageRunner.run()` at line 379 selects `ctx.repo_root` as `cwd`.

**Consequence:** The code_review stage (stage 6) inspects `main`, not the generated code in the worktree. TRD criterion 4 (main unchanged) may pass, but the code review is evaluating the wrong content, making the entire review stage meaningless. This is a silent correctness failure — no error is raised, tests pass, but the semantic outcome is wrong.

**Why tests don't catch it:** The E2E test (`test_e2e_happy_path.py`) stubs the runner with `_ApproveAllRunner` which ignores `cwd`. Unit tests mock subprocess entirely. No test asserts that the `cwd` passed to the runner for stage 6 is the worktree path.

**Note:** `resume()` correctly reads `worktree_path` from `.atlas/current-run` (line 94–96 of `state.py`, reconstructed in `orchestrator.py` line 139–146). The bug only affects an in-process `run_to_completion` loop — after a process restart the resumed ctx has the correct path. This makes the bug intermittent from a user perspective, which is worse than a consistent failure.

**Fix direction:** Either propagate `worktree_path` into `run_to_completion`'s loop variable (requires `step()` to return `RunContext` alongside the outcome, or use a mutable reference), or re-read `.atlas/current-run` at the top of each `step()` call to always have the current-on-disk path.

---

### C2 — `code-gen-agent` is mapped to `DEV-ESSENTIALS:dev-docs-be` in plugin_resolver

**Location:** `src/atlas/plugin_resolver.py`, line 26

**Problem:** The working-tree changes (visible in the git diff) map `"code-gen-agent"` to `"DEV-ESSENTIALS:dev-docs-be"`. This means the stage 5 code generation step silently dispatches to the docs-generation skill rather than a code generation agent. The plugin_resolver comment even acknowledges this ("no dedicated plugin — use claude directly with the task description") but then names the wrong fallback.

**Consequence:** The code generation stage for the "add response-cache middleware" task will invoke the TDS/docs-gen skill on a Flask repo rather than an agent that writes code. The worktree will receive documentation output, not implementation. If the agent exits 0, the pipeline will continue and the code_review stage will "review" documentation as if it were code. The run closes `success` but produces no working cache middleware.

**Why tests don't catch it:** `SubprocessStageRunner` is mocked in all unit and E2E tests. The routing fixture only validates that the `stage.tool` key exists in the allow-list; it does not validate the mapped command value against any ground truth.

**Fix direction:** Either establish a real `code-gen-agent` plugin or map `"code-gen-agent"` to the correct invocation. If using `claude` directly without a slash command, the invocation shape needs adjustment (the `-p "/<cmd> ..."` form requires a recognized slash command; an unrecognized command will produce an error or unexpected behavior from the claude CLI).

---

### C3 — Post-commit hook path resolution is fragile in worktree context

**Location:** `src/atlas/post_commit_hook.py`, `_main_repo_root()`, lines 28–46

**Problem:** The hook uses `git rev-parse --git-common-dir` to find the shared `.git` directory, then infers the main repo root as `common_dir.parent`. In a standard worktree, `--git-common-dir` returns a path like `/path/to/main/.git` (absolute) or `.git` (relative, when called from the main checkout). However, from within a worktree (which is the hook's execution context), `--git-common-dir` returns the path to the shared object store, which may be `/path/to/main/.git` — but the code assumes `common_dir.name == ".git"` to detect this case (line 44). If the `.git` directory has a non-standard name (e.g., due to git configuration) or if `--git-common-dir` returns a gitdir file reference instead of a directory, the parent traversal produces the wrong root.

More concretely: when running from a worktree at `.atlas/worktrees/cache-middleware-abcd1234/`, `git rev-parse --git-common-dir` returns something like `../../.git` (relative). The code at line 40–42 resolves it against `Path.cwd()`, but `cwd` inside a git hook is the repo root of the **worktree**, not the main repo. The `.atlas/` directory does not exist in the worktree (only in the main repo), so `pending_path.parent.mkdir(parents=True, exist_ok=True)` at line 78 silently creates `.atlas/` inside the worktree root instead of in the main repo.

**Consequence:** `pending-scores.jsonl` is written to the worktree's `.atlas/` directory. The orchestrator's flush at `src/atlas/orchestrator.py` lines 161–166 checks `self._repo_root / ".atlas" / "pending-scores.jsonl"`, which points to the main repo. It finds nothing. The `gate_commit` score is permanently lost, and criterion 3 (6 scores) will have only 5. The T5.1 manual run will fail criterion 3.

**The existing code-review document (finding #2) identified the general category of this problem** but attributed it to a `PlumbIO` handle issue. The pending-scores.jsonl design resolves that handle issue, but the path resolution bug remains.

**Fix direction:** Resolve the absolute path of the main repo by always using `git rev-parse --absolute-git-dir` combined with checking whether it's a worktree-local `.git` file (which would then contain `gitdir: <main-repo>/.git/worktrees/<name>`). Alternatively, after resolving `common_dir`, use `git -C <resolved-path> rev-parse --show-toplevel` to get the definitive repo root.

---

## 3. Missing Considerations

### M1 — Plugin invocation contract with `claude -p` is unverified

**Finding:** The working-tree change replaces `claude --slash <plugin> --context <file> --task <task>` with `claude -p "/<plugin_cmd> <task>\n\nContext file: <path>"`. The `-p` flag is the "print" or "prompt" mode of the claude CLI. The slash command format `/<cmd>` inside a `-p` prompt assumes the claude CLI parses inline slash commands from the prompt text.

This assumption has not been verified against the actual claude CLI. The T5.1 manual test recipe preflight at line 27 still references the old invocation shape (`["claude", "--slash", plugin_cmd, "--context", tasks_md, "--task", ctx.task]`). The recipe is now stale relative to the working-tree implementation.

**Risk:** If `claude -p "/consult-experts <task>"` does not dispatch to the `consult-experts` skill (because the CLI requires a different invocation for skill commands), every stage will fail with `plugin_nonzero_exit`. The T5.1 run cannot complete.

**Recommendation:** Verify `claude -p "/consult-experts <simple prompt>"` resolves correctly before running T5.1. Update the T5.1 recipe's preflight section to match the new invocation form.

---

### M2 — `--add-dir` argument behavior with the claude CLI is unspecified

**Finding:** The new invocation adds `"--add-dir", str(ctx.repo_root)`. This flag is not documented in the plan, TRS, or any design document. If this flag does not exist in the installed claude CLI version, every stage will fail with a non-zero exit code due to an unrecognized argument.

**Risk:** Silent failure on every stage. Difficult to debug without examining stderr, which is captured but only surfaced when `outcome.status == "failure"`.

---

### M3 — `_last_code_gen_span_id` state breaks the "Pipeline holds no run state" invariant

**Finding:** The TRS plan (section 3.3) states: "Pipeline is the only stateful class; it holds collaborators by reference but no run state — every call takes a RunContext." The implementation at `orchestrator.py` line 110 adds `self._last_code_gen_span_id: str = ""` as instance state on the Pipeline.

This breaks the resume contract: if the orchestrator process restarts after stage 5 completes and a new `Pipeline` instance is created for `resume()`, `_last_code_gen_span_id` will be `""`. The `flush_pending_scores` call at line 165–166 will flush hook scores with `span_id=""` instead of the actual code_gen span_id, creating an orphaned score row not attributed to any span.

**Risk:** After a process restart during the code_review stage, the `gate_commit` score is written with a blank `span_id`. Plumb queries linking scores to spans will fail to find this score. Criterion 3 appears to pass (row exists) but the data is malformed.

**Recommendation:** Persist `_last_code_gen_span_id` to `.atlas/current-run` (e.g., as a 4th line) and read it back during `resume()` / `read_current_run_with_worktree()`. Alternatively, derive the span_id at flush time by querying plumb for the most recent `code_gen` span.

---

### M4 — No cleanup of worktree on run failure, rejection, or abort

**Finding:** `run_to_completion` closes the run and deletes `.atlas/current-run` on failure or rejection (lines 312–315), but it does not call `self._worktree.cleanup()`. If stage 5 has started (worktree created) and then stage 6 fails or is rejected, the worktree directory persists at `.atlas/worktrees/<slug>-<short_run_id>/`. The next `atlas run` on the same task will generate the same slug; if the run_id's first 8 chars collide (low probability but possible), `worktree.create()` will raise `WorktreeError` due to the existing path. More commonly, the abandoned worktrees accumulate silently.

**The plan (section 7)** documents `WorktreeError` handling but does not specify when `cleanup` is called. `worktree.py` implements `cleanup` but nothing calls it on the failure/rejection paths.

**Recommendation:** Add cleanup calls in `run_to_completion` for failure/rejection paths when `ctx.worktree_path is not None`. Wrap in a try/except to avoid masking the original failure.

---

### M5 — `AbortedError` from `ClickPrompter` is not caught in `run_to_completion`

**Finding:** `ClickPrompter.ask()` raises `AbortedError` on user-quit or 3 bad inputs. The plan (section 7) specifies the pipeline should "mark run aborted via `r.abort('gate_input_unparseable')`". The `run_to_completion` method at lines 306–317 does not catch `AbortedError`. The exception propagates up to `cli.py` at line 84, which catches it and exits with code 1 — but `plumb.close_run()` and `state.delete_current_run()` are never called.

**Consequence:** The plumb run row is left in `pending` status permanently. `.atlas/current-run` persists. `atlas status` will show the aborted run as active. `atlas resume` will attempt to resume an effectively dead run.

**Recommendation:** Catch `AbortedError` in `run_to_completion`, call `plumb.close_run(status="aborted")` and `state.delete_current_run()`, then re-raise.

---

### M6 — `plumb_io.write_example` does not actually write to plumb when `real=True`

**Finding:** In `plumb_io.py`, `write_example()` constructs an `Example` object correctly when `real=True and _PLUMB_AVAILABLE` (lines 204–225), but then appends it to `self.examples` (in-memory list) instead of calling the plumb storage adapter. The comment says "In Phase 2 this would go through the storage adapter" — this is a Phase 2 to-do that was never completed. The `examples` in-memory list is only useful in stub mode.

**Consequence:** Gate rejection examples are never durably persisted to plumb, even in production mode. This silently violates D2 (Decision 2 in the phases document) and FR-7 from the TRS.

---

### M7 — Routing fixture validation at `Pipeline.__init__` adds 20–30ms to every `atlas status` call

**Finding:** The plan (section 11) specifies `Pipeline.__init__` must complete in < 100ms cold. However, `atlas status` in `cli.py` (line 121) does NOT construct a `Pipeline` — it reads state directly from `StateStore`. This is correct. However, `atlas run` and `atlas resume` both construct `Pipeline` at lines 74 and 99, and `_validate_routing_fixture()` reads and parses a JSON file on every construction. In practice this is fast, but the plan's "< 100ms" claim at Pipeline init should be verified against the routing fixture read latency on a slow filesystem. This is a minor point but worth flagging given the plan's explicit performance budget.

---

## 4. Alternative Approaches

### A1 — Avoid in-process span_id state by storing it in current-run

Rather than `_last_code_gen_span_id` as Pipeline instance state (breaking the "no run state in Pipeline" invariant), the span_id could be the 4th line in `.atlas/current-run`. This would make resume trivially correct for the pending-scores flush, maintain the design invariant, and cost exactly 1 write (the existing `write_current_run` call at line 190 would include it). The `read_current_run_with_worktree()` method would return a 4-tuple.

### A2 — Replace the pending-scores.jsonl intermediary with a direct hook-to-state handshake

The current design (hook writes JSONL, orchestrator flushes on next `step()`) adds complexity: two write paths to `.atlas/`, path resolution bugs in the hook, and the span_id attribution problem. A simpler alternative for v1: the hook writes a single flag file (e.g., `.atlas/gate-commit-approved`) rather than structured JSON. The orchestrator's `step()` checks for this file, uses the already-known `_last_code_gen_span_id` (or reads from `.atlas/current-run`), and writes the plumb score in-process. This eliminates the JSON-parsing, the run_id matching, and the path resolution surface area.

### A3 — Make `run_to_completion` return a `tuple[RunContext, list[StageOutcome]]`

Currently `run_to_completion` discards all intermediate outcomes. The CLI has no visibility into which stage failed or what output was produced. Returning the list of outcomes would make the CLI output more informative (e.g., "Stage 3 rejected: reason") without breaking any contracts. The current return of `RunContext` alone gives the caller no way to distinguish "completed" from "awaiting_hook" without inspecting disk state.

---

## 5. Implementation Recommendations

### R1 — Fix C1 immediately: propagate worktree_path through the step loop

The simplest fix is to make `run_to_completion` re-read the current context from disk at the top of each iteration, since `write_current_run` is already called with the worktree path during stage 5. This matches the "state lives in files" principle. Alternatively, return an updated `RunContext` from `step()` as part of a named tuple or by mutating a local variable inside `run_to_completion`.

### R2 — Verify the claude CLI invocation shape before the T5.1 run

Run `claude -p "/consult-experts test" --no-session-persistence` manually and confirm the output is the skill's output, not an error. If the CLI does not support inline slash commands in `-p` mode, the invocation needs to revert to the original `--slash` form or use a different dispatch mechanism.

### R3 — Fix the pending-scores.jsonl path resolution in the hook

Use `git rev-parse --absolute-git-dir` inside the hook (which works correctly from a worktree) and derive the main repo root from the absolute gitdir path by stripping `/worktrees/<name>` if present. This is a well-known pattern for worktree-aware hook scripts.

### R4 — Add worktree cleanup on all run-close paths

In `run_to_completion`, before the `plumb.close_run()` call on failure/rejection/abort paths, call `self._worktree.cleanup(ctx.worktree_path)` when `ctx.worktree_path is not None` and `self._worktree is not None`. Log cleanup failures as warnings but do not re-raise them.

### R5 — Catch `AbortedError` inside `run_to_completion`

Add an `except AbortedError` block that calls `plumb.close_run(status="aborted")`, calls `state.delete_current_run()`, and re-raises. This ensures plumb and filesystem state are consistent on abort.

### R6 — Complete the `write_example` plumb integration or document it as deferred

Either remove the `if self._real` branch in `write_example` (making it always append to the in-memory list) and document that it's not yet durable, or implement the actual storage adapter call. The current code is misleading: it constructs a real `Example` object but then ignores the plumb adapter path.

---

## 6. Risk Mitigation

### For the T5.1 manual E2E run:

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Stage 6 reviews main instead of worktree (C1) | Certain (code path confirmed) | Fix C1 before running T5.1; or manually verify `cwd` in stderr output |
| `code-gen-agent` dispatches to docs-gen (C2) | Certain (mapping confirmed) | Fix C2 before running T5.1 |
| Criterion 3 fails: 5 scores instead of 6 (C3) | High (path resolution bug in hook) | Fix C3 before running T5.1; manual verification: `ls -la $TARGET/.atlas/pending-scores.jsonl` immediately after a worktree commit |
| `claude -p "/<cmd>"` invocation fails (M1) | Medium | Smoke-test the invocation manually before the full run |
| `--add-dir` flag not recognized by claude CLI (M2) | Medium | Test `claude --help` for flag existence; remove flag if absent |
| AbortedError leaves run in open state (M5) | Low for E2E but realistic in prod | Fix M5 before v1.0 tag; acceptable for manual T5.1 if tester avoids `q` |

### For v1.0 release:

The following must be resolved before tagging:
- C1, C2, C3 (correctness blockers)
- M3 (span attribution correctness after process restart)
- M4 (worktree cleanup; currently leaves filesystem debris)
- M5 (run state cleanup on abort)
- M6 (write_example is effectively a no-op in prod; violates FR-7)

---

## 7. Research Findings

### 7.1 `claude` CLI invocation shape

The working-tree diff changes the subprocess invocation from `["claude", "--slash", plugin_cmd, "--context", str(tasks_md), "--task", ctx.task]` to `["claude", "-p", prompt, "--no-session-persistence", "--add-dir", str(ctx.repo_root)]`. The T5.1 manual test recipe (lines 26–27) still documents the old invocation shape, creating a discrepancy between what the recipe says to verify and what the code actually does. The recipe's preflight check (`claude --slash <name>`) will not validate the new `-p "/<cmd>"` mode.

### 7.2 `git rev-parse --git-common-dir` behavior in worktrees

In a git worktree, `git rev-parse --git-common-dir` returns the path to the shared object store (typically `<main-repo>/.git`). When called from the worktree directory, this is an absolute path on most git versions >= 2.5. However, when called from the main repo directory, it returns `.git` (relative). The hook code handles this with the `not common_dir.is_absolute()` check at line 38–41. The critical point is that `Path.cwd()` inside a post-commit hook run from a worktree is the **worktree root** (the worktree's `core.worktree`), not the main repo root. The relative-path resolution guard at lines 40–42 is correct for the main-repo case but does not help with the worktree case, because `--git-common-dir` from a worktree returns an absolute path (bypassing that guard entirely) to a path whose `.parent` is the main repo root — which is actually correct. The real risk is the `pending_path.parent.mkdir` call resolving relative to the wrong root, which only occurs if the absolute-path case ever returns a non-`.git`-named directory.

After careful review: the absolute path case at line 44 (`if common_dir.name == ".git": return common_dir.parent`) is likely correct when `--git-common-dir` returns an absolute path from a worktree. The risk is lower than initially assessed for standard git setups. However, if the main repo's `.git` is a file (as in submodules), `common_dir.name` will not be `.git` and the fallback at line 46 (`return common_dir.parent if common_dir.parent.exists() else None`) will return the parent of the gitdir's parent — likely wrong. The `.git`-as-file case is not tested.

### 7.3 Plumb "never raises" contract

The TRS plan (section 7) states "plumb write fails internally — plumb logs internally; never raises (per plumb API ref)." However, `plumb_io.py`'s `close_run()` wraps `__exit__` in a bare `except Exception: pass` (line 74). This silences any exception plumb might raise during context-manager exit. If plumb has changed its contract, this silent swallowing of errors means the orchestrator will report `success` to the user even if the plumb row is in an undefined state. The defensive `try/except` is correct given the stated contract, but should log the exception rather than silently discarding it.

### 7.4 LoC budget status

The context document estimated 335 total LoC. The actual implementation as committed is larger: `orchestrator.py` alone is 492 lines (significantly over the 110 estimated). This puts total LoC well above the 350 upper bound stated in the tasks document. This does not affect correctness but violates NFR-1 from the TRS and the CLAUDE.md coding-style constraint (files < 400 lines target). The `SubprocessStageRunner` and `ClickPrompter` classes could be split to `runner.py` and `prompter.py` per the plan's own D4 decision criteria ("split only if it grows past ~30 LoC" — both classes are well past 30 LoC individually).

---

## Appendix — Issue Priority Matrix

| ID | Severity | Blocks T5.1 Manual | Blocks v1.0 Tag | LoC to Fix |
| --- | --- | --- | --- | --- |
| C1 — worktree_path not propagated to stage 6 | Critical | Yes | Yes | ~5 |
| C2 — code-gen-agent maps to dev-docs-be | Critical | Yes | Yes | ~1 |
| C3 — hook path resolution fragile | Critical | Yes | Yes | ~10 |
| M1 — claude -p slash invocation unverified | Critical (operational) | Yes | Yes | 0 (verification only) |
| M2 — --add-dir flag unverified | High | Yes | Yes | ~1 if removal needed |
| M3 — _last_code_gen_span_id lost on restart | High | No | Yes | ~8 |
| M4 — worktree not cleaned up on failure | High | No | Yes | ~5 |
| M5 — AbortedError not caught in run_to_completion | High | No | Yes | ~6 |
| M6 — write_example not durable in prod | Medium | No | Debatable | ~10 |
| M7 — LoC budget exceeded | Low | No | No | Refactor |
