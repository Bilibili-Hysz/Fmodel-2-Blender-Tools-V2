# FModel_Tools/scripts/make_v10_full_fixture.py
# 生成带真实几何体、骨架与 FModel 身份的 V10 全量固定样本。
#
# 现有 tests/fixtures/fmodel_v10/*.uemodel 是身份契约专用样本（LODS 为空数组），
# 无法驱动 importer 的建网/建骨架路径。本脚本按 vendor 反序列化层的字节契约
# （io_scene_ueformat/importer/wire.py、archive/reader.py、deserialize/）生成一个
# 完整但最小的骨骼网格文件，供 scripts/blender_smoke_v10.py 的无头 Blender
# 全链路冒烟验证消费。字节为确定性输出（无时间戳、无随机数）。
#
# 契约要点（全部来自 vendor 读取器实现，勿凭记忆修改）：
# - FString：<i32 字节长度> + UTF-8 字节，无终止符（archive/reader.py read_fstring）
# - 头部：MAGIC(8B "UEFORMAT") + fstring(identifier) + u8 version + fstring(name)
#   + fstring(objectPath) + u8 compressed（wire.py parse_header）
# - 属性块：i32 count + 每个 [fstring(name)][i32 size][payload]，
#   FMODEL_* 的 payload 外包 [u8 envelope_version=1][i32 array_size=1]
# - LODS：serialized array；LOD = fstring("LOD0") + 嵌套属性块
# - 身份 payload：[u8 ver=1][fstring skeleton_path][u8 has_guid][32B layout hash]

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "tests", "fixtures", "fmodel_v10", "fmodel_v10_full.uemodel")
READER = os.path.join(REPO, "core", "ueformat_identity_reader.py")

MAGIC = b"UEFORMAT"
IDENTIFIER = "UEMODEL"
FILE_VERSION = 10
OBJECT_NAME = "SK_FModelV10Full"
OBJECT_PATH = "/Game/Characters/FModelV10Full.SK_FModelV10Full"
SKELETON_PATH = "/Game/Characters/FModelV10Full_SKEL.FModelV10Full_SKEL"


def fstring(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def i32(value: int) -> bytes:
    return struct.pack("<i", value)


def f32(*values: float) -> bytes:
    return struct.pack("<%df" % len(values), *values)


def attribute(name: str, payload: bytes, *, enveloped: bool = False) -> bytes:
    if enveloped:
        payload = bytes([1]) + i32(1) + payload
    return fstring(name) + i32(len(payload)) + payload


def attribute_block(framed: list[bytes]) -> bytes:
    return i32(len(framed)) + b"".join(framed)


def lod_payload() -> bytes:
    vertices = attribute("VERTICES", i32(4) + f32(
        0.0, 0.0, 0.0,
        1.0, 0.0, 0.0,
        1.0, 1.0, 0.0,
        0.0, 1.0, 0.0,
    ))
    texcoords = attribute("TEXCOORDS", i32(1) + fstring("") + i32(4) + f32(
        0.0, 0.0,
        1.0, 0.0,
        1.0, 1.0,
        0.0, 1.0,
    ))
    indices = attribute("INDICES", i32(6) + struct.pack("<6I", 0, 1, 2, 0, 2, 3))
    materials = attribute("MATERIALS", i32(1) + fstring("M_FModelV10Full")
                          + fstring("/Game/Characters/M_FModelV10Full.M_FModelV10Full")
                          + i32(0) + i32(2))
    weights = attribute("WEIGHTS", i32(4) + b"".join(
        struct.pack("<Hif", bone, vertex, 1.0)
        for bone, vertex in ((0, 0), (0, 1), (1, 2), (1, 3))
    ))
    lod_body = fstring("LOD0") + attribute_block(
        [vertices, texcoords, indices, materials, weights])
    return i32(1) + lod_body


def skeleton_payload() -> bytes:
    metadata = attribute("METADATA", fstring(SKELETON_PATH))
    bones = attribute("BONES", i32(2)
                      + fstring("root") + i32(-1)
                      + f32(0.0, 0.0, 0.0) + f32(0.0, 0.0, 0.0, 1.0) + f32(1.0, 1.0, 1.0)
                      + fstring("child") + i32(0)
                      + f32(0.0, 0.0, 1.0) + f32(0.0, 0.0, 0.0, 1.0) + f32(1.0, 1.0, 1.0))
    sockets = attribute("SOCKETS", i32(0))
    virtual_bones = attribute("VIRTUALBONES", i32(0))
    return attribute_block([metadata, bones, sockets, virtual_bones])


def identity_payload() -> bytes:
    layout_hash = hashlib.sha256(
        b"FModelV10Full:root>child:quad").digest()
    return (bytes([1])
            + fstring(SKELETON_PATH)
            + bytes([0])
            + layout_hash)


def build() -> bytes:
    body = (attribute_block([
        attribute("LODS", lod_payload()),
        attribute("SKELETON", skeleton_payload()),
        attribute("FMODEL_SKELETON_IDENTITY", identity_payload(), enveloped=True),
    ]))
    return (MAGIC
            + fstring(IDENTIFIER)
            + bytes([FILE_VERSION])
            + fstring(OBJECT_NAME)
            + fstring(OBJECT_PATH)
            + bytes([0])
            + body)


def self_check(path: str) -> dict:
    import importlib.util

    spec = importlib.util.spec_from_file_location("fixture_reader_check", READER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # core/ueformat_identity_reader.py 兜底按裸模块导入同级 skeleton_provenance。
    sys.path.insert(0, os.path.dirname(READER))
    spec.loader.exec_module(module)
    record = module.read_ueformat_identity(path)
    layout_hash = identity_payload()[-32:].hex()
    expected = {
        "kind": "model",
        "file_version": FILE_VERSION,
        "object_name": OBJECT_NAME,
        "skeleton_path": SKELETON_PATH,
        "bone_layout_hash": layout_hash,
        "has_strict_identity": True,
    }
    actual = {
        "kind": record.kind,
        "file_version": record.file_version,
        "object_name": record.object_name,
        "skeleton_path": record.identity["skeleton_path"],
        "bone_layout_hash": record.identity["bone_layout_hash"],
        "has_strict_identity": record.has_strict_identity,
    }
    if actual != expected:
        raise SystemExit(f"自检失败:\n  expected={expected}\n  actual={actual}")
    return actual


def main() -> None:
    data = build()
    with open(FIXTURE, "wb") as handle:
        handle.write(data)
    actual = self_check(FIXTURE)
    print(json.dumps({
        "fixture": FIXTURE,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "record": actual,
    }, indent=2))


if __name__ == "__main__":
    sys.exit(main())
