"""Unit tests for the `atlas loop` CLI surface (T-L2.9)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from atlas import loop
from atlas.cli import app
from atlas.config import LoopConfig

runner = CliRunner()


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_loop_run_calls_run_forever_no_tmux(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    with (
        patch("atlas.cli._find_repo_root", return_value=tmp_path),
        patch("atlas.loop.run_forever") as mock_run_forever,
    ):
        result = runner.invoke(app, ["loop", "run"])

    assert result.exit_code == 0
    assert mock_run_forever.call_count == 1
    _, kwargs = mock_run_forever.call_args
    assert kwargs["repos"] == []
    assert kwargs["repo_root"] == tmp_path


def test_loop_start_invokes_exact_tmux_new_session(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_subprocess_run:
        mock_subprocess_run.return_value.returncode = 0
        result = runner.invoke(app, ["loop", "start"])

    assert result.exit_code == 0
    mock_subprocess_run.assert_called_once_with(
        ["tmux", "new", "-d", "-s", "atlas-loop", "atlas loop run"], check=True
    )


def test_loop_stop_invokes_exact_tmux_kill_session(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_subprocess_run:
        mock_subprocess_run.return_value.returncode = 0
        result = runner.invoke(app, ["loop", "stop"])

    assert result.exit_code == 0
    mock_subprocess_run.assert_called_once_with(
        ["tmux", "kill-session", "-t", "atlas-loop"], check=True
    )


def test_loop_attach_invokes_exact_tmux_attach(tmp_path: Path) -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/tmux"),
        patch("os.execvp") as mock_execvp,
    ):
        result = runner.invoke(app, ["loop", "attach"])

    assert result.exit_code == 0
    mock_execvp.assert_called_once_with("/usr/bin/tmux", ["tmux", "attach", "-t", "atlas-loop"])


def test_loop_start_missing_tmux_produces_clean_error(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        result = runner.invoke(app, ["loop", "start"])

    assert result.exit_code == 1
    assert "tmux is not installed" in result.output


def test_loop_stop_missing_tmux_produces_clean_error(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        result = runner.invoke(app, ["loop", "stop"])

    assert result.exit_code == 1
    assert "tmux is not installed" in result.output


def test_loop_attach_missing_tmux_produces_clean_error(tmp_path: Path) -> None:
    with patch("shutil.which", return_value=None):
        result = runner.invoke(app, ["loop", "attach"])

    assert result.exit_code == 1
    assert "tmux is not installed" in result.output


def test_loop_run_unaffected_by_missing_tmux(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    with (
        patch("atlas.cli._find_repo_root", return_value=tmp_path),
        patch("atlas.loop.run_forever") as mock_run_forever,
    ):
        result = runner.invoke(app, ["loop", "run"])

    assert result.exit_code == 0
    assert mock_run_forever.call_count == 1


def test_loop_status_no_state_file_reports_not_run_yet(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    with patch("atlas.cli._find_repo_root", return_value=tmp_path):
        result = runner.invoke(app, ["loop", "status"])

    assert result.exit_code == 0
    assert "not run yet" in result.output.lower()


def test_loop_status_populated_state_reports_summary(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / ".atlas.toml").write_text(
        "[loop]\nmax_runs_per_day = 20\nmax_dollars_per_day = 10.0\n"
    )
    state = loop.LoopState(
        day=loop._today(),
        runs_today=3,
        dollars_today=1.42,
        last_tick_at="2026-07-24T18:03:11Z",
    )
    state.persist(tmp_path)

    with patch("atlas.cli._find_repo_root", return_value=tmp_path):
        result = runner.invoke(app, ["loop", "status"])

    assert result.exit_code == 0
    assert "3" in result.output
    assert "20" in result.output
    assert "1.42" in result.output
    assert "2026-07-24T18:03:11Z" in result.output
    assert "closed" in result.output.lower()


def test_loop_status_reports_open_breaker(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    state = loop.LoopState(day=loop._today(), breaker_open_until="2099-01-01T00:00:00+00:00")
    state.persist(tmp_path)

    with patch("atlas.cli._find_repo_root", return_value=tmp_path):
        result = runner.invoke(app, ["loop", "status"])

    assert result.exit_code == 0
    assert "open" in result.output.lower()
    assert "2099-01-01" in result.output


def test_loop_config_concurrency_guard_still_enforced() -> None:
    # Sanity check the CLI surface didn't silently bypass the T-L2.3 guard.
    try:
        LoopConfig(concurrency=2)
    except ValueError as exc:
        assert "concurrency" in str(exc)
    else:
        raise AssertionError("expected ValueError for concurrency != 1")
