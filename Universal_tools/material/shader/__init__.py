# FModel_Tools/Universal_tools/material/shader/__init__.py
# 着色器管理模块 — 聚合 shader_manager 的子模块

import bpy

from ..shader_manager import (
    CustomInterfaceItem,
    ShaderManagerProperties,
)

from .operators import (
    MAT_OT_MapNodeGroupInterfaces,
    MAT_OT_CreateBlankShader,
    MAT_OT_ImportDefaultShader,
    MAT_OT_SaveCustomShader,
    MAT_OT_RemoveCustomShader,
    MAT_OT_AddCustomInterface,
    MAT_OT_RemoveCustomInterface,
    MAT_OT_ExportNodeGroupPreset,
    MAT_OT_LoadPresetShaderEditor,
    MAT_OT_QuickRegisterNodeGroup,
    MAT_OT_PreviewPreset,
    MAT_OT_SelectNodeGroup,
    MAT_OT_AddSocketInline,
    MAT_OT_RemoveSocketInline,
    MAT_OT_RenameSocketInline,
    MAT_OT_SetSocketMapping,
    MAT_OT_EditSocketDetail,
    MAT_OT_ExportPresetToShaders,
    MAT_OT_AddDefaultShader,
    MAT_OT_AutoMapSockets,
)

from .panel import (
    FMODEL_PT_ShaderManager,
    SHADER_MT_DefaultShaders,
    SHADER_MT_CustomShaders,
    SHADER_MT_CustomShadersRemove,
)

_classes = (
    FMODEL_PT_ShaderManager,
    SHADER_MT_DefaultShaders,
    SHADER_MT_CustomShaders,
    SHADER_MT_CustomShadersRemove,
    CustomInterfaceItem,
    ShaderManagerProperties,
    MAT_OT_MapNodeGroupInterfaces,
    MAT_OT_CreateBlankShader,
    MAT_OT_ImportDefaultShader,
    MAT_OT_SaveCustomShader,
    MAT_OT_RemoveCustomShader,
    MAT_OT_AddCustomInterface,
    MAT_OT_RemoveCustomInterface,
    MAT_OT_ExportNodeGroupPreset,
    MAT_OT_LoadPresetShaderEditor,
    MAT_OT_QuickRegisterNodeGroup,
    MAT_OT_PreviewPreset,
    MAT_OT_SelectNodeGroup,
    MAT_OT_AddSocketInline,
    MAT_OT_RemoveSocketInline,
    MAT_OT_RenameSocketInline,
    MAT_OT_SetSocketMapping,
    MAT_OT_EditSocketDetail,
    MAT_OT_ExportPresetToShaders,
    MAT_OT_AddDefaultShader,
    MAT_OT_AutoMapSockets,
)


def register():
    from ..shader_manager import _ensure_data_dirs
    _ensure_data_dirs()
    for cls in _classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            print(f"[FModel] register_class FAILED for {cls.__name__}: {e}")
    bpy.types.Scene.fmodel_shader_manager = bpy.props.PointerProperty(type=ShaderManagerProperties)


def unregister():
    del bpy.types.Scene.fmodel_shader_manager
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
