"""Unit tests for atlas.cli_backend — argv, parse_result, preflight, resolve."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.cli_backend import (
    _KNOWN_BACKENDS,
    AntigravityBackend,
    ClaudeCodeBackend,
    CliBackend,
    UnknownBackendError,
    make_backend,
    resolve_backend,
)
from atlas.stages import StageSpec
from atlas.workflow_loader import LoadedWorkflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIR_A = Path("/repo")
_DIR_B = Path("/worktree")


def _stage(backend: str | None = None) -> StageSpec:
    return StageSpec(
        index=0,
        name="test",
        span_kind="code_gen",
        tool="RAW:noop",
        gate_label="Gate 0",
        gate_index=0,
        backend=backend,
    )


def _workflow(default_backend: str | None = None) -> LoadedWorkflow:
    return LoadedWorkflow(name="test-wf", default_backend=default_backend, stages=())


# ---------------------------------------------------------------------------
# ClaudeCodeBackend — argv
# ---------------------------------------------------------------------------


def test_claude_code_backend_argv_byte_identical_to_phase2() -> None:
    """FR-8: build_argv must produce the exact list Phase 2's hardcoded path produced."""
    backend = ClaudeCodeBackend()
    argv = backend.build_argv(
        prompt="hello",
        model="haiku",
        add_dirs=[_DIR_A],
        timeout_s=300,
        extra_flags={},
    )
    # Golden string — matches SubprocessStageRunner's hardcoded block before Phase 3.
    assert argv == [
        "claude",
        "-p",
        "hello",
        "--no-session-persistence",
        "--model",
        "haiku",
        "--add-dir",
        str(_DIR_A),
    ]


def test_claude_code_backend_argv_two_dirs() -> None:
    backend = ClaudeCodeBackend()
    argv = backend.build_argv(
        prompt="p",
        model="haiku",
        add_dirs=[_DIR_A, _DIR_B],
        timeout_s=60,
        extra_flags={},
    )
    assert argv == [
        "claude",
        "-p",
        "p",
        "--no-session-persistence",
        "--model",
        "haiku",
        "--add-dir",
        str(_DIR_A),
        "--add-dir",
        str(_DIR_B),
    ]


def test_claude_code_backend_argv_no_bare_no_output_format() -> None:
    """Resolved Decision #2: --bare is NOT added; --output-format is NOT added."""
    backend = ClaudeCodeBackend()
    argv = backend.build_argv(prompt="p", model="haiku", add_dirs=[], timeout_s=60, extra_flags={})
    assert "--bare" not in argv
    assert "--output-format" not in argv


# ---------------------------------------------------------------------------
# ClaudeCodeBackend — parse_result
# ---------------------------------------------------------------------------


def test_claude_code_backend_parse_result_success() -> None:
    status, output_text, error_type = ClaudeCodeBackend().parse_result("foo", "", 0)
    assert status == "success"
    assert output_text == "foo"
    assert error_type is None


def test_claude_code_backend_parse_result_nonzero_exit() -> None:
    status, output_text, error_type = ClaudeCodeBackend().parse_result("bar", "", 1)
    assert status == "failure"
    assert output_text == "bar"
    assert error_type == "plugin_nonzero_exit"


def test_claude_code_backend_preflight_is_none() -> None:
    assert ClaudeCodeBackend().preflight() is None


# ---------------------------------------------------------------------------
# AntigravityBackend — argv
# ---------------------------------------------------------------------------


def test_antigravity_backend_argv_uses_include_directories() -> None:
    backend = AntigravityBackend()
    argv = backend.build_argv(
        prompt="p",
        model="gemini-flash-lite",
        add_dirs=[_DIR_A, _DIR_B],
        timeout_s=60,
        extra_flags={},
    )
    assert "--include-directories" in argv
    assert "--add-dir" not in argv
    # Both dirs present
    idx = [i for i, a in enumerate(argv) if a == "--include-directories"]
    assert len(idx) == 2
    assert argv[idx[0] + 1] == str(_DIR_A)
    assert argv[idx[1] + 1] == str(_DIR_B)


def test_antigravity_backend_argv_uses_output_format_json() -> None:
    argv = AntigravityBackend().build_argv(
        prompt="p", model="m", add_dirs=[], timeout_s=60, extra_flags={}
    )
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"


def test_antigravity_backend_argv_default_model() -> None:
    backend = AntigravityBackend()
    argv_default = backend.build_argv(
        prompt="p", model="", add_dirs=[], timeout_s=60, extra_flags={}
    )
    assert "gemini-flash-lite" in argv_default

    argv_explicit = backend.build_argv(
        prompt="p", model="gemini-pro", add_dirs=[], timeout_s=60, extra_flags={}
    )
    assert "gemini-pro" in argv_explicit
    assert "gemini-flash-lite" not in argv_explicit


def test_antigravity_backend_argv_starts_with_agy() -> None:
    argv = AntigravityBackend().build_argv(
        prompt="p", model="m", add_dirs=[], timeout_s=60, extra_flags={}
    )
    assert argv[0] == "agy"
    assert argv[1] == "-p"


