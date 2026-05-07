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
    metric = "gate_commit"

    pending_path = repo / ".atlas" / "pending-scores.jsonl"
    pending_path.parent.mkdir(parents=True, exist_ok=True)

    # Idempotency: if a record for this exact (run_id, commit_sha, metric)
    # triple already sits in pending-scores.jsonl, do not append a duplicate.
    # Plumb tracks idempotent score ingestion as v2 deferred work, so atlas
    # enforces local dedupe in the meantime.
    if sha and _already_recorded(pending_path, run_id=run_id, sha=sha, metric=metric):
        sys.exit(0)

    record = {
        "run_id": run_id,
        "metric": metric,
        "value_label": "approved",
        "rationale": f"commit {sha[:8]}" if sha else None,
        "commit_sha": sha or None,
        "ts": time.time(),
    }
    # Append-only; safe under concurrent commits in worktrees.
    with pending_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    sys.exit(0)


def _already_recorded(pending_path: Path, *, run_id: str, sha: str, metric: str) -> bool:
    """Return True if pending-scores.jsonl already has a record for this triple."""
    if not pending_path.exists():
        return False
    try:
        for line in pending_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                rec.get("run_id") == run_id
                and rec.get("commit_sha") == sha
                and rec.get("metric") == metric
            ):
                return True
    except OSError:
        return False
    return False


def _hook_script(python: str) -> str:
    """Return hook script that falls back to python3 if the baked path disappears."""
    return f"""\
#!/bin/sh
# Installed by atlas hook install
# Primary interpreter baked at install time; falls back to python3 on PATH.
if command -v '{python}' > /dev/null 2>&1; then
    '{python}' -m atlas.post_commit_hook
elif command -v python3 > /dev/null 2>&1; then
    python3 -m atlas.post_commit_hook
else
    echo "atlas post-commit hook: Python not found. Re-run 'atlas hook install'." >&2
fi
"""


def install(repo_root: Path) -> None:
    """Write the post-commit hook script and make it executable."""
    import sys

    python = sys.executable
    python_path = Path(python).resolve()

    # Warn when the interpreter lives outside the repo's .venv so the user knows
    # the hook will break if they recreate the venv at a different path.
    repo_venv = repo_root / ".venv"
    try:
        python_path.relative_to(repo_venv)
    except ValueError:
        print(
            f"Warning: baking interpreter path {python!r} which is outside "
            f"{repo_venv}. If you recreate the venv, re-run 'atlas hook install'.",
            file=sys.stderr,
        )

    hook_path = repo_root / ".git" / "hooks" / "post-commit"
    hook_path.write_text(_hook_script(python))
    hook_path.chmod(0o755)
    print(f"Installed atlas post-commit hook at {hook_path}")


if __name__ == "__main__":
    run()
