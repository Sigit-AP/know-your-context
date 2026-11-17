"""Deterministic synthetic Python corpus for ingest-memory profiling (SUB-185).

Regenerates the stress-corpus shape used by SUB-172/178/186: cross-module
imports, 2-4 functions per module calling each other and the hub symbol
``fn_0_0`` of module 0, one class + 3 methods per file. Deterministic:
level N == same bytes every run (no wall clock, no randomness dependency
beyond ``random.Random(7)`` used only for the import-neighbour set, which is
pure (file, neighbour) pairs and therefore stable across runs).

Usage: .venv/bin/python scratch/sub185/gen_corpus.py <L1|L2|L3> <outdir>
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

LEVELS = {"L1": 100, "L2": 1000, "L3": 5000}
SEED = 7

# Roughly matches the SUB-172 corpus scale (~59 LOC per module at L3).
HEAD = '"""Module {i:04d} synthetic corpus (seed 7, deterministic)."""\n'


def module_text(index: int, count: int, rng: random.Random) -> str:
    i = index
    lines: list[str] = [HEAD.format(i=i), ""]
    # 2-3 cross-module imports (source: sibling modules), hub import always.
    neighbours = sorted(rng.sample([j for j in range(count) if j != i], k=2))
    for j in neighbours[:1]:
        lines.append(f"import mod{j:04d}")
    lines.append(f"from mod0000 import fn_0_0")
    if len(neighbours) > 1:
        lines.append(f"from mod{neighbours[1]:04d} import fn_{neighbours[1]:04d}_1")
    lines.append("")
    # one class + 3 methods (matches the stress generator).
    lines.append(f"class Cls{i:04d}:")
    lines.append(f"    def method_a_{i:04d}(self, value):")
    lines.append(f"        return fn_0_0(value) + {i}")
    lines.append(f"    def method_b_{i:04d}(self, value):")
    lines.append(f"        return self.method_a_{i:04d}(value) * 2")
    lines.append(f"    def method_c_{i:04d}(self, value):")
    lines.append(f"        return self.method_b_{i:04d}(value) - 1")
    lines.append("")
    # 3 functions calling each other + the hub (calls edges), so resolutions
    # are real (exact/name_tail hits) and not only parks.
    lines.append(f"def fn_{i:04d}_1(value):")
    lines.append(f"    total = fn_0_0(value) + fn_{i:04d}_2(value)")
    if neighbours:
        lines.append(f"    return total + fn_{neighbours[0]:04d}_1(value)")
    lines.append("")
    lines.append(f"def fn_{i:04d}_2(value):")
    lines.append(f"    return fn_{i:04d}_3(value) * 2")
    lines.append("")
    lines.append(f"def fn_{i:04d}_3(value):")
    lines.append(f"    return value + {i} + Cls{i:04d}().method_a_{i:04d}(value)")
    lines.append("")
    return "\n".join(lines) + "\n"


def module_zero_text(count: int) -> str:
    lines: list[str] = [
        '"""Module 0000 — hub. Every module calls fn_0_0 (SUB-172 hub-and-spoke)."""',
        "",
        "def fn_0_0(value):",
        "    return value + 1",
        "",
        "class Cls0000:",
        "    def method_a_0000(self, value):",
        "        return fn_0_0(value) + 1",
        "    def method_b_0000(self, value):",
        "        return self.method_a_0000(value) * 2",
        "    def method_c_0000(self, value):",
        "        return self.method_b_0000(value) - 1",
        "",
        "def fn_0000_1(value):",
        "    return fn_0_0(value) + 2",
        "",
        "def fn_0000_2(value):",
        "    return fn_0000_1(value) * 2",
        "",
        "def fn_0000_3(value):",
        "    return fn_0000_2(value) + 3",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in LEVELS:
        print("usage: gen_corpus.py <L1|L2|L3> <outdir>", file=sys.stderr)
        return 2
    level, outdir = sys.argv[1], Path(sys.argv[2])
    count = LEVELS[level]
    outdir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(f"{SEED}:{level}:{count}")
    total_loc = 0
    for index in range(count):
        text = module_zero_text(count) if index == 0 else module_text(index, count, rng)
        (outdir / f"mod{index:04d}.py").write_text(text, encoding="utf-8")
        total_loc += text.count("\n")
    print(
        f"level={level} files={count} loc={total_loc} bytes="
        f"{sum(p.stat().st_size for p in outdir.glob('*.py'))} dir={outdir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())