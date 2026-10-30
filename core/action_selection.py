"""Resolve imported actions without reusing same-named data from another task."""

import os


def _normalized_path(path):
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _property(action, name):
    try:
        return action.get(name, "")
    except AttributeError:
        return ""


def _matches_identity(action, source_file, source_ref, staged_blend):
    if source_file and _normalized_path(_property(action, "Pipeline_Source")) != _normalized_path(source_file):
        return False
    if source_ref and _property(action, "Pipeline_SourceRef") != source_ref:
        return False
    if staged_blend and _normalized_path(_property(action, "Pipeline_StagedBlend")) != _normalized_path(staged_blend):
        return False
    return bool(source_file or source_ref or staged_blend)


def _library_path(action):
    library = getattr(action, "library", None)
    return _normalized_path(getattr(library, "filepath", ""))


def _source_bundle_path(action):
    return _normalized_path(_property(action, "Pipeline_SourceBundle"))


def _reject_loaded_action(bpy, action, message):
    try:
        bpy.data.actions.remove(action)
    except Exception:
        pass
    return None, message


def resolve_imported_action(bpy, imported_action, staged_validator=None):
    """Return ``(action, error)`` using source/staged provenance before names."""
    action_name = imported_action.get("action_name", "")
    source_file = imported_action.get("source_file", "")
    source_bundle = imported_action.get("source_bundle", "")
    source_ref = imported_action.get("source_manifest_ref", "")
    staged_blend = imported_action.get("staged_blend", "")
    same_name = [
        action for action in bpy.data.actions
        if action.name == action_name
        or _property(action, "Pipeline_RequestedActionName") == action_name
        or _property(action, "Pipeline_SourceBundleActionName") == action_name
    ]

    # Linked actions cannot safely receive custom provenance properties. Their
    # immutable library filepath plus the verified sidecar catalog reference is
    # the identity supplied by the importer.
    if source_bundle:
        local_bundles = [
            action for action in same_name
            if _source_bundle_path(action) == _normalized_path(source_bundle)
        ]
        matching_bundles = local_bundles or [
            action for action in same_name
            if _library_path(action) == _normalized_path(source_bundle)
        ]
        if len(matching_bundles) == 1:
            return matching_bundles[0], ""
        if len(matching_bundles) > 1:
            return None, f"库包动作不唯一: {action_name} ({len(matching_bundles)} candidates)"
        return None, f"未找到库包动作: {action_name}"

    matching = [
        action for action in same_name
        if _matches_identity(action, source_file, source_ref, staged_blend)
    ]
    owned_matching = [action for action in matching if _property(action, "Pipeline_Owned")]
    if owned_matching:
        matching = owned_matching
    if len(matching) == 1:
        return matching[0], ""
    if len(matching) > 1:
        return None, f"动作来源不唯一: {action_name} ({len(matching)} candidates)"

    if staged_blend:
        if staged_validator is not None:
            try:
                staged_blend = staged_validator(staged_blend)
            except (OSError, ValueError) as exc:
                return None, f"暂存动作包路径无效: {exc}"
        if not os.path.exists(staged_blend):
            return None, f"暂存动作包不存在: {staged_blend}"
        try:
            with bpy.data.libraries.load(staged_blend, link=False) as (data_from, data_to):
                available = [name for name in data_from.actions if name == action_name]
                if len(available) != 1:
                    return None, f"暂存动作不唯一: {action_name} ({len(available)} candidates)"
                data_to.actions = [action_name]
        except Exception as exc:
            return None, f"加载暂存动作失败 {action_name}: {exc}"

        loaded = [action for action in data_to.actions if action is not None]
        if len(loaded) != 1:
            return None, f"暂存动作加载结果不唯一: {action_name} ({len(loaded)} candidates)"
        action = loaded[0]
        if source_file and _normalized_path(_property(action, "Pipeline_Source")) != _normalized_path(source_file):
            return _reject_loaded_action(bpy, action, f"暂存动作来源不匹配: {action_name}")
        if source_ref and _property(action, "Pipeline_SourceRef") != source_ref:
            return _reject_loaded_action(bpy, action, f"暂存动作任务不匹配: {action_name}")
        try:
            action["Pipeline_StagedBlend"] = _normalized_path(staged_blend)
        except Exception as exc:
            return _reject_loaded_action(bpy, action, f"暂存动作标记失败 {action_name}: {exc}")
        return action, ""

    if source_file or source_ref:
        return None, f"未找到来源匹配的动作: {action_name}"
    if len(same_name) == 1:
        return same_name[0], ""
    if len(same_name) > 1:
        return None, f"动作名称不唯一: {action_name} ({len(same_name)} candidates)"
    return None, f"未找到动作: {action_name}"
