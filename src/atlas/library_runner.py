"""Dispatches LIB:-prefixed stage tools to content-pipeline use cases in-process.

Trust boundary: ``_REGISTRY`` is a closed allow-list (TRD-v2 §3.8 / Phase-2 plan
§9). A workflow YAML's ``LIB:<ref>`` can only ever resolve to one of the
hardcoded adapter functions below — never an arbitrary dotted path executed
from YAML content.
"""

from __future__ import annotations

import importlib
import logging
from typing import Protocol

from atlas.orchestrator import RunContext, StageOutcome
from atlas.stages import StageSpec

logger = logging.getLogger(__name__)


class _AdapterFn(Protocol):
    def __call__(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome: ...


_REGISTRY: dict[str, str] = {
    "content_pipeline.capture": "atlas.library_adapters.capture_adapter.invoke",
    "content_pipeline.score_jobs": "atlas.library_adapters.score_jobs_adapter.invoke",
}


class LibraryStageRunner:
    """Dispatches LIB:-prefixed stage tools to content-pipeline use cases in-process.

    Lazily imports content-pipeline modules per-call (NFR-3) so atlas's core
    package import never requires content-pipeline to be installed. Each
    registry entry is a thin adapter function (atlas.library_adapters.*) that
    owns the use-case construction (settings, ports) — kept out of this class
    to bound its size and isolate per-use-case wiring churn.

    NOTE on StageSpec.timeout_s: this runner makes IN-PROCESS calls, not
    subprocesses, so it does NOT enforce stage.timeout_s — there is no
    subprocess.run(timeout=...) to apply it to. A LIB: stage's effective
    latency bound comes from content-pipeline's own internal client timeouts.
    timeout_s is honored only by SubprocessStageRunner (RAW:/plugin stages).
    Setting timeout_s on a LIB: stage is therefore inert (Resolved Decision #5).
    """

    def run(self, *, ctx: RunContext, stage: StageSpec) -> StageOutcome:
        ref = stage.tool.removeprefix("LIB:").strip()
        adapter_path = _REGISTRY.get(ref)
        if adapter_path is None:
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text="",
                error_type="library_ref_unknown",
            )

        try:
            adapter = _import_adapter(adapter_path)
        except ImportError as exc:
            logger.warning("content-pipeline not importable for %s: %s", ref, exc)
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=(
                    "content-pipeline is not installed.\n"
                    "  Install it:  uv sync --extra job  OR  pip install -e ../content-pipeline\n"
                    "  Dependency-free alternative: atlas run \"<task>\" --workflow job_cli"
                ),
                error_type="content_pipeline_not_installed",
            )

        try:
            return adapter(ctx=ctx, stage=stage)
        except Exception as exc:  # noqa: BLE001 - use-case errors must not crash the orchestrator
            logger.error("LibraryStageRunner adapter %s failed: %s", ref, exc)
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=str(exc),
                error_type="library_adapter_error",
            )


def _import_adapter(dotted_path: str) -> _AdapterFn:
    """Resolve a dotted adapter path; raises ImportError if content-pipeline
    (or the adapter module itself) is not on the path."""
    module_path, func_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    fn: _AdapterFn = getattr(module, func_name)
    return fn
