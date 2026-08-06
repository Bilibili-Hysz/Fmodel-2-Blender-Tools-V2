"""Fake-Blender contract for per-task FBX matching."""

import importlib.util
import os
import sys
import types
import unittest


class FakeAction(dict):
    registry = None

    def __init__(self, name, source="", source_ref="", library=None):
        super().__init__()
        if source:
            self["Pipeline_Source"] = source
        if source_ref:
            self["Pipeline_SourceRef"] = source_ref
        self.name = name
        self.frame_range = (1.0, 20.0)
        self.library = library

    def copy(self):
        copied = FakeAction(self.name)
        copied.update(self)
        if self.registry is not None:
            self.registry.append(copied)
        return copied

    def __bool__(self):
        return True


class FakeActions(list):
    pass


class FakeBpy:
    def __init__(self, actions):
        self.data = types.SimpleNamespace(actions=FakeActions(actions))


class FakeContext:
    def __init__(self, manifest, config):
        self.dictionary_manifest = manifest
        self.config = config
        self.imported_assets = None
        self.logs = []

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value

    def log_info(self, stage, message):
        self.logs.append((stage, message))


class FbxMatchSubsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_modules = {name: sys.modules.get(name) for name in (
            "bpy", "addon", "addon.core", "addon.Fmodel",
            "addon.Fmodel.Pipeline", "addon.core.action_selection",
        )}
        cls.bpy = FakeBpy([
            FakeAction("Idle_Hero", "C:/Anims/Hero/Idle.fbx", "Hero/Moves/Idle"),
            FakeAction("Idle_Enemy", "C:/Anims/Enemy/Idle.fbx", "Enemy/Moves/Idle"),
        ])
        sys.modules["bpy"] = cls.bpy

        import core
        core_module = types.ModuleType("addon.core")
        core_module.__dict__.update(core.__dict__)
        addon = types.ModuleType("addon")
        addon.__path__ = []
        fmodel = types.ModuleType("addon.Fmodel")
        fmodel.__path__ = []
        pipeline = types.ModuleType("addon.Fmodel.Pipeline")
        pipeline.__path__ = []
        sys.modules.update({
            "addon": addon,
            "addon.core": core_module,
            "addon.Fmodel": fmodel,
            "addon.Fmodel.Pipeline": pipeline,
        })
        selection_path = os.path.join(os.path.dirname(__file__), "core", "action_selection.py")
        selection_spec = importlib.util.spec_from_file_location(
            "addon.core.action_selection", selection_path
        )
        selection = importlib.util.module_from_spec(selection_spec)
        sys.modules[selection_spec.name] = selection
        selection_spec.loader.exec_module(selection)
        stages_path = os.path.join(
            os.path.dirname(__file__), "Fmodel", "Pipeline", "stages.py"
        )
        stages_spec = importlib.util.spec_from_file_location(
            "addon.Fmodel.Pipeline.stages", stages_path
        )
        cls.stages = importlib.util.module_from_spec(stages_spec)
        sys.modules[stages_spec.name] = cls.stages
        stages_spec.loader.exec_module(cls.stages)

    @classmethod
    def tearDownClass(cls):
        for name, module in cls.old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    @staticmethod
    def manifest():
        return {"skeletons": {
            "Hero": {"Moves": {"__animations__": {
                "Idle": {"hashed_name": "Idle_Hero", "source_file": "C:/Anims/Hero/Idle.fbx"},
            }}},
            "Enemy": {"Moves": {"__animations__": {
                "Idle": {"hashed_name": "Idle_Enemy", "source_file": "C:/Anims/Enemy/Idle.fbx"},
            }}},
        }}

    def setUp(self):
        self.bpy.data.actions[:] = [
            FakeAction("Idle_Hero", "C:/Anims/Hero/Idle.fbx", "Hero/Moves/Idle"),
            FakeAction("Idle_Enemy", "C:/Anims/Enemy/Idle.fbx", "Enemy/Moves/Idle"),
        ]
        FakeAction.registry = self.bpy.data.actions

    def test_fbx_match_copies_external_action_before_stamping_provenance(self):
        external = FakeAction("Idle_Hero")
        self.bpy.data.actions[:] = [external]
        context = FakeContext(self.manifest(), {
            "target_armature": "HeroRig", "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx"],
        })

        self.assertTrue(self.stages.AssetImportStage()._import_fbx_match(context))

        self.assertNotIn("Pipeline_Source", external)
        owned = next(action for action in self.bpy.data.actions if action is not external)
        self.assertEqual(owned["Pipeline_Source"], "C:/Anims/Hero/Idle.fbx")

    def test_each_task_matches_only_its_own_source_and_target_scope(self):
        stage = self.stages.AssetImportStage()
        hero_context = FakeContext(self.manifest(), {
            "target_armature": "HeroRig",
            "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx"],
        })
        enemy_context = FakeContext(self.manifest(), {
            "target_armature": "EnemyRig",
            "source_skeleton": "Enemy",
            "source_subset": ["C:/Anims/Enemy/Idle.fbx"],
        })

        self.assertTrue(stage._import_fbx_match(hero_context))
        self.assertTrue(stage._import_fbx_match(enemy_context))
        self.assertEqual(
            [item["source_manifest_ref"] for item in hero_context.imported_assets["imported_actions"]],
            ["Hero/Moves/Idle"],
        )
        self.assertEqual(
            [item["source_manifest_ref"] for item in enemy_context.imported_assets["imported_actions"]],
            ["Enemy/Moves/Idle"],
        )
        self.assertEqual(hero_context.get_config("target_armature"), "HeroRig")
        self.assertEqual(enemy_context.get_config("target_armature"), "EnemyRig")
        self.assertEqual(
            len(hero_context.imported_assets["imported_actions"])
            + len(enemy_context.imported_assets["imported_actions"]),
            2,
        )

    def test_legacy_skeleton_filter_scopes_fbx_matching(self):
        context = FakeContext(self.manifest(), {
            "target_armature": "HeroRig",
            "skeleton_filter": "Hero",
        })

        self.assertTrue(self.stages.AssetImportStage()._import_fbx_match(context))
        self.assertEqual(
            [item["source_manifest_ref"] for item in context.imported_assets["imported_actions"]],
            ["Hero/Moves/Idle"],
        )

    def test_matching_retry_accepts_renamed_action_with_same_verified_identity(self):
        stage = self.stages.AssetImportStage()
        context = FakeContext(self.manifest(), {
            "import_mode": "fbx_match",
            "target_armature": "HeroRig",
            "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx"],
        })
        self.assertTrue(stage.execute(context))
        first_result = context.imported_assets
        self.bpy.data.actions[:] = [
            FakeAction("Replacement", "C:/Anims/Hero/Idle.fbx", "Hero/Moves/Idle")
        ]

        self.assertTrue(stage.execute(context))
        self.assertIsNot(context.imported_assets, first_result)
        self.assertEqual(
            context.imported_assets["imported_actions"][0]["action_name"], "Replacement"
        )

    def test_matching_retry_reuses_matching_task_result_without_rematching(self):
        stage = self.stages.AssetImportStage()
        context = FakeContext(self.manifest(), {
            "import_mode": "fbx_match",
            "target_armature": "HeroRig",
            "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx"],
        })
        self.assertTrue(stage.execute(context))
        context.dictionary_manifest["skeletons"]["Hero"]["Moves"]["__animations__"]["Idle"]["hashed_name"] = "NoMatch"

        self.assertTrue(stage.execute(context))
        self.assertEqual(
            context.imported_assets["imported_actions"][0]["action_name"], "Idle_Hero"
        )

    def test_task_scoped_partial_match_is_incomplete(self):
        self.bpy.data.actions[:] = [
            FakeAction("Idle_Hero", "C:/Anims/Hero/Idle.fbx", "Hero/Moves/Idle"),
        ]
        manifest = self.manifest()
        manifest["skeletons"]["Hero"]["Moves"]["__animations__"]["Run"] = {
            "hashed_name": "Run_Hero", "source_file": "C:/Anims/Hero/Run.fbx",
        }
        context = FakeContext(manifest, {
            "target_armature": "HeroRig", "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx", "C:/Anims/Hero/Run.fbx"],
        })

        self.assertFalse(self.stages.AssetImportStage()._import_fbx_match(context))
        self.assertEqual(len(context.imported_assets["imported_actions"]), 1)
        self.assertGreaterEqual(len(context.imported_assets["failed_imports"]), 1)

    def test_unscoped_partial_match_is_incomplete(self):
        self.bpy.data.actions[:] = [
            FakeAction("Idle_Hero", "C:/Anims/Hero/Idle.fbx", "Hero/Moves/Idle"),
        ]
        manifest = self.manifest()
        manifest["skeletons"]["Hero"]["Moves"]["__animations__"]["Run"] = {
            "hashed_name": "Run_Hero", "source_file": "C:/Anims/Hero/Run.fbx",
        }
        context = FakeContext(manifest, {"target_armature": "HeroRig"})

        self.assertFalse(self.stages.AssetImportStage()._import_fbx_match(context))
        self.assertEqual(len(context.imported_assets["imported_actions"]), 1)
        self.assertGreaterEqual(len(context.imported_assets["failed_imports"]), 1)

    def test_duplicate_fbx_candidates_are_rejected_as_ambiguous(self):
        self.bpy.data.actions[:] = [
            FakeAction("Idle_Hero_A", "C:/Anims/Hero/Idle.fbx", "Hero/Moves/Idle"),
            FakeAction("Idle_Hero_B", "C:/Anims/Hero/Idle.fbx", "Hero/Moves/Idle"),
        ]
        context = FakeContext(self.manifest(), {
            "target_armature": "HeroRig", "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx"],
        })

        self.assertFalse(self.stages.AssetImportStage()._import_fbx_match(context))
        self.assertEqual(context.imported_assets["imported_actions"], [])
        self.assertEqual(len(context.imported_assets["failed_imports"]), 1)
        self.assertIn("ambiguous", context.imported_assets["failed_imports"][0]["error"])

    def test_task_scope_claims_one_untagged_unique_name_and_stamps_identity(self):
        action = FakeAction("Idle_Hero")
        self.bpy.data.actions[:] = [action]
        context = FakeContext(self.manifest(), {
            "target_armature": "HeroRig", "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx"],
        })

        self.assertTrue(self.stages.AssetImportStage()._import_fbx_match(context))
        owned = next(candidate for candidate in self.bpy.data.actions if candidate is not action)
        self.assertEqual(owned["Pipeline_Source"], "C:/Anims/Hero/Idle.fbx")
        self.assertEqual(owned["Pipeline_SourceRef"], "Hero/Moves/Idle")

    def test_task_scope_rejects_ambiguous_untagged_name_fallback(self):
        self.bpy.data.actions[:] = [FakeAction("Idle_Hero_A"), FakeAction("Idle_Hero_B")]
        context = FakeContext(self.manifest(), {
            "target_armature": "HeroRig", "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx"],
        })

        self.assertFalse(self.stages.AssetImportStage()._import_fbx_match(context))
        self.assertEqual(context.imported_assets["imported_actions"], [])

    def test_task_scope_rejects_untagged_substring_name_fallback(self):
        self.bpy.data.actions[:] = [FakeAction("Idle_Hero_Copy")]
        context = FakeContext(self.manifest(), {
            "target_armature": "HeroRig", "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx"],
        })

        self.assertFalse(self.stages.AssetImportStage()._import_fbx_match(context))
        self.assertEqual(context.imported_assets["imported_actions"], [])

    def test_task_scope_accepts_exact_tagged_identity_without_name_substring(self):
        action = FakeAction(
            "RenamedAction", "C:/Anims/Hero/Idle.fbx", "Hero/Moves/Idle"
        )
        self.bpy.data.actions[:] = [action]
        context = FakeContext(self.manifest(), {
            "target_armature": "HeroRig", "source_skeleton": "Hero",
            "source_subset": ["C:/Anims/Hero/Idle.fbx"],
        })

        self.assertTrue(self.stages.AssetImportStage()._import_fbx_match(context))
        self.assertEqual(context.imported_assets["imported_actions"][0]["action_name"],
                         "RenamedAction")

    def test_root_folder_uses_canonical_uncategorized_ref(self):
        action = FakeAction("Idle_Hero")
        self.bpy.data.actions[:] = [action]
        manifest = {"skeletons": {"Hero": {"": {"__animations__": {
            "Idle": {"hashed_name": "Idle_Hero", "source_file": "C:/Anims/Hero/Idle.fbx"},
        }}}}}
        context = FakeContext(manifest, {"source_skeleton": "Hero"})

        self.assertTrue(self.stages.AssetImportStage()._import_fbx_match(context))
        owned = next(candidate for candidate in self.bpy.data.actions if candidate is not action)
        self.assertEqual(owned["Pipeline_SourceRef"], "Hero/Uncategorized/Idle")


if __name__ == "__main__":
    unittest.main()
