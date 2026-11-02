"""Cross-engine differential runner + deterministic shrinker (I3, W15, SUB-130).

Compares the **pure-Python reference walker** (invoked directly — never
through the kernel-routing ``extract_source``; the F1 contract from
``tests/test_kernel_rust.py``) against the **Rust kernel** on generated
source (:mod:`know_your_context.ingest.fuzzgen`). Outcomes are compared as
canonical-JSON artifacts; any byte difference is a *divergence* — a real
parity bug with a repro attached.

Both engines raising the *same* typed error is agreement (e.g. a
deliberately truncated case failing to parse identically on both sides).
Engines raising *different* errors, or one raising while the other
succeeds, is a divergence.

The shrinker is a deterministic ddmin-style reducer over source **lines**:
chunk sizes halve from ``n//2`` down to 1; at each granularity every chunk
is tried left-to-right; a removal that preserves the divergence is kept
and the search restarts on the smaller source. The trace of accepted
reductions is returned for the regression-test artifact. No randomness,
no clocks.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from know_your_context.domain import canonical_json
from know_your_context.ingest.extractor import TreeSitterExtractor
from know_your_context.ingest.fuzzgen import FuzzCase
from know_your_context.ingest.languages import get_spec

__all__ = [
    "Divergence",
    "DiffuzzError",
    "compare_case",
    "compare_outcomes",
    "kernel_outcome",
    "python_outcome",
    "shrink",
]

#: Upper bound on shrink attempts (each attempt is a full dual extraction);
#: deterministic and generous — a shrink that hits the budget still returns
#: the smallest case found so far.
DEFAULT_SHRINK_BUDGET = 400


class DiffuzzError(RuntimeError):
    """The differential harness itself is misused (fail-closed)."""


Outcome = tuple[str, str]
"""A comparable extraction outcome: ``("ok", canonical_json)`` or
``("error", "<ExceptionType>")``."""


@dataclass(frozen=True)
class Divergence:
    """A proven parity break with repro material."""

    case: FuzzCase
    python_outcome: Outcome
    kernel_outcome: Outcome
    summary: str

    def repro(self) -> dict[str, Any]:
        """A JSON-able repro artifact for regression tests and reports."""
        return {
            "language": self.case.language,
            "seed": self.case.seed,
            "path": self.case.path,
            "source": self.case.source,
            "source_sha256": hashlib.sha256(self.case.source.encode("utf-8")).hexdigest(),
            "python_outcome_kind": self.python_outcome[0],
            "kernel_outcome_kind": self.kernel_outcome[0],
            "summary": self.summary,
        }


def python_outcome(case: FuzzCase) -> Outcome:
    """The pure-Python reference walker, invoked directly (F1 contract)."""
    try:
        result = TreeSitterExtractor(get_spec(case.language)).extract(case.path, case.source)
    except Exception as error:  # noqa: BLE001 — the *type* is the comparable
        return ("error", type(error).__name__)
    return ("ok", canonical_json(result.to_artifact()))


def kernel_outcome(case: FuzzCase) -> Outcome:
    """The Rust kernel via the bridge (kernel errors map onto typed ones)."""
    from know_your_context.ingest import kernel_bridge

    try:
        result = kernel_bridge.extract_source_via_kernel(case.path, case.source, language=case.language)
    except Exception as error:  # noqa: BLE001 — the *type* is the comparable
        return ("error", type(error).__name__)
    return ("ok", canonical_json(result.to_artifact()))


def compare_outcomes(case: FuzzCase, python: Outcome, kernel: Outcome) -> Divergence | None:
    """Build a Divergence when the two outcomes differ, else ``None``."""
    if python == kernel:
        return None
    if python[0] != kernel[0]:
        summary = f"outcome kind differs: python={python[0]} ({python[1]}) kernel={kernel[0]} ({kernel[1]})"
    else:
        left_lines = python[1].splitlines()
        right_lines = kernel[1].splitlines()
        first = next(
            (index for index, (left, right) in enumerate(zip(left_lines, right_lines, strict=False)) if left != right),
            min(len(left_lines), len(right_lines)),
        )
        summary = f"canonical artifacts differ (first differing line {first} of {len(left_lines)}/{len(right_lines)})"
    return Divergence(case=case, python_outcome=python, kernel_outcome=kernel, summary=summary)


def compare_case(case: FuzzCase) -> Divergence | None:
    """Run both engines on ``case``; a Divergence is a proven parity bug."""
    return compare_outcomes(case, python_outcome(case), kernel_outcome(case))


def shrink(
    case: FuzzCase,
    *,
    diverges: Callable[[FuzzCase], bool] | None = None,
    budget: int = DEFAULT_SHRINK_BUDGET,
) -> tuple[FuzzCase, tuple[str, ...]]:
    """Deterministically reduce ``case`` while the divergence persists.

    ``diverges`` defaults to ``compare_case(...) is not None``; tests
    inject their own predicate (e.g. against an instrumented engine).
    Returns ``(smallest_case, trace)`` where each trace line records one
    accepted reduction. 1-minimal under line-chunk removal: after the
    loop, no single-chunk removal at any tried granularity preserves the
    divergence.
    """
    if isinstance(diverges, bool) or diverges is None:

        def _default(candidate: FuzzCase) -> bool:
            return compare_case(candidate) is not None

        predicate: Callable[[FuzzCase], bool] = _default
    else:
        predicate = diverges
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise DiffuzzError(f"budget must be a positive int, got {budget!r}")
    seed_case = FuzzCase(language=case.language, seed=case.seed, path=case.path, source=case.source)
    if not predicate(seed_case):
        raise DiffuzzError("shrink called on a case that does not diverge")

    trace: list[str] = []
    current = seed_case.source
    attempts = 0

    def _try(candidate_lines: tuple[str, ...]) -> bool:
        nonlocal attempts
        attempts += 1
        candidate = FuzzCase(language=case.language, seed=case.seed, path=case.path, source="\n".join(candidate_lines))
        return predicate(candidate)

    lines = tuple(current.split("\n"))
    granularity = max(1, len(lines) // 2)
    while granularity >= 1 and attempts < budget:
        accepted = False
        index = 0
        while index < len(lines) and attempts < budget:
            chunk = lines[index : index + granularity]
            if not chunk:
                index += 1
                continue
            candidate = lines[:index] + lines[index + granularity :]
            if candidate and candidate != lines and _try(tuple(candidate)):
                trace.append(
                    f"removed lines {index + 1}..{index + len(chunk)} ({len(lines)} -> {len(candidate)} lines)"
                )
                lines = tuple(candidate)
                accepted = True
                break  # restart the sweep on the smaller source
            index += granularity
        if not accepted:
            granularity //= 2
    return (
        FuzzCase(language=case.language, seed=case.seed, path=case.path, source="\n".join(lines)),
        tuple(trace),
    )
