# FModel_Tools/ui.py
# UI 面板 — 插件偏好设置、主面板与子面板
# Merged: backup pipeline/asset panels + new material/preferences/tools panels

import bpy
import os
import json
from bpy_extras.io_utils import ExportHelper

from .core.i18n import t
from .core.workflow import resolve_target
from .Assets.Anims import ui_custom_grid
from .Assets.Anims.anim_manager import (
    _sync_manager_library_index, inspect_action_metadata, invalidate_library_scan,
)


class PIPELINE_OT_PickSkeleton(bpy.types.Operator):
    """从选中骨架拾取 FModel_Skeleton 属性填入过滤字段"""
    bl_idname = "pipeline.pick_skeleton_filter"
    bl_label = "拾取骨架名"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        obj = context.active_object
        state = context.scene.pipeline_wizard
        sk_name = obj.get("FModel_Skeleton", "")
        if sk_name:
            state.skeleton_filter = str(sk_name)
            self.report({'INFO'}, f"已填入: {sk_name}")
        else:
            state.skeleton_filter = obj.name
            self.report({'INFO'}, f"无 FModel_Skeleton 属性, 已填入对象名: {obj.name}")
        return {'FINISHED'}


# ============================================================
# 插件偏好设置 (new)
# ============================================================

class FMODEL_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        layout = self.layout
        layout.label(text=t("FModel Tools Next — UEFormat 原生管线"), icon='INFO')
        layout.label(text=t("运行时状态见 3D 视图侧栏：设置与诊断"), icon='SEQ_PREVIEW')


class FMODEL_CoreProperties(bpy.types.PropertyGroup):
    # Compatibility state retained for existing .blend files. The product
    # surface is now native UEFormat-only, so there are no alternate format
    # choices to expose in the UI.
    active_pipeline: bpy.props.EnumProperty(
        name="格式",
        items=[
            ('UEFORMAT', "UEFormat", "UEFormat 原生格式"),
        ],
        default='UEFORMAT'
    )
    fix_bones: bpy.props.BoolProperty(name="自动修复骨骼", default=True)
    transfer_anim: bpy.props.BoolProperty(name="无损重定向", default=True)
    clean_old_anim: bpy.props.BoolProperty(name="清理缓存", default=True)
    purge_orphans: bpy.props.BoolProperty(name="导入后深度清理材质", default=True)
    merge_morphs: bpy.props.BoolProperty(name="自动合并形态键", default=False)
    fix_meshes: bpy.props.BoolProperty(name="修复网格撕裂", default=True)


def _last_operation_modules():
    if __package__:
        from .core.operation_report import format_last_operation, load_last_operation
    else:
        from core.operation_report import format_last_operation, load_last_operation
    return load_last_operation, format_last_operation


def _last_operation_report(context):
    load_last_operation, _format_last_operation = _last_operation_modules()
    scene = getattr(context, "scene", None)
    return load_last_operation(scene) if scene is not None else None


def _last_operation_text(context):
    load_last_operation, format_last_operation = _last_operation_modules()
    scene = getattr(context, "scene", None)
    report = load_last_operation(scene) if scene is not None else None
    if report is None:
        return ""
    return format_last_operation(report, _ueformat_runtime_status())


class FMODEL_OT_CopyLastOperationReport(bpy.types.Operator):
    bl_idname = "fmodel.copy_last_operation_report"
    bl_label = "复制最近操作报告"
    bl_description = "复制统一的 FModel 导入与 UEFormat 运行时报告"

    @classmethod
    def poll(cls, context):
        try:
            report = _last_operation_report(context)
            return bool(report and report.available)
        except Exception:
            return False

    def execute(self, context):
        text = _last_operation_text(context)
        if not text:
            self.report({'WARNING'}, "没有可用的 FModel 操作报告")
            return {'CANCELLED'}
        context.window_manager.clipboard = text
        self.report({'INFO'}, "FModel 操作报告已复制")
        return {'FINISHED'}


def _open_directory(directory: str) -> None:
    path_open = getattr(getattr(bpy.ops, "wm", None), "path_open", None)
    if callable(path_open):
        path_open(filepath=directory)
    elif hasattr(os, "startfile"):
        os.startfile(directory)
    else:
        raise RuntimeError("没有可用的平台文件夹打开器")


class FMODEL_OT_OpenLastOperationSource(bpy.types.Operator):
    bl_idname = "fmodel.open_last_operation_source"
    bl_label = "打开操作来源"
    bl_description = "打开包含主要源资产的文件夹"

    @classmethod
    def poll(cls, context):
        try:
            report = _last_operation_report(context)
            return bool(report and report.source_directory)
        except Exception:
            return False

    def execute(self, context):
        report = _last_operation_report(context)
        directory = report.source_directory if report is not None else ""
        if not directory or not os.path.isdir(directory):
            self.report({'ERROR'}, "源文件夹不可用")
            return {'CANCELLED'}
        try:
            _open_directory(directory)
        except (OSError, RuntimeError) as exc:
            self.report({'ERROR'}, f"无法打开源文件夹: {exc}")
            return {'CANCELLED'}
        return {'FINISHED'}




def _draw_last_operation(layout, context):
    try:
        report = _last_operation_report(context)
    except Exception as exc:
        layout.label(text=f"{t('报告不可用')}: {str(exc)[:120]}", icon='ERROR')
        return
    if report is None or not report.available:
        layout.label(text=t("没有记录 UEFormat 操作"), icon='INFO')
        return

    icon = {
        "success": 'CHECKMARK',
        "partial": 'ERROR',
        "failed": 'CANCEL',
        "cancelled": 'CANCEL',
        "running": 'TIME',
    }.get(report.status, 'INFO')
    headline = report.summary or f"{report.asset_kind or 'Operation'}: {report.status}"
    layout.label(text=headline[:160], icon=icon)
    if report.primary_source_path:
        layout.label(text=os.path.basename(report.primary_source_path)[:120], icon='FILE')
    if report.created_counts:
        counts = "  ".join(f"{name}: {count}" for name, count in report.created_counts.items())
        layout.label(text=counts[:160], icon='OUTLINER_OB_GROUP_INSTANCE')
    if report.failures:
        failure = report.failures[0]
        detail = failure.get("message") or failure.get("detail") or failure.get("path") or t("未知失败")
        code = failure.get("code", "import_failed")
        layout.label(text=f"[{code}] {str(detail)[:130]}", icon='ERROR')
        if len(report.failures) > 1:
            layout.label(text=f"{t('其他失败')}: {len(report.failures) - 1}", icon='INFO')
    if report.parse_warning:
        layout.label(text=report.parse_warning[:150], icon='ERROR')
    if report.export_operation_id:
        layout.label(text=f"{t('FModel 导出')}: {report.export_operation_id}", icon='LINKED')
    if report.import_policy:
        layout.label(text=f"{t('导入策略')}: {report.import_policy.title()}", icon='OPTIONS')
    row = layout.row(align=True)
    row.operator("fmodel.copy_last_operation_report", text=t("复制报告"), icon='COPYDOWN')
    source = row.row(align=True)
    source.enabled = bool(report.source_directory)
    source.operator("fmodel.open_last_operation_source", text=t("打开来源"), icon='FILE_FOLDER')


