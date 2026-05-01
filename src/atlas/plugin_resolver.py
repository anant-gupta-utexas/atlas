"""Plugin name → agent CLI command mapping (D1 resolution)."""
from __future__ import annotations

from atlas.orchestrator import RoutingDriftError

# Maps StageSpec.tool strings to the agent CLI command to invoke.
# Override individual entries via .atlas.toml [plugin_commands] table.
PLUGIN_COMMANDS: dict[str, str] = {
    "consult-experts:research": "consult-experts",
    "consult-experts:pm": "consult-experts",
    "consult-experts:tech-lead": "consult-experts",
    "dev-docs-be": "dev-docs-be",
    "plan-reviewer": "plan-reviewer",
    "code-gen-agent": "code-gen-agent",
    "code-review": "code-review",
}


def resolve(tool: str, *, overrides: dict[str, str] | None = None) -> str:
    """
    Return the agent CLI command for *tool*.

    Checks the optional *overrides* dict first (from .atlas.toml), then
    falls back to ``PLUGIN_COMMANDS``.  Raises ``RoutingDriftError`` if
    the tool is not in either mapping.
    """
    mapping = dict(PLUGIN_COMMANDS)
    if overrides:
        mapping.update(overrides)
    if tool not in mapping:
        raise RoutingDriftError(
            f"Tool {tool!r} is not in the plugin allow-list. "
            "Add it to PLUGIN_COMMANDS or .atlas.toml [plugin_commands]."
        )
    return mapping[tool]
