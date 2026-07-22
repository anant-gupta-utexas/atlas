---
title: atlas loop mode — design note (autonomous, minimal-input development loop)
status: design note — feeds TRD-v2 + system_design
created: 2026-07-21
last_reviewed: 2026-07-21
tags: [loop, orchestrator, github-issues, claude-code, codex, worktree, plumb, autonomy]
---

# atlas loop mode — design note

Upstream design note for adding an **autonomous "loop" mode** to atlas. This is a
research/decision artifact, not a TRS: it defines the problem, the locked
decisions, and a set of TRD Development Phases (L0–L4). Each phase is then
detailed into its own per-phase TRS via the standard Tech-Lead workflow.

> **Scope of this doc vs. the TRD.** This note proposes the phases and the
> load-bearing design decisions. `docs/2_architecture/TRD-v2.md` (phase contract)
> and `docs/2_architecture/system_design.md` (component architecture) are the
> authoritative homes once updated — this note feeds them.

---

## 1. Problem & goal

atlas today is a single-run orchestrator: `atlas run "<task>"` walks one workflow
to completion, with the operator present at the gates. The goal of loop mode is
**minimal-input development** — the operator files small tickets, and a
long-running loop keeps pulling them, running the pipeline in isolated worktrees,
and opening PRs, without a human driving each run. The operator's involvement
collapses to two points: **filing the ticket** and **reviewing the PR**.

This is "loop engineering": designing the outer feedback loop that lets a coding
agent plan → change code → verify → deliver, repeatedly, until the queue is empty
or a budget is hit — rather than prompting the agent by hand each time.

### What already exists (so the loop is mostly wiring, not new machinery)

atlas is at v2.2. The following are built and tested, and the loop reuses them:

- **Workflow engine** — YAML workflows (`workflow_loader.py`, packaged
  `workflows/{dev,job,job_cli}.yaml`); a workflow is a `tuple[StageSpec, ...]`.
- **`Pipeline` state machine** (`orchestrator.py`) — `start`/`resume`/`step`/
  `run_to_completion`; interactive gates (`ClickPrompter`) and an async gate path
  (`gate_is_async`, used by `code_gen`, scored later by the post-commit hook).
- **`CliBackend` abstraction** (`cli_backend.py`) — a strategy protocol
  (`build_argv`/`parse_result`/`preflight`) with `ClaudeCodeBackend` and
  `AntigravityBackend`; backend resolution is 4-tier (per-stage → workflow
  default → `.atlas.toml [backend]` → hard default `claude`). The prior phase's
  own notes name a third backend (e.g. `codex`) as the explicit extension point:
  a one-file change to `cli_backend.py`, zero changes elsewhere by construction.
- **`WorktreeManager`** (`worktree.py`) — one git worktree per run under
  `.atlas/worktrees/`, branch `atlas/<slug>-<shortid>` off `main`; `create()` is
  wired for `isolate` stages. (`merge_back()`/`cleanup()` exist but have no
  production callers yet.)
- **plumb integration** (`plumb_io.py`) — `plumb.run()` → spans/scores, child
  runs via `parent_run_id`, gate rejection → `examples` row. plumb's `runs` table
  already carries `tokens_in`/`tokens_out`/`dollar_cost`; judge scoring exists
  (`plumb judge run`).
- **Compaction-safe run state** — `tasks.md` + `.atlas/current-run` (`state.py`).

### What's missing (what loop mode adds)

1. A **work queue** and a **daemon** that keeps pulling from it (today
   `run_to_completion()` is one run, in-process).
2. **Telemetry** into plumb — `ClaudeCodeBackend` passes no `--output-format
   json`, so cost/tokens never reach the `runs` columns that exist for them.
3. A **headless permission profile** — headless `code_gen` stalls the moment a
   tool call needs approval (no permission flags today).
4. A **branch → PR delivery path** — the PR is the autonomy gate; the dead
   `merge_back()` path is replaced by push-branch + PR.
5. A **second engine** (`codex`) alongside `claude`.

