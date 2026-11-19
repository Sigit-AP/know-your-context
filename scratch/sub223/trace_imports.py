"""SUB-223 import tracer: which modules load during query/codeintel runs, and pydantic's marginal RSS cost."""

from __future__ import annotations

import ast
import importlib.abc
import importlib.machinery
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path("/root/know-your-context/src")
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # rss.py

from rss import rss_mib  # noqa: E402


class Tracer(importlib.abc.MetaPathFinder):
    def __init__(self) -> None:
        self.loaded: list[tuple[str, float]] = []  # (module, rss_after)
        self.pyd_names: dict[str, list[str]] = defaultdict(list)

    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None:
            return None
        orig = spec.loader  # keep BEFORE replacing
        t = self

        class Wrapped(importlib.abc.Loader):
            def create_module(self, spec_):
                return orig.create_module(spec_) if hasattr(orig, "create_module") else None

            def exec_module(self, module):
                orig.exec_module(module)
                rss = rss_mib()
                t.loaded.append((fullname, rss))
                if "know_your_context" in fullname:
                    try:
                        srcfile = Path(module.__file__)
                        tree = ast.parse(srcfile.read_text())
                        for node in ast.walk(tree):
                            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("pydantic"):
                                t.pyd_names[fullname].append(node.names[0].name)
                                t.pyd_names[fullname].extend(a.name for a in node.names[1:])
                    except Exception:
                        pass

        spec.loader = Wrapped()
        return spec


def run(mode: str) -> None:
    t = Tracer()
    sys.meta_path.insert(0, t)
    from know_your_context.cli import main

    if mode == "query":
        argv = ["--store", "/root/know-your-context/know-your-context.sqlite3",
                "--output-root", "/root/know-your-context/know-your-context-projection",
                "query", '{"purpose": "What evidence labels exist for claims?", "token_budget": 512, "depth": 1}']
    else:
        argv = ["--store", "/root/know-your-context/know-your-context.sqlite3",
                "codeintel", "callers-of", "QueryRequest"]
    code = main(argv)
    sys.meta_path.remove(t)

    root: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    last = 0.0
    for name, rss in t.loaded:
        counts[name.split(".")[0]] += 1
        d = rss - last
        if d > 0 and name.split(".")[0] != "rss":
            root[name.split(".")[0]] += d
        last = rss
    print(f"MODE={mode} EXIT={code}")
    print("== pydantic-family modules actually loaded ==")
    for name, rss in t.loaded:
        if name == "pydantic" or name.startswith("pydantic.") or name.startswith("pydantic_core"):
            print(f"  {name:<42} rss_after={rss:8.2f} MiB")
    print("== `from pydantic import ...` names per loaded kyc module ==")
    for mod, names in sorted(t.pyd_names.items()):
        print(f"  {mod}: {', '.join(sorted(set(names)))}")
    print("== RSS delta by top-level package (>0.1 MiB) ==")
    for pkg, d in sorted(root.items(), key=lambda kv: -kv[1]):
        if d > 0.1:
            print(f"  {pkg:<30} {d:8.2f} MiB  ({counts[pkg]} modules)")
    import resource
    print(f"PEAK_RSS={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.2f} MiB")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "query")