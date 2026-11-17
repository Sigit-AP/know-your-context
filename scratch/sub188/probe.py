"""SUB-188 research probe: categorize the ~300k ambiguous_exact failures on L4.

Runs the resolver's decision cascade IN PURE PYTHON over the real L4 store
(sub185_l4_fixed.sqlite), including the two SUB-184 changes NOT yet in the
code:
  A. flat-corpus module fallback (basename suffix match when the exact and
     reversed-path probes miss),
  B. import-binding tier (the file's own kind='import' symbols binding a bare
     call name, resolved via tier-(c) rules WITH the binding ref's tail).

No repo code is touched — this mirrors resolver.py to size each ambiguity
category so the implementation can be TDD'd against real numbers.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import PurePosixPath

STORE = sys.argv[1] if len(sys.argv) > 1 else "/root/.hermes/cache/scratch/sub185_l4_fixed.sqlite"

conn = sqlite3.connect(STORE)
conn.row_factory = sqlite3.Row

PY_SUFFIXES = (".py", "/__init__.py")
SEP = ".::."


def ascii_lower(value: str) -> str:
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in value)


# ------------------------------------------------------------- load corpus
files: set[str] = set()
for row in conn.execute("SELECT path FROM files"):
    files.add(str(row["path"]))

symbols_by_path: dict[str, list[dict]] = defaultdict(list)
for row in conn.execute("SELECT node_id, name, kind, path, line, col FROM symbols"):
    s = {
        "node_id": str(row["node_id"]),
        "name": str(row["name"]),
        "kind": str(row["kind"]),
        "path": str(row["path"]),
        "line": int(row["line"]),
        "col": int(row["col"]),
    }
    symbols_by_path[s["path"]].append(s)

# corpus-wide def counts (kind <> 'import') + first def per name — one pass.
global_defs: dict[str, dict | None] = {}
for s in symbols_by_path.values():
    for sym in s:
        if sym["kind"] != "import":
            prev = global_defs.get(sym["name"])
            if prev is None:
                global_defs[sym["name"]] = sym
            elif prev is not True:
                global_defs[sym["name"]] = True  # 2+ -> ambiguous

refs_by_source: defaultdict[str, list[dict]] = defaultdict(list)
all_refs: list[dict] = []
for row in conn.execute(
    "SELECT ref_id, source_node_id, target_name, name_tail, kind, status, failure_reason FROM unresolved_refs"
):
    r = {
        "ref_id": int(row["ref_id"]),
        "source": str(row["source_node_id"]),
        "target": str(row["target_name"]),
        "tail": str(row["name_tail"] or ""),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "reason": row["failure_reason"],
    }
    all_refs.append(r)
    refs_by_source[r["source"]].append(r)


def source_path_of(node_id: str) -> str | None:
    if node_id.startswith("file:"):
        p = node_id[len("file:") :]
        return p if p in files else None
    # symbol node: maps via its path (flat corpus: node_id hashes, so ask SQL)
    row = conn.execute("SELECT path FROM symbols WHERE node_id = ?", (node_id,)).fetchone()
    return str(row["path"]) if row is not None else None


# ------------------------------------------------------ module-file lookup
_module_file_cache: dict[str, tuple[str, str | int | None]] = {}


def module_file(rel: str) -> tuple[str, str | int | None]:
    """('hit', path) / ('ambiguous', n) / ('miss', None) — resolver mirror
    incl. the flat-corpus basename fallback (change A)."""
    cached = _module_file_cache.get(rel)
    if cached is not None:
        return cached
    if rel in files:
        result: tuple[str, str | int | None] = ("hit", rel)
        _module_file_cache[rel] = result
        return result
    lowered = ascii_lower(rel)
    full = [p for p in files if ascii_lower(p).endswith("/" + lowered)]
    if len(full) > 1:
        result = ("ambiguous", len(full))
    elif len(full) == 1:
        result = ("hit", full[0])
    else:
        base = PurePosixPath(rel).name
        lb = ascii_lower(base)
        flat = [p for p in files if p != rel and (ascii_lower(p).endswith("/" + lb) or p.endswith(base))]
        if len(flat) > 1:
            result = ("ambiguous", len(flat))
        elif len(flat) == 1:
            result = ("hit", flat[0])
        else:
            result = ("miss", None)
    _module_file_cache[rel] = result
    return result


def symbol_in_file(path: str, name: str) -> tuple[str, dict | int | None]:
    matches = [
        s
        for s in symbols_by_path.get(path, [])
        if s["name"] == name or s["name"].endswith(f".{name}") or s["name"].endswith(f"::{name}")
    ]
    if len(matches) == 1:
        return ("hit", matches[0])
    if not matches:
        return ("miss", None)
    return ("ambiguous", len(matches))


def resolve_python_import(target_name: str, edge_kind: str) -> dict | None:
    """Mirror of resolver._resolve_python_import for a flat python corpus.

    Returns {'hit': (node_kind, node_id)} / {'fail': reason} / None.
    """
    dots = len(target_name) - len(target_name.lstrip("."))
    rest = target_name[dots:]
    segments = [seg for seg in rest.split(".") if seg] if rest else []
    splits: list[tuple[list[str], str | None]] = []
    if len(segments) >= 2:
        splits.append((segments[:-1], segments[-1]))
    if segments:
        splits.append((segments, None))
    if dots and segments:
        splits.append(([], segments[-1]))
    fallback: str | None = None
    for module_segments, symbol_tail in splits:
        module_rel = "/".join(module_segments)
        if not module_rel:
            continue
        module_row: str | None = None
        for candidate in (f"{module_rel}{suffix}" for suffix in PY_SUFFIXES):
            status, payload = module_file(candidate)
            if status == "ambiguous":
                return {"fail": f"ambiguous_module:{payload}"}
            if status == "hit":
                assert isinstance(payload, str)
                module_row = payload
                break
        if module_row is None:
            continue
        if symbol_tail is None:
            if edge_kind == "imports":
                return {"hit": ("file", module_row)}
            continue
        status, payload = symbol_in_file(module_row, symbol_tail)
        if status == "hit":
            assert isinstance(payload, dict)
            return {"hit": ("symbol", payload["node_id"])}
        if status == "ambiguous":
            return {"fail": f"ambiguous_module_symbol:{payload}"}
        if edge_kind != "imports":
            return {"fail": f"symbol_not_in_module:{symbol_tail}"}
        if fallback is None:
            fallback = module_row
    if fallback is not None:
        return {"hit": ("file", fallback)}
    return None


# per-file imported module paths (change A unblocks these on flat corpora)
_imported_paths_cache: dict[str, frozenset[str]] = {}


def imported_paths_of(src_path: str) -> frozenset[str]:
    cached = _imported_paths_cache.get(src_path)
    if cached is not None:
        return cached
    targets: set[str] = set()
    for ref in refs_by_source.get(f"file:{src_path}", []):
        if ref["kind"] == "imports":
            targets.add(ref["target"])
    for s in symbols_by_path.get(src_path, []):
        for ref in refs_by_source.get(s["node_id"], []):
            if ref["kind"] == "imports":
                targets.add(ref["target"])
    paths: set[str] = set()
    for tgt in sorted(targets):
        decision = resolve_python_import(tgt, "imports")
        if decision is not None and "hit" in decision and decision["hit"][0] == "file":
            paths.add(decision["hit"][1])
    result = frozenset(paths)
    _imported_paths_cache[src_path] = result
    return result


def same_name_in_paths(name: str, paths: set[str]) -> tuple[str, dict | int | None]:
    total = 0
    first: dict | None = None
    for path in sorted(paths):
        for s in symbols_by_path.get(path, []):
            if s["name"] == name and s["kind"] != "import":
                total += 1
                if first is None:
                    first = s
                if total > 1:
                    return ("ambiguous", total)
    if total == 0 or first is None:
        return ("miss", None)
    return ("hit", first)


# ---------------------------------------------------------------- cascade
def resolve_ref(r: dict) -> dict:
    src_path = source_path_of(r["source"])
    if src_path is None:
        return {"category": "unknown_source"}
    target = r["target"]

    if r["kind"] == "imports":
        decision = resolve_python_import(target, "imports")
        if decision is not None:
            if "hit" in decision:
                return {"category": "imports_recovered", "target_kind": "module", "node": decision["hit"][1]}
            return {"category": "imports_still_fails", "fail": decision["fail"]}
        return {"category": "imports_no_module"}

    if "." in target or target.startswith("."):
        decision = resolve_python_import(target, "calls")
        if decision is not None:
            if "hit" in decision:
                return {"category": "dotted_import_path", "node": decision["hit"][1]}
            return {"category": "dotted_fails", "fail": decision["fail"]}
        return {"category": "dotted_no_module"}

    # (e) scope tier: file -> import -> binding -> global.
    status, payload = same_name_in_paths(target, {src_path})
    if status == "hit":
        assert isinstance(payload, dict)
        return {"category": "file_scope", "target_kind": payload["kind"], "node": payload["node_id"]}

    imported = imported_paths_of(src_path)
    if imported:
        status, payload = same_name_in_paths(target, set(imported))
        if status == "hit":
            assert isinstance(payload, dict)
            return {"category": "import_scope", "target_kind": payload["kind"], "node": payload["node_id"]}
        if status == "ambiguous":
            return {"category": "parked_import_scope_ambiguous", "n": payload}

    # modul-lokal: the file's own import symbols bind the bare name.
    bindings = [s for s in symbols_by_path.get(src_path, []) if s["kind"] == "import" and s["name"] == target]
    if len(bindings) == 1:
        brefs = [ref for ref in refs_by_source.get(bindings[0]["node_id"], []) if ref["kind"] == "imports"]
        resolved: set[str] = set()
        for bref in brefs:
            decision = resolve_python_import(bref["target"], "imports")
            if decision is None or "hit" not in decision:
                continue
            _kind, node = decision["hit"]
            if _kind == "file" and bref["tail"] and bref["tail"] != bref["target"]:
                status, payload = symbol_in_file(node, bref["tail"])
                if status == "hit":
                    assert isinstance(payload, dict)
                    resolved.add(payload["node_id"])
                    continue
                if status == "ambiguous":
                    return {"category": "parked_binding_module_symbol_ambiguous"}
            resolved.add(node)
        if len(resolved) == 1:
            node = next(iter(resolved))
            tk = "module" if node.startswith("file:") else (node_kind(node) or "?")
            return {"category": "import_binding", "target_kind": tk, "node": node}
        if len(resolved) > 1:
            return {"category": "parked_binding_multi"}
        return {"category": "parked_binding_unresolved"}
    if len(bindings) > 1:
        return {"category": "parked_binding_ambiguous"}

    # global scope (unique real definition corpus-wide).
    g = global_defs.get(target)
    if g is None:
        return {"category": "parked_no_scope"}
    prev = global_defs[target]
    if isinstance(prev, dict):
        return {"category": "global_scope", "target_kind": prev["kind"], "node": prev["node_id"]}
    return {"category": "parked_no_scope"}


_node_kind_cache: dict[str, str | None] = {}


def node_kind(node_id: str) -> str | None:
    if node_id in _node_kind_cache:
        return _node_kind_cache[node_id]
    row = conn.execute("SELECT kind FROM symbols WHERE node_id = ?", (node_id,)).fetchone()
    kind = str(row["kind"]) if row is not None else None
    _node_kind_cache[node_id] = kind
    return kind


def main() -> int:
    failed = [r for r in all_refs if r["status"] == "failed" and r["reason"] and r["reason"].startswith("ambiguous_exact:")]
    calls = [r for r in failed if r["kind"] != "imports"]
    imports = [r for r in failed if r["kind"] == "imports"]
    print(f"ambiguous_exact failed: {len(failed)} (calls {len(calls)}, imports {len(imports)})")

    counter = Counter()
    tk = Counter()
    for i, r in enumerate(calls):
        v = resolve_ref(r)
        counter[v["category"]] += 1
        if "target_kind" in v:
            tk[(v["category"], v["target_kind"])] += 1
        if i and i % 50000 == 0:
            print(f"  ... {i}/{len(calls)}", flush=True)
    print("\n== CALLS (" + str(len(calls)) + ") categorization:")
    for cat, n in counter.most_common():
        detail = {k: v for (c, k), v in tk.items() if c == cat and k != "?"}
        print(f"   {cat:38s} {n:6d}   {dict(sorted(detail.items(), key=lambda kv: -kv[1])) if detail else ''}")

    counter2 = Counter()
    for r in imports:
        v = resolve_ref(r)
        counter2[v["category"]] += 1
    print("\n== IMPORTS (" + str(len(imports)) + ") categorization:")
    for cat, n in counter2.most_common():
        print(f"   {cat:38s} {n:6d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())