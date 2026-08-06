"""Regression tests for animation import transaction rollbacks."""

import ast
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from core import library_files


ROOT = Path(__file__).parent
ANIM_MANAGER = ROOT / "Assets" / "Anims" / "anim_manager.py"
PIPELINE_STAGES = ROOT / "Fmodel" / "Pipeline" / "stages.py"


def load_definitions(path, names, globals_):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = [
        node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names
    ]
    module = ast.Module(body=definitions, type_ignores=[])
    exec(compile(module, str(path), "exec"), globals_)


class ActionCollection(list):
    def get(self, name):
        return next((action for action in self if action.name == name), None)

    def remove(self, action):
        super().remove(action)


class FakeStrips(list):
    def __init__(self, fail_after_create=False):
        super().__init__()
        self.fail_after_create = fail_after_create

    def new(self, name, frame_start, action):
        strip = SimpleNamespace(
            name=name,
            frame_start=float(frame_start),
            frame_end=float(frame_start + 10),
            action=action,
            select=False,
        )
        self.append(strip)
        if self.fail_after_create:
            raise TypeError("fault after strip creation")
        return strip


class FakeTracks(list):
    def __init__(self, fail_after_create=False):
        super().__init__()
        self.active = None
        self.fail_after_create = fail_after_create

    def new(self):
        track = SimpleNamespace(
            name="",
            select=False,
            mute=False,
            is_solo=False,
            lock=False,
            strips=FakeStrips(self.fail_after_create),
        )
        self.append(track)
        return track

    def remove(self, track):
        super().remove(track)


class FailingAnimationData:
    def __init__(self, action, action_slot, tracks, fail_action_assignment=False):
        self._action = action
        self._action_slot = action_slot
        self.nla_tracks = tracks
        self.fail_action_assignment = fail_action_assignment

    @property
    def action(self):
        return self._action

    @action.setter
    def action(self, value):
        self._action = value
        if self.fail_action_assignment:
            self.fail_action_assignment = False
            raise AttributeError("fault after action assignment")

    @property
    def action_slot(self):
        return self._action_slot

    @action_slot.setter
    def action_slot(self, value):
        self._action_slot = value


class FakeOperator:
    def report(self, _level, _message):
        pass


