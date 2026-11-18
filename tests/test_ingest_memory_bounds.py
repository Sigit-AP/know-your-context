"""Memory-bound gates for the ingestion pipeline (SUB-186).

The walk consumes content in canonical order and must release each file's
bytes and extraction result once its rows are committed: peak memory is
bounded by the in-flight window, never by corpus size. These tests pin the
bound (traced allocations, deterministic) and the byte-identical output of
the streamed path versus the parallel path.
"""

from __future__ import annotations

import tracemalloc

import pytest

from know_your_context.ingest import pipeline as _pipeline_module
from know_your_context.ingest.pipeline import IngestionPipeline, canonical_ingestion_dump
from know_your_context.store import EventStore

FILE_BYTES = 64 * 1024
FILE_COUNT = 300


def _module_text(index: int, size: int = FILE_BYTES) -> str:
    """One tiny function plus one long comment line — content-heavy, AST-light.

    Padding stays on a single line on purpose: the extractor's per-line cost is
    the sibling module's business, and this gate measures retention of bytes.
    """
    head = f'"""Module {index} padding corpus (deterministic)."""\ndef fn_{index}(value):\n    return value + 1\n'
    body = "# " + ("x" * (size - len(head) - 2)) + "\n"
    return head + body


def _write_corpus(root, count: int = FILE_COUNT) -> int:
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    for index in range(count):
        path = root / f"mod{index:04d}.py"
        text = _module_text(index)
        path.write_text(text, encoding="utf-8")
        total += len(text.encode("utf-8"))
    return total


def _legacy_pipeline() -> IngestionPipeline:
    """Historical sequential recipe: 1 extraction worker, per-file commits."""
    store = EventStore(":memory:")
    store.migrate()
    return IngestionPipeline(store)


def test_peak_traced_memory_is_bounded_by_window_not_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
    corpus = tmp_path / "corpus"
    total_bytes = _write_corpus(corpus)
    pipeline = _legacy_pipeline()

    tracemalloc.start()
    try:
        report = pipeline.ingest_path(corpus)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert report.stats["proposed"] == FILE_COUNT
    assert total_bytes > 8 * 1024 * 1024  # the corpus is several windows large
    # Holding every file's bytes (the historical behavior) peaks at or above
    # the corpus size; a streaming walk never holds half of it at once. The
    # residual growth is the primary-split proposal list, which is O(nodes)
    # by design (it feeds the proposal summary) and tiny next to the bytes.
    assert peak < total_bytes // 2, f"peak traced memory {peak / 1048576:.1f} MiB not bounded"


def _stable_report(report) -> dict:
    """The report minus the two volatile fields (audit id, wall-clock ms)."""
    payload = report.to_dict()
    payload.pop("audit_event_id", None)
    payload.pop("duration_ms", None)
    return payload


