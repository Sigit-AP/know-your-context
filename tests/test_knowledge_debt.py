"""Tests for the knowledge debt queue (I4, W14 SUB-128).

Pins the contract of ``know_your_context.analytics.debt``: four frozen
detectors (low score, flagged contradiction, stale access, old unresolved
refs), a severity x impact priority in quantized Decimal, a budgeted top-K
queue with a kill switch, deterministic proposal drafts (improve/merge/
retire), and burn-down metrics across cycles. The module is pure: it never
reads the store, never writes, never touches the wall clock (``now`` is
injected).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from know_your_context.analytics import debt
from know_your_context.analytics.debt import (
    DEFAULT_BUDGET,
    DEFAULT_LOW_SCORE_THRESHOLD,
    DEFAULT_STALE_DAYS,
    DEFAULT_UNRESOLVED_REF_DAYS,
    IMPACT_DEGREE_SATURATION,
    KILL_SWITCH_ENV,
    MAX_BUDGET,
    SCHEMA_VERSION,
    SIGNAL_CATALOG,
    SIGNAL_WEIGHTS,
    DebtBudgetError,
    DebtConfig,
    DebtConfigError,
    DebtError,
    build_debt_queue,
    build_debt_report,
    burndown,
    kill_switch_disabled,
    queue_snapshot,
    render_debt_markdown,
)
from know_your_context.domain import canonical_json

NOW = "2026-02-01T00:00:00+00:00"
OLD = "2025-12-01T00:00:00+00:00"  # 62 days before NOW
RECENT = "2026-01-30T00:00:00+00:00"  # 2 days before NOW


def _node(
    node_id: str,
    *,
    confidence: str = "0.500000000000",
    last_used_at: str | None = RECENT,
    updated_at: str | None = RECENT,
    created_at: str | None = OLD,
    domain: str = "P1",
    version: int = 1,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "version": version,
        "domain": domain,
        "scores": {
            "p": "0.000000000000",
            "m": "0.000000000000",
            "e": "0.000000000000",
            "uncertainty": "1.000000000000",
            "confidence": confidence,
        },
        "last_used_at": last_used_at,
        "updated_at": updated_at,
        "created_at": created_at,
        "lifecycle_status": "active",
    }


def _link(source: str, target: str, relation: str = "supports") -> dict[str, str]:
    return {"source": source, "target": target, "relation": relation}


def _ref(
    ref_id: int,
    source: str,
    *,
    status: str = "pending",
    first_seen: str = OLD,
    target_name: str = "gamma_fn",
) -> dict[str, Any]:
    return {
        "ref_id": ref_id,
        "source_node_id": source,
        "target_name": target_name,
        "status": status,
        "first_seen": first_seen,
    }


def _queue(nodes, links=(), *, unresolved_refs=None, config=None, env=None, now=NOW):
    return build_debt_queue(nodes, links, now=now, unresolved_refs=unresolved_refs, config=config, env=env)


# --------------------------------------------------------------------- catalog


class TestCatalogAndWeights:
    def test_catalog_order_is_frozen(self) -> None:
        assert SIGNAL_CATALOG == (
            "contradiction_flagged",
            "low_score",
            "stale_access",
            "unresolved_ref_old",
        )

    def test_weights_cover_exactly_the_catalog(self) -> None:
        assert set(SIGNAL_WEIGHTS) == set(SIGNAL_CATALOG)

    def test_weights_are_frozen_exact_strings(self) -> None:
        assert SIGNAL_WEIGHTS == {
            "contradiction_flagged": "0.500000000000",
            "low_score": "0.300000000000",
            "stale_access": "0.100000000000",
            "unresolved_ref_old": "0.100000000000",
        }

    def test_weights_sum_to_one(self) -> None:
        from decimal import Decimal

        total = sum(Decimal(weight) for weight in SIGNAL_WEIGHTS.values())
        assert total == Decimal("1.000000000000")

    def test_defaults_are_pinned(self) -> None:
        assert DEFAULT_LOW_SCORE_THRESHOLD == "0.250000000000"
        assert DEFAULT_STALE_DAYS == 30
        assert DEFAULT_UNRESOLVED_REF_DAYS == 7
        assert DEFAULT_BUDGET == 10
        assert MAX_BUDGET == 100
        assert IMPACT_DEGREE_SATURATION == 10

    def test_schema_version_is_one(self) -> None:
        assert SCHEMA_VERSION == 1


# ---------------------------------------------------------------------- config


class TestConfig:
    def test_default_config_constructs(self) -> None:
        config = DebtConfig()
        assert config.budget == DEFAULT_BUDGET

    def test_budget_zero_is_a_typed_error(self) -> None:
        with pytest.raises(DebtBudgetError):
            DebtConfig(budget=0)

    def test_budget_above_max_is_a_typed_error(self) -> None:
        with pytest.raises(DebtBudgetError):
            DebtConfig(budget=MAX_BUDGET + 1)

    def test_budget_negative_is_a_typed_error(self) -> None:
        with pytest.raises(DebtBudgetError):
            DebtConfig(budget=-3)

    def test_budget_at_max_is_accepted(self) -> None:
        assert DebtConfig(budget=MAX_BUDGET).budget == MAX_BUDGET

    def test_unparseable_threshold_is_a_typed_error(self) -> None:
        with pytest.raises(DebtConfigError):
            DebtConfig(low_score_threshold="not-a-number")

    def test_threshold_above_one_is_a_typed_error(self) -> None:
        with pytest.raises(DebtConfigError):
            DebtConfig(low_score_threshold="1.5")

    def test_threshold_below_zero_is_a_typed_error(self) -> None:
        with pytest.raises(DebtConfigError):
            DebtConfig(low_score_threshold="-0.1")

    def test_negative_stale_days_is_a_typed_error(self) -> None:
        with pytest.raises(DebtConfigError):
            DebtConfig(stale_days=-1)

    def test_negative_unresolved_days_is_a_typed_error(self) -> None:
        with pytest.raises(DebtConfigError):
            DebtConfig(unresolved_ref_days=-1)

    def test_zero_saturation_is_a_typed_error(self) -> None:
        with pytest.raises(DebtConfigError):
            DebtConfig(impact_degree_saturation=0)

    def test_errors_are_value_errors(self) -> None:
        assert issubclass(DebtBudgetError, DebtError)
        assert issubclass(DebtConfigError, DebtError)
        assert issubclass(DebtError, ValueError)


# ------------------------------------------------------------------- low score


class TestLowScore:
    def test_below_threshold_fires(self) -> None:
        queue = _queue([_node("n1", confidence="0.100000000000")])
        item = next(i for i in queue["items"] if i["node_id"] == "n1")
        assert "low_score" in item["signals"]

    def test_at_threshold_is_silent(self) -> None:
        queue = _queue([_node("n1", confidence=DEFAULT_LOW_SCORE_THRESHOLD)])
        assert queue["candidate_count"] == 0

    def test_above_threshold_is_silent(self) -> None:
        queue = _queue([_node("n1", confidence="0.900000000000")])
        assert queue["candidate_count"] == 0

    def test_missing_scores_are_treated_as_zero(self) -> None:
        node = _node("n1")
        del node["scores"]
        queue = _queue([node])
        item = next(i for i in queue["items"] if i["node_id"] == "n1")
        assert "low_score" in item["signals"]

    def test_unparseable_score_is_silent(self) -> None:
        queue = _queue([_node("n1", confidence="junk")])
        assert queue["candidate_count"] == 0

    def test_detail_carries_threshold_and_observed(self) -> None:
        queue = _queue([_node("n1", confidence="0.100000000000")])
        item = queue["items"][0]
        assert item["detail"]["low_score"]["observed"] == "0.100000000000"
        assert item["detail"]["low_score"]["threshold"] == DEFAULT_LOW_SCORE_THRESHOLD


# ---------------------------------------------------------------- contradiction


class TestContradiction:
    def test_unresolved_contradiction_flags_both_endpoints(self) -> None:
        queue = _queue([_node("a"), _node("b")], [_link("a", "b", "contradicts")])
        flagged = {i["node_id"] for i in queue["items"] if "contradiction_flagged" in i["signals"]}
        assert flagged == {"a", "b"}

    def test_supersedes_on_source_resolves(self) -> None:
        queue = _queue(
            [_node("a"), _node("b")],
            [_link("a", "b", "contradicts"), _link("c", "a", "supersedes")],
        )
        assert queue["candidate_count"] == 0

    def test_supersedes_on_target_resolves(self) -> None:
        queue = _queue(
            [_node("a"), _node("b")],
            [_link("a", "b", "contradicts"), _link("c", "b", "supersedes")],
        )
        assert queue["candidate_count"] == 0

    def test_contradiction_with_unknown_endpoint_is_ignored(self) -> None:
        queue = _queue([_node("a")], [_link("a", "ghost", "contradicts")])
        assert queue["candidate_count"] == 0

    def test_duplicate_contradicts_links_fire_once(self) -> None:
        queue = _queue(
            [_node("a"), _node("b")],
            [_link("a", "b", "contradicts"), _link("a", "b", "contradicts")],
        )
        items = [i for i in queue["items"] if i["node_id"] == "a"]
        assert len(items) == 1
        assert items[0]["signals"].count("contradiction_flagged") == 1

    def test_merge_target_is_the_stronger_counterpart(self) -> None:
        queue = _queue(
            [_node("a", confidence="0.100000000000"), _node("b", confidence="0.900000000000")],
            [_link("a", "b", "contradicts")],
        )
        item_a = next(i for i in queue["items"] if i["node_id"] == "a")
        item_b = next(i for i in queue["items"] if i["node_id"] == "b")
        assert item_a["merge_target"] == "b"
        assert item_b["merge_target"] == "a"

    def test_merge_target_tie_breaks_on_smaller_id(self) -> None:
        queue = _queue(
            [_node("b", confidence="0.900000000000"), _node("a", confidence="0.900000000000")],
            [_link("a", "b", "contradicts")],
        )
        item_a = next(i for i in queue["items"] if i["node_id"] == "a")
        item_b = next(i for i in queue["items"] if i["node_id"] == "b")
        assert item_a["merge_target"] == "b"
        assert item_b["merge_target"] == "a"


# ------------------------------------------------------------------------ stale


class TestStale:
    def test_old_last_used_fires(self) -> None:
        queue = _queue([_node("n1", last_used_at=OLD)])
        item = next(i for i in queue["items"] if i["node_id"] == "n1")
        assert "stale_access" in item["signals"]

    def test_exactly_stale_days_is_silent(self) -> None:
        queue = _queue([_node("n1", last_used_at="2026-01-02T00:00:00+00:00")])
        assert queue["candidate_count"] == 0

    def test_recent_use_is_silent(self) -> None:
        queue = _queue([_node("n1", last_used_at=RECENT)])
        assert queue["candidate_count"] == 0

    def test_none_last_used_falls_back_to_updated_at(self) -> None:
        queue = _queue([_node("n1", last_used_at=None, updated_at=OLD)])
        item = next(i for i in queue["items"] if i["node_id"] == "n1")
        assert "stale_access" in item["signals"]

    def test_falls_back_to_created_at(self) -> None:
        queue = _queue([_node("n1", last_used_at=None, updated_at=None, created_at=OLD)])
        item = next(i for i in queue["items"] if i["node_id"] == "n1")
        assert "stale_access" in item["signals"]

    def test_all_timestamps_missing_is_silent(self) -> None:
        queue = _queue([_node("n1", last_used_at=None, updated_at=None, created_at=None)])
        assert queue["candidate_count"] == 0

    def test_unparseable_timestamp_is_silent(self) -> None:
        queue = _queue([_node("n1", last_used_at="not-a-date", updated_at=None, created_at=None)])
        assert queue["candidate_count"] == 0

    def test_naive_timestamp_is_treated_as_utc(self) -> None:
        queue = _queue([_node("n1", last_used_at="2025-12-01T00:00:00")])
        item = next(i for i in queue["items"] if i["node_id"] == "n1")
        assert "stale_access" in item["signals"]

    def test_stale_days_zero_flags_any_past_timestamp(self) -> None:
        config = DebtConfig(stale_days=0)
        queue = _queue([_node("n1", last_used_at=RECENT)], config=config)
        item = next(i for i in queue["items"] if i["node_id"] == "n1")
        assert "stale_access" in item["signals"]


# --------------------------------------------------------------- unresolved refs


class TestUnresolvedRefOld:
    def test_old_pending_ref_fires(self) -> None:
        queue = _queue([_node("n1")], unresolved_refs=[_ref(1, "n1")])
        item = next(i for i in queue["items"] if i["node_id"] == "n1")
        assert "unresolved_ref_old" in item["signals"]

    def test_old_failed_ref_fires(self) -> None:
        queue = _queue([_node("n1")], unresolved_refs=[_ref(1, "n1", status="failed")])
        item = next(i for i in queue["items"] if i["node_id"] == "n1")
        assert "unresolved_ref_old" in item["signals"]

    def test_resolved_ref_is_silent(self) -> None:
        queue = _queue([_node("n1")], unresolved_refs=[_ref(1, "n1", status="resolved")])
        assert queue["candidate_count"] == 0

    def test_young_pending_ref_is_silent(self) -> None:
        queue = _queue([_node("n1")], unresolved_refs=[_ref(1, "n1", first_seen=RECENT)])
        assert queue["candidate_count"] == 0

    def test_exactly_the_age_limit_is_silent(self) -> None:
        queue = _queue([_node("n1")], unresolved_refs=[_ref(1, "n1", first_seen="2026-01-25T00:00:00+00:00")])
        assert queue["candidate_count"] == 0

    def test_ref_for_unknown_node_is_ignored(self) -> None:
        queue = _queue([_node("n1")], unresolved_refs=[_ref(1, "ghost")])
        assert queue["candidate_count"] == 0

    def test_detail_counts_refs(self) -> None:
        queue = _queue([_node("n1")], unresolved_refs=[_ref(1, "n1"), _ref(2, "n1", status="failed")])
        item = queue["items"][0]
        assert item["detail"]["unresolved_ref_old"]["count"] == 2

    def test_none_refs_disables_the_signal(self) -> None:
        queue = _queue([_node("n1")], unresolved_refs=None)
        assert queue["candidate_count"] == 0


# -------------------------------------------------------- severity/impact/priority


class TestScoring:
    def test_severity_is_the_sum_of_present_signal_weights(self) -> None:
        queue = _queue([_node("n1", confidence="0.1", last_used_at=OLD)])
        item = queue["items"][0]
        # low_score (0.3) + stale_access (0.1)
        assert item["severity"] == "0.400000000000"

    def test_severity_caps_at_one_for_all_signals(self) -> None:
        queue = _queue(
            [_node("a", confidence="0.1", last_used_at=OLD), _node("b")],
            [_link("a", "b", "contradicts")],
            unresolved_refs=[_ref(1, "a")],
        )
        item_a = next(i for i in queue["items"] if i["node_id"] == "a")
        assert item_a["severity"] == "1.000000000000"

    def test_impact_of_an_isolated_node(self) -> None:
        queue = _queue([_node("n1", confidence="0.1")])
        item = queue["items"][0]
        # (1 + 0) / (1 + 10)
        assert item["impact"] == "0.090909090909"

    def test_impact_saturates_at_ten_degree(self) -> None:
        nodes = [_node("hub", confidence="0.1")] + [_node(f"spoke-{i}") for i in range(10)]
        links = [_link("hub", f"spoke-{i}") for i in range(10)]
        queue = _queue(nodes, links)
        hub = next(i for i in queue["items"] if i["node_id"] == "hub")
        assert hub["impact"] == "1.000000000000"

    def test_degree_counts_both_directions(self) -> None:
        queue = _queue(
            [_node("a", confidence="0.1"), _node("b"), _node("c")],
            [_link("a", "b"), _link("c", "a")],
        )
        item = next(i for i in queue["items"] if i["node_id"] == "a")
        assert item["degree"] == 2

    def test_self_loops_do_not_count_towards_degree(self) -> None:
        queue = _queue([_node("a", confidence="0.1")], [_link("a", "a")])
        item = queue["items"][0]
        assert item["degree"] == 0

    def test_priority_is_severity_times_impact_quantized(self) -> None:
        queue = _queue([_node("a", confidence="0.1"), _node("b")], [_link("a", "b")])
        item = next(i for i in queue["items"] if i["node_id"] == "a")
        # severity 0.3, impact (1+1)/11 = 0.181818181818... -> priority 0.054545454545
        assert item["priority"] == "0.054545454545"

    def test_contradiction_outranks_low_score_at_equal_degree(self) -> None:
        queue = _queue(
            [
                _node("contra", confidence="0.9"),
                _node("low", confidence="0.1"),
                _node("other", confidence="0.9"),
            ],
            [_link("contra", "other", "contradicts"), _link("low", "other")],
        )
        ranks = {i["node_id"]: i["rank"] for i in queue["items"]}
        assert ranks["contra"] < ranks["low"]


# --------------------------------------------------------------------- ordering


class TestOrdering:
    def _crowd(self) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        nodes = [
            _node("n-low", confidence="0.1"),
            _node("n-stale", last_used_at=OLD),
            _node("n-both", confidence="0.1", last_used_at=OLD),
            _node("n-ok"),
        ]
        links = [_link("n-both", "n-ok"), _link("n-low", "n-ok")]
        return nodes, links

    def test_sorted_by_priority_descending(self) -> None:
        nodes, links = self._crowd()
        queue = _queue(nodes, links)
        priorities = [i["priority"] for i in queue["items"]]
        assert priorities == sorted(priorities, reverse=True)

    def test_tie_breaks_on_severity_then_node_id(self) -> None:
        # Equal degree (0), equal priority drivers -> severity then id.
        nodes = [_node("b-node", confidence="0.1"), _node("a-node", confidence="0.1")]
        queue = _queue(nodes, [])
        assert [i["node_id"] for i in queue["items"]] == ["a-node", "b-node"]

    def test_rank_is_one_based_and_sequential(self) -> None:
        nodes, links = self._crowd()
        queue = _queue(nodes, links)
        assert [i["rank"] for i in queue["items"]] == list(range(1, len(queue["items"]) + 1))

    def test_order_is_stable_under_input_permutation(self) -> None:
        nodes, links = self._crowd()
        baseline = canonical_json(_queue(nodes, links))
        for permuted in (
            list(reversed(nodes)),
            [nodes[2], nodes[0], nodes[3], nodes[1]],
            [nodes[1], nodes[3], nodes[0], nodes[2]],
        ):
            assert canonical_json(_queue(permuted, list(reversed(links)))) == baseline

    def test_two_runs_are_byte_identical(self) -> None:
        nodes, links = self._crowd()
        assert canonical_json(_queue(nodes, links)) == canonical_json(_queue(nodes, links))


# ------------------------------------------------------------------------ budget


class TestBudget:
    def _many(self, count: int) -> list[dict[str, Any]]:
        return [_node(f"n-{i:03d}", confidence="0.1") for i in range(count)]

    def test_default_budget_caps_at_ten(self) -> None:
        queue = _queue(self._many(12))
        assert len(queue["items"]) == 10
        assert queue["truncated"] is True
        assert queue["overflow_count"] == 2
        assert queue["candidate_count"] == 12

    def test_explicit_budget_is_enforced(self) -> None:
        queue = _queue(self._many(12), config=DebtConfig(budget=3))
        assert len(queue["items"]) == 3
        assert queue["overflow_count"] == 9

    def test_budget_above_candidates_is_not_truncated(self) -> None:
        queue = _queue(self._many(3))
        assert queue["truncated"] is False
        assert queue["overflow_count"] == 0
        assert len(queue["items"]) == 3

    def test_budget_keeps_the_highest_priority_items(self) -> None:
        nodes = self._many(4)
        nodes.append(_node("n-zzz", confidence="0.1", last_used_at=OLD))  # severity 0.4
        queue = _queue(nodes, config=DebtConfig(budget=1))
        assert [i["node_id"] for i in queue["items"]] == ["n-zzz"]

    def test_budget_error_surfaces_through_build(self) -> None:
        with pytest.raises(DebtBudgetError):
            _queue(self._many(1), config=DebtConfig(budget=1).__class__(budget=0))


# ------------------------------------------------------------------- kill switch


class TestKillSwitch:
    def test_absent_env_means_enabled(self) -> None:
        assert kill_switch_disabled(env={}) is False

    def test_zero_disables(self) -> None:
        assert kill_switch_disabled(env={KILL_SWITCH_ENV: "0"}) is True

    @pytest.mark.parametrize("value", ["false", "no", "off", "FALSE", " Off "])
    def test_false_spellings_disable(self, value: str) -> None:
        assert kill_switch_disabled(env={KILL_SWITCH_ENV: value}) is True

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_true_spellings_enable(self, value: str) -> None:
        assert kill_switch_disabled(env={KILL_SWITCH_ENV: value}) is False

    def test_garbage_fails_safe_to_disabled(self) -> None:
        assert kill_switch_disabled(env={KILL_SWITCH_ENV: "maybe"}) is True

    def test_disabled_queue_is_a_typed_empty_envelope(self) -> None:
        queue = _queue([_node("n1", confidence="0.1")], env={KILL_SWITCH_ENV: "0"})
        assert queue["disabled"] is True
        assert queue["items"] == []
        assert queue["candidate_count"] == 0
        assert KILL_SWITCH_ENV in queue["reason"]
        assert queue["signal_counts"] == {signal: 0 for signal in SIGNAL_CATALOG}

    def test_disabled_queue_still_echoes_config_and_now(self) -> None:
        queue = _queue([_node("n1", confidence="0.1")], env={KILL_SWITCH_ENV: "0"})
        assert queue["generated_at"] == NOW
        assert queue["config"]["budget"] == DEFAULT_BUDGET


# ----------------------------------------------------------------------- actions


class TestActions:
    def test_stale_and_low_score_proposes_retire(self) -> None:
        queue = _queue([_node("n1", confidence="0.1", last_used_at=OLD)])
        item = queue["items"][0]
        assert item["action"] == "retire"
        assert item["proposal"]["impact"] == "high"
        assert item["proposal"]["diff"] == [{"op": "set_status", "key": "", "value": "deprecate"}]

    def test_contradiction_proposes_merge(self) -> None:
        queue = _queue(
            [_node("a", confidence="0.9"), _node("b", confidence="0.9")],
            [_link("a", "b", "contradicts")],
        )
        item = next(i for i in queue["items"] if i["node_id"] == "a")
        assert item["action"] == "merge"
        assert item["proposal"]["impact"] == "high"
        assert item["proposal"]["diff"] == [{"op": "set_status", "key": "", "value": "superseded"}]
        assert item["merge_target"] == "b"

    def test_retire_wins_over_merge(self) -> None:
        queue = _queue(
            [_node("a", confidence="0.1", last_used_at=OLD), _node("b")],
            [_link("a", "b", "contradicts")],
        )
        item = next(i for i in queue["items"] if i["node_id"] == "a")
        assert item["action"] == "retire"

    def test_unresolved_ref_only_proposes_improve(self) -> None:
        queue = _queue([_node("n1")], unresolved_refs=[_ref(1, "n1")])
        item = queue["items"][0]
        assert item["action"] == "improve"
        assert item["proposal"]["impact"] == "low"
        assert item["proposal"]["diff"] == [{"op": "add_context_tag", "key": "", "value": "debt-review"}]

    def test_low_score_only_proposes_improve(self) -> None:
        queue = _queue([_node("n1", confidence="0.1")])
        assert queue["items"][0]["action"] == "improve"

    def test_every_proposal_carries_a_rationale(self) -> None:
        queue = _queue(
            [_node("a", confidence="0.1", last_used_at=OLD), _node("b", confidence="0.9")],
            [_link("a", "b", "contradicts")],
        )
        for item in queue["items"]:
            assert item["proposal"]["rationale"]


# ------------------------------------------------------------------------ report


class TestReport:
    def test_report_wraps_queue_and_burndown(self) -> None:
        queue = _queue([_node("n1", confidence="0.1")])
        report = build_debt_report(queue, snapshots=[])
        assert report["schema_version"] == SCHEMA_VERSION
        assert report["queue"]["candidate_count"] == 1
        assert report["burndown"]["trend"] == "insufficient_data"

    def test_markdown_has_header_table_and_signal_counts(self) -> None:
        queue = _queue([_node("n1", confidence="0.1")])
        markdown = render_debt_markdown(build_debt_report(queue, snapshots=[]))
        assert markdown.startswith("# Knowledge Debt Report")
        assert "n1" in markdown
        assert "low_score" in markdown
        assert "Burn-down" in markdown

    def test_markdown_is_byte_deterministic(self) -> None:
        queue = _queue([_node("n1", confidence="0.1"), _node("n2", last_used_at=OLD)])
        first = render_debt_markdown(build_debt_report(queue, snapshots=[]))
        second = render_debt_markdown(build_debt_report(queue, snapshots=[]))
        assert first == second

    def test_empty_queue_renders_an_honest_empty_state(self) -> None:
        queue = _queue([_node("n1")])
        markdown = render_debt_markdown(build_debt_report(queue, snapshots=[]))
        assert "no debt candidates" in markdown

    def test_report_echoes_the_config(self) -> None:
        queue = _queue([_node("n1", confidence="0.1")], config=DebtConfig(budget=5))
        report = build_debt_report(queue, snapshots=[])
        assert report["queue"]["config"]["budget"] == 5


# ---------------------------------------------------------------------- burndown


class TestBurndown:
    def test_snapshot_carries_cycle_counts_and_priority(self) -> None:
        queue = _queue([_node("n1", confidence="0.1"), _node("n2", last_used_at=OLD)])
        snap = queue_snapshot(queue, cycle=1, recorded_at=NOW)
        assert snap["schema_version"] == SCHEMA_VERSION
        assert snap["cycle"] == 1
        assert snap["recorded_at"] == NOW
        assert snap["candidate_count"] == 2
        assert snap["signal_counts"]["low_score"] == 1
        assert snap["total_priority"] > "0"

    def test_single_snapshot_is_insufficient_data(self) -> None:
        queue = _queue([_node("n1", confidence="0.1")])
        result = burndown([queue_snapshot(queue, cycle=1, recorded_at=NOW)])
        assert result["trend"] == "insufficient_data"
        assert result["deltas"] == []

    def test_empty_history_is_insufficient_data(self) -> None:
        result = burndown([])
        assert result["trend"] == "insufficient_data"
        assert result["cycles"] == 0

    def test_dropping_counts_is_improving(self) -> None:
        result = burndown(
            [
                {"cycle": 1, "recorded_at": OLD, "candidate_count": 8, "total_priority": "2.0", "signal_counts": {}},
                {"cycle": 2, "recorded_at": NOW, "candidate_count": 5, "total_priority": "1.0", "signal_counts": {}},
            ]
        )
        assert result["trend"] == "improving"
        assert result["net_count_delta"] == -3
        assert result["burned_total"] == 3

    def test_rising_counts_is_worsening(self) -> None:
        result = burndown(
            [
                {"cycle": 1, "recorded_at": OLD, "candidate_count": 2, "total_priority": "0.5", "signal_counts": {}},
                {"cycle": 2, "recorded_at": NOW, "candidate_count": 7, "total_priority": "1.5", "signal_counts": {}},
            ]
        )
        assert result["trend"] == "worsening"
        assert result["net_count_delta"] == 5
        assert result["burned_total"] == 0

    def test_equal_counts_are_flat(self) -> None:
        snap = {"cycle": 1, "recorded_at": OLD, "candidate_count": 4, "total_priority": "1.0", "signal_counts": {}}
        result = burndown([snap, {**snap, "cycle": 2, "recorded_at": NOW}])
        assert result["trend"] == "flat"
        assert result["net_count_delta"] == 0

    def test_deltas_cover_each_adjacent_pair(self) -> None:
        snaps = [
            {"cycle": i, "recorded_at": NOW, "candidate_count": 10 - i, "total_priority": "1.0", "signal_counts": {}}
            for i in range(1, 5)
        ]
        result = burndown(snaps)
        assert len(result["deltas"]) == 3
        assert result["deltas"][0] == {
            "from_cycle": 1,
            "to_cycle": 2,
            "delta_count": -1,
            "delta_priority": "0.000000000000",
        }

    def test_total_priority_delta_is_quantized(self) -> None:
        result = burndown(
            [
                {
                    "cycle": 1,
                    "recorded_at": OLD,
                    "candidate_count": 2,
                    "total_priority": "1.000000000005",
                    "signal_counts": {},
                },
                {
                    "cycle": 2,
                    "recorded_at": NOW,
                    "candidate_count": 2,
                    "total_priority": "0.500000000000",
                    "signal_counts": {},
                },
            ]
        )
        assert result["net_priority_delta"] == "-0.500000000005"

    def test_burndown_is_deterministic(self) -> None:
        snaps = [
            {"cycle": 1, "recorded_at": OLD, "candidate_count": 3, "total_priority": "0.9", "signal_counts": {}},
            {"cycle": 2, "recorded_at": NOW, "candidate_count": 1, "total_priority": "0.4", "signal_counts": {}},
        ]
        assert canonical_json(burndown(snaps)) == canonical_json(burndown(snaps))


# ------------------------------------------------------------------------ purity


class TestPurity:
    def test_inputs_are_not_mutated(self) -> None:
        nodes = [_node("a", confidence="0.1"), _node("b")]
        links = [_link("a", "b", "contradicts")]
        refs = [_ref(1, "a")]
        snapshot = copy.deepcopy((nodes, links, refs))
        _queue(nodes, links, unresolved_refs=refs)
        assert (nodes, links, refs) == snapshot

    def test_output_is_canonical_json_serializable(self) -> None:
        queue = _queue([_node("n1", confidence="0.1")], unresolved_refs=[_ref(1, "n1")])
        assert isinstance(canonical_json(queue), str)

    def test_module_exposes_no_io(self) -> None:
        assert not hasattr(debt, "open")
