"""Deterministic identities and invalidation rules for pipeline resume."""

import hashlib
import json
import os
import uuid

from .enums import PipelineStage, StageStatus


IMPORT_SETTING_KEYS = (
    "import_scale", "rotation_only", "import_curves", "clean_after_import",
    "clean_threshold", "clean_flat_channels",
)
BUILD_SETTING_KEYS = (
    "force_rebuild", "generate_thumbnails", "thumb_resolution",
    "thumb_quality", "thumb_isolate", "chunk_size",
)


def _digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalized_settings(config: dict, keys) -> dict:
    return {key: config.get(key) for key in keys}


def import_settings_fingerprint(config: dict) -> str:
    return _digest(_normalized_settings(config, IMPORT_SETTING_KEYS))


def build_settings_fingerprint(config: dict) -> str:
    return _digest(_normalized_settings(config, BUILD_SETTING_KEYS))


def source_revision(path: str, content_hash: str = "") -> str:
    """Fingerprint source content cheaply, preferring a scanner-provided hash."""
    normalized = os.path.normcase(os.path.abspath(os.path.normpath(path))) if path else ""
    if content_hash:
        return _digest({"path": normalized, "hash": content_hash})
    try:
        stat = os.stat(path)
        revision = {"path": normalized, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        revision = {"path": normalized, "missing": True}
    return _digest(revision)


def make_manifest_source(index: int, skeleton: str, folder: str, name: str,
                         path: str, content_hash: str = "") -> dict:
    """Create an ordered identity for one manifest entry, not merely its path."""
    normalized = os.path.normcase(os.path.abspath(os.path.normpath(path))) if path else ""
    entry_id = _digest({
        "ordinal": index,
        "skeleton": skeleton,
        "folder": (folder or "").replace("\\", "/"),
        "name": name,
        "path": normalized,
    })
    revision = source_revision(path, content_hash)
    return {
        "entry_id": entry_id,
        "task_id": _digest({"entry_id": entry_id, "revision": revision}),
        "revision": revision,
        "path": path,
        "normalized_path": normalized,
        "skeleton": skeleton,
        "folder": folder,
        "name": name,
    }


def manifest_sources(manifest) -> list[dict]:
    return [
        make_manifest_source(index, skeleton, folder, name, entry.source_file, entry.content_hash)
        for index, (skeleton, folder, name, entry) in enumerate(manifest.get_all_animations())
    ]


def armature_identity(armature) -> dict:
    """Persist a UUID-like object identity plus a deterministic bone revision."""
    getter = getattr(armature, "get", lambda _key, default=None: default)
    stable_id = str(getter("FModel_ResumeIdentity", "") or "")
    if not stable_id:
        stable_id = str(uuid.uuid4())
        try:
            armature["FModel_ResumeIdentity"] = stable_id
        except (TypeError, AttributeError):
            stable_id = f"legacy:{getattr(armature, 'name', '')}"
    bones = []
    for bone in getattr(getattr(armature, "data", None), "bones", ()):
        parent = getattr(bone, "parent", None)
        bones.append((str(bone.name), str(parent.name) if parent else ""))
    return {"stable_id": stable_id, "structure_revision": _digest(sorted(bones))}


def make_batch_identity(sources: list[dict], settings_fingerprint: str,
                        target_identity: dict) -> str:
    return _digest({
        "task_ids": [source["task_id"] for source in sources],
        "settings": settings_fingerprint,
        "target": target_identity,
    })


def reusable_import_results(results: dict, checkpoint: dict,
                            expected_sources: list) -> bool:
    """Accept only clean terminal results covering current manifest identities."""
    if not isinstance(results, dict) or not isinstance(checkpoint, dict):
        return False
    if checkpoint.get("state") != "complete" or results.get("failed_imports"):
        return False
    expected = [
        source["task_id"] if isinstance(source, dict) else source
        for source in expected_sources
    ]
    actual = [item.get("task_id") for item in results.get("imported_actions", [])]
    return actual == expected and len(set(actual)) == len(actual)


def reconcile_resume_state(state: dict, import_fingerprint: str,
                           build_fingerprint: str, resume_enabled: bool) -> None:
    """Invalidate only affected downstream state while retaining scan results."""
    config = state.setdefault("config", {})
    saved = config.get("resume_fingerprints", {})
    import_changed = bool(saved) and saved.get("import") != import_fingerprint
    build_changed = bool(saved) and saved.get("build") != build_fingerprint
    statuses = state.setdefault("stage_status", {})

    if import_changed:
        state["imported_assets"] = None
        state["library_manifest"] = None
        state["multi_import_plan"] = None
        state["multi_import_checkpoint"] = None
        for stage in (PipelineStage.ASSET_IMPORT, PipelineStage.LIBRARY_BUILDING,
                      PipelineStage.FINALIZATION):
            statuses[stage.value] = StageStatus.PENDING.value
        config["import_checkpoint"] = {}
    elif build_changed:
        state["library_manifest"] = None
        for stage in (PipelineStage.LIBRARY_BUILDING, PipelineStage.FINALIZATION):
            statuses[stage.value] = StageStatus.PENDING.value

    if not resume_enabled:
        config["import_checkpoint"] = {}
    config["resume_fingerprints"] = {
        "import": import_fingerprint,
        "build": build_fingerprint,
    }
