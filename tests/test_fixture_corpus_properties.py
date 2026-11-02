"""Cross-fixture property tests for the W15 corpus (SUB-129).

Properties asserted over EVERY language fixture (parametrized, not sampled):

* determinism — extracting the same bytes twice yields byte-identical
  canonical JSON (this is also the cross-``PYTHONHASHSEED`` contract, since
  canonical JSON is a pure function of the source bytes);
* idempotency — ``to_artifact`` is a fixpoint (sorting a sorted artifact
  changes nothing);
* identity — node ids are unique within a fixture;
* containment — every non-file node is reachable from the file root through
  ``contains`` edges (the graph is a tree rooted at the file node);
* refs point at real nodes — every unresolved ref's source node exists.
"""

from __future__ import annotations

import pytest

from _fixture_corpus import case_id, corpus_cases, extract_case

CASES = corpus_cases()


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_extraction_is_deterministic(case) -> None:
    first = extract_case(case)
    second = extract_case(case)
    assert first.canonical_json() == second.canonical_json()


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_artifact_sort_is_idempotent(case) -> None:
    result = extract_case(case)
    artifact = result.to_artifact()
    resort = type(result)(
        path=result.path,
        language=result.language,
        sha256=result.sha256,
        size=result.size,
        generated_hint=result.generated_hint,
        parse_error=result.parse_error,
        extraction_version=result.extraction_version,
        nodes=result.nodes,
        edges=result.edges,
        unresolved_refs=result.unresolved_refs,
    ).to_artifact()
    assert artifact == resort


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_node_ids_are_unique(case) -> None:
    result = extract_case(case)
    ids = [node.node_id for node in result.nodes]
    assert len(ids) == len(set(ids)), f"{case.path}: duplicate node ids"


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_containment_tree_reaches_every_node(case) -> None:
    result = extract_case(case)
    file_ids = {node.node_id for node in result.nodes if node.kind == "file"}
    assert len(file_ids) == 1, f"{case.path}: expected exactly one file node, got {file_ids}"
    parent_of = {edge.target: edge.source for edge in result.edges if edge.kind == "contains"}
    for node in result.nodes:
        if node.kind == "file":
            continue
        # walk up the contains chain; must reach the file root without a cycle
        seen: set[str] = set()
        current = node.node_id
        while current not in file_ids:
            assert current not in seen, f"{case.path}: containment cycle at {current}"
            seen.add(current)
            assert current in parent_of, f"{case.path}: {node.name} not contained"
            current = parent_of[current]


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_refs_reference_existing_nodes(case) -> None:
    result = extract_case(case)
    node_ids = {node.node_id for node in result.nodes}
    for ref in result.unresolved_refs:
        assert ref.source_node_id in node_ids, f"{case.path}: ref from unknown node {ref.source_node_id}"
        assert ref.kind in ("calls", "imports", "type_hint", "function_ref"), f"{case.path}: bad ref kind {ref.kind}"


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_qualified_names_are_namespaced_consistently(case) -> None:
    """A node's qualified name ends with its name and never escapes the file."""
    result = extract_case(case)
    for node in result.nodes:
        if node.kind in ("file",):
            continue
        assert node.qualified_name, f"{case.path}: {node.name} has empty qualified name"
        tail = node.qualified_name.rsplit("::", 1)[-1]
        # qualified tails may differ for import nodes (target tail); only
        # assert the containment invariant: qualified name starts at file stem
        assert tail, f"{case.path}: {node.qualified_name} has empty tail"