def _ueformat_runtime_status():
    """Read Vendor state without importing auto_handlers during module load."""
    try:
        if __package__:
            from . import auto_handlers
        else:
            import auto_handlers
        getter = getattr(auto_handlers, "get_vendor_status", None)
        return getter() if callable(getter) else {"state": "pending", "message": "UEFormat 运行时正在加载"}
    except Exception as exc:
        return {"state": "unknown", "message": str(exc)}




def _runtime_state_label(value):
    return t({
        "ready": "已就绪",
        "observed": "已检测",
        "external": "外部运行时",
        "failed": "失败",
        "pending": "等待中",
        "unknown": "未知",
    }.get(str(value or ""), str(value or "未知")))


def _draw_ueformat_runtime_status(layout):
    status = _ueformat_runtime_status()
    state = status.get("state", "unknown")
    icon = {
        "ready": "CHECKMARK",
        "observed": "CHECKMARK",
        "external": "LINKED",
        "failed": "ERROR",
        "pending": "TIME",
    }.get(state, "INFO")
    version = status.get("version")
    suffix = f" ({version})" if version else ""
    row = layout.row(align=True)
    row.label(text=f"UEFormat {t('运行时')}: {_runtime_state_label(state)}{suffix}", icon=icon)
    capabilities = status.get("capabilities") or {}
    external_incompatible = (
        state == "external"
        and isinstance(capabilities, dict)
        and capabilities.get("current") is False
    )
    if state == "external":
        layout.label(
            text=str(status.get("message", "已选择外部 UEFormat 运行时"))[:160],
            icon="ERROR" if external_incompatible else "INFO",
        )
        source_path = str(status.get("source_path", "") or "")
        if source_path:
            layout.label(text=f"{t('外部来源')}: {source_path}"[:160], icon='FILE_SCRIPT')
        remediation = str(status.get("remediation", "") or "")
        if remediation:
            layout.label(text=remediation[:160], icon='INFO')
    elif state in {"failed", "unknown"}:
        layout.label(
            text=str(status.get("message", "UEFormat 运行时不可用"))[:160],
            icon="ERROR",
        )
        remediation = str(status.get("remediation", "") or "")
        if remediation:
            layout.label(text=remediation[:160], icon='INFO')
    tree_sha256 = str(status.get("tree_sha256", "") or "")
    if state in {"ready", "observed"} and tree_sha256:
        layout.label(text=f"{t('Vendor 树')}: {tree_sha256[:12]}", icon='FILE_TICK')


# ============================================================
# 1. 主面板
# ============================================================

class FMODEL_PT_WorkContext(bpy.types.Panel):
    bl_label = "当前工作对象"
    bl_idname = "FMODEL_PT_work_context"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        workspace = context.scene.fmodel_workflow
        row = layout.row(align=True)
        row.prop(workspace, "target", text=t("目标骨架"))
        row.operator("fmodel.set_work_target", text="", icon='EYEDROPPER')
        if resolve_target(context) is None:
            layout.label(text=t("请选择目标骨架；姿势源可直接导入"), icon='INFO')
        props = getattr(context.scene, "fmodel_assets", None)
        if props is not None:
            if props.project_libs:
                layout.label(text=t("当前资产库"), icon='ASSET_MANAGER')
                layout.template_list("ASSETS_UL_ProjectLibraries", "workspace",
                                     props, "project_libs", props, "active_lib_index", rows=2)
                row = layout.row(align=True)
                row.operator("fmodel.browse_lib_path", text=t("选择库目录"), icon='FILE_FOLDER')
                row.operator("fmodel.mount_blend_file", text=t("添加库文件"), icon='FILE_BLEND')
            else:
                layout.operator("fmodel.add_library", text=t("添加资产库"), icon='ADD')
        status = _ueformat_runtime_status()
        if status.get("state") not in {"ready", "observed"}:
            layout.label(text=t("运行环境需要检查，详见设置与诊断"), icon='INFO')
        report = _last_operation_report(context)
        if report and report.available:
            layout.label(text=(report.summary or report.status)[:64],
                         icon='ERROR' if report.status in {'failed', 'partial'} else 'INFO')


class FMODEL_PT_Toolbox(bpy.types.Panel):
    bl_label = "导入与建库"
    bl_idname = "FMODEL_PT_toolbox"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_order = 1
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.label(text=t("快速导入"), icon='IMPORT')
        layout.operator("fmodel.import_model_uemodel", text=t("模型 (.uemodel)"), icon='MESH_DATA')
        row = layout.row()
        row.enabled = resolve_target(context) is not None
        row.operator("fmodel.import_anim_ueanim", text=t("动画 (.ueanim)"), icon='ANIM_DATA')
        layout.operator("fmodel.import_anim_uepose", text=t("姿势 (.uepose)"), icon='POSE_HLT')
        layout.separator()
        layout.prop(context.scene.fmodel_workflow, "show_builder", toggle=True, icon='PACKAGE')


class FMODEL_PT_Diagnostics(bpy.types.Panel):
    bl_label = "设置与诊断"
    bl_idname = "FMODEL_PT_diagnostics"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_order = 6
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.label(text=t("UEFormat 原生管线"), icon='INFO')
        _draw_ueformat_runtime_status(layout)
        header, body = layout.panel('fmodel_operation_details', default_closed=False)
        header.label(text=t("最近操作"), icon='INFO')
        if body:
            _draw_last_operation(body, context)


