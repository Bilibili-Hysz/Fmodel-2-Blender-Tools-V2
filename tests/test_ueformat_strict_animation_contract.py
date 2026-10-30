"""Manual animation/pose import accepts standard UEFormat options."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "Fmodel" / "importers" / "animation" / "operator_anim_ueformat.py"
MODULE_NAME = "addon.Fmodel.importers.animation.operator_anim_ueformat"


class FakeArmature(dict):
    type = "ARMATURE"

    def __bool__(self):
        return True


class StrictOptionFailure:
    def __init__(self, **_kwargs):
        raise TypeError("strict_fmodel_skeleton is unsupported")


def _install_modules():
    names = (
        "bpy", "bpy_extras", "bpy_extras.io_utils",
        "addon", "addon.Fmodel", "addon.Fmodel.importers",
        "addon.Fmodel.importers.animation", "addon.core", "addon.core.utils",
        "addon.core.workflow", MODULE_NAME,
    )
    saved = {name: sys.modules.get(name) for name in names}

    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(
        Operator=type("Operator", (), {}),
        OperatorFileListElement=type("OperatorFileListElement", (), {}),
    )
    bpy.props = types.SimpleNamespace(
        StringProperty=lambda **_kwargs: None,
        FloatProperty=lambda **_kwargs: None,
        BoolProperty=lambda **_kwargs: None,
        CollectionProperty=lambda **_kwargs: None,
    )
    bpy.utils = types.SimpleNamespace(
        register_class=lambda _cls: None,
        unregister_class=lambda _cls: None,
    )
    bpy_extras = types.ModuleType("bpy_extras")
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})

    for name, module in (("bpy", bpy), ("bpy_extras", bpy_extras),
                         ("bpy_extras.io_utils", io_utils)):
        sys.modules[name] = module

    for name in (
        "addon", "addon.Fmodel", "addon.Fmodel.importers",
        "addon.Fmodel.importers.animation",
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    core = types.ModuleType("addon.core")
    core_utils = types.ModuleType("addon.core.utils")
    core_utils.get_ueformat_bridge = None
    core_utils.get_ueformat_api = lambda: (None, None, None, None)
    workflow = types.ModuleType("addon.core.workflow")
    workflow.resolve_target = lambda context: context.active_object
    sys.modules[workflow.__name__] = workflow
    sys.modules["addon.core"] = core
    sys.modules["addon.core.utils"] = core_utils
    return saved


def _restore_modules(saved):
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class StrictAnimationContractTests(unittest.TestCase):
    def setUp(self):
        self.saved = _install_modules()
        spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE_NAME] = self.module
        spec.loader.exec_module(self.module)

    def tearDown(self):
        _restore_modules(self.saved)

    def _operator(self, operator_class):
        operator = operator_class()
        operator.filepath = "C:/Game/Content/Hero/Run.ueanim"
        operator.directory = "C:/Game/Content/Hero"
        operator.files = None
        operator.scale_factor = 0.01
        operator.rotation_only = False
        operator.import_curves = False
        operator.reported = []
        operator.report = lambda level, message: operator.reported.append((level, message))
        return operator

    def test_animation_accepts_options_without_strict_parameter(self):
        calls = []
        class Options:
            def __init__(self, scale_factor, rotation_only, import_curves, override_skeleton):
                calls.append(override_skeleton)
        class Importer:
            def __init__(self, options):
                pass
            def import_file(self, path):
                calls.append(path)
        self.module.get_ueformat_api = lambda: (Importer, object(), Options, object())
        operator = self._operator(self.module.ANIM_OT_ImportUEAnim)
        armature = FakeArmature()
        self.assertEqual(operator.execute(types.SimpleNamespace(active_object=armature)), {"FINISHED"})
        self.assertEqual(calls, [armature, __import__("os").path.normpath(operator.filepath)])

    def test_pose_storage_accepts_options_without_strict_parameter(self):
        calls = []
        class Options:
            def __init__(self, scale_factor, override_skeleton):
                calls.append(override_skeleton)
        self.module.get_ueformat_api = lambda: (object(), object(), object(), Options)
        operator = self._operator(self.module.ANIM_OT_ImportUEPose)
        operator.pose_convention = "CUE4PARSE_ADDITIVE"
        operator.filepath = "C:/Game/Content/Hero/Rest.uepose"
        operator._import_source = lambda path, scale, convention: (calls.append(path), 1, 0)
        operator._refresh_library = lambda scene: None
        armature = FakeArmature()
        self.assertEqual(operator.execute(types.SimpleNamespace(active_object=armature, scene=object())), {"FINISHED"})
        self.assertEqual(calls, [armature, __import__("os").path.normpath(operator.filepath)])


if __name__ == "__main__":
    unittest.main()
