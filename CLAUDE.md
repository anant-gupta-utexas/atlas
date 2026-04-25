# atlas

**Local-CLI agent orchestrator + metrics logger for phase-gated dev workflows.**

Walks a fixed 7-stage pipeline (research → PRD → SDD+TRD → TDS → plan review → code → review), stops at six explicit human gates, and writes every run as a typed span tree into plumb for later analysis.

## Quick Start

```bash
# 1. Set up Python 3.11+ environment
uv venv
source .venv/bin/activate
uv sync

# 2. Install the post-commit git hook (optional, for stage 5+)
atlas hook install

# 3. Run a test task
atlas run "add response-cache middleware to this Flask repo"
```

## Documentation Map

- **[README.md](README.md)** — v1 deliverables, status, quick start
- **[docs/1_product_and_research/PRD.md](docs/1_product_and_research/PRD.md)** — v1 Product Requirements Document (MVP scope, user stories, gates, success metrics)
- **[docs/2_architecture/system_design.md](docs/2_architecture/system_design.md)** — 7-stage state machine, span tree shape, worktree boundary
- **[docs/2_architecture/TRD.md](docs/2_architecture/TRD.md)** — Technical Requirements (NFRs, integrations, data model)
- **[docs/3_guides/getting_started.md](docs/3_guides/getting_started.md)** — Dev environment setup
- **[docs/4_testing/index.md](docs/4_testing/index.md)** — Testing strategy and fixtures

## Project Structure

```
atlas/
├── src/atlas/               # Main package
│   ├── cli.py              # Entry point (@click commands)
│   ├── orchestrator.py      # 7-stage state machine
│   ├── plumb_adapter.py     # plumb integration (span/score writes)
│   ├── worktree_manager.py  # git worktree lifecycle (stage 5)
│   └── post_commit_hook.py  # Score writing from commit output
├── tests/
│   ├── fixtures/            # routing_ground_truth.json, test repos
│   └── unit/                # State machine, span tree, hook parser tests
└── dev/active/              # Feature TDS/plans during implementation
```

## v1 Scope

- **CLI only.** One command: `atlas run "<task description>"`.
- **Seven deterministic stages.** research → prd_draft → trd_draft → tds_gen → plan_review → code_gen → code_review. No dynamic routing in v1.
- **Six human gates.** Hard stops with user signal scoring at each gate.
- **Single-user, single-machine.** No auth, no multi-tenancy, one run per repo at a time.
- **Span tree per run.** Integration with plumb for `runs` / `spans` / `scores` / `examples` rows.
- **Post-commit hook.** Captures `/verify` and `/code-review` output, writes deterministic scores.
- **~300 lines target.** "A state machine, not a framework."

## Key Files

- **`.atlas.toml`** — Per-repo config (model routing, plumb db path, worktree stage).
- **`~/.atlas/config.toml`** — User-wide defaults (merged over `.atlas.toml`).
- **`dev/active/<task-name>/tasks.md`** — Canonical state file (phase, gate, next, per-stage checkboxes).
- **`.git/hooks/post-commit`** — Installed by `atlas hook install`; parses output, writes scores.

## Development Workflow

1. **Before writing code:** Read `[docs/2_architecture/TRD.md](docs/2_architecture/TRD.md)` for integration boundaries and data contracts.
2. **Stages are black boxes.** Each stage invokes an external tool (agent plugin, `/verify` slash command) and parses output. Don't reimplement agent logic inside atlas.
3. **State lives in files.** `dev/active/<task>/tasks.md` is the source of truth. Resume protocol reads it, no in-memory state.
4. **Testing:** Fixture `routing_ground_truth.json` validates the 7-stage table. Mock agent responses in tests; run E2E against a real throwaway feature once per release.
5. **Commitment:** No scope creep past 300 LoC. If a feature is a new file type (router module, agent registry), it fails design review.

## Coding Style

- Functions < 50 lines; files < 400 lines (800 max for complex logic).
- No deep nesting (> 4 levels); use early returns.
- `click` for CLI, `pydantic` for validation, `pathlib` for filesystem operations.
- Sync-only in v1 (no async/await).
- Never mutate `tasks.md` after run close; append to git history instead.

## Assumptions

- Python 3.11+; git 2.5+ (for worktree).
- plumb is installed as a local path dependency during v1 (see `pyproject.toml`).
- Agent plugins (DEV-ESSENTIALS, agent model) are installed in the user's environment; atlas invokes them as black boxes.
- One `atlas run` per repo at a time; no concurrent run handling until v2.