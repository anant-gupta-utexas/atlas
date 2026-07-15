# Code Review — YAML Workflow Engine, Phase 1 (Engine Generalization)

**Reviewer:** Code Reviewer (consult-experts)
**Date:** 2026-06-30
**Commit under review:** `0515425` — *feat: YAML-driven workflow engine (Phase 1 — engine generalization)*
**Plan:** [yaml-workflow-engine-phase-1-plan.md](./yaml-workflow-engine-phase-1-plan.md)
**Scope:** `workflow_loader.py` (new), `stages.py`, `orchestrator.py`, `state.py`, `cli.py`, `plugin_resolver.py`, `post_commit_hook.py`, `workflows/dev.yaml`, tests.

---

## Executive Summary

This is a **high-quality, well-disciplined implementation** that faithfully realizes the TRS. The engine generalization is clean: the three hardcoded conditionals are now data-driven (`stage.isolate`, `stage.gate_is_async`, `gate_label is None`), `StageName`/`GateLabel` are fully deleted (grep-zero confirmed), and the loader is tight, safe (`yaml.safe_load` only), and 100%-covered. All quality gates are green:

| Gate | Result |
|---|---|
| `pytest` (unit + integration) | **153 passed** |
| `pytest tests/e2e` | **3 passed** (parity proof) |
| `mypy src` | **clean** (11 files) |
| `ruff check` / `ruff format --check` | **clean** |
| `workflow_loader.py` coverage | **100%** (target ≥ 90%) |

The architecture is correct and the FR/NFR mapping is honored. However, I found **one genuine correctness bug** in the async-gate metric path that survives every test because the test suite only exercises the `dev` workflow (where the bug is masked), plus a smaller correctness gap in the same feature, and a few minor items. None of these block the dev pipeline today — they bite the *first non-dev workflow*, i.e. exactly what Phase 1 exists to enable. They should be fixed before Phase 2 builds on this seam.

**Recommendation:** Address Critical Issue #1 (and ideally #2) now; they are cheap fixes in code you're already touching, and leaving them turns a Phase-1 latent bug into a Phase-2 "why is the job workflow's gate score wrong" debugging session.

---

## Critical Issues (must fix)

### C1 — Async-gate metric is **not namespaced** for non-dev workflows (correctness bug, masked by tests)

**Where:** `orchestrator.py:346-360` (async branch) + `post_commit_hook.py:76` + `plumb_io.py:235`.

The synchronous gate path correctly namespaces the metric:

```python
# orchestrator.py:371-376 — SYNC gate, correct
self._plumb.record_user_signal(
    ...,
    metric=namespaced_metric(self._workflow_name, stage.gate_label),  # <name>.<gate> for non-dev
    ...,
)
```

But the **async** gate path writes the *bare* `gate_label` to `current-run` line 5:

```python
# orchestrator.py:354-360 — ASYNC gate, NOT namespaced
self._state.write_current_run(
    ...,
    async_gate_metric=stage.gate_label,   # <-- bare label, e.g. "gate_shipped", never "job.gate_shipped"
)
```

The post-commit hook then reads that line verbatim (`post_commit_hook.py:76`) and `flush_pending_scores` writes it through unchanged (`plumb_io.py:235`). Net effect: for a non-dev workflow, **every synchronous gate is namespaced `<workflow>.<gate>` but the async (commit) gate is not** — the score lands under the bare label, inconsistent with FR-7 (§3.7) and with its sibling gates in the same run.

**Why every test passes anyway:** the only workflow exercised end-to-end is `dev`, and `namespaced_metric("dev", x) == x` — namespacing is a no-op for dev, so the bug is invisible until a second workflow exists. There is **no test** asserting the non-dev async metric (grep for `gate_shipped`/`async_gate_metric`/`namespaced` in `tests/` returns nothing). The plan's own T1.10 acceptance criterion — *"a synthetic non-dev workflow whose async-gate stage has `gate: "gate_shipped"` produces a hook-written score with `metric == "gate_shipped"`"* — is actually **wrong**: per FR-7 it should be `"<workflow>.gate_shipped"`. The implementation matches the (incorrect) acceptance criterion, which is why it slipped through.

**Fix:** namespace at the write site, mirroring the sync path:

```python
async_gate_metric=namespaced_metric(self._workflow_name, stage.gate_label),
```

Then add the missing non-dev test (T1.10 should assert the *namespaced* value). The hook stays dumb — it just echoes line 5 — which is the right design; the namespacing belongs at the orchestrator boundary, exactly as the sync path does it.

