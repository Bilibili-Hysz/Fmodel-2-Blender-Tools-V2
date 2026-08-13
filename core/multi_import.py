"""Pure-Python construction and execution helpers for multi-skeleton plans."""

from typing import Callable, Iterable, Mapping, Optional, Tuple

from .types import MultiImportCheckpoint, MultiImportPlan, MultiImportTask
from .library_files import validate_skeleton_output_names
from .resume import make_batch_identity, make_manifest_source
from .skeleton_compatibility import (
    SKELETON_COMPATIBILITY_STRICT,
    normalize_skeleton_compatibility_mode,
)


def _collect_source_files(node) -> list[str]:
    files = []
    if not isinstance(node, dict):
        return files
    animations = node.get("__animations__", {})
    if isinstance(animations, dict):
        for animation in animations.values():
            if isinstance(animation, dict) and animation.get("source_file"):
                files.append(animation["source_file"])
    for key, child in node.items():
        if key != "__animations__":
            files.extend(_collect_source_files(child))
    return files


def _collect_sources(node, skeleton: str, folder: str = "", start: int = 0):
    sources = []
    if not isinstance(node, dict):
        return sources
    animations = node.get("__animations__", {})
    if isinstance(animations, dict):
        for name, animation in animations.items():
            if isinstance(animation, dict) and animation.get("source_file"):
                sources.append(make_manifest_source(
                    start + len(sources), skeleton, folder, name,
                    animation["source_file"], animation.get("content_hash", ""),
                ))
    for key, child in node.items():
        if key == "__animations__":
            continue
        child_folder = "/".join(part for part in (folder, key) if part)
        sources.extend(_collect_sources(child, skeleton, child_folder, start + len(sources)))
    return sources


def build_multi_import_plan(
    manifest_data: dict,
    skeleton_mapping: Mapping[str, str],
    default_target_armature: str = "",
    available_target_armatures: Optional[Iterable[str]] = None,
    require_complete: bool = False,
    import_fingerprint: str = "",
    target_armature_identities: Optional[Mapping[str, dict]] = None,
    target_armature_provenances: Optional[Mapping[str, dict]] = None,
    skeleton_compatibility_modes: Optional[Mapping[str, str]] = None,
    default_compatibility_mode: str = SKELETON_COMPATIBILITY_STRICT,
) -> Tuple[MultiImportPlan, list[str]]:
    """Build tasks only for explicit mappings; single-source keeps active-armature behavior."""
    skeletons = (manifest_data or {}).get("skeletons", {})
    if not isinstance(skeletons, dict):
        skeletons = {}
    validate_skeleton_output_names(skeletons)
    default_compatibility_mode = normalize_skeleton_compatibility_mode(
        default_compatibility_mode
    )
    configured_compatibility_modes = skeleton_compatibility_modes or {}
    available = set(available_target_armatures) if available_target_armatures is not None else None
    single_source = len(skeletons) == 1
    tasks = []
    unmapped = []

    for source_skeleton, source_tree in skeletons.items():
        target_armature = skeleton_mapping.get(source_skeleton, "")
        if single_source and not target_armature:
            target_armature = default_target_armature
        if not target_armature:
            unmapped.append(source_skeleton)
            continue
        if available is not None and target_armature not in available:
            raise ValueError(f"Target armature does not exist or is not an armature: {target_armature}")
        compatibility_mode = normalize_skeleton_compatibility_mode(
            configured_compatibility_modes.get(source_skeleton, default_compatibility_mode)
        )
        source_identities = _collect_sources(source_tree, source_skeleton)
        target_identity = dict((target_armature_identities or {}).get(target_armature, {}))
        if not target_identity:
            target_identity = {"stable_id": target_armature, "structure_revision": "legacy"}
        target_skeleton_provenance = dict(
            (target_armature_provenances or {}).get(target_armature, {})
        )
        batch_target_identity = dict(target_identity)
        if target_skeleton_provenance:
            batch_target_identity["fmodel_skeleton_provenance"] = {
                "state": target_skeleton_provenance.get("state", "legacy"),
                "fingerprint": target_skeleton_provenance.get("fingerprint", ""),
            }
        batch_target_identity["skeleton_compatibility_mode"] = compatibility_mode
        tasks.append(MultiImportTask(
            source_skeleton=source_skeleton,
            target_armature=target_armature,
            compatibility_mode=compatibility_mode,
            source_subset=[item["path"] for item in source_identities],
            source_identities=source_identities,
            target_identity=target_identity,
            target_skeleton_provenance=target_skeleton_provenance,
            import_fingerprint=import_fingerprint,
            task_id=make_batch_identity(
                source_identities, import_fingerprint, batch_target_identity
            ),
        ))

    if require_complete and unmapped:
        raise ValueError(f"unmapped source skeletons: {', '.join(unmapped)}")
    return MultiImportPlan(tasks=tasks), unmapped


def _provenance_compatible(current: dict, persisted: dict) -> bool:
    """Allow legacy resume only when it cannot cross a strict identity boundary."""
    current = current if isinstance(current, dict) else {}
    persisted = persisted if isinstance(persisted, dict) else {}
    current_state = str(current.get("state", "legacy") or "legacy")
    persisted_state = str(persisted.get("state", "legacy") or "legacy")
    if current_state != persisted_state:
        return False
    if current_state == "strict":
        return bool(
            current.get("fingerprint")
            and current.get("fingerprint") == persisted.get("fingerprint")
        )
    if current_state == "invalid":
        return current.get("reason", "") == persisted.get("reason", "")
    return True


def restore_checkpoint(plan: MultiImportPlan, checkpoint_data: Optional[dict]) -> MultiImportCheckpoint:
    """Restore statuses only when persisted tasks still describe the same plan."""
    persisted = MultiImportCheckpoint.from_dict(checkpoint_data or {})
    persisted_tasks = {task.task_id: task for task in persisted.plan.tasks if task.task_id}
    legacy_tasks = {
        (task.source_skeleton, task.target_armature, tuple(task.source_subset)): task
        for task in persisted.plan.tasks if not task.task_id
    }
    for task in plan.tasks:
        old = persisted_tasks.get(task.task_id)
        if old is None and not persisted_tasks:
            old = legacy_tasks.get((task.source_skeleton, task.target_armature, tuple(task.source_subset)))
        if old and _provenance_compatible(
            task.target_skeleton_provenance,
            old.target_skeleton_provenance,
        ):
            task.status = old.status
            task.progress = old.progress
    first_pending = next(
        (index for index, task in enumerate(plan.tasks) if task.status != "completed"),
        len(plan.tasks),
    )
    return MultiImportCheckpoint(plan=plan, current_task_index=first_pending)


def run_pending_tasks(
    checkpoint: MultiImportCheckpoint,
    process_task: Callable[[MultiImportTask], bool],
    persist_checkpoint: Callable[[MultiImportCheckpoint], None],
) -> bool:
    """Execute pending tasks independently and persist after every attempted task."""
    for index, task in enumerate(checkpoint.plan.tasks):
        if task.status == "completed":
            continue
        checkpoint.current_task_index = index
        task.status = "running"
        persist_checkpoint(checkpoint)
        try:
            succeeded = process_task(task)
        except Exception:
            task.status = "failed"
            try:
                persist_checkpoint(checkpoint)
            except Exception:
                pass
            raise
        if succeeded:
            task.status = "completed"
            task.progress = 1.0
            checkpoint.current_task_index = index + 1
            persist_checkpoint(checkpoint)
            continue
        task.status = "failed"
        persist_checkpoint(checkpoint)
        return False
    checkpoint.current_task_index = len(checkpoint.plan.tasks)
    return True
