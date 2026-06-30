from __future__ import annotations

import re
from dataclasses import dataclass

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# plumb's closed set (TRD-v2 §3.1, §7) — validated against at load time.
SPAN_KINDS: frozenset[str] = frozenset({"llm", "tool", "subagent", "handoff", "plan", "verify"})


@dataclass(frozen=True)
class StageSpec:
    index: int
    name: str
    span_kind: str  # constrained to SPAN_KINDS
    tool: str
    gate_label: str | None  # None for stage 3 (reviewed by stage 4)
    gate_index: int | None  # None where gate_label is None
    isolate: bool = False  # stage runs inside an isolated git worktree
    gate_is_async: bool = False  # gate is written by the post-commit hook, not the orchestrator
    backend: str | None = None  # threaded through; unused until Phase 3
    timeout_s: int | None = None  # per-stage subprocess timeout; None → orchestrator default
