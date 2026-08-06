# FModel_Tools/Fmodel/Pipeline/steps/step4_import.py
# 步骤4: 导入动画 + 建立资产库 (合并原步骤4/5)

import bpy

from ....core import (
    PipelineContext, PipelineStage, safe_child_path, validate_filename_component,
)
from ..manager import PipelineManager
from .runner import run_wizard_import_plan, run_wizard_stage


class PIPELINE_OT_WizardStep4(bpy.types.Operator):
    """向导步骤4: 导入动画并建立资产库"""
    bl_idname = "pipeline.wizard_step4"
    bl_label = "导入并打包"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not hasattr(context.scene, "pipeline_wizard"):
            return False
        state = context.scene.pipeline_wizard
        obj = context.active_object
        if state.scan_skeleton_count <= 1:
            handoff_target = getattr(state, "import_target_skeleton", "")
            handoff_armature = bpy.data.objects.get(handoff_target) if handoff_target else None
            return state.scan_completed and (
                (handoff_armature and handoff_armature.type == 'ARMATURE')
                or (obj and obj.type == 'ARMATURE')
            )
        return state.scan_completed and any(
            mapping.target_armature for mapping in state.skeleton_mappings
        )

    def execute(self, context):
        try:
            return self._execute(context)
        except Exception as exc:
            self.report({'ERROR'}, f"导入并打包失败: {exc}")
            return {'CANCELLED'}

    def _execute(self, context):
        state = context.scene.pipeline_wizard

        obj = context.active_object
        if state.scan_skeleton_count <= 1 and obj and obj.type == 'ARMATURE':
            try:
                from .state import handoff_armature
                changed = handoff_armature(state, obj)
            except ModuleNotFoundError:
                changed = False
            if changed:
                self.report({'INFO'}, "目标骨架已变更，请先重新扫描")
                return {'CANCELLED'}
        handoff_target = getattr(state, "import_target_skeleton", "")
        if state.scan_skeleton_count <= 1 and not handoff_target and (not obj or obj.type != 'ARMATURE'):
            self.report({'ERROR'}, "请先选中目标骨架")
            return {'CANCELLED'}

        out_dir = bpy.path.abspath(state.output_dir)

        def prepare_task(task, task_count):
            if not state.lib_save_model:
                return True
            validate_filename_component(state.library_name, "library name")
            validate_filename_component(task.source_skeleton, "source skeleton")
            armature = bpy.data.objects.get(task.target_armature)
            if not armature or armature.type != 'ARMATURE':
                self.report({'ERROR'}, f"无法保存模型，目标骨架无效: {task.target_armature}")
                return False
            model_name = state.library_name if task_count == 1 else f"{state.library_name}_{task.source_skeleton}"
            validate_filename_component(model_name, "model filename")
            model_filename = f"{model_name}_mesh.blend"
            validate_filename_component(model_filename, "model filename")
            safe_child_path(out_dir, model_filename, "model filename")
            try:
                from ....Assets.Models.operators import save_model_logic
                if not save_model_logic(armature, out_dir, model_name, state.lib_model_preview):
                    self.report({'ERROR'}, f"保存模型失败: {task.target_armature}")
                    return False
            except Exception as exc:
                self.report({'ERROR'}, f"保存模型失败 {task.target_armature}: {exc}")
                return False
            return True

        # Each mapped skeleton is imported and packaged as an independent task.
        try:
            success, unmapped = run_wizard_import_plan(
                context,
                state,
                lambda names: self.report({'WARNING'}, f"未映射骨架已跳过: {', '.join(names)}"),
                prepare_task,
            )
        except ValueError as exc:
            self.report({'ERROR'}, f"导入目标无效: {exc}")
            return {'CANCELLED'}
        if not success:
            self.report({'ERROR'}, "导入失败，请查看日志")
            return {'CANCELLED'}

        state.import_completed = True

        # Import-plan tasks already build their own library subset.
        ok_final = run_wizard_stage(context, state, PipelineStage.FINALIZATION.value)
        if not ok_final:
            self.report({'ERROR'}, "资产库后处理失败，请查看日志")
            return {'CANCELLED'}

        state.lib_completed = True
        msg = "导入并打包完成"
        self.report({'INFO'}, msg)
        return {'FINISHED'}

    def draw(self, context):
        pass


class PIPELINE_OT_RebakeThumbnails(bpy.types.Operator):
    """仅重新生成缩略图，不重新导入动画"""
    bl_idname = "pipeline.rebake_thumbnails"
    bl_label = "重新生成缩略图"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not hasattr(context.scene, "pipeline_wizard"):
            return False
        state = context.scene.pipeline_wizard
        return state.lib_completed and bool(state.output_dir and state.library_name)

    def execute(self, context):
        try:
            return self._execute(context)
        except Exception as exc:
            self.report({'ERROR'}, f"重建失败: {exc}")
            return {'CANCELLED'}

    def _execute(self, context):
        state = context.scene.pipeline_wizard
        output_dir = bpy.path.abspath(state.output_dir)
        pipeline_context = (
            PipelineContext.load_from_checkpoint(output_dir)
            or PipelineContext.load_from_blender_text(output_dir=output_dir)
        )
        if not pipeline_context:
            self.report({'ERROR'}, "未找到可重建的管线上下文")
            return {'CANCELLED'}
        plan = (pipeline_context.multi_import_plan or {}).get("tasks", []) if pipeline_context else []
        if len(plan) > 1:
            self.report({'ERROR'}, "多骨架计划不能单独重建缩略图，请重新运行导入并打包")
            return {'CANCELLED'}
        # Force rebuild to re-process all actions for thumbnail regeneration
        previous_state_force = state.lib_force_rebuild
        previous_config = dict(pipeline_context.config)
        operation_error = None
        state.lib_force_rebuild = True
        try:
            # Keep the persisted source/import inputs while only changing the
            # explicit thumbnail rebuild flag for this operation.
            saved_config = dict(pipeline_context.config)
            saved_config["force_rebuild"] = True
            pipeline_context.config = saved_config
            pipeline_context.persist_checkpoint()
            ok_build = PipelineManager().run_stage(
                pipeline_context, PipelineStage.LIBRARY_BUILDING.value
            )
            pipeline_context.persist_checkpoint()
        except Exception as e:
            operation_error = e
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"重建失败: {e}")
            return {'CANCELLED'}
        finally:
            state.lib_force_rebuild = previous_state_force
            pipeline_context.config = previous_config
            try:
                pipeline_context.persist_checkpoint()
            except Exception:
                if operation_error is None:
                    raise
        if not ok_build:
            self.report({'ERROR'}, "缩略图重建失败，请查看日志 (LibraryBuilding stage returned False)")
            return {'CANCELLED'}
        self.report({'INFO'}, "缩略图已更新")
        return {'FINISHED'}


def register():
    bpy.utils.register_class(PIPELINE_OT_WizardStep4)
    bpy.utils.register_class(PIPELINE_OT_RebakeThumbnails)


def unregister():
    bpy.utils.unregister_class(PIPELINE_OT_RebakeThumbnails)
    bpy.utils.unregister_class(PIPELINE_OT_WizardStep4)
