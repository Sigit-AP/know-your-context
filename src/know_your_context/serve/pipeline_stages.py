"""Explicit staged pipeline with per-stage contracts, checkpoints, resume (W13).

The classic ingestion path (``IngestionPipeline.ingest_path``) is one
monolithic call: walk+extract, then resolve. A failure at the end restarts
from zero and per-stage cost is invisible. This module makes the flow
explicit — five contract stages::

    extract → resolve → synthesize → index → seal

Design invariants:

* **Stage = independently retryable unit.** Each stage runs under a
  :class:`~know_your_context.serve.flow_retry.RetryPolicy`; a stage may be
  re-run alone via :meth:`StagedPipelineRunner.run_stage`, which invalidates
  downstream checkpoints because their inputs changed.
* **Checkpoint per stage.** After every stage the runner persists an atomic
  (tmp+rename), digest-protected checkpoint. A tampered or truncated
  checkpoint file fails closed with :class:`CheckpointCorruptError` — resume
  never trusts silently corrupted state.
* **Resume from the last good stage.** Re-running with ``resume=True`` and
  the same input digest skips stages whose checkpoint is ``ok``; the first
  failed/missing stage re-runs and everything downstream of a re-run is
  re-executed (never resumed from stale inputs).
* **Contracts are enforced.** Stage outputs must be JSON-serializable and
  carry non-negative item counts; violations raise :class:`StageContractError`
  and are *not* retried — a contract bug is permanent, not transient.
* **Seal.** The final stage appends a hash-chained record to a seal log:
  tamper-evident proof of what the run consumed and produced.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from know_your_context.domain import canonical_json
from know_your_context.serve.flow_retry import RetryExhaustedError, RetryPolicy, retry

__all__ = [
    "CHECKPOINT_FILE_NAME",
    "STAGE_CONTRACT_VERSION",
    "STAGE_ORDER",
    "CheckpointCorruptError",
    "CheckpointStore",
    "ExtractStage",
    "FlowRunReport",
    "IndexStage",
    "ResolveStage",
    "SealStage",
    "StageContext",
    "StageContractError",
    "StageFailedError",
    "StageName",
    "StageOutput",
    "StageRunRecord",
    "StagedPipelineRunner",
    "SynthesizeStage",
    "build_ingestion_stages",
    "verify_seal_chain",
]

STAGE_CONTRACT_VERSION = 1
CHECKPOINT_FILE_NAME = "flow-checkpoint.json"

#: Transient stage failures worth a retry: I/O and database contention.
#: Typed validation errors (ValueError family) are permanent by construction.
DEFAULT_STAGE_RETRYABLE: tuple[type[BaseException], ...] = (OSError, sqlite3.Error)


class StageName(StrEnum):
    """The five contract stages of the ingestion flow, in execution order."""

    EXTRACT = "extract"
    RESOLVE = "resolve"
    SYNTHESIZE = "synthesize"
    INDEX = "index"
    SEAL = "seal"


#: The contract stage order; the ingestion builder asserts against it.
STAGE_ORDER: tuple[StageName, ...] = (
    StageName.EXTRACT,
    StageName.RESOLVE,
    StageName.SYNTHESIZE,
    StageName.INDEX,
    StageName.SEAL,
)

_VALID_CHECKPOINT_STATUS = frozenset({"ok", "failed"})


class StageContractError(ValueError):
    """A stage violated the inter-stage contract (permanent — never retried)."""

    code = "STAGE_CONTRACT_VIOLATION"


class StageFailedError(RuntimeError):
    """A stage exhausted its retry policy or failed non-retryably."""

    code = "STAGE_FAILED"

    def __init__(self, stage: str, *, attempts: int, cause: BaseException) -> None:
        super().__init__(f"stage {stage!r} failed after {attempts} attempt(s): {type(cause).__name__}: {cause}")
        self.stage = stage
        self.attempts = attempts
        self.cause = cause


class CheckpointCorruptError(ValueError):
    """The checkpoint file failed its digest or shape check (fail closed)."""

    code = "CHECKPOINT_CORRUPT"


@dataclass(frozen=True)
class StageOutput:
    """The contract of one stage run: JSON-able output + item accounting."""

    output: Mapping[str, Any]
    items_in: int = 0
    items_out: int = 0

    def __post_init__(self) -> None:
        for name in ("items_in", "items_out"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be an int >= 0, got {value!r}")


@dataclass
class StageContext:
    """The inter-stage channel: run identity, digests, and stage data."""

    run_id: str
    input_digest: str
    store: Any = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StageRunRecord:
    """One stage's outcome in a run: status, attempts, timing, item counts."""

    stage: str
    status: str  # "ok" | "resumed" | "failed"
    attempts: int
    retries: int
    duration_ms: int
    items_in: int
    items_out: int
    output_digest: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "attempts": self.attempts,
            "retries": self.retries,
            "duration_ms": self.duration_ms,
            "items_in": self.items_in,
            "items_out": self.items_out,
            "output_digest": self.output_digest,
            "error": self.error,
        }


