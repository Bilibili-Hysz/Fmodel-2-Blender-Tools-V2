"""Animation Pipeline adapter for project-index services and background jobs."""

from dataclasses import dataclass
import json
from types import SimpleNamespace

from ...core.project_index_service import (
    PreparedAnimationSelection,
    ProjectAnimationIndexService,
    ProjectIndexServiceError,
    ProjectIndexSnapshot,
)
from ...core.project_index_worker import ProjectIndexWorker


_DEFAULT_SERVICE = ProjectAnimationIndexService()
_WORKER_FACTORY = ProjectIndexWorker
_WORKER = None
_REGISTERED = False
_LIFECYCLE_GENERATION = 0
_TIMER_REGISTERED = False
_PENDING = {}
_POLL_INTERVAL = 0.1


@dataclass
class _PendingProjectPrepare:
    job_id: int
    state_generation: int
    lifecycle_generation: int
    scene_name: str
    active_object_name: str
    key: object
    on_success: object
    on_error: object


def apply_snapshot_to_state(state, snapshot: ProjectIndexSnapshot):
    """Publish derived index display data to a duck-typed WizardState."""
    values = {
        "project_index_state": snapshot.state,
        "project_index_schema_version": snapshot.schema_version,
        "project_index_generation": snapshot.generation or 0,
        "project_index_animation_count": snapshot.animation_count,
        "project_index_match_count": snapshot.matching_count,
        "project_index_last_scan_ns": str(snapshot.last_scan_ns or ""),
        "project_index_message": snapshot.message,
    }
    for name, value in values.items():
        if hasattr(state, name):
            setattr(state, name, value)
    return snapshot


def apply_index_error_to_state(state, error: ProjectIndexServiceError):
    """Expose a stable failure class while preserving the detailed message."""
    if error.code == "no_match":
        visible_state = "no_match"
    elif error.code == "cancelled":
        visible_state = "cancelled"
    elif error.code in {"root_required", "root_unavailable", "index_unavailable"}:
        visible_state = "unavailable"
    else:
        visible_state = "failed"
    if hasattr(state, "project_index_state"):
        state.project_index_state = visible_state
    if hasattr(state, "project_index_match_count"):
        state.project_index_match_count = 0
    if hasattr(state, "project_index_message"):
        state.project_index_message = str(error)
    return error


def inspect_pipeline_project_index(state, service=None):
    service = service or _DEFAULT_SERVICE
    snapshot = service.inspect(
        getattr(state, "project_root", ""),
        getattr(state, "skeleton_filter", ""),
    )
    return apply_snapshot_to_state(state, snapshot)


def prepare_project_default_selection(state, service=None) -> PreparedAnimationSelection:
    """Synchronous/headless preparation path retained as a compatibility fallback."""
    service = service or _DEFAULT_SERVICE
    try:
        prepared = service.prepare_selection(
            getattr(state, "project_root", ""),
            getattr(state, "skeleton_filter", ""),
        )
    except ProjectIndexServiceError as error:
        apply_index_error_to_state(state, error)
        raise
    apply_snapshot_to_state(state, prepared.snapshot)
    _persist_selection(state, prepared, mode="AUTO_PROJECT")
    return prepared


def prepare_explicit_selection(state, root, skeleton, paths, service=None):
    """Prepare a legacy explicit selection without exposing its old selector UI."""
    service = service or _DEFAULT_SERVICE
    try:
        prepared = service.prepare_paths(root, skeleton, paths)
    except ProjectIndexServiceError as error:
        apply_index_error_to_state(state, error)
        raise
    apply_snapshot_to_state(state, prepared.snapshot)
    _persist_selection(state, prepared, mode="EXPLICIT")
    return prepared


def can_schedule_project_prepare(context=None):
    """Return whether foreground Blender timers can safely own the async job."""
    if not _REGISTERED:
        return False
    bpy = _get_bpy()
    if bpy is None or bool(getattr(getattr(bpy, "app", None), "background", False)):
        return False
    timers = getattr(getattr(bpy, "app", None), "timers", None)
    if timers is None or not callable(getattr(timers, "register", None)):
        return False
    scene = getattr(context, "scene", None)
    scene_name = _scene_name(scene)
    scenes = getattr(getattr(bpy, "data", None), "scenes", None)
    return bool(scene_name and callable(getattr(scenes, "get", None)))


