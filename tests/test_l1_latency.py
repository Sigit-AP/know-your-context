"""SUB-216: L1 latency — the query fast path (RED before the change, GREEN after).

Per-query startup profile on the L1 store (100-file synthetic corpus, 10
sequential queries, one fresh process per query, as the stress harness runs
them): interpreter boot 34 ms, cli import 29 ms, full argparse tree
89-125 ms, http_api chain 447 ms, store open 30 ms, dispatch ~12 ms. Every
one of the 10 harness queries re-paid the whole parser tree; the SUB-216 fast
path accepts the well-formed ``query``/``codeintel`` verb shapes without
building the parser and falls back to the real parser for anything else, so
CLI behavior and error messages are byte-identical.

Harness before/after (``harness_final.py L1 kyc``, 2026-11-18 UTC, run alone):
p50 710.5 ms -> 514.5 / 589.5 / 613.0 ms (three runs), solo_w1 640 -> 416 ms,
err 0, h1 6/6, h2 4/4, sigkill 0.

RED/GREEN: ``test_fast_path_parity_with_full_parser`` is RED on the
pre-SUB-216 code (the fast path does not exist) and GREEN after; the byte
identity test pins the harness query outputs (masking invocation-time fields
like ``audit_event_id`` / recommendation ``expiry`` exactly as
``test_rss_read_path`` does).
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from know_your_context.cli import _try_fast_args, build_parser


# The frozen L1 benchmark corpus (bench ``harness_final.py``, seed 7, 100
# files) — same generator and seed as the official stress corpus, so the
# store and every query output are a pure function of (corpus, ingest, query).
def _write_l1_corpus(root: Path, n: int = 100, seed: int = 7) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    nmod = max(4, n // 5)
    for i in range(n):
        mod = i % nmod
        deps = {0, (mod + 1) % nmod, rnd.randrange(nmod)} - {mod}
        deps = sorted(d for d in deps if d < nmod)
        imp = "".join(f"from mod{d} import fn_{d}_0, Cls{d}\n" for d in deps)
        calls = ", ".join(f"fn_{d}_0(rows, cache)" for d in deps)
        parts = [
            f'"""Module {i}: synthetic corpus for stress testing (deterministic)."""\n',
            "import json\nimport os\n",
            imp,
            "\n",
        ]
        for k in range(rnd.randint(2, 4)):
            parts.append(
                f"def fn_{i}_{k}(rows, cache=None):\n"
                f'    """Handle rows for module {i} variant {k}."""\n'
                f"    out = []\n"
                f"    for key, val in rows:\n"
                f"        if val is None:\n"
                f"            continue\n"
                f"        label = str(key).strip().lower()\n"
                f"        out.append({{'k': label, 'v': val}})\n"
                f"    nested = [{calls}]\n"
                f"    if cache is not None:\n"
                f"        cache.setdefault('fn_{i}_{k}', len(out))\n"
                f"    return {{'count': len(out), 'nested': nested}}\n\n"
            )
        parts.append(
            f"class Cls{i}:\n"
            f'    """Container for module {i} items."""\n\n'
            f"    def __init__(self, name):\n"
            f"        self.name = name\n"
            f"        self.items = []\n\n"
            f"    def add(self, item):\n"
            f"        self.items.append(item)\n"
            f"        return len(self.items)\n\n"
            f"    def summary(self):\n"
            f"        return {{'name': self.name, 'n': len(self.items), 'fn': fn_{i}_0(self.items)}}\n"
        )
        (root / f"mod{i}.py").write_text("".join(parts))


# ------------------------------------------------------------------- helpers


def _cmd(argv: list[str]) -> list[str]:
    return [sys.executable, "-m", "know_your_context", *argv]


def _ingest(tmp_path: Path) -> tuple[Path, Path]:
    """Write the L1 corpus and ingest it; return ``(store, output_root)``."""
    corpus = tmp_path / "src"
    _write_l1_corpus(corpus)
    store = tmp_path / "store"
    outroot = tmp_path / "out"
    proc = subprocess.run(
        _cmd(
            [
                "--store",
                str(store),
                "ingest",
                str(corpus),
                "--max-files",
                "65536",
                "--max-nodes",
                "20000000",
                "--max-edges",
                "20000000",
                "--max-refs",
                "20000000",
                "--json",
            ]
        ),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    return store, outroot


def _mask(payload: dict) -> dict:
    """Drop invocation-time fields (repo precedent: test_rss_read_path)."""
    payload = dict(payload)
    payload.pop("audit_event_id", None)
    rec = payload.get("recommendations") or {}
    for key in ("best_actions", "recommendations", "unnecessary_actions"):
        items = rec.get(key) or []
        for item in items:
            if isinstance(item, dict):
                item.pop("expiry", None)
            elif isinstance(item, tuple):
                pass
    return payload


def _interp_p50(sorted_samples: list[float]) -> float:
    """The harness percentile (linear interpolation, measure.py qstats)."""
    s = sorted(sorted_samples)
    k = (len(s) - 1) * 0.5
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


# ------------------------------------------------------------------ payloads


_W2_GOLDEN = [
    {
        "access_only": False,
        "candidates": [],
        "confidence": "0.500000000000",
        "estimated_tokens": 185,
        "knowledge_payload_tokens": 104,
        "node_versions": {"class:340b72f3d592ef70b58ebbc5830803d8": 1},
        "projection_version": "gen-54322bcc14aa2516a5ecdae1",
        "provenance": ["code://mod17.py#L33"],
        "recommendations": {"best_actions": [], "recommendations": [], "unnecessary_actions": []},
        "required_knowledge_watermark": 0,
        "results": [
            {
                "confidence": "0.500000000000",
                "domain": "P1",
                "kind": "class",
                "node_id": "class:340b72f3d592ef70b58ebbc5830803d8",
                "p": "0.000000000000",
                "summary": "Cls17 — class in mod17.py:33",
                "version": 1,
            }
        ],
        "routing": "code_index_fallback",
        "source_knowledge_high_watermark": 0,
        "stale": False,
        "truncated": False,
    },
    {
        "access_only": False,
        "candidates": [],
        "confidence": "0.500000000000",
        "estimated_tokens": 185,
        "knowledge_payload_tokens": 103,
        "node_versions": {"class:19011dc68dd2424a50d61b918a2d394e": 1},
        "projection_version": "gen-54322bcc14aa2516a5ecdae1",
        "provenance": ["code://mod3.py#L34"],
        "recommendations": {"best_actions": [], "recommendations": [], "unnecessary_actions": []},
        "required_knowledge_watermark": 0,
        "results": [
            {
                "confidence": "0.500000000000",
                "domain": "P1",
                "kind": "class",
                "node_id": "class:19011dc68dd2424a50d61b918a2d394e",
                "p": "0.000000000000",
                "summary": "Cls3 — class in mod3.py:34",
                "version": 1,
            }
        ],
        "routing": "code_index_fallback",
        "source_knowledge_high_watermark": 0,
        "stale": False,
        "truncated": False,
    },
    {
        "access_only": False,
        "candidates": [],
        "confidence": "0.500000000000",
        "estimated_tokens": 189,
        "knowledge_payload_tokens": 107,
        "node_versions": {"function:fc8ca94499cf1328736b56421a5b7234": 1},
        "projection_version": "gen-54322bcc14aa2516a5ecdae1",
        "provenance": ["code://mod12.py#L21"],
        "recommendations": {"best_actions": [], "recommendations": [], "unnecessary_actions": []},
        "required_knowledge_watermark": 0,
        "results": [
            {
                "confidence": "0.500000000000",
                "domain": "P1",
                "kind": "function",
                "node_id": "function:fc8ca94499cf1328736b56421a5b7234",
                "p": "0.000000000000",
                "summary": "fn_12_1 — function in mod12.py:21",
                "version": 1,
            }
        ],
        "routing": "code_index_fallback",
        "source_knowledge_high_watermark": 0,
        "stale": False,
        "truncated": False,
    },
    {
        "access_only": False,
        "candidates": [],
        "confidence": "0.500000000000",
        "estimated_tokens": 188,
        "knowledge_payload_tokens": 106,
        "node_versions": {"function:7ef9da502931d37fea16dee2bb2ee644": 1},
        "projection_version": "gen-54322bcc14aa2516a5ecdae1",
        "provenance": ["code://mod0.py#L7"],
        "recommendations": {"best_actions": [], "recommendations": [], "unnecessary_actions": []},
        "required_knowledge_watermark": 0,
        "results": [
            {
                "confidence": "0.500000000000",
                "domain": "P1",
                "kind": "function",
                "node_id": "function:7ef9da502931d37fea16dee2bb2ee644",
                "p": "0.000000000000",
                "summary": "fn_0_0 — function in mod0.py:7",
                "version": 1,
            }
        ],
        "routing": "code_index_fallback",
        "source_knowledge_high_watermark": 0,
        "stale": False,
        "truncated": False,
    },
]

# sha256 of the exact stdout of `codeintel callers-of fn_0_0 --depth 1` on the
# L1 store (46931 B, deterministic — ops surface carries no audit_event_id).
_W1_SHA256 = "942b9845bc1b03d6b3a16f7d255d2dc43de370756cdc6e596b4d040222da97a4"
# Refreshed 2026-11-19: codeintel now discloses depth_capped/truncated (additive).


def _full_parse(argv: list[str]) -> dict | None:
    """The real parser's namespace, or None when it rejects *argv*."""
    try:
        ns = build_parser().parse_args(argv)
    except SystemExit:
        return None
    return vars(ns)


