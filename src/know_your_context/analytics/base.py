"""Leaf-level analytics constants and the shared error base.

``algorithms`` pulls ``centrality`` (and through it ``domain``/``pydantic``).
Callers that only need the schema version or the exception base — the
code-intelligence read path is the important one — import from here instead,
so a CLI query never pays a ~100 ms, tens-of-MB import for a constant.

``algorithms`` re-exports both names, so ``from ...analytics.algorithms import
SCHEMA_VERSION, AlgorithmError`` keeps working unchanged.
"""

from __future__ import annotations

__all__ = ["SCHEMA_VERSION", "AlgorithmError"]

#: Artifact schema version stamped on every analytics payload.
SCHEMA_VERSION = 1


class AlgorithmError(ValueError):
    """Base class for typed algorithm-suite input failures."""