# ============================================================
# 2. 资产管线 (from backup)
# ============================================================

def _draw_pipeline_mapping(layout, context, state):
    if not state.scan_anim_count:
        return
    box = layout.box()
    box.label(text=t("目标骨架"), icon='ARMATURE_DATA')
    if state.scan_skeleton_count > 1:
        for mapping in state.skeleton_mappings:
            entry = box.box()
            entry.label(text=f"{mapping.source_skeleton} · {mapping.source_count}")
            entry.prop(mapping, "target_armature", text=t("目标骨架"))
        missing = sum(1 for item in state.skeleton_mappings if not item.target_armature)
        if missing:
            box.label(text=f"{t('未映射')}: {missing}", icon='ERROR')
    else:
        target = resolve_target(context)
        box.label(text=f"{t('目标骨架')}: {target.name if target else t('未指定')}")


class FMODEL_PT_Pipeline(bpy.types.Panel):
    bl_label = "批量建立动画库"
    bl_idname = "FMODEL_PT_pipeline"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return context.scene.fmodel_workflow.show_builder

    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "pipeline_wizard"):
            layout.label(text=t("管线模块未加载"), icon='INFO')
            return

        state = context.scene.pipeline_wizard
        step = max(2, state.current_step)

        row = layout.row(align=True)
        row.scale_y = 1.2
        steps = [
            (2, "来源", bool(state.source_dir and state.output_dir)),
            (3, "扫描与匹配", state.scan_completed),
            (4, "建立资产库", state.lib_completed),
        ]
        for num, name, done in steps:
            sub = row.row(align=True)
            if num == step:
                op = sub.operator("pipeline.wizard_goto_step", text=t(name), depress=True)
            elif done:
                op = sub.operator("pipeline.wizard_goto_step", text=t(name), icon='CHECKMARK')
            else:
                op = sub.operator("pipeline.wizard_goto_step", text=t(name), icon='RADIOBUT_OFF')
            op.target_step = num

        layout.separator()

        if step == 2:
            col = layout.column(align=True)
            col.label(
                text=t("选择动画来源和资产库输出"),
                icon='FILE_FOLDER',
            )
            col.prop(state, "manual_source_dir", text=t("动画文件夹"))
            col.prop(state, "output_dir", text=t("输出目录"))
            col.prop(state, "library_name", text=t("库名称"))
            if state.flow_type == 'FMODEL':
                row = col.row(align=True)
                row.prop(state, "skeleton_filter", text=t("骨架过滤"))
                row.operator("pipeline.pick_skeleton_filter", text="", icon='EYEDROPPER')

            source_dir = getattr(state, "manual_source_dir", "")
            ready = bool(source_dir and state.output_dir and state.library_name)
            layout.separator()
            if ready:
                layout.operator("pipeline.wizard_step2", text=t("下一步: 扫描"), icon='FORWARD')
            else:
                box = layout.box()
                box.label(text=t("请填写动画文件夹、输出目录和库名称"), icon='INFO')

        elif step == 3:
            source_dir = getattr(state, "manual_source_dir", "")
            can_scan = bool(source_dir and state.output_dir)
            if not can_scan:
                layout.label(text=t("请先填写来源与输出"), icon='ERROR')
                return
            if not state.scan_completed:
                row = layout.row(align=True)
                row.scale_y = 1.3
                row.operator(
                    "pipeline.wizard_step3", text=t("扫描动画文件夹"),
                    icon='VIEWZOOM',
                )
                layout.label(
                    text=t("扫描当前文件夹中的 UEFormat 动画文件"),
                    icon='FILE_CACHE',
                )
            else:
                col = layout.column(align=True)
                col.label(text=f"{t('骨架')}: {state.scan_skeleton_count}", icon='ARMATURE_DATA')
                col.label(text=f"{t('动画')}: {state.scan_anim_count}", icon='ANIM_DATA')
                col.label(text=f"{t('新增')}: {state.scan_new_count}", icon='ADD')
                col.label(text=f"{t('源未变更')}: {getattr(state, 'scan_existing_count', 0)}")
                col.label(text=f"{t('源已变更')}: {getattr(state, 'scan_changed_count', 0)}")
                col.label(text=f"{t('版本待校验')}: {getattr(state, 'scan_unverified_count', 0)}")
                if getattr(state, "scan_pose_count", 0):
                    col.label(text=f"{t('姿势资源')}: {state.scan_pose_count}", icon='ARMATURE_DATA')
                    col.label(text=t("姿势导入到姿势资产库，可应用或烘焙"))
                    col.operator("fmodel.import_anim_uepose", text=t("导入姿势"), icon='IMPORT')
                _draw_pipeline_mapping(layout, context, state)
                layout.operator("pipeline.wizard_step3", text=t("重新扫描"), icon='FILE_REFRESH')
                if not state.scan_anim_count:
                    return
                layout.separator()
                op = layout.operator(
                    "pipeline.wizard_goto_step",
                    text=t("下一步: 建立资产库"),
                    icon='FORWARD',
                )
                op.target_step = 4

        elif step == 4:
            # Once the import/package operation has completed, the full Step 4
            # editor is no longer actionable.  Keeping every mapping row and
            # import option live here makes Blender redraw a potentially large
            # CollectionProperty on every sidebar refresh.  Render a compact
            # completion view instead; this is the same user-visible state as
            # collapsing the panel, without requiring manual intervention.
            if getattr(state, "lib_completed", False):
                box = layout.box()
                box.label(text=t("导入并打包已完成"), icon='CHECKMARK')
                if getattr(state, "scan_anim_count", 0):
                    box.label(
                        text=f"{t('动画')}: {state.scan_anim_count}",
                        icon='ANIM_DATA',
                    )
                if getattr(state, "scan_skeleton_count", 0):
                    box.label(
                        text=f"{t('骨架')}: {state.scan_skeleton_count}",
                        icon='ARMATURE_DATA',
                    )
                if getattr(state, "unmapped_skeletons", ""):
                    box.label(
                        text=f"{t('未映射')}: {state.unmapped_skeletons}",
                        icon='ERROR',
                    )
                layout.operator("fmodel.open_workflow", text=t("浏览动画资产"), icon='ASSET_MANAGER').destination = 'ASSETS'
                layout.operator("fmodel.open_workflow", text=t("调整构建设置"), icon='PREFERENCES').destination = 'EDIT_BUILD'
                layout.operator("pipeline.wizard_step3", text=t("重新扫描"), icon='FILE_REFRESH')
                layout.operator(
                    "pipeline.wizard_reset",
                    text=t("重新开始"),
                    icon='FILE_REFRESH',
                )
                return

            can_import = state.scan_completed and state.scan_anim_count > 0
            obj = resolve_target(context)
            has_arm = obj is not None
            stored_target = obj
            has_stored_target = has_arm
            if not can_import:
                layout.label(text=t("请先完成扫描与匹配"), icon='ERROR')
                return
            if has_stored_target:
                layout.label(text=f"骨架: {stored_target.name}", icon='ARMATURE_DATA')
            elif has_arm:
                layout.label(text=f"骨架: {obj.name}", icon='ARMATURE_DATA')
            elif state.scan_skeleton_count <= 1:
                layout.label(text=t("请选中骨架"), icon='ERROR')
            layout.separator()

            _draw_pipeline_mapping(layout, context, state)

            # Import params
            box_import = layout.box()
            box_import.label(text=t("导入参数"), icon='PREFERENCES')
            col = box_import.column(align=True)
            col.prop(state, "import_scale")
            col.prop(state, "rotation_only")
            col.prop(state, "import_curves")

            # Thumbnail params
            box_thumb = layout.box()
            box_thumb.label(text=t("缩略图"), icon='RENDER_STILL')
            col = box_thumb.column(align=True)
            col.prop(state, "lib_generate_thumbnails")
            if state.lib_generate_thumbnails:
                col.prop(state, "lib_thumb_resolution", text="分辨率")
                col.prop(state, "lib_thumb_quality", text="质量")
                if state.lib_thumb_quality == 'RENDER':
                    col.prop(state, "lib_thumb_isolate", text="仅渲染骨架子级")

            # Build params
            box_build = layout.box()
            box_build.label(text=t("打包选项"), icon='PACKAGE')
            col = box_build.column(align=True)
            col.prop(state, "lib_chunk_size")
            col.prop(state, "lib_force_rebuild")
            col.prop(state, "lib_save_model", icon='OUTLINER_OB_MESH')
            if state.lib_save_model:
                col.prop(state, "lib_model_preview", icon='CAMERA_DATA')

            layout.separator()
            row = layout.row(align=True)
            row.scale_y = 1.3
            mappings_ready = bool(state.skeleton_mappings) and all(
                mapping.target_armature for mapping in state.skeleton_mappings
            )
            target_ready = (
                has_arm or has_stored_target
                if state.scan_skeleton_count <= 1 else mappings_ready
            )
            row.enabled = target_ready and not state.lib_completed
            row.operator("pipeline.wizard_step4", text=t("导入并打包"), icon='IMPORT')

        layout.separator()
        layout.operator("pipeline.wizard_reset", text=t("重新开始"), icon='FILE_REFRESH')


