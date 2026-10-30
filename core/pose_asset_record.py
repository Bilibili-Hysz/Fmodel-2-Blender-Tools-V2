"""Portable source records for poses; Blender output is always derived data."""

import base64
import hashlib
import json
import math
import os

try:
    from .resource_limits import enforce_limit
except ImportError:  # Pure module probes load this file without a package.
    import importlib.util
    from pathlib import Path
    _spec = importlib.util.spec_from_file_location("_pose_resource_limits", Path(__file__).with_name("resource_limits.py"))
    _limits = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_limits)
    enforce_limit = _limits.enforce_limit

SCHEMA = 1
CHUNKED_SCHEMA = 2
BASE64_CHUNK_CHARS = 4096
CHUNKED_SOURCE_THRESHOLD = 1024 * 1024
CONVENTIONS = {"CUE4PARSE_ADDITIVE", "UNSPECIFIED"}


def make_record(data, source, name, object_path, scale, convention):
    if not data:
        raise ValueError("姿势文件为空")
    enforce_limit(len(data), "MAX_BYTES", "姿势源数据")
    identity = object_path or os.path.normcase(os.path.abspath(source))
    record = {
        "schema": SCHEMA, "kind": "uepose", "name": name,
        "asset_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "source": source, "object_path": object_path,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_byte_length": len(data),
        "source_base64": base64.b64encode(data).decode("ascii"),
        "scale": scale, "convention": convention,
    }
    if len(data) > CHUNKED_SOURCE_THRESHOLD:
        encoded = record.pop("source_base64")
        record["schema"] = CHUNKED_SCHEMA
        record["source_base64_chunks"] = [encoded[start:start + BASE64_CHUNK_CHARS] for start in range(0, len(encoded), BASE64_CHUNK_CHARS)]
    decode_record(record)
    return record


def decode_record(record):
    if not isinstance(record, dict) or record.get("schema") not in {SCHEMA, CHUNKED_SCHEMA} or record.get("kind") != "uepose":
        raise ValueError("不支持的姿势资产记录")
    if record.get("convention") not in CONVENTIONS:
        raise ValueError("未知的姿势变换约定")
    scale = record.get("scale")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or not 0 < scale <= 100:
        raise ValueError("无效的姿势缩放")
    for key in ("name", "source", "object_path", "asset_id", "source_sha256"):
        if not isinstance(record.get(key), str):
            raise ValueError(f"姿势资产字段无效: {key}")
    if record["schema"] == CHUNKED_SCHEMA:
        chunks = record.get("source_base64_chunks")
        if not isinstance(chunks, list) or not chunks or any(not isinstance(chunk, str) or not 0 < len(chunk) <= BASE64_CHUNK_CHARS for chunk in chunks):
            raise ValueError("姿势源数据分块无效")
        enforce_limit(sum(map(len, chunks)) // 4 * 3 - min(2, len(chunks[-1]) - len(chunks[-1].rstrip('='))), "MAX_BYTES", "姿势源数据")
        encoded = ''.join(chunks)
    else:
        encoded = record.get("source_base64")
        if not isinstance(encoded, str):
            raise ValueError("姿势资产字段无效: source_base64")
    padding = len(encoded) - len(encoded.rstrip('='))
    enforce_limit(max(0, len(encoded) // 4 * 3 - padding), "MAX_BYTES", "姿势源数据")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("姿势源数据编码无效") from exc
    if not data or hashlib.sha256(data).hexdigest() != record["source_sha256"]:
        raise ValueError("姿势源数据摘要不匹配")
    enforce_limit(len(data), "MAX_BYTES", "姿势源数据")
    if "source_byte_length" in record and (type(record["source_byte_length"]) is not int or record["source_byte_length"] != len(data)):
        raise ValueError("姿势源数据长度不匹配")
    identity = record["object_path"] or os.path.normcase(os.path.abspath(record["source"]))
    if hashlib.sha256(identity.encode("utf-8")).hexdigest() != record["asset_id"]:
        raise ValueError("姿势来源标识不匹配")
    return data


def dumps(record):
    decode_record(record)
    # Short lines avoid Blender Text's expensive handling of one enormous
    # base64 line, while retaining a portable, byte-exact embedded source.
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                      indent=0 if record["schema"] == CHUNKED_SCHEMA else None, allow_nan=False)


def pose_entry_id(name, occurrence=0):
    return hashlib.sha256(f"{name}\0{occurrence}".encode("utf-8")).hexdigest()
