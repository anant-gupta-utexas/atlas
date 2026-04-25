# Product Requirements Document (PRD)

**Project:** atlas — v1 local CLI
**Version:** 1.0 (Week 4 local CLI)
**Status:** Draft, pre-implementation
**Last reviewed:** 2026-04-23

This PRD defines the v1 release of **atlas**, a local CLI orchestrator
for a phase-gated agentic dev workflow. Scope is intentionally tight:
local CLI only. Multi-model routing in production, the HTTP shell, and
an autonomy target against downstream repos are explicitly **out of
scope** and will be covered by follow-up PRDs.

Grounds on:

- [`README.md`](../../README.md) — project-level v1 deliverables.
- [`docs/2_architecture/system_design.md`](../2_architecture/system_design.md)
  — 7-stage state machine, span tree shape, worktree boundary.
- [`docs/2_architecture/TRD.md`](../2_architecture/TRD.md) — NFRs and
  open Tech Lead questions.

---

## Overview

Atlas is a local command-line runtime for a **phase-gated agentic dev
workflow**. It walks a fixed 7-stage pipeline from problem statement to
reviewed code, stops at six explicit human gates, runs the labor between
gates (planning, drafting, code generation, review), and writes every
run as a typed span tree into [plumb](https://github.com/anant-gupta-utexas/plumb)
— the measurement spine this project depends on.

v1's goal is not "autonomy." v1's goal is **middle-ground honesty about
attestation vs. labor**: humans keep the pen on anything that needs a
name next to it (research accepted, PRD finalized, TDS approved, commit
accepted, phase complete). The agent does everything in between. Both
sides of that split are measured.

## Problem Statement

Agentic dev workflows today sit at two extremes, neither of which fits
a measured solo-developer build:

- **All-manual.** Every stage (research → PRD → TRD → TDS → code →
  review) runs in a fresh chat session. State lives only in the chat
  window. A `/compact` or a session end silently loses the plan, and
  the next session resumes in subtly the wrong place. Every stage
  transition is human-driven; there is no structured record of what
  happened.
- **Fully-autonomous.** Agents are handed a ticket and expected to open
  a merged PR overnight (the "Discord → agents → PR" pattern). Volume
  is high, but every human gate that actually decides quality — "is
  this the right PRD?", "is this TDS complete?", "did the diff match
  the plan?" — is gone. The result is velocity without acceptance.

Neither mode produces data that answers the question atlas is built to
answer: *how much of agent-driven dev work actually needs intervention,
at what cost, and where do the failures cluster?* The first mode
produces no structured data at all; the second produces only outcome
data (merged or not), losing the intermediate signal that explains *why*.

Atlas v1 is the middle-ground runtime that fills this gap. A local CLI
that walks a fixed 7-stage pipeline, stops at six explicit human gates,
runs the labor between gates, and writes every run as a span tree into
plumb with a row per gate decision.

## Target Users

### Primary user

**Single operator: the author.** v1 is an internal tool for a single
machine. All design trade-offs favor the solo-user case: no auth, no
multi-tenancy, no secrets management beyond "whatever your shell env
already has", no concurrent-run handling (one `atlas run` at a time per
repo).

This is called out explicitly because the most common v1-PRD drift is
adding "and a login page" / "and a team dashboard". Neither is in scope.

### Secondary stakeholder

**Public reader** — engineers at DevEx, AI/ML, and agentic-systems teams
who encounter the repo and want to understand the workflow. They do not
run `atlas run`; they read the repo, the `runs` it produces, and the
derived plumb metrics.

**Implication for v1:** the CLI surface, the TOML config shape, and the
README must be publishable-quality even though runtime usage is
single-user.

## User Stories

### Happy path — single end-to-end run

> **Note:** v1's first real customer is a throwaway feature — e.g. "add
> a response-cache middleware to a Flask starter repo." Any boring
> Python feature works; the point is to exercise the pipeline, not the
> feature.

1. User runs `atlas run "add response-cache middleware to this Flask repo"`.
2. Atlas inserts a `runs` row, creates
   `dev/active/cache-middleware/tasks.md`, opens Stage 0 (research).
3. Research completes; atlas writes the `plan:research` span and pauses
   for gate 0. User approves → `gate_research` score written, advance.
4. Stages 1–3 run the same pattern: plan spans, user approval at each
   gate, `gate_prd` / `gate_trd` / `gate_tds` scores written.
5. At gate 3 approval, atlas opens a git worktree for Stage 5 and hands
   off to the code-gen agent with the TDS in context.
6. Stage 5 produces code. The post-commit hook fires on each commit
   inside the worktree and writes deterministic `verify_pass` /
   `code_review_finding` scores.
7. User inspects the final diff; on acceptance, commits to merge the
   worktree branch → post-commit hook writes `gate_commit` score.
