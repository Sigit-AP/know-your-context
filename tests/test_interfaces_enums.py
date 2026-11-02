"""Tests for ``know_your_context.enums`` and ``know_your_context.interfaces`` (W13).

Pins: enum values are byte-identical to the strings the frozen contracts
already persist; the runtime subsystems structurally satisfy their Protocols;
fakes missing a method fail the runtime check.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest

from know_your_context.enums import (
    CapabilityMode,
    HealthState,
    MigrationDirection,
    RefStatus,
    RouteAccess,
    SwitchState,
)
from know_your_context.interfaces import (
    AuditSinkProtocol,
    ExtractorProtocol,
    ResolverProtocol,
    StoreProtocol,
    SynthesizerProtocol,
    describe_interfaces,
)


class TestEnumShapes:
    @pytest.mark.parametrize(
        "enum_cls",
        [SwitchState, CapabilityMode, RefStatus, HealthState, MigrationDirection, RouteAccess],
    )
    def test_enums_are_str_enums(self, enum_cls: type[StrEnum]) -> None:
        assert issubclass(enum_cls, StrEnum)
        for member in enum_cls:
            assert member == member.value
            assert isinstance(member, str)

    def test_capability_mode_values_match_daemon_config(self) -> None:
        # DaemonConfig._validate accepts exactly these strings.
        assert {m.value for m in CapabilityMode} == {"off", "enforce"}

    def test_ref_status_values_match_store_column(self) -> None:
        # unresolved_refs.status vocabulary (store.record_resolution_attempt).
        assert {m.value for m in RefStatus} == {"pending", "resolved", "failed"}

    def test_health_state_values_match_diagnose_severities(self) -> None:
        from know_your_context.ops.diagnose import SEVERITY_ORDER

        assert {m.value for m in HealthState} <= set(SEVERITY_ORDER) | {"ok"}

    def test_migration_direction_values(self) -> None:
        assert MigrationDirection.UP == "up"
        assert MigrationDirection.DOWN == "down"

    def test_switch_state_values(self) -> None:
        assert SwitchState.ENABLED == "enabled"
        assert SwitchState.DISABLED == "disabled"

    def test_enum_json_serializes_as_string(self) -> None:
        import json

        assert json.dumps({"mode": CapabilityMode.ENFORCE}) == '{"mode": "enforce"}'


class TestStoreProtocol:
    def test_event_store_satisfies(self, tmp_path: Path) -> None:
        from know_your_context.store import EventStore

        store = EventStore(tmp_path / "s.sqlite3")
        try:
            assert isinstance(store, StoreProtocol)
        finally:
            store.close()

    def test_incomplete_fake_fails(self) -> None:
        class _Fake:
            def close(self) -> None: ...

        assert not isinstance(_Fake(), StoreProtocol)


class TestResolverAndSynthesizerProtocols:
    def test_reference_resolver_satisfies(self) -> None:
        from know_your_context.resolve import ReferenceResolver

        assert isinstance(ReferenceResolver(), ResolverProtocol)

    def test_synthesizer_satisfies(self) -> None:
        from know_your_context.resolve.synthesis import Synthesizer

        assert isinstance(Synthesizer(), SynthesizerProtocol)

    def test_name_match_resolver_is_not_batch_resolver(self) -> None:
        from know_your_context.resolve.name_matcher import NameMatchResolver

        assert not isinstance(NameMatchResolver(), ResolverProtocol)


class TestExtractorProtocol:
    def test_tree_sitter_extractor_satisfies(self) -> None:
        from know_your_context.ingest.extractor import TreeSitterExtractor
        from know_your_context.ingest.languages import get_spec

        assert isinstance(TreeSitterExtractor(get_spec("python")), ExtractorProtocol)

    def test_missing_extract_fails(self) -> None:
        class _Fake:
            def pull(self) -> None: ...

        assert not isinstance(_Fake(), ExtractorProtocol)


class TestAuditSinkProtocol:
    def test_audit_log_satisfies(self, tmp_path: Path) -> None:
        from know_your_context.audit import AuditLog
        from know_your_context.store import EventStore

        store = EventStore(tmp_path / "s.sqlite3")
        try:
            assert isinstance(AuditLog(store), AuditSinkProtocol)
        finally:
            store.close()


class TestDescribeInterfaces:
    def test_describes_all_five(self) -> None:
        assert set(describe_interfaces()) == {
            "StoreProtocol",
            "ExtractorProtocol",
            "ResolverProtocol",
            "SynthesizerProtocol",
            "AuditSinkProtocol",
        }
        assert describe_interfaces()["StoreProtocol"][0] == "db_path"