@dataclass(frozen=True)
class FlowRunReport:
    """The complete record of one staged-pipeline run (ok or failed)."""

    run_id: str
    input_digest: str
    status: str  # "ok"
    stages: tuple[StageRunRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "input_digest": self.input_digest,
            "status": self.status,
            "stages": [record.to_dict() for record in self.stages],
        }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_payload(payload: Mapping[str, Any]) -> str:
    return _sha256_text(canonical_json(payload))


# ------------------------------------------------------------- checkpointing


class CheckpointStore:
    """Atomic, digest-protected per-stage checkpoints in one JSON file.

    The file carries a ``digest`` over its unsigned payload; any tampering or
    truncation fails closed on load. Writes are atomic (tmp + os.replace), so
    a crash mid-write leaves either the old or the new complete file.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._file = self._dir / CHECKPOINT_FILE_NAME

    @property
    def file_path(self) -> Path:
        return self._file

    def _read_raw(self) -> dict[str, Any]:
        if not self._file.exists():
            return {"input_digest": None, "stages": {}}
        try:
            payload = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointCorruptError(f"checkpoint file {self._file} is unreadable: {error}") from error
        if not isinstance(payload, dict) or "stages" not in payload or "digest" not in payload:
            raise CheckpointCorruptError(f"checkpoint file {self._file} has an invalid shape")
        unsigned = {key: value for key, value in payload.items() if key != "digest"}
        if _digest_payload(unsigned) != payload["digest"]:
            raise CheckpointCorruptError(f"checkpoint file {self._file} failed its digest check (tampered?)")
        return payload

    def _write_raw(self, payload: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        unsigned = {key: value for key, value in payload.items() if key != "digest"}
        unsigned["digest"] = _digest_payload(unsigned)
        tmp = self._dir / f".{CHECKPOINT_FILE_NAME}.tmp"
        tmp.write_text(json.dumps(unsigned, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self._file)

    def load_stages(self) -> dict[str, dict[str, Any]]:
        return dict(self._read_raw()["stages"])

    def load_input_digest(self) -> str | None:
        value = self._read_raw()["input_digest"]
        return str(value) if value is not None else None

    def set_input_digest(self, digest: str) -> None:
        payload = self._read_raw()
        payload["input_digest"] = digest
        self._write_raw(payload)

    def save_stage(
        self,
        stage: str,
        *,
        status: str,
        output_digest: str | None,
        metrics: Mapping[str, Any],
        error: str | None = None,
    ) -> None:
        if status not in _VALID_CHECKPOINT_STATUS:
            raise ValueError(f"status must be one of {sorted(_VALID_CHECKPOINT_STATUS)}, got {status!r}")
        payload = self._read_raw()
        payload["stages"][stage] = {
            "stage": stage,
            "status": status,
            "output_digest": output_digest,
            "metrics": dict(metrics),
            "error": error,
            "contract_version": STAGE_CONTRACT_VERSION,
        }
        self._write_raw(payload)

    def invalidate_from(self, stage: str, *, order: Sequence[str]) -> None:
        """Drop *stage* and everything downstream of it (inputs changed)."""
        payload = self._read_raw()
        if stage not in order:
            raise ValueError(f"unknown stage {stage!r} for order {list(order)!r}")
        drop = set(order[list(order).index(stage) :])
        payload["stages"] = {name: cp for name, cp in payload["stages"].items() if name not in drop}
        self._write_raw(payload)

    def clear(self) -> None:
        try:
            self._file.unlink()
        except FileNotFoundError:
            pass


# ------------------------------------------------------------------- runner


class StagedPipelineRunner:
    """Runs contract stages in order with retry, checkpoint, and resume."""

    def __init__(
        self,
        stages: Sequence[Any],
        *,
        checkpoints: CheckpointStore,
        input_payload: Mapping[str, Any],
        retry_policy: RetryPolicy | None = None,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not stages:
            raise ValueError("at least one stage is required")
        names = [str(stage.name) for stage in stages]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate stage names: {names!r}")
        self._stages = list(stages)
        self._order = names
        self._checkpoints = checkpoints
        self._input_payload = dict(input_payload)
        self._retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._now = now
        self._sleep = sleep

    @property
    def checkpoints(self) -> CheckpointStore:
        return self._checkpoints

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(self._order)

    def _input_digest(self) -> str:
        return _digest_payload(
            {
                "contract_version": STAGE_CONTRACT_VERSION,
                "order": self._order,
                "payload": self._input_payload,
            }
        )

    def _validate_output(self, stage_name: str, output: Any) -> StageOutput:
        if not isinstance(output, StageOutput):
            raise StageContractError(f"stage {stage_name!r} must return StageOutput, got {type(output).__name__}")
        try:
            canonical_json(output.output)
        except (TypeError, ValueError) as error:
            raise StageContractError(f"stage {stage_name!r} output is not JSON-canonical: {error}") from error
        return output

    def _execute(self, stage: Any, context: StageContext) -> StageRunRecord:
        name = str(stage.name)
        attempts = 0

        def attempt_fn() -> StageOutput:
            nonlocal attempts
            attempts += 1
            return stage.run(context)

        started = self._now()
        try:
            raw = retry(
                attempt_fn,
                policy=self._retry_policy,
                retryable=DEFAULT_STAGE_RETRYABLE,
                sleep=self._sleep,
            )
        except RetryExhaustedError as error:
            duration = int((self._now() - started) * 1000)
            self._checkpoints.save_stage(
                name,
                status="failed",
                output_digest=None,
                metrics={"attempts": attempts, "duration_ms": duration},
                error=f"{type(error.last_error).__name__}: {error.last_error}",
            )
            raise StageFailedError(name, attempts=attempts, cause=error.last_error) from error
        except Exception as error:  # noqa: BLE001 - typed re-raise with checkpoint
            duration = int((self._now() - started) * 1000)
            self._checkpoints.save_stage(
                name,
                status="failed",
                output_digest=None,
                metrics={"attempts": attempts, "duration_ms": duration},
                error=f"{type(error).__name__}: {error}",
            )
            raise StageFailedError(name, attempts=attempts, cause=error) from error
        try:
            output = self._validate_output(name, raw)
        except StageContractError as error:
            self._checkpoints.save_stage(
                name,
                status="failed",
                output_digest=None,
                metrics={"attempts": attempts, "duration_ms": int((self._now() - started) * 1000)},
                error=str(error),
            )
            raise
        duration = int((self._now() - started) * 1000)
        output_digest = _digest_payload(output.output)
        self._checkpoints.save_stage(
            name,
            status="ok",
            output_digest=output_digest,
            metrics={
                "attempts": attempts,
                "retries": attempts - 1,
                "duration_ms": duration,
                "items_in": output.items_in,
                "items_out": output.items_out,
            },
        )
        context.data.setdefault("_stage_digests", {})[name] = output_digest
        return StageRunRecord(
            stage=name,
            status="ok",
            attempts=attempts,
            retries=attempts - 1,
            duration_ms=duration,
            items_in=output.items_in,
            items_out=output.items_out,
            output_digest=output_digest,
        )

    def run(self, *, resume: bool = True) -> FlowRunReport:
        input_digest = self._input_digest()
        run_id = f"run-{input_digest[:16]}"
        resumable: dict[str, dict[str, Any]] = {}
        if resume:
            stored_digest = self._checkpoints.load_input_digest()
            if stored_digest == input_digest:
                resumable = {
                    name: cp
                    for name, cp in self._checkpoints.load_stages().items()
                    if cp["status"] == "ok" and cp.get("contract_version") == STAGE_CONTRACT_VERSION
                }
            elif stored_digest is not None:
                self._checkpoints.clear()  # stale input: never resume across inputs
        else:
            self._checkpoints.clear()
        self._checkpoints.set_input_digest(input_digest)
        context = StageContext(run_id=run_id, input_digest=input_digest)
        records: list[StageRunRecord] = []
        downstream_dirty = False
        for stage in self._stages:
            name = str(stage.name)
            checkpoint = resumable.get(name)
            if checkpoint is not None and not downstream_dirty:
                metrics = checkpoint["metrics"]
                context.data.setdefault("_stage_digests", {})[name] = checkpoint["output_digest"]
                records.append(
                    StageRunRecord(
                        stage=name,
                        status="resumed",
                        attempts=int(metrics.get("attempts", 1)),
                        retries=int(metrics.get("retries", 0)),
                        duration_ms=int(metrics.get("duration_ms", 0)),
                        items_in=int(metrics.get("items_in", 0)),
                        items_out=int(metrics.get("items_out", 0)),
                        output_digest=checkpoint["output_digest"],
                    )
                )
                continue
            if not downstream_dirty:
                # First re-run: everything from here downstream re-executes.
                self._checkpoints.invalidate_from(name, order=self._order)
                downstream_dirty = True
            records.append(self._execute(stage, context))
        assert len(records) == len(self._stages), "every stage must be accounted exactly once"
        assert [r.stage for r in records] == self._order, "report order must match the stage order"
        return FlowRunReport(run_id=run_id, input_digest=input_digest, status="ok", stages=tuple(records))

    def run_stage(self, stage: str) -> StageRunRecord:
        """Re-run one stage as an independent unit; downstream invalidated."""
        if stage not in self._order:
            raise ValueError(f"unknown stage {stage!r}; known: {self._order!r}")
        self._checkpoints.invalidate_from(stage, order=self._order)
        input_digest = self._input_digest()
        context = StageContext(run_id=f"run-{input_digest[:16]}", input_digest=input_digest)
        target = self._stages[self._order.index(stage)]
        return self._execute(target, context)


# ------------------------------------------------------- ingestion stage set


class ExtractStage:
    """Stage 1: walk + extract via the existing pipeline (resolve deferred)."""

    def __init__(self, pipeline: Any, path: str | Path) -> None:
        self._pipeline = pipeline
        self._path = path
        self.extract_calls = 0

    @property
    def name(self) -> str:
        return str(StageName.EXTRACT)

    def run(self, context: StageContext) -> StageOutput:
        self.extract_calls += 1
        report = self._pipeline.ingest_path(self._path, incremental=True, recursive=True, resolve=False)
        context.data["extract_report"] = report
        context.store = context.store if context.store is not None else getattr(self._pipeline, "_store", None)
        output = {
            "proposed": int(report.stats["proposed"]),
            "skipped": int(report.stats["skipped"]),
            "failed": int(report.stats["failed"]),
            "deleted": int(report.stats["deleted"]),
            "scanned": int(report.stats["scanned"]),
            "nodes": int(report.totals["nodes"]),
            "edges": int(report.totals["edges"]),
            "refs_parked": int(report.totals["refs_parked"]),
            "input_sha256": report.input_sha256,
        }
        return StageOutput(
            output=output, items_in=int(report.stats["scanned"]), items_out=int(report.stats["proposed"])
        )


class ResolveStage:
    """Stage 2: bind parked refs via the pipeline's resolver."""

    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    @property
    def name(self) -> str:
        return str(StageName.RESOLVE)

    def run(self, context: StageContext) -> StageOutput:
        resolution = dict(self._pipeline.resolve_pending())
        output = {key: int(value) for key, value in resolution.items()}
        return StageOutput(
            output=output, items_in=int(output.get("pending", 0)), items_out=int(output.get("resolved", 0))
        )


