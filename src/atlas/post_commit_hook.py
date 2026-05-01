"""
Post-commit hook — runs in a separate subprocess after each git commit in the worktree.

Reads the active run from .atlas/current-run, writes a ``gate_commit`` score row
to plumb, and exits.  Atlas itself never calls this; git invokes it.

Install via: ``atlas hook install``
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(0)  # not in a git repo — nothing to do
    return Path(result.stdout.strip())


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
    repo = _repo_root()
    current_run_path = repo / ".atlas" / "current-run"

    if not current_run_path.exists():
        sys.exit(0)

    lines = current_run_path.read_text().splitlines()
    if len(lines) < 2:
        sys.exit(0)

    run_id = lines[0].strip()
    slug = lines[1].strip()

    # Find the most-recent span_id for code_gen by reading tasks.md.
    # The hook only records the gate_commit score; the span_id is the
    # one written when step() returned awaiting_hook.  We look it up
    # from plumb if available, otherwise skip gracefully.
    sha = _head_sha(repo)

    try:
        from atlas.plumb_io import PlumbIO

        plumb = PlumbIO(real=True)
        # Re-use the existing open run by synthesising a minimal signal.
        # The score is written with span_id="" when no span is available —
        # plumb accepts null span_id for hook-written scores.
        from atlas.orchestrator import GateDecision

        decision = GateDecision(label="approved", turn_count=1, reason=f"commit {sha[:8]}")
        plumb._run_id = run_id  # attach to existing run  # noqa: SLF001
        plumb.record_user_signal(
            run_id=run_id,
            span_id="",
            metric="gate_commit",
            decision=decision,
        )
    except Exception:
        # Hooks must not break the commit flow — fail silently.
        pass

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
