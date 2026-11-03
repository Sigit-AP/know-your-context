"""W16 (SUB-137): artifact-write buffering must not re-probe the hardware.

The storm profile (docs/recovery/w16-perf.md) shows the writer thread inside
``probe_hardware`` (GPU probe, PATH scan, imports) on 7.1% of its samples:
``_write_if_changed`` asked for the buffering hint with the *artifact file*
path, and since every rebuild writes into a FRESH generation directory, the
per-path plan cache never hit — one full hardware probe per artifact per
publish. The hint is a property of the disk under the projection root, so
the probe anchor must be the stable output root, not the artifact file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import know_your_context.hardware.profile as hw_profile
from _serve_helpers import seed_store, upsert_node
from know_your_context.domain import Node, NodeKind, PrimaryDomain
from know_your_context.hardware.probe import reset_probe_cache
from know_your_context.projection import GraphProjector
from know_your_context.store import EventStore


def _node(node_id: str, index: int) -> Node:
    return Node(
        node_id=node_id,
        parent_id="P1",
        kind=NodeKind.LEAF,
        domain=PrimaryDomain.P1,
        summary=f"probe anchor node {index}",
        provenance=("context://probe-anchor",),
        source_version_id="sv-test",
    )


class TestArtifactBufferingProbeAnchor:
    def test_rebuilds_do_not_reprobe_hardware(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        graph_root, db = seed_store(tmp_path)
        store = EventStore(db)
        projector = GraphProjector(store, output_root=tmp_path / "graph")
        upsert_node(store, _node("n-anchor-0", 0))
        projector.repair_if_stale()

        calls = {"count": 0}
        real_probe = hw_profile.probe_hardware

        def counting_probe(*args: Any, **kwargs: Any) -> Any:
            calls["count"] += 1
            return real_probe(*args, **kwargs)

        hw_profile.reset_plan_cache()
        reset_probe_cache()
        monkeypatch.setattr(hw_profile, "probe_hardware", counting_probe)

        for index in range(1, 4):
            upsert_node(store, _node(f"n-anchor-{index}", index))
            receipt = projector.repair_if_stale()
            assert receipt.repaired

        # The disk under the projection root is one stable probe target; three
        # rebuilds must reuse it. Before the fix each artifact file in each
        # fresh generation directory triggered its own full probe (~7/rebuild).
        assert calls["count"] <= 2, f"3 rebuilds triggered {calls['count']} hardware probes"
        store.close()
