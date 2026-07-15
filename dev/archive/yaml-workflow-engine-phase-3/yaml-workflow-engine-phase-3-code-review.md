# Code Review — YAML Workflow Engine, Phase 3 (CLI Backend Dispatch)

**Reviewer:** Code Reviewer (consult-experts)
**Date:** 2026-06-30
**Scope:** Phase 3 implementation (`cli_backend.py`, `SubprocessStageRunner` refactor,
`Config` extension, `cli.py` wiring, tests, docs) + general overview of Phases 1 & 2.
**Baseline verified locally:** 239 passing · `ruff check` clean · `ruff format --check` clean ·
`mypy --strict src` clean · `cli_backend.py` 100% coverage.

---

## Verdict

**Approve.** Phase 3 lands exactly the scope its TRS committed to, with no drift into the
"unchanged" files. The refactor is a genuine strategy extraction (not a rewrite), FR-8 byte-identity
is provably preserved, and the security boundary (agy auth preflight before any subprocess) is
correctly implemented and load-bearing-tested. Every TRD-v2 §13/§14 exit criterion has a real
test behind it. The findings below are all **Low / Nit / Future** — none block the v2.2 tag.

The one open item (T3.8 manual smoke against a live `agy` binary) is correctly deferred and honestly
documented as unattempted, not silently claimed.

---

## What was verified (not just read)

| Claim in tasks.md | Verification |
|---|---|
| 239 passing | `pytest tests/ -q` → **239 passed** ✓ |
| 100% coverage on `cli_backend.py` | `--cov=atlas.cli_backend` → **74/74, 100%** ✓ |
| `mypy --strict src` green | **Success: no issues found in 18 source files** ✓ |
| ruff check + format | **All checks passed** / **18 files already formatted** ✓ |
| FR-8 byte-identity | Diffed `git show 53359e4:orchestrator.py` argv block against `ClaudeCodeBackend.build_argv()` — **identical**, incl. `add_dirs` str-coercion (Phase 2 built `list[str]`, Phase 3 builds `list[Path]` + `str(d)` → same output) ✓ |
| Load-bearing security test exists | `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` sets `mock_run.side_effect = AssertionError` and asserts `agy_missing_auth_env` — proves no spawn ✓ |
| Phase 2 `SHELL:`/`shell=` wiring preserved | `cli.py:113–116` still wires `ShellStageRunner` + `shell=shell`; `composite_runner.py` still has 3 slots ✓ |

---

## Design decisions — sound

1. **Strategy extraction with a narrow Protocol surface.** `CliBackend` returns a 3-tuple
   `(status, output_text, error_type)` from `parse_result()` rather than constructing a
   `StageOutcome`. This keeps the backend ignorant of `StageSpec`/`span_id` and makes a third
   backend a pure-append change. Correct call — the runner owns `StageOutcome` construction.

