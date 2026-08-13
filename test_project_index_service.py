"""Pure-Python contracts for automatic Animation Pipeline index preparation."""

import sqlite3
import tempfile
from contextlib import closing
import unittest
from pathlib import Path
from unittest import mock

from core.project_index_service import (
    ProjectAnimationIndexService,
    ProjectIndexServiceError,
)


class ProjectAnimationIndexServiceTests(unittest.TestCase):
    @staticmethod
    def _asset(root: Path, skeleton="Hero", name="Run") -> Path:
        directory = root / skeleton
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.ueanim"
        path.write_bytes(b"legacy-or-invalid-ueformat-test-payload")
        return path

    def test_inspect_missing_index_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = ProjectAnimationIndexService()

            snapshot = service.inspect(root, "Hero")

            self.assertEqual(snapshot.state, "missing")
            self.assertEqual(snapshot.animation_count, 0)
            self.assertFalse((root / ".fmodel" / "project-assets.sqlite").exists())

    def test_refresh_creates_scans_and_reports_matching_animations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._asset(root)
            service = ProjectAnimationIndexService()

            snapshot = service.refresh(root, "Hero")

            self.assertEqual(snapshot.state, "ready")
            self.assertEqual(snapshot.schema_version, 3)
            self.assertEqual(snapshot.animation_count, 1)
            self.assertEqual(snapshot.matching_count, 1)
            self.assertEqual(snapshot.generation, 1)
            self.assertTrue((root / ".fmodel" / "project-assets.sqlite").is_file())

    def test_refresh_rebuilds_an_incompatible_derived_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._asset(root)
            database = root / ".fmodel" / "project-assets.sqlite"
            database.parent.mkdir()
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA user_version = 2")
                connection.execute("CREATE TABLE obsolete (value TEXT)")
                connection.commit()
            service = ProjectAnimationIndexService()

            snapshot = service.refresh(root, "Hero")

            self.assertEqual(snapshot.state, "ready")
            self.assertEqual(snapshot.schema_version, 3)
            self.assertEqual(snapshot.matching_count, 1)

    def test_prepare_selection_builds_manifest_for_all_matching_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._asset(root, name="Idle")
            second = self._asset(root, name="Run")
            service = ProjectAnimationIndexService()

            prepared = service.prepare_selection(root, "Hero")

            self.assertEqual(prepared.skeleton, "Hero")
            self.assertEqual(set(prepared.paths), {str(first.resolve()), str(second.resolve())})
            self.assertEqual(prepared.manifest["source_type"], "fmodel_ueanim")
            self.assertIn("Hero", prepared.manifest["skeletons"])

    def test_prepare_paths_preserves_an_explicit_legacy_subset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = self._asset(root, name="Idle")
            self._asset(root, name="Run")
            service = ProjectAnimationIndexService()

            prepared = service.prepare_paths(root, "Hero", [selected])

            self.assertEqual(prepared.paths, (str(selected.resolve()),))
            animations = prepared.manifest["skeletons"]["Hero"]["Base"]["__animations__"]
            self.assertEqual(set(animations), {"Idle"})

    def test_prepare_selection_reports_no_match_distinctly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._asset(root, skeleton="Hero")
            service = ProjectAnimationIndexService()

            with self.assertRaises(ProjectIndexServiceError) as raised:
                service.prepare_selection(root, "Enemy")

            self.assertEqual(raised.exception.code, "no_match")
            self.assertIn("Enemy", str(raised.exception))

    def test_refresh_rejects_an_unavailable_root_without_creating_parent_state(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "Missing"
            service = ProjectAnimationIndexService()

            with self.assertRaises(ProjectIndexServiceError) as raised:
                service.refresh(missing, "Hero")

            self.assertEqual(raised.exception.code, "root_unavailable")
            self.assertFalse(missing.exists())


    def test_default_service_scope_excludes_model_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._asset(root)
            (root / "Hero" / "Hero.uemodel").write_bytes(b"model")
            service = ProjectAnimationIndexService()

            snapshot = service.refresh(root, "Hero")

            self.assertEqual(snapshot.animation_count, 1)
            with closing(sqlite3.connect(root / ".fmodel" / "project-assets.sqlite")) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT DISTINCT asset_type FROM assets"
                    ).fetchall(),
                    [("animation",)],
                )

    def test_cancelled_refresh_raises_structured_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._asset(root)
            service = ProjectAnimationIndexService()

            with self.assertRaises(ProjectIndexServiceError) as raised:
                service.refresh(root, "Hero", cancelled=lambda: True)

            self.assertEqual(raised.exception.code, "cancelled")

    def test_prepare_retry_uses_force_scan_once_after_manifest_race(self):
        class RacingIndex:
            root = Path("C:/Project")

            def __init__(self):
                self.scan_forces = []
                self.manifest_calls = 0

            def status(self):
                return {"state": "ready", "schema_version": 3, "rebuild_required": False}

            def scan(self, roots, *, force=False, progress=None, cancelled=None):
                self.scan_forces.append(force)
                return {"state": "ready", "generation": len(self.scan_forces)}

            def summary(self, skeleton, raise_on_error=False):
                return {
                    "state": "ready", "schema_version": 3,
                    "rebuild_required": False, "generation": len(self.scan_forces),
                    "last_scan_ns": 1, "animation_count": 1, "matching_count": 1,
                }

            def animations_for_skeleton(self, skeleton, raise_on_error=False):
                return ["C:/Project/Hero/Run.ueanim"]

            def dictionary_manifest(self, skeleton, paths):
                self.manifest_calls += 1
                if self.manifest_calls == 1:
                    raise ValueError("changed between scan and manifest")
                return {"skeletons": {skeleton: {}}}

        index = RacingIndex()
        service = ProjectAnimationIndexService(
            index_factory=lambda root, **kwargs: index
        )
        with mock.patch.object(Path, "is_dir", return_value=True):
            prepared = service.prepare_selection("C:/Project", "Hero")

        self.assertEqual(index.scan_forces, [False, True])
        self.assertEqual(prepared.skeleton, "Hero")


if __name__ == "__main__":
    unittest.main()
