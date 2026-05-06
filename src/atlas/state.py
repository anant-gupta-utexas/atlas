"""tasks.md + .atlas/current-run state I/O."""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from atlas.stages import STAGES, StageName

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
```

## stages

"""

_STAGE_LINE = "- [ ] {name}\n"
_CHECKED_LINE = "- [x] {name}\n"

_RUN_ID_RE = re.compile(r"<!-- run_id:\s*(\S+)\s*-->")
_TASK_RE = re.compile(r"<!-- task:\s*(\S+)\s*-->")
_CHECKBOX_RE = re.compile(r"^- \[( |x)\] (.+)$", re.MULTILINE)
_CURRENT_BLOCK_RE = re.compile(r"```\nphase: (.+)\ngate:  (.+)\nnext:  (.+)\n```", re.DOTALL)


class StateInconsistencyError(Exception):
    """Raised when .atlas/current-run and tasks.md run_ids disagree."""


class StateStore:
    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._atlas_dir = repo_root / ".atlas"
        self._current_run_path = self._atlas_dir / "current-run"

    def _tasks_md_path(self, slug: str) -> Path:
        return self._repo_root / "dev" / "active" / slug / "tasks.md"

    def create_tasks_md(self, ctx: RunContext) -> None:
        path = self._tasks_md_path(ctx.slug)
        path.parent.mkdir(parents=True, exist_ok=True)

        stage_lines = "".join(_STAGE_LINE.format(name=s.name.value) for s in STAGES)
        # Base64-encode the task text so embedded newlines / markdown survive
        # round-trip through the HTML comment.
        task_b64 = base64.b64encode(ctx.task.encode("utf-8")).decode("ascii")
        content = (
            _TASKS_MD_HEADER.format(
                slug=ctx.slug,
                run_id=ctx.run_id,
                task_b64=task_b64,
                phase=STAGES[0].name.value,
                gate="none",
                next_action="run stage 0 (research)",
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
    ) -> None:
        self._atlas_dir.mkdir(parents=True, exist_ok=True)
        body = f"{run_id}\n{slug}\n"
        if worktree_path is not None or code_gen_span_id is not None:
            body += f"{worktree_path or ''}\n"
        if code_gen_span_id is not None:
            body += f"{code_gen_span_id}\n"
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
        lines = self._current_run_path.read_text().splitlines()
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

    def delete_current_run(self) -> None:
        if self._current_run_path.exists():
            self._current_run_path.unlink()

    def update_current_block(
        self,
        ctx: RunContext,
        *,
        phase: StageName,
        gate: str,
        next_action: str,
    ) -> None:
        path = self._tasks_md_path(ctx.slug)
        content = path.read_text()
        new_block = f"```\nphase: {phase.value}\ngate:  {gate}\nnext:  {next_action}\n```"
        updated = _CURRENT_BLOCK_RE.sub(new_block, content)
        _atomic_write(path, updated)

    def check_box(self, ctx: RunContext, stage: StageName) -> None:
        path = self._tasks_md_path(ctx.slug)
        content = path.read_text()

        def replacer(m: re.Match[str]) -> str:
            checked, name = m.group(1), m.group(2)
            if name == stage.value and checked == " ":
                return f"- [x] {name}"
            return m.group(0)

        updated = _CHECKBOX_RE.sub(replacer, content)
        _atomic_write(path, updated)

    def first_unchecked(self, ctx: RunContext) -> StageName | None:
        path = self._tasks_md_path(ctx.slug)
        content = path.read_text()
        for m in _CHECKBOX_RE.finditer(content):
            checked, name = m.group(1), m.group(2)
            if checked == " ":
                try:
                    return StageName(name)
                except ValueError:
                    continue
        return None

    def assert_consistent(self, ctx: RunContext) -> None:
        result = self.read_current_run()
        if result is None:
            raise StateInconsistencyError(
                f"No .atlas/current-run found; expected run_id={ctx.run_id}"
            )
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
                f"State mismatch: .atlas/current-run says {file_run_id!r}; "
                f"tasks.md header says {tasks_run_id!r}. Resolve manually."
            )
        if ctx.run_id != file_run_id:
            raise StateInconsistencyError(
                f"State mismatch: RunContext has run_id={ctx.run_id!r} but "
                f".atlas/current-run says {file_run_id!r}. Resolve manually."
            )


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
