"""Unit tests for ShellStageRunner (review finding #2 — real CLI dispatch).

These prove SHELL: stages spawn a real, list-form subprocess of the named
binary — the property _FakeSubprocessRunner-based tests can't verify. One test
puts a stub ``content-pipeline`` executable on PATH and asserts it actually
runs; the rest cover the allow-list and failure-mapping (NFR-2: never raise).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from atlas.orchestrator import RunContext
from atlas.shell_runner import ShellStageRunner
from atlas.stages import StageSpec


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(run_id="r1", slug="s", task="t", repo_root=tmp_path)


def _shell_stage(tool: str, *, timeout_s: int | None = None) -> StageSpec:
    return StageSpec(
        index=0,
        name="ingest_postings",
        span_kind="tool",
        tool=tool,
        gate_label=None,
        gate_index=None,
        timeout_s=timeout_s,
    )


def _write_stub_bin(dir_path: Path, name: str, script: str) -> None:
    """Write an executable shell script named *name* into *dir_path*."""
    p = dir_path / name
    p.write_text(script)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_shell_runner_spawns_real_content_pipeline(tmp_path: Path, monkeypatch) -> None:
    """A stub content-pipeline on PATH is actually executed (real subprocess)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # Stub echoes a marker + its args so we can prove it ran with the right argv.
    _write_stub_bin(
        bindir,
        "content-pipeline",
        '#!/bin/sh\necho "STUB-RAN args=$*"\n',
    )
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    runner = ShellStageRunner()
    outcome = runner.run(
        ctx=_ctx(tmp_path),
        stage=_shell_stage("SHELL:content-pipeline capture --source job-boards"),
    )

    assert outcome.status == "success"
    assert outcome.error_type is None
    assert "STUB-RAN args=capture --source job-boards" in outcome.output_text


def test_shell_runner_nonzero_exit_is_failure(tmp_path: Path, monkeypatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_stub_bin(bindir, "content-pipeline", '#!/bin/sh\necho "boom" >&2\nexit 3\n')
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    outcome = ShellStageRunner().run(
        ctx=_ctx(tmp_path), stage=_shell_stage("SHELL:content-pipeline score-jobs --pending")
    )

    assert outcome.status == "failure"
    assert outcome.error_type == "shell_nonzero_exit"


def test_shell_runner_binary_missing_is_clean_failure(tmp_path: Path, monkeypatch) -> None:
    """content-pipeline not on PATH → clean failure, no exception (NFR-2)."""
    # Point PATH at an empty dir so the allow-listed binary is genuinely absent.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    outcome = ShellStageRunner().run(
        ctx=_ctx(tmp_path), stage=_shell_stage("SHELL:content-pipeline capture")
    )

    assert outcome.status == "failure"
    assert outcome.error_type == "shell_command_not_found"
    assert "content-pipeline" in outcome.output_text


def test_shell_runner_rejects_non_allowlisted_command(tmp_path: Path) -> None:
    """A command whose first token isn't allow-listed never spawns anything."""
    outcome = ShellStageRunner().run(ctx=_ctx(tmp_path), stage=_shell_stage("SHELL:rm -rf /"))

    assert outcome.status == "failure"
    assert outcome.error_type == "shell_command_not_allowed"


def test_shell_runner_rejects_empty_command(tmp_path: Path) -> None:
    outcome = ShellStageRunner().run(ctx=_ctx(tmp_path), stage=_shell_stage("SHELL:   "))

    assert outcome.status == "failure"
    assert outcome.error_type == "shell_command_invalid"
