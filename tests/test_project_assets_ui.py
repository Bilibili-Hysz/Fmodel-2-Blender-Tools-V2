"""Fake-Blender contracts for project-first index controls."""

import ast
import importlib.util
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "Fmodel" / "project_assets.py"
CURRENT_GUIDES = (
    ROOT / "README.md",
    ROOT / "README_EN.md",
    ROOT / "README_ZH.md",
    ROOT / "docs" / "ANIMATION_PIPELINE_BACKGROUND_INDEX_DESIGN_2026-08-08.md",
)


class ProjectAssetsUiTests(unittest.TestCase):
    def test_current_guides_make_animation_pipeline_the_project_index_owner(self):
        for path in CURRENT_GUIDES:
            with self.subTest(path=path):
                guide = path.read_text(encoding="utf-8")
                self.assertIn("Animation Pipeline", guide)
                self.assertNotIn("Project Settings uses Open, Scan, and Rebuild", guide)

    def setUp(self):
        self.module_names = (
            "bpy", "addon", "addon.core", "addon.core.project_index",
            "addon.Fmodel", "addon.Fmodel.project_assets",
        )
        self.saved_modules = {name: sys.modules.get(name) for name in self.module_names}
        self.registered = []
        self.bpy = types.ModuleType("bpy")
        self.bpy.types = types.SimpleNamespace(
            Operator=object, Panel=object, PropertyGroup=object, Scene=type("Scene", (), {}),
        )
        self.bpy.props = types.SimpleNamespace(
            StringProperty=lambda **kwargs: kwargs,
            IntProperty=lambda **kwargs: kwargs,
            BoolProperty=lambda **kwargs: kwargs,
            EnumProperty=lambda **kwargs: kwargs,
            PointerProperty=lambda **kwargs: kwargs,
            CollectionProperty=lambda **kwargs: kwargs,
        )
        self.bpy.utils = types.SimpleNamespace(
            register_class=self.registered.append,
            unregister_class=lambda cls: self.registered.remove(cls),
        )
        sys.modules["bpy"] = self.bpy
        addon = types.ModuleType("addon")
        addon.__path__ = []
        core = types.ModuleType("addon.core")
        fmodel = types.ModuleType("addon.Fmodel")
        fmodel.__path__ = []
        self.indexes = []

        class FakeProjectIndex:
            def __init__(inner_self, root, **_kwargs):
                inner_self.root = root
                inner_self.path = ":memory:"
                inner_self.calls = []
                self.indexes.append(inner_self)

            def status(inner_self):
                inner_self.calls.append("status")
                return {"state": "ready", "schema_version": 3, "rebuild_required": False}

            def ensure_schema(inner_self):
                inner_self.calls.append("create")
                return inner_self.status()

            def scan(inner_self, roots):
                inner_self.calls.append(("scan", roots))
                return {"state": "ready", "generation": 3}

            def rebuild(inner_self):
                inner_self.calls.append("rebuild")
                return inner_self.status()

        project_index = types.ModuleType("addon.core.project_index")
        project_index.ProjectIndex = FakeProjectIndex
        sys.modules.update({
            "addon": addon,
            "addon.core": core,
            "addon.core.project_index": project_index,
            "addon.Fmodel": fmodel,
        })
        spec = importlib.util.spec_from_file_location("addon.Fmodel.project_assets", MODULE_PATH)
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)

    def tearDown(self):
        for name, module in self.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_registers_scene_project_settings_and_cleans_them_up(self):
        self.module.register()

        self.assertIn(self.module.FMODEL_ProjectSettings, self.registered)
        self.assertTrue(hasattr(self.bpy.types.Scene, "fmodel_project"))

        self.module.unregister()

        self.assertFalse(hasattr(self.bpy.types.Scene, "fmodel_project"))
        self.assertEqual(self.registered, [])

    def test_project_root_update_synchronizes_project_mode_wizard_source(self):
        settings = types.SimpleNamespace(
            root_path="  C:/NewProject  ",
            indexed_skeleton="Hero",
            indexed_animations=[],
        )
        wizard = types.SimpleNamespace(source_mode="PROJECT", source_dir="C:/OldProject")
        context = types.SimpleNamespace(
            scene=types.SimpleNamespace(pipeline_wizard=wizard),
        )

        self.module._on_project_root_changed(settings, context)

        self.assertEqual(wizard.source_dir, "C:/NewProject")

    def test_project_root_update_refreshes_index_status(self):
        settings = types.SimpleNamespace(
            root_path="  C:/NewProject  ",
            indexed_skeleton="Hero",
            indexed_animations=[],
        )
        context = types.SimpleNamespace(scene=types.SimpleNamespace())
        refreshed = []

        def refresh(project_settings):
            refreshed.append(project_settings)
            project_settings.index_state = "ready"
            return {"state": "ready"}

        self.module.refresh_project_index = refresh

        self.module._on_project_root_changed(settings, context)

        self.assertEqual(refreshed, [settings])
        self.assertEqual(settings.index_state, "ready")

    def test_refresh_pipeline_project_index_migrates_legacy_root_and_status(self):
        settings = types.SimpleNamespace(
            root_path="C:/LegacyProject",
            indexed_skeleton="",
            indexed_animations=[],
        )
        wizard = types.SimpleNamespace(
            project_root="",
            project_index_state="missing",
            project_index_schema_version=0,
            project_index_animation_count=0,
            project_index_message="",
        )
        scene = types.SimpleNamespace(
            fmodel_project=settings,
            pipeline_wizard=wizard,
        )

        status = self.module.refresh_pipeline_project_index(scene)

        self.assertEqual(status["state"], "ready")
        self.assertEqual(wizard.project_root, "C:/LegacyProject")
        self.assertEqual(wizard.project_index_state, "ready")
        self.assertEqual(wizard.project_index_schema_version, 3)
        self.assertEqual(wizard.project_index_message, "Animation index ready")

    def test_refresh_pipeline_project_index_prefers_new_wizard_root(self):
        settings = types.SimpleNamespace(
            root_path="C:/LegacyProject",
            indexed_skeleton="",
            indexed_animations=[],
        )
        wizard = types.SimpleNamespace(
            project_root="C:/PipelineProject",
            project_index_state="missing",
            project_index_schema_version=0,
            project_index_animation_count=0,
            project_index_message="",
        )
        scene = types.SimpleNamespace(
            fmodel_project=settings,
            pipeline_wizard=wizard,
        )

        self.module.refresh_pipeline_project_index(scene)

        self.assertEqual(settings.root_path, "C:/PipelineProject")
        self.assertEqual(wizard.project_root, "C:/PipelineProject")

    def test_project_root_update_leaves_manual_wizard_source_untouched(self):
        settings = types.SimpleNamespace(
            root_path="  C:/NewProject  ",
            indexed_skeleton="Hero",
            indexed_animations=[],
        )
        wizard = types.SimpleNamespace(
            source_mode="MANUAL",
            source_dir="C:/ManualAnims",
            manual_source_dir="C:/ManualAnims",
        )
        context = types.SimpleNamespace(
            scene=types.SimpleNamespace(pipeline_wizard=wizard),
        )

        self.module._on_project_root_changed(settings, context)

        self.assertEqual(wizard.source_dir, "C:/ManualAnims")
        self.assertEqual(wizard.manual_source_dir, "C:/ManualAnims")

    def test_open_scan_and_rebuild_use_pipeline_project_root_and_refresh_state(self):
        settings = types.SimpleNamespace(root_path="C:/Project")
        wizard = types.SimpleNamespace(project_root="C:/Project")
        context = types.SimpleNamespace(
            scene=types.SimpleNamespace(fmodel_project=settings, pipeline_wizard=wizard)
        )

        for operator_class, expected in (
            (self.module.FMODEL_OT_OpenProjectIndex, ("status",)),
            (self.module.FMODEL_OT_ScanProjectIndex, (("scan", ["C:/Project"]),)),
            (
                self.module.FMODEL_OT_RebuildProjectIndex,
                ("rebuild", ("scan", ["C:/Project"])),
            ),
        ):
            operator = operator_class()
            reports = []
            operator.report = lambda level, message: reports.append((level, message))

            self.assertEqual(operator.execute(context), {"FINISHED"})
            for call in expected:
                self.assertIn(call, self.indexes[-1].calls)
            self.assertEqual(settings.index_state, "ready")
            self.assertEqual(settings.index_schema_version, 3)
            self.assertTrue(any("索引" in message for _, message in reports))

    def test_open_reads_ready_index_without_scanning_or_creating(self):
        settings = types.SimpleNamespace(root_path="C:/Project")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))
        operator = self.module.FMODEL_OT_OpenProjectIndex()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(operator.execute(context), {"FINISHED"})
        self.assertEqual(self.indexes[-1].calls, ["status"])
        self.assertEqual(settings.index_state, "ready")
        self.assertEqual(settings.index_schema_version, 3)
        self.assertEqual(reports, [({"INFO"}, "项目索引已加载")])

    def test_scan_creates_missing_index_through_scan_then_reports_scanned(self):
        settings = types.SimpleNamespace(root_path="C:/Project")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))
        indexes = []

        class MissingIndex:
            path = ":memory:"

            def __init__(self, root, **_kwargs):
                self.root = root
                self.calls = []
                self.scanned = False
                indexes.append(self)

            def status(self):
                self.calls.append("status")
                if self.scanned:
                    return {"state": "ready", "schema_version": 3, "rebuild_required": False}
                return {"state": "missing", "schema_version": None, "rebuild_required": True}

            def scan(self, roots):
                self.calls.append(("scan", roots))
                self.scanned = True
                return {"state": "ready", "generation": 3}

        self.module.ProjectIndex = MissingIndex
        operator = self.module.FMODEL_OT_ScanProjectIndex()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(operator.execute(context), {"FINISHED"})
        self.assertEqual(indexes[-1].calls, [("scan", ["C:/Project"]), "status"])
        self.assertEqual(settings.index_state, "ready")
        self.assertEqual(reports, [({"INFO"}, "项目索引已扫描")])

    def test_open_rejects_non_ready_index_without_scanning_or_creating(self):
        settings = types.SimpleNamespace(root_path="C:/Project")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))

        for state, message in (
            ("missing", "项目索引缺失，请先扫描"),
            ("rebuild_required", "需要重建项目索引"),
            ("unavailable", "项目索引不可用"),
        ):
            with self.subTest(state=state):
                indexes = []

                class UnsafeIndex:
                    path = ":memory:"

                    def __init__(self, root, **_kwargs):
                        self.root = root
                        self.calls = []
                        indexes.append(self)

                    def status(self):
                        self.calls.append("status")
                        return {"state": state, "schema_version": 1, "rebuild_required": True}

                    def scan(self, roots):
                        self.calls.append(("scan", roots))
                        raise AssertionError("open must not scan")

                self.module.ProjectIndex = UnsafeIndex
                operator = self.module.FMODEL_OT_OpenProjectIndex()
                reports = []
                operator.report = lambda level, message: reports.append((level, message))

                self.assertEqual(operator.execute(context), {"CANCELLED"})
                self.assertEqual(indexes[-1].calls, ["status"])
                self.assertEqual(settings.index_state, state)
                self.assertEqual(reports, [({"ERROR"}, message)])

    def test_operation_rejects_empty_root_with_controlled_error(self):
        settings = types.SimpleNamespace(root_path="")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))
        operator = self.module.FMODEL_OT_ScanProjectIndex()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(operator.execute(context), {"CANCELLED"})
        self.assertEqual(self.indexes, [])
        self.assertEqual(reports, [({"ERROR"}, "必须先设置项目根目录")])

    def test_operations_cancel_when_project_index_construction_fails(self):
        settings = types.SimpleNamespace(root_path="C:/Unresolvable")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))

        def unavailable_index(root, **_kwargs):
            raise OSError(f"Cannot resolve {root}")

        self.module.ProjectIndex = unavailable_index
        for operator_class in (
            self.module.FMODEL_OT_OpenProjectIndex,
            self.module.FMODEL_OT_ScanProjectIndex,
            self.module.FMODEL_OT_RebuildProjectIndex,
        ):
            operator = operator_class()
            reports = []
            operator.report = lambda level, message: reports.append((level, message))

            self.assertEqual(operator.execute(context), {"CANCELLED"})
            self.assertEqual(reports, [({"ERROR"}, "项目索引不可用: Cannot resolve C:/Unresolvable")])

    def test_refresh_handles_project_index_construction_failure(self):
        settings = types.SimpleNamespace(
            root_path="C:/Unresolvable",
            index_state="ready",
            index_schema_version=2,
            animation_count=4,
            model_count=3,
        )

        def unavailable_index(root, **_kwargs):
            raise OSError(f"Cannot resolve {root}")

        self.module.ProjectIndex = unavailable_index

        status = self.module.refresh_project_index(settings)

        self.assertEqual(status, {
            "state": "unavailable",
            "schema_version": None,
            "rebuild_required": True,
        })
        self.assertEqual(settings.index_state, "unavailable")
        self.assertEqual(settings.index_schema_version, 0)
        self.assertEqual(settings.animation_count, 0)
        self.assertEqual(settings.model_count, 0)

    def test_apply_indexed_animation_selection_serializes_wizard_state(self):
        class AnimationChoices(list):
            def add(self):
                choice = type("AnimationChoice", (), {})()
                self.append(choice)
                return choice

            def clear(self):
                del self[:]

        class ProjectSettings:
            root_path = "C:/Project"
            index_state = "ready"
            indexed_skeleton = "Hero"
            indexed_animations = AnimationChoices()

        class WizardState:
            indexed_selection_mode = "NONE"
            indexed_project_root = ""
            indexed_skeleton = ""
            indexed_selected_paths = "[]"

        settings = ProjectSettings()
        wizard = WizardState()
        context = type("FakeBlenderContext", (), {
            "scene": type("FakeBlenderScene", (), {
                "fmodel_project": settings,
                "pipeline_wizard": wizard,
            })(),
        })()
        self.module.ProjectIndex = lambda root, **_kwargs: type("FakeReadyIndex", (), {
            "status": lambda self: {"state": "ready"},
            "read_only_animations_for_skeleton": lambda self, skeleton, **kwargs: [
                "C:/Project/Hero/Idle.ueanim", "C:/Project/Hero/Run.ueanim",
            ],
        })()

        self.module.refresh_indexed_animation_choices(settings)
        settings.indexed_animations[0].selected = True
        settings.indexed_animations[1].selected = False
        operator = self.module.FMODEL_OT_ApplyIndexedAnimationSelection()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))
        state_module = types.ModuleType("addon.Fmodel.Pipeline.steps.state")
        state_module.clear_workflow_results = mock.Mock()
        pipeline = types.ModuleType("addon.Fmodel.Pipeline")
        pipeline.__path__ = []
        steps = types.ModuleType("addon.Fmodel.Pipeline.steps")
        steps.__path__ = []

        with mock.patch.dict(sys.modules, {
            "addon.Fmodel.Pipeline": pipeline,
            "addon.Fmodel.Pipeline.steps": steps,
            state_module.__name__: state_module,
        }):
            self.assertEqual(operator.execute(context), {"FINISHED"})
        self.assertEqual(wizard.indexed_project_root, "C:/Project")
        self.assertEqual(wizard.indexed_skeleton, "Hero")
        self.assertEqual(wizard.indexed_selected_paths, '["C:/Project/Hero/Idle.ueanim"]')
        self.assertEqual(wizard.indexed_selection_mode, "EXPLICIT")
        self.assertTrue(any("1 个动画" in message for _, message in reports))

    def test_apply_indexed_animation_selection_rejects_unavailable_or_empty_choices(self):
        class ProjectSettings:
            root_path = "C:/Project"
            index_state = "unavailable"
            indexed_skeleton = "Hero"
            indexed_animations = []

        class WizardState:
            indexed_project_root = "old"
            indexed_skeleton = "Old"
            indexed_selected_paths = '["old"]'

        settings = ProjectSettings()
        wizard = WizardState()
        context = type("FakeBlenderContext", (), {
            "scene": type("FakeBlenderScene", (), {
                "fmodel_project": settings,
                "pipeline_wizard": wizard,
            })(),
        })()
        operator = self.module.FMODEL_OT_ApplyIndexedAnimationSelection()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(operator.execute(context), {"CANCELLED"})
        self.assertEqual(wizard.indexed_project_root, "old")
        self.assertEqual(reports, [({"ERROR"}, "需要可用的项目索引")])

    def test_apply_indexed_animation_selection_rolls_back_all_fields_after_assignment_failure(self):
        class WizardState:
            def __init__(self):
                object.__setattr__(self, "fail_selection_mode", True)
                self.indexed_project_root = "old-root"
                self.indexed_skeleton = "Old"
                self.indexed_selected_paths = '["old"]'
                object.__setattr__(self, "indexed_selection_mode", "NONE")

            def __setattr__(self, name, value):
                if name == "indexed_selection_mode" and self.fail_selection_mode:
                    object.__setattr__(self, "fail_selection_mode", False)
                    raise RuntimeError("final assignment failed")
                object.__setattr__(self, name, value)

        settings = types.SimpleNamespace(
            root_path="C:/Project",
            index_state="ready",
            indexed_skeleton="Hero",
            indexed_animations=[types.SimpleNamespace(path="C:/Project/Hero/Idle.ueanim", selected=True)],
        )
        wizard = WizardState()
        context = types.SimpleNamespace(scene=types.SimpleNamespace(
            fmodel_project=settings, pipeline_wizard=wizard,
        ))
        operator = self.module.FMODEL_OT_ApplyIndexedAnimationSelection()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(operator.execute(context), {"CANCELLED"})
        self.assertEqual(wizard.indexed_project_root, "old-root")
        self.assertEqual(wizard.indexed_skeleton, "Old")
        self.assertEqual(wizard.indexed_selected_paths, '["old"]')
        self.assertEqual(wizard.indexed_selection_mode, "NONE")
        self.assertEqual(reports, [({"ERROR"}, "无法应用已索引的动画选择")])

    def test_apply_indexed_animation_selection_clears_workflow_results_only_after_commit(self):
        class WizardState:
            def __init__(self, fail=False):
                object.__setattr__(self, "fail", fail)
                self.indexed_project_root = "old-root"
                self.indexed_skeleton = "Old"
                self.indexed_selected_paths = '["old"]'
                object.__setattr__(self, "indexed_selection_mode", "NONE")

            def __setattr__(self, name, value):
                if name == "indexed_selection_mode" and self.fail:
                    object.__setattr__(self, "fail", False)
                    raise RuntimeError("selection commit failed")
                object.__setattr__(self, name, value)

        settings = types.SimpleNamespace(
            root_path="C:/Project",
            index_state="ready",
            indexed_skeleton="Hero",
            indexed_animations=[types.SimpleNamespace(
                path="C:/Project/Hero/Idle.ueanim", selected=True,
            )],
        )
        state_module = types.ModuleType("addon.Fmodel.Pipeline.steps.state")
        clear_workflow_results = mock.Mock()
        state_module.clear_workflow_results = clear_workflow_results
        pipeline = types.ModuleType("addon.Fmodel.Pipeline")
        pipeline.__path__ = []
        steps = types.ModuleType("addon.Fmodel.Pipeline.steps")
        steps.__path__ = []
        operator = self.module.FMODEL_OT_ApplyIndexedAnimationSelection()
        operator.report = lambda *_args: None

        with mock.patch.dict(sys.modules, {
            "addon.Fmodel.Pipeline": pipeline,
            "addon.Fmodel.Pipeline.steps": steps,
            state_module.__name__: state_module,
        }):
            wizard = WizardState()
            context = types.SimpleNamespace(scene=types.SimpleNamespace(
                fmodel_project=settings, pipeline_wizard=wizard,
            ))
            self.assertEqual(operator.execute(context), {"FINISHED"})
            clear_workflow_results.assert_called_once_with(wizard)

            clear_workflow_results.reset_mock()
            failed_wizard = WizardState(fail=True)
            failed_context = types.SimpleNamespace(scene=types.SimpleNamespace(
                fmodel_project=settings, pipeline_wizard=failed_wizard,
            ))
            self.assertEqual(operator.execute(failed_context), {"CANCELLED"})
            clear_workflow_results.assert_not_called()

    def test_refresh_reports_index_query_failure_instead_of_no_animations(self):
        class AnimationChoices(list):
            def add(self):
                choice = type("AnimationChoice", (), {})()
                self.append(choice)
                return choice

            def clear(self):
                del self[:]

        settings = types.SimpleNamespace(
            root_path="C:/Project",
            index_state="ready",
            indexed_skeleton="Hero",
            indexed_animations=AnimationChoices(),
        )
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))
        operator = self.module.FMODEL_OT_RefreshIndexedAnimationChoices()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        for error in (self.module.sqlite3.Error("database is locked"), OSError("disk unavailable")):
            self.module.ProjectIndex = lambda root, error=error, **_kwargs: type("FailingIndex", (), {
                "status": lambda self: {"state": "ready"},
                "read_only_animations_for_skeleton": lambda self, skeleton, **kwargs: (_ for _ in ()).throw(error),
            })()

            self.assertEqual(operator.execute(context), {"CANCELLED"})
            self.assertEqual(reports[-1][0], {"ERROR"})
            self.assertIn("索引", reports[-1][1])
            self.assertNotIn("没有已索引动画", reports[-1][1])

    def test_read_only_asset_queries_do_not_create_a_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing.sqlite"
            index = type("ReadyIndex", (), {"path": missing_path})()
            settings = types.SimpleNamespace(root_path="C:/Project", index_state="ready")
            self.module.ProjectIndex = lambda root, **_kwargs: type("ReadyIndex", (), {
                "path": missing_path,
                "status": lambda self: {"state": "ready"},
            })()

            self.assertEqual(self.module._asset_counts(index, {"state": "ready"}), (0, 0))
            self.assertEqual(self.module._skeleton_items(settings, None), [])

            self.assertFalse(missing_path.exists())

    def test_refresh_does_not_recreate_index_deleted_after_ready_check(self):
        class AnimationChoices(list):
            def clear(self):
                del self[:]

        with tempfile.TemporaryDirectory() as directory:
            index_path = Path(directory) / "project-assets.sqlite"
            index_path.touch()
            existing = types.SimpleNamespace(path="C:/Project/Hero/Idle.ueanim", selected=True)
            settings = types.SimpleNamespace(
                root_path="C:/Project",
                index_state="ready",
                indexed_skeleton="Hero",
                indexed_animations=AnimationChoices([existing]),
            )

            class DeletingIndex:
                path = index_path

                def status(self):
                    return {"state": "ready"}

                def read_only_animations_for_skeleton(self, skeleton, **kwargs):
                    index_path.unlink()
                    raise self_module.sqlite3.OperationalError("index deleted")

            self_module = self.module
            self.module.ProjectIndex = lambda root, **_kwargs: DeletingIndex()

            self.assertIsNone(self.module.refresh_indexed_animation_choices(settings))
            self.assertEqual(settings.indexed_animations, [existing])
            self.assertFalse(index_path.exists())

    def test_refresh_preserves_existing_choices_when_readiness_or_query_fails(self):
        class AnimationChoices(list):
            def add(self):
                choice = type("AnimationChoice", (), {})()
                self.append(choice)
                return choice

            def clear(self):
                del self[:]

        first = types.SimpleNamespace(path="C:/Project/Hero/Idle.ueanim", selected=True)
        second = types.SimpleNamespace(path="C:/Project/Hero/Run.ueanim", selected=False)
        settings = types.SimpleNamespace(
            root_path="C:/Project",
            index_state="ready",
            indexed_skeleton="Hero",
            indexed_animations=AnimationChoices([first, second]),
        )

        for state, error in (("rebuild_required", None), ("ready", self.module.sqlite3.Error("locked"))):
            with self.subTest(state=state, error=error):
                self.module.ProjectIndex = lambda root, state=state, error=error, **_kwargs: type("Index", (), {
                    "status": lambda self: {"state": state},
                    "read_only_animations_for_skeleton": lambda self, skeleton, **kwargs: (
                        (_ for _ in ()).throw(error) if error else []
                    ),
                })()

                self.assertIsNone(self.module.refresh_indexed_animation_choices(settings))
                self.assertEqual(settings.indexed_animations, [first, second])
                self.assertIs(settings.indexed_animations[0], first)
                self.assertTrue(settings.indexed_animations[0].selected)
                self.assertIs(settings.indexed_animations[1], second)
                self.assertFalse(settings.indexed_animations[1].selected)

    def test_refresh_reports_not_ready_index_instead_of_no_animations(self):
        class AnimationChoices(list):
            def clear(self):
                del self[:]

        settings = types.SimpleNamespace(
            root_path="C:/Project",
            index_state="ready",
            indexed_skeleton="Hero",
            indexed_animations=AnimationChoices(),
        )
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))
        operator = self.module.FMODEL_OT_RefreshIndexedAnimationChoices()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        for state in ("unavailable", "rebuild_required"):
            with self.subTest(state=state):
                self.module.ProjectIndex = lambda root, state=state, **_kwargs: type("NotReadyIndex", (), {
                    "status": lambda self: {"state": state},
                    "read_only_animations_for_skeleton": lambda self, skeleton, **kwargs: [],
                })()

                self.assertEqual(operator.execute(context), {"CANCELLED"})
                self.assertEqual(reports[-1][0], {"ERROR"})
                self.assertIn("索引", reports[-1][1])
                self.assertNotIn("没有已索引动画", reports[-1][1])

    def test_refresh_warns_when_index_query_returns_no_animations(self):
        class AnimationChoices(list):
            def clear(self):
                del self[:]

        settings = types.SimpleNamespace(
            root_path="C:/Project",
            index_state="ready",
            indexed_skeleton="Hero",
            indexed_animations=AnimationChoices(),
        )
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))
        self.module.ProjectIndex = lambda root, **_kwargs: type("EmptyIndex", (), {
            "status": lambda self: {"state": "ready"},
            "read_only_animations_for_skeleton": lambda self, skeleton, **kwargs: [],
        })()
        operator = self.module.FMODEL_OT_RefreshIndexedAnimationChoices()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(operator.execute(context), {"CANCELLED"})
        self.assertEqual(reports, [({"WARNING"}, "所选骨架没有已索引动画")])

    def test_animation_pipeline_owns_folder_ui_and_legacy_panel_is_unregistered(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        registration = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_classes" for target in node.targets)
        )
        registered = [element.id for element in registration.value.elts if isinstance(element, ast.Name)]

        self.assertIn("FMODEL_PT_ProjectSettings", source)
        self.assertNotIn("FMODEL_PT_ProjectSettings", registered)
        pipeline_source = source[source.index("class FMODEL_PT_Pipeline"):]
        self.assertIn('prop(state, "manual_source_dir"', pipeline_source)
        self.assertIn("Folder 模式：直接扫描动画文件夹", pipeline_source)
        for marker in (
            "project_root",
            "fmodel.open_project_index",
            "fmodel.scan_project_index",
            "fmodel.rebuild_project_index",
            "pipeline.cancel_project_index",
            "project_index_progress",
        ):
            self.assertNotIn(marker, pipeline_source)

    def test_project_index_modules_are_not_registered_in_active_lifecycle(self):
        fmodel_init = (ROOT / "Fmodel" / "__init__.py").read_text(encoding="utf-8")
        pipeline_init = (ROOT / "Fmodel" / "Pipeline" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("project_assets.register()", fmodel_init)
        self.assertNotIn("project_index_controller.register()", pipeline_init)
        self.assertNotIn("project_index_controller.unregister()", pipeline_init)

    def test_asset_library_panel_tree_registers_before_extension_tools(self):
        tree = ast.parse((ROOT / "ui.py").read_text(encoding="utf-8"))
        registration = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_classes" for target in node.targets)
        )
        registered = [element.id for element in registration.value.elts if isinstance(element, ast.Name)]
        asset_library_tree = (
            "FMODEL_PT_AssetLibrary",
            "FMODEL_PT_LibrarySettings",
            "FMODEL_PT_ModelAssets",
            "FMODEL_PT_AnimationAssets",
            "FMODEL_PT_AnimDisplay",
            "FMODEL_PT_AnimList",
            "FMODEL_PT_AnimDetail",
        )
        extension_tools_tree = (
            "FMODEL_PT_GeneralTools",
            "FMODEL_PT_Mesh",
            "FMODEL_PT_Skeleton",
            "FMODEL_PT_Animation",
        )

        for asset_panel in asset_library_tree:
            for extension_panel in extension_tools_tree:
                with self.subTest(asset_panel=asset_panel, extension_panel=extension_panel):
                    self.assertLess(registered.index(asset_panel), registered.index(extension_panel))

    def test_legacy_project_panel_is_disabled_and_does_not_write_state(self):
        tree = ast.parse((ROOT / "ui.py").read_text(encoding="utf-8"))
        panel_node = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "FMODEL_PT_ProjectSettings"
        )
        namespace = {
            "bpy": types.SimpleNamespace(types=types.SimpleNamespace(Panel=object)),
            "t": lambda text: text,
        }
        exec(compile(ast.Module(body=[panel_node], type_ignores=[]), "ui.py", "exec"), namespace)

        class Layout:
            def __init__(self):
                self.labels = []

            def label(self, **kwargs):
                self.labels.append(kwargs)

        panel = namespace["FMODEL_PT_ProjectSettings"]()
        panel.layout = Layout()
        panel.draw(types.SimpleNamespace(scene=types.SimpleNamespace()))

        self.assertIn({"text": "Indexed Project 已停用", "icon": "INFO"}, panel.layout.labels)
        self.assertIn({"text": "请在动画管线中选择动画文件夹", "icon": "FILE_FOLDER"}, panel.layout.labels)

    def test_panel_draw_source_never_refreshes_the_project_index(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        project_panel = source[
            source.index("class FMODEL_PT_ProjectSettings"):
            source.index("class FMODEL_PT_Toolbox")
        ]
        pipeline_panel = source[
            source.index("class FMODEL_PT_Pipeline"):
            source.index("class FMODEL_PT_PipelineDebug")
        ]

        self.assertNotIn("refresh_project_index", project_panel)
        self.assertNotIn("refresh_project_index", pipeline_panel)

    def test_pipeline_default_draw_has_no_indexed_animation_choice_loop(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        pipeline_panel = source[
            source.index("class FMODEL_PT_Pipeline"):
            source.index("class FMODEL_PT_PipelineDebug")
        ]

        self.assertNotIn("indexed_animations", pipeline_panel)
        self.assertNotIn("refresh_indexed_animation_choices", pipeline_panel)
        self.assertNotIn("apply_indexed_animation_selection", pipeline_panel)
        self.assertNotIn("ProjectIndex", pipeline_panel)

    def test_pipeline_step2_exposes_folder_source_contract(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        pipeline_panel = source[
            source.index("class FMODEL_PT_Pipeline"):
            source.index("class FMODEL_PT_PipelineDebug")
        ]

        self.assertIn('prop(state, "manual_source_dir"', pipeline_panel)
        self.assertIn('text=t("动画文件夹")', pipeline_panel)
        self.assertNotIn('prop(state, "source_mode"', pipeline_panel)
        self.assertNotIn('prop(state, "project_root"', pipeline_panel)
        for marker in ("fmodel.scan_project_index", "fmodel.rebuild_project_index", "fmodel_project"):
            self.assertNotIn(marker, pipeline_panel)

    def test_maintenance_operators_are_blocked_during_background_prepare(self):
        scanning = types.SimpleNamespace(
            scene=types.SimpleNamespace(
                pipeline_wizard=types.SimpleNamespace(project_index_state="scanning")
            )
        )
        ready = types.SimpleNamespace(
            scene=types.SimpleNamespace(
                pipeline_wizard=types.SimpleNamespace(project_index_state="ready")
            )
        )

        self.assertFalse(self.module.FMODEL_OT_ScanProjectIndex.poll(scanning))
        self.assertFalse(self.module.FMODEL_OT_RebuildProjectIndex.poll(scanning))
        self.assertTrue(self.module.FMODEL_OT_ScanProjectIndex.poll(ready))

    def test_pipeline_step3_exposes_folder_scan_contract(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        pipeline_panel = source[
            source.index("class FMODEL_PT_Pipeline"):
            source.index("class FMODEL_PT_PipelineDebug")
        ]

        self.assertIn('"pipeline.wizard_step3"', pipeline_panel)
        self.assertIn('text=t("扫描动画文件夹")', pipeline_panel)
        self.assertIn('text=t("扫描当前文件夹中的 UEFormat 动画文件")', pipeline_panel)
        for marker in ("project_index_state", "project_index_progress", "pipeline.cancel_project_index"):
            self.assertNotIn(marker, pipeline_panel)

    def test_pipeline_step3_labels_reflect_folder_source(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        pipeline_panel = source[
            source.index("class FMODEL_PT_Pipeline"):
            source.index("class FMODEL_PT_PipelineDebug")
        ]

        self.assertIn('text=t("扫描动画文件夹")', pipeline_panel)
        self.assertIn('text=t("扫描当前文件夹中的 UEFormat 动画文件")', pipeline_panel)
        self.assertNotIn('text=t("准备导入计划")', pipeline_panel)
        self.assertNotIn('text=t("刷新动画索引并准备此骨架")', pipeline_panel)
        self.assertNotIn('text=t("扫描动画目录")', pipeline_panel)

    def test_toolbox_exposes_explicit_use_for_animation_action(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")

        self.assertIn('"fmodel.use_active_armature_for_animation"', source)

    def test_toolbox_places_handoff_before_ueformat_model_import(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        handoff = source.index('"fmodel.use_active_armature_for_animation"')
        uemodel = source.index('"fmodel.import_model_uemodel"')

        self.assertLess(handoff, uemodel)
        self.assertIn('layout.label(text=t("动画交接")', source)
        self.assertIn(
            'layout.label(text=t("准备选中的骨架用于动画管线")',
            source,
        )

    def test_material_tools_are_toolbox_only_and_follow_all_model_imports(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        toolbox = source[
            source.index("class FMODEL_PT_Toolbox"):
            source.index("class FMODEL_PT_Pipeline")
        ]

        self.assertNotIn("class FMODEL_PT_Material", source)
        self.assertIn("_draw_material_tools", toolbox)
        material_panel = toolbox.index("material_header, material_body = layout.panel(")
        self.assertLess(toolbox.index('"fmodel.import_model_uemodel"'), material_panel)
        for legacy_operator_id in (
            '"fmodel.import_model_gltf"',
            '"fmodel.import_model_psk"',
            '"fmodel.import_anim_psa"',
            '"fmodel.export_gltf_batch"',
            '"fmodel.export_fbx_batch"',
        ):
            with self.subTest(operator_id=legacy_operator_id):
                self.assertNotIn(legacy_operator_id, toolbox)

    def test_material_tools_use_a_collapsed_root_layout_panel_and_preserve_operators(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        panel_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "layout"
            and node.func.attr == "panel"
        ]

        self.assertTrue(
            any(
                call.args
                and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == "FMODEL_PT_toolbox_material_tools"
                and any(
                    keyword.arg == "default_closed"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in call.keywords
                )
                for call in panel_calls
            ),
        )
        self.assertIn('material_header.label(text=t("材质工具"), icon=\'MATERIAL\')', source)
        for operator_id in (
            "fmodel.analyze_material_resolution",
            "fmodel.apply_material_resolution_v2",
            "fmodel.export_material_report",
            "fmodel.open_material_report",
        ):
            with self.subTest(operator_id=operator_id):
                self.assertIn(f'"{operator_id}"', source)

    def test_material_panel_is_removed_from_ui_registration(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        registration = source[source.index("classes = ("):]

        self.assertNotIn("FMODEL_PT_Material,", registration)

    def test_toolbox_draw_supports_a_collapsed_material_layout_panel(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        toolbox = source[
            source.index("class FMODEL_PT_Toolbox"):
            source.index("class FMODEL_PT_Pipeline")
        ]
        self.assertLess(
            toolbox.index("col_in = split.column"),
            toolbox.index('col_in.operator("fmodel.import_model_uemodel"'),
        )

        tree = ast.parse(source)
        nodes = [
            node for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            and node.name in {"FMODEL_PT_Toolbox", "_draw_material_tools"}
        ]
        namespace = {
            "bpy": types.SimpleNamespace(types=types.SimpleNamespace(Panel=object)),
            "t": lambda text: text,
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "ui.py", "exec"), namespace)

        class Layout:
            def __init__(self):
                self.operator_ids = []
                self.operator_receivers = []
                self.panel_calls = []
                self.events = []

            def row(self, **_kwargs):
                return self

            def prop(self, *_args, **_kwargs):
                pass

            def separator(self):
                pass

            def label(self, **_kwargs):
                pass

            def operator(self, operator_id, **_kwargs):
                self.operator_ids.append(operator_id)
                self.operator_receivers.append((operator_id, "layout"))
                self.events.append(operator_id)
                return types.SimpleNamespace()

            def split(self, **_kwargs):
                return Split(self)

            def panel(self, panel_id, **kwargs):
                self.panel_calls.append((panel_id, kwargs))
                self.events.append("material_panel")
                return self, None

        class Split:
            def __init__(self, layout):
                self.layout = layout
                self.column_count = 0

            def column(self, **_kwargs):
                self.column_count += 1
                return Column(self.layout, "col_in" if self.column_count == 1 else "col_out")

        class Column:
            def __init__(self, layout, receiver):
                self.layout = layout
                self.receiver = receiver

            def operator(self, operator_id, **_kwargs):
                self.layout.operator_ids.append(operator_id)
                self.layout.operator_receivers.append((operator_id, self.receiver))
                self.layout.events.append(operator_id)
                return types.SimpleNamespace()

            def label(self, **_kwargs):
                pass

        def draw(pipeline):
            panel = namespace["FMODEL_PT_Toolbox"]()
            panel.layout = Layout()
            panel.draw(types.SimpleNamespace(scene=types.SimpleNamespace(
                fmodel_core=types.SimpleNamespace(active_pipeline=pipeline),
            )))
            return panel.layout

        panel_layout = draw("UEFORMAT")

        self.assertIn(
            ("fmodel.import_model_uemodel", "col_in"),
            panel_layout.operator_receivers,
        )

        self.assertEqual(
            panel_layout.panel_calls,
            [
                ("FMODEL_PT_toolbox_material_tools", {"default_closed": True}),
                ("FMODEL_PT_toolbox_last_operation", {"default_closed": False}),
            ],
        )
        self.assertLess(
            panel_layout.operator_ids.index("fmodel.import_model_uemodel"),
            panel_layout.operator_ids.index("fmodel.import_anim_ueanim"),
        )
        self.assertLess(
            panel_layout.events.index("fmodel.import_model_uemodel"),
            panel_layout.events.index("material_panel"),
        )
        self.assertLess(
            panel_layout.events.index("fmodel.import_anim_ueanim"),
            panel_layout.events.index("material_panel"),
        )
        legacy_operator_ids = {
            "fmodel.import_model_gltf",
            "fmodel.export_gltf_batch",
            "fmodel.import_model_psk",
            "fmodel.import_anim_psa",
            "fmodel.export_fbx_batch",
        }
        self.assertTrue(legacy_operator_ids.isdisjoint(panel_layout.operator_ids))

        # A .blend saved by the multi-format build may still expose an old enum
        # value through a test double. The UEFormat-only panel ignores it and
        # renders the native operator surface rather than resurrecting legacy UI.
        for legacy_pipeline in ("GLTF", "PSK"):
            with self.subTest(pipeline=legacy_pipeline):
                legacy_layout = draw(legacy_pipeline)
                self.assertIn("fmodel.import_model_uemodel", legacy_layout.operator_ids)
                self.assertTrue(legacy_operator_ids.isdisjoint(legacy_layout.operator_ids))
                self.assertEqual(
                    legacy_layout.panel_calls,
                    [
                ("FMODEL_PT_toolbox_material_tools", {"default_closed": True}),
                ("FMODEL_PT_toolbox_last_operation", {"default_closed": False}),
            ],
                )

    def test_removed_create_and_update_operator_ids_are_not_registered(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        for name in ("Create", "Update"):
            self.assertNotIn(f"FMODEL_OT_{name}ProjectIndex", source)
            self.assertNotIn(
                f'bl_idname = "fmodel.{name.lower()}_project_index"', source,
            )


if __name__ == "__main__":
    unittest.main()
