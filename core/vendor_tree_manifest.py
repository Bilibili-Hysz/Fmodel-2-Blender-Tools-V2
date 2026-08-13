"""Reproducible source-tree manifest for bundled Vendor runtime files.

The runtime wheel hashes in ``vendor_manifest.toml`` protect the native
compression dependency. This module protects the complete vendored source tree
and the TOML manifest itself, so a release can be reproduced from a commit plus
an exact set of files rather than from an informal copy operation.

This module intentionally has no Blender dependency. It is used by release
scripts and contract tests, and can also be called by a future startup
self-diagnostic without importing ``bpy``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Blender 4.2+ ships tomllib.
    tomllib = None


TREE_MANIFEST_FILENAME = "vendor_tree_manifest.json"
TREE_MANIFEST_SCHEMA = 1
_SHA256_LENGTH = 64
_DEFAULT_EXCLUDE_GLOBS = (
    TREE_MANIFEST_FILENAME,
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.pyd",
)


class VendorTreeManifestError(ValueError):
    """Raised when a Vendor tree manifest or its contents are invalid."""


@dataclass(frozen=True, slots=True)
class VendorTreeFile:
    """Stable digest record for one file relative to the Vendor root."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class VendorTreeManifest:
    """Validated Vendor source-tree manifest."""

    schema_version: int
    vendor_id: str
    package_name: str
    version: str
    source_repository: str
    source_commit: str
    source_snapshot: str
    registration_mode: str
    capabilities: tuple[str, ...]
    files: tuple[VendorTreeFile, ...]
    tree_sha256: str
    root: str = "."
    excluded_globs: tuple[str, ...] = _DEFAULT_EXCLUDE_GLOBS

    @property
    def file_count(self) -> int:
        return len(self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "vendor_id": self.vendor_id,
            "package_name": self.package_name,
            "version": self.version,
            "source_repository": self.source_repository,
            "source_commit": self.source_commit,
            "source_snapshot": self.source_snapshot,
            "registration_mode": self.registration_mode,
            "capabilities": list(self.capabilities),
            "root": self.root,
            "excluded_globs": list(self.excluded_globs),
            "file_count": self.file_count,
            "tree_sha256": self.tree_sha256,
            "files": [asdict(item) for item in self.files],
        }


@dataclass(frozen=True, slots=True)
class VendorTreeVerification:
    """Result of verifying a manifest against a Vendor root."""

    manifest_path: Path
    vendor_root: Path
    tree_sha256: str
    file_count: int
    checked_files: tuple[str, ...]


