"""Host-Python release contracts for Task 5 project-first delivery."""

import ast
from pathlib import Path


ROOT = Path(__file__).parent


def _bl_info():
    module = ast.parse((ROOT / "__init__.py").read_text(encoding="utf-8"))
    assignment = next(
        node for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets)
    )
    return ast.literal_eval(assignment.value)


def test_addon_identity_is_fmodel_tools_next_8_0_0():
    info = _bl_info()

    assert info["name"] == "FModel Tools Next"
    assert info["version"] == (8, 0, 0)


def test_root_readme_documents_project_first_index_workflow():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "FModel Tools Next" in readme
    assert ".fmodel/project-assets.sqlite" in readme
    assert "Open, Scan, and Rebuild" in readme
    assert "legacy" in readme.lower()


def test_blender_task7_probe_reports_project_index_projection():
    probe = (ROOT / "investigation" / "task7_verification" / "blender_task7_integration.py").read_text(
        encoding="utf-8"
    )

    assert "ProjectIndex" in probe
    assert ".scan(" in probe
    assert ".dictionary_manifest(" in probe
    assert '"indexed_result"' in probe


def test_blender_task7_probe_drives_indexed_wizard_to_publication():
    probe_path = ROOT / "investigation" / "task7_verification" / "blender_task7_integration.py"
    probe = probe_path.read_text(encoding="utf-8")
    module = ast.parse(probe)
    indexed_fields = {
        "indexed_project_root",
        "indexed_skeleton",
        "indexed_selected_paths",
    }
    direct_wizard_assignments = [
        target.attr
        for node in ast.walk(module)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else (node.target,)
        )
        if isinstance(target, ast.Attribute)
        and target.attr in indexed_fields
        and isinstance(target.value, ast.Name)
        and target.value.id in {"state", "wizard"}
    ]

    assert "bpy.ops.pipeline.wizard_step3" in probe
    assert "bpy.ops.pipeline.wizard_step4" in probe
    assert "project_assets.register()" in probe
    assert "bpy.context.scene.fmodel_project" in probe
    assert "project_default_indexed_plan" in probe
    assert "directory_scan_calls" in probe
    assert "bpy.ops.fmodel.scan_project_index()" in probe
    assert not direct_wizard_assignments
    assert "indexed_wizard_publication" in probe
    assert "published indexed wizard bundle" in probe
    assert "mounted indexed wizard library" in probe