# --------------------------------------------------------------- RED -> GREEN


def test_fast_path_parity_with_full_parser() -> None:
    """The fast path is exactly as strict as the real parser on every shape.

    RED before SUB-216 (``_try_fast_args`` does not exist), GREEN after. Two
    directions are enforced: (a) any invocation the fast path accepts must be
    accepted by the full parser with an identical namespace (never looser);
    (b) the L1 harness shapes must actually take the fast path (never silently
    dead — a fallback would still be correct, just slow).

    Conservative fallbacks (fast path None + full parser accept) are legal by
    design and not asserted here.
    """
    shapes: list[list[str]] = [
        ["--store", "S", "query", "-"],
        ["query"],
        ["query", "{}"],
        ["query", "-"],
        ["--actor", "me", "--output-root", "O", "--ops-root", "R", "query", "{}"],
        ["--idempotency-key", "k", "query", "-"],
        ["--store", "S", "query", "--projection-wait-seconds", "2.5", "-"],
        ["--store", "S", "codeintel", "callers-of", "fn_0_0", "--depth", "1"],
        ["codeintel", "callees-of", "x", "--limit", "7", "--file", "mod0.py"],
        ["codeintel", "impact", "x", "--depth", "1"],
        ["codeintel", "dependency-path", "a", "b"],
        ["codeintel", "co-change", "--min-support", "3", "--limit", "50"],
        ["codeintel", "boundary-violations", "--layers", "ui:ui;core:core", "--allow", "a:b"],
    ]
    # Shapes the fast path must REJECT (falls back to the parser).
    rejected: list[list[str]] = [
        ["--version"],
        ["serve"],
        ["query", "--store", "S"],  # root option after the verb: parser error
        ["--projection-wait-seconds", "2", "query"],  # verb-level flag pre-verb
        ["query", "a", "b"],
        ["codeintel"],
        ["codeintel", "callers-of"],
        ["codeintel", "boundary-violations"],  # missing required --layers
        ["--store=S", "query", "-"],  # '=' forms stay on the real parser
    ]
    for argv in shapes:
        fast = _try_fast_args(argv)
        assert fast is not None, f"harness shape should hit the fast path: {argv}"
        full = _full_parse(argv)
        assert full is not None, f"fast path accepted what the parser rejects: {argv}"
        assert vars(fast) == full, f"namespace mismatch on {argv}: {vars(fast)} vs {full}"
    for argv in rejected:
        assert _try_fast_args(argv) is None, f"fast path must fall back: {argv}"


