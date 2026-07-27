# Job Workflow Guide

Atlas ships two built-in job-search workflows alongside the default `dev` pipeline.

---

## Two variants: `job` vs `job_cli`

| Variant | Command | How it works | Requires |
|---|---|---|---|
| `job` | `atlas run "<task>" --workflow job` | `ingest_postings` and `score_fit` call content-pipeline **in-process** via `LibraryStageRunner` (Mode A). Returns structured results; `gate_shortlist` shows a rendered shortlist report. | `content-pipeline` installed (`uv sync --extra job`) |
| `job_cli` | `atlas run "<task>" --workflow job_cli` | `ingest_postings` and `score_fit` dispatch via `SHELL:` → `ShellStageRunner` directly to the `content-pipeline` CLI. `tailor_materials` and `emit_package` use `RAW:` → `claude -p`. `content-pipeline` CLI must be on `PATH`, but the Python package need not be pip-installed in atlas's environment (Mode B). | `content-pipeline` console script on `PATH` |

Both variants produce a 4-span run (`ingest_postings`, `score_fit`, `tailor_materials`, `emit_package`) with 3 human gates.

### Why two variants instead of automatic fallback

**Explicit > implicit.** When content-pipeline is not installed and you run `--workflow job`, the `LIB:` stage fails with a clear error:

```
content-pipeline is not installed.
  Install it:  uv sync --extra job  OR  pip install -e ../content-pipeline
  Dependency-free alternative: atlas run "<task>" --workflow job_cli
```

There is no silent runtime switch from Mode A to Mode B.  The two workflows are distinct because their measurement data is separate: `job` runs write metrics under `job.gate_*`, while `job_cli` runs write under `job_cli.gate_*`. If atlas silently fell back to Mode B, you would lose the ability to distinguish which execution path produced a given gate score.

### Metric namespacing

Gate scores are namespaced by workflow name:

| Workflow | Gate metrics |
|---|---|
| `job` | `job.gate_shortlist`, `job.gate_materials`, `job.gate_done` |
| `job_cli` | `job_cli.gate_shortlist`, `job_cli.gate_materials`, `job_cli.gate_done` |
| `dev` (default) | `gate_research`, `gate_prd`, … (bare names, for backward compatibility) |

Both `job` and `job_cli` runs are stored in the same plumb DB alongside `dev` runs. Query by `task_id` prefix (`job.<slug>` vs `job_cli.<slug>` vs `dev.<slug>`) to retrieve the correct subset.

---

## Quick start

### Mode A (library) — preferred

```bash
# 1. Install content-pipeline as a sibling
git clone <content-pipeline-repo> ../content-pipeline

# 2. Install the job extra
uv sync --extra job

# 3. Run
atlas run "find senior SWE roles at Series B startups" --workflow job
```

### Mode B (CLI subprocess) — no Python dependency

```bash
# 1. Install content-pipeline (only the CLI binary needs to be on PATH)
pip install content-pipeline        # or use your system's package manager

# 2. Run
atlas run "find senior SWE roles at Series B startups" --workflow job_cli
```

---

## Stage breakdown

Both variants share the same stage structure, gates, and `span_kind`s:

| # | Stage | `span_kind` | Gate | Notes |
|---|---|---|---|---|
| 0 | `ingest_postings` | `tool` | — | Scrapes configured job-board sources. No gate: atlas fails fast on any source error so `score_fit` always scores a complete capture set. |
| 1 | `score_fit` | `verify` | `gate_shortlist` | Scores each posting against your profile. Gate shows a GREEN/YELLOW/RED shortlist report. |
| 2 | `tailor_materials` | `subagent` | `gate_materials` | `RAW:` → `claude -p` drafts CV + cover letter per shortlisted role. `timeout_s: 1800`. |
| 3 | `emit_package` | `tool` | `gate_done` | `RAW:` → `claude -p` assembles the application package into `docs/01_professional/job_applications/<role-slug>/`. |

### `timeout_s` asymmetry

`tailor_materials` sets `timeout_s: 1800` because it's a `RAW:`/subprocess stage that can run long. `ingest_postings` and `score_fit` in `job.yaml` deliberately omit `timeout_s` — their effective timeout comes from content-pipeline's own HTTP/LLM client settings, not from atlas's subprocess mechanism.

In `job_cli.yaml`, `score_fit` also sets `timeout_s: 1800` because it becomes a `SHELL:` subprocess stage dispatching the `content-pipeline score-jobs --pending` command directly. `timeout_s` is honored by `ShellStageRunner` (unlike `LIB:` stages where it is inert).

---

## Hub-and-spoke trigger model

> **Note:** The trigger skill described here is Phase 4 work and does not yet exist. This section documents the *intended model*, not a shipped feature.

```
second-brain
    │
    │  "find roles" intent
    ▼
ai-workx skill (Phase 4 — future)
    │
    │  atlas run "<task>" --workflow job
    ▼
atlas (this project)
    ├── ingest_postings ──► content-pipeline CaptureUseCase
    ├── score_fit       ──► content-pipeline ScoreJobsUseCase ──► LLM
    ├── tailor_materials ──► claude -p (agentic draft)
    └── emit_package    ──► claude -p (file assembly)
                                │
                                ▼
                           plumb DB
                    (spans + scores + examples)
```

The model is **hub-and-spoke**: atlas is the hub that orchestrates the pipeline; content-pipeline and Claude are spokes that do the domain-specific work. Atlas only coordinates — it does not implement scraping, scoring, or drafting logic itself.

### Data flow

1. **Trigger** (Phase 4): an ai-workx slash-skill in the user's second-brain environment invokes `atlas run "<task>" --workflow job` (or `job_cli`).
2. **Ingest** (`ingest_postings`): content-pipeline's `CaptureUseCase` scrapes configured job-board sources and writes postings to the local archive. Atlas enforces strict partial-failure semantics: if any source fails, the stage fails, preventing a downstream gate from approving an incomplete shortlist.
3. **Score** (`score_fit`): content-pipeline's `ScoreJobsUseCase` runs batch LLM scoring against the user's job profile, then atlas renders a report for the human gate.
4. **Gate** (`gate_shortlist`): the user reviews the shortlist and approves or rejects. The decision is written to plumb as a `user_signal` score under `job.gate_shortlist`.
5. **Tailor** (`tailor_materials`): Claude drafts tailored CV + cover letter pairs for each approved role.
6. **Gate** (`gate_materials`): the user reviews the drafted materials.
7. **Emit** (`emit_package`): Claude assembles the final application package to disk.
8. **Gate** (`gate_done`): final approval.

Every span, gate score, and example write lands in the same plumb DB as the user's dev-pipeline runs, enabling cross-workflow analysis once enough data accumulates.

---

## Credential prerequisites

Content-pipeline's sources (`rss`, `generic`, `ats_boards`) require configuration in content-pipeline's own settings file. Atlas does not manage these credentials — they are a one-time user setup step in the content-pipeline environment. See content-pipeline's own documentation for `JOB_PROFILE_PATH`, `ANTHROPIC_API_KEY`, and source configuration.
