"""Filesystem-only helpers for publishing completed library bundles."""

import json
import hashlib
import os
import re
import shutil
import tempfile
import unicodedata
import uuid

from .resume import source_content_hash
from .types import (
    AssetLibraryManifest, BundleRecord, CatalogEntry, LibraryAsset,
)


_WINDOWS_RESERVED_FILENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_WINDOWS_INVALID_FILENAME_CHARS = frozenset('/\\:?*"<>|')
_WINDOWS_MAX_COMPONENT_LENGTH = 255
_TEMP_BUNDLE_TOKEN_DIGITS = 20


def validate_filename_component(value, label="name"):
    """Reject values that cannot safely name a Windows filesystem component."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty filename component")
    if value in (".", "..") or any(char in _WINDOWS_INVALID_FILENAME_CHARS for char in value):
        raise ValueError(f"{label} must be a safe filename component")
    if any(ord(char) < 32 for char in value) or value[-1] in (".", " "):
        raise ValueError(f"{label} must be a safe filename component")
    if len(value) > _WINDOWS_MAX_COMPONENT_LENGTH:
        raise ValueError(f"{label} must not exceed 255 characters")
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_FILENAMES:
        raise ValueError(f"{label} must be a safe filename component")
    return value


def safe_child_path(directory, filename, label="filename"):
    """Return a validated direct-child path, rejecting escape through a filename."""
    validate_filename_component(filename, label)
    base_dir = os.path.realpath(os.path.abspath(directory))
    path = os.path.realpath(os.path.abspath(os.path.join(base_dir, filename)))
    if os.path.dirname(path) != base_dir:
        raise ValueError(f"{label} must not escape the target directory")
    return path


def storage_component(label):
    """Encode a display label as a stable, portable physical namespace."""
    if not isinstance(label, str) or not label:
        raise ValueError("asset label must be a non-empty string")
    prefix = re.sub(r"[^A-Za-z0-9_]", "_", label)[:16].strip('_') or "asset"
    return f"{prefix}__id_{hashlib.sha256(label.encode('utf-8')).hexdigest()[:32]}"


def library_bundle_base(library_name, skeleton_name, manifest=None):
    """Use encoded names for new bundles; preserve verified legacy namespaces."""
    encoded = f"{storage_component(library_name)}_{storage_component(skeleton_name)}"
    owned = [bundle for bundle in getattr(manifest, "bundles", ()) if bundle.skeletons == [skeleton_name]]
    if owned:
        legacy = f"{library_name}_{skeleton_name}"
        try:
            validate_filename_component(legacy, "legacy bundle stem")
        except ValueError:
            pass
        else:
            pattern = rf"{re.escape(legacy)}(?:_Vol_[1-9][0-9]*)?\.blend"
            if all(re.fullmatch(pattern, bundle.blend_file) for bundle in owned):
                return legacy
    return encoded


def model_storage_stem(library_name, skeleton_name=None):
    return storage_component(library_name) if skeleton_name is None else library_bundle_base(library_name, skeleton_name)


def staging_chunk_path(directory, skeleton_name, chunk_index):
    """Name chunks inside the existing task namespace, independently of labels."""
    filename = f"actions_chunk_{chunk_index:04d}.blend"
    return safe_child_path(directory, filename, "staging chunk filename")


def staging_task_dir(output_dir, task_identity):
    """Return the deterministic direct-child staging namespace for one task."""
    if not isinstance(task_identity, dict) or not task_identity:
        raise ValueError("staging task identity must be an object")
    identity_json = json.dumps(task_identity, ensure_ascii=True, sort_keys=True)
    task_id = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()[:16]
    tmp_root = os.path.realpath(os.path.abspath(os.path.join(output_dir, "_pipeline_tmp")))
    task_dir = os.path.realpath(os.path.abspath(os.path.join(tmp_root, task_id)))
    if os.path.dirname(task_dir) != tmp_root:
        raise ValueError("staging task directory must be under output/_pipeline_tmp")
    return task_dir


def validate_staged_blend_path(task_dir, skeleton_name, staged_blend):
    """Validate a persisted chunk path without opening the referenced file."""
    if not isinstance(skeleton_name, str) or not skeleton_name:
        raise ValueError("source skeleton label is required")
    if not isinstance(staged_blend, str) or not staged_blend:
        raise ValueError("staged blend path must be a non-empty string")
    task_abs = os.path.abspath(task_dir)
    task_real = os.path.realpath(task_abs)
    staged_abs = os.path.abspath(staged_blend)
    staged_real = os.path.realpath(staged_abs)
    filename = os.path.basename(staged_abs)
    validate_filename_component(filename, "staging chunk filename")
    expected = re.fullmatch(
        r".+_chunk_[0-9]{4,}\.blend", filename
    )
    if (
        os.path.dirname(staged_abs) != task_abs
        or os.path.dirname(staged_real) != task_real
        or expected is None
    ):
        raise ValueError("staged blend violates the current task path or filename contract")
    return staged_abs


def validate_skeleton_output_names(skeleton_names):
    """Validate encoded storage names while leaving display labels independent."""
    seen = {}
    generated_stems = {}
    for skeleton_name in skeleton_names:
        if not isinstance(skeleton_name, str) or not skeleton_name or '/' in skeleton_name or '\\' in skeleton_name:
            raise ValueError("source skeleton must be a label without manifest separators")
        physical_name = storage_component(skeleton_name)
        validate_filename_component(physical_name, "source skeleton storage name")
        output_key = physical_name.casefold()
        previous = seen.get(output_key)
        if previous is not None and previous != skeleton_name:
            raise ValueError(
                f"source skeleton output collision: {previous!r} and {skeleton_name!r}"
            )
        seen[output_key] = skeleton_name
        for stem in (physical_name, f"{physical_name}_Vol_2"):
            stem_key = unicodedata.normalize("NFC", stem).casefold()
            previous = generated_stems.get(stem_key)
            if previous is not None and previous != skeleton_name:
                raise ValueError(
                    f"source skeleton output collision: {previous!r} and {skeleton_name!r}"
                )
            generated_stems[stem_key] = skeleton_name


def validate_generated_output_filenames(
    library_name, skeleton_names, include_models=False, volume_indices=(1, 2),
    base_names=None,
):
    """Validate every generated Windows filename component before mutation."""
    storage_component(library_name)
    skeleton_names = tuple(skeleton_names)
    validate_skeleton_output_names(skeleton_names)
    volume_plan = (
        volume_indices
        if isinstance(volume_indices, dict)
        else {skeleton_name: volume_indices for skeleton_name in skeleton_names}
    )
    generated_stems = {}

    if include_models:
        multiple = len(skeleton_names) > 1
        for skeleton_name in skeleton_names:
            model_name = model_storage_stem(library_name, skeleton_name if multiple else None)
            validate_filename_component(f"{model_name}_mesh.blend", "model filename")

    for skeleton_name in skeleton_names:
        base_name = base_names[skeleton_name] if base_names is not None else library_bundle_base(library_name, skeleton_name)
        try:
            skeleton_volume_indices = volume_plan[skeleton_name]
        except KeyError:
            raise ValueError(f"missing volume plan for source skeleton: {skeleton_name!r}")
        for volume_index in skeleton_volume_indices:
            if not isinstance(volume_index, int) or volume_index < 1:
                raise ValueError("volume index must be a positive integer")
            vol_suffix = f"_Vol_{volume_index}" if volume_index > 1 else ""
            stem = f"{base_name}{vol_suffix}"
            stem_key = unicodedata.normalize("NFC", stem).casefold()
            previous = generated_stems.get(stem_key)
            if previous is not None and previous != skeleton_name:
                raise ValueError(
                    f"source skeleton output collision: {previous!r} and {skeleton_name!r}"
                )
            generated_stems[stem_key] = skeleton_name
            validate_filename_component(
                f"{base_name}{vol_suffix}.blend", "bundle filename"
            )
            validate_filename_component(
                f"{base_name}{vol_suffix}_manifest.json", "sidecar filename"
            )
            validate_filename_component(
                f".{base_name}{vol_suffix}.{'9' * _TEMP_BUNDLE_TOKEN_DIGITS}.blend.tmp",
                "temporary bundle filename",
            )


def validate_imported_action_set(imported_actions):
    """Validate persisted import references before incremental publication.

    Only the skeleton becomes part of an output filename. Catalog and action
    components remain ordinary metadata and may contain filename characters.
    """
    if not isinstance(imported_actions, list) or not imported_actions:
        raise ValueError("imported actions must be a non-empty list")
    seen = set()
    skeleton_names = []
    for item in imported_actions:
        if not isinstance(item, dict):
            raise ValueError("imported action must be an object")
        action_name = item.get("action_name")
        reference = item.get("source_manifest_ref")
        if not isinstance(action_name, str) or not action_name:
            raise ValueError("imported action requires an action name")
        if not isinstance(reference, str):
            raise ValueError("imported action requires a source manifest reference")
        parts = reference.split("/")
        if len(parts) < 3 or any(not part for part in parts):
            raise ValueError("imported action has an invalid source manifest reference")
        skeleton_names.append(parts[0])
        key = (reference, action_name, item.get("source_file", ""))
        if key in seen:
            raise ValueError("imported action set contains duplicate references")
        seen.add(key)
    validate_skeleton_output_names(skeleton_names)


def library_index_path(library_path, library_name):
    """Preserve usable legacy index paths and encode other display labels."""
    storage_component(library_name)
    filename = f"{library_name}__library_manifest.json"
    try:
        validate_filename_component(filename, "library index filename")
    except ValueError:
        filename = f"{storage_component(library_name)}__library_manifest.json"
    return safe_child_path(
        library_path, filename, "library index filename"
    )


def library_transaction_path(index_path):
    return f"{index_path}.txn.json"


def _transaction_path_key(path):
    return None if path is None else os.path.normcase(os.path.abspath(path))


def _validate_transaction_path(path, transaction_dir, label, allow_none=False):
    if path is None and allow_none:
        return
    if not isinstance(path, str) or not path:
        raise ValueError(f"library transaction {label} must be a path")
    resolved = os.path.realpath(os.path.abspath(path))
    try:
        contained = os.path.commonpath((transaction_dir, resolved)) == transaction_dir
    except ValueError:
        contained = False
    if not contained:
        raise ValueError(f"library transaction path escapes library directory: {label}")


def _validate_transaction(index_path, transaction):
    """Validate journal shape and path confinement before filesystem mutation."""
    if not isinstance(transaction, dict):
        raise ValueError("library transaction must be an object")
    if transaction.get("state") not in {
        "prepared", "pairs_published", "index_published", "rollback_failed",
        "rolled_back",
    }:
        raise ValueError("library transaction has an invalid state")
    transaction_dir = os.path.realpath(os.path.abspath(os.path.dirname(index_path) or "."))
    recorded_index = transaction.get("index_path", index_path)
    _validate_transaction_path(recorded_index, transaction_dir, "index_path")
    if os.path.normcase(os.path.abspath(recorded_index)) != os.path.normcase(os.path.abspath(index_path)):
        raise ValueError("library transaction index path does not match journal")
    _validate_transaction_path(
        transaction.get("index_backup"), transaction_dir, "index_backup", allow_none=True
    )
    if transaction.get("state") in ("pairs_published", "index_published"):
        if not isinstance(transaction.get("manifest"), dict):
            raise ValueError("library transaction manifest must be an object")
    cleanup_paths = transaction.get("cleanup_paths", [])
    if not isinstance(cleanup_paths, list):
        raise ValueError("library transaction cleanup_paths must be a list")
    for path in cleanup_paths:
        _validate_transaction_path(path, transaction_dir, "cleanup_path")

    replacements = transaction.get("replacements")
    if replacements is None:
        required = ("blend_path", "sidecar_path")
        if any(key not in transaction for key in required):
            raise ValueError("library transaction replacement is incomplete")
        replacements = [transaction]
    if not isinstance(replacements, list):
        raise ValueError("library transaction replacements must be a list")
    for replacement in replacements:
        if not isinstance(replacement, dict):
            raise ValueError("library transaction replacement must be an object")
        for key in ("blend_path", "sidecar_path"):
            if key not in replacement:
                raise ValueError("library transaction replacement is incomplete")
            _validate_transaction_path(replacement[key], transaction_dir, key)
        for key in ("temporary_blend", "blend_backup", "sidecar_backup"):
            _validate_transaction_path(
                replacement.get(key), transaction_dir, key, allow_none=True
            )
    artifact_paths = transaction.get("artifact_paths", [])
    if not isinstance(artifact_paths, list):
        raise ValueError("library transaction artifact_paths must be a list")
    for artifact in artifact_paths:
        if not isinstance(artifact, dict):
            raise ValueError("library transaction artifact must be an object")
        _validate_transaction_path(artifact.get("path"), transaction_dir, "artifact_path")
        _validate_transaction_path(artifact.get("backup"), transaction_dir, "artifact_backup", allow_none=True)
    return replacements


def _write_json_temp(destination, payload):
    directory = os.path.dirname(destination) or "."
    fd, temporary_path = tempfile.mkstemp(prefix=".bundle-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=4)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary_path
    except Exception:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        raise


def _backup_file(path):
    """Return a private copy so a failed replacement can restore the old pair."""
    if not os.path.exists(path):
        return None
    directory = os.path.dirname(path) or "."
    fd, backup_path = tempfile.mkstemp(prefix=".bundle-backup-", dir=directory)
    os.close(fd)
    try:
        shutil.copy2(path, backup_path)
        _fsync_file(backup_path)
        _fsync_directory(backup_path)
        return backup_path
    except Exception:
        try:
            os.remove(backup_path)
        except OSError:
            pass
        raise


def prepare_transaction_artifact(path):
    """Capture an artifact before a build mutates it, for a later transaction."""
    return {"path": path, "backup": _backup_file(path)}


def _ensure_backup_durable(backup_path):
    if backup_path is not None:
        _fsync_file(backup_path)
        _fsync_directory(backup_path)


def prepare_library_transaction(index_path, artifact_paths=()):
    """Write a durable prepared journal before any library artifact mutation."""
    recover_library_transaction(index_path)
    journal_path = library_transaction_path(index_path)
    transaction = {
        "state": "prepared",
        "index_path": index_path,
        "index_backup": _backup_file(index_path),
        "replacements": [],
        "cleanup_paths": [],
        "artifact_paths": [],
    }
    for artifact in artifact_paths:
        if isinstance(artifact, dict):
            path = artifact["path"]
            backup = artifact.get("backup")
            _ensure_backup_durable(backup)
        else:
            path = artifact
            backup = _backup_file(path)
        transaction["artifact_paths"].append({"path": path, "backup": backup})
    _validate_transaction(index_path, transaction)
    _write_transaction(journal_path, transaction)
    return transaction


def persist_transaction_artifact(index_path, path):
    """Back up a late-discovered artifact and durably add it to a prepared journal."""
    journal_path = library_transaction_path(index_path)
    with open(journal_path, "r", encoding="utf-8") as handle:
        transaction = json.load(handle)
    _validate_transaction(index_path, transaction)
    if transaction.get("state") != "prepared":
        raise ValueError("library transaction is not prepared")
    key = os.path.normcase(os.path.abspath(path))
    for artifact in transaction["artifact_paths"]:
        if os.path.normcase(os.path.abspath(artifact["path"])) == key:
            return artifact
    artifact = prepare_transaction_artifact(path)
    transaction["artifact_paths"].append(artifact)
    _validate_transaction(index_path, transaction)
    _write_transaction(journal_path, transaction)
    return artifact


def persist_transaction_replacement(
    index_path, temporary_blend, blend_path, sidecar_path, sidecar_payload
):
    """Durably enroll a completed temporary bundle before resumable cleanup."""
    journal_path = library_transaction_path(index_path)
    with open(journal_path, "r", encoding="utf-8") as handle:
        transaction = json.load(handle)
    _validate_transaction(index_path, transaction)
    if transaction.get("state") != "prepared":
        raise ValueError("library transaction is not prepared")
    key = tuple(_transaction_path_key(path) for path in (
        temporary_blend, blend_path, sidecar_path,
    ))
    for replacement in transaction["replacements"]:
        existing = tuple(_transaction_path_key(replacement[path_key])
                         for path_key in ("temporary_blend", "blend_path", "sidecar_path"))
        if existing == key:
            return replacement
    replacement = {
        "temporary_blend": temporary_blend,
        "replace_blend": True,
        "blend_path": blend_path,
        "sidecar_path": sidecar_path,
        "sidecar_payload": sidecar_payload,
        "blend_backup": _backup_file(blend_path),
        "sidecar_backup": _backup_file(sidecar_path),
    }
    transaction["replacements"].append(replacement)
    _validate_transaction(index_path, transaction)
    _write_transaction(journal_path, transaction)
    return replacement


def _restore_file(backup_path, destination):
    if backup_path is None:
        if os.path.exists(destination):
            os.remove(destination)
            _fsync_directory(destination)
    else:
        os.replace(backup_path, destination)
        _fsync_directory(destination)


def _restore_file_preserving_backup(backup_path, destination):
    """Restore through a copy so a failed rollback keeps its original evidence."""
    if backup_path is None:
        if os.path.exists(destination):
            os.remove(destination)
            _fsync_directory(destination)
        return
    directory = os.path.dirname(destination) or "."
    fd, temporary_path = tempfile.mkstemp(prefix=".bundle-restore-", dir=directory)
    os.close(fd)
    try:
        shutil.copy2(backup_path, temporary_path)
        _fsync_file(temporary_path)
        os.replace(temporary_path, destination)
        _fsync_directory(destination)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def _write_transaction(path, transaction):
    temporary_path = _write_json_temp(path, transaction)
    try:
        os.replace(temporary_path, path)
        _fsync_directory(path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def _remove_cleanup_paths(paths):
    """Remove obsolete published files; missing files are already cleaned up."""
    for path in paths:
        try:
            os.remove(path)
            _fsync_directory(path)
        except FileNotFoundError:
            pass


def _fsync_file(path):
    """Flush a completed file before publishing or referencing it."""
    # Windows rejects fsync on a read-only descriptor with EBADF.
    with open(path, "rb+") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path):
    """Make a completed rename durable where the platform exposes directory fsync."""
    try:
        fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        try:
            os.fsync(fd)
        except (OSError, AttributeError):
            pass
    finally:
        os.close(fd)


def _discard_transaction_artifacts(journal_path, transaction):
    _discard_transaction_backups(transaction)
    if os.path.exists(journal_path):
        os.remove(journal_path)
        _fsync_directory(journal_path)


def _discard_transaction_backups(transaction):
    """Best-effort disposal after the journal no longer needs restoration data."""
    replacements = transaction.get("replacements")
    if replacements is None:
        replacements = [{
            "blend_backup": transaction.get("blend_backup"),
            "sidecar_backup": transaction.get("sidecar_backup"),
        }]
    for replacement in replacements:
        for backup_key in ("blend_backup", "sidecar_backup"):
            backup_path = replacement.get(backup_key)
            if backup_path and os.path.exists(backup_path):
                os.remove(backup_path)
                _fsync_directory(backup_path)
    index_backup = transaction.get("index_backup")
    if index_backup and os.path.exists(index_backup):
        os.remove(index_backup)
        _fsync_directory(index_backup)
    for artifact in transaction.get("artifact_paths", []):
        backup_path = artifact.get("backup")
        if backup_path and os.path.exists(backup_path):
            os.remove(backup_path)
            _fsync_directory(backup_path)


def _finish_rollback(journal_path, transaction):
    """Make rollback terminal before dropping the evidence it formerly required."""
    transaction["state"] = "rolled_back"
    _write_transaction(journal_path, transaction)
    os.remove(journal_path)
    _fsync_directory(journal_path)
    _discard_transaction_backups(transaction)


def fsync_transaction_artifacts(transaction):
    """Flush journaled non-pair artifacts before their index can expose them."""
    for artifact in transaction.get("artifact_paths", []):
        path = artifact["path"]
        if os.path.isfile(path):
            _fsync_file(path)
            _fsync_directory(path)


def recover_library_transaction(index_path):
    """Finish or roll back a transaction left by a hard crash."""
    journal_path = library_transaction_path(index_path)
    if not os.path.isfile(journal_path):
        return
    with open(journal_path, "r", encoding="utf-8") as handle:
        transaction = json.load(handle)
    replacements = _validate_transaction(index_path, transaction)
    persisted_replacements = transaction.get("replacements")
    if persisted_replacements is None:
        replacements = [{
            "blend_path": transaction["blend_path"],
            "sidecar_path": transaction["sidecar_path"],
            "blend_backup": transaction.get("blend_backup"),
            "sidecar_backup": transaction.get("sidecar_backup"),
            "replace_blend": transaction.get("temporary_blend") is not None,
        }]
    if transaction.get("state") == "rolled_back":
        # This journal only records cleanup after a completed rollback. Never
        # restore from its backups: a prior crash may have already removed them.
        os.remove(journal_path)
        _fsync_directory(journal_path)
        _discard_transaction_backups(transaction)
        return
    if transaction.get("state") in ("pairs_published", "index_published"):
        # An index may have been written before the journal state update failed.
        fsync_transaction_artifacts(transaction)
        publish_library_index(index_path, transaction["manifest"])
        transaction["state"] = "index_published"
        _write_transaction(journal_path, transaction)
        _remove_cleanup_paths(transaction.get("cleanup_paths", []))
    else:
        for replacement in replacements:
            replace_blend = replacement.get("replace_blend")
            if replace_blend is None:
                replace_blend = (
                    replacement.get("temporary_blend") is not None
                    or replacement.get("blend_backup") is not None
                )
            if replace_blend:
                _restore_file_preserving_backup(
                    replacement.get("blend_backup"), replacement["blend_path"]
                )
            _restore_file_preserving_backup(
                replacement.get("sidecar_backup"), replacement["sidecar_path"]
            )
            temporary_blend = replacement.get("temporary_blend")
            if temporary_blend and os.path.exists(temporary_blend):
                os.remove(temporary_blend)
                _fsync_directory(temporary_blend)
        if "index_backup" in transaction:
            _restore_file_preserving_backup(transaction.get("index_backup"), index_path)
        for artifact in transaction.get("artifact_paths", []):
            _restore_file_preserving_backup(artifact.get("backup"), artifact["path"])
    _discard_transaction_artifacts(journal_path, transaction)


def _sidecar_action_provenance(sidecar):
    """Return structural action provenance, or ``None`` for an invalid tree."""
    if not isinstance(sidecar, dict):
        return None
    provenance = set()
    found_tree = False
    for skeleton, folders in sidecar.items():
        if skeleton == "__processed_sources__":
            if not isinstance(folders, list) or not all(isinstance(item, str) for item in folders):
                return None
            continue
        if not isinstance(skeleton, str) or not skeleton or not isinstance(folders, dict):
            return None
        found_tree = True
        stack = [(folders, [])]
        while stack:
            node, path = stack.pop()
            for name, value in node.items():
                if not isinstance(name, str):
                    return None
                if name == "__actions__":
                    if not isinstance(value, list):
                        return None
                    for action in value:
                        if isinstance(action, str):
                            action_name = action
                        elif (
                            isinstance(action, list) and 1 <= len(action) <= 2
                            and isinstance(action[0], str)
                            and (len(action) == 1 or isinstance(action[1], str))
                        ):
                            action_name = action[0]
                        else:
                            return None
                        if not action_name:
                            return None
                        provenance.add((skeleton, "/".join(path), action_name))
                elif isinstance(value, dict):
                    stack.append((value, path + [name]))
                else:
                    return None
    return provenance if found_tree else None


def _index_action_provenance(manifest, blend_file):
    catalogs = {catalog.uuid: catalog for catalog in manifest.catalog_entries if catalog.uuid}
    provenance = set()
    for asset in manifest.assets:
        if asset.blend_file != blend_file:
            continue
        catalog = catalogs.get(asset.catalog_id)
        if catalog is None or not asset.action_name:
            return None
        skeleton = asset.skeleton or catalog.skeleton
        parts = [part for part in catalog.path.replace("\\", "/").split("/") if part]
        if skeleton and parts and parts[0] == skeleton:
            parts.pop(0)
        if not skeleton:
            return None
        provenance.add((skeleton, "/".join(parts), asset.action_name))
    return provenance


def load_complete_v2_manifest(manifest_path, library_path):
    """Load a v2 index while retaining only records backed by an exact pair."""
    recover_library_transaction(manifest_path)
    if not os.path.isfile(manifest_path):
        return None
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = AssetLibraryManifest.from_dict(json.load(handle))
    if manifest.version != "2.0":
        return None

    bundles_by_blend = {}
    for bundle in manifest.bundles:
        try:
            blend_path = safe_child_path(library_path, bundle.blend_file, "bundle filename")
            sidecar_path = safe_child_path(library_path, bundle.sidecar_file, "sidecar filename")
        except ValueError:
            continue
        if (
            bundle.completion_state != "completed"
            or not bundle.blend_file
            or not bundle.sidecar_file
            or os.path.basename(bundle.blend_file) != bundle.blend_file
            or os.path.basename(bundle.sidecar_file) != bundle.sidecar_file
            or bundle.sidecar_file != f"{os.path.splitext(bundle.blend_file)[0]}_manifest.json"
            or not os.path.isfile(blend_path)
            or not os.path.isfile(sidecar_path)
        ):
            continue
        try:
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                sidecar_provenance = _sidecar_action_provenance(json.load(handle))
                index_provenance = _index_action_provenance(manifest, bundle.blend_file)
                if sidecar_provenance is None or sidecar_provenance != index_provenance:
                    continue
        except (OSError, ValueError, TypeError):
            continue
        bundles_by_blend[bundle.blend_file] = bundle

    retained_blends = set(bundles_by_blend)
    manifest.bundles = list(bundles_by_blend.values())
    manifest.blend_files = list(bundles_by_blend)
    manifest.assets = [asset for asset in manifest.assets if asset.blend_file in retained_blends]
    catalog_ids = {asset.catalog_id for asset in manifest.assets}
    manifest.catalog_entries = [
        catalog for catalog in manifest.catalog_entries if catalog.uuid in catalog_ids
    ]
    skeletons = set()
    for bundle in manifest.bundles:
        skeletons.update(bundle.skeletons)
    skeletons.update(asset.skeleton for asset in manifest.assets if asset.skeleton)
    skeletons.update(catalog.skeleton for catalog in manifest.catalog_entries if catalog.skeleton)
    manifest.skeletons = sorted(skeletons)
    return manifest


def get_manifest_processed_sources(manifest, skeleton):
    """Return sources from retained completed bundles for one skeleton."""
    return {
        source if "::" in source else f"{skeleton}::{source}"
        for bundle in manifest.bundles
        if isinstance(bundle.skeletons, list) and skeleton in bundle.skeletons
        if bundle.completion_state == "completed"
        if isinstance(bundle.processed_sources, list)
        for source in bundle.processed_sources
        if isinstance(source, str) and source
        # An unqualified source is only safe when its bundle has one skeleton.
        # Multi-skeleton provenance must remain unresolved rather than skipping
        # the same source for every skeleton on a later incremental import.
        if source.startswith(f"{skeleton}::")
        or "::" not in source and len(bundle.skeletons) == 1
    }


def library_source_key(skeleton, source_file, source_dir, source_ref=""):
    value = source_ref
    if source_file and source_dir:
        try:
            value = os.path.relpath(source_file, source_dir).replace('\\', '/')
        except ValueError:
            pass
    return f"{skeleton}::{value}" if skeleton and value else ""


def get_manifest_source_versions(manifest, skeleton):
    versions = {}
    for bundle in manifest.bundles if manifest is not None else ():
        if bundle.completion_state != "completed" or skeleton not in bundle.skeletons:
            continue
        for source in bundle.processed_sources:
            key = source if "::" in source else f"{skeleton}::{source}"
            if not key.startswith(f"{skeleton}::"):
                continue
            if "::" not in source and len(bundle.skeletons) != 1:
                raise ValueError("共享动画包缺少明确的来源身份")
            if key in versions:
                raise ValueError(f"资产库存在重复来源: {key}")
            version = getattr(bundle, "source_versions", {}).get(key, {})
            if version and (
                not isinstance(version, dict)
                or any(not isinstance(version.get(name), str) or not version[name]
                       for name in ("content_hash", "settings", "target"))
            ):
                raise ValueError(f"资产来源版本无效: {key}")
            versions[key] = version
    return versions


def plan_source_updates(manifest, skeleton, current, force_rebuild=False):
    previous = get_manifest_source_versions(manifest, skeleton)
    plan = {key: [] for key in ("new", "unchanged", "changed", "unverified")}
    for key, version in current.items():
        status = (
            "new" if key not in previous else
            "unverified" if not previous[key] or not version else
            "unchanged" if previous[key] == version else "changed"
        )
        plan[status].append(key)
    plan["rebuild"] = bool(force_rebuild or plan["changed"] or plan["unverified"])
    if plan["rebuild"] and manifest is not None:
        bundles = [bundle for bundle in manifest.bundles if skeleton in bundle.skeletons]
        if any(bundle.skeletons != [skeleton] for bundle in bundles):
            raise ValueError(f"拒绝替换共享动画包: {skeleton}")
        if not force_rebuild:
            missing = set(previous) - set(current)
            asset_count = sum(asset.skeleton == skeleton for asset in manifest.assets)
            if missing or asset_count != len(previous):
                raise ValueError(
                    f"更新 {skeleton} 需要完整来源集合，旧库保持不变；"
                    f"请扫描完整源目录或确认强制重建。缺少来源: {', '.join(sorted(missing))}"
                )
    return plan


def library_scan_counts(scan, manifest):
    counts = dict.fromkeys(("new", "unchanged", "changed", "unverified"), 0)
    previous = {skeleton: get_manifest_source_versions(manifest, skeleton)
                for skeleton in scan.skeletons}
    for skeleton, folder, name, entry in scan.get_all_animations():
        key = library_source_key(skeleton, entry.source_file, scan.source_path,
                                 f"{skeleton}/{folder or 'Uncategorized'}/{name}")
        versions = previous[skeleton]
        if key not in versions:
            status = "new"
        elif not versions[key]:
            status = "unverified"
        else:
            status = ("unchanged" if source_content_hash(entry.source_file)
                      == versions[key]["content_hash"] else "changed")
        counts[status] += 1
    return counts


def _legacy_tree_actions(tree):
    for skeleton, folders in tree.items():
        stack = [(folders, [])]
        while stack:
            node, path = stack.pop()
            for name, value in node.items():
                if name == "__actions__":
                    for action in value:
                        if isinstance(action, str):
                            yield skeleton, "/".join(path), action, ""
                        else:
                            yield (
                                skeleton, "/".join(path), action[0],
                                action[1] if len(action) > 1 else "",
                            )
                else:
                    stack.append((value, path + [name]))


def migrate_verified_legacy_manifest(library_path, manifest, action_names_loader):
    """Build a complete first-v2 manifest from verified legacy bundle pairs."""
    if manifest.version != "2.0" or manifest.bundles:
        raise ValueError("legacy library inventory migration requires an empty v2 manifest")
    if not callable(action_names_loader):
        raise ValueError("legacy library inventory requires an action inventory loader")

    migrated = AssetLibraryManifest.from_dict(manifest.to_dict())
    prefix = f"{migrated.library_name}_"
    filenames = os.listdir(library_path)
    blend_files = sorted(
        filename for filename in filenames
        if filename.startswith(prefix)
        and filename.lower().endswith(".blend")
        and not filename.lower().endswith("_mesh.blend")
        and not filename.startswith(".")
    )
    expected_sidecars = {
        f"{os.path.splitext(filename)[0]}_manifest.json" for filename in blend_files
    }
    orphan_sidecars = sorted(
        filename for filename in filenames
        if filename.startswith(prefix)
        and filename.endswith("_manifest.json")
        and filename != f"{migrated.library_name}_manifest.json"
        and filename not in expected_sidecars
    )
    if orphan_sidecars:
        raise ValueError(
            "legacy library inventory contains orphan sidecars: "
            + ", ".join(orphan_sidecars)
        )

    catalogs_by_path = {}
    for blend_file in blend_files:
        blend_path = safe_child_path(library_path, blend_file, "legacy bundle filename")
        sidecar_file = f"{os.path.splitext(blend_file)[0]}_manifest.json"
        sidecar_path = safe_child_path(library_path, sidecar_file, "legacy sidecar filename")
        try:
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                sidecar = json.load(handle)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(
                f"legacy library inventory has an incomplete pair: {blend_file}"
            ) from exc
        provenance = _sidecar_action_provenance(sidecar)
        tree = {
            name: value for name, value in sidecar.items()
            if name != "__processed_sources__"
        } if isinstance(sidecar, dict) else {}
        actions = list(_legacy_tree_actions(tree)) if provenance is not None else []
        sidecar_action_names = [action_name for _, _, action_name, _ in actions]
        try:
            actual_action_names = list(action_names_loader(blend_path))
        except Exception as exc:
            raise ValueError(
                f"legacy library inventory cannot read bundle: {blend_file}"
            ) from exc
        if (
            not actions
            or len(sidecar_action_names) != len(set(sidecar_action_names))
            or set(sidecar_action_names) != set(actual_action_names)
        ):
            raise ValueError(
                f"legacy library inventory does not match bundle actions: {blend_file}"
            )

        skeletons = sorted({skeleton for skeleton, _, _, _ in actions})
        processed_sources = sidecar.get("__processed_sources__", [])
        if not isinstance(processed_sources, list) or not all(
            isinstance(source, str) and source for source in processed_sources
        ):
            raise ValueError(
                f"legacy library inventory has invalid processed sources: {sidecar_file}"
            )
        if len(skeletons) == 1:
            processed_sources = [
                source if "::" in source else f"{skeletons[0]}::{source}"
                for source in processed_sources
            ]

        migrated.bundles.append(BundleRecord(
            blend_file=blend_file,
            sidecar_file=sidecar_file,
            skeletons=skeletons,
            processed_sources=processed_sources,
            completion_state="completed",
        ))
        migrated.blend_files.append(blend_file)
        for skeleton in skeletons:
            if skeleton not in migrated.skeletons:
                migrated.skeletons.append(skeleton)
        for skeleton, folder_path, action_name, thumbnail_path in actions:
            catalog_path = f"{skeleton}/{folder_path}" if folder_path else skeleton
            catalog = catalogs_by_path.get(catalog_path)
            if catalog is None:
                catalog = CatalogEntry(
                    uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, catalog_path)),
                    path=catalog_path,
                    skeleton=skeleton,
                    action_count=0,
                )
                catalogs_by_path[catalog_path] = catalog
                migrated.catalog_entries.append(catalog)
            catalog.action_count += 1
            migrated.assets.append(LibraryAsset(
                action_name=action_name,
                catalog_id=catalog.uuid,
                skeleton=skeleton,
                blend_file=blend_file,
                thumbnail_path=thumbnail_path,
            ))
    return migrated


def publish_bundle_pair(temporary_blend, blend_path, sidecar_path, sidecar_payload):
    """Publish a pair, restoring any prior complete pair if replacement fails."""
    temporary_sidecar = _write_json_temp(sidecar_path, sidecar_payload)
    blend_backup = None
    sidecar_backup = None
    preserve_recovery_artifacts = False
    try:
        blend_backup = _backup_file(blend_path) if temporary_blend else None
        sidecar_backup = _backup_file(sidecar_path)
        if temporary_blend:
            _fsync_file(temporary_blend)
            os.replace(temporary_blend, blend_path)
            _fsync_directory(blend_path)
        os.replace(temporary_sidecar, sidecar_path)
        _fsync_directory(sidecar_path)
    except Exception as publish_error:
        # Restore both destinations, including the "did not previously exist" case.
        rollback_error = None
        try:
            if temporary_blend:
                _restore_file_preserving_backup(blend_backup, blend_path)
        except OSError as exc:
            rollback_error = exc
        try:
            _restore_file_preserving_backup(sidecar_backup, sidecar_path)
        except OSError as exc:
            rollback_error = rollback_error or exc
        if rollback_error:
            # The caller's transaction journal and these backups are the only
            # evidence from which a later recovery can restore the prior pair.
            preserve_recovery_artifacts = True
            raise rollback_error from publish_error
        raise
    finally:
        for temporary_path in (temporary_blend, temporary_sidecar, blend_backup, sidecar_backup):
            try:
                if (not preserve_recovery_artifacts and temporary_path
                        and os.path.exists(temporary_path)):
                    os.remove(temporary_path)
                    _fsync_directory(temporary_path)
            except OSError:
                pass


def publish_bundle_pair_and_index(
    temporary_blend, blend_path, sidecar_path, sidecar_payload, bundle_index, bundle_record
):
    """Publish a bundle pair and expose it to an index only after publication."""
    publish_bundle_pair(temporary_blend, blend_path, sidecar_path, sidecar_payload)
    blend_file = getattr(bundle_record, "blend_file", None)
    if blend_file is None and isinstance(bundle_record, dict):
        blend_file = bundle_record.get("blend_file")
    for index, existing_record in enumerate(bundle_index):
        existing_blend = getattr(existing_record, "blend_file", None)
        if existing_blend is None and isinstance(existing_record, dict):
            existing_blend = existing_record.get("blend_file")
        if existing_blend == blend_file:
            bundle_index[index] = bundle_record
            return
    bundle_index.append(bundle_record)


def manifest_with_bundle(manifest, skeleton, catalog_entries, assets, bundle_record, blend_file):
    """Return a merged manifest without mutating the source manifest."""
    merged = AssetLibraryManifest.from_dict(manifest.to_dict())
    if skeleton not in merged.skeletons:
        merged.skeletons.append(skeleton)
    for catalog_entry in catalog_entries:
        existing_catalog = next(
            (entry for entry in merged.catalog_entries if entry.uuid == catalog_entry.uuid), None
        )
        if existing_catalog:
            existing_catalog.action_count += catalog_entry.action_count
        else:
            merged.catalog_entries.append(catalog_entry)
    merged.assets.extend(assets)
    merged.blend_files = list(merged.blend_files)
    if blend_file not in merged.blend_files:
        merged.blend_files.append(blend_file)
    merged.bundles = list(merged.bundles)
    for index, existing in enumerate(merged.bundles):
        if existing.blend_file == blend_file:
            merged.bundles[index] = bundle_record
            break
    else:
        merged.bundles.append(bundle_record)
    return merged


def replace_manifest_skeleton(manifest, skeleton):
    """Return a v2 manifest with exactly one skeleton's old records removed."""
    replaced = AssetLibraryManifest.from_dict(manifest.to_dict())
    replaced.skeletons = [name for name in replaced.skeletons if name != skeleton]
    replaced.catalog_entries = [
        entry for entry in replaced.catalog_entries if entry.skeleton != skeleton
    ]
    replaced.assets = [asset for asset in replaced.assets if asset.skeleton != skeleton]
    replaced.bundles = [
        bundle for bundle in replaced.bundles if skeleton not in bundle.skeletons
    ]
    replaced.blend_files = [bundle.blend_file for bundle in replaced.bundles]
    return replaced


