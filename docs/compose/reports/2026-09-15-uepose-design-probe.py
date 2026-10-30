"""Observe module integration behavior in an isolated Blender process.

Run: blender --background --factory-startup --python-exit-code 1 --python this_file.py
Outputs go to a new temporary directory. The script records defects; it is
not a passing acceptance suite. It must not run in a user editing session.
"""
import tempfile
import bpy
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import traceback

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(tempfile.mkdtemp(prefix="fmodel-module-design-probe-"))
OUT.mkdir(exist_ok=True)
print("REVIEW_OUTPUT", str(OUT))
RESULT = {'blender': bpy.app.version_string, 'checks': {}}

def check(name, fn):
    try:
        RESULT['checks'][name] = fn()
    except Exception:
        RESULT['checks'][name] = {'error': traceback.format_exc()}
    print('REVIEW_CHECK', name, json.dumps(RESULT['checks'][name], ensure_ascii=False))
    (OUT / 'review_probe_result.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2), encoding='utf-8')

spec = importlib.util.spec_from_file_location('FModelReview', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
check('register', lambda: (addon.register(), addon.auto_handlers._ensure_dependency_addons(), addon.auto_handlers.get_vendor_status())[-1])

def new_rig(name):
    bpy.ops.object.select_all(action='DESELECT')
    data = bpy.data.armatures.new(name + '_Data')
    arm = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(arm)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    for index, bone_name in enumerate(('root', 'left', 'right')):
        bone = data.edit_bones.new(bone_name)
        bone.head = (index, 0, 0)
        bone.tail = (index, 0, 1)
        if index:
            bone.parent = data.edit_bones['root']
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh_data = bpy.data.meshes.new(name + '_MeshData')
    mesh_data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 0, 1)], [], [(0, 1, 2)])
    mesh = bpy.data.objects.new(name + '_Mesh', mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.parent = arm
    mod = mesh.modifiers.new('Armature', 'ARMATURE')
    mod.object = arm
    for bone_name in ('left', 'right'):
        mesh.vertex_groups.new(name=bone_name).add([0, 1, 2], 0.5, 'REPLACE')
    return arm, mesh

from io_scene_ueformat.importer.create.pose import create_pose
from io_scene_ueformat.importer.dto.pose import (
    PoseDto, PoseEntryDto, PoseKeyDto, PoseCurveInfluenceDto,
)
from io_scene_ueformat.options import UEPoseOptions
import numpy as np

def pose_key(bone_name, position=(0.25, 0, 0)):
    return PoseKeyDto(bone_name, np.array(position), np.array((0, 0, 0, 1)), np.zeros(3))

def shape_names(mesh):
    return [k.name for k in mesh.data.shape_keys.key_blocks] if mesh.data.shape_keys else []

def skeleton_only():
    arm, mesh = new_rig('SkeletonOnly')
    bpy.data.objects.remove(mesh, do_unlink=True)
    dto = PoseDto(poses=[PoseEntryDto('BonePose', keys=[pose_key('left')])])
    try:
        create_pose(dto, UEPoseOptions(override_skeleton=arm), 'BonePose')
        outcome = 'completed'
    except Exception as exc:
        outcome = type(exc).__name__
    return {'bones': len(arm.data.bones), 'mesh_count': len(arm.children), 'outcome': outcome}
check('skeleton_without_mesh', skeleton_only)

def multiple_meshes():
    arm, mesh = new_rig('MultipleMesh')
    other = mesh.copy()
    other.data = mesh.data.copy()
    other.name = 'MultipleMesh_Second'
    bpy.context.scene.collection.objects.link(other)
    dto = PoseDto(poses=[PoseEntryDto('BonePose', keys=[pose_key('left')])])
    create_pose(dto, UEPoseOptions(override_skeleton=arm), 'BonePose')
    return {'source_pose_names': ['BonePose'], 'mesh_results': {m.name: shape_names(m) for m in arm.children},
            'armature_actions': [a.name for a in bpy.data.actions]}
check('multiple_meshes', multiple_meshes)

def helper_only():
    arm, mesh = new_rig('HelperOnly')
    bpy.ops.object.mode_set(mode='EDIT')
    helper = arm.data.edit_bones.new('helper')
    helper.head, helper.tail = (0, 0, 0), (0, 0, 1)
    helper.parent = arm.data.edit_bones['root']
    bpy.ops.object.mode_set(mode='OBJECT')
    dto = PoseDto(poses=[PoseEntryDto('HelperPose', keys=[pose_key('helper')])])
    result = create_pose(dto, UEPoseOptions(override_skeleton=arm), 'HelperPose')
    return {'input_bones': ['helper'], 'returned': result, 'shape_keys': shape_names(mesh),
            'actions_created': [a.name for a in bpy.data.actions]}
check('unweighted_bone_pose', helper_only)

def mixed_pose():
    arm, mesh = new_rig('MixedPose')
    mesh.shape_key_add(name='Basis')
    smile = mesh.shape_key_add(name='Smile')
    for vert in smile.data:
        vert.co.y += 0.4
    dto = PoseDto(poses=[PoseEntryDto('Expression', keys=[pose_key('left')],
                       curves=[PoseCurveInfluenceDto(0, 0.75)])], curve_names=['Smile'])
    create_pose(dto, UEPoseOptions(override_skeleton=arm), 'MixedPose')
    return {'source_pose_count': len(dto.poses), 'source_curve_mapping': {'Smile': 0.75},
            'shape_keys': shape_names(mesh), 'mesh_custom_properties': list(mesh.keys()),
            'shape_key_datablock_properties': list(mesh.data.shape_keys.keys())}
check('mixed_bones_and_curves', mixed_pose)

def fs(text):
    data = text.encode('utf-8')
    return struct.pack('<i', len(data)) + data

def attr(name, data):
    return fs(name) + struct.pack('<i', len(data)) + data

def unmatched_curve_operator():
    arm, mesh = new_rig('UnmatchedCurve')
    source = OUT / 'UnmatchedCurve.uepose'
    curves = struct.pack('<i', 1) + fs('MaterialParameter')
    entry = fs('CurvePose') + struct.pack('<i', 0) + struct.pack('<i', 1) + struct.pack('<if', 0, 0.8)
    body = struct.pack('<i', 2) + attr('POSES', struct.pack('<i', 1) + entry) + attr('CURVES', curves)
    source.write_bytes(b'UEFORMAT' + fs('UEPOSE') + bytes([10]) + fs('UnmatchedCurve')
                       + fs('/Review/UnmatchedCurve') + bytes([0]) + body)
    result = bpy.ops.fmodel.import_anim_uepose(filepath=str(source), compatibility_mode='name_retarget')
    return {'operator': sorted(result), 'input_curve': 'MaterialParameter',
            'shape_keys': shape_names(mesh), 'actions': [a.name for a in bpy.data.actions]}
check('unmatched_curve_operator', unmatched_curve_operator)

check('unregister', lambda: (addon.unregister(), 'completed')[-1])

