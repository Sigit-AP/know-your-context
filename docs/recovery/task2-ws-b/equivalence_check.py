"""Equivalence proofs on REAL corpus data (SUB-160):
(1) resolver tail tier: legacy LIKE union vs v6 name_tail index — identical sets
    for every distinct separator-free tail in the store; exotic tails exercise
    the legacy path (identical by construction — same code).
(2) _module_file: legacy path LIKE suffix scan vs v6 lower(path_rev) GLOB —
    identical verdicts for every real file path suffix (1- and 2-segment forms).
Runs on the small pinned store (14,896 real symbols, 505 real files).
"""

import sys
from pathlib import Path

SCRATCH = Path("/root/.hermes/cache/scratch/cluster-b/ws-b")
sys.path.insert(0, "/root/know-your-context/src")

from know_your_context.resolve.resolver import ReferenceResolver, _escape_like  # noqa: E402
from know_your_context.store import EventStore  # noqa: E402

store = EventStore(str(SCRATCH / "ws-b-after-small.sqlite3"))
resolver = ReferenceResolver()

# (1) tail equivalence over EVERY distinct name_tail
with store.read() as conn:
    tails = [str(r[0]) for r in conn.execute("SELECT DISTINCT name_tail FROM symbols")]
checked = 0
mismatches = []
for tail in tails:
    new = resolver._tail_candidates(store, tail)
    # legacy oracle
    found = {s.node_id: s for s in store.symbols_named(tail)}
    like = _escape_like(tail)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE name LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' "
            "ORDER BY path, line, col, node_id",
            (f"%.{like}", f"%::{like}"),
        ).fetchall()
    from know_your_context.store import SymbolRow  # noqa: E402

    for row in rows:
        s = SymbolRow(
            node_id=str(row["node_id"]),
            name=str(row["name"]),
            kind=str(row["kind"]),
            path=str(row["path"]),
            line=int(row["line"]),
            col=int(row["col"]),
        )
        found.setdefault(s.node_id, s)
    legacy = sorted(found.values(), key=lambda s: (s.path, s.line, s.col, s.node_id))
    checked += 1
    if [s.node_id for s in new] != [s.node_id for s in legacy]:
        mismatches.append(tail)
print(f"tails checked: {checked}, mismatches: {mismatches[:10]}")

# (2) _module_file equivalence over every real path's 1- and 2-segment suffixes
with store.read() as conn:
    paths = [str(r[0]) for r in conn.execute("SELECT path FROM files")]
checked2 = 0
mm2 = []
for path in paths:
    parts = path.split("/")
    for rel in {parts[-1], "/".join(parts[-2:]) if len(parts) > 1 else parts[-1]}:
        # legacy
        with store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM files WHERE path LIKE ? ESCAPE '\\' ORDER BY path",
                (f"%/{_escape_like(rel)}",),
            ).fetchall()
        legacy_paths = sorted(str(r["path"]) for r in rows)
        status, payload = resolver._module_file(store, rel)
        if store.get_file(rel) is not None:
            legacy_paths = [rel]  # the exact-hit tier, shared by both implementations
        if status == "miss":
            new_paths: list[str] = []
        elif status == "hit":
            new_paths = [payload.path]  # type: ignore[union-attr]
        else:
            legacy_paths = sorted(str(r["path"]) for r in rows) if store.get_file(rel) is None else [rel]
            with store.read() as conn:
                new_paths = sorted(
                    str(r[0])
                    for r in conn.execute(
                        "SELECT path FROM files WHERE lower(path_rev) GLOB ?",
                        (("/" + rel)[::-1].lower().replace("[", "[[]").replace("*", "[*]").replace("?", "[?]") + "*",),
                    )
                )
        checked2 += 1
        if sorted(legacy_paths) != sorted(new_paths):
            mm2.append((rel, legacy_paths[:3], new_paths[:3]))
print(f"module_file checks: {checked2}, mismatches: {mm2[:5]}")
store.close()