---

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Autonomy gate** | Agent opens a PR; **the operator merges.** The loop never pushes to `main`, never merges, never force-pushes. | The PR review is the attestation gate. Merge/close syncs back as a plumb `user_signal` score. Builds trust before any auto-merge is ever considered. |
| **Engines** | **Claude Code + Codex from day one.** `agy` stays experimental. | Two engines let plumb compare cost/quality per task type from the start. `agy`'s browser-OAuth default blocks headless use. |
| **First dogfood target** | **atlas builds atlas** — the loop works atlas's own backlog in worktrees. | Worktrees isolate the loop from the running copy; every iteration both tests and improves the machine. |
| **Work queue** | **GitHub Issues per repo**, label-driven, via the authenticated `gh` CLI. | Issue → branch → PR → merge is one service; `Closes #n` links automatically; merge status is the quality feedback channel. A graph-based issue tracker (e.g. beads) is a later swap if issue hygiene hurts. |
| **Docs** | **Local markdown**, versioned with the code. | Docs are build inputs the agent reads and diffs in the same PR as the change. A hosted renderer would be additive only — a no-op for the loop. |
| **Run-state** | `tasks.md` + `.atlas/current-run` **stay local files.** | Compaction-safe working memory *inside* a run; not a tracker concern. |
| **tmux** | **Observability only.** The loop runs in a detached tmux session the operator can attach to; control is the CLI + files, not tmux send-keys. | Matches the consensus that headless drives the work and tmux is for watching. |
| **Issue routing** | **Two lanes, hybrid routing** (see §3). | Not every issue is one-shot; "fix a typo" and "add a backend" need different paths. |

---

## 3. Two-lane routing (load-bearing design)

The loop triages each issue into one of two lanes. The router **is** the existing
workflow-selection seam (`wf:*` label → workflow YAML).

- **Router = hybrid.** An explicit `wf:quick` / `wf:planned` label wins. If the
  issue is unlabeled, a fast triage step (haiku) reads title + body and picks the
  lane. The operator stays in control when they care; the loop copes when they
  don't.

- **One-shot lane** (`wf:quick` → `loop_dev.yaml`: `plan → code_gen[isolate] →
  verify → PR`). Small/mechanical issues. The whole issue is the work item
  (one item per loop). Single PR, `Closes #n`.

- **Planned lane** (`wf:planned`). Large issues that need planning. The loop does
  **not** one-shot. Its first pass produces the planning artifact: it runs the
  per-phase TRS authoring step (`dev-docs-be`), opens a **plan-only PR** carrying
  just the `dev/active/<slug>/` triad (`-plan.md`/`-context.md`/`-tasks.md`),
  **surfaces the TRS's "Pending Decisions & Clarifications" in the PR body**, and
  **stops**. That PR is the decision gate — the operator reviews, answers the
  decisions, merges. Subsequent loop passes pick up the committed TRS and
  implement it **task by task**, each task its own worktree run + `/code-review`.
  → **multiple PRs per issue** (`Refs #n` on task PRs, `Closes #n` on the last);
  the issue closes when the last merges.

The planned lane is the loop driving the operator's normal per-phase TRS
discipline autonomously, escalating decisions **as a PR review** rather than
blocking on an interactive prompt.

---

## 4. Target architecture

```
GitHub Issues (label atlas:ready, per repo)   ← operator files small, one-outcome tickets
        │  poll via gh (interval)
        ▼
atlas loop daemon (tmux session "atlas-loop", detached)
        │  triage lane (wf:* label wins, else haiku classify)
        │  claim: atlas:ready → atlas:working
        ├── wf:quick   → loop_dev.yaml (plan→code_gen[isolate]→verify) → 1 PR (Closes #n)
        └── wf:planned → dev-docs-be → plan-only PR (TRS triad + Pending Decisions) → STOP
                          └─ (after merge) later passes: task-by-task PRs (Refs #n → Closes #n)
        │  engine per engine:* label: claude -p --output-format json | codex exec --json -C <wt>
        │  worktree per run · plumb run + spans + deterministic scores
        ▼
Deliver: push branch → gh pr create → comment run_id + scores      (never merges main)
        ▲
operator reviews PR → merge/close → next tick syncs state → plumb user_signal score; relabel/close issue
```

Self-healing (Phase L3): on verify/judge failure → write a plumb example → judge
classifies the failure mode → **one** child-run retry (`parent_run_id`) with the
diagnosis injected into the prompt → then PR or `atlas:blocked`.

---

## 5. Proposed Development Phases (for TRD-v2 §14)

