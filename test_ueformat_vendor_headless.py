"""Contract tests for bundled UEFormat headless registration."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
MODULE_PATH = ROOT / "vendor" / "io_scene_ueformat" / "op" / "__init__.py"
MODULE_NAME = "vendor_ueformat.op"


def _fake_class(name):
    return type(name, (), {})


def _install_modules():
    names = (
        "bpy", "bpy.props", "bpy.types", "vendor_ueformat", MODULE_NAME,
        f"{MODULE_NAME}.import_helpers", f"{MODULE_NAME}.panels",
        f"{MODULE_NAME}.settings",
    )
    saved = {name: sys.modules.get(name) for name in names}

    registered = []
    menu_callbacks = []

    class Scene:
        pass

    class Menu:
        pass

    class Context:
        pass

    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(
        Context=Context,
        Menu=Menu,
        Scene=Scene,
        TOPBAR_MT_file_import=types.SimpleNamespace(
            append=menu_callbacks.append,
            remove=menu_callbacks.remove,
        ),
    )
    bpy_props = types.ModuleType("bpy.props")
    bpy_props.PointerProperty = lambda **kwargs: ("pointer", kwargs.get("type"))
    bpy_types = types.ModuleType("bpy.types")
    bpy_types.Context = Context
    bpy_types.Menu = Menu
    bpy_types.Scene = Scene
    bpy_types.TOPBAR_MT_file_import = bpy.types.TOPBAR_MT_file_import
    bpy.props = bpy_props
    bpy.types = bpy_types
    bpy.utils = types.SimpleNamespace(
        register_class=registered.append,
        unregister_class=lambda cls: registered.remove(cls),
    )

    package = types.ModuleType("vendor_ueformat")
    package.__path__ = []
    op_package = types.ModuleType(MODULE_NAME)
    op_package.__path__ = []

    import_helpers = types.ModuleType(f"{MODULE_NAME}.import_helpers")
    for name in (
        "IO_FH_ueanim", "IO_FH_uemodel", "IO_FH_uepose",
        "UFImportUEAnim", "UFCopyLastImportReport",
        "UFImportUEModel", "UFImportUEPose",
    ):
        setattr(import_helpers, name, _fake_class(name))

    panels = types.ModuleType(f"{MODULE_NAME}.panels")
    for name in ("UEFORMAT_PT_Animation", "UEFORMAT_PT_Model"):
        setattr(panels, name, _fake_class(name))

    settings = types.ModuleType(f"{MODULE_NAME}.settings")
    settings.UFSettings = _fake_class("UFSettings")

    for name, module in (
        ("bpy", bpy),
        ("bpy.props", bpy_props),
        ("bpy.types", bpy_types),
        ("vendor_ueformat", package),
        (MODULE_NAME, op_package),
        (f"{MODULE_NAME}.import_helpers", import_helpers),
        (f"{MODULE_NAME}.panels", panels),
        (f"{MODULE_NAME}.settings", settings),
    ):
        sys.modules[name] = module

    return saved, bpy, registered, menu_callbacks


def _restore_modules(saved):
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class UEFormatVendorHeadlessTests(unittest.TestCase):
    def setUp(self):
        self.saved, self.bpy, self.registered, self.menu_callbacks = _install_modules()
        spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE_NAME] = self.module
        spec.loader.exec_module(self.module)

    def tearDown(self):
        _restore_modules(self.saved)

    def test_headless_registration_skips_panels_and_import_menu(self):
        self.module.register(headless=True)

        registered_names = {cls.__name__ for cls in self.registered}
        self.assertNotIn("UEFORMAT_PT_Animation", registered_names)
        self.assertNotIn("UEFORMAT_PT_Model", registered_names)
        self.assertIn("UFImportUEModel", registered_names)
        self.assertIn("UFImportUEPose", registered_names)
        self.assertEqual(self.menu_callbacks, [])
        self.assertTrue(hasattr(self.bpy.types.Scene, "uf_settings"))

        self.module.unregister()
        self.assertFalse(hasattr(self.bpy.types.Scene, "uf_settings"))
        self.assertEqual(self.registered, [])

    def test_normal_registration_keeps_panels_and_import_menu(self):
        self.module.register()

        registered_names = {cls.__name__ for cls in self.registered}
        self.assertIn("UEFORMAT_PT_Animation", registered_names)
        self.assertIn("UEFORMAT_PT_Model", registered_names)
        self.assertEqual(len(self.menu_callbacks), 1)

        self.module.unregister()
        self.assertEqual(self.menu_callbacks, [])
        self.assertEqual(self.registered, [])


if __name__ == "__main__":
    unittest.main()
