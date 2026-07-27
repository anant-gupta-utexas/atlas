# TRS — Log Rotation for `.atlas/runs/*.log`

**Project:** atlas — v1 local CLI (operational backlog item)
**Component:** new `run_logging.py`, plus small additions to `config.py`, `cli.py`
**Status:** Draft, pre-implementation
**Last reviewed:** 2026-07-26
**Grounds on:** [v1 TRD](../../../docs/2_architecture/TRD.md) §"Deployment & Operations Requirements" (Logging), [system_design.md](../../../docs/2_architecture/system_design.md) (atlas-owned on-disk state table), [STATUS.md](../../../STATUS.md) (v1.1 backlog)

> This TRS details a standalone operational backlog item, not one of [TRD-v2](../../../docs/2_architecture/TRD-v2.md)'s four Development Phases (Engine generalization / Job workflow / CLI backend dispatch / Second-brain trigger). Log rotation is orthogonal to the YAML-workflow-engine generalization work — it does not touch `workflow_loader.py`, `stages.py`, or any Phase 1–3 seam from TRD-v2 Appendix A. Its governing spec text is the v1 TRD's own Logging paragraph, which explicitly deferred rotation to a "v1.1 backlog" item. This TRS closes that backlog item. See "Pending Decisions" for why it is scoped as a self-contained unit rather than folded into TRD-v2.

---

## Phase Summary

**Origin:** GitHub issue "Log rotation for .atlas/runs/*.log" (BACKLOG). Not a TRD-v2 phase; no TRD phase renumbering or re-sequencing is proposed by this TRS.
**PRD release(s) delivered:** None directly. This is operational debt paydown, not a user-facing release feature. It removes a launch caveat from the v1 TRD ("No rotation in v1... track in v1.1 backlog") and can ship in any v1.x patch.
**Goal:** Give `.atlas/runs/<run_id>.log` a bounded-disk-usage retention policy, decide when rotation runs, and make the in-flight-run/resume interaction safe (never delete the log of a run still in progress).

---

## 1. Overview & Scope

### Headline finding from codebase research (read before planning further)

**The run-scoped log writer does not exist yet.** `grep` across `src/atlas/` for `FileHandler`, `basicConfig`, or any `.atlas/runs/` writer turns up nothing. The only `logging.getLogger(...)` call in the package is `src/atlas/plumb_io.py:13` (`_logger = logging.getLogger("atlas.plumb")`), which has no handler attached anywhere, so today it silently falls through to Python's `lastResort` (stderr) — no file is ever written. `.atlas/runs/<run_id>.log` is a **documented-but-unimplemented** v1 feature (TRD.md:194-197, system_design.md's on-disk-state table).

This changes the shape of the task: it is not "add rotation to an existing writer," it is "implement a minimal run-scoped log writer, with rotation built in from day one" — matching how the TRD's own backlog note frames it ("a rotation policy lands when disk usage becomes a real problem"). The alternative — implement rotation logic against a writer that doesn't exist — would be untestable. See Pending Decision #3 for the explicit boundary on how much writer functionality this phase includes.

### In scope