def publish_library_index(index_path, manifest):
    """Atomically publish an index, leaving an existing index untouched on failure."""
    payload = manifest.to_dict() if hasattr(manifest, "to_dict") else manifest
    temporary_path = _write_json_temp(index_path, payload)
    try:
        os.replace(temporary_path, index_path)
        _fsync_directory(index_path)
    finally:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass


def publish_bundle_pair_and_library_index(
    temporary_blend, blend_path, sidecar_path, sidecar_payload,
    index_path, manifest,
):
    """Publish a pair and index as one recoverable filesystem transaction."""
    publish_rebuild_replacement_set(
        index_path,
        [(temporary_blend, blend_path, sidecar_path, sidecar_payload)],
        manifest,
    )


def publish_metadata_repair(index_path, sidecar_replacements, manifest):
    """Publish repaired sidecars and their index under one recoverable journal."""
    publish_rebuild_replacement_set(index_path, sidecar_replacements, manifest)


def publish_rebuild_replacement_set(
    index_path, replacements, manifest, cleanup_paths=(), artifact_paths=()
):
    """Publish all replacement pairs and the index as one recoverable transaction."""
    journal_path = library_transaction_path(index_path)
    if os.path.isfile(journal_path):
        with open(journal_path, "r", encoding="utf-8") as handle:
            transaction = json.load(handle)
        _validate_transaction(index_path, transaction)
        if transaction.get("state") != "prepared":
            recover_library_transaction(index_path)
            transaction = prepare_library_transaction(index_path)
    else:
        transaction = prepare_library_transaction(index_path)
    transaction["manifest"] = manifest.to_dict() if hasattr(manifest, "to_dict") else manifest
    transaction["cleanup_paths"] = list(cleanup_paths)
    try:
        for artifact in artifact_paths:
            if isinstance(artifact, dict):
                path = artifact["path"]
                backup = artifact.get("backup")
                _ensure_backup_durable(backup)
            else:
                path = artifact
                backup = _backup_file(path)
            key = os.path.normcase(os.path.abspath(path))
            if not any(os.path.normcase(os.path.abspath(item["path"])) == key
                       for item in transaction["artifact_paths"]):
                transaction["artifact_paths"].append({"path": path, "backup": backup})
        for temporary_blend, blend_path, sidecar_path, sidecar_payload in replacements:
            key = tuple(_transaction_path_key(path) for path in (
                temporary_blend, blend_path, sidecar_path,
            ))
            if any(
                tuple(_transaction_path_key(item[path_key])
                      for path_key in ("temporary_blend", "blend_path", "sidecar_path")) == key
                for item in transaction["replacements"]
            ):
                continue
            transaction["replacements"].append({
                "temporary_blend": temporary_blend,
                "replace_blend": temporary_blend is not None,
                "blend_path": blend_path,
                "sidecar_path": sidecar_path,
                "sidecar_payload": sidecar_payload,
                "blend_backup": _backup_file(blend_path) if temporary_blend else None,
                "sidecar_backup": _backup_file(sidecar_path),
            })
        _validate_transaction(index_path, transaction)
        _write_transaction(journal_path, transaction)
        for replacement in transaction["replacements"]:
            publish_bundle_pair(
                replacement["temporary_blend"], replacement["blend_path"],
                replacement["sidecar_path"], replacement["sidecar_payload"],
            )
        transaction["state"] = "pairs_published"
        _write_transaction(journal_path, transaction)
        fsync_transaction_artifacts(transaction)
        publish_library_index(index_path, manifest)
        _fsync_directory(index_path)
        transaction["state"] = "index_published"
        _write_transaction(journal_path, transaction)
        try:
            _remove_cleanup_paths(transaction["cleanup_paths"])
        except OSError:
            # The new index is authoritative, but stale files remain scanner-visible.
            # Keep the journal, backups, and cleanup manifest for recovery to retry.
            raise
    except Exception:
        if transaction.get("state") == "index_published":
            raise
        rollback_failed = False
        for replacement in transaction["replacements"]:
            restore_targets = [("sidecar_backup", "sidecar_path")]
            if replacement["replace_blend"]:
                restore_targets.insert(0, ("blend_backup", "blend_path"))
            for backup_key, destination_key in restore_targets:
                try:
                    _restore_file_preserving_backup(
                        replacement[backup_key], replacement[destination_key]
                    )
                except OSError:
                    rollback_failed = True
        try:
            _restore_file_preserving_backup(transaction["index_backup"], index_path)
        except OSError:
            rollback_failed = True
        for artifact in transaction["artifact_paths"]:
            try:
                _restore_file_preserving_backup(artifact["backup"], artifact["path"])
            except OSError:
                rollback_failed = True
        if rollback_failed:
            transaction["state"] = "rollback_failed"
            try:
                _write_transaction(journal_path, transaction)
            except OSError:
                pass
            raise
        _finish_rollback(journal_path, transaction)
        raise
    else:
        _discard_transaction_artifacts(journal_path, transaction)
