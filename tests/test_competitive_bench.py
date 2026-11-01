"""Tests for the competitive benchmark harness (SUB-113, wave 9).

The harness lives in ``bench/competitive/run.py`` (a script, loaded here as a
module). It measures know-your-context vs codegraph vs graphify on identical
tasks over the pinned ``bench/codebase`` dataset.

Pinned here:

* self-checks (``check``) pass on this machine;
* the token estimator is the frozen contract formula (parity with
  ``contracts.estimated_tokens_of_payload``);
* system discovery is honest: a missing competitor CLI yields NOT_SUPPORTED
  entries carrying evidence, never a crash;
* query exit-code policy: graphify's documented 0/3/4 are non-error answers;
* ledger/report structure: every entry carries system/task/target/status and
  (for executed tasks) the exact command argv + exit code;
* a live fixture-level probe per *available* system: ingest + one query,
  skipping honestly when the CLI or dataset is absent (never any network).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = REPO_ROOT / "bench" / "competitive" / "run.py"


def _load_harness() -> Any:
    spec = importlib.util.spec_from_file_location("kys_competitive_run", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cb = _load_harness()

DATASET_MISSING = not cb.dataset_present()
requires_dataset = pytest.mark.skipif(
    DATASET_MISSING,
    reason="codebase bench dataset not vendored (run: uv run python bench/codebase/run.py fetch)",
)
CODEGRAPH_MISSING = cb.find_codegraph() is None
requires_codegraph = pytest.mark.skipif(
    CODEGRAPH_MISSING,
    reason=f"codegraph CLI not installed ({cb.CODEGRAPH_INSTALL}) or ${cb.CODEGRAPH_ENV} unset",
)
GRAPHIFY_MISSING = cb.find_graphify() is None
requires_graphify = pytest.mark.skipif(GRAPHIFY_MISSING, reason="graphify CLI not on PATH")


# --------------------------------------------------------------- self-check


class TestSelfChecks:
    def test_check_exits_zero(self):
        assert cb.cmd_check() == 0

    def test_estimator_matches_frozen_contract(self):
        contracts = __import__(f"{cb.product_module()}.contracts", fromlist=["canonical_json"])
        for payload in ("", "a", "abcd", {"k": [1, 2]}, ["x", None, 3.5]):
            canonical = contracts.canonical_json(payload)
            assert cb.estimated_tokens(canonical) == contracts.estimated_tokens_of_payload(payload)
        # Raw-byte form: ceil(len/4) exactly.
        assert cb.estimated_tokens(b"") == 0
        assert cb.estimated_tokens(b"abc") == 1
        assert cb.estimated_tokens(b"abcd") == 1
        assert cb.estimated_tokens(b"abcde") == 2

    def test_percentile_nearest_rank(self):
        assert cb._percentile([], 0.5) == 0.0
        assert cb._percentile([5.0], 0.95) == 5.0
        assert cb._percentile([1.0, 2.0, 3.0, 4.0], 0.50) == 2.0
        assert cb._percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0

    def test_system_report_shape(self):
        report = cb.system_report()
        assert set(report) == {"know_your_context", "codegraph", "graphify"}
        assert report["know_your_context"]["available"] is True
        for name in ("codegraph", "graphify"):
            info = report[name]
            if info["available"]:
                assert info.get("version"), name
            else:
                assert info.get("evidence"), name


# ----------------------------------------------------------- skip semantics


class TestNotSupported:
    def test_not_supported_entry_carries_evidence(self):
        e = cb.not_supported("codegraph", "concurrent", "fixture", "cli not found (install: …)")
        assert e["status"] == "NOT_SUPPORTED"
        assert e["system"] == "codegraph"
        assert e["task"] == "concurrent"
        assert e["target"] == "fixture"
        assert "cli not found" in e["evidence"]

    def test_unavailable_system_yields_not_supported_per_task(self, tmp_path, monkeypatch):
        """A missing CLI must produce NOT_SUPPORTED entries, never a crash."""
        monkeypatch.setattr(cb, "find_codegraph", lambda: None)
        monkeypatch.setattr(cb, "find_graphify", lambda: None)
        monkeypatch.setattr(cb, "RESULTS_DIR", tmp_path)
        report = cb.system_report()
        assert report["codegraph"]["available"] is False
        assert "install" in report["codegraph"]["evidence"]
        assert report["graphify"]["available"] is False

    def test_query_exit_policy(self):
        assert 0 in cb.QUERY_OK_EXIT["know_your_context"]
        assert 0 in cb.QUERY_OK_EXIT["codegraph"]
        # graphify: 3 = ambiguous, 4 = no match — documented non-error answers.
        assert cb.QUERY_OK_EXIT["graphify"] == frozenset({0, 3, 4})


# -------------------------------------------------------- structure pinning


class TestLedgerStructure:
    def _synthetic_ledger(self) -> dict[str, Any]:
        return {
            "harness": "bench/competitive",
            "harness_version": cb.HARNESS_VERSION,
            "started_utc": "20261101T000000Z",
            "work_dir": "/tmp/x",
            "config": {"clients": 2, "queries_per_client": 1, "cold_repeats": 1, "kill_fraction": 0.4},
            "systems": {
                "know_your_context": {"available": True, "interface": "test"},
                "codegraph": {"available": False, "evidence": "cli not found"},
                "graphify": {"available": True, "version": "graphify 0.0"},
            },
            "reproduce": "uv run python bench/competitive/run.py run",
            "entries": [
                cb.entry("know_your_context", "ingest", "fixture", "OK", command=["x"], exit_code=0, wall_ms=1.0),
                cb.not_supported("codegraph", "ingest", "fixture", "cli not found"),
                cb.entry(
                    "graphify",
                    "determinism",
                    "fixture",
                    "OK",
                    verdict="byte_identical",
                    byte_identical=True,
                ),
            ],
            "finished_utc": "20261101T000001Z",
        }

    def test_summary_counts(self):
        ledger = self._synthetic_ledger()
        ledger["summary"] = cb.summarize(ledger["entries"])
        per = ledger["summary"]["per_system"]
        assert per["know_your_context"] == {"OK": 1, "FAILED": 0, "NOT_SUPPORTED": 0}
        assert per["codegraph"]["NOT_SUPPORTED"] == 1
        assert ledger["summary"]["entries"] == 3

    def test_report_renders_all_sections(self):
        ledger = self._synthetic_ledger()
        ledger["summary"] = cb.summarize(ledger["entries"])
        report = cb.render_report(ledger)
        for section in (
            "## Systems",
            "## Ingest",
            "## Cold-start",
            "## Concurrent load",
            "## Crash recovery",
            "## Determinism",
            "## Tokens per answer",
            "## Scoreboard",
        ):
            assert section in report, section
        assert "NOT_SUPPORTED" in report  # codegraph rows render honestly
        assert ledger["reproduce"] in report

    def test_report_is_deterministic(self):
        ledger = self._synthetic_ledger()
        ledger["summary"] = cb.summarize(ledger["entries"])
        assert cb.render_report(ledger) == cb.render_report(ledger)


# ------------------------------------------------------------- query sets


@requires_dataset
class TestQuerySets:
    def test_query_sets_match_ground_truth(self):
        query_sets = cb.load_query_sets()
        assert set(query_sets) == set(cb.target_names())
        for target, queries in query_sets.items():
            assert queries, target
            assert len(queries) == len(set(queries)), f"duplicate queries for {target}"

    def test_negative_query_present(self):
        """The frozen set must include the negative lookup (expect: nothing)."""
        queries = cb.load_query_sets()["fixture"]
        assert "zzz_no_such_symbol" in queries

    def test_prepare_tree_fixture(self, tmp_path):
        dest = tmp_path / "tree"
        cb.prepare_tree("fixture", dest)
        assert (dest / "pylib" / "core.py").exists()
        assert (dest / "jslib" / "util.js").exists()

    def test_prepare_tree_rejects_unknown_target(self, tmp_path):
        with pytest.raises((SystemExit, StopIteration)):
            cb.prepare_tree("no-such-target", tmp_path / "tree")

    def test_prepare_tree_real_repo(self, tmp_path):
        dest = tmp_path / "tree"
        cb.prepare_tree("requests", dest)
        assert (dest / "src" / "requests" / "sessions.py").exists()


# ------------------------------------------------- live probes (skip-safe)


@requires_dataset
class TestLiveKys:
    def test_ingest_and_query_fixture(self, tmp_path):
        tree = tmp_path / "tree"
        cb.prepare_tree("fixture", tree)
        store = tmp_path / "store.db"
        ingest = cb._ingest_kys(tree, store)
        assert ingest["exit_code"] == 0
        assert ingest["counts"]["symbols"] > 0
        assert ingest["counts"]["dump_sha256"]

        q = cb._query_once("know_your_context", None, tmp_path, "base_helper")
        assert q["exit_ok"] and q["exit_code"] == 0
        assert q["answer_tokens"] > 0
        payload = json.loads(cb.run_cmd(q["command"], timeout=120)["stdout"])
        assert any(h["name"] == "base_helper" for h in payload)

    def test_determinism_fixture_byte_identical(self, tmp_path):
        tree = tmp_path / "tree"
        cb.prepare_tree("fixture", tree)
        a = cb._ingest_kys(tree, tmp_path / "a.db")
        b = cb._ingest_kys(tree, tmp_path / "b.db")
        assert a["counts"]["dump_sha256"] == b["counts"]["dump_sha256"]


@requires_dataset
@requires_codegraph
class TestLiveCodegraph:
    def test_ingest_and_query_fixture(self, tmp_path):
        cli = cb.find_codegraph()
        tree = tmp_path / "tree"
        cb.prepare_tree("fixture", tree)
        ingest = cb._ingest_codegraph(cli, tree)
        assert ingest["exit_code"] == 0
        assert ingest["counts"]["nodes"] > 0
        q = cb._query_once("codegraph", cli, tmp_path, "base_helper")
        assert q["exit_ok"]
        assert "base_helper" in cb.run_cmd(q["command"], timeout=120)["stdout"]


@requires_dataset
@requires_graphify
class TestLiveGraphify:
    def test_ingest_and_query_fixture(self, tmp_path):
        cli = cb.find_graphify()
        tree = tmp_path / "tree"
        cb.prepare_tree("fixture", tree)
        ingest = cb._ingest_graphify(cli, tree)
        assert ingest["exit_code"] == 0
        assert ingest["counts"]["nodes"] > 0
        q = cb._query_once("graphify", cli, tmp_path, "base_helper")
        assert q["exit_ok"]

    def test_negative_query_exit_4_is_accepted(self, tmp_path):
        cli = cb.find_graphify()
        tree = tmp_path / "tree"
        cb.prepare_tree("fixture", tree)
        cb._ingest_graphify(cli, tree)
        q = cb._query_once("graphify", cli, tmp_path, "zzz_no_such_symbol")
        assert q["exit_ok"], f"exit {q['exit_code']} must be accepted (graphify: 4 = no match)"
