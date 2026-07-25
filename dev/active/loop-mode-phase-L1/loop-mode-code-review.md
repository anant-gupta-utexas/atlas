# Code Review — Loop Mode Phases L0 + L1

**Reviewer:** Code Reviewer persona (`/consult-experts`)
**Date:** 2026-07-24
**Scope:** commits `81f55fb` (Phase L0) and `adbc232` (Phase L1)
**Artifacts reviewed:** `src/atlas/cli_backend.py`, `src/atlas/deliverer.py`, `src/atlas/plumb_io.py`, `src/atlas/orchestrator.py`, `src/atlas/workflows/loop_dev.yaml`, `tests/fixtures/codex_jsonl/*`, both phase TRS triads
**Verification performed:** full suite re-run (`301 passed, 1 xfailed` — matches the claim), call-site grep for every new API, diff read of `orchestrator.py`

---

## Verdict

**Approve.** Both phases are honest, well-tested, and the docs match the code — which is rarer than it should be. Every claim I spot-checked held up: the byte-identity invariant, the xfail reason, the coverage numbers, the "no caller yet" scope boundary. The `Pipeline.step()` off-by-one fix (`adbc232`) is a genuine latent-bug catch, correctly attributed to `loop_dev.yaml` surfacing it.

Findings below are **one Medium and four Low/Nit**. None block the phase. The Medium is a correctness risk that is already flagged in your own docs — my contribution is arguing it should be a *hard* gate rather than a documented caveat.

---

## Findings by severity

### 🟡 Medium — M1: `codex_usage_to_tokens` can silently over-count ~4×, and nothing fails loudly if it's wrong

**Location:** [`cli_backend.py:382-392`](../../src/atlas/cli_backend.py#L382)

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

---

### 🟢 Low — L1: `CodexBackend.build_argv` sets `-C`, but `SubprocessStageRunner` overrides the actual cwd

**Location:** [`cli_backend.py:267`](../../src/atlas/cli_backend.py#L267) vs [`orchestrator.py:649`](../../src/atlas/orchestrator.py#L649)

`build_argv` computes `primary = add_dirs[-1]` and passes `-C <primary>` — for an `isolate: true` stage that's the worktree path. But the runner spawns with `cwd=str(atlas_root)` (the atlas install root, chosen so Claude's workspace-scoped plugins resolve).

For Codex this is probably harmless — `-C` should win over the inherited cwd — but it's **unverified against a real `codex exec` invocation**, and the two mechanisms disagree about where the agent is supposed to be working. The `cwd=atlas_root` choice exists for a Claude-specific reason (plugin resolution) that has no Codex analogue.

**Recommendation:** add this to T-L1.8's manual smoke checklist explicitly — confirm the Codex run actually writes into the worktree, not into the atlas install root. A one-line assertion during that manual run closes it. If it turns out `-C` doesn't fully win, the fix is per-backend cwd selection, which is a small change but one you'd rather discover in a smoke test than in a run that edited the wrong repo.

---

### 🟢 Low — L2: `_parse_pr_url` returns `number=0` on a malformed URL instead of failing

**Location:** [`deliverer.py:110-114`](../../src/atlas/deliverer.py#L110)

```python
number = int(match.group(1)) if match else 0
return PrRef(number=number, url=url)
```

Your T-L0.6 implementation note explains the choice: a malformed-but-zero-exit `gh` response isn't a documented failure mode worth hardening in L0. Reasonable for L0 in isolation. But `PrRef(number=0)` is an in-band sentinel that a future L2 caller will plausibly use to comment on / label / close an issue — and `0` is not a valid PR number anywhere. It will fail later, further from the cause.

**Recommendation:** L2 concern, not an L0 defect. When `loop.py` first consumes `PrRef.number`, either raise `DeliveryError` here or make the field `int | None`. Worth a BACKLOG line now so it isn't rediscovered by a confusing `gh api /pulls/0` 404.

---

### 🟢 Low — L3: `Deliverer`'s `main` check is exact-match only

**Location:** [`deliverer.py:62`](../../src/atlas/deliverer.py#L62)

`if branch == "main"` doesn't cover `master`, `refs/heads/main`, or a repo whose default branch is something else entirely. The hardcoded-argv defense (no `--force`, explicit `-u origin <branch>`) is the real protection and it's solid — this assertion is defense-in-depth, which is why this is Low and not Medium.

Still, defense-in-depth that only covers one spelling of the hazard gives more confidence than it earns. Since `GhPrDeliverer` already holds `repo_root`, you could query the actual default branch, or minimally extend to a small frozenset (`{"main", "master"}`) plus a `refs/heads/` strip. The existing security test is the right shape — assert the dangerous call never fires — so extending it is cheap.

---

### ⚪ Nit — N1: `CodexUsageStats.total_cost_usd` is a permanently-`None` field

**Location:** [`cli_backend.py:242`](../../src/atlas/cli_backend.py#L242)

Documented three times (dataclass docstring, `parse_usage` comment, tasks.md) as always `None`, existing "only for call-site symmetry with `UsageStats`." The documentation is genuinely excellent — anyone who encounters it will not file a bug.

That said: a field that is structurally always `None` is a field that invites `if usage.total_cost_usd:` somewhere downstream. The symmetry it buys is notional, since no shared call site consumes both types polymorphically today (`codex_usage_to_tokens` takes `CodexUsageStats` specifically). Consider dropping it — but this is genuinely a matter of taste, and the current version is safe because it's so thoroughly documented. **No action needed.**

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

| # | Action | Severity | Owner phase |
|---|---|---|---|
| 1 | Treat T-L1.1's cache-semantics capture as a hard gate before any analyzed Codex run; or persist the raw 4-field breakdown | Medium | L1 (before L2 data collection) |
| 2 | Add "confirm Codex writes into the worktree, not atlas_root" to T-L1.8's checklist | Low | L1 |
| 3 | Soften the `codex_usage_to_tokens` docstring's confidence about OpenAI's convention | Nit | L1 |
| 4 | BACKLOG: `PrRef.number == 0` sentinel → raise or `int \| None` when L2 first consumes it | Low | L2 |
| 5 | Extend the branch-safety assertion beyond exact `"main"` | Low | L1 or L2 |

Per the Code Reviewer persona's operating principle, **no fixes have been applied** — these await your approval.
