"""Adaptive group-commit window (TASK1-PUSH200 P1).

The write arbiter's gather window used to be a single static budget: every
mutation — including an isolated write with nothing to batch with — paid the
full window (2 ms by plan) before commit. The adaptive window scales the wait
with the measured arrival rate (EMA of inter-submit intervals), never
exceeding the static envelope: sparse traffic commits ~immediately, dense
bursts keep the full batching window. Neither reference implements adaptive
write-batch geometry (codegraph: fixed batch constants
src/extraction/index.ts:46 / src/resolution/index.ts:2473; graphify: no write
arbiter at all, only a static LLM label batch graphify/llm.py:3408).

Kill switch: KNOW_YOUR_CONTEXT_NO_ADAPTIVE_BATCH=1 restores the static window
(W7 rule: every optimization is switchable).
"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from typing import Any, cast

import pytest

from _serve_helpers import make_config
from know_your_context.projection import GraphProjector
from know_your_context.serve import superpower as sp
from know_your_context.store import EventStore


def _arbiter(tmp_path: Path, store: EventStore) -> Any:
    config = make_config(tmp_path)
    projector = GraphProjector(store, output_root=tmp_path / "graph")
    return sp._WriteArbiter(  # noqa: SLF001
        config,
        store=store,
        projector=projector,
        adapter_factory=lambda actor: cast(Any, None),
    )


def _mutation(actor: str = "agent-a") -> Any:
    return sp._Mutation(  # noqa: SLF001
        "knowledge.put_node", {"k": "v"}, actor, Future(), None, None, None
    )


# ------------------------------------------------------------- pure window


class TestAdaptiveWindowPure:
    def test_unknown_arrival_rate_keeps_full_window(self) -> None:
        assert sp._adaptive_window_seconds(0.002, None) == 0.002  # noqa: SLF001

    def test_dense_traffic_keeps_full_window(self) -> None:
        # arrivals every 0.1 ms with a 2 ms budget: batching pays, keep it all
        assert sp._adaptive_window_seconds(0.002, 0.0001) == 0.002  # noqa: SLF001

    def test_sparse_traffic_shrinks_window(self) -> None:
        # arrivals every 50 ms with a 2 ms budget: waiting catches ~4% of the
        # time — the wait must collapse far below the static budget
        got = sp._adaptive_window_seconds(0.002, 0.05)  # noqa: SLF001
        assert got == pytest.approx(0.002 * 0.002 / 0.05)
        assert got < 0.0001

    def test_window_never_exceeds_static_budget(self) -> None:
        for ema in (1e-9, 1e-6, 1e-4, 1e-3, 2e-3, 1e-2, 1.0, 100.0):
            assert sp._adaptive_window_seconds(0.002, ema) <= 0.002  # noqa: SLF001

    def test_window_monotone_nonincreasing_in_ema(self) -> None:
        emas = [1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0]
        windows = [sp._adaptive_window_seconds(0.002, e) for e in emas]  # noqa: SLF001
        pairs = zip(windows, windows[1:], strict=False)
        assert all(a >= b for a, b in pairs)

    def test_nonpositive_ema_is_treated_as_dense(self) -> None:
        assert sp._adaptive_window_seconds(0.002, 0.0) == 0.002  # noqa: SLF001


# ---------------------------------------------------------- arrival EMA


class TestArrivalEma:
    def test_first_arrival_sets_baseline_without_ema(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        arbiter._note_arrival(100.0)  # noqa: SLF001
        assert arbiter._arrival_ema_seconds is None  # noqa: SLF001
        assert arbiter._last_submit_mono == 100.0  # noqa: SLF001

    def test_second_arrival_seeds_ema_with_interval(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        arbiter._note_arrival(100.0)  # noqa: SLF001
        arbiter._note_arrival(100.04)  # noqa: SLF001
        assert arbiter._arrival_ema_seconds == pytest.approx(0.04)  # noqa: SLF001

    def test_ema_blends_with_quarter_alpha(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        arbiter._note_arrival(0.0)  # noqa: SLF001
        arbiter._note_arrival(0.04)  # noqa: SLF001  ema = 0.04
        arbiter._note_arrival(0.14)  # noqa: SLF001  dt=0.10 -> 0.04 + .25*(.10-.04)
        assert arbiter._arrival_ema_seconds == pytest.approx(0.055)  # noqa: SLF001

    def test_nonpositive_interval_ignored(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        arbiter._note_arrival(10.0)  # noqa: SLF001
        arbiter._note_arrival(10.0)  # noqa: SLF001  clock tie: no information
        assert arbiter._arrival_ema_seconds is None  # noqa: SLF001


# ------------------------------------------------------- gather behaviour


class TestGatherAdaptive:
    def _recording_queue(self, arbiter: Any) -> list[float]:
        timeouts: list[float] = []
        original_get = arbiter._write_q.get  # noqa: SLF001

        def recording_get(*, timeout: float, exclude: Any = None) -> None:
            timeouts.append(timeout)
            return None  # queue empty: burst never continues

        arbiter._write_q.get = recording_get  # noqa: SLF001
        assert original_get is not None
        return timeouts

    def test_sparse_traffic_does_not_wait(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        timeouts = self._recording_queue(arbiter)
        # one write per ~50 ms: EMA far above the 2 ms static window
        for tick in range(6):
            arbiter._note_arrival(1000.0 + tick * 0.05)  # noqa: SLF001
        batch = arbiter._gather_batch(_mutation())  # noqa: SLF001
        assert batch == []
        assert timeouts == [] or max(timeouts) < 0.0005

    def test_dense_traffic_keeps_full_window(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        timeouts = self._recording_queue(arbiter)
        # burst: one write per ~0.1 ms
        for tick in range(8):
            arbiter._note_arrival(1000.0 + tick * 0.0001)  # noqa: SLF001
        arbiter._gather_batch(_mutation())  # noqa: SLF001
        assert timeouts, "dense traffic must still gather with a real window"
        # ~full window (real clock ticks between deadline and the get call)
        assert max(timeouts) == pytest.approx(
            arbiter._config.group_commit_window_seconds,  # noqa: SLF001
            abs=0.0005,
        )

    def test_unknown_rate_first_write_waits_full_window(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "s.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        timeouts = self._recording_queue(arbiter)
        arbiter._gather_batch(_mutation())  # noqa: SLF001
        assert max(timeouts) == pytest.approx(
            arbiter._config.group_commit_window_seconds,  # noqa: SLF001
            abs=0.0005,
        )

    def test_kill_switch_restores_static_window(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_NO_ADAPTIVE_BATCH", "1")
        store = EventStore(tmp_path / "s.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        timeouts = self._recording_queue(arbiter)
        for tick in range(6):
            arbiter._note_arrival(1000.0 + tick * 0.05)  # noqa: SLF001  sparse
        arbiter._gather_batch(_mutation())  # noqa: SLF001
        assert max(timeouts) == pytest.approx(
            arbiter._config.group_commit_window_seconds,  # noqa: SLF001
            abs=0.0005,
        )

    def test_batch_cap_unchanged_static_budget(self, tmp_path: Path) -> None:
        # the adaptive mechanism tunes the WINDOW only; the batch cap stays a
        # static Tiger Style budget (fairness + fsync amortization envelope)
        store = EventStore(tmp_path / "s.sqlite3")
        arbiter = _arbiter(tmp_path, store)
        assert arbiter._config.group_commit_batch == make_config(tmp_path).group_commit_batch  # noqa: SLF001

    def test_kill_switch_env_pinned_to_registry(self) -> None:
        # the owner constant and the operator registry must never drift apart
        from know_your_context.ops import switches

        assert switches.ENV_NO_ADAPTIVE_BATCH == sp._NO_ADAPTIVE_BATCH_ENV  # noqa: SLF001
        entry = next(e for e in switches.KILL_SWITCHES if e.env == switches.ENV_NO_ADAPTIVE_BATCH)
        assert entry.name == "daemon.adaptive_batch"
        assert switches.load_switches(env={switches.ENV_NO_ADAPTIVE_BATCH: "1"}).adaptive_batch_disabled() is True
        assert switches.load_switches(env={}).adaptive_batch_disabled() is False
