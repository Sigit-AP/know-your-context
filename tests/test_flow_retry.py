"""Tests for serve/flow_retry.py — deterministic retry/backoff/timeout (W13).

All timing is injected (fake sleep / fake clocks); the only real-clock tests
use generous bounds so they survive loaded CI machines.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time

import pytest

from know_your_context.serve.flow_retry import (
    DEFAULT_RETRY_POLICY,
    Deadline,
    FlowTimeoutError,
    RetryExhaustedError,
    RetryPolicy,
    compute_delay_ms,
    retry,
    retry_async,
    run_with_timeout,
    run_with_timeout_async,
)

# ------------------------------------------------------------------ policy


class TestRetryPolicyValidation:
    def test_defaults_are_sane(self) -> None:
        policy = RetryPolicy()
        assert policy.max_attempts >= 1
        assert policy.max_delay_ms >= policy.base_delay_ms >= 0
        assert policy.backoff_multiplier >= 1.0
        assert 0.0 <= policy.jitter_ratio <= 1.0
        assert policy.seed is None

    def test_frozen(self) -> None:
        policy = RetryPolicy()
        with pytest.raises(AttributeError):
            policy.max_attempts = 9  # type: ignore[misc]

    @pytest.mark.parametrize("value", [0, -1, 1.5, "3", True, None])
    def test_max_attempts_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            RetryPolicy(max_attempts=value)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", [-1, -0.5, "x", True])
    def test_base_delay_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="base_delay_ms"):
            RetryPolicy(base_delay_ms=value)  # type: ignore[arg-type]

    def test_max_delay_below_base_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_delay_ms"):
            RetryPolicy(base_delay_ms=500, max_delay_ms=100)

    @pytest.mark.parametrize("value", [0.5, 0.0, -2.0, "x"])
    def test_multiplier_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="backoff_multiplier"):
            RetryPolicy(backoff_multiplier=value)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", [-0.1, 1.1, "x", True])
    def test_jitter_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="jitter_ratio"):
            RetryPolicy(jitter_ratio=value)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", [1.5, "42", True])
    def test_seed_rejected(self, value: object) -> None:
        with pytest.raises(ValueError, match="seed"):
            RetryPolicy(seed=value)  # type: ignore[arg-type]

    def test_default_policy_singleton_valid(self) -> None:
        assert DEFAULT_RETRY_POLICY.max_attempts >= 1


# ------------------------------------------------------------ compute_delay


class TestComputeDelayMs:
    def test_pure_exponential_without_jitter(self) -> None:
        policy = RetryPolicy(base_delay_ms=100, backoff_multiplier=2.0, jitter_ratio=0.0, max_delay_ms=10_000)
        rng = random.Random(7)
        assert [compute_delay_ms(a, policy, rng=rng) for a in (1, 2, 3, 4)] == [100, 200, 400, 800]

    def test_capped_at_max_delay(self) -> None:
        policy = RetryPolicy(base_delay_ms=100, backoff_multiplier=10.0, jitter_ratio=0.0, max_delay_ms=250)
        rng = random.Random(7)
        assert [compute_delay_ms(a, policy, rng=rng) for a in (1, 2, 3)] == [100, 250, 250]

    def test_attempt_must_be_positive(self) -> None:
        with pytest.raises(AssertionError):
            compute_delay_ms(0, RetryPolicy(), rng=random.Random(1))

    def test_jitter_within_bounds(self) -> None:
        policy = RetryPolicy(base_delay_ms=1000, backoff_multiplier=1.0, jitter_ratio=0.25, max_delay_ms=5000)
        rng = random.Random(99)
        for attempt in range(1, 50):
            delay = compute_delay_ms(attempt, policy, rng=rng)
            assert 750 <= delay <= 1250

    def test_jitter_deterministic_for_same_seed(self) -> None:
        policy = RetryPolicy(base_delay_ms=100, backoff_multiplier=2.0, jitter_ratio=0.3, max_delay_ms=10_000)
        seq_a = [compute_delay_ms(a, policy, rng=random.Random(1234)) for a in range(1, 12)]
        seq_b = [compute_delay_ms(a, policy, rng=random.Random(1234)) for a in range(1, 12)]
        assert seq_a == seq_b

    def test_jitter_differs_across_seeds(self) -> None:
        policy = RetryPolicy(base_delay_ms=100, backoff_multiplier=2.0, jitter_ratio=0.3, max_delay_ms=10_000)
        seq_a = [compute_delay_ms(a, policy, rng=random.Random(1)) for a in range(1, 12)]
        seq_b = [compute_delay_ms(a, policy, rng=random.Random(2)) for a in range(1, 12)]
        assert seq_a != seq_b

    def test_zero_base_stays_zero(self) -> None:
        policy = RetryPolicy(base_delay_ms=0, jitter_ratio=0.5)
        assert compute_delay_ms(3, policy, rng=random.Random(5)) == 0


# --------------------------------------------------------------------- retry


class _Flaky:
    """Fails ``failures`` times with ``error`` then returns ``value``."""

    def __init__(self, failures: int, *, error: Exception | None = None, value: str = "ok") -> None:
        self.failures = failures
        self.error = error if error is not None else OSError("transient")
        self.value = value
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return self.value


class TestRetrySync:
    def test_success_first_try_sleeps_never(self) -> None:
        sleeps: list[float] = []
        flaky = _Flaky(0)
        assert retry(flaky, sleep=sleeps.append) == "ok"
        assert flaky.calls == 1
        assert sleeps == []

    def test_success_after_retries(self) -> None:
        sleeps: list[float] = []
        policy = RetryPolicy(max_attempts=4, base_delay_ms=100, backoff_multiplier=2.0, jitter_ratio=0.0)
        flaky = _Flaky(2)
        assert retry(flaky, policy=policy, sleep=sleeps.append) == "ok"
        assert flaky.calls == 3
        assert sleeps == [0.1, 0.2]  # seconds, deterministic without jitter

    def test_exhaustion_raises_typed_error(self) -> None:
        sleeps: list[float] = []
        policy = RetryPolicy(max_attempts=3, jitter_ratio=0.0)
        flaky = _Flaky(99, error=OSError("boom"))
        with pytest.raises(RetryExhaustedError) as info:
            retry(flaky, policy=policy, sleep=sleeps.append)
        assert info.value.code == "RETRY_EXHAUSTED"
        assert info.value.attempts == 3
        assert isinstance(info.value.last_error, OSError)
        assert isinstance(info.value.__cause__, OSError)

    def test_non_retryable_raises_immediately(self) -> None:
        flaky = _Flaky(99, error=ValueError("permanent"))
        with pytest.raises(ValueError, match="permanent"):
            retry(flaky, retryable=(OSError,), sleep=lambda _: None)
        assert flaky.calls == 1

    def test_keyboard_interrupt_never_retried(self) -> None:
        def boom() -> None:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            retry(boom, sleep=lambda _: None)

    def test_cancelled_error_never_retried(self) -> None:
        calls = 0

        def boom() -> None:
            nonlocal calls
            calls += 1
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            retry(boom, sleep=lambda _: None)
        assert calls == 1

    def test_max_attempts_one_disables_retry(self) -> None:
        flaky = _Flaky(1)
        with pytest.raises(RetryExhaustedError):
            retry(flaky, policy=RetryPolicy(max_attempts=1), sleep=lambda _: None)
        assert flaky.calls == 1

    def test_on_retry_callback_receives_attempt_delay_error(self) -> None:
        seen: list[tuple[int, int, BaseException]] = []
        policy = RetryPolicy(max_attempts=3, base_delay_ms=50, backoff_multiplier=2.0, jitter_ratio=0.0)
        flaky = _Flaky(2, error=OSError("x"))
        retry(flaky, policy=policy, sleep=lambda _: None, on_retry=lambda a, d, e: seen.append((a, d, e)))
        assert [(a, d) for a, d, _ in seen] == [(1, 50), (2, 100)]
        assert all(isinstance(e, OSError) for _, _, e in seen)

    def test_seed_makes_jittered_sleeps_reproducible(self) -> None:
        policy = RetryPolicy(max_attempts=5, base_delay_ms=100, jitter_ratio=0.4, seed=2026)

        def run() -> list[float]:
            sleeps: list[float] = []
            retry(_Flaky(4), policy=policy, sleep=sleeps.append)
            return sleeps

        assert run() == run()
        assert len(run()) == 4

    def test_explicit_rng_is_used(self) -> None:
        policy = RetryPolicy(max_attempts=3, base_delay_ms=100, jitter_ratio=0.0)
        sleeps: list[float] = []
        retry(_Flaky(2), policy=policy, sleep=sleeps.append, rng=random.Random(0))
        assert sleeps == [0.1, 0.2]

    def test_retryable_tuple_respected(self) -> None:
        flaky = _Flaky(1, error=ConnectionError("reset"))
        assert retry(flaky, retryable=(ConnectionError,), sleep=lambda _: None) == "ok"


# ------------------------------------------------------------------ deadline


class TestDeadline:
    def test_remaining_decreases_and_clamps(self) -> None:
        # __init__ consumes one tick for the start time.
        ticks = iter([100.0, 100.0, 100.05, 100.2, 100.5])
        deadline = Deadline(200, now=lambda: next(ticks))
        assert deadline.remaining_ms() == pytest.approx(200.0)
        assert deadline.remaining_ms() == pytest.approx(150.0)
        assert deadline.remaining_ms() == pytest.approx(0.0)

    def test_expired_property(self) -> None:
        ticks = iter([0.0, 0.0, 5.0])
        deadline = Deadline(1000, now=lambda: next(ticks))
        assert not deadline.expired
        assert deadline.expired

    def test_raise_if_expired(self) -> None:
        deadline = Deadline(1, now=lambda: 10.0)
        deadline._started = 0.0  # force expiry via injected clock
        with pytest.raises(FlowTimeoutError) as info:
            deadline.raise_if_expired()
        assert info.value.code == "FLOW_TIMEOUT"

    def test_invalid_timeout_rejected(self) -> None:
        for bad in (0, -5, 1.5, "x", True, None):
            with pytest.raises(ValueError, match="timeout_ms"):
                Deadline(bad)  # type: ignore[arg-type]


# ----------------------------------------------------------- sync timeout


class TestRunWithTimeout:
    def test_fast_function_returns(self) -> None:
        assert run_with_timeout(lambda: 41 + 1, timeout_ms=5000) == 42

    def test_slow_function_times_out(self) -> None:
        with pytest.raises(FlowTimeoutError) as info:
            run_with_timeout(lambda: time.sleep(2.0), timeout_ms=50)
        assert info.value.timeout_ms == 50

    def test_inner_exception_propagates(self) -> None:
        def boom() -> None:
            raise KeyError("inner")

        with pytest.raises(KeyError, match="inner"):
            run_with_timeout(boom, timeout_ms=5000)

    def test_timed_out_thread_is_daemon(self) -> None:
        with pytest.raises(FlowTimeoutError):
            run_with_timeout(lambda: time.sleep(5.0), timeout_ms=30)
        # No assertion on the thread beyond this: daemon=True means the
        # interpreter can exit; the timeout must not join forever.


# --------------------------------------------------------------- async lane


class TestRetryAsync:
    def test_success_first_try(self) -> None:
        async def main() -> tuple[str, int]:
            calls = 0

            async def fn() -> str:
                nonlocal calls
                calls += 1
                return "ok"

            return await retry_async(fn), calls

        result, calls = asyncio.run(main())
        assert (result, calls) == ("ok", 1)

    def test_retries_with_async_sleeps(self) -> None:
        async def main() -> tuple[str, list[float]]:
            sleeps: list[float] = []

            async def fake_sleep(seconds: float) -> None:
                sleeps.append(seconds)

            attempts = 0

            async def fn() -> str:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise OSError("flaky")
                return "ok"

            policy = RetryPolicy(max_attempts=4, base_delay_ms=100, backoff_multiplier=2.0, jitter_ratio=0.0)
            return await retry_async(fn, policy=policy, sleep=fake_sleep), sleeps

        result, sleeps = asyncio.run(main())
        assert result == "ok"
        assert sleeps == [0.1, 0.2]

    def test_cancelled_error_propagates_without_retry(self) -> None:
        async def main() -> int:
            calls = 0

            async def fn() -> str:
                nonlocal calls
                calls += 1
                raise asyncio.CancelledError

            with pytest.raises(asyncio.CancelledError):
                await retry_async(fn)
            return calls

        assert asyncio.run(main()) == 1

    def test_exhaustion_raises_typed(self) -> None:
        async def main() -> None:
            async def fn() -> None:
                raise OSError("always")

            with pytest.raises(RetryExhaustedError) as info:
                await retry_async(fn, policy=RetryPolicy(max_attempts=2, jitter_ratio=0.0), sleep=_noop_sleep)
            assert info.value.attempts == 2

        asyncio.run(main())

    def test_real_task_cancellation_during_backoff(self) -> None:
        async def main() -> str:
            calls = 0

            async def fn() -> str:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("flaky")
                return "late"

            policy = RetryPolicy(max_attempts=3, base_delay_ms=60_000, max_delay_ms=120_000, jitter_ratio=0.0)
            task = asyncio.create_task(retry_async(fn, policy=policy))
            await asyncio.sleep(0.05)  # let the first attempt fail and enter backoff
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return "cancelled-promptly"

        assert asyncio.run(asyncio.wait_for(main(), timeout=5.0)) == "cancelled-promptly"


async def _noop_sleep(_seconds: float) -> None:
    return None


class TestRunWithTimeoutAsync:
    def test_fast_coro_returns(self) -> None:
        async def fn() -> int:
            return 7

        assert asyncio.run(run_with_timeout_async(fn(), timeout_ms=5000)) == 7

    def test_slow_coro_times_out(self) -> None:
        async def fn() -> None:
            await asyncio.sleep(5.0)

        with pytest.raises(FlowTimeoutError):
            asyncio.run(run_with_timeout_async(fn(), timeout_ms=50))

    def test_inner_flow_timeout_not_double_wrapped(self) -> None:
        async def fn() -> None:
            raise FlowTimeoutError("inner", timeout_ms=1)

        async def main() -> None:
            with pytest.raises(FlowTimeoutError, match="inner"):
                await run_with_timeout_async(fn(), timeout_ms=5000)

        asyncio.run(main())

    def test_cancellation_propagates(self) -> None:
        async def main() -> None:
            task = asyncio.create_task(run_with_timeout_async(asyncio.sleep(30), timeout_ms=60_000))
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(asyncio.wait_for(main(), timeout=5.0))


class TestThreadSafety:
    def test_policy_rng_instances_are_independent(self) -> None:
        policy = RetryPolicy(seed=42, jitter_ratio=0.5)
        results: list[list[int]] = [[], []]

        def worker(index: int) -> None:
            rng = policy.rng()
            results[index] = [compute_delay_ms(a, policy, rng=rng) for a in range(1, 8)]

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert results[0] == results[1]  # same seed, same stream — no shared state