# ------------------------------------------------------------- byte identity


def test_l1_harness_query_outputs_byte_identical(tmp_path: Path) -> None:
    """The 10 L1 harness queries are byte-identical on a fresh ingest.

    Golden captured 2026-11-18 from the pre-SUB-216 HEAD on this exact
    corpus/store shape (that run measured p50 710.5 ms). ``audit_event_id`` /
    recommendation ``expiry`` are masked per ``test_rss_read_path``.
    """
    store, _ = _ingest(tmp_path)
    for _ in range(6):
        proc = subprocess.run(
            _cmd(["--store", str(store), "codeintel", "callers-of", "fn_0_0", "--depth", "1"]),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        assert proc.stdout  # non-empty
        assert hashlib.sha256(proc.stdout.encode("utf-8")).hexdigest() == _W1_SHA256
    for term, golden in zip(("Cls17", "Cls3", "fn_12_1", "fn_0_0"), _W2_GOLDEN, strict=True):
        proc = subprocess.run(
            _cmd(["--store", str(store), "query", "-"]),
            input=json.dumps({"purpose": term, "token_budget": 500}),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        assert json.loads(proc.stdout)["routing"] == "code_index_fallback"
        assert _mask(json.loads(proc.stdout)) == _mask(golden), term


@pytest.mark.skipif(
    os.environ.get("KYB_SKIP_HOST_SENSITIVE") == "1",
    reason="host-sensitive budget; set KYB_SKIP_HOST_SENSITIVE=1 to skip",
)
def test_l1_query_p50_budget(tmp_path: Path) -> None:
    """The 10-query L1 plan stays under 900 ms p50 (fresh processes).

    900 ms is ~2x the post-SUB-216 probe p50 (444 ms; harness 514.5-613 ms in
    three runs) and below the pre-fix harness p95 (1278 ms). This is a coarse
    regression tripwire — the deterministic RED/GREEN gate is
    ``test_fast_path_parity_with_full_parser`` — so it deliberately carries
    2x headroom like the RSS budgets in test_rss_read_path. It never fires on
    an idle host (measured p50 444-563 ms); a future import-cost regression
    that adds a couple hundred ms per query pushes the p50 over it.
    """
    if (load := os.getloadavg()[0]) > 3.0:
        pytest.skip(f"host load {load:.1f} too high for a latency budget")
    store, _ = _ingest(tmp_path)
    lat: list[float] = []
    for _ in range(6):
        t0 = time.perf_counter()
        proc = subprocess.run(
            _cmd(["--store", str(store), "codeintel", "callers-of", "fn_0_0", "--depth", "1"]),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        lat.append((time.perf_counter() - t0) * 1000)
    for term in ("Cls17", "Cls3", "fn_12_1", "fn_0_0"):
        t0 = time.perf_counter()
        proc = subprocess.run(
            _cmd(["--store", str(store), "query", "-"]),
            input=json.dumps({"purpose": term, "token_budget": 500}),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        lat.append((time.perf_counter() - t0) * 1000)
    p50 = _interp_p50(lat)
    assert p50 < 900.0, f"L1 query p50 {p50:.1f} ms >= 900 ms budget; latencies={lat}"
