"""SUB-177: ``query`` falls back to the ingested code index on ``no_match``.

Measured baseline (stress3/results2.jsonl, harness2.py): ``ingest`` writes the
code tables (``files``/``symbols``/``graph_edges``) and stops; the knowledge
projection stays empty until an explicit ``ingest --apply``. A plain
``ingest`` → ``query`` flow — exactly what ``codegraph init`` → ``query`` and
``graphify extract`` → ``query`` already support — returned
``routing=no_match, results=[]`` for every level: 0/4, 0/20, 0/50, 0/50 W2
hits where both competitors scored 100%.

Fix shape (additive, no frozen contract touched): when the router reports
``no_match``/``ambiguous`` AND the store holds an ingested code index, the
service answers from that index (lexical symbol match, deterministic rank,
budget-fitted) and reports ``routing="code_index_fallback"``. The knowledge
projection still wins whenever it has a match; an empty code index keeps the
old typed ``no_match``. ``access_only`` is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from know_your_context.contracts import QueryRequest
from know_your_context.ingest.apply import apply_ingested_corpus
from know_your_context.projection import GraphProjector
from know_your_context.service import KnowledgeService
from know_your_context.store import EventStore
from test_ingest_apply_query import _ingest, _write_corpus


def _service(tmp_path: Path) -> tuple[KnowledgeService, EventStore]:
    corpus = _write_corpus(tmp_path / "corpus")
    store, _ = _ingest(tmp_path, corpus)
    projector = GraphProjector(store, output_root=tmp_path / "out")
    return KnowledgeService(store, projector), store


def test_no_match_falls_back_to_code_index(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    response = service.query(QueryRequest(purpose="Widget", token_budget=1024))
    assert response.routing == "code_index_fallback"
    assert response.results, "the ingested code index must answer when the projection cannot"
    names = {node.summary.split(" — ")[0] for node in response.results}
    assert "Widget" in names, f"the ingested class must be found, got {sorted(names)}"
    assert all(node.version == 1 for node in response.results)
    assert response.estimated_tokens <= 1024
    assert response.knowledge_payload_tokens > 0
    assert response.stale is False
    store.close()


def test_fallback_exact_match_ranks_first(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    response = service.query(QueryRequest(purpose="build_widget", token_budget=1024))
    assert response.routing == "code_index_fallback"
    assert response.results[0].summary.startswith("build_widget"), (
        f"an exact name hit must lead, got {response.results[0].summary}"
    )
    store.close()


def test_fallback_substring_match(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    response = service.query(QueryRequest(purpose=" reconcile entries ", token_budget=1024))
    assert response.routing == "code_index_fallback"
    names = {node.summary.split(" — ")[0] for node in response.results}
    assert "reconcile" in names
    store.close()


def test_no_match_still_typed_when_code_index_has_no_hit(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    response = service.query(QueryRequest(purpose="zzzqqq-nothing-matches", token_budget=256))
    assert response.routing == "no_match"
    assert response.results == ()
    assert response.knowledge_payload_tokens == 0
    store.close()


def test_no_fallback_without_code_index(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "empty.sqlite3")
    service = KnowledgeService(store, GraphProjector(store, output_root=tmp_path / "out"))
    response = service.query(QueryRequest(purpose="Widget", token_budget=512))
    assert response.routing == "no_match"
    assert response.results == ()
    store.close()


def test_projection_match_wins_over_fallback(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path / "corpus")
    store, _ = _ingest(tmp_path, corpus)
    projector = GraphProjector(store, output_root=tmp_path / "out")
    apply_ingested_corpus(store, corpus_root=corpus, projector=projector)
    service = KnowledgeService(store, projector)
    response = service.query(QueryRequest(purpose="Widget", token_budget=1024))
    assert response.routing in {"direct", "ranked"}, (
        f"once applied, the knowledge projection answers, got {response.routing}"
    )
    assert response.results
    store.close()


def test_fallback_is_deterministic(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    first = service.query(QueryRequest(purpose="Widget", token_budget=1024))
    second = service.query(QueryRequest(purpose="Widget", token_budget=1024))
    assert [n.model_dump(mode="json") for n in first.results] == [n.model_dump(mode="json") for n in second.results]
    assert first.routing == second.routing == "code_index_fallback"
    store.close()


def test_fallback_respects_token_budget(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    # "i" substrings almost every symbol in the corpus; a tight budget must
    # still fit — the service drops trailing results and flags truncation.
    response = service.query(QueryRequest(purpose="e", token_budget=256))
    assert response.routing == "code_index_fallback"
    assert response.estimated_tokens <= 256
    if len(response.results) > 1:
        assert response.truncated is True
    store.close()


def test_access_only_skips_fallback(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    response = service.query(QueryRequest(purpose="Widget", token_budget=512, access_only=True))
    assert response.routing == "access_only"
    assert response.results == ()
    store.close()


def test_fallback_skips_substring_scan_when_exact_hits_exist(tmp_path: Path) -> None:
    """Storm cost (L3, 50 agents): 46/50 fallback queries timed out at 120 s.

    Root cause: the fallback ran the table-scanning substring stage for every
    token even when the exact-name stage had already answered — 4 scans x 60k
    rows per query, times 50 processes on 2 cores. The substring stage is a
    recall net for when exact matching finds nothing; pin that it stays off
    the exact-hit path.
    """
    service, store = _service(tmp_path)
    calls: list[str] = []
    original = store.symbols_containing

    def spy(fragment: str, *, limit: int = 64):  # noqa: ANN001 - test seam
        calls.append(fragment)
        return original(fragment, limit=limit)

    store.symbols_containing = spy  # type: ignore[method-assign]
    exact = service.query(QueryRequest(purpose="Widget", token_budget=1024))
    assert exact.routing == "code_index_fallback" and exact.results
    assert calls == [], f"exact hits must not trigger the substring scan, got {calls}"
    miss = service.query(QueryRequest(purpose="idget", token_budget=1024))
    assert miss.routing == "code_index_fallback" and miss.results
    assert calls == ["idget"], "the substring net fires only when exact matching is empty"
    store.close()
