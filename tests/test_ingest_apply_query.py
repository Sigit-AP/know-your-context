"""B1 (SUB-173): the official ingest → apply → query path.

Measured baseline (stress-3-products §6 B1): after ``ingest`` of 5,137 files
(exit 0) the store held 181,845 symbols but ``node_revision=0`` /
``node_relation=0``, so every knowledge ``query`` returned
``routing=no_match, results=[]`` (0/4, 0/20, 0/50, 0/50 hits across the four
stress levels) while ``codeintel`` worked on the same store. There was no
official step that turned the ingested corpus into knowledge: the CLI reported
``status="proposal"`` and stopped.

``write-contract-v5`` (ACTIVE) says the write lane applies directly; this
session adds the missing step as an explicit operator lane —
``ingest --apply`` / ``apply-ingest`` — so the gap closes without touching any
frozen operation's semantics.

These tests pin the whole path, not just the function: ingest a real corpus
through the CLI's pipeline, prove ``query`` is blind before the apply, then
prove it finds the corpus's symbols afterwards.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from know_your_context.contracts import QueryRequest
from know_your_context.ingest.apply import apply_ingested_corpus
from know_your_context.ingest.pipeline import IngestionBudget, IngestionPipeline
from know_your_context.projection import GraphProjector
from know_your_context.service import KnowledgeService
from know_your_context.store import EventStore

CORPUS = {
    "alpha.py": (
        "class Widget:\n"
        "    def render(self):\n"
        "        return 'widget'\n"
        "\n"
        "def build_widget(rows):\n"
        "    return [Widget() for _ in rows]\n"
    ),
    "beta.py": (
        "from alpha import Widget\n\ndef summarise(items):\n    return [Widget().render() for item in items]\n"
    ),
    "gamma.py": (
        "class Ledger:\n"
        "    def post(self, entry):\n"
        "        return entry\n"
        "\n"
        "def reconcile(entries):\n"
        "    return [Ledger().post(entry) for entry in entries]\n"
    ),
    "delta.py": "def ping():\n    return 'pong'\n",
    "epsilon.py": "class Cache:\n    def get(self, key):\n        return key\n",
    "zeta.py": "def helper():\n    return 42\n",
}


def _write_corpus(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, source in CORPUS.items():
        (root / name).write_text(source, encoding="utf-8")
    return root


def _ingest(tmp_path: Path, corpus: Path) -> tuple[EventStore, Path]:
    store_path = tmp_path / "soul.sqlite3"
    store = EventStore(store_path)
    pipeline = IngestionPipeline(
        store,
        budget=IngestionBudget(max_files=65536, max_nodes=100_000, max_edges=100_000, max_refs=100_000),
        actor="test",
    )
    report = pipeline.ingest_path(corpus, incremental=True, recursive=True, resolve=True)
    assert report.stats["proposed"] == len(CORPUS)
    return store, store_path


def _counts(store_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(store_path)
    try:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 - fixed names
            for table in ("files", "symbols", "node_revision", "node_relation")
        }
    finally:
        conn.close()


def test_query_is_blind_before_apply_and_sees_the_corpus_after(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path / "corpus")
    store, store_path = _ingest(tmp_path, corpus)
    projector = GraphProjector(store, output_root=tmp_path / "out")
    service = KnowledgeService(store, projector)

    before = _counts(store_path)
    assert before["symbols"] > 0, "the ingest must have written the code index"
    assert before["node_revision"] == 0, "no knowledge nodes yet — the projection is empty"
    blind = service.query(QueryRequest(purpose="Widget", token_budget=1024))
    # SUB-177: pre-apply the query is no longer blind — the code-index
    # fallback answers from the ingested symbols (routing tells the caller
    # which lane answered). The apply lane below remains the step that
    # materialises the corpus as scored knowledge nodes.
    assert blind.routing == "code_index_fallback"
    assert blind.results, "the code index must answer pre-apply"
    assert blind.required_knowledge_watermark == 0

    receipt = apply_ingested_corpus(store, corpus_root=corpus, projector=projector)
    assert receipt.files_applied == len(CORPUS)
    assert receipt.symbol_nodes >= len(CORPUS)
    after = _counts(store_path)
    # The apply writes the roots (KROOT + the seven primaries) plus one branch
    # per file and one leaf per symbol; the receipt counts the corpus nodes.
    assert after["node_revision"] - before["node_revision"] >= receipt.nodes_applied
    assert after["node_relation"] > 0

    response = service.query(QueryRequest(purpose="Widget", token_budget=1024))
    assert response.stale is False
    summaries = {node.summary for node in response.results}
    assert "Widget" in summaries, f"query must find the ingested class, got {sorted(summaries)}"
    assert response.required_knowledge_watermark == receipt.knowledge_watermark
    store.close()


def test_apply_is_idempotent_and_deterministic(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path / "corpus")
    store, store_path = _ingest(tmp_path, corpus)
    projector = GraphProjector(store, output_root=tmp_path / "out")
    first = apply_ingested_corpus(store, corpus_root=corpus, projector=projector)
    ids_after_first = _node_ids(store_path)
    second = apply_ingested_corpus(store, corpus_root=corpus, projector=projector)
    assert second.nodes_applied == first.nodes_applied
    assert second.replayed == first.nodes_applied, "a re-apply replays, it never duplicates"
    assert _node_ids(store_path) == ids_after_first
    assert second.knowledge_watermark == first.knowledge_watermark
    store.close()


def test_apply_is_byte_identical_across_fresh_stores(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path / "corpus")
    reports = []
    id_sets = []
    for run in ("a", "b"):
        store, store_path = _ingest(tmp_path / run, corpus)
        projector = GraphProjector(store, output_root=tmp_path / run / "out")
        receipt = apply_ingested_corpus(store, corpus_root=corpus, projector=projector)
        reports.append(receipt.to_dict())
        id_sets.append(_node_ids(store_path))
        store.close()
    assert id_sets[0] == id_sets[1]
    assert reports[0]["file_nodes"] == reports[1]["file_nodes"]
    assert reports[0]["symbol_nodes"] == reports[1]["symbol_nodes"]
    assert reports[0]["nodes_applied"] == reports[1]["nodes_applied"]


def _node_ids(store_path: Path) -> list[str]:
    conn = sqlite3.connect(store_path)
    try:
        return [str(row[0]) for row in conn.execute("SELECT node_id FROM node_revision ORDER BY node_id")]
    finally:
        conn.close()


def test_apply_cli_command_and_ingest_apply_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from know_your_context.cli import main

    corpus = _write_corpus(tmp_path / "corpus")
    store_path = tmp_path / "cli.sqlite3"
    out_root = tmp_path / "cli-out"
    code = main(
        [
            "--store",
            str(store_path),
            "--output-root",
            str(out_root),
            "ingest",
            str(corpus),
            "--apply",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "applied"
    assert payload["apply"]["files_applied"] == len(CORPUS)
    assert payload["apply"]["nodes_applied"] > 0

    # A standalone `apply-ingest` run over the same store replays, never duplicates.
    code = main(["--store", str(store_path), "--output-root", str(out_root), "apply-ingest", "--json"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    replay = json.loads(captured.out)
    assert replay["replayed"] == replay["nodes_applied"]

    # And the query really answers from the corpus.
    code = main(
        [
            "--store",
            str(store_path),
            "--output-root",
            str(out_root),
            "query",
            '{"purpose": "Widget", "token_budget": 1024}',
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    answer = json.loads(captured.out)
    assert answer["results"], "query must see the applied corpus"
    assert any(node["summary"] == "Widget" for node in answer["results"])
