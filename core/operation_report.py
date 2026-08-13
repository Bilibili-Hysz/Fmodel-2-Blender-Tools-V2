"""Blender-independent reader and formatter for the latest FModel operation.

Import operators persist a compact JSON session on the current scene.  This
module turns that storage contract into a single UI/report model.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SESSION_KEY = "FModel_Import_Session"
KNOWN_STATUSES = {"running", "success", "partial", "failed", "cancelled"}


def _scene_value(scene: Any, key: str, default: Any = "") -> Any:
    getter = getattr(scene, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except (KeyError, ReferenceError, RuntimeError, TypeError):
            pass
    try:
        return scene[key]
    except (KeyError, ReferenceError, RuntimeError, TypeError):
        return default


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("operation report JSON must be an object")
    return parsed


def _string_tuple(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value) for value in values if str(value).strip())


def _count_mapping(values: Any) -> dict[str, int]:
    if not isinstance(values, Mapping):
        return {}
    counts: dict[str, int] = {}
    for key, value in values.items():
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized >= 0:
            counts[str(key)] = normalized
    return dict(sorted(counts.items()))


@dataclass(frozen=True, slots=True)
class LastOperationReport:
    status: str = "idle"
    asset_kind: str = ""
    summary: str = ""
    operation_id: str = ""
    phase: str = ""
    started_at_utc: str = ""
    finished_at_utc: str = ""
    source_paths: tuple[str, ...] = ()
    vendor_version: str = ""
    export_operation_id: str = ""
    export_manifest_path: str = ""
    import_policy: str = ""
    successes: tuple[str, ...] = ()
    failures: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    created_counts: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, int] = field(default_factory=dict)
    parse_warning: str = ""

    @property
    def available(self) -> bool:
        return self.status != "idle"

    @property
    def primary_source_path(self) -> str:
        return self.source_paths[0] if self.source_paths else ""

    @property
    def source_directory(self) -> str:
        source = self.primary_source_path
        if not source:
            return ""
        path = Path(source).expanduser()
        return os.fspath(path if path.is_dir() else path.parent)

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "asset_kind": self.asset_kind,
            "summary": self.summary,
            "operation_id": self.operation_id,
            "phase": self.phase,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "source_paths": list(self.source_paths),
            "vendor_version": self.vendor_version,
            "export_operation_id": self.export_operation_id,
            "export_manifest_path": self.export_manifest_path,
            "import_policy": self.import_policy,
            "successes": list(self.successes),
            "failures": [dict(failure) for failure in self.failures],
            "warnings": list(self.warnings),
            "created_counts": dict(self.created_counts),
            "metrics": dict(self.metrics),
            "parse_warning": self.parse_warning,
        }


def load_last_operation(scene: Any) -> LastOperationReport:
    raw_session = _scene_value(scene, SESSION_KEY, "")
    try:
        session = _json_mapping(raw_session)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return LastOperationReport(
            status="failed",
            summary="Stored operation report is invalid",
            parse_warning=str(exc),
        )

    if session:
        raw_status = str(session.get("status", "running") or "running").lower()
        status = raw_status if raw_status in KNOWN_STATUSES else "failed"
        failures = tuple(
            dict(item) for item in session.get("failures", ()) if isinstance(item, Mapping)
        )
        warnings = list(_string_tuple(session.get("warnings", ())))
        parse_warning = ""
        if raw_status not in KNOWN_STATUSES:
            parse_warning = f"Unknown stored status: {raw_status}"
            warnings.append(parse_warning)
        return LastOperationReport(
            status=status,
            asset_kind=str(session.get("asset_kind", "") or ""),
            summary=str(session.get("summary", "") or ""),
            operation_id=str(session.get("operation_id", "") or ""),
            phase=str(session.get("phase", "") or ""),
            started_at_utc=str(session.get("started_at_utc", "") or ""),
            finished_at_utc=str(session.get("finished_at_utc", "") or ""),
            source_paths=_string_tuple(session.get("source_paths", ())),
            vendor_version=str(session.get("vendor_version", "") or ""),
            export_operation_id=str(session.get("export_operation_id", "") or ""),
            export_manifest_path=str(session.get("export_manifest_path", "") or ""),
            import_policy=str(session.get("import_policy", "") or ""),
            successes=_string_tuple(session.get("successes", ())),
            failures=failures,
            warnings=tuple(warnings),
            created_counts=_count_mapping(session.get("created_counts", {})),
            metrics=_count_mapping(session.get("metrics", {})),
            parse_warning=parse_warning,
        )


    return LastOperationReport()


def format_last_operation(
    report: LastOperationReport,
    vendor_status: Mapping[str, Any] | None = None,
) -> str:
    lines = [
        "FModel Tools Last Operation",
        f"Status: {report.status}",
    ]
    if report.asset_kind:
        lines.append(f"Kind: {report.asset_kind}")
    if report.summary:
        lines.append(f"Summary: {report.summary}")
    if report.operation_id:
        lines.append(f"Operation ID: {report.operation_id}")
    if report.phase:
        lines.append(f"Phase: {report.phase}")
    if report.started_at_utc:
        lines.append(f"Started (UTC): {report.started_at_utc}")
    if report.finished_at_utc:
        lines.append(f"Finished (UTC): {report.finished_at_utc}")
    if report.vendor_version:
        lines.append(f"Import Vendor: {report.vendor_version}")
    if report.export_operation_id:
        lines.append(f"FModel Export Operation ID: {report.export_operation_id}")
    if report.export_manifest_path:
        lines.append(f"Export Manifest: {report.export_manifest_path}")
    if report.import_policy:
        lines.append(f"Import Policy: {report.import_policy}")
    if report.source_paths:
        lines.append("Sources:")
        lines.extend(f"- {path}" for path in report.source_paths)
    if report.created_counts:
        lines.append("Created Datablocks:")
        lines.extend(f"- {name}: {count}" for name, count in report.created_counts.items())
    if report.metrics:
        lines.append("Metrics:")
        lines.extend(f"- {name}: {count}" for name, count in report.metrics.items())
    if report.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in report.warnings)
    if report.failures:
        lines.append("Failures:")
        for failure in report.failures:
            code = str(failure.get("code", "import_failed"))
            path = str(failure.get("path", ""))
            message = str(failure.get("message", failure.get("detail", "")))
            lines.append(f"- [{code}] {path}: {message}".rstrip(": "))
    if report.parse_warning and report.parse_warning not in report.warnings:
        lines.extend(("Report Warning:", f"- {report.parse_warning}"))
    if vendor_status:
        lines.append("UEFormat Runtime:")
        lines.append(f"- State: {vendor_status.get('state', 'unknown')}")
        if vendor_status.get("version"):
            lines.append(f"- Version: {vendor_status['version']}")
        if vendor_status.get("source"):
            lines.append(f"- Source: {vendor_status['source']}")
        if vendor_status.get("source_path"):
            lines.append(f"- Path: {vendor_status['source_path']}")
        if vendor_status.get("tree_sha256"):
            lines.append(f"- Tree SHA-256: {vendor_status['tree_sha256']}")
        if vendor_status.get("message"):
            lines.append(f"- Message: {vendor_status['message']}")
        if vendor_status.get("remediation"):
            lines.append(f"- Remediation: {vendor_status['remediation']}")
    return "\n".join(lines).rstrip() + "\n"