"""Unit tests for atlas.workflow_loader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from atlas.stages import StageSpec
from atlas.workflow_loader import (
    WorkflowNotFoundError,
    WorkflowValidationError,
    load_workflow_file,
    resolve_workflow,
)

_DEV_YAML_PATH = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "dev.yaml"
_JOB_YAML_PATH = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "job.yaml"
_JOB_CLI_YAML_PATH = Path(__file__).parents[2] / "src" / "atlas" / "workflows" / "job_cli.yaml"

_VALID_YAML = """\
name: test_wf
stages:
  - name: score_fit
    span_kind: plan
    tool: some-tool
    gate: gate_done
  - name: score_fit2
    span_kind: verify
    tool: other-tool
"""


def _write(tmp_path: Path, content: str, filename: str = "wf.yaml") -> Path:
    path = tmp_path / filename
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# load_workflow_file — happy path
# ---------------------------------------------------------------------------


def test_load_valid_workflow_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, _VALID_YAML)
    loaded = load_workflow_file(path)

    assert loaded.name == "test_wf"
    assert loaded.default_backend is None
    assert len(loaded.stages) == 2

    s0, s1 = loaded.stages
    assert s0.index == 0
    assert s0.name == "score_fit"
    assert s0.span_kind == "plan"
    assert s0.tool == "some-tool"
    assert s0.gate_label == "gate_done"
    assert s0.gate_index == 0

    assert s1.index == 1
    assert s1.name == "score_fit2"
    assert s1.gate_label is None
    assert s1.gate_index is None


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_load_rejects_invalid_span_kind(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
stages:
  - name: score_fit
    span_kind: research
    tool: t
"""
    path = _write(tmp_path, yaml_text)
    with pytest.raises(WorkflowValidationError, match="span_kind"):
        load_workflow_file(path)


