# FModel_Tools/scripts/blender_smoke_v10.py
# P0 冒烟验证：无头 Blender 消费 V10 固定样本
#
# 验证目标（对应 HANDOFF "REAL_ASSET_GUI_E2E_PENDING" 中可自动化的部分）：
#   1. repo 内 vendor io_scene_ueformat 0.10.0 能在无头 Blender 中加载并导入 V10 文件；
#   2. 全量样本（含几何体+骨架）导入产生有效的网格与骨架对象，骨架对象携带
#      与文件内 FMODEL_SKELETON_IDENTITY 一致的 fmodel_skeleton_identity；
#   3. V2 纯 Python 身份读取器对同一字节读出的身份与规范记录一致
#      （canonical 样本锁定 skeleton_path + bone_layout_hash）。
#
# 说明：tests/fixtures/fmodel_v10/fmodel_v10_{none,gzip,zstd}.uemodel 是身份契约
# 专用样本（LODS 为空数组），importer 不创建对象是正确行为；对它们只做读取与
# 无崩溃导入验证。全链路对象断言使用 fmodel_v10_full.uemodel
# （由 scripts/make_v10_full_fixture.py 确定性生成）。
#
# 运行（Git Bash）：
#   "F:/Blender/Software/blender-5.2.1-windows-x64/blender.exe" \
#     --background --factory-startup \
#     --python "F:/Blender Scripts Github/FModel-2-Blender-Tools-V2/scripts/blender_smoke_v10.py" -- \
#     --repo "F:/Blender Scripts Github/FModel-2-Blender-Tools-V2" \
#     --out "%TEMP%/fmodel_smoke_v10_result.json"
#
# 结果同时写入 --out JSON（Blender 不总是传播脚本的退出码，以 JSON 为准）。

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import traceback

CANONICAL_IDENTITY_FIXTURES = {
    "fmodel_v10_none.uemodel",
    "fmodel_v10_gzip.uemodel",
    "fmodel_v10_zstd.uemodel",
}
EXPECTED_CANONICAL = {
    "object_name": "SM_FModelV10Contract",
    "file_version": 10,
    "skeleton_path": "/CodeWorld/Content/Characters/Heroes/SKEL_FM_Base.SKEL_FM_Base",
    "bone_layout_hash": "d931be2b178d85f8477bb1671eaf1344133440ff6411140c1b09b579afe7d850",
}
MIN_VENDOR_VERSION = (0, 10, 0)


def _parse_args(argv):
    repo_default = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=repo_default)
    parser.add_argument(
        "--fixture",
        default="tests/fixtures/fmodel_v10/fmodel_v10_full.uemodel",
    )
    parser.add_argument("--out", default=os.path.join(
        tempfile.gettempdir(), "fmodel_smoke_v10_result.json"))
    return parser.parse_args(argv)


def _load_reader(reader_path):
    spec = importlib.util.spec_from_file_location(
        "fmodel_v2_identity_reader_smoke", reader_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, os.path.dirname(reader_path))
    spec.loader.exec_module(module)
    return module


