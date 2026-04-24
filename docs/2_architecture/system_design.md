# System Design

> **Status:** Stub (v1 Week 4). This document captures the design
> intent at PRD-approval time. Sections marked *TBD* will be filled
> during the Tech Lead pass; sections marked *locked* are design
> decisions already made in the PRD.

## Problem Statement & Requirements

Atlas is a local CLI runtime that walks a fixed 7-stage dev-workflow
pipeline, stops at six human gates, and writes every run as a typed
span tree into [plumb](https://github.com/anant-gupta-utexas/plumb) —
the measurement spine.

The full scope is in [`../1_product_and_research/PRD.md`](../1_product_and_research/PRD.md).
This document covers the *how*: component shape, data flow, boundary
guarantees, and trade-offs.

Key technical challenges:

1. **Surviving session compaction.** The pipeline's state cannot live
   in the agent's chat window; it must be reconstructable from files
   on disk.
2. **Main-branch isolation.** Stage 5 runs code-generation; that code
   must never touch `main` outside of a user-approved merge.
3. **Measurement without instrumentation drift.** Every stage must
   emit exactly one span of the expected kind; gates must write
   exactly one `scorer='user_signal'` row. Partial runs are tolerated;
   silently-missing rows are not.

## High-Level Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                      atlas CLI (Python)                          │
│                                                                  │
│   atlas run "<task>"   ──▶   State machine                       │
│   atlas status         ──▶   tasks.md reader                     │
│   atlas hook install   ──▶   .git/hooks/post-commit writer       │
│                                                                  │
│   [1] .atlas.toml + ~/.atlas/config.toml        (config merge)   │
│   [2] dev/active/<task>/tasks.md                (canonical state)│
│   [3] .atlas/current-run                        (run_id pointer) │
└────────────────────────────┬─────────────────────────────────────┘
                             │ plumb Python API (no direct SQLite)
                             ▼
               ┌─────────────────────────────┐
               │   plumb (sibling project)   │
               │   runs / spans / scores /   │
               │   examples    (SQLite)      │
               └─────────────────────────────┘

  external effects (invoked by name, not linked):
    • agent plugins:   DEV-ESSENTIALS, DEV-BE-PYTHON
    • git:             worktree, log, post-commit hook
```

Atlas is a thin state machine plus three files on disk. Everything
interesting — the LLM calls, the measurement storage, the version
control — happens in subprocesses atlas invokes or libraries atlas
imports. Atlas itself owns only:

- Stage ordering.
- Gate prompting.
- `tasks.md` read/write.
- Span start/close + score writes (via plumb's API).
- Worktree create/merge coordination for Stage 5.
- Post-commit hook install/uninstall.

## System Components & Services

### `atlas.cli` — CLI surface

- `run(task: str)` — inserts a `runs` row, creates
  `dev/active/<slug>/tasks.md`, starts the state machine.
- `status()` — prints `tasks.md`'s `## current` block; exits non-zero
  if no active run.
- `hook install` / `hook uninstall` — writes to / removes from
  `.git/hooks/post-commit` (idempotent).

One command entrypoint registered via `pyproject.toml`.

### `atlas.pipeline` — state machine

- Seven stages, hardcoded in order: `research`, `prd_draft`,
  `trd_draft`, `tds_gen`, `plan_review`, `code_gen`, `code_review`.
- Each stage: open span → invoke tool (or surface prompt for manual
  stages like research) → close span → check gate → either advance
  or pause.
- Gates: six hard stops, each a one-line user prompt (approve /
  reject), each writes one `scores` row.
- No dynamic routing in v1: stage → tool mapping is a 7-row constant
  (also committed as `tests/fixtures/routing_ground_truth.json`).

### `atlas.state` — `tasks.md` and `.atlas/current-run`

- Owns the `## current` block format.
- Writes per-stage checkbox sections on run start.
- Updates the `## current` block on gate transitions.
- `.atlas/current-run` holds the active `run_id` for the post-commit
  hook to read.

### `atlas.hook` — post-commit hook

- Small Python script dropped into `.git/hooks/post-commit`.
- Reads `.atlas/current-run` for the `run_id`, parses the most recent
  `/verify` and `/code-review` stdout captured in the stage 6 span,
  writes two deterministic scores.
- If the commit corresponds to gate 4, writes the `gate_commit`
  user-signal score and flips the state machine.
- Idempotent on the same commit SHA.

### `atlas.config` — TOML layering

- Loads `.atlas.toml` (project) merged over `~/.atlas/config.toml`
  (user default).
- Validates model-routing keys (shape only; v1 exercises one set).
- Returns a single frozen config object consumed by the state machine.

### `atlas.plumb_io` — measurement writes

- Thin wrapper over plumb's decorator + context-manager API.
- Never touches plumb's SQLite directly.
- Named here so Tech Lead can decide whether atlas ↔ plumb is a
  direct function call or a thin IPC layer (**open question #1** in
  the PRD).

## Data Architecture

### Data models (owned by plumb, referenced by atlas)

Atlas does not own a schema. It writes into plumb's four tables:

- `runs(id, task, status, start_ts, end_ts, dollar_cost, ...)`
- `spans(id, run_id, parent_id, kind, name, input_hash, start_ts, end_ts, ...)`
- `scores(id, span_id | run_id, scorer, metric, value_label, value_numeric, ...)`
- `examples(id, origin_run_id, origin_span_id, input, expected_output, ...)`

Full schema lives in plumb's repo.

### Atlas-owned on-disk state

| File                             | Purpose                               | Owner      | Lifecycle                 |
| -------------------------------- | ------------------------------------- | ---------- | ------------------------- |
| `.atlas.toml`                    | Per-project config                    | User       | Manually authored         |
| `~/.atlas/config.toml`           | User-default config                   | User       | Manually authored         |
| `.atlas/current-run`             | Active `run_id` pointer               | Atlas CLI  | Created on `atlas run`; removed on run close |
| `dev/active/<slug>/tasks.md`     | Canonical pipeline state              | Atlas CLI + user edits allowed | Created on `atlas run`; moved to `dev/archive/` on phase complete |
| `dev/active/<slug>/context.md`   | Session context notes                 | Agent (via `/dev-docs-update`) | Free-form |
| `.git/hooks/post-commit`         | Score-writer hook                     | Atlas CLI (via `hook install`) | Idempotent install/uninstall |

### Data flow (one run)

```
atlas run "X"
  └─▶ insert runs row                 (plumb)
      create dev/active/X/tasks.md    (atlas.state)
      write .atlas/current-run        (atlas.state)
      ├─▶ stage 0: research
      │   └─▶ open span, prompt user, close span
      │       └─▶ gate 0: prompt approve/reject → scores row
      ├─▶ stage 1: prd_draft        (consult-experts PM persona)
      │   └─▶ … same shape …
      ├─▶ … stages 2–4 …
      ├─▶ stage 5: code_gen
      │   └─▶ git worktree add …
      │       invoke code-gen agent inside worktree
      │       (commits inside worktree trigger post-commit hook)
      │         └─▶ post-commit: verify_pass + code_review_finding scores
      │             on gate-4 commit: gate_commit score + advance state
      ├─▶ stage 6: code_review
      │   └─▶ /code-review + /verify
      │       └─▶ gate 5: prompt approve/reject → gate_phase_complete
      └─▶ close runs row (status = success | failure)
```

### Storage strategy

- **Atlas:** flat files only (`*.toml`, `*.md`, `.atlas/current-run`).
  No atlas-owned database.
- **plumb:** single SQLite file at `~/.plumb/plumb.db` (default,
  overridable via TOML).

## API Design

**v1 exposes no network API.** Atlas is a CLI with three commands
(`run`, `status`, `hook install|uninstall`), invoked locally. An HTTP
shell is a v1.1 concern.

Internal "APIs" worth naming:

- **CLI ↔ user.** Each gate is a one-line prompt. Approve/reject + a
  free-form reason line captured as `scores.reason_text` (optional).
- **Atlas ↔ plumb.** A thin wrapper module in atlas
  (`atlas.plumb_io`) calls plumb's Python surface. Whether this is
  direct in-process calls vs. an IPC boundary is PRD open question
  #1.
- **Atlas ↔ plugins.** Atlas invokes plugin slash-commands as black
  boxes. How atlas knows the plugin command finished (exit code,
  output marker, polling) is PRD open question #2.

## Technology Stack

| Layer                  | Choice                        | Rationale                                                                     |
| ---------------------- | ----------------------------- | ----------------------------------------------------------------------------- |
| Language               | Python 3.11+                  | Matches plumb + the rest of the author's backend work; stdlib `tomllib` ships with 3.11. |
| CLI library            | `click` or `typer` (TBD)      | Tech Lead pick. Both are fine; `typer` if the type-hint ergonomics pay off; `click` if stability over novelty. |
| Config                 | `tomllib` (stdlib)            | No runtime dep.                                                               |
| Measurement            | plumb (local path install)    | Required dependency; out of scope to fork.                                    |
| Version control        | git 2.5+                      | Needed for `git worktree`; post-commit hook is standard.                     |
| Persistence (atlas)    | Flat files only               | See "Storage strategy" above.                                                 |
| Testing                | `pytest`                      | Already in the scaffolding.                                                   |

No databases, no ORMs, no web framework. If atlas grows any of these
in v1, it has drifted from scope.

## Scalability & Performance

Not a v1 concern — single user, single machine, one concurrent run
per repo. Per-stage latency is recorded in `spans.start_ts`/`end_ts`
as data, not as a constraint.

Performance ceilings that *do* matter:

- `atlas status` must return in < 500ms on cold cache (it reads one
  markdown file).
- Post-commit hook must complete in < 1s (otherwise the user's commit
  flow feels broken).

## Security Architecture

Restated from PRD §6.4 for completeness:

- **Local-only.** No network listener, no port, no inbound auth.
- **Secrets via env.** LLM API keys come from the user's shell env;
  atlas does not read, persist, or log them.
- **Hook scope.** `atlas hook install` writes only to
  `.git/hooks/post-commit` in the current repo. Never a global hook.
- **Worktree data.** Code generated in the Stage 5 worktree stays in
  the worktree until the user merges. Atlas never pushes, publishes,
  or copies that content.
- **plumb DB path.** Defaults to `~/.plumb/plumb.db` — the user's
  home directory. Configurable via TOML if users want to relocate it
  (e.g. for an encrypted volume).

## Deployment Architecture

- **Dev loop.** `uv sync` → `uv run pytest` → `uv run atlas run …`
  against a sacrificial Flask repo.
- **"Production."** There is no production for v1. The tool runs on
  the author's laptop.
- **CI.** GitHub Actions running `pytest`, `ruff check`, and the
  routing-ground-truth fixture test. No deployment step.
- **Release.** No release mechanism in v1 — the repo *is* the
  artifact. A tagged `v1.0` when Week 4 ships.

## Trade-offs & Alternatives

1. **State machine vs. agent framework.**
   *Chosen:* hand-rolled state machine in ~300 lines of Python.
   *Rejected:* LangGraph, CrewAI, any multi-agent framework. For a
   deterministic 7-stage pipeline, a framework imports complexity
   the pipeline doesn't use.
2. **plumb via direct Python calls vs. subprocess.**
   *Tentative:* direct calls (simpler, faster, shared process).
   *Open question #1* in the PRD — Tech Lead decides.
3. **Score writing via post-commit hook vs. direct plugin edits.**
   *Chosen:* post-commit hook parses plugin stdout.
   *Rejected:* modifying `DEV-ESSENTIALS` to write scores directly.
   Keeps the plugin unchanged; accepts that parsing is brittle and
   will break if the plugin's output format changes.
4. **Resume protocol via CLAUDE.md instruction vs. `/dev-resume`
   slash command.**
   *Chosen for v1:* instruction paragraph.
   *Deferred to v2:* slash command. The cost of drifting once is
   low; the cost of building the wrong slash command twice is
   higher.
5. **Stage 5 worktree boundary vs. branch + reset.**
   *Chosen:* `git worktree`.
   *Rejected:* feature branch on the main working tree. Worktree
   gives a physical directory boundary the code-gen agent cannot
   accidentally escape; feature branches do not.

## Risks & Mitigation

See [`../1_product_and_research/PRD.md`](../1_product_and_research/PRD.md)
"Risks and Mitigation."

## Future Considerations

- **v1.1 — HTTP shell.** A thin FastAPI or Flask layer around the
  CLI so a mobile shortcut can trigger `atlas run`. Adds
  authentication, request validation, and a small queue; none of
  that is in v1.
- **v1.2 — Bounded auto-retry in the worktree.** Stage 5 retries
  `/verify` failures automatically with a hard iteration cap. This
  is where paired `examples` rows (failed span → passing span) start
  appearing at zero marginal authoring cost.
- **v2 — Multiple run kinds.** If atlas picks up non-dev-workflow
  tasks (content-pipeline runs, data-migration runs), `runs.kind`
  becomes meaningful. PRD open question #4.
- **Upstream contribution path.** If a reference repo ends up
  implementing the phase-gated-pipeline-with-state-file pattern
  first, atlas should fork-and-trim rather than ship a third
  implementation.
