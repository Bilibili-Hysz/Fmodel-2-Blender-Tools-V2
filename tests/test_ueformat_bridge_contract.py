"""Pure-Python contracts for the canonical UEFormat bridge."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from core.ueformat_bridge import (
    UEFormatCapabilityError,
    build_model_option_kwargs,
    derive_fmodel_roots,
    inspect_ueformat_module,
    resolve_ueformat_bridge,
)


class _ModelOptions:
    def __init__(
        self,
        scale_factor=0.01,
        bone_length=4.0,
        reorient_bones=False,
        target_lod=0,
        import_collision=False,
        import_morph_targets=True,
        import_sockets=True,
        import_virtual_bones=False,
        import_fmodel_materials=True,
        build_fmodel_material_nodes=True,
        load_fmodel_embedded_textures=True,
        load_fmodel_external_textures=True,
        keep_fmodel_unmapped_parameters=True,
        fmodel_ue_export_root="",
        fmodel_bundle_root="",
    ):
        pass


class _AnimOptions:
    def __init__(self, strict_fmodel_skeleton=True):
        pass


class _PoseOptions:
    def __init__(self, strict_fmodel_skeleton=True):
        pass


def _fake_modules(version=(1, 2, 0)):
    name = "fake_io_scene_ueformat"
    root = types.ModuleType(name)
    root.__file__ = "/fixture/io_scene_ueformat/__init__.py"
    root.bl_info = {"name": "UE Format", "version": version}

    logic = types.ModuleType(f"{name}.importer.logic")
    logic.UEFormatImport = type("UEFormatImport", (), {})
    options = types.ModuleType(f"{name}.options")
    options.UEModelOptions = _ModelOptions
    options.UEAnimOptions = _AnimOptions
    options.UEPoseOptions = _PoseOptions
    modules = {
        "io_scene_ueformat": root,
        name: root,
        logic.__name__: logic,
        options.__name__: options,
    }
    return root, modules


def test_game_export_tree_roots_are_derived_from_content_ancestor():
    export_root, bundle_root = derive_fmodel_roots(
        r"D:\Output\Game\Content\Characters\Girl\girl.uemodel"
    )
    assert export_root == Path(r"D:\Output\Game")
    assert bundle_root == Path(r"D:\Output\Game")


def test_non_game_tree_path_fails_closed_instead_of_guessing_root():
    assert derive_fmodel_roots(r"D:\Temp\girl.uemodel") == (None, None)
    assert derive_fmodel_roots(r"D:\Output\Game\Content\Maps\Login\Login.uescene") == (None, None)


def test_current_addon_capabilities_include_native_asset_contracts():
    root, modules = _fake_modules()
    bridge = inspect_ueformat_module(root, importer=modules.__getitem__)
    assert bridge is not None
    assert bridge.is_current
    assert bridge.capabilities.supports_fmodel_materials
    assert bridge.capabilities.supports_fmodel_textures
    assert bridge.capabilities.supports_strict_skeleton



def test_animation_and_pose_strict_capabilities_are_checked_independently():
    root, modules = _fake_modules()
    partial_options = types.ModuleType(f"{root.__name__}.options")
    partial_options.UEModelOptions = _ModelOptions
    partial_options.UEAnimOptions = type("_AnimWithoutStrict", (), {"__init__": lambda self: None})
    partial_options.UEPoseOptions = _PoseOptions

    def partial_importer(name):
        if name == partial_options.__name__:
            return partial_options
        return modules[name]

    bridge = inspect_ueformat_module(root, importer=partial_importer)

    assert bridge is not None
    assert not bridge.capabilities.supports_strict_animation_skeleton
    assert bridge.capabilities.supports_strict_pose_skeleton
    assert not bridge.capabilities.supports_current_animation
    assert bridge.capabilities.supports_current_pose


def test_legacy_addon_is_rejected_when_current_contract_is_required():
    root, modules = _fake_modules(version=(1, 0, 0))
    with pytest.raises(UEFormatCapabilityError):
        resolve_ueformat_bridge(
            require_current=True,
            allow_legacy=False,
            importer=modules.__getitem__,
        )


def test_current_addon_is_selected_from_top_level_module():
    root, modules = _fake_modules()
    previous = sys.modules.get("io_scene_ueformat")
    sys.modules["io_scene_ueformat"] = root
    try:
        bridge = resolve_ueformat_bridge(importer=modules.__getitem__)
        assert bridge is not None
        assert bridge.module is root
    finally:
        if previous is None:
            sys.modules.pop("io_scene_ueformat", None)
        else:
            sys.modules["io_scene_ueformat"] = previous


def test_model_option_builder_passes_current_fmodel_roots_and_material_flags():
    options = build_model_option_kwargs(
        scale_factor=0.01,
        bone_length=4.0,
        reorient_bones=False,
        target_lod=0,
        import_collision=False,
        import_morph_targets=True,
        import_sockets=True,
        import_virtual_bones=False,
        model_path=r"D:\Output\Game\Content\Characters\Girl\girl.uemodel",
    )
    assert options["fmodel_ue_export_root"] == r"D:\Output\Game"
    assert options["fmodel_bundle_root"] == r"D:\Output\Game"
    assert options["import_fmodel_materials"] is True
    assert options["build_fmodel_material_nodes"] is True
    assert options["load_fmodel_external_textures"] is True

