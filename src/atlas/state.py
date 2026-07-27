"""tasks.md + .atlas/current-run state I/O."""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from atlas.stages import StageSpec

if TYPE_CHECKING:
    from atlas.orchestrator import RunContext

_TASKS_MD_HEADER = """\
# tasks — {slug}

<!-- run_id: {run_id} -->
<!-- task: {task_b64} -->

## current

```
phase: {phase}
gate:  {gate}
next:  {next_action}
workflow: {workflow_name}
```

## stages

"""

_STAGE_LINE = "- [ ] {name}\n"
_CHECKED_LINE = "- [x] {name}\n"

_RUN_ID_RE = re.compile(r"<!-- run_id:\s*(\S+)\s*-->")
_TASK_RE = re.compile(r"<!-- task:\s*(\S+)\s*-->")
_CHECKBOX_RE = re.compile(r"^- \[( |x)\] (.+)$", re.MULTILINE)
_CURRENT_BLOCK_RE = re.compile(
    r"```\nphase: (.+)\ngate:  (.+)\nnext:  (.+)\nworkflow: (.+)\n```", re.DOTALL
)
_WORKFLOW_RE = re.compile(r"^workflow: (.+)$", re.MULTILINE)


class StateInconsistencyError(Exception):
    """Raised when .atlas/current-run and tasks.md run_ids disagree."""


