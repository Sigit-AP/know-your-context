"""W16 (SUB-137): rebuild-scan memoization pins.

The storm profile (docs/recovery/w16-perf.md) shows the writer thread
spending ~52% of its rebuild CPU re-scanning unchanged node fields for
secret shapes on every publish: ``_secret_category`` is a pure function of
its text, and a rebuild at watermark W re-scans events 1..W-1 unchanged
since the last rebuild. These tests pin the memoization contract:

* a second rebuild after ONE new node scans only that node's fields, never
  the whole graph again (call-count pin — the performance behavior itself);
* cached scans return byte-identical categories (purity pin);
* the cache is statically bounded (Tiger Style budget pin);
* rebuilt artifacts stay byte-identical with a warm cache (determinism pin).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _serve_helpers import seed_store, upsert_node
from know_your_context import projection as projection_module
from know_your_context.domain import Node, NodeKind, PrimaryDomain
from know_your_context.projection import GraphProjector
from know_your_context.store import EventStore


def _node(node_id: str, index: int) -> Node:
    return Node(
        node_id=node_id,
        parent_id="P1",
        kind=NodeKind.LEAF,
        domain=PrimaryDomain.P1,
        summary=f"cache pin node {index} with ordinary summary text",
        provenance=("context://cache-pin",),
        source_version_id="sv-test",
    )


def _counting_scan(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count how many times the RAW secret scan runs (cache misses)."""
    counts = {"calls": 0}
    raw = projection_module._raw_secret_category

    def counting(text: str) -> str | None:
        counts["calls"] += 1
        return raw(text)

    monkeypatch.setattr(projection_module, "_raw_secret_category", counting)
    # The cached wrapper must consult the (patched) raw scanner on misses.
    projection_module._secret_category_scan.cache_clear()
    return counts


class TestRebuildScanMemoization:
    def test_second_rebuild_rescans_only_new_node_fields(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        graph_root, db = seed_store(tmp_path)
        store = EventStore(db)
        projector = GraphProjector(store, output_root=tmp_path / "graph")
        for index in range(12):
            upsert_node(store, _node(f"n-cache-{index:02d}", index))
        counts = _counting_scan(monkeypatch)
        projector.repair_if_stale()  # cold rebuild: scans everything once
        assert counts["calls"] > 40, "the cold rebuild must actually scan the graph"

        counts["calls"] = 0
        upsert_node(store, _node("n-cache-new", 99))
        receipt = projector.repair_if_stale()
        assert receipt.repaired
        # One small node contributes a handful of field scans (per-field plus
        # the cross-field joins); a full rescan of 13+9 seeded nodes would be
        # an order of magnitude more. The bound pins "no full rescan".
        assert counts["calls"] <= 40, f"second rebuild re-scanned {counts['calls']} texts (full rescan)"
        store.close()

    def test_cached_scan_is_byte_identical(self) -> None:
        texts = [
            "ordinary text",
            "AKIAIOSFODNN7EXAMPLE",  # pinned AWS key shape
            "nested σ homoglyph",
            "",
            "x" * 4096,
        ]
        for text in texts:
            projection_module._secret_category_scan.cache_clear()
            first = projection_module._secret_category_scan(text)
            second = projection_module._secret_category_scan(text)
            assert first == second
            info = projection_module._secret_category_scan.cache_info()
            assert info.hits >= 1, "the second scan of the same text must be a cache hit"

    def test_cache_is_statically_bounded(self) -> None:
        projection_module._secret_category_scan.cache_clear()
        for index in range(20_000):
            projection_module._secret_category_scan(f"bounded-cache-probe-{index}")
        info = projection_module._secret_category_scan.cache_info()
        assert info.maxsize is not None
        assert info.currsize <= info.maxsize
        assert info.maxsize <= 16_384, "scan cache must stay within its static budget"

    def test_warm_cache_rebuild_is_byte_identical(self, tmp_path: Path) -> None:
        graph_root, db = seed_store(tmp_path)
        store = EventStore(db)
        projector = GraphProjector(store, output_root=tmp_path / "graph")
        for index in range(8):
            upsert_node(store, _node(f"n-det-{index:02d}", index))
        cold = projector.repair_if_stale()
        cold_bytes = (Path(cold.generation_dir) / "graph.json").read_bytes()
        projection_module._secret_category_scan.cache_clear()
        warm = projector.repair_if_stale()  # no-op: watermark unchanged
        assert not warm.repaired
        assert (Path(cold.generation_dir) / "graph.json").read_bytes() == cold_bytes
        upsert_node(store, _node("n-det-new", 100))
        rebuilt = projector.repair_if_stale()
        first_bytes = (Path(rebuilt.generation_dir) / "graph.json").read_bytes()
        # A second process-fresh projector over the same store must produce
        # the identical generation bytes (cache warmth never leaks into output).
        projection_module._secret_category_scan.cache_clear()
        projector2 = GraphProjector(store, output_root=tmp_path / "graph2")
        rebuilt2 = projector2.repair_if_stale()
        second_bytes = (Path(rebuilt2.generation_dir) / "graph.json").read_bytes()
        assert first_bytes == second_bytes
        assert rebuilt.content_hash == rebuilt2.content_hash
        store.close()
