"""Release-blocker test: STAGES must match routing_ground_truth.json row-for-row."""
from __future__ import annotations

import json
from pathlib import Path

from atlas.stages import STAGES, GateLabel, StageName

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "routing_ground_truth.json"


def _load_fixture() -> list[dict]:  # type: ignore[type-arg]
    return json.loads(FIXTURE_PATH.read_text())


def test_fixture_has_seven_rows() -> None:
    rows = _load_fixture()
    assert len(rows) == 7, f"Expected 7 rows, got {len(rows)}"


def test_stages_has_seven_entries() -> None:
    assert len(STAGES) == 7


def test_stage3_has_no_gate() -> None:
    stage3 = STAGES[3]
    assert stage3.name == StageName.TDS_GEN
    assert stage3.gate_label is None
    assert stage3.gate_index is None


def test_stage4_has_gate_tds() -> None:
    stage4 = STAGES[4]
    assert stage4.gate_label == GateLabel.GATE_TDS
    assert stage4.gate_index == 3


def test_fixture_matches_stages_row_for_row() -> None:
    rows = _load_fixture()
    assert len(rows) == len(STAGES), "Fixture row count must equal STAGES length"

    for spec, row in zip(STAGES, rows, strict=True):
        assert spec.index == row["stage_index"], (
            f"Stage {spec.index}: index mismatch"
        )
        assert spec.name.value == row["stage_name"], (
            f"Stage {spec.index}: name mismatch {spec.name.value!r} vs {row['stage_name']!r}"
        )
        assert spec.tool == row["expected_tool"], (
            f"Stage {spec.index}: tool mismatch {spec.tool!r} vs {row['expected_tool']!r}"
        )
        assert spec.span_kind == row["expected_span_kind"], (
            f"Stage {spec.index}: span_kind mismatch {spec.span_kind!r} vs {row['expected_span_kind']!r}"
        )
        expected_gate = row["gate_label"]
        actual_gate = spec.gate_label.value if spec.gate_label is not None else None
        assert actual_gate == expected_gate, (
            f"Stage {spec.index}: gate_label mismatch {actual_gate!r} vs {expected_gate!r}"
        )
        assert spec.gate_index == row["gate_index"], (
            f"Stage {spec.index}: gate_index mismatch {spec.gate_index!r} vs {row['gate_index']!r}"
        )