# ---------------------------------------------------------------------------
# AntigravityBackend — parse_result
# ---------------------------------------------------------------------------


def test_antigravity_backend_parse_result_success_json() -> None:
    import json

    stdout = json.dumps({"response": "ok", "stats": {}})
    status, output_text, error_type = AntigravityBackend().parse_result(stdout, "", 0)
    assert status == "success"
    assert output_text == "ok"
    assert error_type is None


def test_antigravity_backend_parse_result_error_field() -> None:
    import json

    stdout = json.dumps({"response": "", "error": {"message": "rate limited"}})
    status, output_text, error_type = AntigravityBackend().parse_result(stdout, "", 0)
    assert status == "failure"
    assert "rate limited" in output_text
    assert error_type == "agy_response_error"


def test_antigravity_backend_parse_result_unparseable() -> None:
    status, output_text, error_type = AntigravityBackend().parse_result("not-json", "", 0)
    assert status == "failure"
    assert error_type == "agy_unparseable_output"


def test_antigravity_backend_parse_result_response_not_string() -> None:
    import json

    stdout = json.dumps({"response": 42})
    status, output_text, error_type = AntigravityBackend().parse_result(stdout, "", 0)
    assert status == "failure"
    assert error_type == "agy_response_not_string"


def test_antigravity_backend_parse_result_input_error() -> None:
    status, _, error_type = AntigravityBackend().parse_result("", "", 42)
    assert status == "failure"
    assert error_type == "agy_input_error"


def test_antigravity_backend_parse_result_turn_limit() -> None:
    status, _, error_type = AntigravityBackend().parse_result("", "", 53)
    assert status == "failure"
    assert error_type == "agy_turn_limit"


def test_antigravity_backend_parse_result_general_error() -> None:
    for code in [1, 2, 127]:
        status, _, error_type = AntigravityBackend().parse_result("e", "", code)
        assert status == "failure"
        assert error_type == "agy_general_error"


def test_antigravity_backend_parse_result_nonzero_uses_stderr_fallback() -> None:
    status, output_text, _ = AntigravityBackend().parse_result("", "stderr-msg", 1)
    assert output_text == "stderr-msg"


# ---------------------------------------------------------------------------
# AntigravityBackend — preflight
# ---------------------------------------------------------------------------


def test_antigravity_backend_preflight_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    result = AntigravityBackend().preflight()
    assert result is not None
    msg, error_type = result
    assert error_type == "agy_missing_auth_env"
    assert "GEMINI_API_KEY" in msg
    assert "GOOGLE_API_KEY" in msg


def test_antigravity_backend_preflight_gemini_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert AntigravityBackend().preflight() is None


def test_antigravity_backend_preflight_google_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    assert AntigravityBackend().preflight() is None


# ---------------------------------------------------------------------------
# resolve_backend — 4-tier priority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage_backend, wf_backend, config_default, expected",
    [
        # Tier 1 wins
        ("claude", "agy", "agy", "claude"),
        ("agy", "claude", "claude", "agy"),
        ("claude", None, None, "claude"),
        # Tier 2 wins (stage=None)
        (None, "agy", "claude", "agy"),
        (None, "claude", "agy", "claude"),
        (None, "agy", None, "agy"),
        # Tier 3 wins (stage=None, wf=None)
        (None, None, "agy", "agy"),
        (None, None, "claude", "claude"),
        # Tier 4 hard default
        (None, None, None, "claude"),
    ],
)
def test_resolve_backend_priority_order(
    stage_backend: str | None,
    wf_backend: str | None,
    config_default: str | None,
    expected: str,
) -> None:
    stage = _stage(backend=stage_backend)
    workflow = _workflow(default_backend=wf_backend)
    result = resolve_backend(stage=stage, workflow=workflow, config_default=config_default)
    assert result == expected


def test_resolve_backend_handles_workflow_none() -> None:
    """workflow=None → tier 2 skipped, falls through to tier 3 / 4."""
    stage = _stage(backend=None)
    result = resolve_backend(stage=stage, workflow=None, config_default="agy")
    assert result == "agy"

    result2 = resolve_backend(stage=stage, workflow=None, config_default=None)
    assert result2 == "claude"


# ---------------------------------------------------------------------------
# make_backend + CliBackend Protocol
# ---------------------------------------------------------------------------


def test_make_backend_known_names() -> None:
    claude = make_backend("claude")
    assert isinstance(claude, ClaudeCodeBackend)
    assert isinstance(claude, CliBackend)

    agy = make_backend("agy")
    assert isinstance(agy, AntigravityBackend)
    assert isinstance(agy, CliBackend)


def test_make_backend_unknown_name_raises() -> None:
    with pytest.raises(UnknownBackendError, match="opus"):
        make_backend("opus")


def test_known_backends_set() -> None:
    assert _KNOWN_BACKENDS == frozenset({"claude", "agy"})