class AnimationOperatorTransactionTests(unittest.TestCase):
    def setUp(self):
        self.old_action = SimpleNamespace(name="Old", frame_range=(2, 8), library=None)
        self.new_action = SimpleNamespace(name="New", frame_range=(10, 20), library=None)
        self.old_slot = object()
        self.old_strip = SimpleNamespace(
            name="OldStrip", frame_start=1.0, frame_end=9.0,
            action=self.old_action, select=True,
        )

    def _globals(self, actions):
        fake_os = SimpleNamespace(
            path=SimpleNamespace(exists=lambda _path: True, basename=lambda path: path.rsplit("/", 1)[-1])
        )
        bpy = SimpleNamespace(
            types=SimpleNamespace(Operator=FakeOperator),
            props=SimpleNamespace(
                StringProperty=lambda **_kwargs: None,
                EnumProperty=lambda **_kwargs: None,
            ),
            data=SimpleNamespace(actions=actions, objects=SimpleNamespace(get=lambda _name: None)),
        )
        globals_ = {"bpy": bpy, "os": fake_os, "t": lambda value: value}
        load_definitions(
            ANIM_MANAGER,
            {"_AnimationTransaction", "ANIM_OT_PreviewAnim", "ANIM_OT_ApplyAnim"},
            globals_,
        )
        return globals_, bpy

    def _context(self, anim_data, manager):
        arm = SimpleNamespace(name="Arm", type="ARMATURE", animation_data=anim_data)
        scene = SimpleNamespace(
            fmodel_anim_manager=manager,
            frame_start=1,
            frame_end=100,
            frame_current=7,
        )
        return SimpleNamespace(active_object=arm, scene=scene), arm

    def test_preview_rolls_back_action_slot_scene_and_owned_action_on_type_error(self):
        actions = ActionCollection([self.old_action])
        globals_, bpy = self._globals(actions)
        bpy.data.objects.get = lambda _name: context.active_object
        selected = SimpleNamespace(
            node_id="node", item_type="ANIM", action_name="New", blend_path="library.blend"
        )
        manager = SimpleNamespace(tree_items=[selected], import_mode="LINK", active_item_index=0)
        tracks = FakeTracks()
        anim_data = FailingAnimationData(self.old_action, self.old_slot, tracks, True)
        context, arm = self._context(anim_data, manager)

        class LibraryLoad:
            def __enter__(self):
                self.data_to = SimpleNamespace(actions=[])
                return SimpleNamespace(actions=["New"]), self.data_to

            def __exit__(self, *_args):
                actions.append(self_new_action)

        self_new_action = self.new_action
        bpy.data.libraries = SimpleNamespace(load=lambda *_args, **_kwargs: LibraryLoad())
        operator = globals_["ANIM_OT_PreviewAnim"]()
        operator.target_node_id = "node"

        result = operator.execute(context)

        self.assertEqual(result, {"CANCELLED"})
        self.assertIs(arm.animation_data.action, self.old_action)
        self.assertIs(arm.animation_data.action_slot, self.old_slot)
        self.assertEqual((context.scene.frame_start, context.scene.frame_end, context.scene.frame_current), (1, 100, 7))
        self.assertEqual(actions, [self.old_action])

    def test_apply_rolls_back_nla_scene_and_owned_action_after_strip_creation_error(self):
        actions = ActionCollection([self.old_action, self.new_action])
        globals_, _bpy = self._globals(actions)
        selected = SimpleNamespace(
            node_id="node", item_type="ANIM", action_name="New", blend_path=""
        )
        manager = SimpleNamespace(
            tree_items=[selected], active_item_index=0, import_mode="LINK",
            apply_method="APPEND_NLA", nla_insert_mode="CURRENT",
            nla_overwrite_scope="ALL_OVERLAPS", nla_auto_focus_new_strip=False,
        )
        tracks = FakeTracks()
        old_track = tracks.new()
        old_track.name = "OldTrack"
        old_track.strips.fail_after_create = False
        old_track.strips.append(self.old_strip)
        anim_data = FailingAnimationData(self.old_action, self.old_slot, tracks)
        context, arm = self._context(anim_data, manager)
        operator = globals_["ANIM_OT_ApplyAnim"]()
        operator.target_node_id = "node"
        operator.apply_method_override = "APPEND_NLA"
        operator._focus_new_strip = lambda *_args: (_ for _ in ()).throw(
            TypeError("fault after strip creation")
        )

        result = operator.execute(context)

        self.assertEqual(result, {"CANCELLED"})
        self.assertIs(arm.animation_data.action, self.old_action)
        self.assertIs(arm.animation_data.action_slot, self.old_slot)
        self.assertEqual((context.scene.frame_start, context.scene.frame_end, context.scene.frame_current), (1, 100, 7))
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].name, "OldTrack")
        self.assertEqual([strip.name for strip in tracks[0].strips], ["OldStrip"])

    def test_apply_rollback_preserves_fractional_nla_strip_start(self):
        actions = ActionCollection([self.old_action, self.new_action])
        globals_, _bpy = self._globals(actions)
        selected = SimpleNamespace(
            node_id="node", item_type="ANIM", action_name="New", blend_path=""
        )
        manager = SimpleNamespace(
            tree_items=[selected], active_item_index=0, import_mode="LINK",
            apply_method="APPEND_NLA", nla_insert_mode="CURRENT",
            nla_overwrite_scope="ALL_OVERLAPS", nla_auto_focus_new_strip=False,
        )
        tracks = FakeTracks()
        old_track = tracks.new()
        old_track.name = "OldTrack"
        self.old_strip.frame_start = 1.25
        old_track.strips.append(self.old_strip)
        anim_data = FailingAnimationData(self.old_action, self.old_slot, tracks)
        context, _arm = self._context(anim_data, manager)
        operator = globals_["ANIM_OT_ApplyAnim"]()
        operator.target_node_id = "node"
        operator.apply_method_override = "APPEND_NLA"
        operator._focus_new_strip = lambda *_args: (_ for _ in ()).throw(
            TypeError("fault after strip creation")
        )

        result = operator.execute(context)

        self.assertEqual(result, {"CANCELLED"})
        self.assertEqual(tracks[0].strips[0].frame_start, 1.25)

    def test_apply_rejects_missing_target_before_constructing_transaction(self):
        actions = ActionCollection([self.new_action])
        globals_, _bpy = self._globals(actions)
        selected = SimpleNamespace(
            node_id="node", item_type="ANIM", action_name="New", blend_path=""
        )
        manager = SimpleNamespace(tree_items=[selected], active_item_index=0, import_mode="LINK")
        context = SimpleNamespace(
            active_object=None,
            scene=SimpleNamespace(fmodel_anim_manager=manager),
        )
        globals_["_AnimationTransaction"] = lambda *_args: self.fail(
            "transaction should not be constructed for an invalid target"
        )
        operator = globals_["ANIM_OT_ApplyAnim"]()
        operator.target_node_id = "node"
        operator.apply_method_override = "REPLACE"

        result = operator.execute(context)

        self.assertEqual(result, {"CANCELLED"})

    def test_apply_removes_animation_data_created_by_failed_operation(self):
        actions = ActionCollection([self.new_action])
        globals_, _bpy = self._globals(actions)
        selected = SimpleNamespace(
            node_id="node", item_type="ANIM", action_name="New", blend_path=""
        )
        manager = SimpleNamespace(
            tree_items=[selected], active_item_index=0, import_mode="LINK",
            apply_method="APPEND_NLA", nla_insert_mode="CURRENT",
            nla_overwrite_scope="ALL_OVERLAPS", nla_auto_focus_new_strip=False,
        )
        context, arm = self._context(None, manager)
        arm.animation_data_create = lambda: setattr(
            arm, "animation_data", FailingAnimationData(None, None, FakeTracks())
        )
        arm.animation_data_clear = lambda: setattr(arm, "animation_data", None)
        operator = globals_["ANIM_OT_ApplyAnim"]()
        operator.target_node_id = "node"
        operator.apply_method_override = "APPEND_NLA"
        operator._focus_new_strip = lambda *_args: (_ for _ in ()).throw(
            AttributeError("fault after strip creation")
        )

        result = operator.execute(context)

        self.assertEqual(result, {"CANCELLED"})
        self.assertIsNone(arm.animation_data)


