from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parent


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_phase1_ui_exposes_only_ueformat_pipeline():
    source = _read("ui.py")
    toolbox = source[source.index("class FMODEL_PT_Toolbox"):source.index("class FMODEL_PT_Pipeline")]

    assert "('UEFORMAT'," in source
    assert "('GLTF'," not in source
    assert "('PSK'," not in source
    assert 'fmodel.import_model_uemodel' in toolbox
    assert 'fmodel.import_anim_ueanim' in toolbox
    assert 'fmodel.import_anim_uepose' in toolbox
    assert 'fmodel.import_uescene' not in toolbox
    for legacy_id in (
        "fmodel.import_model_gltf",
        "fmodel.export_gltf_batch",
        "fmodel.import_model_psk",
        "fmodel.import_anim_psa",
        "fmodel.export_fbx_batch",
    ):
        assert legacy_id not in toolbox


def test_phase1_import_registration_only_loads_ueformat_asset_operators():
    root = _read("Fmodel/importers/__init__.py")
    model = _read("Fmodel/importers/model/__init__.py")
    animation = _read("Fmodel/importers/animation/__init__.py")
    export = _read("Fmodel/importers/export/__init__.py")

    assert "from . import export" not in root
    assert "from . import scene" not in root
    assert "export.register" not in root
    assert "operator_model_ueformat" in model
    assert "operator_model_gltf" not in model
    assert "operator_model_psk" not in model
    assert "operator_anim_ueformat" in animation
    assert "operator_anim_psa" not in animation
    assert "operator_export_gltf" not in export
    assert "operator_export_fbx" not in export


def test_phase1_auto_loader_and_root_scheme_registration_are_ueformat_only():
    auto_handlers = _read("auto_handlers.py")
    addon_root = _read("__init__.py")

    assert '"io_scene_ueformat"' in auto_handlers
    assert "io_scene_psk_psa" not in auto_handlers
    assert "scheme_psk_auto" not in addon_root
    assert "scheme_psk_mesh_fix" not in addon_root


def test_phase1_keeps_legacy_operator_sources_but_not_unused_vendor():
    for relative in (
        "Fmodel/importers/model/operator_model_gltf.py",
        "Fmodel/importers/model/operator_model_psk.py",
        "Fmodel/importers/animation/operator_anim_psa.py",
        "Fmodel/importers/export/operator_export_gltf.py",
        "Fmodel/importers/export/operator_export_fbx.py",
    ):
        assert (ROOT / relative).is_file(), relative
    assert not (ROOT / "vendor/io_scene_psk_psa").exists()
