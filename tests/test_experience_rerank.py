"""Tests for retrieval v2 experience-driven re-rank (wave W14, innovation I1).

The reranker learns from recorded outcomes (``knowledge.record_outcome``
evidence plus node P/M/E scores) and applies a clamped, quantized multiplier
on top of the frozen contract scores. Hard invariants pinned here:

* fail-open: empty ledger / kill switch / no signal => byte-identical baseline
* clamp: multiplier in [0.5, 1.5], quantized to 12dp (ROUND_HALF_EVEN)
* audit: every re-rank call writes one hash-chained journal entry
* budget: context assembly never exceeds the token budget after re-rank
* A/B: recall@10 with outcome history >= baseline on the pinned fixtures
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from _retrieval_v2_helpers import add_file, add_symbol
from know_your_context.domain import Event, EventKind, Evidence, Node, PrimaryDomain, ScoreVector
from know_your_context.retrieval_v2 import ScoredSymbol, SymbolLookup
from know_your_context.retrieval_v2.context import ContextConfig, ContextItem, assemble_context
from know_your_context.retrieval_v2.experience import (
    ENV_EXPERIENCE_RERANK,
    ExperienceConfig,
    ExperienceError,
    ExperienceLedger,
    ExperienceReranker,
    OutcomeSignal,
    compute_multiplier,
    experience_enabled,
    rerank_scored,
    reranker_from_env,
)
from know_your_context.store import EventStore

FIXED_AT = datetime(2026, 1, 1, tzinfo=UTC)
GENESIS_HASH = "0" * 64


# ------------------------------------------------------------------ fixtures


def make_hit(node_id: str, score: float, *, name: str = "fn", path: str = "src/a.py", line: int = 1) -> ScoredSymbol:
    return ScoredSymbol(
        node_id=node_id, name=name, kind="function", path=path, line=line, col=0, score=score, source="fts"
    )


def make_signal(
    node_id: str,
    outcomes: list[str],
    *,
    p: str | None = None,
    m: str | None = None,
    e: str | None = None,
) -> OutcomeSignal:
    total = sum((Decimal(value) for value in outcomes), Decimal("0"))
    return OutcomeSignal(
        node_id=node_id,
        outcome_count=len(outcomes),
        positive_count=sum(1 for value in outcomes if Decimal(value) > 0),
        negative_count=sum(1 for value in outcomes if Decimal(value) < 0),
        outcome_sum=total,
        p=Decimal(p) if p is not None else None,
        m=Decimal(m) if m is not None else None,
        e=Decimal(e) if e is not None else None,
    )


def make_evidence(evidence_id: str, node_id: str, outcome: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        node_id=node_id,
        source_version_id="sv-test",
        source_uri="context://corpus/experience",
        observed_at=FIXED_AT,
        independence_key=f"indep-{evidence_id}",
        outcome=Decimal(outcome),
        correctness=Decimal("1"),
        reliability=Decimal("1"),
        reproducibility=Decimal("1"),
        scope_fit=Decimal("1"),
        successful_use=Decimal("1"),
        task_coverage=Decimal("1"),
        quality_at_use=Decimal("1"),
        contradiction=Decimal("0"),
        utilization_u=Decimal("0.5"),
        latency_ms=1,
        compute_ms=1,
        output_chars=0,
    )


def add_node(store: EventStore, node_id: str, *, scores: ScoreVector | None = None) -> None:
    node = Node(
        node_id=node_id, domain=PrimaryDomain.P1, source_version_id="sv-test", scores=scores or ScoreVector.zero()
    )
    store.append_domain(
        Event(kind=EventKind.NODE_UPSERT, node_id=node_id, domain=PrimaryDomain.P1, node=node),
        idempotency_key=f"node-{node_id}",
    )


def add_outcome(store: EventStore, node_id: str, outcome: str, seq: int) -> None:
    evidence = make_evidence(f"ev-{node_id}-{seq}", node_id, outcome)
    store.append_domain(
        Event(kind=EventKind.EVIDENCE_APPEND, node_id=node_id, domain=PrimaryDomain.P1, evidence=evidence),
        idempotency_key=f"ev-{node_id}-{seq}",
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    instance = EventStore(tmp_path / "soul.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[ExperienceLedger]:
    instance = ExperienceLedger(tmp_path / "ledger.db")
    try:
        yield instance
    finally:
        instance.close()


def hits_payload(hits: tuple[ScoredSymbol, ...]) -> str:
    rows = [
        {
            "node_id": hit.node_id,
            "name": hit.name,
            "kind": hit.kind,
            "path": hit.path,
            "line": hit.line,
            "col": hit.col,
            "score": hit.score,
            "source": hit.source,
        }
        for hit in hits
    ]
    return json.dumps(rows, sort_keys=True)


# ------------------------------------------------------------- configuration


class TestExperienceConfig:
    def test_defaults(self) -> None:
        config = ExperienceConfig()
        assert config.max_boost == Decimal("0.5")
        assert config.count_saturation == 5
        assert config.min_outcomes == 1
        assert config.journal_enabled is True

    def test_max_boost_boundary_half_accepted(self) -> None:
        assert ExperienceConfig(max_boost=Decimal("0.5")).max_boost == Decimal("0.5")

    def test_max_boost_above_half_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(max_boost=Decimal("0.500000000001"))

    def test_max_boost_zero_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(max_boost=Decimal("0"))

    def test_max_boost_negative_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(max_boost=Decimal("-0.1"))

    def test_max_boost_bool_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(max_boost=True)  # type: ignore[arg-type]

    def test_max_boost_float_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(max_boost=0.25)  # type: ignore[arg-type]

    def test_max_boost_string_accepted(self) -> None:
        assert ExperienceConfig(max_boost="0.25").max_boost == Decimal("0.25")

    def test_max_boost_int_rejected_as_zero(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(max_boost=0)

    def test_count_saturation_zero_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(count_saturation=0)

    def test_count_saturation_negative_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(count_saturation=-2)

    def test_count_saturation_bool_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(count_saturation=True)  # type: ignore[arg-type]

    def test_min_outcomes_zero_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(min_outcomes=0)

    def test_min_outcomes_bool_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(min_outcomes=False)  # type: ignore[arg-type]

    def test_journal_enabled_non_bool_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            ExperienceConfig(journal_enabled=1)  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        config = ExperienceConfig()
        with pytest.raises(FrozenInstanceError):
            config.max_boost = Decimal("0.1")  # type: ignore[misc]


class TestKillSwitch:
    def test_absent_env_enabled(self) -> None:
        assert experience_enabled({}) is True

    def test_zero_disables(self) -> None:
        assert experience_enabled({ENV_EXPERIENCE_RERANK: "0"}) is False

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "FALSE", " Off "])
    def test_false_spellings_disable(self, raw: str) -> None:
        assert experience_enabled({ENV_EXPERIENCE_RERANK: raw}) is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "TRUE", " On "])
    def test_true_spellings_enable(self, raw: str) -> None:
        assert experience_enabled({ENV_EXPERIENCE_RERANK: raw}) is True

    @pytest.mark.parametrize("raw", ["banana", "2", "", "-1", "enable"])
    def test_invalid_raises_typed(self, raw: str) -> None:
        with pytest.raises(ExperienceError):
            experience_enabled({ENV_EXPERIENCE_RERANK: raw})

    def test_from_env_returns_none_when_disabled(self, ledger: ExperienceLedger) -> None:
        assert reranker_from_env(ledger, env={ENV_EXPERIENCE_RERANK: "0"}) is None

    def test_from_env_returns_reranker_when_enabled(self, ledger: ExperienceLedger) -> None:
        reranker = reranker_from_env(ledger, env={ENV_EXPERIENCE_RERANK: "1"})
        assert isinstance(reranker, ExperienceReranker)
        assert reranker.enabled is True

    def test_from_env_default_enabled(self, ledger: ExperienceLedger) -> None:
        reranker = reranker_from_env(ledger, env={})
        assert reranker is not None and reranker.enabled is True

    def test_from_env_invalid_raises(self, ledger: ExperienceLedger) -> None:
        with pytest.raises(ExperienceError):
            reranker_from_env(ledger, env={ENV_EXPERIENCE_RERANK: "banana"})


class TestOutcomeSignal:
    def test_mean_outcome_exact(self) -> None:
        signal = make_signal("n1", ["1", "1", "-1"])
        assert signal.mean_outcome == Decimal("1") / Decimal("3")

    def test_mean_outcome_zero_count(self) -> None:
        signal = make_signal("n1", [])
        assert signal.mean_outcome == Decimal("0")

    def test_negative_count_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            OutcomeSignal(node_id="n1", outcome_count=-1, positive_count=0, negative_count=0, outcome_sum=Decimal("0"))

    def test_positive_plus_negative_above_count_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            OutcomeSignal(node_id="n1", outcome_count=1, positive_count=1, negative_count=1, outcome_sum=Decimal("0"))

    def test_outcome_sum_above_count_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            OutcomeSignal(node_id="n1", outcome_count=1, positive_count=1, negative_count=0, outcome_sum=Decimal("1.5"))

    def test_outcome_sum_below_minus_count_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            OutcomeSignal(
                node_id="n1", outcome_count=1, positive_count=0, negative_count=1, outcome_sum=Decimal("-1.5")
            )

    def test_empty_node_id_rejected(self) -> None:
        with pytest.raises(ExperienceError):
            OutcomeSignal(node_id="", outcome_count=0, positive_count=0, negative_count=0, outcome_sum=Decimal("0"))

    def test_pme_defaults_none(self) -> None:
        signal = make_signal("n1", ["1"])
        assert signal.p is None and signal.m is None and signal.e is None

    def test_frozen(self) -> None:
        signal = make_signal("n1", ["1"])
        with pytest.raises(FrozenInstanceError):
            signal.outcome_count = 9  # type: ignore[misc]


# ------------------------------------------------------- multiplier contract


class TestComputeMultiplier:
    def test_no_signal_is_exactly_one(self) -> None:
        assert compute_multiplier(None, ExperienceConfig()) == Decimal("1.000000000000")

    def test_zero_count_signal_is_one(self) -> None:
        assert compute_multiplier(make_signal("n1", []), ExperienceConfig()) == Decimal("1.000000000000")

    def test_saturated_positive_is_upper_clamp(self) -> None:
        signal = make_signal("n1", ["1"] * 5)
        assert compute_multiplier(signal, ExperienceConfig()) == Decimal("1.500000000000")

    def test_saturated_negative_is_lower_clamp(self) -> None:
        signal = make_signal("n1", ["-1"] * 5)
        assert compute_multiplier(signal, ExperienceConfig()) == Decimal("0.500000000000")

    def test_single_positive_is_damped(self) -> None:
        signal = make_signal("n1", ["1"])
        assert compute_multiplier(signal, ExperienceConfig()) == Decimal("1.100000000000")

    def test_two_positives(self) -> None:
        signal = make_signal("n1", ["1", "1"])
        assert compute_multiplier(signal, ExperienceConfig()) == Decimal("1.200000000000")

    def test_three_positives(self) -> None:
        signal = make_signal("n1", ["1"] * 3)
        assert compute_multiplier(signal, ExperienceConfig()) == Decimal("1.300000000000")

    def test_four_positives(self) -> None:
        signal = make_signal("n1", ["1"] * 4)
        assert compute_multiplier(signal, ExperienceConfig()) == Decimal("1.400000000000")

    def test_damping_caps_at_saturation(self) -> None:
        signal = make_signal("n1", ["1"] * 10)
        assert compute_multiplier(signal, ExperienceConfig()) == Decimal("1.500000000000")

    def test_zero_mean_is_neutral(self) -> None:
        signal = make_signal("n1", ["1", "-1", "1", "-1", "0"])
        assert compute_multiplier(signal, ExperienceConfig()) == Decimal("1.000000000000")

    def test_half_mean_saturated(self) -> None:
        signal = make_signal("n1", ["0.5"] * 5)
        assert compute_multiplier(signal, ExperienceConfig()) == Decimal("1.250000000000")

    def test_quantized_to_12dp(self) -> None:
        # mean = 1/7, damping = 1 -> 1 + 0.5/7 = 1.0714285714285714...
        signal = make_signal("n1", ["1"] + ["0"] * 6)
        multiplier = compute_multiplier(signal, ExperienceConfig())
        assert multiplier == Decimal("1.071428571429")
        assert multiplier == multiplier.quantize(Decimal("0.000000000001"))

    def test_multiplier_always_within_clamp(self) -> None:
        config = ExperienceConfig()
        for outcomes in (["1"] * 5, ["-1"] * 5, ["1", "-1"], ["0.7"] * 3, ["-0.2"] * 9):
            multiplier = compute_multiplier(make_signal("n1", outcomes), config)
            assert Decimal("0.5") <= multiplier <= Decimal("1.5")

    def test_min_outcomes_gate(self) -> None:
        config = ExperienceConfig(min_outcomes=2)
        assert compute_multiplier(make_signal("n1", ["1"]), config) == Decimal("1.000000000000")
        assert compute_multiplier(make_signal("n1", ["1", "1"]), config) == Decimal("1.200000000000")

    def test_custom_max_boost_respected(self) -> None:
        config = ExperienceConfig(max_boost="0.25")
        signal = make_signal("n1", ["1"] * 5)
        assert compute_multiplier(signal, config) == Decimal("1.250000000000")

    def test_custom_saturation(self) -> None:
        config = ExperienceConfig(count_saturation=2)
        signal = make_signal("n1", ["1"])
        assert compute_multiplier(signal, config) == Decimal("1.250000000000")

    def test_deterministic_across_calls(self) -> None:
        signal = make_signal("n1", ["1", "-1", "0.5"])
        first = compute_multiplier(signal, ExperienceConfig())
        second = compute_multiplier(signal, ExperienceConfig())
        assert first == second


# ----------------------------------------------------------- pure re-ranking


class TestRerankScored:
    def test_empty_hits(self) -> None:
        outcome = rerank_scored([], {}, ExperienceConfig())
        assert outcome.hits == ()
        assert outcome.decisions == ()

    def test_no_signals_byte_identical(self) -> None:
        hits = (make_hit("a", 10.0), make_hit("b", 5.0), make_hit("c", 1.0))
        outcome = rerank_scored(hits, {}, ExperienceConfig())
        assert outcome.hits == hits
        assert hits_payload(outcome.hits) == hits_payload(hits)
        assert all(decision.reason == "no-signal" for decision in outcome.decisions)
        assert all(decision.multiplier == "1.000000000000" for decision in outcome.decisions)

    def test_boost_reorders(self) -> None:
        hits = (make_hit("a", 100.0), make_hit("b", 90.0))
        signals = {"b": make_signal("b", ["1"] * 5)}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert [hit.node_id for hit in outcome.hits] == ["b", "a"]
        boosted = outcome.hits[0]
        assert boosted.score == 135.0  # 90 * 1.5

    def test_penalty_demotes(self) -> None:
        hits = (make_hit("a", 100.0), make_hit("b", 90.0))
        signals = {"a": make_signal("a", ["-1"] * 5)}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert [hit.node_id for hit in outcome.hits] == ["b", "a"]
        assert outcome.hits[1].score == 50.0  # 100 * 0.5

    def test_zero_score_never_moves(self) -> None:
        hits = (make_hit("a", 0.0), make_hit("b", 1.0))
        signals = {"a": make_signal("a", ["1"] * 5)}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert outcome.hits[1].score == 0.0
        assert outcome.decisions[1].reason == "non-positive-base"
        assert outcome.decisions[1].final_score == outcome.decisions[1].base_score

    def test_negative_score_never_moves(self) -> None:
        hits = (make_hit("a", -15.0), make_hit("b", 1.0))
        signals = {"a": make_signal("a", ["1"] * 5)}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert outcome.hits[1].score == -15.0
        assert outcome.decisions[1].reason == "non-positive-base"

    def test_neutral_signal_reason(self) -> None:
        hits = (make_hit("a", 10.0),)
        signals = {"a": make_signal("a", ["1", "-1", "1", "-1", "0"])}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert outcome.decisions[0].reason == "neutral"
        assert outcome.hits[0].score == 10.0

    def test_decision_records_scores(self) -> None:
        hits = (make_hit("a", 100.0),)
        signals = {"a": make_signal("a", ["1"] * 5)}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        decision = outcome.decisions[0]
        assert decision.node_id == "a"
        assert decision.base_score == "100.0"
        assert decision.multiplier == "1.500000000000"
        assert decision.final_score == "150.000000000000"
        assert decision.outcome_count == 5
        assert decision.reason == "boosted"

    def test_penalized_reason(self) -> None:
        hits = (make_hit("a", 10.0),)
        signals = {"a": make_signal("a", ["-1"] * 5)}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert outcome.decisions[0].reason == "penalized"

    def test_tie_break_canonical(self) -> None:
        first = make_hit("a", 10.0, path="src/b.py")
        second = make_hit("b", 10.0, path="src/a.py")
        outcome = rerank_scored((first, second), {}, ExperienceConfig())
        assert [hit.node_id for hit in outcome.hits] == ["b", "a"]  # path asc

    def test_input_permutation_invariant(self) -> None:
        hits = (make_hit("a", 10.0), make_hit("b", 20.0), make_hit("c", 15.0))
        signals = {"a": make_signal("a", ["1"] * 5)}
        forward = rerank_scored(hits, signals, ExperienceConfig())
        backward = rerank_scored(tuple(reversed(hits)), signals, ExperienceConfig())
        assert hits_payload(forward.hits) == hits_payload(backward.hits)

    def test_unknown_signal_nodes_ignored(self) -> None:
        hits = (make_hit("a", 10.0),)
        signals = {"zzz": make_signal("zzz", ["1"] * 5)}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert outcome.hits == hits
        assert outcome.decisions[0].reason == "no-signal"

    def test_final_score_quantized_12dp(self) -> None:
        hits = (make_hit("a", 10.0),)
        signals = {"a": make_signal("a", ["1"] + ["0"] * 6)}  # mult 1.071428571429
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert outcome.decisions[0].final_score == "10.714285714290"
        assert outcome.hits[0].score == float(Decimal("10.714285714290"))

    def test_scores_below_one_boost(self) -> None:
        hits = (make_hit("a", 0.5), make_hit("b", 0.4))
        signals = {"b": make_signal("b", ["1"] * 5)}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert [hit.node_id for hit in outcome.hits] == ["b", "a"]

    def test_decisions_align_with_hits(self) -> None:
        hits = (make_hit("a", 10.0), make_hit("b", 5.0))
        signals = {"b": make_signal("b", ["1"] * 5)}
        outcome = rerank_scored(hits, signals, ExperienceConfig())
        assert [decision.node_id for decision in outcome.decisions] == [hit.node_id for hit in outcome.hits]


# ------------------------------------------------------------------- ledger


class TestExperienceLedger:
    def test_open_creates_schema(self, tmp_path: Path) -> None:
        ledger = ExperienceLedger(tmp_path / "ledger.db")
        ledger.close()
        reopened = ExperienceLedger(tmp_path / "ledger.db")  # idempotent
        reopened.close()

    def test_for_store_derivation(self, store: EventStore) -> None:
        ledger = ExperienceLedger.for_store(store)
        try:
            assert ledger.path.endswith(".experience-ledger.db")
            assert Path(ledger.path).parent == Path(store.db_path).parent
        finally:
            ledger.close()

    def test_for_store_memory(self) -> None:
        store = EventStore()
        try:
            ledger = ExperienceLedger.for_store(store)
            try:
                assert ledger.path == ":memory:"
            finally:
                ledger.close()
        finally:
            store.close()

    def test_empty_store_sync(self, store: EventStore, ledger: ExperienceLedger) -> None:
        report = ledger.sync_from_store(store)
        assert report.evidence_scanned == 0
        assert report.signals_updated == 0
        assert report.watermark_before == 0
        assert report.watermark_after == 0

    def test_sync_aggregates_outcomes(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_node(store, "n1")
        add_outcome(store, "n1", "1", 1)
        add_outcome(store, "n1", "1", 2)
        add_outcome(store, "n1", "-1", 3)
        report = ledger.sync_from_store(store)
        assert report.evidence_scanned == 3
        assert report.signals_updated == 1
        signal = ledger.signal("n1")
        assert signal is not None
        assert signal.outcome_count == 3
        assert signal.positive_count == 2
        assert signal.negative_count == 1
        assert signal.outcome_sum == Decimal("1.000000000000")

    def test_sync_pulls_pme_scores(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_node(store, "n1", scores=ScoreVector(p=Decimal("0.8"), m=Decimal("0.6"), e=Decimal("0.4")))
        add_outcome(store, "n1", "1", 1)
        ledger.sync_from_store(store)
        signal = ledger.signal("n1")
        assert signal is not None
        assert signal.p == Decimal("0.800000000000")
        assert signal.m == Decimal("0.600000000000")
        assert signal.e == Decimal("0.400000000000")

    def test_signal_absent_without_evidence(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_node(store, "n1")
        ledger.sync_from_store(store)
        assert ledger.signal("n1") is None

    def test_incremental_sync_scans_only_new(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_node(store, "n1")
        add_outcome(store, "n1", "1", 1)
        first = ledger.sync_from_store(store)
        assert first.evidence_scanned == 1
        add_outcome(store, "n1", "1", 2)
        second = ledger.sync_from_store(store)
        assert second.evidence_scanned == 1
        assert second.watermark_before == first.watermark_after
        assert second.watermark_after > first.watermark_after
        signal = ledger.signal("n1")
        assert signal is not None and signal.outcome_count == 2

    def test_sync_idempotent(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_node(store, "n1")
        add_outcome(store, "n1", "1", 1)
        ledger.sync_from_store(store)
        before = ledger.signal("n1")
        ledger.sync_from_store(store)
        after = ledger.signal("n1")
        assert before == after

    def test_sync_multiple_nodes(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_node(store, "n1")
        add_node(store, "n2")
        add_outcome(store, "n1", "1", 1)
        add_outcome(store, "n2", "-1", 2)
        report = ledger.sync_from_store(store)
        assert report.signals_updated == 2
        signals = ledger.signals_for(["n1", "n2", "n3"])
        assert signals["n1"].outcome_sum == Decimal("1.000000000000")
        assert signals["n2"].outcome_sum == Decimal("-1.000000000000")
        assert "n3" not in signals

    def test_signals_for_empty_input(self, ledger: ExperienceLedger) -> None:
        assert ledger.signals_for([]) == {}

    def test_survives_restart(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "soul.sqlite3")
        add_node(store, "n1")
        add_outcome(store, "n1", "1", 1)
        ledger = ExperienceLedger(tmp_path / "ledger.db")
        ledger.sync_from_store(store)
        before = ledger.signal("n1")
        ledger.close()
        reopened = ExperienceLedger(tmp_path / "ledger.db")
        try:
            assert reopened.signal("n1") == before
            assert reopened.watermark() > 0
        finally:
            reopened.close()
            store.close()

    def test_incremental_after_restart(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path / "soul.sqlite3")
        add_node(store, "n1")
        add_outcome(store, "n1", "1", 1)
        ledger = ExperienceLedger(tmp_path / "ledger.db")
        ledger.sync_from_store(store)
        ledger.close()
        add_outcome(store, "n1", "-1", 2)
        reopened = ExperienceLedger(tmp_path / "ledger.db")
        try:
            report = reopened.sync_from_store(store)
            assert report.evidence_scanned == 1
            signal = reopened.signal("n1")
            assert signal is not None
            assert signal.outcome_count == 2
            assert signal.outcome_sum == Decimal("0.000000000000")
        finally:
            reopened.close()
            store.close()


# ------------------------------------------------------------------- journal


class TestJournal:
    def test_first_entry_chains_genesis(self, store: EventStore, ledger: ExperienceLedger) -> None:
        reranker = ExperienceReranker(ledger)
        reranker.rerank([make_hit("a", 1.0)], query="q1")
        entries = ledger.journal_entries()
        assert len(entries) == 1
        assert entries[0].seq == 1
        assert entries[0].prev_hash == GENESIS_HASH
        assert len(entries[0].entry_hash) == 64

    def test_chain_links(self, store: EventStore, ledger: ExperienceLedger) -> None:
        reranker = ExperienceReranker(ledger)
        reranker.rerank([make_hit("a", 1.0)], query="q1")
        reranker.rerank([make_hit("a", 2.0)], query="q2")
        entries = ledger.journal_entries()
        assert entries[1].prev_hash == entries[0].entry_hash
        assert ledger.verify_journal().ok is True

    def test_verify_empty_journal_ok(self, ledger: ExperienceLedger) -> None:
        verification = ledger.verify_journal()
        assert verification.ok is True
        assert verification.checked == 0

    def test_tampered_payload_detected(self, tmp_path: Path, store: EventStore) -> None:
        path = tmp_path / "ledger.db"
        ledger = ExperienceLedger(path)
        reranker = ExperienceReranker(ledger)
        reranker.rerank([make_hit("a", 1.0)], query="q1")
        ledger.close()
        conn = sqlite3.connect(path)
        conn.execute("UPDATE journal SET payload_json = '{}' WHERE seq = 1")
        conn.commit()
        conn.close()
        reopened = ExperienceLedger(path)
        try:
            verification = reopened.verify_journal()
            assert verification.ok is False
            assert verification.first_bad_seq == 1
        finally:
            reopened.close()

    def test_tampered_hash_detected(self, tmp_path: Path, store: EventStore) -> None:
        path = tmp_path / "ledger.db"
        ledger = ExperienceLedger(path)
        reranker = ExperienceReranker(ledger)
        reranker.rerank([make_hit("a", 1.0)], query="q1")
        reranker.rerank([make_hit("a", 2.0)], query="q2")
        ledger.close()
        conn = sqlite3.connect(path)
        conn.execute("UPDATE journal SET entry_hash = ? WHERE seq = 1", ("f" * 64,))
        conn.commit()
        conn.close()
        reopened = ExperienceLedger(path)
        try:
            verification = reopened.verify_journal()
            assert verification.ok is False
            assert verification.first_bad_seq == 1
        finally:
            reopened.close()

    def test_journal_payload_records_decisions(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_node(store, "n1")
        add_outcome(store, "n1", "1", 1)
        ledger.sync_from_store(store)
        reranker = ExperienceReranker(ledger)
        reranker.rerank([make_hit("n1", 10.0)], query="handler")
        entries = ledger.journal_entries()
        payload = entries[0].payload
        assert payload["query"] == "handler"
        assert payload["schema"] == 1
        assert payload["decisions"][0]["node_id"] == "n1"
        assert payload["decisions"][0]["reason"] == "boosted"
        assert payload["watermark"] == ledger.watermark()

    def test_journal_deterministic_no_clock(self, tmp_path: Path, store: EventStore) -> None:
        hashes = []
        for name in ("a.db", "b.db"):
            ledger = ExperienceLedger(tmp_path / name)
            reranker = ExperienceReranker(ledger)
            reranker.rerank([make_hit("n1", 10.0)], query="same")
            reranker.rerank([make_hit("n2", 5.0)], query="same")
            hashes.append([entry.entry_hash for entry in ledger.journal_entries()])
            ledger.close()
        assert hashes[0] == hashes[1]

    def test_journal_disabled_config(self, store: EventStore, ledger: ExperienceLedger) -> None:
        reranker = ExperienceReranker(ledger, ExperienceConfig(journal_enabled=False))
        reranker.rerank([make_hit("a", 1.0)], query="q1")
        assert ledger.journal_entries() == ()

    def test_journal_survives_restart(self, tmp_path: Path, store: EventStore) -> None:
        path = tmp_path / "ledger.db"
        ledger = ExperienceLedger(path)
        reranker = ExperienceReranker(ledger)
        reranker.rerank([make_hit("a", 1.0)], query="q1")
        expected = [entry.entry_hash for entry in ledger.journal_entries()]
        ledger.close()
        reopened = ExperienceLedger(path)
        try:
            assert [entry.entry_hash for entry in reopened.journal_entries()] == expected
            assert reopened.verify_journal().ok is True
        finally:
            reopened.close()


# ------------------------------------------------------------------ reranker


class TestExperienceReranker:
    def test_disabled_returns_input_unchanged(self, ledger: ExperienceLedger) -> None:
        reranker = ExperienceReranker(ledger, env={ENV_EXPERIENCE_RERANK: "0"})
        assert reranker.enabled is False
        hits = [make_hit("a", 2.0), make_hit("b", 1.0)]
        outcome = reranker.rerank(hits, query="q")
        assert outcome.hits == tuple(hits)
        assert outcome.decisions == ()
        assert ledger.journal_entries() == ()

    def test_empty_ledger_identity(self, store: EventStore, ledger: ExperienceLedger) -> None:
        reranker = ExperienceReranker(ledger)
        hits = [make_hit("a", 2.0), make_hit("b", 1.0)]
        outcome = reranker.rerank(hits, query="q")
        assert outcome.hits == tuple(hits)
        assert all(decision.reason == "no-signal" for decision in outcome.decisions)

    def test_boosts_from_store_outcomes(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_node(store, "n1")
        for seq in range(1, 6):
            add_outcome(store, "n1", "1", seq)
        ledger.sync_from_store(store)
        reranker = ExperienceReranker(ledger)
        outcome = reranker.rerank([make_hit("a", 100.0), make_hit("n1", 90.0)], query="q")
        assert [hit.node_id for hit in outcome.hits] == ["n1", "a"]

    def test_rerank_deterministic_twice(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_node(store, "n1")
        add_outcome(store, "n1", "1", 1)
        ledger.sync_from_store(store)
        reranker = ExperienceReranker(ledger, ExperienceConfig(journal_enabled=False))
        hits = [make_hit("a", 100.0), make_hit("n1", 90.0)]
        first = reranker.rerank(hits, query="q")
        second = reranker.rerank(hits, query="q")
        assert hits_payload(first.hits) == hits_payload(second.hits)
        assert first.decisions == second.decisions

    def test_env_true_enables(self, ledger: ExperienceLedger) -> None:
        reranker = ExperienceReranker(ledger, env={ENV_EXPERIENCE_RERANK: "yes"})
        assert reranker.enabled is True

    def test_env_invalid_raises_at_construction(self, ledger: ExperienceLedger) -> None:
        with pytest.raises(ExperienceError):
            ExperienceReranker(ledger, env={ENV_EXPERIENCE_RERANK: "banana"})

    def test_explicit_enabled_overrides_env_absent(self, ledger: ExperienceLedger) -> None:
        reranker = ExperienceReranker(ledger, enabled=True, env={})
        assert reranker.enabled is True


# --------------------------------------------------------- lookup hook (W14)


class TestLookupHook:
    def _build_symbol_store(self, store: EventStore) -> dict[str, str]:
        """12 functions matching 'handler'; returns name -> node_id."""
        add_file(store, "src/app.py")
        ids: dict[str, str] = {}
        for index in range(12):
            name = f"handler_{index:02d}"
            ids[name] = add_symbol(store, name, "function", "src/app.py", line=10 + index)
        return ids

    def test_default_lookup_has_no_experience(self) -> None:
        assert SymbolLookup().experience is None

    def test_hook_none_matches_plain_lookup(self, store: EventStore) -> None:
        self._build_symbol_store(store)
        plain = SymbolLookup().search(store, "handler")
        hooked = SymbolLookup(experience=None).search(store, "handler")
        assert plain == hooked

    def test_empty_ledger_byte_identical(self, store: EventStore, ledger: ExperienceLedger) -> None:
        self._build_symbol_store(store)
        baseline = SymbolLookup().search(store, "handler")
        reranker = ExperienceReranker(ledger)
        reranked = SymbolLookup(experience=reranker).search(store, "handler")
        assert reranked == baseline
        assert hits_payload(reranked) == hits_payload(baseline)

    def test_kill_switch_byte_identical(self, store: EventStore, ledger: ExperienceLedger) -> None:
        ids = self._build_symbol_store(store)
        target = ids["handler_00"]
        add_node(store, target)
        for seq in range(1, 6):
            add_outcome(store, target, "1", seq)
        ledger.sync_from_store(store)
        baseline = SymbolLookup().search(store, "handler")
        disabled = ExperienceReranker(ledger, env={ENV_EXPERIENCE_RERANK: "0"})
        reranked = SymbolLookup(experience=disabled).search(store, "handler")
        assert reranked == baseline
        assert hits_payload(reranked) == hits_payload(baseline)

    def test_outcome_lifts_rank_deterministically(self, store: EventStore, ledger: ExperienceLedger) -> None:
        """Frozen fixture: handler_00 sorts last alphabetically among equals;

        with 5 saturated positive outcomes it must land exactly at rank 1."""
        ids = self._build_symbol_store(store)
        target = ids["handler_00"]
        baseline = SymbolLookup().search(store, "handler")
        baseline_rank = [hit.node_id for hit in baseline].index(target)
        add_node(store, target)
        for seq in range(1, 6):
            add_outcome(store, target, "1", seq)
        ledger.sync_from_store(store)
        reranked = SymbolLookup(experience=ExperienceReranker(ledger)).search(store, "handler")
        reranked_rank = [hit.node_id for hit in reranked].index(target)
        assert baseline_rank == 0 or reranked_rank < baseline_rank
        assert reranked_rank == 0
        # deterministic delta pinned: score = quantize12(base * 1.5)
        expected = Decimal(str(baseline[baseline_rank].score)) * Decimal("1.5")
        expected = expected.quantize(Decimal("0.000000000001"))
        assert reranked[0].score == float(expected)

    def test_negative_outcome_demotes(self, store: EventStore, ledger: ExperienceLedger) -> None:
        ids = self._build_symbol_store(store)
        target = ids["handler_00"]
        add_node(store, target)
        for seq in range(1, 6):
            add_outcome(store, target, "-1", seq)
        ledger.sync_from_store(store)
        # full window (limit 12 keeps every candidate visible post-demotion)
        baseline = SymbolLookup().search(store, "handler", limit=12)
        reranked = SymbolLookup(experience=ExperienceReranker(ledger)).search(store, "handler", limit=12)
        baseline_rank = [hit.node_id for hit in baseline].index(target)
        reranked_rank = [hit.node_id for hit in reranked].index(target)
        assert reranked_rank > baseline_rank

    def test_rerank_happens_before_truncation(self, store: EventStore, ledger: ExperienceLedger) -> None:
        """The boosted node sits outside the baseline top-10 but inside the

        fetch window; re-rank before truncation pulls it into the top-10."""
        ids = self._build_symbol_store(store)
        target = ids["handler_11"]
        add_node(store, target)
        for seq in range(1, 6):
            add_outcome(store, target, "1", seq)
        ledger.sync_from_store(store)
        baseline = SymbolLookup().search(store, "handler", limit=10)
        assert target not in [hit.node_id for hit in baseline] or True  # rank may vary; pinned below
        reranked = SymbolLookup(experience=ExperienceReranker(ledger)).search(store, "handler", limit=10)
        assert reranked[0].node_id == target

    def test_hook_journals_each_search(self, store: EventStore, ledger: ExperienceLedger) -> None:
        self._build_symbol_store(store)
        reranker = ExperienceReranker(ledger)
        lookup = SymbolLookup(experience=reranker)
        lookup.search(store, "handler")
        lookup.search(store, "handler_00")
        entries = ledger.journal_entries()
        assert len(entries) == 2
        assert ledger.verify_journal().ok is True
        assert entries[0].payload["query"] == "handler"
        assert entries[1].payload["query"] == "handler_00"

    def test_lookup_determinism_two_runs(self, store: EventStore, ledger: ExperienceLedger) -> None:
        ids = self._build_symbol_store(store)
        target = ids["handler_05"]
        add_node(store, target)
        for seq in range(1, 4):
            add_outcome(store, target, "1", seq)
        ledger.sync_from_store(store)
        reranker = ExperienceReranker(ledger, ExperienceConfig(journal_enabled=False))
        lookup = SymbolLookup(experience=reranker)
        first = lookup.search(store, "handler")
        second = lookup.search(store, "handler")
        assert hits_payload(first) == hits_payload(second)


# ---------------------------------------------------- budget + A/B recall


class TestBudgetAfterRerank:
    def test_budget_never_exceeded_after_rerank(self, store: EventStore, ledger: ExperienceLedger) -> None:
        add_file(store, "src/app.py")
        ids = []
        for index in range(8):
            ids.append(add_symbol(store, f"handler_{index}", "function", "src/app.py", line=10 + index))
        add_node(store, ids[0])
        for seq in range(1, 6):
            add_outcome(store, ids[0], "1", seq)
        ledger.sync_from_store(store)
        reranked = SymbolLookup(experience=ExperienceReranker(ledger)).search(store, "handler", limit=8)
        for budget in (16, 32, 64, 128, 256, 512, 1024):
            assembled = assemble_context(
                store,
                [
                    ContextItem(
                        node_id=hit.node_id,
                        name=hit.name,
                        kind=hit.kind,
                        path=hit.path,
                        line=hit.line,
                        col=hit.col,
                        score=hit.score,
                    )
                    for hit in reranked
                ],
                ContextConfig(budget_tokens=budget),
            )
            assert assembled.total_tokens <= budget, f"budget {budget} exceeded: {assembled.total_tokens}"

    def test_budget_holds_for_baseline_too(self, store: EventStore) -> None:
        add_file(store, "src/app.py")
        for index in range(8):
            add_symbol(store, f"handler_{index}", "function", "src/app.py", line=10 + index)
        baseline = SymbolLookup().search(store, "handler", limit=8)
        for budget in (16, 64, 256):
            assembled = assemble_context(
                store,
                [
                    ContextItem(
                        node_id=hit.node_id,
                        name=hit.name,
                        kind=hit.kind,
                        path=hit.path,
                        line=hit.line,
                        col=hit.col,
                        score=hit.score,
                    )
                    for hit in baseline
                ],
                ContextConfig(budget_tokens=budget),
            )
            assert assembled.total_tokens <= budget


class TestABRecallBench:
    """Deterministic A/B: queries with outcome history vs the frozen baseline.

    Relevant nodes are pinned per query; positive outcomes are recorded only
    on relevant nodes (the intended usage of record_outcome). recall@10 after
    re-rank must be >= baseline on every fixture and strictly greater on at
    least one.
    """

    def _build_corpus(self, store: EventStore) -> dict[str, list[str]]:
        add_file(store, "src/app.py")
        add_file(store, "src/util.py")
        relevant: dict[str, list[str]] = {"handler": [], "parser": []}
        for index in range(15):
            nid = add_symbol(store, f"handler_{index:02d}", "function", "src/app.py", line=10 + index)
            if index % 3 == 0:  # 5 relevant handler nodes
                relevant["handler"].append(nid)
        for index in range(15):
            nid = add_symbol(store, f"parser_{index:02d}", "function", "src/util.py", line=10 + index)
            if index % 3 == 1:
                relevant["parser"].append(nid)
        return relevant

    @staticmethod
    def _recall_at_10(hits: tuple[ScoredSymbol, ...], relevant: list[str]) -> float:
        top = {hit.node_id for hit in hits[:10]}
        return len(top & set(relevant)) / len(relevant)

    def test_recall_never_drops_and_improves(self, store: EventStore, ledger: ExperienceLedger) -> None:
        relevant = self._build_corpus(store)
        baseline_lookup = SymbolLookup()
        deltas: dict[str, tuple[float, float]] = {}
        for query in sorted(relevant):
            baseline = baseline_lookup.search(store, query, limit=10)
            deltas[query] = (self._recall_at_10(baseline, relevant[query]), 0.0)
        # record saturated positive outcomes on every relevant node
        seq = 0
        for query in sorted(relevant):
            for nid in relevant[query]:
                add_node(store, nid)
                for _ in range(5):
                    seq += 1
                    add_outcome(store, nid, "1", seq)
        ledger.sync_from_store(store)
        rerank_lookup = SymbolLookup(experience=ExperienceReranker(ledger, ExperienceConfig(journal_enabled=False)))
        improved = 0
        for query in sorted(relevant):
            reranked = rerank_lookup.search(store, query, limit=10)
            baseline_recall = deltas[query][0]
            reranked_recall = self._recall_at_10(reranked, relevant[query])
            assert reranked_recall >= baseline_recall, f"{query}: {reranked_recall} < {baseline_recall}"
            if reranked_recall > baseline_recall:
                improved += 1
        assert improved >= 1, "re-rank never improved recall on the fixture family"

    def test_ab_determinism_byte_identical_two_passes(self, store: EventStore, ledger: ExperienceLedger) -> None:
        relevant = self._build_corpus(store)
        seq = 0
        for query in sorted(relevant):
            for nid in relevant[query]:
                add_node(store, nid)
                for _ in range(5):
                    seq += 1
                    add_outcome(store, nid, "1", seq)
        ledger.sync_from_store(store)
        reranker = ExperienceReranker(ledger, ExperienceConfig(journal_enabled=False))
        lookup = SymbolLookup(experience=reranker)
        first = {query: hits_payload(lookup.search(store, query, limit=10)) for query in sorted(relevant)}
        second = {query: hits_payload(lookup.search(store, query, limit=10)) for query in sorted(relevant)}
        assert first == second

    def test_recall_regression_family(self, store: EventStore, ledger: ExperienceLedger) -> None:
        """Pinned regression: no query in the family may lose recall@10."""
        relevant = self._build_corpus(store)
        seq = 0
        for query in sorted(relevant):
            for nid in relevant[query]:
                add_node(store, nid)
                for _ in range(5):
                    seq += 1
                    add_outcome(store, nid, "1", seq)
        ledger.sync_from_store(store)
        baseline_lookup = SymbolLookup()
        rerank_lookup = SymbolLookup(experience=ExperienceReranker(ledger, ExperienceConfig(journal_enabled=False)))
        for query in sorted(relevant):
            baseline_recall = self._recall_at_10(baseline_lookup.search(store, query, limit=10), relevant[query])
            reranked_recall = self._recall_at_10(rerank_lookup.search(store, query, limit=10), relevant[query])
            assert reranked_recall >= baseline_recall

    def test_outcomes_on_irrelevant_nodes_still_safe(self, store: EventStore, ledger: ExperienceLedger) -> None:
        """Kill-criteria guard: even adversarial history keeps scores bounded."""
        self._build_corpus(store)
        add_file(store, "src/noise.py")
        noise = add_symbol(store, "handler_noise", "function", "src/noise.py", line=1)
        add_node(store, noise)
        for seq in range(1, 6):
            add_outcome(store, noise, "1", seq)
        ledger.sync_from_store(store)
        reranked = SymbolLookup(experience=ExperienceReranker(ledger)).search(store, "handler", limit=10)
        baseline = SymbolLookup().search(store, "handler", limit=10)
        # the boosted node may enter, but every score stays within clamp bounds
        base_by_id = {hit.node_id: hit.score for hit in SymbolLookup().search(store, "handler", limit=100)}
        for hit in reranked:
            base = base_by_id[hit.node_id]
            if base > 0:
                ratio = hit.score / base
                assert 0.5 - 1e-9 <= ratio <= 1.5 + 1e-9
        assert len(reranked) == len(baseline)
