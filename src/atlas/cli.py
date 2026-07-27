"""atlas CLI — thin Typer wrapper around Pipeline."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

try:
    import typer
except ModuleNotFoundError:  # pragma: no cover
    print("typer is required: pip install typer", file=sys.stderr)
    sys.exit(1)

from atlas import loop as _loop
from atlas.config import Config
from atlas.loop_budget import LoopState, breaker_open
from atlas.orchestrator import (
    AbortedError,
    NoActiveRunError,
    RoutingDriftError,
)
from atlas.pipeline_factory import LastOutcomeRunner, make_pipeline
from atlas.state import StateStore
from atlas.workflow_loader import (
    WorkflowNotFoundError,
    WorkflowValidationError,
)


def _available_workflows() -> list[str]:
    """List built-in workflow names discovered from the packaged workflows/ dir."""
    workflows_dir = Path(__file__).parent / "workflows"
    if not workflows_dir.is_dir():
        return []
    return sorted(p.stem for p in workflows_dir.glob("*.yaml"))


app = typer.Typer(
    name="atlas",
    help=(
        "Phase-gated agent pipeline.\n\n"
        f"Available workflows (built-in): {', '.join(_available_workflows()) or 'none'}"
    ),
)


def _find_repo_root() -> Path:
    """Walk up from cwd until we find a .git directory."""
    path = Path.cwd()
    for candidate in [path, *path.parents]:
        if (candidate / ".git").exists():
            return candidate
    typer.echo("Error: not inside a git repository.", err=True)
    raise typer.Exit(1)


def _emit_content_pipeline_hint(recorder: LastOutcomeRunner) -> None:
    """If the last stage failed with content_pipeline_not_installed, echo the fix hint."""
    if recorder.last is not None and recorder.last.error_type == "content_pipeline_not_installed":
        typer.echo(
            "\ncontent-pipeline is not installed.\n"
            "  Install it:  uv sync --extra job  OR  pip install -e ../content-pipeline\n"
            '  Dependency-free alternative: atlas run "<task>" --workflow job_cli',
            err=True,
        )


@app.command()
def run(
    task: str = typer.Argument(..., help="Task description, e.g. 'add response-cache middleware'"),
    slug: str = typer.Option(
        "", "--slug", "-s", help="Short name for the tasks.md directory (auto-derived if omitted)"
    ),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", "-y", help="Auto-approve all gates (for testing)"
    ),
    workflow: str = typer.Option(
        "",
        "--workflow",
        "-w",
        help="Workflow name to load (searches .atlas/workflows/, "
        "~/.atlas/workflows/, then built-in). Defaults to 'dev'.",
    ),
    workflow_file: str = typer.Option(
        "",
        "--workflow-file",
        help="Literal path to a workflow YAML file. Takes priority over --workflow.",
    ),
    backend: str = typer.Option(
        "",
        "--backend",
        "-b",
        help="CLI backend to dispatch with (claude / agy / codex). Overrides "
        "the configured default; a stage's own `backend:` field still wins.",
    ),
    telemetry: bool = typer.Option(
        False,
        "--telemetry",
        help="Request the backend's JSON envelope so token/cost telemetry is "
        "recorded to plumb. Off by default: it changes the dispatched argv, "
        "and attended runs are byte-identical to pre-loop-mode without it.",
    ),
) -> None:
    """Start a new atlas pipeline run."""
    repo_root = _find_repo_root()
    cfg = Config.load(repo_root)

    if not slug:
        slug = _slugify(task)

    try:
        pipeline, recorder = make_pipeline(
            repo_root,
            cfg,
            auto_approve=auto_approve,
            workflow=workflow or None,
            workflow_file=Path(workflow_file) if workflow_file else None,
            backend_override=backend or None,
            telemetry_json=telemetry,
        )
    except RoutingDriftError as exc:
        typer.echo(f"Routing fixture mismatch: {exc}", err=True)
        raise typer.Exit(1)
    except (WorkflowNotFoundError, WorkflowValidationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    ctx = pipeline.start(task=task, slug=slug)
    typer.echo(f"Run {ctx.run_id[:8]} started — {slug}")

    try:
        pipeline.run_to_completion(ctx)
    except AbortedError as exc:
        typer.echo(f"\nRun aborted: {exc}", err=True)
        raise typer.Exit(1)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted. Resume with: atlas resume", err=True)
        raise typer.Exit(1)
    _emit_content_pipeline_hint(recorder)


@app.command()
def resume(
    auto_approve: bool = typer.Option(
        False, "--auto-approve", "-y", help="Auto-approve all gates (for testing)"
    ),
) -> None:
    """Resume an in-flight atlas run in this repo."""
    repo_root = _find_repo_root()
    cfg = Config.load(repo_root)

    # Peek at the active workflow name so make_pipeline creates the right runner
    # (LibraryStageRunner is only added for LIB:-prefixed workflows like job.yaml).
    state = StateStore(repo_root)
    active_workflow: str | None = None
    pair = state.read_current_run()
    if pair is not None:
        _, slug = pair
        active_workflow = state.read_workflow_name(slug)

    try:
        pipeline, recorder = make_pipeline(
            repo_root, cfg, auto_approve=auto_approve, workflow=active_workflow
        )
        ctx = pipeline.resume()
    except NoActiveRunError as exc:
        typer.echo(f"No active run: {exc}", err=True)
        raise typer.Exit(1)
    except RoutingDriftError as exc:
        typer.echo(f"Routing fixture mismatch: {exc}", err=True)
        raise typer.Exit(1)
    except (WorkflowNotFoundError, WorkflowValidationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    typer.echo(f"Resuming run {ctx.run_id[:8]} — {ctx.slug}")

    try:
        pipeline.run_to_completion(ctx)
    except AbortedError as exc:
        typer.echo(f"\nRun aborted: {exc}", err=True)
        raise typer.Exit(1)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted. Resume with: atlas resume", err=True)
        raise typer.Exit(1)
    _emit_content_pipeline_hint(recorder)


@app.command()
def status() -> None:
    """Print the current run state from tasks.md."""
    repo_root = _find_repo_root()
    state = StateStore(repo_root)

    pair = state.read_current_run()
    if pair is None:
        typer.echo("No active atlas run.")
        return

    run_id, slug = pair
    tasks_path = repo_root / "dev" / "active" / slug / "tasks.md"
    if not tasks_path.exists():
        typer.echo(f"tasks.md not found for slug={slug!r}. State may be corrupted.")
        raise typer.Exit(1)

    content = tasks_path.read_text()
    typer.echo(f"Run {run_id[:8]} — {slug}\n")
    # Print only the ## current block
    in_block = False
    for line in content.splitlines():
        if line.startswith("## current"):
            in_block = True
        elif line.startswith("## ") and in_block:
            break
        if in_block:
            typer.echo(line)


@app.command()
def hook(
    action: str = typer.Argument("install", help="'install' to add the post-commit hook"),
) -> None:
    """Manage the atlas git hook."""
    repo_root = _find_repo_root()
    if action == "install":
        from atlas.post_commit_hook import install

        install(repo_root)
    else:
        typer.echo(f"Unknown hook action: {action!r}. Use 'install'.", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# atlas loop — the autonomous loop driver (Phase L2)
# ---------------------------------------------------------------------------

loop_app = typer.Typer(name="loop", help="Run the autonomous loop driver.")
app.add_typer(loop_app, name="loop")

_TMUX_SESSION = "atlas-loop"


def _tmux(*args: str) -> None:
    """Run a tmux subprocess command, exiting cleanly if tmux isn't installed."""
    import subprocess

    try:
        subprocess.run(["tmux", *args], check=True)
    except FileNotFoundError:
        typer.echo(
            "Error: tmux is not installed. Install it to use loop start/stop/attach.", err=True
        )
        raise typer.Exit(1)
    except subprocess.CalledProcessError as exc:
        raise typer.Exit(exc.returncode)


