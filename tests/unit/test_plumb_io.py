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
            "attributes": None,
        }
    ]


def test_set_usage_writes_run_level_cost() -> None:
    """Inverted from T-L0.5's negative assertion, deliberately.

    That test pinned the plumb-P1-a *deferral*: in v1.0.1 run-level
    dollar_cost was unreachable from the online run path, so PlumbIO was
    required to expose no setter at all. plumb v1.1.0 shipped
    ``RunHandle.set_usage``, which is exactly the capability that deferral
    was waiting on, so the correct assertion is now the positive one. If this
    test ever needs reverting, the plumb dependency has gone backwards.
    """
    plumb = PlumbIO(real=False)
    run_id = plumb.open_run(task="t")

    plumb.set_usage(run_id=run_id, dollar_cost=1.25)

    assert plumb.usage == [
        {"run_id": run_id, "tokens_in": None, "tokens_out": None, "dollar_cost": 1.25}
    ]


def test_set_usage_omits_tokens_so_plumb_autofills_them() -> None:
    """atlas must not re-sum span tokens it already handed plumb.

    plumb auto-fills run-level tokens_in/tokens_out from buffered spans at
    close time (v1.1 FR-USAGE-3); dollar_cost is the only field it never
    auto-fills. Passing None for the token fields is therefore correct, not
    an oversight -- this pins the intent so a later "fix" doesn't double-count.
    """
    plumb = PlumbIO(real=False)
    run_id = plumb.open_run(task="t")
    plumb.record_span(
        run_id=run_id,
        kind="llm",
        name="code_gen",
        status="success",
        latency_ms=1.0,
        error_type=None,
        tokens=(10, 20),
    )

    plumb.set_usage(run_id=run_id, dollar_cost=0.5)

    assert plumb.usage[0]["tokens_in"] is None
    assert plumb.usage[0]["tokens_out"] is None


def test_every_span_kind_atlas_emits_is_valid_in_plumb() -> None:
    """Guards the whole class of bug, not just the one instance.

    `sync_prior_prs` passed kind="deliver", which plumb's SpanKind enum does
    not define, so plumb raised ValueError and the entire tick died. It
    survived to production because that code path was unreachable until an
    unrelated sync fix, and because unit tests use PlumbIO(real=False), whose
    stub path never touches the enum.

    Collects every literal `kind=` atlas passes to record_span plus every
    span_kind declared in a shipped workflow, and checks all of them.
    """
    import re
    from pathlib import Path as _Path

    from plumb.core.entities import SpanKind

    valid = {m.value for m in SpanKind}
    src = _Path(__file__).parents[2] / "src" / "atlas"

    emitted: set[str] = set()
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # Scoped to record_span call sites: a bare `kind="..."` scan also
        # picks up plumb_run(kind="online"), which is a *run* kind and a
        # different enum entirely.
        for call in text.split("record_span(")[1:]:
            found = re.search(r'kind="([a-z_]+)"', call)
            if found:
                emitted.add(found.group(1))
    for yml in (src / "workflows").glob("*.yaml"):
        emitted |= set(re.findall(r"span_kind:\s*([a-z_]+)", yml.read_text(encoding="utf-8")))

    assert emitted, "regex found nothing — the guard would pass vacuously"
    assert emitted <= valid, (
        f"invalid SpanKind(s): {sorted(emitted - valid)}; valid={sorted(valid)}"
    )