Each phase is written to become one per-phase TRS. Effort tags are rough. Paths
are under `src/atlas/`.

### Phase L0 — Honest baseline
*Make the existing single-run path real, and add the primitives the loop needs.
No loop yet.*
- Version reconciliation: bump `pyproject.toml` → `2.2.0`, tag `v2.2`; fix/xfail
  the content-pipeline drift integration test so a green suite means green.
- **First live attended run** (has never happened): `atlas run "<small task>"
  --workflow dev` against the real `claude` backend; confirm subprocess spawn +
  gate prompts + a plumb run with spans. Capture findings into
  `headless-clis-reference.md`.
- `ClaudeCodeBackend` (`cli_backend.py`): add `--output-format json` to
  `build_argv`; `parse_result` maps `subtype` → status and surfaces
  `total_cost_usd` + `usage`. Thread these into plumb via `plumb_io.py`
  (`tokens_in`/`tokens_out`/`dollar_cost`). `AntigravityBackend` already parses
  JSON — same shape. **Guard behind a per-run flag** so attended `dev` runs keep
  human-readable stdout (the byte-identity gate-parity constraint still holds for
  attended mode).
- Headless permission profile (loop runs only): **not** `--bare` (the pipeline
  needs plugin/skill discovery), but `--permission-mode acceptEdits` + a curated
  `--allowedTools` allowlist (stored in the target repo's `.claude/settings.json`)
  + a `--max-turns` cap. No `--dangerously-skip-permissions` (worktrees do not
  sandbox the filesystem).
- Delivery primitive: a `Deliverer` (injected like `GatePrompter`, **not** a
  `StageSpec` — it is a post-success side-effect, so attended workflows are
  unaffected): push the worktree branch + `gh pr create`, then
  `WorktreeManager.cleanup()`. Replaces the dead `merge_back()` path.

### Phase L1 — CodexBackend + loop workflow  [S–M]
- `CodexBackend` (`cli_backend.py`, per the `CliBackend` protocol): `build_argv`
  = `codex exec <prompt> --json -C <worktree> --sandbox workspace-write` (+ model
  flag; `-C` satisfies codex's git-repo requirement via the worktree);
  `parse_result` consumes the JSONL stream → final `result` event → status/text +
  token/latency stats; `preflight` verifies auth and fails closed with a typed
  error (mirroring `AntigravityBackend.preflight`). Register in `_KNOWN_BACKENDS`
  / `make_backend()`. Per-run engine selection already resolves stage → workflow
  → toml → default; the loop injects the backend from an `engine:*` label.
- `loop_dev.yaml` (`workflows/`): an ungated `plan → code_gen(isolate) → verify`
  workflow, distinct from the 7-gate attended `dev.yaml`.
- Add a codex section to `headless-clis-reference.md`.

### Phase L2 — The loop daemon (core deliverable)  [L]
- `loop.py` (+ a thin `queue_gh.py` wrapping `gh` with JSON output). `tick()`:
  sync prior PRs first (merged → `user_signal` 1.0; closed-unmerged → 0.0;
  relabel/close the issue) → pull the next `atlas:ready` issue → **triage the
  lane** → claim (label swap + assignee) → build the prompt (issue title + body +
  guardrail "signs" + the existing `context_hint`) → dispatch (one-shot: run the
  Pipeline then the `Deliverer`; planned: run `dev-docs-be` → plan-only PR →
  stop) → comment + relabel. `run_forever()` loops `tick()` on the configured
  interval, enforcing budgets and the circuit breaker between ticks. **Startup
  reconciliation**: on boot, reset orphaned `atlas:working` issues with no open
  PR and prune stale worktrees (crash recovery).
- `[loop]` config in `.atlas.toml` / `~/.atlas/config.toml` (extend the frozen
  `Config`): `repos`, `poll_interval_s`, `max_runs_per_day`,
  `max_dollars_per_day` (checked against summed `total_cost_usd`), `max_turns`,
  breaker thresholds (`no_progress_limit=3`, `identical_error_limit=5`,
  `cooldown_min=30`), `concurrency=1`.
- CLI (`cli.py`, Typer): `atlas loop run` (foreground, for debugging) · `start`
  (`tmux new -d -s atlas-loop 'atlas loop run'`) · `stop` · `status` (budgets
  used, last tick, in-flight issue) · `attach` (`tmux attach -t atlas-loop`).
  Per-run logs to `.atlas/runs/<run_id>.log` for tailing.
- v1 loop is **sequential** (`concurrency=1`), so `.atlas/current-run` staying
  single-run is fine; note that `concurrency>1` (L4) requires per-run state keys.
- Tests: fake `gh` / `subprocess` / `time` (inject a stub queue + stub runner);
  assert the triage → claim → dispatch → deliver → sync state machine and the
  budget/breaker cutoffs.

### Phase L3 — Self-healing + routing  [M]
- Judge gate before the PR: a plumb Anthropic judge (haiku) over the diff for a
  task-completion score; a threshold (default 0.7) gates delivery.
- Diagnosis-injected retry: on verify/judge failure → `write_example`
  (`origin_run_id` = failed run) → judge classifies the failure mode
  (`flaky` / `wrong_approach` / `missing_context` / `infeasible`) → if retryable,
  re-dispatch as a **child run** (`reopen_run` with `parent_run_id`) with the
  diagnosis injected → cap at **one** retry, then `atlas:blocked`.
- Router v1 (stretch): prefer the engine/workflow that scores better in plumb for
  that task class — closing the measurement → routing loop.

### Phase L4 — Scale-out  [M]
- Add the plumb repo as a second target (its own backlog = ready-made issues);
  raise `concurrency > 1` (lift the single-run assumption); weekly `plumb run
  stats` → an external report (headline: cost-per-landed-PR + intervention rate).

---

## 6. Reused primitives (do not rebuild)

| Need | Existing thing |
|---|---|
| Worktree isolation / cleanup | `WorktreeManager.create()` / `cleanup()` (`worktree.py`) |
| Engine abstraction + registration | `CliBackend` protocol + `_KNOWN_BACKENDS` (`cli_backend.py`) |
| Backend/workflow selection | 4-tier resolver + workflow loader (already label-ready) |
| Deferred gate | `gate_is_async` path in `Pipeline.step()` (`orchestrator.py`) |
| Measurement / child runs / examples | `PlumbIO` (`plumb_io.py`); `plumb.run(parent_run_id=…)`; `plumb judge run` |
| Compaction-safe run state | `StateStore` + `tasks.md` (`state.py`) |
| Config merge | frozen `Config` from `.atlas.toml` (`config.py`) |
| Headless flag reference | `headless-clis-reference.md` |
| TRS authoring / review | `dev-docs-be`, `/code-review` — the loop drives these |

## 7. Risks & guardrails

- **Permissions, not YOLO** — allowlist + `acceptEdits` + `--max-turns` inside
  worktrees; codex `--sandbox workspace-write`; never `bypassPermissions`.
- **Budgets** — per-day run count + dollar cap (from captured `total_cost_usd`);
  circuit breaker on repeated no-progress / identical errors.
- **PR-only delivery** — never push `main`, never merge, never force-push.
- **Prompt injection via issue bodies** — private repos + single author today. If
  a target repo ever goes public, issue text becomes untrusted input to the loop:
  require an allowlisted author before dispatch, or sanitize.
- **Orphan recovery** — startup reconciliation resets stale labels/worktrees.

## 8. Design lineage

The loop's mechanics (poll a queue → dispatch a worker into an isolated worktree
→ gate → measure via plumb → diagnosis-injected child-run retry) come from the
**Shipwright** design — a "measured, self-healing agent orchestrator." Two aspects
of that design are **superseded** here: it was scoped as a separate repo with a
markdown-file control plane (chosen for demo-safety in a time-boxed build). For a
daily-driver loop, the control plane is **GitHub Issues** and the code lives **in
atlas**, reusing the `Pipeline` / `WorktreeManager` / `CliBackend` / `PlumbIO`
machinery atlas already ships rather than wrapping it from outside.

## 9. Cross-references

- Phase contract: [`../2_architecture/TRD-v2.md`](../2_architecture/TRD-v2.md)
- Component architecture: [`../2_architecture/system_design.md`](../2_architecture/system_design.md)
- Headless CLI flags/auth: [`headless-clis-reference.md`](./headless-clis-reference.md)
- plumb integration surface: [`PLUMB_API_REFERENCE.md`](./PLUMB_API_REFERENCE.md)
- Backlog: [`BACKLOG.md`](./BACKLOG.md)
