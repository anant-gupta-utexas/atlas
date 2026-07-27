"""Loop daemon state, budgets and circuit breaker (TRD-v3 §3.5, Phase L2).

Split out of ``loop.py`` so the driver stays readable as L3 adds self-healing
and a judge gate to ``tick()``. Everything here is pure decision logic over a
``LoopState`` plus a ``LoopConfig`` — no ``gh``, no subprocess, no plumb —
which is what makes it cheap to unit-test in isolation.

``loop.py`` re-exports this module's public names, so
``from atlas.loop import LoopState, breaker_open`` keeps working.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from atlas.config import LoopConfig

_logger = logging.getLogger("atlas.loop")

_LOOP_STATE_RELATIVE_PATH = Path(".atlas") / "loop-state.json"

# The dedupe list is rewritten to disk every tick, so it must not grow
# without bound in a daemon that runs for weeks.
_MAX_SYNCED_OUTCOMES = 500


def _today() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass
class LoopState:
    """Mutable, persisted-to-disk loop state — survives process restarts.

    Distinct from RunContext/RunResult (per-run) — this is per-loop-process
    (Decision #6). Persisted as .atlas/loop-state.json.
    """

    runs_today: int = 0
    dollars_today: float = 0.0
    day: str = ""
    consecutive_no_progress: int = 0
    consecutive_identical_errors: int = 0
    last_error_signature: str | None = None
    breaker_open_until: str | None = None
    last_tick_at: str | None = None
    synced_pr_outcomes: list[str] = field(default_factory=list)

    @classmethod
    def load_or_init(cls, repo_root: Path) -> LoopState:
        path = repo_root / _LOOP_STATE_RELATIVE_PATH
        if not path.exists():
            return cls(day=_today())
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                runs_today=int(raw.get("runs_today", 0)),
                dollars_today=float(raw.get("dollars_today", 0.0)),
                day=str(raw.get("day", _today())),
                consecutive_no_progress=int(raw.get("consecutive_no_progress", 0)),
                consecutive_identical_errors=int(raw.get("consecutive_identical_errors", 0)),
                last_error_signature=raw.get("last_error_signature"),
                breaker_open_until=raw.get("breaker_open_until"),
                last_tick_at=raw.get("last_tick_at"),
                # Trim a state file written before the bound was introduced.
                synced_pr_outcomes=list(raw.get("synced_pr_outcomes", []))[-_MAX_SYNCED_OUTCOMES:],
            )
        except (json.JSONDecodeError, ValueError, TypeError, KeyError):
            _logger.warning("loop-state.json corrupted at %s; initializing fresh state", path)
            return cls(day=_today())

    def persist(self, repo_root: Path) -> None:
        path = repo_root / _LOOP_STATE_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)


def _reset_daily_counters_if_new_day(state: LoopState) -> None:
    today = _today()
    if state.day != today:
        state.day = today
        state.runs_today = 0
        state.dollars_today = 0.0


def budget_exhausted(state: LoopState, cfg: LoopConfig) -> bool:
    """Whether either daily cap is spent.

    Both halves are live as of 2026-07-26: ``RunResult.dollar_cost`` carries
    the engine-reported total (plumb v1.1's ``set_usage`` persists it), so
    ``dollars_today`` genuinely accumulates. One honest caveat remains — the
    Codex CLI reports no cost at all, so a Codex-only day advances the
    runs-cap but not the dollar-cap. ``warn_on_unenforced_budget`` says so.
    """
    return (
        state.runs_today >= cfg.max_runs_per_day or state.dollars_today >= cfg.max_dollars_per_day
    )


def breaker_open(state: LoopState, cfg: LoopConfig) -> bool:
    if state.breaker_open_until is None:
        return False
    try:
        until = datetime.fromisoformat(state.breaker_open_until)
    except ValueError:
        return False
    return datetime.now(tz=UTC) < until


def record_tick_outcome(
    state: LoopState, cfg: LoopConfig, *, made_progress: bool, error_signature: str | None
) -> None:
    if made_progress:
        state.consecutive_no_progress = 0
        state.consecutive_identical_errors = 0
        state.last_error_signature = None
        return

    state.consecutive_no_progress += 1

    if error_signature is not None and error_signature == state.last_error_signature:
        state.consecutive_identical_errors += 1
    else:
        state.consecutive_identical_errors = 1 if error_signature is not None else 0
    state.last_error_signature = error_signature

    if (
        state.consecutive_no_progress >= cfg.no_progress_limit
        or state.consecutive_identical_errors >= cfg.identical_error_limit
    ):
        deadline = datetime.now(tz=UTC).timestamp() + cfg.cooldown_min * 60
        state.breaker_open_until = datetime.fromtimestamp(deadline, tz=UTC).isoformat()


def remember_synced_outcome(state: LoopState, dedupe_key: str) -> None:
    """Append to the dedupe list, bounding it so an unattended daemon's
    loop-state.json can't grow without limit (it is rewritten every tick).

    A PR outcome is terminal — once relabeled the issue leaves atlas:working
    and sync() stops returning it — so only a recent window needs retaining.
    """
    state.synced_pr_outcomes.append(dedupe_key)
    if len(state.synced_pr_outcomes) > _MAX_SYNCED_OUTCOMES:
        del state.synced_pr_outcomes[:-_MAX_SYNCED_OUTCOMES]


def error_signature(exc: Exception) -> str:
    return f"{type(exc).__name__}:{exc}"


def warn_on_unenforced_budget(loop_cfg: LoopConfig, *, engine: str | None = None) -> None:
    """Warn when the configured dollar cap cannot actually bind.

    Since 2026-07-26 the Claude lane reports real per-run cost, so the cap is
    enforced there. The Codex CLI reports no cost figure at all (VERIFIED,
    0.144.4), so a Codex-pinned loop still advances only ``max_runs_per_day``
    — a spend cap that silently does nothing is worse than no cap at all, so
    that case keeps its loud startup warning.
    """
    if engine == "codex" and loop_cfg.max_dollars_per_day != LoopConfig().max_dollars_per_day:
        _logger.warning(
            "[loop] max_dollars_per_day=%s cannot be enforced on the codex engine: "
            "the Codex CLI reports no cost figure. Only max_runs_per_day=%s bounds "
            "this loop's spend.",
            loop_cfg.max_dollars_per_day,
            loop_cfg.max_runs_per_day,
        )


__all__ = [
    "LoopState",
    "breaker_open",
    "budget_exhausted",
    "error_signature",
    "record_tick_outcome",
    "remember_synced_outcome",
    "warn_on_unenforced_budget",
]
