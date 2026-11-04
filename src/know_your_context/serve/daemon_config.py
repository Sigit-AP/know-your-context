"""Daemon configuration: one frozen, fail-closed value object.

Every tunable of the superpower server lives here. Validation happens at
construction (:class:`ConfigError`), so a daemon can never start with a
nonsensical capacity, a watchdog that cannot fire, or an in-memory store
(which would be invisible to every other process).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from know_your_context.contracts import DEFAULT_MAX_TOKEN_BUDGET
from know_your_context.ops.switches import load_switches
from know_your_context.serve.rpc import MAX_FRAME_BYTES

__all__ = [
    "DEFAULT_AUDIT_QUEUE_SIZE",
    "DEFAULT_GROUP_COMMIT_BATCH",
    "DEFAULT_HEAVY_QUEUE_SIZE",
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "DEFAULT_LISTENER_BACKLOG",
    "DEFAULT_LIVENESS_HEARTBEAT_SECONDS",
    "DEFAULT_LIVENESS_STALL_SECONDS",
    "DEFAULT_MAX_AGENTS",
    "DEFAULT_PPID_POLL_SECONDS",
    "DEFAULT_READ_POOL_SIZE",
    "DEFAULT_READ_PUBLISH_WAIT_SECONDS",
    "DEFAULT_READ_QUEUE_SIZE",
    "DEFAULT_RETRY_AFTER_MS",
    "DEFAULT_SPAWN_TIMEOUT_SECONDS",
    "DEFAULT_WRITE_QUEUE_SIZE",
    "HARDWARE_AUTO",
    "HARD_MAX_AGENTS",
    "HARD_MAX_READ_POOL",
    "MIN_FRAME_BYTES",
    "ConfigError",
    "DaemonConfig",
    "DaemonPaths",
]

DEFAULT_MAX_AGENTS = 50
HARD_MAX_AGENTS = 256
DEFAULT_READ_POOL_SIZE = 8
HARD_MAX_READ_POOL = 64
DEFAULT_WRITE_QUEUE_SIZE = 256
DEFAULT_READ_QUEUE_SIZE = 512
DEFAULT_HEAVY_QUEUE_SIZE = 16
DEFAULT_AUDIT_QUEUE_SIZE = 2048
DEFAULT_GROUP_COMMIT_BATCH = 8
DEFAULT_LISTENER_BACKLOG = 128
#: listen() backlog ceiling even on big-RAM hosts (the kernel clamps to
#: somaxconn anyway; this keeps the static budget honest).
HARD_MAX_LISTENER_BACKLOG = 4096
#: Sentinel for DaemonConfig capacity fields: "the operator did not say —
#: derive from the hardware plan (scale-up only from the historical default)".
#: An explicit integer is always honored verbatim, including one that happens
#: to equal the historical default.
HARDWARE_AUTO = -1
DEFAULT_IDLE_TIMEOUT_SECONDS = 300.0
DEFAULT_SPAWN_TIMEOUT_SECONDS = 15.0
DEFAULT_LIVENESS_HEARTBEAT_SECONDS = 0.5
DEFAULT_LIVENESS_STALL_SECONDS = 10.0
DEFAULT_PPID_POLL_SECONDS = 0.5
DEFAULT_READ_PUBLISH_WAIT_SECONDS = 30.0
DEFAULT_RETRY_AFTER_MS = 100
MIN_FRAME_BYTES = 1024


# AF_UNIX sockaddr paths are capped (~108 bytes on Linux); beyond this the
# socket rendezvous moves to a deterministic short path derived from the
# runtime root hash (the lock/pid/ready/exit artifacts stay with the store).
_MAX_SOCKET_PATH_BYTES = 100


class ConfigError(ValueError):
    """A configuration value outside the fail-closed envelope."""


@dataclass(frozen=True)
class DaemonPaths:
    """The runtime artifacts of one daemon; all live under ``root``.

    Exception: when ``root/daemon.sock`` would exceed the AF_UNIX path cap,
    the socket (a transient rendezvous, never state) moves to
    ``$TMPDIR/kys-<sha256(root)[:16]>/daemon.sock`` — the same deterministic
    location for every process looking at this store.
    """

    root: Path

    @property
    def lock(self) -> Path:
        return self.root / "daemon.lock"

    @property
    def pid(self) -> Path:
        return self.root / "daemon.pid"

    @property
    def socket(self) -> Path:
        candidate = self.root / "daemon.sock"
        if len(str(candidate).encode("utf-8")) <= _MAX_SOCKET_PATH_BYTES:
            return candidate
        digest = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:16]
        return Path(tempfile.gettempdir()) / f"kys-{digest}" / "daemon.sock"

    @property
    def ready(self) -> Path:
        return self.root / "daemon.ready"

    @property
    def exit(self) -> Path:
        return self.root / "daemon.exit.json"

    def ensure(self) -> None:
        """Create the runtime root operator-only (0700), idempotently."""
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        socket_parent = self.socket.parent
        if socket_parent != self.root:
            socket_parent.mkdir(parents=True, exist_ok=True)
            os.chmod(socket_parent, 0o700)


@dataclass(frozen=True)
class DaemonConfig:
    """The complete, validated configuration of one daemon per store."""

    db_path: Path | str
    graph_root: Path | str
    runtime_dir: Path | str | None = None
    # Capacity fields default to HARDWARE_AUTO (-1): "the operator did not
    # say — derive from the single HardwarePlan, scale-up only from the
    # historical default" (SUB-135). An explicit integer is always honored
    # verbatim, even one equal to the historical default.
    max_agents: int = HARDWARE_AUTO
    read_pool_size: int = HARDWARE_AUTO
    write_queue_size: int = HARDWARE_AUTO
    read_queue_size: int = HARDWARE_AUTO
    # Fairness (operator requirement W7): per-agent queue quotas — one
    # spamming agent fills only its own lane; round-robin drain keeps every
    # other agent scheduled.
    per_actor_write_cap: int = 64
    per_actor_read_cap: int = 128
    # Admission control (W7): per-agent token bucket; an agent exceeding its
    # sustained rate gets typed SERVER_BUSY with retry_after instead of an
    # unbounded queue.
    agent_rate_per_second: float = 100.0
    agent_burst: int = 200
    # Heavy lane (ingest v3): single preemptible worker, small queue.
    heavy_queue_size: int = HARDWARE_AUTO
    per_actor_heavy_cap: int = 4
    # Audit lane (Tiger Style static budget): the read lanes forward their
    # journal appends through this bounded queue on the single writer. The
    # bound must absorb one begin+complete burst per read worker; overflow
    # is blocking backpressure (the writer drains audit first every loop).
    audit_queue_size: int = HARDWARE_AUTO
    # Session lease: a session with no heartbeat/request within this window is
    # closed and its slot released (the stdio proxy heartbeats transparently).
    session_lease_seconds: float = 30.0
    # Group commit (W7): the writer joins bursts of up to N mutations or a
    # W-millisecond window into ONE SQLite transaction (savepoint nesting),
    # amortizing fsync. Kill switch: KNOW_YOUR_CONTEXT_NO_GROUP_COMMIT=1.
    group_commit: bool = True
    group_commit_batch: int = HARDWARE_AUTO
    group_commit_window_seconds: float = 0.002
    # listen() backlog on the AF_UNIX rendezvous socket (SUB-135: follows
    # the hardware plan's RAM scale when left at HARDWARE_AUTO).
    listener_backlog: int = HARDWARE_AUTO
    # Graceful drain: on shutdown, new work is refused typed while in-flight
    # items finish within this budget.
    drain_timeout_seconds: float = 10.0
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS
    spawn_timeout_seconds: float = DEFAULT_SPAWN_TIMEOUT_SECONDS
    liveness_heartbeat_seconds: float = DEFAULT_LIVENESS_HEARTBEAT_SECONDS
    liveness_stall_seconds: float = DEFAULT_LIVENESS_STALL_SECONDS
    ppid_poll_seconds: float = DEFAULT_PPID_POLL_SECONDS
    read_publish_wait_seconds: float = DEFAULT_READ_PUBLISH_WAIT_SECONDS
    max_frame_bytes: int = MAX_FRAME_BYTES
    max_token_budget: int = DEFAULT_MAX_TOKEN_BUDGET
    retry_after_ms: int = DEFAULT_RETRY_AFTER_MS
    # Deterministic backpressure test hook: the writer sleeps this long after
    # each mutation. Production configs keep it at zero; the concurrency tests
    # use it to fill the bounded write queue on demand.
    mutation_delay_seconds: float = 0.0
    # The actor the stdio proxy stamps onto host requests that carry no
    # ``_meta.actor`` of their own (audit requirement: every request has one).
    proxy_actor: str = "mcp-proxy"
    # W10 capability security (SUB-117): "off" (default) is the exact legacy
    # behavior; "enforce" requires an operator-issued capability token for
    # every non-public verb (see security/capabilities.py).
    capability_mode: str = "off"
    # W17 node leases (SUB-138): when enabled, the write arbiter claims a
    # lease on every node a mutation touches and a second actor's write to a
    # leased node fails typed (NODE_IN_USE, holder + expiry in the error data,
    # conflict recorded in the hash-chained event log). Default off = exact
    # pre-W17 behavior; enable for multi-agent crews sharing one store.
    node_leases_enabled: bool = False
    node_lease_ttl_seconds: float = 120.0
    # W13 worker watchdog (SUB-124): every daemon worker thread (read lanes,
    # heavy lane, acceptor, reaper) carries a heartbeat supervised with
    # tiered suspect → restart → dead escalation. Kill switch: False keeps
    # the exact pre-W13 behavior (only the global LivenessWatchdog).
    worker_watchdog: bool = True
    watchdog_suspect_seconds: float = 15.0
    watchdog_stall_seconds: float = 45.0
    watchdog_max_restarts: int = 2
    watchdog_restart_window_seconds: float = 120.0
    watchdog_check_interval_seconds: float = 5.0
    # W13 flow (SUB-124): explicit socket timeouts for the stdio proxy.
    # The IO bound is generous: heavy-lane ingest of a large tree is a
    # legitimate multi-minute operation.
    proxy_connect_timeout_seconds: float = 10.0
    proxy_io_timeout_seconds: float = 900.0
    # DST hook (SUB-124): a seed makes the proxy reconnect backoff stream
    # reproducible in simulation; None = per-call nondeterministic jitter.
    proxy_retry_seed: int | None = None
    # SUB-135: what the hardware plan derivation actually changed (empty when
    # every value stayed historical). Announcement, never silent.
    hardware_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", Path(self.db_path))
        object.__setattr__(self, "graph_root", Path(self.graph_root))
        # W13 operator override: KNOW_YOUR_CONTEXT_CAPABILITY_MODE applies only
        # when the constructor left the mode at the safe default — the env can
        # raise the gate to "enforce" but never weaken an explicit "enforce".
        if self.capability_mode == "off":
            override = load_switches().capability_mode_override()
            if override is not None:
                object.__setattr__(self, "capability_mode", str(override))
        if self.runtime_dir is None:
            object.__setattr__(self, "runtime_dir", Path(self.db_path).expanduser().resolve().parent / "superpower")
        else:
            object.__setattr__(self, "runtime_dir", Path(self.runtime_dir))
        self._apply_hardware_sizing()
        self._validate()

    def _apply_hardware_sizing(self) -> None:
        """Derive capacity from the single HardwarePlan (SUB-135, scale-up only).

        A field left at its historical default is *auto* and follows the plan
        (``max(default, derived)``, so small hosts keep the historical sizing
        exactly); an explicitly non-default value is honored verbatim. Kill
        switches: ``KNOW_YOUR_CONTEXT_DISABLE_HW_AUTOSCALER`` freezes worker
        and queue sizing, ``KNOW_YOUR_CONTEXT_DISABLE_HW_TUNING`` freezes the
        disk-profile group-commit batch. A failed probe keeps the historical
        recipe. Every derivation is announced in :attr:`hardware_notes`.
        """
        switches = load_switches()
        autoscaler_off = switches.hw_autoscaler_disabled()
        tuning_off = switches.hw_tuning_disabled()
        auto_fields = {
            "max_agents": DEFAULT_MAX_AGENTS,
            "read_pool_size": DEFAULT_READ_POOL_SIZE,
            "write_queue_size": DEFAULT_WRITE_QUEUE_SIZE,
            "read_queue_size": DEFAULT_READ_QUEUE_SIZE,
            "heavy_queue_size": DEFAULT_HEAVY_QUEUE_SIZE,
            "audit_queue_size": DEFAULT_AUDIT_QUEUE_SIZE,
            "group_commit_batch": DEFAULT_GROUP_COMMIT_BATCH,
            "listener_backlog": DEFAULT_LISTENER_BACKLOG,
        }
        # An explicit operator value is honored verbatim, even one equal to
        # the historical default; HARDWARE_AUTO means "derive from the plan".
        auto = {field for field in auto_fields if int(getattr(self, field)) == HARDWARE_AUTO}
        for field, historical in auto_fields.items():
            if field not in auto:
                continue
            object.__setattr__(self, field, historical)
        if autoscaler_off and tuning_off:
            object.__setattr__(
                self,
                "hardware_notes",
                ("hardware sizing frozen by kill switches (autoscaler + tuning); historical recipe",),
            )
            return
        try:
            from know_your_context.hardware.profile import hardware_plan

            plan = hardware_plan(Path(self.db_path))
        except Exception as error:  # noqa: BLE001 - a probe failure must never break daemon config
            object.__setattr__(
                self,
                "hardware_notes",
                (f"hardware plan unavailable ({error}); fallback to historical sizing",),
            )
            return

        notes: list[str] = []

        def _bump(field: str, derived: int) -> None:
            """Scale-up only, and only for fields the operator left AUTO."""
            if field not in auto:
                return
            historical = auto_fields[field]
            if derived > historical:
                object.__setattr__(self, field, derived)
                notes.append(f"{field}: {historical} -> {derived} (hardware plan)")

        if not autoscaler_off:
            scale = plan.queue_scale()
            workers = plan.base_workers()
            _bump("read_pool_size", min(HARD_MAX_READ_POOL, max(2, workers)))
            _bump("write_queue_size", DEFAULT_WRITE_QUEUE_SIZE * scale)
            _bump("read_queue_size", DEFAULT_READ_QUEUE_SIZE * scale)
            _bump("heavy_queue_size", DEFAULT_HEAVY_QUEUE_SIZE * scale)
            # 256 audit slots per read worker preserves the historical ratio
            # (2048 for 8 readers); the >= 2x pool invariant always holds.
            _bump("audit_queue_size", max(DEFAULT_AUDIT_QUEUE_SIZE, 256 * workers))
            _bump("listener_backlog", min(HARD_MAX_LISTENER_BACKLOG, DEFAULT_LISTENER_BACKLOG * scale))
            _bump("max_agents", min(HARD_MAX_AGENTS, max(DEFAULT_MAX_AGENTS, workers * 8)))
        else:
            notes.append("autoscaler kill switch: pool/queue/backlog/agent sizing stays historical")
        if not tuning_off:
            _bump("group_commit_batch", plan.storage.profile.group_commit_batch)
        else:
            notes.append("hw tuning kill switch: group-commit batch stays historical")
        object.__setattr__(self, "hardware_notes", tuple(notes))

    def _validate(self) -> None:
        if str(self.db_path) == ":memory:":
            raise ConfigError("the daemon requires a file-backed store; ':memory:' is unreachable across processes")
        if not 1 <= int(self.max_agents) <= HARD_MAX_AGENTS:
            raise ConfigError(f"max_agents must be in [1, {HARD_MAX_AGENTS}], got {self.max_agents}")
        if not 1 <= int(self.read_pool_size) <= HARD_MAX_READ_POOL:
            raise ConfigError(f"read_pool_size must be in [1, {HARD_MAX_READ_POOL}], got {self.read_pool_size}")
        if int(self.write_queue_size) < 1:
            raise ConfigError(f"write_queue_size must be >= 1, got {self.write_queue_size}")
        if int(self.read_queue_size) < 1:
            raise ConfigError(f"read_queue_size must be >= 1, got {self.read_queue_size}")
        if not 1 <= int(self.per_actor_write_cap) <= int(self.write_queue_size):
            raise ConfigError(
                f"per_actor_write_cap must be in [1, write_queue_size={self.write_queue_size}], "
                f"got {self.per_actor_write_cap}"
            )
        if not 1 <= int(self.per_actor_read_cap) <= int(self.read_queue_size):
            raise ConfigError(
                f"per_actor_read_cap must be in [1, read_queue_size={self.read_queue_size}], "
                f"got {self.per_actor_read_cap}"
            )
        if float(self.agent_rate_per_second) <= 0:
            raise ConfigError("agent_rate_per_second must be positive")
        if int(self.agent_burst) < 1:
            raise ConfigError("agent_burst must be >= 1")
        if int(self.heavy_queue_size) < 1 or not 1 <= int(self.per_actor_heavy_cap) <= int(self.heavy_queue_size):
            raise ConfigError("heavy queue caps out of range")
        if int(self.audit_queue_size) < 2 * int(self.read_pool_size):
            raise ConfigError(
                f"audit_queue_size ({self.audit_queue_size}) must absorb a begin+complete burst "
                f"from every read worker (>= 2 * read_pool_size={self.read_pool_size})"
            )
        if float(self.session_lease_seconds) <= 0:
            raise ConfigError("session_lease_seconds must be positive")
        if not 0 < float(self.node_lease_ttl_seconds) <= 86400.0:
            raise ConfigError(f"node_lease_ttl_seconds must be in (0, 86400], got {self.node_lease_ttl_seconds}")
        if int(self.group_commit_batch) < 1:
            raise ConfigError("group_commit_batch must be >= 1")
        if not 1 <= int(self.listener_backlog) <= HARD_MAX_LISTENER_BACKLOG:
            raise ConfigError(
                f"listener_backlog must be in [1, {HARD_MAX_LISTENER_BACKLOG}], got {self.listener_backlog}"
            )
        if float(self.group_commit_window_seconds) < 0:
            raise ConfigError("group_commit_window_seconds must be >= 0")
        if float(self.drain_timeout_seconds) <= 0:
            raise ConfigError("drain_timeout_seconds must be positive")
        if float(self.idle_timeout_seconds) <= 0:
            raise ConfigError(f"idle_timeout_seconds must be positive, got {self.idle_timeout_seconds}")
        if float(self.spawn_timeout_seconds) <= 0:
            raise ConfigError(f"spawn_timeout_seconds must be positive, got {self.spawn_timeout_seconds}")
        if float(self.liveness_heartbeat_seconds) <= 0:
            raise ConfigError("liveness_heartbeat_seconds must be positive")
        if float(self.liveness_stall_seconds) <= float(self.liveness_heartbeat_seconds):
            raise ConfigError(
                f"liveness_stall_seconds ({self.liveness_stall_seconds}) must exceed "
                f"liveness_heartbeat_seconds ({self.liveness_heartbeat_seconds})"
            )
        if float(self.ppid_poll_seconds) <= 0:
            raise ConfigError("ppid_poll_seconds must be positive")
        if float(self.read_publish_wait_seconds) <= 0:
            raise ConfigError("read_publish_wait_seconds must be positive")
        if int(self.max_frame_bytes) < MIN_FRAME_BYTES:
            raise ConfigError(f"max_frame_bytes must be >= {MIN_FRAME_BYTES}, got {self.max_frame_bytes}")
        if int(self.retry_after_ms) < 0:
            raise ConfigError("retry_after_ms must be >= 0")
        if float(self.mutation_delay_seconds) < 0:
            raise ConfigError("mutation_delay_seconds must be >= 0")
        if not str(self.proxy_actor).strip():
            raise ConfigError("proxy_actor must be a non-empty string")
        if self.capability_mode not in ("off", "enforce"):
            raise ConfigError(f"capability_mode must be 'off' or 'enforce', got {self.capability_mode!r}")
        if not isinstance(self.worker_watchdog, bool):
            raise ConfigError("worker_watchdog must be a bool")
        if float(self.watchdog_suspect_seconds) <= 0:
            raise ConfigError("watchdog_suspect_seconds must be positive")
        if float(self.watchdog_stall_seconds) < float(self.watchdog_suspect_seconds):
            raise ConfigError(
                f"watchdog_stall_seconds ({self.watchdog_stall_seconds}) must be >= "
                f"watchdog_suspect_seconds ({self.watchdog_suspect_seconds})"
            )
        # Deadline separation (SUB-132): the read lane's longest BOUNDED
        # silence is read_publish_wait_seconds (the freshness wait). A stall
        # threshold inside that window would false-positive on healthy
        # workers — fail closed on the ambiguity. (The heavy lane's ingests
        # are unbounded by design; there the watchdog only alerts, because a
        # live worker's restart is always refused.)
        if float(self.watchdog_stall_seconds) <= float(self.read_publish_wait_seconds):
            raise ConfigError(
                f"watchdog_stall_seconds ({self.watchdog_stall_seconds}) must exceed "
                f"read_publish_wait_seconds ({self.read_publish_wait_seconds}) — the read lane "
                f"may legitimately wait that long for a publish"
            )
        if int(self.watchdog_max_restarts) < 1:
            raise ConfigError("watchdog_max_restarts must be >= 1")
        if float(self.watchdog_restart_window_seconds) <= 0:
            raise ConfigError("watchdog_restart_window_seconds must be positive")
        if float(self.watchdog_check_interval_seconds) <= 0:
            raise ConfigError("watchdog_check_interval_seconds must be positive")
        if float(self.proxy_connect_timeout_seconds) <= 0:
            raise ConfigError("proxy_connect_timeout_seconds must be positive")
        if float(self.proxy_io_timeout_seconds) < float(self.proxy_connect_timeout_seconds):
            raise ConfigError(
                f"proxy_io_timeout_seconds ({self.proxy_io_timeout_seconds}) must be >= "
                f"proxy_connect_timeout_seconds ({self.proxy_connect_timeout_seconds})"
            )
        if self.proxy_retry_seed is not None and (
            isinstance(self.proxy_retry_seed, bool) or not isinstance(self.proxy_retry_seed, int)
        ):
            raise ConfigError(f"proxy_retry_seed must be an int or None, got {self.proxy_retry_seed!r}")

    @property
    def paths(self) -> DaemonPaths:
        return DaemonPaths(Path(self.runtime_dir).expanduser().resolve())  # type: ignore[arg-type]