def _normalized_relative_path(path: str | Path) -> str:
    value = str(path).replace("\\", "/")
    if not value or value.startswith("/") or ":" in value:
        raise VendorTreeManifestError(f"Unsafe Vendor tree path: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise VendorTreeManifestError(f"Unsafe Vendor tree path: {value!r}")
    return "/".join(parts)


def _validate_sha256(value: str, description: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != _SHA256_LENGTH or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise VendorTreeManifestError(f"Invalid {description}: {value!r}")
    return normalized


def _is_excluded(relative_path: str, excluded_globs: Iterable[str]) -> bool:
    normalized = relative_path.replace("\\", "/")
    parts = normalized.split("/")
    if "__pycache__" in parts or normalized.endswith((".pyc", ".pyo", ".pyd")):
        return True
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in excluded_globs)


def _ensure_inside(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise VendorTreeManifestError(
            f"Vendor tree path escapes root: {candidate}"
        ) from exc
    return candidate


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return a streaming SHA-256 digest for a regular file."""

    file_path = Path(path)
    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as handle:
            while chunk := handle.read(chunk_size):
                digest.update(chunk)
    except OSError as exc:
        raise VendorTreeManifestError(f"Unable to read Vendor file {file_path}: {exc}") from exc
    return digest.hexdigest()


def _iter_files(
    vendor_root: str | Path,
    *,
    excluded_globs: Iterable[str] = _DEFAULT_EXCLUDE_GLOBS,
) -> tuple[Path, ...]:
    root = Path(vendor_root).expanduser().resolve()
    if not root.is_dir():
        raise VendorTreeManifestError(f"Vendor root does not exist: {root}")
    excluded = tuple(excluded_globs)
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise VendorTreeManifestError(f"Symlinks are not allowed in Vendor tree: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if _is_excluded(relative, excluded):
            continue
        files.append(path)
    return tuple(sorted(files, key=lambda item: item.relative_to(root).as_posix()))


def _records_for_files(vendor_root: Path, files: Iterable[Path]) -> tuple[VendorTreeFile, ...]:
    records = []
    for path in files:
        relative = _normalized_relative_path(path.relative_to(vendor_root).as_posix())
        records.append(
            VendorTreeFile(
                path=relative,
                size=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )
    return tuple(sorted(records, key=lambda item: item.path))


def tree_digest(records: Iterable[VendorTreeFile]) -> str:
    """Hash canonical file metadata, independent of host path separators."""

    canonical_records = tuple(sorted(records, key=lambda item: item.path))
    payload = json.dumps(
        [asdict(record) for record in canonical_records],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_vendor_metadata(vendor_root: Path) -> dict[str, Any]:
    manifest_path = vendor_root / "vendor_manifest.toml"
    if not manifest_path.is_file():
        raise VendorTreeManifestError(f"Vendor manifest was not found: {manifest_path}")
    if tomllib is None:  # pragma: no cover - supported Blender versions have tomllib.
        raise VendorTreeManifestError("Python tomllib is unavailable")
    try:
        with manifest_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        raise VendorTreeManifestError(f"Unable to parse {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise VendorTreeManifestError("vendor_manifest.toml root must be a table")
    return data


def build_vendor_tree_manifest(
    vendor_root: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
    excluded_globs: Iterable[str] = _DEFAULT_EXCLUDE_GLOBS,
) -> VendorTreeManifest:
    """Build a deterministic manifest for the complete release Vendor root."""

    root = Path(vendor_root).expanduser().resolve()
    metadata = dict(metadata or _read_vendor_metadata(root))
    excluded = tuple(str(item) for item in excluded_globs)
    files = _iter_files(root, excluded_globs=excluded)
    records = _records_for_files(root, files)

    def required_text(key: str, default: str = "") -> str:
        value = metadata.get(key, default)
        if value is None:
            value = default
        text = str(value).strip()
        if not text:
            raise VendorTreeManifestError(f"Vendor metadata field is empty: {key}")
        return text

    capabilities = metadata.get("capabilities", ())
    if not isinstance(capabilities, (list, tuple)):
        raise VendorTreeManifestError("Vendor metadata capabilities must be an array")
    capability_values = tuple(str(item).strip() for item in capabilities)
    if any(not item for item in capability_values):
        raise VendorTreeManifestError("Vendor metadata capabilities must not be empty")

    package_root = root / required_text("package_name", required_text("vendor_id"))
    if not package_root.is_dir():
        raise VendorTreeManifestError(f"Vendor package root was not found: {package_root}")

    return VendorTreeManifest(
        schema_version=TREE_MANIFEST_SCHEMA,
        vendor_id=required_text("vendor_id"),
        package_name=required_text("package_name", required_text("vendor_id")),
        version=required_text("version"),
        source_repository=required_text("source_repository", "unknown"),
        source_commit=required_text("source_commit", "working-tree"),
        source_snapshot=required_text("source_snapshot", "working_tree_snapshot"),
        registration_mode=required_text("registration_mode", "headless"),
        capabilities=capability_values,
        files=records,
        tree_sha256=tree_digest(records),
        excluded_globs=excluded,
    )


def _parse_manifest(data: Mapping[str, Any]) -> VendorTreeManifest:
    if data.get("schema_version") != TREE_MANIFEST_SCHEMA:
        raise VendorTreeManifestError(
            f"Unsupported Vendor tree manifest schema: {data.get('schema_version')!r}"
        )

    raw_files = data.get("files")
    if not isinstance(raw_files, list):
        raise VendorTreeManifestError("Vendor tree manifest files must be an array")
    records: list[VendorTreeFile] = []
    seen: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise VendorTreeManifestError("Vendor tree file entry must be an object")
        relative = _normalized_relative_path(raw.get("path", ""))
        if relative in seen:
            raise VendorTreeManifestError(f"Duplicate Vendor tree path: {relative}")
        seen.add(relative)
        size = raw.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise VendorTreeManifestError(f"Invalid Vendor tree file size: {relative}")
        records.append(
            VendorTreeFile(
                path=relative,
                size=size,
                sha256=_validate_sha256(raw.get("sha256", ""), f"sha256 for {relative}"),
            )
        )

    records = sorted(records, key=lambda item: item.path)
    excluded_raw = data.get("excluded_globs", list(_DEFAULT_EXCLUDE_GLOBS))
    if not isinstance(excluded_raw, list):
        raise VendorTreeManifestError("Vendor tree excluded_globs must be an array")
    excluded = tuple(str(item) for item in excluded_raw)
    tree_sha256 = _validate_sha256(data.get("tree_sha256", ""), "tree_sha256")
    if tree_digest(records) != tree_sha256:
        raise VendorTreeManifestError("Vendor tree manifest tree_sha256 does not match files")

    capabilities = data.get("capabilities", [])
    if not isinstance(capabilities, list):
        raise VendorTreeManifestError("Vendor tree capabilities must be an array")

    file_count = data.get("file_count")
    if file_count != len(records):
        raise VendorTreeManifestError(
            f"Vendor tree file_count {file_count!r} does not match {len(records)} files"
        )

    def manifest_text(key: str, default: str = "") -> str:
        value = str(data.get(key, default)).strip()
        if not value:
            raise VendorTreeManifestError(f"Vendor tree metadata field is empty: {key}")
        return value

    root = _normalized_relative_path(data.get("root", ".")) if data.get("root", ".") != "." else "."
    return VendorTreeManifest(
        schema_version=TREE_MANIFEST_SCHEMA,
        vendor_id=manifest_text("vendor_id"),
        package_name=manifest_text("package_name"),
        version=manifest_text("version"),
        source_repository=manifest_text("source_repository", "unknown"),
        source_commit=manifest_text("source_commit", "working-tree"),
        source_snapshot=manifest_text("source_snapshot", "working_tree_snapshot"),
        registration_mode=manifest_text("registration_mode", "headless"),
        capabilities=tuple(str(item).strip() for item in capabilities),
        files=tuple(records),
        tree_sha256=tree_sha256,
        root=root,
        excluded_globs=excluded,
    )


def load_vendor_tree_manifest(path: str | Path) -> VendorTreeManifest:
    """Load and validate a JSON tree manifest without touching the filesystem tree."""

    manifest_path = Path(path).expanduser().resolve()
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise VendorTreeManifestError(f"Unable to read Vendor tree manifest {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise VendorTreeManifestError("Vendor tree manifest root must be an object")
    return _parse_manifest(data)


def verify_vendor_tree(
    manifest_path: str | Path,
    *,
    vendor_root: str | Path | None = None,
    strict: bool = True,
) -> VendorTreeVerification:
    """Verify all listed files and, in strict mode, reject unlisted files."""

    manifest_file = Path(manifest_path).expanduser().resolve()
    manifest = load_vendor_tree_manifest(manifest_file)
    root = Path(vendor_root).expanduser().resolve() if vendor_root else manifest_file.parent
    root = _ensure_inside(root, root)
    scoped_root = _ensure_inside(root, root / manifest.root)

    expected = {record.path: record for record in manifest.files}
    actual_paths = {
        path.relative_to(scoped_root).as_posix(): path
        for path in _iter_files(scoped_root, excluded_globs=manifest.excluded_globs)
    }
    missing = sorted(set(expected) - set(actual_paths))
    if missing:
        raise VendorTreeManifestError(f"Vendor tree files are missing: {', '.join(missing)}")
    if strict:
        extra = sorted(set(actual_paths) - set(expected))
        if extra:
            raise VendorTreeManifestError(f"Vendor tree contains unlisted files: {', '.join(extra)}")

    actual_records: list[VendorTreeFile] = []
    for relative, expected_record in expected.items():
        candidate = _ensure_inside(scoped_root, scoped_root / Path(*relative.split("/")))
        if not candidate.is_file():
            raise VendorTreeManifestError(f"Vendor tree entry is not a file: {relative}")
        actual_size = candidate.stat().st_size
        actual_hash = sha256_file(candidate)
        if actual_size != expected_record.size or actual_hash != expected_record.sha256:
            raise VendorTreeManifestError(
                f"Vendor tree digest mismatch for {relative}: "
                f"expected {expected_record.size}/{expected_record.sha256}, "
                f"got {actual_size}/{actual_hash}"
            )
        actual_records.append(VendorTreeFile(relative, actual_size, actual_hash))

    digest = tree_digest(actual_records)
    if digest != manifest.tree_sha256:
        raise VendorTreeManifestError(
            f"Vendor tree digest mismatch: expected {manifest.tree_sha256}, got {digest}"
        )
    return VendorTreeVerification(
        manifest_path=manifest_file,
        vendor_root=scoped_root,
        tree_sha256=digest,
        file_count=len(actual_records),
        checked_files=tuple(sorted(expected)),
    )


def write_vendor_tree_manifest(
    manifest: VendorTreeManifest,
    path: str | Path,
) -> Path:
    """Write a canonical JSON manifest atomically and return its path."""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return destination
