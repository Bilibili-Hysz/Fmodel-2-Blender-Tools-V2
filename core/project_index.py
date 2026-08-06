"""Blender-independent lifecycle management for a project's derived asset index."""

from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from time import time_ns
from contextlib import contextmanager


SCHEMA_VERSION = 2

_ANIMATION_EXTENSIONS = {".ueanim", ".uepose"}
_MODEL_EXTENSIONS = {".fbx", ".psk", ".pskx", ".uemodel"}

_SCHEMA_SQL = """
CREATE TABLE project (
    root_path TEXT NOT NULL
);
CREATE TABLE scan_roots (
    path TEXT NOT NULL UNIQUE,
    fingerprint TEXT,
    generation INTEGER
);
CREATE TABLE generations (
    generation INTEGER PRIMARY KEY,
    published_at_ns INTEGER NOT NULL
);
CREATE TABLE files (
    path TEXT NOT NULL UNIQUE,
    scan_root_path TEXT NOT NULL,
    size INTEGER,
    mtime_ns INTEGER,
    content_hash TEXT,
    generation INTEGER NOT NULL
);
CREATE TABLE assets (
    canonical_path TEXT NOT NULL UNIQUE,
    scan_root_path TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    fingerprint TEXT,
    skeleton_name TEXT,
    generation INTEGER NOT NULL
);
CREATE TABLE relations (
    source_path TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_path TEXT NOT NULL,
    scan_root_path TEXT NOT NULL,
    generation INTEGER NOT NULL,
    UNIQUE (source_path, relation_type, target_path)
);
"""


