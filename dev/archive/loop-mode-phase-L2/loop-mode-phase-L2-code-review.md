# Code Review — Loop Mode Phase L2 (loop daemon)

**Reviewer:** Code Reviewer persona (`/consult-experts`)
**Date:** 2026-07-25
**Scope:** commits `03410de` (T-L2.1–L2.8) and `8bfef83` (T-L2.9–L2.14)
**Artifacts reviewed:** `src/atlas/loop.py`, `queue_gh.py`, `triage.py`, `config.py`, `cli.py` (loop sub-app + `make_pipeline`), `deliverer.py`, `tests/unit/test_loop.py`, `test_queue_gh.py`, `test_triage.py`, `test_cli_loop.py`, `tests/integration/test_loop_e2e.py`, the full L2 TRS triad + decisions file, and the L1 code review it follows through on
**Verification performed:** full suite re-run (`400 passed, 1 xfailed` — matches the claim exactly), read of every new module end-to-end, call-site grep for `PlumbIO`/`WorktreeManager`/`Deliverer` reuse, cross-check of each of the 18 Decisions against the code that claims to implement it

---

## Verdict

**Approve with changes.** The phase is well-structured and the discipline from L0/L1 carried forward: `tick()` really is a linear function, the `gh` boundary is real and grep-enforced, `LoopState` persistence is atomic (tmp + `replace`), and the decisions file is unusually honest about what was deferred and why. The 18-decision doc is genuinely load-bearing — I could check the code against it line by line, which is not typical.

But **one Critical finding means the planned lane cannot currently produce a PR at all**, and a second means `sync_prior_prs()` writes its scores to the wrong plumb run whenever it processes more than one PR per tick. Both are in code paths the unit tests mock past, which is why 400 green tests didn't catch them. Neither is a design error — both are integration seams that were unit-tested at the wrong altitude.

Findings: **2 Critical, 4 Important, 5 Minor.**

The honest framing on T-L2.13 in the commit message ("blocked on a `plugin_resolver.resolve()` gap that needs a maintainer decision") is correct and worth keeping. C1 below is very likely a *second* thing that manual smoke would have caught immediately — which is itself the argument for not letting L3 start before T-L2.13 runs.

---

## Critical Issues (must fix)

### 🔴 C1 — The planned lane creates an empty worktree *after* the work is done, then opens a PR with no commits

