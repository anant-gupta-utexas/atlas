# Code Review — `atlas.pipeline` TRS Implementation

Date: 2026-05-01
Reviewer: Cursor agent (`/review`)
Scope: Branch implementation for `dev/active/atlas-pipeline-trs/atlas-pipeline-trs-tasks.md` (Phases 3-5 changes)

## Findings (Ordered by Severity)

### 1) Critical — `code_review` stage can run on `main` instead of the codegen worktree

`Pipeline.step()` creates a worktree path only in a local replacement of `RunContext` during stage 5, but this updated context is not persisted to state and not returned to the caller. Subsequent `step()` calls and `resume()` reconstruct context without `worktree_path`, so the runner falls back to `repo_root`.

Impact:
- Stage 6 (`code_review`) may inspect or operate on `main` instead of generated code in the worktree.
- This breaks the worktree boundary expectation and can hide generated-code regressions at review time.

Code references:
- `src/atlas/orchestrator.py` (`Pipeline.step`, context replacement near code_gen entry)
- `src/atlas/orchestrator.py` (`SubprocessStageRunner.run`, `cwd = ctx.worktree_path if ... else ctx.repo_root`)

---

### 2) Critical — post-commit hook does not durably persist `gate_commit` signal

The hook reads `.atlas/current-run` from `git rev-parse --show-toplevel`. In worktree execution, this may resolve to the linked worktree root where `.atlas/current-run` does not exist. Even when present, the hook constructs `PlumbIO(real=True)` without opening a run handle, so `record_user_signal()` does not write through a real plumb run handle and the signal is effectively non-durable.

Impact:
- `gate_commit` score can be silently missing.
- Gate-4 state/telemetry contract in TRD can be violated without visible failure.

Code references:
- `src/atlas/post_commit_hook.py` (`_repo_root`, `run`)
- `src/atlas/plumb_io.py` (`record_user_signal` behavior requires `_run_handle`)

---

### 3) High — real plugin invocation passes tool name as context

`SubprocessStageRunner` calls:
- `claude --slash <plugin_cmd> --context <stage.tool>`

`stage.tool` is an identifier like `code-gen-agent` or `consult-experts:pm`, not task/TDS content or a context file path.

Impact:
- Real plugin runs may be under-contextualized or fail to perform intended work.
- Manual T5.1 real-plugin run risk is high.

Code reference:
- `src/atlas/orchestrator.py` (`SubprocessStageRunner.run`)

---

### 4) Medium — failed/rejected stages are marked checked before decision outcome is finalized

`Pipeline.step()` marks the stage checkbox before failure and rejection return paths.

Impact:
- `tasks.md` can report progress that does not match actual gate outcome.
- Resume semantics become misleading after failures/rejections.

Code reference:
- `src/atlas/orchestrator.py` (`self._state.check_box(ctx, stage.name)` before failure/rejection handling)

## Testing Coverage Notes

Focused tests pass, but critical behavior is not fully exercised:

- E2E test simulates `gate_commit` by appending directly to in-memory scores, so it does not validate hook durability/path resolution.
- Resume E2E test confirms first unchecked stage after restart, but does not continue through stage 5 -> stage 6 to verify correct worktree cwd propagation.

Command run:

- `python -m pytest tests/integration/test_main_branch_isolation.py tests/e2e/test_e2e_happy_path.py tests/unit/test_pipeline.py tests/unit/test_phase4.py tests/unit/test_worktree.py -q`
- Result: passing

Note: `uv run pytest ...` attempted first but encountered DNS failures while resolving packages from PyPI in this environment.