@loop_app.command("run")
def loop_run(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Log every tick at DEBUG level, not just INFO."
    ),
) -> None:
    """Run the loop daemon in this terminal (foreground, for debugging)."""

    # Without this the daemon emits NOTHING. atlas.loop logs its failures via
    # `logging`, but no handler was ever configured, so an unattended run that
    # died produced an empty log file and the operator's only clue was a
    # breaker that had silently opened (observed live, T-L2.13, 2026-07-27).
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    repo_root = _find_repo_root()
    cfg = Config.load(repo_root)
    try:
        # Called through the module (not a `from ... import`) so tests can
        # patch atlas.loop.run_forever — a direct name binding would capture
        # the real daemon at import time and ignore the patch.
        _loop.run_forever(cfg, repos=list(cfg.loop.repos), repo_root=repo_root)
    except KeyboardInterrupt:
        typer.echo("\nLoop stopped.", err=True)
        raise typer.Exit(0)


@loop_app.command("start")
def loop_start() -> None:
    """Start the loop daemon detached, in a tmux session."""
    _tmux("new", "-d", "-s", _TMUX_SESSION, "atlas loop run")
    typer.echo(f"Loop started in tmux session '{_TMUX_SESSION}'. Attach with: atlas loop attach")


@loop_app.command("stop")
def loop_stop() -> None:
    """Stop the detached loop daemon's tmux session."""
    _tmux("kill-session", "-t", _TMUX_SESSION)
    typer.echo("Loop stopped.")


@loop_app.command("status")
def loop_status() -> None:
    """Print a human-readable summary of the loop's persisted state."""

    repo_root = _find_repo_root()
    cfg = Config.load(repo_root)
    state_path = repo_root / ".atlas" / "loop-state.json"
    if not state_path.exists():
        typer.echo("Loop has not run yet.")
        return

    state = LoopState.load_or_init(repo_root)
    typer.echo(f"Day: {state.day}")
    typer.echo(f"Runs today: {state.runs_today} / {cfg.loop.max_runs_per_day}")
    # Live since 2026-07-26 (plumb v1.1 set_usage + the L0 telemetry chain).
    # The Codex caveat is printed rather than assumed away: that engine
    # reports no cost, so its runs advance the runs-cap but not this one.
    typer.echo(
        f"Dollars today: ${state.dollars_today:.4f} / ${cfg.loop.max_dollars_per_day:.2f}"
        "  (claude-reported; codex runs report no cost)"
    )
    typer.echo(f"Last tick: {state.last_tick_at or 'never'}")
    if breaker_open(state, cfg.loop):
        typer.echo(f"Breaker: OPEN until {state.breaker_open_until}")
    else:
        typer.echo("Breaker: closed")


@loop_app.command("attach")
def loop_attach() -> None:
    """Attach to the detached loop daemon's tmux session (replaces this process)."""
    import os
    import shutil

    tmux_path = shutil.which("tmux")
    if tmux_path is None:
        typer.echo(
            "Error: tmux is not installed. Install it to use loop start/stop/attach.", err=True
        )
        raise typer.Exit(1)
    os.execvp(tmux_path, ["tmux", "attach", "-t", _TMUX_SESSION])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert task description to a filesystem-safe slug (max 40 chars)."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40]


def main() -> None:
    app()


if __name__ == "__main__":
    main()
