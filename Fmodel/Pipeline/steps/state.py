
# FModel_Tools/Fmodel/Pipeline/steps/state.py
# 向导状态

import bpy

try:
    from ....core import (
        SKELETON_COMPATIBILITY_ITEMS,
        SKELETON_COMPATIBILITY_UEFORMAT,
        skeleton_provenance_from_owner,
    )
except (ImportError, AttributeError):
    # Keep isolated unit-test imports and older checkpoints loadable.
    SKELETON_COMPATIBILITY_UEFORMAT = "ueformat"
    SKELETON_COMPATIBILITY_ITEMS = (("ueformat", "UEFormat 原版绑定", "绑定同名骨骼，跳过未匹配轨道"),)
    skeleton_provenance_from_owner = None


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
    state.scan_pose_count = 0
    state.scan_changed_count = 0
    state.scan_unverified_count = 0
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
    provenance = (
        skeleton_provenance_from_owner(armature)
        if skeleton_provenance_from_owner else None
    )
    if provenance and provenance.is_strict:
        skeleton_name = provenance.identity["skeleton_path"].rsplit("/", 1)[-1]
    elif provenance and not provenance.is_legacy:
        skeleton_name = ""
    else:
        legacy_name = armature.get("FModel_Skeleton", "")
        skeleton_name = legacy_name.strip() if isinstance(legacy_name, str) else ""
    library_name = skeleton_name or armature.name
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
                root, ".fmodel", "animation-libraries", library_name
            ),
            "library_name": library_name,
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
        name="Legacy compatibility", items=SKELETON_COMPATIBILITY_ITEMS, options={"HIDDEN"},
        default=SKELETON_COMPATIBILITY_UEFORMAT,
    )
    source_count: bpy.props.IntProperty(name="动画", default=0)
    status: bpy.props.StringProperty(name="状态", default="pending")


class WizardState(bpy.types.PropertyGroup):
    """向导状态"""
    # Persisted step numbers stay stable; legacy step 1 is displayed as Sources.
    current_step: bpy.props.IntProperty(name="当前步骤", default=2, min=1, max=4)

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
    manual_source_dir: bpy.props.StringProperty(
        name="手动源目录", subtype='DIR_PATH', update=_sync_animation_source
    )
    source_dir: bpy.props.StringProperty(name="动画目录", subtype='DIR_PATH')
    output_dir: bpy.props.StringProperty(name="输出目录", subtype='DIR_PATH', update=_sync_animation_source)
    library_name: bpy.props.StringProperty(name="资产库名称", default="AnimLibrary", update=_sync_animation_source)
    skeleton_filter: bpy.props.StringProperty(
        name="骨架过滤", default="", update=_sync_animation_source
    )

    # Step 3: 扫描结果
    scan_completed: bpy.props.BoolProperty(default=False)
    scan_skeleton_count: bpy.props.IntProperty(default=0)
    scan_anim_count: bpy.props.IntProperty(default=0)
    scan_new_count: bpy.props.IntProperty(default=0)
    scan_existing_count: bpy.props.IntProperty(default=0)
    scan_pose_count: bpy.props.IntProperty(default=0)
    scan_changed_count: bpy.props.IntProperty(default=0)
    scan_unverified_count: bpy.props.IntProperty(default=0)
    skeleton_mappings: bpy.props.CollectionProperty(type=WizardSkeletonMapping)
    unmapped_skeletons: bpy.props.StringProperty(default="")

    # Step 4: 导入状态
    import_target_skeleton: bpy.props.StringProperty(name="目标骨架")
    import_completed: bpy.props.BoolProperty(default=False)
    skeleton_compatibility_mode: bpy.props.EnumProperty(
        name="Legacy compatibility", items=SKELETON_COMPATIBILITY_ITEMS, options={"HIDDEN"},
        default=SKELETON_COMPATIBILITY_UEFORMAT,
        description=(
            "按 UEFormat 原版绑定同名骨骼；"
            "骨架标签仅用于动画库检索。"
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
