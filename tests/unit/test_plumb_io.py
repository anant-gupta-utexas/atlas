"""Unit tests for atlas.plumb_io — record_span tokens kwarg (T-L0.5)."""

from __future__ import annotations

from atlas.plumb_io import PlumbIO


def test_record_span_tokens_none_unchanged_stub_shape() -> None:
    """Existing call shape (no tokens) — every pre-Phase-L0 call site."""
    plumb = PlumbIO(real=False)
    run_id = plumb.open_run(task="t")
    span_id = plumb.record_span(
        run_id=run_id,
        kind="tool",
        name="stage",
        status="success",
        latency_ms=12.0,
        error_type=None,
    )
    assert span_id
    assert len(plumb.spans) == 1
    assert plumb.spans[0]["tokens"] is None


def test_record_span_tokens_tuple_captured_in_stub_buffer() -> None:
    plumb = PlumbIO(real=False)
    run_id = plumb.open_run(task="t")
    plumb.record_span(
        run_id=run_id,
        kind="llm",
        name="code_gen",
        status="success",
        latency_ms=42.0,
        error_type=None,
        tokens=(100, 50),
    )
    assert plumb.spans[0]["tokens"] == (100, 50)


def test_record_span_real_mode_passes_tokens_to_add_span() -> None:
    plumb = PlumbIO(real=False)  # real=True but plumb unavailable falls back to stub;
    # exercise the real-mode branch directly via a fake run handle instead.
    plumb._real = True
    calls: list[dict[str, object]] = []

    class _FakeRunHandle:
        def add_span(self, kind: str, name: str, **kwargs: object) -> str:
            calls.append({"kind": kind, "name": name, **kwargs})
            return "span-1"

    plumb._run_handle = _FakeRunHandle()

    span_id = plumb.record_span(
        run_id="r1",
        kind="llm",
        name="code_gen",
        status="success",
        latency_ms=10.0,
        error_type=None,
        tokens=(10, 20),
    )
    assert span_id == "span-1"
    assert calls == [
        {
            "kind": "llm",
            "name": "code_gen",
            "latency_ms": 10.0,
            "status": "success",
            "error_type": None,
            "tokens": (10, 20),
        }
    ]


def test_no_run_level_cost_or_token_write_method_exists() -> None:
    """Negative assertion (T-L0.5): confirms the plumb-P1-a deferral.

    Run-level dollar_cost/tokens_in/tokens_out are not writable from the
    online run path in plumb v1.0.1 -- PlumbIO must expose no method that
    attempts one.
    """
    forbidden_names = {"set_usage", "record_cost", "set_dollar_cost", "record_usage"}
    plumb_methods = {name for name in dir(PlumbIO) if not name.startswith("_")}
    assert forbidden_names.isdisjoint(plumb_methods)
