import os
import sys

import bpy
from ....core.workflow import resolve_target, synchronize_target

from ..manager import PipelineManager
from ....core import (
    DictionaryManifest, ImportMode, MultiImportCheckpoint, PipelineConfig,
    PipelineContext, PipelineStage, SourceType, build_multi_import_plan,
    restore_checkpoint, run_pending_tasks, validate_generated_output_filenames,
    armature_identity, import_settings_fingerprint, skeleton_provenance_from_owner,
)


def _load_or_create_context(config_dict: dict) -> PipelineContext:
    output_dir = config_dict.get("output_dir", "")
    context_data = PipelineContext.load_from_checkpoint(output_dir)
    if context_data is None:
        context_data = PipelineContext.load_from_blender_text(output_dir=output_dir)
    if context_data:
        context_data.apply_pipeline_config(config_dict)
        return context_data
    return PipelineContext(config=config_dict)


def _build_config_from_wizard(state, context) -> dict:
    scene = getattr(context, "scene", None)
    if hasattr(scene, "fmodel_workflow"):
        synchronize_target(scene, resolve_target(context))
    source_type = SourceType.FMODEL_UEANIM.value
    import_mode = ImportMode.UEANIM_DIRECT.value

    cfg = PipelineConfig(
        source_dir=bpy.path.abspath(state.source_dir) if state.source_dir else "",
        output_dir=bpy.path.abspath(state.output_dir) if state.output_dir else "",
        library_name=state.library_name,
        skeleton_filter=state.skeleton_filter,
        source_type=source_type,
        import_mode=import_mode,
        import_scale=state.import_scale,
        rotation_only=state.rotation_only,
        import_curves=state.import_curves,
        generate_thumbnails=state.lib_generate_thumbnails,
        thumb_resolution=state.lib_thumb_resolution,
        thumb_quality=state.lib_thumb_quality,
        thumb_isolate=state.lib_thumb_isolate,
        chunk_size=state.lib_chunk_size,
        force_rebuild=state.lib_force_rebuild,
        import_batch_size=state.import_batch_size,
        max_import_cycles=state.import_max_cycles,
        large_import_threshold=state.import_large_threshold,
        max_consecutive_failures=state.import_max_consecutive_failures,
        import_resume_enabled=state.import_resume_enabled,
        clean_after_import=bool(getattr(state, "clean_after_import", False)),
        clean_threshold=float(getattr(state, "clean_threshold", 0.001)),
        clean_flat_channels=bool(getattr(state, "clean_flat_channels", True)),
        skeleton_compatibility_mode=getattr(
            state, "skeleton_compatibility_mode", "ueformat"
        ),
    ).to_dict()

    handoff_target = getattr(state, "import_target_skeleton", "")
    obj = resolve_target(context)
    if hasattr(getattr(context, "scene", None), "fmodel_workflow"):
        cfg["target_armature"] = obj.name if obj else ""
        return cfg
    if handoff_target:
        cfg["target_armature"] = handoff_target
    elif obj and obj.type == 'ARMATURE':
        cfg["target_armature"] = obj.name

    return cfg


def _mapping_dict(state) -> dict:
    return {
        item.source_skeleton: item.target_armature.name
        for item in state.skeleton_mappings if item.target_armature
    }


def _mapping_compatibility_dict(state) -> dict:
    default_mode = getattr(state, "skeleton_compatibility_mode", "ueformat")
    return {
        item.source_skeleton: getattr(item, "compatibility_mode", default_mode)
        for item in state.skeleton_mappings
        if item.target_armature
    }


def populate_skeleton_mappings(state, pipeline_context, active_object=None):
    """Populate scan mappings, restoring explicit selections from the saved plan."""
    manifest = DictionaryManifest.from_dict(pipeline_context.dictionary_manifest or {})
    restored = {}
    checkpoint = MultiImportCheckpoint.from_dict(pipeline_context.multi_import_checkpoint or {})
    saved_tasks = checkpoint.plan.tasks
    if not saved_tasks and pipeline_context.multi_import_plan:
        saved_tasks = MultiImportCheckpoint.from_dict({
            "plan": pipeline_context.multi_import_plan
        }).plan.tasks
    for task in saved_tasks:
        restored[task.source_skeleton] = task.target_armature
    restored_status = {task.source_skeleton: task.status for task in saved_tasks}

    active_armature = (
        active_object.name
        if active_object and active_object.type == 'ARMATURE' else ""
    )
    state.skeleton_mappings.clear()
    for source_skeleton, skeleton in manifest.skeletons.items():
        item = state.skeleton_mappings.add()
        item.source_skeleton = source_skeleton
        item.source_count = len(skeleton.collect_all_animations())
        item.status = restored_status.get(source_skeleton, "pending")
        target_name = restored.get(source_skeleton, "")
        if len(manifest.skeletons) == 1 and not target_name:
            target_name = active_armature
        item.target_armature = bpy.data.objects.get(target_name) if target_name else None
        restored_task = next(
            (task for task in saved_tasks if task.source_skeleton == source_skeleton),
            None,
        )
        item.compatibility_mode = getattr(
            restored_task, "compatibility_mode",
            getattr(state, "skeleton_compatibility_mode", "ueformat"),
        )
    state.unmapped_skeletons = ", ".join(
        item.source_skeleton for item in state.skeleton_mappings
        if not item.target_armature
    )


