"""Source identity and deterministic in-memory action selection helpers."""

import json
import os


def _normalized_path(path):
    return os.path.normcase(os.path.abspath(path)) if path else ""


def build_source_key(library, blend_path, skeleton, catalog_path, action):
    return json.dumps({
        "library": library or "",
        "blend_path": _normalized_path(blend_path),
        "skeleton": skeleton or "",
        "catalog_path": (catalog_path or "").strip("/"),
        "action": action or "",
    }, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def parse_source_key(source_key):
    if not source_key:
        return {}
    try:
        value = json.loads(source_key)
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return value


def select_existing_action(candidates, action_name, source_key, blend_path):
    """Return only an exact source match, or an unambiguous legacy match."""
    source_matches = [
        candidate for candidate in candidates
        if candidate.get("name") == action_name and source_key
        and candidate.get("source_key") == source_key
    ]
    if source_matches:
        return source_matches[0] if len(source_matches) == 1 else None

    if source_key:
        return None

    legacy_matches = [
        candidate for candidate in candidates
        if candidate.get("name") == action_name
        and (not blend_path or _normalized_path(candidate.get("blend_path")) == _normalized_path(blend_path))
    ]
    return legacy_matches[0] if len(legacy_matches) == 1 else None
