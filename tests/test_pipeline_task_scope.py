"""Fake-Blender contracts for task-scoped library and UEAnim imports."""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
import ast
import copy
import json
from unittest import mock


class FakeActions(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)


class FakeAction(dict):
    registry = None

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.frame_range = (1.0, 10.0)
        self.library = None

    def copy(self):
        copied = FakeAction(self.name)
        copied.update(self)
        if self.registry is not None:
            self.registry.append(copied)
        return copied

    __hash__ = object.__hash__

    def __bool__(self):
        return True


class FakeLibraryLoad:
    def __init__(self, target_actions, source_actions, path=""):
        self.target_actions = target_actions
        self.source_actions = source_actions
        self.path = path

    def __enter__(self):
        self.data_to = types.SimpleNamespace(actions=[])
        return types.SimpleNamespace(actions=self.source_actions), self.data_to

    def __exit__(self, *args):
        for action in self.data_to.actions:
            action.library = types.SimpleNamespace(filepath=self.path)
        self.target_actions.extend(self.data_to.actions)


class FakeContext:
    def __init__(self, manifest, config):
        self.dictionary_manifest = manifest
        self.config = config
        self.imported_assets = None
        self.logs = []
        self.stage_status = {}
        self.blender_saves = 0
        self.saved_checkpoints = []

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value

    def set_stage_status(self, stage, status):
        self.stage_status[stage] = status

    def log_info(self, stage, message):
        self.logs.append(("info", message))

    def log_warning(self, stage, message):
        self.logs.append(("warning", message))

    def log_error(self, stage, message):
        self.logs.append(("error", message))

    def save_to_blender_text(self):
        self.blender_saves += 1
        self.saved_checkpoints.append(copy.deepcopy(self.config.get("import_checkpoint")))

    def persist_checkpoint(self):
        self.save_to_blender_text()


class TaskScopedImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {name: sys.modules.get(name) for name in (
            "bpy", "addon", "addon.core", "addon.Fmodel", "addon.Fmodel.Pipeline",
            "addon.core.action_selection", "addon.Fmodel.importers",
            "addon.Fmodel.importers.animation", "addon.Fmodel.importers.animation.operator_anim_ueformat",
        )}
        cls.actions = FakeActions()
        cls.bpy = types.ModuleType("bpy")
        cls.bpy.data = types.SimpleNamespace(
            actions=cls.actions,
            objects=types.SimpleNamespace(get=lambda name: None),
            libraries=types.SimpleNamespace(
                load=lambda path, link: FakeLibraryLoad(cls.actions, [FakeAction(os.path.basename(path))], path),
                write=lambda **kwargs: None,
            ),
        )
        cls.bpy.path = types.SimpleNamespace(abspath=lambda value: value)
        cls.bpy.ops = types.SimpleNamespace(wm=types.SimpleNamespace(redraw_timer=lambda **kwargs: None))
        sys.modules["bpy"] = cls.bpy

        import core
        addon = types.ModuleType("addon"); addon.__path__ = []
        fmodel = types.ModuleType("addon.Fmodel"); fmodel.__path__ = []
        pipeline = types.ModuleType("addon.Fmodel.Pipeline"); pipeline.__path__ = []
        importers = types.ModuleType("addon.Fmodel.importers"); importers.__path__ = []
        animation = types.ModuleType("addon.Fmodel.importers.animation"); animation.__path__ = []
        core_module = types.ModuleType("addon.core"); core_module.__dict__.update(core.__dict__)
        sys.modules.update({"addon": addon, "addon.core": core_module, "addon.Fmodel": fmodel,
                            "addon.Fmodel.Pipeline": pipeline, "addon.Fmodel.importers": importers,
                            "addon.Fmodel.importers.animation": animation})
        selection_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core", "action_selection.py")
        selection_spec = importlib.util.spec_from_file_location("addon.core.action_selection", selection_path)
        selection = importlib.util.module_from_spec(selection_spec)
        sys.modules[selection_spec.name] = selection
        selection_spec.loader.exec_module(selection)
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        stages_spec = importlib.util.spec_from_file_location("addon.Fmodel.Pipeline.stages", stages_path)
        cls.stages = importlib.util.module_from_spec(stages_spec)
        sys.modules[stages_spec.name] = cls.stages
        stages_spec.loader.exec_module(cls.stages)

    @classmethod
    def tearDownClass(cls):
        for name, module in cls.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self):
        self.actions[:] = []
        FakeAction.registry = self.actions
        armatures = {
            name: types.SimpleNamespace(
                name=name, type="ARMATURE",
                get=lambda key, default=None, name=name: (
                    name.removesuffix("Rig") if key == "FModel_Skeleton" else default
                ),
            )
            for name in ("HeroRig", "EnemyRig")
        }
        class Objects(list):
            def get(self, name):
                return armatures.get(name)

        self.bpy.data.objects = Objects(armatures.values())
        self.bpy.data.libraries.load = lambda path, link: FakeLibraryLoad(
            self.actions, [FakeAction(os.path.basename(path))], path
        )

    def test_ueanim_filters_by_exact_skeleton_and_subset(self):
        with tempfile.TemporaryDirectory() as directory:
            hero = os.path.join(directory, "Hero.ueanim")
            enemy = os.path.join(directory, "Enemy.ueanim")
            open(hero, "w").close(); open(enemy, "w").close()
            imported = []
            class Options:
                def __init__(self, **kwargs): pass
            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    imported.append(path)
                    TaskScopedImportTests.actions.append(FakeAction(os.path.basename(path)))
            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            manifest = {"skeletons": {
                "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": hero}}}},
                "Enemy": {"Moves": {"__animations__": {"Idle": {"source_file": enemy}}}},
            }}
            context = FakeContext(manifest, {"source_skeleton": "Hero", "source_subset": [hero],
                                             "import_resume_enabled": False})

            self.assertTrue(self.stages.AssetImportStage()._import_ueanim_direct(context))
            self.assertEqual(imported, [hero])

    def test_ueanim_rejects_pose_in_legacy_action_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Expressions.uepose")
            open(path, "wb").close()
            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (object, None, object, None)
            sys.modules[api.__name__] = api
            context = FakeContext({"skeletons": {
                "Hero": {"Moves": {"__animations__": {
                    "Expressions": {"source_file": path},
                }}},
            }}, {"source_skeleton": "Hero"})

            self.assertFalse(self.stages.AssetImportStage()._import_ueanim_direct(context))
            self.assertTrue(any("重新扫描" in message for _, message in context.logs))
            self.assertEqual(self.actions, [])

    def test_incremental_content_update_replaces_old_bundle(self):
        self._exercise_incremental_build(False)

    def test_incremental_publication_failure_preserves_old_bundle(self):
        self._exercise_incremental_build(True)

    def _exercise_incremental_build(self, fail_publish):
        from core import library_files
        stage = self.stages.LibraryBuildingStage()
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            output = os.path.join(directory, "library")
            os.mkdir(output)
            context = FakeContext({}, {
                "source_dir": directory, "output_dir": output,
                "library_name": "Lib", "source_skeleton": "Hero",
                "target_armature": "HeroRig", "generate_thumbnails": False,
            })
            assets = types.ModuleType("addon.Assets")
            utils = types.ModuleType("addon.Assets.Utils")
            utils.utils_asset = types.SimpleNamespace()

            def prepare(value):
                with open(source, "wb") as handle:
                    handle.write(value)
                action = FakeAction("Idle")
                action.update(Pipeline_Source=source, Pipeline_SourceRef="Hero/Base/Idle",
                              Pipeline_Owned=True, value=value.decode())
                action.asset_data = types.SimpleNamespace(catalog_id="")
                self.actions.append(action)
                version = self.stages.make_library_source_version(
                    source, context.config, stage._task_identity(context),
                )
                context.imported_assets = {"imported_actions": [{
                    "action_name": "Idle", "source_file": source,
                    "source_manifest_ref": "Hero/Base/Idle", "source_version": version,
                }]}

            def write_bundle(filepath, datablocks, **kwargs):
                with open(filepath, "w", encoding="utf-8") as handle:
                    json.dump([dict(action) for action in datablocks], handle)

            with mock.patch.dict(sys.modules, {"addon.Assets": assets, "addon.Assets.Utils": utils}), \
                    mock.patch.object(self.bpy.data.libraries, "write", side_effect=write_bundle), \
                    mock.patch.object(stage, "_reload_actions_from_blends", return_value={}), \
                    mock.patch.object(stage, "_validate_published_bundle_actions"), \
                    mock.patch.object(self.stages, "get_or_create_catalog_uuid", return_value="catalog"):
                prepare(b"old")
                self.assertTrue(stage.execute(context), context.logs)
                index = library_files.library_index_path(output, "Lib")
                with open(index, "rb") as handle:
                    original_index = handle.read()
                original_bundle = os.path.join(output, context.library_manifest["blend_files"][0])
                with open(original_bundle, "rb") as handle:
                    original_bytes = handle.read()
                prepare(b"new")
                publication_globals = self.stages.persist_transaction_replacement.__globals__
                original_write = publication_globals['_write_json_temp']
                injected_failures = []

                def fail_sidecar(path, payload):
                    if path.endswith("_manifest.json") and not path.endswith("__library_manifest.json"):
                        injected_failures.append(path)
                        raise OSError("injected sidecar failure")
                    return original_write(path, payload)

                with mock.patch.dict(publication_globals, {
                    '_write_json_temp': fail_sidecar if fail_publish else original_write,
                }):
                    result = stage.execute(context)
                self.assertEqual(result, not fail_publish, context.logs)
                if fail_publish:
                    self.assertTrue(injected_failures)
                    with open(index, "rb") as handle:
                        self.assertEqual(handle.read(), original_index)
                    with open(original_bundle, "rb") as handle:
                        self.assertEqual(handle.read(), original_bytes)
                    self.assertEqual(context.library_manifest, json.loads(original_index))
                    self.assertNotIn("成功构建资产库", context.logs[-1][1])
                else:
                    published = library_files.load_complete_v2_manifest(index, output)
                    self.assertEqual(len(published.assets), 1)
                    self.assertEqual(len(published.bundles), 1)
                    with open(os.path.join(output, published.blend_files[0]), encoding="utf-8") as handle:
                        self.assertEqual(json.load(handle)[0]["value"], "new")
                    self.assertEqual(os.path.join(output, published.blend_files[0]), original_bundle)

    def test_update_layout_preserves_source_bundle_when_order_and_chunk_size_change(self):
        stage = self.stages.LibraryBuildingStage()
        entries = [({}, FakeAction(name), f"Hero::{name}") for name in ("B", "C", "A")]
        manifest = types.SimpleNamespace(
            bundles=[types.SimpleNamespace(
                blend_file=f"Lib_Hero{suffix}.blend", skeletons=["Hero"],
                processed_sources=[f"Hero::{name}"],
            ) for name, suffix in (("A", ""), ("B", "_Vol_2"))],
            assets=[types.SimpleNamespace(
                blend_file=f"Lib_Hero{suffix}.blend", source_key=f"Hero::{name}", action_name=name,
            ) for name, suffix in (("A", ""), ("B", "_Vol_2"))],
        )
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(stage, "_occupied_bundle_slots", return_value={1, 2, 3}):
            chunks = stage._stable_update_chunks(manifest, "Hero", entries, directory, "Lib_Hero", 500)
            self.assertEqual([(slot, [key for _, _, key in chunk]) for slot, chunk in chunks],
                             [(1, ["Hero::A"]), (2, ["Hero::B"]), (4, ["Hero::C"])])
            for defect in ("missing_mapping", "renamed_action", "changed_target"):
                with self.subTest(defect=defect):
                    broken = copy.deepcopy(manifest)
                    if defect == "missing_mapping":
                        broken.assets[0].source_key = ""
                    elif defect == "renamed_action":
                        broken.assets[0].action_name = "Different"
                    else:
                        broken.bundles[0].source_versions = {"Hero::A": {"target": "old-target"}}
                        entries[2][0]["source_version"] = {"target": "new-target"}
                    with self.assertRaises(ValueError):
                        stage._stable_update_chunks(broken, "Hero", entries, directory, "Lib_Hero", 500)

    def test_library_reload_skips_bundles_not_requested_by_current_task(self):
        stage = self.stages.LibraryBuildingStage()
        manifest = types.SimpleNamespace(bundles=[types.SimpleNamespace(blend_file="Old.blend")])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.bpy.data.libraries, "load") as load:
            self.assertEqual(stage._reload_actions_from_blends(directory, manifest, []), {})
            load.assert_not_called()

    def test_ueanim_nonempty_missing_target_armature_fails_before_importer_construction(self):
        class Importer:
            def __init__(self, options):
                raise AssertionError("invalid target must fail before importer construction")
        api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
        api.get_ueformat_api = lambda: (Importer, None, object, None)
        sys.modules[api.__name__] = api
        context = FakeContext({}, {"target_armature": "MissingRig"})

        self.assertFalse(self.stages.AssetImportStage()._import_ueanim_direct(context))
        self.assertTrue(any("MissingRig" in message for level, message in context.logs if level == "error"))

    def test_ueanim_non_armature_target_fails_before_importer_construction(self):
        self.bpy.data.objects = types.SimpleNamespace(
            get=lambda name: types.SimpleNamespace(name=name, type="MESH")
        )
        class Importer:
            def __init__(self, options):
                raise AssertionError("invalid target must fail before importer construction")
        api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
        api.get_ueformat_api = lambda: (Importer, None, object, None)
        sys.modules[api.__name__] = api
        context = FakeContext({}, {"target_armature": "HeroMesh"})

        self.assertFalse(self.stages.AssetImportStage()._import_ueanim_direct(context))
        self.assertTrue(any("HeroMesh" in message for level, message in context.logs if level == "error"))

    def test_ueanim_requires_exactly_one_new_action(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            open(source, "w").close()
            class Options:
                def __init__(self, **kwargs): pass
            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    TaskScopedImportTests.actions.extend([FakeAction("Idle"), FakeAction("Extra")])
            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            context = FakeContext({"skeletons": {"Hero": {"": {"__animations__": {
                "Idle": {"source_file": source},
            }}}}}, {"source_skeleton": "Hero", "import_resume_enabled": False})

            self.assertFalse(self.stages.AssetImportStage()._import_ueanim_direct(context))
            self.assertEqual(context.imported_assets["imported_actions"], [])
            self.assertIn("2", context.imported_assets["failed_imports"][0]["error"])
            self.assertEqual(self.actions, [])



    def test_ueanim_name_compatibility_skips_strict_identity_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            open(source, "w").close()
            constructed = []

            class Options:
                def __init__(self, **kwargs):
                    constructed.append(kwargs)

            class Importer:
                def __init__(self, options):
                    pass

                def import_file(self, path):
                    TaskScopedImportTests.actions.append(FakeAction("Idle"))

            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            target_identity = {
                "version": 1,
                "skeleton_path": "/Game/Characters/SK_Hero.SK_Hero",
                "has_skeleton_guid": False,
                "skeleton_guid": None,
                "bone_layout_hash": "b" * 64,
            }
            target = types.SimpleNamespace(
                name="HeroRig",
                type="ARMATURE",
                get=lambda key, default=None: {
                    "FModel_Skeleton": "Hero",
                    "fmodel_skeleton_identity": target_identity,
                }.get(key, default),
            )
            self.bpy.data.objects = types.SimpleNamespace(get=lambda name: target if name == "HeroRig" else None)
            context = FakeContext({"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {
                    "source_file": source,
                    "metadata": {"fmodel_skeleton_identity": {
                        "version": 1,
                        "skeleton_path": "/Game/Characters/SK_Hero.SK_Hero",
                        "has_skeleton_guid": False,
                        "skeleton_guid": None,
                        "bone_layout_hash": "a" * 64,
                    }},
                },
            }}}}}, {
                "source_skeleton": "Hero", "source_subset": [source],
                "target_armature": "HeroRig", "import_resume_enabled": False,
                "skeleton_compatibility_mode": "name_retarget",
            })

            self.assertTrue(self.stages.AssetImportStage()._import_ueanim_direct(context))

            self.assertEqual(len(constructed), 1)
            self.assertNotIn("strict_fmodel_skeleton", constructed[0])
            self.assertEqual(context.imported_assets["failed_imports"], [])
            self.assertEqual(self.actions[0]["Pipeline_SkeletonCompatibility"], "ueformat")



    def test_ueanim_import_exception_removes_partial_action_and_refreshes_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = os.path.join(directory, "Broken.ueanim")
            valid = os.path.join(directory, "Valid.ueanim")
            open(broken, "w").close(); open(valid, "w").close()

            class Options:
                def __init__(self, **kwargs): pass

            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    if path == broken:
                        TaskScopedImportTests.actions.append(FakeAction("Partial"))
                        raise RuntimeError("broken after action creation")
                    TaskScopedImportTests.actions.append(FakeAction("Valid"))

            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            context = FakeContext({"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Broken": {"source_file": broken}, "Valid": {"source_file": valid},
            }}}}}, {
                "source_skeleton": "Hero", "source_subset": [broken, valid],
                "import_resume_enabled": False,
            })

            self.assertTrue(self.stages.AssetImportStage()._import_ueanim_direct(context))
            self.assertEqual([action.name for action in self.actions], ["Valid"])
            self.assertEqual(
                [item["action_name"] for item in context.imported_assets["imported_actions"]],
                ["Valid"],
            )
            self.assertEqual(len(context.imported_assets["failed_imports"]), 1)

    def test_ueanim_root_folder_stamps_canonical_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            open(source, "w").close()
            class Options:
                def __init__(self, **kwargs): pass
            class Importer:
                def __init__(self, options): pass
                def import_file(self, path): TaskScopedImportTests.actions.append(FakeAction("Idle"))
            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            context = FakeContext({"skeletons": {"Hero": {"": {"__animations__": {
                "Idle": {"source_file": source},
            }}}}}, {"source_skeleton": "Hero", "import_resume_enabled": False})

            self.assertTrue(self.stages.AssetImportStage()._import_ueanim_direct(context))
            self.assertEqual(self.actions[0]["Pipeline_SourceRef"], "Hero/Uncategorized/Idle")

    def test_ueanim_cycle_cap_keeps_partial_import_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "Idle.ueanim")
            second = os.path.join(directory, "Run.ueanim")
            open(first, "w").close(); open(second, "w").close()
            class Options:
                def __init__(self, **kwargs): pass
            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    TaskScopedImportTests.actions.append(FakeAction(os.path.basename(path)))
            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {"source_file": first}, "Run": {"source_file": second},
            }}}}}
            context = FakeContext(manifest, {
                "source_skeleton": "Hero", "source_subset": [first, second], "target_armature": "HeroRig",
                "import_batch_size": 1, "max_import_cycles": 1,
                "import_checkpoint": {"task_identity": {"source_skeleton": "Enemy", "source_subset": [], "target_armature": "EnemyRig"},
                                      "cursor": 1, "imported_actions": [{"action_name": "stale"}]},
            })

            self.assertFalse(self.stages.AssetImportStage()._import_ueanim_direct(context))
            checkpoint = context.get_config("import_checkpoint")
            self.assertEqual(checkpoint["cursor"], 1)
            self.assertEqual(checkpoint["task_identity"]["source_skeleton"], "Hero")
            self.assertEqual(checkpoint["task_identity"]["target_armature"], "HeroRig")
            self.assertEqual(checkpoint["task_identity"]["source_subset"], sorted([os.path.normcase(os.path.abspath(first)), os.path.normcase(os.path.abspath(second))]))

            # The saved count is diagnostic state from the earlier invocation;
            # it must not consume the next invocation's cycle budget.
            self.assertTrue(self.stages.AssetImportStage()._import_ueanim_direct(context))
            self.assertEqual(context.get_config("import_checkpoint")["state"], "complete")

    def test_ueanim_failure_abort_flushes_and_persists_prior_batch_success(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "Idle.ueanim")
            second = os.path.join(directory, "Broken.ueanim")
            open(first, "w").close(); open(second, "w").close()

            class Options:
                def __init__(self, **kwargs): pass

            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    if path == second:
                        raise RuntimeError("broken")
                    TaskScopedImportTests.actions.append(FakeAction("Idle"))

            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            staged_action = FakeAction("Idle")
            original_write = self.bpy.data.libraries.write
            def write_library(**kwargs):
                source_action = next(action for action in kwargs["datablocks"] if action.name == "Idle")
                staged_action.update(source_action)
            class ValidStagedLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    return types.SimpleNamespace(actions=["Idle"]), self.data_to
                def __exit__(self, *args):
                    self.data_to.actions = [staged_action]
                    self.target_actions.extend(self.data_to.actions)
            self.bpy.data.libraries.load = lambda path, link: ValidStagedLoad(
                self.actions, [staged_action], path
            )
            self.bpy.data.libraries.write = write_library
            context = FakeContext({"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {"source_file": first}, "Broken": {"source_file": second},
            }}}}}, {
                "source_skeleton": "Hero", "source_subset": [first, second],
                "output_dir": directory, "import_batch_size": 2,
                "max_consecutive_failures": 1,
            })

            try:
                self.assertFalse(self.stages.AssetImportStage()._import_ueanim_direct(context))
                checkpoint = context.get_config("import_checkpoint")
                self.assertEqual(checkpoint["cursor"], 2)
                self.assertEqual(len(checkpoint["imported_actions"]), 1)
                self.assertEqual(checkpoint["imported_actions"][0]["import_status"], "staged")
                self.assertGreaterEqual(context.blender_saves, 1)
            finally:
                self.bpy.data.libraries.write = original_write

    def test_library_build_rejects_missing_or_invalid_source_manifest_refs(self):
        with tempfile.TemporaryDirectory() as directory:
            for source_manifest_ref in ("", "../Hero/Idle"):
                with self.subTest(source_manifest_ref=source_manifest_ref):
                    assets = types.ModuleType("addon.Assets"); assets.__path__ = []
                    utils = types.ModuleType("addon.Assets.Utils"); utils.__path__ = []
                    utils_asset = types.ModuleType("addon.Assets.Utils.utils_asset")
                    sys.modules.update({
                        "addon.Assets": assets,
                        "addon.Assets.Utils": utils,
                        "addon.Assets.Utils.utils_asset": utils_asset,
                    })
                    context = FakeContext({}, {"output_dir": directory, "library_name": "Test"})
                    context.imported_assets = {"imported_actions": [{
                        "action_name": "Idle", "source_manifest_ref": source_manifest_ref,
                    }]}

                    self.assertFalse(self.stages.LibraryBuildingStage().execute(context))
                    self.assertTrue(any(level == "error" for level, _ in context.logs))

    def test_case_variant_labels_still_require_resolvable_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = types.ModuleType("addon.Assets"); assets.__path__ = []
            utils = types.ModuleType("addon.Assets.Utils"); utils.__path__ = []
            utils_asset = types.ModuleType("addon.Assets.Utils.utils_asset")
            sys.modules.update({
                "addon.Assets": assets,
                "addon.Assets.Utils": utils,
                "addon.Assets.Utils.utils_asset": utils_asset,
            })
            context = FakeContext({}, {
                "output_dir": directory, "library_name": "Test", "force_rebuild": True,
            })
            context.imported_assets = {"imported_actions": [
                {"action_name": "Idle", "source_manifest_ref": "Hero/Moves/Idle"},
                {"action_name": "Run", "source_manifest_ref": "hero/Moves/Run"},
            ]}
            writes = []
            original_write = self.bpy.data.libraries.write
            def write_library(**kwargs):
                writes.append(kwargs)
                with open(kwargs["filepath"], "wb") as handle:
                    handle.write(b"blend")
            self.bpy.data.libraries.write = write_library
            try:
                self.assertFalse(self.stages.LibraryBuildingStage().execute(context))
            finally:
                self.bpy.data.libraries.write = original_write

            self.assertEqual(writes, [])
            self.assertTrue(context.logs)
            self.assertFalse(any("output collision" in message for _, message in context.logs))

    def test_force_rebuild_rejects_malformed_imported_action_before_bundle_write(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = types.ModuleType("addon.Assets"); assets.__path__ = []
            utils = types.ModuleType("addon.Assets.Utils"); utils.__path__ = []
            utils_asset = types.ModuleType("addon.Assets.Utils.utils_asset")
            sys.modules.update({
                "addon.Assets": assets,
                "addon.Assets.Utils": utils,
                "addon.Assets.Utils.utils_asset": utils_asset,
            })
            context = FakeContext({}, {
                "output_dir": directory, "library_name": "Test", "force_rebuild": True,
            })
            context.imported_assets = {"imported_actions": [
                {"action_name": "Idle", "source_manifest_ref": "Hero/Moves/Idle"},
                {"action_name": "Idle", "source_manifest_ref": "Hero/Moves/Idle"},
            ]}
            writes = []
            original_write = self.bpy.data.libraries.write
            self.bpy.data.libraries.write = lambda **kwargs: writes.append(kwargs)
            try:
                self.assertFalse(self.stages.LibraryBuildingStage().execute(context))
            finally:
                self.bpy.data.libraries.write = original_write

            self.assertEqual(writes, [])
            self.assertTrue(any("duplicate" in message for _, message in context.logs))

    def test_force_rebuild_resolves_complete_imported_set_before_bundle_write(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = types.ModuleType("addon.Assets"); assets.__path__ = []
            utils = types.ModuleType("addon.Assets.Utils"); utils.__path__ = []
            utils_asset = types.ModuleType("addon.Assets.Utils.utils_asset")
            sys.modules.update({
                "addon.Assets": assets,
                "addon.Assets.Utils": utils,
                "addon.Assets.Utils.utils_asset": utils_asset,
            })
            idle = FakeAction("Idle")
            idle["Pipeline_Source"] = "Idle.ueanim"
            idle["Pipeline_SourceRef"] = "Hero/Moves/Idle"
            self.actions.append(idle)
            context = FakeContext({}, {
                "output_dir": directory, "library_name": "Test", "force_rebuild": True,
                "chunk_size": 1,
            })
            context.imported_assets = {"imported_actions": [
                {"action_name": "Idle", "source_file": "Idle.ueanim",
                 "source_manifest_ref": "Hero/Moves/Idle"},
                {"action_name": "Run", "source_file": "Run.ueanim",
                 "source_manifest_ref": "Mage/Moves/Run"},
            ]}
            writes = []
            original_write = self.bpy.data.libraries.write
            self.bpy.data.libraries.write = lambda **kwargs: writes.append(kwargs)
            try:
                self.assertFalse(self.stages.LibraryBuildingStage().execute(context))
            finally:
                self.bpy.data.libraries.write = original_write

            self.assertEqual(writes, [])
            self.assertTrue(any("Run" in message for _, message in context.logs))

    def test_ueanim_mixed_failure_remains_pending_and_retries_failed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "Idle.ueanim")
            second = os.path.join(directory, "Run.ueanim")
            open(first, "w").close(); open(second, "w").close()
            attempts = []

            class Options:
                def __init__(self, **kwargs): pass

            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    attempts.append(path)
                    if path == second and attempts.count(path) == 1:
                        raise RuntimeError("temporary failure")
                    TaskScopedImportTests.actions.append(FakeAction(os.path.basename(path)))

            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {"source_file": first}, "Run": {"source_file": second},
            }}}}}
            config = {"source_skeleton": "Hero", "source_subset": [first, second],
                      "import_batch_size": 2}
            context = FakeContext(manifest, config)

            self.assertTrue(self.stages.AssetImportStage()._import_ueanim_direct(context))
            checkpoint = context.get_config("import_checkpoint")
            self.assertEqual(
                [item["normalized_path"] for item in checkpoint["pending_sources"]],
                [os.path.normcase(os.path.abspath(second))],
            )
            self.assertEqual(attempts, [first, second])

            self.assertTrue(self.stages.AssetImportStage()._import_ueanim_direct(context))
            self.assertEqual(attempts, [first, second, second])
            self.assertEqual(context.get_config("import_checkpoint")["state"], "complete")

    def test_ueanim_persists_blender_checkpoint_after_each_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "Idle.ueanim")
            second = os.path.join(directory, "Run.ueanim")
            open(first, "w").close(); open(second, "w").close()

            class Options:
                def __init__(self, **kwargs): pass
            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    TaskScopedImportTests.actions.append(FakeAction(os.path.basename(path)))

            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {"source_file": first}, "Run": {"source_file": second},
            }}}}}
            context = FakeContext(manifest, {"source_skeleton": "Hero",
                "source_subset": [first, second], "import_batch_size": 1})

            self.assertTrue(self.stages.AssetImportStage()._import_ueanim_direct(context))
            self.assertEqual(context.blender_saves, 2)

    def test_ueanim_checkpoint_replaces_success_records_with_one_staged_record(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            second_source = os.path.join(directory, "Run.ueanim")
            open(source, "w").close(); open(second_source, "w").close()
            class Options:
                def __init__(self, **kwargs): pass
            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    TaskScopedImportTests.actions.append(FakeAction("Idle"))
            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            staged_action = FakeAction("Idle")
            original_write = self.bpy.data.libraries.write
            def write_library(**kwargs):
                source_action = next(action for action in kwargs["datablocks"] if action.name == "Idle")
                staged_action.update(source_action)
            class ValidStagedLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    return types.SimpleNamespace(actions=["Idle"]), self.data_to
                def __exit__(self, *args):
                    self.data_to.actions = [staged_action]
                    self.target_actions.extend(self.data_to.actions)
            self.bpy.data.libraries.load = lambda path, link: ValidStagedLoad(
                self.actions, [staged_action], path
            )
            self.bpy.data.libraries.write = write_library
            context = FakeContext({"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {"source_file": source},
                "Run": {"source_file": second_source},
            }}}}}, {
                "source_skeleton": "Hero", "source_subset": [source, second_source],
                "import_batch_size": 1, "max_import_cycles": 1,
                "output_dir": directory,
            })

            try:
                self.assertFalse(self.stages.AssetImportStage()._import_ueanim_direct(context))
                staged = context.get_config("import_checkpoint")["imported_actions"]
                self.assertEqual(len(staged), 1)
                self.assertEqual(staged[0]["action_name"], "Idle")
                self.assertEqual(staged[0]["import_status"], "staged")
                self.assertTrue(staged[0]["staged_blend"])
                durable = next(snapshot for snapshot in context.saved_checkpoints if snapshot)
                self.assertEqual(durable["cursor"], 1)
                self.assertEqual(durable["imported_actions"][0]["import_status"], "staged")
            finally:
                self.bpy.data.libraries.write = original_write

    def test_ueanim_staging_writes_and_validates_durable_action_provenance(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "AssetImportStage")
        direct_import = next(node for node in stage.body
                             if isinstance(node, ast.FunctionDef) and node.name == "_import_ueanim_direct")
        flush = next(node for node in direct_import.body
                     if isinstance(node, ast.FunctionDef) and node.name == "_flush_actions_to_temp_blend")
        write = next(
            node for node in ast.walk(flush)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write"
        )
        property_writes = {
            node.targets[0].slice.value: node.lineno
            for node in ast.walk(flush)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].slice, ast.Constant)
            and node.targets[0].slice.value in {
                "Pipeline_RequestedActionName", "Pipeline_StagedBlend",
                "Pipeline_Source", "Pipeline_SourceRef",
            }
        }

        self.assertEqual(
            set(property_writes),
            {"Pipeline_RequestedActionName", "Pipeline_StagedBlend",
             "Pipeline_Source", "Pipeline_SourceRef"},
        )
        self.assertTrue(all(line < write.lineno for line in property_writes.values()))
        post_write_property_assignments = [
            node for node in ast.walk(flush)
            if isinstance(node, ast.Assign)
            and node.lineno > write.lineno
            and any(isinstance(target, ast.Subscript) for target in node.targets)
        ]
        self.assertEqual(post_write_property_assignments, [])

    def test_ueanim_staging_validation_failure_retains_original_action(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            open(source, "w").close()

            class Options:
                def __init__(self, **kwargs): pass

            original = FakeAction("Idle")

            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    TaskScopedImportTests.actions.append(original)

            class MissingStagedActionLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    return types.SimpleNamespace(actions=[]), self.data_to

                def __exit__(self, *args):
                    pass

            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            original_write = self.bpy.data.libraries.write
            self.bpy.data.libraries.write = lambda **kwargs: open(kwargs["filepath"], "wb").close()
            self.bpy.data.libraries.load = lambda path, link: MissingStagedActionLoad(
                self.actions, [], path
            )
            context = FakeContext({"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {"source_file": source},
            }}}}}, {
                "source_skeleton": "Hero", "source_subset": [source],
                "import_batch_size": 1, "output_dir": directory,
            })

            try:
                self.assertFalse(self.stages.AssetImportStage()._import_ueanim_direct(context))
                self.assertIn(original, self.actions)
                imported = context.imported_assets["imported_actions"]
                self.assertEqual(imported[0]["import_status"], "success")
                self.assertFalse(imported[0].get("staged_blend"))
            finally:
                self.bpy.data.libraries.write = original_write

    def test_staged_post_load_source_mismatch_removes_loaded_action(self):
        with tempfile.TemporaryDirectory() as directory:
            staged_blend = os.path.join(directory, "staged.blend")
            open(staged_blend, "w").close()
            loaded = FakeAction("Idle")
            loaded["Pipeline_Source"] = os.path.join(directory, "Other.ueanim")

            class StagedLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    return types.SimpleNamespace(actions=["Idle"]), self.data_to

                def __exit__(self, *args):
                    self.data_to.actions = [loaded]
                    self.target_actions.append(loaded)

            self.bpy.data.libraries.load = lambda path, link: StagedLoad(
                self.actions, [loaded], path
            )
            imported = {
                "action_name": "Idle",
                "source_file": os.path.join(directory, "Idle.ueanim"),
                "source_manifest_ref": "Hero/Moves/Idle",
                "staged_blend": staged_blend,
            }

            selection_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core", "action_selection.py")
            spec = importlib.util.spec_from_file_location("test_action_selection", selection_path)
            selection = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(selection)
            action, error = selection.resolve_imported_action(self.bpy, imported)

            self.assertIsNone(action)
            self.assertIn("来源不匹配", error)
            self.assertNotIn(loaded, self.actions)

    def test_ueanim_complete_cursor_checkpoint_resumes_for_library_build(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            open(source, "w").close()
            action = FakeAction("Idle")
            action["Pipeline_Source"] = source
            action["Pipeline_SourceRef"] = "Hero/Moves/Idle"
            self.actions.append(action)
            class Options:
                def __init__(self, **kwargs): pass
            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    raise AssertionError("completed checkpoint must not reimport")
            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            context = FakeContext({"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {"source_file": source},
            }}}}}, {"source_skeleton": "Hero", "source_subset": [source], "target_armature": "HeroRig"})
            stage = self.stages.AssetImportStage()
            identity = stage._task_identity(context)
            source_identity = self.stages.make_manifest_source(0, "Hero", "Moves", "Idle", source)
            context.config["import_checkpoint"] = {
                "state": "complete", "task_identity": identity,
                "batch_identity": self.stages.make_batch_identity(
                    [source_identity], self.stages.import_settings_fingerprint(context.config),
                    identity["target_identity"],
                ),
                "expected_task_ids": [source_identity["task_id"]],
                "cursor": 1, "cycle_count": 1, "pending_sources": [],
                "imported_actions": [{
                    "action_name": "Idle", "source_manifest_ref": "Hero/Moves/Idle",
                    "source_file": source, "import_status": "success",
                    "task_id": source_identity["task_id"],
                }], "failed_imports": [],
            }

            self.assertTrue(stage._import_ueanim_direct(context))
            self.assertEqual(len(context.imported_assets["imported_actions"]), 1)
            self.assertEqual(context.get_config("import_checkpoint")["state"], "complete")

    def test_ueanim_complete_cursor_resolves_mixed_success_and_staged_records(self):
        with tempfile.TemporaryDirectory() as directory:
            live_source = os.path.join(directory, "Idle.ueanim")
            staged_source = os.path.join(directory, "Run.ueanim")
            for path in (live_source, staged_source):
                open(path, "w").close()
            live = FakeAction("Idle")
            live["Pipeline_Source"] = live_source
            live["Pipeline_SourceRef"] = "Hero/Moves/Idle"
            self.actions.append(live)
            staged = FakeAction("Run")
            staged["Pipeline_Source"] = staged_source
            staged["Pipeline_SourceRef"] = "Hero/Moves/Run"
            class StagedLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    return types.SimpleNamespace(actions=["Run"]), self.data_to
                def __exit__(self, *args):
                    self.data_to.actions = [staged if name == "Run" else name for name in self.data_to.actions]
                    self.target_actions.extend(self.data_to.actions)
            self.bpy.data.libraries.load = lambda path, link: StagedLoad(self.actions, [staged], path)
            class Options:
                def __init__(self, **kwargs): pass
            class Importer:
                def __init__(self, options): pass
                def import_file(self, path): raise AssertionError("must not reimport")
            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            subset = [live_source, staged_source]
            context = FakeContext({"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {"source_file": live_source}, "Run": {"source_file": staged_source},
            }}}}}, {
                "source_skeleton": "Hero", "source_subset": subset,
                "target_armature": "HeroRig", "output_dir": directory,
            })
            stage = self.stages.AssetImportStage()
            identity = stage._task_identity(context)
            task_dir = stage._staging_task_dir(context)
            os.makedirs(task_dir)
            staged_blend = self.stages.staging_chunk_path(task_dir, "Hero", 1)
            open(staged_blend, "w").close()
            source_identities = [
                self.stages.make_manifest_source(0, "Hero", "Moves", "Idle", live_source),
                self.stages.make_manifest_source(1, "Hero", "Moves", "Run", staged_source),
            ]
            context.config["import_checkpoint"] = {
                "state": "complete", "task_identity": identity,
                "batch_identity": self.stages.make_batch_identity(
                    source_identities, self.stages.import_settings_fingerprint(context.config),
                    identity["target_identity"],
                ),
                "expected_task_ids": [item["task_id"] for item in source_identities],
                "cursor": 2, "cycle_count": 1, "pending_sources": [],
                "imported_actions": [
                    {"action_name": "Idle", "source_manifest_ref": "Hero/Moves/Idle",
                     "source_file": live_source, "import_status": "success",
                     "task_id": source_identities[0]["task_id"]},
                    {"action_name": "Run", "source_manifest_ref": "Hero/Moves/Run",
                     "source_file": staged_source, "staged_blend": staged_blend,
                     "import_status": "staged", "task_id": source_identities[1]["task_id"]},
                ], "failed_imports": [],
            }

            self.assertTrue(stage._import_ueanim_direct(context))
            self.assertEqual(len(context.imported_assets["imported_actions"]), 2)
            self.assertEqual(context.get_config("import_checkpoint")["state"], "complete")

    def test_ueanim_post_import_retry_reuses_task_staged_asset_without_reimport(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            open(source, "w").close()
            staged_action = FakeAction("Idle")
            staged_action["Pipeline_Source"] = source
            staged_action["Pipeline_SourceRef"] = "Hero/Moves/Idle"
            load_count = []

            class StagedLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    return types.SimpleNamespace(actions=["Idle"]), self.data_to
                def __exit__(self, *args):
                    self.data_to.actions = [staged_action]
                    self.target_actions.extend(self.data_to.actions)

            self.bpy.data.libraries.load = lambda path, link: (
                load_count.append(path) or StagedLoad(self.actions, [staged_action], path)
            )
            class Options:
                def __init__(self, **kwargs): pass
            class Importer:
                def __init__(self, options): pass
                def import_file(self, path):
                    raise AssertionError("post-import retry must not reimport UEAnim")
            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            context = FakeContext({"skeletons": {"Hero": {"Moves": {"__animations__": {
                "Idle": {"source_file": source},
            }}}}}, {
                "import_mode": "ueanim_direct", "source_skeleton": "Hero",
                "source_subset": [source], "target_armature": "HeroRig",
                "output_dir": directory,
            })
            stage = self.stages.AssetImportStage()
            task_dir = stage._staging_task_dir(context)
            os.makedirs(task_dir)
            staged_blend = self.stages.staging_chunk_path(task_dir, "Hero", 1)
            open(staged_blend, "w").close()
            context.imported_assets = {
                "import_mode": "ueanim_direct",
                "task_identity": stage._task_identity(context),
                "imported_actions": [{
                    "action_name": "Idle", "source_file": source,
                    "source_manifest_ref": "Hero/Moves/Idle",
                    "staged_blend": staged_blend, "import_status": "staged",
                }],
                "failed_imports": [],
            }
            identity = context.imported_assets["task_identity"]
            expected_task_ids = [self.stages.make_manifest_source(
                0, "Hero", "Moves", "Idle", source,
            )["task_id"]]
            context.imported_assets["imported_actions"][0]["task_id"] = expected_task_ids[0]
            context.config["import_checkpoint"] = {
                "state": "complete", "expected_task_ids": expected_task_ids,
            }

            self.assertTrue(stage.execute(context))
            self.assertEqual(load_count, [staged_blend])
            self.assertEqual([action.name for action in self.actions], ["Idle"])

    def test_staging_cleanup_only_removes_current_task_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            current_task = os.path.join(directory, "current")
            other_task = os.path.join(directory, "other")
            os.makedirs(current_task); os.makedirs(other_task)
            committed = os.path.join(current_task, "Hero_chunk_0001.blend")
            unfinished = os.path.join(current_task, "Hero_chunk_0002.blend")
            foreign = os.path.join(other_task, "Hero_chunk_0001.blend")
            for path in (committed, unfinished, foreign):
                open(path, "w").close()

            removed = self.stages.cleanup_committed_staged_blends(
                current_task, {committed, foreign}
            )

            self.assertEqual(removed, [os.path.abspath(committed)])
            self.assertFalse(os.path.exists(committed))
            self.assertTrue(os.path.exists(unfinished))
            self.assertTrue(os.path.exists(foreign))

    def test_staging_cleanup_never_removes_reports_or_regular_blend_files(self):
        with tempfile.TemporaryDirectory() as directory:
            report = os.path.join(directory, "import_failures.json")
            regular_blend = os.path.join(directory, "user_asset.blend")
            for path in (report, regular_blend):
                open(path, "w").close()

            removed = self.stages.cleanup_committed_staged_blends(
                directory, {report, regular_blend}
            )

            self.assertEqual(removed, [])
            self.assertTrue(os.path.exists(report))
            self.assertTrue(os.path.exists(regular_blend))

    def test_staging_cleanup_never_removes_nested_chunk_named_blends(self):
        with tempfile.TemporaryDirectory() as directory:
            nested_dir = os.path.join(directory, "reports")
            os.makedirs(nested_dir)
            nested_chunk = os.path.join(nested_dir, "Hero_chunk_0001.blend")
            open(nested_chunk, "w").close()

            removed = self.stages.cleanup_committed_staged_blends(
                directory, {nested_chunk}
            )

            self.assertEqual(removed, [])
            self.assertTrue(os.path.exists(nested_chunk))

    def test_finalization_retries_only_queued_staging_cleanup_after_success(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "FinalizationStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        completion = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Attribute) and target.attr == "status" for target in node.targets)
            and node.lineno > 1800
        )
        cleanup = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "retry_staged_cleanup"
        )

        self.assertGreater(cleanup.lineno, completion.lineno)
        self.assertIn("pending_staged_cleanup", ast.unparse(cleanup))
        self.assertFalse(any(
            isinstance(node, ast.Attribute) and node.attr == "_cleanup_temp_files"
            for node in ast.walk(execute)
        ))
        self.assertFalse(any(
            isinstance(node, ast.FunctionDef) and node.name == "_cleanup_temp_files"
            for node in stage.body
        ))

    def test_staging_cleanup_rejects_realpath_escape_from_task_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            current_task = os.path.join(directory, "current")
            outside = os.path.join(directory, "outside")
            os.makedirs(current_task); os.makedirs(outside)
            routed_path = os.path.join(current_task, "link", "escaped.blend")
            escaped_path = os.path.join(outside, "escaped.blend")
            open(escaped_path, "w").close()
            real_realpath = self.stages.os.path.realpath

            def routed_realpath(path):
                if os.path.abspath(path) == os.path.abspath(routed_path):
                    return os.path.abspath(escaped_path)
                return real_realpath(path)

            with mock.patch.object(self.stages.os.path, "realpath", side_effect=routed_realpath), \
                    mock.patch.object(self.stages.os, "remove") as remove:
                removed = self.stages.cleanup_committed_staged_blends(
                    current_task, {routed_path}
                )

            self.assertEqual(removed, [])
            remove.assert_not_called()
            self.assertTrue(os.path.exists(escaped_path))

    def test_finalization_fails_when_post_build_library_scan_is_cancelled(self):
        context = FakeContext({}, {})
        context.library_manifest = {
            "library_name": "Heroes",
            "library_path": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        }
        context.status = "completed"
        utils_module = types.ModuleType("addon.core.utils")
        utils_module.mount_dynamic_library = lambda *args: True
        previous_utils = sys.modules.get("addon.core.utils")
        sys.modules["addon.core.utils"] = utils_module
        previous_context = getattr(self.bpy, "context", None)
        previous_ops = self.bpy.ops
        self.bpy.context = types.SimpleNamespace(scene=None)
        self.bpy.ops = types.SimpleNamespace(
            fmodel=types.SimpleNamespace(scan_anims=lambda: {"CANCELLED"})
        )
        try:
            self.assertFalse(self.stages.FinalizationStage().run(context))
        finally:
            self.bpy.context = previous_context
            self.bpy.ops = previous_ops
            if previous_utils is None:
                sys.modules.pop("addon.core.utils", None)
            else:
                sys.modules["addon.core.utils"] = previous_utils

        self.assertEqual(context.status, "failed")
        self.assertEqual(context.stage_status["finalization"], "failed")
        self.assertTrue(any(level == "error" and "扫描" in message
                            for level, message in context.logs))

    def test_finalization_mount_failure_prevents_project_state_and_scan(self):
        context = FakeContext({}, {})
        context.library_manifest = {
            "library_name": "Heroes",
            "library_id": "library-id",
            "library_path": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        }
        project_libs = mock.MagicMock()
        utils_module = types.ModuleType("addon.core.utils")
        utils_module.mount_dynamic_library = mock.Mock(return_value=False)
        previous_utils = sys.modules.get("addon.core.utils")
        sys.modules["addon.core.utils"] = utils_module
        previous_context = getattr(self.bpy, "context", None)
        previous_ops = self.bpy.ops
        scan_anims = mock.Mock(return_value={"FINISHED"})
        self.bpy.context = types.SimpleNamespace(scene=types.SimpleNamespace(
            fmodel_assets=types.SimpleNamespace(project_libs=project_libs)
        ))
        self.bpy.ops = types.SimpleNamespace(fmodel=types.SimpleNamespace(scan_anims=scan_anims))
        try:
            self.assertFalse(self.stages.FinalizationStage().run(context))
        finally:
            self.bpy.context = previous_context
            self.bpy.ops = previous_ops
            if previous_utils is None:
                sys.modules.pop("addon.core.utils", None)
            else:
                sys.modules["addon.core.utils"] = previous_utils

        utils_module.mount_dynamic_library.assert_called_once_with(
            "Heroes", context.library_manifest["library_path"], "library-id"
        )
        project_libs.__iter__.assert_not_called()
        scan_anims.assert_not_called()
        self.assertEqual(context.status, "failed")

    def test_finalization_failure_preserves_queued_chunk_and_untracked_staging_files(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = self.stages.FinalizationStage()
            context = FakeContext({}, {
                "output_dir": directory,
                "source_skeleton": "Hero",
                "source_subset": [os.path.join(directory, "Idle.ueanim")],
                "target_armature": "HeroRig",
            })
            task_dir = stage._staging_task_dir(context)
            os.makedirs(task_dir)
            generated_chunk = os.path.join(task_dir, "Hero_chunk_0001.blend")
            report = os.path.join(task_dir, "import_failures.json")
            user_blend = os.path.join(task_dir, "user_asset.blend")
            for path in (generated_chunk, report, user_blend):
                open(path, "w").close()
            context.config.update({
                "pending_staged_cleanup": [generated_chunk],
                "pending_staged_cleanup_owners": {generated_chunk: task_dir},
            })
            context.library_manifest = {
                "library_name": "Heroes", "library_path": directory,
            }
            utils_module = types.ModuleType("addon.core.utils")
            utils_module.mount_dynamic_library = lambda *args: False
            previous_utils = sys.modules.get("addon.core.utils")
            sys.modules["addon.core.utils"] = utils_module
            try:
                self.assertFalse(stage.execute(context))
            finally:
                if previous_utils is None:
                    sys.modules.pop("addon.core.utils", None)
                else:
                    sys.modules["addon.core.utils"] = previous_utils

            self.assertTrue(os.path.exists(generated_chunk))
            self.assertTrue(os.path.exists(report))
            self.assertTrue(os.path.exists(user_blend))

    def test_finalization_mount_failure_preserves_direct_staging_files(self):
        with tempfile.TemporaryDirectory() as directory:
            staging_dir = os.path.join(directory, "_pipeline_tmp")
            os.makedirs(staging_dir)
            direct_file = os.path.join(staging_dir, "arbitrary-direct-file.txt")
            open(direct_file, "w").close()
            context = FakeContext({}, {"output_dir": directory})
            context.library_manifest = {
                "library_name": "Heroes", "library_path": directory,
            }
            utils_module = types.ModuleType("addon.core.utils")
            utils_module.mount_dynamic_library = lambda *args: False

            with mock.patch.dict(sys.modules, {"addon.core.utils": utils_module}):
                self.assertFalse(self.stages.FinalizationStage().execute(context))

            self.assertTrue(os.path.exists(direct_file))

    def test_finalization_scan_failure_preserves_direct_staging_files(self):
        with tempfile.TemporaryDirectory() as directory:
            staging_dir = os.path.join(directory, "_pipeline_tmp")
            os.makedirs(staging_dir)
            direct_file = os.path.join(staging_dir, "arbitrary-direct-file.txt")
            open(direct_file, "w").close()
            context = FakeContext({}, {"output_dir": directory})
            context.library_manifest = {
                "library_name": "Heroes", "library_path": directory,
            }
            utils_module = types.ModuleType("addon.core.utils")
            utils_module.mount_dynamic_library = lambda *args: True
            previous_context = getattr(self.bpy, "context", None)
            previous_ops = self.bpy.ops
            self.bpy.context = types.SimpleNamespace(scene=None)
            self.bpy.ops = types.SimpleNamespace(
                fmodel=types.SimpleNamespace(scan_anims=lambda: {"CANCELLED"})
            )
            try:
                with mock.patch.dict(sys.modules, {"addon.core.utils": utils_module}):
                    self.assertFalse(self.stages.FinalizationStage().execute(context))
            finally:
                self.bpy.context = previous_context
                self.bpy.ops = previous_ops

            self.assertTrue(os.path.exists(direct_file))

    def test_finalization_cleanup_runs_after_successful_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            context = FakeContext({}, {"output_dir": directory})
            context.library_manifest = {
                "library_name": "Heroes", "library_path": directory,
            }
            events = []
            utils_module = types.ModuleType("addon.core.utils")
            utils_module.mount_dynamic_library = lambda *args: events.append("mount") or True
            previous_context = getattr(self.bpy, "context", None)
            previous_ops = self.bpy.ops
            self.bpy.context = types.SimpleNamespace(scene=None)
            self.bpy.ops = types.SimpleNamespace(fmodel=types.SimpleNamespace(
                scan_anims=lambda: events.append("scan") or {"FINISHED"}
            ))
            try:
                with mock.patch.dict(sys.modules, {"addon.core.utils": utils_module}), \
                        mock.patch.object(
                            self.stages, "retry_staged_cleanup",
                            side_effect=lambda *args: events.append("cleanup"),
                        ):
                    self.assertTrue(self.stages.FinalizationStage().execute(context))
            finally:
                self.bpy.context = previous_context
                self.bpy.ops = previous_ops

            self.assertEqual(events, ["mount", "scan", "cleanup"])

    def test_finalization_cleans_persisted_owner_after_task_config_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            owner_dir = os.path.join(directory, "_pipeline_tmp", "import-task")
            os.makedirs(owner_dir)
            generated_chunk = os.path.join(owner_dir, "Hero_chunk_0001.blend")
            open(generated_chunk, "w").close()
            # The import task settings were cleared, but its durable cleanup checkpoint remains.
            context = FakeContext({}, {
                "output_dir": directory,
                "pending_staged_cleanup": [generated_chunk],
                "pending_staged_cleanup_owners": {generated_chunk: owner_dir},
            })
            context.library_manifest = {
                "library_name": "Heroes", "library_path": directory,
            }
            utils_module = types.ModuleType("addon.core.utils")
            utils_module.mount_dynamic_library = mock.Mock(return_value=True)
            previous_context = getattr(self.bpy, "context", None)
            previous_ops = self.bpy.ops
            scan_anims = mock.Mock(return_value={"FINISHED"})
            self.bpy.context = types.SimpleNamespace(scene=None)
            self.bpy.ops = types.SimpleNamespace(
                fmodel=types.SimpleNamespace(scan_anims=scan_anims)
            )
            try:
                with mock.patch.dict(sys.modules, {"addon.core.utils": utils_module}):
                    self.assertTrue(self.stages.FinalizationStage().execute(context))
            finally:
                self.bpy.context = previous_context
                self.bpy.ops = previous_ops

            utils_module.mount_dynamic_library.assert_called_once()
            scan_anims.assert_called_once_with()
            self.assertFalse(os.path.exists(generated_chunk))
            self.assertEqual(context.config["pending_staged_cleanup"], [])
            self.assertEqual(context.config["pending_staged_cleanup_owners"], {})

    def test_finalization_success_removes_only_queued_direct_child_chunk_blend(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = self.stages.FinalizationStage()
            context = FakeContext({}, {
                "output_dir": directory,
                "source_skeleton": "Hero",
                "source_subset": [os.path.join(directory, "Idle.ueanim")],
                "target_armature": "HeroRig",
            })
            task_dir = stage._staging_task_dir(context)
            os.makedirs(task_dir)
            generated_chunk = os.path.join(task_dir, "Hero_chunk_0001.blend")
            report = os.path.join(task_dir, "import_failures.json")
            user_blend = os.path.join(task_dir, "user_asset.blend")
            nested_dir = os.path.join(task_dir, "reports")
            os.makedirs(nested_dir)
            nested_report = os.path.join(nested_dir, "report.json")
            for path in (generated_chunk, report, user_blend, nested_report):
                open(path, "w").close()
            context.config.update({
                "pending_staged_cleanup": [generated_chunk],
                "pending_staged_cleanup_owners": {generated_chunk: task_dir},
            })
            context.library_manifest = {
                "library_name": "Heroes", "library_path": directory,
            }
            utils_module = types.ModuleType("addon.core.utils")
            utils_module.mount_dynamic_library = lambda *args: True
            previous_utils = sys.modules.get("addon.core.utils")
            previous_context = getattr(self.bpy, "context", None)
            previous_ops = self.bpy.ops
            sys.modules["addon.core.utils"] = utils_module
            self.bpy.context = types.SimpleNamespace(scene=None)
            self.bpy.ops = types.SimpleNamespace(
                fmodel=types.SimpleNamespace(scan_anims=lambda: {"FINISHED"})
            )
            try:
                self.assertTrue(stage.execute(context))
            finally:
                self.bpy.context = previous_context
                self.bpy.ops = previous_ops
                if previous_utils is None:
                    sys.modules.pop("addon.core.utils", None)
                else:
                    sys.modules["addon.core.utils"] = previous_utils

            self.assertFalse(os.path.exists(generated_chunk))
            self.assertTrue(os.path.exists(report))
            self.assertTrue(os.path.exists(user_blend))
            self.assertTrue(os.path.exists(nested_report))

    def test_finalization_keeps_same_named_library_at_different_path_for_scan(self):
        context = FakeContext({}, {})
        context.library_manifest = {
            "library_name": "Heroes",
            "library_id": "new-library-id",
            "library_path": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        }
        old_library = types.SimpleNamespace(
            name="Heroes", library_id="old-library-id", path=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )

        class Libraries(list):
            def add(self):
                library = types.SimpleNamespace(name="", library_id="", path="")
                self.append(library)
                return library

        libraries = Libraries([old_library])
        utils_module = types.ModuleType("addon.core.utils")
        utils_module.mount_dynamic_library = lambda *args: True
        previous_utils = sys.modules.get("addon.core.utils")
        sys.modules["addon.core.utils"] = utils_module
        previous_context = getattr(self.bpy, "context", None)
        previous_ops = self.bpy.ops
        scanned = []
        self.bpy.context = types.SimpleNamespace(scene=types.SimpleNamespace(
            fmodel_assets=types.SimpleNamespace(project_libs=libraries)
        ))
        self.bpy.ops = types.SimpleNamespace(fmodel=types.SimpleNamespace(
            scan_anims=lambda: scanned.append([library.path for library in libraries]) or {"FINISHED"}
        ))
        try:
            self.assertTrue(self.stages.FinalizationStage().run(context))
        finally:
            self.bpy.context = previous_context
            self.bpy.ops = previous_ops
            if previous_utils is None:
                sys.modules.pop("addon.core.utils", None)
            else:
                sys.modules["addon.core.utils"] = previous_utils

        self.assertEqual(len(libraries), 2)
        self.assertEqual(libraries[1].library_id, "new-library-id")
        self.assertEqual(scanned, [[old_library.path, context.library_manifest["library_path"]]])

    def test_finalization_updates_library_matched_by_persistent_id(self):
        context = FakeContext({}, {})
        context.library_manifest = {
            "library_name": "Renamed Heroes",
            "library_id": "persistent-library-id",
            "library_path": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        }
        library = types.SimpleNamespace(
            name="Old name", library_id="persistent-library-id", path="stale-path",
        )
        utils_module = types.ModuleType("addon.core.utils")
        utils_module.mount_dynamic_library = lambda *args: True
        previous_utils = sys.modules.get("addon.core.utils")
        sys.modules["addon.core.utils"] = utils_module
        previous_context = getattr(self.bpy, "context", None)
        previous_ops = self.bpy.ops
        self.bpy.context = types.SimpleNamespace(scene=types.SimpleNamespace(
            fmodel_assets=types.SimpleNamespace(project_libs=[library])
        ))
        self.bpy.ops = types.SimpleNamespace(fmodel=types.SimpleNamespace(
            scan_anims=lambda: {"FINISHED"}
        ))
        try:
            self.assertTrue(self.stages.FinalizationStage().run(context))
        finally:
            self.bpy.context = previous_context
            self.bpy.ops = previous_ops
            if previous_utils is None:
                sys.modules.pop("addon.core.utils", None)
            else:
                sys.modules["addon.core.utils"] = previous_utils

        self.assertEqual(library.name, "Renamed Heroes")
        self.assertEqual(library.path, context.library_manifest["library_path"])

    def test_finalization_updates_library_matched_by_normalized_path(self):
        context = FakeContext({}, {})
        context.library_manifest = {
            "library_name": "Heroes",
            "library_id": "",
            "library_path": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        }
        library = types.SimpleNamespace(name="Old name", library_id="", path=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        utils_module = types.ModuleType("addon.core.utils")
        utils_module.mount_dynamic_library = lambda *args: True
        previous_utils = sys.modules.get("addon.core.utils")
        sys.modules["addon.core.utils"] = utils_module
        previous_context = getattr(self.bpy, "context", None)
        previous_ops = self.bpy.ops
        self.bpy.context = types.SimpleNamespace(scene=types.SimpleNamespace(
            fmodel_assets=types.SimpleNamespace(project_libs=[library])
        ))
        self.bpy.ops = types.SimpleNamespace(fmodel=types.SimpleNamespace(
            scan_anims=lambda: {"FINISHED"}
        ))
        try:
            self.assertTrue(self.stages.FinalizationStage().run(context))
        finally:
            self.bpy.context = previous_context
            self.bpy.ops = previous_ops
            if previous_utils is None:
                sys.modules.pop("addon.core.utils", None)
            else:
                sys.modules["addon.core.utils"] = previous_utils

        self.assertEqual(library.name, "Heroes")

    def test_finalization_fails_when_post_build_library_scan_raises(self):
        context = FakeContext({}, {})
        context.library_manifest = {
            "library_name": "Heroes",
            "library_path": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        }
        context.status = "pending"
        utils_module = types.ModuleType("addon.core.utils")
        utils_module.mount_dynamic_library = lambda *args: True
        previous_utils = sys.modules.get("addon.core.utils")
        sys.modules["addon.core.utils"] = utils_module
        previous_context = getattr(self.bpy, "context", None)
        previous_ops = self.bpy.ops
        self.bpy.context = types.SimpleNamespace(scene=None)

        def fail_scan():
            raise RuntimeError("scan failed")

        self.bpy.ops = types.SimpleNamespace(
            fmodel=types.SimpleNamespace(scan_anims=fail_scan)
        )
        try:
            self.assertFalse(self.stages.FinalizationStage().execute(context))
        finally:
            self.bpy.context = previous_context
            self.bpy.ops = previous_ops
            if previous_utils is None:
                sys.modules.pop("addon.core.utils", None)
            else:
                sys.modules["addon.core.utils"] = previous_utils

        self.assertNotEqual(context.status, "completed")
        self.assertTrue(any(level == "error" and "scan failed" in message
                            for level, message in context.logs))

    def test_library_build_checks_asset_data_after_marking(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        asset_mark = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "asset_mark"
        )
        asset_checks = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
            and isinstance(node.test.operand, ast.Attribute)
            and node.test.operand.attr == "asset_data"
            and node.lineno > asset_mark.lineno
        ]

        self.assertTrue(asset_checks)

    def test_unified_build_defers_library_scan_to_finalization(self):
        operator_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "Anims", "operators_asset.py"
        )
        with open(operator_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        operator = next(node for node in module.body
                        if isinstance(node, ast.ClassDef) and node.name == "FMODEL_OT_build_anim_library")
        execute = next(node for node in operator.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        scan_calls = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "scan_anims"
        ]

        self.assertEqual(scan_calls, [])

    def test_import_and_library_stages_share_staging_task_directory_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            context = FakeContext({}, {
                "output_dir": directory,
                "source_skeleton": "Hero",
                "source_subset": [os.path.join(directory, "Idle.ueanim")],
                "target_armature": "HeroRig",
            })
            import_stage = self.stages.AssetImportStage()
            library_stage = self.stages.LibraryBuildingStage()

            self.assertIs(
                import_stage._staging_task_dir.__func__,
                library_stage._staging_task_dir.__func__,
            )
            self.assertEqual(
                import_stage._staging_task_dir(context),
                library_stage._staging_task_dir(context),
            )

    def test_staging_task_directory_rejects_realpath_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            context = FakeContext({}, {
                "output_dir": directory,
                "source_skeleton": "Hero",
                "source_subset": [os.path.join(directory, "Idle.ueanim")],
                "target_armature": "HeroRig",
            })
            stage = self.stages.AssetImportStage()
            task_dir = stage._staging_task_dir(context)
            tmp_dir = os.path.dirname(task_dir)
            escaped_dir = os.path.join(directory, "escaped-task")
            real_realpath = self.stages.os.path.realpath

            def escaped_realpath(path):
                if os.path.abspath(path) == os.path.abspath(task_dir):
                    return escaped_dir
                return real_realpath(path)

            with mock.patch.object(
                self.stages.os.path, "realpath", side_effect=escaped_realpath
            ):
                with self.assertRaisesRegex(ValueError, "staging task directory"):
                    stage._staging_task_dir(context)

            self.assertEqual(os.path.dirname(task_dir), tmp_dir)

    def test_unified_transaction_commits_before_staged_cleanup(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        prepare_call = next(node for node in ast.walk(execute)
                            if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "prepare_library_transaction")
        publish_call = next(node for node in ast.walk(execute)
                            if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "publish_rebuild_replacement_set")
        cleanup_call = next(node for node in ast.walk(execute)
                            if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "queue_staged_cleanup"
                            and node.lineno > publish_call.lineno)
        self.assertLess(prepare_call.lineno, publish_call.lineno)
        self.assertLess(publish_call.lineno, cleanup_call.lineno)

        with tempfile.TemporaryDirectory() as directory:
            committed = os.path.join(directory, "Hero_chunk_0001.blend")
            failed = os.path.join(directory, "Hero_chunk_0002.blend")
            open(committed, "w").close()
            open(failed, "w").close()

            removed = self.stages.cleanup_committed_staged_blends(directory, {committed})

            self.assertEqual(removed, [os.path.abspath(committed)])
            self.assertFalse(os.path.exists(committed))
            self.assertTrue(os.path.exists(failed))

    def test_library_backups_are_not_created_before_transaction_preparation(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        prepare_call = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "prepare_library_transaction"
        )
        early_backup_calls = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "prepare_transaction_artifact"
            and node.lineno < prepare_call.lineno
        ]

        self.assertEqual(early_backup_calls, [])

    def test_generated_bundle_filenames_are_validated_before_transaction_preparation(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        prepare_call = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "prepare_library_transaction"
        )
        generated_name_validations = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "validate_generated_output_filenames"
            and node.lineno < prepare_call.lineno
        ]

        self.assertGreaterEqual(len(generated_name_validations), 2)

    def test_shared_staged_blend_remains_when_later_chunk_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            staged_blend = os.path.join(directory, "Hero_chunk_0001.blend")
            open(staged_blend, "w").close()
            first = {"action_name": "Idle", "staged_blend": staged_blend}
            second = {"action_name": "Run", "staged_blend": staged_blend}
            references = self.stages.track_staged_blend_references([first, second])

            ready = self.stages.mark_staged_blend_actions_published(references, [first])
            self.stages.cleanup_committed_staged_blends(directory, ready)

            self.assertEqual(ready, set())
            self.assertTrue(os.path.exists(staged_blend))

    def test_shared_staged_blend_is_deleted_after_all_chunks_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            staged_blend = os.path.join(directory, "Hero_chunk_0001.blend")
            open(staged_blend, "w").close()
            first = {"action_name": "Idle", "staged_blend": staged_blend}
            second = {"action_name": "Run", "staged_blend": staged_blend}
            references = self.stages.track_staged_blend_references([first, second])

            first_ready = self.stages.mark_staged_blend_actions_published(references, [first])
            self.stages.cleanup_committed_staged_blends(directory, first_ready)
            second_ready = self.stages.mark_staged_blend_actions_published(references, [second])
            removed = self.stages.cleanup_committed_staged_blends(directory, second_ready)

            self.assertEqual(first_ready, set())
            self.assertEqual(removed, [os.path.abspath(staged_blend)])
            self.assertFalse(os.path.exists(staged_blend))

    def test_locked_committed_staging_cleanup_is_retained_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            staged_blend = os.path.join(directory, "Hero_chunk_0001.blend")
            open(staged_blend, "w").close()
            values = {}
            context = types.SimpleNamespace(
                get_config=lambda key, default=None: values.get(key, default),
                set_config=lambda key, value: values.__setitem__(key, value),
            )
            with mock.patch.object(self.stages.os, "remove", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    self.stages.retry_staged_cleanup(context, [staged_blend], directory)
            self.assertEqual(values["pending_staged_cleanup"], [staged_blend])

            self.stages.retry_staged_cleanup(context, [], directory)

            self.assertEqual(values["pending_staged_cleanup"], [])
            self.assertFalse(os.path.exists(staged_blend))

    def test_partial_locked_cleanup_only_retains_remaining_local_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "Hero_chunk_0001.blend")
            locked = os.path.join(directory, "Hero_chunk_0002.blend")
            open(first, "w").close(); open(locked, "w").close()
            values = {}
            context = types.SimpleNamespace(
                get_config=lambda key, default=None: values.get(key, default),
                set_config=lambda key, value: values.__setitem__(key, value),
            )
            original_remove = self.stages.os.remove

            def remove_then_lock(path):
                if os.path.abspath(path) == os.path.abspath(locked):
                    raise PermissionError("locked")
                original_remove(path)

            with mock.patch.object(self.stages.os, "remove", side_effect=remove_then_lock):
                with self.assertRaises(PermissionError):
                    self.stages.retry_staged_cleanup(context, [first, locked], directory)

            self.assertFalse(os.path.exists(first))
            self.assertEqual(values["pending_staged_cleanup"], [os.path.abspath(locked)])
            self.assertEqual(
                values["pending_staged_cleanup_owners"],
                {os.path.abspath(locked): os.path.abspath(directory)},
            )

    def test_pending_cleanup_retries_using_persisted_owner_task_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_root = os.path.join(directory, "_pipeline_tmp")
            owner_dir = os.path.join(tmp_root, "owner")
            current_dir = os.path.join(tmp_root, "current")
            os.makedirs(owner_dir); os.makedirs(current_dir)
            staged_blend = os.path.join(owner_dir, "Hero_chunk_0001.blend")
            open(staged_blend, "w").close()
            values = {
                "pending_staged_cleanup": [staged_blend],
                "pending_staged_cleanup_owners": {staged_blend: owner_dir},
            }
            context = types.SimpleNamespace(
                get_config=lambda key, default=None: values.get(key, default),
                set_config=lambda key, value: values.__setitem__(key, value),
            )

            self.stages.retry_staged_cleanup(context, [], current_dir)

            self.assertEqual(values["pending_staged_cleanup"], [])
            self.assertEqual(values["pending_staged_cleanup_owners"], {})
            self.assertFalse(os.path.exists(staged_blend))

    def test_pending_cleanup_paths_do_not_replace_persisted_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_root = os.path.join(directory, "_pipeline_tmp")
            owner_dir = os.path.join(tmp_root, "owner")
            current_dir = os.path.join(tmp_root, "current")
            os.makedirs(owner_dir); os.makedirs(current_dir)
            staged_blend = os.path.join(owner_dir, "Hero_chunk_0001.blend")
            open(staged_blend, "w").close()
            values = {
                "pending_staged_cleanup": [staged_blend],
                "pending_staged_cleanup_owners": {staged_blend: owner_dir},
            }
            context = types.SimpleNamespace(
                get_config=lambda key, default=None: values.get(key, default),
                set_config=lambda key, value: values.__setitem__(key, value),
            )

            with mock.patch.object(self.stages.os, "remove", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    self.stages.retry_staged_cleanup(context, [staged_blend], current_dir)

            self.assertEqual(values["pending_staged_cleanup"], [staged_blend])
            self.assertEqual(
                values["pending_staged_cleanup_owners"],
                {os.path.abspath(staged_blend): os.path.abspath(owner_dir)},
            )

    def test_pending_cleanup_rejects_mutually_forged_owner_and_path(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_root = os.path.join(directory, "_pipeline_tmp")
            current_dir = os.path.join(tmp_root, "current")
            forged_owner = os.path.join(directory, "forged-owner")
            os.makedirs(current_dir); os.makedirs(forged_owner)
            forged_path = os.path.join(forged_owner, "forged.blend")
            open(forged_path, "w").close()
            values = {
                "pending_staged_cleanup": [forged_path],
                "pending_staged_cleanup_owners": {forged_path: forged_owner},
            }
            context = types.SimpleNamespace(
                get_config=lambda key, default=None: values.get(key, default),
                set_config=lambda key, value: values.__setitem__(key, value),
            )

            with self.assertRaisesRegex(ValueError, "staging cleanup owner"):
                self.stages.retry_staged_cleanup(context, [], current_dir)

            self.assertTrue(os.path.exists(forged_path))

    def test_pending_cleanup_rejects_non_direct_child_or_non_blend_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_root = os.path.join(directory, "_pipeline_tmp")
            current_dir = os.path.join(tmp_root, "current")
            owner_dir = os.path.join(tmp_root, "owner")
            nested_dir = os.path.join(owner_dir, "nested")
            os.makedirs(current_dir); os.makedirs(nested_dir)
            nested_blend = os.path.join(nested_dir, "nested.blend")
            non_blend = os.path.join(owner_dir, "notes.txt")
            for candidate in (nested_blend, non_blend):
                open(candidate, "w").close()
                values = {
                    "pending_staged_cleanup": [candidate],
                    "pending_staged_cleanup_owners": {candidate: owner_dir},
                }
                context = types.SimpleNamespace(
                    get_config=lambda key, default=None: values.get(key, default),
                    set_config=lambda key, value: values.__setitem__(key, value),
                )

                with self.subTest(candidate=candidate):
                    with self.assertRaisesRegex(ValueError, "staging cleanup path"):
                        self.stages.retry_staged_cleanup(context, [], current_dir)
                    self.assertTrue(os.path.exists(candidate))

    def test_pending_cleanup_quarantines_stale_cross_output_owner(self):
        with tempfile.TemporaryDirectory() as old_output, tempfile.TemporaryDirectory() as current_output:
            old_owner = os.path.join(old_output, "_pipeline_tmp", "old-task")
            current_owner = os.path.join(current_output, "_pipeline_tmp", "current-task")
            os.makedirs(old_owner); os.makedirs(current_owner)
            stale = os.path.join(old_owner, "Hero_chunk_0001.blend")
            open(stale, "w").close()
            values = {
                "pending_staged_cleanup": [stale],
                "pending_staged_cleanup_owners": {stale: old_owner},
            }
            context = types.SimpleNamespace(
                get_config=lambda key, default=None: values.get(key, default),
                set_config=lambda key, value: values.__setitem__(key, value),
            )

            self.stages.retry_staged_cleanup(context, [], current_owner)

            self.assertTrue(os.path.exists(stale))
            self.assertEqual(values["pending_staged_cleanup"], [])
            self.assertEqual(values["pending_staged_cleanup_owners"], {})
            self.assertEqual(values["quarantined_staged_cleanup"], [{
                "path": os.path.abspath(stale),
                "owner": os.path.abspath(old_owner),
                "reason": "owner belongs to a different output",
            }])

    def test_locked_local_cleanup_persists_cross_output_quarantine(self):
        with tempfile.TemporaryDirectory() as old_output, tempfile.TemporaryDirectory() as current_output:
            old_owner = os.path.join(old_output, "_pipeline_tmp", "old-task")
            current_owner = os.path.join(current_output, "_pipeline_tmp", "current-task")
            os.makedirs(old_owner); os.makedirs(current_owner)
            stale = os.path.join(old_owner, "Hero_chunk_0001.blend")
            locked = os.path.join(current_owner, "Hero_chunk_0002.blend")
            open(stale, "w").close(); open(locked, "w").close()
            values = {
                "pending_staged_cleanup": [stale],
                "pending_staged_cleanup_owners": {stale: old_owner},
            }
            context = types.SimpleNamespace(
                get_config=lambda key, default=None: values.get(key, default),
                set_config=lambda key, value: values.__setitem__(key, value),
            )

            with mock.patch.object(self.stages.os, "remove", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    self.stages.retry_staged_cleanup(context, [locked], current_owner)

            self.assertEqual(values["pending_staged_cleanup"], [os.path.abspath(locked)])
            self.assertEqual(
                values["pending_staged_cleanup_owners"],
                {os.path.abspath(locked): os.path.abspath(current_owner)},
            )
            self.assertEqual(values["quarantined_staged_cleanup"], [{
                "path": os.path.abspath(stale),
                "owner": os.path.abspath(old_owner),
                "reason": "owner belongs to a different output",
            }])

    def test_resolve_rejects_bad_staged_path_before_blender_load(self):
        loads = []
        self.bpy.data.libraries.load = lambda path, link: loads.append(path)
        imported = {
            "action_name": "Idle",
            "source_manifest_ref": "Hero/Moves/Idle",
            "staged_blend": "C:/untrusted/Idle.blend",
        }

        action, error = self.stages.resolve_imported_action(
            self.bpy, imported, staged_validator=lambda path: (_ for _ in ()).throw(
                ValueError("bad staged path")
            )
        )

        self.assertIsNone(action)
        self.assertIn("bad staged path", error)
        self.assertEqual(loads, [])

    def test_task_identity_uses_legacy_skeleton_filter_when_source_is_unset(self):
        context = FakeContext({}, {"skeleton_filter": "Hero"})

        identity = self.stages.AssetImportStage()._task_identity(context)

        self.assertEqual(identity["source_skeleton"], "Hero")

    def test_legacy_skeleton_filter_scopes_manifest_sources_and_ueanim_import(self):
        with tempfile.TemporaryDirectory() as directory:
            hero = os.path.join(directory, "Hero.ueanim")
            enemy = os.path.join(directory, "Enemy.ueanim")
            open(hero, "w").close()
            open(enemy, "w").close()
            imported = []

            class Options:
                def __init__(self, **kwargs):
                    pass

            class Importer:
                def __init__(self, options):
                    pass

                def import_file(self, path):
                    imported.append(path)
                    TaskScopedImportTests.actions.append(FakeAction(os.path.basename(path)))

            api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
            api.get_ueformat_api = lambda: (Importer, None, Options, None)
            sys.modules[api.__name__] = api
            manifest = {"skeletons": {
                "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": hero}}}},
                "Enemy": {"Moves": {"__animations__": {"Idle": {"source_file": enemy}}}},
            }}
            context = FakeContext(manifest, {
                "skeleton_filter": "Hero",
                "target_armature": "HeroRig",
                "import_resume_enabled": False,
            })
            stage = self.stages.AssetImportStage()

            self.assertEqual(
                [item["skeleton"] for item in stage._current_manifest_sources(context)],
                ["Hero"],
            )
            self.assertTrue(stage._import_ueanim_direct(context))
            self.assertEqual(imported, [hero])
            self.assertEqual(
                [item["source_manifest_ref"] for item in context.imported_assets["imported_actions"]],
                ["Hero/Moves/Idle"],
            )

    def test_skeleton_labels_and_identity_do_not_gate_pipeline_import(self):
        for mode in ("ueformat", "strict", "name_retarget"):
            for identity_state in ("different", "missing", "stale_cache"):
                with self.subTest(mode=mode, identity=identity_state), tempfile.TemporaryDirectory() as directory:
                    self.actions[:] = []
                    source = os.path.join(directory, "Breathing.ueanim")
                    open(source, "w").close()
                    calls = []
                    target = self.bpy.data.objects.get("EnemyRig")

                    class Options:
                        # Standard upstream options deliberately have no strict keyword.
                        def __init__(self, scale_factor, rotation_only, import_curves, override_skeleton):
                            calls.append(override_skeleton)

                    class Importer:
                        def __init__(self, options):
                            pass

                        def import_file(self, path):
                            TaskScopedImportTests.actions.append(FakeAction("Breathing"))

                    api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
                    api.get_ueformat_api = lambda: (Importer, None, Options, None)
                    sys.modules[api.__name__] = api
                    identity = {"version": 1, "skeleton_path": "/Game/Hero", "bone_layout_hash": "a" * 64,
                                "has_skeleton_guid": False, "skeleton_guid": None}
                    metadata = {} if identity_state == "missing" else {"fmodel_skeleton_identity": identity}
                    context = FakeContext({"skeletons": {"Hero": {"Moves": {"__animations__": {
                        "Breathing": {"source_file": source, "metadata": metadata},
                    }}}}}, {
                        "source_skeleton": "Hero", "skeleton_filter": "Hero", "target_armature": "EnemyRig",
                        "source_subset": [source], "import_resume_enabled": False, "skeleton_compatibility_mode": mode,
                    })
                    with mock.patch.object(self.stages, "read_ueformat_identity",
                                           side_effect=AssertionError("import must not use identity preflight")):
                        self.assertTrue(self.stages.AssetImportStage()._import_ueanim_direct(context))
                    self.assertEqual(calls, [target])
                    self.assertEqual(context.imported_assets["failed_imports"], [])
                    self.assertEqual(self.actions[0]["Pipeline_SkeletonCompatibility"], "ueformat")


    def test_source_label_does_not_select_target_over_active_armature(self):
        selected_targets = []

        class Options:
            def __init__(self, **kwargs):
                selected_targets.append(kwargs.get("override_skeleton"))

        class Importer:
            def __init__(self, options):
                pass

        api = types.ModuleType("addon.Fmodel.importers.animation.operator_anim_ueformat")
        api.get_ueformat_api = lambda: (Importer, None, Options, None)
        sys.modules[api.__name__] = api
        armatures = [
            types.SimpleNamespace(
                name=f"{name}Rig", type="ARMATURE",
                get=lambda key, default=None, name=name: name if key == "FModel_Skeleton" else default,
            )
            for name in ("Hero", "Enemy")
        ]

        class Objects(list):
            def get(self, name):
                return next((obj for obj in self if obj.name == name), None)

        self.bpy.data.objects = Objects(armatures)
        previous_context = getattr(self.bpy, "context", None)
        self.bpy.context = types.SimpleNamespace(object=armatures[1], active_object=armatures[1])
        self.addCleanup(setattr, self.bpy, "context", previous_context)
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            open(source, "w").close()
            context = FakeContext({"skeletons": {"Hero": {"": {"__animations__": {
                "Idle": {"source_file": source},
            }}}}}, {
                "source_skeleton": "Hero",
                "skeleton_filter": "Enemy",
                "import_resume_enabled": False,
            })

            self.assertFalse(self.stages.AssetImportStage()._import_ueanim_direct(context))
        self.assertEqual(selected_targets, [armatures[1]])

    def test_fmodel_manifest_acquisition_ignores_legacy_json_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = os.path.join(directory, "animations_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "Hero": {"Moves": {"__actions__": [["Idle", ""]]}},
                    "Enemy": {"Moves": {"__actions__": [["Attack", ""]]}},
                }, handle)
            context = FakeContext({}, {
                "source_skeleton": "Hero",
                "skeleton_filter": "Enemy",
            })

            self.assertFalse(
                self.stages.DictionaryAcquisitionStage()._process_fmodel_raw_files(context, directory)
            )
            self.assertEqual(context.dictionary_manifest, {})

    def test_fmodel_raw_acquisition_does_not_fall_back_to_json_skeleton_names(self):
        with tempfile.TemporaryDirectory() as directory:
            for skeleton, animation in (("Hero", "Idle"), ("Enemy", "Attack")):
                skeleton_dir = os.path.join(directory, skeleton)
                os.makedirs(skeleton_dir)
                open(os.path.join(skeleton_dir, f"{animation}.ueanim"), "w").close()

            fallback_calls = []
            utils_asset = types.ModuleType("addon.Assets.Utils.utils_asset")
            utils_asset.get_skeleton_name_from_json = lambda path: fallback_calls.append(path)
            context = FakeContext({}, {
                "source_skeleton": "Hero",
                "skeleton_filter": "Enemy",
            })

            with mock.patch.dict(sys.modules, {
                "addon.Assets": types.ModuleType("addon.Assets"),
                "addon.Assets.Utils": types.ModuleType("addon.Assets.Utils"),
                "addon.Assets.Utils.utils_asset": utils_asset,
            }):
                self.assertFalse(
                    self.stages.DictionaryAcquisitionStage()._process_fmodel_raw_files(
                        context, directory
                    )
                )
            self.assertEqual(context.dictionary_manifest, {})
            self.assertEqual(fallback_calls, [])

    def test_checkpoint_staging_validation_uses_action_manifest_skeleton(self):
        with tempfile.TemporaryDirectory() as output_dir:
            task_identity = {"source_skeleton": ""}
            task_dir = self.stages.staging_task_dir(output_dir, task_identity)
            os.makedirs(task_dir)
            staged_blend = self.stages.staging_chunk_path(task_dir, "Hero", 1)
            data = {
                "config": {"output_dir": output_dir},
                "imported_assets": {
                    "task_identity": task_identity,
                    "imported_actions": [{
                        "source_manifest_ref": "Hero/Moves/Idle",
                        "staged_blend": staged_blend,
                    }],
                },
            }

            self.stages.PipelineContext._validate_staged_checkpoint_paths(data)

    def test_runtime_staging_validation_uses_task_path_without_matching_display_labels(self):
        with tempfile.TemporaryDirectory() as output_dir:
            context = FakeContext({}, {"output_dir": output_dir})
            stage = self.stages.AssetImportStage()
            task_dir = stage._staging_task_dir(context)
            os.makedirs(task_dir)
            staged_blend = self.stages.staging_chunk_path(task_dir, "Hero", 1)

            self.assertEqual(
                stage._validate_staged_blend(
                    context, staged_blend,
                    {"source_manifest_ref": "Hero/Moves/Idle"},
                ),
                staged_blend,
            )
            self.assertEqual(stage._validate_staged_blend(
                    context, staged_blend,
                    {"source_manifest_ref": "Enemy/Moves/Idle"},
                ), staged_blend)
            with self.assertRaisesRegex(ValueError, "filename contract"):
                stage._validate_staged_blend(context, os.path.join(output_dir, 'actions_chunk_0001.blend'),
                                           {"source_manifest_ref": "Hero/Moves/Idle"})

    def test_staging_validation_rejects_actions_without_action_or_task_skeleton(self):
        with tempfile.TemporaryDirectory() as output_dir:
            task_identity = {"source_skeleton": ""}
            task_dir = self.stages.staging_task_dir(output_dir, task_identity)
            os.makedirs(task_dir)
            staged_blend = self.stages.staging_chunk_path(task_dir, "Hero", 1)
            data = {
                "config": {"output_dir": output_dir},
                "imported_assets": {
                    "task_identity": task_identity,
                    "imported_actions": [{"staged_blend": staged_blend}],
                },
            }
            context = FakeContext({}, {"output_dir": output_dir})
            stage = self.stages.AssetImportStage()

            with self.assertRaisesRegex(ValueError, "source skeleton"):
                self.stages.PipelineContext._validate_staged_checkpoint_paths(data)
            with self.assertRaisesRegex(ValueError, "source skeleton"):
                stage._validate_staged_blend(context, staged_blend, {})

    def test_force_rebuild_defers_staging_cleanup_until_replacement_set_commits(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        replacement_publish = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "publish_rebuild_replacement_set"
        )
        cleanup_calls = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "queue_staged_cleanup"
        ]
        self.assertTrue(any(call.lineno > replacement_publish.lineno for call in cleanup_calls))

    def test_temporary_replacement_is_journaled_before_checkpoint_and_action_unload(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        journal_calls = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "persist_transaction_replacement"
        ]
        checkpoint_calls = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "persist_checkpoint"
        ]
        action_removals = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "remove"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "actions"
        ]

        self.assertTrue(journal_calls)
        first_journal = min(call.lineno for call in journal_calls)
        first_later_checkpoint = min(
            call.lineno for call in checkpoint_calls if call.lineno > first_journal
        )
        first_later_unload = min(
            call.lineno for call in action_removals if call.lineno > first_journal
        )
        self.assertLess(first_journal, first_later_checkpoint)
        self.assertLess(first_journal, first_later_unload)

    def test_authoritative_resume_marks_staged_actions_for_cleanup_without_new_pairs(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        finalization = next(
            node for node in execute.body
            if isinstance(node, ast.If)
            and "pending_rebuild_pairs" in ast.unparse(node.test)
        )
        mark_calls = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "mark_staged_blend_actions_published"
        ]

        self.assertTrue(mark_calls)
        self.assertTrue(all(call.lineno > finalization.end_lineno for call in mark_calls))

    def test_published_provenance_is_checkpointed_before_incremental_and_rebuild_cleanup(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        persist_calls = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_persist_published_provenance"
        ]
        cleanup_calls = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "queue_staged_cleanup"
        ]

        publish_call = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "publish_rebuild_replacement_set"
        )
        publication_persist = next(call for call in persist_calls
                                   if call.lineno > publish_call.lineno)
        publication_cleanup = next(call for call in cleanup_calls
                                   if call.lineno > publish_call.lineno)
        self.assertLess(publish_call.lineno, publication_persist.lineno)
        self.assertLess(publication_persist.lineno, publication_cleanup.lineno)

    def test_published_provenance_checkpoint_updates_imported_assets_durably(self):
        context = FakeContext({}, {})
        imported = {"action_name": "Idle", "staged_blend": "staged.blend"}
        context.imported_assets = {"imported_actions": [imported]}
        destination = os.path.abspath("Hero.blend")

        self.stages.LibraryBuildingStage()._persist_published_provenance(
            context, [(imported, destination)]
        )

        self.assertEqual(imported["source_bundle"], os.path.normcase(destination))
        self.assertEqual(context.blender_saves, 1)

    def test_partial_publication_updates_matching_resume_record_and_keeps_failures(self):
        imported = {"action_name": "Idle", "source_manifest_ref": "Hero/Moves/Idle",
                    "source_file": os.path.abspath("Idle.ueanim"), "staged_blend": "staged.blend"}
        staged_record = dict(imported)
        other_task = {**imported, "source_manifest_ref": "Enemy/Moves/Idle"}
        failure = {"source_file": "Missing.ueanim", "error": "missing"}
        checkpoint = {"state": "partial", "pending_sources": ["Missing.ueanim"],
                      "imported_actions": [staged_record, other_task], "failed_imports": [failure]}
        context = FakeContext({}, {"import_checkpoint": checkpoint})
        context.imported_assets = {"imported_actions": [imported]}
        destination = os.path.abspath("Hero.blend")

        self.stages.LibraryBuildingStage()._persist_published_provenance(context, [(imported, destination)])

        saved = context.saved_checkpoints[-1]
        self.assertEqual(saved["imported_actions"][0]["source_bundle"], os.path.normcase(destination))
        self.assertNotIn("source_bundle", saved["imported_actions"][1])
        self.assertEqual(saved["state"], "partial")
        self.assertEqual(saved["pending_sources"], ["Missing.ueanim"])
        self.assertEqual(saved["failed_imports"], [failure])

    def test_published_provenance_uses_external_checkpoint_before_staging_cleanup(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        persist = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "_persist_published_provenance")
        calls = [
            node for node in ast.walk(persist)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]

        self.assertIn("persist_checkpoint", [node.func.attr for node in calls])

    def test_library_checkpoint_precedes_generated_action_unload(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        checkpoint_call = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "persist_checkpoint"
        )
        action_remove = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "remove"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "actions"
        )

        self.assertLess(checkpoint_call.lineno, action_remove.lineno)

    def test_incremental_retry_resolves_published_bundle_without_live_staging_action(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "Hero.blend")
            imported = {
                "action_name": "Idle",
                "source_file": os.path.join(directory, "Idle.ueanim"),
                "source_manifest_ref": "Hero/Moves/Idle",
                "staged_blend": os.path.join(directory, "deleted-staging.blend"),
            }
            bundle = types.SimpleNamespace(
                blend_file="Hero.blend",
                processed_sources=["Hero::Idle.ueanim"],
                completion_state="completed",
            )
            stage = self.stages.LibraryBuildingStage()
            source_key = stage._source_key_for_imported(imported, directory)

            resolved = stage._published_bundle_for_source(
                directory, types.SimpleNamespace(bundles=[bundle]), source_key
            )

            self.assertEqual(source_key, "Hero::Idle.ueanim")
            self.assertEqual(resolved, os.path.normcase(os.path.abspath(blend_path)))

    def test_import_resume_loads_only_bundle_with_authoritative_source_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "Hero.blend")
            imported = {"action_name": "Idle", "source_file": os.path.join(directory, "Idle.ueanim"),
                        "source_manifest_ref": "Hero/Moves/Idle", "source_bundle": destination}
            bundle = types.SimpleNamespace(blend_file="Hero.blend", processed_sources=["Hero::Idle.ueanim"],
                                           completion_state="completed")
            manifest = types.SimpleNamespace(bundles=[bundle])
            context = FakeContext({}, {"output_dir": directory, "source_dir": directory})
            stage = self.stages.AssetImportStage()
            with (
                mock.patch.dict(stage._restore_published_actions.__globals__, {
                    "load_complete_v2_manifest": mock.Mock(return_value=manifest),
                    "recover_library_transaction": mock.Mock(),
                }),
                mock.patch.object(self.stages.LibraryBuildingStage, "_reload_actions_from_blends") as reload,
            ):
                stage._restore_published_actions(context, [imported])
                reload.assert_called_once_with(directory, manifest, [imported])
                reload.reset_mock()
                imported["source_bundle"] = os.path.join(directory, "Other.blend")
                with self.assertRaisesRegex(ValueError, "断点动作不属于"):
                    stage._restore_published_actions(context, [imported])
                reload.assert_not_called()

    def test_force_rebuild_passes_obsolete_cleanup_into_transaction_before_stage_success(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        publish_call = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "publish_rebuild_replacement_set"
        )

        self.assertEqual(len(publish_call.args), 5)
        self.assertIsInstance(publish_call.args[3], ast.Name)
        self.assertEqual(publish_call.args[3].id, "obsolete_cleanup_paths")
        self.assertEqual(ast.unparse(publish_call.args[4]), "()")
        self.assertIn("not build_failed", ast.unparse(execute))

    def test_force_rebuild_does_not_remove_generated_actions_before_replacement_commit(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            source = handle.read()
        module = ast.parse(source)
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        remove_calls = [node for node in ast.walk(execute)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "remove"]
        replacement_publish = next(node for node in ast.walk(execute)
                                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                                   and node.func.id == "publish_rebuild_replacement_set")
        action_removals = [node for node in remove_calls
                           if "bpy.data.actions" in ast.unparse(node)]
        self.assertTrue(action_removals)
        unload_loop = next(node for node in ast.walk(execute)
                           if isinstance(node, ast.For) and ast.unparse(node.target) == "act")
        guard = unload_loop.body[0]
        self.assertEqual(ast.unparse(guard.test), "force_rebuild")
        self.assertIsInstance(guard.body[0], ast.Continue)
        self.assertLess(guard.lineno, replacement_publish.lineno)

    def test_force_rebuild_removes_owned_generated_actions_only_postcommit(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        publish_call = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "publish_rebuild_replacement_set"
        )
        postcommit_removals = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "remove"
            and "bpy.data.actions" in ast.unparse(node)
            and node.lineno > publish_call.lineno
        ]

        self.assertTrue(postcommit_removals)
        self.assertIn("Pipeline_Owned", ast.unparse(execute))

    def test_first_v2_migration_precedes_transaction_and_noop_can_publish_index(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        migrate_call = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "migrate_verified_legacy_manifest"
        )
        prepare_call = next(
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "prepare_library_transaction"
        )
        publish_calls = [
            node for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "publish_rebuild_replacement_set"
        ]

        self.assertLess(migrate_call.lineno, prepare_call.lineno)
        self.assertTrue(any(ast.unparse(call.args[1]) == "[]" for call in publish_calls))

    def test_staging_does_not_resolve_task_actions_by_global_name(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            source = handle.read()
        module = ast.parse(source)
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        self.assertNotIn("bpy.data.actions.get(ia.action_name)", ast.unparse(execute))
        self.assertNotIn("bpy.data.actions.get(ia.action_name)", ast.unparse(execute))

    def test_force_rebuild_prepares_thumbnail_artifact_before_thumbnail_generation(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("persist_transaction_artifact(manifest_path, thumb_path)", source)
        self.assertLess(source.index("persist_transaction_artifact(manifest_path, thumb_path)"),
                        source.index("utils_asset.bake_thumbnail_opengl"))

    def test_dynamic_thumbnail_artifact_is_persisted_before_generation(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            source = handle.read()
        module = ast.parse(source)
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        execute_source = ast.unparse(execute)

        self.assertIn("persist_transaction_artifact(manifest_path, thumb_path)", execute_source)
        self.assertLess(
            execute_source.index("persist_transaction_artifact(manifest_path, thumb_path)"),
            execute_source.index("utils_asset.bake_thumbnail_opengl"),
        )

    def test_library_build_recovers_index_before_authoritative_reload(self):
        stages_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "stages.py")
        with open(stages_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        stage = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
        execute = next(node for node in stage.body
                       if isinstance(node, ast.FunctionDef) and node.name == "execute")
        source = ast.unparse(execute)

        self.assertLess(source.index("recover_library_transaction(manifest_path)"),
                        source.index("self._reload_actions_from_blends(output_dir, manifest, imported_actions)"))

    def test_library_build_fails_closed_when_transaction_recovery_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = types.ModuleType("addon.Assets"); assets.__path__ = []
            utils = types.ModuleType("addon.Assets.Utils"); utils.__path__ = []
            utils_asset = types.ModuleType("addon.Assets.Utils.utils_asset")
            sys.modules.update({
                "addon.Assets": assets,
                "addon.Assets.Utils": utils,
                "addon.Assets.Utils.utils_asset": utils_asset,
            })
            context = FakeContext({}, {
                "output_dir": directory, "library_name": "Test",
            })
            context.imported_assets = {"imported_actions": [{
                "action_name": "Idle", "source_manifest_ref": "Hero/Moves/Idle",
            }]}

            with mock.patch.object(
                self.stages, "recover_library_transaction", side_effect=OSError("journal locked")
            ):
                self.assertFalse(self.stages.LibraryBuildingStage().execute(context))

            self.assertTrue(any("journal locked" in message for _, message in context.logs))

    def test_library_reload_requests_same_named_action_from_each_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            loads = []
            class ReloadLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    loads.append(self.path)
                    return types.SimpleNamespace(actions=["Idle"]), self.data_to
                def __exit__(self, *args):
                    pass
            self.bpy.data.libraries.load = lambda path, link: ReloadLoad(self.actions, [], path)
            manifest = types.SimpleNamespace(bundles=[
                types.SimpleNamespace(blend_file="First.blend"),
                types.SimpleNamespace(blend_file="Second.blend"),
            ])

            self.stages.LibraryBuildingStage()._reload_actions_from_blends(directory, manifest)

            self.assertEqual(len(loads), 2)

    def test_persisted_link_actions_resolve_after_destination_bundle_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = os.path.join(directory, "First.blend")
            second_path = os.path.join(directory, "Second.blend")
            persisted = json.loads(json.dumps([
                {"action_name": "Idle", "source_bundle": first_path,
                 "source_manifest_ref": "Hero/Moves/Idle"},
                {"action_name": "Idle", "source_bundle": second_path,
                 "source_manifest_ref": "Enemy/Moves/Idle"},
            ]))

            class ReloadLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    return types.SimpleNamespace(actions=["Idle"]), self.data_to

                def __exit__(self, *args):
                    self.data_to.actions = [FakeAction(name) for name in self.data_to.actions]
                    self.target_actions.extend(self.data_to.actions)

            self.bpy.data.libraries.load = lambda path, link: ReloadLoad(self.actions, [], path)
            manifest = types.SimpleNamespace(bundles=[
                types.SimpleNamespace(blend_file="First.blend"),
                types.SimpleNamespace(blend_file="Second.blend"),
            ])

            self.stages.LibraryBuildingStage()._reload_actions_from_blends(directory, manifest)
            first, first_error = self.stages.resolve_imported_action(self.bpy, persisted[0])
            second, second_error = self.stages.resolve_imported_action(self.bpy, persisted[1])

            self.assertEqual((first_error, second_error), ("", ""))
            self.assertIsNot(first, second)
            self.assertEqual(first["Pipeline_SourceBundle"], os.path.normcase(os.path.abspath(first_path)))
            self.assertEqual(second["Pipeline_SourceBundle"], os.path.normcase(os.path.abspath(second_path)))

    def test_bundle_reload_maps_requested_name_to_blender_suffixed_datablock(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "First.blend")
            existing = FakeAction("Idle")
            self.actions.append(existing)

            class SuffixedReloadLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    return types.SimpleNamespace(actions=["Idle"]), self.data_to

                def __exit__(self, *args):
                    requested = list(self.data_to.actions)
                    # Real Blender mutates the caller's assigned list in place.
                    self.data_to.actions[:] = [FakeAction(f"{name}.001") for name in requested]
                    self.target_actions.extend(self.data_to.actions)

            self.bpy.data.libraries.load = lambda path, link: SuffixedReloadLoad(
                self.actions, [], path
            )
            manifest = types.SimpleNamespace(bundles=[
                types.SimpleNamespace(blend_file="First.blend"),
            ])

            mapping = self.stages.LibraryBuildingStage()._reload_actions_from_blends(
                directory, manifest
            )
            selected, error = self.stages.resolve_imported_action(self.bpy, {
                "action_name": "Idle", "source_bundle": blend_path,
            })

            self.assertEqual(error, "")
            self.assertIs(selected, mapping[os.path.normcase(os.path.abspath(blend_path))]["Idle"])
            self.assertEqual(selected.name, "Idle.001")
            self.assertEqual(selected["Pipeline_RequestedActionName"], "Idle")
            self.assertEqual(selected["Pipeline_SourceBundleActionName"], "Idle")

    def test_bundle_reload_is_idempotent_and_cleans_replaced_staged_action(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "First.blend")
            staged_path = os.path.join(directory, "staged.blend")
            staged = FakeAction("Idle")
            staged["Pipeline_SourceRef"] = "Hero/Moves/Idle"
            staged["Pipeline_StagedBlend"] = os.path.normcase(os.path.abspath(staged_path))
            self.actions.append(staged)
            loads = []

            class ReloadLoad(FakeLibraryLoad):
                def __enter__(self):
                    self.data_to = types.SimpleNamespace(actions=[])
                    loads.append(self.path)
                    return types.SimpleNamespace(actions=["Idle"]), self.data_to

                def __exit__(self, *args):
                    self.data_to.actions = [FakeAction(name) for name in self.data_to.actions]
                    self.target_actions.extend(self.data_to.actions)

            self.bpy.data.libraries.load = lambda path, link: ReloadLoad(self.actions, [], path)
            manifest = types.SimpleNamespace(bundles=[
                types.SimpleNamespace(blend_file="First.blend"),
            ])
            imported = [{
                "action_name": "Idle",
                "source_bundle": blend_path,
                "source_manifest_ref": "Hero/Moves/Idle",
                "staged_blend": staged_path,
            }]
            stage = self.stages.LibraryBuildingStage()

            first = stage._reload_actions_from_blends(directory, manifest, imported)
            second = stage._reload_actions_from_blends(directory, manifest, imported)
            normalized = os.path.normcase(os.path.abspath(blend_path))
            selected, error = self.stages.resolve_imported_action(self.bpy, imported[0])

            self.assertEqual(loads, [blend_path])
            self.assertIs(first[normalized]["Idle"], second[normalized]["Idle"])
            self.assertIs(selected, first[normalized]["Idle"])
            self.assertEqual(error, "")
            self.assertEqual(selected["Pipeline_SourceRef"], "Hero/Moves/Idle")
            self.assertNotIn(staged, self.actions)


if __name__ == "__main__":
    unittest.main()