def schedule_project_prepare(
    context,
    state,
    *,
    selection_mode="AUTO_PROJECT",
    root="",
    skeleton="",
    paths=(),
    on_success=None,
    on_error=None,
):
    """Schedule one project preparation and arrange main-thread finalization.

    Returns a job snapshot when background execution is active, otherwise
    ``None`` so the caller can use the synchronous fallback.
    """
    global _WORKER
    if not can_schedule_project_prepare(context):
        return None
    try:
        worker = _ensure_worker()
        job = worker.schedule(
            root,
            skeleton,
            selection_mode=selection_mode,
            explicit_paths=paths,
        )
    except (RuntimeError, TypeError, ValueError) as error:
        structured = ProjectIndexServiceError(
            "schedule_failed", str(error) or "Animation index job could not be scheduled",
            root=str(root or ""), cause=error,
        )
        apply_index_error_to_state(state, structured)
        if callable(on_error):
            on_error(state, structured)
        raise structured from error

    existing = _PENDING.get(job.job_id)
    if existing is not None and _state_job_id(state) == job.job_id:
        return job

    state_generation = int(getattr(state, "project_index_request_generation", 0) or 0) + 1
    _set_if_present(state, "project_index_request_generation", state_generation)
    _set_if_present(state, "project_index_job_id", str(job.job_id))
    _set_if_present(state, "project_index_cancel_requested", False)
    _set_if_present(state, "project_index_state", "scanning")
    _set_if_present(state, "project_index_progress", 0.0)
    _set_if_present(state, "project_index_progress_phase", "queued")
    _set_if_present(state, "project_index_progress_path", "")
    _set_if_present(state, "project_index_message", "Animation index job queued")

    scene = context.scene
    active = getattr(context, "active_object", None)
    _PENDING[job.job_id] = _PendingProjectPrepare(
        job_id=job.job_id,
        state_generation=state_generation,
        lifecycle_generation=_LIFECYCLE_GENERATION,
        scene_name=_scene_name(scene),
        active_object_name=str(getattr(active, "name", "") or ""),
        key=job.key,
        on_success=on_success,
        on_error=on_error,
    )
    if not _ensure_timer():
        worker.cancel(job.job_id)
        try:
            worker.wait(job.job_id, timeout=1.0)
        except TimeoutError:
            pass
        _PENDING.pop(job.job_id, None)
        error = ProjectIndexServiceError(
            "schedule_failed", "Blender timer registration failed", root=job.key.root
        )
        apply_index_error_to_state(state, error)
        _set_if_present(state, "project_index_job_id", "")
        if callable(on_error):
            on_error(state, error)
        raise error
    return job


def poll_project_job(state=None, job_id=None):
    """Return a worker snapshot and optionally publish progress to state."""
    worker = _WORKER
    target = job_id if job_id is not None else _state_job_id(state)
    if worker is None or not target:
        return None
    snapshot = worker.snapshot(target)
    if state is not None and snapshot is not None and not snapshot.terminal:
        _apply_job_progress(state, snapshot)
    return snapshot


def cancel_project_job(state=None, job_id=None, *, invalidate=True):
    """Cancel an active job and invalidate any delayed main-thread callback."""
    target = job_id if job_id is not None else _state_job_id(state)
    pending = _PENDING.pop(int(target), None) if target else None
    snapshot = _WORKER.snapshot(target) if _WORKER is not None and target else None
    active = bool(snapshot is not None and not snapshot.terminal)
    cancelled = bool(active and _WORKER.cancel(target))
    if state is not None and target:
        _set_if_present(state, "project_index_job_id", "")
        if active or pending is not None:
            if invalidate:
                generation = int(getattr(state, "project_index_request_generation", 0) or 0) + 1
                _set_if_present(state, "project_index_request_generation", generation)
            _set_if_present(state, "project_index_cancel_requested", True)
            _set_if_present(state, "project_index_state", "cancelled")
            _set_if_present(state, "project_index_progress_phase", "cancelled")
            _set_if_present(state, "project_index_message", "Animation index job cancelled")
    return cancelled


def reset_project_job_state(state):
    """Clear transient job metadata restored from a saved .blend file."""
    if state is None:
        return
    _set_if_present(state, "project_index_job_id", "")
    _set_if_present(state, "project_index_progress", 0.0)
    _set_if_present(state, "project_index_progress_phase", "")
    _set_if_present(state, "project_index_progress_path", "")
    _set_if_present(state, "project_index_cancel_requested", False)
    if getattr(state, "project_index_state", "") == "scanning":
        _set_if_present(state, "project_index_state", "missing")
        _set_if_present(
            state,
            "project_index_message",
            "Previous background index job did not survive file reload",
        )