- A minimal run-scoped log writer: attach a `logging.FileHandler` to the `"atlas"` logger namespace for the lifetime of a run, writing to `.atlas/runs/<run_id>.log`. This is infrastructure/scaffolding — it does **not** instrument `SubprocessStageRunner` or any stage to pipe subprocess stdout/stderr into the log (see Pending Decision #3).
- A retention policy: **age-based primary, count-based safety ceiling** (justified below).
- A rotation trigger point: **on run start** — i.e., at the top of both `atlas run` and `atlas resume`, before the pipeline does anything else.
- The in-flight/resume safety invariant: rotation must never delete the log file for the `run_id` currently recorded in `.atlas/current-run`.
- Two new config keys (`log_retention_days`, `log_max_count`) in the existing flat-TOML-key `Config` shape.
- An optional `atlas log prune` command for manual on-demand invocation (mirrors the `atlas hook install`/`uninstall` convenience-command precedent).
- Unit + integration tests; doc updates to TRD.md and system_design.md reflecting the now-implemented behavior.

### Out of scope

- Piping stage/subprocess stdout or stderr into the run log (a separate, larger instrumentation task — see Pending Decision #3).
- Any TRD-v2 YAML-workflow-engine seam (`workflow_loader.py`, `dev.yaml`, per-workflow metric namespacing). Multiple workflows (`dev`, future `job.yaml`) sharing `.atlas/runs/` is a real future condition (TRD-v2 §3.8) but the `run_id` is already globally unique (32-char hex or plumb's opaque id per `plumb_io.py:312-314`), so no workflow-name prefixing is needed for this issue's stated acceptance criteria.
- Concurrent-process locking. v1/v2 are both explicitly single-run-per-repo (PRD "Assumptions"; TRD-v2:27 "v2 does NOT include... concurrent runs"). Rotation code assumes single-writer, no file-lock needed.
- Fixing the pre-existing `[plumb]`-table-vs-flat-key documentation/code mismatch in `config.py` (noted for awareness in §4, not touched here — out of this issue's stated acceptance criteria).
- A daemon or background tick of any kind (see §"Algorithm & Logic Design" for why this option is architecturally unavailable, not merely unchosen).

### Why this scope

The issue's three planning bullets (retention policy, trigger point, resume interaction) all presuppose a log file that can be opened, rotated, and protected. Scaffolding the minimal writer is the smallest addition that makes those three decisions testable; going further (full subprocess-output instrumentation) would pull in `SubprocessStageRunner`, `post_commit_hook.py`, and orchestrator changes well beyond "log rotation."

---

## 2. Requirements Summary

- **FR-1** — `atlas run` and `atlas resume` each perform a rotation pass over `.atlas/runs/*.log` before starting/continuing pipeline work.
- **FR-2** — Rotation deletes any `*.log` file whose modification time is older than `log_retention_days` (default 14), **except** the file matching the `run_id` currently recorded in `.atlas/current-run`, if any.
- **FR-3** — After the age-based pass, if more than `log_max_count` (default 500) files remain, the oldest-by-mtime survivors are deleted until the count ceiling is met. The protected current-run file is still exempt.
- **FR-4** — A new run/resume opens (or re-opens) `.atlas/runs/<run_id>.log` via a `logging.FileHandler` attached to the `"atlas"` logger, closed/detached when the process's `run_to_completion` call returns (success, failure, or `KeyboardInterrupt`).
- **FR-5** — `Config` gains `log_retention_days: int = 14` and `log_max_count: int = 500`, parsed the same way existing flat scalar keys (`model`, `plumb_db_path`) are parsed — no new nested TOML table.
- **FR-6** — (Should-have) `atlas log prune` runs the same rotation pass on demand, printing a count of files removed.
- **FR-7** — Resume that mints a new child `run_id` (via `plumb.reopen_run`, `orchestrator.py:175`) opens a **new** log file for the child id; the parent id's log is no longer protected on subsequent rotations and becomes ordinarily eligible for age/count-based cleanup (documented behavior, not a bug — see §6 Algorithm design, "Resume interaction").

### Non-functional

- **NFR-1** — Rotation pass adds negligible latency to `atlas run`/`atlas resume` startup: a single `glob("*.log")` + `stat()` per file is O(files-in-dir); at `log_max_count` default (500) this is sub-10ms on a warm SSD, well inside the existing `atlas status` <500ms budget (TRD.md's closest analogous NFR — rotation isn't itself NFR'd in the TRD but should not regress the feel of `atlas run` startup).
- **NFR-2** — Best-effort deletion: a single file's `unlink()` failure (permissions, already-removed) logs a warning via the existing `atlas.plumb`-style logger convention and does not abort the run (mirrors the Reliability NFR pattern already established for post-commit-hook parsing: "on parse failure, log and continue").
- **NFR-3** — New module (`run_logging.py`) targets ~60–90 lines, consistent with the repo's per-module leanness (`config.py` is 71 lines; this is a comparable-scope module).
- **NFR-4** — `ruff check`, `ruff format --check`, `mypy src` stay green (existing CI gates, TRD.md §"Quality Assurance Requirements").

---

## 3. Detailed Component Design

### 3.1 Module structure (additions only)

```
src/atlas/
├── run_logging.py      # NEW — open/close run log, rotate_run_logs()
├── config.py            # + log_retention_days, log_max_count fields
└── cli.py                 # wire rotation + log-open into run/resume; + `log prune` command
```

### 3.2 Data structures / signatures

```python
# src/atlas/run_logging.py
"""Run-scoped log file lifecycle: open, close, rotate .atlas/runs/*.log."""

from __future__ import annotations

import logging
import time
from pathlib import Path

_logger = logging.getLogger("atlas.run_logging")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_SECONDS_PER_DAY = 86_400


def open_run_log(runs_dir: Path, run_id: str) -> logging.Handler:
    """Attach a FileHandler for this run_id to the 'atlas' logger. Caller must
    close_run_log() the returned handler when the run ends."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(runs_dir / f"{run_id}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logging.getLogger("atlas").addHandler(handler)
    return handler


def close_run_log(handler: logging.Handler) -> None:
    logging.getLogger("atlas").removeHandler(handler)
    handler.close()


def rotate_run_logs(
    runs_dir: Path,
    *,
    protect_run_id: str | None,
    max_age_days: int,
    max_count: int,
) -> list[Path]:
    """Delete old .log files under runs_dir. Never deletes protect_run_id's file.
    Returns the list of paths actually removed (best-effort; a per-file failure
    is logged and skipped, not raised)."""
    if not runs_dir.exists():
        return []

    now = time.time()
    max_age_s = max_age_days * _SECONDS_PER_DAY
    deleted: list[Path] = []
    survivors: list[tuple[Path, float]] = []

    for path in runs_dir.glob("*.log"):
        if path.stem == protect_run_id:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if now - mtime > max_age_s:
            _safe_unlink(path, deleted)
        else:
            survivors.append((path, mtime))

    if len(survivors) > max_count:
        survivors.sort(key=lambda pair: pair[1])  # oldest mtime first
        excess = len(survivors) - max_count
        for path, _mtime in survivors[:excess]:
            _safe_unlink(path, deleted)

    return deleted


def _safe_unlink(path: Path, deleted: list[Path]) -> None:
    try:
        path.unlink()
        deleted.append(path)
    except OSError as exc:
        _logger.warning("failed to remove old run log %s: %s", path, exc)
```

```python
# src/atlas/config.py — additions to the existing dataclass (config.py:10-16)
@dataclass(frozen=True)
class Config:
    repo_root: Path
    plumb_db_path: Path
    plugin_commands: dict[str, str] = field(default_factory=dict)
    timeout_overrides: dict[str, int] = field(default_factory=dict)
    model: str = "haiku"
    log_retention_days: int = 14   # NEW
    log_max_count: int = 500        # NEW
```

`Config.load()` (config.py:19-57) gains two lines in the `merged` defaults dict and two lines in the final `cls(...)` construction, following the exact pattern already used for `model` (a flat scalar, `str(merged.get(...))`-style coercion) — no new nested TOML table, no new merge helper.

### 3.3 CLI wiring (cli.py)

```python
# cli.py — new shared helper, called from both `run` and `resume` before
# pipeline.start()/resume()
def _rotate_before_start(repo_root: Path, cfg: Config, state: StateStore) -> Path:
    runs_dir = repo_root / ".atlas" / "runs"
    pair = state.read_current_run()
    protect = pair[0] if pair is not None else None
    rotate_run_logs(
        runs_dir,
        protect_run_id=protect,
        max_age_days=cfg.log_retention_days,
        max_count=cfg.log_max_count,
    )
    return runs_dir
```

`run()` (cli.py:61-94) and `resume()` (cli.py:97-126) each: build `state = StateStore(repo_root)` (already implicitly available via `_make_pipeline`, but rotation needs it *before* `_make_pipeline`/`pipeline.start()` runs, so it's constructed once up front and passed in, rather than duplicated inside `_make_pipeline`) → call `_rotate_before_start(...)` → call `pipeline.start()`/`.resume()` to get `ctx` → `handler = open_run_log(runs_dir, ctx.run_id)` → wrap the existing `pipeline.run_to_completion(ctx)` try/except block in a `try/finally: close_run_log(handler)`.

`atlas log prune` — new Typer command, reuses `_rotate_before_start`-equivalent logic directly (no pipeline construction needed):

```python
@app.command("log-prune")
def log_prune() -> None:
    """Manually remove old .atlas/runs/*.log files per the retention policy."""
    repo_root = _find_repo_root()
    cfg = Config.load(repo_root)
    state = StateStore(repo_root)
    pair = state.read_current_run()
    protect = pair[0] if pair is not None else None
    deleted = rotate_run_logs(
        repo_root / ".atlas" / "runs",
        protect_run_id=protect,
        max_age_days=cfg.log_retention_days,
        max_count=cfg.log_max_count,
    )
    typer.echo(f"Removed {len(deleted)} old run log(s).")
```

---

## 4. API Specifications

Not applicable — atlas has no network API surface (v1 TRD Security NFR: "No network listener"). This feature is CLI + filesystem only. The "API" here is the internal `run_logging.py` module boundary specified above.

**Config surface (the closest thing to a public contract):**

| Key | Type | Default | Table |
|---|---|---|---|
| `log_retention_days` | int | `14` | top-level, flat (like `model`) |
| `log_max_count` | int | `500` | top-level, flat (like `model`) |

Note for the record: PRD.md:213-225 and getting_started.md show an aspirational nested `[plumb]` table shape that `config.py` never actually reads (it reads a flat `plumb_db_path` key instead — confirmed at config.py:27,53). This TRS follows the **actual code pattern** (flat keys), not the aspirational docs pattern, to avoid adding a second inconsistency on top of the existing one. See Pending Decision #4.

---

## 5. Database Design

Not applicable. No atlas-owned schema (TRD.md:131, "No atlas-owned schema. All structured data goes through plumb."). Run logs are flat files on disk; retention state is derived from filesystem mtimes at rotation time, not persisted anywhere.

---

## 6. Algorithm & Logic Design

### Retention policy: age-based primary + count-based ceiling (decision + justification)

The issue asks to pick among age-based, count-based, or size-based and justify it.

| Policy | Pro | Con for this tool |
|---|---|---|
| Count-based only (keep last N) | Simplest; single sort+slice | A quiet week followed by a burst of resumes (each resume can mint a fresh child `run_id`, `orchestrator.py:175`) could evict a log from hours ago purely because N other files exist; doesn't track "how long ago" at all, which is the actual thing a developer cares about when deciding whether to keep a debug log |
| Size-based (cap total dir bytes, evict oldest first) | Most directly targets the actual named pain ("disk usage") | Requires summing every file's size on every rotation pass and reasoning about eviction order under a byte budget — more code and more edge cases (what if one file alone exceeds the cap?) for a solo-developer tool whose real run volume is low (gate-based, human-bounded, "minutes not milliseconds" per TRD.md §Performance) |
| **Age-based (primary) + count ceiling (safety net)** | Matches how a developer actually revisits logs — "did I run into this in the last two weeks?" — directly; count ceiling is a cheap backstop against a pathological single-day burst (e.g., a scripted test loop) blowing up file count even within the age window | Doesn't directly bound disk bytes if individual log files are unexpectedly huge — acceptable given no subprocess-output instrumentation is in scope (§1), so per-file size stays small in this phase |

**Decision:** age-based is the primary axis (default 14 days — long enough to cover a multi-day gated task that pauses between human reviews, short enough that a solo dev's `.atlas/runs/` doesn't become a graveyard), with a count ceiling (default 500) purely as an inexpensive guard rail. This mirrors the well-worn `logrotate` pattern of combining a time window with a rotation-count cap, without requiring size summation. If real disk usage becomes a problem once subprocess-output instrumentation lands (an out-of-scope future task), a size-based cap can be layered on top of the same `rotate_run_logs()` seam later — this design doesn't foreclose that.

### Rotation trigger: on run start (not a daemon tick, not exclusively a separate command)

The issue frames this as a three-way choice: run start / daemon tick / separate command.

- **Daemon tick is not actually available as an option.** Atlas has no background process anywhere in its architecture — v1 is "sync-only... no async/await" (CLAUDE.md), and TRD-v2 explicitly keeps "no HTTP shell... no concurrent runs" out of scope even in v2. Introducing a daemon solely to tick a log-rotation timer would be new standing infrastructure this project has deliberately avoided everywhere else; it fails the "state machine, not a framework" test as clearly as any hypothetical new router module would.
- **On run start** (both `atlas run` and `atlas resume`) is chosen as the trigger, because it's a touchpoint that already exists on every invocation that could possibly produce a new log file, requires no new scheduling primitive, and mirrors the existing pattern of `.atlas/current-run` being written as a start-of-run side effect (`orchestrator.py:138`).
- **`atlas log prune`** is added anyway as a Should-have manual escape hatch (mirrors `atlas hook install`/`uninstall`) for a user who wants to reclaim disk without starting a new run — see Pending Decision #2 for whether to cut this to shrink scope further.

### Resume interaction (the specific case the issue calls out)

Sequence for `atlas resume`:

1. `state.read_current_run()` → `(run_id, slug, worktree_path, code_gen_span_id)` or `None`.
2. `rotate_run_logs(runs_dir, protect_run_id=run_id, ...)` — the log for the run about to be resumed is exempt from this pass regardless of its age (a task paused for 3 weeks between human gates must not have its log silently deleted out from under it).
3. `pipeline.resume()` runs. Internally, `orchestrator.py:175` may call `plumb.reopen_run(run_id)`, which can return a **different** `active_run_id` (child run) — `orchestrator.py:179-180` then rewrites `tasks.md`'s run_id comment via `state.update_run_id()`. `.atlas/current-run` is rewritten to the new id (`orchestrator.py:182` region).
4. `open_run_log(runs_dir, ctx.run_id)` opens a **new** log file keyed to the (possibly new) `ctx.run_id`.
5. The **old** (parent) run_id's log file is now unprotected on any future rotation pass — it will age out normally like any closed run's log.

This is a deliberate design choice, not an oversight: the log file is a debugging aid, not the system of record (plumb owns that, per TRD.md:131). Once a run_id is superseded by its child, the parent's log has the same claim to survival as any other completed run's log — i.e., governed by the same age/count policy, no special "orphan" carve-out. This is called out explicitly so a future reader doesn't mistake the parent log's eventual deletion for a bug.

### Pseudocode summary (both CLI entry points)

```
on `atlas run` / `atlas resume`:
    state = StateStore(repo_root)
    protect = state.read_current_run()[0] if exists else None
    rotate_run_logs(runs_dir, protect_run_id=protect,
                     max_age_days=cfg.log_retention_days,
                     max_count=cfg.log_max_count)
    ctx = pipeline.start(...) / pipeline.resume()
    handler = open_run_log(runs_dir, ctx.run_id)
    try:
        pipeline.run_to_completion(ctx)
    finally:
        close_run_log(handler)
```

---

## 7. Error Handling & Edge Cases

| Case | Behavior |
|---|---|
| `.atlas/runs/` doesn't exist yet (first-ever run) | `rotate_run_logs` returns `[]` immediately; `open_run_log` creates the directory via `mkdir(parents=True, exist_ok=True)` |
| Directory contains non-`.log` files | `glob("*.log")` only ever considers `.log` files; anything else is untouched |
| Individual file `unlink()` raises `OSError` (permissions, already removed by another process) | Logged as a warning via `_logger.warning`, loop continues; run is **not** blocked (mirrors TRD.md's post-commit-hook "log and continue" reliability pattern) |
| `.atlas/current-run` missing or malformed when rotating | `state.read_current_run()` returns `None` per existing behavior (`state.py:114-119`); `protect_run_id=None`, rotation proceeds with no exemption — correct, since there is genuinely no run to protect |
| File mtime is in the future (clock skew) | `now - mtime` is negative, which is never `> max_age_s` (a positive number) — the file survives the age pass by construction; no special-casing needed |
| More survivors than `max_count` even after age eviction | Oldest-by-mtime survivors deleted down to the ceiling, protected file still exempt |
| `run_id` collides with an existing `.log` filename (e.g., re-run after a crash reused a stub id) | Out of scope for this issue — `_make_id()` (`plumb_io.py:312-314`) already guarantees 128 bits of entropy; collision is not a realistic edge case this TRS needs to defend against |
| Timeout/non-zero exit from a stage mid-run (`SubprocessStageRunner`) | Unaffected by this change — `close_run_log` runs in the `finally` block regardless of how `run_to_completion` exits, so the log handle is always cleanly detached |

---

## 8. Dependencies & Interfaces

- **`state.py`** — `StateStore.read_current_run()` (state.py:114-119) is the sole interface rotation uses to determine the protected `run_id`. No changes to `state.py` required.
- **`config.py`** — two new fields, parsed inline in `Config.load()` (no new helper function needed).
- **`cli.py`** — `run()` and `resume()` command bodies gain the rotate → open → (existing logic) → close sequence; a new `log-prune` command.
- **`orchestrator.py`** — no changes. `Pipeline.start()`/`resume()`/`run_to_completion()` signatures are untouched; log lifecycle is entirely a `cli.py`-layer concern, keeping `orchestrator.py` (already the largest file at 643 lines) from growing further.
- **stdlib `logging`** — the only external dependency; no new third-party package.

---

## 9. Security Considerations

- Log files may contain task descriptions and stage output (once/if a future task instruments subprocess capture — out of scope here, but worth flagging forward) — same trust boundary as everything else under `.atlas/`, i.e., local-only, no network exposure, respects the repo's own gitignore posture (TRD.md Security NFR: "Private data in `dev/active/`" applies analogously to `.atlas/runs/`).
- `rotate_run_logs` only ever deletes files matching `*.log` directly inside the given `runs_dir` (via non-recursive `Path.glob`, not `rglob`) — no risk of it wandering into unrelated directories or following unexpected symlink targets outside `.atlas/runs/`.
- No user-controlled input reaches a shell or path-traversal-sensitive API; `run_id` is generated internally (`_make_id()`), never taken from untrusted external input.

---

## 10. Testing Strategy

Follows the repo's established pattern: plain pytest functions (no test classes), `tmp_path` for filesystem isolation, small local factory helpers (per `tests/unit/test_state_store.py`, `tests/unit/test_config.py`).

**New file: `tests/unit/test_run_logging.py`**
- `rotate_run_logs` deletes files older than `max_age_days`, keeps newer ones.
- `rotate_run_logs` never deletes the file matching `protect_run_id`, even if it's the oldest file present.
- `rotate_run_logs` enforces `max_count` by evicting oldest survivors first, once age-eviction alone isn't enough.
- `rotate_run_logs` on a missing `runs_dir` returns `[]` without raising.
- `rotate_run_logs` ignores non-`.log` files in the same directory.
- `rotate_run_logs` continues past a single file's `OSError` on `unlink()` (monkeypatch `Path.unlink` to raise for one target) and still removes the others / returns the successfully-deleted subset.
- `open_run_log` creates `runs_dir` if absent and writes to the expected path; `close_run_log` detaches the handler (assert `logging.getLogger("atlas").handlers` no longer contains it).

**Extend: `tests/unit/test_config.py`**
- `log_retention_days` / `log_max_count` default to `14` / `500` when absent from both TOML files.
- Repo `.atlas.toml` override wins over `~/.atlas/config.toml`, matching the existing precedence test pattern (`monkeypatch.setattr(Path, "home", ...)`).

**Integration:** extend (or add alongside) the existing end-to-end happy-path test to assert: after two sequential `atlas run` invocations in the same throwaway repo with `log_retention_days=0` (forces immediate eligibility) and `log_max_count` large, the first run's log is removed by the second run's start-of-run rotation pass, while the second run's own (just-opened) log survives because it's the protected current run.

**Coverage:** target ≥ 80% on `run_logging.py`, matching the existing repo-wide QA gate (TRD.md §"Quality Assurance Requirements": 80% on core modules).

---

## 11. Performance Considerations

- Rotation cost is O(n) `stat()` calls where n = number of `.log` files, plus a sort only in the (uncommon) case survivors exceed `max_count`. At the default ceiling of 500 files this is well under a millisecond of CPU on any modern filesystem, and it happens once per `atlas run`/`resume` invocation — human-gated, not a hot path (TRD.md §Performance: "no hard latency SLA... measured in minutes, not milliseconds").
- No caching needed; there is nothing to cache — the filesystem itself is the source of truth for what needs rotating, checked fresh each invocation.
- No monitoring/telemetry surface beyond the existing `_logger.warning` on individual deletion failures (consistent with "no atlas-specific dashboard" — TRD.md §"Surface for tracking").

---

## Tasks

- **[Add config keys]** [Effort: S]
  - **Description**: Add `log_retention_days: int = 14` and `log_max_count: int = 500` to the `Config` dataclass and its `load()` parsing, following the existing flat-scalar-key pattern used for `model`.
  - **Acceptance Criteria**:
    - [ ] `Config.load()` returns the defaults when neither TOML file sets the keys
    - [ ] Repo `.atlas.toml` value overrides `~/.atlas/config.toml`, which overrides the built-in default
  - **Files to Create/Modify**:
    - `src/atlas/config.py` - add two dataclass fields + load() parsing lines
  - **Dependencies**: none
  - **Testing Requirements**: Unit (extend `tests/unit/test_config.py`)

- **[Implement `run_logging.py`]** [Effort: M]
  - **Description**: New module with `open_run_log`, `close_run_log`, `rotate_run_logs`, and the private `_safe_unlink` helper, per §3.2 pseudocode.
  - **Acceptance Criteria**:
    - [ ] `rotate_run_logs` deletes files older than `max_age_days` except the protected `run_id`
    - [ ] `rotate_run_logs` enforces `max_count` on survivors, oldest-first
    - [ ] `rotate_run_logs` is a no-op (returns `[]`) when `runs_dir` doesn't exist
    - [ ] A single file's deletion failure is logged and does not raise or stop the rest of the pass
    - [ ] `open_run_log` creates `runs_dir` if missing and attaches a `FileHandler` to the `"atlas"` logger
    - [ ] `close_run_log` detaches and closes the handler
  - **Files to Create/Modify**:
    - `src/atlas/run_logging.py` - new module
  - **Dependencies**: none (independent of Task 1; both feed Task 3)
  - **Testing Requirements**: Unit (new `tests/unit/test_run_logging.py`)

- **[Wire rotation + log lifecycle into `cli.py`]** [Effort: M]
  - **Description**: In both `run()` and `resume()`, read the current-run before pipeline construction, call `rotate_run_logs` with it as the protected id, open the run log once `ctx.run_id` is known, and close it in a `finally` around the existing `run_to_completion` try/except.
  - **Acceptance Criteria**:
    - [ ] `atlas run` performs a rotation pass before `pipeline.start()`
    - [ ] `atlas resume` performs a rotation pass (protecting the pre-resume `run_id`) before `pipeline.resume()`
    - [ ] The log handler is closed on success, on `AbortedError`, and on `KeyboardInterrupt` (no leaked handler across invocations)
  - **Files to Create/Modify**:
    - `src/atlas/cli.py` - modify `run()`, `resume()`; add a small shared rotation helper
  - **Dependencies**: Task 1, Task 2
  - **Testing Requirements**: Integration (extend existing e2e happy-path test)

- **[Add `atlas log-prune` command]** [Effort: S]
  - **Description**: Manual on-demand rotation command, mirroring the `atlas hook install`/`uninstall` convenience-command precedent. See Pending Decision #2 — cut this task if the reviewer wants a smaller first cut.
  - **Acceptance Criteria**:
    - [ ] `atlas log-prune` runs the same rotation logic as start-of-run and prints the count of files removed
    - [ ] Never deletes the log for the currently active run, if any
  - **Files to Create/Modify**:
    - `src/atlas/cli.py` - new `log_prune()` Typer command
  - **Dependencies**: Task 1, Task 2
  - **Testing Requirements**: Unit/Integration (CLI invocation test)

- **[Unit tests for `run_logging.py`]** [Effort: M]
  - **Description**: Full coverage of the rotation edge cases in §10.
  - **Acceptance Criteria**:
    - [ ] All cases in §10's `test_run_logging.py` bullet list pass
    - [ ] `run_logging.py` coverage ≥ 80%
  - **Files to Create/Modify**:
    - `tests/unit/test_run_logging.py` - new
  - **Dependencies**: Task 2
  - **Testing Requirements**: Unit

- **[Config tests for new keys]** [Effort: S]
  - **Description**: Extend existing config tests for defaults + precedence of the two new keys.
  - **Acceptance Criteria**:
    - [ ] Defaults test passes
    - [ ] Repo-over-user precedence test passes
  - **Files to Create/Modify**:
    - `tests/unit/test_config.py` - extend
  - **Dependencies**: Task 1
  - **Testing Requirements**: Unit

- **[Integration test: rotation across sequential runs]** [Effort: M]
  - **Description**: End-to-end test asserting a first run's log is rotated away by a second run's start-of-run pass under a forced-zero retention window, while the second run's own log survives.
  - **Acceptance Criteria**:
    - [ ] Two sequential `atlas run` invocations in a throwaway repo with `log_retention_days=0` demonstrate rotation of the first run's log
    - [ ] The active run's own log is never deleted by its own start-of-run rotation pass
  - **Files to Create/Modify**:
    - `tests/e2e/test_e2e_happy_path.py` (or a new adjacent test module) - extend
  - **Dependencies**: Task 3
  - **Testing Requirements**: Integration/E2E

- **[Update docs]** [Effort: S]
  - **Description**: Remove the "No rotation in v1... track in v1.1 backlog" language from TRD.md's Logging paragraph and system_design.md's on-disk-state table; document the actual policy and the two new config keys; update STATUS.md's v1.1 backlog line.
  - **Acceptance Criteria**:
    - [ ] TRD.md Logging section reflects the shipped retention policy
    - [ ] system_design.md's atlas-owned-on-disk-state table row for `.atlas/runs/<run_id>.log` reflects rotation
    - [ ] STATUS.md backlog line updated or removed
  - **Files to Create/Modify**:
    - `docs/2_architecture/TRD.md` - update Logging paragraph
    - `docs/2_architecture/system_design.md` - update on-disk-state table
    - `STATUS.md` - update backlog line
  - **Dependencies**: Task 3 (docs should describe shipped behavior, not aspirational)
  - **Testing Requirements**: none (docs-only)

---

## Phase Deliverables

- Working `.atlas/runs/<run_id>.log` writer with age+count rotation, wired into `atlas run` and `atlas resume`
- All new/extended tests passing; `ruff check`, `ruff format --check`, `mypy src` green
- TRD.md, system_design.md, STATUS.md updated to reflect shipped behavior (no more "no rotation in v1" language)

---

## Pending Decisions & Clarifications

1. **Numeric defaults.** Recommending `log_retention_days=14`, `log_max_count=500` as sensible solo-developer defaults (see §6 justification table). These are policy calls, not derived from a hard technical constraint — happy to adjust if you have a different disk-budget intuition (e.g., 7 days for a more aggressive cleanup, or 30 for a "keep a month of history" feel).
2. **Is `atlas log-prune` worth including in this first cut?** It's cheap (reuses `rotate_run_logs` directly) and mirrors the existing `atlas hook install`/`uninstall` pattern, but it's not required by the issue's stated acceptance criteria. Options: (a) include it now [current plan], (b) cut it to shrink this phase to the minimum that satisfies the three planning bullets, adding it later if the manual-prune need actually comes up.
3. **Scope boundary on log *content*.** This TRS scaffolds the log file's lifecycle (open/rotate/close) but does not instrument stage/subprocess output into it — today almost nothing would ever be written to the file body itself, since the only existing logger call in the codebase (`plumb_io.py:13`) rarely fires. Options: (a) ship this TRS as pure lifecycle/rotation infrastructure now, with a follow-up issue for "actually log something useful into the run log" [current plan — keeps this issue's scope honest to its title], (b) fold minimal stage-output capture into this same phase (would meaningfully increase Effort on Task 3 and likely touch `SubprocessStageRunner`). Recommend (a) — flagging so the reviewer isn't surprised that the shipped log files stay nearly empty until a follow-up lands.
4. **Flat TOML keys vs. the aspirational nested `[plumb]`-style docs.** This TRS follows the code's actual pattern (flat top-level scalar keys). A pre-existing inconsistency between `config.py` (flat) and the docs (nested `[plumb]` table that's never actually parsed) is not fixed here, per scope discipline. Options: (a) leave the pre-existing inconsistency alone [current plan], (b) treat fixing it as a small separate cleanup ticket triggered by this TRS's discovery, not bundled into it.
