# TRS — YAML Workflow Engine, Phase 3 (CLI Backend Dispatch)

**Project:** atlas — v2 YAML-driven gated-workflow engine
**Component:** `src/atlas/cli_backend.py` (new) + `orchestrator.py` (refactor `SubprocessStageRunner`) + `cli.py`, `config.py` (extended)
**Status:** Draft, pre-implementation
**Last reviewed:** 2026-06-30
**Grounds on:** [TRD-v2](../../../docs/2_architecture/TRD-v2.md) §3.4, §4, §5, §6, §10, §13 #7–8, §14 (Phase 3); [`cli-backend-dispatch.md`](../../../docs/1_product_and_research/cli-backend-dispatch.md); [`headless-clis-reference.md`](../../../docs/1_product_and_research/headless-clis-reference.md); [v1 TRD](../../../docs/2_architecture/TRD.md); [PRD](../../../docs/1_product_and_research/PRD.md); Phase 1 TRS ([plan](../yaml-workflow-engine-phase-1/yaml-workflow-engine-phase-1-plan.md)); Phase 2 TRS ([plan](../yaml-workflow-engine-phase-2/yaml-workflow-engine-phase-2-plan.md))

> This TRS details exactly one TRD phase — Phase 3 — into a flat task list. It does not re-plan releases (PRD-owned) or re-sequence phases (TRD-owned). Phase 4 (second-brain trigger skill) is out of scope here and gets its own TRS when picked up.

---

## Phase Summary

**TRD phase:** Phase 3 — CLI backend dispatch (TRD-v2 §14).
**PRD release(s) delivered:** None directly — TRD-v2's preamble notes the PRD's future-releases table predates the YAML-workflow analysis. TRD-v2 §11 tags this phase **v2.2 — CLI backend dispatch with Antigravity**. A formal PRD update should follow Phase 3's exit.
**Goal (verbatim from TRD-v2 §14):** "Enable per-stage dispatch to different agentic CLIs (`claude -p`, `agy -p`), selectable via workflow YAML or config."

**Prerequisite status (2026-06-30):**

