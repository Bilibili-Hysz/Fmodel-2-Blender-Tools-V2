"""Pose source storage and reversible Blender applications, using Vendor parsing."""

import importlib
import json
from contextlib import contextmanager
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

from ....core.pose_asset_record import (
    decode_record, dumps, make_record, pose_entry_id,
)
from ....core.resource_limits import enforce_limit
from ....core.pipeline_activity import pipeline_activity
from ....core.skeleton_compatibility import normalize_skeleton_compatibility_mode
from ....core.ueformat_identity_reader import read_ueformat_identity_bytes
from ....core.utils import get_ueformat_bridge

TAG = "FModel_PoseSource"
BAKES = "FModel_PoseBakes"


def vendor_module(name):
    bridge = get_ueformat_bridge(require_current=True, allow_legacy=False, capability="pose")
    if bridge is None:
        raise ValueError("UEFormat 姿势解析器不可用")
    return importlib.import_module(bridge.module_name + ".importer." + name)


def parse_source(data):
    # Keep wire/DTO interpretation in Vendor. V2 only stores and applies it.
    wire = vendor_module("wire")
    reader = vendor_module("archive.reader").FArchiveReader
    with reader(data) as archive:
        header, body = wire.parse_header(archive)
        if header.identifier != "UEPOSE":
            raise ValueError("所选资源不是 UEPOSE")
        dto = wire.parse_body(header, body)
        vendor_module("validation").validate_pose_dto(dto)
    if not dto.poses:
        raise ValueError("文件没有有效的姿势条目")
    identity = read_ueformat_identity_bytes(data, tolerate_identity_errors=True).identity
    return header, dto, identity


def read_asset(text):
    if text is None or not text.get(TAG):
        raise ValueError("姿势源记录不存在；请刷新姿势库")
    record = json.loads(text.as_string())
    header, dto, identity = parse_source(decode_record(record))
    if text.get(TAG) != record["asset_id"]:
        raise ValueError("姿势源标签与记录不一致")
    return record, header, dto, identity


def store_record(record):
    payload = dumps(record)
    parse_source(decode_record(record))
    text = next((t for t in bpy.data.texts if t.library is None and t.get(TAG) == record["asset_id"]), None)
    created = text is None
    if created:
        text = bpy.data.texts.new("PoseSource_" + record["name"])
    previous = text.as_string()
    try:
        text.clear()
        text.write(payload)
        text[TAG] = record["asset_id"]
        text.use_fake_user = True
    except Exception:
        if created:
            bpy.data.texts.remove(text)
        else:
            text.clear()
            text.write(previous)
        raise
    return text


def import_source(path, scale, convention):
    source = Path(bpy.path.abspath(path)).resolve()
    enforce_limit(source.stat().st_size, "MAX_BYTES", "姿势源文件")
    data = source.read_bytes()
    header, dto, _identity = parse_source(data)
    record = make_record(data, str(source), header.object_name, header.object_path, scale, convention)
    text = store_record(record)
    return text, len(dto.poses), sum(len(p.curves) for p in dto.poses)


def bound_meshes(armature, selected_only=False):
    candidates = bpy.context.selected_objects if selected_only else bpy.context.scene.objects
    return [obj for obj in candidates if obj.type == "MESH" and
            any(mod.type == "ARMATURE" and mod.object == armature for mod in obj.modifiers)]


@contextmanager
def restore_context():
    active = bpy.context.view_layer.objects.active
    selected = tuple(bpy.context.selected_objects)
    mode = active.mode if active else "OBJECT"
    try:
        if active and mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        yield
    finally:
        current = bpy.context.view_layer.objects.active
        if current and current.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for obj in selected:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active
        if active and mode != "OBJECT":
            bpy.ops.object.mode_set(mode=mode)


