"""Static compatibility metadata contracts."""

import ast
from pathlib import Path


def test_minimum_blender_version_is_5_0_without_importing_bpy():
    source = (Path(__file__).resolve().parents[1] / "__init__.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    assignment = next(
        node for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets)
    )
    assert ast.literal_eval(assignment.value)["blender"] == (5, 0, 0)

