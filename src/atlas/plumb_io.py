"""Plumb wrapper — exposes the only calls the orchestrator needs."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

try:
    from plumb import ScorerKind, SpanKind, SpanStatus, run as plumb_run
    from plumb.core.entities import Example, ExampleSource

    _PLUMB_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover — only absent in CI without plumb
    _PLUMB_AVAILABLE = False

if TYPE_CHECKING:
    from atlas.orchestrator import GateDecision


class PlumbIO:
    """
    Thin wrapper around plumb's run context manager.

    Phase 1: instantiate with ``real=False`` (the default when plumb is absent)
    to get a no-op implementation that returns synthetic IDs.

    Phase 2+: instantiate with ``real=True`` to use the actual plumb API.
    The caller is responsible for keeping the run open across all step() calls.
    """

    def __init__(self, *, real: bool = True, task_id: str = "") -> None:
        self._real = real and _PLUMB_AVAILABLE
        self._task_id = task_id
        self._run_handle: object | None = None
        self._run_id: str | None = None

        # In-memory record for unit tests / stubs
        self.spans: list[dict] = []  # type: ignore[type-arg]
        self.scores: list[dict] = []  # type: ignore[type-arg]
        self.examples: list[dict] = []  # type: ignore[type-arg]
        self._closed = False

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def open_run(self, *, task: str) -> str:
        """Start a plumb run (or return a synthetic id). Returns run_id."""
        if not self._real:
            self._run_id = _make_id()
            return self._run_id

        ctx = plumb_run(task_id=task, kind="online")  # type: ignore[call-arg]
        self._run_handle = ctx.__enter__()  # type: ignore[attr-defined]
        self._run_id = self._run_handle.run_id  # type: ignore[attr-defined]
        return self._run_id

    def close_run(self, *, run_id: str, status: str) -> None:
        """Close the plumb run. No-op if already closed."""
        if self._closed:
            return
        self._closed = True
        if not self._real or self._run_handle is None:
            return
        exc: BaseException | None = None
        if status != "success":
            exc = RuntimeError(status)
        try:
            self._run_handle.__exit__(  # type: ignore[attr-defined]
                type(exc), exc, None
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Span / score / example writes
    # ------------------------------------------------------------------

    def record_span(
        self,
        *,
        run_id: str,
        kind: str,
        name: str,
        status: str,
        latency_ms: float,
        error_type: str | None,
    ) -> str:
        """Buffer a span in plumb. Returns span_id."""
        if self._real and self._run_handle is not None:
            span_id: str = self._run_handle.add_span(  # type: ignore[attr-defined]
                kind,
                name,
                latency_ms=latency_ms,
                status=status,
                error_type=error_type,
            )
            return span_id

        span_id = _make_id()
        self.spans.append(
            {
                "span_id": span_id,
                "run_id": run_id,
                "kind": kind,
                "name": name,
                "status": status,
                "latency_ms": latency_ms,
                "error_type": error_type,
            }
        )
        return span_id

    def record_user_signal(
        self,
        *,
        run_id: str,
        span_id: str,
        metric: str,
        decision: "GateDecision",
    ) -> None:
        """Buffer a user-signal score in plumb."""
        if self._real and self._run_handle is not None:
            self._run_handle.add_score(  # type: ignore[attr-defined]
                metric,
                "user_signal",
                value_label=decision.label,
                span_id=span_id,
            )
            return

        self.scores.append(
            {
                "run_id": run_id,
                "span_id": span_id,
                "metric": metric,
                "scorer": "user_signal",
                "value_label": decision.label,
                "rationale": decision.reason,
            }
        )

    def write_example(
        self,
        *,
        run_id: str,
        span_id: str,
        inputs: str,
        expected: str | None,
    ) -> None:
        """Write an examples row (rejected-artifact capture)."""
        inputs_hash = _sha256(inputs)
        expected_hash = _sha256(expected) if expected is not None else None

        if self._real and _PLUMB_AVAILABLE:
            example = Example(  # type: ignore[call-arg]
                example_id=_make_id(),
                task_id=self._task_id,
                inputs_hash=inputs_hash,
                expected_output_hash=expected_hash,
                source=ExampleSource.PRODUCTION_PROMOTION,  # type: ignore[attr-defined]
                created_at=datetime.now(tz=UTC),
            )
            # write via storage adapter (per plumb API ref §"Recording Examples")
            # In Phase 2 this would go through the storage adapter;
            # for now buffer locally just like other records.
            self.examples.append(
                {
                    "example_id": example.example_id,  # type: ignore[attr-defined]
                    "run_id": run_id,
                    "span_id": span_id,
                    "inputs_hash": inputs_hash,
                    "expected_output_hash": expected_hash,
                }
            )
            return

        self.examples.append(
            {
                "example_id": _make_id(),
                "run_id": run_id,
                "span_id": span_id,
                "inputs_hash": inputs_hash,
                "expected_output_hash": expected_hash,
            }
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_id() -> str:
    """Return a 32-char lowercase hex ID."""
    return secrets.token_hex(16)


def _sha256(text: str) -> str:
    """Return a 64-char lowercase hex SHA-256 digest."""
    return hashlib.sha256(text.encode()).hexdigest()
