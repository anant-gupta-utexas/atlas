# Context — Loop Mode, Phase L1 TRS

Reference notes for anyone picking up this work cold.

## Status — L0 is code-complete; L1 has no blocking dependency

Per `STATUS.md` (2026-07-22): *"Loop Mode Phase L0 ('honest baseline') is code-complete:
271 tests pass, 1 xfail, at 96% coverage."* T-L0.8 (first live attended run) and T-L0.9
(manual delivery smoke test) remain open, but both are **manual, off-CI, real-external-system
checks** — they do not block L1's engineering work, which builds on the *code* L0 shipped
(`ClaudeCodeBackend` telemetry, `Deliverer`), not on those two manual proofs. TRD-v3 §14 lists
L1's dependency as simply "L0," and L0's own TRS (`loop-mode-phase-L0-tasks.md`) confirms
T-L0.1–L0.7/L0.10/L0.11 are done — the engineering surface L1 extends is real and merged.

## Key files

### Source-of-truth docs (read first, in order)

- [`docs/2_architecture/TRD-v3.md`](../../../docs/2_architecture/TRD-v3.md) — the phase
  contract this TRS details. §3.3 (`CodexBackend` contract), §3.4 (`loop_dev.yaml` sketch),
  §14 Phase L1 (engineering scope + exit criteria), §13 item 3 (the exit criterion), Appendix A
  (seam inventory — note its "Unchanged" row for `orchestrator.py`, which this TRS's Resolved
  Decision #3 explicitly reconciles against rather than silently violates).
- [`docs/1_product_and_research/loop-mode-design.md`](../../../docs/1_product_and_research/loop-mode-design.md) —
  source design note; cross-check for intent if TRD-v3 §3.3/§3.4/§14 phrasing is ambiguous.
- [`docs/1_product_and_research/headless-clis-reference.md`](../../../docs/1_product_and_research/headless-clis-reference.md) —
  **confirmed to have no Codex section as of this TRS's authoring** (read in full; only Parts
  A–D exist, covering Claude and Antigravity). T-L1.5 adds Part E, populated from the
  verified 0.144.4 schema recorded below. Part B (Claude's `--output-format json` schema) is
  useful only as a *contrast*: Codex's envelope turned out to differ structurally in every
  dimension that matters (no result event, no status, no cost, output in a separate event
  type) — do not reason from Claude's shape when implementing Codex.
- [`docs/2_architecture/TRD-v2.md`](../../../docs/2_architecture/TRD-v2.md) §3.4 — the
  `CliBackend` Protocol contract `CodexBackend` implements without changing (3-method Protocol;
  `parse_usage` stays additive, reusing L0's own precedent — see L0 Resolved Decision #1 in
  `loop-mode-phase-L0-context.md`, not re-litigated here).
- Phase L0 TRS triad ([plan](../loop-mode-phase-L0/loop-mode-phase-L0-plan.md),
  [context](../loop-mode-phase-L0/loop-mode-phase-L0-context.md),
  [tasks](../loop-mode-phase-L0/loop-mode-phase-L0-tasks.md)) — the immediately preceding
  phase; this TRS follows its task-numbering (`T-L1.N`), Resolved Decisions table, and "what
  this TRS deliberately does NOT cover" section conventions directly.

### TRS itself (this directory)

- [`loop-mode-phase-L1-plan.md`](./loop-mode-phase-L1-plan.md) — full design + flat task list
  (T-L1.1–T-L1.10) + Pending Decisions.
- [`loop-mode-phase-L1-tasks.md`](./loop-mode-phase-L1-tasks.md) — checkbox progress tracking.

### Code targets

**New:**
- `tests/fixtures/codex_jsonl/success.jsonl`, `error_during_execution.jsonl`, `malformed.txt` —
  constructed (not live-captured, unless T-L1.1 obtains real output) JSONL fixtures modeling
  the assumed Codex schema.
- `src/atlas/workflows/loop_dev.yaml` — the ungated one-shot workflow.

**Modified:**
- `src/atlas/cli_backend.py` — add `CodexBackend` class (`build_argv`, `parse_result`,
  `parse_usage`, `preflight`), `CodexUsageStats` dataclass, extend `_KNOWN_BACKENDS` to
  `frozenset({"claude", "agy", "codex"})`, extend `make_backend()`. **`CliBackend` Protocol
  itself is unchanged** — same pattern as L0's `parse_usage` addition to `ClaudeCodeBackend`.
- `src/atlas/orchestrator.py` — `Pipeline.run_to_completion()` return type widens from
  `RunContext` to a new `RunResult(ctx, status)` dataclass. **This is the one place this TRS
  extends past TRD-v3 Appendix A's literal "Unchanged" row for `orchestrator.py`** — see
  Resolved Decision #3 in the plan for the full reconciliation. Only the three existing
  `return` statements inside the method change; no new control flow.
- `src/atlas/cli.py` — the two `run_to_completion()` call sites (`run`, `resume`) updated to
  handle the new `RunResult` return type. No CLI-visible behavior change.
- `docs/1_product_and_research/headless-clis-reference.md` — new "Part E — Codex CLI headless
  reference" section, plus a Codex column/row added to the existing Part D comparison table (or
  a new Part F — author's call at implementation time, per T-L1.5).
- `tests/unit/test_cli_backend.py` — new `CodexBackend` test cases (argv, parse_result,
  parse_usage, preflight — see plan's Testing Strategy for the full list).
- `tests/unit/test_workflow_loader.py` — new `loop_dev.yaml` loader tests.
- `tests/integration/test_cli_backend_dispatch.py` — new mocked end-to-end Codex dispatch
  tests + `RunResult` regression tests.
- `STATUS.md` — phase completion entry.

**Unchanged (verify, don't touch):**
- `src/atlas/workflow_loader.py` — confirmed by reading the file: `_ALLOWED_TOP_LEVEL_KEYS`
  (`name`, `default_backend`, `stages`) and `_ALLOWED_STAGE_KEYS` (`name`, `span_kind`, `tool`,
  `gate`, `isolate`, `gate_is_async`, `backend`, `timeout_s`) already cover everything
  `loop_dev.yaml` needs. No schema change required.
- `src/atlas/stages.py` — `SPAN_KINDS` already includes `llm` (the kind chosen for
  `loop_dev`'s `code_gen` stage — see Pending Decision #2 in the plan for why `llm` was chosen
  over `subagent`). No change needed regardless of how that decision resolves, since both are
  already valid members.
- `src/atlas/plugin_resolver.py` — `RAW:` prefix handling and `build_prompt()` already exist
  and are exercised by `dev.yaml`'s `tds_gen` stage; `loop_dev.yaml`'s `plan`/`code_gen` stages
  reuse the same mechanism with no code change.
- `src/atlas/composite_runner.py`, `library_runner.py`, `shell_runner.py`,
  `plugin_resolver.py`'s `resolve()` allow-list, `post_commit_hook.py`, `state.py`,
  `config.py` — no `[loop]` config in L1 (that's L2, TRD-v3 §7); no allow-list changes needed
  since `loop_dev.yaml`'s tool strings are `RAW:`/`/verify`, both already-handled forms.
- `src/atlas/worktree.py`, `src/atlas/deliverer.py` — L0 shipped these; L1 does not modify
  them or wire `Deliverer` to any new automatic caller (see "What this TRS deliberately does
  NOT cover" in the plan).
- `src/atlas/cli.py`'s `_make_pipeline()` and overall command structure — **no `atlas loop`
  command in L1** (that's L2, TRD-v3 §3.8). Only the two `run_to_completion()` call sites'
  handling of the new return type changes; the CLI surface itself is unchanged.
- `tests/fixtures/routing_ground_truth.json` — unchanged. Confirmed by reading
  `orchestrator.py`'s `_validate_routing_fixture()`: it early-returns for any
  `workflow_name != "dev"`, so `loop_dev` runs are never checked against this fixture. No new
  workflow triggers routing-fixture validation.
- `tests/e2e/test_e2e_happy_path.py`, `tests/integration/test_main_branch_isolation.py` — run
  unmodified (regression proof), same as L0's precedent.

If implementation finds any "unchanged" file genuinely needs editing beyond what's listed
here, that's a signal the design has drifted from this TRS — pause and reconcile before
proceeding.

## Decisions made (during this TRS)

See the plan's **Resolved Decisions** table for the full rationale on all six. Summary:

| # | Decision | One-line why |
| - | --- | --- |
| 1 | **Codex schema VERIFIED** against `codex-cli 0.144.4` (2026-07-24) | Real captured stream + `codex exec --help` read directly; the originally-assumed schema was wrong in 4 material ways (see below) |
| 2 | "Guardrail signs" = `StageOutcome.status`/`RunResult.status`, no new type | User-selected; reuses existing tested contract, avoids framework-creep |
| 3 | `Pipeline.run_to_completion()` return type widens to `RunResult` | Appendix A's own escape hatch ("pause and reconcile") invoked explicitly; `cli.py` today discards the return value entirely (confirmed by reading `cli.py:191,238`), and L2's loop cannot exist without knowing run outcome |
| 4 | `-C` gets the **last** `add_dirs` element; the rest go via `--add-dir` *(revised)* | `--add-dir` exists on `codex exec` (verified); dropping the extras would make Codex runs context-poorer than Claude runs and corrupt the A/B comparison |
| 5 | `loop_dev.yaml` stays one-shot-lane only, no planned-lane scaffolding | User-selected; matches TRD-v3 §14 L1 scope verbatim, avoids untested dead code |
| 6 | `CodexUsageStats` is a distinct dataclass, not shared with `UsageStats` | Reinforced by verification: Codex has no cost field and two extra token fields — the shapes genuinely differ |
| 7 | **TRD-v3 §3.3's Codex contract table is factually wrong; this TRS supersedes it** | It describes a `result` event + subtype failure signal that 0.144.4 does not emit. Flagged for upstream correction, not silently diverged from |
| 8 | Status is exit-code-only; never inferred from event content | The stream carries no status field; inventing one would encode a guess as a contract |
| 9 | `CodexUsageStats` keeps all four token fields; `(in,out)` reduction is one documented function | Keeps the decomposition rule revisable without re-running dispatch |

## Verified Codex schema (`codex-cli 0.144.4`, captured 2026-07-24)

Real output from a trivial read-only run:

```jsonl
{"type":"thread.started","thread_id":"019f96b7-e404-7673-8853-2938007f2629"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hi"}}
{"type":"turn.completed","usage":{"input_tokens":16668,"cached_input_tokens":13056,"output_tokens":5,"reasoning_output_tokens":0}}
```

**Four ways this contradicts what TRD-v3 §3.3 (and this TRS's first draft) assumed:**

| Assumed | Actual |
| --- | --- |
| Terminal event `type: "result"` with `status` + `text` | Terminal event is `type: "turn.completed"`, carrying **only** `usage` |
| Failure detectable from a `result` subtype | **No status field anywhere** — failure is exit-code-only |
| Agent output text on the terminal event | Output lives in `item.completed` events where `item.type == "agent_message"` (field `item.text`) |
| `total_cost_usd` present (as with Claude) | **No cost field at all.** Four token fields instead: `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens` |

**Flags verified present** in `codex exec --help`: `--json`, `-C/--cd <DIR>`, `--add-dir <DIR>` (*"additional directories that should be writable alongside the primary workspace"*), `-m/--model <MODEL>`, `-s/--sandbox <read-only|workspace-write|danger-full-access>`, `--skip-git-repo-check`, `--ephemeral`, `--output-schema <FILE>`, `-o/--output-last-message <FILE>`. Bypass flags that exist and must **never** be used: `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`.

**Auth path verified:** `~/.codex/auth.json` exists on this machine after `codex login`. `--ignore-user-config`'s help text confirms auth honors `$CODEX_HOME`, so `preflight()` must read that env var rather than hardcoding `~/.codex`.

**Cost consequence — DECIDED (maintainer, 2026-07-24): v3 is tokens-only.** Codex reporting no dollar figure means engine A/B (TRD-v3 §2 KPIs) compares tokens, not dollars. Claude's cost gap is a plumb-storage problem (P1-a); Codex's is a data-availability problem with no fix short of atlas deriving cost from tokens × a price table. **No price table is built in v3.** If one is ever added, the guardrails are: dated config (`as_of`), computed at **query time not write time**, every derived figure labeled *estimated*, never stored in the same column as a CLI-reported figure — a stale price table produces *confident wrong numbers*, which is worse for a measurement tool than a missing column.

## Verified plumb token storage (read 2026-07-24)

Checked before finalizing the decomposition rule — the answer changes what the rule is *for*:

- `spans` has a **single `tokens INTEGER` column** (`plumb/adapters/_schema.py:47`). Not two.
- `RunHandle.add_span(tokens=(in, out))` splits the tuple onto the `Span` entity (`plumb/api.py:264-285`), but `Span`'s own docstring (`plumb/core/entities.py:123-127`) states the contract plainly: *"On write, `tokens_in + tokens_out` is summed and stored. On read, the sum is surfaced as `tokens_in`; `tokens_out` is always `None`. The in/out split is informational at the entity layer — it is not durable."* (v2-deferred: split into two columns.)
- `runs` **does** have `tokens_in`/`tokens_out` columns (`_schema.py:26-27`) — but those are the ones L0 proved unwritable from the online `with run()` path.

**Consequence:** per-span token storage answers exactly one question — *total tokens billed for this span*. Neither Claude's four usage fields nor Codex's four survive as a breakdown. The reduction rule exists solely to make that one integer correct, which is why the subset-vs-addend ambiguity (plan's Pending Decision #4) matters more than any field-mapping choice: it changes the number itself, not its resolution.

**Claude vs Codex token field alignment** (neither is a superset — the concrete reason `UsageStats` and `CodexUsageStats` stay separate types):

| Concept | Claude | Codex |
| --- | --- | --- |
| Uncached input | `input_tokens` | — |
| Cache read | `cache_read_input_tokens` | `cached_input_tokens` |
| Cache **write** | `cache_creation_input_tokens` | *(not reported)* |
| Output | `output_tokens` | `output_tokens` |
| Reasoning | *(not reported — folded into output)* | `reasoning_output_tokens` |

Claude's fields are documented in `headless-clis-reference.md` Part B (line 210); Codex's are from the verified 0.144.4 capture above.

## Integration points

| Direction | Surface | Failure mode | Test coverage |
| --- | --- | --- | --- |
| `CodexBackend.build_argv()` → argv list | Pure computation; last `add_dirs` element → `-C` | N/A | Unit: argv-shape + worktree-vs-repo_root tests (T-L1.2) |
| `CodexBackend.preflight()` → env/file check | Fail-closed tuple; no subprocess spawned on failure | `codex_missing_auth` | Unit — **load-bearing security test**, mirrors L0/Phase-3's `*_missing_auth_returns_failure_no_subprocess` pattern (T-L1.2) |
| `CodexBackend.parse_result()` → JSONL stdout | Line-by-line scan; never raises; skips malformed lines | `codex_nonzero_exit` / `codex_unparseable_output` / `codex_<status>` | Unit — full case table incl. malformed-line-skip (T-L1.3) |
| `CodexBackend.parse_usage()` → JSONL stdout | Same terminal-event scan; missing fields → `None`, not `KeyError` | Returns `None` or `CodexUsageStats` (possibly `None` fields) | Unit (T-L1.3) |
| `SubprocessStageRunner` → `backend.parse_usage()` | Duck-typed optional call, same mechanism L0 built for Claude | N/A | Integration (T-L1.7) |
| `resolve_workflow(workflow_name="loop_dev")` → loader | No schema change; falls through to `_PACKAGE_WORKFLOWS_DIR` | `WorkflowNotFoundError` only if the file is missing (it won't be) | Unit (T-L1.4) |
| `Pipeline.run_to_completion()` → `cli.py` callers | Return type widens `RunContext` → `RunResult`; both call sites updated same phase | N/A — additive field access via `.ctx` | Unit + Integration (T-L1.6, T-L1.7) |
| `PlumbIO.record_span(tokens=...)` ← `CodexUsageStats` decomposition | Same L0-built kwarg path, unchanged signature | N/A | Integration (T-L1.7, reusing L0's plumb-write test pattern) |

## Where this TRS's task list maps to TRD-v3 §14 Phase L1 scope bullets

| TRD-v3 §14 Phase L1 bullet | This TRS's task |
| --- | --- |
| "`CodexBackend`... register in `_KNOWN_BACKENDS` / `make_backend()`. `build_argv`... `parse_result`... `preflight` fails closed on missing auth" | T-L1.1 (recon) + T-L1.2 (argv/preflight) + T-L1.3 (parse_result/parse_usage) |
| "`loop_dev.yaml`... ungated `plan → code_gen(isolate) → verify`... Finalize guardrail 'signs'" | T-L1.4 (the YAML file) + plan's Resolved Decision #2 (the "signs" resolution) + T-L1.6 (the `RunResult` mechanism that makes "signs" checkable) |
| "Add a Codex section to `headless-clis-reference.md`" | T-L1.5 |
| "Tests: `CodexBackend` argv/parse (captured JSONL fixtures) + preflight; `loop_dev` in the loader tests. Manual smoke..." | T-L1.2/T-L1.3/T-L1.4's unit tests + T-L1.7 (integration) + T-L1.8 (manual smoke) |

T-L1.6 (`Pipeline.run_to_completion` status surfacing) does **not** map to an explicit TRD-v3
§14 L1 bullet — it's this TRS's own Resolved Decision #3, invoking Appendix A's "pause and
reconcile" escape hatch. Flagged prominently so a future reader diffing TRD-vs-TRS doesn't
mistake it for scope creep; the plan's Resolved Decision #3 entry carries the full rationale.

T-L1.9 (lint/type/coverage) and T-L1.10 (STATUS.md) are standard hygiene tasks following L0's
own T-L0.10/T-L0.11 precedent — no individual TRD-v3 bullet, but required to close a
verifiable phase.

## TRD-v3 §13 exit criterion → tests/tasks that prove it

| TRD-v3 §13 exit criterion | Proving task/test |
| --- | --- |
| Item 3: "A `loop_dev` run under `engine:codex` produces a valid `StageOutcome` (mocked in CI via captured JSONL; real dispatch in manual testing if auth allows). `preflight` fails closed on missing auth with no subprocess spawned." | T-L1.7 (`test_codex_dispatch_end_to_end_mocked`, mocked-CI half) + T-L1.8 (manual, real-dispatch half) + T-L1.2's preflight test (no-subprocess-spawned half) |
| "`loop_dev` runs end-to-end on both engines (manual smoke)." | T-L1.8, both legs (`--backend claude` and `--backend codex`) |

## What this TRS deliberately does NOT cover

See the plan's own "What this TRS deliberately does NOT cover" section for the full list
(loop.py/queue_gh.py/atlas loop CLI/[loop] config — all L2; planned lane — L2; self-healing/
judge scoring — L3; gh integration beyond L0's already-shipped `Deliverer`; prompt-injection
mitigation — L2, since no real issue bodies exist yet; any workflow-loader schema change; a
`codex exec` model-selection flag, deliberately deferred pending schema verification).

## Open thread carried from L0

L0's context.md flagged a starter `.claude/settings.json` allowlist as **deferred to L2**
(L0 Resolved Decision #4 in that TRS), reasoning that the tool set can't be known until
`loop_dev.yaml` (L1) and prompt shape (L2) both exist. **L1 now supplies half of that
missing information** — `loop_dev.yaml`'s tool strings are `RAW:<prompt>` and `/verify`, both
already-handled forms requiring no new allow-list entries beyond what `dev.yaml` already
exercises. This doesn't resolve the L2 allowlist question (Codex's `--sandbox workspace-write`
is a different confinement mechanism entirely, and Claude's `--allowedTools` contents still
depend on L2's actual prompt/tool needs), but it's worth the L2 TRS author knowing `loop_dev`
introduces no *new* tool-name surface for Claude's allowlist — only Codex's separate sandbox
flag is new, and that's argv-level, not allowlist-file-level.
