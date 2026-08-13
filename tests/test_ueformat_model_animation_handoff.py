"""Fake-Blender boundary contract for UEFormat model import handoff."""

import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(
    ROOT, "Fmodel", "importers", "model", "operator_model_ueformat.py"
)


class FakeObject(dict):
    def __init__(self, name, object_type, parent=None, children=None):
        super().__init__()
        self.name = name
        self.type = object_type
        self.parent = parent
        self.children = children or []

    def __bool__(self):
        return True


class FakeWizard:
    def __init__(self):
        self.source_mode = "MANUAL"
        self.source_dir = "C:/ExistingSource"
        self.output_dir = "C:/ExistingOutput"
        self.library_name = "ExistingLibrary"
        self.skeleton_filter = ""
        self.import_target_skeleton = ""
        self.current_step = 1
        self.scan_completed = False


def _install_fake_modules():
    saved = {name: sys.modules.get(name) for name in (
        "bpy", "bpy_extras", "bpy_extras.io_utils",
        "addon", "addon.Fmodel", "addon.Fmodel.Pipeline",
        "addon.Fmodel.Pipeline.steps", "addon.Fmodel.Pipeline.steps.single_model_flow",
        "addon.Universal_tools", "addon.Universal_tools.material", "addon.core",
        "addon.core.utils", "addon.Assets", "addon.Assets.Utils",
        "addon.Assets.Utils.utils_asset",
    )}
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(Operator=type("Operator", (), {}))
    bpy.props = types.SimpleNamespace(
        StringProperty=lambda **kw: None, FloatProperty=lambda **kw: None,
        IntProperty=lambda **kw: None, BoolProperty=lambda **kw: None,
    )
    bpy.utils = types.SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    sys.modules["bpy"] = bpy
    bpy_extras = types.ModuleType("bpy_extras")
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})
    sys.modules["bpy_extras"] = bpy_extras
    sys.modules["bpy_extras.io_utils"] = io_utils
    for name in ("addon", "addon.Fmodel", "addon.Fmodel.Pipeline", "addon.Fmodel.Pipeline.steps",
                 "addon.Universal_tools", "addon.Assets", "addon.Assets.Utils"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package
    flow = types.ModuleType("addon.Fmodel.Pipeline.steps.single_model_flow")
    flow.prepare_imported_armature_for_animation = lambda context, armature: {"FINISHED"}
    sys.modules[flow.__name__] = flow
    material = types.ModuleType("addon.Universal_tools.material")
    material.auto_resolve_materials_for_import = lambda meshes, path: None
    sys.modules[material.__name__] = material
    core = types.ModuleType("addon.core")
    core.log_info = core.log_error = core.log_success = lambda *args, **kwargs: None
    core.backup_before = lambda *args, **kwargs: None
    core.OperationType = types.SimpleNamespace(IMPORT_MODEL="IMPORT_MODEL")
    sys.modules[core.__name__] = core
    core_utils = types.ModuleType("addon.core.utils")
    core_utils.get_ueformat_api = lambda: (None, None, None, None)
    sys.modules[core_utils.__name__] = core_utils
    utils_asset = types.ModuleType("addon.Assets.Utils.utils_asset")
    utils_asset.get_skeleton_name_from_json = lambda path: "Hero"
    sys.modules[utils_asset.__name__] = utils_asset
    return saved, flow, core_utils


def _restore_modules(saved):
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class UEFormatModelAnimationHandoffTests(unittest.TestCase):
    def setUp(self):
        self.saved, self.flow, self.core_utils = _install_fake_modules()
        spec = importlib.util.spec_from_file_location(
            "addon.Fmodel.importers.model.operator_model_ueformat", MODULE_PATH
        )
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)

    def tearDown(self):
        sys.modules.pop("addon.Fmodel.importers.model.operator_model_ueformat", None)
        _restore_modules(self.saved)

    def test_canonical_identity_supplies_skeleton_metadata_without_json(self):
        imported_armature = FakeObject("ImportedRig", "ARMATURE")
        context = types.SimpleNamespace(
            active_object=FakeObject("UnrelatedRig", "ARMATURE"),
            scene=types.SimpleNamespace(
                pipeline_wizard=FakeWizard(),
                fmodel_project=types.SimpleNamespace(root_path="C:/Game"),
            ),
        )
        importer = types.SimpleNamespace(import_file=lambda path: (imported_armature, object()))
        self.module.get_ueformat_api = lambda: (
            lambda options: importer, lambda **kwargs: kwargs, None, None
        )
        core_module = sys.modules["addon.core"]
        core_module.skeleton_provenance_from_owner = lambda _armature: types.SimpleNamespace(
            is_strict=True,
            identity={
                "skeleton_path": "/Game/Characters/Hero/SK_Hero_Skeleton",
            },
            fingerprint="f" * 64,
        )
        operator = self.module.MODEL_OT_ImportUEModel()
        operator.filepath = "C:/Game/Hero.uemodel"
        operator.auto_backup = False
        operator.auto_link_mat = False
        operator.scale_factor = 0.01
        operator.bone_length = 4.0
        operator.reorient_bones = False
        operator.target_lod = 0
        operator.import_collision = False
        operator.import_morph_targets = True
        operator.import_sockets = True
        operator.import_virtual_bones = False
        operator.report = lambda *args: None

        with patch.object(self.module, "prepare_imported_armature_for_animation"):
            self.assertEqual(operator.execute(context), {"FINISHED"})

        self.assertEqual(imported_armature["FModel_Skeleton"], "SK_Hero_Skeleton")
        self.assertEqual(imported_armature["FModel_Skeleton_Path"], "/Game/Characters/Hero/SK_Hero_Skeleton")
        self.assertEqual(imported_armature["FModel_Skeleton_Identity_Fingerprint"], "f" * 64)

    def test_model_capability_keeps_canonical_material_options_without_full_bridge(self):
        imported_armature = FakeObject("ImportedRig", "ARMATURE")
        context = types.SimpleNamespace(
            active_object=None,
            scene=types.SimpleNamespace(
                pipeline_wizard=FakeWizard(),
                fmodel_project=types.SimpleNamespace(root_path="C:/Game"),
            ),
        )
        importer = types.SimpleNamespace(
            import_file=lambda path: (imported_armature, object())
        )
        captured = {}

        class FakeModelOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        bridge = types.SimpleNamespace(
            is_current=False,
            capabilities=types.SimpleNamespace(
                supports_current_model=True,
                supports_fmodel_materials=True,
                supports_fmodel_textures=True,
            ),
            UEFormatImport=lambda options: importer,
            UEModelOptions=FakeModelOptions,
        )
        self.module.get_ueformat_bridge = lambda **kwargs: bridge

        def build_model_options(**kwargs):
            captured["builder_input"] = kwargs
            return {
                "scale_factor": kwargs["scale_factor"],
                "bone_length": kwargs["bone_length"],
                "reorient_bones": kwargs["reorient_bones"],
                "target_lod": kwargs["target_lod"],
                "import_collision": kwargs["import_collision"],
                "import_morph_targets": kwargs["import_morph_targets"],
                "import_sockets": kwargs["import_sockets"],
                "import_virtual_bones": kwargs["import_virtual_bones"],
                "import_fmodel_materials": True,
                "build_fmodel_material_nodes": True,
                "load_fmodel_embedded_textures": True,
                "load_fmodel_external_textures": True,
                "keep_fmodel_unmapped_parameters": True,
                "fmodel_ue_export_root": "C:/Game",
                "fmodel_bundle_root": "C:/Game",
            }

        self.module.build_model_option_kwargs = build_model_options
        operator = self.module.MODEL_OT_ImportUEModel()
        operator.filepath = "C:/Game/Content/Hero/Hero.uemodel"
        operator.auto_backup = False
        operator.auto_link_mat = False
        operator.scale_factor = 0.01
        operator.bone_length = 4.0
        operator.reorient_bones = False
        operator.target_lod = 0
        operator.import_collision = False
        operator.import_morph_targets = True
        operator.import_sockets = True
        operator.import_virtual_bones = False
        operator.report = lambda *args: None

        with patch.object(self.module, "prepare_imported_armature_for_animation"):
            self.assertEqual(operator.execute(context), {"FINISHED"})

        self.assertEqual(
            captured["builder_input"]["model_path"].replace("\\", "/"),
            "C:/Game/Content/Hero/Hero.uemodel",
        )
        self.assertTrue(captured["import_fmodel_materials"])
        self.assertTrue(captured["build_fmodel_material_nodes"])
        self.assertTrue(captured["load_fmodel_embedded_textures"])
        self.assertTrue(captured["load_fmodel_external_textures"])
        self.assertEqual(captured["fmodel_ue_export_root"], "C:/Game")
        self.assertEqual(captured["fmodel_bundle_root"], "C:/Game")

    def test_successful_import_handoffs_returned_armature_not_active_object(self):
        imported_armature = FakeObject("ImportedRig", "ARMATURE")
        unrelated_active = FakeObject("UnrelatedRig", "ARMATURE")
        wizard = FakeWizard()
        context = types.SimpleNamespace(
            active_object=unrelated_active,
            scene=types.SimpleNamespace(
                pipeline_wizard=wizard,
                fmodel_project=types.SimpleNamespace(root_path="C:/Game"),
            ),
        )
        importer = types.SimpleNamespace(import_file=lambda path: (imported_armature, object()))
        self.module.get_ueformat_api = lambda: (
            lambda options: importer, lambda **kwargs: kwargs, None, None
        )
        operator = self.module.MODEL_OT_ImportUEModel()
        operator.filepath = "C:/Game/Hero.uemodel"
        operator.auto_backup = False
        operator.auto_link_mat = False
        operator.scale_factor = 0.01
        operator.bone_length = 4.0
        operator.reorient_bones = False
        operator.target_lod = 0
        operator.import_collision = False
        operator.import_morph_targets = True
        operator.import_sockets = True
        operator.import_virtual_bones = False
        operator.report = lambda *args: None

        with patch.object(self.module, "prepare_imported_armature_for_animation") as handoff:
            self.assertEqual(operator.execute(context), {"FINISHED"})

        handoff.assert_called_once_with(context, imported_armature)
        self.assertEqual(imported_armature["FModel_Skeleton"], "Hero")
        self.assertEqual(wizard.current_step, 1)
        self.assertFalse(wizard.scan_completed)


if __name__ == "__main__":
    unittest.main()
