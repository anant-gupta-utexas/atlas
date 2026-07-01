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

# Top-level package names content-pipeline installs (its src-layout maps
# ``src/<pkg>`` -> importable ``<pkg>``). An ImportError naming one of these
# from inside an adapter body means content-pipeline itself is missing —
# distinct from an atlas-side adapter bug — so we surface the install hint.
_CONTENT_PIPELINE_ROOTS: frozenset[str] = frozenset({"application", "infrastructure", "domain"})

_NOT_INSTALLED_HINT = (
    "content-pipeline is not installed.\n"
    "  Install it:  uv sync --extra job  OR  pip install -e ../content-pipeline\n"
    '  Dependency-free alternative: atlas run "<task>" --workflow job_cli'
)


def _is_content_pipeline_import_error(exc: ImportError) -> bool:
    """True when *exc* is a missing content-pipeline module (not an atlas bug).

    Matches on the failed import's top-level package name so an ImportError for
    an unrelated module raised deeper in an adapter body is NOT masked as a
    missing optional dependency.
    """
    root = (exc.name or "").split(".", 1)[0]
    return root in _CONTENT_PIPELINE_ROOTS


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
            # Failure to import the *atlas* adapter module itself — an atlas-side
            # bug, not a missing optional dependency (the adapter modules have no
            # module-level content-pipeline imports). Surface it honestly.
            logger.error("atlas adapter module %s not importable: %s", adapter_path, exc)
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=str(exc),
                error_type="library_adapter_error",
            )

        try:
            return adapter(ctx=ctx, stage=stage)
        except ImportError as exc:
            # content-pipeline imports are function-local inside the adapter body,
            # so a genuinely-missing content-pipeline surfaces here. Only map to
            # the install hint when the missing module is a content-pipeline
            # package; any other ImportError is a real adapter/use-case bug.
            if _is_content_pipeline_import_error(exc):
                logger.warning("content-pipeline not importable for %s: %s", ref, exc)
                return StageOutcome(
                    stage=stage,
                    span_id="",
                    status="failure",
                    output_text=_NOT_INSTALLED_HINT,
                    error_type="content_pipeline_not_installed",
                )
            logger.error("LibraryStageRunner adapter %s import failed: %s", ref, exc)
            return StageOutcome(
                stage=stage,
                span_id="",
                status="failure",
                output_text=str(exc),
                error_type="library_adapter_error",
            )
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
