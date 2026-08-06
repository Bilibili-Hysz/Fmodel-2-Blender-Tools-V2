import json
import os
import sqlite3
import sys

import bpy

from ..manager import PipelineManager
from ....core import (
    DictionaryManifest, ImportMode, MultiImportCheckpoint, PipelineConfig,
    PipelineContext, PipelineStage, SourceType, build_multi_import_plan,
    restore_checkpoint, run_pending_tasks, validate_generated_output_filenames,
    armature_identity, import_settings_fingerprint,
)
from ....core.project_index import ProjectIndex


_AUTO_PROJECT = "AUTO_PROJECT"
_EXPLICIT = "EXPLICIT"
_SCAN_INSTRUCTION = "Project Settings Scan is required before preparing this import plan"


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
    source_type = SourceType.FMODEL_UEANIM.value if state.flow_type == 'FMODEL' else SourceType.UNITY_ANIM.value
    import_mode = ImportMode.UEANIM_DIRECT.value if state.flow_type == 'FMODEL' else ImportMode.FBX_MATCH.value

    cfg = PipelineConfig(
        source_dir=state.source_dir,
        output_dir=state.output_dir,
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
    ).to_dict()

    handoff_target = getattr(state, "import_target_skeleton", "")
    obj = context.active_object
    if handoff_target:
        cfg["target_armature"] = handoff_target
    elif obj and obj.type == 'ARMATURE':
        cfg["target_armature"] = obj.name

    indexed_selection = _indexed_selection_from_state(state)
    indexed_mode = _indexed_selection_mode(state)
    if indexed_mode == _AUTO_PROJECT:
        indexed_selection = _validate_auto_project_selection(
            state, getattr(getattr(context, "scene", None), "fmodel_project", None)
        )
    if indexed_selection:
        indexed_root, indexed_skeleton, indexed_paths = indexed_selection
        cfg.update({
            "indexed_project_root": indexed_root,
            "indexed_skeleton": indexed_skeleton,
            "indexed_selected_paths": indexed_paths,
            "indexed_selection_mode": indexed_mode,
        })

    return cfg


def _indexed_selection_from_state(state):
    """Return a complete indexed selection or reject incomplete indexed intent."""
    mode = _indexed_selection_mode(state)
    if mode == "NONE":
        return None
    if mode == _AUTO_PROJECT and getattr(state, "source_mode", None) != "PROJECT":
        _clear_auto_project_selection(state)
        return None
    root = getattr(state, "indexed_project_root", "")
    skeleton = getattr(state, "indexed_skeleton", "")
    paths = getattr(state, "indexed_selected_paths", "[]")
    if root == "" and skeleton == "" and paths == "[]":
        return None
    if not isinstance(root, str) or not root:
        raise ValueError("indexed project root is required")
    if not isinstance(skeleton, str) or not skeleton:
        raise ValueError("indexed skeleton is required")
    if isinstance(paths, str):
        try:
            paths = json.loads(paths)
        except json.JSONDecodeError as error:
            raise ValueError("indexed selected paths must be valid JSON") from error
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) and path for path in paths):
        raise ValueError("indexed selected paths must be a non-empty list")
    return root, skeleton, paths


def _indexed_selection_mode(state):
    mode = getattr(state, "indexed_selection_mode", None)
    if mode in ("NONE", _AUTO_PROJECT, _EXPLICIT):
        return mode
    # Selections saved before provenance existed were explicit selector input.
    return _EXPLICIT


def _clear_auto_project_selection(state):
    state.indexed_selection_mode = "NONE"
    state.indexed_project_root = ""
    state.indexed_skeleton = ""
    state.indexed_selected_paths = "[]"


def _validate_auto_project_selection(state, project_settings):
    """Confirm persisted Project Default input still matches current project state."""
    persisted = _indexed_selection_from_state(state)
    if not persisted:
        return None
    root, skeleton, _paths = persisted
    current = _project_default_indexed_selection(state, project_settings)
    if not current or current[0] != root or current[1] != skeleton:
        raise ValueError(_SCAN_INSTRUCTION)
    # Project Default means every current indexed animation, so a successful
    # Project Settings Scan can replace paths removed from the prior plan.
    return current


def _project_default_indexed_selection(state, project_settings):
    """Return all ready indexed animations for the Project Default skeleton."""
    if getattr(state, "source_mode", None) != "PROJECT":
        return None

    root = getattr(project_settings, "root_path", "")
    source = getattr(state, "source_dir", "")
    skeleton = getattr(state, "skeleton_filter", "")
    root = root.strip() if isinstance(root, str) else ""
    source = source.strip() if isinstance(source, str) else ""
    skeleton = skeleton.strip() if isinstance(skeleton, str) else ""
    scan_instruction = _SCAN_INSTRUCTION
    if (
        not root
        or not source
        or os.path.normcase(os.path.abspath(source)) != os.path.normcase(os.path.abspath(root))
        or getattr(project_settings, "index_state", "") != "ready"
        or not skeleton
    ):
        raise ValueError(scan_instruction)

    try:
        index = ProjectIndex(root)
        if index.status()["state"] != "ready":
            raise ValueError(scan_instruction)
        paths = index.animations_for_skeleton(skeleton, raise_on_error=True)
    except (OSError, sqlite3.Error, ValueError) as error:
        if str(error) == scan_instruction:
            raise
        raise ValueError(scan_instruction) from error
    if not paths:
        raise ValueError(
            f"Project Settings Scan found no animations for skeleton: {skeleton}"
        )
    return root, skeleton, paths


def _indexed_manifest_from_config(config):
    root = config.get("indexed_project_root")
    if not root:
        return None
    return ProjectIndex(root).dictionary_manifest(
        config.get("indexed_skeleton"), config.get("indexed_selected_paths")
    )


def _mapping_dict(state) -> dict:
    return {
        item.source_skeleton: item.target_armature.name
        for item in state.skeleton_mappings if item.target_armature
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
    try:
        indexed_manifest = _indexed_manifest_from_config(cfg)
    except (OSError, sqlite3.Error, ValueError) as error:
        if cfg.get("indexed_selection_mode") == _AUTO_PROJECT:
            raise ValueError(_SCAN_INSTRUCTION) from error
        raise
    manager = PipelineManager()
    pipeline_context = _load_or_create_context(cfg)
    if indexed_manifest is not None:
        pipeline_context.dictionary_manifest = indexed_manifest
    checkpoint, unmapped = build_plan_from_wizard(
        state, pipeline_context, context.active_object
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
        if not manager.run_stage(pipeline_context, PipelineStage.ASSET_IMPORT.value):
            return False
        pipeline_context.persist_checkpoint()
        if not manager.run_stage(pipeline_context, PipelineStage.LIBRARY_BUILDING.value):
            return False
        if prepare_task and not prepare_task(task, len(checkpoint.plan.tasks)):
            return False
        return True

    persist(checkpoint)
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
