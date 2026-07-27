"""Unit tests for atlas.loop_report.build_weekly_report (Phase L4, T-L4.7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from plumb.core.entities import RunSummaryRow, Score, ScorerKind, Span, SpanKind

from atlas import loop_report
from atlas.config import Config

_SCORED_AT = datetime(2026, 7, 20, tzinfo=UTC)


def _hex(n: int) -> str:
    return f"{n:032x}"


def _row(
    run_id: int,
    *,
    parent_run_id: int | None = None,
    status: str = "success",
    dollar_cost: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> RunSummaryRow:
    return RunSummaryRow(
        run_id=_hex(run_id),
        task_id="t",
        kind="online",
        status=status,
        start_ts="2026-07-20T00:00:00+00:00",
        end_ts=None,
        orchestrator_model="haiku",
        sub_agent_model=None,
        git_sha=None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        dollar_cost=dollar_cost,
        error_type=None,
        parent_run_id=_hex(parent_run_id) if parent_run_id is not None else None,
        span_count=1,
        score_count=0,
    )


def _approved_score(run_id: int) -> Score:
    return Score(
        score_id=_hex(1000 + run_id),
        run_id=_hex(run_id),
        metric_name="user_signal",
        scorer=ScorerKind.HUMAN,
        scorer_version="v1",
        scored_at=_SCORED_AT,
        value_label="approved",
    )


def _rejected_score(run_id: int) -> Score:
    return Score(
        score_id=_hex(2000 + run_id),
        run_id=_hex(run_id),
        metric_name="user_signal",
        scorer=ScorerKind.HUMAN,
        scorer_version="v1",
        scored_at=_SCORED_AT,
        value_label="rejected",
    )


def _engine_span(run_id: int, engine: str) -> Span:
    return Span(
        span_id=_hex(3000 + run_id),
        run_id=_hex(run_id),
        kind=SpanKind.LLM,
        name="task_completion_gate",
        attributes={"engine": engine},
    )


class _FakeStorage:
    def __init__(
        self,
        rows: list[RunSummaryRow],
        *,
        scores_by_run: dict[str, list[Score]] | None = None,
        spans_by_run: dict[str, list[Span]] | None = None,
    ) -> None:
        self._rows = rows
        self._scores_by_run = scores_by_run or {}
        self._spans_by_run = spans_by_run or {}

    def __enter__(self) -> _FakeStorage:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def list_runs_with_counts(self, *, since=None, task_id=None, limit=100):
        return self._rows

    def get_scores_for_run(self, run_id: str) -> list[Score]:
        return self._scores_by_run.get(run_id, [])

    def get_spans_for_run(self, run_id: str) -> list[Span]:
        return self._spans_by_run.get(run_id, [])


def _cfg(tmp_path: Path) -> Config:
    return Config(repo_root=tmp_path, plumb_db_path=tmp_path / "plumb.db")


def _build(tmp_path: Path, storage: _FakeStorage) -> loop_report.WeeklyReport:
    with patch("atlas.loop_report._open_storage", return_value=storage):
        return loop_report.build_weekly_report(_cfg(tmp_path))


def test_mixed_landed_rejected_in_flight_lineages(tmp_path: Path) -> None:
    rows = [
        _row(1, status="success"),  # landed
        _row(2, status="failure"),  # rejected
        _row(3, status="pending"),  # in-flight, not terminal
    ]
    storage = _FakeStorage(
        rows,
        scores_by_run={
            _hex(1): [_approved_score(1)],
            _hex(2): [_rejected_score(2)],
        },
        spans_by_run={
            _hex(1): [_engine_span(1, "claude")],
        },
    )

    report = _build(tmp_path, storage)

    assert report.total_runs == 3
    assert report.landed_prs == 1
    assert report.terminal_lineages == 2  # run 3 is pending, excluded
    assert report.intervention_count == 0


def test_lineage_with_retry_counts_once_toward_intervention_and_landed(tmp_path: Path) -> None:
    """A lineage of 2 runs (original + 1 self-heal retry) counts once toward
    intervention_count, not twice, and once toward landed_prs if the final
    run in the lineage has an approved user_signal."""
    rows = [
        _row(1, status="failure"),  # original attempt, failed
        _row(2, parent_run_id=1, status="success"),  # retry, succeeded
    ]
    storage = _FakeStorage(
        rows,
        scores_by_run={_hex(2): [_approved_score(2)]},
        spans_by_run={_hex(2): [_engine_span(2, "claude")]},
    )

    report = _build(tmp_path, storage)

    assert report.total_runs == 2
    assert report.intervention_count == 1
    assert report.terminal_lineages == 1
    assert report.landed_prs == 1


def test_cost_per_landed_pr_claude_is_none_when_no_claude_landings(tmp_path: Path) -> None:
    rows = [_row(1, status="success", tokens_in=100, tokens_out=50)]
    storage = _FakeStorage(
        rows,
        scores_by_run={_hex(1): [_approved_score(1)]},
        spans_by_run={_hex(1): [_engine_span(1, "codex")]},
    )

    report = _build(tmp_path, storage)

    assert report.cost_per_landed_pr_claude is None
    assert report.tokens_per_landed_pr_codex == (100.0, 50.0)


def test_tokens_per_landed_pr_codex_computed_independently_of_claude_cost(
    tmp_path: Path,
) -> None:
    """A window with only codex landings must produce cost_per_landed_pr_claude
    = None and a real tokens_per_landed_pr_codex, never a blended number."""
    rows = [
        _row(1, status="success", dollar_cost=None, tokens_in=200, tokens_out=80),
        _row(2, status="success", dollar_cost=None, tokens_in=100, tokens_out=40),
    ]
    storage = _FakeStorage(
        rows,
        scores_by_run={_hex(1): [_approved_score(1)], _hex(2): [_approved_score(2)]},
        spans_by_run={
            _hex(1): [_engine_span(1, "codex")],
            _hex(2): [_engine_span(2, "codex")],
        },
    )

    report = _build(tmp_path, storage)

    assert report.cost_per_landed_pr_claude is None
    assert report.tokens_per_landed_pr_codex == (150.0, 60.0)


def test_run_with_no_engine_attribute_excluded_from_per_engine_aggregates(
    tmp_path: Path,
) -> None:
    """A run whose code_gen span has no 'engine' attribute (pre-L4 data) is
    excluded from both per-engine aggregates but still counted in total_runs."""
    rows = [_row(1, status="success", dollar_cost=1.5)]
    storage = _FakeStorage(
        rows,
        scores_by_run={_hex(1): [_approved_score(1)]},
        spans_by_run={
            _hex(1): [Span(span_id=_hex(999), run_id=_hex(1), kind=SpanKind.LLM, name="x")]
        },
    )

    report = _build(tmp_path, storage)

    assert report.total_runs == 1
    assert report.landed_prs == 1
    assert report.cost_per_landed_pr_claude is None
    assert report.tokens_per_landed_pr_codex is None


def test_intervention_rate_none_when_no_terminal_lineages(tmp_path: Path) -> None:
    rows = [_row(1, status="pending")]
    storage = _FakeStorage(rows)

    report = _build(tmp_path, storage)

    assert report.terminal_lineages == 0
    assert report.intervention_rate is None


def test_cost_per_landed_pr_claude_averages_across_landed_lineages(tmp_path: Path) -> None:
    rows = [
        _row(1, status="success", dollar_cost=2.0),
        _row(2, status="success", dollar_cost=4.0),
    ]
    storage = _FakeStorage(
        rows,
        scores_by_run={_hex(1): [_approved_score(1)], _hex(2): [_approved_score(2)]},
        spans_by_run={
            _hex(1): [_engine_span(1, "claude")],
            _hex(2): [_engine_span(2, "claude")],
        },
    )

    report = _build(tmp_path, storage)

    assert report.cost_per_landed_pr_claude == 3.0


def test_loop_report_never_imports_plumb_cli() -> None:
    """SQLiteStorageAdapter is constructed directly, not via the private
    plumb.cli._get_storage helper (Pending Decision #4)."""
    import ast

    src_path = Path(__file__).parents[2] / "src" / "atlas" / "loop_report.py"
    tree = ast.parse(src_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "plumb.cli":
            raise AssertionError("loop_report.py must not import from plumb.cli")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "plumb.cli"
