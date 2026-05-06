"""Plumb wrapper — exposes the only calls the orchestrator needs."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from plumb import run as plumb_run  # type: ignore[import-not-found]
    from plumb.core.entities import Example, ExampleSource  # type: ignore[import-not-found]

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
        self._run_ctx: Any = None   # the _RunFactory context manager (holds __exit__)
        self._run_handle: Any = None  # the RunHandle (returned by __enter__)
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

        self._run_ctx = plumb_run(task_id=task, kind="online")
        self._run_handle = self._run_ctx.__enter__()
        self._run_id = self._run_handle.run_id
        return self._run_id

    def close_run(self, *, run_id: str, status: str) -> None:
        """Close the plumb run. No-op if already closed."""
        if self._closed:
            return
        self._closed = True
        if not self._real or self._run_ctx is None:
            return
        if status != "success":
            exc: BaseException = RuntimeError(status)
            try:
                self._run_ctx.__exit__(type(exc), exc, None)
            except Exception:
                pass
        else:
            try:
                self._run_ctx.__exit__(None, None, None)
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
            span_id: str = self._run_handle.add_span(
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
        decision: GateDecision,
    ) -> None:
        """Buffer a user-signal score in plumb."""
        if self._real and self._run_handle is not None:
            self._run_handle.add_score(
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

    def flush_pending_scores(self, *, run_id: str, pending_path: Path, span_id: str = "") -> int:
        """
        Drain ``.atlas/pending-scores.jsonl`` (written by the post-commit hook)
        through the live plumb run handle. Returns the number of scores flushed.

        Only flushes records matching ``run_id``; rows for other runs are kept
        (defensive — should not happen in single-run-per-repo v1).
        """
        if not pending_path.exists():
            return 0

        # Local import avoids cycle with orchestrator.GateDecision.
        from atlas.orchestrator import GateDecision

        kept: list[str] = []
        flushed = 0
        for line in pending_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if rec.get("run_id") != run_id:
                kept.append(line)
                continue
            decision = GateDecision(
                label=rec.get("value_label", "approved"),
                turn_count=1,
                reason=rec.get("rationale"),
            )
            self.record_user_signal(
                run_id=run_id,
                span_id=span_id,
                metric=rec.get("metric", "gate_commit"),
                decision=decision,
            )
            flushed += 1

        if kept:
            pending_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        else:
            pending_path.unlink()
        return flushed

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
            example = Example(
                example_id=_make_id(),
                task_id=self._task_id,
                inputs_hash=inputs_hash,
                expected_output_hash=expected_hash,
                source=ExampleSource.PRODUCTION_PROMOTION,
                created_at=datetime.now(tz=UTC),
            )
            # write via storage adapter (per plumb API ref §"Recording Examples")
            # In Phase 2 this would go through the storage adapter;
            # for now buffer locally just like other records.
            self.examples.append(
                {
                    "example_id": example.example_id,
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