8. Atlas runs Stage 6 (code review + verify), writes the
   `verify:code_review` span, waits at gate 5.
9. User approves → `gate_phase_complete` score written,
   `runs.status='success'`, run closed.

End state: one complete span tree, all six gates scored, zero
main-branch contamination.

### Gate rejection path

1. At gate 3 the TDS is wrong. User rejects.
2. Atlas writes `scores(metric='gate_tds', value_label='rejected',
   value_numeric=<turn count>)`.
3. Atlas inserts an `examples` row with `origin_run_id`,
   `origin_span_id=<tds_gen span>`, capturing the rejected artifact as
   the "input" half of a future paired example.
4. User re-drives Stage 3 with correction context; the corrected TDS
   becomes a new `plan:tds_gen` span attached to the same run.
5. On second approval, the `examples` row is updated with the corrected
   output as the "expected" half.

This gives plumb a regression-set row per rejection at zero marginal
authoring cost — the reason gate rejections are the primary data source
for the offline↔online loop.

### Resume after compaction

1. Mid-run, the agent session ends (compaction, crash, user walks away
   for the day).
2. Next session: user reopens the agent on the same repo.
3. A project-root `CLAUDE.md` instruction paragraph tells the agent:
   *"before any action, read `dev/active/*/tasks.md`, find the first
   unchecked box, surface the context, ask to confirm."*
4. Agent reads `tasks.md`, sees
   `## current: phase=tds_gen, gate=3, next=…`, surfaces the relevant
   `context.md` lines, asks user to confirm.
5. User confirms → resume from the first unchecked box. No
   re-briefing; all context lives in the file.

## Functional Requirements

### Must Have (MVP)

- **CLI entry point.** Command `atlas` with subcommands:
  - `atlas run "<task description>"` — starts a new run.
  - `atlas hook install` — installs the post-commit git hook in the
    current repo.
  - `atlas status` — prints the `## current` block from the active
    `tasks.md`.
  - Invocation works from any Python project root; atlas discovers its
    config via `.atlas.toml` in the repo, or walks up to
    `~/.atlas/config.toml`.
- **Seven-stage state machine (fixed, in order):**

  | Stage | Name          | Span kind  |
  | ----- | ------------- | ---------- |
  | 0     | research      | `plan`     |
  | 1     | prd_draft     | `plan`     |
  | 2     | trd_draft     | `plan`     |
  | 3     | tds_gen       | `plan`     |
  | 4     | plan_review   | `verify`   |
  | 5     | code_gen      | `subagent` |
  | 6     | code_review   | `verify`   |

  Ordering is deterministic in v1 — no dynamic routing. The
  orchestrator walks stages in order, invokes the right tool at each
  stage, waits at the gate, and proceeds on approval.
- **Six human gates.** Each is a hard stop and writes exactly one
  `scores` row. All six write `scorer='user_signal'`,
  `value_label ∈ {approved, rejected}`, and capture turn count as
  `value_numeric`.

  | #   | Gate label          | Attached span        | Score metric          |
  | --- | ------------------- | -------------------- | --------------------- |
  | 0   | Research reviewed   | `plan:research`      | `gate_research`       |
  | 1   | PRD finalized       | `plan:prd_draft`     | `gate_prd`            |
  | 2   | SDD + TRD finalized | `plan:trd_draft`     | `gate_trd`            |
  | 3   | TDS approved        | `verify:plan_review` | `gate_tds`            |
  | 4   | Per-feature commit  | `subagent:code_gen`  | `gate_commit`         |
  | 5   | Phase complete      | run-level (no span)  | `gate_phase_complete` |

- **Stage 5 worktree boundary.** Stage 5 (`code_gen`) runs inside a
  `git worktree add` boundary. The agent cannot modify `main` directly;
  the entire code_gen span's diff is a single artifact (the worktree
  branch vs. main); failed runs are abandonable by worktree removal.
- **Canonical state file.** Every active task has a
  `dev/active/<task-name>/tasks.md` with a `## current` block (phase +
  gate + next) and per-stage checkboxes. This file is the sole source
  of truth about pipeline state.
- **Post-commit hook.** `atlas hook install` drops a post-commit hook
  that: reads the commit SHA, identifies the active `run_id` from
  `.atlas/current-run`, parses `/verify` and `/code-review` stdout
  captured by the stage 6 span, and writes `verify_pass` /
  `code_review_finding` scores. If the commit corresponds to gate 4, it
  also writes the `gate_commit` user-signal score and advances the
  state machine.
