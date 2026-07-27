"""Weekly cost/intervention report over plumb run data (Phase L4, T-L4.7).

Constructs a ``SQLiteStorageAdapter`` the same way ``plumb/cli.py::_get_storage()``
builds one internally (``plumb.config.get_settings()`` + ``ensure_data_dir()``
+ ``SQLiteStorageAdapter(db_path, clock=...)``) rather than importing that
private, underscore-prefixed helper (Pending Decision #4). ``plumb run
stats``'s own CLI output truncates ``run_id`` and drops ``dollar_cost``
(verified against source, 2026-07-26) — exactly the two fields this report
needs in full, so it reads the storage layer directly instead.

**"Intervention rate" is narrower than TRD-v3 §2's literal, human-centric
KPI** (Pending Decision #5): it counts a self-heal *retry* firing (a robot
intervention — L3's ``parent_run_id`` child-run lineage), not a human
manually unsticking an issue, because atlas/plumb do not durably record the
latter today. Flagged here, not overclaimed.

A "lineage" is a run plus every run transitively reachable via
``parent_run_id`` (an original dispatch plus, at most under L3's own retry
cap, one self-heal retry). ``landed`` means any run in the lineage carries an
approved ``user_signal`` score; a lineage's ``engine`` is read from the first
``"engine"`` span attribute (Pending Decision #6) found across its runs — a
lineage with none is excluded from the per-engine splits but still counted in
``total_runs``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from atlas.config import Config

if TYPE_CHECKING:
    from plumb.adapters.storage_sqlite import SQLiteStorageAdapter
    from plumb.core.entities import RunSummaryRow

_USER_SIGNAL_METRIC = "user_signal"
_APPROVED_LABEL = "approved"
_PENDING_STATUS = "pending"


class _RealClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _open_storage() -> SQLiteStorageAdapter:
    """Mirror plumb/cli.py::_get_storage()'s construction (Pending Decision #4).

    ``_get_storage`` itself is private; a future plumb refactor of it should
    not silently break this import, so its two-line body is replicated here
    rather than imported.
    """
    from plumb.adapters.storage_sqlite import SQLiteStorageAdapter
    from plumb.config import ensure_data_dir, get_settings

    settings = get_settings()
    data_dir = ensure_data_dir(settings)
    db_path = data_dir / "plumb.db"
    return SQLiteStorageAdapter(db_path, clock=_RealClock())


@dataclass(frozen=True)
class WeeklyReport:
    """Cost-per-landed-PR + intervention-rate summary (TRD-v3 §13 #12)."""

    since: datetime | None
    total_runs: int
    landed_prs: int
    intervention_count: int
    terminal_lineages: int
    # None (not 0.0) when no runs on that engine landed in the window — an
    # unreachable average is not the same as a free one (Pending Decision #6).
    intervention_rate: float | None
    cost_per_landed_pr_claude: float | None
    tokens_per_landed_pr_codex: tuple[float, float] | None


def _lineage_root_id(row_by_id: dict[str, RunSummaryRow], run_id: str) -> str:
    """Walk ``parent_run_id`` upward to the ultimate ancestor's run_id."""
    seen: set[str] = set()
    current = run_id
    while current not in seen:
        seen.add(current)
        row = row_by_id.get(current)
        parent_id = row.parent_run_id if row is not None else None
        if parent_id is None or parent_id not in row_by_id:
            return current
        current = parent_id
    return current  # pragma: no cover — defensive cycle guard


def build_weekly_report(
    cfg: Config, *, since: datetime | None = None, limit: int = 10_000
) -> WeeklyReport:
    """Aggregate plumb run data in ``[since, now)`` into a ``WeeklyReport``.

    ``cfg`` is accepted for call-site symmetry with every other atlas entry
    point that takes the resolved ``Config`` (and to leave room for a future
    config-driven filter); the plumb storage connection itself is opened
    independent of it, per ``_open_storage``'s own docstring.
    """
    del cfg  # not needed to open plumb's own storage (Pending Decision #4)
    storage = _open_storage()
    with storage as db:
        rows = db.list_runs_with_counts(since=since, limit=limit)
        row_by_id: dict[str, RunSummaryRow] = {r.run_id: r for r in rows}

        lineages: dict[str, list[RunSummaryRow]] = {}
        for row in rows:
            root_id = _lineage_root_id(row_by_id, row.run_id)
            lineages.setdefault(root_id, []).append(row)

        landed_prs = 0
        intervention_count = 0
        terminal_lineages = 0
        claude_cost_total = 0.0
        claude_landed_count = 0
        codex_tokens_in_total = 0
        codex_tokens_out_total = 0
        codex_landed_count = 0

        for lineage_rows in lineages.values():
            if len(lineage_rows) > 1:
                intervention_count += 1

            is_terminal = any(r.status != _PENDING_STATUS for r in lineage_rows)
            if not is_terminal:
                continue
            terminal_lineages += 1

            landed = False
            for r in lineage_rows:
                scores = db.get_scores_for_run(r.run_id)
                if any(
                    s.metric_name == _USER_SIGNAL_METRIC and s.value_label == _APPROVED_LABEL
                    for s in scores
                ):
                    landed = True
                    break
            if not landed:
                continue
            landed_prs += 1

            engine: str | None = None
            for r in lineage_rows:
                for span in db.get_spans_for_run(r.run_id):
                    attrs = span.attributes or {}
                    if "engine" in attrs:
                        engine = attrs["engine"]
                        break
                if engine is not None:
                    break

            if engine == "claude":
                claude_cost_total += sum(r.dollar_cost or 0.0 for r in lineage_rows)
                claude_landed_count += 1
            elif engine == "codex":
                codex_tokens_in_total += sum(r.tokens_in or 0 for r in lineage_rows)
                codex_tokens_out_total += sum(r.tokens_out or 0 for r in lineage_rows)
                codex_landed_count += 1
            # engine is None (pre-L4 data) or an unrecognized value: counted
            # in landed_prs/total_runs above, excluded from both per-engine
            # aggregates below — not blended into either.

        cost_per_landed_pr_claude = (
            claude_cost_total / claude_landed_count if claude_landed_count > 0 else None
        )
        tokens_per_landed_pr_codex = (
            (
                codex_tokens_in_total / codex_landed_count,
                codex_tokens_out_total / codex_landed_count,
            )
            if codex_landed_count > 0
            else None
        )
        intervention_rate = (
            intervention_count / terminal_lineages if terminal_lineages > 0 else None
        )

        return WeeklyReport(
            since=since,
            total_runs=len(rows),
            landed_prs=landed_prs,
            intervention_count=intervention_count,
            terminal_lineages=terminal_lineages,
            intervention_rate=intervention_rate,
            cost_per_landed_pr_claude=cost_per_landed_pr_claude,
            tokens_per_landed_pr_codex=tokens_per_landed_pr_codex,
        )


def format_report(report: WeeklyReport) -> str:
    """Human-readable text rendering — the default for ``atlas loop report``."""
    lines = [
        f"Weekly report{f' (since {report.since.isoformat()})' if report.since else ''}",
        f"  Total runs:          {report.total_runs}",
        f"  Landed PRs:          {report.landed_prs}",
        f"  Terminal lineages:   {report.terminal_lineages}",
        f"  Self-heal retries:   {report.intervention_count}",
        "  Intervention rate:   "
        + (
            f"{report.intervention_rate:.1%}"
            if report.intervention_rate is not None
            else "n/a (no terminal lineages)"
        ),
        "  Cost / landed PR (claude): "
        + (
            f"${report.cost_per_landed_pr_claude:.4f}"
            if report.cost_per_landed_pr_claude is not None
            else "n/a (no claude landings)"
        ),
        "  Tokens / landed PR (codex): "
        + (
            f"{report.tokens_per_landed_pr_codex[0]:.0f} in / "
            f"{report.tokens_per_landed_pr_codex[1]:.0f} out"
            if report.tokens_per_landed_pr_codex is not None
            else "n/a (no codex landings)"
        ),
    ]
    return "\n".join(lines)


__all__ = ["WeeklyReport", "build_weekly_report", "format_report"]
