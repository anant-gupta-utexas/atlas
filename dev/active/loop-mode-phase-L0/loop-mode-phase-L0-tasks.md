# Tasks — Loop Mode, Phase L0 TRS

Progress checklist. Source-of-truth for design is
[`loop-mode-phase-L0-plan.md`](./loop-mode-phase-L0-plan.md).
Reference notes live in
[`loop-mode-phase-L0-context.md`](./loop-mode-phase-L0-context.md).

## Current

```
phase: not started
gate:  none
next:  T-L0.1 baseline verification
```

## Status — no blocking dependency; two confirmed drifts to fix as part of this phase

Shipped v2.2 is the declared dependency (STATUS.md: "v2.2 — Phase 3 (CLI backend dispatch)
complete", 239 tests, 95% coverage). Re-running the suite at TRS authoring time (2026-07-21)
confirmed **238 passing, 1 failing**:

```
FAILED tests/integration/test_job_adapters_real_import.py::test_score_jobs_adapter_real_import_success
  AttributeError: module 'application.use_cases' has no attribute 'score_jobs'
```

This is exactly the "content-pipeline drift integration test" TRD-v3 §14 Phase L0 calls out —
T-L0.3 owns fixing or `xfail`-marking it. Also confirmed: `pyproject.toml` still says
`version = "1.0.0"` against a shipped state everywhere else called v2.2 — T-L0.2 owns the bump.

There is **no hard T-L0.0 verification gate** (mirrors Phase 3's precedent of "no hard
T-hyphen-0 gate when the prior phase's seams are already confirmed present"). T-L0.1 is a
lightweight sanity re-confirmation, not a blocking checkpoint.

## Tasks (flat — Phase L0 only, no sub-phases)

- [ ] **T-L0.1** — Baseline verification: re-run full suite, reconfirm the exact failing test + pass count; confirm no prior live `atlas run` against a real `claude` backend exists in git history / STATUS.md
- [ ] **T-L0.2** — Version reconciliation: bump `pyproject.toml` `1.0.0` → `2.2.0`; `v2.2` git tag created manually by maintainer (resolved 2026-07-21); add a BACKLOG "future: consider CI-automated tagging" item
- [ ] **T-L0.3** — `xfail(strict=False)` `test_score_jobs_adapter_real_import_success` + `BACKLOG.md` entry. **Decision made, investigation done — do not re-litigate or attempt a fix.** content-pipeline decomposed `ScoreJobsUseCase` into `score_jobs_{ingest,prep,score}.py`; the adapter targets a **superseded API** and re-targeting is `job`-workflow scope. Reason string must name the three modules and frame it as superseded-API, **not** "broken test." No change to `score_jobs_adapter.py`
- [ ] **T-L0.4** — `ClaudeCodeBackend` loop-mode telemetry: `build_argv` gains conditional `--output-format json` + permission-profile flags (gated on `extra_flags`, absent by default); `parse_result` gains a JSON-envelope branch (plain-text branch unchanged); new `parse_usage()` method + `UsageStats` dataclass
- [ ] **T-L0.5** — Thread per-span tokens into `PlumbIO.record_span()` via a `tokens: tuple[int,int] | None = None` kwarg matching plumb's **confirmed** `add_span(tokens=(in, out))` (`plumb/api.py:264`); `None`-safe. **Do NOT** write run-level `dollar_cost`/`tokens_in`/`tokens_out` — confirmed unreachable in plumb v1.0.1's online path; deferred to plumb P1-a (BACKLOG entry). Caller decomposes `UsageStats` into the tuple; `total_cost_usd` stays in-memory only. *(plumb spike resolved 2026-07-21 — scope reduced from original)*
- [ ] **T-L0.6** — `Deliverer` Protocol + `GhPrDeliverer` in new `src/atlas/deliverer.py`: push branch (never `main`, never `--force`) → `gh pr create` → `WorktreeManager.cleanup()`; full error-handling table from the plan
- [ ] **T-L0.7** — Integration tests: loop-mode dispatch end-to-end (mocked JSON envelope) + attended-mode invariance proof (byte-identical argv when no loop-mode flags set)
- [ ] **T-L0.8** — First live attended run (manual, off-CI): real `atlas run "<task>" --workflow dev` against the live `claude` backend; confirm subprocess spawn + gate prompts + a real plumb run with spans; capture findings into `headless-clis-reference.md`
- [ ] **T-L0.9** — Manual delivery smoke test (off-CI): real `GhPrDeliverer.deliver()` against a scratch GitHub repo; confirm a real PR appears and `main` is untouched
- [ ] **T-L0.10** — Lint/type/coverage gate: `ruff check`, `ruff format --check`, `mypy --strict src`, coverage (≥ 80% repo-wide, ≥ 85% on `deliverer.py` + `cli_backend.py` additions)
- [ ] **T-L0.11** — Update `STATUS.md` with L0 completion

## Exit criteria (TRD-v3 §13 #1, #2, #4 — copied for tracking)

- [ ] **§13 #1 (as amended in TRD-v3, 2026-07-21)** — A live `atlas run "<task>"` on the `claude` backend produces a plumb run whose **`code_gen` span carries real `tokens`** from the backend JSON. Run-level `dollar_cost` / token roll-up is **explicitly not an L0 gate** — deferred to plumb P1-a (`set_usage`), verified at L2
- [ ] **§13 #2** — Full v2 suite green; `atlas run` unchanged
- [ ] **§13 #4** — The `Deliverer` pushes a branch + opens a PR for a completed run and calls `cleanup()`; asserted never to push `main` or force-push

(§13 #3 — `CodexBackend` dispatch — belongs to Phase L1, not tracked here.)

## Resolved decisions (see plan's Detailed Component Design / context.md Decisions table for full rationale)

- [x] **#1 — `parse_usage()` is additive, not a `CliBackend` Protocol member.** Keeps the Protocol at 3 methods across all backends. Binding on T-L0.4.
- [x] **#2 — JSON-vs-plain-text detection is by content sniffing, not a mode flag through `parse_result`'s signature.** Protocol signature stays `(stdout, stderr, returncode)` for every backend. Binding on T-L0.4.
- [x] **#4 — `record_span()` gains `tokens=(in, out)` as an optional kwarg** matching plumb's confirmed `add_span(tokens=...)` (`plumb/api.py:264`), not `usage=UsageStats` and not a new sibling method. Run-level `dollar_cost` deferred to plumb P1-a. Binding on T-L0.5. *(plumb spike resolved 2026-07-21.)*
- [x] **#5 — Push safety enforced by hardcoded argv shape + defensive branch-name assertion**, not a runtime flag. Binding on T-L0.6.
- [x] **#6 — Failed `git push`/`gh pr create` does NOT trigger worktree cleanup.** Preserves unpushed work for manual recovery. Binding on T-L0.6.
- [x] **#7 — Plumb write surface (was Open #2), resolved 2026-07-21.** `add_span(tokens=(in,out))` is the real path; run-level `dollar_cost`/`tokens_in`/`tokens_out` unreachable in v1.0.1's online path → deferred to plumb P1-a. Binding on T-L0.5.
- [x] **#8 — `git tag v2.2` stays manual (was Open #5), resolved 2026-07-21.** Version string bumped in code (T-L0.2); tag created by hand. Future CI-automation is a BACKLOG item, not L0 work.

## Decisions closed during TRS authoring (2026-07-21) — none open

All four items below were open questions at first draft and are now settled. Kept as a
record so L1/L2 don't re-litigate them.

- [x] **#1 — `Deliverer.deliver()` ships NARROW in L0**: `deliver(*, run_id, branch, worktree_path, title, body)`. TRD-v3 §3.7's `issue`/`scores` shape is the **L2 target**, not an L0 requirement — L0 implements a documented subset (see the callout box in the plan's Detailed Component Design, written so a reader diffing TRD-vs-code doesn't file a bug). Rejected `dict | None` placeholders: unchecked ≠ stable, and it designs the type twice. Widening is safe because params are keyword-only → additive + mypy-checked. `title`/`body` stay **pre-rendered strings** so L2 can compose richer bodies without touching delivery mechanics. Binding on T-L0.6.
- [x] **#2 — §13 #1 amended in TRD-v3 itself** (not just restated in the TRS). L0's gate is "the `code_gen` span carries real `tokens`"; run-level `dollar_cost` is explicitly **not** an L0 exit gate → deferred to plumb **P1-a (`set_usage`)**, verified at L2. Tokens and dollars are **not symmetric** — `total_cost_usd` has no per-span sink in any plumb version, so don't look for one. TRD-v3 §3.6/§5/§7/§10/§12/§13/§14-L0/Appendix A all updated; the TRD is the authority.
- [x] **#3 — `xfail(strict=False)` the `score_jobs` adapter test — verified, not a judgment call.** content-pipeline **decomposed** `ScoreJobsUseCase` into `score_jobs_{ingest,prep,score}.py` (+ `score_merge.py`); no such class exists anywhere in that repo. So: **not a rename** (no one-line fix) and **not unreachable** (not a permanent xfail) — the adapter targets a **superseded API**, and re-targeting it is **`job`-workflow scope, unrelated to loop mode**. Reason string must name the three replacement modules and frame it as superseded-API, **not** "broken test." No change to `score_jobs_adapter.py` in L0. Binding on T-L0.3.
- [x] **#4 — Starter `.claude/settings.json` allowlist DEFERRED to L2.** An allowlist is a security boundary with **asymmetric failure modes**: too narrow → loud, cheap stall; too broad → silently widened unattended-agent capability with no signal. L0 can't know the tool set (`loop_dev.yaml` is L1, prompt shape is L2), and guessing biases *generous* — the wrong direction. L0 defines the permission **profile** only (`acceptEdits` + `--max-turns` + "an allowlist is required, lives in the target repo"); L2 derives **contents** empirically — start tight, widen on observed denials. Binding on T-L0.4 (profile only, no allowlist file authored).

## Notes for implementation

- **No upstream blocker.** v2.2 is shipped and merged. T-L0.1 is a sanity re-run, not a hard gate.
- **Byte-identity is the load-bearing regression claim, same as Phase 3.** `ClaudeCodeBackend.build_argv` with no loop-mode `extra_flags` set must produce argv identical to the existing `test_claude_code_backend_argv_byte_identical_to_phase2` golden string. If a change to T-L0.4 breaks that test, fix the implementation — do not loosen the test.
- **The `Deliverer` push-safety test is the security-critical test of this phase**, mirroring Phase 3's auth-preflight test: assert the dangerous call (`main` / `--force`) never fires, not just that the happy path looks right.
- **T-L0.8 and T-L0.9 are real, serial, off-CI actions** — they cannot be parallelized with each other trivially (T-L0.9 needs T-L0.6 done; T-L0.8 only needs T-L0.1) and both require live external systems (a real `claude` subscription/session, a real scratch GitHub repo with `gh` auth). Budget real wall-clock time for these, not just engineering time.
- **plumb's write surface is now confirmed (2026-07-21) — build against it directly.** `RunHandle.add_span(..., tokens=(in, out))` (`plumb/api.py:264`) is the real span-token path; tokens persist *summed* into `spans.tokens` (in/out split lost until plumb v1.1). Run-level `runs.dollar_cost`/`tokens_in`/`tokens_out` are **not writable** from the online `with run()` path (`finalize_run` at `plumb/storage_sqlite.py:431` sets none; no `RunHandle` cost setter) — do not attempt them; they're a plumb-P1-a dependency (BACKLOG). T-L0.5 threads `tokens=(in, out)` and nothing more.

## Implementation notes (post-hoc — fill in after work is done)

_Not yet started._