def build_plan_from_wizard(state, pipeline_context, active_object=None):
    available = {obj.name for obj in bpy.data.objects if obj.type == 'ARMATURE'}
    active_armature = (
        active_object.name
        if active_object and active_object.type == 'ARMATURE' else ""
    )
    manifest = pipeline_context.dictionary_manifest or {}
    skeletons = manifest.get("skeletons", {}) if isinstance(manifest, dict) else {}
    if len(skeletons) == 1:
        handoff_target = getattr(state, "import_target_skeleton", "")
        if handoff_target:
            if handoff_target not in available:
                raise ValueError(f"指定的目标骨架不可用: {handoff_target}")
            mappings = {next(iter(skeletons)): handoff_target}
        elif not active_armature or active_armature not in available:
            raise ValueError("当前活动对象不是可用的目标骨架")
        else:
            # Legacy one-skeleton imports bind to the current active armature.
            mappings = {}
    else:
        mappings = _mapping_dict(state)
    plan, unmapped = build_multi_import_plan(
        manifest,
        mappings,
        default_target_armature=active_armature,
        available_target_armatures=available,
        require_complete=len(skeletons) > 1,
        import_fingerprint=import_settings_fingerprint(pipeline_context.config),
        target_armature_identities={
            obj.name: armature_identity(obj)
            for obj in bpy.data.objects if obj.type == 'ARMATURE'
        },
        target_armature_provenances={
            obj.name: skeleton_provenance_from_owner(obj).to_dict()
            for obj in bpy.data.objects if obj.type == 'ARMATURE'
        },
        skeleton_compatibility_modes=(
            _mapping_compatibility_dict(state) if len(skeletons) > 1 else {}
        ),
        default_compatibility_mode=getattr(
            state, "skeleton_compatibility_mode", "ueformat"
        ),
    )
    validate_generated_output_filenames(
        state.library_name,
        (task.source_skeleton for task in plan.tasks),
        include_models=bool(getattr(state, "lib_save_model", False)),
    )
    checkpoint = restore_checkpoint(plan, pipeline_context.multi_import_checkpoint)
    pipeline_context.multi_import_plan = plan.to_dict()
    pipeline_context.multi_import_checkpoint = checkpoint.to_dict()
    state.unmapped_skeletons = ", ".join(unmapped)
    return checkpoint, unmapped


def run_wizard_import_plan(
    context, state, report_unmapped=None, prepare_task=None
) -> tuple[bool, list[str]]:
    """Run import and library construction independently for each mapped task."""
    cfg = _build_config_from_wizard(state, context)
    manager = PipelineManager()
    pipeline_context = _load_or_create_context(cfg)
    checkpoint, unmapped = build_plan_from_wizard(
        state, pipeline_context, resolve_target(context)
    )
    if unmapped and report_unmapped:
        report_unmapped(unmapped)
    if unmapped:
        raise ValueError(f"多骨架导入计划缺少映射: {', '.join(unmapped)}")
    if any(task.status != "completed" for task in checkpoint.plan.tasks):
        pipeline_context.set_stage_status(
            PipelineStage.FINALIZATION.value, "pending"
        )

    def persist(value):
        pipeline_context.multi_import_plan = value.plan.to_dict()
        pipeline_context.multi_import_checkpoint = value.to_dict()
        pipeline_context.persist_checkpoint()

    def process(task):
        pipeline_context.set_config("target_armature", task.target_armature)
        pipeline_context.set_config("source_skeleton", task.source_skeleton)
        pipeline_context.set_config("source_subset", list(task.source_subset))
        pipeline_context.set_config(
            "skeleton_compatibility_mode", task.compatibility_mode
        )
        if not manager.run_stage(pipeline_context, PipelineStage.ASSET_IMPORT.value):
            return False
        pipeline_context.persist_checkpoint()
        if not manager.run_stage(pipeline_context, PipelineStage.LIBRARY_BUILDING.value):
            return False
        if prepare_task and not prepare_task(task, len(checkpoint.plan.tasks)):
            return False
        return True

    persist(checkpoint)
    initial_compatibility_mode = pipeline_context.get_config(
        "skeleton_compatibility_mode", "ueformat"
    )
    try:
        success = bool(checkpoint.plan.tasks) and run_pending_tasks(
            checkpoint, process, persist
        )
        task_status = {task.source_skeleton: task.status for task in checkpoint.plan.tasks}
        for item in state.skeleton_mappings:
            item.status = task_status.get(item.source_skeleton, "unmapped")
    finally:
        pipeline_context.set_config("source_subset", [])
        pipeline_context.set_config("source_skeleton", "")
        pipeline_context.set_config("target_armature", "")
        pipeline_context.set_config("skeleton_compatibility_mode", initial_compatibility_mode)
        task_error_active = sys.exc_info()[0] is not None
        try:
            pipeline_context.persist_checkpoint()
        except Exception:
            if not task_error_active:
                raise
    return success, unmapped


def run_wizard_stage(context, state, stage_name: str) -> bool:
    cfg = _build_config_from_wizard(state, context)
    manager = PipelineManager()
    pipeline_context = _load_or_create_context(cfg)
    success = manager.run_stage(pipeline_context, stage_name)
    pipeline_context.persist_checkpoint()
    return success
