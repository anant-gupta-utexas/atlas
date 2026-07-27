"""TOML config loader — merges .atlas.toml (repo) over ~/.atlas/config.toml (user)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RepoTarget:
    """One `[[loop.repo]]` entry — a GitHub repo paired with its local checkout
    (Phase L4, Pending Decision #1). ``trusted_authors`` is per-target
    (Decision #11): repo visibility/authorship is a property of each repo
    independently, so a global allowlist can't express "atlas is
    private/single-author but plumb is public"."""

    github: str
    local_path: Path
    trusted_authors: tuple[str, ...] = ()


_LEGACY_REPOS_MIGRATION_MSG = (
    "[loop].repos (a flat string list) is no longer supported as of Phase "
    "L4. Migrate to the [[loop.repo]] table-array shape, e.g.:\n\n"
    "[[loop.repo]]\n"
    'github = "owner/repo"\n'
    'local_path = "/abs/path/to/repo"\n'
)


@dataclass(frozen=True)
class LoopConfig:
    """``[loop]`` config block — atlas loop daemon settings (TRD-v3 §7)."""

    repos: tuple[RepoTarget, ...] = ()
    poll_interval_s: int = 60
    max_runs_per_day: int = 20
    max_dollars_per_day: float = 10.0
    max_turns: int = 40
    no_progress_limit: int = 3
    identical_error_limit: int = 5
    cooldown_min: int = 30
    concurrency: int = 1

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")


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
        loop_cfg = _parse_loop_config(
            loop_section if isinstance(loop_section, dict) else {}, repo_root=repo_root
        )
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


def _parse_repo_targets(section: dict[str, object], *, repo_root: Path) -> tuple[RepoTarget, ...]:
    """Parse `[[loop.repo]]` table-array entries into `RepoTarget`s.

    Hard-fails (loudly, at load time) on the pre-L4 flat `repos = [...]`
    shape rather than silently ignoring it — a stale config that parses but
    dispatches to no target is worse than a startup error naming the fix
    (Pending Decision #1).
    """
    if "repos" in section:
        raise ValueError(_LEGACY_REPOS_MIGRATION_MSG)

    raw_targets = section.get("repo", [])
    if not isinstance(raw_targets, list):
        return ()

    targets: list[RepoTarget] = []
    for entry in raw_targets:
        if not isinstance(entry, dict):
            continue
        if "github" not in entry or "local_path" not in entry:
            raise ValueError(
                "[[loop.repo]] entry is missing a required key: both 'github' and "
                f"'local_path' must be set (got: {entry!r})"
            )
        github = str(entry["github"])
        local_path = Path(str(entry["local_path"])).expanduser()
        if not local_path.is_absolute():
            local_path = repo_root / local_path
        local_path = local_path.resolve()

        if not (local_path / ".git").exists():
            raise ValueError(
                f"[[loop.repo]] github={github!r} local_path={local_path} does not exist "
                "or is not a git repo (no .git found)"
            )

        raw_trusted = entry.get("trusted_authors", [])
        trusted_authors = (
            tuple(str(a) for a in raw_trusted) if isinstance(raw_trusted, list) else ()
        )
        targets.append(
            RepoTarget(github=github, local_path=local_path, trusted_authors=trusted_authors)
        )
    return tuple(targets)


def _parse_loop_config(section: dict[str, object], *, repo_root: Path) -> LoopConfig:
    defaults = LoopConfig()
    return LoopConfig(
        repos=_parse_repo_targets(section, repo_root=repo_root),
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