class StateStore:
    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._atlas_dir = repo_root / ".atlas"
        self._current_run_path = self._atlas_dir / "current-run"

    def _tasks_md_path(self, slug: str) -> Path:
        return self._repo_root / "dev" / "active" / slug / "tasks.md"

    def create_tasks_md(
        self,
        ctx: RunContext,
        *,
        stages: tuple[StageSpec, ...],
        workflow_name: str = "dev",
    ) -> None:
        path = self._tasks_md_path(ctx.slug)
        path.parent.mkdir(parents=True, exist_ok=True)

        stage_lines = "".join(_STAGE_LINE.format(name=s.name) for s in stages)
        # Base64-encode the task text so embedded newlines / markdown survive
        # round-trip through the HTML comment.
        task_b64 = base64.b64encode(ctx.task.encode("utf-8")).decode("ascii")
        content = (
            _TASKS_MD_HEADER.format(
                slug=ctx.slug,
                run_id=ctx.run_id,
                task_b64=task_b64,
                phase=stages[0].name,
                gate="none",
                next_action=f"run stage 0 ({stages[0].name})",
                workflow_name=workflow_name,
            )
            + stage_lines
        )

        _atomic_write(path, content)

    def read_task_text(self, slug: str) -> str | None:
        """Return the original task text written to tasks.md, or None if absent."""
        path = self._tasks_md_path(slug)
        if not path.exists():
            return None
        content = path.read_text()
        m = _TASK_RE.search(content)
        if m is None:
            return None
        try:
            return base64.b64decode(m.group(1).encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    def read_workflow_name(self, slug: str) -> str | None:
        """Return the workflow name written to tasks.md's `## current` block, or None."""
        path = self._tasks_md_path(slug)
        if not path.exists():
            return None
        content = path.read_text()
        m = _WORKFLOW_RE.search(content)
        if m is None:
            return None
        return m.group(1).strip()

    def update_run_id(self, slug: str, new_run_id: str) -> None:
        """Rewrite the tasks.md run_id comment in place (used during resume handoff)."""
        path = self._tasks_md_path(slug)
        content = path.read_text()
        updated = _RUN_ID_RE.sub(f"<!-- run_id: {new_run_id} -->", content, count=1)
        _atomic_write(path, updated)

    def write_current_run(
        self,
        run_id: str,
        slug: str,
        worktree_path: Path | None = None,
        code_gen_span_id: str | None = None,
        async_gate_metric: str | None = None,
    ) -> None:
        # .atlas/current-run is positional: line 1 run_id, 2 slug, 3 worktree,
        # 4 code_gen_span_id, 5 async_gate_metric. A later line is reachable only
        # if every earlier line is emitted, so a trailing field forces empty
        # placeholders for the lines it depends on. Readers index by position
        # (read_current_run_with_worktree, read_async_gate_metric, the hook).
        self._atlas_dir.mkdir(parents=True, exist_ok=True)
        body = _build_current_run_body(
            run_id, slug, worktree_path, code_gen_span_id, async_gate_metric
        )
        _atomic_write(self._current_run_path, body)

    def read_current_run(self) -> tuple[str, str] | None:
        result = self.read_current_run_with_worktree()
        if result is None:
            return None
        run_id, slug, _, _ = result
        return run_id, slug

    def read_current_run_with_worktree(
        self,
    ) -> tuple[str, str, Path | None, str | None] | None:
        if not self._current_run_path.exists():
            return None
        return _parse_current_run_body(self._current_run_path.read_text())

    def read_async_gate_metric(self) -> str | None:
        """Return line 5 of .atlas/current-run (the async-gate metric name), if present."""
        if not self._current_run_path.exists():
            return None
        lines = self._current_run_path.read_text().splitlines()
        if len(lines) >= 5 and lines[4].strip():
            return lines[4].strip()
        return None

    def delete_current_run(self) -> None:
        if self._current_run_path.exists():
            self._current_run_path.unlink()

    # -----------------------------------------------------------------
    # Per-run-keyed current-run (Phase L4, T-L4.3) — additive; only
    # loop-dispatched runs use these. Attended `atlas run`/`resume` keep
    # using the singleton methods above, untouched (Pending Decision #3).
    # -----------------------------------------------------------------

    def _keyed_current_run_path(self, run_id: str) -> Path:
        return self._atlas_dir / "runs" / run_id / "current-run"

    def write_current_run_keyed(
        self,
        run_id: str,
        slug: str,
        worktree_path: Path | None = None,
        code_gen_span_id: str | None = None,
        async_gate_metric: str | None = None,
    ) -> None:
        path = self._keyed_current_run_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = _build_current_run_body(
            run_id, slug, worktree_path, code_gen_span_id, async_gate_metric
        )
        _atomic_write(path, body)

    def list_current_runs(self) -> list[tuple[str, str, Path | None, str | None]]:
        """Every live keyed run's ``current-run`` contents, one per concurrent
        dispatch. Order is not significant to callers (orphan-sweep retain-set,
        `atlas loop status`-style introspection)."""
        runs_dir = self._atlas_dir / "runs"
        if not runs_dir.is_dir():
            return []
        results: list[tuple[str, str, Path | None, str | None]] = []
        for run_dir in sorted(runs_dir.glob("*")):
            path = run_dir / "current-run"
            if not path.exists():
                continue
            parsed = _parse_current_run_body(path.read_text())
            if parsed is not None:
                results.append(parsed)
        return results

    def delete_current_run_keyed(self, run_id: str) -> None:
        path = self._keyed_current_run_path(run_id)
        if not path.exists():
            return
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass  # not empty or already gone — best-effort only

    def update_current_block(
        self,
        ctx: RunContext,
        *,
        phase: str,
        gate: str,
        next_action: str,
    ) -> None:
        path = self._tasks_md_path(ctx.slug)
        content = path.read_text()
        workflow_name = self.read_workflow_name(ctx.slug) or "dev"
        new_block = (
            f"```\nphase: {phase}\ngate:  {gate}\nnext:  {next_action}\n"
            f"workflow: {workflow_name}\n```"
        )
        updated = _CURRENT_BLOCK_RE.sub(new_block, content)
        _atomic_write(path, updated)

    def check_box(self, ctx: RunContext, stage_name: str) -> None:
        path = self._tasks_md_path(ctx.slug)
        content = path.read_text()

        def replacer(m: re.Match[str]) -> str:
            checked, name = m.group(1), m.group(2)
            if name == stage_name and checked == " ":
                return f"- [x] {name}"
            return m.group(0)

        updated = _CHECKBOX_RE.sub(replacer, content)
        _atomic_write(path, updated)

    def first_unchecked(self, ctx: RunContext) -> str | None:
        path = self._tasks_md_path(ctx.slug)
        content = path.read_text()
        for m in _CHECKBOX_RE.finditer(content):
            checked, name = m.group(1), m.group(2)
            if checked == " ":
                return name
        return None

    def assert_consistent(self, ctx: RunContext, *, keyed: bool = False) -> None:
        """``keyed=True`` (Phase L4, T-L4.3) checks the per-run-keyed
        current-run file instead of the singleton — additive, keyword-only,
        default-False, so attended `atlas run`'s existing call shape is
        byte-identical (Pending Decision #3)."""
        result = self._read_current_run_keyed_pair(ctx.run_id) if keyed else self.read_current_run()
        if result is None:
            where = f".atlas/runs/{ctx.run_id}/current-run" if keyed else ".atlas/current-run"
            raise StateInconsistencyError(f"No {where} found; expected run_id={ctx.run_id}")
        file_run_id, _ = result

        path = self._tasks_md_path(ctx.slug)
        content = path.read_text()
        m = _RUN_ID_RE.search(content)
        if m is None:
            raise StateInconsistencyError(
                f"tasks.md has no run_id comment; expected run_id={ctx.run_id}"
            )
        tasks_run_id = m.group(1)

        if file_run_id != tasks_run_id:
            raise StateInconsistencyError(
                f"State mismatch: current-run says {file_run_id!r}; "
                f"tasks.md header says {tasks_run_id!r}. Resolve manually."
            )
        if ctx.run_id != file_run_id:
            raise StateInconsistencyError(
                f"State mismatch: RunContext has run_id={ctx.run_id!r} but "
                f"current-run says {file_run_id!r}. Resolve manually."
            )

    def _read_current_run_keyed_pair(self, run_id: str) -> tuple[str, str] | None:
        path = self._keyed_current_run_path(run_id)
        if not path.exists():
            return None
        parsed = _parse_current_run_body(path.read_text())
        if parsed is None:
            return None
        return parsed[0], parsed[1]


def _build_current_run_body(
    run_id: str,
    slug: str,
    worktree_path: Path | None,
    code_gen_span_id: str | None,
    async_gate_metric: str | None,
) -> str:
    body = f"{run_id}\n{slug}\n"
    if worktree_path is not None or code_gen_span_id is not None or async_gate_metric:
        body += f"{worktree_path or ''}\n"
    if code_gen_span_id is not None or async_gate_metric:
        body += f"{code_gen_span_id or ''}\n"
    if async_gate_metric:
        body += f"{async_gate_metric}\n"
    return body


def _parse_current_run_body(text: str) -> tuple[str, str, Path | None, str | None] | None:
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    run_id = lines[0].strip()
    slug = lines[1].strip()
    worktree_path: Path | None = None
    if len(lines) >= 3 and lines[2].strip():
        worktree_path = Path(lines[2].strip())
    code_gen_span_id: str | None = None
    if len(lines) >= 4 and lines[3].strip():
        code_gen_span_id = lines[3].strip()
    return run_id, slug, worktree_path, code_gen_span_id


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
