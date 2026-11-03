"""Hardware integration for analytics (SUB-135).

Batch compute follows the plan's worker count (per-domain centrality suites
and per-community cohesion fan out; merges stay in canonical order so
results are byte-identical), and every batch consults the measured GPU
offload gate (``decide_gpu_offload``) instead of deciding anything locally —
the decision is reported to an optional observer, never acted on without a
kernel (GPU-VERDICT: default NO-GPU).
"""

from __future__ import annotations

from typing import Any

import pytest

from know_your_context.analytics.centrality import compute_centrality
from know_your_context.analytics.communities import detect_communities
from know_your_context.analytics.hardware import analytics_workers
from know_your_context.hardware.gpu_gate import GpuOffloadDecision
from know_your_context.hardware.profile import reset_plan_cache


def _graph() -> dict[str, Any]:
    nodes = []
    links = []
    for domain in ("P1", "P2", "P3"):
        for index in range(6):
            nodes.append({"id": f"{domain}-n{index}", "domain": domain, "kind": "leaf"})
        for index in range(5):
            links.append({"source": f"{domain}-n{index}", "target": f"{domain}-n{index + 1}", "relation": "depends_on"})
    links.append({"source": "P1-n0", "target": "P2-n0", "relation": "depends_on"})
    return {"directed": True, "nodes": nodes, "links": links, "hyperedges": []}


class TestAnalyticsWorkers:
    def test_default_follows_plan(self) -> None:
        assert analytics_workers() >= 1

    def test_kill_switch_gives_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
        assert analytics_workers() == 1
        reset_plan_cache()

    def test_probe_failure_gives_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(path=None, *, refresh=False):
            raise RuntimeError("synthetic probe failure")

        monkeypatch.setattr("know_your_context.hardware.profile.probe_hardware", _boom)
        reset_plan_cache()
        assert analytics_workers() == 1
        reset_plan_cache()

    def test_workers_follow_cores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from know_your_context.hardware.probe import (
            DiskKind,
            DiskProbe,
            GpuKind,
            GpuProbe,
            HardwareProfile,
            MemoryProbe,
        )

        profile = HardwareProfile(
            cpu_count=12,
            memory=MemoryProbe(total_bytes=64 * 1024**3, available_bytes=32 * 1024**3, note="synthetic"),
            disk=DiskProbe(kind=DiskKind.SSD, device="t", rotational=False, note="synthetic"),
            gpu=GpuProbe(kind=GpuKind.NONE, devices=(), notes=()),
            notes=(),
        )
        monkeypatch.setattr(
            "know_your_context.hardware.profile.probe_hardware", lambda path=None, *, refresh=False: profile
        )
        reset_plan_cache()
        assert analytics_workers() == 12
        reset_plan_cache()


class TestCentralityHardware:
    def test_parallel_results_byte_identical(self) -> None:
        graph = _graph()
        sequential = compute_centrality(graph, workers=1)
        parallel = compute_centrality(graph, workers=4)
        planned = compute_centrality(graph)  # plan-derived
        assert sequential == parallel == planned

    def test_offload_gate_consulted_with_measured_inputs(self) -> None:
        decisions: list[GpuOffloadDecision] = []
        result = compute_centrality(_graph(), offload_observer=decisions.append)
        assert len(decisions) == 1
        decision = decisions[0]
        assert decision.edges == 16  # 3x5 intra + 1 cross-domain
        assert decision.t_cpu_s > 0  # measured, never fabricated
        assert decision.offload is False  # no compute GPU on any honest default
        assert result["link_count"] == 16

    def test_no_observer_no_behavior_change(self) -> None:
        assert compute_centrality(_graph()) == compute_centrality(_graph(), workers=1)


class TestCommunitiesHardware:
    def test_parallel_results_byte_identical(self) -> None:
        nodes, links = _graph()["nodes"], _graph()["links"]
        sequential = detect_communities(nodes, links, workers=1)
        parallel = detect_communities(nodes, links, workers=4)
        planned = detect_communities(nodes, links)
        assert sequential == parallel == planned

    def test_offload_gate_consulted(self) -> None:
        decisions: list[GpuOffloadDecision] = []
        nodes, links = _graph()["nodes"], _graph()["links"]
        result = detect_communities(nodes, links, offload_observer=decisions.append)
        assert len(decisions) == 1
        assert decisions[0].edges == 16
        assert decisions[0].t_cpu_s > 0
        assert result["community_count"] >= 1