def prepare(text, index, armature, policy):
    if not armature or armature.type != "ARMATURE" or not getattr(armature, "is_editable", armature.library is None):
        raise ValueError("请选择可编辑的目标骨架")
    if armature.name not in bpy.context.view_layer.objects:
        raise ValueError("目标骨架不在当前视图层")
    if armature.mode == "EDIT":
        raise ValueError("请退出骨架编辑模式后再应用或烘焙")
    normalize_skeleton_compatibility_mode(policy)
    record, header, dto, identity = read_asset(text)
    if record["convention"] != "CUE4PARSE_ADDITIVE":
        raise ValueError("该资源仅保存源数据；请在高级设置中确认导出器的加法姿势约定")
    if not 0 <= index < len(dto.poses):
        raise ValueError("姿势条目已变化；请刷新姿势库")
    vendor_module("create.space").to_blender_space(dto, header.file_version, record["scale"])
    pose = dto.poses[index]
    # Use upstream's first case-insensitive match and skip absent bones.
    get_bone = vendor_module("utils").get_case_insensitive
    bindings, warnings = [], []
    for key in pose.keys:
        bone = get_bone(armature.pose.bones, key.bone_name)
        if bone is None:
            warnings.append("跳过未匹配骨骼: " + key.bone_name)
        else:
            bindings.append((bone.name, key))
    curves = [(dto.curve_names[c.curve_index], float(c.influence)) for c in pose.curves]
    occurrence = sum(p.name == pose.name for p in dto.poses[:index])
    entry = record["asset_id"] + ":" + pose_entry_id(pose.name, occurrence)
    return record, pose, bindings, curves, entry, warnings


@contextmanager
def posed_copy(armature, bindings):
    """Evaluate the known Vendor additive convention on an isolated skeleton."""
    temp = armature.copy()
    temp.data = armature.data.copy()
    data = temp.data
    try:
        temp.animation_data_clear()
        temp.data.pose_position = 'POSE'
        # Object constraints can reference the original object or external
        # controllers. Freeze its evaluated world matrix on the temporary rig.
        world = armature.matrix_world.copy()
        temp.parent = None
        for constraint in temp.constraints:
            temp.constraints.remove(constraint)
        temp.matrix_world = world
        bpy.context.scene.collection.objects.link(temp)
        temp.hide_set(False)
        temp.hide_viewport = False
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = temp
        temp.select_set(True)
        vendor_module("utils").bone_swap_orig_parents(temp)
        for bone in temp.pose.bones:
            bone.matrix_basis.identity()
            for constraint in bone.constraints:
                constraint.mute = True
        for name, key in bindings:
            bone = temp.pose.bones[name]
            post = Quaternion(bone.bone.get("post_quat", (1, 0, 0, 0)))
            delta = Quaternion((key.rotation[3], *key.rotation[:3]))
            q = post.copy()
            q.rotate(delta)
            quat = post.copy()
            quat.rotate(q.conjugated())
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = quat.conjugated()
            bone.rotation_quaternion.normalize()
            bone.location = post.conjugated() @ Vector(key.position)
            bone.scale = Vector((1, 1, 1)) + Vector(key.scale)
        bpy.context.view_layer.update()
        yield temp
    finally:
        bpy.data.objects.remove(temp, do_unlink=True)
        bpy.data.armatures.remove(data)


def curve_bindings(meshes, curves):
    bindings, warnings = [], []
    for name, value in curves:
        found = False
        for mesh in meshes:
            keys = mesh.data.shape_keys
            exact = keys.key_blocks.get(name) if keys else None
            matches = [exact] if exact is not None else [k for k in keys.key_blocks if k.name.casefold() == name.casefold()] if keys else []
            if len(matches) > 1:
                warnings.append("跳过形态键大小写歧义: " + mesh.name + "/" + name)
                continue
            if matches and not getattr(keys, "is_editable", keys.library is None):
                warnings.append("跳过只读形态键: " + mesh.name + "/" + name)
                continue
            if matches:
                bindings.append((mesh, matches[0], value))
                found = True
        if not found:
            warnings.append("未应用曲线（源数据已保留）: " + name)
    return bindings, warnings