2. **`preflight()` as a method, not a pipeline phase.** Late-binding auth check matches the existing
   `resolve_timeout()` pattern and keeps resolution per-stage. The security requirement ("do not
   silently fall back to browser OAuth") is enforced *before* `build_argv()` is even called, and the
   test proves `subprocess.run` is never reached. This is the strongest part of the change.

3. **Import-cycle avoidance.** `cli_backend.py` imports `StageSpec`/`LoadedWorkflow` at module level
   but the runner imports `make_backend`/`resolve_backend` *locally inside `run()`*, and
   `SubprocessStageRunner.__init__` types `loaded_workflow` as `object` with a `# type: ignore` at
   the call site. Slightly ugly but a legitimate way to keep `orchestrator ↔ cli_backend` acyclic
   under `mypy --strict`. See Finding L-2 for a cleaner option.

4. **Fail-closed on unknown backend.** `make_backend()` raises `UnknownBackendError`, caught in the
   runner → `error_type="unknown_backend"`, run halts at that stage. No fuzzy-match. Deterministic
   and correct per §7's edge-case table.

---

## Findings

### Low

**L-1 — `agy` exit-code list is a documented assumption, not a verified contract.**
`AntigravityBackend.parse_result()` hardcodes `42 = input error`, `53 = turn limit`, else general.
These come from `headless-clis-reference.md` Part C, and `agy` is flagged experimental with contested
headless auth (issue #78). If the real binary uses different codes, the mapping silently mislabels
failures (they still surface as failures — no correctness risk — but the `error_type` could mislead).
*This is exactly what T3.8's manual smoke test should confirm.* Recommend: when T3.8 runs, capture the
real exit codes and reconcile the table; until then the code comment should say "per reference doc,
unverified against live binary."

**L-2 — `loaded_workflow: object` + `# type: ignore[arg-type]` weakens the runner's type safety.**
`SubprocessStageRunner.__init__` takes `loaded_workflow: object = None` to dodge the import cycle,
then passes it to `resolve_backend(workflow=...)` with a `# type: ignore`. A cleaner alternative:
use `TYPE_CHECKING` guarded import + string annotation (`loaded_workflow: "LoadedWorkflow | None"`),
which gives you real type-checking on the field without a runtime import. Not worth reworking now, but
worth a note — the `object` typing means a wrong-typed workflow would only fail at runtime.

**L-3 — `_KNOWN_BACKENDS` is defined but not consulted by `make_backend()`.**
`make_backend()` hardcodes `if name == "claude" / "agy"` and only uses `_KNOWN_BACKENDS` for the error
message. That's fine and arguably clearer, but it means the frozenset and the factory can drift (add
a backend to one, forget the other). A `test_known_backends_set` guards the frozenset's contents but
nothing asserts `make_backend` covers every member. Minor: consider a test that loops
`for name in _KNOWN_BACKENDS: assert isinstance(make_backend(name), CliBackend)`.

### Nit

**N-1 — `parse_result` non-zero branch prefers stdout, but a JSON error envelope on exit≠0 is lost.**
For `agy`, if the binary exits non-zero *and* emitted a JSON `{"error": {...}}` on stdout, the code
returns `("failure", stdout, "agy_general_error")` without parsing the envelope — the structured
message is passed through raw. Acceptable (the raw JSON still reaches the gate as text), but if the
real `agy` reliably emits the envelope on failure, parsing it for exit-1 too would give cleaner gate
output. Defer until T3.8 tells you what agy actually does on failure.

**N-2 — `timeout_s` and `extra_flags` are Protocol params neither backend uses.**
Both `build_argv` signatures accept `timeout_s` and `extra_flags` and ignore them. That's a
deliberate forward-compatibility seam (a future backend might encode a `--timeout` flag), and keeping
the Protocol uniform is reasonable. Just flagging that they're currently dead params — fine to leave,
but a one-line comment ("reserved for backends that encode timeout/flags in argv") would prevent a
future reader from thinking it's a bug.

### Future / out-of-scope (correctly deferred, noted for the record)

- **T3.8 manual smoke** — the only open task; honestly marked "not yet attempted." Blocks nothing in CI.
- **Per-backend model config in `.atlas.toml`** (`[backend.agy] model = ...`) — deferred per Decision #3.
- **`atlas run --backend <name>` flag** — deferred per Decision #4; non-breaking to add later.
- **Third backend** (`codex`, etc.) — Protocol + factory + allow-list is the clean extension point.

---

## FR / Exit-criteria traceability (all satisfied)

| Requirement | Evidence |
|---|---|
| FR-2 / §14 exit #1 — dev pipeline byte-identical | `test_claude_code_backend_argv_byte_identical_to_phase2` + `test_dev_pipeline_unaffected_by_phase_3` + git-diff confirmed |
| FR-6 / §14 — agy auth preflight, no silent hang | `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` (asserts no spawn) |
| FR-7 / §13 #8 — 4-tier resolution | `test_resolve_backend_priority_order` (9-case matrix) + runner-level stage/workflow tests |
| FR-9 / §13 #7 — agy dispatch end-to-end | `test_agy_dispatch_end_to_end_mocked` (asserts `agy` + `--include-directories`) |
| NFR-4 — LoC budget | `cli_backend.py` = 191 LoC (≤ 200 target) ✓ |
| NFR-5 — coverage | 100% on `cli_backend.py`; 95% repo-wide (≥ 80/85 gates) ✓ |
| NFR-7 — mypy/ruff | green ✓ |
| FR-8 — regression | 239 pass (193 Phase-2 baseline + 46 new); `test_e2e_happy_path.py` unmodified ✓ |

---

## Phase 1 & 2 overview (context for this review)

**Phase 1 (YAML engine generalization)** — took the hardcoded 7-stage dev pipeline and made it
YAML-driven: `workflow_loader.py` parses a trusted workflow YAML into `tuple[StageSpec, ...]`,
validates schema (closed key sets, `SPAN_KINDS`, name regex, duplicate detection) with line-aware
errors and no raw tracebacks. Introduced `StageSpec.backend` and `LoadedWorkflow.default_backend` as
**parsed-but-inert** fields — a clean forward seam that Phase 3 consumes without touching the loader.
The deliberate decision to *not* validate `backend` at load time (Decision #7) is the right layering:
the loader validates structure, dispatch validates content.

**Phase 2 (library-or-subprocess dispatch)** — added `CompositeStageRunner` as a prefix-router
(`LIB:` → `LibraryStageRunner`, `SHELL:` → `ShellStageRunner`, else → `SubprocessStageRunner`),
keeping `Pipeline` unaware of dispatch mechanics (it sees only the `StageRunner` Protocol). The Phase 2
**code-review resolution** (commit `53359e4`) is notable and well-handled: it split `RAW:` from a real
`SHELL:` subprocess path with a closed `{content-pipeline}` allow-list and `shell=False` (no shell
interpolation) — a genuine security-conscious fix, not cosmetic. `ShellStageRunner` never raises
(FileNotFoundError / timeout / non-zero all map to `StageOutcome` failures), satisfying NFR-2.

**Architectural through-line (Phases 1→2→3).** Each phase makes exactly one seam swappable while the
defensible core (gates + durable `tasks.md` state + plumb spans) stays untouched:
- Phase 1: *what stages run* → YAML.
- Phase 2: *how a stage's tool dispatches* → Composite/Library/Shell/Subprocess.
- Phase 3: *which CLI the subprocess is* → `CliBackend` strategy.

This is a disciplined layering. The `StageOutcome` boundary is respected at every level — `Pipeline`
doesn't know about runners, runners don't know about backends, backends don't know about
`StageSpec`. That consistency is the strongest architectural signal across the three phases.

**One cross-phase observation:** `orchestrator.py` is now ~718 LoC and holds `SubprocessStageRunner`,
`ClickPrompter`, `Pipeline`, `RunContext`, `StageOutcome`, `resolve_timeout`, and helpers. It's over
the project's own 400/800-line file guidance and is the natural next split candidate (e.g. extract
`SubprocessStageRunner` into `subprocess_runner.py` alongside the sibling runner modules). Not a Phase 3
task — flagging as tech-debt for a future cleanup phase.

---

## Recommendation

Ship v2.2. Address L-1/N-1 opportunistically **when T3.8 runs against a live `agy` binary** (that's
the moment the exit-code and failure-envelope assumptions become verifiable). L-2, L-3, N-2 are
optional polish. The orchestrator.py split is a separate future-phase concern.
