"""CLI tests for the codeintel ops command (W15, SUB-130).

Pins ``know-your-context codeintel ...`` end-to-end through ``cli.main``:
exit codes, canonical-JSON stdout, typed error envelopes on stderr, the
read-only store adapter, the source-version co-change adapter and the
OPS-only rule (``codeintel`` never joins the frozen knowledge/OPS
operations).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from know_your_context.cli import main
from know_your_context.http_api import KNOWLEDGE_OPERATIONS, OPS_OPERATIONS
from know_your_context.ops.codeintel_cli import (
    _parse_flows,
    _parse_layers,
    change_sets_from_source_versions,
    load_code_graph,
)
from know_your_context.store import EventStore


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict | None, str]:
    code = main(argv)
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else None
    return code, payload, captured.err


def _ok(payload: dict | None) -> dict:
    assert payload is not None
    return payload


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "codeintel.sqlite3"
    store = EventStore(path)
    store.upsert_symbol(node_id="n:a", name="alpha", kind="function", path="core/f.py", line=1, col=0)
    store.upsert_symbol(node_id="n:b", name="beta", kind="function", path="core/f.py", line=5, col=0)
    store.upsert_symbol(node_id="n:c", name="gamma", kind="function", path="ui/g.py", line=2, col=0)
    store.upsert_symbol(node_id="n:file", name="f.py", kind="file", path="core/f.py", line=1, col=0)
    store.add_graph_edge("n:a", "n:b", "calls", line=3)
    store.add_graph_edge("n:b", "n:c", "calls", line=7)
    store.add_graph_edge("n:file", "n:a", "contains", line=1)
    store.close()
    return path


def _argv(store: Path, *rest: str) -> list[str]:
    return ["--store", str(store), "codeintel", *rest]


# ------------------------------------------------------------------ adapters


class TestLoadCodeGraph:
    def test_reads_symbols_and_edges(self, store_path):
        store = EventStore(store_path)
        try:
            graph = load_code_graph(store)
        finally:
            store.close()
        assert len(graph.nodes) == 4
        assert len(graph.edges) == 3
        assert graph.dropped_edges == 0

    def test_edges_with_unknown_endpoints_dropped(self, store_path):
        store = EventStore(store_path)
        try:
            store.add_graph_edge("n:a", "n:ghost", "calls", line=9)
            graph = load_code_graph(store)
        finally:
            store.close()
        assert graph.dropped_edges == 1


class TestChangeSets:
    def _insert_version(self, store: EventStore, version_id: str, manifest: list[dict], observed: str) -> None:
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO source_version (source_version_id, source_id, source_uri, content_sha256, "
                "manifest_hash, fingerprint, byte_length, char_length, line_count, file_count, observed_at, "
                "parser_version, normalization_version, engine_version, scoring_contract_version, "
                "source_state, manifest_json) VALUES (?, 'src', 'uri', 'h', 'mh', ?, 0, 0, 0, 0, ?, "
                "'p', 'n', 'e', 's', 'CURRENT', ?)",
                (version_id, f"fp-{version_id}", observed, json.dumps(manifest)),
            )

    def test_consecutive_manifest_diff(self, tmp_path):
        store = EventStore(tmp_path / "sv.sqlite3")
        try:
            manifest_v1 = [{"path": "a.py", "sha256": "1"}, {"path": "b.py", "sha256": "2"}]
            self._insert_version(store, "v1", manifest_v1, "2026-01-01T00:00:00+00:00")
            self._insert_version(
                store,
                "v2",
                [{"path": "a.py", "sha256": "9"}, {"path": "b.py", "sha256": "2"}, {"path": "c.py", "sha256": "3"}],
                "2026-01-02T00:00:00+00:00",
            )
            sets = change_sets_from_source_versions(store)
        finally:
            store.close()
        assert sets == (("a.py", "c.py"),)

    def test_unchanged_manifest_yields_no_set(self, tmp_path):
        store = EventStore(tmp_path / "sv2.sqlite3")
        try:
            manifest = [{"path": "a.py", "sha256": "1"}]
            self._insert_version(store, "v1", manifest, "2026-01-01T00:00:00+00:00")
            self._insert_version(store, "v2", manifest, "2026-01-02T00:00:00+00:00")
            assert change_sets_from_source_versions(store) == ()
        finally:
            store.close()

    def test_corrupt_manifest_skipped(self, tmp_path):
        store = EventStore(tmp_path / "sv3.sqlite3")
        try:
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO source_version (source_version_id, source_id, source_uri, content_sha256, "
                    "manifest_hash, fingerprint, byte_length, char_length, line_count, file_count, observed_at, "
                    "parser_version, normalization_version, engine_version, scoring_contract_version, "
                    "source_state, manifest_json) VALUES ('vx', 'src', 'uri', 'h', 'mh', 'fp', 0, 0, 0, 0, "
                    "'2026-01-01T00:00:00+00:00', 'p', 'n', 'e', 's', 'CURRENT', 'not-json')",
                )
            assert change_sets_from_source_versions(store) == ()
        finally:
            store.close()

    def test_removed_paths_count_as_changed(self, tmp_path):
        store = EventStore(tmp_path / "sv4.sqlite3")
        try:
            manifest_v1 = [{"path": "a.py", "sha256": "1"}, {"path": "b.py", "sha256": "2"}]
            self._insert_version(store, "v1", manifest_v1, "2026-01-01T00:00:00+00:00")
            self._insert_version(store, "v2", [{"path": "a.py", "sha256": "1"}], "2026-01-02T00:00:00+00:00")
            assert change_sets_from_source_versions(store) == (("b.py",),)
        finally:
            store.close()


# ------------------------------------------------------------------ parsers


class TestLayerParsers:
    def test_parse_layers_ok(self):
        assert _parse_layers("ui:ui,web;core:core") == {"ui": ("ui", "web"), "core": ("core",)}

    def test_parse_layers_missing_colon(self):
        assert isinstance(_parse_layers("ui"), str)

    def test_parse_layers_empty_name(self):
        assert isinstance(_parse_layers(":ui"), str)

    def test_parse_layers_duplicate(self):
        assert isinstance(_parse_layers("ui:a;ui:b"), str)

    def test_parse_layers_empty(self):
        assert isinstance(_parse_layers("  "), str)

    def test_parse_flows_ok(self):
        assert _parse_flows("ui:core,core:data") == [("ui", "core"), ("core", "data")]

    def test_parse_flows_bad(self):
        assert isinstance(_parse_flows("uicore"), str)

    def test_parse_flows_empty_ok(self):
        assert _parse_flows("") == []


# ------------------------------------------------------------------ end-to-end


class TestCliTraversal:
    def test_callers_of(self, store_path, capsys):
        code, payload, err = _invoke(_argv(store_path, "callers-of", "gamma"), capsys)
        assert code == 0
        assert _ok(payload)["kind"] == "analytics.code_intel.callers_of"
        names = [entry["name"] for entry in _ok(payload)["entries"]]
        assert names == ["beta", "alpha"]

    def test_callees_of(self, store_path, capsys):
        code, payload, _ = _invoke(_argv(store_path, "callees-of", "alpha"), capsys)
        assert code == 0
        assert [entry["name"] for entry in _ok(payload)["entries"]] == ["beta", "gamma"]

    def test_callers_depth_cap(self, store_path, capsys):
        code, payload, _ = _invoke(_argv(store_path, "callers-of", "gamma", "--depth", "1"), capsys)
        assert code == 0
        assert [entry["name"] for entry in _ok(payload)["entries"]] == ["beta"]

    def test_unknown_symbol_exit_1(self, store_path, capsys):
        code, payload, err = _invoke(_argv(store_path, "callers-of", "ghost"), capsys)
        assert code == 1
        assert payload is None
        envelope = json.loads(err)
        assert envelope["error"]["code"] == "CODEINTEL_QUERY"
        assert "unknown symbol" in envelope["error"]["message"]

    def test_depth_out_of_range_exit_1(self, store_path, capsys):
        code, _, err = _invoke(_argv(store_path, "callees-of", "alpha", "--depth", "99"), capsys)
        assert code == 1
        assert "max_depth" in json.loads(err)["error"]["message"]

    def test_by_node_id(self, store_path, capsys):
        code, payload, _ = _invoke(_argv(store_path, "callees-of", "n:a"), capsys)
        assert code == 0
        assert _ok(payload)["root"]["node_id"] == "n:a"


class TestCliDependencyPath:
    def test_path_found(self, store_path, capsys):
        code, payload, _ = _invoke(_argv(store_path, "dependency-path", "alpha", "gamma"), capsys)
        assert code == 0
        assert _ok(payload)["paths"][0]["node_ids"] == ["n:a", "n:b", "n:c"]

    def test_no_path_exit_0(self, store_path, capsys):
        code, payload, _ = _invoke(_argv(store_path, "dependency-path", "gamma", "alpha"), capsys)
        assert code == 0
        assert _ok(payload)["path_count"] == 0


class TestCliImpact:
    def test_impact(self, store_path, capsys):
        code, payload, _ = _invoke(_argv(store_path, "impact", "gamma"), capsys)
        assert code == 0
        assert _ok(payload)["direct_dependents"] == 1
        assert _ok(payload)["total_impacted"] == 3  # beta(d1) + alpha, f.py(d2)

    def test_impact_artifact_kind(self, store_path, capsys):
        _, payload, _ = _invoke(_argv(store_path, "impact", "beta"), capsys)
        assert _ok(payload)["kind"] == "analytics.code_intel.impact_analysis"


class TestCliCoChange:
    def test_empty_history(self, store_path, capsys):
        code, payload, _ = _invoke(_argv(store_path, "co-change"), capsys)
        assert code == 0
        assert _ok(payload)["pair_count"] == 0
        assert _ok(payload)["change_set_count"] == 0

    def test_with_history(self, store_path, capsys):
        store = EventStore(store_path)
        helper = TestChangeSets()
        try:
            for index in range(3):
                helper._insert_version(
                    store,
                    f"v{index}",
                    [{"path": "a.py", "sha256": str(index)}, {"path": "b.py", "sha256": str(index)}],
                    f"2026-01-0{index + 1}T00:00:00+00:00",
                )
        finally:
            store.close()
        code, payload, _ = _invoke(_argv(store_path, "co-change", "--min-support", "1"), capsys)
        assert code == 0
        assert _ok(payload)["pair_count"] == 1
        assert _ok(payload)["pairs"][0]["support"] == 2


class TestCliBoundary:
    def test_violation_reported(self, store_path, capsys):
        # ui/g.py (gamma) is called BY core beta? edge n:b->n:c means core -> ui
        code, payload, _ = _invoke(
            _argv(store_path, "boundary-violations", "--layers", "core:core;ui:ui", "--allow", "core:ui"), capsys
        )
        assert code == 0
        assert _ok(payload)["total_violations"] == 0  # core->ui explicitly allowed

    def test_violation_when_not_allowed(self, store_path, capsys):
        code, payload, _ = _invoke(_argv(store_path, "boundary-violations", "--layers", "core:core;ui:ui"), capsys)
        assert code == 0
        assert _ok(payload)["total_violations"] == 1
        assert _ok(payload)["violations"][0]["from_layer"] == "core"
        assert _ok(payload)["violations"][0]["to_layer"] == "ui"

    def test_bad_layer_spec_exit_2(self, store_path, capsys):
        code, _, err = _invoke(_argv(store_path, "boundary-violations", "--layers", "bogus"), capsys)
        assert code == 2
        assert json.loads(err)["error"]["code"] == "CLI_USAGE"

    def test_bad_flow_spec_exit_2(self, store_path, capsys):
        code, _, err = _invoke(
            _argv(store_path, "boundary-violations", "--layers", "core:core", "--allow", "bad"), capsys
        )
        assert code == 2

    def test_unknown_layer_in_flow_exit_1(self, store_path, capsys):
        code, _, err = _invoke(
            _argv(store_path, "boundary-violations", "--layers", "core:core", "--allow", "core:ghost"), capsys
        )
        assert code == 1
        assert "unknown layers" in json.loads(err)["error"]["message"]


class TestOpsOnly:
    def test_not_a_knowledge_operation(self):
        assert "codeintel" not in KNOWLEDGE_OPERATIONS
        assert "codeintel" not in OPS_OPERATIONS
