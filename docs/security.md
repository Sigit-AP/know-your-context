# Security model

Know Your Context is a local, deterministic context engine. Its security
model has four layers: the **no-AI runtime boundary**, **network
containment**, **capability-based authorization** for the multi-agent
daemon, and **supply-chain + audit** guarantees. Every layer fails closed:
a broken control denies, never silently permits.

## 1. The no-AI runtime boundary

The product never calls a model, an embedding API or a vector store — and
this is enforced, not promised:

- `runtime_guard.py` asserts at runtime that no forbidden distributions are
  importable, that no provider credentials exist in the environment, and
  that no model-asset environment variables are set. The MCP server refuses
  to start when provider credentials are present.
- `tests/test_no_ai_runtime.py` scans the runtime surface so a new module
  cannot join the serving path without re-facing the guard.
- External vision/LLM extraction is still possible, but only through the
  contract-v4 `submit_extraction` verb: hash-bound to the source file,
  tagged `agent-extracted`, review-gated, never auto-promoted.

## 2. Network containment

- **Loopback by default.** The HTTP API and the web view bind to loopback
  with a host allowlist (`localhost`, `127.0.0.1`, `::1`); the web view adds
  anti-DNS-rebinding checks and issues a token in the URL, then moves the
  session to a cookie after first load.
- **Unix socket, mode 0600.** The superpower daemon listens on a unix socket
  created `0600` — only the local user can connect.
- **No outbound.** Nothing in the serving path opens outbound sockets; the
  runtime guard treats that as a violation.
- **Bounded input.** Request bodies are byte-capped with a total deadline;
  streaming bodies have their own cap and timeout; internal error envelopes
  are sanitized (no raw exception text, secrets redacted on every surface).

## 3. Capability security (W10)

Every agent connected to the superpower daemon can be constrained by an
operator-issued capability token (`security/capabilities.py`):

- **Token**: `kyc1.<base64url(payload)>.<base64url(HMAC-SHA256)>` over
  canonical JSON `{actor, verbs, scope, expires_at, nonce, issued_at}`.
  Verification is a single `hmac.compare_digest` — constant time, no oracle.
- **Root of trust**: a 32-byte secret in `<runtime>/capability-secret`
  (mode `0600`) or the `KNOW_YOUR_CONTEXT_CAPABILITY_SECRET` environment
  variable (hex). Nothing leaves the machine.
- **Revocation without restart**: the registry file is rewritten atomically
  on every issue/revoke and reloaded on `mtime` change, so a revocation is
  effective on the very next verification.
- **Audit**: denials and decisions go to an append-only JSONL hash chain
  (`capability-audit.jsonl` + head marker); truncation and tampering are
  detectable.
- **Enforcement**: `--capability-mode enforce` (default `off`, preserving
  prior behavior). Denials surface as JSON-RPC `CAPABILITY_DENIED` (-32009).
  Without a token, only public read verbs are reachable; `daemon shutdown`
  requires an admin-scoped token.
- Failure modes are all typed `CapabilityError` subclasses: malformed,
  forged, expired, revoked, actor-mismatch, store-corruption. Fail closed.

Manage tokens with the CLI: `capability issue`, `capability revoke`,
`capability list` (see [api.md](api.md)).

## 4. Supply chain and audit

- All runtime dependencies are pinned exactly (`==`) in `pyproject.toml`;
  CI runs `bandit` and `pip-audit` as blocking gates, plus a license
  attribution guard.
- pypdf is pinned to 6.21.4 (CVE-2026-119203 / CVE-2026-119204 /
  CVE-2026-119205 — see [CHANGELOG.md](../CHANGELOG.md)).
- Every mutation is an event in the hash-chained, append-only store; the
  chain is verifiable (`lifecycle record`/`trend`, `migrate verify`), and
  rollback restores byte-exact pre-apply state.

## Threat model

| Threat | Control | Failure mode |
|---|---|---|
| Agent calls a model / provider from inside the product | Runtime guard + startup refusal | Fail closed: the process refuses to run |
| Malicious or misprompted agent issues write verbs | Capability tokens (W10), scope lanes | Typed `CAPABILITY_DENIED`, audited |
| Token theft or replay | HMAC over canonical payload, expiry, nonce, actor binding | Forgery/replay rejected by `compare_digest` |
| Revocation lag after compromise | Atomic registry reload on mtime | Effective on the next verification, no restart |
| DNS rebinding against the web view | Host allowlist + anti-rebinding + token URL → session cookie | Request rejected before routing |
| Local privilege snooping of the daemon | Unix socket `0600`, secret file `0600` | Other users cannot connect or read |
| Resource exhaustion via huge bodies or slow clients | Byte caps, total deadline, streaming cap + timeout | `PAYLOAD_TOO_LARGE`, typed timeout |
| Error responses leaking internals | Sanitized envelopes, redacted secrets everywhere | `INTERNAL_ERROR` with generic text |
| Audit trail tampering | Hash-chained event log + capability audit head marker | Truncation/tampering detectable on verify |
| Dependency CVEs | Exact pins, blocking `bandit` + `pip-audit` in CI | Build fails, never ships |

## Reading a denial

A denial always tells you which layer fired: `VALIDATION_ERROR` (adapter
boundary), `ACTOR_REQUIRED` / `SCOPE_DENIED` / `CAPABILITY_DENIED`
(daemon), `PATH_OUTSIDE_ROOT` (containment), `SERVER_BUSY` (bounded
capacity, with `retry_after_ms`). No denial path retries silently or logs a
secret.
