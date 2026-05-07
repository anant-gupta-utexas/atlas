"""TOML config loader — merges .atlas.toml (repo) over ~/.atlas/config.toml (user)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    repo_root: Path
    plumb_db_path: Path
    plugin_commands: dict[str, str] = field(default_factory=dict)
    timeout_overrides: dict[str, int] = field(default_factory=dict)
    model: str = "haiku"  # default to haiku for cost efficiency

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
        return cls(
            repo_root=repo_root,
            plumb_db_path=Path(str(merged["plumb_db_path"])),
            plugin_commands=plugin_commands,
            timeout_overrides=timeout_overrides,
            model=str(merged.get("model", "haiku")),
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