def test_load_rejects_duplicate_stage_name(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
stages:
  - name: score_fit
    span_kind: plan
    tool: t1
  - name: score_fit
    span_kind: plan
    tool: t2
"""
    path = _write(tmp_path, yaml_text)
    with pytest.raises(WorkflowValidationError, match="duplicate stage name"):
        load_workflow_file(path)


def test_load_rejects_duplicate_gate_label(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
stages:
  - name: a
    span_kind: plan
    tool: t1
    gate: gate_done
  - name: b
    span_kind: plan
    tool: t2
    gate: gate_done
"""
    path = _write(tmp_path, yaml_text)
    with pytest.raises(WorkflowValidationError, match="duplicate gate label"):
        load_workflow_file(path)


def test_load_rejects_bad_name_format(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
stages:
  - name: Score-Fit
    span_kind: plan
    tool: t1
"""
    path = _write(tmp_path, yaml_text)
    with pytest.raises(WorkflowValidationError, match="invalid name"):
        load_workflow_file(path)

    ok_text = yaml_text.replace("Score-Fit", "score_fit2")
    path2 = _write(tmp_path, ok_text, "wf2.yaml")
    loaded = load_workflow_file(path2)
    assert loaded.stages[0].name == "score_fit2"


def test_load_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
extra_field: foo
stages:
  - name: a
    span_kind: plan
    tool: t1
"""
    path = _write(tmp_path, yaml_text)
    with pytest.raises(WorkflowValidationError, match="unknown top-level key"):
        load_workflow_file(path)


def test_load_rejects_unknown_stage_key(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
stages:
  - name: a
    span_kind: plan
    tool: t1
    retries: 3
"""
    path = _write(tmp_path, yaml_text)
    with pytest.raises(WorkflowValidationError, match="unknown key"):
        load_workflow_file(path)


def test_loader_rejects_unsafe_yaml_tags(tmp_path: Path) -> None:
    yaml_text = "name: !!python/object:os.system ['echo hi']\nstages: []\n"
    path = _write(tmp_path, yaml_text)
    with pytest.raises(yaml.YAMLError):
        load_workflow_file(path)


def test_load_isolate_requires_git_on_path(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
stages:
  - name: a
    span_kind: subagent
    tool: t1
    isolate: true
"""
    path = _write(tmp_path, yaml_text)
    with patch("atlas.workflow_loader.shutil.which", return_value=None):
        with pytest.raises(WorkflowValidationError, match="git is not on PATH"):
            load_workflow_file(path)


# ---------------------------------------------------------------------------
# timeout_s
# ---------------------------------------------------------------------------


def test_load_stage_timeout_s(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
stages:
  - name: a
    span_kind: plan
    tool: t1
    timeout_s: 900
  - name: b
    span_kind: plan
    tool: t2
"""
    path = _write(tmp_path, yaml_text)
    loaded = load_workflow_file(path)
    assert loaded.stages[0].timeout_s == 900
    assert loaded.stages[1].timeout_s is None


@pytest.mark.parametrize("bad_value", [0, -5, "600", 1.5])
def test_load_rejects_bad_timeout_s(tmp_path: Path, bad_value: object) -> None:
    yaml_text = f"""\
name: wf
stages:
  - name: a
    span_kind: plan
    tool: t1
    timeout_s: {bad_value!r}
"""
    path = _write(tmp_path, yaml_text)
    with pytest.raises(WorkflowValidationError, match="timeout_s"):
        load_workflow_file(path)


def test_load_rejects_empty_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    with pytest.raises(WorkflowValidationError, match="empty or non-mapping"):
        load_workflow_file(path)


def test_load_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, "- a\n- b\n")
    with pytest.raises(WorkflowValidationError, match="empty or non-mapping"):
        load_workflow_file(path)


def test_load_rejects_empty_stages_list(tmp_path: Path) -> None:
    path = _write(tmp_path, "name: wf\nstages: []\n")
    with pytest.raises(WorkflowValidationError, match="non-empty list"):
        load_workflow_file(path)


def test_load_rejects_non_dict_stage_entry(tmp_path: Path) -> None:
    path = _write(tmp_path, "name: wf\nstages:\n  - not_a_mapping\n")
    with pytest.raises(WorkflowValidationError, match="must be a mapping"):
        load_workflow_file(path)


def test_load_rejects_missing_name(tmp_path: Path) -> None:
    path = _write(tmp_path, "stages:\n  - name: a\n    span_kind: plan\n    tool: t\n")
    with pytest.raises(WorkflowValidationError, match="workflow 'name'"):
        load_workflow_file(path)


def test_load_rejects_missing_tool(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
stages:
  - name: a
    span_kind: plan
"""
    path = _write(tmp_path, yaml_text)
    with pytest.raises(WorkflowValidationError, match="missing 'tool'"):
        load_workflow_file(path)


def test_load_default_backend_parsed_unvalidated(tmp_path: Path) -> None:
    yaml_text = """\
name: wf
default_backend: anything-goes
stages:
  - name: a
    span_kind: plan
    tool: t
"""
    path = _write(tmp_path, yaml_text)
    loaded = load_workflow_file(path)
    assert loaded.default_backend == "anything-goes"


# ---------------------------------------------------------------------------
# Dev-pipeline parity (T1.4)
# ---------------------------------------------------------------------------


def test_dev_pipeline_parity() -> None:
    loaded = load_workflow_file(_DEV_YAML_PATH)
    assert loaded.name == "dev"

    expected = (
        StageSpec(
            0,
            "research",
            "plan",
            "consult-experts:research",
            "gate_research",
            0,
        ),
        StageSpec(1, "prd_draft", "plan", "consult-experts:pm", "gate_prd", 1),
        StageSpec(2, "trd_draft", "plan", "consult-experts:tech-lead", "gate_trd", 2),
        StageSpec(3, "tds_gen", "plan", "dev-docs-be", None, None),
        StageSpec(4, "plan_review", "verify", "plan-reviewer", "gate_tds", 3),
        StageSpec(
            5,
            "code_gen",
            "subagent",
            "code-gen-agent",
            "gate_commit",
            4,
            isolate=True,
            gate_is_async=True,
        ),
        StageSpec(6, "code_review", "verify", "code-review", "gate_phase_complete", 5),
    )

    assert loaded.stages == expected
    assert len(loaded.stages) == 7
    for stage in loaded.stages:
        assert stage.timeout_s is None
    isolate_stages = [s.name for s in loaded.stages if s.isolate]
    assert isolate_stages == ["code_gen"]
    async_stages = [s.name for s in loaded.stages if s.gate_is_async]
    assert async_stages == ["code_gen"]


def test_load_job_yaml_via_loader() -> None:
    loaded = load_workflow_file(_JOB_YAML_PATH)
    assert loaded.name == "job"
    assert len(loaded.stages) == 4

    by_name = {s.name: s for s in loaded.stages}
    assert by_name["ingest_postings"].gate_label is None
    assert by_name["ingest_postings"].gate_index is None
    assert by_name["ingest_postings"].tool.startswith("LIB:")
    assert by_name["score_fit"].gate_label == "gate_shortlist"
    assert by_name["score_fit"].gate_index == 0
    assert by_name["score_fit"].tool.startswith("LIB:")
    assert by_name["tailor_materials"].gate_label == "gate_materials"
    assert by_name["tailor_materials"].gate_index == 1
    assert by_name["tailor_materials"].tool.startswith("RAW:")
    assert by_name["tailor_materials"].timeout_s == 1800
    assert by_name["emit_package"].gate_label == "gate_done"
    assert by_name["emit_package"].gate_index == 2
    assert by_name["emit_package"].tool.startswith("RAW:")
    assert by_name["emit_package"].timeout_s is None

    for name in ("ingest_postings", "score_fit"):
        assert by_name[name].timeout_s is None

    span_kinds = [s.span_kind for s in loaded.stages]
    assert span_kinds == ["tool", "verify", "subagent", "tool"]


def test_load_job_cli_yaml_via_loader() -> None:
    loaded = load_workflow_file(_JOB_CLI_YAML_PATH)
    assert loaded.name == "job_cli"
    assert len(loaded.stages) == 4

    by_name = {s.name: s for s in loaded.stages}
    for stage in loaded.stages:
        assert stage.tool.startswith("RAW:"), f"{stage.name} tool is not RAW:-prefixed"

    assert by_name["ingest_postings"].gate_label is None
    assert by_name["score_fit"].gate_label == "gate_shortlist"
    assert by_name["score_fit"].timeout_s == 1800
    assert by_name["tailor_materials"].gate_label == "gate_materials"
    assert by_name["tailor_materials"].timeout_s == 1800
    assert by_name["emit_package"].gate_label == "gate_done"

    gate_labels = [s.gate_label for s in loaded.stages if s.gate_label is not None]
    assert len(gate_labels) == len(set(gate_labels))


# ---------------------------------------------------------------------------
# resolve_workflow (T1.3)
# ---------------------------------------------------------------------------


def test_resolve_workflow_priority_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = tmp_path / "repo"
    home = tmp_path / "home"
    (repo_root / ".atlas" / "workflows").mkdir(parents=True)
    (home / ".atlas" / "workflows").mkdir(parents=True)

    repo_wf = repo_root / ".atlas" / "workflows" / "custom.yaml"
    home_wf = home / ".atlas" / "workflows" / "custom.yaml"
    repo_wf.write_text("name: custom\nstages:\n  - name: a\n    span_kind: plan\n    tool: t\n")
    home_wf.write_text(
        "name: custom_home\nstages:\n  - name: a\n    span_kind: plan\n    tool: t\n"
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    loaded = resolve_workflow(workflow_file=None, workflow_name="custom", repo_root=repo_root)
    assert loaded.name == "custom"  # repo .atlas/workflows wins over ~/.atlas/workflows

    repo_wf.unlink()
    loaded2 = resolve_workflow(workflow_file=None, workflow_name="custom", repo_root=repo_root)
    assert loaded2.name == "custom_home"  # falls back to ~/.atlas/workflows

    # --workflow-file beats both
    literal = tmp_path / "literal.yaml"
    literal.write_text("name: literal\nstages:\n  - name: a\n    span_kind: plan\n    tool: t\n")
    loaded3 = resolve_workflow(workflow_file=literal, workflow_name="custom", repo_root=repo_root)
    assert loaded3.name == "literal"


def test_resolve_workflow_defaults_to_dev(tmp_path: Path) -> None:
    loaded = resolve_workflow(workflow_file=None, workflow_name=None, repo_root=tmp_path)
    assert loaded.name == "dev"


def test_resolve_workflow_not_found_lists_all_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "nonexistent_home")
    with pytest.raises(WorkflowNotFoundError) as exc_info:
        resolve_workflow(workflow_file=None, workflow_name="totally_missing", repo_root=tmp_path)

    msg = str(exc_info.value)
    assert str(tmp_path / ".atlas" / "workflows" / "totally_missing.yaml") in msg
    assert "totally_missing.yaml" in msg


def test_resolve_workflow_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(WorkflowNotFoundError, match="missing.yaml"):
        resolve_workflow(workflow_file=missing, workflow_name=None, repo_root=tmp_path)


def test_resolve_workflow_rejects_path_traversal_name(tmp_path: Path) -> None:
    with pytest.raises(WorkflowNotFoundError, match="Invalid workflow name"):
        resolve_workflow(workflow_file=None, workflow_name="../../etc/passwd", repo_root=tmp_path)


def test_resolve_workflow_perf_smoke(tmp_path: Path) -> None:
    import time

    t0 = time.monotonic()
    resolve_workflow(workflow_file=None, workflow_name="dev", repo_root=tmp_path)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < 100


def test_loader_perf_smoke() -> None:
    import time

    t0 = time.monotonic()
    load_workflow_file(_DEV_YAML_PATH)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < 50
