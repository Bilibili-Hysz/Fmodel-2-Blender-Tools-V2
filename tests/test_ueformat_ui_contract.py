"""Source-level contract for the V2 UEFormat toolbox entry points."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_PATH = ROOT / "ui.py"

def _operator_ids_from_draw(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "operator":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            ids.add(node.args[0].value)
    return ids


def test_ueformat_toolbox_exposes_model_animation_and_pose_only():
    operator_ids = _operator_ids_from_draw(UI_PATH)
    assert {
        "fmodel.import_model_uemodel",
        "fmodel.import_anim_ueanim",
        "fmodel.import_anim_uepose",
    }.issubset(operator_ids)
    assert "fmodel.import_uescene" not in operator_ids



def test_toolbox_surfaces_vendor_runtime_status():
    source = UI_PATH.read_text(encoding="utf-8")
    assert "_draw_ueformat_runtime_status" in source
    assert "UEFormat" in source and "运行时" in source


def test_toolbox_surfaces_unified_last_operation_actions():
    source = UI_PATH.read_text(encoding="utf-8")
    operator_ids = _operator_ids_from_draw(UI_PATH)
    assert "最近操作" in source
    assert "_draw_last_operation" in source
    assert {
        "fmodel.copy_last_operation_report",
        "fmodel.open_last_operation_source",
    }.issubset(operator_ids)


def test_vendor_runtime_status_explains_external_override():
    source = UI_PATH.read_text(encoding="utf-8")
    assert 'state == "external"' in source
    assert 'status.get("remediation"' in source
    assert "Vendor 树" in source
