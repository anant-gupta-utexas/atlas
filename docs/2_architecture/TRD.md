# Technical Requirements Document (TRD)

**Project:** atlas — v1 local CLI
**Scope:** v1 (Week 4 local CLI). Subsequent releases get their own TRDs.
**Status:** Stub (pending Tech Lead pass). Open questions below are the
primary input to that pass.

## Executive Summary

Atlas is a ≤ ~300-line Python CLI that walks a 7-stage dev-workflow
pipeline end-to-end, pauses at six human gates, and writes every run as
a typed span tree into [plumb](https://github.com/anant-gupta-utexas/plumb).
There is no network surface, no UI, and no database of its own. Stage 5
runs code generation inside a `git worktree` so `main` is never touched
without an explicit user merge.

Non-technical framing: atlas is the "middle-ground" runtime between
all-manual chat sessions and all-autonomous overnight PR bots. The
attestation/labor split is the thesis — humans own every decision, atlas
owns the work in between, both sides are measured.

## Business Context & Objectives

See [`../1_product_and_research/PRD.md`](../1_product_and_research/PRD.md)
§§ "Overview", "Problem Statement", "Success Metrics". Not duplicated
here.

KPIs the v1 build must make measurable:

- End-to-end run completeness (one well-formed span tree per successful
  run).
- Gate score completeness (6 / 6 `scorer='user_signal'` rows per run).
- Routing top-1 accuracy (100% on the 7-row fixture in v1 — sanity
  baseline).
- Main-branch isolation (100% — zero unintended commits on `main`
  outside user-approved worktree merges).

## Functional Requirements

Already specified in the PRD (§ "Functional Requirements") and the SDD
(§§ "System Components", "Data Architecture"). This TRD does not
re-state them; updates belong in the PRD.

The one concrete functional requirement that is *only* in the TRD: the
7-row routing ground-truth table must be committed at
`tests/fixtures/routing_ground_truth.json` and referenced by at least
one test that runs in CI.

## Non-Functional Requirements (NFRs)

### Performance

- `atlas status` cold-cache latency: < 500 ms. (Reads one markdown
  file; anything slower is a bug.)
- Post-commit hook completion: < 1 s. (Longer makes the user's commit
  flow feel broken.)
- No SLA on gate-to-gate time; it is human-bounded. Per-stage latency
  is recorded for later analysis, not enforced.

### Security

Per PRD §6.4:

- No network listener, no inbound port.
- LLM API keys from env; atlas never reads, persists, or logs them.
- Hook install is scoped to `.git/hooks/post-commit` in the current
  repo. No global hooks.
- plumb DB defaults to `~/.plumb/plumb.db`; relocatable via TOML for
  users who want an encrypted volume.

### Reliability & Availability

- Single-user, single-machine: no uptime SLA.
- Crashed / killed runs close with `runs.status='failure'` and a
  truncated but well-formed span tree. plumb queries must tolerate
  partial runs.
- Post-commit hook is idempotent on the same commit SHA.
- `atlas status` and `atlas hook install|uninstall` must be safe to
  invoke at any point in a run lifecycle.

### Usability

- CLI-only surface. No browser, no mobile, no terminal-UI framework.
- Help output (`atlas --help`) names the six gates explicitly so the
  tool is self-describing without reading the README.
- Error messages on malformed `.atlas.toml` surface the offending key
  + line number; no raw tracebacks in the gate prompt path.

## System Constraints & Assumptions

- **Runtime:** Python 3.11+ (`tomllib` stdlib requirement).
- **git:** ≥ 2.5 for worktrees.
- **plumb:** local path install during v1. Promotion to a versioned
  dependency is a v1.1 decision.
- **Plugins:** `DEV-ESSENTIALS` and `DEV-BE-PYTHON` installed in the
  user's agent environment. Atlas does not ship them; it invokes them
  by name.
- **Solo-user assumption.** No auth, no multi-tenancy, no concurrency
  handling. One `atlas run` at a time per repo. Two overlapping runs
  in the same repo are undefined behavior.
- **v1 real-run target.** A throwaway feature (e.g. Flask cache
  middleware). Any boring Python feature works; the pipeline is the
  point.

## Integration Requirements

| Integration          | Surface                              | Version / shape           | Owner         |
| -------------------- | ------------------------------------ | ------------------------- | ------------- |
| plumb                | Python API (decorator + ctx manager) | v1 (path install)         | sibling repo  |
| git                  | Subprocess (`git worktree`, `log`, `rev-parse`) | ≥ 2.5           | system        |
| DEV-ESSENTIALS       | Slash commands invoked by name (`/code-review`, `/verify`, etc.) | Stable names   | external agent plugin |
| DEV-BE-PYTHON        | Slash commands invoked by name (`/dev-docs-be`) | Stable names | external agent plugin |
| LLM providers        | Keys via env; calls go through plugins, not atlas directly | — | external       |

Atlas's code does not import from the plugins. Communication is
subprocess stdout + exit code. (The exact shape of "plugin finished"
detection is **open question #2**.)

## Data Requirements

- **No atlas-owned schema.** All structured data goes through plumb.
- **Atlas-owned flat files:** `.atlas.toml`, `~/.atlas/config.toml`,
  `.atlas/current-run`, `dev/active/<slug>/tasks.md`,
  `.git/hooks/post-commit`. Full list in
  [`system_design.md`](./system_design.md) §"Atlas-owned on-disk
  state."
- **Retention:** indefinite for plumb's DB; `dev/active/<slug>/` moves
  to `dev/archive/<slug>/` on phase complete and is retained with the
  repo's history.
- **Migration:** none for v1. If plumb's schema changes, the plumb
  project owns its own migrations; atlas revs its pinned plumb version.

## Infrastructure & Environment Requirements

- **Dev:** local laptop. `uv sync` + `uv run pytest`. No hosted infra.
- **CI:** GitHub Actions on push + PR. `pytest`, `ruff check`,
  `mypy src` (if adopted), and the routing-ground-truth fixture test.
- **Staging / prod:** none. v1 has no deployed surface.

## Compliance & Regulatory Requirements

None. Personal tool, local-only, no third-party data handled beyond
what the user explicitly types into prompts.

## Quality Assurance Requirements

- **Coverage target:** 80% on `atlas.pipeline` and `atlas.state`.
  Lower acceptable on `atlas.cli` (thin entry point) and
  `atlas.plumb_io` (mostly pass-through).
- **Mandatory tests:**
  - Routing ground-truth fixture test (7 stages → 7 expected tools;
    100% match).
  - Main-branch isolation test (Stage 5 executes; `git log main` is
    unchanged between run start and gate 4).
  - Resume-after-compaction test (simulate session end; a fresh
    process reading `tasks.md` + `.atlas/current-run` finds the
    first unchecked box).
  - Hook idempotency test (two commits on the same SHA do not
    double-write scores).
- **Linters:** `ruff check`, `ruff format`. `mypy src` is a nice-to-have
  for v1; mandatory in v1.1 if atlas grows past ~300 LoC.
- **Quality gates:** CI must be green before merging to `main`. No
  manual override.

## Deployment & Operations Requirements

- **Deployment:** none. The repo is the artifact.
- **Release:** tagged `v1.0` in git when Week 4 ships and a full
  end-to-end run completes on the real target.
- **Monitoring:** plumb queries over the user's own DB. No Prometheus,
  no Grafana, no hosted telemetry.
- **Logging:** atlas writes a run-scoped log at `.atlas/runs/<run_id>.log`.
  Rotation is "delete anything older than 30 days" — a trivial cron on
  the user's machine, not atlas code.
- **Alerting:** none. A failed run is visible in the terminal; that is
  sufficient for a solo-user tool.

## Dependencies & Risks

### Dependencies

- plumb (required; see Integration Requirements).
- DEV-ESSENTIALS, DEV-BE-PYTHON plugin packages installed in the
  user's agent environment.
- git 2.5+, Python 3.11+.

### Risks

See [`../1_product_and_research/PRD.md`](../1_product_and_research/PRD.md)
§ "Risks and Mitigation."

### Open questions (for the Tech Lead pass)

The PRD flags four; re-listed here with TRD framing:

1. **Atlas ↔ plumb boundary.** Direct in-process Python calls, or a
   subprocess + stdout/IPC boundary?
   - *Trade-off:* direct is simpler and faster; subprocess gives a
     harder failure boundary and easier mocking.
   - *Recommendation pending Tech Lead input.*
2. **Plugin "finished" detection.** Exit code, output marker string,
   or poll on a sentinel file?
   - *Trade-off:* exit-code is cleanest but requires plugins to
     exit on completion; output markers are robust but brittle to
     plugin format changes.
   - *Recommendation pending Tech Lead input.*
3. **`.atlas/current-run` consistency.** What's the contract if
   `.atlas/current-run` and `dev/active/*/tasks.md` disagree? (E.g.
   user deletes `tasks.md` by hand while a run is open.)
   - *Default:* `atlas status` / `atlas run` detect the mismatch,
     print a recovery hint, refuse to continue.
4. **`runs.kind` discriminator.** Add the column in v1 (value fixed
   to `"dev_workflow"`), or defer until a second run kind appears?
   - *Trade-off:* adding it early costs nothing and makes the later
     split free; adding it late means a migration.
   - *Recommendation:* add the column in v1 (plumb's schema
     decision, not atlas's), value fixed to `"dev_workflow"`.

## Success Criteria & Acceptance Criteria

v1 ships when all five hold, measured on the Week 4 real run:

1. **Pipeline completeness.** One `runs` row closed with
   `status='success'`; span tree contains exactly the seven expected
   spans in the expected order.
2. **Gate score completeness.** 6 / 6 `scorer='user_signal'` rows,
   each linked to the correct span for the run.
3. **Main-branch isolation.** `git log main` unchanged between run
   start and the explicit user merge at gate 4. Verified by the
   isolation test in CI.
4. **Routing top-1 accuracy.** 100% on the 7-row fixture. The
   deterministic sanity baseline — a real measurement when
   orchestrator models vary.
5. **Resume protocol works.** A simulated session compaction
   mid-run is followed by a clean resume from the first unchecked
   box in `tasks.md`.

Release blocker criteria: any of the five failing blocks the v1.0 tag.
Non-blocker improvements (ergonomics, error messages, log formatting)
ship in v1.0.1.
