"""Pure filesystem helpers for recovering animation bundle metadata."""

import json
import os
import tempfile
import uuid

try:
    from ...core.library_files import (
        recover_library_transaction, publish_metadata_repair, safe_child_path,
        validate_filename_component,
    )
except ImportError:  # Supports standalone filesystem contract tests.
    from core.library_files import (
        recover_library_transaction, publish_metadata_repair, safe_child_path,
        validate_filename_component,
    )


def _read_json_object(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _is_usable_v2_index(data):
    return (
        isinstance(data, dict)
        and data.get("version") == "2.0"
        and isinstance(data.get("bundles"), list)
        and isinstance(data.get("catalog_entries"), list)
        and isinstance(data.get("assets"), list)
    )


def _library_index_path(abs_dir, library_name, legacy=False):
    """Return a direct-child index path or reject an unsafe library name."""
    validate_filename_component(library_name, "library name")
    suffix = "_manifest.json" if legacy else "__library_manifest.json"
    return safe_child_path(abs_dir, f"{library_name}{suffix}", "library index filename")


def load_library_index(abs_dir, library_name):
    """Load the v2 index first, then the legacy library index."""
    candidates = (
        (_library_index_path(abs_dir, library_name), "v2_index"),
        (_library_index_path(abs_dir, library_name, legacy=True), "legacy_index"),
    )
    for path, level in candidates:
        if level == "v2_index":
            # A published index with retained cleanup work must be recovered
            # before any caller can enumerate stale sidecars beside it.
            recover_library_transaction(path)
        data = _read_json_object(path)
        if level == "v2_index" and not _is_usable_v2_index(data):
            continue
        if data is not None:
            return data, level
    return None, None


def retained_bundle_files(index):
    """Return completed v2 bundle filenames; ``None`` preserves legacy scans."""
    if not _is_usable_v2_index(index):
        return None
    retained = set()
    for bundle in index["bundles"]:
        if not isinstance(bundle, dict) or bundle.get("completion_state") != "completed":
            continue
        blend_file = bundle.get("blend_file")
        sidecar_file = bundle.get("sidecar_file")
        if isinstance(blend_file, str) and blend_file:
            validate_filename_component(blend_file, "bundle filename")
            retained.add(blend_file)
        if isinstance(sidecar_file, str) and sidecar_file:
            validate_filename_component(sidecar_file, "sidecar filename")
    return retained


def _append_action(tree, skeleton, folder_path, action_name, thumbnail_path=""):
    current = tree.setdefault(skeleton or "Unknown Skeleton", {})
    for part in (part for part in folder_path.replace("\\", "/").split("/") if part):
        current = current.setdefault(part, {})
    current.setdefault("__actions__", []).append([action_name, thumbnail_path])


def _is_valid_action(action):
    if isinstance(action, str):
        return bool(action)
    return (
        isinstance(action, list)
        and 1 <= len(action) <= 2
        and isinstance(action[0], str)
        and bool(action[0])
        and (len(action) == 1 or isinstance(action[1], str))
    )


def _is_valid_tree(tree):
    if not isinstance(tree, dict):
        return False
    for name, value in tree.items():
        if not isinstance(name, str):
            return False
        if name == "__actions__":
            if not isinstance(value, list) or not all(_is_valid_action(action) for action in value):
                return False
        elif not _is_valid_tree(value):
            return False
    return True


def _tree_action_names(tree):
    return {action for _, _, action, _ in _iter_tree_actions(tree)}


def _bundle_skeletons(index, blend_file):
    if not isinstance(index, dict):
        return []
    bundles = index.get("bundles", [])
    if not isinstance(bundles, list):
        return []
    for bundle in bundles:
        if isinstance(bundle, dict) and bundle.get("blend_file", bundle.get("blend")) == blend_file:
            skeletons = bundle.get("skeletons", [])
            if not isinstance(skeletons, list) or not all(
                isinstance(name, str) and name for name in skeletons
            ):
                return []
            return skeletons
    return []


def _tree_from_index(index, blend_file):
    if not isinstance(index, dict):
        return {}
    catalog_entries = index.get("catalog_entries", [])
    assets = index.get("assets", [])
    if not isinstance(catalog_entries, list) or not isinstance(assets, list):
        return {}
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("uuid"), str)
        or not item.get("uuid")
        or not isinstance(item.get("path", ""), str)
        or not isinstance(item.get("skeleton", ""), str)
        for item in catalog_entries
    ):
        return {}
    catalogs = {
        item.get("uuid"): item
        for item in catalog_entries
    }
    bundle_skeletons = _bundle_skeletons(index, blend_file)
    tree = {}
    for asset in assets:
        if not isinstance(asset, dict):
            return {}
        if asset.get("blend_file") != blend_file:
            continue
        action_name = asset.get("action_name")
        if (
            not isinstance(action_name, str)
            or not action_name
            or not isinstance(asset.get("catalog_id", ""), str)
            or not isinstance(asset.get("skeleton", ""), str)
            or not isinstance(asset.get("thumbnail_path", ""), str)
        ):
            return {}
        catalog = catalogs.get(asset.get("catalog_id"), {})
        skeleton = asset.get("skeleton") or catalog.get("skeleton")
        path = catalog.get("path", "")
        parts = [part for part in path.replace("\\", "/").split("/") if part]
        if not skeleton and bundle_skeletons:
            skeleton = bundle_skeletons[0]
        if not skeleton and parts:
            skeleton = parts.pop(0)
        elif skeleton and parts and parts[0] == skeleton:
            parts.pop(0)
        _append_action(
            tree,
            skeleton or "Unknown Skeleton",
            "/".join(parts),
            action_name,
            asset.get("thumbnail_path", ""),
        )
    return tree


