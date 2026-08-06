# FModel_Tools/Fmodel/Pipeline/steps/single_model_flow.py
# Single-model animation handoff — active Armature → editable wizard configuration.

import bpy
import logging
import os


logger = logging.getLogger(__name__)


def resolve_animation_source(wizard, project_root=""):
    """Resolve the animation source directory based on source mode.

    Args:
        wizard: WizardState instance (or duck-typed equivalent) with
            source_mode and manual_source_dir fields.
        project_root: fmodel_project.root_path string (may be empty).

    Returns:
        Resolved source directory string, or empty string when unresolvable.
    """
    if wizard.source_mode == 'MANUAL':
        manual = getattr(wizard, "manual_source_dir", "")
        stripped = manual.strip() if manual else ""
        if stripped:
            return stripped
        return ""
    if project_root and project_root.strip():
        return project_root.strip()
    return ""


def _handoff_armature(wizard, armature, project_root="", advance_to_step=True):
    """Core handoff logic — pure function, returns operator return set.

    This function exists so that tests can verify handoff behavior without
    a real Blender operator context. The operator's execute() delegates here.

    Args:
        wizard: object with mutable WizardState fields.
        armature: active Blender Object (or None / non-armature).
        project_root: fmodel_project.root_path value.
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
            skeleton = str(selected.get("FModel_Skeleton", selected.name) or selected.name)
            desired = {"skeleton_filter": skeleton, "import_target_skeleton": selected.name}
            if automatic:
                desired["flow_type"] = "FMODEL"
                if root_path.strip():
                    desired.update({
                        "source_mode": "PROJECT", "source_dir": root_path.strip(),
                        "output_dir": os.path.join(root_path.strip(), ".fmodel", "animation-libraries", skeleton),
                        "library_name": skeleton,
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

        project = getattr(scene, "fmodel_project", None)
        project_root = getattr(project, "root_path", "") or ""
        return _handoff_armature(
            wizard, armature, project_root, advance_to_step=False
        )
    except Exception:
        logger.exception("Skipping non-fatal imported-model animation handoff")
        return {'CANCELLED'}


class FMODEL_OT_UseActiveArmatureForAnimation(bpy.types.Operator):
    """Set up the animation pipeline with the active armature's identity.

    Derives the source skeleton from FModel_Skeleton custom property when
    present, otherwise from the armature object name. Writes only editable
    wizard configuration — never invokes scan or import operators.
    """
    bl_idname = "fmodel.use_active_armature_for_animation"
    bl_label = "Use Active Armature for Animation"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        armature = context.active_object
        return armature is not None and armature.type == 'ARMATURE'

    def execute(self, context):
        armature = context.active_object
        if armature is None or armature.type != 'ARMATURE':
            self.report({'ERROR'}, "Select an Armature for animation")
            return {'CANCELLED'}

        project_root = ""
        fmodel_project = getattr(context.scene, "fmodel_project", None)
        if fmodel_project is not None:
            project_root = getattr(fmodel_project, "root_path", "") or ""

        state = context.scene.pipeline_wizard
        return _handoff_armature(state, armature, project_root)


def register():
    bpy.utils.register_class(FMODEL_OT_UseActiveArmatureForAnimation)


def unregister():
    bpy.utils.unregister_class(FMODEL_OT_UseActiveArmatureForAnimation)