class SynthesizeStage:
    """Stage 3: the synthesis passes over the resolved graph."""

    def __init__(self, store: Any, *, config: Any = None) -> None:
        self._store = store
        self._config = config

    @property
    def name(self) -> str:
        return str(StageName.SYNTHESIZE)

    def run(self, context: StageContext) -> StageOutput:
        from know_your_context.resolve.synthesis import run_synthesis  # deferred: import cost

        report = run_synthesis(self._store, config=self._config)
        output = {"passes": len(report.passes), "emitted": int(report.emitted), "inserted": int(report.inserted)}
        return StageOutput(output=output, items_in=int(report.emitted), items_out=int(report.inserted))


class IndexStage:
    """Stage 4: the FTS index reflects the post-synthesis graph."""

    def __init__(self, store: Any) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return str(StageName.INDEX)

    def run(self, context: StageContext) -> StageOutput:
        from know_your_context.retrieval_v2.fts import ensure_fts_index, rebuild_fts_index

        if ensure_fts_index(self._store):
            indexed = 1
            rows = rebuild_fts_index(self._store)
        else:
            indexed = 0
            rows = 0
        return StageOutput(output={"rebuilt": bool(indexed), "rows": int(rows)}, items_out=max(indexed, int(rows)))


class SealStage:
    """Stage 5: hash-chained seal record binding inputs to stage outputs."""

    def __init__(self, seal_path: str | Path) -> None:
        self._seal_path = Path(seal_path)

    @property
    def name(self) -> str:
        return str(StageName.SEAL)

    def run(self, context: StageContext) -> StageOutput:
        digests = dict(context.data.get("_stage_digests", {}))
        missing = [stage for stage in ("extract", "resolve", "synthesize", "index") if stage not in digests]
        assert not missing, f"seal requires upstream stage digests, missing: {missing}"
        prev = _last_seal_hash(self._seal_path)
        unsigned = {
            "run_id": context.run_id,
            "input_digest": context.input_digest,
            "stage_digests": digests,
            "prev_seal_sha256": prev,
            "contract_version": STAGE_CONTRACT_VERSION,
        }
        record = dict(unsigned)
        record["seal_sha256"] = _digest_payload(unsigned)
        self._seal_path.parent.mkdir(parents=True, exist_ok=True)
        with self._seal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return StageOutput(output={"seal_sha256": record["seal_sha256"]}, items_out=1)


