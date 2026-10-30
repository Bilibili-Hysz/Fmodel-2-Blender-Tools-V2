# FModel_Tools/Fmodel/Pipeline/steps/single_model_flow.py
# Single-model animation handoff — active Armature → editable wizard configuration.

import bpy
import logging
import os
from ....core.workflow import set_target


logger = logging.getLogger(__name__)


def resolve_animation_source(wizard, project_root=""):
    """Resolve the active animation folder.

    Folder is the only active source mode. The legacy project-root and source
    fallbacks are limited to old states that still advertise PROJECT (or
    pre-mode schemas); a current MANUAL state with an empty folder resolves to
    an empty path instead of silently reusing stale project data.
    """
    has_manual_field = hasattr(wizard, "manual_source_dir")
    manual = getattr(wizard, "manual_source_dir", "")
    stripped = manual.strip() if manual else ""
    if stripped:
        return stripped

    mode = str(getattr(wizard, "source_mode", "") or "").upper()
    if mode == "MANUAL" and has_manual_field:
        return ""

    # Migration-only compatibility for older .blend files and callers.
    legacy_source = getattr(wizard, "source_dir", "")
    stripped = legacy_source.strip() if legacy_source else ""
    if stripped:
        return stripped
    legacy_root = project_root or getattr(wizard, "project_root", "")
    return legacy_root.strip() if legacy_root else ""


def _handoff_armature(wizard, armature, project_root="", advance_to_step=True):
    """Core handoff logic — pure function, returns operator return set.

    This function exists so that tests can verify handoff behavior without
    a real Blender operator context. The operator's execute() delegates here.

    Args:
        wizard: object with mutable WizardState fields.
        armature: active Blender Object (or None / non-armature).
        project_root: legacy project-root value accepted only for migration callers.
        advance_to_step: Preserve manual handoff navigation when true. Auto
            handoff uses false to leave the wizard on its current step.

    Returns:
        {'FINISHED'} on success, {'CANCELLED'} on invalid armature.
    """
    if armature is None or not hasattr(armature, "type") or armature.type != 'ARMATURE':
        return {'CANCELLED'}

    try:
        from .state import handoff_armature, clear_workflow_results
    except ModuleNotFoundError:
        def clear_workflow_results(state):
            for name, value in (
                ("scan_completed", False), ("scan_skeleton_count", 0),
                ("scan_anim_count", 0), ("scan_new_count", 0),
                ("scan_existing_count", 0), ("unmapped_skeletons", ""),
                ("import_completed", False), ("lib_completed", False),
            ):
                if hasattr(state, name):
                    setattr(state, name, value)
            mappings = getattr(state, "skeleton_mappings", None)
            if mappings is not None:
                mappings.clear()

        def handoff_armature(state, selected, root_path, automatic=False):
            metadata = selected.get("FModel_Skeleton", "")
            skeleton = metadata.strip() if isinstance(metadata, str) else ""
            library_name = skeleton or selected.name
            desired = {"skeleton_filter": skeleton, "import_target_skeleton": selected.name}
            if automatic:
                desired["flow_type"] = "FMODEL"
                if root_path.strip():
                    desired.update({
                        "source_mode": "MANUAL", "manual_source_dir": root_path.strip(),
                        "source_dir": root_path.strip(),
                        "output_dir": os.path.join(root_path.strip(), ".fmodel", "animation-libraries", library_name),
                        "library_name": library_name,
                    })
            changed = any(getattr(state, name, None) != value for name, value in desired.items())
            for name, value in desired.items():
                setattr(state, name, value)
            if changed:
                clear_workflow_results(state)
            return changed

    changed = handoff_armature(wizard, armature, project_root, automatic=not advance_to_step)
    if advance_to_step:
        # Retain existing manual handoff source resolution behavior.
        source_dir = resolve_animation_source(wizard, project_root)
        if wizard.source_dir != source_dir:
            wizard.source_dir = source_dir
            if not changed:
                clear_workflow_results(wizard)
        wizard.current_step = 2
    return {'FINISHED'}


def prepare_imported_armature_for_animation(context, armature):
    """Configure animation defaults for an imported Armature without navigation."""
    try:
        scene = getattr(context, "scene", None)
        wizard = getattr(scene, "pipeline_wizard", None)
        if wizard is None:
            logger.warning("Skipping animation handoff: Pipeline Wizard is unavailable")
            return {'CANCELLED'}

        result = _handoff_armature(
            wizard, armature, "", advance_to_step=False
        )
        if result == {'FINISHED'}:
            set_target(scene, armature)
        return result
    except Exception:
        logger.exception("Skipping non-fatal imported-model animation handoff")
        return {'CANCELLED'}


class FMODEL_OT_UseActiveArmatureForAnimation(bpy.types.Operator):
    """Set up the animation pipeline with the active armature's identity.

    Derives the source skeleton from the canonical identity or legacy metadata.
    Without either, leaves the source filter open for explicit mapping after
    scan. Writes configuration only; never invokes scan or import operators.
    """
    bl_idname = "fmodel.use_active_armature_for_animation"
    bl_label = "使用当前骨架进行动画管线"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        armature = context.active_object
        return armature is not None and armature.type == 'ARMATURE'

    def execute(self, context):
        armature = context.active_object
        if armature is None or armature.type != 'ARMATURE':
            self.report({'ERROR'}, "请选择用于动画的骨架")
            return {'CANCELLED'}

        state = context.scene.pipeline_wizard
        result = _handoff_armature(state, armature, "")
        set_target(context.scene, armature)
        return result


def register():
    bpy.utils.register_class(FMODEL_OT_UseActiveArmatureForAnimation)


def unregister():
    bpy.utils.unregister_class(FMODEL_OT_UseActiveArmatureForAnimation)
