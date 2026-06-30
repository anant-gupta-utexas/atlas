"""Plugin name → agent slash-command mapping (D1 resolution).

Values are passed to ``claude -p`` as the prompt prefix.
- Slash-command form: ``"/PLUGIN:cmd"`` or ``"/skill-name"`` — prepended with "/"
- Raw prompt form: ``"RAW:<text>"`` — the text after "RAW:" is used verbatim as the prompt prefix

Commands from installed plugins use their namespaced form ``PLUGIN:command-name``
where required; skills use their bare name.

Resolution order (§3.5): ``.atlas.toml [plugin_commands.<tool>]`` overrides >
the workflow YAML's ``tool`` field (i.e. ``StageSpec.tool`` itself, already the
primary source — there's no separate lookup for it) > ``PLUGIN_COMMANDS``.
``PLUGIN_COMMANDS`` below is the **dev-pipeline-only** fallback table; non-dev
workflows are expected to either use a ``RAW:``-prefixed ``tool`` string
(bypassing resolution entirely) or supply a ``.atlas.toml`` override — a tool
string absent from both raises ``RoutingDriftError``.
"""

from __future__ import annotations

from atlas.orchestrator import RoutingDriftError

# Dev-pipeline-only fallback. Maps StageSpec.tool strings to the command to
# invoke. Prefix with "RAW:" for stages that use a plain prompt instead of a
# slash command. Non-dev workflows must not rely on this table.
PLUGIN_COMMANDS: dict[str, str] = {
    # consult-experts is a DEV-ESSENTIALS skill (bare name, no namespace)
    "consult-experts:research": "consult-experts",
    "consult-experts:pm": "consult-experts",
    "consult-experts:tech-lead": "consult-experts",
    # dev-docs-be is a DEV-ESSENTIALS plugin command (namespaced)
    "dev-docs-be": "DEV-ESSENTIALS:dev-docs-be",
    # plan-reviewer is a DEV-ESSENTIALS agent — invoked via Agent tool inside claude,
    # so we use consult-experts with a plan-review role framing
    "plan-reviewer": "consult-experts",
    # code-gen-agent: no dedicated plugin — raw prompt instructs claude to implement
    "code-gen-agent": "RAW:Implement the following task by writing and committing code:",
    # code-review is a DEV-ESSENTIALS plugin command (namespaced)
    "code-review": "DEV-ESSENTIALS:code-review",
}


def resolve(tool: str, *, overrides: dict[str, str] | None = None) -> str:
    """
    Return the command string for *tool*.

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


def build_prompt(cmd: str, task: str, context_hint: str) -> str:
    """Build the full prompt string for ``claude -p``."""
    if cmd.startswith("RAW:"):
        raw_prefix = cmd[4:]
        return f"{raw_prefix}\n\n{task}\n\n{context_hint}"
    return f"/{cmd} {task}\n\n{context_hint}"
