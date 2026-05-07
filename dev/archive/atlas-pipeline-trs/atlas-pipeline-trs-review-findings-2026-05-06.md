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

## External Plumb Updates (Phase A, Now Implemented)

- Orchestrator handoff guide now exists in plumb docs and defines:
  - child-run handoff via `parent_run_id` (recommended for cross-process continuation),
  - sibling runs for independent flows,
  - same-run continuation as a v2 roadmap item.
- `RunHandle.add_score()` now accepts `rationale`.
  - In v1 this is documented as in-memory only; durable `scores.rationale` is deferred to v2.
- Plumb dependency model now supports optional extras:
  - minimal base install plus `[cli]`, `[http]`, `[judge]`, and `[all]`.
- Deferred features are explicitly cataloged in plumb docs:
  - durable `scores.rationale`,
  - idempotent score ingestion,
  - `resume_run`,
  - `RunHandle.add_example`.

## Open Findings (Ordered by Severity)

### P0 - Atlas resume path is not aligned with plumb handoff guidance

- `Pipeline.resume()` calls `self._plumb.reopen_run(run_id)`, but does not use the returned run id.
- `PlumbIO.reopen_run()` currently calls `plumb_run(task_id=run_id, kind="online")`.
- Atlas does not explicitly model plumb's documented child-run pattern (`parent_run_id`) for resume handoff and does not persist/track the new active run id.
- If reopen fails, code falls back to warning + local id assignment, which risks non-durable behavior in real mode.

Impact:
- Stage 6 spans/scores after resume may be mis-attributed across runs or degrade to non-durable behavior under failure.

### P1 - Resume still reconstructs task from slug, not original task prompt

- `StateStore.create_tasks_md()` writes `# tasks - {slug}` style header content.
- `_parse_task_from_tasks_md()` reads that header and returns it as `ctx.task` during resume.
- Result: resumed plugin prompts/examples can use slug text rather than the original task description.

Impact:
- Reduced prompt quality and misleading example inputs after resume.

### P1 - Rejection examples are still not durably persisted in real plumb path

- `PlumbIO.write_example()` constructs an `Example` in real mode but still appends to `self.examples`.
- No call to plumb storage write path is made for examples.
- Plumb has now documented first-class `RunHandle.add_example` as v2 work, so atlas needs an interim strategy until that API is available.

Impact:
- Rejection example persistence is effectively stub-only behavior.

### P2 - Hook idempotency is still not enforced

- `post_commit_hook.run()` appends to `.atlas/pending-scores.jsonl` unconditionally.
- No dedupe key/check for `(run_id, commit_sha, metric)` replay cases.
- Plumb now tracks idempotent score ingestion as v2 deferred work, so atlas should enforce local dedupe for hook replay in the meantime.

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

### P2 - Atlas does not yet pass `rationale` into plumb score writes

- Plumb `RunHandle.add_score()` now accepts `rationale`.
- Atlas `PlumbIO.record_user_signal()` currently does not forward `decision.reason` when calling `add_score()`.

Impact:
- Human gate rationale is currently missing from plumb score write calls (and will also be needed once durable rationale lands in v2).

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

1. Align atlas resume to plumb handoff guidance:
   - Implement explicit child-run handoff semantics (or another documented pattern) in `PlumbIO.reopen_run()`.
   - Propagate/track the active run id returned by resume/handoff logic.
2. Persist original task text explicitly:
   - Store `task` in `tasks.md` metadata or `.atlas/current-run`.
   - Resume must rehydrate from persisted original task text, not slug/header.
3. Use plumb `add_score(rationale=...)` now:
   - Thread `GateDecision.reason` and hook rationale through atlas score writes.
   - Keep expectation explicit that durability of rationale remains v2 in plumb.
4. Enforce hook idempotency in atlas until plumb v2 support is available:
   - Add dedupe guard keyed by `(run_id, commit_sha, metric)`.
   - Add unit test that repeated hook invocation does not duplicate scores.
5. Record real latency:
   - Measure runner runtime in `Pipeline.step()` and emit actual `latency_ms`.
6. Eliminate same-process context drift:
   - Ensure `run_to_completion()` updates/uses the latest `RunContext` after worktree creation.
7. Handle rejection examples as an interim atlas action:
   - Either write through plumb storage adapter directly in real mode or keep explicit temporary local buffering until plumb v2 `add_example` API is available.
8. Complete runtime validation:
   - Install/import plumb successfully in atlas env with the new dependency model and re-run a real run + resume flow to confirm rows are durable and correctly attributed.