**Location:** [`loop.py:302-319`](../../../src/atlas/loop.py#L302) (`run_planned_first_pass`)

```python
result_proc = subprocess.run(argv, cwd=str(repo_root), ...)   # ← writes into repo_root
...
worktree = WorktreeManager(repo_root)
wt_path = worktree.create(slug=ctx_slug, run_id=run_id)       # ← fresh branch off main, empty
branch = f"atlas/{ctx_slug}-{run_id[:8]}"
pr_ref = queue_gh.deliver_pr(..., worktree_path=wt_path, ...)  # ← pushes zero commits
```

Three compounding problems, in execution order:

1. **The `dev-docs-be` subprocess runs with `cwd=repo_root`, not in a worktree.** Whatever TRS triad it produces lands in the *main working tree*, dirty and uncommitted.
2. **`WorktreeManager.create()` calls `_check_repo_clean()`** ([`worktree.py:50`](../../../src/atlas/worktree.py#L50)). Step 1 just made the repo dirty by writing the triad. So `create()` raises `WorktreeError` on the happy path. This is caught by `tick()`'s handler and reported as a failure — meaning **the planned lane's success path terminates in a caught error.**
3. Even if the repo *were* clean (agent produced nothing), `create()` branches off `main` and nothing is ever `git add`/`git commit`-ed — `grep -n "commit\|git add" src/atlas/loop.py` returns nothing. `deliver_pr` → `git push -u origin <branch>` pushes a branch identical to `main`, and `gh pr create` fails with "No commits between main and ...".

So there is no input under which this function opens a plan PR. TRD-v3 §13 #6 — L2's own exit criterion — requires exactly that.

Contrast with the quick lane, which is correct: `Pipeline` creates the worktree *before* the isolated stage ([`orchestrator.py:305-306`](../../../src/atlas/orchestrator.py#L305)), the agent works inside it, and `loop.py` reads back `result.ctx.worktree_path`. The planned lane inverted that order.

**Why the tests missed it:** `test_loop.py:257` patches `run_planned_first_pass` wholesale; `test_loop_e2e.py` mocks at the same boundary. The function's body has no direct test.

**Recommendation:** restructure to match the quick lane's ordering — create the worktree first, run `dev-docs-be` with `cwd=wt_path` (and `add_dirs=[wt_path]`), then commit the triad before delivering. Roughly:

```python
worktree = WorktreeManager(repo_root)
wt_path = worktree.create(slug=ctx_slug, run_id=run_id)
# ... run dev-docs-be with cwd=wt_path, add_dirs=[wt_path] ...
subprocess.run(["git", "add", "-A"], cwd=wt_path, check=True)
subprocess.run(["git", "commit", "-m", f"docs(plan): TRS triad for #{issue.number}"],
               cwd=wt_path, check=True)
# ... then deliver_pr(worktree_path=wt_path, ...)
```

Add a direct test for `run_planned_first_pass` against a real temp git repo (the `test_loop_e2e.py` fixture already builds one) asserting the branch has ≥1 commit ahead of `main` before `deliver_pr` is reached.

---

### 🔴 C2 — `sync_prior_prs()` reuses one `PlumbIO` handle across PRs; `close_run()`'s `_closed` latch silently drops all scores after the first

**Location:** [`loop.py:341-355`](../../../src/atlas/loop.py#L341)

A fresh `PlumbIO(real=True)` is constructed *inside* the loop body, so each PR gets its own object — that part is fine. The problem is the interaction with `close_run`'s idempotence latch:

```python
def close_run(self, *, run_id: str, status: str) -> None:
    if self._closed:
        return
    self._closed = True
```

`reopen_run` does reset `self._closed = False` ([`plumb_io.py:100`](../../../src/atlas/plumb_io.py#L100)), so the per-PR `PlumbIO` instances are individually coherent. **The real defect is different and worse:** `reopen_run` opens a *child run* with a new `run_id` and returns it, then `record_user_signal(run_id=active_run_id, ...)` writes through `self._run_handle` — ignoring the `run_id` argument entirely ([`plumb_io.py:185-192`](../../../src/atlas/plumb_io.py#L185)). That's fine here. But note what `sync_prior_prs` writes:

```python
span_id="",   # ← empty string, not None
```

`record_user_signal` forwards `span_id=""` straight into `add_score(..., span_id=span_id, ...)`. Every other call site in atlas passes a real span id. An empty-string foreign key into plumb's `scores.span_id` is either a constraint violation or a dangling reference depending on plumb's schema strictness — and since this is the *headline* signal of the whole phase (TRD-v3 §13 #5: "merging writes `user_signal`"), a silently-malformed score row defeats the exit criterion while looking green.

Secondly, and independently: **the `state.synced_pr_outcomes` dedupe list is appended to but never bounded or pruned.** It grows by one entry per merged/closed PR forever, is re-serialized to `loop-state.json` on every single `tick()` (line 449, ~every 60s), and is loaded and re-written in full each time. At the design cap of 20 runs/day this is slow-burn, but it is an unbounded on-disk structure in a process explicitly designed to run unattended for weeks.

**Recommendation:**
1. Pass a real span id, or `None` if plumb accepts it — check `record_user_signal`'s contract and make `span_id: str | None` explicit rather than sentinel-ing to `""`. Add a test asserting the score row's `span_id` is not `""`.
2. Bound `synced_pr_outcomes` (a `deque(maxlen=500)` serialized as a list, or prune entries older than N days). Dedupe only needs to outlive the window in which a PR outcome could be re-observed.

---

## Important Improvements (should fix)

### 🟠 I1 — `tick()` claims the issue, then reports `action="dispatched"` on the gh-identity failure path *before* any dispatch happens

**Location:** [`loop.py:419-435`](../../../src/atlas/loop.py#L419)

If `current_gh_user()` raises, `tick()` returns `TickResult(action="dispatched", ...)` with `detail="failed: could not resolve gh identity"`. Nothing was dispatched — the issue is still `atlas:ready`, unclaimed. `TickResult.action` is the machine-readable field (`Literal[...]`); `detail` is the human one. Anything that counts dispatches by `action` (a future `atlas loop status`, a weekly report in L4, a metrics query) over-counts.

The same overload happens on the exception path at line 467 — a run that failed *after* claiming is also `action="dispatched"`. That one is at least arguably true (a dispatch was attempted), but it's indistinguishable from success without string-matching `detail`, which is exactly what a typed `action` field exists to avoid.

**Recommendation:** add `"failed"` to the `action` Literal and use it for both paths, or add a separate `ok: bool`. Keep `detail` for humans.

### 🟠 I2 — `run_one_shot` / `run_planned_first_pass` return a hardcoded `0.0` cost, making `max_dollars_per_day` permanently inert

**Location:** [`loop.py:245`](../../../src/atlas/loop.py#L245), [`loop.py:320`](../../../src/atlas/loop.py#L320)

```python
return pr_ref, result.ctx.run_id, 0.0
```

`tick()` then does `state.dollars_today += cost`, which is always `+= 0.0`, so `budget_exhausted`'s dollar half can never trip. The commit message for `03410de` is admirably upfront about this ("cost extraction from RunResult is unwired, so `max_dollars_per_day` is currently inert"), and TRD-v3 §3.6 explains the underlying plumb P1-a dependency — so this is *known*, not hidden.

The problem is that it's known *in a commit message and a task file*, and invisible at the two places an operator actually looks: `atlas loop status` prints `Dollars today: $0.00 / $10.00` with total confidence, and `.atlas.toml`'s `[loop] max_dollars_per_day` reads like a working safety control. Someone configuring `max_dollars_per_day = 5` reasonably believes they have a spend cap. They do not.

Decision #17 also says triage-classifier cost should count toward the dollar cap "if measurable" — it currently isn't counted at all, and `_classify` doesn't even attempt token extraction.

**Recommendation:** make the inertness impossible to miss at runtime, not just in docs:
- `loop status` should print `Dollars today: (not tracked — pending plumb P1-a)` rather than `$0.00`.
- `Config.load` (or `run_forever` startup) should log a `WARNING` once if `max_dollars_per_day` is set to a non-default value, saying it will not be enforced.
- Consider raising at config-parse time if an operator explicitly sets it — a safety control that silently doesn't work is worse than one that refuses to load.

### 🟠 I3 — `cfg.loop.max_turns` is parsed, documented, and never used

**Location:** [`config.py:18`](../../../src/atlas/config.py#L18) declares it; `grep -rn "max_turns" src/atlas/` shows the only consumer is `cli_backend.py:110`, which reads it from a stage's `extra_flags`, not from `LoopConfig`.

Neither `run_one_shot` (via `make_pipeline`) nor `run_planned_first_pass` (which passes `extra_flags={}` explicitly at line 278) ever threads `cfg.loop.max_turns` through. It's a config knob that does nothing — the same class of problem as I2 but with no plumb dependency as an excuse. For an unattended loop, "max turns per run" is a real runaway-cost guard.

**Recommendation:** thread it into `extra_flags={"max_turns": str(cfg.loop.max_turns)}` in both lanes, or remove the field from `LoopConfig` until it's wired. Don't leave it half-present.

### 🟠 I4 — `reconcile_orphans` matches worktrees to active issues by title slug, which collides and strands

**Location:** [`loop.py:569-579`](../../../src/atlas/loop.py#L569)

```python
def _is_worktree_for_active_issue(worktree_dir, active_slugs) -> bool:
    return any(name.startswith(f"{slug}-") for slug in active_slugs)
```

`_slugify` truncates to 40 chars. Two issues titled "Add response-cache middleware to the Flask API layer" and "Add response-cache middleware to the Django API layer" produce the *same* slug. More importantly, this is a startup sweep that **deletes worktrees** (`worktree_manager.cleanup`), and it matches on a lossy, user-controlled string rather than the `run_id` that's already embedded in the directory name.

Two failure modes: a live run's worktree gets cleaned up because its slug didn't match (data loss — the agent's uncommitted work), or an orphan is retained forever because it collided with an active issue's slug (leak).

The directory name is `{slug}-{run_id[:8]}`, and `.atlas/current-run` already tracks the live run. Matching on the run-id suffix would be exact.

**Recommendation:** key the active set on `run_id[:8]` read from `StateStore`/`.atlas/current-run` (and, once concurrency > 1 in L4, from `LoopState`), not on re-slugified issue titles. At minimum, add a guard that never cleans up a worktree whose run_id matches the currently-active run.

---

## Minor Suggestions (nice to have)

### 🔵 m1 — `queue_gh.deliver_pr`'s try/except is a no-op

[`queue_gh.py:170-171`](../../../src/atlas/queue_gh.py#L170):

```python
except DeliveryError:
    raise
```

Catching an exception only to re-raise it unchanged does nothing but suggest to a reader that something was intended here. Delete the try/except; the pass-through is already the behavior.

### 🔵 m2 — `_find_linked_pr_number`'s docstring describes an implementation it doesn't have

[`queue_gh.py:211-217`](../../../src/atlas/queue_gh.py#L211) — the docstring discusses `timelineItems` being "unnecessarily heavy" and a `gh pr list --search "linked:<n>"`-style lookup, then the body does neither (it uses `closedByPullRequestsReferences`). It reads like notes from an abandoned approach. Same for `sync()`'s docstring at line 190-198, which describes matching "by number embedded in a `Closes #<n>` reference … resolved by the caller" — the caller doesn't do that either. Both are actively misleading to the next reader. Rewrite to describe what the code does.

### 🔵 m3 — `triage._classify` records `latency_ms=0.0` unconditionally

[`triage.py:81`](../../../src/atlas/triage.py#L81) initializes `latency_ms = 0.0` and never updates it, so every triage span in plumb reports zero latency. `run_planned_first_pass` does this correctly with `time.monotonic()` (line 263/283) — mirror that. Small, but it silently poisons any latency analysis over triage spans.

### 🔵 m4 — `_format_run_summary` takes a `cost` parameter it ignores

[`loop.py:496-497`](../../../src/atlas/loop.py#L496) — the signature accepts `cost: float` and the body never references it. Given I2, that's consistent (cost is always 0.0), but an unused parameter is a lint miss and will silently stay unused when cost *does* get wired. Either include it in the comment body or drop the parameter.

### 🔵 m5 — `run_forever`'s breaker check sleeps without persisting or logging

[`loop.py:510-512`](../../../src/atlas/loop.py#L510):

```python
if breaker_open(state, cfg.loop):
    time.sleep(cfg.loop.poll_interval_s)
    continue
```

When the breaker is open, `run_forever` short-circuits before `tick()` — so `state.last_tick_at` stops advancing and nothing is logged. An operator running `atlas loop status` during a 30-minute cooldown sees a stale `Last tick` and no indication the daemon is alive and deliberately waiting. `tick()` itself handles the breaker-open case correctly (returns `action="breaker_open"`, persists, updates `last_tick_at`) — this outer check duplicates that logic less well. Consider dropping the outer check and letting `tick()` handle it, or at minimum log at INFO each time it fires.

---

## Architecture Considerations

**What's genuinely good, and should be kept as precedent:**

- **The `gh` boundary is real.** Decision #15's grep test isn't ceremonial — the commit message reports it caught two raw `gh` calls during development, which were moved into `queue_gh.py` as `current_user()`/`find_run_id_comment()`. That's the test doing exactly its job. This is the third phase in a row where an "assert the dangerous thing never happens" test earned its keep.
- **`LoopState.persist` is atomic** (tmp-file + `replace`) and `load_or_init` degrades gracefully on corruption rather than crashing an unattended daemon. Correct instinct for this process shape.
- **The fixtures are real captures**, not synthesized — and the context file records the schema detail that mattered (`labels` objects carry `id`/`description`/`color`; parsing must read only `name`). That note is why `_list_labeled` is correctly defensive.
- **Decision #11's `make_pipeline` promotion** is the right call and is implemented faithfully — the quick lane really does construct `Pipeline` identically to `cli.py::run`. This is what prevents the drift the decision was written to prevent.

**Two structural concerns:**

1. **`loop.py` at 596 lines is at the edge of the repo's own limit** (CLAUDE.md: "files < 400 lines (800 max for complex logic)"). It's currently coherent, but L3 adds self-healing and a judge gate to `tick()`. The natural split is already visible in the section-comment banners — budgets/breaker (lines 113-168) is a pure, dependency-free unit that would move to `loop_budget.py` cleanly, the way `triage.py` was split out under Decision #4. Worth doing *before* L3 rather than during.

2. **`from atlas.cli import make_pipeline` inverts the expected dependency direction.** `loop.py` (a library module) imports from `cli.py` (the entry point), and `cli.py`'s loop commands import back from `loop.py` inside function bodies to dodge the cycle. The context file flags this as "a load-bearing circular-import note for T-L2.9" — accurate, but the note documents the workaround rather than the fix. Decision #11 explicitly offered "promote it out of `cli.py` into `orchestrator.py` alongside `Pipeline` itself" as the alternative and it's the better one: `make_pipeline` is a `Pipeline` factory, it has nothing to do with the CLI, and moving it removes the cycle entirely instead of deferring the import. Cheap now; annoying once L3 and L4 both import it.

**On Decision #2 (planned lane first-pass-only):** the scoping rationale is sound and I'd have made the same call. But C1 means the scoped-down version doesn't work either — so the decision's premise ("the simple version ships now, the complex version waits") hasn't actually been validated yet. Worth re-confirming after C1 is fixed that first-pass-only is still the right L3 boundary.

**On the exit criteria:** §13 #5–#8 are all still unchecked in `tasks.md`, correctly, since they depend on T-L2.13. Given C1, #6 (two-lane routing) would fail today. #5, #7, #8 look sound on inspection of the quick lane. I'd expect T-L2.13 to pass for the quick lane once run, and to have surfaced C1 within minutes for the planned lane.

---

## Resolution (applied 2026-07-25)

All Next Steps below were implemented and verified. Final gate: **424 passed, 1 xfailed**
(was 400/1), `ruff check` + `ruff format --check` clean, `mypy --strict` clean on 24 source
files, coverage 95.02%.

| Item | Status | Notes |
| --- | --- | --- |
| C1 planned-lane ordering | Fixed | Worktree created first, `dev-docs-be` runs with `cwd=wt_path`, triad committed via new `_commit_all()` before delivery; worktree cleaned up on both failure paths |
| C2 `span_id=""` | Fixed | Score now anchored to a real `record_span("deliver"/"pr_outcome")` id |
| C2 unbounded dedupe list | Fixed | Bounded to 500 entries, trimmed on both append and load |
| Missing direct tests | Added | `test_planned_lane_commits_triad_before_delivering` (asserts commits-ahead-of-main + triad in tree + not in main worktree), `test_planned_lane_raises_when_agent_produces_nothing`, `test_sync_score_is_anchored_to_a_real_span` (real stub `PlumbIO`, not a MagicMock), plus two dedupe-bound tests |
| I1 `action` overload | Fixed | Added `"failed"` to the `TickResult.action` Literal; both failure paths use it |
| I2 inert dollar cap | Fixed | `loop status` reports "not tracked / NOT enforced"; `run_forever` warns at startup when a non-default cap is set |
| I3 `max_turns` unwired | Fixed | Threaded `SubprocessStageRunner(max_turns=...)` → `make_pipeline(max_turns=...)` → both lanes; `atlas run` still leaves it None |
| I4 worktree slug matching | Fixed | Extracted `_sweep_orphaned_worktrees()`, now keys on `.atlas/current-run`'s exact path; fails safe (sweeps nothing) if that file is unreadable |
| m1–m5 | Fixed | No-op try/except removed, both misleading docstrings rewritten, triage latency measured, unused `cost` param dropped, outer breaker check removed so `tick()` owns it |
| Arch #1 `loop.py` size | Done | Split budgets/breaker/`LoopState` into `loop_budget.py` (183 lines); `loop.py` 729 → 600 |
| Arch #2 circular import | Done | `make_pipeline` + `LastOutcomeRunner` moved to new `pipeline_factory.py`; `cli.py` no longer needs lazy imports of `loop` |

**Two things worth flagging from the fix pass:**

1. **C1 was confirmed by a pre-existing test flipping red.** `test_planned_lane_stops_after_plan_pr`
   passed before the fix only because its fake `claude` wrote nothing to disk *and* nothing
   checked for commits — so the old code's empty branch went unnoticed. After the ordering
   fix it correctly failed with "nothing to commit", and now uses a triad-writing fake.
   That's the clearest possible evidence the bug was real rather than theoretical.

2. **One correction to C1 as originally written.** I claimed `WorktreeManager.create()` would
   raise on the happy path because `dev-docs-be` dirties the repo. That's wrong in the common
   case: `_check_repo_clean()` deliberately ignores untracked files ([`worktree.py:160-167`](../../../src/atlas/worktree.py#L160)),
   and a brand-new `dev/active/<slug>/` triad is entirely untracked. The `WorktreeError` only
   fires if the agent also modifies a tracked file. The other two failure modes (wrong cwd,
   no commit → empty branch) stand unchanged, and the lane still could not open a PR under
   any input — so the severity and the fix are unaffected.

**Also hardened opportunistically:** the Decision #15 grep test now scans every module under
`src/atlas/` (excluding the sanctioned `queue_gh.py` and `deliverer.py`) rather than just
`loop.py`, so `loop_budget.py` and any module L3/L4 adds are in scope automatically. Verified
it fails when a raw `gh` call is introduced into the new module.

**Not addressed (unchanged from the review's scope):** the three remaining L1 findings
(M1 Codex cache-semantics, L1 cwd-vs-`--sandbox`, L3 branch-safety exact-match) stay open and
tracked in BACKLOG.md per Decision #3. T-L2.13's manual smoke is still pending and is now
meaningfully more informative than it would have been before these fixes.

---

## Next Steps

Suggested order:

1. **Fix C1** (planned-lane worktree ordering + commit) — blocks TRD-v3 §13 #6, and blocks T-L2.13's planned-lane smoke from being meaningful.
2. **Fix C2** (`span_id=""`, and bound `synced_pr_outcomes`) — blocks the §13 #5 headline signal being trustworthy.
3. **Add the two missing direct tests** the Criticals expose: `run_planned_first_pass` against a real temp git repo asserting commits-ahead-of-main, and a `sync_prior_prs` test asserting the written score's `span_id`. These are the altitude the current mocks skip past.
4. **I1–I4** in any order; I2's operator-visible warning is the highest-value of the four given it's a safety control that reads as working.
5. **m1–m5** as cleanup, ideally in the same pass.
6. **Then** run T-L2.13's manual smoke — it's much more informative after C1/C2 than before.
7. **Before L3 starts:** move `make_pipeline` out of `cli.py` (Architecture #2) and split budgets/breaker out of `loop.py` (Architecture #1). Both get harder once L3's diff lands on top.

Items 1–3 are what I'd consider blocking for "Phase L2 code-complete" as stated in `STATUS.md` — the current claim is accurate about tests passing but overstates functional completeness of the planned lane. Suggest amending the STATUS.md entry to scope the completion claim to the quick lane until C1 lands.

**Not in scope for L2, still tracked:** the three remaining L1 findings (M1 Codex cache-semantics, L1 cwd-vs-`--sandbox`, L3 branch-safety exact-match) remain open per Decision #3 — I confirmed they're in BACKLOG.md and were not silently absorbed. That follow-through is correct.
