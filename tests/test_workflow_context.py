"""Shared target safety and legacy scene migration, without Blender."""
from types import SimpleNamespace as NS

from core.workflow import migrate_scene, resolve_target, set_target, synchronize_target


def scene_state():
    hero, other = NS(name='Hero', type='ARMATURE'), NS(name='Other', type='ARMATURE')
    scene = NS(objects={hero.name: hero, other.name: other},
               fmodel_workflow=NS(initialized=True, target=hero),
               pipeline_wizard=NS(import_target_skeleton='Hero', scan_completed=True,
                                  import_completed=True, lib_completed=True, skeleton_mappings=[]),
               fmodel_pose_library=NS(target=hero))
    return scene, hero, other


def test_selection_cannot_redirect_fixed_target():
    scene, hero, other = scene_state()
    assert resolve_target(NS(scene=scene, active_object=other)) is hero
    assert resolve_target(NS(scene=scene, active_object=None)) is hero


def test_deleted_or_cleared_target_never_uses_legacy_or_active_target():
    scene, hero, other = scene_state()
    del scene.objects[hero.name]
    context = NS(scene=scene, active_object=other)
    assert resolve_target(context) is None
    set_target(scene, None)
    assert resolve_target(context) is None
    assert scene.pipeline_wizard.import_target_skeleton == ''
    assert scene.fmodel_pose_library.target is None


def test_target_change_preserves_scan_and_invalidates_completion():
    scene, hero, other = scene_state()
    wizard = scene.pipeline_wizard
    wizard.skeleton_mappings = [NS(target_armature=hero, status='completed')]
    set_target(scene, other)
    assert wizard.scan_completed
    assert not wizard.import_completed and not wizard.lib_completed
    assert wizard.skeleton_mappings[0].target_armature is other


def test_multi_target_mapping_is_independent_of_daily_target():
    scene, hero, other = scene_state()
    mappings = [NS(target_armature=hero), NS(target_armature=other)]
    scene.pipeline_wizard.skeleton_mappings = mappings
    set_target(scene, other)
    assert [m.target_armature for m in mappings] == [hero, other]


def test_rename_updates_legacy_mirror_without_switching_pointer():
    scene, hero, _other = scene_state()
    del scene.objects[hero.name]
    hero.name = 'Renamed'
    scene.objects[hero.name] = hero
    synchronize_target(scene, hero)
    assert scene.pipeline_wizard.import_target_skeleton == 'Renamed'
    assert scene.fmodel_workflow.target is hero


def test_migration_prefers_saved_pipeline_and_never_repeats():
    scene, hero, other = scene_state()
    scene.fmodel_workflow.initialized = False
    scene.fmodel_workflow.target = None
    scene.fmodel_pose_library.target = other
    migrate_scene(scene)
    assert scene.fmodel_workflow.target is hero
    set_target(scene, None)
    scene.pipeline_wizard.import_target_skeleton = 'Other'
    migrate_scene(scene)
    assert scene.fmodel_workflow.target is None


def test_migration_uses_pose_target_when_saved_pipeline_target_missing():
    scene, hero, other = scene_state()
    scene.fmodel_workflow.initialized = False
    scene.fmodel_workflow.target = None
    scene.pipeline_wizard.import_target_skeleton = 'Deleted'
    scene.fmodel_pose_library.target = other
    migrate_scene(scene)
    assert scene.fmodel_workflow.target is other


def test_other_scene_target_is_rejected():
    import pytest
    scene, _hero, _other = scene_state()
    foreign = NS(name='Foreign', type='ARMATURE')
    with pytest.raises(ValueError):
        set_target(scene, foreign)
