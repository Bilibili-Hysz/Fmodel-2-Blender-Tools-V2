"""Run in factory Blender: source -> library -> apply/bake -> reload assertions."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(tempfile.mkdtemp(prefix="fmodel-pose-acceptance-"))
spec = importlib.util.spec_from_file_location('PoseAcceptance', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
addon.auto_handlers._ensure_dependency_addons()
from PoseAcceptance.Fmodel.importers.animation import pose_assets as service
from PoseAcceptance.Fmodel.importers.animation.pose_library_ui import refresh


def fs(value):
    data = value.encode('utf-8')
    return struct.pack('<i', len(data)) + data


def attr(name, payload):
    return fs(name) + struct.pack('<i', len(payload)) + payload


def make_source(name, bone='root', curve='Smile'):
    key = fs(bone) + struct.pack('<3f4f3f', 0.25, 0, 0, 0, 0, 0, 1, 0, 0, 0)
    entry = fs(name) + struct.pack('<i', int(bool(bone))) + (key if bone else b'')
    entry += struct.pack('<i', int(bool(curve))) + (struct.pack('<if', 0, .75) if curve else b'')
    curves = struct.pack('<i', int(bool(curve))) + (fs(curve) if curve else b'')
    body = struct.pack('<i', 2) + attr('POSES', struct.pack('<i', 1) + entry) + attr('CURVES', curves)
    path = OUT / (name + '.uepose')
    path.write_bytes(b'UEFORMAT' + fs('UEPOSE') + bytes([10]) + fs(name) + fs('/Game/' + name) + bytes([0]) + body)
    return path


def rig():
    bpy.ops.object.select_all(action='DESELECT')
    arm = bpy.data.objects.new('Rig', bpy.data.armatures.new('Rig'))
    bpy.context.scene.collection.objects.link(arm)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    root = arm.data.edit_bones.new('root')
    root.head, root.tail = (0, 0, 0), (0, 0, 1)
    helper = arm.data.edit_bones.new('helper')
    helper.head, helper.tail = (0, 0, 0), (0, 0, 1)
    helper.parent = root
    bpy.ops.object.mode_set(mode='OBJECT')
    return arm


def mesh(arm, name):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 0, 1)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    # Deliberately no parent: discovery must follow the modifier binding.
    mod = obj.modifiers.new('Rig', 'ARMATURE')
    mod.object = arm
    obj.vertex_groups.new(name='root').add([0, 1, 2], 1, 'REPLACE')
    obj.shape_key_add(name='Basis')
    smile = obj.shape_key_add(name='Smile')
    for vertex in smile.data:
        vertex.co.y += .4
    smile.value = .2
    return obj


try:
    arm = rig()
    source = make_source('Expression')
    bpy.context.view_layer.objects.active = None
    assert bpy.ops.fmodel.import_anim_uepose(filepath=str(source)) == {'FINISHED'}
    assert bpy.context.view_layer.objects.active is None
    bpy.context.view_layer.objects.active = arm
    item = bpy.context.scene.fmodel_pose_library.items[0]
    text = item.source
    assert service.read_asset(text)[2].poses[0].name == 'Expression'
    helper_source = make_source('Helper', bone='helper', curve='')
    helper_text, _, _ = service.import_source(str(helper_source), .01, 'CUE4PARSE_ADDITIVE')
    result = service.apply_pose(helper_text, 0, arm, 'name_retarget')
    assert result['bones'] == 1 and arm.pose.bones['helper'].matrix_basis != __import__('mathutils').Matrix.Identity(4)
    # Bone-only application works before any mesh exists.
    for bone in arm.pose.bones:
        bone.matrix_basis.identity()
    meshes = [mesh(arm, 'Face'), mesh(arm, 'Body')]
    arm.pose.bones['helper'].location = (0, .15, .3)
    previous = {b.name: b.matrix_basis.copy() for b in arm.pose.bones}
    action = bpy.data.actions.new('Existing')
    arm.animation_data_create().action = action
    object_count = len(bpy.data.objects)
    result = service.bake_pose(text, 0, arm, 'name_retarget')
    assert result['shape_keys'] == 2
    assert len(bpy.data.objects) == object_count
    assert arm.animation_data.action == action
    assert all(b.matrix_basis == previous[b.name] for b in arm.pose.bones)
    for obj in meshes:
        keys = obj.data.shape_keys.key_blocks
        assert list(keys.keys()) == ['Basis', 'Smile', 'Expression']
        assert abs(keys['Smile'].value - .2) < 1e-6
        for a, b in zip(keys['Expression'].data, keys['Basis'].data):
            assert (a.co - b.co - Vector((.0025, .3, 0))).length < 1e-5, (a.co[:], b.co[:])
    service.bake_pose(text, 0, arm, 'name_retarget')
    assert all(len(obj.data.shape_keys.key_blocks) == 3 for obj in meshes)
    assert bpy.context.view_layer.objects.active == arm
    # Exercise the new panel entry points and preserve state in Pose Mode.
    refresh(bpy.context.scene)
    state = bpy.context.scene.fmodel_pose_library
    state.target = arm
    state.policy = 'name_retarget'
    state.active_index = next(i for i, row in enumerate(state.items) if row.source == text)
    state.search = 'no_such_pose'
    assert state.active_index == -1
    state.search = 'Expression'
    assert state.items[state.active_index].source == text
    state.search = ''
    assert bpy.ops.fmodel.check_pose_asset() == {'FINISHED'}
    assert state.result and not state.result_details
    assert bpy.ops.fmodel.pose_source_settings(source_name=text.name, scale=.01,
                                              convention='CUE4PARSE_ADDITIVE') == {'FINISHED'}
    bpy.ops.object.mode_set(mode='POSE')
    service.bake_pose(text, 0, arm, 'name_retarget')
    assert arm.mode == 'POSE'
    assert all(b.matrix_basis == previous[b.name] for b in arm.pose.bones)
    bpy.ops.object.mode_set(mode='OBJECT')
    # Applying a parent pose must produce the same descendant matrices as the
    # isolated evaluator, not double the parent's transform on its children.
    _record, _pose, bindings, _curves, _entry, _warnings = service.prepare(text, 0, arm, 'name_retarget')
    with service.restore_context(), service.posed_copy(arm, bindings) as temp:
        expected = {bone.name: bone.matrix.copy() for bone in temp.pose.bones}
    result = service.apply_pose(text, 0, arm, 'name_retarget')
    for bone in arm.pose.bones:
        assert max(abs(bone.matrix[i][j] - expected[bone.name][i][j]) for i in range(4) for j in range(4)) < 1e-5, bone.name
    for bone in arm.pose.bones:
        bone.matrix_basis = previous[bone.name]
    for obj in meshes:
        obj.data.shape_keys.key_blocks['Smile'].value = .2
    # Old strict presets now use upstream matching even without identity.
    assert service.apply_pose(text, 0, arm, 'strict')['bones'] == 1
    for bone in arm.pose.bones:
        bone.matrix_basis = previous[bone.name]
    for obj in meshes:
        obj.data.shape_keys.key_blocks['Smile'].value = .2
    # Entire operation is evaluated before original meshes are edited.
    original_evaluate = service.evaluated_vertices
    def fail_second(obj, *args):
        if obj == meshes[1]:
            raise RuntimeError('injected evaluation failure')
        return original_evaluate(obj, *args)
    service.evaluated_vertices = fail_second
    try:
        service.bake_pose(text, 0, arm, 'name_retarget')
        raise AssertionError('fault did not propagate')
    except RuntimeError:
        pass
    finally:
        service.evaluated_vertices = original_evaluate
    assert len(bpy.data.objects) == object_count
    assert all(b.matrix_basis == previous[b.name] for b in arm.pose.bones)
    unknown = make_source('UnknownCurve', bone='', curve='MaterialCurve')
    unknown_text, _, curve_count = service.import_source(str(unknown), .01, 'CUE4PARSE_ADDITIVE')
    assert curve_count == 1
    try:
        service.bake_pose(unknown_text, 0, arm, 'name_retarget')
        raise AssertionError('zero-output bake should fail')
    except ValueError:
        pass
    assert service.read_asset(unknown_text)[2].curve_names == ['MaterialCurve']
    library = OUT / 'Poses.blend'
    assert bpy.ops.fmodel.save_pose_library(filepath=str(library)) == {'FINISHED'}
    original_source = source.read_bytes()
    source.unlink()
    for t in list(bpy.data.texts):
        if t.get(service.TAG):
            bpy.data.texts.remove(t)
    assert bpy.ops.fmodel.load_pose_library(filepath=str(library)) == {'FINISHED'}
    records = [service.read_asset(t)[0] for t in bpy.data.texts if t.get(service.TAG)]
    assert len(records) == 3
    assert any(service.decode_record(r) == original_source for r in records)
    assert len(bpy.context.scene.fmodel_pose_library.items) == 3
    assert bpy.ops.fmodel.load_pose_library(filepath=str(library)) == {'FINISHED'}
    assert len([t for t in bpy.data.texts if t.get(service.TAG)]) == 3
    # A malformed library must not publish any records or change old ones.
    before_texts = {t.name: t.as_string() for t in bpy.data.texts}
    bogus = bpy.data.texts.new('BadPose')
    bogus[service.TAG] = 'bad'
    bogus.write('{"schema": 999}')
    bad_library = OUT / 'Invalid.blend'
    bpy.data.libraries.write(str(bad_library), {bogus})
    bpy.data.texts.remove(bogus)
    try:
        outcome = bpy.ops.fmodel.load_pose_library(filepath=str(bad_library))
        assert outcome == {'CANCELLED'}
    except RuntimeError:
        pass  # Blender propagates the operator ERROR report.
    assert {t.name: t.as_string() for t in bpy.data.texts} == before_texts
    scene_path = OUT / 'PoseScene.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))
    bpy.ops.wm.open_mainfile(filepath=str(scene_path))
    state = bpy.context.scene.fmodel_pose_library
    assert len(state.items) == 3
    assert all(row.source and row.source.get(service.TAG) for row in state.items)
    assert state.target and state.target.name == 'Rig'
    assert any(service.decode_record(service.read_asset(row.source)[0]) == original_source for row in state.items)
    print('POSE_ACCEPTANCE_PASSED', json.dumps({'library': str(library), 'sources': 3, 'baked_meshes': 2}))
finally:
    addon.unregister()
