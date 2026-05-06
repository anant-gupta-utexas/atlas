# Plan Review — Add Response-Cache Middleware

**Task:** `atlas run "add response-cache middleware to this Flask repo"`
**Reviewer Role:** Senior Technical Plan Reviewer
**Review Date:** 2026-05-05
**Branch:** `claude/brave-lamport-fda4f2`
**Scope:** The plan to run this task through the atlas v1 pipeline, plus the two uncommitted diffs in `orchestrator.py` and `plugin_resolver.py` that represent a mid-flight correction to how plugins are invoked.

---

## 1. Executive Summary

The "add response-cache middleware" task is a sound and intentionally trivial v1.0 target — the PRD and TRD both name exactly this scenario as the Week 4 real-run target. The pipeline architecture is appropriate for the problem. However, **the two uncommitted diffs that reconfigure how plugins are invoked contain critical correctness and consistency issues that will prevent a successful T5.1 manual run if merged as-is**. Separately, several architectural issues identified in the prior code review (`atlas-pipeline-trs-code-review.md`) remain unresolved and are not addressed by the current diffs. The task as described is implementable, but the pipeline has known broken paths that will be hit during a real run.

Verdict: **not ready to start T5.1 manual gate** until the issues below are resolved.

---

## 2. Critical Issues

### 2.1 Plugin Command Diff Introduces a Stale `--slash` Reference in the Test Recipe

**Severity: Critical (blocks T5.1)**

The `T5.1-manual-test-recipe.md` (line 26) still instructs testers to verify that plugins resolve as `claude --slash <name>` and references the old CLI argument shape:

```
"claude --slash <plugin_cmd> --context <tasks_md> --task <ctx.task>"
```

The uncommitted diff in `orchestrator.py` changes the invocation to:

```
["claude", "-p", prompt, "--no-session-persistence", "--add-dir", str(ctx.repo_root)]
```

The test recipe is now wrong. A tester following it will pre-flight against the wrong CLI interface and may miss that `claude -p` does not exist under that name on their system, or that the `--add-dir` flag requires a specific Claude CLI version. More immediately: if `claude -p` is not the correct CLI entrypoint for their installation, every stage will fail with `plugin_nonzero_exit` and there is no error message in the code that explains the actual command that was attempted.

**What must be verified before T5.1:** confirm the exact Claude CLI interface available on the target machine. The command `claude -p "<prompt>" --no-session-persistence --add-dir <dir>` must actually exist and function. The PR #006b9a9 that introduced the test recipe was written against the old `--slash` interface. If the new interface is correct, the test recipe needs to be updated first.

---

### 2.2 `code-gen-agent` Maps to `DEV-ESSENTIALS:dev-docs-be` — Semantically Wrong

**Severity: Critical (corrupts stage 5 output)**

In the current `plugin_resolver.py` diff:

```python
"code-gen-agent": "DEV-ESSENTIALS:dev-docs-be",
```

`dev-docs-be` is a documentation/plan generation skill (`dev-docs-be` = "backend dev documentation"). Mapping `code-gen-agent` to it means stage 5 (`code_gen`, the only stage that actually writes code) will invoke a documentation generator instead of a code-generation agent. This is not a configuration inconvenience — it changes what work is done in the most important stage of the pipeline.

The comment in the resolver acknowledges this: `"code-gen-agent: no dedicated plugin — use claude directly with the task description"`. If there is no dedicated plugin available, the correct behavior is either to invoke `claude` directly (outside the slash-command path) or raise a `RoutingDriftError` so the user knows to override it in `.atlas.toml`. Silently falling back to a documentation tool produces no code, no file changes, and no commits in the worktree — which means the post-commit hook never fires, `gate_commit` is never written, and the run hangs in `awaiting_hook` status permanently.

**Downstream cascade:** if the worktree has no commits, `git worktree remove --force` in cleanup will leave nothing behind but the `gate_commit` score will also be missing, violating TRD success criterion 3 (6/6 user-signal scores).

---

### 2.3 `plan-reviewer` Maps to `DEV-ESSENTIALS:plan-reviewer` — Existence Unverified

**Severity: Critical (blocks stage 4)**

The diff maps `"plan-reviewer": "DEV-ESSENTIALS:plan-reviewer"`. The comment says this plugin is "invoked via the Agent tool, not a slash cmd." If `DEV-ESSENTIALS:plan-reviewer` does not exist as an installed slash command in the user's Claude environment, stage 4 (`plan_review`) will immediately fail with `plugin_nonzero_exit` and the run will halt before reaching code generation.