class ProjectIndex:
    """Own the SQLite database stored with one project root.

    The index is intentionally limited to derived source metadata. It does not
    import Blender APIs or persist Blender runtime or publication state.
    """

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / ".fmodel" / "project-assets.sqlite"

    def ensure_schema(self):
        """Create a new v2 schema, or report an existing incompatible schema."""
        if self.path.exists():
            status = self.status()
            if status["state"] != "missing":
                return status

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                self._create_schema(connection)
            return self.status()
        except (OSError, sqlite3.Error):
            return self._unavailable_status()

    def status(self):
        """Return the index state without creating or modifying the database."""
        return self._status_at(self.path)

    def _status_at(self, path):
        if not path.is_file():
            return {
                "state": "missing",
                "schema_version": None,
                "rebuild_required": True,
            }

        try:
            with self._connect_read_only_at(path) as connection:
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
                schema_shape_valid = self._has_expected_schema_objects(connection)
        except (OSError, sqlite3.Error):
            return self._unavailable_status()

        compatible = (
            schema_version == SCHEMA_VERSION
            and schema_shape_valid
        )
        return {
            "state": "ready" if compatible else "rebuild_required",
            "schema_version": schema_version,
            "rebuild_required": not compatible,
        }

    def rebuild(self):
        """Replace the derived index with a fresh empty v2 database."""
        staging_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, staging_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
            )
            staging_path = Path(staging_name)
            os.close(descriptor)
            with self._connect_at(staging_path) as connection:
                self._create_schema(connection)
            staged_status = self._status_at(staging_path)
            if staged_status["state"] != "ready":
                return self._unavailable_status()
            os.replace(staging_path, self.path)
            staging_path = None
            return staged_status
        except (OSError, sqlite3.Error):
            return self._unavailable_status()
        finally:
            if staging_path is not None:
                try:
                    staging_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def scan(self, roots):
        """Discover supported assets and atomically publish one root-scoped update."""
        try:
            scan_roots = _canonical_roots(roots)
        except (OSError, ValueError):
            return {"state": "failed", "generation": self._published_generation()}

        status = self.ensure_schema()
        if status["state"] != "ready":
            return {"state": "failed", "generation": self._published_generation()}

        try:
            discoveries_by_root = {}
            for scan_root in scan_roots:
                discoveries_by_root[scan_root] = self._discover_root(scan_root)
        except (OSError, ValueError, json.JSONDecodeError):
            return {"state": "failed", "generation": self._published_generation()}

        try:
            with self._connect() as connection:
                existing_by_root = {
                    scan_root: {
                        path: (size, mtime_ns, content_hash)
                        for path, size, mtime_ns, content_hash in connection.execute(
                            "SELECT path, size, mtime_ns, content_hash FROM files WHERE scan_root_path = ?",
                            (scan_root,),
                        )
                    }
                    for scan_root in scan_roots
                }
                changed_by_root = {}
                removed_by_root = {}
                for scan_root, discoveries in discoveries_by_root.items():
                    existing = existing_by_root[scan_root]
                    current = {discovery["path"]: discovery for discovery in discoveries}
                    removed_by_root[scan_root] = set(existing) - set(current)
                    changed_by_root[scan_root] = [
                        self._parse_discovery(discovery)
                        for path, discovery in current.items()
                        if existing.get(path) != (
                            discovery["size"], discovery["mtime_ns"], discovery["content_hash"]
                        )
                    ]
                generation = connection.execute(
                    "SELECT COALESCE(MAX(generation), 0) + 1 FROM generations"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO generations (generation, published_at_ns) VALUES (?, ?)",
                    (generation, time_ns()),
                )
                for scan_root in scan_roots:
                    connection.execute(
                        "INSERT OR REPLACE INTO scan_roots (path, fingerprint, generation) VALUES (?, ?, ?)",
                        (scan_root, None, generation),
                    )
                    affected_paths = removed_by_root[scan_root] | {
                        discovery["path"] for discovery in changed_by_root[scan_root]
                    }
                    for path in affected_paths:
                        connection.execute("DELETE FROM relations WHERE target_path = ? OR source_path = ?", (path, path))
                        connection.execute("DELETE FROM assets WHERE canonical_path = ?", (path,))
                        connection.execute("DELETE FROM files WHERE path = ?", (path,))
                    for discovery in changed_by_root[scan_root]:
                        self._publish_discovery(connection, discovery, generation)
            return {"state": "ready", "generation": generation}
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error):
            return {"state": "failed", "generation": self._published_generation()}

    def animations_for_skeleton(self, name, raise_on_error=False):
        """Return canonical animation paths without creating the derived index."""
        if self.status()["state"] != "ready":
            return []
        try:
            with self._connect_read_only_at(self.path) as connection:
                return [
                    row[0]
                    for row in connection.execute(
                        "SELECT canonical_path FROM assets "
                        "WHERE asset_type = 'animation' AND skeleton_name = ? "
                        "ORDER BY canonical_path",
                        (name,),
                    )
                ]
        except (OSError, sqlite3.Error):
            if raise_on_error:
                raise
            return []

    def read_only_animations_for_skeleton(self, name, raise_on_error=False):
        """Backward-compatible alias for the read-only animation query."""
        return self.animations_for_skeleton(name, raise_on_error=raise_on_error)

    def dictionary_manifest(self, skeleton, paths):
        """Project current indexed animation selections into a pipeline manifest.

        The index is only an accelerator, so every selected file is statted
        again before its indexed row can authorize downstream pipeline work.
        """
        if self.status()["state"] != "ready":
            raise ValueError("project index is not ready")
        if not isinstance(skeleton, str) or not skeleton:
            raise ValueError("skeleton must be a non-empty string")
        if not isinstance(paths, (list, tuple)) or not paths:
            raise ValueError("paths must be a non-empty sequence")

        selected_paths = [str(Path(path).expanduser().resolve()) for path in paths]
        if len(selected_paths) != len(set(selected_paths)):
            raise ValueError("selected paths must be unique")
        placeholders = ", ".join("?" for _ in selected_paths)
        try:
            with self._connect_read_only_at(self.path) as connection:
                rows = connection.execute(
                    "SELECT assets.canonical_path, assets.scan_root_path, assets.fingerprint, "
                    "files.size, files.mtime_ns, files.content_hash "
                    "FROM assets JOIN files ON files.path = assets.canonical_path "
                    "WHERE assets.asset_type = 'animation' AND assets.skeleton_name = ? "
                    f"AND assets.canonical_path IN ({placeholders})",
                    [skeleton, *selected_paths],
                ).fetchall()
        except (OSError, sqlite3.Error) as error:
            raise ValueError("project index is unavailable") from error

        rows_by_path = {row[0]: row for row in rows}
        if len(rows_by_path) != len(selected_paths):
            raise ValueError("selection is not indexed for the requested skeleton")

        skeleton_tree = {}
        for path in selected_paths:
            indexed_path, scan_root, fingerprint, size, mtime_ns, content_hash = rows_by_path[path]
            try:
                current = Path(indexed_path).stat()
            except OSError as error:
                raise ValueError("selected indexed file is no longer available") from error
            if (current.st_size != size or current.st_mtime_ns != mtime_ns
                    or content_hash != _content_hash(
                        Path(indexed_path), Path(indexed_path).with_suffix(".json")
                    )
                    or fingerprint != _fingerprint(indexed_path, size, mtime_ns, content_hash)):
                raise ValueError("selected indexed file is stale")

            relative_parent = Path(indexed_path).parent.relative_to(scan_root)
            folder_parts = relative_parent.parts[1:]
            folder = "/".join(folder_parts) if folder_parts else "Base"
            name = Path(indexed_path).stem
            folder_tree = skeleton_tree
            for part in folder.split("/"):
                folder_tree = folder_tree.setdefault(part, {})
            animations = folder_tree.setdefault("__animations__", {})
            if name in animations:
                raise ValueError("selected paths produce duplicate animation names")
            extension = Path(indexed_path).suffix.lower()
            animations[name] = {
                "original_name": name,
                "hashed_name": name,
                "source_file": indexed_path,
                "content_hash": "",
                "metadata": {"file_type": extension.removeprefix(".")},
            }

        return {
            "version": "1.0",
            "source_type": "fmodel_ueanim",
            "source_path": str(self.root),
            "skeletons": {skeleton: skeleton_tree},
        }

    def _discover_root(self, scan_root):
        """Stat every candidate source without parsing its metadata."""
        discoveries = []
        for directory, subdirectories, filenames in os.walk(scan_root):
            for filename in filenames:
                path = Path(directory, filename)
                if path.suffix.lower() not in _ANIMATION_EXTENSIONS | _MODEL_EXTENSIONS:
                    continue
                stat = path.stat()
                canonical_path = str(path.resolve())
                asset_type = "animation" if path.suffix.lower() in _ANIMATION_EXTENSIONS else "model"
                discoveries.append({
                    "path": canonical_path,
                    "scan_root": scan_root,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "content_hash": _content_hash(path, path.with_suffix(".json")),
                    "asset_type": asset_type,
                })
        return discoveries

    @staticmethod
    def _parse_discovery(discovery):
        """Parse only a changed source, retaining explicit relation semantics."""
        path = Path(discovery["path"])
        asset_type = discovery["asset_type"]
        skeleton_name = _skeleton_from_metadata(
            path.with_suffix(".json"),
            allow_generic_fallback=asset_type == "animation",
        )
        if asset_type == "animation" and not skeleton_name:
            relative_parts = path.relative_to(discovery["scan_root"]).parts
            skeleton_name = relative_parts[0] if len(relative_parts) > 1 else "Generic"
        return {**discovery, "skeleton_name": skeleton_name}

    @staticmethod
    def _publish_discovery(connection, discovery, generation):
        fingerprint = _fingerprint(
            discovery["path"], discovery["size"], discovery["mtime_ns"], discovery["content_hash"]
        )
        connection.execute(
            "INSERT INTO files (path, scan_root_path, size, mtime_ns, content_hash, generation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (discovery["path"], discovery["scan_root"], discovery["size"],
              discovery["mtime_ns"], discovery["content_hash"], generation),
        )
        connection.execute(
            "INSERT INTO assets (canonical_path, scan_root_path, asset_type, fingerprint, skeleton_name, generation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (discovery["path"], discovery["scan_root"], discovery["asset_type"], fingerprint,
             discovery["skeleton_name"], generation),
        )
        if discovery["asset_type"] == "animation":
            connection.execute(
                "INSERT INTO relations "
                "(source_path, relation_type, target_path, scan_root_path, generation) "
                "VALUES (?, 'skeleton_uses_animation', ?, ?, ?)",
                (discovery["skeleton_name"], discovery["path"], discovery["scan_root"], generation),
            )
        elif discovery["skeleton_name"]:
            connection.execute(
                "INSERT INTO relations "
                "(source_path, relation_type, target_path, scan_root_path, generation) "
                "VALUES (?, 'model_uses_skeleton', ?, ?, ?)",
                (discovery["path"], discovery["skeleton_name"], discovery["scan_root"], generation),
            )

    def _published_generation(self):
        if not self.path.is_file():
            return None
        try:
            with self._connect() as connection:
                return connection.execute("SELECT MAX(generation) FROM generations").fetchone()[0]
        except (OSError, sqlite3.Error):
            return None

    @contextmanager
    def _connect(self):
        with self._connect_at(self.path) as connection:
            yield connection

    @contextmanager
    def _connect_at(self, path):
        connection = sqlite3.connect(path)
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def _connect_read_only_at(self, path):
        connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _create_schema(connection):
        connection.executescript(_SCHEMA_SQL)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _has_expected_schema_objects(connection):
        return _schema_objects(connection) == _canonical_schema_objects()

    @staticmethod
    def _unavailable_status():
        return {
            "state": "unavailable",
            "schema_version": None,
            "rebuild_required": True,
        }


