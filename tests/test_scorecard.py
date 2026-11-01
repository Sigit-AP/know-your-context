"""Tests for the capability scorecard (bench/scorecard/).

The scorecard is a measurement instrument: every claim about our side is a
real pytest node id executed by ``run.py run``; every score is a deterministic
function of the pass set and the documented opponent statuses. These tests pin
the document shape, the formulas, the JUnit mapping, and the harness itself.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_PY = REPO_ROOT / "bench" / "scorecard" / "run.py"
CAPS_PATH = REPO_ROOT / "bench" / "scorecard" / "capabilities.json"

spec = importlib.util.spec_from_file_location("scorecard_run", RUN_PY)
assert spec is not None and spec.loader is not None
scorecard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorecard)

_EV = [{"path": "README.md", "line": 1, "note": "synthetic"}]

DOC = scorecard.load_capabilities()
OPPONENT_REPOS = {
    "codegraph": Path("/root/src/codegraph"),
    "graphify": Path("/root/src/graphify"),
}


# ------------------------------------------------------------------ document
def test_capability_count_in_range():
    assert 60 <= len(DOC["capabilities"]) <= 80


def test_all_ten_categories_present():
    cats = {c["category"] for c in DOC["capabilities"]}
    assert set(scorecard.EXPECTED_CATEGORIES) <= cats


def test_ids_unique():
    ids = [c["id"] for c in DOC["capabilities"]]
    assert len(ids) == len(set(ids))


def test_weights_in_range():
    for c in DOC["capabilities"]:
        assert isinstance(c["weight"], int) and not isinstance(c["weight"], bool)
        assert 1 <= c["weight"] <= 3


def test_every_capability_has_proofs_and_both_opponents():
    for c in DOC["capabilities"]:
        assert c["ours"]["proof_tests"], c["id"]
        assert set(c["opponents"]) == {"codegraph", "graphify"}, c["id"]


def test_opponent_status_and_evidence_shape():
    for c in DOC["capabilities"]:
        for name, opp in c["opponents"].items():
            assert opp["status"] in ("supported", "partial", "absent"), (c["id"], name)
            assert opp["evidence"], (c["id"], name)
            for e in opp["evidence"]:
                assert e["path"] and isinstance(e["line"], int) and e["note"], (c["id"], name)


def _defined_nodes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.add(f"{node.name}::{sub.name}")
    return out


def test_every_proof_node_id_is_defined():
    """Static existence proof for every claim; the green-pass proof is
    ``run.py run`` itself (and ``test_run_check_subprocess_exit_zero``)."""
    cache: dict[str, set[str]] = {}
    for c in DOC["capabilities"]:
        for node in c["ours"]["proof_tests"]:
            parts = node.split("::")
            assert len(parts) in (2, 3), node
            src = REPO_ROOT / parts[0]
            assert src.is_file(), node
            if parts[0] not in cache:
                cache[parts[0]] = _defined_nodes(src)
            assert "::".join(parts[1:]) in cache[parts[0]], node


def test_validator_accepts_the_shipped_document():
    assert scorecard.validate(DOC) == []


def test_validator_flags_missing_proof(tmp_path):
    bad = json.loads(json.dumps(DOC))
    bad["capabilities"][0]["ours"]["proof_tests"] = ["tests/test_domain.py::test_no_such_test_zzz"]
    problems = scorecard.validate(bad)
    assert any("proof test not defined" in p for p in problems)


# ------------------------------------------------------- opponent evidence
@pytest.mark.parametrize("opp", sorted(OPPONENT_REPOS))
def test_opponent_evidence_files_exist(opp):
    repo = OPPONENT_REPOS[opp]
    if not repo.is_dir():
        pytest.skip(f"opponent repo not present: {repo}")
    for c in DOC["capabilities"]:
        for e in c["opponents"][opp]["evidence"]:
            target = repo / e["path"]
            assert target.exists(), f"{c['id']}: {opp} evidence missing: {e['path']}"
            if target.is_file() and target.stat().st_size < 2_000_000:
                lines = target.read_text(encoding="utf-8", errors="replace").count("\n") + 1
                assert 1 <= e["line"] <= lines, f"{c['id']}: {opp} line out of range: {e}"


# ------------------------------------------------------------------- scoring
def test_status_points_frozen():
    assert scorecard.STATUS_POINTS == {"supported": 1.0, "partial": 0.5, "absent": 0.0}
    assert DOC["scoring"]["status_points"] == scorecard.STATUS_POINTS


def test_composite_weights_sum_to_one_and_come_from_data():
    comp = DOC["scoring"]["composite"]
    assert comp["capability_weight"] == 0.6
    assert comp["bench_weight"] == 0.4
    assert abs(comp["capability_weight"] + comp["bench_weight"] - 1.0) < 1e-9


def _tiny_doc():
    return {
        "capabilities": [
            {
                "id": "a",
                "category": "integrity",
                "weight": 2,
                "ours": {"proof_tests": ["t::p1", "t::p2"]},
                "opponents": {
                    "codegraph": {
                        "status": "supported",
                        "evidence": _EV,
                    },
                    "graphify": {
                        "status": "partial",
                        "evidence": _EV,
                    },
                },
            },
            {
                "id": "b",
                "category": "security",
                "weight": 3,
                "ours": {"proof_tests": ["t::p3"]},
                "opponents": {
                    "codegraph": {
                        "status": "absent",
                        "evidence": _EV,
                    },
                    "graphify": {
                        "status": "supported",
                        "evidence": _EV,
                    },
                },
            },
        ]
    }


def test_scoring_hand_computed():
    # total weight 5; ours passes only "a" (2/5=40%); codegraph 2*1+3*0=2 (40%);
    # graphify 2*0.5+3*1=4 (80%). relative: 100 vs codegraph, 50 vs graphify.
    scores = scorecard.compute_capability_scores(_tiny_doc(), {"t::p1", "t::p2"})
    assert scores["total_weight"] == 5
    assert scores["percent"] == {"ours": 40.0, "codegraph": 40.0, "graphify": 80.0}
    assert scores["relative_percent_vs"] == {"codegraph": 100.0, "graphify": 50.0}


def test_scoring_requires_all_proofs_of_a_capability():
    scores = scorecard.compute_capability_scores(_tiny_doc(), {"t::p1"})  # p2 missing
    assert scores["percent"]["ours"] == 0.0


def test_scoring_deterministic_and_order_independent():
    doc = _tiny_doc()
    passed = {"t::p1", "t::p2", "t::p3"}
    a = scorecard.compute_capability_scores(doc, passed)
    b = scorecard.compute_capability_scores(doc, set(reversed(sorted(passed))))
    flipped = json.loads(json.dumps(doc))
    flipped["capabilities"].reverse()
    c = scorecard.compute_capability_scores(flipped, passed)
    assert a == b == c


# ------------------------------------------------------------- junit mapping
def test_junit_parser_groups_and_fails_closed(tmp_path):
    xml = tmp_path / "j.xml"
    xml.write_text(
        """<testsuite>
  <testcase classname="tests.test_domain" name="test_ok"/>
  <testcase classname="tests.test_domain" name="test_bad"><failure/></testcase>
  <testcase classname="tests.test_domain" name="test_skipped"><skipped/></testcase>
  <testcase classname="tests.test_domain" name="test_param[1]"/>
  <testcase classname="tests.test_domain" name="test_param[2]"><error/></testcase>
  <testcase classname="tests.test_serve_gates.TestStormAllVerbs" name="test_cls_ok"/>
