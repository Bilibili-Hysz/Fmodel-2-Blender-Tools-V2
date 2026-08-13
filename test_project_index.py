"""Host-Python contracts for the Blender-independent project index."""

import importlib.util
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


def load_project_index_module():
    module_path = Path(__file__).parent / "core" / "project_index.py"
    spec = importlib.util.spec_from_file_location("project_index", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_index = load_project_index_module()

    def test_canonical_schema_objects_are_cached_without_changing_validation(self):
        canonical = self.project_index._canonical_schema_objects
        canonical.cache_clear()
        try:
            with patch.object(
                self.project_index.sqlite3,
                "connect",
                wraps=self.project_index.sqlite3.connect,
            ) as connect:
                first = canonical()
                second = canonical()

            self.assertIs(first, second)
            self.assertEqual(connect.call_count, 1)
        finally:
            canonical.cache_clear()

    def test_ensure_schema_creates_canonical_database_and_persists_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            index = self.project_index.ProjectIndex(root)

            status = index.ensure_schema()

            expected_path = root.resolve() / ".fmodel" / "project-assets.sqlite"
            self.assertEqual(index.path, expected_path)
            self.assertTrue(expected_path.is_file())
            self.assertFalse(status["rebuild_required"])
            self.assertEqual(status["schema_version"], 3)
            connection = sqlite3.connect(expected_path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(
                    {row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )},
                    {"project", "scan_roots", "generations", "files", "assets", "relations"},
                )
                self.assertEqual(
                    [row[1] for row in connection.execute("PRAGMA table_info(assets)")],
                    [
                        "canonical_path", "scan_root_path", "asset_type", "fingerprint",
                        "skeleton_name", "provenance_state", "skeleton_path",
                        "skeleton_guid", "bone_layout_hash", "provenance_fingerprint",
                        "provenance_reason", "generation",
                    ],
                )
            finally:
                connection.close()

    def _ready_index_with_live_bytes(self, directory):
        index = self.project_index.ProjectIndex(Path(directory) / "project")
        self.assertEqual(index.ensure_schema()["state"], "ready")
        return index, index.path.read_bytes()

    def _assert_live_index_survives_failed_rebuild(self, index, live_bytes):
        self.assertEqual(index.status()["state"], "ready")
        self.assertEqual(index.path.read_bytes(), live_bytes)
        self.assertEqual(list(index.path.parent.glob(f".{index.path.name}.*.tmp")), [])

    def test_rebuild_uses_exclusive_sibling_temp_and_closes_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            index, _ = self._ready_index_with_live_bytes(directory)
            descriptor = os.open(os.devnull, os.O_RDONLY)
            staging = index.path.parent / ".project-assets.sqlite.unique.tmp"
            with patch.object(self.project_index.tempfile, "mkstemp", return_value=(descriptor, str(staging))) as mkstemp, patch.object(self.project_index.os, "close", wraps=os.close) as close:
                self.assertEqual(index.rebuild()["state"], "ready")
            self.assertEqual(mkstemp.call_args.kwargs["dir"], index.path.parent)
            close.assert_called_once_with(descriptor)

    def test_rebuild_validation_and_replace_failures_preserve_live_index(self):
        with tempfile.TemporaryDirectory() as directory:
            index, live_bytes = self._ready_index_with_live_bytes(directory)
            with patch.object(index, "_status_at", side_effect=lambda path: {"state": "rebuild_required"} if path != index.path else {"state": "ready"}):
                self.assertEqual(index.rebuild()["state"], "unavailable")
            self.assertEqual(index.path.read_bytes(), live_bytes)
            with patch.object(self.project_index.os, "replace", side_effect=OSError("replace failed")):
                self.assertEqual(index.rebuild()["state"], "unavailable")
            self.assertEqual(index.path.read_bytes(), live_bytes)

    def test_rebuild_returns_staged_ready_status_without_post_publish_status_check(self):
        with tempfile.TemporaryDirectory() as directory:
            index, _ = self._ready_index_with_live_bytes(directory)

            with patch.object(index, "status", side_effect=OSError("post-publish status failed")):
                status = index.rebuild()

            self.assertEqual(status, {
                "state": "ready", "schema_version": 3, "rebuild_required": False,
            })
            self.assertTrue(index.path.is_file())

    def test_rebuild_preserves_ready_live_index_when_staging_preparation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            index, live_bytes = self._ready_index_with_live_bytes(directory)

            with patch.object(type(index.path.parent), "mkdir", side_effect=OSError("permission denied")):
                status = index.rebuild()

            self.assertEqual(status["state"], "unavailable")
            self._assert_live_index_survives_failed_rebuild(index, live_bytes)

    def test_rebuild_preserves_ready_live_index_when_staging_connection_cannot_open(self):
        with tempfile.TemporaryDirectory() as directory:
            index, live_bytes = self._ready_index_with_live_bytes(directory)
            def fail_staging_connection(path):
                if Path(path) != index.path:
                    raise sqlite3.OperationalError("staging database is locked")
                return original_connect(path)

            original_connect = index._connect_at if hasattr(index, "_connect_at") else None
            with patch.object(index, "_connect_at", side_effect=fail_staging_connection, create=True):
                status = index.rebuild()

            self.assertEqual(status["state"], "unavailable")
            self._assert_live_index_survives_failed_rebuild(index, live_bytes)

    def test_rebuild_preserves_ready_live_index_when_staged_validation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            index, live_bytes = self._ready_index_with_live_bytes(directory)
            def staged_validation_status(path):
                if Path(path) != index.path:
                    return {"state": "rebuild_required", "schema_version": 1, "rebuild_required": True}
                return original_status(path)

            original_status = index._status_at if hasattr(index, "_status_at") else None
            with patch.object(index, "_status_at", side_effect=staged_validation_status, create=True):
                status = index.rebuild()

            self.assertEqual(status["state"], "unavailable")
            self._assert_live_index_survives_failed_rebuild(index, live_bytes)

    def test_animation_query_can_preserve_legacy_empty_result_or_surface_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.status = lambda: {"state": "ready"}
            index._connect_read_only_at = lambda path: (
                _ for _ in ()
            ).throw(sqlite3.Error("database is locked"))

            self.assertEqual(index.animations_for_skeleton("Hero"), [])
            with self.assertRaisesRegex(sqlite3.Error, "database is locked"):
                index.animations_for_skeleton("Hero", raise_on_error=True)

    def test_animation_query_does_not_recreate_index_deleted_after_ready_check(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.ensure_schema()
            index.status = lambda: {"state": "ready"}
            index.path.unlink()

            with self.assertRaises(sqlite3.Error):
                index.animations_for_skeleton("Hero", raise_on_error=True)

            self.assertFalse(index.path.exists())

    def test_mismatched_stored_schema_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.ensure_schema()
            connection = sqlite3.connect(index.path)
            try:
                connection.execute("PRAGMA user_version = 999")
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["schema_version"], 999)
            self.assertEqual(status["state"], "rebuild_required")

    def test_same_version_database_with_malformed_project_table_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.path.parent.mkdir(parents=True)
            connection = sqlite3.connect(index.path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE project (root_path INTEGER NOT NULL);
                    CREATE TABLE scan_roots (path TEXT NOT NULL, fingerprint TEXT);
                    CREATE TABLE files (
                        path TEXT NOT NULL,
                        size INTEGER,
                        mtime_ns INTEGER,
                        content_hash TEXT
                    );
                    CREATE TABLE assets (
                        canonical_path TEXT NOT NULL,
                        asset_type TEXT NOT NULL,
                        fingerprint TEXT
                    );
                    CREATE TABLE relations (
                        source_path TEXT NOT NULL,
                        relation_type TEXT NOT NULL,
                        target_path TEXT NOT NULL
                    );
                    PRAGMA user_version = 2;
                    """
                )
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["state"], "rebuild_required")

    def test_same_version_database_with_extra_column_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.path.parent.mkdir(parents=True)
            connection = sqlite3.connect(index.path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE project (root_path TEXT NOT NULL, unexpected TEXT);
                    CREATE TABLE scan_roots (path TEXT NOT NULL UNIQUE, fingerprint TEXT);
                    CREATE TABLE files (
                        path TEXT NOT NULL UNIQUE,
                        size INTEGER,
                        mtime_ns INTEGER,
                        content_hash TEXT
                    );
                    CREATE TABLE assets (
                        canonical_path TEXT NOT NULL UNIQUE,
                        asset_type TEXT NOT NULL,
                        fingerprint TEXT
                    );
                    CREATE TABLE relations (
                        source_path TEXT NOT NULL,
                        relation_type TEXT NOT NULL,
                        target_path TEXT NOT NULL,
                        UNIQUE (source_path, relation_type, target_path)
                    );
                    PRAGMA user_version = 2;
                    """
                )
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["state"], "rebuild_required")

    def test_same_version_database_without_required_unique_constraints_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.path.parent.mkdir(parents=True)
            connection = sqlite3.connect(index.path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE project (root_path TEXT NOT NULL);
                    CREATE TABLE scan_roots (path TEXT NOT NULL, fingerprint TEXT);
                    CREATE TABLE files (
                        path TEXT NOT NULL,
                        size INTEGER,
                        mtime_ns INTEGER,
                        content_hash TEXT
                    );
                    CREATE TABLE assets (
                        canonical_path TEXT NOT NULL,
                        asset_type TEXT NOT NULL,
                        fingerprint TEXT
                    );
                    CREATE TABLE relations (
                        source_path TEXT NOT NULL,
                        relation_type TEXT NOT NULL,
                        target_path TEXT NOT NULL
                    );
                    PRAGMA user_version = 2;
                    """
                )
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["state"], "rebuild_required")

    def test_same_version_database_with_extra_unique_index_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.ensure_schema()
            connection = sqlite3.connect(index.path)
            try:
                connection.execute(
                    "CREATE UNIQUE INDEX files_extra_content_hash ON files (content_hash)"
                )
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["state"], "rebuild_required")

    def test_same_version_database_with_nullable_required_column_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.path.parent.mkdir(parents=True)
            connection = sqlite3.connect(index.path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE project (root_path TEXT);
                    CREATE TABLE scan_roots (path TEXT NOT NULL UNIQUE, fingerprint TEXT);
                    CREATE TABLE files (
                        path TEXT NOT NULL UNIQUE,
                        size INTEGER,
                        mtime_ns INTEGER,
                        content_hash TEXT
                    );
                    CREATE TABLE assets (
                        canonical_path TEXT NOT NULL UNIQUE,
                        asset_type TEXT NOT NULL,
                        fingerprint TEXT
                    );
                    CREATE TABLE relations (
                        source_path TEXT NOT NULL,
                        relation_type TEXT NOT NULL,
                        target_path TEXT NOT NULL,
                        UNIQUE (source_path, relation_type, target_path)
                    );
                    PRAGMA user_version = 2;
                    """
                )
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["state"], "rebuild_required")

    def test_same_version_database_with_duplicate_unique_index_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.ensure_schema()
            connection = sqlite3.connect(index.path)
            try:
                connection.execute(
                    "CREATE UNIQUE INDEX duplicate_files_path ON files(path)"
                )
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["state"], "rebuild_required")

    def test_same_version_database_with_generated_column_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.ensure_schema()
            connection = sqlite3.connect(index.path)
            try:
                connection.execute(
                    "ALTER TABLE project ADD COLUMN root_path_copy TEXT "
                    "GENERATED ALWAYS AS (root_path) VIRTUAL"
                )
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["state"], "rebuild_required")

    def test_same_version_database_with_partial_unique_index_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.ensure_schema()
            connection = sqlite3.connect(index.path)
            try:
                connection.execute(
                    "CREATE UNIQUE INDEX files_nonempty_hash "
                    "ON files (content_hash) WHERE content_hash IS NOT NULL"
                )
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["state"], "rebuild_required")

    def test_same_version_database_with_unexpected_trigger_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self.project_index.ProjectIndex(directory)
            index.ensure_schema()
            connection = sqlite3.connect(index.path)
            try:
                connection.execute(
                    "CREATE TRIGGER project_noop AFTER INSERT ON project "
                    "BEGIN SELECT 1; END"
                )
                connection.commit()
            finally:
                connection.close()

            status = index.status()

            self.assertTrue(status["rebuild_required"])
            self.assertEqual(status["state"], "rebuild_required")

    def test_roots_use_separate_canonical_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.project_index.ProjectIndex(Path(directory) / "first")
            second = self.project_index.ProjectIndex(Path(directory) / "second")

            first.ensure_schema()
            second.ensure_schema()

            self.assertNotEqual(first.root, second.root)
            self.assertNotEqual(first.path, second.path)
            self.assertTrue(first.path.is_file())
            self.assertTrue(second.path.is_file())

    def test_scan_prefers_sibling_metadata_over_relative_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Other" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            (animation.with_suffix(".json")).write_text(
                '[{"Type": "AnimSequence", "Skeleton": {"ObjectName": "Skeleton\'Hero\'"}}]',
                encoding="utf-8",
            )
            index = self.project_index.ProjectIndex(root)

            result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            self.assertEqual(index.animations_for_skeleton("Hero"), [str(animation.resolve())])
            self.assertEqual(index.animations_for_skeleton("Other"), [])

    def test_scan_does_not_prune_supported_assets_in_dot_prefixed_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / ".source" / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            index = self.project_index.ProjectIndex(root)

            result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            self.assertEqual(index.animations_for_skeleton(".source"), [str(animation.resolve())])

    def test_dictionary_manifest_projects_current_indexed_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Moves" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            index = self.project_index.ProjectIndex(root)
            index.scan([root])

            manifest = index.dictionary_manifest("Hero", [str(animation.resolve())])

            entry = manifest["skeletons"]["Hero"]["Moves"]["__animations__"]["Run"]
            self.assertEqual(manifest["source_path"], str(root.resolve()))
            self.assertEqual(entry["source_file"], str(animation.resolve()))
            self.assertEqual(entry["hashed_name"], "Run")
            self.assertEqual(entry["metadata"]["file_type"], "ueanim")

    def test_dictionary_manifest_rejects_mismatched_or_stale_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            other = root / "Enemy" / "Idle.ueanim"
            animation.parent.mkdir(parents=True)
            other.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            other.write_bytes(b"other")
            index = self.project_index.ProjectIndex(root)
            index.scan([root])

            with self.assertRaises(ValueError):
                index.dictionary_manifest("Hero", [str(other.resolve())])
            with self.assertRaises(ValueError):
                index.dictionary_manifest("Hero", [str(root / "missing.ueanim")])

            animation.write_bytes(b"changed animation")
            with self.assertRaises(ValueError):
                index.dictionary_manifest("Hero", [str(animation.resolve())])

    def test_dictionary_manifest_does_not_recreate_index_deleted_after_ready_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            index = self.project_index.ProjectIndex(root)
            self.assertEqual(index.scan([root])["state"], "ready")
            index.status = lambda: {"state": "ready"}
            index.path.unlink()

            with self.assertRaisesRegex(ValueError, "unavailable"):
                index.dictionary_manifest("Hero", [str(animation.resolve())])

            self.assertFalse(index.path.exists())

    def test_scan_removes_deleted_animation_from_published_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            index = self.project_index.ProjectIndex(root)
            index.scan([root])
            animation.unlink()

            result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            self.assertEqual(index.animations_for_skeleton("Hero"), [])

    def test_incremental_scan_keeps_other_scan_root_records_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            first_root = project / "first"
            second_root = project / "second"
            first = first_root / "Hero" / "Run.ueanim"
            second = second_root / "Enemy" / "Idle.ueanim"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            index = self.project_index.ProjectIndex(project)
            index.scan([first_root, second_root])
            first.unlink()

            result = index.scan([first_root])

            self.assertEqual(result["state"], "ready")
            self.assertEqual(index.animations_for_skeleton("Hero"), [])
            self.assertEqual(index.animations_for_skeleton("Enemy"), [str(second.resolve())])

    def test_incremental_scan_does_not_reparse_unchanged_animation_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            animation.with_suffix(".json").write_text(
                '[{"Type": "AnimSequence", "Skeleton": "Hero"}]', encoding="utf-8"
            )
            index = self.project_index.ProjectIndex(root)
            index.scan([root])

            with patch.object(
                self.project_index,
                "_skeleton_from_metadata",
                wraps=self.project_index._skeleton_from_metadata,
            ) as parse_metadata:
                result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            parse_metadata.assert_not_called()
            self.assertEqual(index.animations_for_skeleton("Hero"), [str(animation.resolve())])

    def test_incremental_scan_reparses_only_changed_animation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            changed = root / "Hero" / "Run.ueanim"
            unchanged = root / "Hero" / "Idle.ueanim"
            changed.parent.mkdir(parents=True)
            changed.write_bytes(b"old")
            unchanged.write_bytes(b"unchanged")
            changed.with_suffix(".json").write_text(
                '[{"Type": "AnimSequence", "Skeleton": "Hero"}]', encoding="utf-8"
            )
            unchanged.with_suffix(".json").write_text(
                '[{"Type": "AnimSequence", "Skeleton": "Hero"}]', encoding="utf-8"
            )
            index = self.project_index.ProjectIndex(root)
            index.scan([root])
            changed.write_bytes(b"new source contents")

            with patch.object(
                self.project_index,
                "_skeleton_from_metadata",
                wraps=self.project_index._skeleton_from_metadata,
            ) as parse_metadata:
                result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            self.assertEqual(parse_metadata.call_count, 1)
            self.assertEqual(
                parse_metadata.call_args.args[0], changed.with_suffix(".json")
            )
            self.assertEqual(
                index.animations_for_skeleton("Hero"),
                [str(unchanged.resolve()), str(changed.resolve())],
            )

    def test_incremental_scan_reparses_same_stat_source_and_changed_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"original")
            metadata = animation.with_suffix(".json")
            metadata.write_text(
                '[{"Type": "AnimSequence", "Skeleton": "Hero"}]', encoding="utf-8"
            )
            index = self.project_index.ProjectIndex(root)
            index.scan([root])
            original_stat = animation.stat()
            animation.write_bytes(b"changed!")
            os.utime(animation, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            metadata.write_text(
                '[{"Type": "AnimSequence", "Skeleton": "Enemy"}]', encoding="utf-8"
            )

            with patch.object(
                self.project_index,
                "_skeleton_from_metadata",
                wraps=self.project_index._skeleton_from_metadata,
            ) as parse_metadata:
                result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            self.assertEqual(parse_metadata.call_count, 1)
            self.assertEqual(index.animations_for_skeleton("Hero"), [])
            self.assertEqual(index.animations_for_skeleton("Enemy"), [str(animation.resolve())])

    def test_failed_scan_retains_preceding_published_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            index = self.project_index.ProjectIndex(root)
            first = index.scan([root])
            animation.unlink()

            original_discover = index._discover_root
            index._discover_root = lambda scan_root, **_kwargs: (_ for _ in ()).throw(OSError("unreadable"))
            try:
                failed = index.scan([root])
            finally:
                index._discover_root = original_discover

            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["generation"], first["generation"])
            self.assertEqual(index.animations_for_skeleton("Hero"), [str(animation.resolve())])

    def test_scan_rejects_overlapping_canonical_roots_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            nested = root / "nested"
            animation = nested / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            index = self.project_index.ProjectIndex(root)
            first = index.scan([nested])

            failed = index.scan([root, nested])

            self.assertEqual(failed, {"state": "failed", "generation": first["generation"]})
            self.assertEqual(
                index.animations_for_skeleton("Hero"), [str(animation.resolve())]
            )
            connection = sqlite3.connect(index.path)
            try:
                self.assertEqual(
                    connection.execute("SELECT generation FROM generations ORDER BY generation").fetchall(),
                    [(first["generation"],)],
                )
            finally:
                connection.close()

    def test_scan_rejects_overlapping_roots_without_creating_fresh_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            nested = root / "nested"
            nested.mkdir(parents=True)
            index = self.project_index.ProjectIndex(root)

            failed = index.scan([root, nested])

            self.assertEqual(failed, {"state": "failed", "generation": None})
            self.assertFalse(index.path.exists())

    def test_publish_failure_rolls_back_root_deletion_and_new_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"old animation")
            index = self.project_index.ProjectIndex(root)
            first = index.scan([root])
            animation.write_bytes(b"new animation")

            original_publish = index._publish_discovery

            def fail_after_publish(connection, discovery, generation):
                original_publish(connection, discovery, generation)
                raise sqlite3.Error("publish failed")

            index._publish_discovery = fail_after_publish
            try:
                failed = index.scan([root])
            finally:
                index._publish_discovery = original_publish

            self.assertEqual(failed, {"state": "failed", "generation": first["generation"]})
            self.assertEqual(index.animations_for_skeleton("Hero"), [str(animation.resolve())])
            connection = sqlite3.connect(index.path)
            try:
                self.assertEqual(
                    connection.execute("SELECT generation FROM generations ORDER BY generation").fetchall(),
                    [(first["generation"],)],
                )
            finally:
                connection.close()

    def test_default_scan_is_ueformat_only_and_legacy_scan_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            native = root / "Hero" / "Hero.uemodel"
            legacy_psk = root / "Hero" / "Hero.psk"
            legacy_fbx = root / "Hero" / "Hero.fbx"
            native.parent.mkdir(parents=True)
            native.write_bytes(b"native")
            legacy_psk.write_bytes(b"psk")
            legacy_fbx.write_bytes(b"fbx")

            native_index = self.project_index.ProjectIndex(root)
            self.assertEqual(native_index.scan_scope, "ueformat")
            self.assertEqual(native_index.scan([root])["state"], "ready")
            connection = sqlite3.connect(native_index.path)
            try:
                rows = connection.execute(
                    "SELECT canonical_path FROM assets ORDER BY canonical_path"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(rows, [(str(native.resolve()),)])

            legacy_index = self.project_index.ProjectIndex(root, include_legacy=True)
            self.assertEqual(legacy_index.scan_scope, "legacy-compatible")
            self.assertEqual(legacy_index.scan([root])["state"], "ready")
            connection = sqlite3.connect(legacy_index.path)
            try:
                rows = connection.execute(
                    "SELECT canonical_path FROM assets ORDER BY canonical_path"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(
                rows,
                [
                    (str(legacy_fbx.resolve()),),
                    (str(legacy_psk.resolve()),),
                    (str(native.resolve()),),
                ],
            )

    def test_model_skeleton_relation_requires_explicit_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            model = root / "Hero" / "Hero.psk"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            index = self.project_index.ProjectIndex(root, include_legacy=True)

            index.scan([root])

            connection = sqlite3.connect(index.path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT relation_type FROM relations WHERE source_path = ?",
                        (str(model.resolve()),),
                    ).fetchall(),
                    [],
                )
            finally:
                connection.close()

    def test_model_skeleton_relation_ignores_generic_skeleton_metadata_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            model = root / "Other" / "Character.psk"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            model.with_suffix(".json").write_text(
                '[{"Type": "Skeleton", "Name": "Hero"}]',
                encoding="utf-8",
            )
            index = self.project_index.ProjectIndex(root, include_legacy=True)

            index.scan([root])

            connection = sqlite3.connect(index.path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT relation_type FROM relations WHERE source_path = ?",
                        (str(model.resolve()),),
                    ).fetchall(),
                    [],
                )
            finally:
                connection.close()

    def test_model_skeleton_relation_uses_explicit_sibling_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            model = root / "Other" / "Character.psk"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            model.with_suffix(".json").write_text(
                '[{"Type": "SkeletalMesh", "Skeleton": {"ObjectName": "Skeleton\'Hero\'"}}]',
                encoding="utf-8",
            )
            index = self.project_index.ProjectIndex(root, include_legacy=True)

            index.scan([root])

            connection = sqlite3.connect(index.path)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT relation_type, target_path FROM relations WHERE source_path = ?",
                        (str(model.resolve()),),
                    ).fetchall(),
                    [("model_uses_skeleton", "Hero")],
                )
            finally:
                connection.close()


    @staticmethod
    def _strict_provenance(skeleton="Hero"):
        return {
            "state": "strict",
            "identity": {
                "skeleton_path": f"/Game/{skeleton}/{skeleton}",
                "skeleton_guid": None,
                "bone_layout_hash": "01" * 32,
            },
            "fingerprint": "strict-fingerprint",
            "reason": "",
        }

    def test_unchanged_strict_ueformat_scan_skips_content_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            index = self.project_index.ProjectIndex(root, asset_scope="animation")
            with patch.object(
                self.project_index, "_provenance_for_discovery",
                return_value=self._strict_provenance(),
            ):
                self.assertEqual(index.scan([root])["state"], "ready")

            with patch.object(
                self.project_index, "_content_hash_pair",
                wraps=self.project_index._content_hash_pair,
            ) as content_hash:
                self.assertEqual(index.scan([root])["state"], "ready")

            content_hash.assert_not_called()

    def test_force_scan_hashes_unchanged_strict_ueformat_without_reparsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            index = self.project_index.ProjectIndex(root, asset_scope="animation")
            with patch.object(
                self.project_index, "_provenance_for_discovery",
                return_value=self._strict_provenance(),
            ):
                index.scan([root])

            with patch.object(
                self.project_index, "_content_hash_pair",
                wraps=self.project_index._content_hash_pair,
            ) as content_hash, patch.object(
                self.project_index, "_provenance_for_discovery",
                side_effect=AssertionError("unchanged content must not be reparsed"),
            ):
                result = index.scan([root], force=True)

            self.assertEqual(result["state"], "ready")
            self.assertEqual(content_hash.call_count, 1)

    def test_stat_only_change_updates_fingerprint_without_reparsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            index = self.project_index.ProjectIndex(root, asset_scope="animation")
            with patch.object(
                self.project_index, "_provenance_for_discovery",
                return_value=self._strict_provenance(),
            ):
                index.scan([root])
            with closing(sqlite3.connect(index.path)) as connection:
                old_fingerprint, old_mtime = connection.execute(
                    "SELECT assets.fingerprint, files.mtime_ns FROM assets "
                    "JOIN files ON files.path = assets.canonical_path"
                ).fetchone()
            stat = animation.stat()
            os.utime(animation, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

            with patch.object(
                self.project_index, "_content_hash_pair",
                wraps=self.project_index._content_hash_pair,
            ) as content_hash, patch.object(
                self.project_index, "_provenance_for_discovery",
                side_effect=AssertionError("same content must not be reparsed"),
            ):
                result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            self.assertEqual(content_hash.call_count, 1)
            with closing(sqlite3.connect(index.path)) as connection:
                new_fingerprint, new_mtime = connection.execute(
                    "SELECT assets.fingerprint, files.mtime_ns FROM assets "
                    "JOIN files ON files.path = assets.canonical_path"
                ).fetchone()
            self.assertNotEqual(new_fingerprint, old_fingerprint)
            self.assertGreater(new_mtime, old_mtime)

    def test_cancelled_scan_preserves_previous_generation_and_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            first = root / "Hero" / "Idle.ueanim"
            first.parent.mkdir(parents=True)
            first.write_bytes(b"idle")
            index = self.project_index.ProjectIndex(root, asset_scope="animation")
            published = index.scan([root])
            (first.parent / "Run.ueanim").write_bytes(b"run")

            cancelled = index.scan([root], cancelled=lambda: True)

            self.assertEqual(cancelled, {
                "state": "cancelled", "generation": published["generation"]
            })
            with closing(sqlite3.connect(index.path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute("SELECT MAX(generation) FROM generations").fetchone()[0],
                    published["generation"],
                )

    def test_scan_progress_is_observational_and_reports_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            events = []
            index = self.project_index.ProjectIndex(root, asset_scope="animation")

            result = index.scan([root], progress=events.append)

            self.assertEqual(result["state"], "ready")
            self.assertIn("discover", {event["phase"] for event in events})
            self.assertIn("compare", {event["phase"] for event in events})
            self.assertEqual(events[-1]["phase"], "published")

    def test_animation_scope_excludes_and_removes_model_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            animation = root / "Hero" / "Run.ueanim"
            model = root / "Hero" / "Hero.uemodel"
            animation.parent.mkdir(parents=True)
            animation.write_bytes(b"animation")
            model.write_bytes(b"model")
            self.project_index.ProjectIndex(root).scan([root])

            scoped = self.project_index.ProjectIndex(root, asset_scope="animation")
            self.assertEqual(scoped.scan_scope, "ueformat-animation")
            self.assertEqual(scoped.scan([root])["state"], "ready")

            with closing(sqlite3.connect(scoped.path)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT asset_type, COUNT(*) FROM assets GROUP BY asset_type"
                    ).fetchall(),
                    [("animation", 1)],
                )

    def test_rejects_unknown_asset_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "asset scope"):
                self.project_index.ProjectIndex(directory, asset_scope="texture")


if __name__ == "__main__":
    unittest.main()