class LinkExistingTransactionTests(unittest.TestCase):
    def test_incomplete_load_removes_only_actions_introduced_by_failed_file(self):
        class Action(SimpleNamespace):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.properties = {}

            def get(self, name, default=None):
                return self.properties.get(name, default)

            def __setitem__(self, name, value):
                self.properties[name] = value

        existing = Action(name="Existing", frame_range=(1, 2))
        first = Action(name="First", frame_range=(1, 3))
        partial = Action(name="Partial", frame_range=(1, 4))
        actions = ActionCollection([existing])

        class LibraryLoad:
            def __enter__(self):
                self.data_to = SimpleNamespace(actions=[])
                return SimpleNamespace(actions=["First", "Partial"]), self.data_to

            def __exit__(self, *_args):
                actions.extend([first, partial])
                self.data_to.actions[:] = [first, partial]

        bpy = SimpleNamespace(
            path=SimpleNamespace(abspath=lambda value: value),
            data=SimpleNamespace(
                actions=actions,
                libraries=SimpleNamespace(load=lambda *_args, **_kwargs: LibraryLoad()),
            ),
        )

        class ImportedAction:
            calls = 0

            def __init__(self, **kwargs):
                type(self).calls += 1
                if type(self).calls == 2:
                    raise AttributeError("metadata fault")
                self.__dict__.update(kwargs)

        class ImportedAssetCollection:
            def __init__(self, import_mode, task_identity):
                self.import_mode = import_mode
                self.task_identity = task_identity
                self.imported_actions = []
                self.failed_imports = []

            def to_dict(self):
                return {
                    "imported_actions": list(self.imported_actions),
                    "failed_imports": list(self.failed_imports),
                }

        globals_ = {
            "bpy": bpy,
            "os": os,
            "json": json,
            "PipelineStageBase": object,
            "PipelineContext": object,
            "ImportedAction": ImportedAction,
            "ImportedAssetCollection": ImportedAssetCollection,
            "ImportMode": SimpleNamespace(LINK_EXISTING=SimpleNamespace(value="link_existing")),
            "copy_action_for_pipeline": lambda action: action,
        }
        load_definitions(PIPELINE_STAGES, {"AssetImportStage"}, globals_)
        stage = globals_["AssetImportStage"]()
        stage.get_stage_name = lambda: "import"
        stage._source_skeleton = lambda context: str(
            context.get_config("source_skeleton", "")
            or context.get_config("skeleton_filter", "")
            or ""
        )
        stage._task_identity = lambda _context: {"source_subset": []}
        stage._normalized_source_path = lambda path: os.path.abspath(path)

        with tempfile.TemporaryDirectory() as source_dir:
            Path(source_dir, "library.blend").write_text("blend", encoding="utf-8")
            Path(source_dir, "library_manifest.json").write_text(
                json.dumps({"Hero": {"__actions__": [["First", ""], ["Partial", ""]]}}),
                encoding="utf-8",
            )
            context = SimpleNamespace(
                imported_assets=None,
                get_config=lambda name, default="": source_dir if name == "source_dir" else default,
                log_error=lambda *_args: None,
                log_warning=lambda *_args: None,
                log_info=lambda *_args: None,
            )

            result = stage._import_link_existing(context)

        self.assertFalse(result)
        self.assertEqual(actions, [existing])
        self.assertEqual(context.imported_assets["imported_actions"], [])
        self.assertEqual(len(context.imported_assets["failed_imports"]), 1)


