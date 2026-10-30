"""Contracts for skeleton search labels and upstream binding migration."""

from __future__ import annotations

import importlib
import sys
import types
import unittest


class SkeletonCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_bpy = sys.modules.get("bpy")
        cls._saved_core = {
            name: module for name, module in sys.modules.items()
            if name == "core" or name.startswith("core.")
        }
        sys.modules["bpy"] = types.ModuleType("bpy")
        for name in tuple(cls._saved_core):
            sys.modules.pop(name, None)
        cls.compat = importlib.import_module("core.skeleton_compatibility")
        cls.multi_import = importlib.import_module("core.multi_import")
        cls.types = importlib.import_module("core.types")
        cls.addClassCleanup(cls._restore_modules)

    @classmethod
    def _restore_modules(cls):
        for name in tuple(sys.modules):
            if name == "core" or name.startswith("core."):
                sys.modules.pop(name, None)
        sys.modules.update(cls._saved_core)
        if cls._saved_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = cls._saved_bpy

    @staticmethod
    def _manifest():
        return {
            "skeletons": {
                "SKEL_FM_Base.SKEL_FM_Base": {
                    "Base": {
                        "__animations__": {
                            "Idle": {"source_file": "C:/Game/Base/Idle.ueanim"},
                        },
                    },
                },
            },
        }

    def test_upstream_is_the_default_and_roundtrips(self):
        plan, unmapped = self.multi_import.build_multi_import_plan(
            self._manifest(), {"SKEL_FM_Base.SKEL_FM_Base": "SKEL_FM_Lynn_Seashell"},
        )
        self.assertEqual(unmapped, [])
        self.assertEqual(plan.tasks[0].compatibility_mode, self.compat.SKELETON_COMPATIBILITY_UEFORMAT)
        restored = self.types.MultiImportPlan.from_dict(plan.to_dict(), validate=True)
        self.assertEqual(restored.tasks[0].compatibility_mode, "ueformat")

    def test_old_modes_migrate_to_one_task_identity(self):
        source = "SKEL_FM_Base.SKEL_FM_Base"
        strict, _ = self.multi_import.build_multi_import_plan(
            self._manifest(), {source: "SKEL_FM_Lynn_Seashell"},
        )
        compatible, _ = self.multi_import.build_multi_import_plan(
            self._manifest(), {source: "SKEL_FM_Lynn_Seashell"},
            default_compatibility_mode=self.compat.SKELETON_COMPATIBILITY_NAME_RETARGET,
        )
        task = compatible.tasks[0]
        self.assertEqual(task.compatibility_mode, "ueformat")
        self.assertEqual(strict.tasks[0].task_id, task.task_id)
        self.assertEqual(
            self.types.MultiImportTask.from_dict(task.to_dict(), validate=True).compatibility_mode,
            "ueformat",
        )

    def test_per_source_old_mode_migrates_to_upstream(self):
        source = "SKEL_FM_Base.SKEL_FM_Base"
        plan, _ = self.multi_import.build_multi_import_plan(
            self._manifest(), {source: "SKEL_FM_Lynn_Seashell"},
            skeleton_compatibility_modes={source: "name_retarget"},
        )
        self.assertEqual(plan.tasks[0].compatibility_mode, "ueformat")

    def test_invalid_mode_is_rejected_at_config_and_checkpoint_boundaries(self):
        with self.assertRaises(ValueError):
            self.compat.normalize_skeleton_compatibility_mode("guess")
        with self.assertRaises(ValueError):
            self.types.PipelineConfig(skeleton_compatibility_mode="guess")
        with self.assertRaisesRegex(ValueError, "compatibility_mode"):
            self.types.MultiImportTask.from_dict(
                {"source_skeleton": "Base", "compatibility_mode": "guess"},
                validate=True,
            )

    def test_skeleton_filter_accepts_only_the_documented_repeated_name_alias(self):
        match = self.compat.skeleton_names_match
        self.assertTrue(match("SKEL_FM_Base.SKEL_FM_Base", "SKEL_FM_Base"))
        self.assertTrue(match("/Game/Characters/SKEL_FM_Base.SKEL_FM_Base", "skel_fm_base"))
        self.assertTrue(match("SKEL_FM_Base", "SKEL_FM_Base.SKEL_FM_Base"))
        self.assertTrue(match("SK_Hero_Skeleton", "Hero"))
        self.assertTrue(match("SKEL_FM_Base", "FM_Base"))
        self.assertFalse(match("SKEL_FM_Base_Child", "SKEL_FM_Base"))
        self.assertFalse(match("SKEL_FM_Base", "SKEL_FM_Base_Child"))
        self.assertFalse(match("SK_Hero_Skeleton_Child", "Hero"))

    def _library_pair(self):
        from core.skeleton_provenance import inspect_skeleton_provenance
        identity = {"version": 1, "skeleton_path": "/Game/Hero", "bone_layout_hash": "ab" * 32,
                    "has_skeleton_guid": False, "skeleton_guid": None}
        action = types.SimpleNamespace(
            get=lambda key, default=None: identity if key == "fmodel_target_skeleton_identity" else default,
            fcurves=[types.SimpleNamespace(data_path='pose.bones["root"].location')],
        )
        target = types.SimpleNamespace(
            type="ARMATURE", name="HeroRig",
            get=lambda key, default=None: identity if key == "fmodel_skeleton_identity" else default,
            data=types.SimpleNamespace(bones=[types.SimpleNamespace(name="root")]),
        )
        return action, target, identity, inspect_skeleton_provenance(identity).to_dict()

    def test_library_actions_do_not_require_identity_or_complete_channels(self):
        for policy in (None, "strict", "bone_names", "ueformat"):
            for scenario in ("different_identity", "missing_identity", "malformed_identity", "missing_bones", "curve_only"):
                with self.subTest(policy=policy, scenario=scenario):
                    action, target, identity, _ = self._library_pair()
                    if scenario == "different_identity":
                        target.get = lambda key, default=None: {**identity, "skeleton_path": "/Game/Other"}
                    elif scenario == "missing_identity":
                        action.get = lambda key, default=None: default
                    elif scenario == "malformed_identity":
                        action.get = lambda key, default=None: "invalid-json"
                    elif scenario == "missing_bones":
                        target.data.bones = []
                    elif scenario == "curve_only":
                        action.fcurves = []
                    self.assertEqual(self.compat.validate_library_action(action, target, policy), "")

    def test_library_requires_an_armature_target(self):
        action, _target, _identity, _provenance = self._library_pair()
        for target in (None, types.SimpleNamespace(type="MESH")):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "目标骨架"):
                self.compat.validate_library_action(action, target)

    def test_empty_skeleton_filter_remains_unfiltered(self):
        self.assertTrue(self.compat.skeleton_names_match("SKEL_FM_Base", ""))
        self.assertTrue(self.compat.skeleton_names_match("", "SKEL_FM_Base"))


if __name__ == "__main__":
    unittest.main()
