# FModel_Tools/Fmodel/Pipeline/steps/step3_scan.py
# 步骤3: 扫描字典

import json

import bpy

from ....core import DictionaryManifest, PipelineContext, PipelineStage
from ....core.project_index import ProjectIndex
from .runner import (
    _build_config_from_wizard, _indexed_selection_from_state,
    _load_or_create_context, _project_default_indexed_selection,
    _validate_auto_project_selection, populate_skeleton_mappings, run_wizard_stage,
)


class PIPELINE_OT_WizardStep3(bpy.types.Operator):
    """向导步骤3: 扫描字典"""
    bl_idname = "pipeline.wizard_step3"
    bl_label = "扫描字典"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not hasattr(context.scene, "pipeline_wizard"):
            return False
        state = context.scene.pipeline_wizard
        return state.source_dir and state.output_dir

    def execute(self, context):
        state = context.scene.pipeline_wizard
        selection_mode = getattr(state, "indexed_selection_mode", "EXPLICIT")
        try:
            indexed_selection = _indexed_selection_from_state(state)
            if selection_mode == "AUTO_PROJECT":
                indexed_selection = _validate_auto_project_selection(
                    state, getattr(context.scene, "fmodel_project", None)
                )
        except ValueError:
            message = (
                "Project Settings Scan is required before preparing this import plan"
                if selection_mode == "AUTO_PROJECT" else "索引选择不完整或格式错误"
            )
            self.report({'ERROR'}, f"扫描失败：{message}")
            return {'CANCELLED'}
        project_default = False
        if not indexed_selection:
            try:
                indexed_selection = _project_default_indexed_selection(
                    state, getattr(context.scene, "fmodel_project", None)
                )
            except ValueError as error:
                self.report({'ERROR'}, f"扫描失败：{error}")
                return {'CANCELLED'}
            project_default = bool(indexed_selection)
        if indexed_selection:
            indexed_root, indexed_skeleton, indexed_paths = indexed_selection
            try:
                index = ProjectIndex(indexed_root)
                index_ready = index.status()["state"] == "ready"
            except (OSError, ValueError):
                index_ready = False
            if not index_ready:
                message = (
                    "Project Settings Scan is required before preparing this import plan"
                    if project_default or selection_mode == "AUTO_PROJECT"
                    else "项目索引未就绪"
                )
                self.report({'ERROR'}, f"扫描失败：{message}")
                return {'CANCELLED'}
            try:
                manifest_data = index.dictionary_manifest(indexed_skeleton, indexed_paths)
            except (OSError, TypeError, ValueError):
                message = (
                    "Project Settings Scan is required before preparing this import plan"
                    if project_default or selection_mode == "AUTO_PROJECT"
                    else "索引选择已过期或不匹配"
                )
                self.report({'ERROR'}, f"扫描失败：{message}")
                return {'CANCELLED'}
            if project_default:
                # Persist the automatic selection so Step 4 revalidates it.
                state.indexed_project_root = indexed_root
                state.indexed_skeleton = indexed_skeleton
                state.indexed_selected_paths = json.dumps(indexed_paths)
                state.indexed_selection_mode = "AUTO_PROJECT"
            config = _build_config_from_wizard(state, context)
            config.update({
                "indexed_project_root": indexed_root,
                "indexed_skeleton": indexed_skeleton,
                "indexed_selected_paths": indexed_paths,
            })
            pipeline_context = _load_or_create_context(config)
            pipeline_context.dictionary_manifest = manifest_data
            pipeline_context.persist_checkpoint()
        else:
            ok_stage1 = run_wizard_stage(context, state, PipelineStage.DICTIONARY_ACQUISITION.value)
            if not ok_stage1:
                self.report({'ERROR'}, "扫描失败：字典阶段未通过")
                return {'CANCELLED'}

            ok_stage2 = run_wizard_stage(context, state, PipelineStage.FORMAT_CONVERSION.value)
            if not ok_stage2:
                self.report({'ERROR'}, "扫描失败：格式中转阶段未通过")
                return {'CANCELLED'}

            pipeline_context = PipelineContext.load_from_blender_text(
                output_dir=state.output_dir
            )
        if not pipeline_context or not pipeline_context.dictionary_manifest:
            self.report({'ERROR'}, "扫描失败：未生成字典清单")
            return {'CANCELLED'}

        manifest = DictionaryManifest.from_dict(pipeline_context.dictionary_manifest)
        anim_total = len(manifest.get_all_animations())
        skeleton_total = len(manifest.skeletons)

        state.scan_skeleton_count = skeleton_total
        state.scan_anim_count = anim_total
        state.scan_new_count = anim_total
        state.scan_existing_count = 0
        populate_skeleton_mappings(state, pipeline_context, context.active_object)
        state.scan_completed = True

        if state.flow_type == 'FMODEL':
            self.report({'INFO'}, f"扫描完成: {anim_total} 个动画, {skeleton_total} 个骨架")
        else:
            self.report({'INFO'}, f"扫描完成: {anim_total} 个动画")

        return {'FINISHED'}


def register():
    bpy.utils.register_class(PIPELINE_OT_WizardStep3)


def unregister():
    bpy.utils.unregister_class(PIPELINE_OT_WizardStep3)