def load_bundle_tree(blend_path, index, action_names=None, index_level=None):
    """Return ``(tree, recovery_level)`` with same-stem sidecar precedence."""
    blend_file = os.path.basename(blend_path)
    validate_filename_component(blend_file, "bundle filename")
    sidecar_path = os.path.splitext(blend_path)[0] + "_manifest.json"
    validate_filename_component(os.path.basename(sidecar_path), "sidecar filename")
    sidecar = _read_json_object(sidecar_path)
    authoritative_v2 = index_level == "v2_index" or (
        isinstance(index, dict) and index.get("version") == "2.0"
    )
    sidecar_allowed = not authoritative_v2 or _is_authoritative_v2_pair(
        index, blend_path, sidecar_path
    )
    if sidecar is not None and sidecar_allowed:
        tree = {skeleton: folder_tree for skeleton, folder_tree in sidecar.items()
                if skeleton != "__processed_sources__"}
        if (
            tree
            and all(isinstance(skeleton, str) and skeleton for skeleton in tree)
            and _is_valid_tree(tree)
        ):
            if action_names is None or _tree_action_names(tree) == set(action_names):
                return tree, "sidecar"

    tree = _tree_from_index(index, blend_file)
    if tree and (not authoritative_v2 or _is_authoritative_v2_pair(index, blend_path, sidecar_path)):
        return tree, index_level or (
            "v2_index" if str((index or {}).get("version", "")).startswith("2") else "legacy_index"
        )

    skeletons = _bundle_skeletons(index, blend_file)
    if len(skeletons) > 1:
        return {}, "metadata_ambiguous"
    skeletons = skeletons or ["Unknown Skeleton"]
    fallback = {}
    for skeleton in skeletons:
        for action_name in action_names:
            _append_action(fallback, skeleton, "Metadata Missing", action_name)
    return fallback, "metadata_missing"


