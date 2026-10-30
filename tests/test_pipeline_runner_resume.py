"""Runner-level fake-Blender contract for resuming mapped import tasks."""

import ast
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


class FakeMappings(list):
    def clear(self):
        del self[:]

    def add(self):
        item = types.SimpleNamespace(
            source_skeleton="", source_count=0, status="pending", target_armature=None
        )
        self.append(item)
        return item


class PipelineRunnerResumeTests(unittest.TestCase):
    def setUp(self):
        self.output_temp = tempfile.TemporaryDirectory()
        self.output_dir = self.output_temp.name
        self.saved_modules = {
            name: sys.modules.get(name) for name in (
                "bpy", "addon", "addon.core", "addon.Fmodel",
                "addon.Fmodel.Pipeline", "addon.Fmodel.Pipeline.manager",
                "addon.Fmodel.Pipeline.steps", "addon.Fmodel.Pipeline.steps.runner",
                "addon.Fmodel.Pipeline.steps.step3_scan",
                "addon.Fmodel.Pipeline.steps.step4_import",
            )
        }
        hero_rig = types.SimpleNamespace(name="HeroRig", type="ARMATURE")
        enemy_rig = types.SimpleNamespace(name="EnemyRig", type="ARMATURE")
        self.bpy = types.ModuleType("bpy")
        self.bpy.types = types.SimpleNamespace(Operator=object)
        self.bpy.path = types.SimpleNamespace(abspath=os.path.abspath)
        self.bpy.data = types.SimpleNamespace(
            texts=FakeTexts(), objects=FakeObjects([hero_rig, enemy_rig])
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
        steps = types.ModuleType("addon.Fmodel.Pipeline.steps")
        steps.__path__ = []
        manager = types.ModuleType("addon.Fmodel.Pipeline.manager")
        self.stage_calls = []
        self.persisted_after_import = False

        stage_calls = self.stage_calls

        class FakePipelineManager:
            def run_stage(self, context, stage_name):
                stage_calls.append((context.get_config("target_armature"), stage_name))
                if stage_name == PipelineRunnerResumeTests._asset_import_stage:
                    context.imported_assets = {
                        "import_mode": context.get_config("import_mode"),
                        "task_identity": {
                            "source_skeleton": context.get_config("source_skeleton"),
                            "source_subset": sorted(
                                os.path.normcase(os.path.abspath(path))
                                for path in context.get_config("source_subset", [])
                            ),
                            "target_armature": context.get_config("target_armature"),
                        },
                        "imported_actions": [{"action_name": "Idle"}],
                        "failed_imports": [],
                    }
                return True

        manager.PipelineManager = FakePipelineManager
        sys.modules.update({
            "addon": addon,
            "addon.core": self.core,
            "addon.Fmodel": fmodel,
            "addon.Fmodel.Pipeline": pipeline,
            "addon.Fmodel.Pipeline.manager": manager,
            "addon.Fmodel.Pipeline.steps": steps,
        })
        runner_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "steps", "runner.py"
        )
        spec = importlib.util.spec_from_file_location(
            "addon.Fmodel.Pipeline.steps.runner", runner_path
        )
        self.runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.runner
        spec.loader.exec_module(self.runner)
        type(self)._asset_import_stage = self.core.PipelineStage.ASSET_IMPORT.value

    def _load_step4(self):
        step_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "steps", "step4_import.py"
        )
        spec = importlib.util.spec_from_file_location(
            "addon.Fmodel.Pipeline.steps.step4_import", step_path
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _load_step3(self):
        step_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "steps", "step3_scan.py"
        )
        spec = importlib.util.spec_from_file_location(
            "addon.Fmodel.Pipeline.steps.step3_scan", step_path
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_step3_reports_current_dictionary_failure_detail(self):
        step3 = self._load_step3()
        state = types.SimpleNamespace(
            source_dir="C:/Anims", output_dir=self.output_dir,
            scan_completed=True, import_completed=True, lib_completed=True,
            current_step=2,
        )
        reports = []
        operator = step3.PIPELINE_OT_WizardStep3()
        operator.report = lambda level, message: reports.append((level, message))
        stage = self.core.PipelineStage.DICTIONARY_ACQUISITION.value

        def fail_stage(_context, _state, _stage):
            pipeline_context = self.core.PipelineContext(config={"output_dir": self.output_dir})
            pipeline_context.log_error(stage, "0 个匹配骨架筛选 'WrongRig'；mismatch=23")
            pipeline_context.set_stage_status(stage, "failed")
            pipeline_context.persist_checkpoint()
            return False

        with mock.patch.object(step3, "run_wizard_stage", side_effect=fail_stage):
            result = operator.execute(types.SimpleNamespace(
                scene=types.SimpleNamespace(pipeline_wizard=state), active_object=None,
            ))

        self.assertEqual(result, {"CANCELLED"})
        self.assertFalse(state.scan_completed)
        self.assertIn("WrongRig", reports[-1][1])
        self.assertNotIn("字典阶段未通过", reports[-1][1])

    def test_step3_failure_detail_does_not_reuse_older_scan_error(self):
        step3 = self._load_step3()
        stage = self.core.PipelineStage.DICTIONARY_ACQUISITION.value
        pipeline_context = self.core.PipelineContext(config={"output_dir": self.output_dir})
        pipeline_context.log_error(stage, "old mismatch")
        pipeline_context.set_stage_status(stage, "failed")
        pipeline_context.log_info(stage, f"开始执行阶段: {stage}")
        pipeline_context.set_stage_status(stage, "failed")
        pipeline_context.persist_checkpoint()

        self.assertEqual(
            step3._scan_stage_error(self.output_dir, stage, "字典阶段未通过"),
            "字典阶段未通过",
        )

    def tearDown(self):
        self.output_temp.cleanup()
        for name, module in self.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_legacy_checkpoint_without_resume_identities_restarts_all_mappings(self):
        manifest = {
            "skeletons": {
                "Hero": {"Moves": {"__animations__": {
                    "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"}
                }}},
                "Enemy": {"Moves": {"__animations__": {
                    "Idle": {"source_file": "C:/Anims/Enemy/Idle.ueanim"}
                }}},
            }
        }
        plan, unmapped = self.core.build_multi_import_plan(
            manifest, {"Hero": "HeroRig", "Enemy": "EnemyRig"}
        )
        self.assertEqual(unmapped, [])
        plan.tasks[0].status = "completed"
        plan.tasks[0].progress = 1.0
        checkpoint = self.core.MultiImportCheckpoint(plan=plan, current_task_index=1)
        persisted = self.core.PipelineContext(
            config={"output_dir": self.output_dir},
            dictionary_manifest=manifest,
            multi_import_plan=plan.to_dict(),
            multi_import_checkpoint=checkpoint.to_dict(),
        )
        persisted.save_to_blender_text()

        reconstructed = self.core.PipelineContext.load_from_blender_text()
        self.assertEqual(
            reconstructed.multi_import_checkpoint["plan"]["tasks"][0]["status"],
            "completed",
        )
        state = types.SimpleNamespace(
            flow_type="FMODEL",
            source_dir="C:/Anims",
            output_dir=self.output_dir,
            library_name="TestLibrary",
            skeleton_filter="",
            import_scale=0.01,
            rotation_only=False,
            import_curves=False,
            lib_generate_thumbnails=False,
            lib_thumb_resolution="256",
            lib_thumb_quality="OPENGL",
            lib_thumb_isolate=True,
            lib_chunk_size=500,
            lib_force_rebuild=False,
            import_batch_size=100,
            import_max_cycles=10,
            import_large_threshold=1000,
            import_max_consecutive_failures=20,
            import_resume_enabled=True,
            skeleton_mappings=FakeMappings([
                types.SimpleNamespace(
                    source_skeleton="Hero", target_armature=self.bpy.data.objects.get("HeroRig"),
                    status="completed",
                ),
                types.SimpleNamespace(
                    source_skeleton="Enemy", target_armature=self.bpy.data.objects.get("EnemyRig"),
                    status="pending",
                ),
            ]),
            unmapped_skeletons="",
        )
        blender_context = types.SimpleNamespace(active_object=None)

        success, unmapped = self.runner.run_wizard_import_plan(blender_context, state)

        self.assertTrue(success)
        self.assertEqual(unmapped, [])
        self.assertEqual(self.stage_calls, [
            ("HeroRig", self.core.PipelineStage.ASSET_IMPORT.value),
            ("HeroRig", self.core.PipelineStage.LIBRARY_BUILDING.value),
            ("EnemyRig", self.core.PipelineStage.ASSET_IMPORT.value),
            ("EnemyRig", self.core.PipelineStage.LIBRARY_BUILDING.value),
        ])
        restored = self.core.PipelineContext.load_from_blender_text()
        statuses = [
            task["status"]
            for task in restored.multi_import_checkpoint["plan"]["tasks"]
        ]
        self.assertEqual(statuses, ["completed", "completed"])
        self.assertEqual(restored.get_config("source_skeleton"), "")
        self.assertEqual(restored.get_config("target_armature"), "")
        self.assertEqual(restored.get_config("source_subset"), [])

    def test_legacy_wizard_config_omits_indexed_identity(self):
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
        )

        config = self.runner._build_config_from_wizard(
            state, types.SimpleNamespace(active_object=None)
        )

        self.assertNotIn("indexed_project_root", config)
        self.assertNotIn("indexed_skeleton", config)
        self.assertNotIn("indexed_selected_paths", config)

    def test_import_option_changes_preserve_scan_but_restart_completed_import_tasks(self):
        manifest = {
            "skeletons": {
                "Hero": {"Moves": {"__animations__": {
                    "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"}
                }}},
                "Enemy": {"Moves": {"__animations__": {
                    "Idle": {"source_file": "C:/Anims/Enemy/Idle.ueanim"}
                }}},
            }
        }
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            clean_after_import=False, clean_threshold=0.001, clean_flat_channels=True,
            skeleton_mappings=FakeMappings([
                types.SimpleNamespace(source_skeleton="Hero", target_armature=self.bpy.data.objects.get("HeroRig"), status="completed"),
                types.SimpleNamespace(source_skeleton="Enemy", target_armature=self.bpy.data.objects.get("EnemyRig"), status="pending"),
            ]), unmapped_skeletons="",
        )
        plan, unmapped = self.core.build_multi_import_plan(
            manifest, {"Hero": "HeroRig", "Enemy": "EnemyRig"}
        )
        self.assertEqual(unmapped, [])
        plan.tasks[0].status = "completed"
        plan.tasks[0].progress = 1.0
        checkpoint = self.core.MultiImportCheckpoint(plan=plan, current_task_index=1)
        config = self.runner._build_config_from_wizard(
            state, types.SimpleNamespace(active_object=None)
        )
        self.core.PipelineContext(
            config=config,
            dictionary_manifest=manifest,
            multi_import_plan=plan.to_dict(),
            multi_import_checkpoint=checkpoint.to_dict(),
        ).save_to_blender_text()

        state.import_scale = 2.0
        state.lib_force_rebuild = True
        state.lib_generate_thumbnails = True
        state.lib_chunk_size = 25
        state.clean_after_import = True
        state.clean_threshold = 0.5
        state.clean_flat_channels = False

        success, unmapped = self.runner.run_wizard_import_plan(
            types.SimpleNamespace(active_object=None), state
        )

        self.assertTrue(success)
        self.assertEqual(unmapped, [])
        self.assertEqual(self.stage_calls, [
            ("HeroRig", self.core.PipelineStage.ASSET_IMPORT.value),
            ("HeroRig", self.core.PipelineStage.LIBRARY_BUILDING.value),
            ("EnemyRig", self.core.PipelineStage.ASSET_IMPORT.value),
            ("EnemyRig", self.core.PipelineStage.LIBRARY_BUILDING.value),
        ])
        restored = self.core.PipelineContext.load_from_blender_text()
        self.assertEqual(restored.dictionary_manifest, manifest)
        self.assertEqual(
            restored.multi_import_checkpoint["plan"]["tasks"][0]["status"], "completed"
        )

    def test_external_checkpoint_is_preferred_over_blender_text(self):
        with tempfile.TemporaryDirectory() as output_dir:
            external = self.core.PipelineContext(
                config={"output_dir": output_dir},
                imported_assets={"imported_actions": [{"action_name": "External"}]},
            )
            external.persist_checkpoint()
            self.core.PipelineContext(
                config={"output_dir": output_dir},
                imported_assets={"imported_actions": [{"action_name": "BlenderText"}]},
            ).save_to_blender_text()

            restored = self.core.PipelineContext.load_from_blender_text()

            self.assertEqual(
                restored.imported_assets["imported_actions"][0]["action_name"], "External"
            )
            self.assertTrue(os.path.isfile(os.path.join(output_dir, ".fmodel_pipeline_checkpoint.json")))

    def test_external_checkpoint_rejects_mismatched_embedded_output_dir(self):
        with tempfile.TemporaryDirectory() as requested_output, tempfile.TemporaryDirectory() as other_output:
            checkpoint_path = os.path.join(
                requested_output, ".fmodel_pipeline_checkpoint.json"
            )
            self.core.PipelineContext(
                config={"output_dir": other_output},
                dictionary_manifest={"wrong": True},
            ).save_to_file(checkpoint_path)

            restored = self.core.PipelineContext.load_from_checkpoint(requested_output)

            self.assertIsNone(restored)

    def test_external_checkpoint_rejects_invalid_persisted_schemas(self):
        with tempfile.TemporaryDirectory() as output_dir:
            checkpoint_path = os.path.join(
                output_dir, ".fmodel_pipeline_checkpoint.json"
            )
            valid = self.core.PipelineContext(
                config={"output_dir": output_dir},
            ).to_dict()
            invalid_payloads = (
                [],
                {**valid, "config": []},
                {**valid, "stage_status": {"asset_import": "unknown"}},
                {**valid, "multi_import_plan": {"version": "1.0", "tasks": {}}},
                {**valid, "multi_import_checkpoint": {
                    "version": "1.0", "plan": {"version": "1.0", "tasks": []},
                    "current_task_index": -1,
                }},
            )

            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    with open(checkpoint_path, "w", encoding="utf-8") as handle:
                        json.dump(payload, handle)
                    self.assertIsNone(
                        self.core.PipelineContext.load_from_checkpoint(output_dir)
                    )

    def test_invalid_external_checkpoint_falls_back_to_matching_blender_text(self):
        with tempfile.TemporaryDirectory() as output_dir:
            self.core.PipelineContext(
                config={"output_dir": output_dir},
                dictionary_manifest={"fallback": True},
            ).save_to_blender_text()
            checkpoint_path = os.path.join(
                output_dir, ".fmodel_pipeline_checkpoint.json"
            )
            with open(checkpoint_path, "w", encoding="utf-8") as handle:
                json.dump({"config": [], "stage_status": []}, handle)

            restored = self.runner._load_or_create_context({
                "output_dir": output_dir,
            })

            self.assertEqual(restored.dictionary_manifest, {"fallback": True})

    def test_requested_output_without_checkpoint_does_not_adopt_other_output_state(self):
        with tempfile.TemporaryDirectory() as output_a, tempfile.TemporaryDirectory() as output_b:
            self.core.PipelineContext(
                config={"source_dir": "A", "output_dir": output_a, "library_name": "A"},
                dictionary_manifest={"from": "A"},
            ).persist_checkpoint()

            restored = self.runner._load_or_create_context({
                "source_dir": "B", "output_dir": output_b, "library_name": "B",
            })

            self.assertEqual(restored.get_config("output_dir"), output_b)
            self.assertIsNone(restored.dictionary_manifest)

    def test_requested_output_accepts_normalized_matching_blender_text_fallback(self):
        nested_output = os.path.join(self.output_dir, "nested")
        os.makedirs(nested_output)
        text_output = os.path.join(nested_output, "..", "nested")
        self.core.PipelineContext(
            config={"source_dir": "A", "output_dir": text_output, "library_name": "A"},
            dictionary_manifest={"matching": True},
        ).save_to_blender_text()

        restored = self.runner._load_or_create_context({
            "source_dir": "A", "output_dir": nested_output, "library_name": "A",
        })

        self.assertEqual(restored.dictionary_manifest, {"matching": True})

    def test_run_wizard_stage_persists_terminal_status_externally(self):
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
        )

        def finalize(manager, context, stage_name):
            context.set_stage_status(stage_name, "completed")
            context.status = "completed"
            return True

        with mock.patch.object(self.runner.PipelineManager, "run_stage", new=finalize):
            self.assertTrue(self.runner.run_wizard_stage(
                types.SimpleNamespace(active_object=None), state,
                self.core.PipelineStage.FINALIZATION.value,
            ))

        restored = self.core.PipelineContext.load_from_checkpoint(self.output_dir)
        self.assertEqual(restored.status, "completed")
        self.assertEqual(restored.stage_status["finalization"], "completed")

    def test_single_skeleton_uses_current_active_armature_not_saved_mapping(self):
        manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
            "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"},
        }}}}}
        saved_plan, _ = self.core.build_multi_import_plan(manifest, {"Hero": "EnemyRig"})
        self.core.PipelineContext(
            config={"output_dir": self.output_dir},
            dictionary_manifest=manifest,
            multi_import_plan=saved_plan.to_dict(),
            multi_import_checkpoint=self.core.MultiImportCheckpoint(plan=saved_plan).to_dict(),
        ).save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir, library_name="TestLibrary",
            skeleton_filter="", import_scale=0.01, rotation_only=False, import_curves=False,
            lib_generate_thumbnails=False, lib_thumb_resolution="256", lib_thumb_quality="OPENGL",
            lib_thumb_isolate=True, lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000, import_max_consecutive_failures=20,
            import_resume_enabled=True, skeleton_mappings=FakeMappings(), unmapped_skeletons="",
        )

        success, unmapped = self.runner.run_wizard_import_plan(
            types.SimpleNamespace(active_object=self.bpy.data.objects.get("HeroRig")), state
        )

        self.assertTrue(success)
        self.assertEqual(unmapped, [])
        self.assertEqual(self.stage_calls, [
            ("HeroRig", self.core.PipelineStage.ASSET_IMPORT.value),
            ("HeroRig", self.core.PipelineStage.LIBRARY_BUILDING.value),
        ])

    def test_single_model_handoff_target_survives_active_object_switch(self):
        manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
            "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"},
        }}}}}
        self.core.PipelineContext(
            config={"output_dir": self.output_dir}, dictionary_manifest=manifest
        ).save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="Hero", import_target_skeleton="HeroRig",
            import_scale=0.01, rotation_only=False, import_curves=False,
            lib_generate_thumbnails=False, lib_thumb_resolution="256", lib_thumb_quality="OPENGL",
            lib_thumb_isolate=True, lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000, import_max_consecutive_failures=20,
            import_resume_enabled=True, skeleton_mappings=FakeMappings(), unmapped_skeletons="",
        )

        success, unmapped = self.runner.run_wizard_import_plan(
            types.SimpleNamespace(active_object=self.bpy.data.objects.get("EnemyRig")), state
        )

        self.assertTrue(success)
        self.assertEqual(unmapped, [])
        self.assertEqual(self.stage_calls, [
            ("HeroRig", self.core.PipelineStage.ASSET_IMPORT.value),
            ("HeroRig", self.core.PipelineStage.LIBRARY_BUILDING.value),
        ])
        restored = self.core.PipelineContext.load_from_blender_text()
        self.assertEqual(
            restored.multi_import_checkpoint["plan"]["tasks"][0]["target_armature"], "HeroRig"
        )

    def test_missing_single_model_handoff_target_fails_without_active_fallback(self):
        manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
            "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"},
        }}}}}
        self.core.PipelineContext(
            config={"output_dir": self.output_dir}, dictionary_manifest=manifest
        ).save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="Hero", import_target_skeleton="MissingRig",
            import_scale=0.01, rotation_only=False, import_curves=False,
            lib_generate_thumbnails=False, lib_thumb_resolution="256", lib_thumb_quality="OPENGL",
            lib_thumb_isolate=True, lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000, import_max_consecutive_failures=20,
            import_resume_enabled=True, skeleton_mappings=FakeMappings(), unmapped_skeletons="",
        )

        with self.assertRaisesRegex(ValueError, "指定的目标骨架不可用: MissingRig"):
            self.runner.run_wizard_import_plan(
                types.SimpleNamespace(active_object=self.bpy.data.objects.get("EnemyRig")), state
            )

        self.assertEqual(self.stage_calls, [])

    def test_import_result_is_persisted_before_library_build_and_reused_on_retry(self):
        manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
            "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"},
        }}}}}
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            skeleton_mappings=FakeMappings(), unmapped_skeletons="",
        )
        plan, _ = self.core.build_multi_import_plan(
            manifest, {}, default_target_armature="HeroRig"
        )
        self.core.PipelineContext(
            config={"output_dir": self.output_dir},
            dictionary_manifest=manifest,
            multi_import_plan=plan.to_dict(),
            multi_import_checkpoint=self.core.MultiImportCheckpoint(plan=plan).to_dict(),
        ).save_to_blender_text()
        original_run_stage = self.runner.PipelineManager.run_stage

        def interrupt_after_import(manager, context, stage_name):
            if stage_name == self.core.PipelineStage.LIBRARY_BUILDING.value:
                persisted = self.core.PipelineContext.load_from_blender_text()
                self.assertEqual(
                    persisted.imported_assets["task_identity"]["source_skeleton"], "Hero"
                )
                raise RuntimeError("interrupted before library build")
            return original_run_stage(manager, context, stage_name)

        self.runner.PipelineManager.run_stage = interrupt_after_import
        with self.assertRaises(RuntimeError):
            self.runner.run_wizard_import_plan(
                types.SimpleNamespace(active_object=self.bpy.data.objects.get("HeroRig")), state
            )
        self.runner.PipelineManager.run_stage = original_run_stage
        self.stage_calls.clear()

        self.assertTrue(self.runner.run_wizard_import_plan(
            types.SimpleNamespace(active_object=self.bpy.data.objects.get("HeroRig")), state
        )[0])
        self.assertEqual(self.stage_calls, [
            ("HeroRig", self.core.PipelineStage.ASSET_IMPORT.value),
            ("HeroRig", self.core.PipelineStage.LIBRARY_BUILDING.value),
        ])

    def test_task_exception_persists_failure_and_clears_task_scope(self):
        manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
            "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"},
        }}}}}
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            skeleton_mappings=FakeMappings(), unmapped_skeletons="",
        )
        plan, _ = self.core.build_multi_import_plan(
            manifest, {}, default_target_armature="HeroRig"
        )
        persisted = self.core.PipelineContext(
            config={"output_dir": self.output_dir},
            dictionary_manifest=manifest,
            multi_import_plan=plan.to_dict(),
            multi_import_checkpoint=self.core.MultiImportCheckpoint(plan=plan).to_dict(),
        )
        persisted.save_to_blender_text()

        def explode(manager, context, stage_name):
            raise RuntimeError("task exploded")

        with mock.patch.object(self.runner.PipelineManager, "run_stage", new=explode):
            with self.assertRaisesRegex(RuntimeError, "task exploded"):
                self.runner.run_wizard_import_plan(
                    types.SimpleNamespace(active_object=self.bpy.data.objects.get("HeroRig")), state
                )

        restored = self.core.PipelineContext.load_from_blender_text()
        self.assertEqual(
            restored.multi_import_checkpoint["plan"]["tasks"][0]["status"], "failed"
        )
        self.assertEqual(restored.get_config("source_skeleton"), "")
        self.assertEqual(restored.get_config("target_armature"), "")
        self.assertEqual(restored.get_config("source_subset"), [])

    def test_each_mapped_target_is_prepared_after_its_import_and_library_build(self):
        manifest = {
            "skeletons": {
                "Hero": {"Moves": {"__animations__": {
                    "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"}
                }}},
                "Enemy": {"Moves": {"__animations__": {
                    "Attack": {"source_file": "C:/Anims/Enemy/Attack.ueanim"}
                }}},
            }
        }
        self.core.PipelineContext(
            config={"output_dir": self.output_dir}, dictionary_manifest=manifest
        ).save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            clean_after_import=False, clean_threshold=0.001, clean_flat_channels=True,
            skeleton_mappings=FakeMappings([
                types.SimpleNamespace(source_skeleton="Hero", target_armature=self.bpy.data.objects.get("HeroRig"), status="pending"),
                types.SimpleNamespace(source_skeleton="Enemy", target_armature=self.bpy.data.objects.get("EnemyRig"), status="pending"),
            ]), unmapped_skeletons="",
        )
        events = []
        original_run_stage = self.runner.PipelineManager.run_stage

        def record_stage(manager, pipeline_context, stage_name):
            events.append(("stage", pipeline_context.get_config("source_skeleton"), stage_name))
            return original_run_stage(manager, pipeline_context, stage_name)

        self.runner.PipelineManager.run_stage = record_stage
        try:
            success, _ = self.runner.run_wizard_import_plan(
                types.SimpleNamespace(active_object=None), state,
                prepare_task=lambda task, task_count: events.append(
                    ("prepare", task.source_skeleton, task.target_armature, task_count)
                ) or True,
            )
        finally:
            self.runner.PipelineManager.run_stage = original_run_stage

        self.assertTrue(success)
        self.assertEqual(events, [
            ("stage", "Hero", self.core.PipelineStage.ASSET_IMPORT.value),
            ("stage", "Hero", self.core.PipelineStage.LIBRARY_BUILDING.value),
            ("prepare", "Hero", "HeroRig", 2),
            ("stage", "Enemy", self.core.PipelineStage.ASSET_IMPORT.value),
            ("stage", "Enemy", self.core.PipelineStage.LIBRARY_BUILDING.value),
            ("prepare", "Enemy", "EnemyRig", 2),
        ])

    def test_failed_import_or_library_build_does_not_prepare_task(self):
        manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
            "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"},
        }}}}}
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            skeleton_mappings=FakeMappings(), unmapped_skeletons="",
        )
        blender_context = types.SimpleNamespace(
            active_object=self.bpy.data.objects.get("HeroRig"),
        )

        for failed_stage in (
            self.core.PipelineStage.ASSET_IMPORT.value,
            self.core.PipelineStage.LIBRARY_BUILDING.value,
        ):
            with self.subTest(failed_stage=failed_stage):
                self.core.PipelineContext(
                    config={"output_dir": self.output_dir}, dictionary_manifest=manifest,
                ).save_to_blender_text()
                prepare_task = mock.Mock(return_value=True)

                def fail_stage(_manager, _context, stage_name):
                    return stage_name != failed_stage

                with mock.patch.object(
                    self.runner.PipelineManager, "run_stage", new=fail_stage,
                ):
                    success, _ = self.runner.run_wizard_import_plan(
                        blender_context, state, prepare_task=prepare_task,
                    )

                self.assertFalse(success)
                prepare_task.assert_not_called()

    def test_failed_task_preparation_rejects_after_import_and_library_build(self):
        manifest = {"skeletons": {"Hero": {"Moves": {"__animations__": {
            "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"}
        }}}}}
        self.core.PipelineContext(
            config={"output_dir": self.output_dir}, dictionary_manifest=manifest
        ).save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            clean_after_import=False, clean_threshold=0.001, clean_flat_channels=True,
            skeleton_mappings=FakeMappings(), unmapped_skeletons="",
        )

        success, _ = self.runner.run_wizard_import_plan(
            types.SimpleNamespace(active_object=self.bpy.data.objects.get("HeroRig")), state,
            prepare_task=lambda task, task_count: False,
        )

        self.assertFalse(success)
        self.assertEqual(self.stage_calls, [
            ("HeroRig", self.core.PipelineStage.ASSET_IMPORT.value),
            ("HeroRig", self.core.PipelineStage.LIBRARY_BUILDING.value),
        ])

    def test_multi_skeleton_unmapped_fails_before_any_task_work(self):
        manifest = {"skeletons": {
            "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": "Hero.ueanim"}}}},
            "Enemy": {"Moves": {"__animations__": {"Attack": {"source_file": "Enemy.ueanim"}}}},
        }}
        self.core.PipelineContext(
            config={"output_dir": self.output_dir}, dictionary_manifest=manifest
        ).save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            clean_after_import=False, clean_threshold=0.001, clean_flat_channels=True,
            skeleton_mappings=FakeMappings([
                types.SimpleNamespace(source_skeleton="Hero", target_armature=self.bpy.data.objects.get("HeroRig"), status="pending"),
                types.SimpleNamespace(source_skeleton="Enemy", target_armature=None, status="pending"),
            ]), unmapped_skeletons="Enemy",
        )
        prepared = []
        with self.assertRaisesRegex(ValueError, "Enemy"):
            self.runner.run_wizard_import_plan(
                types.SimpleNamespace(active_object=None), state,
                prepare_task=lambda task, count: prepared.append(task.source_skeleton) or True,
            )
        self.assertEqual(prepared, [])
        self.assertEqual(self.stage_calls, [])

    def test_unsafe_source_skeleton_is_rejected_before_task_preparation(self):
        manifest = {"skeletons": {"../Hero": {"Moves": {"__animations__": {
            "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"},
        }}}}}
        self.core.PipelineContext(
            config={"output_dir": self.output_dir}, dictionary_manifest=manifest
        ).save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            clean_after_import=False, clean_threshold=0.001, clean_flat_channels=True,
            skeleton_mappings=FakeMappings(), unmapped_skeletons="",
        )
        prepared = []

        with self.assertRaisesRegex(ValueError, "source skeleton"):
            self.runner.run_wizard_import_plan(
                types.SimpleNamespace(active_object=self.bpy.data.objects.get("HeroRig")), state,
                prepare_task=lambda task, count: prepared.append(task.source_skeleton) or True,
            )

        self.assertEqual(prepared, [])
        self.assertEqual(self.stage_calls, [])

    def test_colliding_skeleton_outputs_use_distinct_storage_names(self):
        manifest = {"skeletons": {
            "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": "Hero.ueanim"}}}},
            "hero": {"Moves": {"__animations__": {"Run": {"source_file": "hero.ueanim"}}}},
        }}
        self.core.PipelineContext(
            config={"output_dir": self.output_dir}, dictionary_manifest=manifest
        ).save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            clean_after_import=False, clean_threshold=0.001, clean_flat_channels=True,
            skeleton_mappings=FakeMappings([
                types.SimpleNamespace(source_skeleton="Hero", target_armature=self.bpy.data.objects.get("HeroRig"), status="pending"),
                types.SimpleNamespace(source_skeleton="hero", target_armature=self.bpy.data.objects.get("EnemyRig"), status="pending"),
            ]), unmapped_skeletons="",
        )
        prepared = []

        self.runner.run_wizard_import_plan(
            types.SimpleNamespace(active_object=None), state,
            prepare_task=lambda task, count: prepared.append(task.source_skeleton) or True,
        )

        self.assertEqual(set(prepared), set(['Hero', 'hero']))
        self.assertEqual(len(self.stage_calls), 4)

    def test_generated_volume_stem_collision_is_encoded_without_blocking_tasks(self):
        manifest = {"skeletons": {
            "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": "Hero.ueanim"}}}},
            "Hero_Vol_2": {"Moves": {"__animations__": {"Run": {"source_file": "Hero2.ueanim"}}}},
        }}
        pipeline_context = self.core.PipelineContext(
            config={"output_dir": self.output_dir}, dictionary_manifest=manifest
        )
        pipeline_context.save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="TestLibrary", skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=1, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            clean_after_import=False, clean_threshold=0.001, clean_flat_channels=True,
            skeleton_mappings=FakeMappings([
                types.SimpleNamespace(source_skeleton="Hero", target_armature=self.bpy.data.objects.get("HeroRig"), status="pending"),
                types.SimpleNamespace(source_skeleton="Hero_Vol_2", target_armature=self.bpy.data.objects.get("EnemyRig"), status="pending"),
            ]), unmapped_skeletons="",
        )
        prepared = []
        persisted = pipeline_context.multi_import_checkpoint

        self.runner.run_wizard_import_plan(
            types.SimpleNamespace(active_object=None), state,
            prepare_task=lambda task, count: prepared.append(task.source_skeleton) or True,
        )

        restored = self.core.PipelineContext.load_from_blender_text(output_dir=self.output_dir)
        self.assertTrue(all(task["status"] == "completed" for task in restored.multi_import_checkpoint["plan"]["tasks"]))
        self.assertEqual(set(prepared), set(['Hero', 'Hero_Vol_2']))
        self.assertEqual(len(self.stage_calls), 4)

    def test_generated_filename_length_is_encoded_without_blocking_tasks(self):
        skeleton = "S" * 220
        manifest = {"skeletons": {
            skeleton: {"Moves": {"__animations__": {"Idle": {"source_file": "Hero.ueanim"}}}},
        }}
        pipeline_context = self.core.PipelineContext(
            config={"output_dir": self.output_dir}, dictionary_manifest=manifest
        )
        pipeline_context.save_to_blender_text()
        state = types.SimpleNamespace(
            flow_type="FMODEL", source_dir="C:/Anims", output_dir=self.output_dir,
            library_name="L" * 30, skeleton_filter="", import_scale=0.01,
            rotation_only=False, import_curves=False, lib_generate_thumbnails=False,
            lib_thumb_resolution="256", lib_thumb_quality="OPENGL", lib_thumb_isolate=True,
            lib_chunk_size=500, lib_force_rebuild=False, import_batch_size=100,
            import_max_cycles=10, import_large_threshold=1000,
            import_max_consecutive_failures=20, import_resume_enabled=True,
            clean_after_import=False, clean_threshold=0.001, clean_flat_channels=True,
            skeleton_mappings=FakeMappings(), unmapped_skeletons="",
        )
        prepared = []

        self.runner.run_wizard_import_plan(
            types.SimpleNamespace(active_object=self.bpy.data.objects.get("HeroRig")), state,
            prepare_task=lambda task, count: prepared.append(task.source_skeleton) or True,
        )

        restored = self.core.PipelineContext.load_from_blender_text(output_dir=self.output_dir)
        self.assertEqual(restored.multi_import_checkpoint["plan"]["tasks"][0]["status"], "completed")
        self.assertEqual(prepared, [skeleton])
        self.assertEqual(len(self.stage_calls), 2)

    def test_model_save_validates_names_and_output_containment_before_calling_save_logic(self):
        step_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "steps", "step4_import.py"
        )
        with open(step_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        operator = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "PIPELINE_OT_WizardStep4"
        )
        execute = next(
            node for node in operator.body
            if isinstance(node, ast.FunctionDef) and node.name == "_execute"
        )
        prepare = next(
            node for node in execute.body
            if isinstance(node, ast.FunctionDef) and node.name == "prepare_task"
        )
        calls = {
            node.func.id: node.lineno
            for node in ast.walk(prepare)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {
                "model_storage_stem", "safe_child_path", "save_model_logic",
            }
        }

        self.assertLess(calls["model_storage_stem"], calls["safe_child_path"])
        self.assertLess(calls["safe_child_path"], calls["save_model_logic"])

    def test_step4_operator_reports_and_cancels_operational_error(self):
        step4 = self._load_step4()
        state = types.SimpleNamespace(
            scan_skeleton_count=1, import_target_skeleton="", output_dir=self.output_dir,
        )
        reports = []
        operator = step4.PIPELINE_OT_WizardStep4()
        operator.report = lambda level, message: reports.append((level, message))

        with mock.patch.object(
            step4, "run_wizard_import_plan", side_effect=OSError("checkpoint unavailable")
        ):
            result = operator.execute(types.SimpleNamespace(
                scene=types.SimpleNamespace(pipeline_wizard=state),
                active_object=self.bpy.data.objects.get("HeroRig"),
            ))

        self.assertEqual(result, {"CANCELLED"})
        self.assertTrue(any("checkpoint unavailable" in message for _, message in reports))

    def test_step4_requires_rescan_when_active_target_changes_handoff(self):
        step4 = self._load_step4()
        state = types.SimpleNamespace(
            scan_skeleton_count=1, scan_completed=True,
            import_target_skeleton="PreviousRig", output_dir=self.output_dir,
        )
        reports = []
        operator = step4.PIPELINE_OT_WizardStep4()
        operator.report = lambda level, message: reports.append((level, message))

        state_module = types.ModuleType("addon.Fmodel.Pipeline.steps.state")
        state_module.handoff_armature = mock.Mock(return_value=True)
        with (
            mock.patch.dict(sys.modules, {state_module.__name__: state_module}),
            mock.patch.object(step4, "run_wizard_import_plan") as import_plan,
        ):
            result = operator._execute(types.SimpleNamespace(
                scene=types.SimpleNamespace(pipeline_wizard=state),
                active_object=self.bpy.data.objects.get("HeroRig"),
            ))

        self.assertEqual(result, {"CANCELLED"})
        import_plan.assert_not_called()
        state_module.handoff_armature.assert_called_once_with(
            state, self.bpy.data.objects.get("HeroRig")
        )
        self.assertTrue(any("扫描" in message for _, message in reports))

    def test_step4_multi_skeleton_ignores_active_armature_handoff(self):
        step4 = self._load_step4()
        mappings = FakeMappings([
            types.SimpleNamespace(
                source_skeleton="Hero", target_armature="HeroRig", status="completed"
            ),
            types.SimpleNamespace(
                source_skeleton="Enemy", target_armature="EnemyRig", status="completed"
            ),
        ])
        state = types.SimpleNamespace(
            scan_skeleton_count=2, scan_completed=True,
            import_target_skeleton="HeroRig", skeleton_mappings=mappings,
            output_dir=self.output_dir, lib_save_model=False,
        )
        operator = step4.PIPELINE_OT_WizardStep4()
        operator.report = mock.Mock()

        state_module = types.ModuleType("addon.Fmodel.Pipeline.steps.state")
        state_module.handoff_armature = mock.Mock(return_value=True)
        with (
            mock.patch.dict(sys.modules, {state_module.__name__: state_module}),
            mock.patch.object(step4, "run_wizard_import_plan", return_value=(True, [])) as import_plan,
            mock.patch.object(step4, "run_wizard_stage", return_value=True) as final_stage,
        ):
            result = operator._execute(types.SimpleNamespace(
                scene=types.SimpleNamespace(pipeline_wizard=state),
                active_object=self.bpy.data.objects.get("EnemyRig"),
            ))

        self.assertEqual(result, {"FINISHED"})
        state_module.handoff_armature.assert_not_called()
        self.assertEqual(
            [(item.source_skeleton, item.target_armature, item.status) for item in mappings],
            [("Hero", "HeroRig", "completed"), ("Enemy", "EnemyRig", "completed")],
        )
        import_plan.assert_called_once()
        final_stage.assert_called_once_with(
            mock.ANY, state, self.core.PipelineStage.FINALIZATION.value
        )

    def test_rebake_restores_force_flags_and_persisted_config_in_finally(self):
        step_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Fmodel", "Pipeline", "steps", "step4_import.py"
        )
        with open(step_path, encoding="utf-8") as handle:
            module = ast.parse(handle.read())
        operator = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "PIPELINE_OT_RebakeThumbnails"
        )
        execute = next(
            node for node in operator.body
            if isinstance(node, ast.FunctionDef) and node.name == "_execute"
        )
        guarded = next(node for node in ast.walk(execute) if isinstance(node, ast.Try) and node.finalbody)
        final_source = "\n".join(ast.unparse(node) for node in guarded.finalbody)

        self.assertIn("state.lib_force_rebuild = previous_state_force", final_source)
        self.assertIn("pipeline_context.config = previous_config", final_source)
        self.assertIn("pipeline_context.persist_checkpoint()", final_source)


if __name__ == "__main__":
    unittest.main()
