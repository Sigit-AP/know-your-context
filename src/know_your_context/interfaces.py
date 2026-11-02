"""Structural interfaces (Protocols) for the major subsystems (W13).

One canonical home for the subsystem contracts the codebase already honours
informally. All protocols are :func:`runtime_checkable`, so adapters and fakes
can be validated with ``isinstance`` in tests and at composition seams.

These protocols describe the *used* surface of each subsystem (the methods
callers actually invoke), not the full public API — structural subtyping means
``EventStore`` satisfies :class:`StoreProtocol` without inheriting anything.

Nothing here imports the subsystems themselves; conformance is pinned by
``tests/test_interfaces_enums.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "AuditSinkProtocol",
    "ExtractorProtocol",
    "ResolverProtocol",
    "StoreProtocol",
    "SynthesizerProtocol",
    "describe_interfaces",
]


@runtime_checkable
class StoreProtocol(Protocol):
    """The event store's read/maintain surface used by read-only consumers."""

    @property
    def db_path(self) -> str | Path: ...

    def read(self) -> AbstractContextManager[Any]: ...

    def close(self) -> None: ...

    def table_names(self) -> set[str]: ...

    def applied_migrations(self) -> list[int]: ...


@runtime_checkable
class ExtractorProtocol(Protocol):
    """One language extractor: source text in, deterministic extraction out."""

    def extract(self, path: str, source: str, *, generated_hint: bool = False) -> Any: ...


@runtime_checkable
class ResolverProtocol(Protocol):
    """The two-phase reference resolver's batch entry point."""

    def resolve_pending(self, store: Any, *, limit: int | None = None) -> Any: ...


@runtime_checkable
class SynthesizerProtocol(Protocol):
    """The synthesis pipeline: one run over the store, one report."""

    def run(self, store: Any) -> Any: ...


@runtime_checkable
class AuditSinkProtocol(Protocol):
    """The audit journal's begin/complete pair (invocation lifecycle)."""

    def begin(self, invocation: Any) -> Any: ...

    def complete(self, invocation_id: str, outcome: Any) -> Any: ...


_PROTOCOL_METHODS: Mapping[str, tuple[str, ...]] = {
    "AuditSinkProtocol": ("begin", "complete"),
    "ExtractorProtocol": ("extract",),
    "ResolverProtocol": ("resolve_pending",),
    "StoreProtocol": ("db_path", "read", "close", "table_names", "applied_migrations"),
    "SynthesizerProtocol": ("run",),
}


def describe_interfaces() -> Mapping[str, tuple[str, ...]]:
    """Protocol name → method names, for introspection and docs."""
    return dict(_PROTOCOL_METHODS)
