"""Fake-Blender contracts for single-model animation handoff and source resolution.

Red → Green — tests drive implementation of:
  - resolve_animation_source(state, project_root) -> str
  - FMODEL_OT_UseActiveArmatureForAnimation (bl_idname fmodel.use_active_armature_for_animation)
  - source_mode / manual_source_dir WizardState fields
"""

import ast
import importlib.util
import os
import sys
import types
import unittest


# ---------------------------------------------------------------------------
# Fake bpy module — the installed bpy is an empty namespace package stub.
# ---------------------------------------------------------------------------

def _install_fake_bpy():
    """Replace the empty bpy namespace package with a fake module."""
    saved = sys.modules.get("bpy")
    fake = types.ModuleType("bpy")
    fake.types = types.SimpleNamespace(
        Operator=type("Operator", (), {}),
        PropertyGroup=type("PropertyGroup", (), {}),
    )
    fake.utils = types.SimpleNamespace(
        register_class=lambda cls: None,
        unregister_class=lambda cls: None,
    )
    fake.props = types.SimpleNamespace(
        StringProperty=lambda **kw: None,
        BoolProperty=lambda **kw: None,
        FloatProperty=lambda **kw: None,
        IntProperty=lambda **kw: None,
        EnumProperty=lambda **kw: None,
        PointerProperty=lambda **kw: None,
        CollectionProperty=lambda **kw: None,
    )
    sys.modules["bpy"] = fake
    return saved


def _restore_bpy(saved):
    if saved is None:
        sys.modules.pop("bpy", None)
    else:
        sys.modules["bpy"] = saved


# ---------------------------------------------------------------------------
# Fake helper types
# ---------------------------------------------------------------------------

class FakeArmature:
    """Minimal armature-like object for handoff tests."""
    def __init__(self, name, custom_props=None):
        self.name = name
        self.type = "ARMATURE"
        self._props = dict(custom_props or {})

    def get(self, key, default=None):
        return self._props.get(key, default)


class FakeNonArmature:
    """An object that is not an armature."""
    def __init__(self, name="Cube"):
        self.name = name
        self.type = "MESH"


class FakeWizardState:
    """Minimal fake for WizardState fields used by the handoff."""
    def __init__(self, source_mode="MANUAL", manual_source_dir="",
                 project_root="", source_dir="", skeleton_filter="",
                 import_target_skeleton="", current_step=1, output_dir="",
                 library_name=""):
        self.source_mode = source_mode
        self.manual_source_dir = manual_source_dir
        self.project_root = project_root
        self.source_dir = source_dir
        self.skeleton_filter = skeleton_filter
        self.import_target_skeleton = import_target_skeleton
        self.current_step = current_step
        self.output_dir = output_dir
        self.library_name = library_name


class FakeProjectSettings:
    """Minimal fake for scene.fmodel_project."""
    def __init__(self, root_path=""):
        self.root_path = root_path


class FakeContext:
    """Minimal fake for bpy.context-like object."""
    def __init__(self, active_object=None, project_root=""):
        self.active_object = active_object
        self.scene = types.SimpleNamespace(
            pipeline_wizard=FakeWizardState(),
            fmodel_project=FakeProjectSettings(root_path=project_root),
        )


# ---------------------------------------------------------------------------
# Module-level setup: import the target module once
# ---------------------------------------------------------------------------

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "Fmodel", "Pipeline", "steps", "single_model_flow.py"
)


