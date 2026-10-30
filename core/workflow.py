"""Shared work context. No UI drawing, operators, or file I/O in this module.

The workspace pointer owns the target; legacy fields are migration inputs and
compatibility mirrors. A cleared/deleted target must never fall back to selection.
"""


def valid_armature(obj, scene=None):
    if obj is None or getattr(obj, "type", None) != "ARMATURE":
        return False
    objects = getattr(scene, "objects", None)
    return objects is None or objects.get(obj.name) == obj


def resolve_target(context):
    scene = getattr(context, "scene", None)
    workspace = getattr(scene, "fmodel_workflow", None)
    if workspace is not None:
        target = workspace.target
        return target if valid_armature(target, scene) else None
    # Compatibility for callers that have not registered the workspace schema.
    pose = getattr(scene, "fmodel_pose_library", None)
    target = getattr(pose, "target", None)
    if valid_armature(target, scene):
        return target
    wizard = getattr(scene, "pipeline_wizard", None)
    objects = getattr(scene, "objects", None)
    name = getattr(wizard, "import_target_skeleton", "")
    target = objects.get(name) if objects is not None and name else None
    if valid_armature(target, scene):
        return target
    target = getattr(context, "active_object", None)
    return target if valid_armature(target, scene) else None


def set_target(scene, target):
    if target is not None and not valid_armature(target, scene):
        raise ValueError("目标必须是当前场景中的骨架")
    workspace = getattr(scene, "fmodel_workflow", None)
    if workspace is not None:
        workspace.initialized = True
        if workspace.target != target:
            workspace.target = target
    synchronize_target(scene, target)


def synchronize_target(scene, target):
    """Keep legacy consumers coherent without discarding valid source scans."""
    wizard = getattr(scene, "pipeline_wizard", None)
    if wizard is not None:
        name = target.name if target else ""
        if wizard.import_target_skeleton != name:
            wizard.import_target_skeleton = name
            wizard.import_completed = False
            wizard.lib_completed = False
        mappings = getattr(wizard, "skeleton_mappings", ())
        if len(mappings) == 1 and mappings[0].target_armature != target:
            mappings[0].target_armature = target
            mappings[0].status = "pending"
    pose = getattr(scene, "fmodel_pose_library", None)
    if pose is not None and pose.target != target:
        pose.target = target


def migrate_scene(scene):
    """One-time migration of saved scenes; never guess from current selection."""
    workspace = getattr(scene, "fmodel_workflow", None)
    if workspace is None or workspace.initialized:
        return
    target = workspace.target
    wizard = getattr(scene, "pipeline_wizard", None)
    name = getattr(wizard, "import_target_skeleton", "")
    if not valid_armature(target, scene) and name:
        target = scene.objects.get(name)
    if not valid_armature(target, scene):
        target = getattr(getattr(scene, "fmodel_pose_library", None), "target", None)
    set_target(scene, target if valid_armature(target, scene) else None)


def selected_library(scene):
    assets = getattr(scene, "fmodel_assets", None)
    if assets is not None and 0 <= assets.active_lib_index < len(assets.project_libs):
        return assets.project_libs[assets.active_lib_index]
    return None