def _schema_objects(connection):
    return {
        (object_type, name.lower(), table_name.lower(), _normalize_sql(sql))
        for object_type, name, table_name, sql in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
        )
    }


def _canonical_schema_objects():
    connection = sqlite3.connect(":memory:")
    try:
        ProjectIndex._create_schema(connection)
        return _schema_objects(connection)
    finally:
        connection.close()


def _normalize_sql(sql):
    """Normalize only unquoted SQL whitespace and case for schema comparison."""
    if sql is None:
        return None

    tokens = []
    position = 0
    while position < len(sql):
        character = sql[position]
        if character.isspace():
            position += 1
            continue
        if character in "'\"`":
            quote = character
            end = position + 1
            while end < len(sql):
                if sql[end] == quote:
                    end += 1
                    if end < len(sql) and sql[end] == quote:
                        end += 1
                        continue
                    break
                end += 1
            tokens.append(sql[position:end])
            position = end
            continue
        if character == "[":
            end = sql.find("]", position + 1)
            if end == -1:
                end = len(sql) - 1
            tokens.append(sql[position:end + 1])
            position = end + 1
            continue
        if character.isalnum() or character in "_$":
            end = position + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] in "_$"):
                end += 1
            tokens.append(sql[position:end].lower())
            position = end
            continue
        tokens.append(character)
        position += 1
    return " ".join(tokens)


