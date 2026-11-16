"""Watchtower fleet probing: parallel fan-out (TASK1-PUSH200 P3).

The fleet probe used to walk stores strictly sequentially — fleet latency grew
O(n) even though probes are independent, read-mostly, and never raise.
``aggregate_status`` now fans out over a bounded ThreadPoolExecutor
(min(4, fleet size)) and re-sorts by name, so the report stays byte-identical
to the serial run. Serial mode remains one env away
(KNOW_YOUR_CONTEXT_WATCHTOWER_SERIAL=1) per the every-optimization-switchable
rule.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from know_your_context.ops import watchtower
from know_your_context.ops.watchtower import StoreTarget, aggregate_status


def _targets(count: int) -> list[StoreTarget]:
    return [StoreTarget(name=f"store-{i:02d}", store_path=f"/nonexistent/{i}.sqlite3") for i in range(count)]


class TestParallelProbe:
    def test_results_stay_name_sorted_and_complete(self, tmp_path: Path) -> None:
        targets = _targets(6)
        report = aggregate_status(targets)
        assert [s.name for s in report.stores] == sorted(t.name for t in targets)
        assert all(not s.reachable for s in report.stores)  # missing files: typed, never raised

    def test_fleet_probes_overlap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # mechanism pin: 6 probes x 0.3s must overlap (serial floor = 1.8s)
        original = watchtower.probe_store

        def slow_probe(target: StoreTarget, *, stale_after_seconds: float = 300.0):
            time.sleep(0.3)
            return original(target, stale_after_seconds=stale_after_seconds)

        monkeypatch.setattr(watchtower, "probe_store", slow_probe)
        started = time.monotonic()
        report = aggregate_status(_targets(6))
        elapsed = time.monotonic() - started
        assert len(report.stores) == 6
        assert elapsed < 1.4, f"probes ran serially: {elapsed:.2f}s for 6x0.3s probes"

    def test_serial_switch_restores_sequential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_WATCHTOWER_SERIAL", "1")
        original = watchtower.probe_store

        def slow_probe(target: StoreTarget, *, stale_after_seconds: float = 300.0):
            time.sleep(0.15)
            return original(target, stale_after_seconds=stale_after_seconds)

        monkeypatch.setattr(watchtower, "probe_store", slow_probe)
        started = time.monotonic()
        aggregate_status(_targets(4))
        elapsed = time.monotonic() - started
        assert elapsed >= 0.6, f"serial switch did not serialize: {elapsed:.2f}s for 4x0.15s"

    def test_kill_switch_env_pinned_to_registry(self) -> None:
        from know_your_context.ops import switches

        entry = next(e for e in switches.KILL_SWITCHES if e.env == switches.ENV_WATCHTOWER_SERIAL)
        assert entry.name == "watchtower.serial"
        assert switches.load_switches(env={switches.ENV_WATCHTOWER_SERIAL: "1"}).active()

    def test_duplicate_names_still_rejected_before_any_probe(self) -> None:
        dup = StoreTarget(name="dup", store_path="/nonexistent/a.sqlite3")
        with pytest.raises(watchtower.WatchtowerError):
            aggregate_status([dup, dup])
