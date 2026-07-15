---
title: atlas — CLI backend dispatch (multi-CLI StageRunner) spec + layer-ownership decision
status: design-note
created: 2026-06-28
last_reviewed: 2026-06-28
tags: [atlas, claude-code, antigravity, gemini-cli, headless, stage-runner, architecture-decision]
---

# CLI backend dispatch — where headless `claude -p` / `agy -p` lives

Short architecture note pinning **which project owns headless-CLI subprocess dispatch**,
and pointing at the flag/auth reference that doubles as the implementation spec.

## Decision

**atlas owns headless-CLI subprocess dispatch. content-pipeline stays API-only.**

| Concern | Owner | Mechanism |
| --- | --- | --- |
| Triggering an **agent** in a worktree via a headless CLI (`claude -p`, `agy -p`) | **atlas** | `StageRunner` Protocol → `SubprocessStageRunner` (`src/atlas/orchestrator.py`) |
| Making a **single structured LLM completion** | **content-pipeline** | `LLMClientPort.complete()` → `AnthropicClient` / `OpenRouterClient` (SDK, *not* subprocess) |

**Dependency direction:** `content-pipeline → atlas → CLI`. content-pipeline must **never**
shell out to a coding-agent CLI directly. If a content-pipeline stage needs agentic,
multi-turn, worktree-isolated execution (e.g. promoting the Brown-lite PRD writer from a
single completion to a real build), it **consumes atlas as a library/workflow**
(`atlas.run_workflow(...)`) and atlas owns the subprocess.

## Why atlas, not content-pipeline

The two projects operate at different altitudes, and the existing code already draws the
line:

- **atlas already *is* the subprocess dispatcher.** `SubprocessStageRunner`
  (`orchestrator.py:461`) already builds
  `["claude", "-p", prompt, "--no-session-persistence", "--model", m, "--add-dir", ...]`,
  handles timeouts, and returns a `StageOutcome`. It already accepts `command_overrides`
  and `model`. Adding `agy` is a *natural extension of a layer that already exists here*,
  not a new concern. The `StageRunner` Protocol (`orchestrator.py:91`) is the stable seam;
  `Pipeline`, gates, `WorktreeManager`, and plumb instrumentation all wrap it.
- **content-pipeline deliberately wants the opposite.** Its `LLMClientPort.complete()`
  wants one synchronous, structured (Pydantic-validated) completion with HTTP retry
  semantics. Driving that through a CLI subprocess is an impedance mismatch (serialize →
  spawn → parse JSON → re-validate), adds process-spawn latency to a batch workload, loses
  granular `429/5xx` retry, and — for a distributable artifact — runs into Anthropic's
  third-party-auth constraint. Full rationale in the reference doc linked below.

The principle: **subprocess-to-an-agentic-CLI is an *agent-execution* concern (atlas);
structured-completion-via-API is an *LLM-call* concern (content-pipeline).**

## Implementation shape (atlas-side)

Generalize the concrete runner — do **not** add a new layer. Make the backend CLI a
strategy on `SubprocessStageRunner`, mirroring how content-pipeline made its LLM backend a
setting:

```python
class SubprocessStageRunner:
    def __init__(self, *, backend: CliBackend = ClaudeCodeBackend(), model="haiku", ...): ...

class ClaudeCodeBackend:   # ["claude","-p",prompt,"--no-session-persistence","--model",m,"--add-dir",...]
class AntigravityBackend:  # ["agy","-p",prompt,"--output-format","json","--include-directories",...]
```

Each `CliBackend` owns the per-CLI argv + parsing differences (workspace flag, system-prompt
override, session persistence, output-format/exit-code parsing, auth). The `StageRunner`
Protocol and `StageOutcome` contract are unchanged, so `Pipeline`/gates/worktrees/plumb stay
untouched. This is the **workflow-registry / router** evolution from the atlas note: a
workflow can declare which CLI backend it runs on, and a router (rule/metadata/LLM, later
plumb-score-informed) can pick it per ticket.

Rough scope: **v0.4.0**.

### Per-CLI deltas the backends must encode

(From the reference doc — these are the concrete differences each `CliBackend` implements.)

| Dimension | `ClaudeCodeBackend` | `AntigravityBackend` |
| --- | --- | --- |
| Command | `claude` | `agy` (was `gemini`; Gemini CLI retired 2026-06-18) |
| Workspace dir flag | `--add-dir` | `--include-directories` |
| Session control | `--no-session-persistence`, `--resume/--continue` | not documented in headless ref |
| System prompt | `--system-prompt` / `--append-system-prompt` | `GEMINI_SYSTEM_MD` (full replace) |
| Output / result | `--output-format json` → `result` / `structured_output` | `--output-format json` → `response` / `stats` |
| Failure signal | returncode + `system/api_retry` events | exit codes (`0/1/42/53`) |
| CI determinism | `--bare` | sandbox via `-s`; no bare-equivalent |
| Auth (headless) | `ANTHROPIC_API_KEY` (clean) | **browser OAuth by default**; API-key headless contested (issue #78) |
| Free-tier batch fit | API billed per token | ~20 req/day free + weekly cap |

## Source of truth

The full headless feature/flag/auth/quota reference for **both CLIs** — which doubles as the
`CliBackend` implementation spec — lives under content-pipeline (where the research
originated):

→ [`../content-pipeline/headless-clis-reference.md`](../content-pipeline/headless-clis-reference.md)

That doc's **Part B** (Claude Code) and **Part C** (Antigravity) are the per-backend
contracts; **Part D** is the comparison table reproduced in condensed form above.

## Cross-references

- atlas overview: [`README.md`](./README.md)
- Workflow/registry evolution context: [`dev-workflow-automation-plan.md`](./dev-workflow-automation-plan.md)
- content-pipeline LLM-backend decision (why it stays API-only): [`../content-pipeline/headless-clis-reference.md`](../content-pipeline/headless-clis-reference.md) (Part A)
- atlas runner seam: `src/atlas/orchestrator.py` (`StageRunner` Protocol, `SubprocessStageRunner`)
