"""Factory Blender acceptance: work context, three-step build and panel contracts.

blender --background --factory-startup --python-exit-code 1 --python this_file.py
Only writes a new temporary directory. Layout checks use real RNA with a recording
layout, so this validates drawing contracts, not rendered pixel appearance.
"""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(tempfile.mkdtemp(prefix='fmodel-workflow-acceptance-'))
spec = importlib.util.spec_from_file_location('WorkflowAcceptance', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
addon.auto_handlers._ensure_dependency_addons()
from WorkflowAcceptance.core.workflow import resolve_target, set_target, migrate_scene
from WorkflowAcceptance.Fmodel.Pipeline.steps.runner import _build_config_from_wizard
from WorkflowAcceptance.Fmodel.importers.animation import pose_library_ui
from WorkflowAcceptance.Assets.Anims import ui_custom_grid

checks = []


def passed(name):
    checks.append(name)
    print('WORKFLOW_CHECK', name)


def rig(name):
    bpy.ops.object.select_all(action='DESELECT')
    arm = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(arm)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    bone = arm.data.edit_bones.new('root')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    return arm


scene = bpy.context.scene
hero, other = rig('Hero'), rig('Other')
set_target(scene, hero)
assert resolve_target(bpy.context) == hero
assert pose_library_ui.target_armature(bpy.context) == hero
assert scene.pipeline_wizard.import_target_skeleton == hero.name
hero.name = 'RenamedHero'
assert _build_config_from_wizard(scene.pipeline_wizard, bpy.context)['target_armature'] == hero.name
assert scene.pipeline_wizard.import_target_skeleton == hero.name
passed('fixed_target_survives_selection_and_rename')

set_target(scene, None)
assert resolve_target(bpy.context) is None
deleted = rig('Deleted')
set_target(scene, deleted)
bpy.data.objects.remove(deleted, do_unlink=True)
bpy.context.view_layer.objects.active = other
assert resolve_target(bpy.context) is None
assert not bpy.ops.pipeline.wizard_step4.poll()
passed('cleared_or_deleted_target_never_falls_back')

# Migration order is explicit: saved pipeline target, then saved pose target.
scene.fmodel_workflow.initialized = False
scene.pipeline_wizard.import_target_skeleton = hero.name
migrate_scene(scene)
assert scene.fmodel_workflow.target == hero
scene.fmodel_pose_library.target = other
assert resolve_target(bpy.context) == other
set_target(scene, hero)
passed('legacy_target_migration_and_pose_property_bridge')


def fs(value):
    data = value.encode('utf8')
    return struct.pack('<i', len(data)) + data


def attr(name, value):
    return fs(name) + struct.pack('<i', len(value)) + value


source_dir = OUT / 'source'
source_dir.mkdir()
skeleton_path = '/Game/Workflow/SK_Workflow'
layout_hash = '0123456789abcdef' * 4
identity = dict(version=1, skeleton_path=skeleton_path, has_skeleton_guid=False,
                skeleton_guid=None, bone_layout_hash=layout_hash)
hero['fmodel_skeleton_identity'] = json.dumps(identity)
hero['FModel_Skeleton'] = 'SK_Workflow'
positions = struct.pack('<i', 2) + struct.pack('<i3f', 0, 0, 0, 0) + struct.pack('<i3f', 30, 10, 0, 0)
rotations = struct.pack('<i', 1) + struct.pack('<i4f', 0, 0, 0, 0, 1)
scales = struct.pack('<i', 1) + struct.pack('<i3f', 0, 1, 1, 1)
tracks = struct.pack('<i', 1) + fs('root') + positions + rotations + scales
metadata = struct.pack('<if', 31, 30) + fs('') + bytes([0, 0]) + struct.pack('<i', 0)
target = bytes([1]) + fs('/Game/Workflow/Walk') + fs(skeleton_path) + bytes([0]) + bytes.fromhex(layout_hash)
body = (struct.pack('<i', 3) + attr('METADATA', metadata) + attr('TRACKS', tracks)
        + attr('FMODEL_TARGET_SKELETON', bytes([1]) + struct.pack('<i', 1) + target))
source = source_dir / 'Walk.ueanim'
source.write_bytes(b'UEFORMAT' + fs('UEANIM') + bytes([10]) + fs('Walk') + fs('/Game/Workflow/Walk') + bytes([0]) + body)

scene_path = OUT / 'Workflow.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))
state = scene.pipeline_wizard
state.manual_source_dir = '//source'
state.output_dir = '//library'
state.library_name = 'Workflow'
state.skeleton_filter = ''
state.lib_generate_thumbnails = False
state.lib_save_model = False
state.import_curves = False
assert bpy.ops.pipeline.wizard_step2() == {'FINISHED'}
assert 'PipelineContext' not in bpy.data.texts
assert bpy.ops.pipeline.wizard_step3() == {'FINISHED'}
assert state.scan_completed and state.scan_anim_count == 1
assert (OUT / 'library' / '.fmodel_pipeline_checkpoint.json').exists()
assert bpy.ops.pipeline.wizard_goto_step(target_step=2) == {'FINISHED'}
assert state.scan_completed
assert bpy.ops.pipeline.wizard_goto_step(target_step=4) == {'FINISHED'}
assert bpy.ops.pipeline.wizard_step4() == {'FINISHED'}
assert state.lib_completed and resolve_target(bpy.context) == hero
passed('fresh_relative_path_scan_back_navigation_and_build')

assert bpy.ops.fmodel.scan_anims() == {'FINISHED'}
manager = scene.fmodel_anim_manager
index = next(i for i, item in enumerate(manager.tree_items) if item.item_type == 'ANIM')
manager.active_item_index = index
manager.import_mode = 'APPEND'
assert bpy.ops.fmodel.apply_anim(apply_method_override='REPLACE') == {'FINISHED'}
assert hero.animation_data and hero.animation_data.action
assert other.animation_data is None
passed('library_apply_uses_fixed_target')
assets = scene.fmodel_assets
previous_library = assets.active_lib_index
second = assets.project_libs.add()
second.name = 'Second'
manager.active_folder_index = 0
manager.active_item_index = index
manager.grid_page = 3
assets.active_lib_index = len(assets.project_libs) - 1
assert manager.active_folder_index == -1 and manager.active_item_index == -1 and manager.grid_page == 1
assets.active_lib_index = previous_library
manager.active_item_index = index
passed('library_switch_clears_stale_folder_selection_and_page')

# Scope uses whole directory boundaries, even for similarly prefixed libraries.
items = [SimpleNamespace(blend_path=str(OUT / 'A'), item_type='LIBRARY'),
         SimpleNamespace(blend_path=str(OUT / 'AB' / 'Walk.blend'), item_type='ANIM'),
         SimpleNamespace(blend_path=str(OUT / 'A' / 'Walk.blend'), item_type='ANIM')]
fake = SimpleNamespace(tree_items=items, active_library_index=0, library_scope='CURRENT')
assert not ui_custom_grid.in_library_scope(fake, items[1])
assert ui_custom_grid.in_library_scope(fake, items[2])
passed('current_library_scope_respects_path_boundaries')

# A real pose item exercises target controls, optional bake and source settings.
key = fs('root') + struct.pack('<3f4f3f', .25, 0, 0, 0, 0, 0, 1, 0, 0, 0)
entry = fs('Pose') + struct.pack('<i', 1) + key + struct.pack('<i', 0)
pose_body = struct.pack('<i', 2) + attr('POSES', struct.pack('<i', 1) + entry) + attr('CURVES', struct.pack('<i', 0))
pose_path = OUT / 'Pose.uepose'
pose_path.write_bytes(b'UEFORMAT' + fs('UEPOSE') + bytes([10]) + fs('Pose') + fs('/Game/Pose') + bytes([0]) + pose_body)
from WorkflowAcceptance.Fmodel.importers.animation import pose_assets
pose_assets.import_source(str(pose_path), 1.0, 'CUE4PARSE_ADDITIVE')
pose_library_ui.refresh(scene)
scene.fmodel_pose_library.show_bake = True
scene.fmodel_pose_library.show_advanced = True


class OperatorProperties:
    def __init__(self, rna):
        object.__setattr__(self, 'rna', rna)

    def __setattr__(self, name, value):
        assert name in self.rna.properties, name


class Layout:
    """Validate all operator IDs and property references against registered RNA."""
    def __init__(self, nested=False):
        self.nested = nested

    def row(self, **kwargs): return Layout(True)
    def column(self, **kwargs): return Layout(True)
    def box(self, **kwargs): return Layout(True)
    def split(self, **kwargs): return Layout(True)
    def separator(self, **kwargs): pass
    def label(self, **kwargs): pass
    def template_icon(self, **kwargs): pass

    def panel(self, *args, **kwargs):
        assert not self.nested, 'UILayout.panel must use a top-level layout'
        return Layout(), Layout()

    def prop(self, data, name, **kwargs):
        assert hasattr(data, name), (type(data), name)

    def operator(self, name, **kwargs):
        group, op = name.split('.')
        return OperatorProperties(getattr(getattr(bpy.ops, group), op).get_rna_type())

    def template_list(self, name, list_id, data, prop, active, active_prop, **kwargs):
        assert hasattr(bpy.types, name), name
        assert hasattr(data, prop) and hasattr(active, active_prop)


ui_context = SimpleNamespace(scene=scene, active_object=other, screen=bpy.context.screen,
                             region=SimpleNamespace(width=320))
workspace = scene.fmodel_workflow
for kind in ('MODEL', 'ANIMATION', 'POSE'):
    workspace.asset_kind = kind
    for cls in addon.ui._classes + (pose_library_ui.FMODEL_PT_PoseLibrary,):
        if not issubclass(cls, bpy.types.Panel):
            continue
        if hasattr(cls, 'poll') and not cls.poll(ui_context):
            continue
        cls.draw(SimpleNamespace(layout=Layout()), ui_context)
workspace.show_builder = True
for step in (1, 2, 3, 4):
    state.current_step = step
    state.lib_completed = False
    addon.ui.FMODEL_PT_Pipeline.draw(SimpleNamespace(layout=Layout()), ui_context)
state.lib_completed = True
addon.ui.FMODEL_PT_Pipeline.draw(SimpleNamespace(layout=Layout()), ui_context)
passed('all_panel_modes_and_steps_resolve_registered_rna')

workspace.asset_kind = 'POSE'
bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))
bpy.ops.wm.open_mainfile(filepath=str(scene_path))
assert bpy.context.scene.fmodel_workflow.target.name == 'RenamedHero'
assert bpy.context.scene.fmodel_workflow.asset_kind == 'POSE'
passed('workspace_survives_scene_reload')
addon.unregister()
assert not hasattr(bpy.types.Scene, 'fmodel_workflow')
assert addon.workflow_state.restore_workspaces not in bpy.app.handlers.load_post
addon.register()
addon.unregister()
passed('registration_and_handler_cleanup')
(OUT / 'result.json').write_text(json.dumps(dict(status='pass', blender=bpy.app.version_string,
                                               checks=checks), ensure_ascii=False, indent=2), encoding='utf8')
print('WORKFLOW_ACCEPTANCE_PASSED', OUT / 'result.json')
