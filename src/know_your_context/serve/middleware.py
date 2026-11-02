"""Explicit middleware layer for the webview HTTP surface (W13).

The viewer's request pipeline is one ordered :class:`MiddlewareStack`:

1. :class:`RequestIdMiddleware` — echo the caller's ``X-Request-Id`` or attach
   the handler-generated one, so every response is traceable;
2. :class:`TimingMiddleware` — measure wall time, emit ``Server-Timing``, and
   keep a bounded in-memory trace ring (served by ``/api/health/detail``);
3. :class:`CapabilityGateMiddleware` — the session-token/host capability check
   as an explicit layer (a denied request never reaches routing);
4. :class:`TypedErrorMiddleware` — map typed errors to the JSON error envelope;
   an untyped error maps to a ``500 INTERNAL`` envelope whose message never
   leaks internals.

Every stage is a small, independently testable object implementing the
:class:`Middleware` protocol. The stack fails closed: an empty stack or a
member that does not satisfy the protocol raises :class:`MiddlewareError`.

This module imports only the stdlib and :mod:`know_your_context.enums`, so it
can sit under the HTTP handler without cycles.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DEFAULT_TRACE_CAPACITY",
    "INTERNAL_ERROR_CODE",
    "REQUEST_ID_HEADER",
    "SERVER_TIMING_HEADER",
    "CapabilityDeniedError",
    "CapabilityGateMiddleware",
    "Middleware",
    "MiddlewareError",
    "MiddlewareStack",
    "RequestContext",
    "RequestTiming",
    "RequestIdMiddleware",
    "TimingMiddleware",
    "TypedErrorMiddleware",
]

#: Response header carrying the request correlation id.
REQUEST_ID_HEADER = "X-Request-Id"
#: Response header carrying the measured handler time (integers only).
SERVER_TIMING_HEADER = "Server-Timing"
#: Envelope code for untyped failures (internals are never echoed).
INTERNAL_ERROR_CODE = "INTERNAL"
#: Default capacity of the in-memory request trace ring.
DEFAULT_TRACE_CAPACITY = 64


class MiddlewareError(RuntimeError):
    """A middleware contract violation (typed, fail-closed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class CapabilityDeniedError(MiddlewareError):
    """The capability gate refused the request (mapped to HTTP 401)."""

    status = 401

    def __init__(self, message: str) -> None:
        super().__init__("CAPABILITY_DENIED", message)


@dataclass(frozen=True)
class RequestContext:
    """One request flowing through the stack.

    ``response_headers`` is the single mutable channel: middleware attach
    response headers there (the mapping itself is shared; the frozen dataclass
    only pins identity fields).
    """

    request_id: str
    method: str
    path: str
    client: str
    headers: Mapping[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id:
            raise MiddlewareError("INVALID_CONTEXT", "request_id must be non-empty")
        if not self.method or not self.path.startswith("/"):
            raise MiddlewareError("INVALID_CONTEXT", f"method/path malformed: {self.method!r} {self.path!r}")


@dataclass(frozen=True)
class RequestTiming:
    """One completed request's timing record (integer milliseconds)."""

    request_id: str
    path: str
    status: int
    duration_ms: int


@runtime_checkable
class Middleware(Protocol):
    """The middleware contract: ``before`` may raise; ``after`` never must."""

    def before(self, ctx: RequestContext) -> None: ...

    def after(self, ctx: RequestContext, *, status: int) -> None: ...


class RequestIdMiddleware:
    """Attach the correlation id to the response (echo or handler-generated)."""

    def before(self, ctx: RequestContext) -> None:
        presented = ctx.headers.get(REQUEST_ID_HEADER) or ctx.headers.get(REQUEST_ID_HEADER.lower())
        ctx.response_headers[REQUEST_ID_HEADER] = presented if presented else ctx.request_id

    def after(self, ctx: RequestContext, *, status: int) -> None:
        return


class TimingMiddleware:
    """Wall-time measurement plus a bounded trace ring of recent requests."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        capacity: int = DEFAULT_TRACE_CAPACITY,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise MiddlewareError("INVALID_CAPACITY", f"capacity must be an int >= 1, got {capacity!r}")
        self._clock = clock
        self._starts: dict[str, float] = {}
        self._trace: deque[RequestTiming] = deque(maxlen=capacity)

    def before(self, ctx: RequestContext) -> None:
        self._starts[ctx.request_id] = self._clock()

    def stamp(self, ctx: RequestContext) -> None:
        """Set the ``Server-Timing`` response header from elapsed time so far.

        Called just before response headers are sent (the final duration is
        recorded by ``after`` once the status is known).
        """
        started = self._starts.get(ctx.request_id)
        if started is None:
            return
        duration_ms = max(0, int(round((self._clock() - started) * 1000)))
        ctx.response_headers[SERVER_TIMING_HEADER] = f"app;dur={duration_ms}"

    def after(self, ctx: RequestContext, *, status: int) -> None:
        started = self._starts.pop(ctx.request_id, None)
        if started is None:
            return  # before() never ran (guard short-circuit); nothing to time
        duration_ms = max(0, int(round((self._clock() - started) * 1000)))
        self._trace.append(
            RequestTiming(request_id=ctx.request_id, path=ctx.path, status=status, duration_ms=duration_ms)
        )

    def recent(self) -> tuple[RequestTiming, ...]:
        """The trace ring, oldest first."""
        return tuple(self._trace)

    def summary(self) -> dict[str, int]:
        """Integer-only percentiles over the trace ring (nearest-rank)."""
        durations = sorted(entry.duration_ms for entry in self._trace)
        if not durations:
            return {"count": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0}

        def _rank(pct: int) -> int:
            index = (pct * len(durations) + 99) // 100 - 1
            return durations[max(0, index)]

        return {
            "count": len(durations),
            "p50_ms": _rank(50),
            "p95_ms": _rank(95),
            "max_ms": durations[-1],
        }


class CapabilityGateMiddleware:
    """The authorization decision as an explicit middleware layer.

    ``checker`` receives the context and returns True to admit; anything else
    raises :class:`CapabilityDeniedError` (typed, mapped to 401 downstream).
    """

    def __init__(self, *, checker: Callable[[RequestContext], bool]) -> None:
        self._checker = checker

    def before(self, ctx: RequestContext) -> None:
        if not self._checker(ctx):
            raise CapabilityDeniedError(f"request to {ctx.path!r} is not authorized")

    def after(self, ctx: RequestContext, *, status: int) -> None:
        return


class TypedErrorMiddleware:
    """Map handler failures to the webview JSON error envelope.

    Typed errors carry ``code`` (and optionally ``status``) attributes; untyped
    errors become ``500 INTERNAL`` with a static message — internals never leak.
    """

    def wrap(
        self, ctx: RequestContext, handler: Callable[[RequestContext], tuple[int, dict[str, Any]]]
    ) -> tuple[int, dict[str, Any]]:
        try:
            return handler(ctx)
        except Exception as error:  # noqa: BLE001 -- the mapping IS the boundary
            return self.map_error(error)

    def map_error(self, error: BaseException) -> tuple[int, dict[str, Any]]:
        """Map one error to ``(status, envelope)`` without a handler wrapper."""
        code = str(getattr(error, "code", INTERNAL_ERROR_CODE))
        status = int(getattr(error, "status", 500))
        if code == INTERNAL_ERROR_CODE:
            message = "internal error"  # never echo internals
        else:
            message = str(error).split(": ", 1)[-1] if code in str(error) else str(error)
        return status, {"error": {"code": code, "message": message, "retryable": False}}


class MiddlewareStack:
    """An ordered middleware tuple: ``before`` forward, ``after`` reversed."""

    def __init__(self, members: tuple[Middleware, ...]) -> None:
        if not members:
            raise MiddlewareError("EMPTY_STACK", "a middleware stack needs at least one member")
        for member in members:
            if not isinstance(member, Middleware):
                raise MiddlewareError(
                    "INVALID_MEMBER", f"{type(member).__name__} does not satisfy the Middleware protocol"
                )
        self._members = tuple(members)

    @property
    def members(self) -> tuple[Middleware, ...]:
        return self._members

    def before(self, ctx: RequestContext) -> None:
        for member in self._members:
            member.before(ctx)

    def stamp(self, ctx: RequestContext) -> None:
        """Let members that stamp response headers (e.g. timing) do so."""
        for member in self._members:
            stamp = getattr(member, "stamp", None)
            if callable(stamp):
                stamp(ctx)

    def after(self, ctx: RequestContext, *, status: int) -> None:
        for member in reversed(self._members):
            member.after(ctx, status=status)
