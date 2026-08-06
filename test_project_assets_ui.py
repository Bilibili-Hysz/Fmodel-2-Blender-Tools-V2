"""Fake-Blender contracts for project-first index controls."""

import ast
import importlib.util
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).parent
MODULE_PATH = ROOT / "Fmodel" / "project_assets.py"
USER_GUIDES = (
    ROOT / "README.md",
    ROOT / "docs" / "compose" / "reports" / "fmodel-tools-next-project-index.md",
    ROOT / "docs" / "compose" / "reports" / "single-model-animation-flow.md",
    ROOT / "docs" / "compose" / "specs" / "2026-07-17-fmodel-tools-next-project-index-design.md",
    ROOT / "docs" / "compose" / "specs" / "2026-07-17-single-model-animation-flow-design.md",
)


class ProjectAssetsUiTests(unittest.TestCase):
    def test_project_index_terminology_in_user_documentation_uses_open_scan_rebuild(self):
        for path in USER_GUIDES:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("Create / Update index", text)
                self.assertNotIn("create/update/rebuild actions", text)
                self.assertTrue(
                    "Open, Scan, and Rebuild" in text or "Open/Scan/Rebuild" in text,
                )

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
            def __init__(inner_self, root):
                inner_self.root = root
                inner_self.path = ":memory:"
                inner_self.calls = []
                self.indexes.append(inner_self)

            def status(inner_self):
                inner_self.calls.append("status")
                return {"state": "ready", "schema_version": 2, "rebuild_required": False}

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

    def test_open_scan_and_rebuild_use_project_root_and_refresh_state(self):
        settings = types.SimpleNamespace(root_path="C:/Project")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))

        for operator_class, expected in (
            (self.module.FMODEL_OT_OpenProjectIndex, "status"),
            (self.module.FMODEL_OT_ScanProjectIndex, ("scan", ["C:/Project"])),
            (self.module.FMODEL_OT_RebuildProjectIndex, "rebuild"),
        ):
            operator = operator_class()
            reports = []
            operator.report = lambda level, message: reports.append((level, message))

            self.assertEqual(operator.execute(context), {"FINISHED"})
            self.assertIn(expected, self.indexes[-1].calls)
            self.assertEqual(settings.index_state, "ready")
            self.assertEqual(settings.index_schema_version, 2)
            self.assertTrue(any("Project index" in message for _, message in reports))

    def test_open_reads_ready_index_without_scanning_or_creating(self):
        settings = types.SimpleNamespace(root_path="C:/Project")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))
        operator = self.module.FMODEL_OT_OpenProjectIndex()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(operator.execute(context), {"FINISHED"})
        self.assertEqual(self.indexes[-1].calls, ["status"])
        self.assertEqual(settings.index_state, "ready")
        self.assertEqual(settings.index_schema_version, 2)
        self.assertEqual(reports, [({"INFO"}, "Project index loaded")])

    def test_scan_creates_missing_index_through_scan_then_reports_scanned(self):
        settings = types.SimpleNamespace(root_path="C:/Project")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))
        indexes = []

        class MissingIndex:
            path = ":memory:"

            def __init__(self, root):
                self.root = root
                self.calls = []
                self.scanned = False
                indexes.append(self)

            def status(self):
                self.calls.append("status")
                if self.scanned:
                    return {"state": "ready", "schema_version": 2, "rebuild_required": False}
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
        self.assertEqual(reports, [({"INFO"}, "Project index scanned")])

    def test_open_rejects_non_ready_index_without_scanning_or_creating(self):
        settings = types.SimpleNamespace(root_path="C:/Project")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))

        for state, message in (
            ("missing", "Project index is missing; scan it"),
            ("rebuild_required", "Rebuild required for project index"),
            ("unavailable", "Project index unavailable"),
        ):
            with self.subTest(state=state):
                indexes = []

                class UnsafeIndex:
                    path = ":memory:"

                    def __init__(self, root):
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
        self.assertEqual(reports, [({"ERROR"}, "Project root is required")])

    def test_operations_cancel_when_project_index_construction_fails(self):
        settings = types.SimpleNamespace(root_path="C:/Unresolvable")
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings))

        def unavailable_index(root):
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
            self.assertEqual(reports, [({"ERROR"}, "Project index unavailable: Cannot resolve C:/Unresolvable")])

    def test_refresh_handles_project_index_construction_failure(self):
        settings = types.SimpleNamespace(
            root_path="C:/Unresolvable",
            index_state="ready",
            index_schema_version=2,
            animation_count=4,
            model_count=3,
        )

        def unavailable_index(root):
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
        self.module.ProjectIndex = lambda root: type("FakeReadyIndex", (), {
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
        self.assertTrue(any("1 animation" in message for _, message in reports))

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
        self.assertEqual(reports, [({"ERROR"}, "A ready project index is required")])

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
        self.assertEqual(reports, [({"ERROR"}, "Unable to apply indexed animation selection")])

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
            self.module.ProjectIndex = lambda root, error=error: type("FailingIndex", (), {
                "status": lambda self: {"state": "ready"},
                "read_only_animations_for_skeleton": lambda self, skeleton, **kwargs: (_ for _ in ()).throw(error),
            })()

            self.assertEqual(operator.execute(context), {"CANCELLED"})
            self.assertEqual(reports[-1][0], {"ERROR"})
            self.assertIn("index", reports[-1][1].lower())
            self.assertNotIn("no indexed animations", reports[-1][1].lower())

    def test_read_only_asset_queries_do_not_create_a_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing.sqlite"
            index = type("ReadyIndex", (), {"path": missing_path})()
            settings = types.SimpleNamespace(root_path="C:/Project", index_state="ready")
            self.module.ProjectIndex = lambda root: type("ReadyIndex", (), {
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
            self.module.ProjectIndex = lambda root: DeletingIndex()

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
                self.module.ProjectIndex = lambda root, state=state, error=error: type("Index", (), {
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
                self.module.ProjectIndex = lambda root, state=state: type("NotReadyIndex", (), {
                    "status": lambda self: {"state": state},
                    "read_only_animations_for_skeleton": lambda self, skeleton, **kwargs: [],
                })()

                self.assertEqual(operator.execute(context), {"CANCELLED"})
                self.assertEqual(reports[-1][0], {"ERROR"})
                self.assertIn("index", reports[-1][1].lower())
                self.assertNotIn("no indexed animations", reports[-1][1].lower())

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
        self.module.ProjectIndex = lambda root: type("EmptyIndex", (), {
            "status": lambda self: {"state": "ready"},
            "read_only_animations_for_skeleton": lambda self, skeleton, **kwargs: [],
        })()
        operator = self.module.FMODEL_OT_RefreshIndexedAnimationChoices()
        reports = []
        operator.report = lambda level, message: reports.append((level, message))

        self.assertEqual(operator.execute(context), {"CANCELLED"})
        self.assertEqual(reports, [({"WARNING"}, "No indexed animations for the selected skeleton")])

    def test_toolbox_shows_project_settings_first_and_animation_pipeline_label(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")

        self.assertIn("FMODEL_PT_ProjectSettings", source)
        self.assertIn('bl_label = "Project Settings"', source)
        self.assertIn('"fmodel.open_project_index", text="Open", icon=\'FILE_FOLDER\'', source)
        self.assertIn('"fmodel.scan_project_index", text="Scan", icon=\'FILE_REFRESH\'', source)
        self.assertIn('"fmodel.rebuild_project_index", text="Rebuild", icon=\'RECOVER_LAST\'', source)
        self.assertIn('bl_label = "Animation Pipeline"', source)
        self.assertLess(
            source.index("FMODEL_PT_ProjectSettings,"),
            source.index("FMODEL_PT_Toolbox,"),
        )

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

    def test_project_settings_draw_is_read_only_for_write_forbidden_settings(self):
        tree = ast.parse((ROOT / "ui.py").read_text(encoding="utf-8"))
        panel_node = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "FMODEL_PT_ProjectSettings"
        )
        namespace = {"bpy": types.SimpleNamespace(types=types.SimpleNamespace(Panel=object))}
        exec(compile(ast.Module(body=[panel_node], type_ignores=[]), "ui.py", "exec"), namespace)

        class WriteForbiddenSettings:
            def __init__(self):
                object.__setattr__(self, "root_path", "")
                object.__setattr__(self, "index_state", "ready")
                object.__setattr__(self, "index_schema_version", 0)
                object.__setattr__(self, "animation_count", 0)
                object.__setattr__(self, "model_count", 0)

            def __setattr__(self, _name, _value):
                raise AssertionError("Panel.draw must not write project settings")

        class Layout:
            def __init__(self):
                self.labels = []

            def label(self, **kwargs):
                self.labels.append(kwargs)

            def prop(self, *_args, **_kwargs):
                pass

            def row(self, **_kwargs):
                return self

            def operator(self, *_args, **_kwargs):
                pass

        panel = namespace["FMODEL_PT_ProjectSettings"]()
        panel.layout = Layout()
        settings = WriteForbiddenSettings()
        panel.draw(types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_project=settings)))

        self.assertIn({"text": "Index: missing", "icon": "FILE_CACHE"}, panel.layout.labels)

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

    def test_pipeline_step2_exposes_source_mode_contract(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        pipeline_panel = source[
            source.index("class FMODEL_PT_Pipeline"):
            source.index("class FMODEL_PT_PipelineDebug")
        ]

        self.assertIn('prop(state, "source_mode"', pipeline_panel)
        self.assertIn('prop(state, "manual_source_dir"', pipeline_panel)
        self.assertIn('fmodel_project", None)', pipeline_panel)

    def test_pipeline_step3_labels_reflect_the_selected_source(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        pipeline_panel = source[
            source.index("class FMODEL_PT_Pipeline"):
            source.index("class FMODEL_PT_PipelineDebug")
        ]

        self.assertIn('text="Prepare Import Plan"', pipeline_panel)
        self.assertIn('text="Validates indexed animations for this skeleton"', pipeline_panel)
        self.assertIn('text="Scan Animation Directory"', pipeline_panel)

    def test_toolbox_exposes_explicit_use_for_animation_action(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")

        self.assertIn('"fmodel.use_active_armature_for_animation"', source)

    def test_toolbox_places_handoff_before_ueformat_model_import(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        handoff = source.index('"fmodel.use_active_armature_for_animation"')
        uemodel = source.index('"fmodel.import_model_uemodel"')

        self.assertLess(handoff, uemodel)
        self.assertIn('layout.label(text="Animation Handoff"', source)
        self.assertIn(
            'layout.label(text="Prepare selected Armature for Animation Pipeline"',
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
        for operator_id in (
            '"fmodel.import_model_uemodel"',
            '"fmodel.import_model_gltf"',
            '"fmodel.import_model_psk"',
        ):
            with self.subTest(operator_id=operator_id):
                self.assertLess(toolbox.index(operator_id), material_panel)

        self.assertLess(
            toolbox.index("elif props.active_pipeline == 'PSK':"),
            material_panel,
        )

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
        self.assertIn('material_header.label(text="Material Tools", icon=\'MATERIAL\')', source)
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
            [("FMODEL_PT_toolbox_material_tools", {"default_closed": True})],
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
        for pipeline, import_operator in (
            ("GLTF", "fmodel.import_model_gltf"),
            ("PSK", "fmodel.import_model_psk"),
        ):
            with self.subTest(pipeline=pipeline):
                pipeline_layout = draw(pipeline)
                self.assertEqual(
                    pipeline_layout.panel_calls,
                    [("FMODEL_PT_toolbox_material_tools", {"default_closed": True})],
                )
                self.assertLess(
                    pipeline_layout.events.index(import_operator),
                    pipeline_layout.events.index("material_panel"),
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