def main():
    args = _parse_args(sys.argv[sys.argv.index("--") + 1:]
                       if "--" in sys.argv else [])
    repo = os.path.abspath(args.repo)
    fixture = os.path.abspath(os.path.join(repo, args.fixture)
                              if not os.path.isabs(args.fixture) else args.fixture)
    reader_path = os.path.join(repo, "core", "ueformat_identity_reader.py")
    require_objects = os.path.basename(fixture) not in CANONICAL_IDENTITY_FIXTURES

    result = {
        "status": "fail",
        "fixture": fixture,
        "require_objects": require_objects,
        "blender_version": None,
        "vendor_version": None,
        "registered": False,
        "import_returned": None,
        "new_objects": [],
        "new_materials": [],
        "mesh_summary": [],
        "armature_identity": None,
        "armature_identity_source": None,
        "reader_record": None,
        "errors": [],
    }

    def finish(code):
        result["status"] = "pass" if code == 0 else "fail"
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
        except OSError as error:
            print(f"[smoke] 无法写入结果文件 {args.out}: {error}")
        print("[smoke] RESULT_JSON=" + json.dumps(result, ensure_ascii=False))
        sys.exit(code)

    errors = result["errors"]

    def check(condition, message):
        if not condition:
            errors.append(message)
        return condition

    try:
        import bpy

        result["blender_version"] = tuple(bpy.app.version)

        sys.path.insert(0, os.path.join(repo, "vendor"))
        import io_scene_ueformat as vendor

        result["vendor_version"] = tuple(vendor.bl_info.get("version", ()))
        if result["vendor_version"] < MIN_VENDOR_VERSION:
            errors.append(
                f"vendor 版本过低: {result['vendor_version']} < {MIN_VENDOR_VERSION}")
            finish(1)

        try:
            vendor.register()
            result["registered"] = True
        except Exception as error:  # 注册失败不阻断：logic 层导入不依赖面板
            errors.append(f"vendor.register() 警告: {error}")
            traceback.print_exc()

        from io_scene_ueformat.importer.logic import UEFormatImport
        from io_scene_ueformat.options import UEModelOptions

        reader = _load_reader(reader_path)
        record = reader.read_ueformat_identity(fixture)
        reader_view = {
            "kind": record.kind,
            "file_version": record.file_version,
            "object_name": record.object_name,
            "skeleton_path": record.identity["skeleton_path"],
            "bone_layout_hash": record.identity["bone_layout_hash"],
            "has_strict_identity": record.has_strict_identity,
        }
        result["reader_record"] = reader_view

        check(reader_view["kind"] == "model", f"样本种类异常: {reader_view['kind']}")
        check(reader_view["file_version"] == 10,
              f"样本版本异常: {reader_view['file_version']}")
        check(reader_view["has_strict_identity"], "样本缺少严格骨架身份")
        if os.path.basename(fixture) == "fmodel_v10_none.uemodel":
            for key, expected_value in EXPECTED_CANONICAL.items():
                check(reader_view.get(key) == expected_value,
                      f"canonical {key} 与规范值不一致: "
                      f"{reader_view.get(key)!r} != {expected_value!r}")

        options = UEModelOptions(scale_factor=0.01, enable_fmodel_extensions=True)
        importer = UEFormatImport(options)

        before_objects = set(bpy.data.objects.keys())
        before_materials = set(bpy.data.materials.keys())
        created = importer.import_file(fixture)
        result["import_returned"] = getattr(created, "name", repr(created))

        new_objects = [
            name for name in bpy.data.objects.keys() if name not in before_objects
        ]
        new_materials = [
            name for name in bpy.data.materials.keys() if name not in before_materials
        ]
        result["new_objects"] = new_objects
        result["new_materials"] = new_materials

        if require_objects:
            meshes = []
            armatures = []
            for name in new_objects:
                obj = bpy.data.objects.get(name)
                if obj is None or obj.data is None:
                    errors.append(f"新对象无效: {name}")
                    continue
                if obj.type == "MESH":
                    meshes.append(obj)
                elif obj.type == "ARMATURE":
                    armatures.append(obj)

            check(bool(meshes), "未创建任何网格对象")
            check(bool(armatures), "未创建任何骨架对象")
            if meshes:
                result["mesh_summary"] = [{
                    "name": mesh.name,
                    "vertices": len(mesh.data.vertices),
                    "material_slots": len(mesh.material_slots),
                    "materials": [slot.material.name if slot.material else None
                                  for slot in mesh.material_slots],
                } for mesh in meshes]
                check(any(
                    name.startswith(record.object_name) for name in new_objects
                ), f"新对象均不是期望的 {record.object_name}: {new_objects}")
                check(all(
                    len(mesh.data.vertices) > 0 for mesh in meshes
                ), "网格顶点数为 0")
                check(all(
                    len(mesh.material_slots) >= 1 for mesh in meshes
                ), "网格没有材质槽")
            if armatures:
                armature = armatures[0]
                identity_raw = armature.get("fmodel_skeleton_identity")
                result["armature_identity_source"] = armature.get(
                    "fmodel_skeleton_identity_source")
                if identity_raw:
                    try:
                        identity = json.loads(identity_raw)
                    except (TypeError, ValueError) as error:
                        errors.append(f"骨架身份 JSON 无效: {error}")
                    else:
                        result["armature_identity"] = identity
                        check(
                            identity.get("skeleton_path")
                            == reader_view["skeleton_path"],
                            "骨架身份 skeleton_path 与读取器记录不一致",
                        )
                        check(
                            identity.get("bone_layout_hash")
                            == reader_view["bone_layout_hash"],
                            "骨架身份 bone_layout_hash 与读取器记录不一致",
                        )
                        check(len(armature.data.bones) > 0, "骨架没有骨骼")
                else:
                    errors.append("骨架对象缺少 fmodel_skeleton_identity")
        else:
            print("[smoke] 身份专用样本：只验证读取与无崩溃导入")

        finish(0 if not errors else 1)

    except Exception as error:
        traceback.print_exc()
        errors.append(f"{type(error).__name__}: {error}")
        finish(1)


if __name__ == "__main__":
    main()