def inspect_pose(text, index, armature, policy):
    _record, _pose, bones, curves, _entry, warnings = prepare(text, index, armature, policy)
    targets, skipped = curve_bindings(bound_meshes(armature), curves)
    return {"bones": len(bones), "curves": len(targets), "warnings": warnings + skipped}


def _application_warnings(armature, meshes):
    warnings = []
    owners = [armature] + [m.data.shape_keys for m in meshes if m.data.shape_keys]
    if any(owner.animation_data and (owner.animation_data.action or owner.animation_data.nla_tracks or
                                    owner.animation_data.drivers) for owner in owners):
        warnings.append("当前有动画/驱动器，切换帧时可能覆盖本次未插帧姿势")
    if any(not c.mute for bone in armature.pose.bones for c in bone.constraints):
        warnings.append("骨骼约束保持启用，最终显示结果会继续受约束影响")
    return warnings


@pipeline_activity()
def apply_pose(text, index, armature, policy):
    record, pose, bones, curves, entry, warnings = prepare(text, index, armature, policy)
    meshes = bound_meshes(armature)
    curve_targets, curve_warnings = curve_bindings(meshes, curves)
    warnings.extend(curve_warnings)
    warnings.extend(_application_warnings(armature, meshes))
    if not bones and not curve_targets:
        warnings.append("没有可应用的骨骼或曲线；姿势源数据仍保留在库中")
        return {"poses": 1, "bones": 0, "curves": 0, "warnings": warnings}
    matrices = {b.name: b.matrix_basis.copy() for b in armature.pose.bones}
    values = [(key, key.value, key.slider_min, key.slider_max) for _, key, _ in curve_targets]
    try:
        with restore_context():
            with posed_copy(armature, bones) as temp:
                desired = {b.name: b.matrix.copy() for b in temp.pose.bones}
            # Matrices are in armature space, preserving reoriented rest axes.
            if bones:
                for bone in sorted(armature.pose.bones, key=lambda b: len(b.parent_recursive)):
                    parent_args = {}
                    if bone.parent:
                        parent_args = {
                            "parent_matrix": desired[bone.parent.name],
                            "parent_matrix_local": bone.parent.bone.matrix_local,
                        }
                    # Avoid using the parent's stale evaluated matrix while
                    # assigning the whole hierarchy in a single update.
                    bone.matrix_basis = bone.bone.convert_local_to_pose(
                        desired[bone.name], bone.bone.matrix_local,
                        invert=True, **parent_args,
                    )
            for _, key, value in curve_targets:
                key.slider_min = min(key.slider_min, value)
                key.slider_max = max(key.slider_max, value)
                key.value = value
            bpy.context.view_layer.update()
    except Exception:
        for bone in armature.pose.bones:
            bone.matrix_basis = matrices[bone.name]
        for key, value, low, high in values:
            key.value, key.slider_min, key.slider_max = value, low, high
        raise
    return {"poses": 1, "bones": len(bones), "curves": len(curve_targets), "warnings": warnings}


def evaluated_vertices(mesh, temp_armature, armature, curves):
    copy = mesh.copy()
    copy.data = mesh.data.copy()
    data = copy.data
    try:
        copy.animation_data_clear()
        world = mesh.matrix_world.copy()
        copy.parent = None
        for constraint in copy.constraints:
            copy.constraints.remove(constraint)
        copy.matrix_world = world
        bpy.context.scene.collection.objects.link(copy)
        copy.hide_set(False)
        copy.hide_viewport = False
        for mod in copy.modifiers:
            if mod.type == "ARMATURE" and mod.object == armature:
                mod.object = temp_armature
                mod.show_viewport = True
            else:
                mod.show_viewport = False
        keys = copy.data.shape_keys
        if keys:
            keys.animation_data_clear()
            for key in keys.key_blocks:
                key.value = 0
            targets, _ = curve_bindings([copy], curves)
            for _, key, value in targets:
                key.slider_min = min(key.slider_min, value)
                key.slider_max = max(key.slider_max, value)
                key.value = value
        copy.show_only_shape_key = False
        bpy.context.view_layer.update()
        evaluated = copy.evaluated_get(bpy.context.evaluated_depsgraph_get())
        try:
            output = evaluated.to_mesh()
            if len(output.vertices) != len(mesh.data.vertices):
                raise ValueError("烘焙改变了网格拓扑: " + mesh.name)
            return [v.co.copy() for v in output.vertices]
        finally:
            evaluated.to_mesh_clear()
    finally:
        bpy.data.objects.remove(copy, do_unlink=True)
        bpy.data.meshes.remove(data)


