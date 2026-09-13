from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    assert "from . import export" not in root
    assert "from . import scene" not in root
    assert "export.register" not in root
    assert "operator_model_ueformat" in model
    assert "operator_model_gltf" not in model
    assert "operator_model_psk" not in model
    assert "operator_anim_ueformat" in animation
    assert "operator_anim_psa" not in animation
    assert not (ROOT / "Fmodel/importers/export").exists()


def test_phase1_auto_loader_and_root_scheme_registration_are_ueformat_only():
    auto_handlers = _read("auto_handlers.py")
    addon_root = _read("__init__.py")

    assert '"io_scene_ueformat"' in auto_handlers
    assert "io_scene_psk_psa" not in auto_handlers
    assert "scheme_psk_auto" not in addon_root
    assert "scheme_psk_mesh_fix" not in addon_root


def test_phase1_retired_operator_sources_are_removed():
    for relative in (
        "Fmodel/importers/model/operator_model_gltf.py",
        "Fmodel/importers/model/operator_model_psk.py",
        "Fmodel/importers/animation/operator_anim_psa.py",
        "Fmodel/importers/export/operator_export_gltf.py",
        "Fmodel/importers/export/operator_export_fbx.py",
        "Universal_tools/skeleton/scheme_auto.py",
        "Universal_tools/skeleton/scheme_copy.py",
        "Universal_tools/mesh/scheme_morph_merge.py",
        "Universal_tools/mesh/scheme_psk_auto.py",
        "Universal_tools/mesh/scheme_psk_mesh_fix.py",
        "core/project_index.py",
        "core/project_index_service.py",
        "core/project_index_worker.py",
        "Fmodel/project_assets.py",
        "Fmodel/Pipeline/project_index_controller.py",
        "Assets/Utils/incremental_preview.py",
        "Assets/Utils/link_existing_library.py",
    ):
        assert not (ROOT / relative).exists(), relative
    assert not (ROOT / "vendor/io_scene_psk_psa").exists()


def test_retired_v1_entry_points_are_absent_from_runtime_payload():
    payload_tokens = (
        "pipeline.run_full",
        "pipeline.run_stage",
        "pipeline.save_checkpoint",
        "pipeline.load_checkpoint",
        "fmodel.process_unity_anims",
        "fmodel_nla_studio",
        "fix_scheme",
        "fmodel.link_existing_library",
        "fmodel.preview_incremental",
        "fmodel.apply_incremental",
        "ImportMode.FBX_MATCH",
        "ImportMode.LINK_EXISTING",
        "UNITY_ANIM",
        "unity_skip_processed",
        "indexed_project_root",
        "project_index_controller",
        "resolver_v2_enabled",
        "auto_resolve",
        "shader_manager",
        "manifest_builder",
        "channel_resolver",
        "node_builder",
        "GameProfile",
        "fmodel_gp_editor",
        "material_ownership",
    )
    payload_paths = [
        ROOT / "__init__.py",
        ROOT / "auto_handlers.py",
        ROOT / "ui.py",
    ]
    for directory in ("Assets", "Fmodel", "Universal_tools", "core"):
        payload_paths.extend(sorted((ROOT / directory).rglob("*.py")))
    for path in payload_paths:
        text = path.read_text(encoding="utf-8")
        for token in payload_tokens:
            assert token not in text, f"{token} remains in {path}"
