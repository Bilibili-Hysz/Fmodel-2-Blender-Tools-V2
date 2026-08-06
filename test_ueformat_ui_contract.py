"""Source-level contract for the V2 UEFormat toolbox entry points."""

import ast
from pathlib import Path


ROOT = Path(__file__).parent
UI_PATH = ROOT / "ui.py"
SCENE_OPERATOR_PATH = ROOT / "Fmodel" / "importers" / "scene" / "operator_scene_ueformat.py"


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


def test_ueformat_toolbox_exposes_scene_import_alongside_model_animation_and_pose():
    operator_ids = _operator_ids_from_draw(UI_PATH)
    assert {
        "fmodel.import_model_uemodel",
        "fmodel.import_anim_ueanim",
        "fmodel.import_anim_uepose",
        "fmodel.import_uescene",
    }.issubset(operator_ids)


def test_scene_operator_id_matches_toolbox_dispatch():
    source = SCENE_OPERATOR_PATH.read_text(encoding="utf-8")
    assert 'bl_idname = "fmodel.import_uescene"' in source
