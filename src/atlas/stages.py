from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StageName(StrEnum):
    RESEARCH = "research"
    PRD_DRAFT = "prd_draft"
    TRD_DRAFT = "trd_draft"
    TDS_GEN = "tds_gen"
    PLAN_REVIEW = "plan_review"
    CODE_GEN = "code_gen"
    CODE_REVIEW = "code_review"


class GateLabel(StrEnum):
    GATE_RESEARCH = "gate_research"
    GATE_PRD = "gate_prd"
    GATE_TRD = "gate_trd"
    GATE_TDS = "gate_tds"
    GATE_COMMIT = "gate_commit"  # written by hook, not orchestrator
    GATE_PHASE_COMPLETE = "gate_phase_complete"


@dataclass(frozen=True)
class StageSpec:
    index: int
    name: StageName
    span_kind: str  # "plan" | "verify" | "subagent"
    tool: str
    gate_label: GateLabel | None  # None for stage 3 (reviewed by stage 4)
    gate_index: int | None  # 0–5; None where gate_label is None


STAGES: tuple[StageSpec, ...] = (
    StageSpec(
        0, StageName.RESEARCH, "plan", "consult-experts:research", GateLabel.GATE_RESEARCH, 0
    ),
    StageSpec(1, StageName.PRD_DRAFT, "plan", "consult-experts:pm", GateLabel.GATE_PRD, 1),
    StageSpec(2, StageName.TRD_DRAFT, "plan", "consult-experts:tech-lead", GateLabel.GATE_TRD, 2),
    StageSpec(3, StageName.TDS_GEN, "plan", "dev-docs-be", None, None),
    StageSpec(4, StageName.PLAN_REVIEW, "verify", "plan-reviewer", GateLabel.GATE_TDS, 3),
    StageSpec(5, StageName.CODE_GEN, "subagent", "code-gen-agent", GateLabel.GATE_COMMIT, 4),
    StageSpec(6, StageName.CODE_REVIEW, "verify", "code-review", GateLabel.GATE_PHASE_COMPLETE, 5),
)

STAGE_BY_NAME: dict[StageName, StageSpec] = {s.name: s for s in STAGES}
