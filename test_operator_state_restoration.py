"""Regression tests for batch result and Blender selection cleanup."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).parent
UEFORMAT = ROOT / "Fmodel" / "importers" / "animation" / "operator_anim_ueformat.py"
PSA = ROOT / "Fmodel" / "importers" / "animation" / "operator_anim_psa.py"
GLTF = ROOT / "Fmodel" / "importers" / "export" / "operator_export_gltf.py"
FBX = ROOT / "Fmodel" / "importers" / "export" / "operator_export_fbx.py"
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

    def test_psa_cancels_when_every_file_fails_and_finishes_when_partial(self):
        for failures, expected in (({"good", "bad"}, {"CANCELLED"}), ({"bad"}, {"FINISHED"})):
            bpy = SimpleNamespace(
                types=SimpleNamespace(Operator=FakeOperator),
                props=SimpleNamespace(StringProperty=lambda **_kwargs: None, CollectionProperty=lambda **_kwargs: None),
                data=SimpleNamespace(actions=[]),
            )
            bpy.ops = SimpleNamespace(psa=SimpleNamespace(import_file=lambda filepath: (
                (_ for _ in ()).throw(RuntimeError("bad file")) if filepath in failures else None
            )))
            cls = load_class(PSA, "ANIM_OT_ImportPSA", {
                "bpy": bpy, "os": __import__("os"), "tempfile": __import__("tempfile"), "ImportHelper": object,
            })
            operator = cls()
            operator.directory = ""
            operator.files = [SimpleNamespace(name="good"), SimpleNamespace(name="bad")]
            operator.create_safe_psa = lambda path: (path, [])
            context = SimpleNamespace(active_object=FakeObject("Rig", "ARMATURE"))
            with self.subTest(failures=failures):
                self.assertEqual(operator.execute(context), expected)


class IndividualExportSelectionTests(unittest.TestCase):
    def _run_export(self, path, class_name, export_name, failures):
        first = FakeObject("First", selected=True)
        second = FakeObject("Second", selected=True)
        ignored = FakeObject("Camera", "CAMERA", selected=True)
        objects = [first, second, ignored]
        active = SimpleNamespace(value=second)

        def select_all(action):
            self.assertEqual(action, "DESELECT")
            for obj in objects:
                obj.selected = False

        calls = []
        def export(**kwargs):
            calls.append(kwargs)
            if len(calls) in failures:
                raise RuntimeError("export failure")

        bpy = SimpleNamespace(
            types=SimpleNamespace(Operator=FakeOperator),
            props=SimpleNamespace(StringProperty=lambda **_kwargs: None, EnumProperty=lambda **_kwargs: None,
                                  BoolProperty=lambda **_kwargs: None, FloatProperty=lambda **_kwargs: None),
            ops=SimpleNamespace(object=SimpleNamespace(select_all=select_all), export_scene=SimpleNamespace(**{export_name: export})),
        )
        cls = load_class(path, class_name, {"bpy": bpy, "os": __import__("os"), "ExportHelper": object})
        operator = cls()
        operator.filepath = "C:/exports/all.glb"
        operator.export_individual = True
        operator.export_animations = True
        operator.export_apply = True
        if class_name.endswith("GLTF_Batch"):
            operator.export_format = "GLB"
            operator.export_skins = True
            operator.export_morph = True
            operator.export_yup = True
        else:
            operator.export_morph = True
            operator.export_scale = 1.0
            operator.export_axis_forward = "-Z"
            operator.export_axis_up = "Y"

        context = SimpleNamespace(
            selected_objects=objects,
            view_layer=SimpleNamespace(objects=SimpleNamespace(active=None)),
        )
        context.view_layer.objects.active = active.value
        result = operator.execute(context)
        expected = {"CANCELLED"} if len(failures) == 2 else {"FINISHED"}
        self.assertEqual(result, expected)
        if failures == {1, 2}:
            self.assertIn(({'ERROR'}, "所有对象导出失败"), operator.reports)
        self.assertEqual([obj.selected for obj in objects], [True, True, True])
        self.assertIs(context.view_layer.objects.active, active.value)

    def test_gltf_restores_selection_and_active_after_success_and_failure(self):
        self._run_export(GLTF, "EXPORT_OT_GLTF_Batch", "gltf", set())
        self._run_export(GLTF, "EXPORT_OT_GLTF_Batch", "gltf", {2})

    def test_gltf_cancels_when_every_individual_export_fails(self):
        self._run_export(GLTF, "EXPORT_OT_GLTF_Batch", "gltf", {1, 2})

    def test_fbx_restores_selection_and_active_after_success_and_failure(self):
        self._run_export(FBX, "EXPORT_OT_FBX_Batch", "fbx", set())
        self._run_export(FBX, "EXPORT_OT_FBX_Batch", "fbx", {2})

    def test_fbx_cancels_when_every_individual_export_fails(self):
        self._run_export(FBX, "EXPORT_OT_FBX_Batch", "fbx", {1, 2})


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
