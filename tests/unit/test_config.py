"""Unit tests for config.py — TOML loader + merge logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.config import Config, LoopConfig, _deep_merge

# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------


def test_deep_merge_scalar_override() -> None:
    base: dict[str, object] = {"a": 1, "b": 2}
    _deep_merge(base, {"b": 99})
    assert base == {"a": 1, "b": 99}


def test_deep_merge_nested_dict_merged_not_replaced() -> None:
    base: dict[str, object] = {"d": {"x": 1, "y": 2}}
    _deep_merge(base, {"d": {"y": 99, "z": 3}})
    assert base == {"d": {"x": 1, "y": 99, "z": 3}}


def test_deep_merge_adds_new_key() -> None:
    base: dict[str, object] = {"a": 1}
    _deep_merge(base, {"b": 2})
    assert base["b"] == 2


# ---------------------------------------------------------------------------
# Config.load — defaults (no .atlas.toml, no ~/.atlas/config.toml)
# ---------------------------------------------------------------------------


def test_config_load_defaults(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path)
    assert cfg.repo_root == tmp_path
    assert cfg.plumb_db_path == Path.home() / ".plumb" / "plumb.db"
    assert cfg.plugin_commands == {}
    assert cfg.timeout_overrides == {}


# ---------------------------------------------------------------------------
# Config.load — repo-local .atlas.toml
# ---------------------------------------------------------------------------


def test_config_load_repo_toml_overrides_plumb_db(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text('plumb_db_path = "/custom/plumb.db"\n')
    cfg = Config.load(tmp_path)
    assert cfg.plumb_db_path == Path("/custom/plumb.db")


def test_config_load_repo_toml_plugin_commands(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text('[plugin_commands]\n"code-gen-agent" = "my-custom-agent"\n')
    cfg = Config.load(tmp_path)
    assert cfg.plugin_commands["code-gen-agent"] == "my-custom-agent"


def test_config_load_repo_toml_timeout_overrides(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text("[timeout_overrides]\ncode_gen = 300\n")
    cfg = Config.load(tmp_path)
    assert cfg.timeout_overrides["code_gen"] == 300


# ---------------------------------------------------------------------------
# Config.load — user config merged under repo config
# ---------------------------------------------------------------------------


def test_config_load_repo_takes_priority_over_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repo .atlas.toml must win over ~/.atlas/config.toml."""
    user_dir = tmp_path / "home" / ".atlas"
    user_dir.mkdir(parents=True)
    (user_dir / "config.toml").write_text('plumb_db_path = "/user/plumb.db"\n')

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".atlas.toml").write_text('plumb_db_path = "/repo/plumb.db"\n')

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Patch Path.home() to use our fake home
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    cfg = Config.load(repo_dir)
    assert cfg.plumb_db_path == Path("/repo/plumb.db")


def test_config_load_user_config_used_when_no_repo_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_dir = tmp_path / "home" / ".atlas"
    user_dir.mkdir(parents=True)
    (user_dir / "config.toml").write_text('plumb_db_path = "/user/plumb.db"\n')

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()  # no .atlas.toml

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    cfg = Config.load(repo_dir)
    assert cfg.plumb_db_path == Path("/user/plumb.db")


# ---------------------------------------------------------------------------
# T3.5 — Config.default_backend
# ---------------------------------------------------------------------------


def test_config_default_backend_from_toml(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text('[backend]\ndefault = "agy"\n')
    cfg = Config.load(tmp_path)
    assert cfg.default_backend == "agy"


def test_config_default_backend_fallback(tmp_path: Path) -> None:
    """No [backend] section → falls back to 'claude'."""
    cfg = Config.load(tmp_path)
    assert cfg.default_backend == "claude"


def test_config_default_backend_malformed_section(tmp_path: Path) -> None:
    """Malformed backend value (not a table) → defaults safely to 'claude'."""
    toml = tmp_path / ".atlas.toml"
    toml.write_text('backend = "claude"\n')
    cfg = Config.load(tmp_path)
    assert cfg.default_backend == "claude"


# ---------------------------------------------------------------------------
# T-L2.3 — LoopConfig / Config.loop
# ---------------------------------------------------------------------------


def test_loop_config_defaults_no_section(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path)
    assert cfg.loop == LoopConfig()


def test_loop_config_section_overrides_defaults(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text(
        "[loop]\n"
        'repos = ["anant-gupta-utexas/atlas"]\n'
        "poll_interval_s = 30\n"
        "max_runs_per_day = 5\n"
        "max_dollars_per_day = 2.5\n"
        "max_turns = 20\n"
        "no_progress_limit = 2\n"
        "identical_error_limit = 4\n"
        "cooldown_min = 15\n"
        "concurrency = 1\n"
        'trusted_authors = ["anant-gupta-utexas"]\n'
    )
    cfg = Config.load(tmp_path)
    assert cfg.loop.repos == ("anant-gupta-utexas/atlas",)
    assert cfg.loop.poll_interval_s == 30
    assert cfg.loop.max_runs_per_day == 5
    assert cfg.loop.max_dollars_per_day == 2.5
    assert cfg.loop.max_turns == 20
    assert cfg.loop.no_progress_limit == 2
    assert cfg.loop.identical_error_limit == 4
    assert cfg.loop.cooldown_min == 15
    assert cfg.loop.concurrency == 1
    assert cfg.loop.trusted_authors == ("anant-gupta-utexas",)


def test_loop_config_concurrency_not_one_raises() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        LoopConfig(concurrency=2)


def test_loop_config_trusted_authors_absent_is_empty_tuple(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text('[loop]\nrepos = ["a/b"]\n')
    cfg = Config.load(tmp_path)
    assert cfg.loop.trusted_authors == ()


def test_loop_config_toml_concurrency_not_one_raises(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text("[loop]\nconcurrency = 2\n")
    with pytest.raises(ValueError, match="concurrency"):
        Config.load(tmp_path)