The original `plugin_resolver.py` (before the diff) used the bare name `plan-reviewer`. The diff adds the `DEV-ESSENTIALS:` namespace prefix without any verification step in the preflight checklist. The test recipe's preflight step (lines 26-28) does not include `DEV-ESSENTIALS:plan-reviewer` in its list of commands to verify.

---

### 2.4 Unresolved Code Review Finding #1: Stage 6 Runs on `main` Instead of Worktree

**Severity: Critical (breaks main-branch isolation criterion)**

This was identified as Critical in `atlas-pipeline-trs-code-review.md` (finding #1). The uncommitted diffs do not address it. The issue:

`Pipeline.step()` creates a locally-reblit `RunContext` with `worktree_path` set when entering `code_gen`, and this updated context is persisted to `.atlas/current-run` via `write_current_run`. However, after `run_to_completion` returns `awaiting_hook` and the user later calls `atlas resume`, the reconstituted `RunContext` reads the worktree path from disk correctly.

Looking at the actual code in `orchestrator.py` lines 179-190, `write_current_run(ctx.run_id, ctx.slug, worktree_path)` IS called, and `read_current_run_with_worktree()` does read the third line. So the worktree path should survive resume. However, the `code_review` stage (stage 6) at `SubprocessStageRunner.run()` (lines 378-379) uses:

```python
cwd = ctx.worktree_path if ctx.worktree_path is not None else ctx.repo_root
```

This applies for ALL stages, including stage 6. So stage 6 would run in the worktree only if `worktree_path` is still set in `ctx` at that point. Tracing the flow: after `awaiting_hook` returns, `run_to_completion` exits. When the user runs `atlas resume`, `resume()` reads `.atlas/current-run` (which now has 3 lines including the worktree path) and sets `worktree_path` correctly. Stage 6 would then run in the worktree.

**BUT:** after the worktree commits trigger the post-commit hook and the gate-commit score is flushed, stage 6 (`code_review`) should run inside the worktree to review the generated code — not on `main`. This appears to work as designed. The prior code review finding may have been based on an earlier version of the code. However, this should be explicitly re-verified because the test suite does not cover the stage 5 -> stage 6 `cwd` path end-to-end (as noted in the code review document).

---

### 2.5 Unresolved Code Review Finding #4: Checkbox Marked Before Gate Decision

**Severity: High (corrupts resume state on rejection or failure)**

Also from `atlas-pipeline-trs-code-review.md` (finding #4), not addressed by the current diffs. In `orchestrator.py`, the comment on lines 210-213 says:

```
# NOTE: tasks.md checkbox is NOT marked here. We only check the box once
# the gate decision is finalized (success / awaiting_hook / approved) so
# that resume after a failure or rejection re-runs the same stage instead
# of skipping past it.
```

The code does correctly delay `check_box` — it is only called after confirming the outcome is not a failure, and not a rejection. This appears fixed compared to what the code review found. Re-verification against the current code is warranted to confirm this discrepancy.

---

## 3. Missing Considerations

### 3.1 Prompt Injection via `ctx.task` in the New Invocation Shape

The new `prompt` construction (orchestrator diff, lines 385-387) is:

```python
prompt = f"/{plugin_cmd} {ctx.task}\n\nContext file: {tasks_md}"
```

`ctx.task` comes from user-supplied CLI input: `atlas run "add response-cache middleware to this Flask repo"`. This string is interpolated directly into the prompt passed to `claude -p`. If a user's task description contains characters that interfere with slash-command parsing (e.g. `\n`, `/`, backticks) or if the task description itself looks like a prompt injection (e.g. "ignore previous instructions and..."), the plugin receives a malformed command.

This is low-risk in the single-user local tool context, but the task string is never sanitized or quoted. At minimum, the task should be shell-safe and the slash-command prefix should be on its own line, separated from the task description, to reduce ambiguity in how the agent CLI parses the prompt.

### 3.2 `--add-dir` Flag Availability

The new subprocess invocation adds `--add-dir str(ctx.repo_root)`. This flag is only available in specific versions of the Claude CLI. There is no version check or graceful degradation if the flag is absent — the subprocess will exit non-zero and the stage will fail with `plugin_nonzero_exit`, with no indication to the user that the CLI version is the problem.

The T5.1 preflight does not check for `--add-dir` support. Add `claude --help | grep add-dir` to the preflight checklist.

### 3.3 No Worktree Cleanup on Run Abort

The `run_to_completion` method handles `awaiting_hook` by returning early. If the user Ctrl+C's during any other stage, `KeyboardInterrupt` is caught in `cli.py` (line 88) and the process exits without calling `WorktreeManager.cleanup()`. The worktree directory remains under `.atlas/worktrees/`. On a subsequent `atlas run` with the same slug and run ID prefix, `WorktreeManager.create()` would raise `WorktreeError` because the path already exists.

For the cache-middleware task specifically, if the user needs to retry a failed code-gen stage, they must manually run `git worktree remove` before retrying. This is a friction point that should be documented in the failure-mode cheatsheet (which currently covers worktree leftovers only after abort, not after retry).

### 3.4 `pending-scores.jsonl` Race Condition During Multi-Commit Code Gen

The post-commit hook appends one record per commit to `.atlas/pending-scores.jsonl`. The orchestrator flushes this file at the start of each `step()`. If the code-gen agent makes multiple commits (plausible for a middleware feature: initial implementation, then tests, then fixup), the file accumulates multiple records. The flush in `plumb_io.py` (`flush_pending_scores`) processes all matching `run_id` records and writes multiple `gate_commit` scores to plumb.

This means plumb will have more than 6 scores for a single run (one `gate_commit` per worktree commit, not one total), violating TRD success criterion 3 ("6/6 user-signal scores"). The flush logic at `plumb_io.py` lines 161-184 does not deduplicate on metric name. For the T5.1 manual run, this will cause the plumb verification query to return more than 1 row for `gate_commit`, potentially causing confusion about whether criterion 3 passed.

### 3.5 `plumb_io.py` Example Write in `real=True` Mode Does Not Actually Write to plumb

In `plumb_io.py` lines 204-224, the `write_example` method under `real=True` mode creates an `Example` object but then appends it to `self.examples` (the in-memory list) rather than writing it through the plumb storage adapter. The comment says "In Phase 2 this would go through the storage adapter." For the T5.1 manual run with `PlumbIO(real=True)`, gate rejections will not produce durable `examples` rows in plumb's SQLite DB. This is a data completeness issue that may not block T5.1 (examples rows are not one of the five TRD success criteria) but will produce misleading debugging output if the user queries plumb for examples after a rejection.

### 3.6 `latency_ms` Is Always 0.0

`orchestrator.py` line 199: `latency_ms=0.0`. The TRS plan (section 6.2) specifies timing the stage with `monotonic()` and computing `(t1-t0)*1000`. The implementation omits this — all spans will report 0.0 ms latency in plumb. For the cache-middleware run, plumb queries on latency (`spans.latency_ms`) will return zero for all stages, making the measurement data useless. This is not a blocking issue for T5.1 but is a data integrity gap against the TRS spec.

---

## 4. Alternative Approaches

### 4.1 For the `code-gen-agent` Mapping

Rather than mapping `code-gen-agent` to `DEV-ESSENTIALS:dev-docs-be`, the correct alternatives are:

**Option A:** Invoke `claude` directly without a slash command for stage 5, passing the task and tasks.md path as the full prompt. This matches the comment's intent ("use claude directly with the task description") and what the code-gen stage conceptually does.

**Option B:** Require the user to configure `[plugin_commands]` in `.atlas.toml` to override `code-gen-agent` with the correct local plugin before running. Raise a clear `RoutingDriftError` with an actionable message if the mapping points to a known documentation tool.

**Option C:** Treat stage 5 as a special case in `SubprocessStageRunner.run()` that bypasses the slash-command prefix entirely, analogous to how stage 3 has `gate_label=None`.

### 4.2 For Prompt Construction

Rather than embedding `ctx.task` directly in a format string with the slash command, build the prompt as two separate parts: the slash-command invocation on line 1, and the task/context on subsequent lines. This is consistent with how slash commands are typically parsed and reduces the surface area for malformed inputs.

---

## 5. Implementation Recommendations

1. **Update `T5.1-manual-test-recipe.md`** to reflect the new `claude -p` invocation shape, including the `--add-dir` flag. Add a preflight step that verifies `claude -p --help` outputs the expected flags. This document is currently inconsistent with the code.

2. **Resolve the `code-gen-agent` mapping** before running T5.1. Either use a known-good plugin name or make stage 5 explicitly bypass the slash-command dispatch path. Document the chosen plugin name in `.atlas.toml` for the real run.

3. **Verify `DEV-ESSENTIALS:plan-reviewer` exists** in the target Claude environment before attempting T5.1. Add it to the plugin verification checklist in the test recipe's preflight section.

4. **Add `latency_ms` timing** to `SubprocessStageRunner.run()` using `time.monotonic()`. The TRS specifies this and it is a one-line fix. Without it, the span tree is structurally correct but lacks the measurement data the tool is designed to capture.

5. **Deduplicate `gate_commit` scores** in `flush_pending_scores`. For a single run, only one `gate_commit` score should be written to plumb regardless of how many worktree commits the code-gen agent makes. The simplest fix is to track whether a `gate_commit` score has already been flushed for the current `run_id` and skip subsequent records of the same metric.

6. **Document worktree cleanup** in the failure-mode cheatsheet for the case where a user retries a failed `code_gen` stage without first removing the leftover worktree directory.

7. **Fix `write_example` in `real=True` mode** to actually write through plumb's storage adapter rather than buffering in memory. This is documented as a Phase 2 concern but is needed for a real run that produces rejections.

---

## 6. Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `claude -p` CLI interface is wrong | High (uncommitted diff unverified) | Stage 0 fails immediately | Verify `claude -p --help` before T5.1; update test recipe |
| `code-gen-agent` runs docs tool instead of codegen | Certain (as coded) | No code produced; hook never fires; stuck in `awaiting_hook` | Fix mapping before T5.1 |
| `DEV-ESSENTIALS:plan-reviewer` does not exist | Medium | Stage 4 fails; run halts before code gen | Preflight verification step |
| Multiple worktree commits produce >1 `gate_commit` score | High (code-gen agents typically commit multiple times) | TRD criterion 3 fails | Deduplicate in `flush_pending_scores` |
| Worktree not cleaned up after Ctrl+C | High (users frequently interrupt runs) | Next run with same slug fails immediately | Document in cheatsheet; add cleanup to `KeyboardInterrupt` handler |
| latency_ms = 0.0 on all spans | Certain (as coded) | Measurement data useless | Add `time.monotonic()` timing |

---

## 7. Research Findings

### Claude CLI Interface (`claude -p`)

The `claude -p "<prompt>"` invocation shape (with `-p` as the prompt flag) is the standard non-interactive mode for the Anthropic Claude CLI (`claude` from the `@anthropic-ai/claude-code` package). However:

- The `--no-session-persistence` flag suppresses session storage — correct for a black-box invocation, but only available in recent CLI versions.
- The `--add-dir` flag adds a directory to the model's context window — this is a relatively recent addition and may not be present in older CLI installations.
- Slash commands (`/DEV-ESSENTIALS:dev-docs-be`) in `-p` mode are processed if the Claude CLI has those plugins installed. The namespaced form `PLUGIN:command` requires the plugin to be installed and registered with the CLI.

The old interface (`claude --slash <cmd> --context <file> --task <task>`) referenced in the pre-diff code and the test recipe is the interface from an earlier CLI design. The new `-p` interface is more standard but the two shapes are incompatible. Any environment configured for the old interface will fail all 7 stages.

### DEV-ESSENTIALS Plugin Architecture

From the available context in `CLAUDE.md` and the plugin resolver, DEV-ESSENTIALS provides skills including `consult-experts`, `dev-docs-be`, `code-review`, and `verify`. These are invoked as slash commands within the Claude agent environment. The `plan-reviewer` command (`DEV-ESSENTIALS:plan-reviewer`) is not mentioned in the system prompt's skills list (`CLAUDE.md` available-skills section), which raises a question about whether it exists as a named skill or whether it is implemented as a mode of `consult-experts`.

The `consult-experts` skill listed in the system prompt (and in the old resolver) dispatches to specific expert personas. Using `consult-experts` with appropriate context for a plan review may be the correct approach for stage 4, rather than a hypothetical `DEV-ESSENTIALS:plan-reviewer` command that may not exist.

### Flask Cache Middleware Scope

The actual Flask feature (response-cache middleware) is a well-scoped, 1-2 file implementation. The pipeline is proportionately capable of handling it. No concerns about the target task scope — it is the right size for a v1.0 validation run.
