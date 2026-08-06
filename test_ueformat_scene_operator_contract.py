"""Synthetic runtime contracts for the V2 canonical UEScene dispatch operator."""

import importlib.util
import sys
import types
import unittest
from unittest.mock import Mock


ROOT = __import__("pathlib").Path(__file__).parent
MODULE_PATH = ROOT / "Fmodel" / "importers" / "scene" / "operator_scene_ueformat.py"
MODULE_NAME = "addon.Fmodel.importers.scene.operator_scene_ueformat"


class FakeScene(dict):
    def __init__(self, **attributes):
        super().__init__()
        for key, value in attributes.items():
            setattr(self, key, value)


def _install_fake_modules():
    names = (
        "bpy",
        "bpy_extras",
        "bpy_extras.io_utils",
        "addon",
        "addon.Fmodel",
        "addon.Fmodel.importers",
        "addon.Fmodel.importers.scene",
        "addon.core",
        "addon.core.utils",
        MODULE_NAME,
    )
    saved = {name: sys.modules.get(name) for name in names}

    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(Operator=type("Operator", (), {}))
    bpy.props = types.SimpleNamespace(
        StringProperty=lambda **kwargs: None,
        BoolProperty=lambda **kwargs: None,
    )
    bpy.utils = types.SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy_extras = types.ModuleType("bpy_extras")
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})

    bridge = types.SimpleNamespace(
        capabilities=types.SimpleNamespace(supports_uescene=True),
    )
    core = types.ModuleType("addon.core")
    core.OperationType = types.SimpleNamespace(PIPELINE="PIPELINE")
    core.backup_before = Mock()
    core.log_info = Mock()
    core.log_error = Mock()
    core.log_success = Mock()

    core_utils = types.ModuleType("addon.core.utils")
    core_utils.get_ueformat_bridge = Mock(return_value=bridge)
    core_utils.build_uescene_settings_kwargs = Mock(
        return_value={
            "fmodel_ue_export_root": "D:/Output/Game",
            "fmodel_bundle_root": "D:/Output/Game",
            "strict_uescene_models": True,
            "flatten_uescene_hierarchy": True,
            "flatten_uescene_instances": False,
        }
    )
    core_utils.parse_uescene_import_status = lambda raw: raw

    for name, module in (
        ("bpy", bpy),
        ("bpy_extras", bpy_extras),
        ("bpy_extras.io_utils", io_utils),
        ("addon", types.ModuleType("addon")),
        ("addon.Fmodel", types.ModuleType("addon.Fmodel")),
        ("addon.Fmodel.importers", types.ModuleType("addon.Fmodel.importers")),
        ("addon.Fmodel.importers.scene", types.ModuleType("addon.Fmodel.importers.scene")),
        ("addon.core", core),
        ("addon.core.utils", core_utils),
    ):
        if name in {"addon", "addon.Fmodel", "addon.Fmodel.importers", "addon.Fmodel.importers.scene", "addon.core"}:
            module.__path__ = []
        sys.modules[name] = module

    return saved, bpy, core, core_utils


def _restore_modules(saved):
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class UESceneOperatorContractTests(unittest.TestCase):
    def setUp(self):
        self.saved, self.bpy, self.core, self.core_utils = _install_fake_modules()
        spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE_NAME] = self.module
        spec.loader.exec_module(self.module)

    def tearDown(self):
        _restore_modules(self.saved)

    def _operator(self, result, status):
        settings = types.SimpleNamespace(
            last_import_status=status,
            last_import_report="canonical report",
        )
        scene = FakeScene(uf_settings=settings)
        context = types.SimpleNamespace(scene=scene)

        def import_uescene(**kwargs):
            return result

        self.bpy.ops = types.SimpleNamespace(
            uf=types.SimpleNamespace(import_uescene=import_uescene)
        )
        operator = self.module.SCENE_OT_ImportUEScene()
        operator.filepath = "D:/Output/Game/Content/Maps/Login/Login.uescene"
        operator.bundle_root = ""
        operator.strict_required_models = True
        operator.flatten_hierarchy = True
        operator.flatten_instances = False
        operator.import_fmodel_materials = True
        operator.build_fmodel_material_nodes = True
        operator.load_fmodel_embedded_textures = True
        operator.load_fmodel_external_textures = True
        operator.keep_fmodel_unmapped_parameters = True
        operator.auto_backup = False
        operator.reports = []
        operator.report = lambda level, message: operator.reports.append((level, message))
        return operator, context, settings

    def test_success_dispatches_to_canonical_operator_and_captures_report(self):
        operator, context, settings = self._operator(
            {"FINISHED"},
            {"outcome": "success", "required_model_failures": []},
        )
        self.assertEqual(operator.execute(context), {"FINISHED"})
        self.assertEqual(context.scene["FModel_UEFormat_Scene_Report"], "canonical report")
        self.assertEqual(context.scene["FModel_UEFormat_Scene_Status"], '{"outcome": "success", "required_model_failures": []}')
        self.assertTrue(any("成功" in message for _level, message in operator.reports))
        self.core_utils.get_ueformat_bridge.assert_called_once_with(
            require_current=True,
            allow_legacy=False,
            capability="scene",
        )

    def test_partial_dispatch_finishes_with_warning_and_failure_context(self):
        operator, context, _settings = self._operator(
            {"FINISHED"},
            {
                "outcome": "partial",
                "required_model_failures": [{"detail": "Models/Login.uemodel"}],
            },
        )
        self.assertEqual(operator.execute(context), {"FINISHED"})
        self.assertTrue(any(level == {"WARNING"} for level, _message in operator.reports))
        self.assertTrue(any("Models/Login.uemodel" in message for _level, message in operator.reports))

    def test_failed_dispatch_cancels(self):
        operator, context, _settings = self._operator(
            {"CANCELLED"},
            {
                "outcome": "failed",
                "required_model_failures": [{"detail": "missing model"}],
            },
        )
        self.assertEqual(operator.execute(context), {"CANCELLED"})
        self.assertTrue(any(level == {"ERROR"} for level, _message in operator.reports))


if __name__ == "__main__":
    unittest.main()
