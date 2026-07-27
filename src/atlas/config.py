"""TOML config loader — merges .atlas.toml (repo) over ~/.atlas/config.toml (user)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LoopConfig:
    """``[loop]`` config block — atlas loop daemon settings (TRD-v3 §7)."""

    repos: tuple[str, ...] = ()
    poll_interval_s: int = 60
    max_runs_per_day: int = 20
    max_dollars_per_day: float = 10.0
    max_turns: int = 40
    no_progress_limit: int = 3
    identical_error_limit: int = 5
    cooldown_min: int = 30
    concurrency: int = 1  # frozen at 1 for v3.0-v3.2
    trusted_authors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.concurrency != 1:
            raise ValueError("concurrency > 1 is not supported until Phase L4")


@dataclass(frozen=True)
class Config:
    repo_root: Path
    plumb_db_path: Path
    plugin_commands: dict[str, str] = field(default_factory=dict)
    timeout_overrides: dict[str, int] = field(default_factory=dict)
    model: str = "haiku"  # default to haiku for cost efficiency
    default_backend: str = "claude"  # from .atlas.toml [backend] default
    # Per-engine model names from .atlas.toml [backend.models], e.g.
    #   [backend.models]
    #   codex = "gpt-5.1-codex"
    # Model names are engine-specific; `model` above is the Claude one. An
    # engine with no entry gets "" and falls back to its own CLI default.
    backend_models: dict[str, str] = field(default_factory=dict)
    loop: LoopConfig = field(default_factory=LoopConfig)

    @classmethod
    def load(cls, repo_root: Path) -> Config:
        """
        Load config by merging:
          1. Built-in defaults
          2. ``~/.atlas/config.toml`` (user-wide)
          3. ``<repo_root>/.atlas.toml`` (repo-local, highest priority)
        """
        merged: dict[str, object] = {
            "plumb_db_path": str(Path.home() / ".plumb" / "plumb.db"),
            "plugin_commands": {},
            "timeout_overrides": {},
            "model": "haiku",
        }

        user_cfg = Path.home() / ".atlas" / "config.toml"
        if user_cfg.exists():
            _deep_merge(merged, _read_toml(user_cfg))

        repo_cfg = repo_root / ".atlas.toml"
        if repo_cfg.exists():
            _deep_merge(merged, _read_toml(repo_cfg))

        raw_plugin = merged.get("plugin_commands", {})
        plugin_commands: dict[str, str] = (
            {str(k): str(v) for k, v in raw_plugin.items()} if isinstance(raw_plugin, dict) else {}
        )
        raw_timeout = merged.get("timeout_overrides", {})
        timeout_overrides: dict[str, int] = (
            {str(k): int(v) for k, v in raw_timeout.items()}
            if isinstance(raw_timeout, dict)
            else {}
        )
        backend_section = merged.get("backend", {})
        default_backend: str = (
            str(backend_section.get("default", "claude"))
            if isinstance(backend_section, dict)
            else "claude"
        )
        raw_models = backend_section.get("models", {}) if isinstance(backend_section, dict) else {}
        backend_models: dict[str, str] = (
            {str(k): str(v) for k, v in raw_models.items()} if isinstance(raw_models, dict) else {}
        )
        loop_section = merged.get("loop", {})
        loop_cfg = _parse_loop_config(loop_section if isinstance(loop_section, dict) else {})
        return cls(
            repo_root=repo_root,
            plumb_db_path=Path(str(merged["plumb_db_path"])),
            plugin_commands=plugin_commands,
            timeout_overrides=timeout_overrides,
            model=str(merged.get("model", "haiku")),
            default_backend=default_backend,
            backend_models=backend_models,
            loop=loop_cfg,
        )


def _int_field(section: dict[str, object], key: str, default: int) -> int:
    raw = section.get(key, default)
    return int(raw) if isinstance(raw, (int, float, str)) else default


def _float_field(section: dict[str, object], key: str, default: float) -> float:
    raw = section.get(key, default)
    return float(raw) if isinstance(raw, (int, float, str)) else default


def _parse_loop_config(section: dict[str, object]) -> LoopConfig:
    defaults = LoopConfig()
    raw_repos = section.get("repos", defaults.repos)
    repos = tuple(str(r) for r in raw_repos) if isinstance(raw_repos, list) else defaults.repos
    raw_trusted = section.get("trusted_authors", defaults.trusted_authors)
    trusted_authors = (
        tuple(str(a) for a in raw_trusted)
        if isinstance(raw_trusted, list)
        else defaults.trusted_authors
    )
    return LoopConfig(
        repos=repos,
        poll_interval_s=_int_field(section, "poll_interval_s", defaults.poll_interval_s),
        max_runs_per_day=_int_field(section, "max_runs_per_day", defaults.max_runs_per_day),
        max_dollars_per_day=_float_field(
            section, "max_dollars_per_day", defaults.max_dollars_per_day
        ),
        max_turns=_int_field(section, "max_turns", defaults.max_turns),
        no_progress_limit=_int_field(section, "no_progress_limit", defaults.no_progress_limit),
        identical_error_limit=_int_field(
            section, "identical_error_limit", defaults.identical_error_limit
        ),
        cooldown_min=_int_field(section, "cooldown_min", defaults.cooldown_min),
        concurrency=_int_field(section, "concurrency", defaults.concurrency),
        trusted_authors=trusted_authors,
    )


def _read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as f:
        return tomllib.load(f)


def _deep_merge(base: dict[str, object], override: dict[str, object]) -> None:
    """Merge *override* into *base* in-place. Nested dicts are merged recursively."""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)  # type: ignore[arg-type]
        else:
            base[k] = v
