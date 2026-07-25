"""Two-lane triage router — `wf:quick`/`wf:planned` label wins, else classify.

Decision #4: separate file for unit-testability and to keep loop.py from
growing past a readable size. Decision #13: the classify fallback dispatches
directly via CliBackend.build_argv()/parse_result(), bypassing
SubprocessStageRunner's stage/gate/worktree machinery entirely — a single
haiku RAW: call, not an agentic run.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from atlas.plumb_io import PlumbIO
    from atlas.queue_gh import Issue

_logger = logging.getLogger("atlas.triage")

_CLASSIFY_TIMEOUT_S = 120
_CLASSIFY_MODEL = "haiku"

_CLASSIFY_PROMPT_TEMPLATE = (
    "Classify this GitHub issue as either 'quick' (a single small, well-scoped "
    "change completable in one pass) or 'planned' (large enough to need a design "
    "doc / task breakdown before implementation). Respond with exactly one word "
    "('quick' or 'planned') followed by a one-line rationale.\n\n"
    "Title: {title}\nBody: {body}"
)


@dataclass(frozen=True)
class TriageResult:
    lane: Literal["quick", "planned"]
    source: Literal["label", "classify"]
    rationale: str | None = None  # populated only when source == "classify"


def triage(issue: Issue, *, plumb: PlumbIO, run_id: str) -> TriageResult:
    has_quick = "wf:quick" in issue.labels
    has_planned = "wf:planned" in issue.labels

    if has_quick and has_planned:
        _logger.warning(
            "issue #%s has both wf:quick and wf:planned labels; resolving to planned",
            issue.number,
        )
        return TriageResult(lane="planned", source="label")

    if has_planned:
        return TriageResult(lane="planned", source="label")

    if has_quick:
        return TriageResult(lane="quick", source="label")

    return _classify(issue, plumb=plumb, run_id=run_id)


def _classify(issue: Issue, *, plumb: PlumbIO, run_id: str) -> TriageResult:
    from atlas.cli_backend import UnknownBackendError, make_backend

    prompt = _CLASSIFY_PROMPT_TEMPLATE.format(title=issue.title, body=issue.body)

    try:
        backend = make_backend("claude")
    except UnknownBackendError:
        _logger.warning("triage classify: unknown backend 'claude'; defaulting to planned")
        return TriageResult(lane="planned", source="classify", rationale="backend unavailable")

    argv = backend.build_argv(
        prompt=prompt,
        model=_CLASSIFY_MODEL,
        add_dirs=[],
        timeout_s=_CLASSIFY_TIMEOUT_S,
        extra_flags={},
    )

    latency_ms = 0.0
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            timeout=_CLASSIFY_TIMEOUT_S,
            text=True,
        )
    except subprocess.TimeoutExpired:
        _logger.warning("triage classify: subprocess timed out; defaulting to planned")
        plumb.record_span(
            run_id=run_id,
            kind="plan",
            name="triage",
            status="failure",
            latency_ms=latency_ms,
            error_type="triage_timeout",
        )
        return TriageResult(lane="planned", source="classify", rationale="classify timed out")

    status, output_text, error_type = backend.parse_result(
        result.stdout, result.stderr, result.returncode
    )

    plumb.record_span(
        run_id=run_id,
        kind="plan",
        name="triage",
        status=status,
        latency_ms=latency_ms,
        error_type=error_type,
    )

    if status != "success":
        _logger.warning("triage classify failed (error_type=%s); defaulting to planned", error_type)
        return TriageResult(lane="planned", source="classify", rationale=output_text or error_type)

    lane, rationale = _parse_classify_response(output_text)
    return TriageResult(lane=lane, source="classify", rationale=rationale)


def _parse_classify_response(output_text: str) -> tuple[Literal["quick", "planned"], str]:
    stripped = output_text.strip()
    if not stripped:
        _logger.warning("triage classify: empty response; defaulting to planned")
        return "planned", "empty classifier response"

    first_word, _, rest = stripped.partition(" ")
    first_word_lower = first_word.strip().lower().strip(".:,")
    rationale = rest.strip() or stripped

    if first_word_lower == "quick":
        return "quick", rationale
    if first_word_lower == "planned":
        return "planned", rationale

    _logger.warning(
        "triage classify: unparseable response %r; defaulting to planned", stripped[:200]
    )
    return "planned", f"unparseable classifier response: {stripped[:200]}"


__all__ = ["TriageResult", "triage"]
