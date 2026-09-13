from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "io_scene_ueformat"


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing assignment {name!r} in {path}")


def test_bundled_vendor_version_satisfies_bridge_minimum():
    """The bridge version gate must accept the bundled vendor.

    Regression guard: the vendor moved from the legacy FModel edition (1.x)
    to the canonical UEFormat versioning (0.10.0) while the gate still
    required 1.2.0, so every import failed at bridge resolution even though
    all capability signatures were present.
    """
    from core.ueformat_bridge import CURRENT_ADDON_MIN_VERSION

    info = _literal_assignment(VENDOR / "__init__.py", "bl_info")
    assert tuple(info["version"]) >= CURRENT_ADDON_MIN_VERSION


def test_bundled_ueformat_vendor_is_current_fmodel_snapshot():
    info = _literal_assignment(VENDOR / "__init__.py", "bl_info")
    assert tuple(info["version"]) == (0, 10, 0)
    assert tuple(info["blender"]) == (4, 2, 0)

    manifest = (VENDOR / "blender_manifest.toml").read_text(encoding="utf-8")
    assert 'id = "io_scene_ueformat"' in manifest
    assert 'version = "0.10.0"' in manifest
    assert 'blender_version_min = "4.2.0"' in manifest
    assert 'license = ["SPDX:GPL-3.0-or-later"]' in manifest


def test_bundled_ueformat_vendor_contains_fmodel_surfaces_and_headless_entrypoint():
    options = (VENDOR / "options.py").read_text(encoding="utf-8")
    for field in (
        "import_fmodel_materials",
        "build_fmodel_material_nodes",
        "load_fmodel_embedded_textures",
        "load_fmodel_external_textures",
        "fmodel_ue_export_root",
        "fmodel_bundle_root",
    ):
        assert field in options
    assert "strict_fmodel_skeleton" in options

    root = (VENDOR / "__init__.py").read_text(encoding="utf-8")
    op = (VENDOR / "op" / "__init__.py").read_text(encoding="utf-8")
    assert "def register_headless" in root
    assert "def register(*, headless: bool = False)" in op
    assert "UEFORMAT_PT_Scene" not in op
    assert "UFImportUEScene" not in op
    assert "if not headless or operator not in _PANEL_CLASSES" in op


def test_bundled_ueformat_vendor_carries_runtime_zstandard_wheels():
    wheels = {path.name for path in (VENDOR / "wheels").glob("zstandard-*.whl")}
    assert any("cp311-cp311-win_amd64" in name for name in wheels)
    assert any("cp312-cp312-win_amd64" in name for name in wheels)
    assert any("cp313-cp313-win_amd64" in name for name in wheels)


def test_legacy_material_resolver_and_custom_shaders_are_retired():
    for relative in (
        "Universal_tools/material",
        "core/material_ownership.py",
        "Shaders/shaders.json",
        "Shaders/shaders.blend",
        "Shaders/Customshaders.blend",
        "Shaders/Customshaders.json",
        "Shaders/GameProfiles",
        "Shaders/PRESET_SPEC.md",
    ):
        assert not (ROOT / relative).exists(), relative
    readme = (ROOT / "README_EN.md").read_text(encoding="utf-8")
    assert "retired together with" in readme
    assert "remains registered as a" not in readme
