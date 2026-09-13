"""Host-Python release contracts for Task 5 project-first delivery."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _bl_info():
    module = ast.parse((ROOT / "__init__.py").read_text(encoding="utf-8"))
    assignment = next(
        node for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets)
    )
    return ast.literal_eval(assignment.value)


def test_addon_identity_is_fmodel_tools_next_8_1_0():
    info = _bl_info()

    assert info["name"] == "FModel Tools Next"
    assert info["version"] == (8, 1, 0)


def test_root_readme_documents_folder_only_workflow():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "FModel Tools Next" in readme
    assert "Animation Pipeline" in readme
    assert "Folder" in readme
    assert "Indexed Project" in readme
    assert "retired" in readme.lower()
    assert ".fmodel/project-assets.sqlite" not in readme
    assert "Refresh Index" not in readme
    assert "Reset and Rescan" not in readme
    assert "Project Settings uses Open, Scan, and Rebuild" not in readme
    assert "legacy" in readme.lower()