def _load_current_pipeline_context(context):
    from .core import PipelineContext
    state = getattr(context.scene, "pipeline_wizard", None)
    output_dir = getattr(state, "output_dir", "")
    if output_dir:
        output_dir = bpy.path.abspath(output_dir)
        return (PipelineContext.load_from_checkpoint(output_dir)
                or PipelineContext.load_from_blender_text(output_dir=output_dir))
    return PipelineContext.load_from_blender_text()


class FMODEL_PT_PipelineDebug(bpy.types.Panel):
    bl_label = "管线调试"
    bl_idname = "FMODEL_PT_pipeline_debug"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_parent_id = "FMODEL_PT_diagnostics"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        row_ops = layout.row(align=True)
        row_ops.operator("pipeline.export_context_json", text=t("导出上下文"), icon='EXPORT')
        row_ops.operator("pipeline.clear_context", text=t("清除旧版场景缓存"), icon='TRASH')
        layout.separator()
        ctx = _load_current_pipeline_context(context)
        if ctx:
            layout.label(text=f"状态: {ctx.current_stage}")
            if ctx.config and ctx.config.get("source_dir"):
                layout.label(text=f"动画目录: {ctx.config['source_dir']}")


# ============================================================
# 4. 拓展工具 (new - with per-panel poll)
# ============================================================

class FMODEL_PT_GeneralTools(bpy.types.Panel):
    bl_label = "辅助工具"
    bl_order = 5
    bl_idname = "FMODEL_PT_general_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        pass


class FMODEL_PT_Mesh(bpy.types.Panel):
    bl_label = "网格工具"
    bl_idname = "FMODEL_PT_mesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_parent_id = "FMODEL_PT_general_tools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("fmodel.clean_empty_morphs", text=t("清理空白形态键"), icon='BRUSH_DATA')
        col.operator("fmodel.separate_by_material_smart", text=t("按材质分离"), icon='MATERIAL')


class FMODEL_PT_Skeleton(bpy.types.Panel):
    bl_label = "骨架 / 姿态"
    bl_idname = "FMODEL_PT_skeleton"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_parent_id = "FMODEL_PT_general_tools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        skel_props = context.scene.fmodel_skeleton
        obj = context.active_object

        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("fmodel.sort_bones", text=t("整理骨骼层级"), icon='GROUP_BONE')
        row = col.row(align=True)
        row.operator("fmodel.rename_bones", text=t("批量重命名"), icon='SORTALPHA')
        row.operator("fmodel.fix_bone_names", text=t("UE标准"), icon='CHECKMARK')
        row = col.row(align=True)
        row.operator("fmodel.generate_bone_mapping", text=t("生成映射"), icon='TEXT')
        row.operator("fmodel.map_bone_names", text=t("应用映射"), icon='FILE_TEXT')

        layout.separator()
        if not (obj and obj.type == 'ARMATURE' and obj.mode == 'POSE'):
            layout.label(text=t("姿态镜像需进入姿态模式"), icon='INFO')
            return

        row_src = layout.row(align=True)
        row_src.prop_search(skel_props, "mirror_source_bone", obj.pose, "bones", text="源", icon='BONE_DATA')
        row_src.operator("fmodel.grab_active_bone", text="", icon='EYEDROPPER').target_field = 'SOURCE'
        row_tgt = layout.row(align=True)
        row_tgt.prop_search(skel_props, "mirror_target_bone", obj.pose, "bones", text="目标", icon='BONE_DATA')
        row_tgt.operator("fmodel.grab_active_bone", text="", icon='EYEDROPPER').target_field = 'TARGET'
        row_adv = layout.row()
        row_adv.prop(skel_props, "show_mirror_adv", text="选项",
                     icon='TRIA_DOWN' if skel_props.show_mirror_adv else 'TRIA_RIGHT', emboss=False)
        if skel_props.show_mirror_adv:
            box = layout.box()
            box.prop(skel_props, "mirror_plane", expand=True)
            row = box.row(align=True)
            row.prop(skel_props, "mirror_apply_loc", toggle=True)
            row.prop(skel_props, "mirror_apply_rot", toggle=True)
            row.prop(skel_props, "mirror_strict_axis", toggle=True)
        layout.operator("fmodel.mirror_pose_custom", text=t("执行镜像"), icon='PLAY')