- ✅ **Phase 1 complete** — `workflow_loader.py` ships; `StageSpec.backend: str | None` is parsed and threaded through (Phase 1 §3.2, Resolved Decision #5); `LoadedWorkflow.default_backend` is parsed; both fields are currently **inert** in `SubprocessStageRunner`.
- ✅ **Phase 2 complete** — `CompositeStageRunner` dispatches `LIB:`/`RAW:`/plugin-command tools to the right runner; `job.yaml`'s `tailor_materials.backend: claude` field is parsed but not consumed.
- The seams Phase 3 needs (`StageSpec.backend`, `LoadedWorkflow.default_backend`, `SubprocessStageRunner` as the single subprocess dispatcher, `CompositeStageRunner` already wrapping it) are all in place. Phase 3 is the phase that finally *consumes* the `backend` field. **No upstream gating task is required** — Phase 1 and 2 are merged on `main`.

---

## 1. Overview & Scope

### In scope

Everything TRD-v2 §14 Phase 3's engineering-scope bullets and §3.4 specify:

- Define a `CliBackend` Protocol per TRD-v2 §3.4 (signature locked: `build_argv(prompt, model, add_dirs, timeout_s, extra_flags) -> list[str]` and `parse_result(stdout, stderr, returncode) -> StageOutcome`).
- Implement `ClaudeCodeBackend` by **extracting** the existing argv-construction and stdout-handling logic from `SubprocessStageRunner.run()` (lines 582–622 in `orchestrator.py`) into a class that satisfies the Protocol. **No flag changes** (see Resolved Decision #2 below — `--bare` is deliberately NOT added; `--no-session-persistence` stays).
- Implement `AntigravityBackend` per `agy -p` flag surface (`headless-clis-reference.md` Part C): `agy -p <prompt> --output-format json --include-directories <dirs> --model <m>`, parse JSON `.response` / `.error` fields, validate `GEMINI_API_KEY` or `GOOGLE_API_KEY` is set before subprocess dispatch (TRD-v2 §4 Security).
- Refactor `SubprocessStageRunner` to accept a `CliBackend` strategy (default: `ClaudeCodeBackend()`). The runner becomes a thin shell that owns subprocess invocation + timeout + retry/exception handling; the backend owns argv + result parsing.
- Backend resolution (4-tier, per TRD-v2 §3.4): per-stage `StageSpec.backend` → workflow `LoadedWorkflow.default_backend` → `.atlas.toml [backend] default` → hard default `"claude"`. Resolution happens inside `SubprocessStageRunner.run()` (or a small helper) so per-stage selection is dynamic, not fixed at runner construction time.
- Extend `Config` (`config.py`) with a `default_backend: str = "claude"` field, read from `.atlas.toml`'s `[backend]` section.
- Unit tests for both backends: argv construction tables, result parsing for success/error/timeout cases.
- Integration test: at least one stage dispatches to `AntigravityBackend` via a mocked subprocess (TRD-v2 §13 #7).
- Update `docs/3_guides/` (or README) with per-CLI auth requirements (`ANTHROPIC_API_KEY` for Claude clean-headless; `GEMINI_API_KEY`/`GOOGLE_API_KEY` for agy; the "experimental status of agy" caveat per TRD-v2 §5).

### Out of scope (deferred to later phases / out of scope entirely)

- **Phase 4** (second-brain ai-workx trigger skill) — out of TRD-v2 scope.
- **New CLI flag** `atlas run --backend <name>` — explicitly NOT added (Resolved Decision #4 below). The 4-tier resolution from §3.4 is exact; a flag can be added later without breaking changes.
- **Switching `ClaudeCodeBackend` to `--output-format json`** — explicitly NOT done (Resolved Decision #1 below). Claude stays on plain-text mode for dev-pipeline byte-identity; Antigravity uses JSON output where the per-CLI table makes that the only sane choice (agy's exit-code-only failure signal makes JSON parsing necessary to distinguish auth/quota errors from other failures).
- **Adding `--bare` to Claude invocations** — explicitly NOT done (Resolved Decision #2 below). `--bare` would skip the DEV-ESSENTIALS plugin discovery the dev pipeline relies on, breaking Phase 1's parity claim.
- **A real `agy -p` subprocess invocation in CI** — TRD-v2 §13 #7 states "mocked in CI; real dispatch in manual testing if auth allows." Phase 3 ships with mocked subprocess in CI and a documented manual smoke test for live runs (T3.8 below).
- **Per-backend model knobs in `.atlas.toml`** — Phase 3 adds `[backend] default = "claude"` only; per-backend model overrides (e.g. `[backend.agy] model = "gemini-flash-lite"`) are not in scope. The existing top-level `model = "haiku"` continues to serve Claude; `AntigravityBackend()` constructor takes its own default. If a real workflow needs per-stage agy-model selection in Phase 3, it sets `backend: agy` and the backend uses its hardcoded default — finer-grained per-backend model config is YAGNI here.
- **Antigravity authentication beyond env-var presence checking.** `AntigravityBackend` validates that `GEMINI_API_KEY` or `GOOGLE_API_KEY` is set *before* `subprocess.run()` (TRD-v2 §4 Security), but does NOT attempt browser OAuth or any interactive flow. If the env vars are unset, the stage fails with a clear error — this matches the maintainer-resolved "explicit > implicit" pattern from Phase 2's Resolved Decision #2.

### Why this scope

Phase 3 is the only phase that touches the *subprocess-dispatch* seam (how a stage's tool string becomes an argv list and how the subprocess result becomes a `StageOutcome`). Phase 1 made workflows YAML-driven; Phase 2 made stages library-or-subprocess-dispatched; Phase 3 makes the *subprocess* itself swappable. The defensible core (gates + durable state + plumb measurement) is unchanged by all three phases — that's the whole point of TRD-v2 §6's "Pipeline sees only the `StageRunner` Protocol and `StageOutcome`" boundary.

Importantly: this phase is where the `backend` field added in Phase 1 finally pays off. Without Phase 3, `StageSpec.backend` is a parsed-but-ignored hint; after Phase 3, it's the authoritative per-stage dispatch knob.

---

## 2. Requirements Summary

### Functional (from TRD-v2 §3.4, §14, §13 #7–8, mapped to FR IDs for traceability)

- **FR-1** (§3.4) — `CliBackend` Protocol defined with the locked signature: `name: str`, `build_argv(*, prompt: str, model: str, add_dirs: list[Path], timeout_s: int, extra_flags: dict[str, str]) -> list[str]`, `parse_result(stdout: str, stderr: str, returncode: int) -> StageOutcome`. Both backends are `runtime_checkable` Protocol implementations (mypy-type-checked, not just duck-typed).
- **FR-2** (§3.4) — `ClaudeCodeBackend.build_argv()` produces `["claude", "-p", prompt, "--no-session-persistence", "--model", model, "--add-dir", <dir1>, "--add-dir", <dir2>, ...]` — byte-identical to the current `SubprocessStageRunner` argv (FR-8 below depends on this).
- **FR-3** (§3.4) — `AntigravityBackend.build_argv()` produces `["agy", "-p", prompt, "--output-format", "json", "--model", model, "--include-directories", <dir1>, "--include-directories", <dir2>, ...]`. Note: `agy` uses `--include-directories` (NOT `--add-dir`), repeated per-directory (per `headless-clis-reference.md` Part C's flag table).
- **FR-4** (§3.4) — `ClaudeCodeBackend.parse_result()` treats stdout verbatim as `output_text` (preserves dev-pipeline behavior); on `returncode != 0` returns `status="failure"` with `error_type="plugin_nonzero_exit"`; on success returns `status="success"`. Identical semantics to today's `SubprocessStageRunner`.
- **FR-5** (§3.4) — `AntigravityBackend.parse_result()` JSON-parses stdout (expecting `{"response": str, "stats": {...}, "error": {...}?}` per `headless-clis-reference.md` Part C). On `returncode == 0` and a `response` field, returns `status="success", output_text=<response>`. On non-zero returncode (1/42/53 — agy's documented exit codes) returns `status="failure"` with a specific `error_type` (see §6.3's table). On unparseable JSON returns `status="failure", error_type="agy_unparseable_output"`.
- **FR-6** (§3.4, TRD-v2 §4 Security) — `AntigravityBackend` validates that `GEMINI_API_KEY` or `GOOGLE_API_KEY` is set in `os.environ` **before** spawning the subprocess. If neither is set, returns `StageOutcome(status="failure", error_type="agy_missing_auth_env")` without attempting subprocess dispatch. The check happens once per `run()` call (cheap; no caching needed). This makes the "do not silently fall back to browser auth" requirement enforceable.
- **FR-7** (§3.4 backend resolution order) — Per-stage `StageSpec.backend` → `LoadedWorkflow.default_backend` → `Config.default_backend` → hard default `"claude"`. Resolution is centralized in a `resolve_backend()` helper (analogous to Phase 1's `resolve_timeout()`), exposed at module level so it's directly unit-testable.
- **FR-8** (regression safety) — All Phase 1 + Phase 2 tests pass unchanged. Dev pipeline behavior is byte-identical (same argv, same stdout-as-`output_text`, same `--model haiku` default). Job pipeline behavior is byte-identical for the `RAW:` and `LIB:` stages; `tailor_materials.backend: claude` now correctly resolves to `ClaudeCodeBackend`, but since that backend produces the same argv as today's hardcoded path, no behavioral difference.
- **FR-9** (§14) — At least one integration test demonstrates `AntigravityBackend` dispatch end-to-end: a synthetic workflow with `backend: agy` on a stage, content-pipeline mocked / not needed, `subprocess.run` patched to return a canned agy JSON response. The test asserts the argv contains `agy` (not `claude`), `--include-directories` (not `--add-dir`), and the parsed `output_text` matches the mocked `response` field.

### Non-functional (from TRD-v2 §4, §5, §10)

- **NFR-1** (§4 Performance) — `CliBackend.build_argv()` and `parse_result()` are pure computation, < 1 ms each (TRD-v2 §4 verbatim). No I/O, no env reads inside `build_argv()` (env reads belong in `AntigravityBackend.run()`-time validation, FR-6, not in argv construction).
- **NFR-2** (§4 Reliability) — Backend resolution is deterministic; same inputs always produce the same backend. Resolution failure (unknown backend name) → `StageOutcome(status="failure", error_type="unknown_backend")`, not an exception that crashes the orchestrator loop.
- **NFR-3** (§4 Usability) — `agy` auth failure produces a clear, user-facing error message naming the required env vars and pointing at `headless-clis-reference.md` Part C's auth note. Not a silent hang, not a raw traceback (TRD-v2 §13 #7's "agy auth failure produces a clear error, not a silent hang").
- **NFR-4** (§5 LoC budget) — TRD-v2 §5 caps engine code (orchestrator + loader + backends + state) at ~600 lines total. Phase 3 adds `cli_backend.py` (target ≤ 200 lines: Protocol + 2 backend classes + `resolve_backend()`). Current state: orchestrator.py = 719 LoC, workflow_loader.py = 188, state.py = ~270, composite_runner.py = 41 (Phase 2 split). Phase 3's additions push the engine total higher, but Phase 3's *contribution* (the `cli_backend.py` file) is the new "backends" line item in TRD-v2 §5's parenthetical, so the existing total stays in budget if the new file is lean.
- **NFR-5** (§10 coverage) — `cli_backend.py` ≥ 85% (matches TRD-v2 §10's explicit target). Existing modules unchanged from Phase 2 (full suite ≥ 80%).
- **NFR-6** (§4 Security) — `yaml.safe_load()` is already used by Phase 1's loader; Phase 3 introduces no new YAML parsing. Trust boundary unchanged: workflow YAML is trusted, `backend: <name>` is allowed to be any string but is validated against the closed set `{"claude", "agy"}` at *dispatch* time (not load time — keeping Phase 1's loader unchanged, see Resolved Decision #6).
- **NFR-7** (§4 mypy/ruff CI gates) — `mypy --strict src` and `ruff check`/`ruff format --check` pass.

---

## 3. Detailed Component Design

### 3.1 Module structure (post–Phase 3)

```
src/atlas/
├── __init__.py
├── cli.py                     # + Config.default_backend wired into _make_pipeline()
├── orchestrator.py             # SubprocessStageRunner refactored to accept a CliBackend strategy
├── composite_runner.py          # unchanged from Phase 2
├── library_runner.py             # unchanged from Phase 2
├── library_adapters/              # unchanged from Phase 2
├── workflow_loader.py              # unchanged from Phase 1
├── stages.py                        # unchanged from Phase 1
├── state.py                          # unchanged from Phase 1
├── plugin_resolver.py                 # unchanged from Phase 1
├── plumb_io.py                         # unchanged
├── post_commit_hook.py                  # unchanged
├── cli_backend.py                        # NEW — CliBackend Protocol + Claude + Antigravity backends + resolve_backend()
├── worktree.py                            # unchanged
├── config.py                               # + default_backend field
└── workflows/
    ├── dev.yaml                          # unchanged (no per-stage backend overrides; uses claude by default)
    ├── job.yaml                           # unchanged from Phase 2 (already has tailor_materials.backend: claude, now finally consumed)
    └── job_cli.yaml                        # unchanged from Phase 2
```

`cli_backend.py` is the only new code module. `Config` gains one field. `SubprocessStageRunner` is refactored to delegate argv/result-parsing to a `CliBackend`. No other Phase 1 / Phase 2 files change.

### 3.2 `cli_backend.py` — the new module

```python
# src/atlas/cli_backend.py
"""Per-CLI argv construction and result-parsing strategies for SubprocessStageRunner.

Trust boundary: backends never call subprocess themselves — they only build
argv lists and parse already-captured stdout/stderr/returncode. SubprocessStageRunner
owns the actual subprocess.run() call (TRD-v2 §6: 'Pipeline sees only the StageRunner
Protocol and StageOutcome — it does not know which CLI was used'). Same boundary
applies one level deeper: SubprocessStageRunner sees only the CliBackend Protocol
and a StageOutcome — it does not know which CLI-specific flag dialect was used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from atlas.orchestrator import StageOutcome
from atlas.stages import StageSpec
from atlas.workflow_loader import LoadedWorkflow

_KNOWN_BACKENDS: frozenset[str] = frozenset({"claude", "agy"})


@runtime_checkable
class CliBackend(Protocol):
    name: str

    def build_argv(
        self,
        *,
        prompt: str,
        model: str,
        add_dirs: list[Path],
        timeout_s: int,
        extra_flags: dict[str, str],
    ) -> list[str]: ...

    def parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> tuple[str, str, str | None]:
        """Return (status, output_text, error_type).

        status: 'success' | 'failure'
        output_text: text to surface at the next gate (may be empty)
        error_type: None on success; an error_type string otherwise
        """
        ...

    def preflight(self) -> tuple[str, str | None] | None:
        """Optional pre-dispatch check (e.g. env-var validation for agy).

        Returns None if preflight passes; otherwise returns (error_message, error_type)
        for SubprocessStageRunner to surface as a StageOutcome(status='failure').
        """
        ...


class ClaudeCodeBackend:
    name = "claude"

    def build_argv(self, *, prompt, model, add_dirs, timeout_s, extra_flags):
        argv = [
            "claude", "-p", prompt,
            "--no-session-persistence",
            "--model", model,
        ]
        for d in add_dirs:
            argv.extend(["--add-dir", str(d)])
        return argv

    def parse_result(self, stdout, stderr, returncode):
        if returncode != 0:
            return ("failure", stdout, "plugin_nonzero_exit")
        return ("success", stdout, None)

    def preflight(self):
        return None  # No pre-dispatch check; claude -p handles its own auth at subprocess level


class AntigravityBackend:
    name = "agy"

    def __init__(self, *, default_model: str = "gemini-flash-lite") -> None:
        self._default_model = default_model

    def build_argv(self, *, prompt, model, add_dirs, timeout_s, extra_flags):
        effective_model = model or self._default_model
        argv = [
            "agy", "-p", prompt,
            "--output-format", "json",
            "--model", effective_model,
        ]
        for d in add_dirs:
            argv.extend(["--include-directories", str(d)])
        return argv

    def parse_result(self, stdout, stderr, returncode):
        # agy exit codes: 0=success, 1=general error, 42=input error, 53=turn limit.
        if returncode == 42:
            return ("failure", stdout or stderr, "agy_input_error")
        if returncode == 53:
            return ("failure", stdout or stderr, "agy_turn_limit")
        if returncode != 0:
            return ("failure", stdout or stderr, "agy_general_error")

        # Parse JSON envelope. agy --output-format json returns {response, stats, error?}.
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return ("failure", stdout, "agy_unparseable_output")

        if "error" in payload and payload["error"]:
            err = payload["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return ("failure", msg, "agy_response_error")

        response = payload.get("response", "")
        if not isinstance(response, str):
            return ("failure", stdout, "agy_response_not_string")
        return ("success", response, None)

    def preflight(self):
        # TRD-v2 §4 Security: must validate API key before subprocess; do NOT
        # silently fall back to browser OAuth.
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            return (
                "Antigravity (agy) requires GEMINI_API_KEY or GOOGLE_API_KEY in the "
                "environment for headless dispatch. Browser OAuth fallback is "
                "intentionally disabled. See docs/3_guides/cli_backends.md.",
                "agy_missing_auth_env",
            )
        return None


def resolve_backend(
    *, stage: StageSpec, workflow: LoadedWorkflow, config_default: str | None
) -> str:
    """Resolve a stage's backend name per TRD-v2 §3.4's 4-tier order.

    1. Per-stage StageSpec.backend (highest priority).
    2. Workflow's LoadedWorkflow.default_backend.
    3. Config.default_backend (from .atlas.toml [backend] default).
    4. Hard default 'claude'.
    """
    if stage.backend is not None:
        return stage.backend
    if workflow.default_backend is not None:
        return workflow.default_backend
    if config_default is not None:
        return config_default
    return "claude"


def make_backend(name: str) -> CliBackend:
    """Construct a CliBackend instance by name; raise UnknownBackendError otherwise."""
    if name == "claude":
        return ClaudeCodeBackend()
    if name == "agy":
        return AntigravityBackend()
    raise UnknownBackendError(
        f"Unknown backend {name!r}. Allowed: {sorted(_KNOWN_BACKENDS)}"
    )


class UnknownBackendError(Exception):
    """Raised when resolve_backend() returns a name not in _KNOWN_BACKENDS."""
```

**Notes:**
- The Protocol uses a 3-tuple return (`status`, `output_text`, `error_type`) from `parse_result()` rather than constructing a `StageOutcome` directly — the runner owns `StageOutcome` construction (with `stage=stage`, `span_id=""`) so backends don't need to know about `StageSpec`. This keeps the `CliBackend` surface narrow and easier to add a third backend to later.
- `preflight()` is the seam for the `agy` env-var check; for backends with no preflight (`ClaudeCodeBackend`), it returns `None`.
- `make_backend()` and `UnknownBackendError` live in this module so the runner doesn't carry that knowledge; the runner just calls `make_backend(resolved_name)`.

### 3.3 `SubprocessStageRunner` refactor

```python
# src/atlas/orchestrator.py — refactored SubprocessStageRunner
class SubprocessStageRunner:
    def __init__(
        self,
        *,
        timeout_overrides: dict[str, int] | None = None,
        command_overrides: dict[str, str] | None = None,
        model: str = "haiku",
        default_backend: str = "claude",          # NEW — Config.default_backend, tier 3
        loaded_workflow: LoadedWorkflow | None = None,  # NEW — for tier 2 lookup
    ) -> None:
        self._timeout_overrides = timeout_overrides or {}
        self._command_overrides = command_overrides or {}
        self._model = model
        self._default_backend = default_backend
        self._workflow = loaded_workflow

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        from atlas.cli_backend import make_backend, resolve_backend
        from atlas.plugin_resolver import build_prompt, resolve

        plugin_cmd = resolve(stage.tool, overrides=self._command_overrides)
        timeout_s = resolve_timeout(stage, self._timeout_overrides)

        # Build prompt (unchanged from Phase 2).
        atlas_root = _find_atlas_root()
        target_dir = ctx.worktree_path if ctx.worktree_path is not None else ctx.repo_root
        tasks_md = ctx.repo_root / "dev" / "active" / ctx.slug / "tasks.md"
        context_hint = f"Context file: {tasks_md}\nWorking directory: {target_dir}"
        prompt = build_prompt(plugin_cmd, ctx.task, context_hint)

        # Resolve the per-stage backend (NEW).
        backend_name = resolve_backend(
            stage=stage,
            workflow=self._workflow,  # may be None for legacy direct-construction
            config_default=self._default_backend,
        )
        try:
            backend = make_backend(backend_name)
        except UnknownBackendError as exc:
            return StageOutcome(
                stage=stage, span_id="", status="failure",
                output_text=str(exc), error_type="unknown_backend",
            )

        # Run the backend's preflight (env-var checks, etc.).
        preflight = backend.preflight()
        if preflight is not None:
            msg, error_type = preflight
            return StageOutcome(
                stage=stage, span_id="", status="failure",
                output_text=msg, error_type=error_type,
            )

        # Build argv via the backend.
        add_dirs = [ctx.repo_root]
        if ctx.worktree_path is not None:
            add_dirs.append(ctx.worktree_path)
        argv = backend.build_argv(
            prompt=prompt,
            model=self._model,
            add_dirs=add_dirs,
            timeout_s=timeout_s,
            extra_flags={},
        )

        # Subprocess invocation (unchanged shape, argv from backend).
        try:
            result = subprocess.run(
                argv,
                cwd=str(atlas_root),
                capture_output=True,
                check=False,
                timeout=timeout_s,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return StageOutcome(
                stage=stage, span_id="", status="failure",
                output_text="", error_type="plugin_timeout",
            )

        # Backend interprets the result.
        status, output_text, error_type = backend.parse_result(
            result.stdout, result.stderr, result.returncode
        )
        return StageOutcome(
            stage=stage, span_id="", status=status,
            output_text=output_text, error_type=error_type,
        )
```

**What changed vs Phase 2's `SubprocessStageRunner.run()`:**

| Phase 2 line | Replaced by |
|---|---|
| Hardcoded `["claude", "-p", prompt, "--no-session-persistence", "--model", self._model, ...]` | `backend.build_argv(...)` |
| Hardcoded `if result.returncode != 0: return StageOutcome(..., error_type="plugin_nonzero_exit")` | `backend.parse_result(...)` → tuple |
| Implicit "always claude" | Explicit `resolve_backend()` + `make_backend()` |
| No preflight | `backend.preflight()` returns failure tuple if auth env vars missing |

**What did NOT change:**
- Subprocess invocation pattern (`subprocess.run(argv, cwd=..., capture_output=True, check=False, timeout=..., text=True)`).
- Timeout handling (`TimeoutExpired` → `error_type="plugin_timeout"`).
- `plugin_resolver.resolve()` + `build_prompt()` flow.
- `_find_atlas_root()` cwd logic.

### 3.4 `Config` extension

```python
# src/atlas/config.py
@dataclass(frozen=True)
class Config:
    repo_root: Path
    plumb_db_path: Path
    plugin_commands: dict[str, str] = field(default_factory=dict)
    timeout_overrides: dict[str, int] = field(default_factory=dict)
    model: str = "haiku"
    default_backend: str = "claude"  # NEW — from .atlas.toml [backend] default

    @classmethod
    def load(cls, repo_root: Path) -> Config:
        # ... existing merge logic ...
        backend_section = merged.get("backend", {})
        default_backend = (
            str(backend_section.get("default", "claude"))
            if isinstance(backend_section, dict)
            else "claude"
        )
        return cls(
            # ... existing fields ...
            default_backend=default_backend,
        )
```

**`.atlas.toml` schema additions:**

```toml
[backend]
default = "claude"   # or "agy" — switches the project-wide default
```

Per-stage overrides continue to use the YAML field (`backend: claude` or `backend: agy` on a stage); the `.atlas.toml` `[backend]` section is the project-wide default tier (#3 in the 4-tier resolution).

### 3.5 `cli.py` wiring (`_make_pipeline()`)

```python
# src/atlas/cli.py — _make_pipeline()
def _make_pipeline(repo_root, cfg, *, auto_approve=False, workflow=None, workflow_file=None):
    loaded = resolve_workflow(
        workflow_file=workflow_file, workflow_name=workflow, repo_root=repo_root
    )
    plumb = PlumbIO(real=True)
    state = StateStore(repo_root)
    worktree = WorktreeManager(repo_root)
    default_runner = SubprocessStageRunner(
        timeout_overrides=cfg.timeout_overrides,
        command_overrides=cfg.plugin_commands,
        model=cfg.model,
        default_backend=cfg.default_backend,   # NEW
        loaded_workflow=loaded,                 # NEW — workflow-level default_backend
    )
    library: LibraryStageRunner | None = None
    if any(s.tool.startswith("LIB:") for s in loaded.stages):
        library = LibraryStageRunner()
    composite = CompositeStageRunner(default=default_runner, library=library)
    # ... rest unchanged ...
```

`loaded_workflow` is threaded into `SubprocessStageRunner` so the runner can read `workflow.default_backend` at dispatch time (tier 2 of `resolve_backend()`). This is the only Phase-3-specific `cli.py` change beyond passing `cfg.default_backend`.

---

## 4. API Specifications

### 4.1 CLI surface

**No new flags.** Phase 3 deliberately omits an `atlas run --backend <name>` flag (Resolved Decision #4 below). Backend selection happens through the existing YAML/config mechanisms only.

Phase 1's existing flags continue to work unchanged:
```
atlas run "<task>" --workflow <name>
atlas run "<task>" --workflow-file <path>
atlas resume
atlas status
atlas hook install
```

### 4.2 Public Python API additions

| Symbol | Module | Purpose |
|---|---|---|
| `CliBackend` (Protocol) | `atlas.cli_backend` | Backend strategy interface |
| `ClaudeCodeBackend` | `atlas.cli_backend` | Default backend; produces today's argv |
| `AntigravityBackend` | `atlas.cli_backend` | New backend for `agy -p` dispatch |
| `resolve_backend(stage, workflow, config_default)` | `atlas.cli_backend` | 4-tier resolution helper |
| `make_backend(name)` | `atlas.cli_backend` | Factory; raises `UnknownBackendError` |
| `UnknownBackendError` | `atlas.cli_backend` | Raised for unknown backend names |
| `Config.default_backend` | `atlas.config` | New field, default `"claude"` |
| `SubprocessStageRunner(default_backend=..., loaded_workflow=...)` | `atlas.orchestrator` | Two new kwargs |

### 4.3 Error surface (extends Phase 2's table)

| Condition | `error_type` | When detected |
|---|---|---|
| Resolved backend name not in `{"claude", "agy"}` | `unknown_backend` | At `make_backend()` call, before subprocess |
| `agy` selected but `GEMINI_API_KEY`/`GOOGLE_API_KEY` both unset | `agy_missing_auth_env` | At `AntigravityBackend.preflight()`, before subprocess |
| `agy -p` subprocess exits with code 1 (general error / API failure) | `agy_general_error` | In `AntigravityBackend.parse_result()` |
| `agy -p` exits with code 42 (input error) | `agy_input_error` | In `parse_result()` |
| `agy -p` exits with code 53 (turn limit exceeded) | `agy_turn_limit` | In `parse_result()` |
| `agy -p` exits 0 but stdout is not valid JSON | `agy_unparseable_output` | In `parse_result()` |
| `agy -p` returns JSON with non-empty `error` object | `agy_response_error` | In `parse_result()` |
| `agy -p` returns JSON with `response` field not a string | `agy_response_not_string` | In `parse_result()` |
| `claude -p` subprocess exits non-zero (unchanged from Phase 2) | `plugin_nonzero_exit` | In `ClaudeCodeBackend.parse_result()` |
| Subprocess timeout (either backend) | `plugin_timeout` | In `SubprocessStageRunner.run()` (catches `TimeoutExpired`) |

---

## 5. Database Design

Unchanged from Phase 2. No plumb schema changes (TRD-v2 §7, §13 #10). No new state files. `.atlas.toml`'s `[backend]` section is read but not written by atlas.

---

## 6. Algorithm & Logic Design

### 6.1 Backend resolution (`resolve_backend()` pseudocode)

```
function resolve_backend(stage, workflow, config_default) -> str:
    if stage.backend is not None: return stage.backend       # Tier 1: per-stage YAML
    if workflow.default_backend is not None: return workflow.default_backend  # Tier 2: workflow YAML
    if config_default is not None: return config_default       # Tier 3: .atlas.toml
    return "claude"                                              # Tier 4: hard default
```

Pure function, no I/O, deterministic. Unit-tested in `test_cli_backend.py::test_resolve_backend_priority_order`.

### 6.2 Dispatch flow (`SubprocessStageRunner.run()` pseudocode)

```
function run(ctx, stage) -> StageOutcome:
    plugin_cmd = plugin_resolver.resolve(stage.tool)
    timeout_s = resolve_timeout(stage, overrides)
    prompt = build_prompt(plugin_cmd, ctx.task, context_hint)

    backend_name = resolve_backend(stage, workflow, config_default)
    try:
        backend = make_backend(backend_name)
    except UnknownBackendError as e:
        return failure("unknown_backend", str(e))

    preflight = backend.preflight()
    if preflight is not None:
        msg, error_type = preflight
        return failure(error_type, msg)

    add_dirs = [ctx.repo_root] + ([ctx.worktree_path] if ctx.worktree_path else [])
    argv = backend.build_argv(prompt=prompt, model=self._model, add_dirs=add_dirs, ...)

    try:
        result = subprocess.run(argv, ...)
    except TimeoutExpired:
        return failure("plugin_timeout", "")

    status, output_text, error_type = backend.parse_result(
        result.stdout, result.stderr, result.returncode
    )
    return StageOutcome(stage=stage, span_id="", status=status,
                        output_text=output_text, error_type=error_type)
```

### 6.3 `AntigravityBackend.parse_result()` decision table

| `returncode` | stdout shape | Result |
|---|---|---|
| 0 | `{"response": "<text>", "stats": {...}}` | `success`, `output_text=<text>` |
| 0 | `{"response": "...", "error": {...non-empty...}}` | `failure`, `agy_response_error` |
| 0 | `{"response": 42}` (non-string) | `failure`, `agy_response_not_string` |
| 0 | Not valid JSON | `failure`, `agy_unparseable_output` |
| 1 | (any) | `failure`, `agy_general_error` |
| 42 | (any) | `failure`, `agy_input_error` |
| 53 | (any) | `failure`, `agy_turn_limit` |
| Other non-zero | (any) | `failure`, `agy_general_error` |

`stdout` is prepended (or fallback to `stderr` if empty) into `output_text` for non-zero returncodes so failures still surface diagnostic text at the gate / log.

### 6.4 Why backend preflight is a method, not a separate phase

A separate Phase-3-level "preflight" pipeline phase was considered (checking env vars at `Pipeline.start()` time before any stage runs). Rejected for three reasons:

1. **Resolution is per-stage**, so the set of "backends in use" isn't known until the runner iterates stages — checking at `start()` would require pre-resolving every stage's backend, which doubles the dispatch logic.
2. **Late binding matches Phase 1/2 pattern.** `resolve_timeout()` and tool resolution both happen inside `run()`, not at construction time. Adding a new early-binding phase just for backend auth would diverge from this pattern without a real benefit.
3. **Failures still surface clearly.** A `preflight()` failure produces a `StageOutcome(status="failure", error_type="agy_missing_auth_env")` that follows the same gate / log path as any other stage failure. The user sees the actionable error before the subprocess would have hung.

---

## 7. Error Handling & Edge Cases

| Case | Handling |
|---|---|
| User sets `backend: opus` on a stage (typo / future-CLI name) | `make_backend()` raises `UnknownBackendError` → `StageOutcome(status="failure", error_type="unknown_backend")`. Run halts at that stage. No "fuzzy match" / "did you mean" — keep it deterministic. |
| `.atlas.toml` has `[backend] default = "agy"` but `GEMINI_API_KEY` is unset | Every claude-or-agy stage in the workflow will fail at `AntigravityBackend.preflight()` with `agy_missing_auth_env` (assuming no per-stage `backend: claude` override). The error message names both env vars and points at the auth docs. |
| `agy -p` subprocess hangs past `timeout_s` | Same code path as Claude: `subprocess.TimeoutExpired` → `error_type="plugin_timeout"`. No backend-specific timeout logic. |
| Workflow YAML has `default_backend: agy` and a stage with `backend: claude` | Per-stage wins (tier 1 > tier 2). The stage dispatches to `ClaudeCodeBackend`, the rest of the workflow dispatches to `AntigravityBackend`. Mixed-backend runs are fully supported. |
| `ClaudeCodeBackend.parse_result()` receives unparseable stdout | Not applicable — Claude is text-mode; any returncode-0 result is treated as success with stdout as `output_text`. This is the dev-pipeline byte-identity property (FR-8). |
| Two backends share an `error_type` (e.g. both timeout) | `plugin_timeout` is raised by `SubprocessStageRunner` itself (catching `TimeoutExpired`), not by either backend — so there's no collision. Each backend's `parse_result()` returns its own `agy_*` / `plugin_nonzero_exit` codes, which are distinct. |
| `agy` JSON includes a populated `error` field but `returncode == 0` | Treated as `failure` with `error_type="agy_response_error"` (the API succeeded at the transport level but the model reported an error). Per `headless-clis-reference.md` Part C's schema: `error` is "present if the request failed." |
| `SubprocessStageRunner` constructed with `loaded_workflow=None` (e.g. legacy direct construction in a test) | `resolve_backend()` is called with `workflow=None`; the helper handles `None` by treating tier 2 as absent (still falls through to tier 3 / 4). Defensive: prevents test breakage if a test constructs the runner without a workflow. |
| User wants to add a third backend later (e.g. `codex`) | Append the class to `cli_backend.py`, extend `_KNOWN_BACKENDS` and `make_backend()`. No `Pipeline` / `Runner` / `CompositeStageRunner` changes. This is exactly the extension point §3.4 calls out. |

**Retry/fallback strategy:** None. Same stance as Phase 1 / 2 — backend dispatch failures are deterministic config / auth / network errors; the user re-runs after fixing.

---

## 8. Dependencies & Interfaces

| Dependency | Direction | Notes |
|---|---|---|
| Phase 1 + Phase 2 (merged on main) | hard, blocking | `StageSpec.backend`, `LoadedWorkflow.default_backend`, `SubprocessStageRunner`, `CompositeStageRunner` all present and tested. **No `T3.0` verification gate is needed** — Phases 1+2 are merged (unlike Phase 2's draft-against-not-yet-merged-Phase-1 banner). A quick sanity grep at T3.1 confirms the seams. |
| `cli_backend.py` → `atlas.orchestrator` | internal | Imports `StageOutcome`. |
| `cli_backend.py` → `atlas.workflow_loader` | internal | Imports `LoadedWorkflow` (for `resolve_backend()` signature). |
| `cli_backend.py` → `atlas.stages` | internal | Imports `StageSpec`. |
| `orchestrator.SubprocessStageRunner` → `atlas.cli_backend` | internal | Imports `make_backend`, `resolve_backend`, `UnknownBackendError`. Local-imported inside `run()` to avoid an import cycle (same pattern as `plugin_resolver` import today). |
| `cli.py::_make_pipeline()` → `atlas.cli_backend` (indirect) | internal | Threads `cfg.default_backend` and `loaded` into `SubprocessStageRunner`. |
| `agy` external binary | optional runtime | Phase 3 does NOT add `agy` to the install path. Users who want agy dispatch install it themselves per `headless-clis-reference.md` Part C. CI mocks `subprocess.run`, never spawns real `agy`. |
| `claude` external binary | unchanged | Already a runtime prerequisite from v1. |

No new PyPI dependencies. Phase 3 introduces only one new module (`cli_backend.py`) and one `Config` field.

---

## 9. Security Considerations

Carried from TRD-v2 §4 Security and Phase 1 / 2 §9, applied to Phase 3's surface:

- **Argv list-form throughout.** `ClaudeCodeBackend.build_argv()` and `AntigravityBackend.build_argv()` return `list[str]`; `SubprocessStageRunner.run()` passes that list directly to `subprocess.run(argv, ...)` with no `shell=True`. Same posture as Phase 1 / 2.
- **Backend allow-list is closed (`_KNOWN_BACKENDS = {"claude", "agy"}`).** A workflow YAML can specify `backend: <anything>` but only the two hardcoded names dispatch successfully — `make_backend()` is the gate, not the loader. Same trust pattern as Phase 2's `_REGISTRY` closed allow-list for `LIB:` references.
- **Antigravity auth fail-closed (TRD-v2 §4 Security).** `AntigravityBackend.preflight()` returns a failure tuple if neither `GEMINI_API_KEY` nor `GOOGLE_API_KEY` is set in `os.environ`. The subprocess is **never spawned** without one of these — preventing the documented "browser OAuth auto-opens on a desktop / prints a paste URL over SSH" failure mode (`headless-clis-reference.md` Part C). This is the maintainer-binding intent from TRD-v2 §4: "Do not silently fall back to browser auth."
- **Env-var reads happen exactly once per `run()` call**, inside `preflight()`. No caching, no mutation, no logging of the keys themselves. The key values are never embedded in argv, prompts, or `output_text`.
- **`ANTHROPIC_API_KEY` for Claude is unchanged.** `ClaudeCodeBackend` does not enforce its presence (Claude's own subprocess does that, surfacing via `plugin_nonzero_exit`). Document the env-var requirement in the auth guide (T3.7) but do not add a preflight check — Claude's existing behavior is acceptable, and adding the check would expand atlas's responsibility beyond what TRD-v2 §4 mandates (which is specifically about agy's failure mode).
- **`output_text` may contain LLM-generated content from either backend.** Same exposure surface as Phase 2; no new sensitive-data path.

---

## 10. Testing Strategy

Per TRD-v2 §10's coverage targets: `cli_backend.py` ≥ 85% (verbatim from §10). Carry-forward for existing modules.

### Unit tests — new file `tests/unit/test_cli_backend.py`

| Test | Validates |
|---|---|
| `test_claude_code_backend_argv_byte_identical_to_phase2` | `ClaudeCodeBackend.build_argv()` produces exactly the argv list `SubprocessStageRunner` constructed in Phase 2 (golden-string assertion on the joined argv). FR-8 byte-identity. |
| `test_claude_code_backend_parse_result_success` | `returncode=0`, `stdout="foo"` → `("success", "foo", None)`. |
| `test_claude_code_backend_parse_result_nonzero_exit` | `returncode=1`, `stdout="bar"` → `("failure", "bar", "plugin_nonzero_exit")`. |
| `test_claude_code_backend_preflight_is_none` | `ClaudeCodeBackend().preflight()` returns `None` (no env-var check). |
| `test_antigravity_backend_argv_uses_include_directories` | `AntigravityBackend.build_argv(add_dirs=[a, b])` argv contains `--include-directories a` AND `--include-directories b` (no `--add-dir`). |
| `test_antigravity_backend_argv_uses_output_format_json` | argv contains `--output-format json`. |
| `test_antigravity_backend_argv_default_model` | When `model=""` is passed, `argv` contains `gemini-flash-lite` (the constructor default). When `model="gemini-pro"` is passed, that value wins. |
| `test_antigravity_backend_parse_result_success_json` | `returncode=0`, valid `{"response": "ok"}` → `("success", "ok", None)`. |
| `test_antigravity_backend_parse_result_error_field` | `returncode=0` but `{"error": {"message": "rate limited"}}` → `("failure", "rate limited", "agy_response_error")`. |
| `test_antigravity_backend_parse_result_unparseable` | `returncode=0`, stdout is not JSON → `agy_unparseable_output`. |
| `test_antigravity_backend_parse_result_response_not_string` | `returncode=0`, `{"response": 42}` → `agy_response_not_string`. |
| `test_antigravity_backend_parse_result_input_error` | `returncode=42` → `agy_input_error`. |
| `test_antigravity_backend_parse_result_turn_limit` | `returncode=53` → `agy_turn_limit`. |
| `test_antigravity_backend_parse_result_general_error` | `returncode=1` → `agy_general_error`. Same for any other non-zero, non-42, non-53 code. |
| `test_antigravity_backend_preflight_no_env` | Monkeypatch both `GEMINI_API_KEY` and `GOOGLE_API_KEY` unset → `preflight()` returns `(msg, "agy_missing_auth_env")`; `msg` names both env vars. |
| `test_antigravity_backend_preflight_gemini_key_set` | Only `GEMINI_API_KEY` set → `preflight()` returns `None`. |
| `test_antigravity_backend_preflight_google_key_set` | Only `GOOGLE_API_KEY` set → `preflight()` returns `None`. |
| `test_resolve_backend_priority_order` | Table-driven: 16 cases covering all 4 tiers × `None`/value combinations. Per-stage `claude` + workflow `agy` → `claude`; per-stage `None` + workflow `agy` → `agy`; per-stage `None` + workflow `None` + config `"agy"` → `"agy"`; all `None` → `"claude"`. |
| `test_resolve_backend_handles_workflow_none` | `workflow=None` → tier 2 skipped, falls through to tier 3 / 4 without crash. |
| `test_make_backend_known_names` | `make_backend("claude")` returns a `ClaudeCodeBackend`; `make_backend("agy")` returns an `AntigravityBackend`. Both satisfy the `CliBackend` Protocol (`isinstance(b, CliBackend)` is `True` thanks to `@runtime_checkable`). |
| `test_make_backend_unknown_name_raises` | `make_backend("opus")` raises `UnknownBackendError` with a message listing `_KNOWN_BACKENDS`. |

### Unit tests — updated `tests/unit/test_subprocess_runner.py` (or equivalent)

| Test | Validates |
|---|---|
| `test_subprocess_runner_uses_claude_by_default` | Construct runner with no `loaded_workflow`/`default_backend`; mock `subprocess.run`; assert spawned argv starts with `["claude", ...]`. |
| `test_subprocess_runner_respects_stage_backend_field` | Mock `subprocess.run`; stage with `backend="agy"` and env vars set → argv starts with `["agy", ...]`. |
| `test_subprocess_runner_respects_workflow_default_backend` | Loaded workflow with `default_backend="agy"`, stage with `backend=None` → argv starts with `["agy", ...]`. |
| `test_subprocess_runner_unknown_backend_returns_failure` | Stage with `backend="nonsense"` → `StageOutcome(status="failure", error_type="unknown_backend")`. No exception bubbles up. |
| `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` | Stage `backend="agy"`, both env vars unset; assert `subprocess.run` is NEVER called (use `assert_not_called()`) AND `StageOutcome.error_type == "agy_missing_auth_env"`. This is the load-bearing security test. |

### Integration test — new file `tests/integration/test_cli_backend_dispatch.py`

| Test | Validates |
|---|---|
| `test_agy_dispatch_end_to_end_mocked` | Synthetic single-stage workflow with `backend: agy`; `subprocess.run` patched to return `CompletedProcess(returncode=0, stdout='{"response": "agy-said-this", "stats": {}}', stderr="")`; `GEMINI_API_KEY` env set; full `Pipeline.step()` runs; assert resulting `StageOutcome.output_text == "agy-said-this"`, span recorded in plumb, gate score namespaced correctly. FR-9. |
| `test_mixed_backend_workflow` | Synthetic 2-stage workflow: stage 1 `backend: claude` (mocked claude returning text), stage 2 `backend: agy` (mocked agy returning JSON); assert each stage spawned the correct binary, both reach the gate prompter, plumb span tree has both spans with correct kinds. |
| `test_dev_pipeline_unaffected_by_phase_3` | Re-run `test_e2e_happy_path.py` (the Phase 1/2 e2e regression test) unchanged. Asserts `claude` argv is unchanged from Phase 2 — `--no-session-persistence`, `--model`, `--add-dir` flags present; `--bare`, `--output-format json` ABSENT. FR-8. |
| `test_job_workflow_tailor_materials_dispatches_via_claude_backend` | `--workflow job` (content-pipeline mocked); `tailor_materials.backend: claude` is finally consumed; spawned argv is byte-identical to what Phase 2 produced for that stage. |

### Updated existing tests

| File | Change |
|---|---|
| `tests/unit/test_workflow_loader.py` | No change. `StageSpec.backend` parsing was already tested in Phase 1; Phase 3 only adds *consumption* of that field. |
| `tests/unit/test_composite_runner.py` | No change. CompositeStageRunner doesn't know about backends. |
| `tests/unit/test_config.py` (or equivalent — Phase 1 may have created this) | Add `test_config_default_backend_from_toml` (`[backend] default = "agy"` in `.atlas.toml` → `Config.default_backend == "agy"`) and `test_config_default_backend_fallback` (no `[backend]` section → `"claude"`). |
| `tests/e2e/test_e2e_happy_path.py` | Unchanged. Same regression-proof bar as Phase 1's T1.13 and Phase 2's T2.8. |

### Mocking strategy

- Backend unit tests mock at the `parse_result()` input boundary — pass synthetic stdout/stderr/returncode tuples; no real subprocess.
- Runner unit tests mock `subprocess.run` (via `monkeypatch.setattr("subprocess.run", ...)` or `unittest.mock.patch`). The argv list passed to the mock is the assertion surface.
- Integration tests mock `subprocess.run` to return canned `CompletedProcess` objects per backend. **No real `claude` or `agy` subprocess in CI** (TRD-v2 §13 #7).
- Env-var tests use `monkeypatch.delenv` / `setenv` (pytest fixture); never mutate real `os.environ` outside the fixture scope.

### Coverage target

`cli_backend.py` ≥ 85% (TRD-v2 §10 verbatim). `orchestrator.py` coverage may dip slightly if the `SubprocessStageRunner.run()` body is now smaller and exercised by fewer branches — fix by ensuring all preflight / unknown-backend paths are covered by the runner-level tests above. Full suite ≥ 80% (existing CI floor).

---

## 11. Performance Considerations

- **`build_argv()` / `parse_result()` are pure computation**, no I/O, < 1 ms each (TRD-v2 §4 NFR, FR / NFR-1). Verified by a synthetic timing test (`test_cli_backend_perf` — assert each call < 1 ms on a 1000-iteration loop).
- **`resolve_backend()` is pure computation**, no I/O. Inlining vs helper-function: kept as a helper for testability. Call cost negligible vs the subprocess dispatch.
- **`AntigravityBackend.preflight()` reads `os.environ`** — single dict lookup, < 1 µs. No file or network I/O.
- **No new caching needed.** Each backend instance is short-lived (constructed per `run()` call via `make_backend()`); the alternative — caching per-name backend instances — adds complexity for zero measurable gain in dispatch cost vs the subprocess spawn that follows.

---

## 12. Pending Decisions & Clarifications

The six items below were presented to the maintainer (2026-06-30); the user declined to triage individually, so this TRS proceeds with each item's **recommended default** (marked ✓). They remain open and the implementation should call out any of them in code review if they create friction.

### ✓ #1 — `parse_result()` output format strategy

**Decided default:** Plain-text for `ClaudeCodeBackend`; JSON (`--output-format json`) for `AntigravityBackend`. **Rationale:** Claude's text-mode stdout is what Phase 1's dev-pipeline parity claim depends on (the DEV-ESSENTIALS plugins print human-readable output that `gate_*` prompts surface). Switching Claude to JSON breaks FR-8. Antigravity, by contrast, **needs** JSON: its plain-text mode offers no error envelope (just a returncode and stderr), so robust failure classification (auth error vs rate limit vs turn limit vs network) requires the JSON `error` field.

**Alternatives the user can swap to:** (a) both backends on JSON (clean but changes dev pipeline UX); (b) both on plain-text (simplest but agy failures become opaque); (c) per-backend constructor flag (deferred — YAGNI for Phase 3). If the user prefers a different option, only `ClaudeCodeBackend.parse_result()` changes — the runner code is the same.

### ✓ #2 — Claude `--bare` flag

**Decided default:** Do NOT add `--bare`. Keep `--no-session-persistence` as today. **Rationale:** `--bare` skips auto-discovery of plugins / skills / `CLAUDE.md` / MCP — but the dev pipeline depends on DEV-ESSENTIALS plugins (`dev-docs-be`, `code-review`, `consult-experts`) being discovered. Adding `--bare` would break the dev pipeline. The TRD-v2 §3.4 table listing `--bare` as Claude's "CI determinism" mechanism appears to be a research-note recommendation from the source `headless-clis-reference.md` Part B; it's not a hard contract for atlas.

**Alternatives:** (a) opt-in `bare: bool = False` constructor flag (the workflow author opts in per-stage or per-config — non-breaking, low-cost — could be added in a follow-up if needed); (b) `--bare` unconditional + migrate dev pipeline off plugin discovery (large change; not justified by §14 Phase 3 goals).

### ✓ #3 — `.atlas.toml` `[backend]` schema

**Decided default:** Single `[backend] default = "claude"` key. **Rationale:** Smallest schema delta; matches TRD-v2 §3.4 wording exactly. Per-backend model knobs (`[backend.claude] model = ...`, `[backend.agy] model = ...`) are explicitly out of scope for Phase 3 — the existing top-level `model` field continues to serve Claude (Phase 2 behavior preserved), and `AntigravityBackend` carries its own default.

**Alternatives:** (a) per-CLI subtables (anticipates Phase 3+ growth but adds parsing surface for no immediate consumer); (b) single top-level `default_backend` field (deviates from §3.4's `[backend]` wording).

### ✓ #4 — `atlas run --backend <name>` CLI flag

**Decided default:** Do NOT add the flag. Stay with the 4-tier resolution from TRD-v2 §3.4 exactly. **Rationale:** YAGNI for Phase 3; non-breaking to add later. Phase 1 / 2 both held the line on flag additions ("only what the TRD specifies"). A CLI flag is a 5th tier above per-stage YAML; if a user needs ad-hoc backend overrides without editing config / YAML, they can use `--workflow-file <path>` with a one-off YAML.

**Alternatives:** (a) add `--backend` as the highest-priority tier (one Typer option in `cli.py`; small).

### ✓ #5 — `AntigravityBackend` default model

**Decided default:** `"gemini-flash-lite"`. **Rationale:** Matches Claude's `haiku` cost-efficient default; consistent with the free-tier `agy` allowance (~20 req/day on `gemini-flash-lite` per `headless-clis-reference.md` Part C). Per-stage / config overrides remain available.

**Alternatives:** (a) `"gemini-2.5-pro"` or equivalent — higher quality, lower free-tier budget; (b) no default — require explicit model (adds friction). Whatever model is chosen, it lives in `AntigravityBackend.__init__(default_model=...)` and can be swapped in one place.

### ✓ #6 — Backend module layout

**Decided default:** Single new file `src/atlas/cli_backend.py` containing `CliBackend` Protocol + both backend classes + `resolve_backend()` + `make_backend()` + `UnknownBackendError`. **Rationale:** Matches Phase 2's `composite_runner.py` precedent — one focused module per dispatch strategy. Total target ≤ 200 lines; easy to scan; coverage target (NFR-5) applies to one file cleanly.

**Alternatives:** (a) split per-backend files; (b) `backends/` subpackage. Both add structure for what's only two backends today; defer to the moment a third backend lands.

**Plus an additional load-bearing item:**

### ✓ #7 — Validate `backend` field at YAML load time?

**Decided default:** NO — validate at dispatch time only (in `make_backend()`). **Rationale:** Phase 1's loader is strict about everything that gates plumb's closed sets (span_kind), but `backend` is **not** plumb-affecting — it's a runtime-dispatch choice. Loading a workflow with `backend: opus` should succeed (so a workflow can be inspected / lint-checked offline); the error surfaces only when atlas tries to actually run that stage. This matches the existing posture for `tool: "RAW:..."` — Phase 1's loader doesn't validate prompt content, only structure.

**Alternative:** validate at load time. This would require adding `_KNOWN_BACKENDS` (or a per-backend allow-list registry) to `workflow_loader.py`, creating a coupling between the loader and `cli_backend.py` that Phase 1 deliberately avoided.


---

## Tasks

Flat list, ordered by execution sequence. Cross-task dependencies captured via `Dependencies`.

* **[T3.1] Sanity-check Phase 1 + Phase 2 seams** [Effort: S]
  - **Description**: Quick verification (not a hard gate — Phases 1 / 2 are merged on `main`, unlike Phase 2's draft-against-pre-Phase-1 situation). Grep-confirm: `StageSpec` has the `backend: str | None` field, `LoadedWorkflow.default_backend` exists, `SubprocessStageRunner` is the single subprocess dispatcher. Run the full existing suite once to establish the regression baseline.
  - **Acceptance Criteria**:
    - [ ] `grep -n "backend:" src/atlas/stages.py` confirms `StageSpec.backend: str | None`.
    - [ ] `grep -n "default_backend" src/atlas/workflow_loader.py` confirms `LoadedWorkflow.default_backend` is parsed.
    - [ ] `tests/unit/test_workflow_loader.py` passes (loader correctly parses `backend:` and `default_backend:` fields without consuming them).
    - [ ] Full existing test suite green (Phase 1's 153 + Phase 2's job-workflow tests + 3 e2e — count is informational, baseline for regression).
  - **Files to Create/Modify**: None — verification only.
  - **Dependencies**: Phase 1 + Phase 2 (merged on `main`)
  - **Testing Requirements**: Full existing suite re-run, no new tests

* **[T3.2] Author `cli_backend.py` — Protocol + both backends + helpers** [Effort: L]
  - **Description**: Implement `src/atlas/cli_backend.py` per §3.2: `CliBackend` Protocol (with `@runtime_checkable`), `ClaudeCodeBackend` (byte-identical argv to Phase 2's hardcoded path — verified by T3.3's argv parity test), `AntigravityBackend` (uses `--include-directories`, `--output-format json`, `gemini-flash-lite` default model, env-var preflight), `resolve_backend()` helper (4-tier resolution), `make_backend()` factory, `UnknownBackendError`, `_KNOWN_BACKENDS` frozenset. Pure code module; no orchestrator / cli changes yet.
  - **Acceptance Criteria**:
    - [ ] `src/atlas/cli_backend.py` exists; `mypy --strict src` passes.
    - [ ] `ClaudeCodeBackend()` and `AntigravityBackend()` both satisfy `isinstance(_, CliBackend)` (thanks to `@runtime_checkable`).
    - [ ] `_KNOWN_BACKENDS == frozenset({"claude", "agy"})`.
    - [ ] No new PyPI dependency added to `pyproject.toml`.
    - [ ] File size ≤ 200 lines (NFR-4).
  - **Files to Create/Modify**:
    - `src/atlas/cli_backend.py` - new
  - **Dependencies**: T3.1
  - **Testing Requirements**: None yet (tests in T3.3)

* **[T3.3] Unit-test `cli_backend.py`** [Effort: L]
  - **Description**: Implement all unit tests from §10's "Unit tests — new file" table (the ~20 tests covering argv construction, parse_result for each returncode/JSON-shape case, preflight env-var paths, resolve_backend priority table, make_backend known/unknown names). Hit the 85% coverage target (NFR-5 / TRD-v2 §10 verbatim).
  - **Acceptance Criteria**:
    - [ ] `pytest tests/unit/test_cli_backend.py -v` passes all listed tests.
    - [ ] `pytest --cov=src/atlas/cli_backend tests/unit/test_cli_backend.py` reports ≥ 85% coverage.
    - [ ] `test_claude_code_backend_argv_byte_identical_to_phase2` asserts the exact argv list Phase 2's hardcoded path produced (golden-string comparison) — proves FR-8 byte-identity for Claude.
    - [ ] `test_antigravity_backend_preflight_no_env` confirms preflight returns failure tuple WITHOUT touching `subprocess.run` (no I/O).
    - [ ] `test_resolve_backend_priority_order` covers all 4 tiers × None/value combos (table-driven).
  - **Files to Create/Modify**:
    - `tests/unit/test_cli_backend.py` - new
  - **Dependencies**: T3.2
  - **Testing Requirements**: Unit, ≥ 85% coverage on `cli_backend.py`

* **[T3.4] Refactor `SubprocessStageRunner` to use the strategy** [Effort: M]
  - **Description**: Modify `src/atlas/orchestrator.py::SubprocessStageRunner` per §3.3: add `default_backend: str = "claude"` and `loaded_workflow: LoadedWorkflow | None = None` constructor kwargs; replace the hardcoded `["claude", "-p", ...]` argv construction with `backend.build_argv(...)`; insert `backend.preflight()` call before subprocess; replace the post-subprocess returncode branching with `backend.parse_result(...)`. The subprocess invocation pattern (`subprocess.run(argv, cwd=..., capture_output=True, ...)`), `TimeoutExpired` handling, and `plugin_resolver` / `build_prompt` flow are all unchanged.
  - **Acceptance Criteria**:
    - [ ] Phase 1's existing `test_subprocess_runner_*` tests still pass without modification (FR-8 — dev pipeline byte-identity).
    - [ ] New tests from §10's "Unit tests — updated `test_subprocess_runner.py`" table all pass: `test_subprocess_runner_uses_claude_by_default`, `test_subprocess_runner_respects_stage_backend_field`, `test_subprocess_runner_respects_workflow_default_backend`, `test_subprocess_runner_unknown_backend_returns_failure`, `test_subprocess_runner_agy_missing_auth_returns_failure_no_subprocess` (the load-bearing security test — `subprocess.run` MUST NOT be called when auth env vars are missing).
    - [ ] `orchestrator.py` LoC delta is small (≤ 30 net lines added; the hardcoded argv block is replaced, not appended).
    - [ ] Local `from atlas.cli_backend import ...` is used inside `run()` to avoid an import cycle (same pattern as today's `plugin_resolver` local import).
  - **Files to Create/Modify**:
    - `src/atlas/orchestrator.py` - refactor `SubprocessStageRunner`
    - `tests/unit/test_subprocess_runner.py` (or `tests/unit/test_phase4.py` if that's where Phase 1's runner tests live) - add 5 new tests
  - **Dependencies**: T3.2, T3.3
  - **Testing Requirements**: Unit (subprocess.run mocked); existing Phase 1/2 runner tests must remain green

* **[T3.5] Extend `Config` with `default_backend`** [Effort: S]
  - **Description**: Add `default_backend: str = "claude"` field to `Config` (`src/atlas/config.py`). Extend `Config.load()` to read `[backend] default` from `.atlas.toml` (per §3.4); fall back to `"claude"` when the section is absent or malformed. Update `_make_pipeline()` in `cli.py` to pass `default_backend=cfg.default_backend` and `loaded_workflow=loaded` into `SubprocessStageRunner(...)` (§3.5).
  - **Acceptance Criteria**:
    - [ ] `Config.default_backend` exists; `Config.load(repo_root)` with no `.atlas.toml` returns `default_backend == "claude"`.
    - [ ] `Config.load(...)` with `[backend] default = "agy"` in `.atlas.toml` returns `default_backend == "agy"`.
    - [ ] `Config.load(...)` with a malformed `backend` section (e.g. `backend = "claude"` as a top-level string, not a table) defaults safely to `"claude"` (no crash).
    - [ ] `_make_pipeline()` threads both new kwargs into `SubprocessStageRunner` (grep-confirm).
    - [ ] Unit tests `test_config_default_backend_from_toml` + `test_config_default_backend_fallback` pass.
  - **Files to Create/Modify**:
    - `src/atlas/config.py` - add field + `[backend]` parsing
    - `src/atlas/cli.py` - `_make_pipeline()` wires the new kwargs
    - `tests/unit/test_config.py` - add 2 tests (or create if absent)
  - **Dependencies**: T3.4
  - **Testing Requirements**: Unit

* **[T3.6] Integration test — `agy` dispatch end-to-end (mocked)** [Effort: M]
  - **Description**: Implement `tests/integration/test_cli_backend_dispatch.py` per §10's "Integration test" table. The four tests: `test_agy_dispatch_end_to_end_mocked` (FR-9 demonstration), `test_mixed_backend_workflow` (per-stage override + workflow default coexist), `test_dev_pipeline_unaffected_by_phase_3` (re-runs `test_e2e_happy_path.py` as a smoke check + asserts the claude argv hasn't gained `--bare` or `--output-format json`), `test_job_workflow_tailor_materials_dispatches_via_claude_backend` (Phase 2's parsed-but-inert `backend: claude` field is now correctly consumed).
  - **Acceptance Criteria**:
    - [ ] All 4 tests pass.
    - [ ] `test_agy_dispatch_end_to_end_mocked` verifies the full `Pipeline.step()` flow: stage runs, span recorded, gate score namespaced — confirming the `agy` path produces the same plumb instrumentation as the `claude` path (Pipeline-level invariant).
    - [ ] `test_dev_pipeline_unaffected_by_phase_3` includes assertions that the claude argv does NOT contain `--bare` or `--output-format` (negative assertions, locking in Resolved Decisions #1 and #2).
    - [ ] `tests/e2e/test_e2e_happy_path.py` passes unchanged (no file edits required — same proof-bar Phase 1's T1.13 and Phase 2's T2.8 set).
  - **Files to Create/Modify**:
    - `tests/integration/test_cli_backend_dispatch.py` - new
  - **Dependencies**: T3.4, T3.5
  - **Testing Requirements**: Integration (`subprocess.run` mocked throughout; no real CLI binaries in CI)

* **[T3.7] Document per-CLI auth requirements + `agy` experimental status** [Effort: S]
  - **Description**: Per TRD-v2 §14 Phase 3's scope bullet "Document per-CLI auth requirements and the experimental status of `agy` support", add a short guide doc at `docs/3_guides/cli_backends.md`. Cover: (a) how to pick a backend (per-stage YAML / workflow default / `.atlas.toml` / hard default — the 4-tier order from §3.4); (b) Claude auth (`ANTHROPIC_API_KEY`); (c) Antigravity auth (`GEMINI_API_KEY` or `GOOGLE_API_KEY`, fail-closed if neither set, link to `headless-clis-reference.md` Part C); (d) `agy` is **experimental** until headless API-key auth stabilizes (TRD-v2 §5 caveat — Gemini CLI retired 2026-06-18; agy issue #78 contested). Cross-link `cli-backend-dispatch.md` and `headless-clis-reference.md`.
  - **Acceptance Criteria**:
    - [ ] `docs/3_guides/cli_backends.md` exists.
    - [ ] Doc names both env vars for agy and the failure-mode message format (`agy_missing_auth_env`).
    - [ ] Doc states `agy` support is experimental and links to TRD-v2 §5 / `headless-clis-reference.md` Part C's auth-status note.
    - [ ] Doc includes a worked example: a one-stage workflow YAML with `backend: agy` and the expected `.atlas.toml` shape.
    - [ ] No code changes in this task.
  - **Files to Create/Modify**:
    - `docs/3_guides/cli_backends.md` - new
  - **Dependencies**: T3.2 (for accurate behavior to document)
  - **Testing Requirements**: None (docs)

* **[T3.8] Manual smoke test against a real `agy` binary (off-CI)** [Effort: S]
  - **Description**: One-shot manual verification, **not gated by CI** (TRD-v2 §13 #7: "real dispatch in manual testing if auth allows"). Install `agy` locally, export `GEMINI_API_KEY`, author a throwaway one-stage workflow with `backend: agy` and `tool: "RAW:Write a haiku about pipelines."`. Run `atlas run "<task>" --workflow-file /tmp/agy-test.yaml --auto-approve`. Capture the output and append it to this TRS's tasks file as evidence. If `agy` auth is broken at run time (per the issue-#78 contested-headless-auth caveat), document the failure mode and skip the live run — the mocked CI tests remain the primary proof of correctness, and §13 #7's wording explicitly allows "manual testing if auth allows."
  - **Acceptance Criteria**:
    - [ ] Attempted at least once; result (success or auth-failure observation) appended to `yaml-workflow-engine-phase-3-tasks.md` as a dated note.
    - [ ] If the live run succeeds: paste the truncated `output_text` and the `span_id` from plumb into the tasks file.
    - [ ] If the live run fails on auth: paste the exact error text and confirm it matches the `agy_missing_auth_env` shape from `AntigravityBackend.preflight()` OR the post-subprocess failure path (depending on which fired).
  - **Files to Create/Modify**:
    - `dev/active/yaml-workflow-engine-phase-3/yaml-workflow-engine-phase-3-tasks.md` - append result note
  - **Dependencies**: T3.6, T3.7
  - **Testing Requirements**: Manual; not CI-gated

* **[T3.9] CI lint / type-check pass** [Effort: S]
  - **Description**: Run `ruff check`, `ruff format --check`, `mypy --strict src` against the full repo after Phase 3 lands. Fix any lint / type errors introduced by the new module or the refactor. Confirm coverage gate (≥ 80% repo-wide, ≥ 85% on `cli_backend.py`) passes.
  - **Acceptance Criteria**:
    - [ ] `ruff check src tests` is clean.
    - [ ] `ruff format --check src tests` is clean.
    - [ ] `mypy --strict src` is clean.
    - [ ] `pytest --cov=src/atlas --cov-fail-under=80` passes.
    - [ ] Per-file coverage report shows `cli_backend.py` ≥ 85%.
  - **Files to Create/Modify**: Any lint / type fixes that surface.
  - **Dependencies**: T3.3, T3.4, T3.5, T3.6
  - **Testing Requirements**: CI green on all 4 gates

* **[T3.10] Update `STATUS.md` + tag `v2.2`** [Effort: S]
  - **Description**: Mark Phase 3 complete in `STATUS.md` (per TRD-v2 §11, this phase tags as `v2.2 — CLI backend dispatch with Antigravity`). Add a short "Phase 3 done" entry under "Recent" describing what shipped (CliBackend Protocol + Claude + Antigravity backends + 4-tier resolution + auth fail-closed). Tag the release: `git tag v2.2 && git push origin v2.2` (user-discretionary, not part of this task's acceptance — flagged here so it isn't forgotten).
  - **Acceptance Criteria**:
    - [ ] `STATUS.md`'s `## Current` block reflects Phase 3 completion.
    - [ ] `STATUS.md`'s `## Recent` lists the Phase 3 deliverables.
    - [ ] (Optional, user-driven) `v2.2` tag created.
  - **Files to Create/Modify**:
    - `STATUS.md` - update
  - **Dependencies**: T3.9
  - **Testing Requirements**: None (documentation)

---

## Phase Deliverables

- `src/atlas/cli_backend.py` ships with `CliBackend` Protocol + `ClaudeCodeBackend` + `AntigravityBackend` + `resolve_backend()` + `make_backend()` + `UnknownBackendError`. ≤ 200 lines.
- `SubprocessStageRunner` refactored to a thin shell that owns subprocess invocation + timeout; argv construction and result parsing delegated to the backend strategy.
- Backend selection follows TRD-v2 §3.4's 4-tier resolution: per-stage `StageSpec.backend` → workflow `LoadedWorkflow.default_backend` → `Config.default_backend` (new field, from `.atlas.toml [backend] default`) → hard default `"claude"`.
- `AntigravityBackend` enforces `GEMINI_API_KEY` or `GOOGLE_API_KEY` presence via `preflight()` before `subprocess.run()` — no silent browser-OAuth fallback (TRD-v2 §4 Security; §13 #7's "clear error, not a silent hang").
- Dev pipeline runs byte-identically to Phase 2 (`ClaudeCodeBackend.build_argv()` is golden-string-equal to today's hardcoded argv; `--bare` and `--output-format json` deliberately absent — see Resolved Decisions #1 + #2).
- Job pipeline's `tailor_materials.backend: claude` field (parsed-but-inert in Phase 2) is finally consumed.
- ≥ 85% coverage on `cli_backend.py`; ≥ 80% repo-wide.
- `ruff check`, `ruff format --check`, `mypy --strict src` all green.
- Documentation: `docs/3_guides/cli_backends.md` covers per-CLI auth, the 4-tier resolution, and `agy`'s experimental status.
- Integration test demonstrates `agy` dispatch end-to-end (mocked subprocess); dev / job pipelines unaffected.
- Manual smoke test against a real `agy` binary attempted (T3.8); outcome documented even if auth blocks the live run.

---

## Resolved Decisions

The seven items below are this TRS's binding design choices. Items #1–6 are the user-skipped questions from the pre-drafting clarification round; #7 was identified during drafting. All are settled and bind implementation. If the user wants to override one, this section is where to flag the change.

1. **`parse_result()` output format → text for Claude; JSON for Antigravity.** Preserves dev-pipeline byte-identity (FR-8); robust agy failure classification (auth vs quota vs turn-limit) requires the JSON `error` field. See Pending Decisions §12 #1 for the alternative table. Binding on T3.2, T3.3.
2. **Do NOT add `--bare` to Claude argv.** `--bare` would skip DEV-ESSENTIALS plugin discovery the dev pipeline relies on. `--no-session-persistence` is retained as the only "CI determinism" flag for Claude. See §12 #2. Binding on T3.2.
3. **`.atlas.toml [backend] default = "claude"` is the schema.** Per-backend model subtables (e.g. `[backend.agy] model = ...`) explicitly deferred. Existing top-level `model` field serves Claude (Phase 2 behavior preserved); `AntigravityBackend` carries its own default. See §12 #3. Binding on T3.5.
4. **No `atlas run --backend <name>` CLI flag in Phase 3.** Stay with §3.4's 4-tier resolution exactly. Adding a flag later is non-breaking. See §12 #4. Binding on T3.5.
5. **`AntigravityBackend` default model = `"gemini-flash-lite"`.** Matches Claude's `haiku` cost-efficient default; fits the documented `agy` free-tier allowance. See §12 #5. Binding on T3.2.
6. **Single new file `src/atlas/cli_backend.py`** for the Protocol + both backends + helpers. Matches Phase 2's `composite_runner.py` precedent. See §12 #6. Binding on T3.2.
7. **`backend` field NOT validated at YAML load time.** Phase 1's loader knows nothing about `_KNOWN_BACKENDS`. An unknown backend name fails at dispatch (`make_backend()` → `UnknownBackendError`), not at load. Matches the existing `RAW:` posture (loader validates structure, not tool content). Binding on T3.2 (no loader changes).

---

## Appendix A — Phase 2 → Phase 3 seam inventory

The minimal set of code locations Phase 3 touches:

| File | Change |
|---|---|
| `src/atlas/cli_backend.py` | **NEW.** Protocol + Claude + Antigravity + helpers. |
| `src/atlas/orchestrator.py` | `SubprocessStageRunner.__init__` gains `default_backend` + `loaded_workflow` kwargs; `SubprocessStageRunner.run()` replaces hardcoded argv block with `backend.build_argv()` + `backend.preflight()` + `backend.parse_result()`. ~30 net lines added. |
| `src/atlas/config.py` | `Config.default_backend: str = "claude"` field; `Config.load()` reads `[backend] default`. |
| `src/atlas/cli.py` | `_make_pipeline()` threads `cfg.default_backend` and `loaded` into `SubprocessStageRunner(...)`. ~2 line change. |
| `tests/unit/test_cli_backend.py` | **NEW.** ~20 tests covering argv / parse_result / preflight / resolve. |
| `tests/unit/test_subprocess_runner.py` (or `test_phase4.py`) | +5 tests for backend wiring + auth-fail security path. |
| `tests/unit/test_config.py` | +2 tests for `[backend] default` parsing. |
| `tests/integration/test_cli_backend_dispatch.py` | **NEW.** 4 integration tests. |
| `docs/3_guides/cli_backends.md` | **NEW.** Per-CLI auth + experimental-status doc. |
| `STATUS.md` | Phase 3 completion update. |

**Files NOT touched** (verify, don't modify):
- `src/atlas/workflow_loader.py` — loader unchanged (Resolved Decision #7).
- `src/atlas/stages.py` — `StageSpec.backend` was added in Phase 1; Phase 3 only consumes it.
- `src/atlas/state.py` — no state-file shape changes.
- `src/atlas/composite_runner.py`, `library_runner.py`, `library_adapters/*` — Phase 2 work unchanged.
- `src/atlas/plugin_resolver.py`, `plumb_io.py`, `post_commit_hook.py`, `worktree.py` — all unchanged.
- `src/atlas/workflows/*.yaml` — no YAML changes; existing `tailor_materials.backend: claude` already correct.
- `tests/e2e/test_e2e_happy_path.py` — runs unmodified (regression proof).
- `routing_ground_truth.json` — dev pipeline unchanged.

If implementation finds any file outside the "touched" list above genuinely needs editing, that's a signal the design has drifted from this TRS — pause and reconcile before proceeding.