def test_streamed_ingest_output_is_identical_to_the_parallel_path(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    _write_corpus(corpus, count=40)

    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
    serial_store = EventStore(":memory:")
    serial_store.migrate()
    serial_report = IngestionPipeline(serial_store).ingest_path(corpus)
    serial_dump = canonical_ingestion_dump(serial_store)

    monkeypatch.delenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", raising=False)
    monkeypatch.delenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", raising=False)
    parallel_store = EventStore(":memory:")
    parallel_store.migrate()
    parallel_report = IngestionPipeline(parallel_store).ingest_path(corpus)
    parallel_dump = canonical_ingestion_dump(parallel_store)

    assert _stable_report(serial_report) == _stable_report(parallel_report)
    assert serial_dump == parallel_dump


@pytest.mark.parametrize("count", [1, 7])
def test_small_corpora_and_documents_keep_the_bound(tmp_path, monkeypatch, count):
    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
    corpus = tmp_path / "corpus"
    total_bytes = _write_corpus(corpus, count=count)
    pipeline = _legacy_pipeline()

    tracemalloc.start()
    try:
        report = pipeline.ingest_path(corpus)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert report.stats["proposed"] == count
    assert peak < max(total_bytes, 1024 * 1024)


# ---------------------------------------------------------------------------
# SUB-185: the resolve pass must run in bounded batches.
#
# ``ReferenceResolver.resolve_pending`` pulls the whole pending queue (its
# ``limit`` default is ``None`` = everything), then materialises decisions +
# edge payloads + recorded rows + a read-back report for every ref in the
# queue — O(pending) transient memory on top of whatever the commit phase
# left alive (measured +119 MiB traced at 60k pending refs). The pipeline
# owns the pass invocation, so the bound lives here: chunked calls with a
# pinned batch cap, aggregated into the exact same report mapping.
# ---------------------------------------------------------------------------


def _refs_module_text(index: int, count: int = 120) -> str:
    """One tiny module with cross-file refs — imports + a call to a sibling's
    function (all real ``unresolved_refs`` kinds for the synthetic resolver
    surface: one module-level import, one named import, one call)."""
    other = (index + 1) % count
    third = (index + 7) % count
    return (
        f'"""Module {index} ref-rich corpus (deterministic)."""\n'
        f"import mod{other:04d}\n"
        f"from mod{third:04d} import fn_target\n"
        f"def fn_{index}(value):\n"
        f"    return fn_target(value) + mod{other:04d}.helper(value)\n"
    )


def _write_refs_corpus(root, count: int = 120) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (root / f"mod{index:04d}.py").write_text(_refs_module_text(index, count), encoding="utf-8")


def test_resolve_pass_chunks_the_pending_queue(tmp_path, monkeypatch):
    """Every resolve read is bounded by the batch cap; at least two chunks run."""
    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
    monkeypatch.setattr(_pipeline_module, "RESOLVE_BATCH", 64)
    corpus = tmp_path / "corpus"
    _write_refs_corpus(corpus, count=120)

    store = EventStore(":memory:")
    store.migrate()
    real_fetch = store.pending_unresolved_refs
    requested: list[int] = []
    seen_limits_none: list[bool] = []

    def recording_fetch(*, limit=None):
        if limit is None:
            seen_limits_none.append(True)
        else:
            requested.append(int(limit))
        return real_fetch(limit=limit)

    monkeypatch.setattr(store, "pending_unresolved_refs", recording_fetch)

    report = IngestionPipeline(store).ingest_path(corpus, incremental=True, recursive=True, resolve=True)

    assert report.stats["proposed"] == 120
    assert report.resolution is not None
    assert report.resolution["pending"] == 0
    # The pass must actually fan out: more refs than one batch, and never a
    # single unbounded pull.
    assert not seen_limits_none, "resolve pass called the pending queue without a limit cap"
    assert len(requested) >= 2, f"expected >=2 chunked calls, got {len(requested)}"
    assert max(requested) <= 64, f"chunk {max(requested)} exceeds the RESOLVE_BATCH cap"


def test_resolve_chunked_output_is_batch_size_independent(tmp_path, monkeypatch):
    """Aggregation must not depend on the batch size: 17-ref chunks and one
    big batch produce the same report, the same store rows (rowids included)
    and the same canonical dump."""
    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER", "1")
    monkeypatch.setenv("KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING", "1")
    corpus = tmp_path / "corpus"
    _write_refs_corpus(corpus, count=150)

    dumps: list[str] = []
    reports: list[dict] = []
    for batch in (17, 4096):
        monkeypatch.setattr(_pipeline_module, "RESOLVE_BATCH", batch)
        store = EventStore(":memory:")
        store.migrate()
        report = IngestionPipeline(store).ingest_path(corpus, incremental=True, recursive=True, resolve=True)
        assert report.resolution is not None
        assert report.resolution["pending"] == 0
        reports.append(_stable_report(report))
        dumps.append(canonical_ingestion_dump(store))

    assert reports[0] == reports[1]
    assert dumps[0] == dumps[1], "chunked resolve aggregation is not batch-size independent"
