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

def model_save():
    model_dir = OUT / 'model'
    success = save_model_logic(arm, str(model_dir), 'ReviewModel', save_preview=False)
    with bpy.data.libraries.load(str(model_dir / 'ReviewModel_mesh.blend'), link=False) as (df, dt):
        contents = {'objects': list(df.objects), 'meshes': list(df.meshes), 'armatures': list(df.armatures)}
    return {'success': success, 'source_meshes': [c.name for c in arm.children if c.type == 'MESH'], 'saved': contents}
check('model_save_inventory', model_save)

def sort_bones():
    bpy.context.view_layer.objects.active = arm
    result = bpy.ops.fmodel.sort_bones()
    return {'operator': sorted(result), 'weighted_bones': [v.name for v in mesh.vertex_groups], 'hidden': [b.name for b in arm.data.collections['Hidden_Helpers'].bones]}
check('bone_sort_branch_coverage', sort_bones)

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

def scan_wizard():
    state = bpy.context.scene.pipeline_wizard
    state.manual_source_dir = str(source_dir)
    state.output_dir = str(OUT / 'wizard_output')
    state.library_name = 'Review'
    state.skeleton_filter = 'SK_Review'
    try:
        status = sorted(bpy.ops.pipeline.wizard_step3())
    except RuntimeError as error:
        status = str(error)
    checkpoint = PipelineContext.load_from_checkpoint(state.output_dir)
    return {'operator_result': status, 'scan_completed': state.scan_completed, 'disk_checkpoint_present': checkpoint is not None, 'disk_scan_skeletons': list((checkpoint.dictionary_manifest or {}).get('skeletons', {})) if checkpoint else [], 'text_exists': 'PipelineContext' in bpy.data.texts}
check('fresh_wizard_scan', scan_wizard)

mesh.shape_key_add(name='Basis')
smile = mesh.shape_key_add(name='Smile')
smile.value = 0.25
smile.keyframe_insert(data_path='value', frame=1)
original_face_action = mesh.data.shape_keys.animation_data.action.name

cfg = PipelineConfig(source_dir=str(source_dir), output_dir=str(OUT / 'pipeline_output'), library_name='Review', skeleton_filter='SK_Review', target_armature=arm.name, generate_thumbnails=False, import_curves=True, clean_after_import=True).to_dict()
cfg.update(source_skeleton='SK_Review', source_subset=[str(source)])
ctx = PipelineContext(config=cfg)

def import_pipeline():
    scanned = DictionaryAcquisitionStage().run(ctx)
    imported = AssetImportStage().run(ctx) if scanned else False
    shape_anim = mesh.data.shape_keys.animation_data
    return {'scan': scanned, 'import': imported, 'imported_actions': (ctx.imported_assets or {}).get('imported_actions'), 'actions_in_memory': [a.name for a in bpy.data.actions], 'shape_action_before': original_face_action, 'shape_action_after': shape_anim.action.name if shape_anim and shape_anim.action else None, 'logs': ctx.logs[-12:]}
check('pipeline_staging_and_curves', import_pipeline)

def build_library():
    success = LibraryBuildingStage().run(ctx)
    inventories = {}
    for path in (OUT / 'pipeline_output').glob('*.blend'):
        with bpy.data.libraries.load(str(path), link=False) as (df, dt):
            inventories[path.name] = list(df.actions)
    result = {'success': success, 'inventories': inventories, 'manifest': ctx.library_manifest, 'logs': ctx.logs[-8:]}
    return result
check('published_library', build_library)

def append_then_link():
    from types import SimpleNamespace
    from FModelReview.Assets.Anims.anim_manager import _load_action_for_item
    from FModelReview.Assets.Anims.animation_source import build_source_key
    bundle_path = str(OUT / 'pipeline_output' / 'Review_SK_Review.blend')
    item = SimpleNamespace(item_type='ANIM', action_name='ReviewMove', name='ReviewMove', blend_path=bundle_path, skeleton='SK_Review')
    item.source_key = build_source_key('Review', bundle_path, item.skeleton, 'Base', item.action_name)
    appended = _load_action_for_item(item, use_link=False)
    linked = _load_action_for_item(item, use_link=True)
    result = {'append_loaded': appended is not None, 'link_loaded': linked is not None, 'same_datablock': appended == linked, 'link_has_library': bool(linked.library) if linked else None}
    if appended is not None:
        bpy.data.actions.remove(appended)
    return result
check('append_then_link_semantics', append_then_link)

def alias_staging():
    arm['FModel_Skeleton'] = 'Review'
    alias_cfg = dict(cfg, output_dir=str(OUT / 'alias_output'), clean_after_import=False, import_curves=False)
    alias_ctx = PipelineContext(config=alias_cfg)
    DictionaryAcquisitionStage().run(alias_ctx)
    ok = AssetImportStage().run(alias_ctx)
    alias_ctx.persist_checkpoint()
    loaded = PipelineContext.load_from_checkpoint(alias_cfg['output_dir'])
    arm['FModel_Skeleton'] = 'SK_Review'
    return {'import': ok, 'checkpoint_loadable': loaded is not None, 'logs': alias_ctx.logs[-8:]}
check('display_alias_staging_contract', alias_staging)

def nla_overwrite():
    action = bpy.data.actions.new('NlaReview')
    assign_action(arm, action)
    track = arm.animation_data.nla_tracks.new()
    strip = track.strips.new('Old', 1, action)
    try:
        overwrite_nla_strip(track, 'Replacement', 1, action, [strip], arm)
        result = 'accepted'
    except RuntimeError as error:
        result = str(error)
    return {'overwrite_result': result, 'remaining_strips': [s.name for s in track.strips]}
check('nla_overwrite', nla_overwrite)

def rebake_only():
    state = bpy.context.scene.pipeline_wizard
    state.output_dir = cfg['output_dir']
    state.library_name = cfg['library_name']
    state.lib_completed = True
    before = sorted(p.name for p in Path(cfg['output_dir']).glob('*.blend'))
    result = sorted(bpy.ops.pipeline.rebake_thumbnails())
    after = sorted(p.name for p in Path(cfg['output_dir']).glob('*.blend'))
    return {'operator': result, 'bundles_before': before, 'bundles_after': after, 'thumbnails': [str(p) for p in Path(cfg['output_dir']).rglob('*.webp')]}
check('rebake_bundle_path_stability', rebake_only)

def relative_checkpoint_path():
    raw = '//ReviewOutput'
    return {'input': raw, 'blender_absolute': bpy.path.abspath(raw), 'checkpoint_path': PipelineContext._checkpoint_path_for_output(raw)}
check('blender_relative_checkpoint_path', relative_checkpoint_path)

def fps_resume_contract():
    from FModelReview.core.resume import import_settings_fingerprint
    old_fps = bpy.context.scene.render.fps
    fingerprint_before = import_settings_fingerprint(cfg)
    bpy.context.scene.render.fps = 60
    fingerprint_after = import_settings_fingerprint(cfg)
    bpy.context.scene.render.fps = old_fps
    return {'original_fps': old_fps, 'changed_fps': 60, 'fingerprint_unchanged': fingerprint_before == fingerprint_after, 'source_duration_seconds': 1, 'stored_action_frame_range': RESULT['checks']['pipeline_staging_and_curves']['imported_actions'][0]['frame_range']}
check('fps_resume_contract', fps_resume_contract)

check('unregister', lambda: (addon.unregister(), 'completed')[-1])
