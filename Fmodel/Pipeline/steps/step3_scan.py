# FModel_Tools/Fmodel/Pipeline/steps/step3_scan.py
# 向导步骤3: Folder 动画文件夹扫描

import bpy

from ....core import DictionaryManifest, PipelineContext, PipelineStage
from .runner import (
    _load_or_create_context, populate_skeleton_mappings, run_wizard_stage,
)


def _apply_scan_results(state, pipeline_context, active_object=None):
    if not pipeline_context or not pipeline_context.dictionary_manifest:
        raise ValueError("扫描失败：未生成字典清单")
    manifest = DictionaryManifest.from_dict(pipeline_context.dictionary_manifest)
    anim_total = len(manifest.get_all_animations())
    skeleton_total = len(manifest.skeletons)
    state.scan_skeleton_count = skeleton_total
    state.scan_anim_count = anim_total
    state.scan_new_count = anim_total
    state.scan_existing_count = 0
    populate_skeleton_mappings(state, pipeline_context, active_object)
    state.scan_completed = True
    return anim_total, skeleton_total


class PIPELINE_OT_WizardStep3(bpy.types.Operator):
    """向导步骤3: 扫描当前 Folder 中的 UEFormat 动画"""
    bl_idname = "pipeline.wizard_step3"
    bl_label = "扫描字典"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        if not hasattr(context.scene, "pipeline_wizard"):
            return False
        state = context.scene.pipeline_wizard
        return bool(state.source_dir and state.output_dir)

    def execute(self, context):
        state = context.scene.pipeline_wizard
        try:
            ok_stage1 = run_wizard_stage(
                context, state, PipelineStage.DICTIONARY_ACQUISITION.value
            )
            if not ok_stage1:
                self.report({'ERROR'}, "扫描失败：字典阶段未通过")
                return {'CANCELLED'}

            ok_stage2 = run_wizard_stage(
                context, state, PipelineStage.FORMAT_CONVERSION.value
            )
            if not ok_stage2:
                self.report({'ERROR'}, "扫描失败：格式中转阶段未通过")
                return {'CANCELLED'}

            pipeline_context = PipelineContext.load_from_blender_text(
                output_dir=state.output_dir
            )
            anim_total, skeleton_total = _apply_scan_results(
                state, pipeline_context, context.active_object
            )
        except (OSError, TypeError, ValueError) as error:
            self.report({'ERROR'}, str(error) or "扫描失败")
            return {'CANCELLED'}

        self.report({'INFO'}, f"扫描完成: {anim_total} 个动画, {skeleton_total} 个骨架")
        return {'FINISHED'}


def register():
    bpy.utils.register_class(PIPELINE_OT_WizardStep3)


def unregister():
    bpy.utils.unregister_class(PIPELINE_OT_WizardStep3)
