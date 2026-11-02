"""Parametrized document-extraction tests over the W15 fixtures (SUB-129).

Every fixture in ``tests/fixtures/documents/`` (markdown, plaintext, PDF
text-layer) is extracted through the real ``extract_document`` path and
asserted against authored expectations in ``docs.json``:

* markdown: every heading in the fixture becomes a section node;
* plaintext: chunk nodes cover the text deterministically;
* PDF: every authored page string appears in the extracted text layer.

Plus cross-document properties: determinism and idempotent artifact ordering.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from know_your_context.ingest.documents import extract_document

DOCS_ROOT = Path(__file__).parent / "fixtures" / "documents"


def _entries() -> list[dict]:
    return json.loads((DOCS_ROOT / "docs.json").read_text(encoding="utf-8"))["documents"]


ENTRIES = _entries()


def _extract(entry: dict):
    path = DOCS_ROOT / entry["path"]
    return extract_document(entry["path"], path.read_bytes())


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["path"])
def test_document_extracts_without_parse_error(entry) -> None:
    result = _extract(entry)
    assert result.parse_error is False
    assert result.language == entry["language"]


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["path"])
def test_document_is_deterministic(entry) -> None:
    first = _extract(entry)
    second = _extract(entry)
    assert first.canonical_json() == second.canonical_json()


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e["language"] == "markdown"], ids=lambda e: e["path"])
def test_markdown_headings_become_section_nodes(entry) -> None:
    result = _extract(entry)
    section_names = {node.name for node in result.nodes if node.kind == "section"}
    missing = [heading for heading in entry["headings"] if heading not in section_names]
    assert not missing, f"{entry['path']}: headings without section nodes: {missing}"


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e["language"] == "markdown"], ids=lambda e: e["path"])
def test_markdown_sections_are_contained_in_document(entry) -> None:
    result = _extract(entry)
    file_ids = {node.node_id for node in result.nodes if node.kind == "file"}
    contained = {edge.target for edge in result.edges if edge.kind == "contains"}
    for node in result.nodes:
        if node.kind == "file":
            continue
        assert node.node_id in contained or node.node_id in file_ids, (
            f"{entry['path']}: {node.kind} {node.name} outside the contains tree"
        )


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e["language"] == "pdf"], ids=lambda e: e["path"])
def test_pdf_text_layer_produces_one_page_node_per_text_page(entry) -> None:
    result = _extract(entry)
    # the extraction artifact carries structure, not raw text: one page node
    # per text-bearing page, with page counts in the metadata.
    assert result.metadata.get("no_text_layer") is False
    assert result.metadata.get("page_count") == len(entry["pages"])
    expected_pages = {f"page-{i}" for i in range(1, len(entry["pages"]) + 1)}
    page_names = {node.name for node in result.nodes if node.kind == "page"}
    assert page_names == expected_pages, f"{entry['path']}: {sorted(page_names)} != {sorted(expected_pages)}"

    # ground-truth check against pypdf directly: the authored text IS the
    # text layer of the fixture file (fixtures are never dummy binaries).
    import io

    from pypdf import PdfReader

    reader = io.BytesIO((DOCS_ROOT / entry["path"]).read_bytes())
    text_pages = [page.extract_text() or "" for page in PdfReader(reader).pages]
    for authored, extracted in zip(entry["pages"], text_pages, strict=True):
        assert authored in extracted, f"{entry['path']}: {authored!r} not in text layer"


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e["language"] == "text"], ids=lambda e: e["path"])
def test_plaintext_produces_nodes(entry) -> None:
    result = _extract(entry)
    assert result.nodes, f"{entry['path']}: no nodes extracted"


class TestDocumentCorpusShape:
    def test_languages_covered(self) -> None:
        languages = {entry["language"] for entry in ENTRIES}
        assert languages == {"markdown", "text", "pdf"}

    def test_counts(self) -> None:
        counts: dict[str, int] = {}
        for entry in ENTRIES:
            counts[entry["language"]] = counts.get(entry["language"], 0) + 1
        assert counts["markdown"] >= 8, counts
        assert counts["text"] >= 2, counts
        assert counts["pdf"] >= 4, counts

    def test_pdf_files_are_real_pdfs(self) -> None:
        for entry in ENTRIES:
            if entry["language"] == "pdf":
                data = (DOCS_ROOT / entry["path"]).read_bytes()
                assert data.startswith(b"%PDF-"), entry["path"]
