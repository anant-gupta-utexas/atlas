# TRS — Loop Mode, Phase L0 (Honest Baseline)

**Detailed from:** [`docs/2_architecture/TRD-v3.md`](../../../docs/2_architecture/TRD-v3.md) §14 Phase L0
**Grounds on:** [`loop-mode-design.md`](../../../docs/1_product_and_research/loop-mode-design.md) §5 Phase L0, [`headless-clis-reference.md`](../../../docs/1_product_and_research/headless-clis-reference.md) Part B, [`TRD-v2.md`](../../../docs/2_architecture/TRD-v2.md) §3.4 (backend contract)

---

## Phase Summary

**Phase L0 — Honest baseline** → delivers part of `v3.0` (TRD-v3 §11).

> Goal (copied from TRD-v3 §14): *"Make the existing single-run path real for the first time, and add the telemetry + permission + delivery primitives the loop depends on. No loop yet."*

**Dependencies:** Shipped v2.2 (239 tests, 95% coverage — confirmed at TRS authoring time; see Context "Status").

**Exit criteria (TRD-v3 §13, copied for tracking):**
- #1 — Live attended run, measured: a live `atlas run "<task>" --workflow dev` against the real `claude` backend produces a plumb run whose `code_gen` span carries real `tokens` from the backend JSON. **Run-level `dollar_cost` (and the run-level token roll-up) is deferred to plumb P1-a (`set_usage`) and is NOT an L0 exit gate** — it becomes real when plumb v1.1 lands and is verified at L2 (cost-per-landed-PR). *(TRD-v3 §13 #1 and §3.6 were amended 2026-07-21 to reflect this; the TRD is now the authority for this wording and this TRS follows it — no open restatement question remains.)*
  > **Tokens and dollars are not symmetric — do not conflate them.** Tokens have a span-level sink today (`add_span(tokens=(in, out))`). `total_cost_usd` has **no per-span sink at all** — plumb has no per-span cost column in v1.0.1 *or* v1.1 (`spans.attributes` is JSON; P1-a's `set_usage` is deliberately run-level). Do not go looking for one.
- #2 — Attended-mode invariance: full v2 suite green; `atlas run` unchanged.
- #4 — Delivery primitive: the `Deliverer` pushes a branch + opens a PR for a completed run and calls `cleanup()`; asserted never to push `main` or force-push.

(§13 #3 — `CodexBackend` dispatch — belongs to Phase L1, not L0.)

---

## Overview & Scope

L0 has no loop code at all. It is entirely about making three things true that the TRD-v3 relies on and that have never been exercised or built:

1. **The single-run path has never actually been run against a live `claude` backend.** All 239 tests mock the subprocess boundary. L0's first task is to just run it for real, once, and capture what happens — this is a research/verification task, not a feature build, but it gates everything downstream (if live dispatch surfaces a surprise, L1+ plans against a false premise).
2. **Telemetry primitives loop mode needs but attended mode must not see.** `ClaudeCodeBackend` currently returns plain-text stdout (TRD-v2 Phase-3 Resolved Decision #1 — this is deliberate, for `dev` pipeline gate-parity). Loop runs need structured cost/token data. This is a **new, gated code path** on the same class, not a replacement.
3. **The `Deliverer` primitive** — push branch + open PR — that Phase L2's loop will call after every completed run. Built and manually exercised now; not wired to any automatic trigger until L2.

Also folded into L0 per TRD-v3 §14: version reconciliation (`pyproject.toml` is currently `1.0.0`; docs/STATUS.md/git tags all say `v2.2` — this mismatch is a real, confirmed drift, not a hypothetical) and fixing/xfailing one confirmed-failing integration test so "green suite" is actually true.

**Explicitly NOT in scope for L0** (per TRD-v3 §14 phase boundaries):
- `CodexBackend` (Phase L1).
- `loop_dev.yaml` (Phase L1).
- `loop.py`, `queue_gh.py`, any polling/ticking (Phase L2).
- `[loop]` config section (Phase L2 — L0 touches no config schema beyond what telemetry needs, which is nothing new in `Config`).
- Any GitHub Issues interaction (Phase L2).

## Requirements Summary

From TRD-v3 §3.6, §3.7, §14 Phase L0, and §12 Resolved Decision #8:

- **FR-L0.1** — First live attended run against the real `claude` backend, confirming subprocess spawn, gate prompts, and a plumb run with spans actually happens end to end.
- **FR-L0.2** — `ClaudeCodeBackend` gains an opt-in JSON telemetry mode (`--output-format json`) that is **off by default** and only active for loop-mode dispatch; `parse_result` in that mode surfaces `total_cost_usd` + `usage.input_tokens` / `usage.output_tokens`; attended `dev` runs keep byte-identical plain-text argv and parsing.
- **FR-L0.3** — Telemetry threads into plumb at the **span level**: `PlumbIO.record_span()` passes `(input_tokens, output_tokens)` to plumb's `RunHandle.add_span(tokens=(in, out))` when a backend surfaces them (persisted as a summed `spans.tokens`); no-op (existing behavior) when it doesn't. **Run-level `dollar_cost`/`tokens_in`/`tokens_out` are explicitly deferred to plumb P1-a** — not reachable from the online run path in plumb v1.0.1. `total_cost_usd` is parsed and surfaced in-memory (for logging / a future PR-comment body) but not written to plumb in v3.0.
- **FR-L0.4** — A non-interactive permission profile is defined and applied when loop-mode dispatch is requested: `--permission-mode acceptEdits` + `--allowedTools <allowlist>` (read from the *target repo's* `.claude/settings.json`) + `--max-turns <cap>`. Never `--dangerously-skip-permissions`. Not used by attended mode.
- **FR-L0.5** — A `Deliverer` Protocol + `GhPrDeliverer` implementation: pushes the run's worktree branch, opens a PR via `gh pr create`, then calls `WorktreeManager.cleanup()`. Never pushes `main`, never force-pushes, never merges.
- **FR-L0.6** — Version reconciliation: `pyproject.toml` version bumped to `2.2.0`; a `v2.2` git tag exists (or is flagged for the user to create — tagging is a git-history action, not a code change this TRS enforces by itself).
- **FR-L0.7** — The confirmed-failing `test_score_jobs_adapter_real_import_success` (content-pipeline drift, `tests/integration/test_job_adapters_real_import.py`) is fixed or `xfail`-marked with a tracking reason, so a green `pytest` run means green.

## Detailed Component Design

### Classes/Modules Structure

```
src/atlas/
├── cli_backend.py          # MODIFY — ClaudeCodeBackend gains loop-mode telemetry path
├── deliverer.py             # NEW — Deliverer Protocol + GhPrDeliverer
├── plumb_io.py               # MODIFY — record_span() gains tokens=(in,out) tuple kwarg (plumb's real add_span shape); run-level dollar_cost deferred to plumb P1-a
├── worktree.py               # WIRE — cleanup() called by GhPrDeliverer (merge_back() untouched, just unused by this path)
└── cli.py                    # NOT MODIFIED in L0 — no `atlas loop` command yet (that's L2); Deliverer is exercised via a small manual/test harness only
```

No new top-level package. `deliverer.py` is a new sibling module to `worktree.py`, matching the "one focused module per collaborator" precedent (`composite_runner.py`, `shell_runner.py`, `cli_backend.py`).

### Method Signatures

```python
# cli_backend.py — additive, backward compatible

class ClaudeCodeBackend:
    name = "claude"

    def build_argv(
        self,
        *,
        prompt: str,
        model: str,
        add_dirs: list[Path],
        timeout_s: int,
        extra_flags: dict[str, str],
    ) -> list[str]:
        """Unchanged signature. Behavior branches on extra_flags["telemetry"] == "json":
        attended callers never set this key, so argv stays byte-identical to today.
        """

    def parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> tuple[str, str, str | None]:
        """Unchanged signature and return shape (status, output_text, error_type) —
        Protocol is not modified. JSON-mode parsing is an internal branch keyed off
        whether stdout parses as the known `--output-format json` envelope; plain-text
        callers get exactly today's behavior (non-zero exit -> failure, else success).
        """

    def parse_usage(self, stdout: str) -> UsageStats | None:
        """NEW method, not on the CliBackend Protocol (opt-in capability, not a
        required strategy method — Protocol stays 3 methods for all backends).
        Returns None if stdout isn't the JSON envelope (i.e. attended plain-text mode).
        Called by SubprocessStageRunner only when loop-mode telemetry was requested.
        """


@dataclass(frozen=True)
class UsageStats:
    total_cost_usd: float | None   # PARSED + surfaced in-memory only in v3.0 — no durable
                                   # plumb column reachable from the online run path until P1.
    input_tokens: int | None       # threaded to plumb as add_span(tokens=(in, out)); plumb
    output_tokens: int | None      # SUMS these into spans.tokens (split lost until plumb v1.1).


# deliverer.py — new module

class DeliveryError(Exception):
    """Raised on git push / gh pr create failure."""


@dataclass(frozen=True)
class PrRef:
    number: int
    url: str


class Deliverer(Protocol):
    def deliver(
        self,
        *,
        run_id: str,
        branch: str,
        worktree_path: Path,
        title: str,
        body: str,
    ) -> PrRef: ...


class GhPrDeliverer:
    def __init__(self, *, repo_root: Path, worktree: WorktreeManager) -> None: ...

    def deliver(
        self,
        *,
        run_id: str,
        branch: str,
        worktree_path: Path,
        title: str,
        body: str,
    ) -> PrRef:
        """
        1. git push -u origin <branch>              (cwd=worktree_path; branch-scoped, never --force)
        2. gh pr create --head <branch> --title <title> --body <body>  (cwd=repo_root)
        3. self._worktree.cleanup(worktree_path)     (always, even if push/PR partially failed —
           see Error Handling)
        Returns PrRef parsed from `gh pr create`'s stdout URL.
        Raises DeliveryError on push or gh failure; never raises on cleanup (best-effort).
        """
```

> ### ⚠ `Deliverer.deliver()` — L0 implements a **narrowed subset** of TRD-v3 §3.7's signature. This is intentional, not drift.
>
> **TRD-v3 §3.7 shows the L2 target shape:** `deliver(*, run_id, issue, worktree_path, branch, scores)`.
> **L0 implements:** `deliver(*, run_id, branch, worktree_path, title, body)`.
>
> If you are diffing the TRD against the code and about to file a bug: don't — this is the recorded decision (2026-07-21), rationale below.
>
> **Why narrow now.** L0 genuinely has no `Issue` or `Score` type — the queue (`queue_gh.py`) and the loop's run-scoring summary are both Phase L2. The alternative (placeholder `issue: dict | None` / `scores: dict | None` params so the Protocol "never changes") is a false stability: a Protocol whose params are `dict | None` is not a stable contract, it's an *unchecked* one. You'd design the type twice — once as an unvalidatable dict, once for real in L2 — and get no type protection in between.
>
> **Why widening in L2 is safe.** The params are **keyword-only** (the `*` is already in the TRD's own signature), so adding `issue` / `scores` is a purely additive, mypy-checked change: the type checker points at every call site that needs updating. No positional-arg breakage is possible.
>
> **Why `title`/`body` stay pre-rendered strings.** Composing a PR body from an issue + scores is **L2's job, not the `Deliverer`'s** — the `Deliverer`'s contract is "push this branch, open a PR with this text, clean up." Keeping them as strings the caller pre-renders is exactly what lets L2 add `issue`/`scores` (and richer body composition) **without disturbing the delivery mechanics** that L0 tests lock down.

### Data Structures

```python
@dataclass(frozen=True)
class UsageStats:
    total_cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None

@dataclass(frozen=True)
class PrRef:
    number: int
    url: str
```

Both are plain frozen dataclasses, matching existing style (`StageOutcome`, `GateDecision`, `RunContext` in `orchestrator.py`).

## API Specifications

Not applicable — atlas has no HTTP surface (v1/v2/v3 constraint, TRD-v3 §5 "no HTTP shell"). The only "APIs" in this phase are:

- **Subprocess argv contracts** (`claude` CLI, `git`, `gh`) — see Algorithm & Logic Design below for exact argv shapes.
- **`gh pr create` invocation** — no REST calls; the loop never talks to GitHub's API directly, only through the `gh` CLI subprocess (consistent with TRD-v3 §3.1's `queue_gh.py` design for L2, but L0's `Deliverer` is the first `gh` touchpoint and predates `queue_gh.py`).

No authentication is handled by atlas code — `gh` and `claude` each own their own auth session (TRD-v3 §5).

## Database Design

Not applicable in the traditional sense — atlas has no first-party database. The only persistent store touched is **plumb's SQLite DB** (`~/.plumb/plumb.db`), and this phase does **not** migrate its schema.

**Confirmed plumb v1.0.1 write surface (spike resolved — no longer a Pending Decision):**

- **The real span-token write path is `RunHandle.add_span(..., tokens=(in, out))`** (`plumb/api.py:264`). It takes an `(input, output)` tuple.
- **Span tokens persist as a single *summed* `spans.tokens` column** — the in/out split is **lost at the DB layer** until plumb v1.1. atlas can pass the tuple, but only the sum is durable.
- **Run-level `runs.dollar_cost` / `runs.tokens_in` / `runs.tokens_out` are NOT writable by the online `with run()` path.** Those columns exist in plumb's schema, but `finalize_run` (`plumb/storage_sqlite.py:431`, the `_FINALIZE_RUN` SQL) sets none of them, and `RunHandle` exposes no cost/usage setter. There is **no reachable code path in plumb v1.0.1** to populate them from atlas's online run.

**Consequence for L0 (already reflected in TRD-v3 §3.6 + §13 #1, amended 2026-07-21 — the TRD is the authority; this section just restates it):** v3.0 records **per-span tokens** from the backend JSON via `add_span(tokens=...)`, and **defers run-level `dollar_cost` (and the run-level token roll-up) to plumb P1-a**, where a `set_usage` setter + `finalize_run` threading are expected to land. `total_cost_usd` from the `claude` JSON envelope is still *parsed and surfaced in-memory* (logged, available to the caller / future PR-comment body), it just has **no durable sink at any level** — there is no per-span cost column in plumb v1.0.1 *or* v1.1, so there is nothing to fall back to.

**Data access pattern:** `PlumbIO.record_span()` gains an optional `tokens: tuple[int, int] | None = None` kwarg (matching plumb's actual `add_span(tokens=(in, out))` shape — **not** a `usage=UsageStats` kwarg, since plumb takes a bare tuple and has no per-span cost field). When present, it threads straight to `self._run_handle.add_span(..., tokens=tokens)`; when `None` (every existing call site), behavior is byte-identical to today. Stub mode buffers the tuple in `self.spans` for test assertions. The `UsageStats` dataclass produced by `ClaudeCodeBackend.parse_usage` is *decomposed at the call site* (`SubprocessStageRunner` / the loop-mode dispatch path) into the `(input_tokens, output_tokens)` tuple `record_span` wants; `total_cost_usd` is carried separately (logged / returned) rather than written to plumb.

**Migration strategy:** none required. The span-token path is additive and reachable today; the run-level cost path is a plumb-P1-a concern, not an atlas migration.

## Algorithm & Logic Design

### `ClaudeCodeBackend` loop-mode telemetry (pseudocode)

```
build_argv(prompt, model, add_dirs, timeout_s, extra_flags):
    argv = ["claude", "-p", prompt, "--no-session-persistence", "--model", model]
    for d in add_dirs: argv += ["--add-dir", str(d)]

    if extra_flags.get("telemetry") == "json":
        argv += ["--output-format", "json"]
    # attended callers never pass extra_flags["telemetry"], so argv is
    # byte-identical to today's output in that case — this is the load-bearing
    # regression test (mirrors T3.3's byte-identity test from Phase 3).

    if extra_flags.get("permission_mode"):
        argv += ["--permission-mode", extra_flags["permission_mode"]]
    if extra_flags.get("allowed_tools"):
        argv += ["--allowedTools", extra_flags["allowed_tools"]]
    if extra_flags.get("max_turns"):
        argv += ["--max-turns", str(extra_flags["max_turns"])]

    return argv


parse_result(stdout, stderr, returncode):
    if returncode != 0:
        return ("failure", stdout, "plugin_nonzero_exit")
    # Plain-text branch (attended, default) — UNCHANGED:
    if not _looks_like_json_envelope(stdout):
        return ("success", stdout, None)
    # JSON branch (loop-mode) — new:
    payload = json.loads(stdout)          # if this raises, fall through to plain-text-shaped failure
    subtype = payload.get("subtype")
    if subtype != "success":
        return ("failure", payload.get("result") or stdout, f"claude_{subtype}")
    return ("success", payload.get("result", ""), None)


parse_usage(stdout) -> UsageStats | None:
    if not _looks_like_json_envelope(stdout):
        return None
    payload = json.loads(stdout)
    usage = payload.get("usage", {})
    return UsageStats(
        total_cost_usd=payload.get("total_cost_usd"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
    )
```

`_looks_like_json_envelope` is a cheap heuristic (e.g. `stdout.lstrip().startswith("{")` then a guarded `json.loads`) — **not** a second flag threaded through, because `parse_result`'s signature is fixed by the `CliBackend` Protocol (`stdout, stderr, returncode`) and must not grow a mode parameter that `AntigravityBackend`/future backends don't share. This mirrors how `AntigravityBackend.parse_result` already unconditionally expects JSON — `ClaudeCodeBackend` instead detects which shape it got.

**Why detect-by-content instead of passing a mode flag into `parse_result`:** `SubprocessStageRunner.run()` already knows whether it requested `telemetry=json` (it built the argv). Simpler alternative: `SubprocessStageRunner` calls `parse_result` for status/text as today, and — only in loop mode — separately calls the new `backend.parse_usage(stdout)`, which itself sniffs the envelope. This avoids changing `parse_result`'s contract or return arity. **This is the recommended design** (see Pending Decisions for the alternative considered and rejected).

### `GhPrDeliverer.deliver()` (pseudocode)

```
deliver(run_id, branch, worktree_path, title, body):
    push = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=worktree_path, capture_output=True, text=True,
    )
    if push.returncode != 0:
        raise DeliveryError(f"git push failed: {push.stderr}")
        # cleanup NOT called here — the branch may still be salvageable;
        # a failed push means the worktree still holds unpushed work.

    pr = subprocess.run(
        ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body],
        cwd=repo_root, capture_output=True, text=True,
    )
    if pr.returncode != 0:
        raise DeliveryError(f"gh pr create failed: {pr.stderr}")
        # cleanup still NOT called — branch is pushed but no PR; don't destroy
        # the worktree out from under a branch a human might want to inspect.

    pr_ref = _parse_pr_url(pr.stdout)   # gh pr create prints the PR URL on success

    try:
        self._worktree.cleanup(worktree_path)
    except WorktreeError:
        log.warning(...)   # best-effort — a failed cleanup does not fail delivery;
                            # the PR already exists and is the source of truth.

    return pr_ref
```

**Safety invariants enforced by construction** (never by runtime flag):
- `git push` argv is always `["git", "push", "-u", "origin", branch]` — no `--force`, no `main` as a literal anywhere in this function.
- `branch` is asserted (or defensively re-derived from `git rev-parse --abbrev-ref HEAD` inside `worktree_path`, matching `WorktreeManager.merge_back`'s existing pattern) to never equal `"main"` before push — belt-and-suspenders alongside the argv-shape guarantee, and the thing the mandatory test in QA below asserts against.

## Error Handling & Edge Cases

| Case | Handling |
| --- | --- |
| `claude` subprocess times out | Already handled by `SubprocessStageRunner` (`subprocess.TimeoutExpired` → `plugin_timeout`); L0 adds no new timeout path, loop-mode argv reuses the same `timeout_s`. |
| JSON envelope present but malformed (`json.loads` raises) | `parse_result` catches `JSONDecodeError` and falls back to `("failure", stdout, "claude_unparseable_json")` — never propagates the exception up through `StageOutcome`. |
| `usage`/`total_cost_usd` keys absent from an otherwise-valid JSON envelope | `parse_usage` returns a `UsageStats` with `None` fields rather than raising `KeyError`; caller (plumb-write site) treats `None` as "don't write this column," not an error. |
| `git push` fails (no remote, network, auth) | `DeliveryError` raised; worktree is **not** cleaned up (see pseudocode rationale — preserves the unpushed work for manual recovery). Caller (manual harness in L0; `loop.py` in L2) surfaces this as a run failure. |
| `gh pr create` fails (no `gh` auth, repo not found, branch already has an open PR) | `DeliveryError` raised; worktree **not** cleaned up (branch is pushed; a human can `gh pr create` manually or investigate). |
| `gh` not installed at all | `subprocess.run(["gh", ...])` raises `FileNotFoundError` — `GhPrDeliverer` catches this specifically and re-raises as `DeliveryError("gh CLI not found on PATH")`, a clear message rather than a raw traceback. |
| `WorktreeManager.cleanup()` fails after a successful PR | Caught and logged; **does not** raise `DeliveryError` — the PR is the durable artifact; a leftover worktree directory is a cheap, safe failure mode (existing `cleanup()` is itself idempotent/safe to retry). |
| Attended (non-loop) `atlas run` accidentally reaches loop-mode telemetry code | Cannot happen by construction: nothing in `cli.py`'s `run`/`resume` commands sets `extra_flags["telemetry"]` — there is no CLI flag exposing it in L0 (no `atlas loop` command exists yet). The only caller of the new path in L0 is test code + the manual delivery harness. |
| Version/tag drift (`pyproject.toml` says `1.0.0`, docs say `v2.2`) | Fixed directly: version bumped to `2.2.0`. Git tag creation is called out as a user action in Tasks (tags are a maintainer/CI decision atlas code cannot force). |
| `test_score_jobs_adapter_real_import_success` failure | Root-caused (see Context — it's an `AttributeError: module 'application.use_cases' has no attribute 'score_jobs'`, i.e. a content-pipeline-side rename/move) and either fixed at the atlas import site or `xfail(reason=..., strict=False)`-marked with a linked follow-up note in BACKLOG.md. Decision on which is a **Pending Decision** below — it depends on inspecting the actual content-pipeline module layout, which is outside atlas's repo. |

**Retry strategy:** none added in L0. `Deliverer.deliver()` is a single-attempt operation; retrying `git push`/`gh pr create` automatically is explicitly out of scope (no self-healing until Phase L3, and L0 has no loop to retry from anyway).

## Dependencies & Interfaces

- **Upstream:** none beyond shipped v2.2 (TRD-v3 §14 "Dependencies: Shipped v2.2").
- **External CLIs newly invoked by atlas code:** `gh` (via `GhPrDeliverer`) — first appearance of `gh` anywhere in the atlas codebase; `git push` (via `GhPrDeliverer`) — `git` itself is already a dependency (`WorktreeManager`), but `push` is a new subcommand atlas issues.
- **Interfaces this phase must not break:**
  - `CliBackend` Protocol (3 methods) — `parse_usage` is an *additional* method on `ClaudeCodeBackend`, not a Protocol requirement, so `AntigravityBackend` and any future backend need not implement it. `SubprocessStageRunner` checks `hasattr(backend, "parse_usage")` (or `isinstance` against a separate `UsageCapableBackend` Protocol) before calling it.
  - `Pipeline` (`orchestrator.py`) — **unchanged**, per Appendix A's explicit note ("if implementation finds `Pipeline` genuinely needs editing, that is a signal the design has drifted — pause and reconcile"). `Deliverer` is invoked *outside* `Pipeline.run_to_completion()`, by whatever harness calls it (manual script in L0; `loop.py` in L2), exactly mirroring TRD-v3 §6's boundary guarantee.
  - `WorktreeManager` — unchanged interface; `cleanup()` already exists and is idempotent (verified by reading `worktree.py:114-144` — safe no-op if the path doesn't exist). L0 just adds a caller.

## Security Considerations

- **No new secrets handled by atlas.** `gh` auth is the operator's existing session (TRD-v3 §5); atlas never reads, stores, or logs a GitHub token. `claude`'s auth is unchanged (subprocess-level, per `ClaudeCodeBackend.preflight()`'s existing `None`-return contract).
- **Permission profile is additive and off by default.** `--permission-mode acceptEdits` + `--allowedTools` + `--max-turns` are only added to argv when loop-mode flags are explicitly passed — attended `atlas run` never sets them, so today's interactive permission behavior (whatever `claude -p` does by default) is unchanged. **`--dangerously-skip-permissions` must never appear in any argv this phase constructs** — enforced by a negative-assertion test (see Testing Strategy).
- **`GhPrDeliverer` push safety is the load-bearing security property of this phase.** The branch-scoped push (`git push -u origin <branch>`, never `main`, never `--force`) is asserted by a dedicated test using a fake subprocess that fails the test if invoked with `main` or `--force` anywhere in argv — mirroring the pattern `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` used in Phase 3 for a different security boundary (assert-the-dangerous-call-never-happens, not just check the return value).
- **`.claude/settings.json` allowlist lives in the target repo, not atlas.** L0 does not author this file's *content* (that's a per-repo, user-authored artifact per TRD-v3 §7) — it only defines the `--allowedTools` flag construction that *would* read from it if present. Actually populating atlas's own `.claude/settings.json` with a sane starter allowlist may be a convenience the maintainer wants; flagged as a Pending Decision.
- **Input validation:** `title`/`body` strings passed to `gh pr create` are passed as separate argv elements (not shell-interpolated — `subprocess.run` with a list, `shell=False`, matching every existing subprocess call in this codebase), so no shell-injection surface regardless of issue/task content. This matters even though L0 has no issue-body input yet (that's L2) — establishing the safe argv-list pattern now means L2's `queue_gh.py` inherits it by construction.

## Testing Strategy

**Unit tests** (`tests/unit/test_cli_backend.py` — extend existing file):
- `ClaudeCodeBackend.build_argv` with `extra_flags={}` (no telemetry key) → byte-identical to the existing golden-string test (regression guard, mirrors `test_claude_code_backend_argv_byte_identical_to_phase2`).
- `build_argv` with `extra_flags={"telemetry": "json"}` → argv includes `--output-format json`.
- `build_argv` with permission-profile flags set → argv includes `--permission-mode acceptEdits`, `--allowedTools ...`, `--max-turns ...`, in that combination and never `--dangerously-skip-permissions`.
- `parse_result` on plain-text stdout (no telemetry) → unchanged behavior (existing tests must still pass untouched).
- `parse_result` on a valid JSON success envelope → `("success", <result field>, None)`.
- `parse_result` on a JSON envelope with `subtype != "success"` → `("failure", ..., "claude_<subtype>")` for each documented subtype (`error_during_execution`, `error_max_turns`, `error_max_budget_usd`, `error_max_structured_output_retries`).
- `parse_result` on malformed JSON (envelope-looking but `json.loads` raises) → `("failure", stdout, "claude_unparseable_json")`, no exception escapes.
- `parse_usage` on plain-text stdout → `None`.
- `parse_usage` on a full JSON envelope → correct `UsageStats` extraction.
- `parse_usage` on a JSON envelope missing `usage`/`total_cost_usd` → `UsageStats` with `None` fields, no `KeyError`.

**Unit tests** (`tests/unit/test_deliverer.py` — new file):
- `GhPrDeliverer.deliver()` happy path (faked `subprocess.run`) → correct argv sequence (`git push -u origin <branch>` then `gh pr create --head <branch> ...`), returns a `PrRef` parsed from the faked `gh` stdout, calls `WorktreeManager.cleanup()` exactly once.
- **Load-bearing security test:** faked `subprocess.run` raises `AssertionError` if invoked with `"main"` or `"--force"` anywhere in argv → `deliver()` with a non-`main` branch never trips it; a defensive test that explicitly tries to construct a `deliver()` call with `branch="main"` is rejected before any subprocess call (whatever the chosen guard mechanism is — raise `DeliveryError` early, per the pseudocode's "defensively re-derive/assert" note).
- `git push` failure → `DeliveryError` raised, `gh pr create` never called (assert via a subprocess fake that fails the test if the second call happens), `cleanup()` never called.
- `gh pr create` failure (after successful push) → `DeliveryError` raised, `cleanup()` never called.
- `gh` binary missing (`FileNotFoundError` from `subprocess.run`) → `DeliveryError` with a clear message, not a raw traceback.
- Successful delivery but `cleanup()` raises `WorktreeError` → `deliver()` still returns the `PrRef` (does not re-raise the cleanup error).

**Unit tests** (`tests/unit/test_plumb_io.py` — extend existing coverage, file may need creating if it doesn't exist — check first):
- `record_span(..., tokens=(in, out))` → stub-mode (`real=False`) buffer captures the tuple; a real-mode test (plumb installed) asserts it reaches `add_span(tokens=...)`.
- `record_span(..., tokens=None)` (today's call shape) → unchanged behavior, existing tests pass untouched.
- Negative assertion: no `PlumbIO` method writes a run-level `dollar_cost`/`tokens_in`/`tokens_out` (confirming the deferral — there is no such call to test *for*, only *against*).

**Integration test** (`tests/integration/` — new or extend `test_cli_backend_dispatch.py`):
- `test_dev_pipeline_unaffected_by_phase_l0` — full mocked-subprocess dispatch through `SubprocessStageRunner` with no loop-mode flags → byte-identical argv and behavior to the pre-L0 baseline (same spirit as Phase 3's `test_dev_pipeline_unaffected_by_phase_3`).
- `test_claude_backend_loop_mode_telemetry_end_to_end` — mocked subprocess returning a realistic JSON envelope → `SubprocessStageRunner` (or whatever the loop-mode call site becomes) parses `UsageStats`, decomposes it to `(input_tokens, output_tokens)`, and that tuple reaches the `PlumbIO` stub's `record_span(tokens=...)` capture. `total_cost_usd` is asserted present in-memory but not written to any plumb column.

**Manual / off-CI** (this is the "first live attended run" — cannot be a CI-gated automated test by definition, since it requires a real `claude` subprocess and real plumb DB):
- FR-L0.1: run `atlas run "<small real task>" --workflow dev` for real, in a scratch repo, against the live `claude` backend. Confirm: subprocess actually spawns, gate prompts appear and block on real `input()`, a plumb run row is created with a non-trivial span tree. Capture findings (surprises, timing, actual JSON shape observed if `--output-format json` is exercised manually too) into `headless-clis-reference.md`.
- Manual delivery smoke: construct a throwaway branch + worktree, call `GhPrDeliverer.deliver()` against a real (test/scratch) GitHub repo, confirm a real PR appears, confirm `main` is untouched (`git log main` before/after comparison, mirroring the existing E2E `test_main_branch_isolation.py` pattern).

**Coverage targets:** `deliverer.py` ≥ 85% (new file, correctness-critical — matches the bar set for `queue_gh.py`/`loop.py`/`CodexBackend` in TRD-v3 §10 for later phases); `cli_backend.py` additions covered to the same ≥ 85% the module already holds (currently 100%); repo-wide ≥ 80% (existing gate, TRD-v2 precedent).

**Mocking strategy:** all `subprocess.run` calls faked via `unittest.mock.patch`, following the exact pattern already established in `test_cli_backend.py` and `test_worktree.py` — no new mocking framework introduced.

## Performance Considerations

- **No hard latency SLA** (TRD-v1 §Technical Requirements, carried forward). L0 adds one extra `subprocess.run` per delivered run (`git push`) and one (`gh pr create`) — both network-bound, both already timeout-wrapped at the OS/CLI level; no atlas-side timeout is added in L0 (TRD-v3 §4 Performance's "wrap each `gh` invocation in a timeout" requirement is explicitly a Phase L2 concern, since L2 is where `gh` calls happen inside an unattended polling loop that must not hang forever — L0's `Deliverer` is invoked manually/synchronously by a human who can Ctrl-C).
- **JSON parsing overhead** (`json.loads` on `claude`'s stdout) is negligible relative to subprocess spawn + LLM inference time — not a measured concern.
- **No caching** — this phase introduces no repeated/hot-path calls; `Deliverer.deliver()` runs once per completed run.

## Tasks

Ordered by execution sequence. Cross-task dependencies via `Dependencies` field only — no sub-phases.

---

* **[T-L0.1] Baseline verification — confirm the drift and the live-run gap** [Effort: S]
  - **Description**: Re-run the full test suite to reconfirm the exact failing test (`test_score_jobs_adapter_real_import_success`, confirmed failing at TRS authoring time — `AttributeError: module 'application.use_cases' has no attribute 'score_jobs'`) and reconfirm no prior live `atlas run` against a real `claude` backend exists in git history / STATUS.md. This is a sanity-check task (mirrors Phase 3's T3.1), not a build task — it fixes the starting-point facts this TRS is written against.
  - **Acceptance Criteria**:
      - [ ] `pytest tests/ -q` output captured; exact failing test(s) and pass count recorded in this TRS's context file
      - [ ] Confirmed (via STATUS.md + git log + a repo-wide grep for `open_run` outside test files) that no live attended run has occurred
  - **Files to Create/Modify**: none (verification only)
  - **Dependencies**: none
  - **Testing Requirements**: N/A (this task *is* test-running)

* **[T-L0.2] Version reconciliation** [Effort: S]
  - **Description**: Bump `pyproject.toml` version from `1.0.0` to `2.2.0` to match STATUS.md / docs, which already describe the shipped state as v2.2. The `v2.2` git tag is created **manually** by the maintainer (resolved 2026-07-21 — not automated in code). Add a BACKLOG.md future action item to *consider* automating tag creation in CI on merge to main if it later proves worth it.
  - **Acceptance Criteria**:
      - [ ] `pyproject.toml`'s `[project].version` reads `2.2.0`
      - [ ] `uv sync` (or equivalent) still resolves cleanly post-bump
      - [ ] BACKLOG.md has a "future: consider CI-automated release tagging" action item
  - **Files to Create/Modify**:
      - `pyproject.toml` — version bump
      - `docs/1_product_and_research/BACKLOG.md` — future CI-tagging action item
  - **Dependencies**: T-L0.1
  - **Testing Requirements**: none (metadata-only change); confirm `uv sync` doesn't error

* **[T-L0.3] `xfail` the superseded `score_jobs` adapter test** [Effort: S] — *investigation complete; decision made, do not re-litigate*
  - **Description**: Mark `test_score_jobs_adapter_real_import_success` `@pytest.mark.xfail(strict=False)` with a **precise reason string naming the three replacement modules**, and add a `BACKLOG.md` entry. **The investigation is already done (2026-07-21) — do not redo it and do not attempt a fix in L0.** Verified facts: content-pipeline (at `/Users/anant/PersonalProjects/content-pipeline`) **decomposed** `ScoreJobsUseCase` into `application/use_cases/score_jobs_{ingest,prep,score}.py` (+ `score_merge.py`); **no `ScoreJobsUseCase` class exists anywhere in that repo**; atlas's `score_jobs_adapter.py:16` imports the pre-split class and calls `run_pending()`. Re-targeting the adapter means designing the ingest → prep → score composition — **`job`-workflow scope, unrelated to loop mode**. **Framing requirement:** the reason string and BACKLOG entry must say *"the `LIB:content_pipeline.score_jobs` adapter targets a superseded content-pipeline API; re-targeting it to the ingest/prep/score pipeline is `job`-workflow scope"* — **not** "the drift test is broken." The test is reporting a real, correct signal; L0 simply isn't the phase that acts on it. **Do not silently skip or delete the test** — the drift must stay visible.
  - **Acceptance Criteria**:
      - [ ] `pytest tests/ -q` exits 0 (green) — the test is `xfail`-marked, so it no longer counts as a failure
      - [ ] The `xfail` reason string names `score_jobs_ingest` / `score_jobs_prep` / `score_jobs_score` explicitly, so the next reader gets the replacement API without re-investigating
      - [ ] `strict=False` (not `strict=True`) — if content-pipeline ever restores a compatible symbol, an unexpected pass must not fail the suite
      - [ ] A `BACKLOG.md` entry frames this as `job`-workflow adapter re-targeting work, not as a broken test
      - [ ] **No change to `src/atlas/library_adapters/score_jobs_adapter.py`** — re-targeting is explicitly out of L0 scope
  - **Files to Create/Modify**:
      - `tests/integration/test_job_adapters_real_import.py` — `xfail` mark + reason string
      - `docs/1_product_and_research/BACKLOG.md` — `job`-workflow adapter re-targeting entry
  - **Dependencies**: T-L0.1
  - **Testing Requirements**: the mark itself is validated by the full suite going green

* **[T-L0.4] `ClaudeCodeBackend` loop-mode telemetry — argv + parse_result + parse_usage** [Effort: M]
  - **Description**: Extend `ClaudeCodeBackend.build_argv` to append `--output-format json` (and the permission-profile flags — `--permission-mode`, `--allowedTools`, `--max-turns`) only when the corresponding `extra_flags` keys are set; attended callers (no keys set) get byte-identical argv to today. Extend `parse_result` to detect and handle the JSON envelope (mapping `subtype` → status) while preserving the plain-text branch unchanged. Add a new `parse_usage(stdout) -> UsageStats | None` method (not on the `CliBackend` Protocol) that extracts `total_cost_usd`/`usage.input_tokens`/`usage.output_tokens` when the JSON envelope is present, else `None`.
  - **Acceptance Criteria**:
      - [ ] `build_argv` with no telemetry/permission `extra_flags` keys produces argv identical to the current `test_claude_code_backend_argv_byte_identical_to_phase2` golden string
      - [ ] `build_argv` with `extra_flags={"telemetry": "json"}` includes `--output-format json`
      - [ ] `build_argv` with permission-profile `extra_flags` set includes `--permission-mode acceptEdits`, `--allowedTools <value>`, `--max-turns <value>`, and **never** `--dangerously-skip-permissions` under any input (negative-assertion test)
      - [ ] `parse_result` on plain-text stdout behaves exactly as before (existing tests pass unmodified)
      - [ ] `parse_result` on each documented JSON `subtype` value maps to the correct `(status, output_text, error_type)`
      - [ ] `parse_result` never raises on malformed JSON — falls back to a `claude_unparseable_json` failure
      - [ ] `parse_usage` returns `None` on plain-text stdout, correct `UsageStats` on valid JSON, and `UsageStats(None, None, None)` (not a raised exception) on JSON missing the usage keys
  - **Files to Create/Modify**:
      - `src/atlas/cli_backend.py` — `ClaudeCodeBackend.build_argv`/`parse_result` extended; `parse_usage` + `UsageStats` added
      - `tests/unit/test_cli_backend.py` — new test cases per Testing Strategy above
  - **Dependencies**: T-L0.1
  - **Testing Requirements**: Unit (all branches above); no integration test needed for this task alone (covered by T-L0.6)

* **[T-L0.5] Thread per-span tokens into `PlumbIO`** [Effort: S] — *plumb spike resolved; scope reduced*
  - **Description**: Extend `PlumbIO.record_span()` with an optional `tokens: tuple[int, int] | None = None` kwarg matching plumb's **confirmed** real signature `RunHandle.add_span(..., tokens=(in, out))` (`plumb/api.py:264`). When present, thread the tuple straight to `add_span`; when `None` (every existing call site), behavior is byte-identical to today. Stub mode (`real=False`) buffers the tuple in `self.spans` for test assertions. The caller decomposes `ClaudeCodeBackend.parse_usage`'s `UsageStats` into the `(input_tokens, output_tokens)` tuple; `total_cost_usd` is carried separately (logged / returned), **not** written to plumb. **Do NOT attempt to write run-level `runs.dollar_cost`/`tokens_in`/`tokens_out`** — confirmed unreachable in plumb v1.0.1's online path (`finalize_run`/`_FINALIZE_RUN` at `plumb/storage_sqlite.py:431` sets none; `RunHandle` has no cost setter). That is a plumb-P1-a dependency, tracked in BACKLOG.md, not built here.
  - **Acceptance Criteria**:
      - [ ] `record_span(..., tokens=None)` (today's call shape, every existing call site) behaves identically to pre-L0 — zero regression in existing `PlumbIO` tests
      - [ ] `record_span(..., tokens=(in, out))` in stub mode captures the tuple in the in-memory buffer for test assertions; in real mode passes it to `add_span(tokens=...)`
      - [ ] No code attempts a run-level cost/token write (verified by inspection — there is no such reachable plumb call)
      - [ ] A `BACKLOG.md` entry exists for the plumb-P1-a run-level `dollar_cost` threading (`set_usage` + `finalize_run` change on plumb's side, then atlas consumes it)
  - **Files to Create/Modify**:
      - `src/atlas/plumb_io.py` — `record_span()` gains `tokens=` kwarg
      - `tests/unit/test_plumb_io.py` — new/extended (create file if it doesn't already exist — verify first)
      - `docs/1_product_and_research/BACKLOG.md` — plumb-P1-a run-level cost dependency entry
  - **Dependencies**: T-L0.4 (needs `UsageStats` to exist so the caller can decompose it)
  - **Testing Requirements**: Unit only; the real `add_span(tokens=...)` write is exercised end-to-end alongside T-L0.8's live run

* **[T-L0.6] `Deliverer` Protocol + `GhPrDeliverer`** [Effort: M]
  - **Description**: New module `src/atlas/deliverer.py` implementing the `Deliverer` Protocol and `GhPrDeliverer` per the Detailed Component Design above: push the worktree branch (never `main`, never `--force`), open a PR via `gh pr create`, then call `WorktreeManager.cleanup()`. Wire error handling exactly per the Error Handling table (push failure ⇒ no cleanup; PR failure ⇒ no cleanup; cleanup failure ⇒ swallowed, PR still returned).
  - **Acceptance Criteria**:
      - [ ] Happy path: correct two-subprocess sequence, `PrRef` returned, `cleanup()` called exactly once
      - [ ] Push failure ⇒ `DeliveryError`, `gh pr create` never invoked, `cleanup()` never invoked
      - [ ] PR-create failure ⇒ `DeliveryError`, `cleanup()` never invoked
      - [ ] `gh` binary missing ⇒ `DeliveryError` with a clear message (not a raw `FileNotFoundError` traceback)
      - [ ] **Load-bearing security test**: a fake `subprocess.run` that raises `AssertionError` if called with `"main"` or `"--force"` anywhere in argv passes for every code path exercised by the other tests in this task
      - [ ] Cleanup failure after successful delivery does not prevent `PrRef` from being returned
  - **Files to Create/Modify**:
      - `src/atlas/deliverer.py` — new
      - `tests/unit/test_deliverer.py` — new
  - **Dependencies**: none (only depends on existing `WorktreeManager`)
  - **Testing Requirements**: Unit (all cases above), ≥ 85% coverage on the new file

* **[T-L0.7] Integration test — loop-mode dispatch end-to-end (mocked) + attended-mode invariance proof** [Effort: S–M]
  - **Description**: Add/extend an integration test proving (a) a full `SubprocessStageRunner.run()` dispatch with loop-mode `extra_flags` set produces a `StageOutcome` plus extractable `UsageStats`, with the subprocess mocked to return a realistic JSON envelope; and (b) the existing attended dev-pipeline dispatch path is provably unaffected (same spirit as Phase 3's `test_dev_pipeline_unaffected_by_phase_3`) — byte-identical argv when no loop-mode flags are set.
  - **Acceptance Criteria**:
      - [ ] `test_claude_backend_loop_mode_telemetry_end_to_end` passes against a captured/realistic JSON fixture
      - [ ] `test_dev_pipeline_unaffected_by_phase_l0` passes — proves zero behavior change for attended callers
      - [ ] Full existing test suite (238 previously-passing tests, minus/plus T-L0.3's resolution) still green
  - **Files to Create/Modify**:
      - `tests/integration/test_cli_backend_dispatch.py` — extended
  - **Dependencies**: T-L0.4, T-L0.5
  - **Testing Requirements**: Integration

* **[T-L0.8] First live attended run (manual, off-CI)** [Effort: S, but real-time/serial]
  - **Description**: Execute FR-L0.1 for real: `atlas run "<small real task>" --workflow dev` in a scratch git repo against the live `claude` backend (no mocks). Observe and record: does the subprocess spawn correctly, do gate prompts appear and block correctly, does a plumb run get created with a real span tree in `~/.plumb/plumb.db`. This is the phase's headline exit criterion (§13 #1, **as restated** — span-level tokens, not run-level cost) and cannot be automated — it is the first time this code path has ever executed against a real backend. Note: attended `dev` runs stay plain-text (no `--output-format json`), so the *attended* live run confirms spawn/gates/spans but not token telemetry; to observe real `spans.tokens` end-to-end, also do one manual loop-mode-flagged dispatch (`--output-format json` path) against live `claude` and confirm the tuple lands in `spans.tokens`.
  - **Acceptance Criteria**:
      - [ ] A real `atlas run` completes at least through gate 0, with a real subprocess spawn confirmed (e.g. via process inspection or explicit logging)
      - [ ] A plumb `runs` row exists in `~/.plumb/plumb.db` with a non-empty span tree after the run
      - [ ] One manual loop-mode (`--output-format json`) dispatch against live `claude` shows a non-zero `spans.tokens` value written via `add_span(tokens=...)` — the real proof the telemetry path works (attended mode alone can't show this)
      - [ ] Findings (timing, actual JSON envelope shape observed, any surprises vs. the mocked-test assumptions, and confirmation that run-level `dollar_cost` is indeed unwritable in the installed plumb version) are written into `headless-clis-reference.md`
  - **Files to Create/Modify**:
      - `docs/1_product_and_research/headless-clis-reference.md` — findings appended
  - **Dependencies**: T-L0.1 (baseline confirmed first)
  - **Testing Requirements**: Manual/E2E only — not CI-gated

* **[T-L0.9] Manual delivery smoke test (off-CI)** [Effort: S]
  - **Description**: Exercise `GhPrDeliverer.deliver()` for real against a scratch/test GitHub repo: create a throwaway branch inside a worktree, call `deliver()`, confirm a real PR appears on GitHub, and confirm `main` is untouched (`git log main` before/after, mirroring `test_main_branch_isolation.py`'s existing assertion style but run manually against a live remote).
  - **Acceptance Criteria**:
      - [ ] A real PR is created and visible via `gh pr view` or the GitHub UI
      - [ ] `git log main` (or the default branch) is byte-identical before and after
      - [ ] No `--force` push occurred (confirm via `git reflog` on the remote-tracking branch, or by inspecting the exact command run)
  - **Files to Create/Modify**: none (manual verification; findings noted in this TRS's context file)
  - **Dependencies**: T-L0.6
  - **Testing Requirements**: Manual/E2E only — not CI-gated

* **[T-L0.10] Lint, type-check, coverage gate** [Effort: S]
  - **Description**: Run `ruff check`, `ruff format --check`, `mypy --strict src` and confirm all green; confirm coverage gates hold (≥ 80% repo-wide, ≥ 85% on `deliverer.py` and the `cli_backend.py` additions).
  - **Acceptance Criteria**:
      - [ ] `ruff check` clean
      - [ ] `ruff format --check` clean
      - [ ] `mypy --strict src` clean
      - [ ] Coverage gates met
  - **Files to Create/Modify**: none (verification task)
  - **Dependencies**: T-L0.2, T-L0.3, T-L0.4, T-L0.5, T-L0.6, T-L0.7
  - **Testing Requirements**: N/A (this task is the verification gate itself)

* **[T-L0.11] Update STATUS.md** [Effort: S]
  - **Description**: Record Phase L0 completion in `STATUS.md` (module coverage table entry for `deliverer.py`; note on live-run confirmation; note on version bump) following the precedent set by prior phase completions.
  - **Acceptance Criteria**:
      - [ ] `STATUS.md` reflects L0 completion, new test count, new module
  - **Files to Create/Modify**:
      - `STATUS.md`
  - **Dependencies**: T-L0.10
  - **Testing Requirements**: none (docs-only)

---

## Phase Deliverables

- Working `ClaudeCodeBackend` loop-mode telemetry path (opt-in, attended mode unaffected) and `Deliverer`/`GhPrDeliverer` primitive, both manually exercised end-to-end at least once against real external systems (`claude`, `git`, `gh`).
- Version reconciliation (`pyproject.toml` → `2.2.0`) and a genuinely green test suite (drift test fixed or honestly `xfail`-marked).
- Tests passing: full existing suite (239, adjusted per T-L0.3) plus new unit/integration coverage for `deliverer.py` and the `cli_backend.py` telemetry additions.
- Documentation updated: `headless-clis-reference.md` (live-run findings), `STATUS.md` (phase completion), `BACKLOG.md` (if the drift test is `xfail`-marked rather than fixed).

## Pending Decisions & Clarifications

**All five are now closed (2026-07-21).** Kept as a decision record — each entry states what was chosen and why, so the reasoning survives into L1/L2 rather than being re-litigated.

1. **✅ CLOSED — `Deliverer.deliver()` ships narrow in L0; §3.7's shape is the L2 target.** L0 builds `deliver(*, run_id, branch, worktree_path, title, body)`. Rejected the placeholder alternative (`issue: dict | None` / `scores: dict | None` "so the Protocol never changes") because that stability is illusory — a Protocol with `dict | None` params is unchecked, not stable, and you'd design the type twice with no type protection in between. Widening in L2 is mechanical and safe: the params are **keyword-only**, so adding `issue`/`scores` is additive and mypy flags every call site. `title`/`body` stay **pre-rendered strings** in L0 — composing a body from an issue is L2's job, not the `Deliverer`'s, which is precisely what lets L2 extend it without disturbing L0's delivery mechanics. See the callout box in Detailed Component Design (written so a future reader diffing TRD §3.7 against the code doesn't file it as a bug).
2. **✅ CLOSED (2026-07-21) — plumb write surface confirmed and TRD-v3 amended upstream.** `RunHandle.add_span(..., tokens=(in, out))` (`plumb/api.py:264`) is the real span-token path (persists *summed* into `spans.tokens`; in/out split lost until plumb v1.1). Run-level `runs.dollar_cost`/`tokens_in`/`tokens_out` are **not writable** from the online `with run()` path (`finalize_run` at `plumb/storage_sqlite.py:431` sets none; no `RunHandle` cost setter) — a live run today yields `dollar_cost = NULL`. **Critically, `total_cost_usd` has no per-span fallback either** (no per-span cost column in v1.0.1 or v1.1), so it is a genuine plumb **P1-a (`set_usage`)** dependency, not something L0 can route around. TRD-v3 §3.6, §5, §7, §10, §12, §13 (#1/#5/#12), §14-L0, and Appendix A were all amended to reflect this — **the TRD, not this TRS, is now the authority**, and no restatement question remains open. Downstream consequence to carry into the **L2 TRS**: `max_dollars_per_day` cannot be enforced against `runs.dollar_cost` pre-P1-a; L2 must accumulate in-process `total_cost_usd` (persisted across restarts) or lean on `max_runs_per_day` as the hard bound (TRD-v3 §12).
3. **✅ CLOSED — `xfail(strict=False)`, and the answer is "neither fix nor drift": the API was decomposed.** Investigated against the sibling repo at `/Users/anant/PersonalProjects/content-pipeline` and **verified**: `application/use_cases/score_jobs.py` does not exist; content-pipeline split it into `score_jobs_ingest.py` / `score_jobs_prep.py` / `score_jobs_score.py` (+ `score_merge.py`), and **no `ScoreJobsUseCase` class exists anywhere in that repo**. atlas's `score_jobs_adapter.py:16` still imports the pre-split class and calls `run_pending()` on it. So this is **not a rename** (no one-line fix available) and **not unreachable** (not a true permanent xfail) — it is an adapter targeting a **superseded API**, where re-targeting requires designing how the adapter composes ingest → prep → score. That is **`job`-workflow design work, entirely unrelated to loop mode**, and pulling it into L0 would smuggle an unrelated redesign into the loop's first phase. **Framing matters for the reason string:** do not write "the drift test is broken" — write that the `LIB:content_pipeline.score_jobs` adapter targets a superseded API and re-targeting is `job`-workflow scope. TRD-v3 §14's "fix or `xfail`" phrasing was amended upstream to record why `xfail` is correct here.
4. **✅ CLOSED — defer the starter `.claude/settings.json` allowlist to L2.** An allowlist is a **security boundary with asymmetric failure modes**: too narrow → the loop stalls on a denied tool (loud, obvious, cheap to fix); too broad → an unattended agent's capabilities are silently widened, with nothing to tell you. The second is the one you must not author blind — and the natural instinct when guessing at an allowlist is to be *generous*, exactly the wrong direction. L0 cannot know the required tool set because `loop_dev.yaml` (L1) and the loop's prompt shape (L2) don't exist yet. **What L0 does instead** (already in scope, T-L0.4): define the permission *profile* — `--permission-mode acceptEdits` + `--max-turns` + the fact that an allowlist is required and lives in the target repo. The allowlist's **contents** get derived empirically in L2: start deliberately tight, widen only on observed denials.
5. **✅ RESOLVED (maintainer, 2026-07-21) — git tagging stays manual.** T-L0.2 bumps the version string; `git tag v2.2 && git push --tags` is a manual, human-discretionary step for now. A future action item (folded into BACKLOG.md) may automate tagging in CI on merge to main if/when it becomes worth it — not built in L0.