def _last_seal_hash(seal_path: Path) -> str | None:
    if not seal_path.exists():
        return None
    last: str | None = None
    with seal_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = str(json.loads(line)["seal_sha256"])
    return last


def verify_seal_chain(seal_path: str | Path) -> bool:
    """True when every seal record hashes correctly and links to its predecessor."""
    path = Path(seal_path)
    if not path.exists():
        return True
    try:
        prev: str | None = None
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                unsigned = {key: value for key, value in record.items() if key != "seal_sha256"}
                if _digest_payload(unsigned) != record["seal_sha256"]:
                    return False
                if unsigned.get("prev_seal_sha256") != prev:
                    return False
                prev = record["seal_sha256"]
    except OSError, json.JSONDecodeError, KeyError, TypeError:
        return False
    return True


def build_ingestion_stages(
    pipeline: Any,
    store: Any,
    path: str | Path,
    *,
    checkpoints: CheckpointStore,
    seal_path: str | Path,
    synthesis_config: Any = None,
) -> list[Any]:
    """The five real ingestion stages wired over the existing public APIs."""
    stages: list[Any] = [
        ExtractStage(pipeline, path),
        ResolveStage(pipeline),
        SynthesizeStage(store, config=synthesis_config),
        IndexStage(store),
        SealStage(seal_path),
    ]
    assert [str(stage.name) for stage in stages] == [str(name) for name in STAGE_ORDER], (
        "ingestion stages must follow the contract order"
    )
    return stages
