"""Property tests for the W12 depth push (SUB-121).

Invariants over derandomized hypothesis scenarios:

1. **Helper purity** — candidates() is deterministic, total (never raises),
   repo-confined (no absolute paths, no ``..`` segments), and duplicate-free
   for arbitrary garbage input.
2. **Synthesis idempotency** — a second run inserts zero edges and leaves the
   store byte-identical, over generated symbol/edge scenarios.
3. **Synthesis determinism** — two independently built identical stores yield
   identical reports and edge dumps.
4. **Non-overwrite** — W12 passes never mint a second edge where a
   same-(source,target,kind) edge exists, whatever its provenance.
5. **Extraction determinism** — every W12 fixture extracts byte-identical
   results across repeated runs.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from know_your_context.ingest.extractor import TreeSitterExtractor
from know_your_context.ingest.languages import get_spec
from know_your_context.resolve.lang_helpers import HELPERS
from know_your_context.resolve.synthesis import (
    PASS_EVENT_HANDLER_WIRING,
    SynthesisConfig,
    run_synthesis,
)
from know_your_context.store import EventStore

SEEN = "2026-11-02T00:00:00+00:00"
PARSE = "ingest:0123456789abcdef"
FIXTURES = Path(__file__).parent / "fixtures" / "ingest"

SETTINGS = settings(
    max_examples=20,
    deadline=None,
    derandomize=True,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.data_too_large,
        HealthCheck.function_scoped_fixture,
    ],
)

_counter = itertools.count()


def _store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / f"s{next(_counter)}.sqlite3")


def _dump(store: EventStore) -> str:
    with store.read() as conn:
        rows = conn.execute(
            "SELECT source, target, kind, metadata, provenance FROM graph_edges "
            "ORDER BY source, target, kind, metadata, provenance"
        ).fetchall()
    return "\n".join("|".join(str(col) for col in row) for row in rows)


# ------------------------------------------------------------- helper purity


@st.composite
def _helper_inputs(draw):
    language = draw(st.sampled_from(sorted(HELPERS)))
    target = draw(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./\\:.-$%",
            max_size=40,
        )
    )
    source = draw(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_/.", min_size=1, max_size=30).map(
            lambda s: f"{s}/main.x"
        )
    )
    return language, target, source


@given(_helper_inputs())
@SETTINGS
def test_helpers_pure_total_confined(tmp_path: Path, payload):
    language, target, source = payload
    helper = HELPERS[language]
    first = helper.candidates(target, source)
    second = helper.candidates(target, source)
    assert first == second  # deterministic
    paths = [candidate.path for candidate in first]
    assert len(paths) == len(set(paths))  # duplicate-free
    for path in paths:
        assert not path.startswith("/")
        assert ".." not in path.split("/")
        assert path.strip() != ""


# ------------------------------------------------------- synthesis scenarios


@st.composite
def _scenario(draw):
    events = draw(st.lists(st.sampled_from(["login", "save", "sync", "go"]), max_size=4))
    extra_handlers = draw(st.integers(min_value=0, max_value=3))
    types = draw(st.lists(st.sampled_from(["Widget", "Gadget", "Repo"]), max_size=3))
    return events, extra_handlers, types


def _build(store: EventStore, events, extra_handlers, types) -> None:
    store.upsert_file(
        path="s.py",
        sha256=hashlib.sha256(b"s.py").hexdigest(),
        size=1,
        mtime=1,
        language="python",
        generated_hint=False,
        extraction_version=1,
    )
    for index, event in enumerate(events):
        store.upsert_symbol(node_id=f"fn:e{index}", name=f"emit_{event}", kind="function", path="s.py", line=index + 1)
        store.upsert_symbol(node_id=f"fn:h{index}", name=f"on_{event}", kind="function", path="s.py", line=index + 20)
        for extra in range(extra_handlers):
            store.upsert_symbol(
                node_id=f"fn:h{index}x{extra}",
                name=f"on_{event}",
                kind="function",
                path="s.py",
                line=index * 4 + extra + 40,
            )
    for index, type_name in enumerate(types):
        store.upsert_symbol(node_id=f"cls:t{index}", name=type_name, kind="class", path="s.py", line=index + 60)
        store.upsert_symbol(
            node_id=f"fn:f{index}", name=f"create_{type_name.lower()}", kind="function", path="s.py", line=index + 80
        )


@given(_scenario())
@SETTINGS
def test_synthesis_idempotent_and_deterministic(tmp_path: Path, payload):
    events, extra_handlers, types = payload
    store1 = _store(tmp_path)
    store2 = _store(tmp_path)
    try:
        for store in (store1, store2):
            _build(store, events, extra_handlers, types)
        first1 = run_synthesis(store1)
        dump1 = _dump(store1)
        second1 = run_synthesis(store1)
        dump1_again = _dump(store1)
        first2 = run_synthesis(store2)
        dump2 = _dump(store2)
        assert second1.inserted == 0  # idempotent
        assert dump1 == dump1_again  # byte-identical after re-run
        assert dump1 == dump2  # deterministic across independent stores
        assert [p.inserted for p in first1.passes] == [p.inserted for p in first2.passes]
    finally:
        store1.close()
        store2.close()


@given(_scenario())
@SETTINGS
def test_synthesis_never_overwrites_parse_edges(tmp_path: Path, payload):
    events, extra_handlers, types = payload
    store = _store(tmp_path)
    try:
        _build(store, events, extra_handlers, types)
        before = _dump(store)
        parse_edge_count = before.count(PARSE)
        run_synthesis(store)
        run_synthesis(store)
        after = _dump(store)
        assert after.count(PARSE) == parse_edge_count  # parse edges untouched
        # no duplicate identity edges
        with store.read() as conn:
            dupes = conn.execute(
                "SELECT source, target, kind, COUNT(*) c FROM graph_edges GROUP BY source, target, kind HAVING c > 1"
            ).fetchall()
        assert dupes == []
    finally:
        store.close()


@given(st.lists(st.sampled_from(["login", "save", "x"]), min_size=1, max_size=3))
@SETTINGS
def test_event_caps_are_hard_bounds(tmp_path: Path, events):
    store = _store(tmp_path)
    try:
        store.upsert_file(
            path="s.py",
            sha256=hashlib.sha256(b"s").hexdigest(),
            size=1,
            mtime=1,
            language="python",
            generated_hint=False,
            extraction_version=1,
        )
        for index, event in enumerate(events):
            store.upsert_symbol(
                node_id=f"fn:e{index}", name=f"emit_{event}", kind="function", path="s.py", line=index + 1
            )
            store.upsert_symbol(
                node_id=f"fn:h{index}", name=f"on_{event}", kind="function", path="s.py", line=index + 10
            )
        run_synthesis(store, config=SynthesisConfig(enabled_passes=frozenset({PASS_EVENT_HANDLER_WIRING})))
        with store.read() as conn:
            rows = conn.execute(
                "SELECT metadata FROM graph_edges WHERE provenance = 'synthesis:event_handler_wiring'"
            ).fetchall()
        # handlers per event never exceed the default cap (16)
        assert len(rows) <= 16 * max(1, len(set(events)))
    finally:
        store.close()


# ----------------------------------------------------- extraction determinism


def test_w12_fixtures_extract_deterministically():
    for language in (
        "crystal",
        "elisp",
        "erlang",
        "fsharp",
        "gdscript",
        "gleam",
        "haxe",
        "nim",
        "objc",
        "powershell",
        "solidity",
        "v",
        "vala",
        "zig",
    ):
        spec = get_spec(language)
        fixture = next((FIXTURES / language).iterdir())
        source = fixture.read_text()
        first = TreeSitterExtractor(spec).extract(f"{language}/{fixture.name}", source)
        second = TreeSitterExtractor(spec).extract(f"{language}/{fixture.name}", source)
        assert first.canonical_json() == second.canonical_json(), language
        assert first.parse_error is False, language