@pipeline_activity()
def bake_pose(text, index, armature, policy, selected_only=False):
    record, pose, bones, curves, entry, warnings = prepare(text, index, armature, policy)
    meshes = bound_meshes(armature, selected_only)
    if not meshes:
        raise ValueError("没有符合范围的绑定网格；请选择网格或使用全部绑定网格")
    for mesh in meshes:
        if mesh.library or mesh.data.library or mesh.data.users != 1:
            raise ValueError("烘焙需要可编辑的单用户网格: " + mesh.name)
        if sum(m.type == "ARMATURE" and m.object == armature for m in mesh.modifiers) != 1:
            raise ValueError("网格必须只有一个目标骨架修改器: " + mesh.name)
        if mesh.data.shape_keys and not mesh.data.shape_keys.use_relative:
            raise ValueError("暂不支持绝对形态键网格: " + mesh.name)
        mapping = json.loads(mesh.get(BAKES, "{}"))
        if not isinstance(mapping, dict) or any(not isinstance(v, str) for v in mapping.values()):
            raise ValueError("网格的姿势烘焙来源记录无效: " + mesh.name)
    _, unmatched = curve_bindings(meshes, curves)
    warnings.extend(unmatched)
    samples = []
    with restore_context(), posed_copy(armature, bones) as temp:
        for mesh in meshes:
            vertices = evaluated_vertices(mesh, temp, armature, curves)
            baseline = mesh.data.shape_keys.reference_key.data if mesh.data.shape_keys else mesh.data.vertices
            previous_name = json.loads(mesh.get(BAKES, "{}")).get(entry, "")
            updating = mesh.data.shape_keys and previous_name in mesh.data.shape_keys.key_blocks
            if not updating and not any((v - b.co).length > 1e-7 for v, b in zip(vertices, baseline)):
                warnings.append("无可见形变，跳过网格: " + mesh.name)
                continue
            samples.append((mesh, vertices))
    if not samples:
        raise ValueError("没有有效的形态键输出；源姿势数据仍保留在库中")
    snapshots = []
    try:
        for mesh, vertices in samples:
            old_map = mesh.get(BAKES)
            mapping = json.loads(old_map or "{}")
            had_basis = mesh.data.shape_keys is not None
            keys = mesh.data.shape_keys
            key = keys.key_blocks.get(mapping.get(entry, "")) if keys else None
            old_coords = [v.co.copy() for v in key.data] if key else None
            snapshots.append((mesh, old_map, had_basis, key, old_coords))
            if not had_basis:
                mesh.shape_key_add(name="Basis", from_mix=False)
            if key is None:
                key = mesh.shape_key_add(name=pose.name, from_mix=False)
                snapshots[-1] = (mesh, old_map, had_basis, key, None)
            for vertex, co in zip(key.data, vertices):
                vertex.co = co
            mapping[entry] = key.name
            mesh[BAKES] = json.dumps(mapping, ensure_ascii=False)
    except Exception:
        for mesh, old_map, had_basis, key, old_coords in reversed(snapshots):
            if key and old_coords is not None:
                for vertex, co in zip(key.data, old_coords):
                    vertex.co = co
            elif key:
                mesh.shape_key_remove(key)
            if not had_basis and mesh.data.shape_keys:
                mesh.shape_key_clear()
            if old_map is None:
                mesh.pop(BAKES, None)
            else:
                mesh[BAKES] = old_map
        raise
    return {"shape_keys": len(samples), "warnings": warnings}
