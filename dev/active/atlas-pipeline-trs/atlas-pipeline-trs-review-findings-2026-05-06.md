# Atlas Pipeline TRS Review Findings (2026-05-06)

## Scope

This review compares the original findings against the newer T5.1 implementation changes and re-validates the current branch behavior in the local repo.

## Executive Summary

Most pipeline tests are now green, and several important remediation items are implemented. However, release-critical durability gaps remain for real plumb-backed resume flows. In particular, the code still lacks a proven durable reattach path for resumed runs, still loses original task text on resume, and still does not durably persist rejection examples through plumb.

## What Is Confirmed Fixed

- `code_gen_span_id` persistence and rehydration:
  - `Pipeline.step()` stores `code_gen_span_id` in `.atlas/current-run`.
  - `Pipeline.resume()` rehydrates `_last_code_gen_span_id`.
- `awaiting_hook` control flow hardening:
  - `run_to_completion()` now waits for commit score records and has a retry cap.
- `close_run()` context-manager usage:
  - `PlumbIO.close_run()` now exits `_run_ctx` (not `_run_handle`).
- Hook interpreter reliability:
  - Post-commit hook script uses baked interpreter path and fallback logic.

## Open Findings (Ordered by Severity)

### P0 - Resume durability in real plumb mode is still unproven and likely incorrect

- `Pipeline.resume()` calls `self._plumb.reopen_run(run_id)`, but does not use the returned run id.
- `PlumbIO.reopen_run()` currently calls `plumb_run(task_id=run_id, kind="online")`, which appears to open a new run keyed by `task_id` rather than reattaching to the original active run.
- The implementation comments claim "child run" behavior, but no explicit parent linkage is set in this layer.
- If reopen fails, code falls back to warning + local id assignment, which risks non-durable behavior in real mode.

Impact:
- Stage 6 spans/scores after resume may land in a different run than expected, or degrade to non-durable behavior under failure.

### P1 - Resume still reconstructs task from slug, not original task prompt

- `StateStore.create_tasks_md()` writes `# tasks - {slug}` style header content.
- `_parse_task_from_tasks_md()` reads that header and returns it as `ctx.task` during resume.
- Result: resumed plugin prompts/examples can use slug text rather than the original task description.

Impact:
- Reduced prompt quality and misleading example inputs after resume.

### P1 - Rejection examples are still not durably persisted in real plumb path

- `PlumbIO.write_example()` constructs an `Example` in real mode but still appends to `self.examples`.
- No call to plumb storage write path is made for examples.

Impact:
- Rejection example persistence is effectively stub-only behavior.

### P2 - Hook idempotency is still not enforced

- `post_commit_hook.run()` appends to `.atlas/pending-scores.jsonl` unconditionally.
- No dedupe key/check for `(run_id, commit_sha, metric)` replay cases.

Impact:
- Duplicate `gate_commit` scores are possible if the hook runs repeatedly for the same commit.

### P2 - Span latency telemetry remains placeholder data

- `Pipeline.step()` still records every span with `latency_ms=0.0`.

Impact:
- Latency analytics are not meaningful.

### P2 - Same-process context drift after code_gen is still possible

- `Pipeline.step()` may create an updated `ctx` with `worktree_path`, but caller-owned `ctx` in `run_to_completion()` is not replaced.
- This can allow stage 6 to run with stale context in same-process flow.

Impact:
- `code_review` may target repo root instead of the generated-code worktree.

## Verification Performed (Current Session)

### Passing tests

- `uv run pytest tests/unit/test_remediation.py tests/unit/test_review_fixes.py -q`
  - Result: `29 passed`
- `uv run pytest -q`
  - Result: `111 passed`
- `uv run pytest --cov=src/atlas --cov-report=term-missing --cov-fail-under=80`
  - Result: `111 passed`, total coverage `92.34%`

### Environment/runtime checks

- `uv run python -c "import plumb"` equivalent check failed with `ModuleNotFoundError`.
- Attempted local editable install from sibling repo:
  - `uv pip install -e ../plumb`
  - Blocked by dependency resolution/network (`tenacity` fetch DNS failure).

Notes:
- Current green tests are primarily stub-mode validations and do not prove real plumb durability semantics.

## Recommended Next Steps

1. Fix real resume durability first:
   - Implement a real plumb reattach/child-run strategy with explicit and verified linkage semantics.
   - Propagate/track the active run id returned by reopen logic.
2. Persist original task text explicitly:
   - Store `task` in `tasks.md` metadata or `.atlas/current-run`.
   - Resume must rehydrate from persisted original task text, not slug/header.
3. Make example persistence real:
   - Route `write_example()` through plumb storage API in real mode.
4. Enforce hook idempotency:
   - Add dedupe guard keyed by `(run_id, commit_sha, metric)`.
   - Add unit test that repeated hook invocation does not duplicate scores.
5. Record real latency:
   - Measure runner runtime in `Pipeline.step()` and emit actual `latency_ms`.
6. Eliminate same-process context drift:
   - Ensure `run_to_completion()` updates/uses the latest `RunContext` after worktree creation.
7. Complete runtime validation:
   - Install/import plumb successfully in atlas env, then run a real run + resume flow to confirm rows are durable and correctly attributed.

