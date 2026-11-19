"""SUB-223 baseline: reproduce prior spike RSS numbers (interp / +pydantic / +contracts / CLI query / CLI codeintel)."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path("/root/know-your-context/src")
sys.path.insert(0, str(SRC))

from rss import rss_delta_mib, rss_mib, peak_rss_mib  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "imports"

if mode == "imports":
    lines: list[str] = []
    cur = rss_delta_mib("interpreter (start)", 0.0, lines)
    import pydantic  # noqa: F401
    cur = rss_delta_mib("+ pydantic pkg", cur, lines)
    import pydantic_core  # noqa: F401
    cur = rss_delta_mib("+ pydantic_core", cur, lines)
    import know_your_context.contracts as c  # noqa: F401
    cur = rss_delta_mib("+ contracts", cur, lines)
    print("\n".join(lines))
    print(f"PEAK_RSS={peak_rss_mib():.2f} MiB")

elif mode == "query":
    from know_your_context.cli import main
    argv = ["--store", "/root/know-your-context/know-your-context.sqlite3",
            "--output-root", "/root/know-your-context/know-your-context-projection",
            "query", '{"purpose": "What evidence labels exist for claims?", "token_budget": 512, "depth": 1}']
    code = main(argv)
    print(f"EXIT={code} PEAK_RSS={peak_rss_mib():.2f} MiB", file=sys.stderr)

elif mode == "codeintel":
    from know_your_context.cli import main
    argv = ["--store", "/root/know-your-context/know-your-context.sqlite3",
            "codeintel", "callers-of", "QueryRequest"]
    code = main(argv)
    print(f"EXIT={code} PEAK_RSS={peak_rss_mib():.2f} MiB", file=sys.stderr)