class LibraryBundleTransactionTests(unittest.TestCase):
    def test_sidecar_write_failure_rolls_back_bundle_and_index_references(self):
        with tempfile.TemporaryDirectory() as output_dir:
            bundle_path = Path(output_dir, "Library_Hero.blend")
            sidecar_path = Path(output_dir, "Library_Hero_manifest.json")
            index_path = Path(output_dir, "Library__library_manifest.json")
            temporary_blend = Path(output_dir, ".Library_Hero.tmp.blend")
            temporary_blend.write_text("blend", encoding="utf-8")
            original_write_json_temp = library_files._write_json_temp

            def fail_sidecar_write(destination, payload):
                if destination == str(sidecar_path):
                    raise OSError("sidecar write failed")
                return original_write_json_temp(destination, payload)

            library_files._write_json_temp = fail_sidecar_write
            try:
                with self.assertRaises(OSError):
                    library_files.publish_rebuild_replacement_set(
                        str(index_path),
                        [(str(temporary_blend), str(bundle_path), str(sidecar_path), {"Hero": {}})],
                        {"version": "2.0", "bundles": []},
                    )
            finally:
                library_files._write_json_temp = original_write_json_temp

            self.assertFalse(bundle_path.exists())
            self.assertFalse(sidecar_path.exists())
            self.assertFalse(index_path.exists())
            self.assertFalse(Path(f"{index_path}.txn.json").exists())

    def test_scanner_recovers_blends_without_same_stem_sidecars(self):
        globals_ = {
            "os": __import__("os"),
            "re": __import__("re"),
            "_VOL_PATTERN": __import__("re").compile(r"(.+)_Vol_\d+$", __import__("re").IGNORECASE),
        }
        load_definitions(
            ANIM_MANAGER,
            {"_get_base_name", "_scan_library_directory"},
            globals_,
        )
        globals_["_load_library_index"] = lambda *_args: (None, "metadata_missing")
        globals_["retained_bundle_files"] = lambda _index: None
        globals_["_load_bundle_tree"] = lambda *_args: ({"Hero": {"__actions__": [["Walk", ""]]}}, "metadata_missing")
        globals_["merge_dict"] = lambda destination, source, _path: destination.update(source)
        globals_["_count_actions_recursive"] = lambda _tree: 1
        class LibraryLoad:
            def __enter__(self):
                return SimpleNamespace(actions=["Walk"]), SimpleNamespace()

            def __exit__(self, *_args):
                return False

        globals_["bpy"] = SimpleNamespace(
            data=SimpleNamespace(libraries=SimpleNamespace(
                load=lambda *_args, **_kwargs: LibraryLoad()
            ))
        )

        with tempfile.TemporaryDirectory() as library_dir:
            library = Path(library_dir)
            (library / "Complete.blend").write_text("blend", encoding="utf-8")
            (library / "Complete_manifest.json").write_text("{}", encoding="utf-8")
            (library / "Incomplete.blend").write_text("blend", encoding="utf-8")

            entries, _models = globals_["_scan_library_directory"](library_dir, "Library")

            self.assertEqual([entry[0] for entry in entries], ["Complete", "Incomplete"])
            self.assertEqual([entry[2] for entry in entries], [1, 1])
            self.assertEqual(entries[1][4], [("Incomplete.blend", "metadata_missing")])


if __name__ == "__main__":
    unittest.main()
