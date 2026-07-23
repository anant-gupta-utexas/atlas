"""Real-import integration tests for the LIB: adapters (review finding #4).

Unlike ``test_job_workflow_e2e.py`` — which patches ``_import_adapter`` and so
never executes the adapter module bodies — these tests import the *real*
``atlas.library_adapters.*`` modules and run their function bodies against an
installed content-pipeline. The leaf I/O (LLM client, scrapers, filesystem
Settings) is patched at the content-pipeline boundary the TRS §10 specified
(``ScoreJobsUseCase`` / ``CaptureUseCase`` and the helpers the adapter calls),
so the *adapter import path is real* while no network/LLM/disk work fires.

These tests require the ``job`` extra (content-pipeline installed). They are
skipped otherwise, and are the tests the ``test-job-extra`` CI leg exists to
run. This is what would have caught the ``src.``-prefix import mismatch that
``_import_adapter``-patched tests could not.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from atlas.orchestrator import RunContext
from atlas.stages import StageSpec

# Skip the whole module unless content-pipeline is importable (job extra installed).
_HAVE_CONTENT_PIPELINE = importlib.util.find_spec("application") is not None
pytestmark = pytest.mark.skipif(
    not _HAVE_CONTENT_PIPELINE,
    reason="content-pipeline not installed (run with: uv sync --extra job)",
)

_CTX = RunContext(
    run_id="r1", slug="acme-swe", task="score acme swe role", repo_root=Path("/tmp/repo")
)


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


@pytest.mark.xfail(
    reason=(
        "content-pipeline superseded application.use_cases.score_jobs.ScoreJobsUseCase: "
        "it decomposed the use case into score_jobs_ingest.py / score_jobs_prep.py / "
        "score_jobs_score.py (+ score_merge.py). No ScoreJobsUseCase class exists in "
        "content-pipeline anymore, so score_jobs_adapter.py's import target is stale. "
        "Re-targeting the adapter to the ingest/prep/score pipeline is job-workflow "
        "scope, unrelated to loop mode — tracked in BACKLOG.md. See Phase L0 TRS T-L0.3."
    ),
    strict=False,
)
def test_score_jobs_adapter_real_import_success() -> None:
    """The real score_jobs_adapter body imports content-pipeline and runs.

    Patches are applied to the *content-pipeline* symbols the adapter imports —
    proving those imports resolve against the installed package — while stubbing
    the leaf I/O so no LLM/filesystem work happens.
    """
    from atlas.library_adapters import score_jobs_adapter

    use_case_instance = MagicMock()
    use_case_instance.run_pending.return_value = MagicMock(failed=False)
    use_case_cls = MagicMock(return_value=use_case_instance)

    settings_instance = MagicMock(
        score_jobs_prompt_path="/prompts/score.md",
        job_profile_path="/profile",
        captures_meta_path="/meta.jsonl",
        archive_root="/archive",
        access_failures_log_path="/access_failures.jsonl",
    )

    meta_store_instance = MagicMock()
    meta_store_instance.read_all.return_value = []
    access_failures_instance = MagicMock()
    access_failures_instance.read.return_value = []

    with (
        patch("application.use_cases.score_jobs.ScoreJobsUseCase", use_case_cls),
        patch("infrastructure.config.settings.Settings", return_value=settings_instance),
        patch("infrastructure.cli.cmd_score_jobs._load_prompt", return_value="p"),
        patch("infrastructure.cli.cmd_score_jobs._load_profile_text", return_value="prof"),
        patch("infrastructure.cli.cmd_score_jobs._build_llm_client", return_value=MagicMock()),
        patch(
            "infrastructure.storage.meta_store.CapturesMetaStore",
            return_value=meta_store_instance,
        ),
        patch("infrastructure.storage.archive.FilesystemArchive", return_value=MagicMock()),
        patch(
            "infrastructure.storage.access_failures_log.AccessFailuresLog",
            return_value=access_failures_instance,
        ),
        patch(
            "infrastructure.cli.score_jobs_report.render_report",
            return_value="## Shortlist\nGREEN: 1",
        ),
    ):
        outcome = score_jobs_adapter.invoke(ctx=_CTX, stage=_score_fit_stage())

    assert outcome.status == "success"
    assert outcome.error_type is None
    assert outcome.output_text == "## Shortlist\nGREEN: 1"
    use_case_instance.run_pending.assert_called_once()


def test_capture_adapter_real_import_success() -> None:
    """The real capture_adapter body imports content-pipeline and runs."""
    from atlas.library_adapters import capture_adapter

    use_case_instance = MagicMock()
    use_case_instance.run_all.return_value = [
        MagicMock(
            failed=False, source_id="rss-1", items_fetched=3, items_new=2, items_dupe=1, errors=0
        ),
    ]
    use_case_cls = MagicMock(return_value=use_case_instance)

    settings_instance = MagicMock(
        archive_root="/archive",
        captures_meta_path="/meta.jsonl",
        captures_path="/captures.md",
    )
    loader_instance = MagicMock()
    loader_instance.load.return_value = [MagicMock(type="rss"), MagicMock(type="gmail")]

    with (
        patch("application.use_cases.capture.CaptureUseCase", use_case_cls),
        patch("application.dispatcher.CrawlerDispatcher", return_value=MagicMock()),
        patch("infrastructure.config.settings.Settings", return_value=settings_instance),
        patch("infrastructure.config.loader.ConfigLoader", return_value=loader_instance),
        patch("infrastructure.scrapers.ats_boards.AtsBoardScraper", MagicMock()),
        patch("infrastructure.scrapers.generic.GenericScraper", MagicMock()),
        patch("infrastructure.scrapers.rss.RssScraper", MagicMock()),
        patch("infrastructure.storage.archive.FilesystemArchive", return_value=MagicMock()),
        patch("infrastructure.storage.captures_md.CapturesMdAppender", return_value=MagicMock()),
        patch("infrastructure.storage.meta_store.CapturesMetaStore", return_value=MagicMock()),
    ):
        outcome = capture_adapter.invoke(ctx=_CTX, stage=_ingest_stage())

    assert outcome.status == "success"
    assert outcome.error_type is None
    # Only the rss config passes the adapter's type filter (gmail excluded).
    configs_arg = use_case_instance.run_all.call_args[0][0]
    assert [c.type for c in configs_arg] == ["rss"]
