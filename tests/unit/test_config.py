"""Unit tests for config.py — TOML loader + merge logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.config import Config, LoopConfig, RepoTarget, _deep_merge


def _init_git_repo(path: Path) -> Path:
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


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
    repo_dir = _init_git_repo(tmp_path / "atlas")
    toml = tmp_path / ".atlas.toml"
    toml.write_text(
        "[loop]\n"
        "poll_interval_s = 30\n"
        "max_runs_per_day = 5\n"
        "max_dollars_per_day = 2.5\n"
        "max_turns = 20\n"
        "no_progress_limit = 2\n"
        "identical_error_limit = 4\n"
        "cooldown_min = 15\n"
        "concurrency = 1\n"
        "\n"
        "[[loop.repo]]\n"
        f'github = "anant-gupta-utexas/atlas"\n'
        f'local_path = "{repo_dir}"\n'
        'trusted_authors = ["anant-gupta-utexas"]\n'
    )
    cfg = Config.load(tmp_path)
    assert cfg.loop.repos == (
        RepoTarget(
            github="anant-gupta-utexas/atlas",
            local_path=repo_dir,
            trusted_authors=("anant-gupta-utexas",),
        ),
    )
    assert cfg.loop.poll_interval_s == 30
    assert cfg.loop.max_runs_per_day == 5
    assert cfg.loop.max_dollars_per_day == 2.5
    assert cfg.loop.max_turns == 20
    assert cfg.loop.no_progress_limit == 2
    assert cfg.loop.identical_error_limit == 4
    assert cfg.loop.cooldown_min == 15
    assert cfg.loop.concurrency == 1


def test_loop_config_concurrency_below_one_raises() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        LoopConfig(concurrency=0)


def test_loop_config_concurrency_above_one_no_longer_raises() -> None:
    """Phase L4 lifts the L2/L3-era concurrency==1 guard."""
    cfg = LoopConfig(concurrency=3)
    assert cfg.concurrency == 3


def test_loop_config_trusted_authors_absent_is_empty_tuple(tmp_path: Path) -> None:
    repo_dir = _init_git_repo(tmp_path / "a")
    toml = tmp_path / ".atlas.toml"
    toml.write_text(f'[[loop.repo]]\ngithub = "a/b"\nlocal_path = "{repo_dir}"\n')
    cfg = Config.load(tmp_path)
    assert cfg.loop.repos[0].trusted_authors == ()


def test_loop_config_toml_concurrency_zero_raises(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text("[loop]\nconcurrency = 0\n")
    with pytest.raises(ValueError, match="concurrency"):
        Config.load(tmp_path)


# ---------------------------------------------------------------------------
# T-L4.1 — RepoTarget / [[loop.repo]] table-array parsing
# ---------------------------------------------------------------------------


def test_legacy_flat_repos_shape_hard_fails_with_migration_message(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text('[loop]\nrepos = ["anant-gupta-utexas/atlas"]\n')
    with pytest.raises(ValueError, match=r"\[\[loop\.repo\]\]"):
        Config.load(tmp_path)


def test_repo_target_missing_local_path_key_hard_fails(tmp_path: Path) -> None:
    """A [[loop.repo]] entry missing local_path must not silently default to
    repo_root (which could accidentally 'work' if repo_root itself is a git
    repo, masking a config typo) — it must fail loudly instead."""
    toml = tmp_path / ".atlas.toml"
    toml.write_text('[[loop.repo]]\ngithub = "a/b"\n')
    with pytest.raises(ValueError, match="local_path"):
        Config.load(tmp_path)


def test_repo_target_missing_github_key_hard_fails(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    toml.write_text(f'[[loop.repo]]\nlocal_path = "{tmp_path}"\n')
    with pytest.raises(ValueError, match="github"):
        Config.load(tmp_path)


def test_repo_target_local_path_must_exist_and_be_a_git_repo(tmp_path: Path) -> None:
    toml = tmp_path / ".atlas.toml"
    missing = tmp_path / "nope"
    toml.write_text(f'[[loop.repo]]\ngithub = "a/b"\nlocal_path = "{missing}"\n')
    with pytest.raises(ValueError, match="does not exist"):
        Config.load(tmp_path)


def test_repo_target_local_path_resolved_to_absolute(tmp_path: Path) -> None:
    repo_dir = _init_git_repo(tmp_path / "child")
    toml = tmp_path / ".atlas.toml"
    toml.write_text('[[loop.repo]]\ngithub = "a/b"\nlocal_path = "child"\n')
    cfg = Config.load(tmp_path)
    assert cfg.loop.repos[0].local_path == repo_dir.resolve()
    assert cfg.loop.repos[0].local_path.is_absolute()


def test_multiple_repo_targets_parsed_in_order(tmp_path: Path) -> None:
    repo_a = _init_git_repo(tmp_path / "a")
    repo_b = _init_git_repo(tmp_path / "b")
    toml = tmp_path / ".atlas.toml"
    toml.write_text(
        f'[[loop.repo]]\ngithub = "org/a"\nlocal_path = "{repo_a}"\n\n'
        f'[[loop.repo]]\ngithub = "org/b"\nlocal_path = "{repo_b}"\n'
    )
    cfg = Config.load(tmp_path)
    assert [t.github for t in cfg.loop.repos] == ["org/a", "org/b"]
