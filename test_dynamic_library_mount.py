"""Contracts for identity-safe dynamic Blender asset-library mounting."""

import importlib.util
import os
import sys
import types
import unittest
from unittest import mock


class DynamicLibraryMountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_bpy = sys.modules.get("bpy")
        cls.bpy = types.ModuleType("bpy")
        cls.bpy.path = types.SimpleNamespace(abspath=lambda value: value)
        cls.bpy.data = types.SimpleNamespace(filepath="")
        sys.modules["bpy"] = cls.bpy
        path = os.path.join(os.path.dirname(__file__), "core", "utils.py")
        spec = importlib.util.spec_from_file_location("test_core_utils", path)
        cls.utils = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.utils)

    @classmethod
    def tearDownClass(cls):
        if cls.previous_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = cls.previous_bpy

    def _configure(self, libraries):
        self.bpy.context = types.SimpleNamespace(preferences=types.SimpleNamespace(
            filepaths=types.SimpleNamespace(asset_libraries=libraries)
        ))

        def add_library(*, directory):
            libraries.append(types.SimpleNamespace(name="", path=directory, library_id=""))

        def remove_library(*, index):
            libraries.pop(index)

        self.bpy.ops = types.SimpleNamespace(preferences=types.SimpleNamespace(
            asset_library_add=mock.Mock(side_effect=add_library),
            asset_library_remove=mock.Mock(side_effect=remove_library),
        ))

    def setUp(self):
        self.utils._DYNAMIC_LIBRARY_OWNERS.clear()

    def test_same_display_name_at_different_path_is_not_repointed(self):
        existing = types.SimpleNamespace(name="[FModel_Auto] Heroes", path=os.path.join("old", "Heroes"))
        libraries = [existing]
        self._configure(libraries)

        self.assertTrue(self.utils.mount_dynamic_library("Heroes", os.path.join("new", "Heroes")))

        self.assertEqual(existing.path, os.path.normpath(os.path.join("old", "Heroes")))
        self.assertEqual(len(libraries), 2)
        self.assertEqual(libraries[1].path, os.path.abspath(os.path.join("new", "Heroes")))

    def test_normalized_path_is_identity_even_when_display_name_differs(self):
        target = os.path.abspath(os.path.join("library", "Heroes"))
        existing = types.SimpleNamespace(name="Old display name", path=target + os.sep + ".")
        libraries = [existing]
        self._configure(libraries)

        self.assertTrue(self.utils.mount_dynamic_library("Heroes", target))

        self.bpy.ops.preferences.asset_library_add.assert_not_called()
        self.assertEqual(existing.name, "[FModel_Auto] Heroes")
        self.assertEqual(existing.path, os.path.normpath(target))

    def test_persistent_id_can_follow_library_to_a_new_path(self):
        existing = types.SimpleNamespace(
            name="Old display name", path=os.path.join("old", "Heroes"), library_id="stable-id"
        )
        libraries = [existing]
        self._configure(libraries)

        self.assertTrue(self.utils.mount_dynamic_library(
            "Heroes", os.path.join("new", "Heroes"), "stable-id"
        ))

        self.bpy.ops.preferences.asset_library_add.assert_not_called()
        self.assertEqual(existing.path, os.path.abspath(os.path.join("new", "Heroes")))

    def test_add_without_target_registration_fails_closed(self):
        libraries = []
        self._configure(libraries)
        self.bpy.ops.preferences.asset_library_add.side_effect = lambda **kwargs: None

        self.assertFalse(self.utils.mount_dynamic_library("Heroes", os.path.join("new", "Heroes")))

        self.assertEqual(libraries, [])

    def test_cleanup_preserves_external_entry_with_fmodel_display_prefix(self):
        external = types.SimpleNamespace(
            name="[FModel_Auto] External", path=os.path.join("external", "Library")
        )
        libraries = [external]
        self._configure(libraries)

        self.utils.cleanup_dynamic_libraries()

        self.assertEqual(libraries, [external])
        self.bpy.ops.preferences.asset_library_remove.assert_not_called()

    def test_cleanup_removes_entry_added_by_mount(self):
        libraries = []
        self._configure(libraries)
        target = os.path.join("new", "Heroes")

        self.assertTrue(self.utils.mount_dynamic_library("Heroes", target, "heroes-id"))
        self.utils.cleanup_dynamic_libraries()

        self.assertEqual(libraries, [])
        self.bpy.ops.preferences.asset_library_remove.assert_called_once_with(index=0)
        self.assertEqual(self.utils._DYNAMIC_LIBRARY_OWNERS, [])

    def test_cleanup_uses_tracked_nonempty_id_when_owned_path_changes(self):
        libraries = []
        self._configure(libraries)

        self.assertTrue(self.utils.mount_dynamic_library(
            "Heroes", os.path.join("new", "Heroes"), "heroes-id"
        ))
        libraries[0].path = os.path.join("moved", "Heroes")
        self.utils.cleanup_dynamic_libraries()

        self.assertEqual(libraries, [])
        self.bpy.ops.preferences.asset_library_remove.assert_called_once_with(index=0)

    def test_cleanup_removes_only_owned_full_identity_pair(self):
        libraries = []
        self._configure(libraries)
        owned_path = os.path.join("owned", "Heroes")

        self.assertTrue(self.utils.mount_dynamic_library("Heroes", owned_path, "owned-id"))
        external_same_path = types.SimpleNamespace(
            name="External path", path=owned_path, library_id="external-id"
        )
        external_same_id = types.SimpleNamespace(
            name="External ID", path=os.path.join("external", "Heroes"), library_id="owned-id"
        )
        libraries.extend((external_same_path, external_same_id))

        self.utils.cleanup_dynamic_libraries()

        self.assertEqual(libraries, [external_same_path, external_same_id])
        self.bpy.ops.preferences.asset_library_remove.assert_called_once_with(index=0)

    def test_cleanup_no_id_owner_does_not_remove_same_path_entry_with_id(self):
        libraries = []
        self._configure(libraries)
        owned_path = os.path.join("owned", "Heroes")

        self.assertTrue(self.utils.mount_dynamic_library("Heroes", owned_path))
        external = types.SimpleNamespace(name="External", path=owned_path, library_id="external-id")
        libraries.append(external)

        self.utils.cleanup_dynamic_libraries()

        self.assertEqual(libraries, [external])
        self.bpy.ops.preferences.asset_library_remove.assert_called_once_with(index=0)

    def test_cleanup_retains_owner_after_remove_failure_for_retry(self):
        libraries = []
        self._configure(libraries)

        self.assertTrue(self.utils.mount_dynamic_library("Heroes", os.path.join("owned", "Heroes")))
        self.bpy.ops.preferences.asset_library_remove.side_effect = RuntimeError("temporary failure")

        self.utils.cleanup_dynamic_libraries()

        self.assertEqual(len(libraries), 1)
        self.assertEqual(len(self.utils._DYNAMIC_LIBRARY_OWNERS), 1)
        self.bpy.ops.preferences.asset_library_remove.side_effect = lambda *, index: libraries.pop(index)

        self.utils.cleanup_dynamic_libraries()

        self.assertEqual(libraries, [])
        self.assertEqual(self.utils._DYNAMIC_LIBRARY_OWNERS, [])

    def test_preexisting_path_or_id_match_is_not_claimed_for_cleanup(self):
        path_match = types.SimpleNamespace(
            name="External path", path=os.path.join("existing", "Path")
        )
        id_match = types.SimpleNamespace(
            name="External ID", path=os.path.join("existing", "ID"), library_id="stable-id"
        )
        libraries = [path_match, id_match]
        self._configure(libraries)

        self.assertTrue(self.utils.mount_dynamic_library("Path", path_match.path))
        self.assertTrue(self.utils.mount_dynamic_library("ID", os.path.join("moved", "ID"), "stable-id"))
        self.utils.cleanup_dynamic_libraries()

        self.assertEqual(libraries, [path_match, id_match])
        self.bpy.ops.preferences.asset_library_remove.assert_not_called()
        self.assertEqual(self.utils._DYNAMIC_LIBRARY_OWNERS, [])


if __name__ == "__main__":
    unittest.main()
