"""DST-lite: deterministic simulation tests for the superpower lanes (SUB-118).

The harness (`serve/simulation.py`) injects clock + RNG + queue ordering,
all driven from one seed. The proof obligations from the operator:

* two runs with the SAME seed produce a byte-identical trace (here: the
  sha256 digest, compared in-process AND across PYTHONHASHSEED values in
  subprocesses);
* runs with DIFFERENT seeds explore different scenario coverage;
* a 50-agent storm replays byte-identically from its seed;
* every invariant check holds on every run;
* seeds that exposed bugs are pinned forever as regression tests.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from know_your_context.serve.simulation import (
    SimClock,
    SimConfig,
    SimResult,
    Simulation,
)

REPO_SRC = Path(__file__).resolve().parents[1] / "src"


def storm_config(**overrides) -> SimConfig:
    base = {
        "agents": 50,
        "steps": 4000,
        "write_queue_size": 32,
        "per_actor_write_cap": 8,
        "read_queue_size": 8,
        "per_actor_read_cap": 2,
        "group_commit_batch": 8,
        "agent_rate_per_second": 40.0,
        "agent_burst": 6,
        "session_lease_seconds": 2.0,
        "failure_probability": 0.05,
        "deadline_probability": 0.15,
    }
    base.update(overrides)
    return SimConfig(**base)


# --------------------------------------------------------------------- clock


class TestSimClock:
    def test_starts_at_zero(self) -> None:
        assert SimClock().now() == 0.0

    def test_advance_moves_time_forward(self) -> None:
        clock = SimClock()
        clock.advance(1.5)
        clock.advance(0.25)
        assert clock.now() == 1.75

    def test_zero_advance_is_allowed(self) -> None:
        clock = SimClock()
        clock.advance(0.0)
        assert clock.now() == 0.0

    @pytest.mark.parametrize("dt", [-0.5, -1.0])
    def test_backwards_time_is_a_programmer_error(self, dt: float) -> None:
        clock = SimClock()
        with pytest.raises(AssertionError):
            clock.advance(dt)


class TestSimConfig:
    @pytest.mark.parametrize(
        "field_overrides",
        [
            {"agents": 0},
            {"agents": -3},
            {"steps": -1},
            {"write_queue_size": 0},
            {"per_actor_write_cap": 0},
            {"per_actor_write_cap": 65, "write_queue_size": 64},
            {"read_queue_size": 0},
            {"per_actor_read_cap": 9, "read_queue_size": 8},
            {"group_commit_batch": 0},
            {"session_lease_seconds": 0.0},
            {"agent_rate_per_second": 0.0},
            {"agent_burst": 0},
            {"failure_probability": 1.5},
            {"deadline_probability": -0.1},
            {"spammer_fraction": 1.1},
        ],
    )
    def test_invalid_config_is_a_programmer_error(self, field_overrides: dict) -> None:
        with pytest.raises(AssertionError):
            SimConfig(**field_overrides)

    def test_default_config_is_valid(self) -> None:
        SimConfig()


# --------------------------------------------------------------- determinism


class TestDeterminism:
    @pytest.mark.parametrize("seed", [0, 1, 7, 42, 777, 90210])
    def test_same_seed_byte_identical_trace(self, seed: int) -> None:
        first = Simulation(seed=seed, config=SimConfig(agents=12, steps=600)).run()
        second = Simulation(seed=seed, config=SimConfig(agents=12, steps=600)).run()
        assert first.trace == second.trace
        assert first.trace_digest == second.trace_digest
        assert first.coverage == second.coverage
        assert first.counters == second.counters

    def test_trace_digest_matches_recomputed_hash(self) -> None:
        result = Simulation(seed=42, config=SimConfig(agents=8, steps=300)).run()
        recomputed = hashlib.sha256("\n".join(result.trace).encode("utf-8")).hexdigest()
        assert result.trace_digest == recomputed

    @pytest.mark.parametrize(("seed_a", "seed_b"), [(1, 2), (42, 43), (100, 200)])
    def test_different_seeds_produce_different_traces(self, seed_a: int, seed_b: int) -> None:
        config = SimConfig(agents=12, steps=600)
        first = Simulation(seed=seed_a, config=config).run()
        second = Simulation(seed=seed_b, config=config).run()
        assert first.trace_digest != second.trace_digest

    @pytest.mark.parametrize(("seed_a", "seed_b"), [(7, 8), (13, 17)])
    def test_different_seeds_explore_different_coverage(self, seed_a: int, seed_b: int) -> None:
        config = SimConfig(agents=8, steps=60)
        first = Simulation(seed=seed_a, config=config).run()
        second = Simulation(seed=seed_b, config=config).run()
        assert first.coverage != second.coverage

    def _run_subprocess_digest(self, seed: int, steps: int, hashseed: int) -> str:
        env = {"PYTHONHASHSEED": str(hashseed), "PYTHONPATH": str(REPO_SRC), "PATH": "/usr/bin:/bin"}
        completed = subprocess.run(
            [sys.executable, "-m", "know_your_context.serve.simulation", str(seed), str(steps)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=True,
        )
        match = re.search(r"^digest=([0-9a-f]{64})$", completed.stdout, re.MULTILINE)
        assert match, completed.stdout
        return match.group(1)

    @pytest.mark.parametrize("seed", [7, 42])
    def test_digest_stable_across_pythonhashseed(self, seed: int) -> None:
        assert self._run_subprocess_digest(seed, 400, 0) == self._run_subprocess_digest(seed, 400, 1)

    def test_cli_replays_identically_in_two_processes(self) -> None:
        assert self._run_subprocess_digest(1234, 400, 0) == self._run_subprocess_digest(1234, 400, 0)


# -------------------------------------------------------------------- storms


@pytest.fixture(scope="module")
def storm_result() -> SimResult:
    return Simulation(seed=2026, config=storm_config()).run()


class TestStorm:
    @pytest.mark.parametrize(
        "scenario",
        [
            "write_commit",
            "read_ok",
            "busy_write",
            "busy_read",
            "admission_busy",
            "dedup_shared",
            "idempotent_replay",
            "deadline_drop",
            "session_expired",
            "member_failure",
            "batch_multi",
            "stale_read",
        ],
    )
    def test_storm_covers_scenario(self, storm_result: SimResult, scenario: str) -> None:
        assert scenario in storm_result.coverage

    @pytest.mark.parametrize(
        "check",
        [
            "ack_implies_commit",
            "watermark_monotone",
            "write_cap_respected",
            "read_cap_respected",
            "fairness_ok",
            "no_unresolved_at_halt",
        ],
    )
    def test_storm_invariant_holds(self, storm_result: SimResult, check: str) -> None:
        assert storm_result.checks[check], f"invariant {check} failed; replay: seed={storm_result.seed}"

    def test_storm_replays_byte_identical(self) -> None:
        first = Simulation(seed=2026, config=storm_config()).run()
        second = Simulation(seed=2026, config=storm_config()).run()
        assert first.trace_digest == second.trace_digest

    def test_every_ack_is_a_committed_event(self, storm_result: SimResult) -> None:
        committed = set(storm_result.committed)
        for event_id in storm_result.acked:
            assert event_id in committed

    def test_write_queue_never_exceeded_its_static_budget(self, storm_result: SimResult) -> None:
        assert storm_result.max_write_depth <= storm_config().write_queue_size

    def test_read_queue_never_exceeded_its_static_budget(self, storm_result: SimResult) -> None:
        assert storm_result.max_read_depth <= storm_config().read_queue_size

    def test_polite_agents_commit_despite_spammers(self, storm_result: SimResult) -> None:
        assert storm_result.counters.get("commit_polite", 0) > 0
        assert storm_result.counters.get("commit_spammer", 0) > 0

    def test_spammers_absorb_most_typed_busy(self, storm_result: SimResult) -> None:
        assert storm_result.counters.get("busy_write_spammer", 0) > storm_result.counters.get("busy_write_polite", 0)

    def test_trace_lines_are_canonical(self, storm_result: SimResult) -> None:
        pattern = re.compile(r"^t=\d+\.\d{4} ev=[a-z_]+( \w+=\S+)*$")
        for line in storm_result.trace[:50]:
            assert pattern.match(line), line


# ---------------------------------------------------------------- edges


class TestEdges:
    def test_zero_steps_is_an_empty_run(self) -> None:
        result = Simulation(seed=1, config=SimConfig(steps=0)).run()
        assert result.trace == ()
        assert result.trace_digest == hashlib.sha256(b"").hexdigest()
        assert result.committed == ()
        assert result.acked == ()

    def test_single_agent(self) -> None:
        result = Simulation(seed=5, config=SimConfig(agents=1, steps=400)).run()
        assert result.checks["ack_implies_commit"]
        assert "write_commit" in result.coverage

    def test_large_seed(self) -> None:
        result = Simulation(seed=2**63 - 1, config=SimConfig(agents=4, steps=100)).run()
        assert isinstance(result.trace_digest, str)

    def test_counters_are_json_serializable(self) -> None:
        result = Simulation(seed=9, config=SimConfig(agents=6, steps=200)).run()
        json.dumps(dict(result.counters))


# ------------------------------------------------- pinned regression seeds
#
# Every seed below exposed a defect or a scenario class worth keeping forever
# (see docs/recovery/w10-tiger-dst.md). The digest pins the exact trace; a
# deliberate harness change must update the pin AND say why in the doc.


class TestRegressionSeeds:
    @pytest.mark.parametrize(
        ("seed", "steps", "expected_digest"),
        [
            (7, 800, "6ab13bc6d9402a8c38b5d8080664a62fbb1e4c961335edfd398ed7c0dfa268f9"),
            (42, 800, "8f1502cd78c05c69c197fa28fbf0397c79124e8240804710f4aa7fe3237ce89a"),
            (1337, 800, "597c06d9e1a1ed951eb2e53b8dd7ed241af28397a7cfec83231c7e2047cfd71c"),
        ],
    )
    def test_pinned_seed_replay(self, seed: int, steps: int, expected_digest: str) -> None:
        result = Simulation(seed=seed, config=SimConfig(agents=16, steps=steps)).run()
        assert result.trace_digest == expected_digest
        assert all(result.checks.values())
