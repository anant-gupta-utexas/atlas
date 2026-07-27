"""YAML → tuple[StageSpec, ...] loading and workflow resolution.

Trust boundary: a workflow YAML's ``tool: "RAW:<prompt>"`` is equivalent to
the user typing a command directly into ``claude -p``. This module performs
no sandboxing of that prompt text — treat a workflow file as trusted input,
exactly as you would a shell script.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from atlas.stages import _NAME_RE, SPAN_KINDS, StageSpec

_ALLOWED_TOP_LEVEL_KEYS = {"name", "default_backend", "stages"}
_ALLOWED_STAGE_KEYS = {
    "name",
    "span_kind",
    "tool",
    "gate",
    "isolate",
    "gate_is_async",
    "backend",
    "timeout_s",
}

_PACKAGE_WORKFLOWS_DIR = Path(__file__).parent / "workflows"


class WorkflowValidationError(Exception):
    """Raised on any YAML schema/content violation.

    Carries a human-readable, line-aware message — never a raw traceback.
    """


class WorkflowNotFoundError(Exception):
    """Raised by resolve_workflow when no candidate path exists."""


@dataclass(frozen=True)
class LoadedWorkflow:
    name: str
    default_backend: str | None
    stages: tuple[StageSpec, ...]


def load_workflow_file(path: Path) -> LoadedWorkflow:
    """Parse one workflow YAML file into a LoadedWorkflow.

    Uses ``yaml.safe_load()`` only (NFR-4). Raises WorkflowValidationError on:
    unknown top-level/stage keys, invalid span_kind, duplicate stage name,
    duplicate gate label, stage name not matching ``[a-z][a-z0-9_]*``,
    isolate=true with git unavailable, and non-positive-int timeout_s.
    """
    raw = yaml.safe_load(path.read_text())
    if raw is None or not isinstance(raw, dict):
        raise WorkflowValidationError(f"{path}: empty or non-mapping YAML document")

    unknown_top = set(raw.keys()) - _ALLOWED_TOP_LEVEL_KEYS
    if unknown_top:
        raise WorkflowValidationError(f"{path}: unknown top-level key(s): {sorted(unknown_top)}")

    name = raw.get("name")
    if not name or not isinstance(name, str) or not _NAME_RE.match(name):
        raise WorkflowValidationError(f"{path}: workflow 'name' missing or invalid: {name!r}")

    default_backend = raw.get("default_backend")  # str | None, unvalidated in Phase 1

    raw_stages = raw.get("stages")
    if not raw_stages or not isinstance(raw_stages, list):
        raise WorkflowValidationError(f"{path}: 'stages' must be a non-empty list")

    seen_names: set[str] = set()
    seen_gates: set[str] = set()
    stages: list[StageSpec] = []
    gate_idx = 0

    for i, raw_stage in enumerate(raw_stages):
        if not isinstance(raw_stage, dict):
            raise WorkflowValidationError(f"{path}: stage[{i}] must be a mapping")

        unknown_stage_keys = set(raw_stage.keys()) - _ALLOWED_STAGE_KEYS
        if unknown_stage_keys:
            raise WorkflowValidationError(
                f"{path}: stage[{i}] unknown key(s): {sorted(unknown_stage_keys)}"
            )

        stage_name = raw_stage.get("name")
        if not stage_name or not isinstance(stage_name, str) or not _NAME_RE.match(stage_name):
            raise WorkflowValidationError(f"{path}: stage[{i}] invalid name: {stage_name!r}")
        if stage_name in seen_names:
            raise WorkflowValidationError(f"{path}: duplicate stage name {stage_name!r}")
        seen_names.add(stage_name)

        span_kind = raw_stage.get("span_kind")
        if span_kind not in SPAN_KINDS:
            raise WorkflowValidationError(
                f"{path}: stage[{i}] {stage_name!r} span_kind {span_kind!r} "
                f"not one of {sorted(SPAN_KINDS)}"
            )

        tool = raw_stage.get("tool")
        if not tool:
            raise WorkflowValidationError(f"{path}: stage[{i}] {stage_name!r} missing 'tool'")

        gate_label = raw_stage.get("gate")  # may be absent or YAML null -> None
        gate_index: int | None = None
        if gate_label is not None:
            if gate_label in seen_gates:
                raise WorkflowValidationError(f"{path}: duplicate gate label {gate_label!r}")
            seen_gates.add(gate_label)
            gate_index = gate_idx
            gate_idx += 1

        isolate = bool(raw_stage.get("isolate", False))
        backend = raw_stage.get("backend")  # str | None; unvalidated in Phase 1
        gate_is_async = bool(raw_stage.get("gate_is_async", False))

        if isolate and shutil.which("git") is None:
            raise WorkflowValidationError(
                f"{path}: stage[{i}] {stage_name!r} has isolate: true but git is not on PATH"
            )

        timeout_s = raw_stage.get("timeout_s")
        if timeout_s is not None and (
            isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s <= 0
        ):
            raise WorkflowValidationError(
                f"{path}: stage[{i}] {stage_name!r} timeout_s must be a positive int, "
                f"got {timeout_s!r}"
            )

        stages.append(
            StageSpec(
                index=i,
                name=stage_name,
                span_kind=span_kind,
                tool=tool,
                gate_label=gate_label,
                gate_index=gate_index,
                isolate=isolate,
                gate_is_async=gate_is_async,
                backend=backend,
                timeout_s=timeout_s,
            )
        )

    return LoadedWorkflow(name=name, default_backend=default_backend, stages=tuple(stages))


def resolve_workflow(
    *, workflow_file: Path | None, workflow_name: str | None, repo_root: Path
) -> LoadedWorkflow:
    """Resolve a workflow per the §3.2 resolution order.

    1. ``workflow_file`` (literal path) — highest priority.
    2. ``workflow_name`` — search ``.atlas/workflows/<name>.yaml`` →
       ``~/.atlas/workflows/<name>.yaml`` → built-in
       ``src/atlas/workflows/<name>.yaml``.
    3. Neither given — default ``"dev"`` via step 2's search path.

    Raises WorkflowNotFoundError naming every location checked.
    """
    if workflow_file is not None:
        if not workflow_file.exists():
            raise WorkflowNotFoundError(f"--workflow-file path not found: {workflow_file}")
        return load_workflow_file(workflow_file)

    name = workflow_name or "dev"
    if not _NAME_RE.match(name):
        raise WorkflowNotFoundError(f"Invalid workflow name {name!r}: must match [a-z][a-z0-9_]*")

    candidates = [
        repo_root / ".atlas" / "workflows" / f"{name}.yaml",
        Path.home() / ".atlas" / "workflows" / f"{name}.yaml",
        _PACKAGE_WORKFLOWS_DIR / f"{name}.yaml",
    ]
    for c in candidates:
        if c.exists():
            return load_workflow_file(c)
    raise WorkflowNotFoundError(
        f"Workflow {name!r} not found. Checked: " + ", ".join(str(c) for c in candidates)
    )
