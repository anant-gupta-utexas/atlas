# Technical Requirements Document (TRD)

> **Historical record.** This TRD covers the v1 pipeline as it shipped. It is not
> updated for v2 (YAML workflow engine, multi-workflow, CLI backend dispatch) —
> see [`TRD-v2.md`](./TRD-v2.md) for that scope and
> [`../3_guides/yaml_workflow_engine.md`](../3_guides/yaml_workflow_engine.md)
> for the current, living reference. NFRs, integration contracts, and the v1
> success criteria below remain accurate for the `dev` workflow and carry
> forward unchanged into v2.

**Project:** atlas — v1 local CLI
**Scope:** v1 (Week 4 local CLI). Subsequent releases get their own TRDs.
**Status:** v1 approved (Tech Lead pass complete 2026-04-24). Shipped.

## Executive Summary

Atlas is a ≤ ~300-line Python CLI that walks a 7-stage dev-workflow
pipeline end-to-end, pauses at six human gates, and writes every run as
a typed span tree into [plumb](https://github.com/anant-gupta-utexas/plumb).
There is no network surface, no UI, and no database of its own. Stage 5
runs code generation inside a `git worktree` so `main` is never touched
without an explicit user merge.

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

**Measurement protocol.** `atlas status` measured via `time atlas status`
over 10 runs on a warm SSD; P95 must be under target. Post-commit hook
measured via `time git commit` on a trivial diff with the hook
installed. Both targets are spot-checked during the Week 4 real run;
no continuous perf gate in CI for v1.

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
| plumb                | Python API (decorator + ctx manager) — direct in-process calls | path install pinned to commit SHA recorded in `pyproject.toml` | sibling repo  |
| git                  | Subprocess (`git worktree`, `log`, `rev-parse`) | ≥ 2.5           | system        |
| DEV-ESSENTIALS       | Slash commands invoked by name (`/code-review`, `/verify`, etc.) | plugin commit SHA pinned in `pyproject.toml` | external agent plugin |
| DEV-BE-PYTHON        | Slash commands invoked by name (`/dev-docs-be`) | plugin commit SHA pinned in `pyproject.toml` | external agent plugin |
| LLM providers        | Keys via env; calls go through plugins, not atlas directly | — | external       |

**Atlas ↔ plumb boundary.** Direct in-process Python calls (no IPC).
Rationale: same author, no trust boundary to enforce; subprocess adds
serialization overhead and a second failure mode the v1 LoC budget
can't absorb. Revisit at v1.1 when the HTTP shell lands — that layer
*does* want a boundary because web-request lifetimes and plumb writes
have different failure semantics.

**Plugin lifecycle detection.** Exit code is the primary signal. Atlas
invokes plugins via `subprocess.run(..., capture_output=True, check=False)`,
inspects `returncode` for lifecycle, and parses `stdout` only for score
extraction (not for liveness). Each plugin invocation is wrapped in a
timeout; on timeout or non-zero exit the span is closed with
`status='failure'` and the run halts at the current gate.

## Data Requirements

- **No atlas-owned schema.** All structured data goes through plumb.
- **Atlas-owned flat files:** `.atlas.toml`, `~/.atlas/config.toml`,
  `.atlas/current-run`, `dev/active/<slug>/tasks.md`,
  `.git/hooks/post-commit`. Full list in
  [`system_design.md`](./system_design.md) §"Atlas-owned on-disk
  state."
- **State consistency contract.** On every `atlas run` and
  `atlas status` invocation, atlas reads `.atlas/current-run` and the
  `## current` block of the referenced `tasks.md`. If the `run_id` in
  `.atlas/current-run` does not match the `run_id` recorded in
  `tasks.md`'s header, atlas exits non-zero with a recovery hint
  naming both values. No automatic reconciliation; the user resolves
  the mismatch before the run can continue.
- **Retention:** indefinite for plumb's DB; `dev/active/<slug>/` moves
  to `dev/archive/<slug>/` on phase complete and is retained with the
  repo's history.
- **Migration:** none for v1. If plumb's schema changes, the plumb
  project owns its own migrations; atlas revs its pinned plumb version.

## Infrastructure & Environment Requirements

- **Dev:** local laptop. `uv sync` + `uv run pytest`. No hosted infra.
- **CI:** GitHub Actions, manual `workflow_dispatch` only. `pytest`,
  `ruff check`, `mypy src`, and the routing-ground-truth fixture test.
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
  - State consistency test (mismatch between `.atlas/current-run` and
    `tasks.md` header `run_id` causes `atlas run` / `atlas status` to
    exit non-zero with a recovery hint naming both values).
- **Linters:** `ruff check`, `ruff format`, `mypy src`. All three are
  CI gates in v1. Frozen dataclasses already give half the type-coverage
  value for free; retrofitting annotations later is more expensive than
  writing them the first time.
- **Quality gates:** CI must be green before merging to `main`. No
  manual override.

## Deployment & Operations Requirements

- **Deployment:** none. The repo is the artifact.
- **Release:** tagged `v1.0` in git when Week 4 ships and a full
  end-to-end run completes on the real target.
- **Monitoring:** plumb queries over the user's own DB. No Prometheus,
  no Grafana, no hosted telemetry.
- **Logging:** atlas writes a run-scoped log at `.atlas/runs/<run_id>.log`.
  No rotation in v1; logs accumulate until the user cleans them. A
  rotation policy lands when disk usage becomes a real problem (track
  in v1.1 backlog).
- **Alerting:** none. A failed run is visible in the terminal; that is
  sufficient for a solo-user tool.

## Dependencies & Risks

### Dependencies

- **plumb** (required; see Integration Requirements). Path install,
  pinned to a specific commit SHA recorded in `pyproject.toml`.
- **Agent plugins:** DEV-ESSENTIALS and DEV-BE-PYTHON installed in the
  user's agent environment, pinned to specific commit SHAs recorded in
  `pyproject.toml`.
- **System:** git 2.5+, Python 3.11+.
- **Python packages (minimum versions, pinned in `pyproject.toml`):**
  `typer >= 0.12` (CLI; pick over `click` for type-hint ergonomics — if
  this proves wrong during Day 1, swap is one file), `pytest >= 8.0`,
  `mypy >= 1.10`, `ruff >= 0.4`. Major-version bumps require a
  conscious upgrade, not a silent `pip install`.

### Risks

See [`../1_product_and_research/PRD.md`](../1_product_and_research/PRD.md)
§ "Risks and Mitigation."

### Resolved decisions (Tech Lead pass, 2026-04-24)

The PRD flagged four open questions. All four are resolved; recorded
here for traceability.

1. **Atlas ↔ plumb boundary.** Direct in-process Python calls. See
   §"Integration Requirements."
2. **Plugin lifecycle detection.** Exit code primary; stdout for score
   parsing only. See §"Integration Requirements."
3. **`.atlas/current-run` consistency.** Detect mismatch, print
   recovery hint, refuse to continue. See §"Data Requirements" — state
   consistency contract.
4. **`runs.kind` discriminator.** Deferred. v1 writes runs without a
   kind column; if a second run kind appears later, the schema change
   is prioritized in plumb's planning at that time. Cost of adding
   later (a single column + backfill of existing rows to
   `"dev_workflow"`) is acceptable.

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
   orchestrator models vary. **A fixture failure is a release blocker
   even if all other criteria pass**; a routing regression is too easy
   to rationalize as "the pipeline still ran."
5. **Resume protocol works.** A simulated session compaction
   mid-run is followed by a clean resume from the first unchecked
   box in `tasks.md`.

Release blocker criteria: any of the five failing blocks the v1.0 tag.
Non-blocker improvements (ergonomics, error messages, log formatting)
ship in v1.0.1.
