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
from functools import lru_cache


SCHEMA_VERSION = 3

_ANIMATION_EXTENSIONS = {".ueanim", ".uepose"}
_NATIVE_MODEL_EXTENSIONS = {".uemodel"}
_LEGACY_MODEL_EXTENSIONS = {".fbx", ".psk", ".pskx"}
# Kept as a compatibility aggregate for migration code and older callers.
_MODEL_EXTENSIONS = _NATIVE_MODEL_EXTENSIONS | _LEGACY_MODEL_EXTENSIONS
_UEFORMAT_EXTENSIONS = _ANIMATION_EXTENSIONS | _NATIVE_MODEL_EXTENSIONS
_ASSET_SCOPES = {"all", "animation", "model"}


class _ScanCancelled(Exception):
    pass


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
    provenance_state TEXT NOT NULL,
    skeleton_path TEXT,
    skeleton_guid TEXT,
    bone_layout_hash TEXT,
    provenance_fingerprint TEXT,
    provenance_reason TEXT,
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

    def __init__(self, root, *, include_legacy: bool = False, asset_scope: str = "all"):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / ".fmodel" / "project-assets.sqlite"
        # Legacy model formats remain available only through an explicit
        # migration/indexing opt-in. Animation Pipeline requests animation-only
        # scope so model files no longer inflate or slow its derived index.
        self.include_legacy = bool(include_legacy)
        if asset_scope not in _ASSET_SCOPES:
            raise ValueError(f"unsupported project index asset scope: {asset_scope}")
        self.asset_scope = asset_scope

    @property
    def scan_scope(self) -> str:
        base = "legacy-compatible" if self.include_legacy else "ueformat"
        return base if self.asset_scope == "all" else f"{base}-{self.asset_scope}"

    def ensure_schema(self):
        """Create a new v3 schema, or report an existing incompatible schema."""
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
        """Replace the derived index with a fresh empty v3 database."""
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

    def scan(self, roots, *, force=False, progress=None, cancelled=None):
        """Incrementally publish one root-scoped update.

        Normal scans stat every candidate but hash only new/changed strict
        UEFormat files. Legacy/invalid records remain conservatively hashed so
        optional JSON sidecars can still invalidate their metadata. ``force``
        hashes every candidate and is used for stale-selection recovery.
        """
        previous_generation = self._published_generation()
        try:
            scan_roots = _canonical_roots(roots)
        except (OSError, ValueError):
            return {"state": "failed", "generation": previous_generation}

        status = self.ensure_schema()
        if status["state"] != "ready":
            return {"state": "failed", "generation": previous_generation}

        try:
            discoveries_by_root = {}
            for scan_root in scan_roots:
                discoveries_by_root[scan_root] = self._discover_root(
                    scan_root, progress=progress, cancelled=cancelled
                )
            if _is_scan_cancelled(cancelled):
                raise _ScanCancelled()
        except _ScanCancelled:
            return {"state": "cancelled", "generation": previous_generation}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"state": "failed", "generation": previous_generation}

        try:
            with self._connect() as connection:
                existing_by_root = {
                    scan_root: {
                        path: (size, mtime_ns, content_hash, provenance_state)
                        for path, size, mtime_ns, content_hash, provenance_state in connection.execute(
                            "SELECT files.path, files.size, files.mtime_ns, files.content_hash, "
                            "COALESCE(assets.provenance_state, 'legacy') "
                            "FROM files LEFT JOIN assets ON assets.canonical_path = files.path "
                            "WHERE files.scan_root_path = ?",
                            (scan_root,),
                        )
                    }
                    for scan_root in scan_roots
                }
                changed_by_root = {}
                refreshed_by_root = {}
                removed_by_root = {}
                total = sum(len(items) for items in discoveries_by_root.values())
                completed = 0
                for scan_root, discoveries in discoveries_by_root.items():
                    existing = existing_by_root[scan_root]
                    current = {discovery["path"]: discovery for discovery in discoveries}
                    removed_by_root[scan_root] = set(existing) - set(current)
                    changed_by_root[scan_root] = []
                    refreshed_by_root[scan_root] = []
                    for path, discovery in current.items():
                        if _is_scan_cancelled(cancelled):
                            raise _ScanCancelled()
                        existing_record = existing.get(path)
                        strict_unchanged = (
                            existing_record is not None
                            and existing_record[3] == "strict"
                            and not force
                            and existing_record[0] == discovery["size"]
                            and existing_record[1] == discovery["mtime_ns"]
                        )
                        if not strict_unchanged:
                            hydrated = self._hash_discovery(discovery)
                            effective_hash = hydrated["content_hash"]
                            if (
                                existing_record
                                and existing_record[3] == "strict"
                                and Path(path).suffix.lower() in _UEFORMAT_EXTENSIONS
                            ):
                                # Strict native assets no longer depend on an
                                # optional sibling JSON after first publication.
                                effective_hash = hydrated["binary_content_hash"]
                            comparable = (
                                hydrated["size"], hydrated["mtime_ns"], effective_hash
                            )
                            if existing_record is None or existing_record[:3] != comparable:
                                if existing_record is not None and existing_record[2] == effective_hash:
                                    refreshed_by_root[scan_root].append({
                                        **hydrated, "content_hash": effective_hash,
                                    })
                                else:
                                    changed_by_root[scan_root].append(
                                        self._parse_discovery(hydrated)
                                    )
                        completed += 1
                        _notify_scan_progress(
                            progress, "compare", completed, total, path
                        )
                if _is_scan_cancelled(cancelled):
                    raise _ScanCancelled()

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
                        connection.execute(
                            "DELETE FROM relations WHERE target_path = ? OR source_path = ?",
                            (path, path),
                        )
                        connection.execute("DELETE FROM assets WHERE canonical_path = ?", (path,))
                        connection.execute("DELETE FROM files WHERE path = ?", (path,))
                    for discovery in changed_by_root[scan_root]:
                        self._publish_discovery(connection, discovery, generation)
                    for discovery in refreshed_by_root[scan_root]:
                        self._publish_stat_refresh(connection, discovery, generation)
            _notify_scan_progress(progress, "published", total, total, "")
            return {"state": "ready", "generation": generation}
        except _ScanCancelled:
            return {"state": "cancelled", "generation": previous_generation}
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error):
            return {"state": "failed", "generation": previous_generation}

    def summary(self, skeleton="", raise_on_error=False):
        """Return read-only animation statistics for Pipeline status display."""
        status = self.status()
        result = {
            **status,
            "generation": None,
            "last_scan_ns": None,
            "animation_count": 0,
            "matching_count": 0,
        }
        if status["state"] != "ready":
            return result
        try:
            with self._connect_read_only_at(self.path) as connection:
                result["animation_count"] = connection.execute(
                    "SELECT COUNT(*) FROM assets WHERE asset_type = 'animation'"
                ).fetchone()[0]
                if skeleton:
                    result["matching_count"] = connection.execute(
                        "SELECT COUNT(*) FROM assets "
                        "WHERE asset_type = 'animation' AND skeleton_name = ?",
                        (skeleton,),
                    ).fetchone()[0]
                generation = connection.execute(
                    "SELECT generation, published_at_ns FROM generations "
                    "ORDER BY generation DESC LIMIT 1"
                ).fetchone()
                if generation:
                    result["generation"], result["last_scan_ns"] = generation
        except (OSError, sqlite3.Error):
            if raise_on_error:
                raise
            return {
                **result,
                "state": "unavailable",
                "rebuild_required": True,
                "generation": None,
                "last_scan_ns": None,
                "animation_count": 0,
                "matching_count": 0,
            }
        return result

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

    def probe_ueformat_provenance(self, path, raise_on_error=False):
        """Probe canonical UEFormat identity without changing the SQLite index.

        The v3 schema persists the identity fields discovered by a scan. This
        read-only boundary lets Project Assets inspect a real
        `.uemodel/.ueanim/.uepose` extension without teaching the directory
        scanner to import Blender or mesh data.
        """
        try:
            inspect_skeleton_provenance, read_ueformat_identity = _load_identity_tools()
            record = read_ueformat_identity(path)
            provenance = inspect_skeleton_provenance(record.identity)
            result = provenance.to_dict()
            result.update({
                "identifier": record.identifier,
                "kind": record.kind,
                "object_name": record.object_name,
                "file_version": record.file_version,
                "compressed": record.compressed,
                "identity_extension": record.identity_extension,
            })
            return result
        except (OSError, TypeError, ValueError) as error:
            if raise_on_error:
                raise
            return {
                "state": "invalid",
                "identity": None,
                "fingerprint": "",
                "reason": str(error) or "UEFormat identity probe failed",
            }

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
                    "assets.provenance_state, assets.skeleton_path, assets.skeleton_guid, "
                    "assets.bone_layout_hash, assets.provenance_fingerprint, "
                    "assets.provenance_reason, files.size, files.mtime_ns, files.content_hash "
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
            (
                indexed_path, scan_root, fingerprint, provenance_state,
                skeleton_path, skeleton_guid, bone_layout_hash,
                provenance_fingerprint, provenance_reason, size, mtime_ns, content_hash,
            ) = rows_by_path[path]
            try:
                current = Path(indexed_path).stat()
            except OSError as error:
                raise ValueError("selected indexed file is no longer available") from error
            current_hash = _content_hash(
                Path(indexed_path),
                None
                if provenance_state == "strict"
                and Path(indexed_path).suffix.lower() in _UEFORMAT_EXTENSIONS
                else Path(indexed_path).with_suffix(".json"),
            )
            if (current.st_size != size or current.st_mtime_ns != mtime_ns
                    or content_hash != current_hash
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
            metadata = {
                "file_type": extension.removeprefix("."),
                "native_ueformat": extension in {".ueanim", ".uepose"}
                and provenance_state == "strict",
                "skeleton_display_name": skeleton,
                "fmodel_skeleton_provenance": {
                    "state": provenance_state,
                    "fingerprint": provenance_fingerprint or "",
                    "reason": provenance_reason or "",
                },
            }
            if provenance_state == "strict" and skeleton_path:
                metadata["skeleton_path"] = skeleton_path
                metadata["fmodel_skeleton_identity"] = {
                    "version": 1,
                    "skeleton_path": skeleton_path,
                    "has_skeleton_guid": bool(skeleton_guid),
                    "skeleton_guid": skeleton_guid,
                    "bone_layout_hash": bone_layout_hash,
                }
            animations[name] = {
                "original_name": name,
                "hashed_name": name,
                "source_file": indexed_path,
                "content_hash": "",
                "metadata": metadata,
            }

        return {
            "version": "1.0",
            "source_type": "fmodel_ueanim",
            "source_path": str(self.root),
            "skeletons": {skeleton: skeleton_tree},
        }

    def _discover_root(self, scan_root, *, progress=None, cancelled=None):
        """Stat candidates without hashing or parsing their contents."""
        discoveries = []
        candidate_extensions = self._candidate_extensions()
        for directory, subdirectories, filenames in os.walk(scan_root):
            if _is_scan_cancelled(cancelled):
                raise _ScanCancelled()
            subdirectories[:] = [name for name in subdirectories if name != ".fmodel"]
            for filename in filenames:
                if _is_scan_cancelled(cancelled):
                    raise _ScanCancelled()
                path = Path(directory, filename)
                suffix = path.suffix.lower()
                if suffix not in candidate_extensions:
                    continue
                stat = path.stat()
                canonical_path = str(path.resolve())
                asset_type = "animation" if suffix in _ANIMATION_EXTENSIONS else "model"
                discoveries.append({
                    "path": canonical_path,
                    "scan_root": scan_root,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "asset_type": asset_type,
                })
                _notify_scan_progress(
                    progress, "discover", len(discoveries), None, canonical_path
                )
        return discoveries

    def _candidate_extensions(self):
        model_extensions = _MODEL_EXTENSIONS if self.include_legacy else _NATIVE_MODEL_EXTENSIONS
        if self.asset_scope == "animation":
            return set(_ANIMATION_EXTENSIONS)
        if self.asset_scope == "model":
            return set(model_extensions)
        return set(_ANIMATION_EXTENSIONS | model_extensions)

    @staticmethod
    def _hash_discovery(discovery):
        path = Path(discovery["path"])
        content_hash, binary_content_hash = _content_hash_pair(
            path, path.with_suffix(".json")
        )
        return {
            **discovery,
            "content_hash": content_hash,
            "binary_content_hash": binary_content_hash,
        }

    @staticmethod
    def _parse_discovery(discovery):
        """Parse one changed source with binary UEFormat identity first.

        Canonical UEFormat files carry their own skeleton identity.  A sibling
        JSON file is therefore consulted only when the binary record is legacy
        or unavailable; a malformed optional sidecar must not block a valid
        native asset.
        """
        path = Path(discovery["path"])
        asset_type = discovery["asset_type"]
        provenance = _provenance_for_discovery(path)
        identity = provenance.get("identity") or {}
        skeleton_name = ""
        if identity.get("skeleton_path"):
            skeleton_name = str(identity["skeleton_path"]).rsplit("/", 1)[-1]
        if not skeleton_name:
            try:
                skeleton_name = _skeleton_from_metadata(
                    path.with_suffix(".json"),
                    allow_generic_fallback=asset_type == "animation",
                )
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                # JSON remains a legacy compatibility source, never a native
                # UEFormat requirement.  Relative-path fallback below keeps
                # the asset discoverable when the sidecar is absent/broken.
                skeleton_name = ""
        if asset_type == "animation" and not skeleton_name:
            relative_parts = path.relative_to(discovery["scan_root"]).parts
            skeleton_name = relative_parts[0] if len(relative_parts) > 1 else "Generic"
        stored_content_hash = discovery["content_hash"]
        if (
            provenance.get("state") == "strict"
            and path.suffix.lower() in _UEFORMAT_EXTENSIONS
        ):
            stored_content_hash = discovery["binary_content_hash"]
        return {
            **discovery,
            "content_hash": stored_content_hash,
            "skeleton_name": skeleton_name,
            "provenance_state": provenance.get("state", "legacy"),
            "skeleton_path": identity.get("skeleton_path"),
            "skeleton_guid": identity.get("skeleton_guid"),
            "bone_layout_hash": identity.get("bone_layout_hash"),
            "provenance_fingerprint": provenance.get("fingerprint", ""),
            "provenance_reason": provenance.get("reason", ""),
        }

    @staticmethod
    def _publish_stat_refresh(connection, discovery, generation):
        """Publish stat-only changes when the authoritative content hash is stable."""
        fingerprint = _fingerprint(
            discovery["path"], discovery["size"], discovery["mtime_ns"],
            discovery["content_hash"],
        )
        connection.execute(
            "UPDATE files SET size = ?, mtime_ns = ?, content_hash = ?, generation = ? "
            "WHERE path = ?",
            (
                discovery["size"], discovery["mtime_ns"], discovery["content_hash"],
                generation, discovery["path"],
            ),
        )
        connection.execute(
            "UPDATE assets SET fingerprint = ?, generation = ? WHERE canonical_path = ?",
            (fingerprint, generation, discovery["path"]),
        )
        connection.execute(
            "UPDATE relations SET generation = ? WHERE source_path = ? OR target_path = ?",
            (generation, discovery["path"], discovery["path"]),
        )

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
            "INSERT INTO assets ("
            "canonical_path, scan_root_path, asset_type, fingerprint, skeleton_name, "
            "provenance_state, skeleton_path, skeleton_guid, bone_layout_hash, "
            "provenance_fingerprint, provenance_reason, generation"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                discovery["path"], discovery["scan_root"], discovery["asset_type"],
                fingerprint, discovery["skeleton_name"],
                discovery["provenance_state"], discovery["skeleton_path"],
                discovery["skeleton_guid"], discovery["bone_layout_hash"],
                discovery["provenance_fingerprint"], discovery["provenance_reason"],
                generation,
            ),
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


