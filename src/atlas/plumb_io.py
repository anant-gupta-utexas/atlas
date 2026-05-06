"""Plumb wrapper — exposes the only calls the orchestrator needs."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

_logger = logging.getLogger("atlas.plumb")

try:
    import plumb as _plumb_module  # type: ignore[import-not-found]
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
        self._parent_run_id: str | None = None

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
        try:
            self._run_ctx.__exit__(None, None, None)
        except Exception:
            _logger.warning("close_run: __exit__ raised for run_id=%s status=%s", run_id, status)

    def reopen_run(self, run_id: str) -> str:
        """Reattach to a previously-opened run via plumb's child-run handoff.

        Per plumb's orchestrator handoff guide, atlas resume opens a NEW run
        with ``parent_run_id`` pointing at the original.  Subsequent
        ``record_span`` / ``record_user_signal`` calls write into this child
        run, and the two rows stay linked via ``parent_run_id`` in plumb's DB.

        Returns the *child* run id — callers must use this id (not the
        original) for all subsequent writes after resume.

        In stub mode (real=False) we simply re-use the supplied run_id so that
        unit tests see a single coherent run_id throughout.
        """
        if not self._real:
            self._run_id = run_id
            self._closed = False
            return run_id

        # Reset closed flag so subsequent writes go through.
        self._closed = False
        try:
            # Child-run handoff: parent_run_id links the rows; task_id is the
            # original task identifier (we re-use the parent run id here as a
            # stable task identifier so the child clearly belongs to the same
            # task lineage).
            self._run_ctx = plumb_run(  # type: ignore[possibly-undefined]
                task_id=self._task_id or run_id,
                kind="online",
                parent_run_id=run_id,
            )
            self._run_handle = self._run_ctx.__enter__()
            child_run_id: str = self._run_handle.run_id
            self._run_id = child_run_id
            self._parent_run_id = run_id
            return child_run_id
        except Exception:
            _logger.warning("reopen_run: failed to open child run for run_id=%s", run_id)
            self._run_id = run_id
            return run_id

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
        """Buffer a user-signal score in plumb.

        Forwards ``decision.reason`` as ``rationale``.  Plumb v1 carries this
        in-memory; durable persistence of scores.rationale lands in plumb v2.
        """
        if self._real and self._run_handle is not None:
            self._run_handle.add_score(
                metric,
                "user_signal",
                value_label=decision.label,
                span_id=span_id,
                rationale=decision.reason,
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
                _logger.info(
                    "flush_pending_scores: keeping record for different run_id=%s (active=%s)",
                    rec.get("run_id"),
                    run_id,
                )
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
        """Write an examples row (rejected-artifact capture).

        Until plumb v2 ships ``RunHandle.add_example``, atlas persists examples
        in real mode by going directly to plumb's storage writer
        (``plumb._storage_writer.write_example``).  This is the same adapter
        plumb itself uses for all durable example writes today.
        """
        inputs_hash = _sha256(inputs)
        expected_hash = _sha256(expected) if expected is not None else None

        if self._real and _PLUMB_AVAILABLE:
            example = Example(
                example_id=_make_id(),
                task_id=self._task_id,
                inputs_hash=inputs_hash,
                expected_output_hash=expected_hash,
                source=ExampleSource.PRODUCTION_PROMOTION,
                origin_run_id=run_id,
                created_at=datetime.now(tz=UTC),
            )
            try:
                # Interim path: write through plumb's storage writer directly.
                # When v2 add_example lands on RunHandle, swap this for
                # self._run_handle.add_example(...).
                _plumb_module._storage_writer.write_example(example)  # type: ignore[attr-defined]
            except Exception:
                _logger.warning(
                    "write_example: durable persistence failed for run_id=%s span_id=%s",
                    run_id,
                    span_id,
                )
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
