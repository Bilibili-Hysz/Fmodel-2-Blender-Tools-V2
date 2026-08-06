"""Fake-Blender contracts for the legacy full-pipeline entry point."""

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock


class FakeText:
    def __init__(self):
        self.value = ""

    def clear(self):
        self.value = ""

    def write(self, value):
        self.value += value

    def as_string(self):
        return self.value


class FakeTexts(dict):
    def new(self, name):
        text = FakeText()
        self[name] = text
        return text


class FakeObjects(list):
    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


class PipelineManagerFullEntryTests(unittest.TestCase):
    def setUp(self):
        self.output_temp = tempfile.TemporaryDirectory()
        self.output_dir = self.output_temp.name
        module_names = (
            "bpy", "addon", "addon.core", "addon.Fmodel",
            "addon.Fmodel.Pipeline", "addon.Fmodel.Pipeline.stages",
            "addon.Fmodel.Pipeline.manager",
        )
        self.saved_modules = {name: sys.modules.get(name) for name in module_names}
        self.bpy = types.ModuleType("bpy")
        self.bpy.types = types.SimpleNamespace(Operator=object, PropertyGroup=object, Scene=type("Scene", (), {}))
        self.bpy.props = types.SimpleNamespace(
            StringProperty=lambda **kwargs: None,
            BoolProperty=lambda **kwargs: None,
            FloatProperty=lambda **kwargs: None,
            EnumProperty=lambda **kwargs: None,
            PointerProperty=lambda **kwargs: None,
        )
        self.bpy.data = types.SimpleNamespace(
            texts=FakeTexts(),
            objects=FakeObjects([
                types.SimpleNamespace(name="HeroRig", type="ARMATURE"),
                types.SimpleNamespace(name="EnemyRig", type="ARMATURE"),
            ]),
        )
        sys.modules["bpy"] = self.bpy

        self.core = importlib.import_module("core")
        self.core.context.bpy = self.bpy
        addon = types.ModuleType("addon")
        addon.__path__ = []
        fmodel = types.ModuleType("addon.Fmodel")
        fmodel.__path__ = []
        pipeline = types.ModuleType("addon.Fmodel.Pipeline")
        pipeline.__path__ = []
        stages = types.ModuleType("addon.Fmodel.Pipeline.stages")
        for name in (
            "DictionaryAcquisitionStage", "FormatConversionStage", "AssetImportStage",
            "LibraryBuildingStage", "FinalizationStage",
        ):
            setattr(stages, name, type(name, (), {"run": lambda self, context: True}))
        sys.modules.update({
            "addon": addon,
            "addon.core": self.core,
            "addon.Fmodel": fmodel,
            "addon.Fmodel.Pipeline": pipeline,
            "addon.Fmodel.Pipeline.stages": stages,
        })
        manager_path = os.path.join(os.path.dirname(__file__), "Fmodel", "Pipeline", "manager.py")
        spec = importlib.util.spec_from_file_location("addon.Fmodel.Pipeline.manager", manager_path)
        self.manager_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.manager_module
        spec.loader.exec_module(self.manager_module)

    def tearDown(self):
        self.output_temp.cleanup()
        for name, module in self.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_full_entry_runs_two_skeleton_plan_as_isolated_tasks(self):
        manifest = {"skeletons": {
            "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": "Hero/Idle.ueanim"}}}},
            "Enemy": {"Moves": {"__animations__": {"Idle": {"source_file": "Enemy/Idle.ueanim"}}}},
        }}
        plan, _ = self.core.build_multi_import_plan(
            manifest, {"Hero": "HeroRig", "Enemy": "EnemyRig"}
        )
        context = self.core.PipelineContext(
            dictionary_manifest=manifest,
            multi_import_plan=plan.to_dict(),
            multi_import_checkpoint=self.core.MultiImportCheckpoint(plan=plan).to_dict(),
        )
        calls = []
        saves = []
        manager = self.manager_module.PipelineManager()

        def run_stage(pipeline_context, stage_name):
            calls.append((
                stage_name,
                pipeline_context.get_config("source_skeleton", ""),
                pipeline_context.get_config("target_armature", ""),
                tuple(pipeline_context.get_config("source_subset", [])),
            ))
            return True

        manager.run_stage = run_stage
        context.save_to_blender_text = lambda: saves.append(context.to_dict())

        self.assertTrue(manager.run_full_pipeline(context))
        self.assertEqual(calls, [
            (self.core.PipelineStage.DICTIONARY_ACQUISITION.value, "", "", ()),
            (self.core.PipelineStage.FORMAT_CONVERSION.value, "", "", ()),
            (self.core.PipelineStage.ASSET_IMPORT.value, "Hero", "HeroRig", ("Hero/Idle.ueanim",)),
            (self.core.PipelineStage.LIBRARY_BUILDING.value, "Hero", "HeroRig", ("Hero/Idle.ueanim",)),
            (self.core.PipelineStage.ASSET_IMPORT.value, "Enemy", "EnemyRig", ("Enemy/Idle.ueanim",)),
            (self.core.PipelineStage.LIBRARY_BUILDING.value, "Enemy", "EnemyRig", ("Enemy/Idle.ueanim",)),
            (self.core.PipelineStage.FINALIZATION.value, "", "", ()),
        ])
        self.assertGreaterEqual(len(saves), 4)
        self.assertEqual(
            [task["status"] for task in context.multi_import_checkpoint["plan"]["tasks"]],
            ["completed", "completed"],
        )

    def test_operator_without_plan_keeps_single_legacy_route(self):
        calls = []

        class FakeManager:
            def create_context(self, config=None):
                calls.append(("create", config))
                return self_context

            def run_full_pipeline(self, context):
                calls.append(("run", context.multi_import_plan))
                return True

        self_context = self.core.PipelineContext(config={"output_dir": self.output_dir})
        self.manager_module.PipelineManager = FakeManager
        props = types.SimpleNamespace(
            source_dir="C:/Anims", output_dir=self.output_dir, library_name="Legacy",
            source_type="fmodel_ueanim", import_mode="ueanim_direct",
            unity_skip_processed=True, import_scale=0.01, rotation_only=False,
            generate_thumbnails=False,
        )
        operator = self.manager_module.PIPELINE_OT_RunFullPipeline()
        operator.report = lambda *args: None

        result = operator.execute(types.SimpleNamespace(scene=types.SimpleNamespace(pipeline_props=props)))

        self.assertEqual(result, {"FINISHED"})
        self.assertEqual([call[0] for call in calls], ["create", "run"])
        self.assertIsNone(calls[1][1])

    def test_full_operator_reports_and_cancels_checkpoint_load_error(self):
        props = types.SimpleNamespace(
            source_dir="C:/Anims", output_dir=self.output_dir, library_name="Legacy",
            source_type="fmodel_ueanim", import_mode="ueanim_direct",
            unity_skip_processed=True, import_scale=0.01, rotation_only=False,
            generate_thumbnails=False,
        )
        reports = []
        operator = self.manager_module.PIPELINE_OT_RunFullPipeline()
        operator.report = lambda level, message: reports.append((level, message))

        with mock.patch.object(
            self.core.PipelineContext, "load_from_checkpoint",
            side_effect=OSError("checkpoint unreadable"),
        ):
            result = operator.execute(
                types.SimpleNamespace(scene=types.SimpleNamespace(pipeline_props=props))
            )

        self.assertEqual(result, {"CANCELLED"})
        self.assertTrue(any("checkpoint unreadable" in message for _, message in reports))

    def test_stage_operator_reports_and_cancels_operational_error(self):
        props = types.SimpleNamespace(
            source_dir="C:/Anims", output_dir=self.output_dir, library_name="Legacy",
            source_type="fmodel_ueanim", import_mode="ueanim_direct",
        )
        reports = []
        operator = self.manager_module.PIPELINE_OT_RunStage()
        operator.stage_name = self.core.PipelineStage.ASSET_IMPORT.value
        operator.report = lambda level, message: reports.append((level, message))

        with mock.patch.object(
            self.core.PipelineContext, "load_from_checkpoint",
            side_effect=OSError("checkpoint unreadable"),
        ):
            result = operator.execute(
                types.SimpleNamespace(scene=types.SimpleNamespace(pipeline_props=props))
            )

        self.assertEqual(result, {"CANCELLED"})
        self.assertTrue(any("checkpoint unreadable" in message for _, message in reports))

    def test_full_entry_rejects_plan_with_stale_target_mapping(self):
        manifest = {"skeletons": {
            "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": "Hero/Idle.ueanim"}}}},
        }}
        plan, _ = self.core.build_multi_import_plan(manifest, {"Hero": "DeletedRig"})
        context = self.core.PipelineContext(
            dictionary_manifest=manifest,
            multi_import_plan=plan.to_dict(),
        )
        calls = []
        manager = self.manager_module.PipelineManager()
        manager.run_stage = lambda pipeline_context, stage_name: calls.append(stage_name) or True

        self.assertFalse(manager.run_full_pipeline(context))
        self.assertEqual(calls, [
            self.core.PipelineStage.DICTIONARY_ACQUISITION.value,
            self.core.PipelineStage.FORMAT_CONVERSION.value,
        ])
        self.assertTrue(any("DeletedRig" in log["message"] for log in context.logs))

    def test_full_entry_resumes_at_pending_task(self):
        manifest = {"skeletons": {
            "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": "Hero/Idle.ueanim"}}}},
            "Enemy": {"Moves": {"__animations__": {"Idle": {"source_file": "Enemy/Idle.ueanim"}}}},
        }}
        plan, _ = self.core.build_multi_import_plan(
            manifest, {"Hero": "HeroRig", "Enemy": "EnemyRig"}
        )
        plan.tasks[0].status = "completed"
        plan.tasks[0].progress = 1.0
        context = self.core.PipelineContext(
            dictionary_manifest=manifest,
            multi_import_plan=plan.to_dict(),
            multi_import_checkpoint=self.core.MultiImportCheckpoint(
                plan=plan, current_task_index=1
            ).to_dict(),
        )
        calls = []
        manager = self.manager_module.PipelineManager()
        manager.run_stage = lambda pipeline_context, stage_name: calls.append((
            stage_name, pipeline_context.get_config("source_skeleton", "")
        )) or True
        context.save_to_blender_text = lambda: None

        self.assertTrue(manager.run_full_pipeline(context))
        self.assertNotIn((self.core.PipelineStage.ASSET_IMPORT.value, "Hero"), calls)
        self.assertIn((self.core.PipelineStage.ASSET_IMPORT.value, "Enemy"), calls)

    def test_pending_plan_task_invalidates_completed_finalization(self):
        manifest = {"skeletons": {
            "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": "Hero/Idle.ueanim"}}}},
        }}
        plan, _ = self.core.build_multi_import_plan(manifest, {"Hero": "HeroRig"})
        context = self.core.PipelineContext(
            dictionary_manifest=manifest,
            multi_import_plan=plan.to_dict(),
            multi_import_checkpoint=self.core.MultiImportCheckpoint(plan=plan).to_dict(),
        )
        context.stage_status[self.core.PipelineStage.FINALIZATION.value] = "completed"
        calls = []
        manager = self.manager_module.PipelineManager()
        manager.run_stage = lambda pipeline_context, stage_name: calls.append(stage_name) or True
        context.save_to_blender_text = lambda: None

        self.assertTrue(manager.run_full_pipeline(context))
        self.assertEqual(calls[-1], self.core.PipelineStage.FINALIZATION.value)

    def test_operator_discards_legacy_plan_without_resume_identities(self):
        manifest = {"skeletons": {
            "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": "Hero/Idle.ueanim"}}}},
            "Enemy": {"Moves": {"__animations__": {"Idle": {"source_file": "Enemy/Idle.ueanim"}}}},
        }}
        plan, _ = self.core.build_multi_import_plan(
            manifest, {"Hero": "HeroRig", "Enemy": "EnemyRig"}
        )
        saved = self.core.PipelineContext(
            config={"output_dir": self.output_dir},
            dictionary_manifest=manifest,
            multi_import_plan=plan.to_dict(),
        )
        saved.save_to_blender_text()
        calls = []

        class FakeManager:
            def create_context(self, config=None):
                self.fail("explicit plan should restore its context")

            def run_full_pipeline(self, context):
                calls.append(context)
                return True

        self.manager_module.PipelineManager = FakeManager
        props = types.SimpleNamespace(
            source_dir="C:/Anims", output_dir=self.output_dir, library_name="Mapped",
            source_type="fmodel_ueanim", import_mode="ueanim_direct",
            unity_skip_processed=True, import_scale=0.01, rotation_only=False,
            generate_thumbnails=False,
        )
        operator = self.manager_module.PIPELINE_OT_RunFullPipeline()
        operator.report = lambda *args: None

        result = operator.execute(types.SimpleNamespace(scene=types.SimpleNamespace(pipeline_props=props)))

        self.assertEqual(result, {"FINISHED"})
        self.assertIsNone(calls[0].multi_import_plan)
        self.assertEqual(calls[0].get_config("source_dir"), "C:/Anims")

    def test_operator_prefers_output_checkpoint_over_blender_text(self):
        with tempfile.TemporaryDirectory() as output_dir:
            self.core.PipelineContext(
                config=self.core.PipelineConfig(
                    source_dir="C:/Anims", output_dir=output_dir, library_name="Mapped",
                    source_type="fmodel_ueanim", import_mode="ueanim_direct",
                    unity_skip_processed=True, import_scale=0.01, rotation_only=False,
                    generate_thumbnails=False,
                ).to_dict(),
                imported_assets={"source": "external"},
            ).persist_checkpoint()
            self.core.PipelineContext(
                config={"output_dir": output_dir},
                imported_assets={"source": "blender"},
            ).save_to_blender_text()
            captured = []

            class FakeManager:
                def create_context(self, config=None):
                    raise AssertionError("external checkpoint should be restored")

                def run_full_pipeline(self, context):
                    captured.append(context)
                    return True

            self.manager_module.PipelineManager = FakeManager
            props = types.SimpleNamespace(
                source_dir="C:/Anims", output_dir=output_dir, library_name="Mapped",
                source_type="fmodel_ueanim", import_mode="ueanim_direct",
                unity_skip_processed=True, import_scale=0.01, rotation_only=False,
                generate_thumbnails=False,
            )
            operator = self.manager_module.PIPELINE_OT_RunFullPipeline()
            operator.report = lambda *args: None

            self.assertEqual(
                operator.execute(types.SimpleNamespace(scene=types.SimpleNamespace(pipeline_props=props))),
                {"FINISHED"},
            )
            self.assertEqual(captured[0].imported_assets["source"], "external")

    def test_asset_operator_restores_output_context_and_applies_pipeline_config(self):
        asset_path = os.path.join(
            os.path.dirname(__file__), "Assets", "Anims", "operators_asset.py"
        )
        with open(asset_path, encoding="utf-8") as handle:
            source = handle.read()

        external_call = source.index("PipelineContext.load_from_checkpoint(out_dir)")
        fallback_call = source.index(
            "PipelineContext.load_from_blender_text(output_dir=out_dir)", external_call
        )
        new_context = source.index("PipelineContext(config=cfg)", fallback_call)
        apply_config = source.index("pipeline_context.apply_pipeline_config(cfg)", new_context)

        self.assertLess(external_call, fallback_call)
        self.assertLess(fallback_call, new_context)
        self.assertLess(new_context, apply_config)
        self.assertNotIn("pipeline_context.config.update(cfg)", source)

    def test_wizard_scan_reads_context_for_selected_output_directory(self):
        scan_path = os.path.join(
            os.path.dirname(__file__), "Fmodel", "Pipeline", "steps", "step3_scan.py"
        )
        with open(scan_path, encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("PipelineContext.load_from_blender_text(", source)
        self.assertIn("output_dir=state.output_dir", source)

    def test_wizard_state_exposes_serializable_indexed_selection_fields(self):
        state_path = os.path.join(
            os.path.dirname(__file__), "Fmodel", "Pipeline", "steps", "state.py"
        )
        def property_factory(**kwargs):
            return kwargs

        fake_bpy = types.ModuleType("bpy")
        fake_bpy.types = types.SimpleNamespace(
            PropertyGroup=object, Object=object,
        )
        fake_bpy.props = types.SimpleNamespace(
            StringProperty=property_factory,
            BoolProperty=property_factory,
            FloatProperty=property_factory,
            IntProperty=property_factory,
            EnumProperty=property_factory,
            PointerProperty=property_factory,
            CollectionProperty=property_factory,
        )
        fake_bpy.utils = types.SimpleNamespace(
            register_class=lambda cls: None, unregister_class=lambda cls: None,
        )
        previous_bpy = sys.modules.get("bpy")
        sys.modules["bpy"] = fake_bpy
        try:
            spec = importlib.util.spec_from_file_location("test_wizard_state", state_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            if previous_bpy is None:
                sys.modules.pop("bpy", None)
            else:
                sys.modules["bpy"] = previous_bpy

        annotations = module.WizardState.__annotations__
        self.assertEqual(annotations["indexed_project_root"]["subtype"], "DIR_PATH")
        self.assertEqual(annotations["indexed_skeleton"]["default"], "")
        self.assertEqual(annotations["indexed_selected_paths"]["default"], "[]")

    def test_wizard_scan_legacy_state_runs_existing_stages(self):
        step3 = self._load_wizard_step3()
        calls = []
        manifest = {"skeletons": {
            "Hero": {"Base": {"__animations__": {
                "Idle": {"source_file": "Hero/Idle.ueanim"}
            }}}
        }}
        pipeline_context = self.core.PipelineContext(dictionary_manifest=manifest)
        step3.run_wizard_stage = lambda context, state, stage: calls.append(stage) or True
        state = self._wizard_state(source_mode="MANUAL")
        operator = step3.PIPELINE_OT_WizardStep3()
        operator.report = lambda *args: None

        original_loader = self.core.PipelineContext.__dict__["load_from_blender_text"]
        with mock.patch.object(
            step3.PipelineContext,
            "load_from_blender_text",
            new=classmethod(lambda cls, **kwargs: pipeline_context),
        ):
            self.assertEqual(
                operator.execute(types.SimpleNamespace(
                    scene=types.SimpleNamespace(pipeline_wizard=state), active_object=None
                )),
                {"FINISHED"},
            )

        self.assertIs(self.core.PipelineContext.__dict__["load_from_blender_text"], original_loader)
        self.assertEqual(calls, [
            self.core.PipelineStage.DICTIONARY_ACQUISITION.value,
            self.core.PipelineStage.FORMAT_CONVERSION.value,
        ])
        self.assertEqual(state.scan_anim_count, 1)

    def test_wizard_scan_manual_mode_ignores_auto_project_selection(self):
        step3 = self._load_wizard_step3()
        calls = []
        manifest = {"skeletons": {
            "Hero": {"Base": {"__animations__": {
                "Idle": {"source_file": "Hero/Idle.ueanim"}
            }}}
        }}
        pipeline_context = self.core.PipelineContext(dictionary_manifest=manifest)
        step3.run_wizard_stage = lambda context, state, stage: calls.append(stage) or True
        state = self._wizard_state(
            source_mode="MANUAL", indexed_project_root="C:/Project",
            indexed_skeleton="Hero", indexed_selected_paths='["C:/Project/Hero/Idle.ueanim"]',
            indexed_selection_mode="AUTO_PROJECT",
        )
        operator = step3.PIPELINE_OT_WizardStep3()
        operator.report = lambda *args: None

        with mock.patch.object(
            step3.PipelineContext,
            "load_from_blender_text",
            new=classmethod(lambda cls, **kwargs: pipeline_context),
        ):
            self.assertEqual(operator.execute(types.SimpleNamespace(
                scene=types.SimpleNamespace(pipeline_wizard=state), active_object=None,
            )), {"FINISHED"})

        self.assertEqual(calls, [
            self.core.PipelineStage.DICTIONARY_ACQUISITION.value,
            self.core.PipelineStage.FORMAT_CONVERSION.value,
        ])
        self.assertEqual(state.indexed_selection_mode, "NONE")
        self.assertEqual(state.indexed_selected_paths, "[]")

    def test_wizard_scan_indexed_state_projects_manifest_without_raw_stages(self):
        step3 = self._load_wizard_step3()
        with tempfile.TemporaryDirectory() as project_dir:
            root = os.path.join(project_dir, "project")
            animation = os.path.join(root, "Hero", "Moves", "Run.ueanim")
            os.makedirs(os.path.dirname(animation))
            with open(animation, "wb") as handle:
                handle.write(b"animation")
            index = importlib.import_module("core.project_index").ProjectIndex(root)
            index.scan([root])
            step3.run_wizard_stage = mock.Mock(side_effect=AssertionError("raw stages must not run"))
            state = self._wizard_state(
                output_dir=self.output_dir,
                indexed_project_root=root,
                indexed_skeleton="Hero",
                indexed_selected_paths=json.dumps([animation]),
            )
            operator = step3.PIPELINE_OT_WizardStep3()
            operator.report = lambda *args: None

            self.assertEqual(
                operator.execute(types.SimpleNamespace(
                    scene=types.SimpleNamespace(pipeline_wizard=state), active_object=None
                )),
                {"FINISHED"},
            )

        self.assertFalse(step3.run_wizard_stage.called)
        self.assertEqual(state.scan_skeleton_count, 1)
        self.assertEqual(state.scan_anim_count, 1)
        self.assertEqual(
            self.core.PipelineContext.load_from_checkpoint(self.output_dir)
            .dictionary_manifest["skeletons"]["Hero"]["Moves"]
            ["__animations__"]["Run"]["source_file"],
            os.path.abspath(animation),
        )
        self.assertIn("PipelineContext", self.bpy.data.texts)

    def test_project_default_step3_builds_manifest_without_directory_stages(self):
        step3 = self._load_wizard_step3()
        with tempfile.TemporaryDirectory() as project_dir:
            root = os.path.join(project_dir, "project")
            animation = os.path.join(root, "Hero", "Moves", "Run.ueanim")
            os.makedirs(os.path.dirname(animation))
            with open(animation, "wb") as handle:
                handle.write(b"animation")
            importlib.import_module("core.project_index").ProjectIndex(root).scan([root])
            step3.run_wizard_stage = mock.Mock(
                side_effect=AssertionError("raw stages must not run")
            )
            state = self._wizard_state(
                source_mode="PROJECT", source_dir=root, output_dir=self.output_dir,
                skeleton_filter="Hero",
            )
            project = types.SimpleNamespace(root_path=root, index_state="ready")
            operator = step3.PIPELINE_OT_WizardStep3()
            operator.report = lambda *args: None

            self.assertEqual(
                operator.execute(types.SimpleNamespace(
                    scene=types.SimpleNamespace(
                        pipeline_wizard=state, fmodel_project=project,
                    ),
                    active_object=None,
                )),
                {"FINISHED"},
            )

        saved = self.core.PipelineContext.load_from_checkpoint(self.output_dir)
        self.assertFalse(step3.run_wizard_stage.called)
        self.assertEqual(saved.config["indexed_project_root"], root)
        self.assertEqual(saved.config["indexed_skeleton"], "Hero")
        self.assertEqual(saved.config["indexed_selected_paths"], [os.path.abspath(animation)])
        self.assertEqual(state.indexed_project_root, root)
        self.assertEqual(state.indexed_skeleton, "Hero")
        self.assertEqual(state.indexed_selected_paths, json.dumps([os.path.abspath(animation)]))

    def test_project_default_step3_reports_project_settings_scan_error_without_stages(self):
        step3 = self._load_wizard_step3()
        step3.run_wizard_stage = mock.Mock(
            side_effect=AssertionError("raw stages must not run")
        )
        step3._project_default_indexed_selection = mock.Mock(
            side_effect=ValueError("Project Settings Scan is required")
        )
        state = self._wizard_state(source_mode="PROJECT", skeleton_filter="Hero")
        reports = []
        operator = step3.PIPELINE_OT_WizardStep3()
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(
            operator.execute(types.SimpleNamespace(
                scene=types.SimpleNamespace(
                    pipeline_wizard=state, fmodel_project=object(),
                ),
                active_object=None,
            )),
            {"CANCELLED"},
        )

        self.assertFalse(step3.run_wizard_stage.called)
        self.assertTrue(any("Project Settings" in message and "Scan" in message
                            for _, message in reports))

    def test_project_default_step3_rejects_stale_indexed_file_without_stages(self):
        step3 = self._load_wizard_step3()
        with tempfile.TemporaryDirectory() as project_dir:
            root = os.path.join(project_dir, "project")
            animation = os.path.join(root, "Hero", "Moves", "Run.ueanim")
            os.makedirs(os.path.dirname(animation))
            with open(animation, "wb") as handle:
                handle.write(b"animation")
            importlib.import_module("core.project_index").ProjectIndex(root).scan([root])
            os.remove(animation)
            step3.run_wizard_stage = mock.Mock(
                side_effect=AssertionError("raw stages must not run")
            )
            state = self._wizard_state(
                source_mode="PROJECT", source_dir=root, skeleton_filter="Hero",
            )
            reports = []
            operator = step3.PIPELINE_OT_WizardStep3()
            operator.report = lambda level, message: reports.append((level, message))

            self.assertEqual(
                operator.execute(types.SimpleNamespace(
                    scene=types.SimpleNamespace(
                        pipeline_wizard=state,
                        fmodel_project=types.SimpleNamespace(
                            root_path=root, index_state="ready",
                        ),
                    ),
                    active_object=None,
                )),
                {"CANCELLED"},
            )

        self.assertFalse(step3.run_wizard_stage.called)
        self.assertTrue(any("Project Settings" in message and "Scan" in message
                            for _, message in reports))

    def test_wizard_scan_partial_indexed_state_errors_without_raw_stages(self):
        step3 = self._load_wizard_step3()
        step3.run_wizard_stage = mock.Mock(side_effect=AssertionError("raw stages must not run"))
        state = self._wizard_state(
            output_dir=self.output_dir,
            indexed_project_root="C:/Project",
            indexed_skeleton="Hero",
            indexed_selected_paths="{not json",
        )
        operator = step3.PIPELINE_OT_WizardStep3()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(
            operator.execute(types.SimpleNamespace(
                scene=types.SimpleNamespace(pipeline_wizard=state), active_object=None
            )),
            {"CANCELLED"},
        )

        self.assertFalse(step3.run_wizard_stage.called)
        self.assertTrue(any("索引选择" in message for _, message in reports))

    def test_wizard_scan_indexed_selection_reconciles_and_replaces_saved_manifest(self):
        step3 = self._load_wizard_step3()
        with tempfile.TemporaryDirectory() as project_dir:
            root = os.path.join(project_dir, "project")
            animation_a = os.path.join(root, "Hero", "Moves", "Walk.ueanim")
            animation_b = os.path.join(root, "Hero", "Moves", "Run.ueanim")
            os.makedirs(os.path.dirname(animation_a))
            for animation in (animation_a, animation_b):
                with open(animation, "wb") as handle:
                    handle.write(b"animation")
            importlib.import_module("core.project_index").ProjectIndex(root).scan([root])
            operator = step3.PIPELINE_OT_WizardStep3()
            operator.report = lambda *args: None
            step3.run_wizard_stage = mock.Mock(side_effect=AssertionError("raw stages must not run"))

            first = self._wizard_state(
                output_dir=self.output_dir,
                indexed_project_root=root,
                indexed_skeleton="Hero",
                indexed_selected_paths=json.dumps([animation_a]),
            )
            second = self._wizard_state(
                output_dir=self.output_dir,
                indexed_project_root=root,
                indexed_skeleton="Hero",
                indexed_selected_paths=json.dumps([animation_b]),
            )
            context = types.SimpleNamespace(
                scene=types.SimpleNamespace(pipeline_wizard=first), active_object=None,
            )
            self.assertEqual(operator.execute(context), {"FINISHED"})
            context.scene.pipeline_wizard = second
            self.assertEqual(operator.execute(context), {"FINISHED"})

        saved = self.core.PipelineContext.load_from_checkpoint(self.output_dir)
        self.assertFalse(step3.run_wizard_stage.called)
        self.assertEqual(saved.config["indexed_selected_paths"], [os.path.abspath(animation_b)])
        animations = saved.dictionary_manifest["skeletons"]["Hero"]["Moves"]["__animations__"]
        self.assertEqual(set(animations), {"Run"})
        reloaded = self.core.PipelineContext.load_from_blender_text(output_dir=self.output_dir)
        self.assertEqual(reloaded.config["indexed_selected_paths"], [os.path.abspath(animation_b)])
        self.assertEqual(
            set(reloaded.dictionary_manifest["skeletons"]["Hero"]["Moves"]["__animations__"]),
            {"Run"},
        )

    def _load_wizard_step3(self):
        module_names = (
            "addon.core.project_index", "addon.Fmodel.Pipeline.steps",
            "addon.Fmodel.Pipeline.steps.runner",
            "addon.Fmodel.Pipeline.steps.step3_scan",
        )
        saved = {name: sys.modules.get(name) for name in module_names}
        self.addCleanup(self._restore_modules, saved)
        steps = types.ModuleType("addon.Fmodel.Pipeline.steps")
        steps.__path__ = []
        runner = types.ModuleType("addon.Fmodel.Pipeline.steps.runner")
        runner._build_config_from_wizard = lambda state, context: {
            "source_dir": state.source_dir,
            "output_dir": state.output_dir,
            "library_name": "AnimLibrary",
            "source_type": "fmodel_ueanim",
            "import_mode": "ueanim_direct",
            "import_resume_enabled": True,
        }
        def indexed_selection_from_state(state):
            mode = getattr(state, "indexed_selection_mode", "EXPLICIT")
            if mode == "NONE":
                return None
            if mode == "AUTO_PROJECT" and getattr(state, "source_mode", None) != "PROJECT":
                state.indexed_selection_mode = "NONE"
                state.indexed_project_root = ""
                state.indexed_skeleton = ""
                state.indexed_selected_paths = "[]"
                return None
            root = getattr(state, "indexed_project_root", "")
            skeleton = getattr(state, "indexed_skeleton", "")
            paths = getattr(state, "indexed_selected_paths", "[]")
            if root == "" and skeleton == "" and paths == "[]":
                return None
            if not isinstance(root, str) or not root:
                raise ValueError("indexed project root is required")
            if not isinstance(skeleton, str) or not skeleton:
                raise ValueError("indexed skeleton is required")
            if isinstance(paths, str):
                try:
                    paths = json.loads(paths)
                except json.JSONDecodeError as error:
                    raise ValueError("indexed selected paths must be valid JSON") from error
            if not isinstance(paths, list) or not paths:
                raise ValueError("indexed selected paths must be a non-empty list")
            return root, skeleton, paths

        runner._indexed_selection_from_state = indexed_selection_from_state
        runner._validate_auto_project_selection = lambda state, settings: (
            indexed_selection_from_state(state)
        )
        def project_default_indexed_selection(state, project_settings):
            if getattr(state, "source_mode", None) != "PROJECT":
                return None
            root = project_settings.root_path
            skeleton = state.skeleton_filter
            if project_settings.index_state != "ready":
                raise ValueError("Project Settings Scan is required")
            paths = importlib.import_module("core.project_index").ProjectIndex(root)
            paths = paths.animations_for_skeleton(skeleton, raise_on_error=True)
            if not paths:
                raise ValueError("Project Settings Scan found no animations")
            return root, skeleton, paths

        runner._project_default_indexed_selection = project_default_indexed_selection
        def load_or_create_context(config):
            pipeline_context = (
                self.core.PipelineContext.load_from_checkpoint(config["output_dir"])
                or self.core.PipelineContext.load_from_blender_text(output_dir=config["output_dir"])
            )
            if pipeline_context:
                pipeline_context.apply_pipeline_config(config)
                return pipeline_context
            return self.core.PipelineContext(config=config)

        runner._load_or_create_context = load_or_create_context
        runner.populate_skeleton_mappings = lambda state, context, active: None
        runner.run_wizard_stage = lambda context, state, stage: True
        sys.modules.update({
            "addon.core.project_index": importlib.import_module("core.project_index"),
            "addon.Fmodel.Pipeline.steps": steps,
            "addon.Fmodel.Pipeline.steps.runner": runner,
        })
        path = os.path.join(
            os.path.dirname(__file__), "Fmodel", "Pipeline", "steps", "step3_scan.py"
        )
        spec = importlib.util.spec_from_file_location(
            "addon.Fmodel.Pipeline.steps.step3_scan", path
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _restore_modules(saved):
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    @staticmethod
    def _wizard_state(**overrides):
        state = types.SimpleNamespace(
            source_dir="C:/Anims", output_dir="C:/Output", flow_type="FMODEL",
            scan_skeleton_count=0, scan_anim_count=0, scan_new_count=0,
            scan_existing_count=0, scan_completed=False,
        )
        for name, value in overrides.items():
            setattr(state, name, value)
        return state

    def test_operator_invalidates_completed_context_when_pipeline_config_changes(self):
        saved = self.core.PipelineContext(
            config={"source_dir": "C:/Old", "output_dir": self.output_dir, "library_name": "Old"},
            dictionary_manifest={"skeletons": {}},
            imported_assets={"imported_actions": [{"action_name": "Idle"}]},
            library_manifest={"assets": [{"action_name": "Idle"}]},
            multi_import_plan={"tasks": [{"source_skeleton": "Hero"}]},
            multi_import_checkpoint={"plan": {"tasks": [{"status": "completed"}]}},
            status=self.core.StageStatus.COMPLETED.value,
            current_stage=self.core.PipelineStage.FINALIZATION.value,
            stage_status={stage.value: self.core.StageStatus.COMPLETED.value
                          for stage in self.core.PipelineStage},
        )
        saved.save_to_blender_text()
        captured = []

        class FakeManager:
            def create_context(self, config=None):
                self.fail("saved context should be reused and reset")

            def run_full_pipeline(self, context):
                captured.append(context)
                return True

        self.manager_module.PipelineManager = FakeManager
        props = types.SimpleNamespace(
            source_dir="C:/New", output_dir=self.output_dir, library_name="Old",
            source_type="fmodel_ueanim", import_mode="ueanim_direct",
            unity_skip_processed=True, import_scale=0.01, rotation_only=False,
            generate_thumbnails=False,
        )
        operator = self.manager_module.PIPELINE_OT_RunFullPipeline()
        operator.report = lambda *args: None

        self.assertEqual(
            operator.execute(types.SimpleNamespace(scene=types.SimpleNamespace(pipeline_props=props))),
            {"FINISHED"},
        )
        reset = captured[0]
        self.assertIsNone(reset.dictionary_manifest)
        self.assertIsNone(reset.imported_assets)
        self.assertIsNone(reset.library_manifest)
        self.assertIsNone(reset.multi_import_plan)
        self.assertIsNone(reset.multi_import_checkpoint)
        self.assertEqual(reset.status, self.core.StageStatus.PENDING.value)
        self.assertTrue(all(status == self.core.StageStatus.PENDING.value
                            for status in reset.stage_status.values()))


if __name__ == "__main__":
    unittest.main()