- **Model-routing TOML (shape only).** `.atlas.toml` per project merged
  over `~/.atlas/config.toml`:

  ```toml
  [models]
  plan_model   = "claude-opus-4-7@https://api.anthropic.com/v1"
  code_model   = "claude-sonnet-4-6@https://api.anthropic.com/v1"
  review_model = "claude-sonnet-4-6@https://api.anthropic.com/v1"

  [pipeline]
  worktree_stage = 5
  state_file     = "dev/active/{task}/tasks.md"

  [plumb]
  db_path = "~/.plumb/plumb.db"
  ```

  v1 exercises only one model config in practice; the
  `<model>@<base_url>` string shape is there so the later model-swap
  experiment is a config edit, not a code change.
- **Routing ground-truth fixture.** A 7-row table committed as
  `tests/fixtures/routing_ground_truth.json`. Every run validates
  against this table: dispatching to a tool not matching the expected
  value is a routing failure (deterministic in v1; becomes a real
  measurement when different orchestrator models are swapped in).

### Should Have

- `atlas hook uninstall` — symmetric removal of the post-commit hook.
- Friendly error surface when `.atlas.toml` is malformed (don't leak a
  stack trace into the user's gate prompt).
- A minimal `atlas --help` that names the six gates explicitly so the
  CLI is self-describing.

### Could Have (deferred to v1.1+)

- `/dev-resume` slash command — for v1, the resume protocol is a
  `CLAUDE.md` instruction paragraph. Slash command lands when drift is
  felt twice.
- Bounded auto-retry loop on `/verify` failures — manual re-run only in
  v1.
- `atlas log <run_id>` — span-tree dump. Can be done via plumb queries
  for v1.

## Technical Requirements

### Performance

No hard latency SLA. Gate-to-gate time is human-bounded (measured in
minutes, not milliseconds). Per-stage latency is recorded via span
`start_ts` / `end_ts` — useful as data, not a requirement.

### Security

- **Local-only.** No network listener. Atlas never opens a port in v1.
- **No secrets in atlas.** API keys for LLM providers are read from
  env; atlas does not persist them.
- **Private data in `dev/active/`.** The working surface may contain
  sensitive context. This is per-repo and respects each repo's
  gitignore; atlas does not copy content outside the repo (except
  plumb's SQLite DB at `~/.plumb/plumb.db`, which is the user's own
  data store).
- **Hook scope.** `atlas hook install` writes only to
  `.git/hooks/post-commit` in the current repo. No global hooks, no
  user-wide changes.

### Scalability

Not applicable to v1 (single user, single machine, one run at a time
per repo). Concurrency handling is a Phase 2 concern once a hosted HTTP
layer is in the mix.

### Compliance

Not applicable.

### Integration

- **plumb** (required): atlas writes `runs`, `spans`, `scores`,
  `examples` rows via plumb's decorator + context-manager API. Atlas
  does not touch the SQLite file directly; it goes through the plumb
  Python surface.
- **Existing agent plugins** (`DEV-ESSENTIALS`, `DEV-BE-PYTHON`): atlas
  invokes them as black boxes — slash commands and agents called by
  name. No modifications to the plugins in v1 (score writing is via a
  post-hook that parses plugin stdout, not direct writes from the
  plugin).
- **git** (required): atlas requires a git repo as the working
  surface. `git worktree`, `git log`, and post-commit hook are the only
  git touchpoints.

### Reliability

- Every stage emits a span on entry, regardless of outcome.
- Crashed / killed runs leave `runs.status='failure'` with a truncated
  but well-formed span tree; plumb queries must not break on partial
  runs.
- The post-commit hook is idempotent — repeated commits for the same
  gate overwrite the prior score rather than appending duplicates.

### Footprint

Target ≤ ~300 lines of Python — "a state machine, not a framework."
Anything larger is a signal that the pipeline is doing too much; stages
should invoke external tools, not reimplement them.

## Success Metrics

Measured against the Week 4 real run.

### Primary — correctness of the data shape

- **End-to-end run completeness.** ≥ 1 `runs` row closed with
  `status='success'` whose span tree contains exactly: one
  `plan:research`, one `plan:prd_draft`, one `plan:trd_draft`, one
  `plan:tds_gen`, one `verify:plan_review`, one `subagent:code_gen`
  (with ≥ 1 tool child span), one `verify:code_review`.
- **Gate score completeness.** 6 / 6 gates produce a
  `scorer='user_signal'` row linked to the correct span for that run.

### Secondary — routing and isolation

- **Routing top-1 accuracy on the fixture: 100%** (deterministic in
  v1; this is a sanity baseline — the real measurement happens when
  orchestrator model choice varies).
- **Main-branch isolation: 100%.** Zero commits appear on `main` from
  Stage 5 outside the worktree merge the user explicitly makes.

### Tertiary — data usefulness

- **≥ 1 `examples` row written** from a gate rejection during the v1
  real run (even a contrived rejection counts — the goal is to
  exercise the path).
- **`atlas status` parity with `tasks.md`.** The `## current` block
  printed by `atlas status` matches the `## current` block in
  `tasks.md` byte-for-byte.

### Surface for tracking

No atlas-specific dashboard in v1. All metrics are exposed via plumb's
existing query paths. "atlas v1 is working" means "plumb has the right
rows in it after an atlas run."

## Timelines / Milestones

### v1 delivery (Week 4, ~5 hrs total)

| Day | Milestone                                                                         | Acceptance signal                                              |
| --- | --------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| 1   | CLI skeleton + 7-stage state machine (no plumb integration, no real tools)        | `atlas run "<task>"` walks stages and pauses at each gate stub |
| 2   | plumb integration: span emission per stage, `scores` writes per gate              | First dry run emits a complete tree in `~/.plumb/plumb.db`     |
| 3   | Worktree boundary for Stage 5 + `atlas hook install` + post-commit score parser   | Commit inside worktree writes `verify_pass` + `gate_commit`    |
| 4   | Model-routing TOML config + routing ground-truth fixture + `atlas status`         | Fixture test passes; `atlas status` prints `## current` block  |
| 5   | End-to-end run on the first real target (Flask cache middleware)                  | One run, 7 spans, 6 gate scores, worktree merge clean on main  |

### Future releases (deferred, listed so they're explicit)

| Release | Scope                                                                         |
| ------- | ----------------------------------------------------------------------------- |
| v1.1    | Multi-model routing in practice; HTTP shell + mobile trigger                  |
| v1.2    | Orchestrator drives real project tickets; bounded auto-retry on `/verify`     |
| v1.3    | Orchestrator extends a second downstream pipeline autonomously                |
| v2      | Replace `tasks.md` CLAUDE.md resume protocol with `/dev-resume` slash command |

Each of these gets its own PRD when the time lands; none gate v1.

## Dependencies

- **Runtime:** Python 3.11+, SQLite (bundled with Python), git 2.5+
  (for worktrees).
- **Packages:** `plumb` (local path install from the sibling repo
  during v1), `tomllib` (stdlib), `click` or `typer` for the CLI.
- **External tools invoked by name:** `DEV-ESSENTIALS` and
  `DEV-BE-PYTHON` plugins installed in the user's agent environment.

## Risks and Mitigation

| Risk                                                                                                           | Impact | Probability | Mitigation                                                                                             |
| -------------------------------------------------------------------------------------------------------------- | ------ | ----------- | ------------------------------------------------------------------------------------------------------ |
| Scope creep into a "framework" (multi-agent, dynamic routing, UI)                                              | High   | High        | Hard ≤~300 LoC target; any new file type (router module, agent-registry, UI) is an automatic design-review trigger. |
| Plugin stdout format changes and the post-commit parser breaks                                                 | Med    | Med         | Treat parsing as best-effort; on parse failure, log and continue (don't block the run). Revisit once schema stabilizes. |
| plumb API churn during v1 week                                                                                 | Med    | Med         | Pin plumb to a path install during Week 4; lift to a versioned release once the schema settles.       |
| Worktree boundary misses an edge case and Stage 5 touches `main`                                               | High   | Low         | E2E test (day 5) explicitly asserts `git log main` is unchanged between run start and gate 4.         |
| Resume-from-compaction protocol fails because `tasks.md` is missing or malformed                               | Med    | Low         | `atlas run` creates `tasks.md` before doing anything else; `atlas status` fails loudly if absent.     |
| Gate prompts fatigue the solo user and intervention quality drops                                              | Med    | Med         | Not a v1 fix; explicitly accepted. Track as a signal in `scores.value_numeric` (turn count per gate). |

---

## Assumptions (inline)

> **v1 first target.** The v1 real run uses a throwaway Flask caching
> middleware feature. If a different throwaway feature is chosen, only
> the happy-path user story and the Day 5 milestone change — one line
> each.

> **Hook ergonomics.** `atlas hook install` writes to
> `.git/hooks/post-commit`, is idempotent, and is removable via a
> sibling `atlas hook uninstall`. This is the cheapest install shape
> that matches the rest of the ecosystem.

> **Single concurrent run per repo.** If two `atlas run` invocations
> overlap on the same repo, behavior is undefined. Concurrency
> handling is a Phase 2 concern.

## Open questions (for the Tech Lead / System Design pass)

Surface naturally from the PRD but belong in the TRD / SDD:

1. What exactly serializes across the atlas ↔ plumb boundary — direct
   function calls, or a thin IPC layer?
2. How does atlas discover that a plugin command has finished (exit
   code? output marker? polling)?
3. Where does `.atlas/current-run` live, and what happens if it's out
   of sync with `tasks.md`?
4. Do we need a `runs.kind` discriminator for "dev-workflow run" vs
   future run types, or is v1 implicitly single-kind?

Flagged, not answered. The TRD pass picks these up.