</testsuite>"""
    )
    out = scorecard.parse_junit(xml)
    assert out == {
        "tests/test_domain.py::test_ok": True,
        "tests/test_domain.py::test_bad": False,
        "tests/test_domain.py::test_skipped": False,
        "tests/test_domain.py::test_param": False,
        "tests/test_serve_gates.py::TestStormAllVerbs::test_cls_ok": True,
    }


def test_junit_nodeid_reconstructs_real_class_based_proof():
    node = scorecard._junit_nodeid(
        "tests.test_serve_gates.TestDrainAndForce", "test_graceful_drain_answers_in_flight_and_refuses_new"
    )
    assert node == (
        "tests/test_serve_gates.py::TestDrainAndForce::test_graceful_drain_answers_in_flight_and_refuses_new"
    )
    assert node in scorecard.proof_node_ids(DOC)


# ------------------------------------------------------------- bench component
def test_bench_extract_explicit_shape():
    payload = {"summary": {"relative_percent_vs": {"codegraph": 123.4, "graphify": 98.0}}}
    assert scorecard._extract_bench_relative(payload) == {"codegraph": 123.4, "graphify": 98.0}


def test_bench_extract_per_system_shape():
    payload = {
        "summary": {
            "per_system": {
                "know_your_context": {"OK": 6, "FAILED": 0, "NOT_SUPPORTED": 0},
                "codegraph": {"OK": 5, "FAILED": 1, "NOT_SUPPORTED": 0},
                "graphify": {"OK": 6, "FAILED": 0, "NOT_SUPPORTED": 0},
            }
        }
    }
    rel = scorecard._extract_bench_relative(payload)
    assert rel == {"codegraph": 120.0, "graphify": 100.0}


def test_bench_extract_unrecognized_returns_none():
    assert scorecard._extract_bench_relative({"hello": "world"}) is None


def test_bench_absent_renormalizes_to_capability_only():
    scores = scorecard.compute_capability_scores(_tiny_doc(), {"t::p1", "t::p2"})
    final = scorecard.compute_final(scores, None, 0.6, 0.4)
    assert final["final_vs"] == scores["relative_percent_vs"]
    assert final["composite"] == 75.0  # mean(100.0, 50.0)


def test_final_blend_hand_computed():
    scores = scorecard.compute_capability_scores(_tiny_doc(), {"t::p1", "t::p2"})
    bench = {"codegraph": 200.0, "graphify": 50.0}
    final = scorecard.compute_final(scores, bench, 0.6, 0.4)
    assert final["final_vs"]["codegraph"] == 0.6 * 100.0 + 0.4 * 200.0
    assert final["final_vs"]["graphify"] == 0.6 * 50.0 + 0.4 * 50.0
    assert final["composite"] == round((140.0 + 50.0) / 2, 4)


# ------------------------------------------------------------------- harness
def test_run_check_subprocess_exit_zero():
    proc = subprocess.run(
        [sys.executable, str(RUN_PY), "check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "capabilities" in proc.stdout


def test_run_e2e_tiny_document(tmp_path, monkeypatch):
    """Full harness against a 2-capability document with fast real proofs:
    pytest executes, the ledger carries the hand-computed scores, and the
    composite honors a synthetic bench ledger."""
    doc = {
        "suite": "capability-scorecard",
        "version": "0.0.0-test",
        "scoring": json.loads(json.dumps(DOC["scoring"])),
        "capabilities": [
            {
                "id": "e2e.one",
                "category": "integrity",
                "weight": 1,
                "ours": {"proof_tests": ["tests/test_domain.py::test_primary_domains_are_exactly_seven_stable_ids"]},
                "opponents": {
                    "codegraph": {
                        "status": "absent",
                        "evidence": _EV,
                    },
                    "graphify": {
                        "status": "supported",
                        "evidence": _EV,
                    },
                },
            },
            {
                "id": "e2e.two",
                "category": "security",
                "weight": 1,
                "ours": {"proof_tests": ["tests/test_scoring.py::test_zero_evidence_case_is_the_zero_assessment"]},
                "opponents": {
                    "codegraph": {
                        "status": "supported",
                        "evidence": _EV,
                    },
                    "graphify": {
                        "status": "partial",
                        "evidence": _EV,
                    },
                },
            },
        ],
    }
    caps = tmp_path / "capabilities.json"
    caps.write_text(json.dumps(doc))
    results = tmp_path / "results"
    bench = tmp_path / "bench-ledger.json"
    bench.write_text(json.dumps({"summary": {"relative_percent_vs": {"codegraph": 200.0}}}))

    monkeypatch.setattr(scorecard, "CAPABILITIES_PATH", caps)
    monkeypatch.setattr(scorecard, "RESULTS_DIR", results)
    monkeypatch.setattr(scorecard, "EXPECTED_CATEGORIES", ("integrity", "security"))
    monkeypatch.setattr(scorecard, "MIN_CAPABILITIES", 1)
    monkeypatch.setenv("KYS_COMPETITIVE_LEDGER", str(bench))

    assert scorecard.cmd_run() == 0
    ledgers = list(results.glob("ledger-*.json"))
    assert len(ledgers) == 1
    ledger = json.loads(ledgers[0].read_text())
    assert ledger["proofs"]["proof_passed"] == 2
    assert ledger["proofs"]["failed"] == [] and ledger["proofs"]["missing"] == []
    # ours 2/2 = 100%, codegraph 0+1 = 50%, graphify 1+0.5 = 75%
    assert ledger["capability_scores"]["percent"] == {
        "ours": 100.0,
        "codegraph": 50.0,
        "graphify": 75.0,
    }
    assert ledger["bench_component"]["status"] == "loaded"
    assert ledger["final"]["final_vs"]["codegraph"] == 0.6 * 200.0 + 0.4 * 200.0
    # graphify absent from the bench ledger -> capability component only
    assert ledger["final"]["final_vs"]["graphify"] == round(100.0 * 100.0 / 75.0, 4)
    assert (results / "scorecard-latest.md").is_file()


def test_run_e2e_fails_closed_on_red_proof(tmp_path, monkeypatch):
    doc = {
        "suite": "capability-scorecard",
        "version": "0.0.0-test",
        "scoring": json.loads(json.dumps(DOC["scoring"])),
        "capabilities": [
            {
                "id": "e2e.red",
                "category": "integrity",
                "weight": 1,
                "ours": {"proof_tests": ["tests/test_domain.py::test_no_such_proof_zzz"]},
                "opponents": {
                    "codegraph": {
                        "status": "absent",
                        "evidence": _EV,
                    },
                    "graphify": {
                        "status": "absent",
                        "evidence": _EV,
                    },
                },
            }
        ],
    }
    caps = tmp_path / "capabilities.json"
    caps.write_text(json.dumps(doc))
    monkeypatch.setattr(scorecard, "CAPABILITIES_PATH", caps)
    monkeypatch.setattr(scorecard, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(scorecard, "EXPECTED_CATEGORIES", ("integrity",))
    monkeypatch.setattr(scorecard, "MIN_CAPABILITIES", 1)
    monkeypatch.setenv("KYS_COMPETITIVE_LEDGER", str(tmp_path / "absent.json"))
    assert scorecard.cmd_run() == 1