def cancel_all_project_jobs():
    """Cancel every queued/running request without touching Blender RNA."""
    global _LIFECYCLE_GENERATION
    _LIFECYCLE_GENERATION += 1
    pending_ids = tuple(_PENDING)
    _PENDING.clear()
    worker = _WORKER
    if worker is not None:
        for job_id in pending_ids:
            worker.cancel(job_id)
    _unregister_poll_timer()
    return len(pending_ids)

def register():
    """Enable foreground async scheduling for this add-on lifecycle."""
    global _REGISTERED, _LIFECYCLE_GENERATION
    if _REGISTERED:
        return
    _LIFECYCLE_GENERATION += 1
    _REGISTERED = True
    bpy = _get_bpy()
    for scene in _iter_scenes(bpy):
        reset_project_job_state(getattr(scene, "pipeline_wizard", None))


def unregister():
    """Cancel worker/timer state before Blender unregisters Wizard RNA types."""
    global _REGISTERED, _WORKER
    _REGISTERED = False
    cancel_all_project_jobs()
    worker = _WORKER
    if worker is not None:
        worker.shutdown(wait=False)
    _WORKER = None


def _persist_selection(state, prepared, *, mode):
    state.indexed_project_root = prepared.root
    state.indexed_skeleton = prepared.skeleton
    state.indexed_selected_paths = json.dumps(list(prepared.paths))
    state.indexed_selection_mode = mode


def _ensure_worker():
    global _WORKER
    if _WORKER is None:
        _WORKER = _WORKER_FACTORY()
    return _WORKER


def _ensure_timer():
    global _TIMER_REGISTERED
    if _TIMER_REGISTERED:
        return True
    bpy = _get_bpy()
    timers = getattr(getattr(bpy, "app", None), "timers", None) if bpy else None
    if timers is None:
        return False
    try:
        timers.register(_poll_timer, first_interval=0.05, persistent=False)
    except (ReferenceError, RuntimeError, TypeError, ValueError):
        return False
    _TIMER_REGISTERED = True
    return True


def _unregister_poll_timer():
    global _TIMER_REGISTERED
    bpy = _get_bpy()
    timers = getattr(getattr(bpy, "app", None), "timers", None) if bpy else None
    if timers is not None and callable(getattr(timers, "unregister", None)):
        try:
            is_registered = getattr(timers, "is_registered", None)
            if not callable(is_registered) or is_registered(_poll_timer):
                timers.unregister(_poll_timer)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    _TIMER_REGISTERED = False

def _poll_timer():
    """Blender main-thread timer: publish progress and finalize current jobs."""
    global _TIMER_REGISTERED
    if not _REGISTERED:
        _TIMER_REGISTERED = False
        return None
    worker = _WORKER
    if worker is None or not _PENDING:
        _TIMER_REGISTERED = False
        return None

    bpy = _get_bpy()
    for job_id, pending in tuple(_PENDING.items()):
        if pending.lifecycle_generation != _LIFECYCLE_GENERATION:
            worker.cancel(job_id)
            _PENDING.pop(job_id, None)
            continue
        scene = _resolve_scene(bpy, pending.scene_name)
        state = getattr(scene, "pipeline_wizard", None) if scene is not None else None
        if state is None:
            worker.cancel(job_id)
            _PENDING.pop(job_id, None)
            continue
        if not _request_is_current(state, pending):
            worker.cancel(job_id)
            if _state_job_id(state) == job_id:
                _set_if_present(state, "project_index_job_id", "")
                _set_if_present(state, "project_index_state", "cancelled")
                _set_if_present(state, "project_index_progress_phase", "cancelled")
                _set_if_present(
                    state, "project_index_message",
                    "Animation index result discarded because inputs changed",
                )
            _PENDING.pop(job_id, None)
            continue

        snapshot = worker.snapshot(job_id)
        if snapshot is None:
            _PENDING.pop(job_id, None)
            continue
        _apply_job_progress(state, snapshot)
        if not snapshot.terminal:
            continue
        try:
            if snapshot.status == "succeeded" and snapshot.result is not None:
                apply_snapshot_to_state(state, snapshot.result.snapshot)
                _persist_selection(
                    state,
                    snapshot.result,
                    mode=snapshot.key.selection_mode,
                )
                callback = pending.on_success
                if callable(callback):
                    callback(
                        state,
                        snapshot.result,
                        _callback_context(bpy, scene, pending.active_object_name),
                    )
            else:
                error = snapshot.error or ProjectIndexServiceError(
                    "cancelled" if snapshot.status == "cancelled" else "worker_failed",
                    "Animation index job cancelled" if snapshot.status == "cancelled"
                    else "Animation index job failed",
                    root=snapshot.key.root,
                )
                apply_index_error_to_state(state, error)
                if callable(pending.on_error):
                    pending.on_error(state, error)
        except Exception as error:
            structured = error if isinstance(error, ProjectIndexServiceError) else ProjectIndexServiceError(
                "finalize_failed",
                str(error) or "Import plan finalization failed",
                root=snapshot.key.root,
                cause=error,
            )
            apply_index_error_to_state(state, structured)
            if callable(pending.on_error):
                try:
                    pending.on_error(state, structured)
                except Exception:
                    pass
        finally:
            if _state_job_id(state) == job_id:
                _set_if_present(state, "project_index_job_id", "")
            _set_if_present(state, "project_index_cancel_requested", False)
            _PENDING.pop(job_id, None)

    if _PENDING:
        return _POLL_INTERVAL
    _TIMER_REGISTERED = False
    return None