class FMODEL_PT_Animation(bpy.types.Panel):
    bl_label = "动画工具"
    bl_idname = "FMODEL_PT_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_parent_id = "FMODEL_PT_general_tools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        box_bake = layout.box()
        box_bake.label(text=t("动画清理"), icon='ARMATURE_DATA')
        col_bake = box_bake.column(align=True)
        col_bake.scale_y = 1.2
        col_bake.operator("fmodel.clean_all_actions", text=t("清理动画数据"), icon='TRASH')
        col_bake.operator("fmodel.clean_action", text=t("清理当前动作"), icon='BRUSH_DATA')


# ============================================================
# 5. 资产库 (from backup)
# ============================================================

class FMODEL_PT_AssetLibrary(bpy.types.Panel):
    bl_label = "资产浏览"
    bl_order = 3
    bl_idname = "FMODEL_PT_asset_library"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'

    def draw(self, context):
        self.layout.prop(context.scene.fmodel_workflow, "asset_kind", expand=True)


class FMODEL_PT_LibrarySettings(bpy.types.Panel):
    """资产库设置"""
    bl_label = "保存与库管理"
    bl_order = 4
    bl_idname = "FMODEL_PT_library_settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        if not hasattr(context.scene, "fmodel_assets"):
            layout.label(text=t("资产库模块未加载"), icon='INFO')
            return

        if not hasattr(context.scene, "fmodel_anim_manager"):
            layout.label(text=t("请先扫描"), icon='INFO')
            return

        props = context.scene.fmodel_assets
        manager = context.scene.fmodel_anim_manager

        publish = layout.box()
        publish.label(text=t("保存资产"), icon='EXPORT')
        publish.operator("fmodel.save_model", text=t("保存目标模型"), icon='MESH_DATA')
        publish.operator("fmodel.save_pose_library", text=t("保存姿势源库"), icon='POSE_HLT')
        publish.operator("fmodel.open_workflow", text=t("从动画文件夹建立库"), icon='PACKAGE').destination = 'BUILD'
        layout.separator()
        row = layout.row()
        row.template_list(
            "ASSETS_UL_ProjectLibraries", "",
            props, "project_libs",
            props, "active_lib_index",
            rows=3
        )
        col_btns = row.column(align=True)
        col_btns.operator("fmodel.add_library", text="", icon='ADD')
        col_btns.operator("fmodel.remove_library", text="", icon='REMOVE')
        col_btns.separator()
        col_btns.operator("fmodel.browse_lib_path", text="", icon='FILE_FOLDER')
        col_btns.operator("fmodel.mount_blend_file", text="", icon='FILE_BLEND')
        col_btns.separator()
        col_btns.operator("fmodel.open_lib_folder", text="", icon='SCREEN_BACK')

        if 0 <= props.active_lib_index < len(props.project_libs):
            library = props.project_libs[props.active_lib_index]
            layout.prop(library, "name", text=t("库名称"))
            layout.prop(library, "path", text=t("路径"))
            if not library.path or not os.path.exists(bpy.path.abspath(library.path)):
                layout.label(text=t("请选择有效的资产库路径"), icon='ERROR')
        if manager.tree_items:
            layout.label(text=t("扫描内容"))
            layout.template_list(
                "ASSETS_UL_LibraryTree", "", manager, "tree_items",
                manager, "active_library_index", rows=5,
            )

        layout.separator()
        row_scan = layout.row(align=True)
        row_scan.scale_y = 1.2
        row_scan.operator("fmodel.scan_anims", text=t("扫描全部"), icon='VIEWZOOM')
        row_scan.operator("fmodel.scan_local_anims", text=t("本地"), icon='FILE_REFRESH')

        layout.separator()
        header, maintenance = layout.panel("fmodel_library_maintenance", default_closed=True)
        header.label(text=t("库维护"), icon='TOOL_SETTINGS')
        if maintenance is None:
            return
        layout = maintenance

        repair = layout.row(align=True)
        repair.operator("fmodel.repair_library_metadata", text="修复缺失动画元数据", icon='TOOL_SETTINGS')
        layout.operator("pipeline.rebake_thumbnails", text=t("重建库与缩略图"), icon='RENDER_STILL')
        layout.label(text=t("重建会重新发布动画库文件"), icon='INFO')

        row_mount = layout.row(align=True)
        row_mount.operator("fmodel.mount_all_libs", text=t("挂载到 Blender"), icon='LINKED')
        row_mount.operator("fmodel.unmount_all_libs", text=t("卸载"), icon='UNLINKED')


