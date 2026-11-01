"""Capability-based security for superpower agents (SUB-117, W10).

Pins the whole capability contract:

* token codec — HMAC-SHA256 signed, canonical payload, deterministic
  issuance, constant-time verification, fail-closed decoding;
* forgery / expiry / revocation / actor-binding / scope / allowlist;
* the file-backed store (secret 0600, atomic registry, hash-chained audit,
  reload-on-change so revocation is immediately effective);
* daemon integration in ``capability_mode="enforce"`` over a real socket
  (typed ``CAPABILITY_DENIED`` + audit event; mode ``"off"`` unchanged);
* the operator CLI ``capability issue|revoke|list``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from _serve_helpers import SocketClient, make_config, wait_for
from know_your_context.security.capabilities import (
    SCOPE_LANES,
    SCOPES,
    TOKEN_PREFIX,
    Capability,
    CapabilityActorMismatchError,
    CapabilityError,
    CapabilityExpiredError,
    CapabilityForgedError,
    CapabilityMalformedError,
    CapabilityRevokedError,
    CapabilityStore,
    CapabilityStoreError,
    decode_token,
    generate_secret,
    issue_token,
    mint_local_admin_token,
    verify_token,
)
from know_your_context.serve import rpc
from know_your_context.serve.daemon_config import ConfigError, DaemonConfig
from know_your_context.serve.superpower import (
    HEAVY_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    CapabilityDenied,
    SuperpowerDaemon,
    stop_daemon,
)

SECRET = bytes(range(32))
OTHER_SECRET = bytes(range(32, 64))
NOW = 1_900_000_000.0


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "actor": "agent-a",
        "verbs": ["knowledge_query"],
        "scope": "read",
        "expires_at": NOW + 3600,
        "nonce": "n" * 32,
        "issued_at": NOW,
    }
    base.update(overrides)
    return base


def _issue(**overrides: object) -> str:
    payload = _payload(**overrides)
    return issue_token(
        SECRET,
        actor=payload["actor"],  # type: ignore[arg-type]
        scope=payload["scope"],  # type: ignore[arg-type]
        verbs=payload["verbs"],  # type: ignore[arg-type]
        expires_at=payload["expires_at"],  # type: ignore[arg-type]
        nonce=payload["nonce"],  # type: ignore[arg-type]
        issued_at=payload["issued_at"],  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------- codec


class TestTokenFormat:
    def test_prefix_and_three_parts(self) -> None:
        token = _issue()
        assert token.startswith(TOKEN_PREFIX + ".")
        assert len(token.split(".")) == 3

    def test_payload_is_base64url(self) -> None:
        _, payload_b64, _ = _issue().split(".")
        base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))

    def test_signature_is_base64url_32_bytes(self) -> None:
        _, _, sig_b64 = _issue().split(".")
        raw = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        assert len(raw) == 32

    def test_payload_keys_exact(self) -> None:
        _, payload_b64, _ = _issue().split(".")
        decoded = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
        assert set(decoded) == {"actor", "verbs", "scope", "expires_at", "nonce", "issued_at"}

    def test_deterministic_issuance(self) -> None:
        assert _issue() == _issue()

    def test_verb_order_irrelevant(self) -> None:
        assert _issue(verbs=["a", "b"]) == _issue(verbs=["b", "a"])

    def test_distinct_nonces_distinct_tokens(self) -> None:
        assert _issue(nonce="a" * 32) != _issue(nonce="b" * 32)

    def test_distinct_secrets_distinct_tokens(self) -> None:
        mine = _issue()
        theirs = issue_token(
            OTHER_SECRET,
            actor="agent-a",
            scope="read",
            verbs=["knowledge_query"],
            expires_at=NOW + 3600,
            nonce="n" * 32,
            issued_at=NOW,
        )
        assert mine != theirs

    def test_decode_roundtrip(self) -> None:
        cap = decode_token(_issue(scope="write", verbs=["knowledge_query", "knowledge_log_event"]))
        assert cap.actor == "agent-a"
        assert cap.scope == "write"
        assert cap.verbs == frozenset({"knowledge_query", "knowledge_log_event"})
        assert cap.expires_at == NOW + 3600
        assert cap.nonce == "n" * 32
        assert cap.issued_at == NOW

    def test_decode_does_not_verify_signature(self) -> None:
        """decode_token is inspection only; a forged payload still decodes."""
        token = _issue()
        head, payload_b64, _ = token.split(".")
        forged = f"{head}.{payload_b64}.{'A' * 43}"
        assert decode_token(forged).actor == "agent-a"


class TestVerify:
    def test_roundtrip(self) -> None:
        cap = verify_token(SECRET, _issue(), now=NOW + 10)
        assert cap.actor == "agent-a"

    @pytest.mark.parametrize("scope", sorted(SCOPES))
    def test_all_scopes_roundtrip(self, scope: str) -> None:
        token = _issue(scope=scope)
        assert verify_token(SECRET, token, now=NOW + 1).scope == scope

    def test_verify_returns_frozen_capability(self) -> None:
        import dataclasses

        cap = verify_token(SECRET, _issue(), now=NOW + 1)
        assert isinstance(cap, Capability)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cap.actor = "mallory"  # type: ignore[misc]

    def test_compare_digest_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[object, object]] = []
        real = hmac.compare_digest

        def spy(a: object, b: object) -> bool:
            calls.append((a, b))
            return real(a, b)  # type: ignore[arg-type]

        monkeypatch.setattr("know_your_context.security.capabilities.hmac.compare_digest", spy)
        verify_token(SECRET, _issue(), now=NOW + 1)
        assert calls, "signature comparison must go through hmac.compare_digest"

    def test_expiry_in_the_future_ok(self) -> None:
        verify_token(SECRET, _issue(expires_at=NOW + 1), now=NOW)

    def test_expiry_boundary_is_expired(self) -> None:
        with pytest.raises(CapabilityExpiredError):
            verify_token(SECRET, _issue(expires_at=NOW), now=NOW)

    def test_expired(self) -> None:
        with pytest.raises(CapabilityExpiredError):
            verify_token(SECRET, _issue(expires_at=NOW - 1), now=NOW - 0.5)

    def test_revoked(self) -> None:
        with pytest.raises(CapabilityRevokedError):
            verify_token(SECRET, _issue(), now=NOW + 1, revoked=frozenset({"n" * 32}))

    def test_not_revoked_ok(self) -> None:
        verify_token(SECRET, _issue(), now=NOW + 1, revoked=frozenset({"x" * 32}))

    def test_actor_binding_ok(self) -> None:
        cap = verify_token(SECRET, _issue(), now=NOW + 1, actor="agent-a")
        assert cap.actor == "agent-a"

    def test_actor_mismatch_rejected(self) -> None:
        with pytest.raises(CapabilityActorMismatchError):
            verify_token(SECRET, _issue(), now=NOW + 1, actor="agent-b")

    def test_actor_none_skips_binding(self) -> None:
        verify_token(SECRET, _issue(), now=NOW + 1, actor=None)

    def test_errors_are_capability_errors(self) -> None:
        for exc in (
            CapabilityMalformedError,
            CapabilityForgedError,
            CapabilityExpiredError,
            CapabilityRevokedError,
            CapabilityActorMismatchError,
        ):
            assert issubclass(exc, CapabilityError)


class TestForgery:
    def test_wrong_secret(self) -> None:
        with pytest.raises(CapabilityForgedError):
            verify_token(OTHER_SECRET, _issue(), now=NOW + 1)

    def test_flipped_payload_char(self) -> None:
        head, payload_b64, sig = _issue().split(".")
        index = len(payload_b64) // 2
        flipped = payload_b64[:index] + ("A" if payload_b64[index] != "A" else "B") + payload_b64[index + 1 :]
        with pytest.raises(CapabilityError):
            verify_token(SECRET, f"{head}.{flipped}.{sig}", now=NOW + 1)

    def test_flipped_signature_char(self) -> None:
        head, payload_b64, sig = _issue().split(".")
        flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
        with pytest.raises(CapabilityForgedError):
            verify_token(SECRET, f"{head}.{payload_b64}.{flipped}", now=NOW + 1)

    def test_signature_from_other_token(self) -> None:
        _, _, other_sig = _issue(nonce="z" * 32).split(".")
        head, payload_b64, _ = _issue().split(".")
        with pytest.raises(CapabilityForgedError):
            verify_token(SECRET, f"{head}.{payload_b64}.{other_sig}", now=NOW + 1)

    def test_signed_with_payload_as_key(self) -> None:
        """An attacker who knows the payload but not the secret cannot self-sign."""
        head, payload_b64, _ = _issue().split(".")
        forged_sig = (
            base64.urlsafe_b64encode(hmac.new(payload_b64.encode(), payload_b64.encode(), hashlib.sha256).digest())
            .decode()
            .rstrip("=")
        )
        with pytest.raises(CapabilityForgedError):
            verify_token(SECRET, f"{head}.{payload_b64}.{forged_sig}", now=NOW + 1)

    def test_algorithm_confusion_none(self) -> None:
        """An empty signature segment is not even token-shaped."""
        head, payload_b64, _ = _issue().split(".")
        with pytest.raises(CapabilityMalformedError):
            verify_token(SECRET, f"{head}.{payload_b64}.", now=NOW + 1)


class TestMalformed:
    @pytest.mark.parametrize(
        "token",
        [
            "",
            "kyc1",
            "kyc1.",
            "kyc1.a",
            "kyc1.a.b.c",
            "v0.abc.def",
            ".abc.def",
            "kyc1.!!!.###",
            "kyc1.bm90LWpzb24.sig",
        ],
    )
    def test_shape_rejected(self, token: str) -> None:
        with pytest.raises(CapabilityMalformedError):
            verify_token(SECRET, token, now=NOW + 1)

    def test_non_string_token(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            verify_token(SECRET, 123, now=NOW + 1)  # type: ignore[arg-type]

    def _signed(self, payload: dict[str, object]) -> str:
        return issue_token(
            SECRET,
            actor=str(payload.get("actor", "agent-a")),
            scope=str(payload.get("scope", "read")),
            verbs=payload.get("verbs", ["knowledge_query"]),  # type: ignore[arg-type]
            expires_at=float(payload.get("expires_at", NOW + 3600)),  # type: ignore[arg-type]
            nonce=str(payload.get("nonce", "n" * 32)),
            issued_at=float(payload.get("issued_at", NOW)),  # type: ignore[arg-type]
        )

    @pytest.mark.parametrize("key", ["actor", "verbs", "scope", "expires_at", "nonce", "issued_at"])
    def test_missing_key_rejected(self, key: str) -> None:
        payload = _payload()
        del payload[key]
        body = (
            base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
        sig = base64.urlsafe_b64encode(hmac.new(SECRET, body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        with pytest.raises(CapabilityMalformedError):
            verify_token(SECRET, f"kyc1.{body}.{sig}", now=NOW + 1)

    def test_extra_key_rejected(self) -> None:
        payload = _payload(admin=True)
        body = (
            base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
        sig = base64.urlsafe_b64encode(hmac.new(SECRET, body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        with pytest.raises(CapabilityMalformedError):
            verify_token(SECRET, f"kyc1.{body}.{sig}", now=NOW + 1)

    def test_issue_rejects_bad_scope(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(scope="superuser")

    def test_issue_rejects_empty_actor(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(actor="  ")

    def test_issue_rejects_long_actor(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(actor="a" * 129)

    def test_issue_rejects_non_list_verbs(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(verbs="knowledge_query")

    def test_issue_rejects_empty_verb(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(verbs=[""])

    def test_issue_rejects_long_verb(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(verbs=["v" * 129])

    def test_issue_rejects_too_many_verbs(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(verbs=[f"v{i}" for i in range(65)])

    def test_issue_rejects_empty_nonce(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(nonce="")

    def test_issue_rejects_long_nonce(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(nonce="n" * 129)

    def test_issue_rejects_bool_expiry(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(expires_at=True)

    def test_issue_rejects_negative_expiry(self) -> None:
        with pytest.raises(CapabilityMalformedError):
            _issue(expires_at=-1.0)


class TestScopeLanes:
    def test_scope_constants(self) -> None:
        assert SCOPES == ("read", "write", "admin")
        assert SCOPE_LANES["read"] == frozenset({"read"})
        assert SCOPE_LANES["write"] == frozenset({"read", "write", "heavy"})
        assert SCOPE_LANES["admin"] == frozenset({"read", "write", "heavy", "admin"})

    @pytest.mark.parametrize(
        ("scope", "lane", "allowed"),
        [
            ("read", "read", True),
            ("read", "write", False),
            ("read", "heavy", False),
            ("read", "admin", False),
            ("write", "read", True),
            ("write", "write", True),
            ("write", "heavy", True),
            ("write", "admin", False),
            ("admin", "read", True),
            ("admin", "write", True),
            ("admin", "heavy", True),
            ("admin", "admin", True),
        ],
    )
    def test_lane_matrix(self, scope: str, lane: str, allowed: bool) -> None:
        cap = decode_token(_issue(scope=scope))
        assert cap.allows_lane(lane) is allowed

    def test_allows_verb(self) -> None:
        cap = decode_token(_issue(verbs=["knowledge_query"]))
        assert cap.allows_verb("knowledge_query")
        assert not cap.allows_verb("knowledge_log_event")

    def test_expired_helper(self) -> None:
        cap = decode_token(_issue(expires_at=NOW + 5))
        assert not cap.expired(NOW + 4)
        assert cap.expired(NOW + 5)
        assert cap.expired(NOW + 6)


# ------------------------------------------------------------------- store


@pytest.fixture()
def cap_root(tmp_path: Path) -> Path:
    root = tmp_path / "caps"
    root.mkdir()
    return root


@pytest.fixture()
def cap_store(cap_root: Path) -> CapabilityStore:
    store = CapabilityStore(cap_root)
    store.ensure_secret()
    return store


class TestStoreSecret:
    def test_ensure_secret_creates_0600(self, cap_root: Path) -> None:
        store = CapabilityStore(cap_root)
        secret = store.ensure_secret()
        assert len(secret) == 32
        mode = store.secret_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_ensure_secret_idempotent(self, cap_store: CapabilityStore) -> None:
        assert cap_store.ensure_secret() == cap_store.ensure_secret()

    def test_load_secret_roundtrip(self, cap_store: CapabilityStore) -> None:
        assert cap_store.load_secret() == cap_store.ensure_secret()

    def test_load_secret_missing_fails_closed(self, cap_root: Path) -> None:
        with pytest.raises(CapabilityStoreError):
            CapabilityStore(cap_root).load_secret()

    def test_load_secret_bad_hex_fails_closed(self, cap_root: Path) -> None:
        store = CapabilityStore(cap_root)
        store.secret_path.write_text("not-hex", encoding="utf-8")
        with pytest.raises(CapabilityStoreError):
            store.load_secret()

    def test_load_secret_wrong_length_fails_closed(self, cap_root: Path) -> None:
        store = CapabilityStore(cap_root)
        store.secret_path.write_text("ab" * 16, encoding="utf-8")
        with pytest.raises(CapabilityStoreError):
            store.load_secret()

    def test_env_secret_overrides_file(self, cap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = CapabilityStore(cap_root)
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_CAPABILITY_SECRET", SECRET.hex())
        assert store.load_secret() == SECRET

    def test_env_secret_bad_hex_fails_closed(self, cap_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = CapabilityStore(cap_root)
        store.ensure_secret()
        monkeypatch.setenv("KNOW_YOUR_CONTEXT_CAPABILITY_SECRET", "zz")
        with pytest.raises(CapabilityStoreError):
            store.load_secret()

    def test_generate_secret_length_and_randomness(self) -> None:
        one, two = generate_secret(), generate_secret()
        assert len(one) == 32 and len(two) == 32
        assert one != two


class TestStoreIssueRevoke:
    def test_issue_returns_token_and_capability(self, cap_store: CapabilityStore) -> None:
        token, cap = cap_store.issue(actor="agent-a", scope="read", now=NOW)
        assert token.startswith(TOKEN_PREFIX + ".")
        assert cap.actor == "agent-a"
        assert cap_store.verify(token, actor="agent-a", now=NOW + 1).nonce == cap.nonce

    def test_issue_records_in_registry(self, cap_store: CapabilityStore) -> None:
        _, cap = cap_store.issue(actor="agent-a", scope="read", now=NOW)
        listing = cap_store.list_registry()
        nonces = [entry["nonce"] for entry in listing["issued"]]
        assert cap.nonce in nonces

    def test_issue_default_verbs_cover_scope(self, cap_store: CapabilityStore) -> None:
        _, cap = cap_store.issue(actor="w", scope="write", now=NOW)
        assert frozenset(READ_TOOLS | WRITE_TOOLS | HEAVY_TOOLS) <= cap.verbs

    def test_issue_explicit_verbs(self, cap_store: CapabilityStore) -> None:
        _, cap = cap_store.issue(actor="r", scope="read", verbs=["knowledge_query"], now=NOW)
        assert cap.verbs == frozenset({"knowledge_query"})

    def test_issue_ttl(self, cap_store: CapabilityStore) -> None:
        _, cap = cap_store.issue(actor="a", scope="read", ttl_seconds=60, now=NOW)
        assert cap.expires_at == NOW + 60
        assert cap.issued_at == NOW

    def test_issue_nonce_collision_fails(self, cap_store: CapabilityStore) -> None:
        cap_store.issue(actor="a", scope="read", nonce="fixed-nonce", now=NOW)
        with pytest.raises(CapabilityStoreError):
            cap_store.issue(actor="b", scope="read", nonce="fixed-nonce", now=NOW)

    def test_issue_verbs_outside_scope_fail(self, cap_store: CapabilityStore) -> None:
        with pytest.raises(CapabilityStoreError):
            cap_store.issue(actor="a", scope="read", verbs=["knowledge_log_event"], now=NOW)

    def test_issue_audited(self, cap_store: CapabilityStore) -> None:
        cap_store.issue(actor="agent-a", scope="read", now=NOW)
        events = cap_store.audit_events()
        assert any(e["event"] == "capability_issued" and e["actor"] == "agent-a" for e in events)

    def test_revoke_makes_token_unusable(self, cap_store: CapabilityStore) -> None:
        token, cap = cap_store.issue(actor="a", scope="read", now=NOW)
        cap_store.verify(token, actor="a", now=NOW + 1)
        cap_store.revoke(cap.nonce, now=NOW + 2)
        with pytest.raises(CapabilityRevokedError):
            cap_store.verify(token, actor="a", now=NOW + 3)

    def test_revocation_immediately_effective(self, cap_store: CapabilityStore) -> None:
        """Same process, no restart: the very next verify sees the revocation."""
        token, cap = cap_store.issue(actor="a", scope="write", now=NOW)
        assert cap_store.verify(token, actor="a", now=NOW + 1).actor == "a"
        cap_store.revoke(cap.nonce, now=NOW + 1)
        with pytest.raises(CapabilityRevokedError):
            cap_store.verify(token, actor="a", now=NOW + 1)

    def test_revoke_unknown_nonce_fails(self, cap_store: CapabilityStore) -> None:
        with pytest.raises(CapabilityStoreError):
            cap_store.revoke("no-such-nonce", now=NOW)

    def test_double_revoke_fails(self, cap_store: CapabilityStore) -> None:
        _, cap = cap_store.issue(actor="a", scope="read", now=NOW)
        cap_store.revoke(cap.nonce, now=NOW + 1)
        with pytest.raises(CapabilityStoreError):
            cap_store.revoke(cap.nonce, now=NOW + 2)

    def test_revoke_by_token(self, cap_store: CapabilityStore) -> None:
        token, cap = cap_store.issue(actor="a", scope="read", now=NOW)
        cap_store.revoke_token(token, now=NOW + 1)
        with pytest.raises(CapabilityRevokedError):
            cap_store.verify(token, actor="a", now=NOW + 2)

    def test_revoke_audited(self, cap_store: CapabilityStore) -> None:
        _, cap = cap_store.issue(actor="a", scope="read", now=NOW)
        cap_store.revoke(cap.nonce, now=NOW + 1)
        events = cap_store.audit_events()
        assert any(e["event"] == "capability_revoked" and e["nonce"] == cap.nonce for e in events)

    def test_list_registry_shape(self, cap_store: CapabilityStore) -> None:
        cap_store.issue(actor="a", scope="read", now=NOW)
        _, cap = cap_store.issue(actor="b", scope="write", now=NOW)
        cap_store.revoke(cap.nonce, now=NOW + 1)
        listing = cap_store.list_registry()
        assert set(listing) == {"issued", "revoked"}
        assert len(listing["issued"]) == 2
        assert [e["nonce"] for e in listing["revoked"]] == [cap.nonce]
        issued_a = next(e for e in listing["issued"] if e["actor"] == "a")
        assert set(issued_a) == {"nonce", "actor", "scope", "verbs", "issued_at", "expires_at", "revoked"}

    def test_registry_corruption_fails_closed(self, cap_store: CapabilityStore) -> None:
        token, _ = cap_store.issue(actor="a", scope="read", now=NOW)
        cap_store.registry_path.write_text("{corrupt", encoding="utf-8")
        with pytest.raises(CapabilityStoreError):
            cap_store.verify(token, actor="a", now=NOW + 1)

    def test_cross_instance_reload(self, cap_root: Path) -> None:
        """A second store instance (another process) sees issues/revokes."""
        first = CapabilityStore(cap_root)
        first.ensure_secret()
        token, cap = first.issue(actor="a", scope="read", now=NOW)
        second = CapabilityStore(cap_root)
        assert second.verify(token, actor="a", now=NOW + 1).nonce == cap.nonce
        second.revoke(cap.nonce, now=NOW + 2)
        with pytest.raises(CapabilityRevokedError):
            first.verify(token, actor="a", now=NOW + 3)

    def test_verify_missing_secret_fails_closed(self, cap_root: Path) -> None:
        store = CapabilityStore(cap_root)
        with pytest.raises(CapabilityStoreError):
            store.verify(_issue(), now=NOW + 1)


class TestAuditChain:
    def test_empty_chain_valid(self, cap_store: CapabilityStore) -> None:
        assert cap_store.verify_audit_chain() is True

    def test_chain_links(self, cap_store: CapabilityStore) -> None:
        cap_store.issue(actor="a", scope="read", now=NOW)
        _, cap = cap_store.issue(actor="b", scope="write", now=NOW + 1)
        cap_store.revoke(cap.nonce, now=NOW + 2)
        assert cap_store.verify_audit_chain() is True
        events = cap_store.audit_events()
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        assert events[0]["prev"] == "0" * 64
        for previous, current in zip(events, events[1:], strict=False):
            assert current["prev"] == previous["hash"]

    def test_tamper_detected(self, cap_store: CapabilityStore) -> None:
        cap_store.issue(actor="a", scope="read", now=NOW)
        cap_store.issue(actor="b", scope="read", now=NOW + 1)
        lines = cap_store.audit_path.read_text(encoding="utf-8").splitlines()
        middle = json.loads(lines[0])
        middle["actor"] = "mallory"
        lines[0] = json.dumps(middle, sort_keys=True)
        cap_store.audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert cap_store.verify_audit_chain() is False

    def test_truncation_detected(self, cap_store: CapabilityStore) -> None:
        cap_store.issue(actor="a", scope="read", now=NOW)
        cap_store.issue(actor="b", scope="read", now=NOW + 1)
        lines = cap_store.audit_path.read_text(encoding="utf-8").splitlines()
        cap_store.audit_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        assert cap_store.verify_audit_chain() is False

    def test_audit_is_append_only_jsonl(self, cap_store: CapabilityStore) -> None:
        cap_store.audit({"event": "custom", "detail": 1}, now=NOW)
        cap_store.audit({"event": "custom", "detail": 2}, now=NOW + 1)
        events = [e for e in cap_store.audit_events() if e["event"] == "custom"]
        assert [e["detail"] for e in events] == [1, 2]

    def test_audit_thread_safe(self, cap_store: CapabilityStore) -> None:
        def worker(n: int) -> None:
            for i in range(10):
                cap_store.audit({"event": "concurrent", "worker": n, "i": i})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        concurrent = [e for e in cap_store.audit_events() if e["event"] == "concurrent"]
        assert len(concurrent) == 40
        assert cap_store.verify_audit_chain() is True


class TestMintLocalAdmin:
    def test_mint_requires_secret(self, cap_root: Path) -> None:
        assert mint_local_admin_token(CapabilityStore(cap_root), now=NOW) is None

    def test_mint_admin_token(self, cap_store: CapabilityStore) -> None:
        token = mint_local_admin_token(cap_store, now=NOW)
        assert token is not None
        cap = cap_store.verify(token, now=NOW + 1)
        assert cap.scope == "admin"
        assert cap.allows_lane("admin")


# ------------------------------------------------------------- daemon wiring


def _query_args() -> dict[str, object]:
    return {"purpose": "run the gates", "domain": "P1", "token_budget": 512}


def _write_args(node_id: str = "n-cap-write") -> dict[str, object]:
    return {
        "node": {
            "node_id": node_id,
            "kind": "leaf",
            "domain": "P1",
            "summary": "capability write probe",
            "provenance": ["context://test"],
            "source_version_id": "sv-test",
        }
    }


@pytest.fixture()
def enforce_daemon(tmp_path: Path):
    """A started in-process daemon in capability enforce mode + its store."""
    config = make_config(tmp_path, idle_timeout_seconds=120.0, capability_mode="enforce")
    config.paths.ensure()
    store = CapabilityStore(config.paths.root)
    store.ensure_secret()
    instance = SuperpowerDaemon(config)
    instance.start()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance, config, store
    instance.shutdown("test-teardown")
    thread.join(timeout=10)


def _send_tool(
    client: SocketClient,
    name: str,
    arguments: dict[str, object],
    *,
    actor: str = "agent-a",
    capability: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, object] = {"actor": actor}
    if capability is not None:
        meta["capability"] = capability
    return client.request("tools/call", {"name": name, "arguments": arguments, "_meta": meta})


class TestDaemonConfigFlag:
    def test_default_mode_off(self, tmp_path: Path) -> None:
        config = make_config(tmp_path)
        assert config.capability_mode == "off"

    def test_enforce_accepted(self, tmp_path: Path) -> None:
        config = make_config(tmp_path, capability_mode="enforce")
        assert config.capability_mode == "enforce"

    def test_invalid_mode_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            make_config(tmp_path, capability_mode="sometimes")

    def test_rpc_code_exported(self) -> None:
        assert rpc.CAPABILITY_DENIED == -32009

    def test_capability_denied_is_serve_error(self) -> None:
        from know_your_context.serve.superpower import ServeError

        assert issubclass(CapabilityDenied, ServeError)
        assert CapabilityDenied("x").rpc_code == rpc.CAPABILITY_DENIED


class TestDaemonEnforceMode:
    def test_public_read_without_capability(self, enforce_daemon) -> None:
        _, config, _ = enforce_daemon
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_query", _query_args())
        assert "result" in response, response

    @pytest.mark.parametrize("verb", sorted(READ_TOOLS))
    def test_all_read_verbs_public(self, enforce_daemon, verb: str) -> None:
        _, config, _ = enforce_daemon
        args: dict[str, object] = {"node_id": "n-gates"} if verb == "knowledge_get_node" else _query_args()
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, verb, args)
        # Public means: not a CAPABILITY_DENIED (the call itself may fail typed
        # for argument shape — that is the verb's own contract, not the gate).
        if "error" in response:
            assert response["error"]["code"] != rpc.CAPABILITY_DENIED, response

    def test_write_without_capability_denied(self, enforce_daemon) -> None:
        _, config, _ = enforce_daemon
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_log_event", _write_args())
        assert response["error"]["code"] == rpc.CAPABILITY_DENIED
        assert "capability" in response["error"]["message"]

    def test_heavy_without_capability_denied(self, enforce_daemon) -> None:
        _, config, _ = enforce_daemon
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_ingest_file", {"path": "x"})
        assert response["error"]["code"] == rpc.CAPABILITY_DENIED

    def test_read_scope_cannot_write(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        token, _ = store.issue(actor="agent-a", scope="read")
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_log_event", _write_args(), capability=token)
        assert response["error"]["code"] == rpc.CAPABILITY_DENIED
        assert response["error"]["data"]["reason"] == "scope_insufficient"

    def test_read_scope_can_read(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        token, _ = store.issue(actor="agent-a", scope="read")
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_query", _query_args(), capability=token)
        assert "result" in response, response

    def test_write_scope_writes(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        token, _ = store.issue(actor="agent-a", scope="write")
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_log_event", _write_args(), capability=token)
        assert "result" in response, response
        assert not response["result"].get("isError"), response

    def test_write_scope_reads(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        token, _ = store.issue(actor="agent-a", scope="write")
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_query", _query_args(), capability=token)
        assert "result" in response, response

    def test_admin_scope_writes(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        token, _ = store.issue(actor="agent-a", scope="admin")
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_log_event", _write_args("n-cap-admin"), capability=token)
        assert "result" in response, response

    def test_verb_outside_allowlist_denied(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        token, _ = store.issue(actor="agent-a", scope="write", verbs=["knowledge_query"])
        with SocketClient(config.paths.socket) as client:
            denied = _send_tool(client, "knowledge_log_event", _write_args(), capability=token)
            allowed = _send_tool(client, "knowledge_query", _query_args(), capability=token)
        assert denied["error"]["code"] == rpc.CAPABILITY_DENIED
        assert denied["error"]["data"]["reason"] == "verb_not_allowed"
        assert "result" in allowed, allowed

    def test_forged_token_denied(self, enforce_daemon) -> None:
        _, config, _ = enforce_daemon
        forged = issue_token(
            OTHER_SECRET,
            actor="agent-a",
            scope="admin",
            verbs=["knowledge_log_event"],
            expires_at=time.time() + 3600,
            nonce="f" * 32,
            issued_at=time.time(),
        )
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_log_event", _write_args(), capability=forged)
        assert response["error"]["code"] == rpc.CAPABILITY_DENIED
        assert response["error"]["data"]["reason"] == "forged"

    def test_expired_token_denied(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        expired = issue_token(
            store.load_secret(),
            actor="agent-a",
            scope="write",
            verbs=["knowledge_log_event"],
            expires_at=time.time() - 1,
            nonce="e" * 32,
            issued_at=time.time() - 10,
        )
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_log_event", _write_args(), capability=expired)
        assert response["error"]["code"] == rpc.CAPABILITY_DENIED
        assert response["error"]["data"]["reason"] == "expired"

    def test_revoked_token_denied_immediately(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        token, cap = store.issue(actor="agent-a", scope="write")
        with SocketClient(config.paths.socket) as client:
            ok = _send_tool(client, "knowledge_log_event", _write_args("n-cap-rev1"), capability=token)
            assert "result" in ok, ok
            store.revoke(cap.nonce)
            denied = _send_tool(client, "knowledge_log_event", _write_args("n-cap-rev2"), capability=token)
        assert denied["error"]["code"] == rpc.CAPABILITY_DENIED
        assert denied["error"]["data"]["reason"] == "revoked"

    def test_actor_mismatch_denied(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        token, _ = store.issue(actor="agent-a", scope="write")
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_log_event", _write_args(), actor="agent-b", capability=token)
        assert response["error"]["code"] == rpc.CAPABILITY_DENIED
        assert response["error"]["data"]["reason"] == "actor_mismatch"

    def test_non_string_capability_denied(self, enforce_daemon) -> None:
        _, config, _ = enforce_daemon
        with SocketClient(config.paths.socket) as client:
            response = client.request(
                "tools/call",
                {
                    "name": "knowledge_log_event",
                    "arguments": _write_args(),
                    "_meta": {"actor": "agent-a", "capability": 42},
                },
            )
        assert response["error"]["code"] == rpc.CAPABILITY_DENIED
        assert response["error"]["data"]["reason"] == "malformed"

    def test_denial_audited(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        with SocketClient(config.paths.socket) as client:
            _send_tool(client, "knowledge_log_event", _write_args())
        deadline = time.monotonic() + 5
        events: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            events = [e for e in store.audit_events() if e["event"] == "capability_denied"]
            if events:
                break
            time.sleep(0.02)
        assert events, "a capability denial must be audited"
        event = events[-1]
        assert event["actor"] == "agent-a"
        assert event["verb"] == "knowledge_log_event"
        assert event["reason"] == "capability_required"
        assert store.verify_audit_chain() is True

    def test_denial_audit_includes_nonce(self, enforce_daemon) -> None:
        _, config, store = enforce_daemon
        token, cap = store.issue(actor="agent-a", scope="read")
        with SocketClient(config.paths.socket) as client:
            _send_tool(client, "knowledge_log_event", _write_args(), capability=token)
        assert wait_for(
            lambda: any(e.get("nonce") == cap.nonce for e in store.audit_events() if e["event"] == "capability_denied")
        )

    def test_status_public_in_enforce_mode(self, enforce_daemon) -> None:
        _, config, _ = enforce_daemon
        with SocketClient(config.paths.socket) as client:
            response = client.request("daemon.status")
        assert "result" in response, response
        assert response["result"]["running"] is True

    def test_shutdown_requires_admin(self, tmp_path: Path) -> None:
        config = make_config(tmp_path, idle_timeout_seconds=120.0, capability_mode="enforce")
        config.paths.ensure()
        store = CapabilityStore(config.paths.root)
        store.ensure_secret()
        daemon = SuperpowerDaemon(config)
        daemon.start()
        thread = threading.Thread(target=daemon.serve_forever, daemon=True)
        thread.start()
        try:
            with SocketClient(config.paths.socket) as client:
                denied = client.request("daemon.shutdown", {"force": False})
            assert denied["error"]["code"] == rpc.CAPABILITY_DENIED
            assert daemon._stop.is_set() is False  # noqa: SLF001
            token = mint_local_admin_token(store, actor="operator")
            assert token is not None
            with SocketClient(config.paths.socket) as client:
                granted = client.request(
                    "daemon.shutdown", {"force": True, "_meta": {"actor": "operator", "capability": token}}
                )
            assert "result" in granted, granted
        finally:
            daemon.shutdown("test-teardown", force=True)
            thread.join(timeout=10)

    def test_unknown_verb_still_unknown_in_enforce(self, enforce_daemon) -> None:
        _, config, _ = enforce_daemon
        with SocketClient(config.paths.socket) as client:
            response = _send_tool(client, "knowledge_wipe_all", {})
        assert response["error"]["code"] == rpc.UNKNOWN_VERB

    def test_actor_still_required_in_enforce(self, enforce_daemon) -> None:
        _, config, _ = enforce_daemon
        with SocketClient(config.paths.socket) as client:
            response = client.call_tool("knowledge_query", _query_args(), actor=None)
        assert response["error"]["code"] == rpc.ACTOR_REQUIRED


class TestDaemonOffModeUnchanged:
    """Backward compatibility: default mode is byte-identical behavior."""

    @pytest.fixture()
    def off_daemon(self, tmp_path: Path):
        config = make_config(tmp_path, idle_timeout_seconds=120.0)
        instance = SuperpowerDaemon(config)
        instance.start()
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        yield instance, config
        instance.shutdown("test-teardown")
        thread.join(timeout=10)

    def test_off_mode_write_and_read(self, off_daemon) -> None:
        _, config = off_daemon
        with SocketClient(config.paths.socket) as client:
            written = client.call_tool("knowledge_log_event", _write_args("n-cap-off"))
            assert not written["result"].get("isError"), written
            read = client.call_tool("knowledge_get_node", {"node_id": "n-cap-off"})
        assert "n-cap-off" in json.dumps(read)

    def test_off_mode_ignores_capability_meta(self, off_daemon) -> None:
        _, config = off_daemon
        with SocketClient(config.paths.socket) as client:
            response = client.request(
                "tools/call",
                {
                    "name": "knowledge_query",
                    "arguments": _query_args(),
                    "_meta": {"actor": "agent-a", "capability": "kyc1.garbage.garbage"},
                },
            )
        assert "result" in response, response

    def test_off_mode_shutdown_needs_no_token(self, off_daemon) -> None:
        instance, config = off_daemon
        with SocketClient(config.paths.socket) as client:
            response = client.request("daemon.shutdown", {"force": True})
        assert "result" in response, response
        assert wait_for(lambda: instance._stop.is_set())  # noqa: SLF001


# -------------------------------------------------------------------- CLI


@pytest.fixture()
def cli_store(tmp_path: Path) -> Path:
    db = tmp_path / "soul.sqlite3"
    from _serve_helpers import seed_store

    seed_store(tmp_path)
    return db


def _run_cli(*argv: str) -> tuple[int, str, str]:
    import contextlib
    import io

    from know_your_context.cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TestCapabilityCli:
    def test_issue_prints_token_json(self, cli_store: Path) -> None:
        code, out, _ = _run_cli(
            "--store", str(cli_store), "capability", "issue", "--actor", "agent-a", "--scope", "read"
        )
        assert code == 0
        payload = json.loads(out)
        assert payload["token"].startswith(TOKEN_PREFIX + ".")
        assert payload["capability"]["actor"] == "agent-a"
        assert payload["capability"]["scope"] == "read"

    def test_issue_default_scope_is_read(self, cli_store: Path) -> None:
        code, out, _ = _run_cli("--store", str(cli_store), "capability", "issue", "--actor", "a")
        assert code == 0
        assert json.loads(out)["capability"]["scope"] == "read"

    def test_issue_with_verbs_and_ttl(self, cli_store: Path) -> None:
        code, out, _ = _run_cli(
            "--store",
            str(cli_store),
            "capability",
            "issue",
            "--actor",
            "a",
            "--scope",
            "write",
            "--verbs",
            "knowledge_query,knowledge_log_event",
            "--ttl-seconds",
            "60",
        )
        assert code == 0
        cap = json.loads(out)["capability"]
        assert cap["verbs"] == ["knowledge_log_event", "knowledge_query"]  # canonical sort
        assert cap["expires_at"] - cap["issued_at"] == 60

    def test_issue_invalid_scope_usage_error(self, cli_store: Path) -> None:
        with pytest.raises(SystemExit):
            _run_cli("--store", str(cli_store), "capability", "issue", "--actor", "a", "--scope", "root")

    def test_issue_then_verify_roundtrip(self, cli_store: Path) -> None:
        code, out, _ = _run_cli("--store", str(cli_store), "capability", "issue", "--actor", "agent-a")
        assert code == 0
        token = json.loads(out)["token"]
        config = DaemonConfig(db_path=cli_store, graph_root=cli_store.parent / "graph")
        store = CapabilityStore(config.paths.root)
        assert store.verify(token, actor="agent-a").actor == "agent-a"

    def test_list_empty(self, cli_store: Path) -> None:
        _run_cli("--store", str(cli_store), "capability", "issue", "--actor", "a")
        code, out, _ = _run_cli("--store", str(cli_store), "capability", "list")
        assert code == 0
        listing = json.loads(out)
        assert len(listing["issued"]) == 1
        assert listing["revoked"] == []

    def test_list_without_store_fails(self, tmp_path: Path) -> None:
        db = tmp_path / "missing.sqlite3"
        code, _, err = _run_cli("--store", str(db), "capability", "list")
        assert code == 1
        assert "capability" in err.lower() or "secret" in err.lower()

    def test_revoke_by_nonce(self, cli_store: Path) -> None:
        _, out, _ = _run_cli("--store", str(cli_store), "capability", "issue", "--actor", "a")
        nonce = json.loads(out)["capability"]["nonce"]
        code, out, _ = _run_cli("--store", str(cli_store), "capability", "revoke", "--nonce", nonce)
        assert code == 0
        assert json.loads(out)["revoked"] == nonce
        code, out, _ = _run_cli("--store", str(cli_store), "capability", "list")
        assert [e["nonce"] for e in json.loads(out)["revoked"]] == [nonce]

    def test_revoke_by_token(self, cli_store: Path) -> None:
        _, out, _ = _run_cli("--store", str(cli_store), "capability", "issue", "--actor", "a")
        token = json.loads(out)["token"]
        code, out, _ = _run_cli("--store", str(cli_store), "capability", "revoke", "--token", token)
        assert code == 0

    def test_revoke_unknown_fails(self, cli_store: Path) -> None:
        _run_cli("--store", str(cli_store), "capability", "issue", "--actor", "a")
        code, _, err = _run_cli("--store", str(cli_store), "capability", "revoke", "--nonce", "nope")
        assert code == 1
        assert "unknown" in err.lower() or "no such" in err.lower()

    def test_revoked_token_rejected_end_to_end(self, cli_store: Path) -> None:
        _, out, _ = _run_cli("--store", str(cli_store), "capability", "issue", "--actor", "agent-a")
        token = json.loads(out)["token"]
        nonce = json.loads(out)["capability"]["nonce"]
        config = DaemonConfig(db_path=cli_store, graph_root=cli_store.parent / "graph")
        store = CapabilityStore(config.paths.root)
        assert store.verify(token, actor="agent-a").nonce == nonce
        _run_cli("--store", str(cli_store), "capability", "revoke", "--nonce", nonce)
        with pytest.raises(CapabilityRevokedError):
            store.verify(token, actor="agent-a")

    def test_stop_daemon_passes_capability(self, cli_store: Path) -> None:
        """stop_daemon threads an optional capability into daemon.shutdown."""
        config = DaemonConfig(
            db_path=cli_store,
            graph_root=cli_store.parent / "graph",
            capability_mode="enforce",
            idle_timeout_seconds=120.0,
        )
        config.paths.ensure()
        store = CapabilityStore(config.paths.root)
        store.ensure_secret()
        daemon = SuperpowerDaemon(config)
        daemon.start()
        thread = threading.Thread(target=daemon.serve_forever, daemon=True)
        thread.start()
        try:
            token = mint_local_admin_token(store, actor="operator")
            report = stop_daemon(config, capability=token)
            assert report["stopped"] is True
        finally:
            daemon.shutdown("test-teardown", force=True)
            thread.join(timeout=10)

    def test_stop_daemon_without_token_graceful_denied_but_alive(self, cli_store: Path) -> None:
        """Without a token the enforce-mode daemon refuses shutdown and stays up.

        (The SIGKILL escalation leg of stop_daemon targets the pidfile's pid;
        in in-process tests that IS the test process, so it is only exercised
        by the subprocess suites — here we pin the refusal itself.)
        """
        config = DaemonConfig(
            db_path=cli_store,
            graph_root=cli_store.parent / "graph",
            capability_mode="enforce",
            idle_timeout_seconds=120.0,
        )
        config.paths.ensure()
        CapabilityStore(config.paths.root).ensure_secret()
        daemon = SuperpowerDaemon(config)
        daemon.start()
        thread = threading.Thread(target=daemon.serve_forever, daemon=True)
        thread.start()
        try:
            with SocketClient(config.paths.socket) as client:
                denied = client.request("daemon.shutdown", {"force": True})
            assert denied["error"]["code"] == rpc.CAPABILITY_DENIED
            assert daemon._stop.is_set() is False  # noqa: SLF001
        finally:
            daemon.shutdown("test-teardown", force=True)
            thread.join(timeout=10)
