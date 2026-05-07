# Core Concepts

## The 7-stage pipeline

Atlas walks a fixed, deterministic pipeline in order. No dynamic routing
in v1 — stages cannot be skipped or reordered.

| Stage | Name | Tool | Span kind |
|-------|------|------|-----------|
| 0 | research | Claude Code + web search | plan |
| 1 | prd_draft | consult-experts → PM persona | plan |
| 2 | trd_draft | consult-experts → Tech Lead persona | plan |
| 3 | tds_gen | /dev-docs-be | plan |
| 4 | plan_review | plan-reviewer agent | verify |
| 5 | code_gen | Claude Code (inside git worktree) | subagent |
| 6 | code_review | /code-review + /verify | verify |

Each stage opens a plumb span on entry, invokes its tool (or surfaces a
prompt for research), closes the span, then pauses at a gate.

## Six human gates

Every stage transition requires an explicit human approval. These are
hard stops — not suggestions.

| # | Gate label | Span attached | Score metric |
|---|------------|---------------|--------------|
| 0 | Research reviewed | plan:research | gate_research |
| 1 | PRD finalized | plan:prd_draft | gate_prd |
| 2 | SDD + TRD finalized | plan:trd_draft | gate_trd |
| 3 | TDS approved | verify:plan_review | gate_tds |
| 4 | Per-feature commit | subagent:code_gen | gate_commit |
| 5 | Phase complete | run-level | gate_phase_complete |

At each gate, you approve or reject. Both outcomes write a
`scorer='user_signal'` row in plumb. A rejection also creates an
`examples` row — a regression-set entry at zero authoring cost.

## The state file

`dev/active/<task>/tasks.md` is the sole source of pipeline state.
Atlas creates it on `atlas run`; every gate transition updates it.

```markdown
## current
phase: tds_gen
gate: 3
next: <first unchecked item below>

## stage 0 — research
- [x] Research complete

## stage 3 — tds_gen
- [ ] TDS approved
```

`atlas status` prints the `## current` block. A fresh Claude Code
session reads this file to resume from the first unchecked box — no
human re-briefing.

## The git worktree boundary

Stage 5 (`code_gen`) runs inside a `git worktree add` directory. The
code-generation agent cannot touch `main` directly. The generated diff
lives entirely in the worktree branch until you make the explicit merge
at gate 4. A failed run can be abandoned by removing the worktree.

## Plumb integration

Atlas writes all measurement data into
[plumb](https://github.com/anant-gupta-utexas/plumb) via direct
in-process Python calls. It never touches plumb's SQLite file directly.

Per run, atlas writes:

- One `runs` row on start; closed with `status` on run end.
- Seven typed `spans` rows — one per stage, with the span kind from the
  table above.
- Six `scores` rows — one per gate, `scorer='user_signal'`.
- One `examples` row per gate rejection (input = rejected artifact,
  expected = corrected artifact after re-approval).

`plumb run stats` shows atlas run history. `plumb example promote`
turns gate rejections into regression test cases.

## Model routing

`.atlas.toml` maps pipeline roles to model strings:

```toml
[models]
plan_model   = "claude-opus-4-7@https://api.anthropic.com/v1"
code_model   = "claude-sonnet-4-6@https://api.anthropic.com/v1"
review_model = "claude-sonnet-4-6@https://api.anthropic.com/v1"
```

The `<model>@<base_url>` shape means a model swap is a config edit.
Atlas does not make LLM calls directly — it invokes agent plugins that
pick up the model from config.

## Resume after compaction

Atlas is compaction-safe by design. When a Claude Code session ends
mid-run, a project-root `CLAUDE.md` instruction tells the next fresh
session to read `dev/active/*/tasks.md`, find the first unchecked box,
and confirm before resuming. No state lives in the chat window.

## Size target

Atlas targets ≤ ~300 lines of Python — "a state machine, not a
framework." Stages invoke external tools; atlas owns only ordering,
gate prompts, state file writes, span/score writes, and the worktree
boundary. If a feature requires a new file type (router module, agent
registry), it is an automatic design-review trigger.
