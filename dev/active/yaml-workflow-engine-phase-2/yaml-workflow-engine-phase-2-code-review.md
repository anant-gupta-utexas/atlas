# Code Review — YAML Workflow Engine Phase 2

## Executive Summary

Phase 2 has the right high-level shape: a closed `LIB:` registry, separate `CompositeStageRunner`, shipped `job` / `job_cli` workflows, and focused tests around dispatch and namespacing. However, the current implementation is not merge-ready for the job workflow.

I found two critical runtime blockers and one important CI/test-gate gap:

- `job` Mode A targets content-pipeline modules that do not exist in the checked-out sibling project.
- `job_cli` is documented as direct content-pipeline CLI dispatch, but `RAW:` currently means "send a raw prompt to `claude -p`", not "execute this shell command".
- The required CI leg with `uv sync --extra job` is commented out, so the optional dependency path is not actually enforced.

I attempted focused runtime checks, but `uv` tried to resolve packages from PyPI and failed due DNS/network errors in the current environment. The findings below are grounded in static inspection of atlas plus the sibling `content-pipeline` checkout, and were independently re-verified against the actual files on a follow-up pass (see "Verification trace" under each finding).

The deepest single observation: the four findings are entangled. The test design (finding #4) is the reason the API mismatch (finding #1) and the `RAW:` semantics gap (finding #2) survived merge; the missing CI leg (finding #3) is the reason the test gap survived. So the question is not "fix four bugs" but "make one scope decision (what does Phase 2 actually claim?) and let the code/test/CI changes flow from it." See [Implications](#implications) and [Options](#options-grouped-by-scope-decision) below.

## Critical Issues (Must Fix)

### 1. `job` Mode A imports content-pipeline APIs that are absent in the sibling checkout

**Severity:** Critical  
**Files:** `src/atlas/library_adapters/score_jobs_adapter.py`, `src/atlas/library_adapters/capture_adapter.py`, `src/atlas/workflows/job.yaml`  

`job.yaml` dispatches `score_fit` to `LIB:content_pipeline.score_jobs`, and the adapter imports:

```python
from src.application.use_cases.score_jobs import ScoreJobsUseCase
from src.infrastructure.cli.score_jobs_report import render_report
```

The checked-out sibling `content-pipeline/src/application/use_cases/` contains `capture.py` and `classify.py`, not `score_jobs.py`; its CLI exposes `classify --pending`, not `score-jobs --pending`; and `src.infrastructure.scrapers.ats_boards` is also absent. As implemented, a real `--workflow job` run will fail even when the `job` extra is installed, because the adapter is wired to a future or different content-pipeline API rather than the API in the repo.

This violates Phase 2's core exit criteria: `atlas run "..." --workflow job` cannot produce the promised 4-span/3-gate run against the actual optional dependency.

**Verification trace (re-confirmed on follow-up pass):**

| atlas adapter import | sibling file present? |
|---|---|
| `src.application.use_cases.score_jobs.ScoreJobsUseCase` | ❌ — only `capture.py`, `classify.py`, `__init__.py` exist under `use_cases/` |
| `src.infrastructure.cli.cmd_score_jobs._build_llm_client` / `_load_profile_text` / `_load_prompt` | ❌ — only `cmd_capture.py`, `cmd_classify.py`, `main.py` exist under `infrastructure/cli/` |
| `src.infrastructure.cli.score_jobs_report.render_report` | ❌ — file does not exist |
| `src.infrastructure.scrapers.ats_boards.AtsBoardScraper` | ❌ — `scrapers/` has `rss.py`, `imap.py`, `generic.py`, `__init__.py` only |
| `src.application.use_cases.capture.CaptureUseCase` | ✅ |
| `src.application.dispatcher.CrawlerDispatcher` | ✅ (assumed — `application/` exists) |

So `score_jobs_adapter.invoke()` will `ImportError` at every reachable line of its body, and `capture_adapter.invoke()` will `ImportError` specifically on the `AtsBoardScraper` line — both before any real use-case work runs.

**Hidden-by-design failure mode.** `LibraryStageRunner.run()` (`src/atlas/library_runner.py:60-74`) catches `ImportError` from `_import_adapter` and converts it to `StageOutcome(error_type="content_pipeline_not_installed", ...)`. The `_import_adapter` call resolves the dotted *adapter module* path (`atlas.library_adapters.score_jobs_adapter`), so an `ImportError` raised *inside* the adapter's function body when it tries to import `src.application.use_cases.score_jobs` is also caught at runner level and surfaced under the same `content_pipeline_not_installed` label. From the user's perspective, "I ran `uv sync --extra job` and atlas still tells me content-pipeline isn't installed" — when the real cause is an atlas-side adapter wired to APIs that don't ship in the installed package. This is worse than a loud crash because it points the user at the wrong remedy.

**Recommended fix:** Reconcile the TRS with the real content-pipeline API before merging. Either update content-pipeline first to provide `ScoreJobsUseCase`, `score_jobs_report`, and `AtsBoardScraper`, or change atlas's adapters/workflows/docs to target the existing `ClassifyUseCase`, `classify --pending`, and available scraper types. Independent of which way you go, **narrow `LibraryStageRunner`'s `ImportError` handling** to only catch the `_import_adapter` (atlas-adapter-resolution) `ImportError`, and let `ImportError`s raised from *within* the adapter body surface as `library_adapter_error` with the original message — so the next time this happens, the user sees "no module named `src.application.use_cases.score_jobs`" instead of a misleading "not installed."

### 2. `job_cli` does not actually execute content-pipeline CLI commands

**Severity:** Critical  
**Files:** `src/atlas/workflows/job_cli.yaml`, `src/atlas/plugin_resolver.py`, `src/atlas/orchestrator.py`  

`job_cli.yaml` says:

```yaml
tool: "RAW:content-pipeline capture --source job-boards"
```

But `RAW:` is interpreted by `build_prompt()` as raw prompt text, and `SubprocessStageRunner` always invokes:

```python
claude -p <prompt>
```

So the `job_cli` workflow does not directly run `content-pipeline capture ...` or `content-pipeline score-jobs ...`; it asks Claude to process those strings as prompt text. That means the dependency-free Mode B path is not a real CLI-dispatch path, and the tests do not catch it because they use `_FakeSubprocessRunner` instead of a stub `content-pipeline` executable on `PATH`.

This breaks the explicit fallback story in Phase 2: when `job` fails due missing content-pipeline imports, `job_cli` is supposed to be the runnable dependency-free alternative.

**Verification trace (re-confirmed on follow-up pass):**

```62:67:src/atlas/plugin_resolver.py
def build_prompt(cmd: str, task: str, context_hint: str) -> str:
    """Build the full prompt string for ``claude -p``."""
    if cmd.startswith("RAW:"):
        raw_prefix = cmd[4:]
        return f"{raw_prefix}\n\n{task}\n\n{context_hint}"
    return f"/{cmd} {task}\n\n{context_hint}"
```

```555:598:src/atlas/orchestrator.py
def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
    from atlas.plugin_resolver import build_prompt, resolve  # local import to avoid cycles
    # ...
    prompt = build_prompt(plugin_cmd, ctx.task, context_hint)
    # ...
    result = subprocess.run(
        [
            "claude",
            "-p",
            prompt,
            "--no-session-persistence",
            # ...
        ],
        # ...
    )
```

There is exactly one subprocess shape in `SubprocessStageRunner`: `["claude", "-p", prompt, ...]`. The `RAW:` branch in `build_prompt` strips the prefix and concatenates the rest with the task and a context hint — there is no code path that would `subprocess.run(["content-pipeline", "capture", ...])`. So:

- `job.yaml`'s `LIB:` stages dispatch to `LibraryStageRunner` (in-process Python).
- `job.yaml`'s `RAW:` stages dispatch to `SubprocessStageRunner` → `claude -p`.
- **`job_cli.yaml`'s `RAW:` stages — all four of them — also dispatch to `SubprocessStageRunner` → `claude -p`.** The "subprocess" being spawned is `claude`, not `content-pipeline`.

The two shipped workflows therefore differ only in what string is sent to Claude, not in whether content-pipeline runs as a Python dependency vs. as a CLI. Resolved Decision #2 in the TRS reads "shipped `job_cli.yaml` … the literal Mode-B variant from §3.8 … runs without content-pipeline as a Python dependency" and the §3.7 spec calls for "`RAW:` subprocess dispatch to content-pipeline's CLI". The implementation does neither — it routes through `claude -p` regardless.

The `job_cli.*` metric namespace (`job_cli.gate_shortlist`, …) measures "which prompt we sent Claude" rather than "library-in-process vs subprocess-to-content-pipeline", which is the comparison the TRS calls out as load-bearing in §3.7.

**Recommended fix:** Add an explicit shell/CLI runner convention, such as `SHELL:` or `CMD:`, with list-form subprocess execution and a workflow-author allow-list (e.g., first token must be in `{"content-pipeline"}`). `CompositeStageRunner` dispatches `SHELL:`-prefixed tools to a new `ShellStageRunner` that calls `subprocess.run([...], shell=False, timeout=stage.timeout_s)` — `timeout_s` is honored, NFR-2 (no unhandled exceptions) is preserved by mapping `FileNotFoundError`/`TimeoutExpired`/non-zero exits to `StageOutcome(status="failure", ...)`. Then switch `job_cli.yaml`'s two `content-pipeline …` lines from `RAW:` to `SHELL:`. Estimated cost: ~80–150 LoC new + tests. The alternative — change the docs and acceptance criteria to state that `job_cli` is Claude-mediated — is cheaper but materially changes what the TRS claims Phase 2 delivers; given the TRS, a direct CLI runner is the cleaner fix.

## Important Improvements (Should Fix)

### 3. The `job` extra CI gate is documented but not active

**Severity:** Important  
**File:** `.github/workflows/ci.yml`  

T2.9's acceptance criteria require a CI job/step that installs the `job` extra and runs the job-workflow suite with content-pipeline installed. The current CI file only includes a commented template for `test-job-extra`; the active jobs run without the extra.

This leaves the highest-risk integration path untested in CI and is the reason the missing content-pipeline API mismatch can survive as a completed task.

**Verification trace (re-confirmed on follow-up pass):** lines 55–90 of `.github/workflows/ci.yml` are an entirely commented-out `test-job-extra` job. The active `test` job runs `uv sync --extra dev` (line 36) — no `--extra job`. The active comment block even acknowledges the gap explicitly: "To enable: check out content-pipeline in CI using an actions/checkout step targeting your private content-pipeline repo, then uncomment the job." T2.9's acceptance criterion in the TRS is "CI has a job/step that runs *with* the `job` extra installed and asserts the rest of `test_job_workflow_e2e.py` passes." That criterion is not met, but T2.9 is shown as complete in the task list.

This is the structural reason finding #1 survived merge: there is no CI signal that exercises the `LIB:` adapter import path against a real (or even stub) content-pipeline install, so the API mismatch is silently green.

**Recommended fix:** Enable an actual CI job that checks out/symlinks content-pipeline, runs `uv sync --extra dev --extra job`, and runs the job workflow tests that exercise the real adapter import path. If the content-pipeline repo is private and unavailable to CI, two acceptable paths: (a) check in a tiny stub content-pipeline package under `tests/_fakes/content_pipeline_stub/` matching the expected use-case shape, and install *that* via the `job` extra in CI — adapter bodies execute, imports resolve, but no real LLM / scrapers fire; or (b) mark the Phase 2 exit criterion incomplete and uncheck T2.9 rather than leaving a misleading green check.

### 4. Integration tests mock above the required use-case boundary

**Severity:** Important  
**File:** `tests/integration/test_job_workflow_e2e.py`  

The TRS says the job integration tests should mock content-pipeline use cases, not `LibraryStageRunner`. The implemented tests patch `atlas.library_runner._import_adapter` to return `_success_adapter`, so they bypass both `score_jobs_adapter.py` and `capture_adapter.py`. The adapter unit tests also fabricate `sys.modules`, so no test imports the real sibling API.

This test shape verifies atlas's span/gate plumbing, but it does not verify the Phase 2 integration contract with content-pipeline.

**Verification trace (re-confirmed on follow-up pass):**

- `tests/integration/test_job_workflow_e2e.py:17` imports `from unittest.mock import patch`; the body patches `atlas.library_runner._import_adapter` to return a `_success_adapter` defined locally (lines 61–68). That swap happens *one layer above* `score_jobs_adapter.invoke` / `capture_adapter.invoke` — the adapter module bodies are never executed in this test file.
- `tests/integration/test_job_workflow_e2e.py:43-53` defines `_FakeSubprocessRunner` that returns success for every stage without ever calling `subprocess.run`. The composite runner is constructed with this fake as `default=` (lines 82, 99), so the `RAW:` → `claude -p` path is never exercised either. This is why finding #2 wasn't caught: a real `RAW:content-pipeline …` string would have to either (a) reach a real `claude` invocation, where it would obviously not be a CLI dispatch, or (b) reach a stub `content-pipeline` binary on `PATH`, where it would obviously never arrive. Neither happens.
- The adapter unit tests (`tests/unit/test_library_adapters.py`) work the same way — they `sys.modules`-inject fake `src.application.use_cases.*` modules, which means the real adapter import statements are satisfied by fakes, *not* by content-pipeline. So those tests can be green even when the real sibling API doesn't exist.

**Root-cause framing.** The TRS §10 explicitly specified mocking at the *use-case class* boundary (`ScoreJobsUseCase` / `CaptureUseCase`) precisely so the adapter import path would be real. The implementation moved the mock boundary up — almost certainly because mocking at the use-case class boundary is harder when the use-case class doesn't actually exist in the sibling (finding #1). So the test-design drift and the API mismatch reinforce each other: each made the other invisible.

**Recommended fix:** Keep the existing plumbing tests, but add at least one integration path that uses the real `atlas.library_adapters.*` modules with either the actual content-pipeline package installed or test doubles injected at the use-case class boundary. The smallest honest version: add `tests/_fakes/content_pipeline_stub/` with `src.application.use_cases.score_jobs.ScoreJobsUseCase` etc. defined as importable stubs that take the same constructor arguments and return canned `*RunResult` objects; install it via `[project.optional-dependencies.test-stub]`; run one integration test per adapter that imports it for real and patches the use-case *class* (not `_import_adapter`).

## Minor Suggestions (Nice To Have)

- `LibraryStageRunner` currently classifies any `ImportError` from importing an adapter as `content_pipeline_not_installed`. That is acceptable for the current UX, but it can mask an atlas adapter import bug as a missing optional dependency. Consider narrowing this once the content-pipeline API is stable.
- The docs still use `pip install content-pipeline` for Mode B, but the local package appears to be a sibling editable dependency rather than a published package. Prefer wording that matches the actual install path.

## Architecture Considerations

The closed `LIB:` registry is the right security shape. The main architectural mismatch is that Phase 2 introduces two different concepts under the same "subprocess" language:

- Existing `RAW:` means "raw prompt to Claude through `SubprocessStageRunner`".
- Mode B requires "direct shell command to content-pipeline".

Those should be separate runner semantics. Reusing `RAW:` for both makes the workflow file look correct while executing a materially different path.

## Implications

1. **Phase 2's headline deliverable doesn't currently run.** `atlas run "..." --workflow job` is wired to a `ScoreJobsUseCase` etc. that doesn't exist in the sibling. Any real invocation surfaces as `content_pipeline_not_installed` even when the package *is* installed — the lazy-import pattern combined with the broad `ImportError` catch in `LibraryStageRunner` masks an adapter-side API mismatch as a missing-extra error, which points the user at the wrong remedy.
2. **The Mode B fallback isn't actually Mode B.** The whole point of `job_cli.yaml` per Resolved Decision #2 was a runnable, dependency-free path that dispatches to content-pipeline's CLI. Today both `job.yaml` and `job_cli.yaml` route through `claude -p`; they differ only in the prompt string. The `job_cli.*` vs `job.*` metric namespacing only measures "which prompt we sent to Claude," not "library vs subprocess" — the comparison the TRS calls out as load-bearing.
3. **The test design is the root cause both findings survived merge.** Tests mocked above the boundaries the TRS explicitly named (use-case classes / on-`PATH` stub binary). Once `_import_adapter` is patched, no test exercises the adapter module body; once `_FakeSubprocessRunner` is in place, no test exercises whether `RAW:` even invokes a subprocess. The test-design drift was probably *caused* by finding #1 (it's hard to mock a class that doesn't exist), which means fixing the tests in isolation requires either fixing #1 or accepting stub fixtures.
4. **The CI gate that would have caught #1 was authored as a comment.** T2.9 is checked off but its acceptance criterion isn't met. If T2.9 stays closed, neither finding #1 nor any future Mode A regression has a CI signal.
5. **Scope is the underlying decision.** The TRS assumed content-pipeline would provide `score_jobs` + `cmd_score_jobs` + `render_report` + `ats_boards`. It doesn't, and there's no commitment in the TRS to *make* it. Phase 2's exit criteria are over-specified relative to today's sibling — even with perfect atlas code, the integration target isn't there. Every other finding is downstream of this single mismatch.

## Options (grouped by scope decision)

The four findings are entangled, so the response paths group by **what does Phase 2 claim?**, not by which finding to fix first.

**Option A — Full TRS, paid in full.** Update content-pipeline first (add `ScoreJobsUseCase`, `cmd_score_jobs`, `score_jobs_report`, `AtsBoardScraper`), then in atlas: add a real `SHELL:`/`CMD:` runner, switch `job_cli.yaml` to it, enable the `--extra job` CI leg with a content-pipeline checkout step, add use-case-boundary integration tests. Highest cost; gates atlas Phase 2 on a downstream change in another repo. Honors the TRS as written.

**Option B — Retarget Mode A to the API that exists today.** Rewrite `score_jobs_adapter` against `ClassifyUseCase` (the use case that actually exists), redefine `gate_shortlist`'s rendered content around classify output, drop the `AtsBoardScraper` import from `capture_adapter` (use `rss` + `generic` only). Still fix `RAW:` → `SHELL:` and the CI/test gaps. Keeps Mode A in scope but quietly changes its semantics — the `gate_shortlist` experience becomes "classified pending items" rather than "scored shortlist with render_report." Phase 2 ships honestly without waiting on content-pipeline; gate experience is thinner than the TRS promised.

**Option C — Ship Mode B honestly, defer Mode A.** Strip `job.yaml` and the `LibraryStageRunner`/adapters from Phase 2's deliverables; keep `job_cli.yaml` only. Add the real `SHELL:` runner so `job_cli` actually dispatches to `content-pipeline` on `PATH`. Mark T2.9 / Mode A items deferred in the TRS, with a clear "blocked on content-pipeline API." Smallest merge-now footprint; satisfies the "atlas runs without content-pipeline installed" criterion authentically. Loses the proof-of-life for `LIB:` / `LibraryStageRunner` until a future phase.

**Option D — Docs-only fix.** Change the TRS and docs to admit `job_cli` is Claude-mediated, accept that `--workflow job` is non-functional until content-pipeline catches up, leave the code as-is. Included only for completeness — the worst of the four because it ships a workflow whose `name` and intent diverge from its behavior.

**Cross-cutting work required in A, B, and C (not in D):**

- **Direct-CLI runner.** Cleanest shape: a `SHELL:` (or `CMD:`) prefix dispatched by `CompositeStageRunner` to a new small `ShellStageRunner` that calls `subprocess.run([...], shell=False, timeout=stage.timeout_s)` with a workflow-file-level allow-list (e.g., first token must be in `{"content-pipeline"}`). Failure modes (`FileNotFoundError`, `TimeoutExpired`, non-zero exit) map to `StageOutcome(status="failure", error_type=...)`, never raise. ~80–150 LoC + tests. Without this, the namespacing distinction between `job.*` and `job_cli.*` metrics is meaningless.
- **Tests at the boundaries the TRS specified.** Mode A: patch `ScoreJobsUseCase` / `CaptureUseCase` themselves (so the real adapter import path runs). Mode B: drop a stub `content-pipeline` binary on `PATH` inside the test fixture (so the real `subprocess.run` path runs). Optionally back both with a `tests/_fakes/content_pipeline_stub/` package wired through `[project.optional-dependencies.test-stub]` so CI can prove the import path without needing the sibling repo.
- **Real CI signal.** Either enable `test-job-extra` with a stub or real content-pipeline checkout, or uncheck T2.9 and document the gap. Either is fine; leaving the checkmark with a commented-out job is not.
- **Narrow `LibraryStageRunner`'s `ImportError` catch.** Distinguish "atlas adapter module not importable" from "content-pipeline module not importable from inside an adapter body" — only the former should produce `content_pipeline_not_installed`; the latter should produce `library_adapter_error` with the original message. Without this, the same class of failure as #1 will recur invisibly.

## Recommendation

The review's two-critical / one-important framing is right, but the actionable framing is **decide scope first, code changes second.** Options A vs B vs C are the question "what is Phase 2's exit criterion now that we know the sibling API doesn't match the TRS?" The code changes flow deterministically from that answer.

I lean toward **Option C as the merge-now path** — it's the only one where Phase 2 has a working, demonstrable deliverable that doesn't depend on either an out-of-repo change (A) or a quiet semantic rewrite of the TRS (B) — then promote to B (and eventually A) as content-pipeline catches up. Whichever path is chosen, the cross-cutting work above is required to make the *next* iteration's findings catchable in CI rather than discoverable only by code review.

## Next Steps

1. Decide between Options A / B / C / D above. This is the gating question — everything else is downstream.
2. Independent of which option: introduce and test a real direct CLI runner (`SHELL:`/`CMD:`), or revise the Mode B contract in the TRS.
3. Independent of which option: either enable the `job` extra CI leg (with stub or real content-pipeline) or uncheck T2.9.
4. Independent of which option: narrow `LibraryStageRunner`'s `ImportError` handling so adapter-body imports surface as `library_adapter_error`, not `content_pipeline_not_installed`.
5. Add integration coverage that exercises the real adapter modules instead of patching `_import_adapter`.

Please review the findings and approve which option to implement before I proceed with any fixes.
