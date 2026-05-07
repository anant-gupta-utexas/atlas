# atlas

> Phase-gated agent orchestrator for a structured dev workflow.

[![Tests](https://img.shields.io/badge/tests-82%20passing-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

atlas walks a fixed 7-stage dev pipeline — research → PRD → SDD+TRD → TDS
→ plan review → code → review — stops at six explicit human gates, and writes
every run as a typed span tree into [plumb](https://github.com/anant-gupta-utexas/plumb)
for later analysis.

The design premise: **humans keep the pen on decisions, the agent does everything
in between, and both sides of that split are measured.**

---

## Why atlas

Agentic dev workflows today sit at two extremes:

- **All-manual.** Every stage runs in a fresh chat session. State lives only
  in the chat window. Session end silently loses the plan; every stage
  transition is human-driven with no structured record.
- **Fully-autonomous.** Agents open PRs overnight. Every human gate that
  actually decides quality — "is this the right PRD?", "did the diff match
  the plan?" — is gone. Velocity without acceptance.

Neither mode produces data that answers: *how much of agent-driven dev work
actually needs intervention, at what cost, and where do failures cluster?*

atlas is the middle-ground runtime. Six hard gates preserve human judgment;
the agent handles all labor between gates. The span tree records both sides
so the data is analyzable after the fact.

---

## What you get

- **One command:** `atlas run "<task>"` walks all 7 stages.
- **Six human gates.** Hard stops at: research reviewed, PRD finalized,
  SDD+TRD finalized, TDS approved, commit accepted, phase complete.
  Each gate writes a `user_signal` score into plumb.
- **git worktree boundary for Stage 5.** Code generation runs in an
  isolated worktree — main is never touched by the agent directly.
- **Post-commit hook.** `atlas hook install` writes deterministic
  `verify_pass` and `gate_commit` scores on every commit.
- **Compaction-safe state file.** `dev/active/<task>/tasks.md` is the
  sole source of pipeline state — chat sessions are ephemeral, this file
  is not.
- **Model-routing config.** `.atlas.toml` per project; model swaps
  are config edits, not code changes.
- **Full plumb integration.** Every run produces a `runs` row, 7 typed
  spans, 6 gate scores, and an `examples` row per gate rejection.
- **82 tests, 91% coverage, CI on every push.**

---

## Quick start

```bash
git clone https://github.com/anant-gupta-utexas/atlas.git
cd atlas
uv venv && source .venv/bin/activate
uv sync

atlas run "add response-cache middleware to this Flask repo"
atlas status
atlas hook install
```

Prerequisites: Python 3.11+, git 2.5+ (worktrees), [plumb](https://github.com/anant-gupta-utexas/plumb) installed.

---

## The 7-stage pipeline

| Stage | Name | Tool | Span kind |
|-------|------|------|-----------|
| 0 | research | Claude Code + web search | plan |
| 1 | prd_draft | consult-experts → PM persona | plan |
| 2 | trd_draft | consult-experts → Tech Lead persona | plan |
| 3 | tds_gen | /dev-docs-be | plan |
| 4 | plan_review | plan-reviewer agent | verify |
| 5 | code_gen | Claude Code (inside git worktree) | subagent |
| 6 | code_review | /code-review + /verify | verify |

Gate rejections write an `examples` row in plumb — a regression-set row per
rejection at zero marginal authoring cost.

---

## Configuration

`.atlas.toml` in your project root (merged over `~/.atlas/config.toml`):

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

---

## State file shape

Every active task has a `dev/active/<task>/tasks.md`:

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

`atlas status` prints the `## current` block. After a session compaction,
a fresh Claude Code session reads this file and resumes from the first
unchecked box — no human re-briefing.

---

## Documentation

| What you need | Where |
|---------------|-------|
| Product requirements + scope | [PRD](docs/1_product_and_research/PRD.md) |
| System design + span tree | [System Design](docs/2_architecture/system_design.md) |
| Technical requirements | [TRD](docs/2_architecture/TRD.md) |
| Dev environment setup | [Getting Started](docs/3_guides/getting_started.md) |
| Testing strategy | [Testing](docs/4_testing/index.md) |
| Repo conventions | [CLAUDE.md](CLAUDE.md) |

---

## Relationship to plumb

atlas writes all measurement data into
[plumb](https://github.com/anant-gupta-utexas/plumb) via its decorator +
context manager surface. atlas does not touch the SQLite file directly.

plumb is the schema + query layer; atlas is one of its consumers. The
`plumb run stats` CLI shows atlas run history; `plumb example promote`
turns gate rejections into regression test cases.

---

## License

MIT — see [LICENSE](LICENSE).
