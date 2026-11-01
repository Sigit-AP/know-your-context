"""Capability-based security for superpower agents (W10, SUB-117).

The problem: every agent connected to the superpower daemon used to hold the
same power — one malicious or misprompted agent could call any write verb.
This module adds operator-issued capability tokens that bind an agent to an
explicit verb allowlist, a scope (read / write / admin) and an expiry, with
instant revocation and a hash-chained audit trail.

Design (SOTA bearer-capability pattern, macaroons lineage, local-only):

* **Token**: ``kyc1.<base64url(payload)>.<base64url(HMAC-SHA256)>``. The
  payload is canonical JSON (sorted keys, tight separators) carrying exactly
  ``{actor, verbs, scope, expires_at, nonce, issued_at}`` — issuance is
  deterministic, and verification is a single ``hmac.compare_digest`` over
  the exact signed bytes (constant-time, no oracle).
* **Root of trust**: a 32-byte secret in ``<runtime>/capability-secret``
  (mode 0600) or the ``KNOW_YOUR_CONTEXT_CAPABILITY_SECRET`` env var (hex).
  Whoever holds the file is the operator; nothing leaves the machine.
* **Revocation**: a registry file rewritten atomically on every
  issue/revoke; verifiers stat it per call and reload on change, so a
  revocation is effective on the very next verification — no daemon restart.
* **Audit**: a separate append-only JSONL hash chain
  (``capability-audit.jsonl`` + ``capability-audit.head``) so security
  denials are recorded even when the domain store's writer lane is busy.
  Truncation and tampering are detectable via the head marker.

Fail closed everywhere: malformed, forged, expired, revoked, actor-mismatch
and store-corruption all raise typed :class:`CapabilityError` subclasses.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import secrets
import stat
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from know_your_context.security import SecurityError

__all__ = [
    "AUDIT_HEAD_NAME",
    "AUDIT_LOG_NAME",
    "REGISTRY_NAME",
    "SCOPES",
    "SCOPE_LANES",
    "SECRET_ENV_VAR",
    "SECRET_NAME",
    "TOKEN_PREFIX",
    "Capability",
    "CapabilityActorMismatchError",
    "CapabilityError",
    "CapabilityExpiredError",
    "CapabilityForgedError",
    "CapabilityMalformedError",
    "CapabilityRevokedError",
    "CapabilityStore",
    "CapabilityStoreError",
    "decode_token",
    "generate_secret",
    "issue_token",
    "mint_local_admin_token",
    "verify_token",
]

TOKEN_PREFIX = "kyc1"  # nosec B105 -- wire-format prefix, not a credential
SCOPES: tuple[str, ...] = ("read", "write", "admin")
# Scope -> the dispatch lanes it may enter. Lane assignment of individual
# verbs lives in serve.superpower (write semantics); this module only knows
# the lane lattice.
SCOPE_LANES: dict[str, frozenset[str]] = {
    "read": frozenset({"read"}),
    "write": frozenset({"read", "write", "heavy"}),
    "admin": frozenset({"read", "write", "heavy", "admin"}),
}
SECRET_NAME = "capability-secret"  # nosec B105 -- file NAME, not a credential
REGISTRY_NAME = "capabilities.json"
AUDIT_LOG_NAME = "capability-audit.jsonl"
AUDIT_HEAD_NAME = "capability-audit.head"
SECRET_ENV_VAR = "KNOW_YOUR_CONTEXT_CAPABILITY_SECRET"  # nosec B105 -- env var NAME, not a credential

_PAYLOAD_KEYS = frozenset({"actor", "verbs", "scope", "expires_at", "nonce", "issued_at"})
_MAX_ACTOR_CHARS = 128
_MAX_VERBS = 64
_MAX_VERB_CHARS = 128
_MAX_NONCE_CHARS = 128
_GENESIS_HASH = "0" * 64


class CapabilityError(SecurityError):
    """Base class for fail-closed capability decisions."""


class CapabilityMalformedError(CapabilityError):
    """The token does not parse or its payload violates the schema."""


class CapabilityForgedError(CapabilityError):
    """The HMAC signature does not match the payload under the local secret."""


class CapabilityExpiredError(CapabilityError):
    """The token's expiry has passed (boundary inclusive)."""


class CapabilityRevokedError(CapabilityError):
    """The token's nonce is on the operator's revocation list."""


