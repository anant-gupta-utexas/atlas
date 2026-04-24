# atlas

**Local-CLI agent orchestrator + metrics logger for a phase-gated dev
workflow.** A state machine that walks a fixed 7-stage dev pipeline
(research → PRD → SDD+TRD → TDS → plan review → code → review), stops at
six explicit human gates, and writes every run as a typed span tree for
later analysis.

> Status: **design → build (v1).** The runtime target is ~300 lines of
> Python. If atlas grows a framework, it has drifted from v1 scope.

## What this is (and isn't)

Atlas is an orchestrator for an agentic dev workflow. v1 is intentionally
tight:

- **CLI only.** `atlas run "<task>"`. No HTTP layer, no UI.
- **Six hard human gates.** Humans decide research-accepted,
  PRD-finalized, TRD-finalized, TDS-approved, commit-accepted,
  phase-complete. Atlas does everything in between.
- **Single-user, single-machine.** No auth, no multi-tenancy, no
  concurrency. One `atlas run` per repo at a time.
- **Deterministic routing.** Stages walk in order; no dynamic dispatch in
  v1. (Routing ground-truth is a 7-row fixture used for later model-swap
  experiments.)
- **Typed span tree per run.** Atlas emits `runs` / `spans` / `scores` /
  `examples` rows so every stage is inspectable after the fact.

What v1 is **not**: an agent framework, an auto-PR bot, or a multi-model
routing layer in practice. The TOML config shape is there, but only one
model set is exercised.

## Quick start

```bash
uv venv
source .venv/bin/activate
uv sync

# Once the CLI lands:
atlas run "add response-cache middleware to this Flask repo"
atlas status
atlas hook install
```

Prerequisites: Python 3.11+, git 2.5+ (for worktrees).

## Documentation map

- **[CLAUDE.md](CLAUDE.md)** — signpost; project structure and coding
  conventions.
- **[docs/1_product_and_research/PRD.md](docs/1_product_and_research/PRD.md)**
  — v1 Product Requirements Document. The authoritative scope doc.
- **[docs/2_architecture/system_design.md](docs/2_architecture/system_design.md)**
  — System design: 7-stage state machine, span tree shape, worktree
  boundary.
- **[docs/2_architecture/TRD.md](docs/2_architecture/TRD.md)** — Technical
  Requirements; lifts NFRs from the PRD and flags open questions for the
  Tech Lead pass.
- **[docs/3_guides/getting_started.md](docs/3_guides/getting_started.md)**
  — Dev environment setup.
- **[docs/4_testing/index.md](docs/4_testing/index.md)** — Testing
  strategy.

## License

MIT — see [LICENSE](LICENSE).
