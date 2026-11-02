"""Deterministic retry / backoff / timeout primitives for all flow I/O (W13).

One policy object, two execution lanes (sync + async), zero global state:

* **Deterministic backoff**: delays come from :func:`compute_delay_ms`, a pure
  function of ``(attempt, policy, rng)``. The rng is always an explicit
  ``random.Random`` — either injected by the caller or derived from
  ``policy.seed`` — so DST simulations (``serve/simulation.py``) replay the
  exact delay stream byte-for-byte. The global ``random`` module is never
  touched.
* **Cancellation is sacred**: ``asyncio.CancelledError`` and
  ``KeyboardInterrupt`` are never retried and never swallowed; the async lane
  awaits a real (injectable) sleep so task cancellation interrupts backoff
  immediately.
* **Typed failures**: retry exhaustion raises :class:`RetryExhaustedError`
  (carrying ``attempts`` + ``last_error``, chained to the cause); timeouts
  raise :class:`FlowTimeoutError` (carrying ``timeout_ms``). Neither is a
  bare ``Exception`` string.
* **Invariant asserts**: attempt accounting (``attempts <= max_attempts``),
  delay bounds (``0 <= delay <= max_delay`` envelope), and deadline
  monotonicity are asserted, not assumed.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_RETRY_POLICY",
    "Deadline",
    "FlowTimeoutError",
    "RetryExhaustedError",
    "RetryPolicy",
    "compute_delay_ms",
    "retry",
    "retry_async",
    "run_with_timeout",
    "run_with_timeout_async",
]

#: Transient-by-default: cancellation/interrupts are BaseException and are
#: re-raised untouched regardless of this tuple.
DEFAULT_RETRYABLE: tuple[type[BaseException], ...] = (Exception,)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_MS = 100
DEFAULT_MAX_DELAY_MS = 5_000
DEFAULT_BACKOFF_MULTIPLIER = 2.0
DEFAULT_JITTER_RATIO = 0.2


class RetryExhaustedError(RuntimeError):
    """All attempts failed; ``__cause__`` is the last underlying error."""

    code = "RETRY_EXHAUSTED"

    def __init__(self, message: str, *, attempts: int, last_error: BaseException) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class FlowTimeoutError(TimeoutError):
    """A bounded wait outlived its deadline."""

    code = "FLOW_TIMEOUT"

    def __init__(self, message: str, *, timeout_ms: int) -> None:
        super().__init__(message)
        self.timeout_ms = timeout_ms


def _check_int(name: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an int >= {minimum}, got {value!r}")
    return value


def _check_number(name: str, value: object, *, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ValueError(f"{name} must be a number >= {minimum}, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class RetryPolicy:
    """Fail-closed retry knobs; every field validated at construction.

    ``seed`` makes jittered backoffs reproducible: :meth:`rng` returns a
    fresh ``random.Random(seed)`` per call, so two retry loops built from the
    same policy produce the identical delay stream (DST replay) and never
    share mutable rng state across threads.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_ms: int = DEFAULT_BASE_DELAY_MS
    max_delay_ms: int = DEFAULT_MAX_DELAY_MS
    backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER
    jitter_ratio: float = DEFAULT_JITTER_RATIO
    seed: int | None = None

    def __post_init__(self) -> None:
        _check_int("max_attempts", self.max_attempts, minimum=1)
        _check_int("base_delay_ms", self.base_delay_ms, minimum=0)
        _check_int("max_delay_ms", self.max_delay_ms, minimum=0)
        if self.max_delay_ms < self.base_delay_ms:
            raise ValueError(f"max_delay_ms must be >= base_delay_ms, got {self.max_delay_ms} < {self.base_delay_ms}")
        _check_number("backoff_multiplier", self.backoff_multiplier, minimum=1.0)
        jitter = _check_number("jitter_ratio", self.jitter_ratio, minimum=0.0)
        if jitter > 1.0:
            raise ValueError(f"jitter_ratio must be <= 1.0, got {self.jitter_ratio!r}")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ValueError(f"seed must be an int or None, got {self.seed!r}")

    def rng(self) -> random.Random:
        """A fresh deterministic stream; never shared between call sites."""
        return random.Random(self.seed)


#: Shared default; immutable so the singleton is safe.
DEFAULT_RETRY_POLICY = RetryPolicy()


def compute_delay_ms(attempt: int, policy: RetryPolicy, *, rng: random.Random) -> int:
    """The backoff delay *after* the 1-based failed ``attempt``.

    Pure: same ``(attempt, policy, rng state)`` → same delay. Exponential
    ``base * multiplier ** (attempt - 1)`` capped at ``max_delay_ms``, then
    symmetric jitter of ``± jitter_ratio`` drawn from ``rng``.
    """
    assert attempt >= 1, f"attempt is 1-based, got {attempt}"
    base = policy.base_delay_ms * (policy.backoff_multiplier ** (attempt - 1))
    capped = min(float(policy.max_delay_ms), base)
    if policy.jitter_ratio > 0.0 and capped > 0.0:
        delta = capped * policy.jitter_ratio
        capped += rng.uniform(-delta, delta)
    # max_delay_ms is a hard bound: jitter never pushes past it.
    delay = max(0, min(policy.max_delay_ms, int(round(capped))))
    assert 0 <= delay <= policy.max_delay_ms  # hard envelope, jitter included
    return delay


