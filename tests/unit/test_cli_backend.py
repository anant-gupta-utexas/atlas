"""Unit tests for atlas.cli_backend — argv, parse_result, preflight, resolve."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from atlas.cli_backend import (
    _KNOWN_BACKENDS,
    CLAUDE_TOKEN_REDUCTION_RULE,
    CODEX_TOKEN_REDUCTION_RULE,
    AntigravityBackend,
    ClaudeCodeBackend,
    CliBackend,
    CodexBackend,
    CodexUsageStats,
    UnknownBackendError,
    UsageReporting,
    UsageStats,
    claude_usage_attributes,
    claude_usage_to_tokens,
    codex_usage_attributes,
    codex_usage_to_tokens,
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
# ClaudeCodeBackend — loop-mode telemetry / permission-profile argv (T-L0.4)
# ---------------------------------------------------------------------------


def test_claude_code_backend_argv_telemetry_json() -> None:
    argv = ClaudeCodeBackend().build_argv(
        prompt="p",
        model="haiku",
        add_dirs=[],
        timeout_s=60,
        extra_flags={"telemetry": "json"},
    )
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"


def test_claude_code_backend_argv_permission_profile_flags() -> None:
    argv = ClaudeCodeBackend().build_argv(
        prompt="p",
        model="haiku",
        add_dirs=[],
        timeout_s=60,
        extra_flags={
            "permission_mode": "acceptEdits",
            "allowed_tools": "Bash(git *),Edit",
            "max_turns": "10",
        },
    )
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "--allowedTools" in argv
    assert argv[argv.index("--allowedTools") + 1] == "Bash(git *),Edit"
    assert "--max-turns" in argv
    assert argv[argv.index("--max-turns") + 1] == "10"
    assert "--dangerously-skip-permissions" not in argv


@pytest.mark.parametrize(
    "extra_flags",
    [
        {},
        {"telemetry": "json"},
        {"permission_mode": "acceptEdits"},
        {"allowed_tools": "*"},
        {"max_turns": "1"},
        {
            "telemetry": "json",
            "permission_mode": "acceptEdits",
            "allowed_tools": "*",
            "max_turns": "5",
        },
    ],
)
def test_claude_code_backend_argv_never_skips_permissions(
    extra_flags: dict[str, str],
) -> None:
    argv = ClaudeCodeBackend().build_argv(
        prompt="p", model="haiku", add_dirs=[], timeout_s=60, extra_flags=extra_flags
    )
    assert "--dangerously-skip-permissions" not in argv


# ---------------------------------------------------------------------------
# ClaudeCodeBackend — parse_result JSON envelope (T-L0.4)
# ---------------------------------------------------------------------------


def _json_envelope(**overrides: object) -> str:
    import json as _json

    payload: dict[str, object] = {
        "subtype": "success",
        "result": "done",
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    payload.update(overrides)
    return _json.dumps(payload)


def test_claude_code_backend_parse_result_json_success() -> None:
    status, output_text, error_type = ClaudeCodeBackend().parse_result(_json_envelope(), "", 0)
    assert status == "success"
    assert output_text == "done"
    assert error_type is None


@pytest.mark.parametrize(
    "subtype",
    [
        "error_during_execution",
        "error_max_turns",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    ],
)
def test_claude_code_backend_parse_result_json_error_subtypes(subtype: str) -> None:
    stdout = _json_envelope(subtype=subtype, result=None)
    status, output_text, error_type = ClaudeCodeBackend().parse_result(stdout, "", 0)
    assert status == "failure"
    assert error_type == f"claude_{subtype}"


def test_claude_code_backend_parse_result_json_malformed_never_raises() -> None:
    stdout = '{"subtype": "success", "result": '  # truncated / invalid JSON
    status, output_text, error_type = ClaudeCodeBackend().parse_result(stdout, "", 0)
    assert status == "failure"
    assert output_text == stdout
    assert error_type == "claude_unparseable_json"


def test_claude_code_backend_parse_result_plain_text_unchanged() -> None:
    status, output_text, error_type = ClaudeCodeBackend().parse_result("plain text", "", 0)
    assert status == "success"
    assert output_text == "plain text"
    assert error_type is None


# ---------------------------------------------------------------------------
# ClaudeCodeBackend — parse_usage (T-L0.4)
# ---------------------------------------------------------------------------


def test_claude_code_backend_parse_usage_plain_text_is_none() -> None:
    assert ClaudeCodeBackend().parse_usage("plain text") is None


def test_claude_code_backend_parse_usage_json_envelope() -> None:
    usage = ClaudeCodeBackend().parse_usage(_json_envelope())
    assert usage == UsageStats(
        total_cost_usd=0.0123,
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )


def test_claude_code_backend_parse_usage_missing_keys_no_keyerror() -> None:
    stdout = '{"subtype": "success", "result": "done"}'
    usage = ClaudeCodeBackend().parse_usage(stdout)
    assert usage == UsageStats(
        total_cost_usd=None,
        input_tokens=None,
        output_tokens=None,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )


def test_claude_code_backend_parse_usage_malformed_json_is_none() -> None:
    assert ClaudeCodeBackend().parse_usage('{"subtype": ') is None


# ---------------------------------------------------------------------------
# ClaudeCodeBackend — the ARRAY envelope (VERIFIED, Claude Code 2.1.220)
#
# These are the regression tests for the 2026-07-26 finding: `claude -p
# --output-format json` emits a JSON *array* of stream events, not the bare
# object this module was designed against. Because the old envelope sniff
# only tested for "{", every real envelope fell through to the plain-text
# branch: parse_result returned the raw JSON blob as "output text" and
# parse_usage returned None, so no live run could ever produce telemetry.
# ---------------------------------------------------------------------------


def _stream_envelope(**result_overrides: object) -> str:
    """The real shape: stream events terminated by a `result` element."""
    result_event: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "result": "done",
        "total_cost_usd": 0.0123,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 700,
        },
    }
    result_event.update(result_overrides)
    return json.dumps(
        [
            {"type": "system", "subtype": "init", "session_id": "s1"},
            {"type": "rate_limit_event", "session_id": "s1"},
            {"type": "assistant", "message": {"role": "assistant"}},
            result_event,
        ]
    )


def test_claude_parse_result_reads_array_envelope() -> None:
    status, output_text, error_type = ClaudeCodeBackend().parse_result(_stream_envelope(), "", 0)
    assert (status, output_text, error_type) == ("success", "done", None)


def test_claude_parse_result_array_envelope_failure_subtype() -> None:
    stdout = _stream_envelope(subtype="error_max_turns", result="hit the cap")
    status, output_text, error_type = ClaudeCodeBackend().parse_result(stdout, "", 0)
    assert status == "failure"
    assert error_type == "claude_error_max_turns"
    assert output_text == "hit the cap"


def test_claude_parse_result_array_without_result_event_is_failure() -> None:
    """A truncated stream must not report a phantom success.

    Mirrors CodexBackend's turn.completed presence check.
    """
    stdout = json.dumps([{"type": "system", "subtype": "init"}, {"type": "assistant"}])
    status, _output, error_type = ClaudeCodeBackend().parse_result(stdout, "", 0)
    assert status == "failure"
    assert error_type == "claude_no_result_event"


def test_claude_parse_result_uses_last_result_event() -> None:
    stdout = json.dumps(
        [
            {"type": "result", "subtype": "success", "result": "first"},
            {"type": "result", "subtype": "success", "result": "last"},
        ]
    )
    assert ClaudeCodeBackend().parse_result(stdout, "", 0)[1] == "last"


def test_claude_parse_usage_reads_array_envelope() -> None:
    usage = ClaudeCodeBackend().parse_usage(_stream_envelope())
    assert usage == UsageStats(
        total_cost_usd=0.0123,
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=300,
        cache_read_input_tokens=700,
    )


def test_claude_usage_to_tokens_sums_disjoint_input_fields() -> None:
    """Anthropic's usage fields are disjoint — cache hits are NOT in input_tokens.

    A real captured run reported input_tokens=2 against
    cache_read_input_tokens=19589; reading input_tokens alone recorded a
    ~31.5k-token dispatch as 2 tokens.
    """
    usage = UsageStats(
        total_cost_usd=0.5,
        input_tokens=2,
        output_tokens=4,
        cache_creation_input_tokens=11973,
        cache_read_input_tokens=19589,
    )
    assert claude_usage_to_tokens(usage) == (2 + 11973 + 19589, 4)


def test_claude_usage_to_tokens_treats_missing_fields_as_zero() -> None:
    usage = UsageStats(
        total_cost_usd=None,
        input_tokens=None,
        output_tokens=None,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    assert claude_usage_to_tokens(usage) == (0, 0)


def test_claude_usage_attributes_persist_raw_breakdown_and_rule() -> None:
    """The reduction rule must be recomputable from stored data (L1 review M1)."""
    attrs = claude_usage_attributes(
        UsageStats(
            total_cost_usd=0.25,
            input_tokens=2,
            output_tokens=4,
            cache_creation_input_tokens=100,
            cache_read_input_tokens=900,
        )
    )
    assert attrs["engine"] == "claude"
    assert attrs["token_reduction_rule"] == CLAUDE_TOKEN_REDUCTION_RULE
    assert attrs["cache_read_input_tokens"] == 900
    assert attrs["total_cost_usd"] == 0.25


def test_claude_span_usage_composes_tokens_attributes_and_cost() -> None:
    usage = ClaudeCodeBackend().span_usage(_stream_envelope())
    assert usage is not None
    assert usage.tokens == (100 + 300 + 700, 50)
    assert usage.dollar_cost == 0.0123
    assert usage.attributes["engine"] == "claude"


def test_claude_span_usage_none_for_plain_text() -> None:
    assert ClaudeCodeBackend().span_usage("just some prose") is None


def test_codex_span_usage_reports_no_dollar_cost() -> None:
    """Structural, not incidental: the Codex CLI emits no cost figure at all."""
    stdout = json.dumps(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )
    usage = CodexBackend().span_usage(stdout)
    assert usage is not None
    assert usage.dollar_cost is None
    assert usage.attributes["engine"] == "codex"


def test_usage_reporting_protocol_excludes_agy() -> None:
    """agy reports no usage — the runner must not try to read any from it."""
    assert isinstance(ClaudeCodeBackend(), UsageReporting)
    assert isinstance(CodexBackend(), UsageReporting)
    assert not isinstance(AntigravityBackend(), UsageReporting)


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
# CodexBackend — build_argv
# ---------------------------------------------------------------------------

_CODEX_FIXTURES = Path(__file__).parents[1] / "fixtures" / "codex_jsonl"


def test_codex_backend_build_argv_shape() -> None:
    argv = CodexBackend().build_argv(
        prompt="do the task",
        model="gpt-5-codex",
        add_dirs=[_DIR_A],
        timeout_s=60,
        extra_flags={},
    )
    assert argv == [
        "codex",
        "exec",
        "do the task",
        "--json",
        "-C",
        str(_DIR_A),
        "--sandbox",
        "workspace-write",
        "--model",
        "gpt-5-codex",
    ]


def test_codex_backend_build_argv_uses_worktree_as_primary() -> None:
    argv = CodexBackend().build_argv(
        prompt="p",
        model="m",
        add_dirs=[_DIR_A, _DIR_B],
        timeout_s=60,
        extra_flags={},
    )
    assert argv[argv.index("-C") + 1] == str(_DIR_B)
    assert "--add-dir" in argv
    assert argv[argv.index("--add-dir") + 1] == str(_DIR_A)


def test_codex_backend_build_argv_single_dir_no_add_dir() -> None:
    argv = CodexBackend().build_argv(
        prompt="p",
        model="m",
        add_dirs=[_DIR_A],
        timeout_s=60,
        extra_flags={},
    )
    assert argv[argv.index("-C") + 1] == str(_DIR_A)
    assert "--add-dir" not in argv


def test_codex_backend_build_argv_paths_are_absolute() -> None:
    """L1 code review finding L1 — load-bearing, do not loosen.

    SubprocessStageRunner spawns every backend with cwd=atlas_root (chosen so
    Claude's workspace-scoped plugins resolve), which is NOT where a Codex run
    should write. `-C <dir>` is what redirects Codex to the worktree.

    Verified empirically against codex-cli 0.144.4 (2026-07-25): with an
    ABSOLUTE `-C` path, the agent's working root is the `-C` directory and the
    inherited cwd is irrelevant. With a RELATIVE `-C` path, codex resolves it
    against the inherited cwd and exits 1 ("No such file or directory") — i.e.
    relative paths silently couple the run to atlas_root.

    add_dirs reach build_argv as absolute paths today (orchestrator builds them
    from ctx.repo_root / ctx.worktree_path). This test pins that invariant so a
    future refactor passing relative paths fails here rather than in a live run
    that edits the wrong repo.
    """
    argv = CodexBackend().build_argv(
        prompt="p",
        model="m",
        add_dirs=[_DIR_A, _DIR_B],
        timeout_s=60,
        extra_flags={},
    )
    cd_path = Path(argv[argv.index("-C") + 1])
    assert cd_path.is_absolute(), f"-C must be absolute, got {cd_path!r}"
    for i, token in enumerate(argv):
        if token == "--add-dir":
            assert Path(argv[i + 1]).is_absolute()


def test_codex_backend_build_argv_never_bypasses_sandbox() -> None:
    argv = CodexBackend().build_argv(
        prompt="p",
        model="m",
        add_dirs=[_DIR_A, _DIR_B],
        timeout_s=60,
        extra_flags={},
    )
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert "--dangerously-bypass-hook-trust" not in argv


# ---------------------------------------------------------------------------
# CodexBackend — parse_result
# ---------------------------------------------------------------------------


def test_codex_backend_parse_result_success() -> None:
    stdout = (_CODEX_FIXTURES / "success.jsonl").read_text(encoding="utf-8")
    status, output_text, error_type = CodexBackend().parse_result(stdout, "", 0)
    assert (status, output_text, error_type) == ("success", "hi", None)


def test_codex_backend_parse_result_joins_multiple_agent_messages() -> None:
    stdout = (_CODEX_FIXTURES / "multi_message.jsonl").read_text(encoding="utf-8")
    status, output_text, error_type = CodexBackend().parse_result(stdout, "", 0)
    assert status == "success"
    assert "first message" in output_text
    assert "second message" in output_text
    assert error_type is None


def test_codex_backend_parse_result_nonzero_exit() -> None:
    stdout = (_CODEX_FIXTURES / "success.jsonl").read_text(encoding="utf-8")
    status, _, error_type = CodexBackend().parse_result(stdout, "", 1)
    assert status == "failure"
    assert error_type == "codex_nonzero_exit"


def test_codex_backend_parse_result_no_turn_completed() -> None:
    stdout = (_CODEX_FIXTURES / "truncated.jsonl").read_text(encoding="utf-8")
    status, output_text, error_type = CodexBackend().parse_result(stdout, "", 0)
    assert status == "failure"
    assert error_type == "codex_no_turn_completed"
    assert output_text == stdout


def test_codex_backend_parse_result_malformed_stream() -> None:
    stdout = (_CODEX_FIXTURES / "malformed.txt").read_text(encoding="utf-8")
    status, _, error_type = CodexBackend().parse_result(stdout, "", 0)
    assert status == "failure"
    assert error_type == "codex_no_turn_completed"


def test_codex_backend_parse_result_skips_bad_lines() -> None:
    stdout = "\n".join(
        [
            "not json",
            '{"type":"item.completed","item":{"id":"i","type":"agent_message","text":"ok"}}',
            '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
        ]
    )
    status, output_text, error_type = CodexBackend().parse_result(stdout, "", 0)
    assert status == "success"
    assert output_text == "ok"
    assert error_type is None


def test_codex_backend_parse_result_tool_only_run_is_success() -> None:
    stdout = '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
    status, output_text, error_type = CodexBackend().parse_result(stdout, "", 0)
    assert (status, output_text, error_type) == ("success", "", None)


def test_codex_backend_parse_result_skips_blank_lines_and_non_dict_item() -> None:
    stdout = "\n".join(
        [
            "",
            "   ",
            '{"type":"item.completed","item":"not-a-dict"}',
            '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
        ]
    )
    status, output_text, error_type = CodexBackend().parse_result(stdout, "", 0)
    assert (status, output_text, error_type) == ("success", "", None)


# ---------------------------------------------------------------------------
# CodexBackend — parse_usage
# ---------------------------------------------------------------------------


def test_codex_backend_parse_usage_success() -> None:
    stdout = (_CODEX_FIXTURES / "success.jsonl").read_text(encoding="utf-8")
    usage = CodexBackend().parse_usage(stdout)
    assert usage == CodexUsageStats(
        input_tokens=16668,
        output_tokens=5,
        cached_input_tokens=13056,
        reasoning_output_tokens=0,
    )


def test_codex_usage_stats_has_no_cost_field() -> None:
    """L1 code review finding N1: the Codex CLI reports no cost figure, so the
    dataclass carries no permanently-None total_cost_usd field to invite a
    downstream `if usage.total_cost_usd:`. Engine A/B in v3 is tokens-only."""
    assert not hasattr(CodexUsageStats(1, 2, 3, 4), "total_cost_usd")
    assert "total_cost_usd" not in CodexUsageStats.__dataclass_fields__


def test_codex_backend_parse_usage_no_turn_completed() -> None:
    stdout = (_CODEX_FIXTURES / "malformed.txt").read_text(encoding="utf-8")
    assert CodexBackend().parse_usage(stdout) is None


def test_codex_backend_parse_usage_skips_blank_lines() -> None:
    stdout = "\n".join(
        [
            "",
            '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
        ]
    )
    usage = CodexBackend().parse_usage(stdout)
    assert usage is not None
    assert usage.input_tokens == 1


def test_codex_backend_usage_to_plumb_tokens() -> None:
    """Pending Decision #4 RESOLVED (T-L1.1, 2026-07-26): sub-fields are subsets.

    Measured on codex-cli 0.144.4 with a cold/warm capture pair: input_tokens
    held flat (68719 -> 69161) while cached_input_tokens rose 29% (48384 ->
    62464). Under the old addend rule input_tokens had to fall by ~14k; it
    did not. cached_input_tokens is the served-from-cache portion OF
    input_tokens, per OpenAI's convention.

    Adding them, as the v1 rule did, inflated this fixture's input by 78%.
    """
    usage = CodexUsageStats(
        input_tokens=16668,
        output_tokens=5,
        cached_input_tokens=13056,
        reasoning_output_tokens=0,
    )
    in_tokens, out_tokens = codex_usage_to_tokens(usage)
    assert in_tokens == 16668
    assert out_tokens == 5


def test_codex_reasoning_tokens_are_not_added_to_output() -> None:
    """reasoning_output_tokens is a subset of output_tokens (OpenAI convention)."""
    usage = CodexUsageStats(
        input_tokens=100,
        output_tokens=662,
        cached_input_tokens=50,
        reasoning_output_tokens=159,
    )
    assert codex_usage_to_tokens(usage) == (100, 662)


def test_codex_usage_attributes_preserves_raw_breakdown() -> None:
    """L1 code review finding M1 — load-bearing, do not loosen.

    plumb collapses tokens=(in, out) into one summed spans.tokens column, so
    neither the in/out split nor the cached/reasoning breakdown survives there.
    This mechanism has now proven itself: Pending Decision #4's
    cached-as-addend assumption WAS backwards (T-L1.1, 2026-07-26), and spans
    written under the v1 rule are recomputable precisely because the raw
    breakdown and the rule name were both persisted here.
    """
    usage = CodexUsageStats(
        input_tokens=16668,
        output_tokens=5,
        cached_input_tokens=13056,
        reasoning_output_tokens=0,
    )
    attrs = codex_usage_attributes(usage)

    # Every raw field survives, unreduced.
    assert attrs["input_tokens"] == 16668
    assert attrs["cached_input_tokens"] == 13056
    assert attrs["output_tokens"] == 5
    assert attrs["reasoning_output_tokens"] == 0
    # The reduction rule is stamped, so a later correction knows what to undo.
    assert attrs["token_reduction_rule"] == CODEX_TOKEN_REDUCTION_RULE
    assert attrs["engine"] == "codex"

    # The recorded breakdown must actually reproduce the reduced total, or the
    # "recomputable" claim above is false. Under the v2 subset rule the totals
    # ARE the raw top-level fields; the cached/reasoning sub-fields are kept
    # so the superseded v1 addend total stays derivable from stored history.
    in_tokens, out_tokens = codex_usage_to_tokens(usage)
    assert attrs["input_tokens"] == in_tokens
    assert attrs["output_tokens"] == out_tokens
    v1_in = attrs["input_tokens"] + attrs["cached_input_tokens"]
    assert v1_in == 29724  # what a v1-stamped span recorded, recomputable


def test_codex_usage_attributes_is_json_serializable() -> None:
    """plumb validates attributes as JSON-serializable and fail-closes if not."""
    usage = CodexUsageStats(
        input_tokens=None,
        output_tokens=None,
        cached_input_tokens=None,
        reasoning_output_tokens=None,
    )
    assert json.loads(json.dumps(codex_usage_attributes(usage)))["engine"] == "codex"


# ---------------------------------------------------------------------------
# CodexBackend — preflight
# ---------------------------------------------------------------------------


def test_codex_backend_preflight_missing_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    with patch("subprocess.run") as mock_run:
        result = CodexBackend().preflight()
        mock_run.assert_not_called()
    assert result is not None
    msg, error_type = result
    assert error_type == "codex_missing_auth"
    assert "OPENAI_API_KEY" in msg


def test_codex_backend_preflight_env_var_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert CodexBackend().preflight() is None


def test_codex_backend_preflight_auth_file_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    assert CodexBackend().preflight() is None


def test_codex_backend_preflight_defaults_to_home_codex_dir_when_codex_home_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No $CODEX_HOME -> falls back to ~/.codex/auth.json (Path.home())."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    assert CodexBackend().preflight() is None


# ---------------------------------------------------------------------------
# CodexBackend — registration
# ---------------------------------------------------------------------------


def test_make_backend_codex() -> None:
    assert isinstance(make_backend("codex"), CodexBackend)


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
    assert _KNOWN_BACKENDS == frozenset({"claude", "agy", "codex"})


# ---------------------------------------------------------------------------
# T-L1.1 — real captured Codex output (codex-cli 0.144.4, 2026-07-26)
#
# These fixtures are live captures, not constructed. They close the write-path
# and failure-path gaps the L1 TRS flagged UNVERIFIED.
# ---------------------------------------------------------------------------

_REAL_FIXTURES = Path(__file__).parents[1] / "fixtures" / "codex_jsonl"


def test_codex_real_write_heavy_capture_parses() -> None:
    """Write-path event types: item.started/completed x command_execution,
    file_change, agent_message. The edit really landed under workspace-write."""
    stdout = (_REAL_FIXTURES / "write_heavy_real.jsonl").read_text(encoding="utf-8")
    backend = CodexBackend()

    status, text, error_type = backend.parse_result(stdout, "", 0)
    assert status == "success"
    assert error_type is None
    assert text

    usage = backend.span_usage(stdout)
    assert usage is not None
    # Subset rule: the reduced input is input_tokens itself, not input+cached.
    assert usage.tokens == (68700, 392)
    assert usage.attributes["cached_input_tokens"] == 61440
    assert usage.dollar_cost is None


def test_codex_real_sandbox_denial_still_reports_success() -> None:
    """No failure event type exists in 0.144.4 — VERIFIED, not assumed.

    A sandbox-blocked write exits 0 and still emits turn.completed; the agent
    just says it couldn't comply. This is the honest consequence of L1
    Resolved Decision #8 (status is exit-code-only) and is exactly what the
    `verify` stage and the PR gate exist to catch. A parser that inferred
    failure from event content would be guessing.
    """
    stdout = (_REAL_FIXTURES / "sandbox_denied_real.jsonl").read_text(encoding="utf-8")
    status, _text, error_type = CodexBackend().parse_result(stdout, "", 0)
    assert status == "success"
    assert error_type is None


def test_codex_hard_failure_emits_no_stdout() -> None:
    """A preflight failure (e.g. untrusted dir) exits nonzero with empty stdout.

    Captured live: stdout was 0 bytes, the message went to stderr. parse_result
    must report failure from the exit code rather than looking for an event.
    """
    status, _text, error_type = CodexBackend().parse_result("", "not a trusted directory", 1)
    assert status == "failure"
    assert error_type is not None


# ---------------------------------------------------------------------------
# resolve_backend — the override tier (2026-07-26)
# ---------------------------------------------------------------------------


def test_override_beats_workflow_default_backend() -> None:
    """Regression: `--backend codex --workflow loop_dev` silently ran claude.

    Every shipped loop workflow declares `default_backend:`, so folding the
    override into the config tier made it permanently unreachable. Confirmed
    live 2026-07-26 — the run's spans came back stamped engine: claude.
    """
    assert (
        resolve_backend(
            stage=_stage(),
            workflow=_workflow(default_backend="claude"),
            config_default=None,
            override="codex",
        )
        == "codex"
    )


def test_override_beats_stage_pin() -> None:
    """An explicit run-scoped instruction outranks a per-stage YAML pin too."""
    assert (
        resolve_backend(
            stage=_stage(backend="claude"),
            workflow=_workflow(default_backend="claude"),
            config_default=None,
            override="codex",
        )
        == "codex"
    )


def test_no_override_preserves_original_tier_order() -> None:
    """Absent an override, TRD-v2 §3.4's original order is untouched."""
    assert (
        resolve_backend(
            stage=_stage(backend="agy"), workflow=_workflow("claude"), config_default="codex"
        )
        == "agy"
    )
    assert (
        resolve_backend(stage=_stage(), workflow=_workflow("claude"), config_default="codex")
        == "claude"
    )
    assert resolve_backend(stage=_stage(), workflow=None, config_default="codex") == "codex"
    assert resolve_backend(stage=_stage(), workflow=None, config_default=None) == "claude"
