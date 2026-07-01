# Core Concepts

## Workflows and the YAML engine

Atlas v2 is a YAML-driven multi-workflow engine. Each workflow is a YAML file that defines an ordered sequence of stages, each with a tool, span kind, optional gate, and optional backend. The engine walks the stages in order, opens plumb spans, invokes tools, pauses at gates, and writes measurement data.

The `dev` workflow (the default) encodes the original 7-stage software-development pipeline. Other workflows (`job`, `job_cli`, or custom ones you write) follow the same mechanics.

See [yaml_workflow_engine.md](yaml_workflow_engine.md) for the full schema reference and how to write a custom workflow.

---

## The dev workflow — 7 stages

When you run `atlas run "<task>"` without `--workflow`, atlas loads `dev.yaml`.

| Stage | Name | Tool | Span kind |
|-------|------|------|-----------|
| 0 | `research` | `consult-experts:research` | `plan` |
| 1 | `prd_draft` | `consult-experts:pm` | `plan` |
| 2 | `trd_draft` | `consult-experts:tech-lead` | `plan` |
| 3 | `tds_gen` | `dev-docs-be` | `plan` |
| 4 | `plan_review` | `plan-reviewer` | `verify` |
| 5 | `code_gen` | `code-gen-agent` | `subagent` |
| 6 | `code_review` | `code-review` | `verify` |

Stages run in fixed order. There is no dynamic routing or conditional branching.

---

## Human gates

Most stages end at a gate — a hard stop requiring explicit human approval before the next stage starts.

| # | Gate label | After stage | plumb metric |
|---|------------|-------------|--------------|
| 0 | `gate_research` | `research` | `gate_research` |
| 1 | `gate_prd` | `prd_draft` | `gate_prd` |
| 2 | `gate_trd` | `trd_draft` | `gate_trd` |
| 3 | `gate_tds` | `plan_review` | `gate_tds` |
| 4 | `gate_commit` | `code_gen` | `gate_commit` |
| 5 | `gate_phase_complete` | `code_review` | `gate_phase_complete` |

Stage 3 (`tds_gen`) has no gate — it runs immediately into stage 4 (`plan_review`).

At each gate, approve to continue or reject to halt. Both outcomes write a `scorer='user_signal'` row in plumb. A rejection also creates an `examples` row — a regression-set entry at zero authoring cost.

`gate_commit` (stage 5) is async: the gate score is written by the post-commit hook (installed via `atlas hook install`), not by the orchestrator inline. The hook reads `.atlas/current-run` line 5 for the gate metric name.

---

## The state file

`dev/active/<task>/tasks.md` is the sole source of pipeline state. Atlas creates it on `atlas run`; every gate transition updates it.

```markdown
## current
phase: plan_review
gate: 3
next: gate_tds
workflow: dev

## stage 0 — research
- [x] Research complete

## stage 3 — tds_gen
- [ ] TDS approved
```

The `workflow:` field was added in v2 and names the workflow that started this run. On `atlas resume`, atlas reads this field and re-resolves the workflow YAML to reconstruct the stage table.

`atlas status` prints the `## current` block. A fresh Claude Code session reads this file to resume from the first unchecked box — no human re-briefing.

---

## The git worktree boundary

Stage 5 (`code_gen`) has `isolate: true` in `dev.yaml`. This causes the stage to run inside a `git worktree add` directory. The code-generation agent cannot touch `main` directly. The generated diff lives entirely in the worktree branch until you explicitly merge at gate 4.

A failed run can be abandoned by removing the worktree. The check that the repo is clean happens at worktree creation time (not at YAML load time) to avoid TOCTOU bugs.

---

## Runner types

Each stage's tool string determines which runner executes it:

| Tool-string prefix | Runner | Mechanism |
|---|---|---|
| (no prefix) | `SubprocessStageRunner` | `plugin_resolver.resolve()` → `claude -p /<cmd>` |
| `RAW:` | `SubprocessStageRunner` | Inline prompt → configured backend CLI |
| `LIB:` | `LibraryStageRunner` | In-process Python adapter (content-pipeline) |
| `SHELL:` | `ShellStageRunner` | Direct list-form subprocess (allow-listed commands only) |

The dev workflow uses only plugin slash-commands (no prefix). The `job` workflow uses `LIB:` for in-process content-pipeline stages and `RAW:` for Claude-dispatched drafting stages.

---

## CLI backends

`RAW:` and plugin-command stages dispatch through a `CliBackend` strategy. The backend is selected per-stage via a 4-tier cascade:

1. Per-stage `backend:` field in the workflow YAML.
2. Workflow-level `default_backend:` field.
3. `.atlas.toml [backend] default`.
4. Hard default: `"claude"`.

Currently supported backends: `"claude"` (Claude Code CLI) and `"agy"` (Antigravity/Gemini, experimental). See [cli_backends.md](cli_backends.md) for auth requirements and error types.

---

## Plumb integration

Atlas writes all measurement data into [plumb](https://github.com/anant-gupta-utexas/plumb) via direct in-process Python calls. It never touches the SQLite file directly.

Per run, atlas writes:

- One `runs` row on start; closed with `status` on run end.
- One typed `spans` row per stage, with the stage's `span_kind` from the YAML.
- One `scores` row per gate (`scorer='user_signal'`).
- One `examples` row per gate rejection (input = rejected artifact, expected = corrected artifact after re-approval).

Gate score metric names are namespaced by workflow: `dev` workflow uses bare names (`gate_research`, etc.) for backward compatibility; all other workflows prefix with the workflow name (`job.gate_shortlist`, `my_workflow.gate_done`, etc.).

`plumb run stats` shows atlas run history. `plumb example promote` turns gate rejections into regression test cases.

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

[plumb]
db_path = "~/.plumb/plumb.db"

[backend]
default = "claude"   # project-wide backend default; per-stage backend: fields override this
```

The `<model>@<base_url>` shape means a model swap is a config edit, not a code change. The `[backend] default` key was added in v2.2 (Phase 3).

---

## Resume after compaction

Atlas is compaction-safe by design. When a Claude Code session ends mid-run, the `CLAUDE.md` instruction paragraph tells a fresh session to read `dev/active/*/tasks.md`, find the first unchecked box, and confirm before resuming. No state lives in the chat window.

`atlas resume` reads the `workflow:` field from `tasks.md`, re-resolves the YAML, and reconstructs the `stages` tuple. If the original workflow YAML has been deleted or edited in a breaking way since the run started, `atlas resume` exits with a clear error rather than silently using a different workflow.

---

## Size and scope

Atlas targets approximately 300–600 lines of engine code (orchestrator + loader + backends + state). As of v2.2:

- `workflow_loader.py` — 188 LoC
- `cli_backend.py` — 192 LoC
- `composite_runner.py` — 57 LoC
- `shell_runner.py` — 118 LoC
- `library_runner.py` — 139 LoC
- `orchestrator.py` — 746 LoC (includes `Pipeline`, `SubprocessStageRunner`, gate prompters, and helpers — over the 400/800-line file guidance; flagged for future split)

Atlas does not implement agent logic, LLM scoring, or document generation. Stages invoke external tools; atlas owns only ordering, gate prompts, state file writes, span/score writes, and the worktree boundary.