def _is_scan_cancelled(callback):
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:
        return False


def _notify_scan_progress(callback, phase, completed, total, path):
    if callback is None:
        return
    try:
        callback({
            "phase": phase,
            "completed": completed,
            "total": total,
            "path": path,
        })
    except Exception:
        # Progress reporting is observational and must not invalidate a scan.
        return


def _schema_objects(connection):
    return {
        (object_type, name.lower(), table_name.lower(), _normalize_sql(sql))
        for object_type, name, table_name, sql in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
        )
    }


@lru_cache(maxsize=1)
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


def _content_hash(path, metadata_path=None):
    """Hash an asset, optionally including a legacy sibling sidecar."""
    digest = hashlib.sha256()
    candidates = [Path(path)]
    if metadata_path is not None:
        candidates.append(Path(metadata_path))
    for candidate in candidates:
        digest.update(b"\0")
        if candidate.is_file():
            with candidate.open("rb") as source:
                for chunk in iter(lambda: source.read(64 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _content_hash_pair(path, metadata_path):
    """Hash the native asset once for both native and legacy invalidation modes."""
    path = Path(path)
    metadata_path = Path(metadata_path) if metadata_path is not None else None
    binary_digest = hashlib.sha256()
    combined_digest = hashlib.sha256()
    binary_digest.update(b"\0")
    combined_digest.update(b"\0")
    if path.is_file():
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                binary_digest.update(chunk)
                combined_digest.update(chunk)
    if metadata_path is not None:
        combined_digest.update(b"\0")
        if metadata_path.is_file():
            with metadata_path.open("rb") as source:
                for chunk in iter(lambda: source.read(64 * 1024), b""):
                    combined_digest.update(chunk)
    return combined_digest.hexdigest(), binary_digest.hexdigest()


def _load_identity_tools():
    """Load provenance tools both as a package and as a direct probe module."""
    try:
        from .skeleton_provenance import inspect_skeleton_provenance
        from .ueformat_identity_reader import read_ueformat_identity
        return inspect_skeleton_provenance, read_ueformat_identity
    except ImportError:
        # Host-Python project-index tests may load this file directly. Avoid
        # mutating sys.path; load the two pure modules under stable top-level
        # names so the reader's own fallback import remains valid.
        import importlib.util
        import sys

        core_dir = Path(__file__).resolve().parent
        module_paths = {
            "skeleton_provenance": core_dir / "skeleton_provenance.py",
            "ueformat_identity_reader": core_dir / "ueformat_identity_reader.py",
        }
        for name, module_path in module_paths.items():
            if name in sys.modules:
                continue
            spec = importlib.util.spec_from_file_location(name, module_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Unable to load {name}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        from skeleton_provenance import inspect_skeleton_provenance
        from ueformat_identity_reader import read_ueformat_identity
        return inspect_skeleton_provenance, read_ueformat_identity


def _provenance_for_discovery(path):
    """Read binary identity only for formats that can carry it.

    This helper is deliberately fail-closed and returns a JSON-safe record so
    a malformed or unsupported UEFormat file is indexed as `invalid` rather
    than aborting the complete directory scan. Non-UEFormat legacy assets do
    not claim strict provenance.
    """
    if path.suffix.lower() not in _UEFORMAT_EXTENSIONS:
        return {
            "state": "legacy",
            "identity": None,
            "fingerprint": "",
            "reason": "FModel identity is not applicable to this asset format",
        }
    try:
        inspect_skeleton_provenance, read_ueformat_identity = _load_identity_tools()
        record = read_ueformat_identity(path)
        result = inspect_skeleton_provenance(record.identity).to_dict()
        result.update({
            "identifier": record.identifier,
            "kind": record.kind,
            "object_name": record.object_name,
            "file_version": record.file_version,
            "compressed": record.compressed,
            "identity_extension": record.identity_extension,
        })
        return result
    except (OSError, TypeError, ValueError) as error:
        return {
            "state": "invalid",
            "identity": None,
            "fingerprint": "",
            "reason": str(error) or "UEFormat identity parse failed",
        }


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