class CapabilityActorMismatchError(CapabilityError):
    """The token was issued for a different actor than the requester."""


class CapabilityStoreError(CapabilityError):
    """The local capability store is missing or corrupt; fail closed."""


# --------------------------------------------------------------------- model


@dataclass(frozen=True)
class Capability:
    """A verified (or decoded-for-inspection) capability payload."""

    actor: str
    verbs: frozenset[str]
    scope: str
    expires_at: float
    nonce: str
    issued_at: float

    def allows_lane(self, lane: str) -> bool:
        return lane in SCOPE_LANES[self.scope]

    def allows_verb(self, verb: str) -> bool:
        return verb in self.verbs

    def expired(self, now: float) -> bool:
        return float(now) >= self.expires_at

    def record(self) -> dict[str, Any]:
        """The operator-facing registry view (never carries the token)."""
        return {
            "nonce": self.nonce,
            "actor": self.actor,
            "scope": self.scope,
            "verbs": sorted(self.verbs),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


# --------------------------------------------------------------------- codec


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise CapabilityMalformedError(f"invalid base64url segment: {error}") from None


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapabilityMalformedError(f"{field} must be a finite number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise CapabilityMalformedError(f"{field} must be a non-negative finite number")
    return number


def _validate_fields(payload: Mapping[str, Any]) -> Capability:
    if set(payload) != _PAYLOAD_KEYS:
        missing = sorted(_PAYLOAD_KEYS - set(payload))
        extra = sorted(set(payload) - _PAYLOAD_KEYS)
        raise CapabilityMalformedError(
            f"payload keys must be exactly {sorted(_PAYLOAD_KEYS)}; missing={missing} extra={extra}"
        )
    actor = payload["actor"]
    if not isinstance(actor, str) or not actor.strip() or len(actor) > _MAX_ACTOR_CHARS:
        raise CapabilityMalformedError("actor must be a non-empty string of at most 128 characters")
    scope = payload["scope"]
    if scope not in SCOPES:
        raise CapabilityMalformedError(f"scope must be one of {list(SCOPES)}, got {scope!r}")
    verbs = payload["verbs"]
    if not isinstance(verbs, list) or len(verbs) > _MAX_VERBS:
        raise CapabilityMalformedError(f"verbs must be a list of at most {_MAX_VERBS} strings")
    for verb in verbs:
        if not isinstance(verb, str) or not verb or len(verb) > _MAX_VERB_CHARS:
            raise CapabilityMalformedError("every verb must be a non-empty string of at most 128 characters")
    nonce = payload["nonce"]
    if not isinstance(nonce, str) or not nonce or len(nonce) > _MAX_NONCE_CHARS:
        raise CapabilityMalformedError("nonce must be a non-empty string of at most 128 characters")
    return Capability(
        actor=actor.strip(),
        verbs=frozenset(verbs),
        scope=str(scope),
        expires_at=_validate_number(payload["expires_at"], "expires_at"),
        nonce=nonce,
        issued_at=_validate_number(payload["issued_at"], "issued_at"),
    )


def _sign(secret: bytes, payload_b64: str) -> str:
    return _b64encode(hmac.new(secret, payload_b64.encode("utf-8"), hashlib.sha256).digest())


def _split_token(token: str) -> tuple[str, str]:
    if not isinstance(token, str):
        raise CapabilityMalformedError(f"a capability token is a string, got {type(token).__name__}")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX or not parts[1] or not parts[2]:
        raise CapabilityMalformedError("a capability token is '<prefix>.<payload>.<signature>'")
    return parts[1], parts[2]


def issue_token(
    secret: bytes,
    *,
    actor: str,
    scope: str,
    verbs: Sequence[str],
    expires_at: float,
    nonce: str,
    issued_at: float,
) -> str:
    """Mint a signed token; deterministic for identical inputs."""
    if isinstance(verbs, str) or not isinstance(verbs, Sequence):
        raise CapabilityMalformedError(f"verbs must be a sequence of strings, got {type(verbs).__name__}")
    # Validate BEFORE any numeric coercion so a bool/NaN can never be
    # laundered through float(); then sign the normalized payload.
    raw = {
        "actor": actor,
        "verbs": sorted({str(verb) for verb in verbs}),
        "scope": scope,
        "expires_at": expires_at,
        "nonce": nonce,
        "issued_at": issued_at,
    }
    capability = _validate_fields(raw)  # fail closed before anything is signed
    payload = {
        "actor": capability.actor,
        "verbs": sorted(capability.verbs),
        "scope": capability.scope,
        "expires_at": capability.expires_at,
        "nonce": capability.nonce,
        "issued_at": capability.issued_at,
    }
    payload_b64 = _b64encode(_canonical(payload).encode("utf-8"))
    return f"{TOKEN_PREFIX}.{payload_b64}.{_sign(secret, payload_b64)}"


def decode_token(token: str) -> Capability:
    """Parse a token WITHOUT verifying the signature (inspection only)."""
    payload_b64, _ = _split_token(token)
    try:
        decoded = json.loads(_b64decode(payload_b64))
    except json.JSONDecodeError as error:
        raise CapabilityMalformedError(f"payload is not JSON: {error.msg}") from None
    if not isinstance(decoded, dict):
        raise CapabilityMalformedError("payload must be a JSON object")
    return _validate_fields(decoded)


def verify_token(
    secret: bytes,
    token: str,
    *,
    now: float | None = None,
    actor: str | None = None,
    revoked: frozenset[str] = frozenset(),
) -> Capability:
    """Verify a token end to end; raises a typed :class:`CapabilityError`.

    Order is deliberate: shape, then signature (constant-time), then
    expiry/revocation/actor — an unsigned token never learns anything about
    expiry or revocation state.
    """
    payload_b64, signature = _split_token(token)
    capability = decode_token(token)
    expected = _sign(secret, payload_b64)
    if not hmac.compare_digest(expected, signature):
        raise CapabilityForgedError("capability signature mismatch")
    moment = time.time() if now is None else float(now)
    if capability.expired(moment):
        raise CapabilityExpiredError(f"capability expired at {capability.expires_at}")
    if capability.nonce in revoked:
        raise CapabilityRevokedError(f"capability {capability.nonce} has been revoked")
    if actor is not None and actor != capability.actor:
        raise CapabilityActorMismatchError(f"capability was issued for {capability.actor!r}, not {actor!r}")
    return capability


def generate_secret() -> bytes:
    """A fresh 32-byte HMAC secret."""
    return secrets.token_bytes(32)


# --------------------------------------------------------------------- store


def _default_scope_verbs() -> dict[str, frozenset[str]]:
    """The frozen tool surface per scope (lazy import: serve imports us)."""
    from know_your_context.serve.superpower import HEAVY_TOOLS, READ_TOOLS, WRITE_TOOLS

    write = frozenset(READ_TOOLS | WRITE_TOOLS | HEAVY_TOOLS)
    return {"read": frozenset(READ_TOOLS), "write": write, "admin": write}


class CapabilityStore:
    """The file-backed operator store: secret, registry, audit chain.

    Everything lives under one root (the daemon's runtime dir, mode 0700).
    The registry is re-read whenever its ``(mtime_ns, size)`` changes, so
    revocations issued by any process are effective on the next verify.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._registry_cache: tuple[tuple[int, int] | None, dict[str, Any]] = (None, {})
        self._audit_lock = threading.Lock()
        self._registry_lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def secret_path(self) -> Path:
        return self._root / SECRET_NAME

    @property
    def registry_path(self) -> Path:
        return self._root / REGISTRY_NAME

    @property
    def audit_path(self) -> Path:
        return self._root / AUDIT_LOG_NAME

    @property
    def audit_head_path(self) -> Path:
        return self._root / AUDIT_HEAD_NAME

    # ------------------------------------------------------------- secret

    def ensure_secret(self) -> bytes:
        """Return the local secret, creating it operator-only on first use."""
        try:
            return self.load_secret()
        except CapabilityStoreError:
            pass
        secret = generate_secret()
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._root / f".{SECRET_NAME}.tmp"
        tmp.write_text(secret.hex(), encoding="utf-8")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, self.secret_path)
        return secret

    def load_secret(self) -> bytes:
        """The env var wins over the file; both fail closed on bad shape."""
        env = os.environ.get(SECRET_ENV_VAR)
        if env:
            return self._parse_secret(env.strip(), source=f"${SECRET_ENV_VAR}")
        try:
            text = self.secret_path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise CapabilityStoreError(f"no capability secret at {self.secret_path}: {error}") from error
        return self._parse_secret(text, source=str(self.secret_path))

    @staticmethod
    def _parse_secret(text: str, *, source: str) -> bytes:
        try:
            raw = bytes.fromhex(text)
        except ValueError:
            raise CapabilityStoreError(f"capability secret at {source} is not hex") from None
        if len(raw) != 32:
            raise CapabilityStoreError(f"capability secret at {source} must be 32 bytes, got {len(raw)}")
        return raw

    # ------------------------------------------------------------ registry

    def _registry_fingerprint(self) -> tuple[int, int] | None:
        try:
            info = self.registry_path.stat()
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def _load_registry(self) -> dict[str, Any]:
        fingerprint = self._registry_fingerprint()
        with self._registry_lock:
            cached_fingerprint, cached = self._registry_cache
            if fingerprint == cached_fingerprint and fingerprint is not None:
                return json.loads(json.dumps(cached))  # defensive copy
            if fingerprint is None:
                registry: dict[str, Any] = {"version": 1, "issued": {}, "revoked": {}}
            else:
                try:
                    registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise CapabilityStoreError(
                        f"capability registry at {self.registry_path} is unreadable: {error}"
                    ) from error
                if (
                    not isinstance(registry, dict)
                    or registry.get("version") != 1
                    or not isinstance(registry.get("issued"), dict)
                    or not isinstance(registry.get("revoked"), dict)
                ):
                    raise CapabilityStoreError(f"capability registry at {self.registry_path} has an invalid shape")
            self._registry_cache = (fingerprint, registry)
            return json.loads(json.dumps(registry))

    def _write_registry(self, registry: Mapping[str, Any]) -> None:
        with self._registry_lock:
            self._root.mkdir(parents=True, exist_ok=True)
            tmp = self._root / f".{REGISTRY_NAME}.tmp"
            tmp.write_text(_canonical(registry) + "\n", encoding="utf-8")
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp, self.registry_path)
            self._registry_cache = (self._registry_fingerprint(), dict(registry))

    def _revoked_nonces(self) -> frozenset[str]:
        return frozenset(self._load_registry()["revoked"])

    # -------------------------------------------------------------- issue

    def issue(
        self,
        *,
        actor: str,
        scope: str,
        verbs: Sequence[str] | None = None,
        ttl_seconds: float = 3600.0,
        expires_at: float | None = None,
        nonce: str | None = None,
        now: float | None = None,
    ) -> tuple[str, Capability]:
        """Issue, record and audit one capability; returns (token, payload)."""
        secret = self.load_secret()
        moment = time.time() if now is None else float(now)
        if expires_at is None:
            expires_at = moment + float(ttl_seconds)
        chosen_verbs = list(verbs) if verbs is not None else sorted(_default_scope_verbs().get(scope, ()))
        scope_verbs = _default_scope_verbs().get(scope)
        if scope_verbs is not None:
            outside = sorted(set(chosen_verbs) - scope_verbs)
            if outside:
                raise CapabilityStoreError(f"verbs outside the {scope!r} scope: {outside}")
        chosen_nonce = nonce or secrets.token_hex(16)
        token = issue_token(
            secret,
            actor=actor,
            scope=scope,
            verbs=chosen_verbs,
            expires_at=expires_at,
            nonce=chosen_nonce,
            issued_at=moment,
        )
        capability = decode_token(token)
        registry = self._load_registry()
        if chosen_nonce in registry["issued"]:
            raise CapabilityStoreError(f"capability nonce {chosen_nonce!r} is already issued")
        registry["issued"][chosen_nonce] = capability.record()
        self._write_registry(registry)
        self.audit(
            {
                "event": "capability_issued",
                "actor": capability.actor,
                "scope": capability.scope,
                "nonce": capability.nonce,
                "expires_at": capability.expires_at,
            },
            now=moment,
        )
        return token, capability

    # ------------------------------------------------------------- revoke

    def revoke(self, nonce: str, *, now: float | None = None) -> dict[str, Any]:
        """Revoke one issued nonce; effective on the very next verify."""
        registry = self._load_registry()
        if nonce not in registry["issued"]:
            raise CapabilityStoreError(f"unknown capability nonce {nonce!r}")
        if nonce in registry["revoked"]:
            raise CapabilityStoreError(f"capability nonce {nonce!r} is already revoked")
        moment = time.time() if now is None else float(now)
        registry["revoked"][nonce] = {"revoked_at": moment}
        self._write_registry(registry)
        self.audit({"event": "capability_revoked", "nonce": nonce}, now=moment)
        return {"revoked": nonce, "revoked_at": moment}

    def revoke_token(self, token: str, *, now: float | None = None) -> dict[str, Any]:
        return self.revoke(decode_token(token).nonce, now=now)

    def is_revoked(self, nonce: str) -> bool:
        return nonce in self._revoked_nonces()

    def list_registry(self) -> dict[str, Any]:
        """Operator view: issued capabilities + revoked nonces, sorted."""
        registry = self._load_registry()
        revoked = registry["revoked"]
        issued = []
        for nonce in sorted(registry["issued"]):
            entry = dict(registry["issued"][nonce])
            entry["revoked"] = nonce in revoked
            issued.append(entry)
        revoked_list = [{"nonce": nonce, "revoked_at": revoked[nonce].get("revoked_at")} for nonce in sorted(revoked)]
        return {"issued": issued, "revoked": revoked_list}

    # ------------------------------------------------------------- verify

    def verify(self, token: str, *, actor: str | None = None, now: float | None = None) -> Capability:
        """Verify against the local secret and the live revocation registry."""
        return verify_token(
            self.load_secret(),
            token,
            now=now,
            actor=actor,
            revoked=self._revoked_nonces(),
        )

    # -------------------------------------------------------------- audit

    def audit(self, event: Mapping[str, Any], *, now: float | None = None) -> None:
        """Append one hash-chained audit event (seq, prev, at, ..., hash)."""
        moment = time.time() if now is None else float(now)
        with self._audit_lock:
            seq = 1
            prev = _GENESIS_HASH
            try:
                with self.audit_path.open("rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() > 0:
                        # Count lines and read the tail hash; the log is the
                        # operator's security record and stays small by
                        # policy (denials + lifecycle, never payloads).
                        handle.seek(0)
                        lines = handle.read().splitlines()
                        seq = len(lines) + 1
                        prev = str(json.loads(lines[-1])["hash"])
            except FileNotFoundError:
                pass
            except (OSError, json.JSONDecodeError, KeyError) as error:
                raise CapabilityStoreError(
                    f"capability audit log at {self.audit_path} is unreadable: {error}"
                ) from error
            record: dict[str, Any] = {"seq": seq, "prev": prev, "at": moment, **dict(event)}
            record["hash"] = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
            self._root.mkdir(parents=True, exist_ok=True)
            fresh = not self.audit_path.exists()
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(_canonical(record) + "\n")
            if fresh:
                os.chmod(self.audit_path, stat.S_IRUSR | stat.S_IWUSR)
            tmp = self._root / f".{AUDIT_HEAD_NAME}.tmp"
            tmp.write_text(f"{seq} {record['hash']}\n", encoding="utf-8")
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp, self.audit_head_path)

    def audit_events(self) -> list[dict[str, Any]]:
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        events = []
        for line in lines:
            if line.strip():
                events.append(json.loads(line))
        return events

    def verify_audit_chain(self) -> bool:
        """True when the chain is intact from genesis to the head marker.

        Detects tampering (hash/prev mismatch), reordering (seq gaps) and
        truncation (the head marker names the newest seq+hash).
        """
        try:
            events = self.audit_events()
        except json.JSONDecodeError:
            return False
        prev = _GENESIS_HASH
        for expected_seq, event in enumerate(events, start=1):
            if event.get("seq") != expected_seq or event.get("prev") != prev:
                return False
            body = {key: value for key, value in event.items() if key != "hash"}
            digest = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
            if event.get("hash") != digest:
                return False
            prev = digest
        try:
            head = self.audit_head_path.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            return not events
        if len(head) != 2:
            return False
        if not events:
            return False
        return head[0] == str(events[-1]["seq"]) and head[1] == events[-1]["hash"]


def mint_local_admin_token(
    store: CapabilityStore,
    *,
    actor: str = "operator",
    ttl_seconds: float = 60.0,
    now: float | None = None,
) -> str | None:
    """A short-lived admin token minted by the secret holder; None without a secret.

    The operator CLI uses this to keep ``daemon stop`` graceful under
    capability enforcement: holding the secret file IS the root of trust.
    """
    try:
        token, _ = store.issue(actor=actor, scope="admin", ttl_seconds=ttl_seconds, now=now)
    except CapabilityStoreError as error:
        if "secret" in str(error):
            return None
        raise
    return token