def _write_json_atomic(path, data):
    """Replace one JSON file atomically without ever writing a blend file."""
    directory = os.path.dirname(path) or "."
    fd, temporary_path = tempfile.mkstemp(prefix=".fmodel-repair-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=4)
        os.replace(temporary_path, path)
    finally:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass


def _sidecar_tree(sidecar):
    if not isinstance(sidecar, dict):
        return None
    tree = {name: value for name, value in sidecar.items() if name != "__processed_sources__"}
    if not tree or not all(isinstance(name, str) and name for name in tree) or not _is_valid_tree(tree):
        return None
    return tree


def _bundle_record(index, blend_file):
    for bundle in (index or {}).get("bundles", []):
        if isinstance(bundle, dict) and bundle.get("blend_file", bundle.get("blend")) == blend_file:
            return bundle
    return None


def _bundle_records(index, blend_file):
    return [
        record for record in (index or {}).get("bundles", [])
        if isinstance(record, dict)
        and record.get("blend_file", record.get("blend")) == blend_file
    ]


def _is_authoritative_v2_pair(index, blend_path, sidecar_path):
    """Require an exact completed v2 bundle pair before exposing metadata."""
    if not _is_usable_v2_index(index):
        return False
    blend_file = os.path.basename(blend_path)
    sidecar_file = os.path.basename(sidecar_path)
    if sidecar_file != f"{os.path.splitext(blend_file)[0]}_manifest.json":
        return False
    return (
        os.path.isfile(blend_path)
        and os.path.isfile(sidecar_path)
        and any(
            isinstance(record, dict)
            and record.get("blend_file") == blend_file
            and record.get("sidecar_file") == sidecar_file
            and record.get("completion_state") == "completed"
            for record in index["bundles"]
        )
    )


def _skeletons_from_tree(tree):
    return [name for name in tree if name and name != "__processed_sources__"]


def _normalized_processed_sources(sidecar, tree):
    sources = sidecar.get("__processed_sources__") if isinstance(sidecar, dict) else None
    if not isinstance(sources, list) or not all(
        isinstance(source, str) and source for source in sources
    ):
        return None
    skeletons = _skeletons_from_tree(tree)
    if len(skeletons) == 1:
        return [
            source if "::" in source else f"{skeletons[0]}::{source}"
            for source in sources
        ]
    return list(sources)


def _merged_processed_sources(records, tree):
    sources = []
    for record in records:
        values = record.get("processed_sources", [])
        if isinstance(values, list) and all(isinstance(value, str) and value for value in values):
            sources.extend(values)
    skeletons = _skeletons_from_tree(tree)
    if not skeletons:
        skeletons = [
            skeleton for record in records
            for skeleton in record.get("skeletons", [])
            if isinstance(skeleton, str) and skeleton
        ]
    if len(set(skeletons)) == 1:
        skeleton = skeletons[0]
        sources = [
            source if "::" in source else f"{skeleton}::{source}"
            for source in sources
        ]
    return sorted(set(sources))


def _repair_bundle_record(blend_file, sidecar_file, tree, processed_sources=None):
    return {
        "blend_file": blend_file,
        "sidecar_file": sidecar_file,
        "skeletons": _skeletons_from_tree(tree),
        "processed_sources": processed_sources or [],
        "completion_state": "completed",
    }


def _repair_existing_bundle_record(
    record, blend_file, sidecar_file, tree, sidecar_processed_sources=None
):
    """Normalize a legacy or partial v2 record from its verified sidecar."""
    repaired = _repair_bundle_record(
        blend_file, sidecar_file, tree, sidecar_processed_sources
    )
    if sidecar_processed_sources is not None:
        return repaired
    processed_sources = record.get("processed_sources", []) if isinstance(record, dict) else []
    if isinstance(processed_sources, list) and all(isinstance(value, str) for value in processed_sources):
        repaired["processed_sources"] = processed_sources
    return repaired


def _iter_tree_actions(tree):
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
                            yield skeleton, "/".join(path), action[0], action[1] if len(action) > 1 else ""
                else:
                    stack.append((value, path + [name]))


def _tree_has_actions(tree):
    return any(True for _ in _iter_tree_actions(tree))


def _recover_index_metadata(index, blend_file, tree):
    """Fill absent v2 asset records from a verified sidecar without losing metadata."""
    catalogs = index["catalog_entries"]
    assets = index["assets"]
    changed = False
    for skeleton, folder_path, action_name, thumbnail_path in _iter_tree_actions(tree):
        catalog_path = f"{skeleton}/{folder_path}" if folder_path else skeleton
        catalog = next((item for item in catalogs if item.get("path") == catalog_path), None)
        if catalog is None:
            catalog = {
                "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, catalog_path)),
                "path": catalog_path,
                "skeleton": skeleton,
                "action_count": 0,
            }
            catalogs.append(catalog)
            changed = True
        elif not isinstance(catalog.get("uuid"), str) or not catalog.get("uuid"):
            catalog["uuid"] = str(uuid.uuid5(uuid.NAMESPACE_URL, catalog_path))
            changed = True
        if not isinstance(catalog.get("skeleton"), str) or not catalog.get("skeleton"):
            catalog["skeleton"] = skeleton
            changed = True
        exists = any(
            asset.get("blend_file") == blend_file
            and asset.get("action_name") == action_name
            and asset.get("catalog_id") == catalog.get("uuid")
            for asset in assets
        )
        if exists:
            continue
        assets.append({
            "action_name": action_name,
            "catalog_id": catalog["uuid"],
            "skeleton": skeleton,
            "blend_file": blend_file,
            "thumbnail_path": thumbnail_path,
        })
        changed = True
        recovered_count = sum(
            asset.get("catalog_id") == catalog["uuid"] for asset in assets
        )
        old_count = catalog.get("action_count", 0)
        try:
            old_count = int(old_count)
        except (TypeError, ValueError):
            old_count = 0
        new_count = max(old_count, recovered_count)
        if catalog.get("action_count") != new_count:
            catalog["action_count"] = new_count
            changed = True
    return changed


def _normalize_repaired_metadata(index, recovered_trees):
    """Rebuild the index's derived metadata from verified bundle sidecars."""
    catalogs_by_path = {}
    used_uuids = set()
    for catalog in index["catalog_entries"]:
        if not isinstance(catalog, dict) or not isinstance(catalog.get("path"), str):
            continue
        catalog = dict(catalog)
        path = catalog["path"]
        if path in catalogs_by_path:
            continue
        catalog_uuid = catalog.get("uuid")
        if not isinstance(catalog_uuid, str) or not catalog_uuid or catalog_uuid in used_uuids:
            catalog_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, path))
            suffix = 0
            while catalog_uuid in used_uuids:
                suffix += 1
                catalog_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}#{suffix}"))
            catalog["uuid"] = catalog_uuid
        used_uuids.add(catalog_uuid)
        catalogs_by_path[path] = catalog
    assets = []
    for blend_file, tree in sorted(recovered_trees.items()):
        for skeleton, folder_path, action_name, thumbnail_path in _iter_tree_actions(tree):
            path = f"{skeleton}/{folder_path}" if folder_path else skeleton
            catalog = catalogs_by_path.get(path)
            if catalog is None:
                catalog = {"uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, path)), "path": path}
                catalogs_by_path[path] = catalog
            catalog["skeleton"] = skeleton
            assets.append({
                "action_name": action_name, "catalog_id": catalog["uuid"],
                "skeleton": skeleton, "blend_file": blend_file,
                "thumbnail_path": thumbnail_path,
            })
    deduped_assets = []
    seen_assets = set()
    for asset in assets:
        key = (asset["blend_file"], asset["catalog_id"], asset["action_name"])
        if key not in seen_assets:
            seen_assets.add(key)
            deduped_assets.append(asset)
    used_catalogs = {asset["catalog_id"] for asset in deduped_assets}
    catalogs = [catalog for catalog in catalogs_by_path.values() if catalog.get("uuid") in used_catalogs]
    for catalog in catalogs:
        catalog["action_count"] = sum(asset["catalog_id"] == catalog["uuid"] for asset in deduped_assets)
    index["catalog_entries"] = sorted(catalogs, key=lambda item: item["path"])
    index["assets"] = sorted(deduped_assets, key=lambda item: (item["blend_file"], item["catalog_id"], item["action_name"]))


def repair_library_metadata(abs_dir, library_name, action_names_loader=None):
    """Explicitly restore missing animation sidecars and v2 bundle records.

    Existing valid sidecars are authoritative and never rewritten.  This helper
    deliberately only reads ``.blend`` files through ``action_names_loader``;
    every mutation is an atomic JSON replacement.
    """
    if not os.path.isdir(abs_dir):
        raise ValueError("library directory does not exist")

    index_path = _library_index_path(abs_dir, library_name)
    # Never repair against a half-published v2 state or fall back around it.
    recover_library_transaction(index_path)
    index = _read_json_object(index_path)
    usable_v2_index = _is_usable_v2_index(index)
    declared_blends = None
    if usable_v2_index:
        for record in index["bundles"]:
            if not isinstance(record, dict):
                continue
            blend_file = record.get("blend_file")
            sidecar_file = record.get("sidecar_file")
            if isinstance(blend_file, str) and blend_file:
                validate_filename_component(blend_file, "bundle filename")
            if isinstance(sidecar_file, str) and sidecar_file:
                validate_filename_component(sidecar_file, "sidecar filename")
        declared_blends = {
            record.get("blend_file")
            for record in index["bundles"]
            if isinstance(record, dict)
            and isinstance(record.get("blend_file"), str)
            and record.get("blend_file")
        }
        for filename in declared_blends:
            safe_child_path(abs_dir, filename, "bundle filename")
        missing_declared = sorted(
            filename for filename in declared_blends
            if not os.path.isfile(safe_child_path(abs_dir, filename, "bundle filename"))
        )
        if missing_declared:
            raise OSError(
                "declared bundle is unavailable: " + ", ".join(missing_declared)
            )
    if not usable_v2_index:
        partial = index if isinstance(index, dict) and index.get("version") == "2.0" else {}
        index = {
            "version": "2.0",
            "library_name": partial.get("library_name", library_name),
            "library_path": partial.get("library_path", abs_dir),
            "skeletons": partial.get("skeletons", []) if isinstance(partial.get("skeletons", []), list) else [],
            "blend_files": partial.get("blend_files", []) if isinstance(partial.get("blend_files", []), list) else [],
            "catalog_entries": [item for item in partial.get("catalog_entries", []) if isinstance(item, dict)]
            if isinstance(partial.get("catalog_entries", []), list) else [],
            "assets": [item for item in partial.get("assets", []) if isinstance(item, dict)]
            if isinstance(partial.get("assets", []), list) else [],
            "bundles": [item for item in partial.get("bundles", []) if isinstance(item, dict)]
            if isinstance(partial.get("bundles", []), list) else [],
        }
        if isinstance(partial.get("library_id"), str) and partial["library_id"]:
            index["library_id"] = partial["library_id"]
    index["library_path"] = os.path.abspath(abs_dir)
    index.setdefault("skeletons", [])
    index.setdefault("blend_files", [])

    sidecars_created = 0
    index_records_added = 0
    index_changed = False
    if not isinstance(index.get("library_id"), str) or not index["library_id"]:
        index["library_id"] = str(
            uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(abs_dir))
        )
        index_changed = True
    original_derived_metadata = json.dumps({
        "catalog_entries": index["catalog_entries"], "assets": index["assets"],
        "blend_files": index["blend_files"], "skeletons": index["skeletons"],
    }, sort_keys=True)
    sidecar_replacements = []
    recovered_trees = {}
    unresolved_blends = set()
    for filename in sorted(os.listdir(abs_dir)):
        if not filename.lower().endswith(".blend") or filename.endswith("_mesh.blend"):
            continue
        if declared_blends is not None and filename not in declared_blends:
            continue

        blend_path = safe_child_path(abs_dir, filename, "bundle filename")
        sidecar_file = os.path.splitext(filename)[0] + "_manifest.json"
        sidecar_path = safe_child_path(abs_dir, sidecar_file, "sidecar filename")
        sidecar = _read_json_object(sidecar_path)
        tree = _sidecar_tree(sidecar)
        if tree is not None and action_names_loader is not None:
            actual_actions = set(action_names_loader(blend_path))
            if _tree_action_names(tree) != actual_actions:
                tree = None
        sidecar_processed_sources = _normalized_processed_sources(sidecar, tree) if tree else None
        records = _bundle_records(index, filename)
        if tree is None:
            # A completed v2 record with an exact sidecar declaration is
            # authoritative about its pair; do not resurrect it from its own
            # derived asset rows when that sidecar is missing or invalid.
            authoritative_record = any(
                record.get("completion_state") == "completed"
                and record.get("sidecar_file") == sidecar_file
                for record in records
            )
            tree = {} if authoritative_record else _tree_from_index(index, filename)
            index_tree_rejected = False
            if tree:
                indexed_actions = {action for _, _, action, _ in _iter_tree_actions(tree)}
                actual_actions = set(action_names_loader(blend_path)) if action_names_loader else set()
                if actual_actions != indexed_actions:
                    tree = {}
                    index_tree_rejected = True
            if not tree and not index_tree_rejected:
                action_names = action_names_loader(blend_path) if action_names_loader else []
                tree, _ = load_bundle_tree(blend_path, index, action_names, "v2_index")
            if not _tree_has_actions(tree):
                if records:
                    if any(len(record.get("skeletons", [])) > 1 for record in records):
                        for record in records:
                            record["completion_state"] = "unresolved"
                        unresolved_blends.add(filename)
                        index_changed = True
                        continue
                    # A bundle with no verified sidecar or recoverable actions is
                    # not publishable; remove its derived records in the same
                    # index repair transaction.
                    index["bundles"] = [
                        record for record in index["bundles"] if record not in records
                    ]
                    index["assets"] = [
                        asset for asset in index["assets"]
                        if asset.get("blend_file") != filename
                    ]
                    index_changed = True
                continue
            processed_sources = _merged_processed_sources(records, tree)
            sidecar_payload = dict(tree)
            if processed_sources:
                sidecar_payload["__processed_sources__"] = processed_sources
                sidecar_processed_sources = processed_sources
            sidecar_replacements.append((None, blend_path, sidecar_path, sidecar_payload))
            sidecars_created += 1

        if _recover_index_metadata(index, filename, tree):
            index_changed = True

        if not records:
            index["bundles"].append(_repair_bundle_record(
                filename, sidecar_file, tree, sidecar_processed_sources
            ))
            index_records_added += 1
            index_changed = True
        else:
            merged_sources = sidecar_processed_sources
            if merged_sources is None:
                merged_sources = _merged_processed_sources(records, tree)
            repaired_record = _repair_existing_bundle_record(
                records[0], filename, sidecar_file, tree, merged_sources
            )
            first_index = index["bundles"].index(records[0])
            if records != [repaired_record]:
                index["bundles"] = [
                    record for record in index["bundles"]
                    if record not in records
                ]
                index["bundles"].insert(first_index, repaired_record)
                index_changed = True

        if filename not in index["blend_files"]:
            index["blend_files"].append(filename)
            index_changed = True
        for skeleton in _skeletons_from_tree(tree):
            if skeleton not in index["skeletons"]:
                index["skeletons"].append(skeleton)
                index_changed = True
        recovered_trees[filename] = tree

    unresolved_assets = [
        asset for asset in index["assets"]
        if isinstance(asset, dict) and asset.get("blend_file") in unresolved_blends
    ]
    unresolved_catalog_ids = {
        asset.get("catalog_id") for asset in unresolved_assets
        if isinstance(asset.get("catalog_id"), str) and asset.get("catalog_id")
    }
    unresolved_skeletons = {
        skeleton for record in index["bundles"]
        if isinstance(record, dict) and record.get("blend_file") in unresolved_blends
        for skeleton in record.get("skeletons", [])
        if isinstance(skeleton, str) and skeleton
    }
    unresolved_catalogs = [
        catalog for catalog in index["catalog_entries"]
        if isinstance(catalog, dict) and (
            catalog.get("uuid") in unresolved_catalog_ids
            or catalog.get("skeleton") in unresolved_skeletons
        )
    ]
    _normalize_repaired_metadata(index, recovered_trees)
    preserved_catalog_ids = {catalog.get("uuid") for catalog in unresolved_catalogs}
    index["catalog_entries"] = [
        catalog for catalog in index["catalog_entries"]
        if catalog.get("uuid") not in preserved_catalog_ids
    ] + unresolved_catalogs
    index["assets"].extend(unresolved_assets)
    index["blend_files"] = sorted(set(recovered_trees) | unresolved_blends)
    index["skeletons"] = sorted(
        {skeleton for tree in recovered_trees.values() for skeleton in _skeletons_from_tree(tree)}
        | unresolved_skeletons
    )
    index_changed = index_changed or original_derived_metadata != json.dumps({
        "catalog_entries": index["catalog_entries"], "assets": index["assets"],
        "blend_files": index["blend_files"], "skeletons": index["skeletons"],
    }, sort_keys=True)
    if index_changed or sidecar_replacements or not os.path.exists(index_path):
        publish_metadata_repair(index_path, sidecar_replacements, index)
    return {"sidecars_created": sidecars_created, "index_records_added": index_records_added}


# Names used by the Blender scanner and source-level contract tests.
_load_library_index = load_library_index
_load_bundle_tree = load_bundle_tree
