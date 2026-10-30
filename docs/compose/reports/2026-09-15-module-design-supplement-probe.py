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

from FModelReview.Assets.Models.operators import save_model_logic
from FModelReview.Fmodel.Pipeline.stages import DictionaryAcquisitionStage, AssetImportStage, LibraryBuildingStage
from FModelReview.core import PipelineContext, PipelineConfig
from FModelReview.core.action_safety import assign_action, overwrite_nla_strip
from FModelReview.core.library_files import library_scan_counts

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

arm, mesh = new_rig('ReviewRig')

def fs(value):
    payload = value.encode('utf-8')
    return struct.pack('<i', len(payload)) + payload

def attr(name, payload):
    return fs(name) + struct.pack('<i', len(payload)) + payload

source_dir = OUT / 'source'
source_dir.mkdir()
source = source_dir / 'ReviewMove.ueanim'
layout_hash = '0123456789abcdef' * 4
skeleton_path = '/Game/Review/SK_Review'
identity = {'version': 1, 'skeleton_path': skeleton_path, 'has_skeleton_guid': False, 'skeleton_guid': None, 'bone_layout_hash': layout_hash}
arm['fmodel_skeleton_identity'] = json.dumps(identity)
arm['FModel_Skeleton'] = 'SK_Review'
positions = struct.pack('<i', 2) + struct.pack('<i3f', 0, 0, 0, 0) + struct.pack('<i3f', 30, 10, 0, 0)
rotations = struct.pack('<i', 2) + struct.pack('<i4f', 0, 0, 0, 0, 1) + struct.pack('<i4f', 30, 0, 0, 0, 1)
scales = struct.pack('<i', 2) + struct.pack('<i3f', 0, 1, 1, 1) + struct.pack('<i3f', 30, 1, 1, 1)
tracks = struct.pack('<i', 1) + fs('root') + positions + rotations + scales
curves = struct.pack('<i', 1) + fs('Smile') + struct.pack('<i', 2) + struct.pack('<if', 0, 0) + struct.pack('<if', 30, 1)
metadata = struct.pack('<if', 31, 30) + fs('') + bytes([0, 0]) + struct.pack('<i', 0)
target = bytes([1]) + fs('/Game/Review/ReviewMove') + fs(skeleton_path) + bytes([0]) + bytes.fromhex(layout_hash)
attrs = [attr('METADATA', metadata), attr('TRACKS', tracks), attr('CURVES', curves), attr('FMODEL_TARGET_SKELETON', bytes([1]) + struct.pack('<i', 1) + target)]
source.write_bytes(b'UEFORMAT' + fs('UEANIM') + bytes([10]) + fs('ReviewMove') + fs('/Game/Review/ReviewMove.ReviewMove') + bytes([0]) + struct.pack('<i', len(attrs)) + b''.join(attrs))

from FModelReview.Fmodel.Pipeline.steps.runner import (
    _build_config_from_wizard, run_wizard_import_plan,
)
from FModelReview.core.resume import armature_identity

def completed_plan_outputs():
    state = bpy.context.scene.pipeline_wizard
    state.manual_source_dir = str(source_dir)
    state.output_dir = str(OUT / 'resume_output')
    state.library_name = 'ResumeReview'
    state.skeleton_filter = 'SK_Review'
    state.import_curves = False
    state.lib_generate_thumbnails = False
    state.lib_save_model = False
    bpy.context.view_layer.objects.active = arm
    config = _build_config_from_wizard(state, bpy.context)
    ctx = PipelineContext(config=config)
    assert DictionaryAcquisitionStage().run(ctx)
    ctx.persist_checkpoint()
    first = run_wizard_import_plan(bpy.context, state)
    output = Path(state.output_dir)
    bundles = sorted(output.glob('*.blend'))
    assert first[0] and bundles, 'Baseline import must produce a bundle'
    callbacks = []
    state.lib_save_model = True
    second = run_wizard_import_plan(
        bpy.context, state,
        prepare_task=lambda task, count: callbacks.append(task.task_id) or True,
    )
    for bundle in bundles:
        bundle.rename(bundle.with_suffix('.blend.moved'))
    third = run_wizard_import_plan(bpy.context, state)
    restored = PipelineContext.load_from_checkpoint(state.output_dir)
    from FModelReview.Fmodel.Pipeline.steps.runner import run_wizard_stage
    from FModelReview.core import PipelineStage
    finalization = run_wizard_stage(bpy.context, state, PipelineStage.FINALIZATION.value)
    state.import_resume_enabled = False
    no_resume = run_wizard_import_plan(bpy.context, state)
    return {
        'initial_result': first, 'initial_bundles': [p.name for p in bundles],
        'enable_save_model_result': second, 'prepare_task_calls': callbacks,
        'after_moving_bundle_result': third,
        'finalization_after_moving_bundle': finalization,
        'resume_disabled_result': no_resume,
        'remaining_bundles': [p.name for p in output.glob('*.blend')],
        'restored_task_statuses': [t['status'] for t in restored.multi_import_checkpoint['plan']['tasks']],
    }
check('completed_plan_outputs', completed_plan_outputs)

def rest_pose_revision():
    before = armature_identity(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    bone = arm.data.edit_bones['left']
    head_before = list(bone.head)
    bone.head.x += 2
    bone.tail.x += 2
    head_after = list(bone.head)
    bpy.ops.object.mode_set(mode='OBJECT')
    arm.data.bones['left']['post_quat'] = [1.0, 0.0, 0.0, 0.0]
    arm.data.bones['left']['orig_loc'] = [2.0, 0.0, 0.0]
    after = armature_identity(arm)
    return {'before': before, 'after': after, 'unchanged': before == after,
            'head_before': head_before, 'head_after': head_after}
check('rest_pose_revision', rest_pose_revision)

def pose_success_restoration():
    from io_scene_ueformat.importer.create.pose import create_pose
    from io_scene_ueformat.importer.dto.pose import PoseDto, PoseEntryDto, PoseKeyDto
    from io_scene_ueformat.options import UEPoseOptions
    import numpy as np
    pose_arm, pose_mesh = new_rig('PoseReview')
    pose_arm.pose.bones['right'].location = (0.0, 0.25, 0.5)
    before = list(pose_arm.pose.bones['right'].location)
    key = PoseKeyDto('left', np.array([0.2, 0, 0]),
                     np.array([0, 0, 0, 1]), np.array([0, 0, 0]))
    dto = PoseDto(poses=[PoseEntryDto('ReviewPose', keys=[key])])
    options = UEPoseOptions(override_skeleton=pose_arm)
    create_pose(dto, options, 'ReviewPose')
    after = list(pose_arm.pose.bones['right'].location)
    first_keys = [k.name for k in pose_mesh.data.shape_keys.key_blocks]
    create_pose(dto, options, 'ReviewPose')
    return {'bone_location_before': before, 'bone_location_after': after,
            'first_shape_keys': first_keys,
            'after_reimport_shape_keys': [k.name for k in pose_mesh.data.shape_keys.key_blocks]}
check('pose_success_restoration', pose_success_restoration)

check('unregister', lambda: (addon.unregister(), 'completed')[-1])