def _never_retry(error: BaseException) -> bool:
    return isinstance(error, KeyboardInterrupt | asyncio.CancelledError)


def retry[T](
    fn: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    retryable: tuple[type[BaseException], ...] = DEFAULT_RETRYABLE,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    on_retry: Callable[[int, int, BaseException], None] | None = None,
) -> T:
    """Run ``fn`` until it succeeds or the policy's attempts are exhausted.

    ``sleep`` receives **seconds** (injectable — tests pass a recorder).
    Cancellation and non-retryable errors propagate immediately; exhaustion
    raises :class:`RetryExhaustedError` chained to the last error.
    """
    active = policy if policy is not None else DEFAULT_RETRY_POLICY
    stream = rng if rng is not None else active.rng()
    attempt = 0
    while True:
        attempt += 1
        try:
            result = fn()
        except BaseException as error:  # noqa: BLE001 - classified below, never swallowed
            if _never_retry(error) or not isinstance(error, retryable):
                raise
            if attempt >= active.max_attempts:
                raise RetryExhaustedError(
                    f"retry exhausted after {attempt} attempt(s): {type(error).__name__}: {error}",
                    attempts=attempt,
                    last_error=error,
                ) from error
            delay_ms = compute_delay_ms(attempt, active, rng=stream)
            if on_retry is not None:
                on_retry(attempt, delay_ms, error)
            sleep(delay_ms / 1000.0)
            continue
        assert attempt <= active.max_attempts, f"attempt accounting broken: {attempt} > {active.max_attempts}"
        return result


async def retry_async[T](
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    retryable: tuple[type[BaseException], ...] = DEFAULT_RETRYABLE,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
    on_retry: Callable[[int, int, BaseException], None] | None = None,
) -> T:
    """The async lane of :func:`retry` — real awaits, real cancellation.

    Backoff awaits ``sleep`` (default :func:`asyncio.sleep`), so a cancelled
    task aborts mid-backoff instead of after it. ``asyncio.CancelledError``
    is never retried and never swallowed.
    """
    active = policy if policy is not None else DEFAULT_RETRY_POLICY
    stream = rng if rng is not None else active.rng()
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await fn()
        except BaseException as error:  # noqa: BLE001 - classified below, never swallowed
            if _never_retry(error) or not isinstance(error, retryable):
                raise
            if attempt >= active.max_attempts:
                raise RetryExhaustedError(
                    f"retry exhausted after {attempt} attempt(s): {type(error).__name__}: {error}",
                    attempts=attempt,
                    last_error=error,
                ) from error
            delay_ms = compute_delay_ms(attempt, active, rng=stream)
            if on_retry is not None:
                on_retry(attempt, delay_ms, error)
            await sleep(delay_ms / 1000.0)
            continue
        assert attempt <= active.max_attempts, f"attempt accounting broken: {attempt} > {active.max_attempts}"
        return result


class Deadline:
    """A monotonic deadline; ``now`` injectable for deterministic tests."""

    def __init__(self, timeout_ms: int, *, now: Callable[[], float] = time.monotonic) -> None:
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError(f"timeout_ms must be an int > 0, got {timeout_ms!r}")
        self._timeout_ms = timeout_ms
        self._now = now
        self._started = now()

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    def remaining_ms(self) -> float:
        remaining = self._timeout_ms - (self._now() - self._started) * 1000.0
        return max(0.0, remaining)

    @property
    def expired(self) -> bool:
        return self.remaining_ms() <= 0.0

    def raise_if_expired(self) -> None:
        if self.expired:
            raise FlowTimeoutError(f"deadline of {self._timeout_ms}ms exceeded", timeout_ms=self._timeout_ms)


def run_with_timeout[T](fn: Callable[[], T], *, timeout_ms: int) -> T:
    """Run a blocking callable with a hard bound, on a daemon thread.

    Python cannot kill a thread, so a timed-out callable keeps running in the
    background (daemon — it can never block interpreter exit); the caller
    gets :class:`FlowTimeoutError` immediately and must treat the underlying
    resource as abandoned. Errors raised by ``fn`` propagate unchanged.
    """
    deadline = Deadline(timeout_ms)
    box: list[tuple[str, Any]] = []

    def target() -> None:
        try:
            box.append(("ok", fn()))
        except BaseException as error:  # noqa: BLE001 - re-raised on the caller thread
            box.append(("err", error))

    worker = threading.Thread(target=target, name="flow-timeout", daemon=True)
    worker.start()
    worker.join(timeout=deadline.remaining_ms() / 1000.0)
    if worker.is_alive():
        raise FlowTimeoutError(f"operation exceeded {timeout_ms}ms", timeout_ms=timeout_ms)
    assert len(box) == 1, f"completed thread must record exactly one outcome, got {len(box)}"
    kind, payload = box[0]
    if kind == "err":
        raise payload
    return payload


async def run_with_timeout_async[T](coro: Awaitable[T], *, timeout_ms: int) -> T:
    """``asyncio.wait_for`` with a typed timeout that never double-wraps."""
    Deadline(timeout_ms)  # validation only — the event loop owns the clock
    try:
        return await asyncio.wait_for(coro, timeout=timeout_ms / 1000.0)
    except TimeoutError as error:
        if isinstance(error, FlowTimeoutError):
            raise
        raise FlowTimeoutError(f"operation exceeded {timeout_ms}ms", timeout_ms=timeout_ms) from error
