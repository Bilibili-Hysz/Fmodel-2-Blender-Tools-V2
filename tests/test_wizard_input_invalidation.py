"""Regression contracts for wizard source and target invalidation."""

import importlib.util
import os
import sys
import types


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "Fmodel", "Pipeline", "steps", "state.py")


class Mappings(list):
    def clear(self):
        del self[:]


def load_state():
    previous = sys.modules.get("bpy")
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(PropertyGroup=object, Object=object)
    bpy.props = types.SimpleNamespace(**{name: lambda **kwargs: kwargs for name in (
        "StringProperty", "BoolProperty", "FloatProperty", "IntProperty", "EnumProperty", "PointerProperty", "CollectionProperty"
    )})
    bpy.utils = types.SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    sys.modules["bpy"] = bpy
    try:
        spec = importlib.util.spec_from_file_location("test_wizard_state", STATE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = previous


def completed_state(**changes):
    values = dict(flow_type="UNITY", source_mode="MANUAL", source_dir="C:/Old", output_dir="C:/Out", library_name="Old", skeleton_filter="Old", import_target_skeleton="OldRig", scan_completed=True, scan_skeleton_count=2, scan_anim_count=5, scan_new_count=3, scan_existing_count=2, skeleton_mappings=Mappings([object()]), unmapped_skeletons="Old", import_completed=True, lib_completed=True)
    values.update(changes)
    return types.SimpleNamespace(**values)


def assert_cleared(state):
    assert not state.scan_completed
    assert (state.scan_skeleton_count, state.scan_anim_count, state.scan_new_count, state.scan_existing_count) == (0, 0, 0, 0)
    assert state.skeleton_mappings == []
    assert state.unmapped_skeletons == ""
    assert not state.import_completed and not state.lib_completed


def test_automatic_handoff_forces_fmodel_defaults_and_clears_once():
    state_module = load_state()
    state = completed_state()
    armature = types.SimpleNamespace(name="HeroRig", type="ARMATURE", get=lambda key, default="": "Hero" if key == "FModel_Skeleton" else default)
    assert state_module.handoff_armature(state, armature, "C:/Project", automatic=True)
    assert (state.flow_type, state.source_mode, state.source_dir, state.skeleton_filter, state.import_target_skeleton) == ("FMODEL", "MANUAL", "C:/Project", "Hero", "HeroRig")
    assert_cleared(state)


def test_canonical_skeleton_path_overrides_stale_display_alias():
    state_module = load_state()
    state_module.skeleton_provenance_from_owner = lambda armature: types.SimpleNamespace(
        is_strict=True, is_legacy=False,
        identity={"skeleton_path": "/Game/Characters/Andariel_Skeleton.Andariel_Skeleton"},
    )
    state = completed_state()
    armature = types.SimpleNamespace(
        name="Andariel_LOD0_Skeleton", type="ARMATURE",
        get=lambda key, default="": "Andariel_LOD0_Skeleton" if key == "FModel_Skeleton" else default,
    )

    assert state_module.handoff_armature(state, armature, "C:/Project", automatic=True)
    assert state.skeleton_filter == "Andariel_Skeleton.Andariel_Skeleton"
    assert state.import_target_skeleton == "Andariel_LOD0_Skeleton"
    assert state.library_name == "Andariel_Skeleton.Andariel_Skeleton"


def test_unknown_or_invalid_identity_never_uses_object_name_as_source_filter():
    state_module = load_state()
    state = completed_state()
    armature = types.SimpleNamespace(
        name="DisplayRig", type="ARMATURE", get=lambda key, default="": default,
    )
    assert state_module.handoff_armature(state, armature, "C:/Project", automatic=True)
    assert state.skeleton_filter == ""
    assert state.library_name == "DisplayRig"
    assert state.output_dir == os.path.join("C:/Project", ".fmodel", "animation-libraries", "DisplayRig")

    state_module.skeleton_provenance_from_owner = lambda selected: types.SimpleNamespace(
        is_strict=False, is_legacy=False, identity=None,
    )
    armature.get = lambda key, default="": "StaleAlias" if key == "FModel_Skeleton" else default
    assert state_module.handoff_armature(state, armature, "C:/Project", automatic=True) is False
    assert state.skeleton_filter == ""


def test_source_or_skeleton_input_reset_clears_all_derived_results():
    state_module = load_state()
    state = completed_state()
    state_module.clear_workflow_results(state)
    assert_cleared(state)
