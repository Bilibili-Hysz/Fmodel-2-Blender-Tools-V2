
# FModel_Tools/Fmodel/Pipeline/steps/state.py
# 向导状态

import bpy

try:
    from ....core import (
        SKELETON_COMPATIBILITY_ITEMS,
        SKELETON_COMPATIBILITY_STRICT,
    )
except (ImportError, AttributeError):
    # Keep isolated unit-test imports and older checkpoints loadable.
    SKELETON_COMPATIBILITY_STRICT = "strict"
    SKELETON_COMPATIBILITY_ITEMS = (
        ("strict", "严格身份", "要求 UEFormat 骨架身份完全一致"),
        ("name_retarget", "按名称兼容", "显式绑定同名骨骼"),
    )


_handoff_in_progress = set()


def _armature_poll(self, obj):
    return obj.type == 'ARMATURE'


def _sync_animation_source(self, context):
    """Keep the active animation source synchronized with the Folder field.

    Indexed Project is retired from the active UI/pipeline.  The old project
    fields remain only so older .blend files can still be opened safely.
    """
    from .single_model_flow import resolve_animation_source

    if id(self) in _handoff_in_progress:
        return
    source_dir = resolve_animation_source(self, "")
    if source_dir != getattr(self, "source_dir", ""):
        self.source_dir = source_dir
    clear_workflow_results(self)




def clear_workflow_results(state):
    """Discard results derived from the wizard's source and target inputs."""
    state.scan_completed = False
    state.scan_skeleton_count = 0
    state.scan_anim_count = 0
    state.scan_new_count = 0
    state.scan_existing_count = 0
    mappings = getattr(state, "skeleton_mappings", None)
    if mappings is not None:
        mappings.clear()
    state.unmapped_skeletons = ""
    state.import_completed = False
    state.lib_completed = False


def handoff_armature(state, armature, project_root="", automatic=False):
    """Apply one armature selection atomically and reset stale derived results."""
    if armature is None or getattr(armature, "type", None) != "ARMATURE":
        return False
    skeleton_name = str(armature.get("FModel_Skeleton", armature.name) or armature.name)
    root = project_root.strip() if project_root else ""
    desired = {
        "flow_type": "FMODEL" if automatic else getattr(state, "flow_type", "FMODEL"),
        "skeleton_filter": skeleton_name,
        "import_target_skeleton": armature.name,
    }
    if automatic and root:
        desired.update({
            "source_mode": "MANUAL",
            "manual_source_dir": root,
            "source_dir": root,
            "output_dir": __import__("os").path.join(
                root, ".fmodel", "animation-libraries", skeleton_name
            ),
            "library_name": skeleton_name,
        })
    changed = any(getattr(state, name, None) != value for name, value in desired.items())
    if not changed:
        return False
    _handoff_in_progress.add(id(state))
    try:
        for name, value in desired.items():
            setattr(state, name, value)
    finally:
        _handoff_in_progress.discard(id(state))
    clear_workflow_results(state)
    return True


# ============================================================
# 步骤状态管理
# ============================================================

class WizardSkeletonMapping(bpy.types.PropertyGroup):
    source_skeleton: bpy.props.StringProperty(name="源骨架")
    target_armature: bpy.props.PointerProperty(
        name="Target armature", type=bpy.types.Object, poll=_armature_poll
    )
    compatibility_mode: bpy.props.EnumProperty(
        name="兼容策略", items=SKELETON_COMPATIBILITY_ITEMS,
        default=SKELETON_COMPATIBILITY_STRICT,
    )
    source_count: bpy.props.IntProperty(name="动画", default=0)
    status: bpy.props.StringProperty(name="状态", default="pending")


