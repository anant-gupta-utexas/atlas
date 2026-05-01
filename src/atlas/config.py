"""TOML config loader — merges .atlas.toml (repo) over ~/.atlas/config.toml (user)."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef]
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class Config:
    repo_root: Path
    plumb_db_path: Path
    plugin_commands: dict[str, str] = field(default_factory=dict)
    timeout_overrides: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, repo_root: Path) -> "Config":
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
        }

        user_cfg = Path.home() / ".atlas" / "config.toml"
        if user_cfg.exists():
            _deep_merge(merged, _read_toml(user_cfg))

        repo_cfg = repo_root / ".atlas.toml"
        if repo_cfg.exists():
            _deep_merge(merged, _read_toml(repo_cfg))

        return cls(
            repo_root=repo_root,
            plumb_db_path=Path(str(merged["plumb_db_path"])),
            plugin_commands=dict(merged.get("plugin_commands", {})),  # type: ignore[arg-type]
            timeout_overrides={
                k: int(v)  # type: ignore[arg-type]
                for k, v in dict(merged.get("timeout_overrides", {})).items()  # type: ignore[union-attr]
            },
        )


def _read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as f:
        return tomllib.load(f)  # type: ignore[arg-type]


def _deep_merge(base: dict[str, object], override: dict[str, object]) -> None:
    """Merge *override* into *base* in-place. Nested dicts are merged recursively."""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)  # type: ignore[arg-type]
        else:
            base[k] = v
