"""Typed errors for the contract-v4 extraction path (SUB-106).

Every failure of the ``submit_extraction`` verb and the review surface is a
typed, fail-closed :class:`ExtractionError` with a stable machine-readable
``code`` from the contract's error vocabulary — never a raw exception on the
wire. ``audit_event_id`` is attached once the invocation's single final audit
event has been appended, mirroring the service's ``KnowledgeError`` discipline
(spec 7.7).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ConfidenceNotDecimalError",
    "ConfidenceOutOfRangeError",
    "ExtractionAutoRejectedError",
    "ExtractionDuplicateError",
    "ExtractionError",
    "ExtractionNotFoundError",
    "ExtractionQueueFullError",
    "ExtractionStateError",
    "MethodSchemaError",
    "PayloadSchemaError",
    "PayloadTooLargeError",
    "SourceHashMismatchError",
    "SourceNotFoundError",
    "SourcePathOutsideRootError",
]


class ExtractionError(RuntimeError):
    """Base class for every typed contract-v4 extraction failure."""

    code = "EXTRACTION_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.audit_event_id: str | None = None


class PayloadSchemaError(ExtractionError):
    """The submission violates the closed payload schema."""

    code = "PAYLOAD_SCHEMA"


class MethodSchemaError(ExtractionError):
    """The method descriptor violates its closed schema."""

    code = "METHOD_SCHEMA"


class ConfidenceNotDecimalError(ExtractionError):
    """The confidence self-report is not a decimal string."""

    code = "CONFIDENCE_NOT_DECIMAL"


class ConfidenceOutOfRangeError(ExtractionError):
    """The confidence self-report falls outside ``[0, 1]``."""

    code = "CONFIDENCE_OUT_OF_RANGE"


class PayloadTooLargeError(ExtractionError):
    """A size cap (text, nodes, edges, fields, total bytes) was exceeded."""

    code = "PAYLOAD_TOO_LARGE"


class SourceHashMismatchError(ExtractionError):
    """The file bound by ``source_path`` does not hash to ``source_sha256``."""

    code = "SOURCE_HASH_MISMATCH"


class SourcePathOutsideRootError(ExtractionError):
    """``source_path`` escapes the startup-fixed extraction root."""

    code = "SOURCE_PATH_OUTSIDE_ROOT"


class SourceNotFoundError(ExtractionError):
    """``source_path`` names no readable regular file under the root."""

    code = "SOURCE_NOT_FOUND"


class ExtractionDuplicateError(ExtractionError):
    """The idempotency key was reused with a different payload."""

    code = "EXTRACTION_DUPLICATE"


class ExtractionNotFoundError(ExtractionError):
    """No queue row carries the requested receipt id."""

    code = "EXTRACTION_NOT_FOUND"


class ExtractionStateError(ExtractionError):
    """The row is already decided (applied/rejected) or otherwise not actionable."""

    code = "EXTRACTION_STATE"


class ExtractionQueueFullError(ExtractionError):
    """The durable review queue reached its fail-closed row cap."""

    code = "EXTRACTION_QUEUE_FULL"


class ExtractionAutoRejectedError(ExtractionError):
    """Disk-mode: the post-write auto-review rejected the submission.

    The queue row is marked ``rejected`` with the typed reason codes as its
    note; nothing reaches the canonical graph (or is compensated out of it).
    """

    code = "EXTRACTION_AUTO_REJECTED"

    def __init__(
        self,
        message: str,
        *,
        reasons: tuple[str, ...] = (),
        audit_references: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reasons = tuple(reasons)
        self.audit_references = dict(audit_references or {})