class WizardState(bpy.types.PropertyGroup):
    """向导状态"""
    current_step: bpy.props.IntProperty(name="当前步骤", default=1, min=1, max=4)

    # Step 1: UEFormat-only workflow. The property is retained so older .blend
    # files can be normalized without changing the surrounding wizard schema.
    flow_type: bpy.props.EnumProperty(
        name="流程类型",
        items=[
            ('FMODEL', "FModel / UEFormat", "处理 UEFormat 导出的 .ueanim / .uepose 文件"),
        ],
        default='FMODEL',
    )

    # Step 2: 动画文件夹
    source_mode: bpy.props.EnumProperty(
        name="源目录模式",
        items=[
            ('MANUAL', "Folder", "直接扫描一个动画文件夹"),
        ],
        default='MANUAL',
        update=_sync_animation_source,
    )
    # Legacy Indexed Project fields are retained only so old .blend files
    # can be opened and normalized; they are not read by the active pipeline.
    project_root: bpy.props.StringProperty(
        name="项目根目录", subtype='DIR_PATH', update=_sync_animation_source
    )
    project_index_state: bpy.props.StringProperty(name="动画索引状态", default="missing")
    project_index_schema_version: bpy.props.IntProperty(name="动画索引架构", default=0)
    project_index_generation: bpy.props.IntProperty(name="动画索引生成", default=0)
    project_index_animation_count: bpy.props.IntProperty(name="已索引动画", default=0)
    project_index_match_count: bpy.props.IntProperty(name="匹配动画", default=0)
    project_index_last_scan_ns: bpy.props.StringProperty(name="动画索引上次扫描", default="")
    project_index_message: bpy.props.StringProperty(name="动画索引消息", default="")
    project_index_job_id: bpy.props.StringProperty(
        name="动画索引任务", default="", options={'SKIP_SAVE'}
    )
    project_index_request_generation: bpy.props.IntProperty(
        name="动画索引请求代次", default=0, options={'SKIP_SAVE'}
    )
    project_index_progress: bpy.props.FloatProperty(
        name="动画索引进度", default=0.0, min=0.0, max=1.0,
        subtype='FACTOR', options={'SKIP_SAVE'},
    )
    project_index_progress_phase: bpy.props.StringProperty(
        name="动画索引进度阶段", default="", options={'SKIP_SAVE'}
    )
    project_index_progress_path: bpy.props.StringProperty(
        name="动画索引进度路径", default="", options={'SKIP_SAVE'}
    )
    project_index_cancel_requested: bpy.props.BoolProperty(
        name="已请求取消动画索引", default=False, options={'SKIP_SAVE'}
    )
    manual_source_dir: bpy.props.StringProperty(
        name="手动源目录", subtype='DIR_PATH', update=_sync_animation_source
    )
    source_dir: bpy.props.StringProperty(name="动画目录", subtype='DIR_PATH')
    output_dir: bpy.props.StringProperty(name="输出目录", subtype='DIR_PATH')
    library_name: bpy.props.StringProperty(name="资产库名称", default="AnimLibrary")
    skeleton_filter: bpy.props.StringProperty(
        name="骨架过滤", default="", update=_sync_animation_source
    )

    # Legacy indexed-selection fields; inactive in Folder-only mode.
    indexed_selection_mode: bpy.props.EnumProperty(
        name="索引选择来源",
        items=[
            ('NONE', "None", "No indexed selection"),
            ('AUTO_PROJECT', "Project Default", "Generated from Project Default"),
            ('EXPLICIT', "Indexed Selector", "Selected in Project Assets"),
        ],
        default='NONE',
    )
    indexed_project_root: bpy.props.StringProperty(name="索引项目目录", subtype='DIR_PATH')
    indexed_skeleton: bpy.props.StringProperty(name="索引骨架", default="")
    indexed_selected_paths: bpy.props.StringProperty(name="索引动画路径", default="[]")

    # Step 3: 扫描结果
    scan_completed: bpy.props.BoolProperty(default=False)
    scan_skeleton_count: bpy.props.IntProperty(default=0)
    scan_anim_count: bpy.props.IntProperty(default=0)
    scan_new_count: bpy.props.IntProperty(default=0)
    scan_existing_count: bpy.props.IntProperty(default=0)
    skeleton_mappings: bpy.props.CollectionProperty(type=WizardSkeletonMapping)
    unmapped_skeletons: bpy.props.StringProperty(default="")

    # Step 4: 导入状态
    import_target_skeleton: bpy.props.StringProperty(name="目标骨架")
    import_completed: bpy.props.BoolProperty(default=False)
    skeleton_compatibility_mode: bpy.props.EnumProperty(
        name="骨架兼容策略", items=SKELETON_COMPATIBILITY_ITEMS,
        default=SKELETON_COMPATIBILITY_STRICT,
        description=(
            "严格身份是默认模式。按名称兼容是"
            "显式的部分重定向路径，不等同于 Unreal Retarget Manager。"
        ),
    )

    # Step 4: 导入参数（从Operator移到PropertyGroup）
    import_scale: bpy.props.FloatProperty(
        name="缩放",
        default=0.01,
        min=0.0001,
        max=100.0,
        precision=4
    )
    rotation_only: bpy.props.BoolProperty(
        name="仅导入旋转",
        default=False
    )
    import_curves: bpy.props.BoolProperty(
        name="导入曲线",
        default=True
    )

    # 大批量导入任务切片参数
    import_batch_size: bpy.props.IntProperty(
        name="导入批次",
        default=200,
        min=20,
        max=2000,
        description="每批导入动作数量，越小越稳，越大越快"
    )
    import_large_threshold: bpy.props.IntProperty(
        name="巨量阈值",
        default=2000,
        min=200,
        max=50000,
        description="超过该数量自动强制降批次以防崩溃"
    )
    import_max_cycles: bpy.props.IntProperty(
        name="最大循环",
        default=500,
        min=10,
        max=50000,
        description="导入批次循环上限"
    )
    import_max_consecutive_failures: bpy.props.IntProperty(
        name="连续失败阈值",
        default=120,
        min=10,
        max=5000,
        description="连续失败达到阈值后提前中止任务"
    )
    import_resume_enabled: bpy.props.BoolProperty(
        name="启用断点恢复",
        default=True,
        description="导入中断后可从上次批次位置继续"
    )

    # 导入后清理选项
    clean_after_import: bpy.props.BoolProperty(
        name="导入后清理动作",
        default=False,
        description="导入完成后自动清理冗余关键帧（适用于动捕/烘焙动作）"
    )
    clean_threshold: bpy.props.FloatProperty(
        name="精简阈值",
        default=0.001,
        min=0.0,
        max=1.0,
        precision=4
    )
    clean_flat_channels: bpy.props.BoolProperty(
        name="精简平坦通道",
        default=True
    )

    # Step 5: 资产库
    lib_generate_thumbnails: bpy.props.BoolProperty(name="生成缩略图", default=True)
    lib_thumb_resolution: bpy.props.EnumProperty(
        name="缩略图分辨率",
        items=[('128', "128", ""), ('256', "256", ""), ('512', "512", ""), ('1024', "1024", "")],
        default='256'
    )
    lib_thumb_quality: bpy.props.EnumProperty(
        name="渲染质量",
        items=[
            ('OPENGL', "OpenGL (快)", "视口快照, 速度快"),
            ('RENDER', "Render (高质量)", "使用场景渲染引擎, 速度慢但质量高"),
        ],
        default='OPENGL'
    )
    lib_thumb_isolate: bpy.props.BoolProperty(
        name="仅渲染骨架子级",
        description="渲染时隐藏场景其他物体，仅保留骨架及其子级网格",
        default=True
    )
    lib_chunk_size: bpy.props.IntProperty(name="分卷大小", default=500, min=50, max=5000)
    lib_force_rebuild: bpy.props.BoolProperty(name="强制重建", default=False)
    lib_save_model: bpy.props.BoolProperty(name="同时保存模型", default=False, description="打包动画后自动保存当前骨架的模型资产")
    lib_model_preview: bpy.props.BoolProperty(name="生成模型预览图", default=True)
    lib_completed: bpy.props.BoolProperty(default=False)


def register():
    bpy.utils.register_class(WizardSkeletonMapping)
    bpy.utils.register_class(WizardState)


def unregister():
    bpy.utils.unregister_class(WizardState)
    bpy.utils.unregister_class(WizardSkeletonMapping)
