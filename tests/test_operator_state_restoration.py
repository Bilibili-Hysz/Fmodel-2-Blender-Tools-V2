"""Regression tests for batch result and Blender selection cleanup."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
UEFORMAT = ROOT / "Fmodel" / "importers" / "animation" / "operator_anim_ueformat.py"
MODEL_OPERATORS = ROOT / "Assets" / "Models" / "operators.py"


def load_class(path, name, globals_):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), globals_)
    return globals_[name]


def load_function(path, name, globals_):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), globals_)
    return globals_[name]


class FakeOperator:
    def __init__(self):
        self.reports = []

    def report(self, level, message):
        self.reports.append((level, message))


class FakeObject:
    def __init__(self, name, object_type="MESH", selected=False):
        self.name = name
        self.type = object_type
        self.selected = selected

    def select_set(self, value):
        self.selected = value


class BatchResultTests(unittest.TestCase):
    def _ueformat_operator(self, name, failures):
        class Importer:
            def __init__(self, _options):
                pass

            def import_file(self, path):
                if path in failures:
                    raise RuntimeError("bad file")

        bpy = SimpleNamespace(
            types=SimpleNamespace(Operator=FakeOperator),
            props=SimpleNamespace(StringProperty=lambda **_kwargs: None, CollectionProperty=lambda **_kwargs: None,
                                  FloatProperty=lambda **_kwargs: None, BoolProperty=lambda **_kwargs: None),
        )
        cls = load_class(UEFORMAT, name, {
            "bpy": bpy, "os": __import__("os"), "ImportHelper": object,
            "get_ueformat_api": lambda: (Importer, None, lambda **kwargs: kwargs, lambda **kwargs: kwargs),
        })
        operator = cls()
        operator.directory = ""
        operator.files = [SimpleNamespace(name="good"), SimpleNamespace(name="bad")]
        operator.scale_factor = 1.0
        operator.rotation_only = False
        operator.import_curves = False
        return operator

    def test_ueanim_and_uepose_cancel_when_every_file_fails(self):
        for name in ("ANIM_OT_ImportUEAnim", "ANIM_OT_ImportUEPose"):
            with self.subTest(operator=name):
                operator = self._ueformat_operator(name, {"bad", "good"})
                context = SimpleNamespace(active_object=FakeObject("Rig", "ARMATURE"))
                self.assertEqual(operator.execute(context), {"CANCELLED"})

    def test_ueanim_and_uepose_finish_with_partial_success(self):
        for name in ("ANIM_OT_ImportUEAnim", "ANIM_OT_ImportUEPose"):
            with self.subTest(operator=name):
                operator = self._ueformat_operator(name, {"bad"})
                context = SimpleNamespace(active_object=FakeObject("Rig", "ARMATURE"))
                self.assertEqual(operator.execute(context), {"FINISHED"})

class ModelSaveCleanupTests(unittest.TestCase):
    def _save_model(self, fail_write, fail_cleanup=False):
        class Data:
            def __init__(self, name, bones=None):
                self.name = name
                self.bones = [] if bones is None else bones

        class ObjectList(list):
            def link(self, obj):
                if obj not in self:
                    self.append(obj)
                if self.collection not in obj.users_collection:
                    obj.users_collection.append(self.collection)

            def unlink(self, obj):
                self.remove(obj)
                obj.users_collection.remove(self.collection)

        class Collection:
            def __init__(self, name):
                self.name = name
                self.objects = ObjectList()
                self.objects.collection = self

        class Collections(list):
            def new(self, name):
                collection = Collection(name)
                self.append(collection)
                return collection

            def remove(self, collection):
                if fail_cleanup:
                    raise RuntimeError("cleanup failed")
                super().remove(collection)

        armature = FakeObject("Rig", "ARMATURE", selected=True)
        armature.data = Data("RigData")
        armature.children = []
        armature.users_collection = []
        other = FakeObject("Other", selected=True)
        other.users_collection = []
        collections = Collections()
        context = SimpleNamespace(
            selected_objects=[armature, other],
            active_object=other,
            scene=SimpleNamespace(collection=SimpleNamespace(children=SimpleNamespace(link=lambda _collection: None))),
            view_layer=SimpleNamespace(objects=SimpleNamespace(active=other)),
        )

        def deselect_all(action):
            self.assertEqual(action, "DESELECT")
            armature.selected = False
            other.selected = False
            if hasattr(context, "active_object"):
                context.active_object.selected = False

        def duplicate():
            duplicate_armature = FakeObject("Rig", "ARMATURE")
            duplicate_armature.data = Data("RigData")
            duplicate_armature.children = []
            duplicate_armature.users_collection = []
            context.active_object = duplicate_armature

        def delete():
            for collection in list(collections):
                for obj in list(collection.objects):
                    if obj.selected:
                        collection.objects.unlink(obj)

        def write(**_kwargs):
            if fail_write:
                raise OSError("write failed")

        bpy = SimpleNamespace(
            context=context,
            data=SimpleNamespace(collections=collections, libraries=SimpleNamespace(write=write)),
            ops=SimpleNamespace(object=SimpleNamespace(select_all=deselect_all, duplicate=duplicate, delete=delete)),
        )
        save_model_logic = load_function(MODEL_OPERATORS, "save_model_logic", {
            "bpy": bpy, "os": __import__("os"), "json": __import__("json"),
            "export_bone_mapping": lambda _armature: {},
        })
        return save_model_logic, armature, other, context, collections

    def test_model_save_cleans_temporary_collection_and_restores_state_on_success(self):
        save, armature, other, context, collections = self._save_model(False)
        with self.subTest(result="success"):
            self.assertTrue(save(armature, self._temp_dir(), "Hero", False))
        self.assertEqual(collections, [])
        self.assertTrue(armature.selected)
        self.assertTrue(other.selected)
        self.assertIs(context.view_layer.objects.active, other)

    def test_model_save_cleans_temporary_collection_and_restores_state_on_failure(self):
        save, armature, other, context, collections = self._save_model(True)
        with self.assertRaises(OSError):
            save(armature, self._temp_dir(), "Hero", False)
        self.assertEqual(collections, [])
        self.assertTrue(armature.selected)
        self.assertTrue(other.selected)
        self.assertIs(context.view_layer.objects.active, other)

    def test_model_save_preserves_write_failure_when_cleanup_also_fails(self):
        save, _armature, _other, _context, _collections = self._save_model(True, fail_cleanup=True)
        with self.assertRaisesRegex(OSError, "write failed"):
            save(_armature, self._temp_dir(), "Hero", False)

    def _temp_dir(self):
        import tempfile
        return tempfile.mkdtemp()


if __name__ == "__main__":
    unittest.main()