def _model_draw_panel(self, context):
    from .Assets.Models.operators import (
        get_model_dir, _scan_models, _get_preview_icon, _invalidate_model_scan_cache,
    )
    layout = self.layout

    if not hasattr(context.scene, "fmodel_model_assets"):
        layout.label(text=t("模型资产模块未加载"), icon='INFO')
        return

    props = context.scene.fmodel_model_assets

    # ── Actions ──
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator("fmodel.open_model_selector", text=t("浏览导入"), icon='IMPORT')
    row.operator("fmodel.refresh_model_browser", text="", icon='FILE_REFRESH')

    active_lib_path = get_model_dir(context)
    if active_lib_path:
        display_path = active_lib_path
        if len(display_path) > 45:
            display_path = "..." + display_path[-42:]
        layout.label(text=display_path, icon='FILE_FOLDER')

    layout.separator()

    # ── Model list ──
    layout.prop(props, "search", text="", icon='VIEWZOOM')
    models = [m for m in _scan_models(context) if props.search.casefold() in m["name"].casefold()]
    if not models:
        layout.label(text=t("未保存模型"), icon='INFO')
        return

    layout.label(text=f"{len(models)} 个模型", icon='OUTLINER_OB_ARMATURE')

    selected_name = props.selected_model
    selected_model = next((m for m in models if m["name"] == selected_name), None)

    # ── Selected detail ──
    if selected_model:
        box = layout.box()
        icon_id = _get_preview_icon(selected_model["preview"])
        if icon_id:
            icon_row = box.row()
            icon_row.alignment = 'CENTER'
            icon_row.template_icon(icon_value=icon_id, scale=6.0)
        metadata = box.row(align=True)
        metadata.alignment = 'CENTER'
        metadata.label(text=selected_model["name"], icon='OUTLINER_OB_ARMATURE')

        meta = selected_model.get("meta", {})
        if meta:
            stats = box.row(align=True)
            stats.alignment = 'CENTER'
            parts = []
            if meta.get("bone_count"): parts.append(f"{meta['bone_count']} 骨骼")
            if meta.get("mesh_count"): parts.append(f"{meta['mesh_count']} 网格")
            if meta.get("vert_count"): parts.append(f"{meta['vert_count']:,} 顶点")
            stats.label(text=" · ".join(parts) if parts else "")

        row_ops = box.row(align=True)
        row_ops.scale_y = 1.2
        op_imp = row_ops.operator("fmodel.import_model_from_browser", text=t("导入"), icon='IMPORT')
        op_imp.model_name = selected_model["name"]
        op_imp.model_dir = selected_model["path"]
        header, more = layout.panel("fmodel_model_more", default_closed=True)
        header.label(text=t("更多操作"))
        if more:
            op_del = more.operator("fmodel.delete_model", text=t("删除磁盘模型文件"), icon='TRASH')
            op_del.model_name = selected_model["name"]
            op_del.model_dir = selected_model["path"]

    # ── Grid ──
    col_count = max(1, min(3, int(getattr(context.region, "width", 320) / 150)))
    for i in range(0, len(models), col_count):
        row_cards = layout.row(align=True)
        for j in range(col_count):
            idx = i + j
            card = row_cards.column(align=True)
            if idx < len(models):
                model = models[idx]
                is_selected = model["name"] == selected_name
                icon_id = _get_preview_icon(model["preview"])
                btn = card.operator(
                    "fmodel.select_model", text=model["name"],
                    icon_value=icon_id if icon_id else 0, depress=is_selected
                )
                btn.model_name = model["name"]
            else:
                card.label(text="")


class FMODEL_PT_ModelAssets(bpy.types.Panel):
    bl_label = "模型资产"
    bl_idname = "FMODEL_PT_model_assets"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_parent_id = "FMODEL_PT_asset_library"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        return context.scene.fmodel_workflow.asset_kind == 'MODEL'

    def draw(self, context):
        _model_draw_panel(self, context)


class FMODEL_PT_AnimationAssets(bpy.types.Panel):
    bl_label = "动画资产"
    bl_idname = "FMODEL_PT_animation_assets"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_parent_id = "FMODEL_PT_asset_library"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        return context.scene.fmodel_workflow.asset_kind == 'ANIMATION'

    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "fmodel_anim_manager"):
            box = layout.box()
            box.label(text=t("在 [资产库设置] 中点击扫描"), icon='INFO')
            return

        manager = context.scene.fmodel_anim_manager
        row = layout.row(align=True)
        row.operator("fmodel.scan_anims", text=t("扫描资产库"), icon='VIEWZOOM')
        row.operator("fmodel.scan_local_anims", text=t("场景动作"), icon='FILE_REFRESH')
        layout.prop(manager, "library_scope", text=t("浏览范围"))
        anim_count = sum(1 for it in manager.tree_items if it.item_type == 'ANIM')
        if anim_count == 0:
            box = layout.box()
            box.label(text=t("未找到动画"), icon='INFO')
            return

        layout.label(text=f"{len(manager.skeleton_items)} 骨架 / {anim_count} 动画", icon='ANIM_DATA')
        row_filter = layout.row(align=True)
        row_filter.prop(manager, "active_skeleton", text="")
        row_filter.prop(manager, "search_filter", text="", icon='VIEWZOOM')
        if manager.search_filter:
            op = row_filter.operator("wm.context_set_string", text="", icon='X')
            op.data_path = "scene.fmodel_anim_manager.search_filter"
            op.value = ""

        if manager.search_filter:
            count = len(ui_custom_grid.visible_animation_indices(manager))
            layout.label(text=f"{count} 个匹配", icon='INFO')


        header, body = layout.panel("fmodel_browser_display", default_closed=True)
        header.label(text=t("显示设置"), icon='PREFERENCES')
        if body:
            _draw_animation_display(body, context)
        _draw_animation_list(layout, context)
        _draw_animation_detail(layout, context)
        _draw_animation_actions(layout, context)


def _draw_animation_actions(layout, context):
    manager = context.scene.fmodel_anim_manager
    target = resolve_target(context)
    box = layout.box()
    box.label(text=f"{t('目标骨架')}: {target.name if target else t('未指定')}", icon='ARMATURE_DATA')
    box.prop(manager, "import_mode", text=t("动作数据"))
    selected = (0 <= manager.active_item_index < len(manager.tree_items)
                and manager.active_item_index in ui_custom_grid.visible_animation_indices(manager))
    row = box.row()
    row.scale_y = 1.25
    row.enabled = selected and target is not None
    row.operator("fmodel.apply_anim", text=t("应用到骨架"), icon='IMPORT').apply_method_override = 'REPLACE'
    header, nla = layout.panel("fmodel_nla_options", default_closed=True)
    header.label(text=t("插入 NLA"), icon='NLA')
    if nla:
        nla.prop(manager, "nla_insert_mode")
        if manager.nla_insert_mode == 'OVERWRITE_TRACK':
            nla.label(text=t("已有重叠条带时拒绝插入"), icon='INFO')
        nla.prop(manager, "nla_auto_focus_new_strip")
        row = nla.row()
        row.enabled = selected and target is not None
        row.operator("fmodel.apply_anim", text=t("插入 NLA"), icon='NLA').apply_method_override = 'APPEND_NLA'