def _request_is_current(state, pending):
    if pending.lifecycle_generation != _LIFECYCLE_GENERATION:
        return False
    if _state_job_id(state) != pending.job_id:
        return False
    if int(getattr(state, "project_index_request_generation", 0) or 0) != pending.state_generation:
        return False
    try:
        if pending.key.selection_mode == "AUTO_PROJECT":
            if getattr(state, "source_mode", "PROJECT") != "PROJECT":
                return False
            current = ProjectIndexWorker.make_key(
                getattr(state, "project_root", ""),
                getattr(state, "skeleton_filter", ""),
                selection_mode="AUTO_PROJECT",
            )
        else:
            paths = json.loads(getattr(state, "indexed_selected_paths", "[]") or "[]")
            current = ProjectIndexWorker.make_key(
                getattr(state, "indexed_project_root", ""),
                getattr(state, "indexed_skeleton", ""),
                selection_mode="EXPLICIT",
                explicit_paths=paths,
            )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return current == pending.key


def _apply_job_progress(state, snapshot):
    progress = snapshot.progress
    _set_if_present(state, "project_index_progress", progress.fraction)
    _set_if_present(state, "project_index_progress_phase", progress.phase)
    _set_if_present(state, "project_index_progress_path", progress.path)
    _set_if_present(state, "project_index_cancel_requested", snapshot.status == "cancelling")
    if snapshot.status in {"queued", "running", "cancelling"}:
        _set_if_present(state, "project_index_state", "scanning")
    if progress.total:
        message = f"{progress.phase}: {progress.completed}/{progress.total}"
    elif progress.completed:
        message = f"{progress.phase}: {progress.completed}"
    else:
        message = progress.phase
    _set_if_present(state, "project_index_message", f"Animation index {message}")


def _callback_context(bpy, scene, active_object_name):
    objects = getattr(getattr(bpy, "data", None), "objects", None) if bpy else None
    active = objects.get(active_object_name) if active_object_name and hasattr(objects, "get") else None
    return SimpleNamespace(scene=scene, active_object=active)


def _iter_scenes(bpy):
    scenes = getattr(getattr(bpy, "data", None), "scenes", None) if bpy else None
    if scenes is None:
        return ()
    values = getattr(scenes, "values", None)
    if callable(values):
        return tuple(values())
    try:
        return tuple(scenes)
    except TypeError:
        return ()

def _resolve_scene(bpy, scene_name):
    scenes = getattr(getattr(bpy, "data", None), "scenes", None) if bpy else None
    return scenes.get(scene_name) if scenes is not None and hasattr(scenes, "get") else None


def _scene_name(scene):
    return str(getattr(scene, "name_full", "") or getattr(scene, "name", "") or "")


def _state_job_id(state):
    if state is None:
        return None
    value = getattr(state, "project_index_job_id", "")
    try:
        return int(value) if str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _set_if_present(state, name, value):
    if hasattr(state, name):
        setattr(state, name, value)


def _get_bpy():
    try:
        import bpy
    except ImportError:
        return None
    return bpy


__all__ = [
    "apply_index_error_to_state",
    "apply_snapshot_to_state",
    "can_schedule_project_prepare",
    "cancel_all_project_jobs",
    "cancel_project_job",
    "inspect_pipeline_project_index",
    "poll_project_job",
    "prepare_explicit_selection",
    "prepare_project_default_selection",
    "register",
    "reset_project_job_state",
    "schedule_project_prepare",
    "unregister",
]
