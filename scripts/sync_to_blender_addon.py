"""Synchronize the V2 runtime package into Blender's user addons directory.

Default invocation mirrors the clean runtime payload from this repository into:

    %APPDATA%\\Blender Foundation\\Blender\\5.2\\scripts\\addons\\FModel_Tools

The source repository, tests, documentation, Git metadata, caches, backup
folders, retired Project Index modules and inactive legacy-format operators are
deliberately not copied. If a destination already exists, it is
moved to a timestamped backup before the new package is installed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Iterable


DEFAULT_PACKAGE_NAME = "FModel_Tools"
DEFAULT_BLENDER_VERSION = "5.2"
RUNTIME_FILES = ("__init__.py", "auto_handlers.py", "ui.py")
RUNTIME_DIRECTORIES = (
    "Assets",
    "Fmodel",
    "Universal_tools",
    "core",
    "vendor",
    "Shaders",
)
EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".mimocode",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "backup",
    "backups",
    "investigation",
    "tests",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_FILE_NAMES = {".DS_Store", "Thumbs.db"}


@dataclass(frozen=True)
class SyncResult:
    source_root: Path
    target: Path
    backup: Path | None
    file_count: int
    total_bytes: int
    digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_root": str(self.source_root),
            "target": str(self.target),
            "backup": str(self.backup) if self.backup else None,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "digest": self.digest,
        }


def default_source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_addons_directory(blender_version: str = DEFAULT_BLENDER_VERSION) -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        roaming = Path(appdata)
    else:
        roaming = Path.home() / "AppData" / "Roaming"
    return roaming / "Blender Foundation" / "Blender" / blender_version / "scripts" / "addons"


def _is_excluded(path: Path) -> bool:
    excluded_parts = {part.lower() for part in path.parts}
    if any(name.lower() in excluded_parts for name in EXCLUDED_DIRECTORY_NAMES):
        return True
    return path.name in EXCLUDED_FILE_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES


def _validate_source_root(source_root: Path) -> None:
    if not source_root.is_dir():
        raise FileNotFoundError(f"source root does not exist: {source_root}")
    required = [source_root / name for name in RUNTIME_FILES]
    required.extend(source_root / name for name in RUNTIME_DIRECTORIES)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("source runtime entries are missing: " + "; ".join(missing))


def iter_runtime_files(source_root: Path) -> Iterable[Path]:
    """Yield only files that belong in the installable Blender addon payload."""
    _validate_source_root(source_root)

    candidates = [source_root / name for name in RUNTIME_FILES]
    for directory_name in RUNTIME_DIRECTORIES:
        root = source_root / directory_name
        candidates.extend(root.rglob("*"))

    seen: set[Path] = set()
    for candidate in sorted(candidates):
        if not candidate.is_file() or _is_excluded(candidate):
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        yield candidate


def _file_digest(files: Iterable[Path], root: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        file_count += 1
        total_bytes += len(data)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
    return file_count, total_bytes, digest.hexdigest()


def _assert_safe_destination(addons_directory: Path, target: Path, package_name: str) -> None:
    addons_directory = addons_directory.resolve()
    target = target.resolve()
    if target.name != package_name:
        raise ValueError(f"target leaf must be {package_name!r}: {target}")
    try:
        target.relative_to(addons_directory)
    except ValueError as error:
        raise ValueError(
            f"refusing to modify a target outside the addons directory: {target}"
        ) from error


def _backup_path(addons_directory: Path, package_name: str) -> Path:
    backup_root = addons_directory.parent / ".fmodel_tools_backups"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = backup_root / f"{package_name}_{timestamp}"
    suffix = 1
    while candidate.exists():
        candidate = backup_root / f"{package_name}_{timestamp}_{suffix}"
        suffix += 1
    return candidate


def _copy_to_stage(source_root: Path, files: list[Path], stage: Path) -> None:
    for source in files:
        relative = source.relative_to(source_root)
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _verify_stage(stage: Path, expected: dict[str, tuple[int, str]]) -> None:
    actual_paths = {
        path.relative_to(stage).as_posix(): path
        for path in stage.rglob("*")
        if path.is_file()
    }
    if set(actual_paths) != set(expected):
        missing = sorted(set(expected) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(expected))
        raise RuntimeError(f"staged payload mismatch; missing={missing}, extra={extra}")
    for relative, (size, sha256) in expected.items():
        candidate = actual_paths[relative]
        data = candidate.read_bytes()
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if len(data) != size or actual_sha256 != sha256:
            raise RuntimeError(f"staged file verification failed: {relative}")


def sync_runtime(
    *,
    source_root: Path | None = None,
    addons_directory: Path | None = None,
    package_name: str = DEFAULT_PACKAGE_NAME,
    backup_existing: bool = True,
    dry_run: bool = False,
) -> SyncResult:
    source_root = (source_root or default_source_root()).expanduser().resolve()
    addons_directory = (addons_directory or default_addons_directory()).expanduser().resolve()
    target = addons_directory / package_name
    _validate_source_root(source_root)
    _assert_safe_destination(addons_directory, target, package_name)
    if source_root == target:
        raise ValueError("source and target are the same directory")

    files = list(iter_runtime_files(source_root))
    if not files:
        raise RuntimeError("no runtime files were selected")
    expected: dict[str, tuple[int, str]] = {}
    for file in files:
        data = file.read_bytes()
        expected[file.relative_to(source_root).as_posix()] = (len(data), hashlib.sha256(data).hexdigest())
    file_count, total_bytes, digest = _file_digest(files, source_root)

    if dry_run:
        return SyncResult(source_root, target, None, file_count, total_bytes, digest)

    addons_directory.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{package_name}.sync-", dir=str(addons_directory)))
    backup: Path | None = None
    try:
        _copy_to_stage(source_root, files, stage)
        _verify_stage(stage, expected)

        if target.exists() or target.is_symlink():
            if target.is_symlink():
                raise RuntimeError(f"refusing to replace symlink target: {target}")
            if backup_existing:
                backup = _backup_path(addons_directory, package_name)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(backup))
            else:
                shutil.rmtree(target)
        shutil.move(str(stage), str(target))
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        if backup is not None and not target.exists():
            shutil.move(str(backup), str(target))
        raise

    return SyncResult(source_root, target, backup, file_count, total_bytes, digest)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=None, help="V2 repository root")
    parser.add_argument("--addons", type=Path, default=None, help="Blender addons directory")
    parser.add_argument("--package-name", default=DEFAULT_PACKAGE_NAME)
    parser.add_argument("--blender-version", default=DEFAULT_BLENDER_VERSION)
    parser.add_argument("--no-backup", action="store_true", help="Replace an existing target without a backup")
    parser.add_argument("--dry-run", action="store_true", help="Only list the payload that would be synchronized")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    addons_directory = args.addons or default_addons_directory(args.blender_version)
    try:
        result = sync_runtime(
            source_root=args.source,
            addons_directory=addons_directory,
            package_name=args.package_name,
            backup_existing=not args.no_backup,
            dry_run=args.dry_run,
        )
    except Exception as error:
        print(f"FModel Tools sync failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "dry-run" if args.dry_run else "synchronized", **result.to_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
