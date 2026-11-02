"""Shared enumerations for the operational/architecture surface (W13).

These enums replace free-form status/mode strings *at the boundary where new
code is written*. Every member is a :class:`enum.StrEnum`, so a member *is*
its string value: serialisation, SQL parameters and frozen-contract payloads
that already carry these exact strings are byte-identical whether they hold
the plain string or the enum member. Existing persisted data is untouched.

The values deliberately mirror strings that already exist in the codebase
(``unresolved_refs.status``, ``DaemonConfig.capability_mode``, diagnose
severities) — the enums give them one canonical, exhaustively-checkable home.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "CapabilityMode",
    "HealthState",
    "MigrationDirection",
    "RefStatus",
    "RouteAccess",
    "SwitchState",
]


class SwitchState(StrEnum):
    """Resolved state of one operator kill switch."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class CapabilityMode(StrEnum):
    """Daemon capability-gate mode (mirrors ``DaemonConfig.capability_mode``)."""

    OFF = "off"
    ENFORCE = "enforce"


class RefStatus(StrEnum):
    """Lifecycle states of an ``unresolved_refs`` row (mirror of the column)."""

    PENDING = "pending"
    RESOLVED = "resolved"
    FAILED = "failed"


class HealthState(StrEnum):
    """Roll-up health of one store, from diagnose severities."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class MigrationDirection(StrEnum):
    """Direction of one explicit schema migration."""

    UP = "up"
    DOWN = "down"


class RouteAccess(StrEnum):
    """Access category of one webview API route (loopback viewer)."""

    AUTHENTICATED = "authenticated"
