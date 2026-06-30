"""Adapter for the content_pipeline.score_jobs LIB: stage.

Lazily imports content-pipeline at call time (NFR-3) — no module-level
``from src....`` import anywhere in this file.
"""

from __future__ import annotations

from atlas.orchestrator import RunContext, StageOutcome
from atlas.stages import StageSpec


def invoke(*, ctx: RunContext, stage: StageSpec) -> StageOutcome:
    """Construct ScoreJobsUseCase from content-pipeline Settings and run_pending()."""
    from src.application.use_cases.score_jobs import ScoreJobsUseCase
    from src.infrastructure.cli.cmd_score_jobs import (
        _build_llm_client,
        _load_profile_text,
        _load_prompt,
    )
    from src.infrastructure.cli.score_jobs_report import render_report
    from src.infrastructure.config.settings import Settings
    from src.infrastructure.storage.access_failures_log import AccessFailuresLog
    from src.infrastructure.storage.archive import FilesystemArchive
    from src.infrastructure.storage.meta_store import CapturesMetaStore

    settings = Settings()  # reads content-pipeline's own env/config, unrelated to .atlas.toml
    prompt_text = _load_prompt(settings.score_jobs_prompt_path)
    profile_text = _load_profile_text(settings.job_profile_path)
    llm_client = _build_llm_client(settings)
    meta_store = CapturesMetaStore(meta_path=settings.captures_meta_path)
    archive_reader = FilesystemArchive(settings.archive_root)

    use_case = ScoreJobsUseCase(
        llm_client=llm_client,
        meta_store=meta_store,
        archive_reader=archive_reader,
        profile_text=profile_text,
        prompt_text=prompt_text,
    )
    result = use_case.run_pending()

    ats_failures = AccessFailuresLog(settings.access_failures_log_path).read()
    report = render_report(meta_store.read_all(), ats_failures=ats_failures)

    status = "failure" if result.failed else "success"
    return StageOutcome(
        stage=stage,
        span_id="",
        status=status,
        output_text=report,
        error_type="score_jobs_failed" if result.failed else None,
    )
