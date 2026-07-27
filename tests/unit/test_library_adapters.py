"""Unit tests for atlas.library_adapters.* (T2.2).

content-pipeline is an optional dependency (T2.6) and is not installed in
atlas's own test environment, so these tests stub content-pipeline's modules
in sys.modules before each adapter's function-local imports run. This proves
atlas's wiring (which functions/classes it calls, with what arguments, how it
maps results to StageOutcome) without requiring a real content-pipeline
install or real LLM/network calls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from atlas.orchestrator import RunContext
from atlas.stages import StageSpec

_CTX = RunContext(
    run_id="r1", slug="acme-swe", task="score acme swe role", repo_root=Path("/tmp/repo")
)


def _stub_module(name: str, **attrs: object) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _score_fit_stage() -> StageSpec:
    return StageSpec(
        index=1,
        name="score_fit",
        span_kind="verify",
        tool="LIB:content_pipeline.score_jobs",
        gate_label="gate_shortlist",
        gate_index=0,
    )


def _ingest_stage() -> StageSpec:
    return StageSpec(
        index=0,
        name="ingest_postings",
        span_kind="tool",
        tool="LIB:content_pipeline.capture",
        gate_label=None,
        gate_index=None,
    )


# ---------------------------------------------------------------------------
# score_jobs_adapter
# ---------------------------------------------------------------------------


def _install_score_jobs_stubs(
    *,
    run_pending_result: MagicMock,
    report_text: str = "## Shortlist\n...",
) -> dict[str, ModuleType]:
    use_case_instance = MagicMock()
    use_case_instance.run_pending.return_value = run_pending_result
    use_case_cls = MagicMock(return_value=use_case_instance)

    settings_instance = MagicMock(
        score_jobs_prompt_path="/prompts/classify.md",
        job_profile_path="/profile",
        captures_meta_path="/meta.jsonl",
        archive_root="/archive",
        access_failures_log_path="/access_failures.jsonl",
    )
    settings_cls = MagicMock(return_value=settings_instance)

    meta_store_instance = MagicMock()
    meta_store_instance.read_all.return_value = []
    meta_store_cls = MagicMock(return_value=meta_store_instance)

    access_failures_instance = MagicMock()
    access_failures_instance.read.return_value = []
    access_failures_cls = MagicMock(return_value=access_failures_instance)

    modules = {
        "application": _stub_module("application"),
        "application.use_cases": _stub_module("application.use_cases"),
        "application.use_cases.score_jobs": _stub_module(
            "application.use_cases.score_jobs", ScoreJobsUseCase=use_case_cls
        ),
        "infrastructure": _stub_module("infrastructure"),
        "infrastructure.cli": _stub_module("infrastructure.cli"),
        "infrastructure.cli.cmd_score_jobs": _stub_module(
            "infrastructure.cli.cmd_score_jobs",
            _build_llm_client=MagicMock(return_value=MagicMock()),
            _load_profile_text=MagicMock(return_value="profile text"),
            _load_prompt=MagicMock(return_value="prompt text"),
        ),
        "infrastructure.cli.score_jobs_report": _stub_module(
            "infrastructure.cli.score_jobs_report",
            render_report=MagicMock(return_value=report_text),
        ),
        "infrastructure.config": _stub_module("infrastructure.config"),
        "infrastructure.config.settings": _stub_module(
            "infrastructure.config.settings", Settings=settings_cls
        ),
        "infrastructure.storage": _stub_module("infrastructure.storage"),
        "infrastructure.storage.access_failures_log": _stub_module(
            "infrastructure.storage.access_failures_log",
            AccessFailuresLog=access_failures_cls,
        ),
        "infrastructure.storage.archive": _stub_module(
            "infrastructure.storage.archive", FilesystemArchive=MagicMock()
        ),
        "infrastructure.storage.meta_store": _stub_module(
            "infrastructure.storage.meta_store", CapturesMetaStore=meta_store_cls
        ),
    }
    return modules


def test_score_jobs_adapter_success() -> None:
    from atlas.library_adapters import score_jobs_adapter

    run_pending_result = MagicMock(failed=False, items_pending=2, items_scored=2)
    modules = _install_score_jobs_stubs(
        run_pending_result=run_pending_result, report_text="## Shortlist\nGREEN: 2"
    )
    stage = _score_fit_stage()

    with patch.dict(sys.modules, modules):
        outcome = score_jobs_adapter.invoke(ctx=_CTX, stage=stage)

    assert outcome.status == "success"
    assert outcome.error_type is None
    assert outcome.output_text == "## Shortlist\nGREEN: 2"
    assert outcome.stage is stage


def test_score_jobs_adapter_failure() -> None:
    from atlas.library_adapters import score_jobs_adapter

    run_pending_result = MagicMock(failed=True, items_pending=1, items_scored=0)
    modules = _install_score_jobs_stubs(run_pending_result=run_pending_result)
    stage = _score_fit_stage()

    with patch.dict(sys.modules, modules):
        outcome = score_jobs_adapter.invoke(ctx=_CTX, stage=stage)

    assert outcome.status == "failure"
    assert outcome.error_type == "score_jobs_failed"


def test_score_jobs_adapter_zero_pending() -> None:
    from atlas.library_adapters import score_jobs_adapter

    run_pending_result = MagicMock(failed=False, items_pending=0, items_scored=0)
    modules = _install_score_jobs_stubs(run_pending_result=run_pending_result)
    stage = _score_fit_stage()

    with patch.dict(sys.modules, modules):
        outcome = score_jobs_adapter.invoke(ctx=_CTX, stage=stage)

    assert outcome.status == "success"
    assert outcome.error_type is None


# ---------------------------------------------------------------------------
# capture_adapter
# ---------------------------------------------------------------------------


def _install_capture_stubs(*, run_all_results: list[MagicMock]) -> dict[str, ModuleType]:
    use_case_instance = MagicMock()
    use_case_instance.run_all.return_value = run_all_results
    use_case_cls = MagicMock(return_value=use_case_instance)

    dispatcher_instance = MagicMock()
    dispatcher_cls = MagicMock(return_value=dispatcher_instance)

    settings_instance = MagicMock(
        archive_root="/archive",
        captures_meta_path="/meta.jsonl",
        captures_path="/captures.md",
    )
    settings_cls = MagicMock(return_value=settings_instance)

    loader_instance = MagicMock()
    loader_instance.load.return_value = [
        MagicMock(type="rss"),
        MagicMock(type="generic"),
        MagicMock(type="gmail"),  # excluded by the adapter's type filter
    ]
    loader_cls = MagicMock(return_value=loader_instance)

    modules = {
        "application": _stub_module("application"),
        "application.dispatcher": _stub_module(
            "application.dispatcher", CrawlerDispatcher=dispatcher_cls
        ),
        "application.use_cases": _stub_module("application.use_cases"),
        "application.use_cases.capture": _stub_module(
            "application.use_cases.capture", CaptureUseCase=use_case_cls
        ),
        "infrastructure": _stub_module("infrastructure"),
        "infrastructure.config": _stub_module("infrastructure.config"),
        "infrastructure.config.loader": _stub_module(
            "infrastructure.config.loader", ConfigLoader=loader_cls
        ),
        "infrastructure.config.settings": _stub_module(
            "infrastructure.config.settings", Settings=settings_cls
        ),
        "infrastructure.scrapers": _stub_module("infrastructure.scrapers"),
        "infrastructure.scrapers.ats_boards": _stub_module(
            "infrastructure.scrapers.ats_boards", AtsBoardScraper=MagicMock()
        ),
        "infrastructure.scrapers.generic": _stub_module(
            "infrastructure.scrapers.generic", GenericScraper=MagicMock()
        ),
        "infrastructure.scrapers.rss": _stub_module(
            "infrastructure.scrapers.rss", RssScraper=MagicMock()
        ),
        "infrastructure.storage": _stub_module("infrastructure.storage"),
        "infrastructure.storage.archive": _stub_module(
            "infrastructure.storage.archive", FilesystemArchive=MagicMock()
        ),
        "infrastructure.storage.captures_md": _stub_module(
            "infrastructure.storage.captures_md", CapturesMdAppender=MagicMock()
        ),
        "infrastructure.storage.meta_store": _stub_module(
            "infrastructure.storage.meta_store", CapturesMetaStore=MagicMock()
        ),
    }
    return modules


def test_capture_adapter_all_sources_succeed() -> None:
    from atlas.library_adapters import capture_adapter

    results = [
        MagicMock(
            failed=False, source_id="rss-1", items_fetched=3, items_new=2, items_dupe=1, errors=0
        ),
        MagicMock(
            failed=False,
            source_id="generic-1",
            items_fetched=1,
            items_new=1,
            items_dupe=0,
            errors=0,
        ),
    ]
    modules = _install_capture_stubs(run_all_results=results)
    stage = _ingest_stage()

    with patch.dict(sys.modules, modules):
        outcome = capture_adapter.invoke(ctx=_CTX, stage=stage)

    assert outcome.status == "success"
    assert outcome.error_type is None
    assert outcome.stage is stage


def test_capture_adapter_partial_failure() -> None:
    from atlas.library_adapters import capture_adapter

    results = [
        MagicMock(
            failed=False, source_id="rss-1", items_fetched=3, items_new=2, items_dupe=1, errors=0
        ),
        MagicMock(
            failed=True, source_id="generic-1", items_fetched=0, items_new=0, items_dupe=0, errors=1
        ),
    ]
    modules = _install_capture_stubs(run_all_results=results)
    stage = _ingest_stage()

    with patch.dict(sys.modules, modules):
        outcome = capture_adapter.invoke(ctx=_CTX, stage=stage)

    assert outcome.status == "failure"
    assert outcome.error_type == "capture_source_failed"
