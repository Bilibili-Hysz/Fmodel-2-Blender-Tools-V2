"""Source-level contracts for the Blender-only animation loader seams."""

import os
import ast
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANIM_MANAGER = os.path.join(ROOT, "Assets", "Anims", "anim_manager.py")
UI = os.path.join(ROOT, "ui.py")
UI_ASSETS = os.path.join(ROOT, "Assets", "ui_assets.py")
_SOURCE_SPEC = importlib.util.spec_from_file_location("animation_source", os.path.join(ROOT, "Assets", "Anims", "animation_source.py"))
_SOURCE_MODULE = importlib.util.module_from_spec(_SOURCE_SPEC)
_SOURCE_SPEC.loader.exec_module(_SOURCE_MODULE)


class AnimationLoaderContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(ANIM_MANAGER, encoding="utf-8") as handle:
            cls.anim_source = handle.read()
        with open(UI, encoding="utf-8") as handle:
            cls.ui_source = handle.read()

    def test_loader_reads_selected_bundle_before_reusing_actions(self):
        loader = self.anim_source.index("def _load_action_for_item")
        bundle_branch = self.anim_source.index("before =", loader)
        read_bundle = self.anim_source.index("bpy.data.libraries.load", bundle_branch)
        reuse = self.anim_source.index("select_existing_action", bundle_branch)
        self.assertLess(read_bundle, reuse)
        self.assertIn('"FModel_SourceBundle"', self.anim_source[loader:])
        self.assertIn('"FModel_SourceSkeleton"', self.anim_source[loader:])

    def test_loader_and_operators_preserve_exact_source_identity(self):
        self.assertIn("parse_source_key(source_key)", self.anim_source)
        self.assertIn("if not action:", self.anim_source)
        self.assertIn("return {'CANCELLED'}", self.anim_source)
        self.assertIn('"source_key": ti.source_key', self.anim_source)
        self.assertIn("inspect_action_metadata", self.ui_source)

    def test_detail_draw_uses_non_mutating_metadata_inspection(self):
        detail = self.ui_source.index("class FMODEL_PT_AnimDetail")
        helpers = self.ui_source.index("# 动画辅助函数", detail)
        detail_source = self.ui_source[detail:helpers]
        self.assertIn("inspect_action_metadata(item)", detail_source)
        self.assertNotIn("_load_action_for_item", detail_source)

    def test_preview_and_apply_do_not_finish_after_loader_failure(self):
        preview = self.anim_source.index("class ANIM_OT_PreviewAnim")
        apply = self.anim_source.index("class ANIM_OT_ApplyAnim")
        self.assertIn("action = _load_action_for_item", self.anim_source[preview:apply])
        self.assertIn("action = _load_action_for_item", self.anim_source[apply:])
        self.assertGreaterEqual(self.anim_source[preview:apply].count("return {'CANCELLED'}"), 1)
        self.assertGreaterEqual(self.anim_source[apply:].count("return {'CANCELLED'}"), 1)

    def test_preview_exception_branch_cancels(self):
        preview = self.anim_source.index("class ANIM_OT_PreviewAnim")
        apply = self.anim_source.index("class ANIM_OT_ApplyAnim")
        branch = self.anim_source[preview:apply]
        self.assertIn("except Exception as e:", branch)
        exception_branch = branch[branch.index("except Exception as e:"):]
        self.assertIn("return {'CANCELLED'}", exception_branch)

    def test_local_legacy_loader_reuses_only_unambiguous_action(self):
        module = ast.parse(self.anim_source)
        function = next(node for node in module.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_load_action_for_item")
        namespace = {
            "bpy": types.SimpleNamespace(data=types.SimpleNamespace(actions=[])),
            "os": os,
            "parse_source_key": lambda value: {},
            "select_existing_action": _SOURCE_MODULE.select_existing_action,
            "_action_prop": lambda action, key, default="": getattr(action, "_values", {}).get(key, default),
            "_tag_action_provenance": lambda action, source_key, blend_path, skeleton: action,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        loader = namespace["_load_action_for_item"]

        only = types.SimpleNamespace(name="Walk", library=None, _values={})
        namespace["bpy"].data.actions[:] = [only]
        item = types.SimpleNamespace(item_type="ANIM", action_name="Walk", name="Walk",
                                     blend_path="", skeleton="Hero", source_key="")
        self.assertIs(loader(item), only)

        namespace["bpy"].data.actions[:] = [only, types.SimpleNamespace(name="Walk", library=None, _values={})]
        self.assertIsNone(loader(item))

    def test_linked_action_reuses_immutable_library_path_when_tags_cannot_be_written(self):
        module = ast.parse(self.anim_source)
        function = next(node for node in module.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_load_action_for_item")
        blend_path = os.path.abspath("selected.blend")
        linked = types.SimpleNamespace(
            name="Walk", library=types.SimpleNamespace(filepath=blend_path), _values={}
        )

        class LibraryLoad:
            def __enter__(self):
                return types.SimpleNamespace(actions=["Walk"]), types.SimpleNamespace(actions=[])

            def __exit__(self, *args):
                return None

        namespace = {
            "bpy": types.SimpleNamespace(
                data=types.SimpleNamespace(
                    actions=[linked],
                    libraries=types.SimpleNamespace(load=lambda path, link: LibraryLoad()),
                )
            ),
            "os": os,
            "parse_source_key": _SOURCE_MODULE.parse_source_key,
            "select_existing_action": _SOURCE_MODULE.select_existing_action,
            "_action_prop": lambda action, key, default="": action._values.get(key, default),
            "_tag_action_provenance": lambda action, *args: action,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        source_key = _SOURCE_MODULE.build_source_key(
            "Heroes", blend_path, "Hero", "Locomotion", "Walk"
        )
        item = types.SimpleNamespace(
            item_type="ANIM", action_name="Walk", name="Walk", blend_path=blend_path,
            skeleton="Hero", source_key=source_key,
        )

        self.assertIs(namespace["_load_action_for_item"](item, use_link=True), linked)

    def test_append_loads_independent_local_action_without_localizing_linked_source(self):
        module = ast.parse(self.anim_source)
        function = next(node for node in module.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_load_action_for_item")
        blend_path = os.path.abspath("selected.blend")

        class Action:
            def __init__(self, name, library):
                self.name = name
                self.library = library
                self._values = {}
                self.make_local_calls = 0

            def make_local(self):
                self.make_local_calls += 1

        linked = Action("Walk", types.SimpleNamespace(filepath=blend_path))
        actions = [linked]
        load_calls = []

        class LibraryLoad:
            def __init__(self):
                self.data_to = types.SimpleNamespace(actions=[])

            def __enter__(self):
                return types.SimpleNamespace(actions=["Walk"]), self.data_to

            def __exit__(self, *args):
                if self.data_to.actions:
                    self.data_to.actions = [Action("Walk", None)]
                    actions.extend(self.data_to.actions)

        namespace = {
            "bpy": types.SimpleNamespace(
                data=types.SimpleNamespace(
                    actions=actions,
                    libraries=types.SimpleNamespace(load=lambda path, link: (
                        load_calls.append((path, link)) or LibraryLoad()
                    )),
                )
            ),
            "os": os,
            "parse_source_key": _SOURCE_MODULE.parse_source_key,
            "select_existing_action": _SOURCE_MODULE.select_existing_action,
            "_action_prop": lambda action, key, default="": action._values.get(key, default),
            "_tag_action_provenance": lambda action, *args: action,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        source_key = _SOURCE_MODULE.build_source_key(
            "Heroes", blend_path, "Hero", "Locomotion", "Walk"
        )
        item = types.SimpleNamespace(
            item_type="ANIM", action_name="Walk", name="Walk", blend_path=blend_path,
            skeleton="Hero", source_key=source_key,
        )

        appended = namespace["_load_action_for_item"](item, use_link=False)

        self.assertIs(appended, actions[-1])
        self.assertIsNone(appended.library)
        self.assertIsNot(appended, linked)
        self.assertIsNotNone(linked.library)
        self.assertEqual(linked.make_local_calls, 0)
        self.assertEqual(load_calls, [(blend_path, False), (blend_path, False)])

    def test_append_returns_data_to_action_despite_same_name_collision(self):
        module = ast.parse(self.anim_source)
        function = next(node for node in module.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_load_action_for_item")
        blend_path = os.path.abspath("selected.blend")

        class Action:
            def __init__(self, name):
                self.name = name
                self.library = None
                self._values = {}

        existing = Action("Walk")
        appended = Action("Walk")
        actions = [existing]

        class LibraryLoad:
            def __init__(self):
                self.data_to = types.SimpleNamespace(actions=[])

            def __enter__(self):
                return types.SimpleNamespace(actions=["Walk"]), self.data_to

            def __exit__(self, *args):
                if self.data_to.actions:
                    self.data_to.actions = [appended]
                    actions.append(appended)

        namespace = {
            "bpy": types.SimpleNamespace(
                data=types.SimpleNamespace(
                    actions=actions,
                    libraries=types.SimpleNamespace(load=lambda path, link: LibraryLoad()),
                )
            ),
            "os": os,
            "parse_source_key": _SOURCE_MODULE.parse_source_key,
            "select_existing_action": _SOURCE_MODULE.select_existing_action,
            "_action_prop": lambda action, key, default="": action._values.get(key, default),
            "_tag_action_provenance": lambda action, *args: action,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        source_key = _SOURCE_MODULE.build_source_key(
            "Heroes", blend_path, "Hero", "Locomotion", "Walk"
        )
        item = types.SimpleNamespace(
            item_type="ANIM", action_name="Walk", name="Walk", blend_path=blend_path,
            skeleton="Hero", source_key=source_key,
        )

        self.assertIs(namespace["_load_action_for_item"](item, use_link=False), appended)

    def test_append_operators_never_make_existing_linked_action_local(self):
        preview = self.anim_source.index("class ANIM_OT_PreviewAnim")
        repair = self.anim_source.index("class ANIM_OT_RepairLibraryMetadata")
        apply_operator = self.anim_source.index("class ANIM_OT_ApplyAnim")
        self.assertNotIn("make_local", self.anim_source[preview:repair])
        self.assertNotIn("make_local", self.anim_source[apply_operator:])

    def test_repair_uses_selected_library_name_when_directory_basename_differs(self):
        module = ast.parse(self.anim_source)
        operator = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ANIM_OT_RepairLibraryMetadata"
        )
        execute = next(
            node for node in operator.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )

        class LibraryLoad:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                actions = ["Idle"] if os.path.basename(self.path) == "Heroes_Hero.blend" else ["Old"]
                return types.SimpleNamespace(actions=actions), types.SimpleNamespace(actions=[])

            def __exit__(self, *args):
                return None

        namespace = {
            "bpy": types.SimpleNamespace(
                path=types.SimpleNamespace(abspath=os.path.abspath),
                data=types.SimpleNamespace(libraries=types.SimpleNamespace(
                    load=lambda path, link: LibraryLoad(path)
                )),
            ),
            "os": os,
            "repair_library_metadata": None,
        }
        exec(compile(ast.Module(body=[execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)

        with tempfile.TemporaryDirectory(prefix="renamed-output-") as directory:
            blend_file = "Heroes_Hero.blend"
            sidecar_file = "Heroes_Hero_manifest.json"
            open(os.path.join(directory, blend_file), "wb").close()
            with open(os.path.join(directory, sidecar_file), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)
            open(os.path.join(directory, "Unrelated.blend"), "wb").close()
            with open(os.path.join(directory, "Unrelated_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Other": {"Base": {"__actions__": [["Old", ""]]}}}, handle)
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "library_name": "Heroes",
                    "bundles": [{
                        "blend_file": blend_file, "sidecar_file": sidecar_file,
                        "skeletons": ["Hero"], "processed_sources": [],
                        "completion_state": "completed",
                    }],
                    "catalog_entries": [], "assets": [],
                }, handle)

            namespace["repair_library_metadata"] = self._load_repair_helper()
            library = types.SimpleNamespace(
                name="Heroes", library_id="persistent-id", path=directory
            )
            context = types.SimpleNamespace(scene=types.SimpleNamespace(
                fmodel_assets=types.SimpleNamespace(project_libs=[library], active_lib_index=0)
            ))
            reports = []
            instance = types.SimpleNamespace(report=lambda *args: reports.append(args))

            self.assertEqual(namespace["execute"](instance, context), {"FINISHED"})
            self.assertTrue(os.path.isfile(index_path))
            self.assertFalse(os.path.exists(os.path.join(
                directory, os.path.basename(directory) + "__library_manifest.json"
            )))
            with open(index_path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(
                [record["blend_file"] for record in repaired["bundles"]],
                [blend_file],
            )

    @staticmethod
    def _load_repair_helper():
        scanner_path = os.path.join(ROOT, "Assets", "Anims", "library_scanner.py")
        spec = importlib.util.spec_from_file_location("repair_scanner_for_operator", scanner_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.repair_library_metadata

    def test_provenance_tagging_tolerates_linked_runtime_error(self):
        module = ast.parse(self.anim_source)
        function = next(node for node in module.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_tag_action_provenance")

        class LinkedAction:
            def __setitem__(self, key, value):
                raise RuntimeError("linked data is read-only")

            def __setattr__(self, key, value):
                if key.startswith("FModel_"):
                    raise RuntimeError("linked data is read-only")
                object.__setattr__(self, key, value)

        namespace = {"os": os}
        exec(compile(ast.Module(body=[function], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        action = LinkedAction()
        self.assertIs(namespace["_tag_action_provenance"](
            action, "key", "bundle.blend", "Hero"
        ), action)

    def test_loader_resolves_blender_relative_library_path(self):
        self.assertIn("bpy.path.abspath(blend_path)", self.anim_source)

    def test_apply_validates_armature_before_loading_action(self):
        apply_operator = self.anim_source.index("class ANIM_OT_ApplyAnim")
        source = self.anim_source[apply_operator:]
        validation = source.index("target_arm.type != 'ARMATURE'")
        load = source.index("action = _load_action_for_item")
        self.assertLess(validation, load)

    def test_tree_identity_is_skeleton_scoped_across_operators_and_filters(self):
        for class_name in ("ANIM_OT_ToggleFolder", "ANIM_OT_PreviewAnim", "ANIM_OT_ApplyAnim"):
            execute = self._operator_method(class_name, "execute")
            source = ast.unparse(execute)
            self.assertIn("target_skeleton", source)
            self.assertIn("_tree_item_matches", source)

        with open(UI_ASSETS, encoding="utf-8") as handle:
            source = handle.read()
        anim_tree = source[source.index("class ASSETS_UL_AnimTree"):source.index("class ASSETS_UL_FolderTree")]
        self.assertIn("collapsed.add((item.skeleton, item.node_id))", anim_tree)
        self.assertIn("(item.skeleton, item.parent_id) in collapsed", anim_tree)

    def test_preview_uses_skeleton_scope_when_node_ids_collide(self):
        execute = self._operator_method("ANIM_OT_PreviewAnim", "execute")
        enemy = types.SimpleNamespace(
            item_type="ANIM", node_id="shared/Walk", source_key="", skeleton="Enemy",
            action_name="Walk", blend_path="enemy.blend",
        )
        hero = types.SimpleNamespace(
            item_type="ANIM", node_id="shared/Walk", source_key="", skeleton="Hero",
            action_name="Walk", blend_path="hero.blend",
        )
        manager = types.SimpleNamespace(tree_items=[enemy, hero], import_mode="APPEND")
        armature = types.SimpleNamespace(name="Hero", type="ARMATURE")
        selected = []
        action = types.SimpleNamespace(frame_range=(1, 10))
        namespace = {
            "bpy": types.SimpleNamespace(data=types.SimpleNamespace(
                actions=[], objects=types.SimpleNamespace(get=lambda name: armature),
            )),
            "_tree_item_matches": lambda item, node, skeleton, source: (
                item.node_id == node and item.skeleton == skeleton
            ),
            "_load_action_for_item": lambda item, use_link: selected.append(item) or action,
            "assign_action": lambda target, loaded: None,
            "remove_owned_action": lambda *args: False,
            "t": lambda message: message,
        }
        exec(compile(ast.Module(body=[execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        operator = types.SimpleNamespace(
            target_node_id="shared/Walk", target_skeleton="Hero", source_key="",
            report=lambda *args: None,
        )
        context = types.SimpleNamespace(
            active_object=armature,
            scene=types.SimpleNamespace(fmodel_anim_manager=manager),
        )

        self.assertEqual(namespace["execute"](operator, context), {"FINISHED"})
        self.assertEqual(selected, [hero])
        self.assertEqual(manager.active_item_index, 1)

    def test_preview_and_apply_use_slot_aware_assignment(self):
        preview = self.anim_source.index("class ANIM_OT_PreviewAnim")
        apply_operator = self.anim_source.index("class ANIM_OT_ApplyAnim")
        self.assertIn("assign_action(arm, action)", self.anim_source[preview:apply_operator])
        self.assertIn("replace_action(target_arm, action)", self.anim_source[apply_operator:])

    def test_apply_reports_safety_rejection_and_cancels(self):
        execute = self._operator_method("ANIM_OT_ApplyAnim", "execute")
        reports = []
        action = types.SimpleNamespace(frame_range=(1, 10))
        item = types.SimpleNamespace(item_type="ANIM", action_name="Walk")
        manager = types.SimpleNamespace(
            tree_items=[item], active_item_index=0, import_mode="APPEND",
            apply_method="REPLACE",
        )
        context = types.SimpleNamespace(
            active_object=types.SimpleNamespace(type="ARMATURE", animation_data=None),
            scene=types.SimpleNamespace(fmodel_anim_manager=manager),
        )
        namespace = {
            "_load_action_for_item": lambda selected, use_link: action,
            "replace_action": lambda target, loaded: (_ for _ in ()).throw(
                RuntimeError("cannot safely replace action while NLA tracks exist")
            ),
            "animation_state": lambda target: target.animation_data,
            "restore_animation_state": lambda target, state: None,
            "remove_owned_action": lambda actions, loaded, baseline: False,
            "bpy": types.SimpleNamespace(data=types.SimpleNamespace(actions=[action])),
            "t": lambda message: message,
        }
        exec(compile(ast.Module(body=[execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        operator = types.SimpleNamespace(
            target_node_id="", source_key="", apply_method_override="AUTO",
            report=lambda levels, message: reports.append((levels, message)),
        )

        result = namespace["execute"](operator, context)

        self.assertEqual(result, {"CANCELLED"})
        self.assertIn("cannot safely replace", reports[-1][1])

    def test_apply_rolls_back_on_any_exception(self):
        execute = self._operator_method("ANIM_OT_ApplyAnim", "execute")
        action = types.SimpleNamespace(frame_range=(1, 10))
        manager = types.SimpleNamespace(
            tree_items=[types.SimpleNamespace(item_type="ANIM", action_name="Walk")],
            active_item_index=0, import_mode="APPEND", apply_method="REPLACE",
        )
        armature = types.SimpleNamespace(type="ARMATURE", animation_data="original")
        restored = []
        cleaned = []
        namespace = {
            "bpy": types.SimpleNamespace(data=types.SimpleNamespace(actions=[action])),
            "_load_action_for_item": lambda selected, use_link: action,
            "replace_action": lambda *args: (_ for _ in ()).throw(KeyError("unexpected")),
            "animation_state": lambda target: target.animation_data,
            "restore_animation_state": lambda target, state: restored.append(state),
            "remove_owned_action": lambda *args: cleaned.append(args),
            "t": lambda message: message,
        }
        exec(compile(ast.Module(body=[execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        operator = types.SimpleNamespace(
            target_node_id="", source_key="", apply_method_override="AUTO",
            report=lambda *args: None,
        )
        context = types.SimpleNamespace(
            active_object=armature,
            scene=types.SimpleNamespace(fmodel_anim_manager=manager),
        )

        self.assertEqual(namespace["execute"](operator, context), {"CANCELLED"})
        self.assertEqual(restored, ["original"])
        self.assertEqual(len(cleaned), 1)

    def test_preview_assignment_failure_removes_only_newly_loaded_action(self):
        execute = self._operator_method("ANIM_OT_PreviewAnim", "execute")
        preexisting = types.SimpleNamespace(name="UserWalk")
        loaded = types.SimpleNamespace(name="LibraryWalk", frame_range=(1, 10))
        actions = [preexisting]
        armature = types.SimpleNamespace(name="Hero", type="ARMATURE")
        item = types.SimpleNamespace(
            item_type="ANIM", action_name="Walk", blend_path="library.blend",
            source_key="source", node_id="node",
        )
        manager = types.SimpleNamespace(tree_items=[item], import_mode="APPEND")
        context = types.SimpleNamespace(
            active_object=armature,
            scene=types.SimpleNamespace(fmodel_anim_manager=manager),
        )

        def load_action(selected, use_link):
            actions.append(loaded)
            return loaded

        namespace = {
            "bpy": types.SimpleNamespace(data=types.SimpleNamespace(
                actions=actions,
                objects=types.SimpleNamespace(get=lambda name: armature),
            )),
            "_load_action_for_item": load_action,
            "_tree_item_matches": lambda item, node, skeleton, source: item.source_key == source,
            "assign_action": lambda target, action: (_ for _ in ()).throw(
                RuntimeError("slot rejected")
            ),
            "remove_owned_action": self._remove_owned_action,
            "t": lambda message: message,
        }
        exec(compile(ast.Module(body=[execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        operator = types.SimpleNamespace(
            target_node_id="node", target_skeleton="", source_key="source",
            report=lambda *args: None,
        )

        self.assertEqual(namespace["execute"](operator, context), {"CANCELLED"})
        self.assertEqual(actions, [preexisting])

    def test_apply_rejection_preserves_preexisting_action(self):
        execute = self._operator_method("ANIM_OT_ApplyAnim", "execute")
        action = types.SimpleNamespace(name="UserWalk", frame_range=(1, 10))
        actions = [action]
        item = types.SimpleNamespace(item_type="ANIM", action_name="Walk")
        manager = types.SimpleNamespace(
            tree_items=[item], active_item_index=0, import_mode="APPEND",
            apply_method="REPLACE",
        )
        context = types.SimpleNamespace(
            active_object=types.SimpleNamespace(type="ARMATURE", animation_data=None),
            scene=types.SimpleNamespace(fmodel_anim_manager=manager),
        )
        namespace = {
            "bpy": types.SimpleNamespace(data=types.SimpleNamespace(actions=actions)),
            "_load_action_for_item": lambda selected, use_link: action,
            "replace_action": lambda target, loaded: (_ for _ in ()).throw(
                RuntimeError("slot rejected")
            ),
            "animation_state": lambda target: target.animation_data,
            "restore_animation_state": lambda target, state: None,
            "remove_owned_action": self._remove_owned_action,
            "t": lambda message: message,
        }
        exec(compile(ast.Module(body=[execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        operator = types.SimpleNamespace(
            target_node_id="", source_key="", apply_method_override="AUTO",
            report=lambda *args: None,
        )

        self.assertEqual(namespace["execute"](operator, context), {"CANCELLED"})
        self.assertEqual(actions, [action])

    def test_append_nla_slot_rejection_removes_new_action_and_new_animation_data(self):
        execute = self._operator_method("ANIM_OT_ApplyAnim", "execute")
        loaded = types.SimpleNamespace(name="LibraryWalk", frame_range=(1, 10))
        actions = []

        class Armature:
            name = "Hero"
            type = "ARMATURE"
            animation_data = None

            def animation_data_create(self):
                self.animation_data = types.SimpleNamespace(nla_tracks=[])
                return self.animation_data

            def animation_data_clear(self):
                self.animation_data = None

        armature = Armature()
        item = types.SimpleNamespace(item_type="ANIM", action_name="Walk")
        manager = types.SimpleNamespace(
            tree_items=[item], active_item_index=0, import_mode="APPEND",
            apply_method="APPEND_NLA", nla_insert_mode="END",
        )
        scene = types.SimpleNamespace(
            fmodel_anim_manager=manager, frame_current=1, frame_end=10,
        )

        def load_action(selected, use_link):
            actions.append(loaded)
            return loaded

        namespace = {
            "bpy": types.SimpleNamespace(data=types.SimpleNamespace(actions=actions)),
            "_load_action_for_item": load_action,
            "append_nla_strip_to_new_track": lambda *args: (_ for _ in ()).throw(
                RuntimeError("action has no slot for armature Hero")
            ),
            "animation_state": lambda obj: None if obj.animation_data is None else obj.animation_data,
            "restore_animation_state": lambda obj, state: obj.animation_data_clear() if state is None else None,
            "remove_owned_action": self._remove_owned_action,
            "t": lambda message: message,
        }
        exec(compile(ast.Module(body=[execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        operator = types.SimpleNamespace(
            target_node_id="", source_key="", apply_method_override="AUTO",
            report=lambda *args: None,
        )

        self.assertEqual(namespace["execute"](operator, types.SimpleNamespace(
            active_object=armature, scene=scene,
        )), {"CANCELLED"})
        self.assertIsNone(armature.animation_data)
        self.assertEqual(actions, [])

    def test_apply_rejects_negative_active_item_index_without_loading(self):
        execute = self._operator_method("ANIM_OT_ApplyAnim", "execute")
        load_calls = []
        reports = []
        manager = types.SimpleNamespace(
            tree_items=[types.SimpleNamespace(item_type="ANIM")],
            active_item_index=-1, import_mode="APPEND",
        )
        context = types.SimpleNamespace(
            active_object=types.SimpleNamespace(type="ARMATURE"),
            scene=types.SimpleNamespace(fmodel_anim_manager=manager),
        )
        namespace = {
            "_load_action_for_item": lambda *args: load_calls.append(args),
            "t": lambda message: message,
        }
        exec(compile(ast.Module(body=[execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        operator = types.SimpleNamespace(
            target_node_id="", source_key="", apply_method_override="AUTO",
            report=lambda levels, message: reports.append((levels, message)),
        )

        self.assertEqual(namespace["execute"](operator, context), {"CANCELLED"})
        self.assertEqual(load_calls, [])
        self.assertTrue(reports)

    def test_repair_operator_rejects_negative_active_library_index(self):
        poll = self._operator_method("ANIM_OT_RepairLibraryMetadata", "poll")
        execute = self._operator_method("ANIM_OT_RepairLibraryMetadata", "execute")
        props = types.SimpleNamespace(
            project_libs=[types.SimpleNamespace(path="valid")], active_lib_index=-1
        )
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_assets=props))
        namespace = {"os": os}
        exec(compile(ast.Module(body=[poll, execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        reports = []
        operator = types.SimpleNamespace(report=lambda levels, message: reports.append((levels, message)))

        self.assertFalse(namespace["poll"].__func__(None, context))
        self.assertEqual(namespace["execute"](operator, context), {"CANCELLED"})
        self.assertTrue(reports)

    def test_scan_restores_expansion_state_for_existing_nodes(self):
        module = ast.parse(self.anim_source)
        functions = [
            next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name)
            for name in (
                "_tree_expansion_key", "_tree_expansion_state", "_restore_tree_expansion_state",
                "traverse_tree",
            )
        ]
        scan_class = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "ANIM_OT_ScanAnims"
        )
        execute = next(
            node for node in scan_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )

        class Items(list):
            def add(self):
                item = types.SimpleNamespace(
                    name="", item_type="", lib_type="", node_id="", parent_id="",
                    skeleton="", blend_path="", action_name="", source_key="",
                    thumb_path="", depth=0, is_expanded=False, anim_count=0, volume_count=0,
                )
                self.append(item)
                return item

            def clear(self):
                del self[:]

        manager = types.SimpleNamespace(
            tree_items=Items(), skeleton_items=Items(), active_item_index=0,
            active_library_index=0, active_folder_index=0, tree_revision=0,
        )
        library_root = tempfile.TemporaryDirectory()
        self.addCleanup(library_root.cleanup)
        library_paths = [
            os.path.join(library_root.name, "FirstLibrary"),
            os.path.join(library_root.name, "SecondLibrary"),
        ]
        for library_path in library_paths:
            os.makedirs(library_path, exist_ok=True)
        libraries = [types.SimpleNamespace(name="Library", path=library_path) for library_path in library_paths]
        context = types.SimpleNamespace(scene=types.SimpleNamespace(
            fmodel_anim_manager=manager,
            fmodel_assets=types.SimpleNamespace(project_libs=libraries),
        ))
        bpy = types.SimpleNamespace(path=types.SimpleNamespace(abspath=lambda path: path))
        namespace = {
            "bpy": bpy,
            "os": os,
            "t": lambda message: message,
            "reset_active_skeleton_sync": lambda: None,
            "build_source_key": lambda *parts: ":".join(parts),
            "_scan_library_directory": lambda path, *_args: ([
                ("Moves", [os.path.join(path, "moves.blend")], 1, {
                    "Hero": {"Locomotion": {"__actions__": [("Walk", "moves.blend", "")]}}
                }, []),
            ], []),
        }
        exec(compile(ast.Module(body=functions + [execute], type_ignores=[]), ANIM_MANAGER, "exec"), namespace)
        operator = types.SimpleNamespace(report=lambda *_args: None)

        self.assertEqual(namespace["execute"](operator, context), {"FINISHED"})
        for item in manager.tree_items:
            if item.blend_path == library_paths[0]:
                item.is_expanded = False
            elif item.blend_path.startswith(library_paths[1]):
                item.is_expanded = True

        self.assertEqual(namespace["execute"](operator, context), {"FINISHED"})
        expanded = {
            (item.item_type, item.blend_path, item.node_id, item.skeleton): item.is_expanded
            for item in manager.tree_items
        }
        self.assertFalse(expanded[("LIBRARY", library_paths[0], "lib_Library", "")])
        self.assertTrue(expanded[("LIBRARY", library_paths[1], "lib_Library", "")])
        self.assertFalse(expanded[("ANIM_LIB", library_paths[0], "anim_Library_Moves", "")])
        self.assertTrue(expanded[("ANIM_LIB", library_paths[1], "anim_Library_Moves", "")])
        self.assertFalse(expanded[("FOLDER", library_paths[0], "anim_Library_Moves/Locomotion", "Hero")])
        self.assertTrue(expanded[("FOLDER", library_paths[1], "anim_Library_Moves/Locomotion", "Hero")])

    def test_library_access_paths_reject_negative_active_index(self):
        sources = {
            ANIM_MANAGER: (
                "ANIM_OT_BrowseLibPath", "ANIM_OT_OpenLibFolder",
                "ANIM_OT_RepairLibraryMetadata",
            ),
            UI: ("FMODEL_OT_RemoveLibrary",),
        }
        for path, class_names in sources.items():
            module = ast.parse(Path(path).read_text(encoding="utf-8"))
            for class_name in class_names:
                operator = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == class_name)
                source = ast.unparse(operator)
                self.assertIn("0 <= props.active_lib_index < len(props.project_libs)", source)

        ui_module = ast.parse(Path(UI).read_text(encoding="utf-8"))
        select_operator = next(
            node for node in ui_module.body
            if isinstance(node, ast.ClassDef) and node.name == "FMODEL_OT_SelectTreeNode"
        )
        self.assertIn("0 <= self.lib_index < len(props.project_libs)", ast.unparse(select_operator))

    def test_removing_last_library_clamps_active_index_to_zero(self):
        module = ast.parse(Path(UI).read_text(encoding="utf-8"))
        operator = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "FMODEL_OT_RemoveLibrary"
        )
        execute = next(
            node for node in operator.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )

        class Libraries(list):
            def remove(self, index):
                del self[index]

        props = types.SimpleNamespace(project_libs=Libraries([object()]), active_lib_index=0)
        context = types.SimpleNamespace(scene=types.SimpleNamespace(fmodel_assets=props))
        namespace = {"_sync_manager_library_index": lambda _context: None}
        exec(compile(ast.Module(body=[execute], type_ignores=[]), UI, "exec"), namespace)

        self.assertEqual(namespace["execute"](types.SimpleNamespace(), context), {"FINISHED"})
        self.assertEqual(props.active_lib_index, 0)

    def test_project_library_operators_sync_manager_selection(self):
        with open(UI, encoding="utf-8") as handle:
            ui_module = ast.parse(handle.read())
        for class_name in ("FMODEL_OT_AddLibrary", "FMODEL_OT_RemoveLibrary"):
            operator = next(
                node for node in ui_module.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            execute = next(
                node for node in operator.body
                if isinstance(node, ast.FunctionDef) and node.name == "execute"
            )
            self.assertIn("_sync_manager_library_index", ast.unparse(execute))

        anim_module = ast.parse(self.anim_source)
        mount = next(
            node for node in anim_module.body
            if isinstance(node, ast.ClassDef) and node.name == "ANIM_OT_MountBlendFile"
        )
        execute = next(
            node for node in mount.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )
        self.assertIn("_sync_manager_library_index", ast.unparse(execute))

    def _operator_method(self, class_name, method_name):
        module = ast.parse(self.anim_source)
        operator = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        return next(
            node for node in operator.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )

    @staticmethod
    def _remove_owned_action(actions, action, baseline):
        if action not in baseline and action in actions:
            actions.remove(action)


if __name__ == "__main__":
    unittest.main()