---

## Important Improvements (should fix)

### I1 — `resume()` drops the async-gate metric (line 5) when the run id changes

**Where:** `orchestrator.py:239-246`.

When the child-run handoff produces a new run id, `resume()` rewrites `current-run`:

```python
if active_run_id != run_id:
    self._state.update_run_id(slug, active_run_id)
    self._state.write_current_run(
        active_run_id,
        slug,
        worktree_path,
        code_gen_span_id=code_gen_span_id,
        # async_gate_metric NOT passed -> defaults to None -> line 5 is dropped
    )
```

`write_current_run` only emits line 5 when `async_gate_metric` is truthy (`state.py:135-136`). So if a run is interrupted **after** the async gate stage has run (line 5 was written) and then resumed with a real plumb backend (where `reopen_run` returns a *new* id), the rewrite **truncates line 5**. The hook then silently falls back to the literal `"gate_commit"` (`post_commit_hook.py:76`).

- For `dev`, this is harmless (the metric *is* `gate_commit`).
- For a non-dev workflow, the post-resume commit score is recorded under the **wrong metric**.

I confirmed the truncation by simulating `write_current_run` directly: the resume rewrite produces a 4-line file, losing the metric.

**Fix:** read and re-pass the existing metric during the rewrite:

```python
existing_metric = self._state.read_async_gate_metric()   # accessor already exists, state.py:164
self._state.write_current_run(
    active_run_id, slug, worktree_path,
    code_gen_span_id=code_gen_span_id,
    async_gate_metric=existing_metric,
)
```

Note: `read_async_gate_metric()` was added (`state.py:164`) but is **never called anywhere** (grep confirms zero production callers) — this resume rewrite is exactly the caller it was presumably written for. Wiring it here closes the gap and justifies the accessor's existence.

### I2 — `write_current_run`'s positional-line encoding is fragile and under-documented

**Where:** `state.py:121-137`.

The line-5 metric is reachable only if lines 3 and 4 are also emitted, which depends on a chain of truthiness checks across three optional params. The current code gets this right for the call sites that exist, but the coupling is implicit: a future caller passing `async_gate_metric` *without* `worktree_path`/`code_gen_span_id` still works (verified — empty placeholder lines are emitted), but nothing documents that line position is load-bearing. A positional flat file with optional middle lines is a classic source of off-by-one drift.

This isn't a bug today, but given C1/I1 already show the line-5 contract is easy to break, consider either (a) a short comment block on `write_current_run` stating "lines are positional: run_id / slug / worktree / span_id / async_metric; trailing lines may be empty placeholders," or (b) longer-term, moving `current-run` to a small key=value or JSON format so the hook reads by key, not index. (b) is out of scope for Phase 1 but worth a note for Phase 2/3 since the hook contract is about to gain more fields.

---

## Minor Suggestions (nice to have)

- **`stages.py:23` doc vs behavior:** `timeout_s` comment says "None → orchestrator default," which is accurate, but the *4-tier* resolution (override → yaml → `_DEFAULT_TIMEOUT_S` → global fallback) lives only in `resolve_timeout`'s docstring. Fine as-is; just flagging that the single-line comment understates the resolution order. No change required.

- **`resolve_workflow` name-validation asymmetry (`workflow_loader.py:174-176`):** the path-traversal guard validates `name` *after* defaulting (`workflow_name or "dev"`), so an explicit empty `--workflow ""` becomes `dev` silently. That matches the "neither given → dev" intent and the CLI passes `workflow or None`, so empty-string never reaches here as a distinct case. Correct, but the interaction is subtle — a one-line comment ("empty/None both fall through to dev") would help the next reader. Security guard itself is correctly placed *before* any path join (✓ matches §9 / T1.3).

- **`_validate_routing_fixture` error message (`orchestrator.py:500`):** message still says `"...STAGES has {len}"` referencing the now-deleted module-level `STAGES` constant. Cosmetic — update to `"workflow has {len} stages"` for clarity since `STAGES` no longer exists.

- **`Pipeline.__init__` lazy default-load (`orchestrator.py:159-163`):** when `stages is None`, it hardcodes `workflow_name="dev"` in the internal `resolve_workflow` call but ignores the `workflow_name` *parameter*. In practice `cli.py` always passes `stages`, so this path is test/convenience-only, but it means constructing `Pipeline(stages=None, workflow_name="job")` would silently load `dev` stages under a `job` name label — an inconsistent state. Either drop the `None` convenience path (CLI never uses it) or honor the `workflow_name` param when resolving. Low impact; flagging for correctness hygiene on the seam Phase 2 will lean on.