def _draw_animation_display(layout, context):
    if not hasattr(context.scene, "fmodel_anim_manager"):
        return
    manager = context.scene.fmodel_anim_manager
    row_mode = layout.row(align=True)
    row_mode.prop(manager, "display_mode", expand=True)
    layout.separator()
    row_view = layout.row(align=True)
    row_view.enabled = manager.display_mode != 'TREE'
    row_view.prop(manager, "show_as_grid", icon='MESH_GRID', text="卡片视图", toggle=True)
    row_params = layout.row(align=True)
    row_params.prop(manager, "list_rows", text=t("列表行"))
    row_col = layout.row(align=True)
    row_col.enabled = manager.display_mode != 'TREE' and manager.show_as_grid
    row_col.prop(manager, "grid_columns", text="列")
    row_col.prop(manager, "grid_row_count", text=t("网格行"))
    row_col.prop(manager, "grid_icon_scale", text="缩放", slider=True)
    if manager.display_mode == 'DETAIL_H':
        layout.prop(manager, "split_factor", text="分栏", slider=True)
    layout.separator()


def _draw_animation_list(layout, context):
    manager = getattr(context.scene, "fmodel_anim_manager", None)
    if not manager:
        return

    _draw_anim_list(layout, context, manager)

    layout.separator()
    row = layout.row(align=True)
    row.scale_y = 1.2
    op = row.operator("screen.frame_jump", text="", icon='REW')
    op.end = False
    row.operator("screen.animation_play", text="", icon='PAUSE' if getattr(context.screen, "is_animation_playing", False) else 'PLAY')
    row.prop(context.scene, "frame_current", text="", slider=True)



def _draw_anim_list(layout, context, manager):
    active_folder_id, active_skel = ui_custom_grid.selected_folder(manager)

    if manager.display_mode == 'TREE':
        layout.template_list(
            "ASSETS_UL_AnimTree", "",
            manager, "tree_items",
            manager, "active_item_index",
            rows=manager.list_rows
        )
    elif manager.display_mode == 'DETAIL_H':
        box_split = layout.box()
        split = box_split.split(factor=manager.split_factor)
        col_l = split.column()
        col_l.label(text=t("文件夹"), icon='FILE_FOLDER')
        col_l.template_list(
            "ASSETS_UL_FolderTree", "",
            manager, "tree_items",
            manager, "active_folder_index",
            rows=manager.list_rows
        )
        col_r = split.column()
        col_r.label(text=t("动画"), icon='ANIM_DATA')
        v_idxs = _get_visible_anims(manager, active_folder_id, active_skel)
        if manager.show_as_grid:
            ui_custom_grid.FModelCustomGrid.draw(
                col_r, context, manager, v_idxs,
                _draw_grid_card, _draw_empty_card
            )
        else:
            col_r.template_list(
                "ASSETS_UL_AnimGrid", "",
                manager, "tree_items",
                manager, "active_item_index",
                rows=manager.list_rows
            )
    elif manager.display_mode == 'DETAIL_V':
        col = layout.column(align=True)
        col.template_list(
            "ASSETS_UL_FolderTree", "",
            manager, "tree_items",
            manager, "active_folder_index",
            rows=manager.list_rows
        )
        col.separator(factor=1.5)
        v_idxs = _get_visible_anims(manager, active_folder_id, active_skel)
        if manager.show_as_grid:
            ui_custom_grid.FModelCustomGrid.draw(
                col, context, manager, v_idxs,
                _draw_grid_card, _draw_empty_card
            )
        else:
            col.template_list(
                "ASSETS_UL_AnimGrid", "",
                manager, "tree_items",
                manager, "active_item_index",
                rows=manager.list_rows
            )


def _draw_animation_detail(layout, context):
    manager = getattr(context.scene, "fmodel_anim_manager", None)
    if not manager:
        return

    if not 0 <= manager.active_item_index < len(manager.tree_items):
        layout.label(text=t("选择一个动画"), icon='INFO')
        return

    item = manager.tree_items[manager.active_item_index]
    if (item.item_type != 'ANIM' or manager.active_item_index not in ui_custom_grid.visible_animation_indices(manager)):
        layout.label(text=t("选择一个动画"), icon='INFO')
        return

    from .Assets.Anims.anim_manager import get_custom_icon

    # ── Upper: preview image ──
    box_img = layout.box()
    col_img = box_img.column(align=True)
    col_img.scale_y = 1.2
    icon_id = get_custom_icon(item.thumb_path, item.blend_path)
    if icon_id:
        icon_row = col_img.row(align=True)
        icon_row.alignment = 'CENTER'
        icon_row.template_icon(icon_value=icon_id, scale=8.0)
    else:
        icon_row = col_img.row(align=True)
        icon_row.alignment = 'CENTER'
        icon_row.label(text=t("无预览图"), icon='IMAGE_DATA')

    # ── Lower: details ──
    box_info = layout.box()
    col_info = box_info.column(align=True)
    col_info.scale_y = 0.9
    col_info.label(text=item.name, icon='ANIM_DATA')

    act = inspect_action_metadata(item)
    if act and act.frame_range:
        frames = int(act.frame_range[1] - act.frame_range[0])
        fps = context.scene.render.fps / max(context.scene.render.fps_base, 0.0001)
        duration = frames / fps if fps > 0 else 0
        col_info.label(text=f"帧数: {frames} ({duration:.1f}s @ {fps}fps)")
    col_info.label(text=f"骨架: {item.skeleton}")

    if item.blend_path:
        import os
        col_info.label(text=f"源: {os.path.basename(item.blend_path)}")


# ============================================================
# 动画辅助函数 (from backup)
# ============================================================

_VISIBLE_ANIMS_CACHE = {}


def _get_visible_anims(manager, active_folder_id, active_skel):
    search = manager.search_filter.lower().strip()
    key = (id(manager), manager.tree_revision, active_folder_id, active_skel, manager.active_skeleton, search,
           getattr(manager, "library_scope", "ALL"), getattr(manager, "active_library_index", -1))
    cached = _VISIBLE_ANIMS_CACHE.get(key)
    if cached is not None:
        return cached

    visible = ui_custom_grid.visible_animation_indices(manager, active_folder_id, active_skel)
    _VISIBLE_ANIMS_CACHE.clear()
    _VISIBLE_ANIMS_CACHE[key] = visible
    return visible


