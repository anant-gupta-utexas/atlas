# Code Review — Loop Mode Phases L0 + L1

**Reviewer:** Code Reviewer persona (`/consult-experts`)
**Date:** 2026-07-24
**Scope:** commits `81f55fb` (Phase L0) and `adbc232` (Phase L1)
**Artifacts reviewed:** `src/atlas/cli_backend.py`, `src/atlas/deliverer.py`, `src/atlas/plumb_io.py`, `src/atlas/orchestrator.py`, `src/atlas/workflows/loop_dev.yaml`, `tests/fixtures/codex_jsonl/*`, both phase TRS triads
**Verification performed:** full suite re-run (`301 passed, 1 xfailed` — matches the claim), call-site grep for every new API, diff read of `orchestrator.py`
**Status:** all five findings **closed** (#4 by Phase L2's T-L2.12; #1/#2/#3/#5 on 2026-07-25) — see [Recommended actions](#recommended-actions)

---

## Verdict

**Approve.** Both phases are honest, well-tested, and the docs match the code — which is rarer than it should be. Every claim I spot-checked held up: the byte-identity invariant, the xfail reason, the coverage numbers, the "no caller yet" scope boundary. The `Pipeline.step()` off-by-one fix (`adbc232`) is a genuine latent-bug catch, correctly attributed to `loop_dev.yaml` surfacing it.

Findings below are **one Medium and four Low/Nit**. None block the phase. The Medium is a correctness risk that is already flagged in your own docs — my contribution is arguing it should be a *hard* gate rather than a documented caveat.

> **Update (2026-07-25): all five findings are now closed.** See [Recommended actions](#recommended-actions) for how each was resolved. The finding bodies below are preserved as originally written — they record the reasoning, and two of them were resolved by a *different* route than they proposed once plumb v1.1.0 and a local `codex` CLI turned out to be available. Suite after fixes: **415 passed, 1 xfailed**; `ruff`/`ruff format`/`mypy --strict` clean; 100% coverage on `cli_backend.py` and `deliverer.py`.

---

## Findings by severity

### 🟡 Medium — M1: `codex_usage_to_tokens` can silently over-count ~4×, and nothing fails loudly if it's wrong

**Location (as reviewed, `adbc232`):** `cli_backend.py:382-392` → now [`codex_usage_to_tokens`](../../../src/atlas/cli_backend.py#L391)

```python
in_tokens = (usage.input_tokens or 0) + (usage.cached_input_tokens or 0)
```

You already know this one — Pending Decision #4, called out in the L1 tasks as "the one open item that can silently produce wrong numbers." I'm escalating it rather than repeating it, for one reason: **the failure mode is silent and the data is durable.**

On the `success.jsonl` fixture (`input_tokens: 16668`, `cached_input_tokens: 13056`), the two interpretations differ by 78%. Plumb sums `(in, out)` into a single `spans.tokens` column, so once these rows land there is no way to reconstruct which convention produced them — the split is explicitly non-durable (your own Resolved Decision #12). A corrected reduction rule later cannot retroactively fix history, unlike the query-time cost synthesis you correctly designed for in Decision #10.

The `logger.debug` of raw values (`cli_backend.py:348-355`) is good instinct but insufficient: debug logs are ephemeral and unqueryable, while the wrong number is permanent and queryable. Engine A/B comparison — the entire point of v3's token measurement (§13 #12) — is exactly the analysis this corrupts.

**Recommendation (pick one):**
1. **Preferred:** make T-L1.1's cold/warm-cache capture pair a *hard* precondition for any Codex run whose spans are written to a plumb DB you intend to analyze. Cheap: two runs of the same prompt, compare `input_tokens` across them.
2. If Codex runs land before T-L1.1: persist the raw four-field breakdown somewhere durable and queryable (a span attribute, or a JSONL sidecar keyed by `span_id`) so the reduction can be recomputed. The `(in, out)` tuple sent to plumb stays as-is; you're just keeping the receipts.

Also worth noting: the docstring justifies the addend choice via "Anthropic's convention — Claude's `input_tokens` excludes its own cache fields." That's sound reasoning about Anthropic, but it's an inference about **OpenAI's** schema. The comment reads slightly more confident than the evidence supports; consider softening to "assumed, pending T-L1.1."

> **✅ RESOLVED 2026-07-25 — via option 2, which became the better option after this review was written.**
>
> The review recommended option 1 (gate on T-L1.1) as *preferred* because option 2's durable sink didn't exist: `spans.attributes` was an unlanded plumb proposal, leaving only a JSONL sidecar. **plumb v1.1.0 has since shipped the column** (`ALTER TABLE spans ADD COLUMN attributes TEXT`, schema `user_version` 1→2), verified against the installed package rather than the changelog. So the raw breakdown went to its proper home and no sidecar was built.
>
> - `PlumbIO.record_span()` takes an `attributes: dict[str, object] | None` kwarg, threaded to `add_span(attributes=...)`. Defaults to `None`, so every existing call site is unchanged.
> - New `cli_backend.codex_usage_attributes()` returns the raw four fields plus `CODEX_TOKEN_REDUCTION_RULE` — a version stamp naming the convention that produced the stored total, so a later correction knows exactly what to undo.
> - `codex_usage_to_tokens`'s docstring now says the addend rule is **assumed**, names T-L1.1 as what settles it, and points at the recovery path (this also closes action #3).
>
> Verified end-to-end against a real SQLite DB written through plumb's own adapter:
>
> ```
> spans.tokens (summed)  = 29729     # in/out split lost here, exactly as Decision #12 documents
> attributes             = {"engine": "codex", "token_reduction_rule": "cached_input_as_addend_v1",
>                           "input_tokens": 16668, "cached_input_tokens": 13056,
>                           "output_tokens": 5, "reasoning_output_tokens": 0}
> if Decision #4 is WRONG → 16668    # recomputable from stored attrs; no data loss
> ```
>
> **This changes the finding's character, not just its status.** The original severity rested on "silent *and* permanent." The breakdown is now durable and queryable, so a wrong Pending Decision #4 is a **recomputable** error. **T-L1.1 drops from a data-integrity blocker to a measurement-accuracy task** — still worth running, no longer urgent, and no longer something that must precede Codex data collection.
>
> Pinned by `test_codex_usage_attributes_preserves_raw_breakdown` (asserts the stored breakdown actually reproduces the reduced total — otherwise the "recomputable" claim is false) and `test_codex_usage_attributes_is_json_serializable` (plumb fail-closes on non-serializable attributes).

---

### 🟢 Low — L1: `CodexBackend.build_argv` sets `-C`, but `SubprocessStageRunner` overrides the actual cwd

**Location (as reviewed, `adbc232`):** `cli_backend.py:267` vs `orchestrator.py:649` → now [`build_argv`](../../../src/atlas/cli_backend.py#L270) vs [`SubprocessStageRunner.run`](../../../src/atlas/orchestrator.py#L649)

`build_argv` computes `primary = add_dirs[-1]` and passes `-C <primary>` — for an `isolate: true` stage that's the worktree path. But the runner spawns with `cwd=str(atlas_root)` (the atlas install root, chosen so Claude's workspace-scoped plugins resolve).

For Codex this is probably harmless — `-C` should win over the inherited cwd — but it's **unverified against a real `codex exec` invocation**, and the two mechanisms disagree about where the agent is supposed to be working. The `cwd=atlas_root` choice exists for a Claude-specific reason (plugin resolution) that has no Codex analogue.

**Recommendation:** add this to T-L1.8's manual smoke checklist explicitly — confirm the Codex run actually writes into the worktree, not into the atlas install root. A one-line assertion during that manual run closes it. If it turns out `-C` doesn't fully win, the fix is per-backend cwd selection, which is a small change but one you'd rather discover in a smoke test than in a run that edited the wrong repo.

> **✅ RESOLVED 2026-07-25 — answered by experiment, not deferred to T-L1.8.**
>
> This review assumed no Codex CLI was reachable, so it recommended a manual smoke check. That assumption was wrong: `codex-cli 0.144.4` — the exact version L1 verified its schema against — was installed locally. The ambiguity was therefore settled directly, replicating atlas's spawn pattern (`cwd=<elsewhere>`, `-C <target>`, marker file in each):
>
> | `-C` form | Result |
> |---|---|
> | **absolute** | exit 0; agent read `marker-in-target` — **`-C` wins, inherited cwd irrelevant** |
> | **relative** | exit 1, `No such file or directory` — resolved against the inherited cwd |
>
> **The existing code was already correct.** `add_dirs` reach `build_argv` as absolute paths (built from `ctx.repo_root` / `ctx.worktree_path`), so Codex runs work in the worktree as intended. No production change was needed.
>
> What *was* missing is a guard on the invariant that makes it correct. `test_codex_backend_build_argv_paths_are_absolute` now asserts `-C` and every `--add-dir` path is absolute, with the empirical finding recorded in its docstring — so a future refactor that starts passing relative paths fails in CI rather than in a live run that silently edits `atlas_root` instead of the worktree.
>
> `orchestrator.py`'s `cwd=atlas_root` is left alone: it exists for Claude's workspace-scoped plugin resolution and is harmless for Codex given absolute `-C`.

---

### 🟢 Low — L2: `_parse_pr_url` returns `number=0` on a malformed URL instead of failing

**Location (as reviewed, `81f55fb`):** `deliverer.py:110-114` → now [`_parse_pr_url`](../../../src/atlas/deliverer.py#L161)

```python
number = int(match.group(1)) if match else 0
return PrRef(number=number, url=url)
```

Your T-L0.6 implementation note explains the choice: a malformed-but-zero-exit `gh` response isn't a documented failure mode worth hardening in L0. Reasonable for L0 in isolation. But `PrRef(number=0)` is an in-band sentinel that a future L2 caller will plausibly use to comment on / label / close an issue — and `0` is not a valid PR number anywhere. It will fail later, further from the cause.

**Recommendation:** L2 concern, not an L0 defect. When `loop.py` first consumes `PrRef.number`, either raise `DeliveryError` here or make the field `int | None`. Worth a BACKLOG line now so it isn't rediscovered by a confusing `gh api /pulls/0` 404.

> **✅ RESOLVED — closed by Phase L2's own T-L2.12 (2026-07-25), before this fix pass began.**
>
> Landed exactly where the review predicted it would matter: L2 is the first phase whose code reads `PrRef.number` back (`loop.py` composes `Closes #<n>` bodies and `queue_gh.py` comments/labels by number). `_parse_pr_url` now raises `DeliveryError` on a match failure rather than sentinel-ing to `number=0`.
>
> The chosen option was "raise", not `int | None` — correct call: a `None` would push the same "can't act on this PR" decision onto every caller, whereas raising routes into `loop.tick()`'s existing `DeliveryError` handler, which leaves the issue `atlas:working` for manual triage. Covered by `test_deliver_malformed_pr_url_raises_instead_of_number_zero_sentinel`, which also asserts cleanup is skipped — the PR does exist at that point (gh exited 0), so the worktree is preserved for recovery.

---

### 🟢 Low — L3: `Deliverer`'s `main` check is exact-match only

**Location (as reviewed, `81f55fb`):** `deliverer.py:62` → now [`deliver`'s branch guard](../../../src/atlas/deliverer.py#L105)

`if branch == "main"` doesn't cover `master`, `refs/heads/main`, or a repo whose default branch is something else entirely. The hardcoded-argv defense (no `--force`, explicit `-u origin <branch>`) is the real protection and it's solid — this assertion is defense-in-depth, which is why this is Low and not Medium.

Still, defense-in-depth that only covers one spelling of the hazard gives more confidence than it earns. Since `GhPrDeliverer` already holds `repo_root`, you could query the actual default branch, or minimally extend to a small frozenset (`{"main", "master"}`) plus a `refs/heads/` strip. The existing security test is the right shape — assert the dangerous call never fires — so extending it is cheap.

> **✅ RESOLVED 2026-07-25 — both proposed fixes applied, layered.**
>
> The review offered the frozenset and the default-branch query as alternatives. They cover different gaps, so both landed:
>
> - **`_PROTECTED_BRANCHES`** = `{main, master, trunk, develop}`, compared after `refs/heads/` strip, lowercase, and whitespace trim. Catches the common spellings with no subprocess.
> - **`_detect_default_branch()`** reads `git symbolic-ref --short refs/remotes/origin/HEAD` — local, read-only, no network — catching a repo whose trunk is named something the static set can't anticipate (e.g. `production`).
>
> **The probe fails open by design**, and that's the load-bearing decision here: a missing `origin/HEAD` is routine in fresh clones and shallow CI checkouts, so a probe that failed *closed* would block legitimate delivery on a condition unrelated to safety. The static set still applies when detection is unavailable, and the hardcoded argv shape remains the primary protection — so failing open degrades to exactly the guarantee that existed before this fix, never worse. An empty/whitespace branch name is also now rejected.
>
> Covered by a 10-case parametrized rejection test (`main`, `master`, `trunk`, `develop`, `Main`, `MASTER`, `refs/heads/main`, `refs/heads/master`, `"  main  "`), an unusual-default-branch test asserting no push follows detection, a fail-open test, and an empty-branch test. All assert the dangerous call never fires, per the existing test's shape.

---

### ⚪ Nit — N1: `CodexUsageStats.total_cost_usd` is a permanently-`None` field

**Location (as reviewed, `adbc232`):** `cli_backend.py:242` → now [`CodexUsageStats`](../../../src/atlas/cli_backend.py#L230) (field removed)

Documented three times (dataclass docstring, `parse_usage` comment, tasks.md) as always `None`, existing "only for call-site symmetry with `UsageStats`." The documentation is genuinely excellent — anyone who encounters it will not file a bug.

That said: a field that is structurally always `None` is a field that invites `if usage.total_cost_usd:` somewhere downstream. The symmetry it buys is notional, since no shared call site consumes both types polymorphically today (`codex_usage_to_tokens` takes `CodexUsageStats` specifically). Consider dropping it — but this is genuinely a matter of taste, and the current version is safe because it's so thoroughly documented. **No action needed.**

> **✅ RESOLVED 2026-07-25 — field dropped, on maintainer instruction to fix all findings.**
>
> Note this reverses the "no action needed" verdict above. That verdict stands as written — the field *was* safe, because the documentation was thorough. Removing it is a taste call the maintainer made, not a defect the review uncovered.
>
> `CodexUsageStats` is now four fields (`input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_output_tokens`). The dataclass docstring explains the *absence* — so a reader diffing against `UsageStats` sees a deliberate asymmetry rather than an oversight — and `test_codex_usage_stats_has_no_cost_field` asserts it stays gone, since the natural instinct on seeing the asymmetry is to "fix" it.
>
> ⚠️ **This is a breaking change to a public dataclass.** No production consumer existed (only test references, all updated) and TRD-v3 §3.3 already documents Codex as reporting no cost figure, so in-repo risk is nil. But any out-of-repo code constructing `CodexUsageStats` **positionally** will now bind the wrong values rather than fail loudly — keyword construction is unaffected. Flagged rather than assumed harmless.

---

## What's notably good

Calling these out because they're the parts worth preserving as the pattern for L2:

- **Byte-identity as a regression invariant.** Gating every loop-mode flag on `extra_flags.get(...)` with an empty-dict default, then proving attended argv is byte-identical, is exactly the right way to add a mode to a shipped path. `test_dev_pipeline_unaffected_by_phase_l0` is load-bearing — keep it that way.
- **Fail-closed preflight with no subprocess spawned.** `CodexBackend.preflight()` checks `OPENAI_API_KEY`, then `$CODEX_HOME/auth.json`, then `~/.codex/auth.json`, and returns before anything spawns. Testing that the dangerous call *never fires* — rather than that the return value looks right — is the correct shape for a security test.
- **The `Pipeline.step()` fix is properly scoped and properly explained.** Mirroring the guard the gated branch already had, rather than inventing new control flow, is the minimal correct fix. The commit message explains why it never fired before (`dev.yaml`/`job.yaml` gate their final stage) — that's the sentence that saves the next reader twenty minutes.
- **The xfail is framed as superseded-API, not "broken test."** With the three replacement modules named. This is the difference between a marker someone deletes in six months and one they can act on.
- **`RunResult` widening was reconciled in writing before it was implemented.** Appendix A said "pause and reconcile"; you paused and reconciled, in the TRS, with the alternative scoping explicitly offered. That's the process working.
- **`parse_result` never infers failure from event content** (Decision #8), with the honest consequence — exit-0-but-useless reports success — written down rather than hidden. `verify` plus the PR gate are the right catchers.

---

## Architectural fit

No concerns. Both phases stay inside the boundaries the system design sets:

- `CodexBackend` implements the same 3-method `CliBackend` Protocol as its siblings; `parse_usage` stays additive and off-Protocol (L0 Decision #1), so the Protocol is still 3 methods across all three backends.
- Backends still never spawn subprocesses — the trust boundary in `cli_backend.py`'s module docstring holds.
- `orchestrator.py` was touched exactly once, narrowly, with the deviation documented against Appendix A.
- `loop_dev.yaml` adds no new schema features; it's the first workflow with an ungated final stage, which is what surfaced the `step()` bug.
- No new file types (router module, agent registry) — the CLAUDE.md design-review tripwire isn't hit.

**On the "nothing calls this yet" observation:** `parse_usage`, `codex_usage_to_tokens`, `Deliverer`, and `record_span(tokens=...)` have zero production call sites. I flag this only to confirm it's deliberate — the L1 plan states it plainly ("Neither has a caller yet"), lists `Deliverer` wiring under out-of-scope, and `SubprocessStageRunner` still hardcodes `extra_flags={}`. That's a coherent primitives-before-driver sequencing, not dead code. **The thing to watch is L2:** these primitives have been unit-tested but never integration-proven through the runner, so L2's first task should be wiring them through `SubprocessStageRunner` and confirming a real `code_gen` span carries real tokens — which is, correctly, exactly what TRD-v3 §13 #1 already says.

---

## Open exit criteria (unchanged by this review)

These remain genuinely open and this review does not close any of them:

| Item | Status |
|---|---|
| T-L0.8 — live attended run | Not run; needs real `claude` session |
| T-L0.9 — delivery smoke vs scratch repo | Not run; needs real `gh` auth |
| T-L1.1 — write-heavy Codex capture | Not run; **now also gates M1** |
| T-L1.8 — manual smoke, both engines | Not run; **add L1's cwd check to it** |

The "code-complete, manual verification pending" framing in both tasks.md files is accurate. No checkbox is marked that shouldn't be.

---

## Recommended actions

| # | Action | Severity | Owner phase | Status |
|---|---|---|---|---|
| 1 | Treat T-L1.1's cache-semantics capture as a hard gate before any analyzed Codex run; or persist the raw 4-field breakdown | Medium | L1 (before L2 data collection) | **Closed (2026-07-25)** — took the second option, which plumb v1.1.0 made available: `spans.attributes` now stores the raw 4-field breakdown plus `CODEX_TOKEN_REDUCTION_RULE`. T-L1.1 remains valuable but is **no longer load-bearing for data integrity** — a wrong Pending Decision #4 is now recomputable from stored data instead of permanently corrupting history. |
| 2 | Add "confirm Codex writes into the worktree, not atlas_root" to T-L1.8's checklist | Low | L1 | **Closed (2026-07-25)** — settled empirically instead of deferring to a manual check: `codex-cli 0.144.4` was available locally, so the ambiguity was tested directly. Absolute `-C` wins over inherited cwd; relative `-C` resolves against cwd and exits 1. Pinned by `test_codex_backend_build_argv_paths_are_absolute`. |
| 3 | Soften the `codex_usage_to_tokens` docstring's confidence about OpenAI's convention | Nit | L1 | **Closed (2026-07-25)** — now states the cached-as-addend rule is **assumed**, names T-L1.1 as what settles it, and points at the attributes-based recovery path. |
| 4 | BACKLOG: `PrRef.number == 0` sentinel → raise or `int \| None` when L2 first consumes it | Low | L2 | **Closed (T-L2.12, 2026-07-25)** — `_parse_pr_url` raises `DeliveryError` on a malformed `gh pr create` URL instead of sentinel-ing to `number=0`. |
| 5 | Extend the branch-safety assertion beyond exact `"main"` | Low | L1 or L2 | **Closed (2026-07-25)** — `_PROTECTED_BRANCHES` + `refs/heads/` strip + case/whitespace normalization, plus a fail-open `origin/HEAD` probe for unusually-named default branches. |

**All five findings are now closed.** Actions #1, #2, #3, and #5 were fixed on 2026-07-25 at the maintainer's instruction ("fix all the issues"); #4 had already been closed by T-L2.12.

Two of these closed differently than this review originally proposed, because the constraints changed between writing and fixing:

- **#1** was written when `spans.attributes` was an unlanded plumb proposal, so the review recommended a JSONL sidecar as the fallback. plumb **v1.1.0 has since shipped the column** (verified against the installed package, not just the changelog), so the durable breakdown went to its proper home. The sidecar is unnecessary.
- **#2** recommended a manual smoke check because the review assumed no Codex CLI was reachable. It was — `codex-cli 0.144.4`, the exact version L1 verified against — so the question was answered by experiment rather than deferred. The current code was already correct; the test now prevents a future refactor from silently breaking it.

**Still genuinely open (unchanged by this pass):** T-L0.8, T-L0.9, T-L1.1, and T-L1.8 remain unrun, as they require live external systems. Finding #1's closure lowers T-L1.1's stakes from "data integrity" to "measurement accuracy" — worth running, no longer urgent.
