# FModel_Tools/Fmodel/Pipeline/steps/step3_scan.py
# 向导步骤3: Folder 动画文件夹扫描

import bpy
from ....core.workflow import resolve_target

from ....core import (
    DictionaryManifest, PipelineContext, PipelineStage,
    library_index_path, load_complete_v2_manifest, library_scan_counts,
)
from .runner import (
    _load_or_create_context, populate_skeleton_mappings, run_wizard_stage,
)


def _scan_stage_error(output_dir, stage, fallback):
    pipeline_context = (
        PipelineContext.load_from_checkpoint(output_dir)
        or PipelineContext.load_from_blender_text(output_dir=output_dir)
    )
    if not pipeline_context or pipeline_context.get_stage_status(stage) != "failed":
        return fallback
    logs = pipeline_context.logs
    generic = f"阶段 {stage} 失败"
    failures = [
        index for index, entry in enumerate(logs)
        if isinstance(entry, dict) and entry.get("stage") == stage
        and entry.get("level") == "error" and entry.get("message") == generic
    ]
    if not failures:
        return fallback
    last_failure = failures[-1]
    # Validation logs after the status update; execution logs before it.
    for entry in reversed(logs[last_failure + 1:]):
        if isinstance(entry, dict) and entry.get("stage") == stage and entry.get("level") == "error":
            return str(entry.get("message") or fallback)
    for entry in reversed(logs[(failures[-2] + 1 if len(failures) > 1 else 0):last_failure]):
        if not isinstance(entry, dict) or entry.get("stage") != stage:
            continue
        if entry.get("message") == f"开始执行阶段: {stage}":
            break
        if entry.get("level") in {"error", "warning"} and entry.get("message"):
            return str(entry["message"])
    return fallback


def _apply_scan_results(state, pipeline_context, active_object=None):
    if not pipeline_context or not pipeline_context.dictionary_manifest:
        raise ValueError("扫描失败：未生成字典清单")
    manifest = DictionaryManifest.from_dict(pipeline_context.dictionary_manifest)
    anim_total = len(manifest.get_all_animations())
    skeleton_total = len(manifest.skeletons)
    state.scan_skeleton_count = skeleton_total
    state.scan_anim_count = anim_total
    state.scan_pose_count = len(manifest.pose_resources)
    output_dir = bpy.path.abspath(state.output_dir)
    library = load_complete_v2_manifest(library_index_path(output_dir, state.library_name), output_dir)
    counts = library_scan_counts(manifest, library)
    state.scan_new_count = counts["new"]
    state.scan_existing_count = counts["unchanged"]
    state.scan_changed_count = counts["changed"]
    state.scan_unverified_count = counts["unverified"]
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
        state.scan_completed = False
        state.import_completed = False
        state.lib_completed = False
        state.current_step = 3
        try:
            ok_stage1 = run_wizard_stage(
                context, state, PipelineStage.DICTIONARY_ACQUISITION.value
            )
            if not ok_stage1:
                self.report({'ERROR'}, "扫描失败：" + _scan_stage_error(
                    bpy.path.abspath(state.output_dir),
                    PipelineStage.DICTIONARY_ACQUISITION.value, "字典阶段未通过"
                ))
                return {'CANCELLED'}

            ok_stage2 = run_wizard_stage(
                context, state, PipelineStage.FORMAT_CONVERSION.value
            )
            if not ok_stage2:
                self.report({'ERROR'}, "扫描失败：" + _scan_stage_error(
                    bpy.path.abspath(state.output_dir),
                    PipelineStage.FORMAT_CONVERSION.value, "格式中转阶段未通过"
                ))
                return {'CANCELLED'}

            output_dir = bpy.path.abspath(state.output_dir)
            pipeline_context = (
                PipelineContext.load_from_checkpoint(output_dir)
                or PipelineContext.load_from_blender_text(output_dir=output_dir)
            )
            anim_total, skeleton_total = _apply_scan_results(
                state, pipeline_context, resolve_target(context)
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
