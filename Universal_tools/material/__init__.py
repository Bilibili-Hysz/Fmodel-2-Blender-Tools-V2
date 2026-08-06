# FModel_Tools/Universal_tools/material/__init__.py
# 材质工具 — Phase 1-3 自动材质解析管线

import bpy
import importlib

from .resolver import (
    resolve_objects_materials,
    resolve_objects_materials_with_options,
    format_summary,
    export_report_json,
)
from .auto_resolve import auto_resolve_materials_for_import

if "bpy" in locals():
    for mod in list(locals().values()):
        if hasattr(mod, 'reload'):
            importlib.reload(mod)


def _preset_items_callback(self, context):
    try:
        from .presets import get_preset_enum_items
        return get_preset_enum_items()
    except Exception:
        return [("pbr_standard", "PBR Standard", "")]

def _category_items_callback(self, context):
    return [
        ("ALL", "全部", ""),
        ("PBR", "PBR", ""),
        ("NPR", "NPR", ""),
        ("Hybrid", "混合", ""),
    ]


class MaterialProperties(bpy.types.PropertyGroup):
    """材质工具属性"""
    umodel_tex_dir: bpy.props.StringProperty(name="纹理目录", subtype='DIR_PATH')
    umodel_mat_path: bpy.props.StringProperty(name="材质目录", subtype='DIR_PATH')
    resolver_v2_enabled: bpy.props.BoolProperty(name="启用Resolver v2", default=True)
    resolver_preset: bpy.props.EnumProperty(
        name="材质预设", description="选择材质处理预设", items=_preset_items_callback,
    )
    preset_category_filter: bpy.props.EnumProperty(
        name="类别筛选", description="按类别筛选预设列表", items=_category_items_callback,
    )
    resolver_style: bpy.props.StringProperty(
        name="材质风格（旧）", description="已废弃", default="realistic",
    )
    auto_resolve_on_import: bpy.props.BoolProperty(
        name="导入时自动解析材质",
        description="导入模型后自动使用当前预设重建材质节点",
        default=True,
    )
    heuristic_fallback_enabled: bpy.props.BoolProperty(
        name="无JSON时猜测式匹配",
        description="当模型缺少JSON信息时，尝试通过材质名与贴图文件名的相似度自动匹配贴图。匹配基于文本近似，可能将错误的贴图链接到材质，建议仅在命名规范的游戏中使用",
        default=False,
    )
    search_depth: bpy.props.IntProperty(
        name="贴图搜索深度",
        description="从模型文件向上回溯的目录层数，在此范围内递归搜索所有图片文件作为贴图候选",
        default=2, min=1, max=10,
    )
    strict_apply_only: bpy.props.BoolProperty(name="严格模式仅应用", default=True)
    analyze_only: bpy.props.BoolProperty(name="仅分析不写入", default=False)
    strict_mode: bpy.props.BoolProperty(name="严格模式", default=False)
    report_out_path: bpy.props.StringProperty(name="报告输出", subtype='FILE_PATH', default="//MaterialResolveReport.json")
    last_summary: bpy.props.StringProperty(name="上次摘要", default="")
    last_error_summary: bpy.props.StringProperty(name="上次错误", default="")
    last_failed_count: bpy.props.IntProperty(name="失败材质", default=0)
    last_report_path: bpy.props.StringProperty(name="上次报告", default="")
    npr_shadow_color: bpy.props.FloatVectorProperty(
        name="阴影色", subtype='COLOR', size=4,
        default=(0.15, 0.15, 0.25, 1.0), min=0.0, max=1.0,
    )
    npr_highlight_color: bpy.props.FloatVectorProperty(
        name="高光色", subtype='COLOR', size=4,
        default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0,
    )
    npr_ramp_interpolation: bpy.props.EnumProperty(
        name="插值模式",
        items=[("CONSTANT", "恒定", ""), ("LINEAR", "线性", "")],
        default="CONSTANT",
    )
    npr_use_outline: bpy.props.BoolProperty(name="轮廓线", default=False)
    npr_ramp_stops: bpy.props.IntProperty(
        name="色阶数", description="ToonRamp色阶分段数",
        default=3, min=2, max=6,
    )


from .operators import (
    MAT_OT_ProcessMaterial,
    MAT_OT_AnalyzeMaterialResolution,
    MAT_OT_ApplyMaterialResolutionV2,
    MAT_OT_ExportMaterialReport,
    MAT_OT_OpenMaterialReport,
)


_classes = (
    MaterialProperties,
    MAT_OT_ProcessMaterial,
    MAT_OT_AnalyzeMaterialResolution,
    MAT_OT_ApplyMaterialResolutionV2,
    MAT_OT_ExportMaterialReport,
    MAT_OT_OpenMaterialReport,
)


from . import shader


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.fmodel_material = bpy.props.PointerProperty(type=MaterialProperties)
    shader.register()
    # Register built-in presets
    from .presets import discover_and_register
    discover_and_register(["pbr_standard", "pbr_orm", "npr_cel", "npr_toon_ramp"])


def unregister():
    shader.unregister()
    del bpy.types.Scene.fmodel_material
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    # Clean up preset registry
    from .presets import PRESET_REGISTRY
    PRESET_REGISTRY.clear()
