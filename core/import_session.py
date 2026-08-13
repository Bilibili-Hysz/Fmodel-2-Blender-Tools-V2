"""Blender-independent import session and result-report contract.

The operators currently have different reporting behavior. This module defines a
small common result model first; Blender UI persistence and datablock rollback
can be layered on top without making the core contract depend on ``bpy``.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ImportStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ImportFailure:
    path: str
    phase: str
    message: str
    code: str = "import_failed"
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "phase": self.phase,
            "message": self.message,
            "code": self.code,
            "detail": dict(self.detail),
        }


@dataclass(slots=True)
class ImportSession:
    """Collect one model/animation/pose/scene operation result."""

    asset_kind: str
    source_paths: tuple[str, ...] = ()
    vendor_version: str = ""
    export_operation_id: str = ""
    export_manifest_path: str = ""
    import_policy: str = ""
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at_utc: str = field(default_factory=_utc_now_text)
    finished_at_utc: str = ""
    phase: str = "initializing"
    status: ImportStatus = ImportStatus.RUNNING
    successes: list[str] = field(default_factory=list)
    failures: list[ImportFailure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    created_counts: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, int] = field(default_factory=dict)
    cancel_reason: str = ""

    def __post_init__(self) -> None:
        self.asset_kind = str(self.asset_kind).strip() or "unknown"
        self.source_paths = tuple(self._normalize_path(path) for path in self.source_paths if str(path).strip())
        self.vendor_version = str(self.vendor_version).strip()
        self.export_operation_id = str(self.export_operation_id).strip()
        self.export_manifest_path = str(self.export_manifest_path).strip()
        self.import_policy = str(self.import_policy).strip().lower()

    @staticmethod
    def _normalize_path(path: str | os.PathLike[str]) -> str:
        return os.path.normpath(os.fspath(path))

    @property
    def success_count(self) -> int:
        return len(self.successes)

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    @property
    def attempted_count(self) -> int:
        return self.success_count + self.failure_count

    def set_phase(self, phase: str) -> None:
        self.phase = str(phase).strip() or "unknown"

    def record_success(self, path: str | os.PathLike[str]) -> None:
        normalized = self._normalize_path(path)
        if normalized not in self.successes:
            self.successes.append(normalized)

    def record_failure(
        self,
        path: str | os.PathLike[str],
        *,
        phase: str | None = None,
        message: str,
        code: str = "import_failed",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        self.failures.append(
            ImportFailure(
                path=self._normalize_path(path),
                phase=str(phase or self.phase).strip() or "unknown",
                message=str(message),
                code=str(code).strip() or "import_failed",
                detail=dict(detail or {}),
            )
        )

    def record_warning(self, message: str) -> None:
        value = str(message).strip()
        if value and value not in self.warnings:
            self.warnings.append(value)

    def record_created(self, kind: str, count: int = 1) -> None:
        key = str(kind).strip() or "unknown"
        if count < 0:
            raise ValueError("created count must not be negative")
        self.created_counts[key] = self.created_counts.get(key, 0) + count

    def record_metric(self, name: str, count: int = 1) -> None:
        """Record an operation statistic that is not a Blender datablock count."""
        key = str(name).strip() or "unknown"
        if count < 0:
            raise ValueError("metric count must not be negative")
        self.metrics[key] = self.metrics.get(key, 0) + count

    def cancel(self, reason: str = "") -> None:
        self.cancel_reason = str(reason).strip()
        self.status = ImportStatus.CANCELLED
        self.phase = "cancelled"
        if not self.finished_at_utc:
            self.finished_at_utc = _utc_now_text()

    def finish(self) -> ImportStatus:
        if self.status is ImportStatus.CANCELLED:
            return self.status
        if self.failure_count and self.success_count:
            self.status = ImportStatus.PARTIAL
        elif self.failure_count:
            self.status = ImportStatus.FAILED
        else:
            self.status = ImportStatus.SUCCESS
        self.phase = "completed"
        if not self.finished_at_utc:
            self.finished_at_utc = _utc_now_text()
        return self.status

    def fail_all(
        self,
        *,
        phase: str,
        message: str,
        code: str = "import_failed",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        paths = self.source_paths or ("<unknown>",)
        for path in paths:
            self.record_failure(path, phase=phase, message=message, code=code, detail=detail)
        self.finish()

    @property
    def summary(self) -> str:
        label = self.asset_kind
        if self.status is ImportStatus.RUNNING:
            return f"{label}: running ({self.attempted_count} attempted)"
        if self.status is ImportStatus.CANCELLED:
            suffix = f": {self.cancel_reason}" if self.cancel_reason else ""
            return f"{label}: cancelled{suffix}"
        return (
            f"{label}: {self.status.value}; "
            f"{self.success_count} succeeded, {self.failure_count} failed"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "asset_kind": self.asset_kind,
            "source_paths": list(self.source_paths),
            "vendor_version": self.vendor_version,
            "export_operation_id": self.export_operation_id,
            "export_manifest_path": self.export_manifest_path,
            "import_policy": self.import_policy,
            "phase": self.phase,
            "status": self.status.value,
            "successes": list(self.successes),
            "failures": [failure.to_dict() for failure in self.failures],
            "warnings": list(self.warnings),
            "created_counts": dict(sorted(self.created_counts.items())),
            "metrics": dict(sorted(self.metrics.items())),
            "cancel_reason": self.cancel_reason,
            "summary": self.summary,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def persist_import_session(scene: Any, session: ImportSession) -> None:
    """Store the latest report in Blender's scene custom-property namespace.

    This helper only requires an object supporting ``scene[key] = value`` and is
    therefore easy to exercise with a fake scene in contract tests.
    """

    scene["FModel_Import_Session"] = session.to_json()
    scene["FModel_Import_Status"] = session.status.value
    scene["FModel_Import_Summary"] = session.summary
    scene["FModel_Import_Operation_Id"] = session.operation_id
