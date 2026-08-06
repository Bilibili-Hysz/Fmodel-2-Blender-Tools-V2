import json
import os
from typing import Dict, List, Tuple

from .rules import TEXTURE_EXTENSIONS


_SCAN_CACHE: Dict[str, Tuple[float, Dict[str, List[str]]]] = {}


def _norm_token(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum() or c in ("_", "-"))


def scan_texture_index(base_dir: str) -> Dict[str, List[str]]:
    abs_dir = os.path.normpath(os.path.abspath(base_dir)) if base_dir else ""
    if not abs_dir or not os.path.isdir(abs_dir):
        return {}

    mtime = os.path.getmtime(abs_dir)
    if abs_dir in _SCAN_CACHE and _SCAN_CACHE[abs_dir][0] == mtime:
        return _SCAN_CACHE[abs_dir][1]

    index: Dict[str, List[str]] = {}
    for root, dirs, files in os.walk(abs_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for file_name in files:
            low = file_name.lower()
            if not low.endswith(TEXTURE_EXTENSIONS):
                continue
            stem = os.path.splitext(low)[0]
            token = _norm_token(stem)
            path = os.path.join(root, file_name)
            if token not in index:
                index[token] = []
            index[token].append(path)

    _SCAN_CACHE[abs_dir] = (mtime, index)
    return index


def load_sidecar_metadata(mat_json_path: str) -> Dict:
    if not mat_json_path:
        return {}

    path = os.path.normpath(os.path.abspath(mat_json_path))
    if not os.path.exists(path) or not os.path.isfile(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def extract_metadata_texture_hints(metadata: Dict) -> Dict[str, str]:
    if not isinstance(metadata, dict):
        return {}

    hints: Dict[str, str] = {}

    def key_to_channel(k: str):
        low_key = k.lower()
        if any(tag in low_key for tag in ("base", "albedo", "diffuse", "color")):
            return "base_color"
        if "normal" in low_key:
            return "normal"
        if "rough" in low_key:
            return "roughness"
        if "metal" in low_key:
            return "metallic"
        if "ao" in low_key or "occlusion" in low_key:
            return "ao"
        if "emiss" in low_key:
            return "emissive"
        if "opacity" in low_key or "alpha" in low_key or "mask" in low_key:
            return "opacity"
        if "orm" in low_key or "arm" in low_key:
            return "orm"
        return ""

    def maybe_texture_path(s: str) -> bool:
        low = s.lower().strip()
        if not low:
            return False
        if low.endswith(TEXTURE_EXTENSIONS):
            return True
        return any(x in low for x in ("/textures/", "\\textures\\", "_d", "_n", "_m", "_r", "_ao", "_orm", "_base"))

    def walk(node, key_hint=""):
        if isinstance(node, dict):
            for k, v in node.items():
                ch = key_to_channel(k)
                walk(v, ch or key_hint)
            return

        if isinstance(node, list):
            for v in node:
                walk(v, key_hint)
            return

        if isinstance(node, str) and maybe_texture_path(node):
            if key_hint and key_hint not in hints:
                hints[key_hint] = node

    walk(metadata)
    return hints
