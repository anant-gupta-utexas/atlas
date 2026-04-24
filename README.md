# atlas

**Local-CLI agent orchestrator + metrics logger for a phase-gated dev
workflow.** A state machine that walks a fixed 7-stage dev pipeline
(research → PRD → SDD+TRD → TDS → plan review → code → review), stops at
six explicit human gates, and writes every run as a span tree into
[plumb](https://github.com/anant-gupta-utexas/plumb) — the measurement
spine for the author's Personal OS build.

> Status: **design → build (v1, Week 4).** The runtime target is ~300
> lines of Python. If atlas grows a framework, it has drifted from v1 scope.

## What this is (and isn't)

Atlas is the orchestrator component of a personal second-brain + agentic
dev workflow. v1 is intentionally tight:

- **CLI only.** `atlas run "<task>"`. No HTTP layer, no UI, no Railway.
- **Six hard human gates.** Humans decide research-accepted,
  PRD-finalized, TRD-finalized, TDS-approved, commit-accepted,
  phase-complete. Atlas does everything in between.
- **Single-user, single-machine.** No auth, no multi-tenancy, no
  concurrency. One `atlas run` per repo at a time.
- **Deterministic routing.** Stages walk in order; no dynamic dispatch in
  v1. (Routing ground-truth is a 7-row fixture used for later model-swap
  experiments.)
- **plumb-native.** Atlas does not own a DB; it writes `runs` / `spans`
  / `scores` / `examples` rows through plumb's Python surface.

What v1 is **not**: an agent framework, an auto-PR bot, or a multi-model
routing layer in practice. The TOML config shape is there, but only one
model set is exercised.

## Quick start

```bash
uv venv
source .venv/bin/activate
uv sync

# Once the CLI lands (Week 4 — see PRD §4.1):
atlas run "add response-cache middleware to this Flask repo"
atlas status
atlas hook install
```

Prerequisites: Python 3.11+, git 2.5+ (for worktrees), and a local clone
of `anant-gupta-utexas/plumb` for the measurement-spine dependency.

## Documentation map

- **[CLAUDE.md](CLAUDE.md)** — signpost; project structure and coding
  conventions (inherited from the Python scaffolding template; will be
  trimmed as atlas's own shape settles).
- **[docs/1_product_and_research/PRD.md](docs/1_product_and_research/PRD.md)**
  — v1 Product Requirements Document. The authoritative scope doc.
- **[docs/2_architecture/system_design.md](docs/2_architecture/system_design.md)**
  — System design stub: 7-stage state machine, span tree shape, worktree
  boundary.
- **[docs/2_architecture/TRD.md](docs/2_architecture/TRD.md)** — Technical
  Requirements stub; lifts NFRs from the PRD and flags open questions
  for the Tech Lead pass.
- **[docs/3_guides/getting_started.md](docs/3_guides/getting_started.md)**
  — Dev environment setup.
- **[docs/4_testing/index.md](docs/4_testing/index.md)** — Testing
  strategy.

## Build plan

Atlas is Phase 1 Week 4 of the author's
[Personal OS plan](https://github.com/anant-gupta-utexas). The full
12-week plan, the cross-project context (plumb, `DEV-ESSENTIALS`,
`DEV-BE-PYTHON`), and the decisions log live in a private second-brain
vault. What lives in this repo are the PRD, the SDD/TRD, the code, and
the measurement hooks.

## Portfolio signal

Agentic systems. For hiring-side readers: what's interesting here is not
"an orchestrator" — there are many — it's the **measurement discipline**:
every run is a typed span tree, every human gate is a scored row, every
rejection is a candidate `examples` row, and the whole thing runs
against the author's own Python backend work so the data is real.

## License

MIT — see [LICENSE](LICENSE).