def _import_flow_module():
    """Import single_model_flow, returning the module or None on failure."""
    if not os.path.isfile(_MODULE_PATH):
        return None
    spec = importlib.util.spec_from_file_location(
        "addon.Fmodel.Pipeline.steps.single_model_flow", _MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


_SAVED_BPY = None


def setUpModule():
    global _SAVED_BPY
    _SAVED_BPY = _install_fake_bpy()


def tearDownModule():
    _restore_bpy(_SAVED_BPY)


# ===================================================================
# resolve_animation_source tests
# ===================================================================

class ResolveAnimationSourceTests(unittest.TestCase):
    """Standalone resolve_animation_source(str, str) -> str tests."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_flow_module()
        if cls.mod is None:
            raise unittest.SkipTest("single_model_flow.py not yet implemented")

    def test_project_default_returns_project_root(self):
        wizard = FakeWizardState(source_mode="PROJECT")
        result = self.mod.resolve_animation_source(wizard, "C:/Game")
        self.assertEqual(result, "C:/Game")

    def test_project_default_empty_root_returns_empty(self):
        wizard = FakeWizardState(source_mode="PROJECT")
        result = self.mod.resolve_animation_source(wizard, "")
        self.assertEqual(result, "")

    def test_manual_source_overrides_project_default(self):
        wizard = FakeWizardState(source_mode="MANUAL", manual_source_dir="C:/Anims")
        result = self.mod.resolve_animation_source(wizard, "C:/Game")
        self.assertEqual(result, "C:/Anims")

    def test_manual_empty_dir_returns_empty(self):
        wizard = FakeWizardState(source_mode="MANUAL", manual_source_dir="")
        result = self.mod.resolve_animation_source(wizard, "C:/Game")
        self.assertEqual(result, "")

    def test_manual_source_takes_leading_or_trailing_whitespace(self):
        wizard = FakeWizardState(source_mode="MANUAL", manual_source_dir="  C:/Anims  ")
        result = self.mod.resolve_animation_source(wizard, "C:/Game")
        self.assertEqual(result, "C:/Anims")

    def test_project_default_strips_whitespace_from_root(self):
        wizard = FakeWizardState(source_mode="PROJECT")
        result = self.mod.resolve_animation_source(wizard, "  C:/Game  ")
        self.assertEqual(result, "C:/Game")


# ===================================================================
# Handoff operator contract tests (AST / string-level)
# ===================================================================

class HandoffOperatorContractTests(unittest.TestCase):
    """Contract tests for the operator that do not require Blender registration."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_flow_module()

    def test_operator_exists(self):
        if self.mod is None:
            self.fail("single_model_flow.py not found / failed to import")
        self.assertTrue(
            hasattr(self.mod, "FMODEL_OT_UseActiveArmatureForAnimation"),
            "Operator class not found in module",
        )

    def test_bl_idname_is_correct(self):
        if self.mod is None:
            self.fail("single_model_flow.py not found / failed to import")
        cls = self.mod.FMODEL_OT_UseActiveArmatureForAnimation
        self.assertEqual(cls.bl_idname, "fmodel.use_active_armature_for_animation")

    def test_operator_is_bpy_operator_subclass(self):
        if self.mod is None:
            self.fail("single_model_flow.py not found / failed to import")
        cls = self.mod.FMODEL_OT_UseActiveArmatureForAnimation
        mro_names = {c.__name__ for c in cls.__mro__}
        self.assertIn("Operator", mro_names)

    def test_operator_has_poll_classmethod(self):
        if self.mod is None:
            self.fail("single_model_flow.py not found / failed to import")
        cls = self.mod.FMODEL_OT_UseActiveArmatureForAnimation
        self.assertTrue(hasattr(cls, "poll"))

    def test_operator_has_execute_method(self):
        if self.mod is None:
            self.fail("single_model_flow.py not found / failed to import")
        cls = self.mod.FMODEL_OT_UseActiveArmatureForAnimation
        self.assertTrue(hasattr(cls, "execute"))

    def test_handoff_source_does_not_contain_scan_or_import_bpy_ops(self):
        """The handoff module must not call any scan/import operators."""
        if not os.path.isfile(_MODULE_PATH):
            self.fail("single_model_flow.py not found")
        with open(_MODULE_PATH, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
                    if getattr(func.value.value, "id", None) == "bpy" and func.value.attr == "ops":
                        op_name = func.attr
                        if "scan" in op_name.lower() or "import" in op_name.lower():
                            self.fail(
                                f"Handoff source calls scan/import operator: bpy.ops.{op_name}"
                            )


# ===================================================================
# Handoff logic tests (call _handoff_armature)
# ===================================================================

class HandoffExecutionTests(unittest.TestCase):
    """Test the actual handoff logic via the module's _handoff_armature function."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _import_flow_module()

    def setUp(self):
        if self.mod is None:
            self.skipTest("single_model_flow.py not yet implemented")
        self.wizard = FakeWizardState()

    # --- FModel_Skeleton wins over object name ---

    def test_handoff_prefills_metadata_skeleton_from_fmodel_skeleton(self):
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        result = self.mod._handoff_armature(self.wizard, armature, "C:/Game")
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.skeleton_filter, "Hero")

    def test_handoff_falls_back_to_object_name(self):
        armature = FakeArmature("DisplayRig", {})
        result = self.mod._handoff_armature(self.wizard, armature, "")
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.skeleton_filter, "DisplayRig")

    def test_handoff_falls_back_to_object_name_for_empty_metadata_skeleton(self):
        for skeleton_value in ("", None):
            with self.subTest(skeleton_value=skeleton_value):
                armature = FakeArmature(
                    "DisplayRig", {"FModel_Skeleton": skeleton_value}
                )
                result = self.mod._handoff_armature(self.wizard, armature, "")
                self.assertEqual(result, {"FINISHED"})
                self.assertEqual(self.wizard.skeleton_filter, armature.name)
                self.assertEqual(self.wizard.import_target_skeleton, armature.name)

    def test_handoff_sets_import_target_skeleton_to_armature_name(self):
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        result = self.mod._handoff_armature(self.wizard, armature, "C:/Game")
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.import_target_skeleton, "DisplayRig")

    # --- source_dir resolution ---

    def test_legacy_project_handoff_sets_source_dir(self):
        self.wizard.source_mode = "PROJECT"
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        result = self.mod._handoff_armature(self.wizard, armature, "C:/Game")
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.source_dir, "C:/Game")

    def test_handoff_manual_source_overrides_project_default(self):
        self.wizard.source_mode = "MANUAL"
        self.wizard.manual_source_dir = "C:/Anims"
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        result = self.mod._handoff_armature(self.wizard, armature, "C:/Game")
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.source_dir, "C:/Anims")

    def test_handoff_no_project_root_sets_empty_source_dir(self):
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        result = self.mod._handoff_armature(self.wizard, armature, "")
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.source_dir, "")

    def test_handoff_no_project_root_clears_prefilled_source_dir(self):
        self.wizard.source_dir = "C:/Stale"
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        result = self.mod._handoff_armature(self.wizard, armature, "")
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.source_dir, "")

    def test_handoff_empty_manual_source_clears_prefilled_source_dir(self):
        self.wizard.source_mode = "MANUAL"
        self.wizard.source_dir = "C:/Stale"
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        result = self.mod._handoff_armature(self.wizard, armature, "C:/Game")
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.source_dir, "")

    # --- step advancement ---

    def test_handoff_advances_to_step_2(self):
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        self.mod._handoff_armature(self.wizard, armature, "C:/Game")
        self.assertEqual(self.wizard.current_step, 2)

    def test_auto_handoff_sets_project_library_defaults_without_changing_step(self):
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        result = self.mod._handoff_armature(
            self.wizard, armature, "C:/Game", advance_to_step=False
        )
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.current_step, 1)
        self.assertEqual(self.wizard.source_mode, "MANUAL")
        self.assertEqual(self.wizard.manual_source_dir, "C:/Game")
        self.assertEqual(self.wizard.source_dir, "C:/Game")
        self.assertEqual(
            self.wizard.output_dir,
            os.path.join("C:/Game", ".fmodel", "animation-libraries", "Hero"),
        )
        self.assertEqual(self.wizard.library_name, "Hero")

    def test_auto_handoff_without_project_root_preserves_existing_paths(self):
        self.wizard = FakeWizardState(
            source_mode="MANUAL",
            source_dir="C:/ExistingSource",
            output_dir="C:/ExistingOutput",
            library_name="ExistingLibrary",
        )
        armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
        result = self.mod._handoff_armature(
            self.wizard, armature, "", advance_to_step=False
        )
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.wizard.source_mode, "MANUAL")
        self.assertEqual(self.wizard.source_dir, "C:/ExistingSource")
        self.assertEqual(self.wizard.output_dir, "C:/ExistingOutput")
        self.assertEqual(self.wizard.library_name, "ExistingLibrary")

    # --- non-armature does not mutate state ---

    def test_handoff_rejects_none_armature(self):
        pre_skeleton = self.wizard.skeleton_filter
        pre_source = self.wizard.source_dir
        pre_step = self.wizard.current_step
        result = self.mod._handoff_armature(self.wizard, None, "C:/Game")
        self.assertEqual(result, {"CANCELLED"})
        self.assertEqual(self.wizard.skeleton_filter, pre_skeleton)
        self.assertEqual(self.wizard.source_dir, pre_source)
        self.assertEqual(self.wizard.current_step, pre_step)

    def test_handoff_rejects_non_armature_object(self):
        mesh = FakeNonArmature("Cube")
        pre_skeleton = self.wizard.skeleton_filter
        pre_source = self.wizard.source_dir
        pre_step = self.wizard.current_step
        result = self.mod._handoff_armature(self.wizard, mesh, "C:/Game")
        self.assertEqual(result, {"CANCELLED"})
        self.assertEqual(self.wizard.skeleton_filter, pre_skeleton)
        self.assertEqual(self.wizard.source_dir, pre_source)
        self.assertEqual(self.wizard.current_step, pre_step)


# ===================================================================
# WizardState field contract tests (AST-level for state.py)
# ===================================================================

class WizardStateFieldContractTests(unittest.TestCase):
    """Verify source_mode and manual_source_dir exist in WizardState."""

    STATE_PATH = os.path.join(
        os.path.dirname(__file__), "Fmodel", "Pipeline", "steps", "state.py"
    )

    def test_source_mode_enum_property_exists(self):
        self.assertTrue(os.path.isfile(self.STATE_PATH))
        with open(self.STATE_PATH, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            # Class-level annotations are AnnAssign with Name target (not Attribute)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "source_mode":
                    found = True
                    break
        self.assertTrue(found, "source_mode EnumProperty not found in WizardState")

    def test_project_root_property_exists(self):
        self.assertTrue(os.path.isfile(self.STATE_PATH))
        with open(self.STATE_PATH, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        found = any(
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "project_root"
            for node in ast.walk(tree)
        )
        self.assertTrue(found, "project_root property not found in WizardState")

    def test_manual_source_dir_property_exists(self):
        self.assertTrue(os.path.isfile(self.STATE_PATH))
        with open(self.STATE_PATH, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "manual_source_dir":
                    found = True
                    break
        self.assertTrue(found, "manual_source_dir property not found in WizardState")

    def test_source_mode_has_only_manual_item(self):
        self.assertTrue(os.path.isfile(self.STATE_PATH))
        with open(self.STATE_PATH, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == "source_mode":
                    # The EnumProperty(...) call is in the annotation, not value
                    call_node = node.annotation if isinstance(node.annotation, ast.Call) else node.value
                    if isinstance(call_node, ast.Call):
                        for kw in call_node.keywords:
                            if kw.arg == "items":
                                items_literal = kw.value
                                if isinstance(items_literal, ast.List):
                                    item_ids = {
                                        elt.elts[0].value
                                        for elt in items_literal.elts
                                        if isinstance(elt, (ast.Tuple, ast.List))
                                           and len(elt.elts) >= 1
                                    }
                                    self.assertEqual(item_ids, {"MANUAL"})
                                return
        self.fail("source_mode EnumProperty with MANUAL item not found")


if __name__ == "__main__":
    unittest.main()