def _draw_grid_card(ctx, col_layout, item, item_idx, is_active, mgr):
    from .Assets.Anims.anim_manager import get_custom_icon
    card = col_layout.box()
    card.scale_x = 1.0
    card.scale_y = 1.0
    card_col = card.column(align=True)
    card_col.ui_units_x = mgr.grid_icon_scale + 2
    icon_id = get_custom_icon(item.thumb_path, item.blend_path)
    icon_row = card_col.row(align=True)
    icon_row.alignment = 'CENTER'
    icon_row.template_icon(icon_value=icon_id if icon_id else 0, scale=mgr.grid_icon_scale)
    name_row = card_col.row(align=True)
    name_row.alignment = 'CENTER'
    op_sel = name_row.operator("fmodel.select_grid_item", text=item.name, emboss=False)
    op_sel.target_index = item_idx



def _draw_empty_card(ctx, col_layout, mgr):
    card = col_layout.box()
    card.scale_x = 1.0
    card.scale_y = 1.0
    card_col = card.column(align=True)
    card_col.ui_units_x = mgr.grid_icon_scale + 2
    icon_row = card_col.row(align=True)
    icon_row.alignment = 'CENTER'
    icon_row.template_icon(icon_value=0, scale=mgr.grid_icon_scale)


# ============================================================
# 资产库 + 管线 Operators (from backup)
# ============================================================

_LIB_TREE_EXPANDED = {}

class FMODEL_OT_ToggleTreeNode(bpy.types.Operator):
    bl_idname = "fmodel.toggle_tree_node"
    bl_label = "展开/折叠"
    bl_options = {'INTERNAL'}
    node_id: bpy.props.StringProperty()

    def execute(self, context):
        if len(_LIB_TREE_EXPANDED) > 1024:
            _LIB_TREE_EXPANDED.clear()
        _LIB_TREE_EXPANDED[self.node_id] = not _LIB_TREE_EXPANDED.get(self.node_id, False)
        return {'FINISHED'}


class FMODEL_OT_SelectTreeNode(bpy.types.Operator):
    bl_idname = "fmodel.select_tree_node"
    bl_label = "选中"
    bl_options = {'INTERNAL'}
    lib_index: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.fmodel_assets
        if 0 <= self.lib_index < len(props.project_libs):
            props.active_lib_index = self.lib_index
        return {'FINISHED'}


class FMODEL_OT_AddLibrary(bpy.types.Operator):
    bl_idname = "fmodel.add_library"
    bl_label = "添加资产库"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.fmodel_assets
        item = props.project_libs.add()
        item.name = f"Library_{len(props.project_libs)}"
        props.active_lib_index = len(props.project_libs) - 1
        _sync_manager_library_index(context)
        return {'FINISHED'}


class FMODEL_OT_RemoveLibrary(bpy.types.Operator):
    bl_idname = "fmodel.remove_library"
    bl_label = "移除资产库"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.fmodel_assets
        return 0 <= props.active_lib_index < len(props.project_libs)

    def execute(self, context):
        props = context.scene.fmodel_assets
        if not (0 <= props.active_lib_index < len(props.project_libs)):
            return {'CANCELLED'}
        invalidate_library_scan(context)
        props.project_libs.remove(props.active_lib_index)
        props.active_lib_index = min(props.active_lib_index, len(props.project_libs) - 1)
        props.active_lib_index = max(0, props.active_lib_index)
        _sync_manager_library_index(context)
        return {'FINISHED'}


class PIPELINE_OT_ClearContext(bpy.types.Operator):
    bl_idname = "pipeline.clear_context"
    bl_label = "清除旧版场景缓存"
    bl_description = "仅清除场景内旧版 PipelineContext 文本缓存；磁盘检查点保留"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .core import PipelineContext
        PipelineContext.clear_blender_text()
        self.report({'INFO'}, "已清除旧版场景缓存；磁盘检查点保留")
        return {'FINISHED'}


class PIPELINE_OT_ExportContextJson(bpy.types.Operator, ExportHelper):
    bl_idname = "pipeline.export_context_json"
    bl_label = "导出管线上下文"
    bl_options = {'REGISTER'}
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = bpy.path.abspath("//PipelineContext.json")
        return super().invoke(context, event)

    def execute(self, context):
        ctx = _load_current_pipeline_context(context)
        if not ctx:
            self.report({'ERROR'}, "没有可导出的 PipelineContext")
            return {'CANCELLED'}
        try:
            os.makedirs(os.path.dirname(self.filepath) or '.', exist_ok=True)
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(ctx.to_dict(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.report({'ERROR'}, f"导出失败: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"已导出到: {self.filepath}")
        return {'FINISHED'}


# ============================================================
# Registration
# ============================================================

_classes = (
    FMODEL_AddonPreferences,
    FMODEL_CoreProperties,
    FMODEL_OT_CopyLastOperationReport,
    FMODEL_OT_OpenLastOperationSource,
    FMODEL_OT_ToggleTreeNode,
    FMODEL_OT_SelectTreeNode,
    FMODEL_OT_AddLibrary,
    FMODEL_OT_RemoveLibrary,
    PIPELINE_OT_ClearContext,
    PIPELINE_OT_ExportContextJson,
    PIPELINE_OT_PickSkeleton,
    FMODEL_PT_WorkContext,
    FMODEL_PT_Toolbox,
    FMODEL_PT_Diagnostics,
    FMODEL_PT_Pipeline,
    FMODEL_PT_PipelineDebug,
    FMODEL_PT_AssetLibrary,
    FMODEL_PT_LibrarySettings,
    FMODEL_PT_ModelAssets,
    FMODEL_PT_AnimationAssets,
    FMODEL_PT_GeneralTools,
    FMODEL_PT_Mesh,
    FMODEL_PT_Skeleton,
    FMODEL_PT_Animation,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.fmodel_core = bpy.props.PointerProperty(type=FMODEL_CoreProperties)


def unregister():
    del bpy.types.Scene.fmodel_core
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
