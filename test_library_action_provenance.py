"""Pure/fake-bpy contracts for staged library action provenance."""

import importlib.util
import os
import tempfile
import unittest


_SPEC = importlib.util.spec_from_file_location(
    "action_selection", os.path.join(os.path.dirname(__file__), "core", "action_selection.py")
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
resolve_imported_action = _MODULE.resolve_imported_action


class FakeAction(dict):
    def __init__(self, name, source):
        super().__init__(Pipeline_Source=source)
        self.name = name


class RejectPropertyWriteAction(FakeAction):
    def __setitem__(self, name, value):
        if name == "Pipeline_StagedBlend":
            raise RuntimeError("properties are read-only")
        super().__setitem__(name, value)


class FakeLibraryLoad:
    def __init__(self, bpy, path):
        self.bpy = bpy
        self.path = path
        self.data_from = type("DataFrom", (), {"actions": [action.name for action in bpy.bundles[path]]})()
        self.data_to = type("DataTo", (), {"actions": []})()

    def __enter__(self):
        return self.data_from, self.data_to

    def __exit__(self, exc_type, exc, traceback):
        requested = list(self.data_to.actions)
        self.data_to.actions = [
            action for action in self.bpy.bundles[self.path]
            if action.name in requested
        ]
        self.bpy.data.actions.extend(self.data_to.actions)


class FakeBpy:
    def __init__(self, actions, bundles):
        self.bundles = bundles
        self.data = type("Data", (), {})()
        self.data.actions = actions
        self.data.libraries = type("Libraries", (), {
            "load": lambda _, path, link=False: FakeLibraryLoad(self, path)
        })()


class LibraryActionProvenanceTests(unittest.TestCase):
    def test_same_named_actions_load_from_each_tasks_staged_blend(self):
        with tempfile.TemporaryDirectory() as directory:
            first_blend = os.path.join(directory, "Hero_chunk.blend")
            second_blend = os.path.join(directory, "Enemy_chunk.blend")
            for path in (first_blend, second_blend):
                with open(path, "wb"):
                    pass
            hero = FakeAction("Idle", "C:/Anims/Hero/Idle.ueanim")
            enemy = FakeAction("Idle", "C:/Anims/Enemy/Idle.ueanim")
            hero["Pipeline_SourceRef"] = "Hero/Locomotion/Idle"
            enemy["Pipeline_SourceRef"] = "Enemy/Locomotion/Idle"
            unrelated = FakeAction("Idle", "C:/Anims/Other/Idle.ueanim")
            bpy = FakeBpy([unrelated], {first_blend: [hero], second_blend: [enemy]})

            selected_hero, hero_error = resolve_imported_action(bpy, {
                "action_name": "Idle",
                "source_file": "C:/Anims/Hero/Idle.ueanim",
                "source_manifest_ref": "Hero/Locomotion/Idle",
                "staged_blend": first_blend,
            })
            selected_enemy, enemy_error = resolve_imported_action(bpy, {
                "action_name": "Idle",
                "source_file": "C:/Anims/Enemy/Idle.ueanim",
                "source_manifest_ref": "Enemy/Locomotion/Idle",
                "staged_blend": second_blend,
            })

            self.assertIs(selected_hero, hero)
            self.assertIs(selected_enemy, enemy)
            self.assertEqual((hero_error, enemy_error), ("", ""))
            self.assertEqual(hero["Pipeline_StagedBlend"], os.path.normcase(os.path.abspath(first_blend)))
            self.assertEqual(enemy["Pipeline_StagedBlend"], os.path.normcase(os.path.abspath(second_blend)))
            self.assertNotIn("Pipeline_StagedBlend", unrelated)

    def test_ambiguous_staged_bundle_fails_instead_of_using_global_action(self):
        with tempfile.TemporaryDirectory() as directory:
            staged_blend = os.path.join(directory, "ambiguous.blend")
            with open(staged_blend, "wb"):
                pass
            bpy = FakeBpy(
                [FakeAction("Idle", "wrong")],
                {staged_blend: [FakeAction("Idle", "one"), FakeAction("Idle", "two")]},
            )

            action, error = resolve_imported_action(bpy, {
                "action_name": "Idle", "source_file": "expected", "staged_blend": staged_blend,
            })

            self.assertIsNone(action)
            self.assertIn("不唯一", error)

    def test_staged_source_ref_mismatch_removes_loaded_action(self):
        with tempfile.TemporaryDirectory() as directory:
            staged_blend = os.path.join(directory, "idle.blend")
            with open(staged_blend, "wb"):
                pass
            staged = FakeAction("Idle", "expected")
            staged["Pipeline_SourceRef"] = "Hero/Other/Idle"
            bpy = FakeBpy([], {staged_blend: [staged]})

            action, error = resolve_imported_action(bpy, {
                "action_name": "Idle",
                "source_file": "expected",
                "source_manifest_ref": "Hero/Moves/Idle",
                "staged_blend": staged_blend,
            })

            self.assertIsNone(action)
            self.assertIn("任务不匹配", error)
            self.assertEqual(bpy.data.actions, [])

    def test_staged_blank_source_ref_removes_loaded_action(self):
        with tempfile.TemporaryDirectory() as directory:
            staged_blend = os.path.join(directory, "idle.blend")
            with open(staged_blend, "wb"):
                pass
            staged = FakeAction("Idle", "expected")
            bpy = FakeBpy([], {staged_blend: [staged]})

            action, error = resolve_imported_action(bpy, {
                "action_name": "Idle",
                "source_file": "expected",
                "source_manifest_ref": "Hero/Moves/Idle",
                "staged_blend": staged_blend,
            })

            self.assertIsNone(action)
            self.assertIn("任务不匹配", error)
            self.assertEqual(bpy.data.actions, [])
            self.assertNotIn("Pipeline_SourceRef", staged)

    def test_staged_exact_source_ref_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            staged_blend = os.path.join(directory, "idle.blend")
            with open(staged_blend, "wb"):
                pass
            staged = FakeAction("Idle", "expected")
            staged["Pipeline_SourceRef"] = "Hero/Moves/Idle"
            bpy = FakeBpy([], {staged_blend: [staged]})

            action, error = resolve_imported_action(bpy, {
                "action_name": "Idle",
                "source_file": "expected",
                "source_manifest_ref": "Hero/Moves/Idle",
                "staged_blend": staged_blend,
            })

            self.assertIs(action, staged)
            self.assertEqual(error, "")

    def test_staged_property_write_failure_removes_loaded_action(self):
        with tempfile.TemporaryDirectory() as directory:
            staged_blend = os.path.join(directory, "idle.blend")
            with open(staged_blend, "wb"):
                pass
            staged = RejectPropertyWriteAction("Idle", "expected")
            staged["Pipeline_SourceRef"] = "Hero/Moves/Idle"
            bpy = FakeBpy([], {staged_blend: [staged]})

            action, error = resolve_imported_action(bpy, {
                "action_name": "Idle",
                "source_file": "expected",
                "source_manifest_ref": "Hero/Moves/Idle",
                "staged_blend": staged_blend,
            })

            self.assertIsNone(action)
            self.assertIn("标记失败", error)
            self.assertEqual(bpy.data.actions, [])


if __name__ == "__main__":
    unittest.main()
