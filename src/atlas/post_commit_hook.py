"""
Post-commit hook — runs in a separate subprocess after each git commit.

Atlas itself never calls this; git invokes it via .git/hooks/post-commit.

Contract: the hook does NOT open its own plumb run handle (it can't — plumb
runs are owned by the orchestrator process that started them). Instead, it
appends a single line to ``<main-repo>/.atlas/pending-scores.jsonl``.  The
next orchestrator ``step()`` (or ``run_to_completion``) flushes this file
through the live ``PlumbIO`` run handle, guaranteeing durable, span-attributed
delivery of the ``gate_commit`` score.

The hook MUST resolve the *main* repo root, not the worktree root, because
``.atlas/`` lives only in the main checkout. We use ``git rev-parse
--git-common-dir`` for this — it returns the shared ``.git`` directory across
worktrees.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


def _main_repo_root() -> Path | None:
    """Return the main repo root (not the worktree root). None if not in a repo."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        # Resolve relative to the current cwd (git's default behaviour).
        common_dir = Path.cwd() / common_dir
    common_dir = common_dir.resolve()
    # .git/ is a child of the main repo root.
    if common_dir.name == ".git":
        return common_dir.parent
    return common_dir.parent if common_dir.parent.exists() else None


def _head_sha(cwd: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def run() -> None:
    """Entry point called by the git post-commit hook script."""
    repo = _main_repo_root()
    if repo is None:
        sys.exit(0)

    current_run_path = repo / ".atlas" / "current-run"
    if not current_run_path.exists():
        sys.exit(0)

    lines = current_run_path.read_text().splitlines()
    if len(lines) < 2:
        sys.exit(0)

    run_id = lines[0].strip()
    sha = _head_sha(Path.cwd())

    pending_path = repo / ".atlas" / "pending-scores.jsonl"
    pending_path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "run_id": run_id,
        "metric": "gate_commit",
        "value_label": "approved",
        "rationale": f"commit {sha[:8]}" if sha else None,
        "ts": time.time(),
    }
    # Append-only; safe under concurrent commits in worktrees.
    with pending_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    sys.exit(0)


HOOK_SCRIPT = """\
#!/bin/sh
# Installed by atlas hook install
python -m atlas.post_commit_hook
"""


def install(repo_root: Path) -> None:
    """Write the post-commit hook script and make it executable."""
    hook_path = repo_root / ".git" / "hooks" / "post-commit"
    hook_path.write_text(HOOK_SCRIPT)
    hook_path.chmod(0o755)
    print(f"Installed atlas post-commit hook at {hook_path}")


if __name__ == "__main__":
    run()
