"""Release-blocker test: dev.yaml's stages must match routing_ground_truth.json row-for-row."""

from __future__ import annotations

import json
from pathlib import Path

from atlas.workflow_loader import load_workflow_file

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "routing_ground_truth.json"
DEV_YAML_PATH = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"


def _load_fixture() -> list[dict]:  # type: ignore[type-arg]
    return json.loads(FIXTURE_PATH.read_text())


def _load_stages():  # type: ignore[no-untyped-def]
    return load_workflow_file(DEV_YAML_PATH).stages


def test_fixture_has_seven_rows() -> None:
    rows = _load_fixture()
    assert len(rows) == 7, f"Expected 7 rows, got {len(rows)}"


def test_stages_has_seven_entries() -> None:
    assert len(_load_stages()) == 7


def test_stage3_has_no_gate() -> None:
    stage3 = _load_stages()[3]
    assert stage3.name == "tds_gen"
    assert stage3.gate_label is None
    assert stage3.gate_index is None


def test_stage4_has_gate_tds() -> None:
    stage4 = _load_stages()[4]
    assert stage4.gate_label == "gate_tds"
    assert stage4.gate_index == 3


def test_fixture_matches_stages_row_for_row() -> None:
    rows = _load_fixture()
    stages = _load_stages()
    assert len(rows) == len(stages), "Fixture row count must equal stages length"

    for spec, row in zip(stages, rows, strict=True):
        assert spec.index == row["stage_index"], f"Stage {spec.index}: index mismatch"
        assert spec.name == row["stage_name"], (
            f"Stage {spec.index}: name mismatch {spec.name!r} vs {row['stage_name']!r}"
        )
        assert spec.tool == row["expected_tool"], (
            f"Stage {spec.index}: tool mismatch {spec.tool!r} vs {row['expected_tool']!r}"
        )
        assert spec.span_kind == row["expected_span_kind"], (
            f"Stage {spec.index}: span_kind mismatch "
            f"{spec.span_kind!r} vs {row['expected_span_kind']!r}"
        )
        expected_gate = row["gate_label"]
        assert spec.gate_label == expected_gate, (
            f"Stage {spec.index}: gate_label mismatch {spec.gate_label!r} vs {expected_gate!r}"
        )
        assert spec.gate_index == row["gate_index"], (
            f"Stage {spec.index}: gate_index mismatch {spec.gate_index!r} vs {row['gate_index']!r}"
        )
