"""Tests for ``know_your_context.serve.middleware`` (W13 SUB-125).

The webview HTTP layer runs an explicit middleware stack: request-id, timing,
capability gate, typed error mapping. Behavior contract: the stack is ordered,
every stage sees the request context, failures map to typed JSON envelopes,
and a middleware that raises never produces a bare connection drop.
"""

from __future__ import annotations

import pytest

from know_your_context.enums import RouteAccess
from know_your_context.serve.middleware import (
    DEFAULT_TRACE_CAPACITY,
    REQUEST_ID_HEADER,
    SERVER_TIMING_HEADER,
    CapabilityDeniedError,
    CapabilityGateMiddleware,
    Middleware,
    MiddlewareError,
    MiddlewareStack,
    RequestContext,
    RequestIdMiddleware,
    RequestTiming,
    TimingMiddleware,
    TypedErrorMiddleware,
)


class _Clock:
    """Injectable deterministic clock."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _ctx(path: str = "/api/graph", request_id: str = "r-1", headers: dict[str, str] | None = None) -> RequestContext:
    return RequestContext(request_id=request_id, method="GET", path=path, client="127.0.0.1", headers=headers or {})


class TestRequestContext:
    def test_context_fields(self) -> None:
        ctx = _ctx()
        assert ctx.request_id == "r-1"
        assert ctx.method == "GET"
        assert ctx.path == "/api/graph"
        assert ctx.client == "127.0.0.1"
        assert ctx.response_headers == {}

    def test_context_rejects_empty_request_id(self) -> None:
        with pytest.raises(MiddlewareError):
            RequestContext(request_id="", path="/", method="GET", client="127.0.0.1")

    def test_context_is_frozen(self) -> None:
        ctx = _ctx()
        with pytest.raises(AttributeError):
            ctx.path = "/other"  # type: ignore[misc]


class TestRequestIdMiddleware:
    def test_echoes_present_request_id(self) -> None:
        mw = RequestIdMiddleware()
        ctx = _ctx(headers={REQUEST_ID_HEADER: "given-id"})
        mw.before(ctx)
        assert ctx.response_headers[REQUEST_ID_HEADER] == "given-id"

    def test_uses_context_id_when_header_absent(self) -> None:
        mw = RequestIdMiddleware()
        ctx = _ctx(request_id="generated-1")
        mw.before(ctx)
        assert ctx.response_headers[REQUEST_ID_HEADER] == "generated-1"

    def test_is_middleware_protocol(self) -> None:
        assert isinstance(RequestIdMiddleware(), Middleware)


class TestTimingMiddleware:
    def test_measures_duration_into_server_timing_header(self) -> None:
        clock = _Clock()
        mw = TimingMiddleware(clock=clock)
        ctx = _ctx()
        mw.before(ctx)
        clock.advance(0.125)
        mw.stamp(ctx)
        assert ctx.response_headers[SERVER_TIMING_HEADER] == "app;dur=125"
        mw.after(ctx, status=200)
        assert mw.recent()[0].duration_ms == 125

    def test_records_trace(self) -> None:
        clock = _Clock()
        mw = TimingMiddleware(clock=clock)
        ctx = _ctx(path="/api/health")
        mw.before(ctx)
        clock.advance(0.010)
        mw.after(ctx, status=200)
        trace = mw.recent()
        assert len(trace) == 1
        assert isinstance(trace[0], RequestTiming)
        assert trace[0].path == "/api/health"
        assert trace[0].status == 200
        assert trace[0].duration_ms == 10

    def test_trace_ring_buffer_bounded_and_ordered(self) -> None:
        clock = _Clock()
        mw = TimingMiddleware(clock=clock, capacity=3)
        for index in range(4):
            ctx = _ctx(path=f"/p{index}", request_id=f"r{index}")
            mw.before(ctx)
            clock.advance(0.001)
            mw.after(ctx, status=200)
        trace = mw.recent()
        assert len(trace) == 3
        assert [entry.path for entry in trace] == ["/p1", "/p2", "/p3"]

    def test_default_capacity_is_sane(self) -> None:
        assert DEFAULT_TRACE_CAPACITY >= 8

    def test_capacity_validated_fail_closed(self) -> None:
        with pytest.raises(MiddlewareError):
            TimingMiddleware(capacity=0)
        with pytest.raises(MiddlewareError):
            TimingMiddleware(capacity=True)

    def test_summary_empty(self) -> None:
        assert TimingMiddleware().summary() == {"count": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0}

    def test_summary_integer_percentiles(self) -> None:
        clock = _Clock()
        mw = TimingMiddleware(clock=clock)
        for index in range(10):
            ctx = _ctx(request_id=f"r{index}")
            mw.before(ctx)
            clock.advance(0.001 * (index + 1))  # durations 1..10 ms
            mw.after(ctx, status=200)
        summary = mw.summary()
        assert summary["count"] == 10
        assert summary["max_ms"] == 10
        assert summary["p50_ms"] >= 1
        assert summary["p95_ms"] >= summary["p50_ms"]


class TestCapabilityGateMiddleware:
    def test_allows_when_checker_passes(self) -> None:
        mw = CapabilityGateMiddleware(checker=lambda ctx: True)
        mw.before(_ctx())  # no raise

    def test_denies_with_typed_error(self) -> None:
        mw = CapabilityGateMiddleware(checker=lambda ctx: False)
        with pytest.raises(CapabilityDeniedError) as info:
            mw.before(_ctx())
        assert info.value.code == "CAPABILITY_DENIED"

    def test_denial_carries_status(self) -> None:
        mw = CapabilityGateMiddleware(checker=lambda ctx: False)
        with pytest.raises(CapabilityDeniedError) as info:
            mw.before(_ctx())
        assert info.value.status == 401

    def test_checker_receives_context(self) -> None:
        seen: list[str] = []

        def _deny(ctx: RequestContext) -> bool:
            seen.append(ctx.path)
            return False

        mw = CapabilityGateMiddleware(checker=_deny)
        with pytest.raises(CapabilityDeniedError):
            mw.before(_ctx(path="/api/audit"))
        assert seen == ["/api/audit"]

    def test_route_access_enum(self) -> None:
        assert RouteAccess.AUTHENTICATED == "authenticated"


class TestTypedErrorMiddleware:
    def test_maps_typed_error_to_envelope(self) -> None:
        mw = TypedErrorMiddleware()

        class _Boom(Exception):
            code = "BOOMED"
            status = 409

        def handler(ctx: RequestContext) -> tuple[int, dict]:
            raise _Boom("kaput")

        status, payload = mw.wrap(_ctx(), handler)
        assert status == 409
        assert payload["error"]["code"] == "BOOMED"
        assert payload["error"]["retryable"] is False

    def test_untyped_error_maps_to_500_without_leaking(self) -> None:
        mw = TypedErrorMiddleware()

        def handler(ctx: RequestContext) -> tuple[int, dict]:
            raise RuntimeError("secret internals")

        status, payload = mw.wrap(_ctx(), handler)
        assert status == 500
        assert payload["error"]["code"] == "INTERNAL"
        assert "secret" not in payload["error"]["message"]

    def test_capability_denial_maps_to_401(self) -> None:
        mw = TypedErrorMiddleware()

        def handler(ctx: RequestContext) -> tuple[int, dict]:
            raise CapabilityDeniedError("no token")

        status, payload = mw.wrap(_ctx(), handler)
        assert status == 401
        assert payload["error"]["code"] == "CAPABILITY_DENIED"

    def test_success_passes_through(self) -> None:
        mw = TypedErrorMiddleware()
        status, payload = mw.wrap(_ctx(), lambda ctx: (200, {"ok": True}))
        assert status == 200
        assert payload == {"ok": True}


class TestMiddlewareStack:
    def test_runs_before_in_order_and_after_in_reverse(self) -> None:
        calls: list[str] = []

        class _Rec:
            def __init__(self, tag: str) -> None:
                self.tag = tag

            def before(self, ctx: RequestContext) -> None:
                calls.append(f"before:{self.tag}")

            def after(self, ctx: RequestContext, *, status: int) -> None:
                calls.append(f"after:{self.tag}")

        stack = MiddlewareStack((_Rec("a"), _Rec("b")))
        ctx = _ctx()
        stack.before(ctx)
        stack.after(ctx, status=200)
        assert calls == ["before:a", "before:b", "after:b", "after:a"]

    def test_rejects_empty_stack(self) -> None:
        with pytest.raises(MiddlewareError):
            MiddlewareStack(())

    def test_rejects_non_middleware_member(self) -> None:
        with pytest.raises(MiddlewareError):
            MiddlewareStack((object(),))  # type: ignore[arg-type]

    def test_full_composition(self) -> None:
        timings = TimingMiddleware()
        stack = MiddlewareStack(
            (
                RequestIdMiddleware(),
                timings,
                CapabilityGateMiddleware(checker=lambda ctx: True),
            )
        )
        ctx = _ctx()
        stack.before(ctx)
        stack.stamp(ctx)
        stack.after(ctx, status=200)
        assert ctx.response_headers[REQUEST_ID_HEADER] == "r-1"
        assert SERVER_TIMING_HEADER in ctx.response_headers
        assert timings.summary()["count"] == 1

    def test_gate_denial_stops_later_middleware(self) -> None:
        calls: list[str] = []

        class _Rec:
            def before(self, ctx: RequestContext) -> None:
                calls.append("ran")

            def after(self, ctx: RequestContext, *, status: int) -> None:
                pass

        stack = MiddlewareStack((CapabilityGateMiddleware(checker=lambda ctx: False), _Rec()))
        with pytest.raises(CapabilityDeniedError):
            stack.before(_ctx())
        assert calls == []
