"""atlas CLI — thin Typer wrapper around Pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import typer
except ModuleNotFoundError:  # pragma: no cover
    print("typer is required: pip install typer", file=sys.stderr)
    sys.exit(1)

from atlas.config import Config
from atlas.orchestrator import (
    AbortedError,
    AutoPrompter,
    ClickPrompter,
    NoActiveRunError,
    Pipeline,
    RoutingDriftError,
    SubprocessStageRunner,
)
from atlas.plumb_io import PlumbIO
from atlas.state import StateStore
from atlas.worktree import WorktreeManager

app = typer.Typer(name="atlas", help="Phase-gated agent pipeline.")


def _find_repo_root() -> Path:
    """Walk up from cwd until we find a .git directory."""
    path = Path.cwd()
    for candidate in [path, *path.parents]:
        if (candidate / ".git").exists():
            return candidate
    typer.echo("Error: not inside a git repository.", err=True)
    raise typer.Exit(1)


def _make_pipeline(repo_root: Path, cfg: Config, *, auto_approve: bool = False) -> Pipeline:
    plumb = PlumbIO(real=True)
    state = StateStore(repo_root)
    worktree = WorktreeManager(repo_root)
    runner = SubprocessStageRunner(
        timeout_overrides=cfg.timeout_overrides,
        command_overrides=cfg.plugin_commands,
    )
    prompter: ClickPrompter | AutoPrompter = AutoPrompter() if auto_approve else ClickPrompter()
    return Pipeline(
        repo_root=repo_root,
        state=state,
        plumb=plumb,
        runner=runner,
        prompter=prompter,
        worktree=worktree,
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
) -> None:
    """Start a new atlas pipeline run."""
    repo_root = _find_repo_root()
    cfg = Config.load(repo_root)

    if not slug:
        slug = _slugify(task)

    try:
        pipeline = _make_pipeline(repo_root, cfg, auto_approve=auto_approve)
    except RoutingDriftError as exc:
        typer.echo(f"Routing fixture mismatch: {exc}", err=True)
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


@app.command()
def resume(
    auto_approve: bool = typer.Option(
        False, "--auto-approve", "-y", help="Auto-approve all gates (for testing)"
    ),
) -> None:
    """Resume an in-flight atlas run in this repo."""
    repo_root = _find_repo_root()
    cfg = Config.load(repo_root)

    try:
        pipeline = _make_pipeline(repo_root, cfg, auto_approve=auto_approve)
        ctx = pipeline.resume()
    except NoActiveRunError as exc:
        typer.echo(f"No active run: {exc}", err=True)
        raise typer.Exit(1)
    except RoutingDriftError as exc:
        typer.echo(f"Routing fixture mismatch: {exc}", err=True)
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