def _canonical_roots(roots):
    """Resolve scan roots once so source ownership is stable across scans."""
    canonical_roots = sorted({str(Path(root).expanduser().resolve()) for root in roots})
    root_paths = [Path(root) for root in canonical_roots]
    if any(
        candidate != root and candidate.is_relative_to(root)
        for root in root_paths
        for candidate in root_paths
    ):
        raise ValueError("scan roots must not overlap")
    return canonical_roots


def _fingerprint(path, size, mtime_ns, content_hash):
    return f"{path}\0{size}\0{mtime_ns}\0{content_hash}"


def _content_hash(path, metadata_path):
    """Hash an asset and its sibling metadata so either can invalidate it."""
    digest = hashlib.sha256()
    for candidate in (path, metadata_path):
        digest.update(b"\0")
        if candidate.is_file():
            with candidate.open("rb") as source:
                for chunk in iter(lambda: source.read(64 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _skeleton_from_metadata(metadata_path, allow_generic_fallback=False):
    """Read only declared skeleton identities; callers supply the sibling JSON."""
    if not metadata_path.is_file():
        return ""
    with metadata_path.open("r", encoding="utf-8-sig") as metadata_file:
        metadata = json.load(metadata_file)
    for item in _metadata_items(metadata):
        skeleton = item.get("Skeleton") or item.get("skeleton")
        if not skeleton and isinstance(item.get("Properties"), dict):
            properties = item["Properties"]
            skeleton = properties.get("Skeleton") or properties.get("skeleton")
        name = _skeleton_name(skeleton)
        if name:
            return name
        if (allow_generic_fallback and item.get("Type") == "Skeleton"
                and isinstance(item.get("Name"), str)):
            return item["Name"]
    return ""


def _metadata_items(metadata):
    if isinstance(metadata, list):
        return (item for item in metadata if isinstance(item, dict))
    if isinstance(metadata, dict):
        return (metadata,)
    return ()


def _skeleton_name(skeleton):
    if isinstance(skeleton, dict):
        skeleton = skeleton.get("ObjectName") or skeleton.get("Name")
    if not isinstance(skeleton, str):
        return ""
    match = re.search(r"'([^']+)'", skeleton)
    return match.group(1) if match else skeleton


__all__ = ["ProjectIndex", "SCHEMA_VERSION"]
