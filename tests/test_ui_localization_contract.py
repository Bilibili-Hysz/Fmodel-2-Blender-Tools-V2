"""Source-level contract for the Chinese-first FModel Tools UI."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


VISIBLE_MODULES = (
    ROOT / "ui.py",
    ROOT / "Fmodel" / "Pipeline" / "steps" / "single_model_flow.py",
    ROOT / "Fmodel" / "Pipeline" / "steps" / "step3_scan.py",
    ROOT / "Fmodel" / "Pipeline" / "steps" / "state.py",
    ROOT / "Fmodel" / "importers" / "animation" / "operator_anim_ueformat.py",
    ROOT / "Fmodel" / "importers" / "model" / "operator_model_ueformat.py",
    ROOT / "core" / "skeleton_compatibility.py",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_primary_ui_uses_chinese_first_labels():
    ui = _source(ROOT / "ui.py")
    assert 'bl_label = "批量建立动画库"' in ui
    assert 'text=t("UEFormat 原生管线")' in ui
    assert 'text=t("最近操作")' in ui


def test_active_ui_has_no_retired_english_captions():
    forbidden = (
        'bl_label = "Project Settings"',
        'bl_label = "Animation Pipeline"',
        'bl_label = "Copy Last Operation Report"',
        'text="Animation Source"',
        'text="Project Root"',
        'text="Index Maintenance"',
        'text="Prepare Import Plan"',
        'text="Scan Animation Directory"',
        'text="Name-based compatibility is explicitly enabled"',
        'text="Only matching bone names are bound; source-only bones are skipped."',
        'text="This is not full Unreal Retarget Manager/reference-pose retargeting."',
    )
    for path in VISIBLE_MODULES:
        text = _source(path)
        for caption in forbidden:
            assert caption not in text, f"retired English UI caption remains in {path}: {caption}"


def test_binding_label_is_localized():
    import importlib.util
    spec = importlib.util.spec_from_file_location("binding_labels", ROOT / "core" / "skeleton_compatibility.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.SKELETON_COMPATIBILITY_ITEMS == (("ueformat", "UEFormat 原版绑定", "绑定同名骨骼，跳过目标中不存在的轨道"),)


def test_i18n_contains_fallbacks_for_new_visible_terms():
    path = ROOT / "core" / "i18n.py"
    tree = ast.parse(_source(path))
    mapping_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_CN_TO_EN" for target in node.targets)
    )
    mapping = ast.literal_eval(mapping_node.value)
    keys = {key[1] for key in mapping if isinstance(key, tuple) and len(key) == 2}
    for term in (
        "动画管线",
        "UEFormat 原生管线",
        "最近操作",
        "严格身份",
        "按名称兼容",
        "单骨架快速打包",
    ):
        assert term in keys, f"missing en_US fallback for {term}"


def test_saved_compatibility_values_migrate_to_upstream():
    import importlib.util
    spec = importlib.util.spec_from_file_location("binding_migration", ROOT / "core" / "skeleton_compatibility.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for value in (None, "", "strict", "name_retarget", "ueformat"):
        assert module.normalize_skeleton_compatibility_mode(value) == "ueformat"


if __name__ == "__main__":
    raise SystemExit("run with pytest")