---

## Architecture Considerations

- **The data-driven refactor is exactly right.** `isolate` / `gate_is_async` as sibling per-stage booleans (Resolved Decision #2) is the correct model — it removes the hardcoding §3.4 exists to kill, and the parity test (`test_dev_pipeline_parity`) is a genuine regression guard, not a tautology.
- **Hook stays dependency-free.** Keeping YAML parsing out of the hook subprocess and passing the metric via `current-run` line 5 is the right call (consistent with v1's "thin best-effort parser" philosophy). C1's fix preserves this — the fix is at the orchestrator write site, the hook stays dumb.
- **`backend` / `default_backend` threaded-but-unconsumed** (Resolved Decision #4) is correctly handled: parsed, stored, never dispatched on. The Phase-3 seam is stable. ✓
- **`_DEFAULT_TIMEOUT_S` retained as tier-3 fallback** (Decision #5) rather than deleted — correct for FR-8 parity, and the `_GLOBAL_FALLBACK_TIMEOUT_S` addition genuinely fixes the latent `KeyError` v1 would have thrown for non-dev stage names. `dev.yaml` ships with no `timeout_s` (✓ confirmed), proving the fallback path.
- **Test suite blind spot (root cause of C1/I1):** every integration/e2e test runs `dev`, where `namespaced_metric` is an identity function and the bare-vs-namespaced distinction vanishes. The suite has *no* end-to-end exercise of a non-dev workflow's gate scoring. This is the single highest-leverage gap: one synthetic non-dev workflow fixture driven through a gate (sync **and** async) would have caught both C1 and I1. Strongly recommend adding it as part of the fix, and before Phase 2 — Phase 2 *is* the first real non-dev workflow, and it will inherit this blind spot otherwise.

---

## Next Steps

1. **Fix C1** — namespace `async_gate_metric` at `orchestrator.py:359`; correct T1.10's acceptance criterion to expect the namespaced value.
2. **Fix I1** — re-pass the existing metric via `read_async_gate_metric()` in the `resume()` rewrite (`orchestrator.py:241-246`); this also gives the orphaned accessor its caller.
3. **Add the missing test** — a synthetic non-dev workflow fixture exercising both a sync gate (assert `<name>.<gate>`) and the async gate (assert `<name>.<gate>` survives the hook + a run-id-changing resume). This closes the architectural blind spot, not just C1/I1.
4. **Optional minors** — `_validate_routing_fixture` message wording; `Pipeline(stages=None)` workflow-name consistency; `write_current_run` positional-line comment.

---

---

## Resolution (2026-06-30 — fixes implemented)

All findings were approved and fixed. Changes (uncommitted, on `atlas/yaml-workflow-engine-phase-1`):

| Item | Fix | Location |
|---|---|---|
| **C1** | Namespace the async-gate metric at the orchestrator write site, mirroring the sync path. | `orchestrator.py:366` |
| **I1** | Read + re-pass the existing async-gate metric during the run-id-changing resume rewrite (wires up the previously-orphaned `read_async_gate_metric()`). | `orchestrator.py:242-253` |
| **Test gap** | New `tests/unit/test_non_dev_workflow.py` — drives a synthetic `job` workflow through a sync gate (asserts `job.gate_shortlist`) and the async gate (asserts `job.gate_shipped` survives the hook write **and** a run-id-changing resume). Verified to **fail against pre-fix code** (`'gate_shipped' != 'job.gate_shipped'`) — genuine regression guards, not tautologies. | new file, 3 tests |
| **Minor** | `_validate_routing_fixture` message no longer references the deleted `STAGES` constant. | `orchestrator.py:507` |
| **Minor** | `Pipeline(stages=None)` now honors its `workflow_name` param instead of hardcoding `"dev"`. | `orchestrator.py:162` |
| **I2 (minor)** | Documented the positional-line contract on `write_current_run`. | `state.py:129` |

**Verification after fixes:** `pytest` **156 passed**, `tests/e2e` **3 passed**, `mypy src` clean, `ruff check`/`format --check` clean, `workflow_loader.py` coverage **100%**.

Net source diff: +17/−3 lines across `orchestrator.py` + `state.py`, plus one new test file. No behavioral change to the `dev` pipeline (namespacing remains a no-op for `dev`).
