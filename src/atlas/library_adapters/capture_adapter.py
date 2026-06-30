"""Adapter for the content_pipeline.capture LIB: stage.

Lazily imports content-pipeline at call time (NFR-3) — no module-level
``from src....`` import anywhere in this file.
"""

from __future__ import annotations

from atlas.orchestrator import RunContext, StageOutcome
from atlas.stages import StageSpec


def invoke(*, ctx: RunContext, stage: StageSpec) -> StageOutcome:
    """Construct CaptureUseCase with the dispatcher registered for job-board
    sources only (rss, generic, ats_boards) and call run_all() across the
    user's configured SourceConfig list.

    Gmail/IMAP/web_search/LinkedIn registration is intentionally omitted —
    those require credential wiring (settings.gmail_*, settings.imap_*) that
    is a one-time user setup concern, not this adapter's job.
    """
    from src.application.dispatcher import CrawlerDispatcher
    from src.application.use_cases.capture import CaptureUseCase
    from src.infrastructure.config.loader import ConfigLoader
    from src.infrastructure.config.settings import Settings
    from src.infrastructure.scrapers.ats_boards import AtsBoardScraper
    from src.infrastructure.scrapers.generic import GenericScraper
    from src.infrastructure.scrapers.rss import RssScraper
    from src.infrastructure.storage.archive import FilesystemArchive
    from src.infrastructure.storage.captures_md import CapturesMdAppender
    from src.infrastructure.storage.meta_store import CapturesMetaStore

    settings = Settings()
    dispatcher = CrawlerDispatcher()
    dispatcher.register("rss", RssScraper)
    dispatcher.register("generic", GenericScraper)
    dispatcher.register("ats_boards", AtsBoardScraper)

    archive = FilesystemArchive(archive_root=settings.archive_root)
    meta_store = CapturesMetaStore(meta_path=settings.captures_meta_path)
    captures_writer = CapturesMdAppender(captures_path=settings.captures_path)

    use_case = CaptureUseCase(
        dispatcher=dispatcher,
        archive=archive,
        meta_store=meta_store,
        captures_writer=captures_writer,
        captures_meta_path=settings.captures_meta_path,
    )

    loader = ConfigLoader()
    configs = [c for c in loader.load() if c.type in ("rss", "generic", "ats_boards")]
    results = use_case.run_all(configs)

    any_failed = any(r.failed for r in results)
    summary = "\n".join(
        f"{r.source_id}: fetched={r.items_fetched} new={r.items_new} "
        f"dupe={r.items_dupe} errors={r.errors}"
        for r in results
    )
    return StageOutcome(
        stage=stage,
        span_id="",
        status="failure" if any_failed else "success",
        output_text=summary,
        error_type="capture_source_failed" if any_failed else None,
    )
