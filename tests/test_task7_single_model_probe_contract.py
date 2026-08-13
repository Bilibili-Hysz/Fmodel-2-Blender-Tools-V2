"""Source contract for the real-Blender single-model Task 7 probe."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "investigation" / "task7_verification" / "blender_task7_integration.py"


def test_task7_probe_uses_explicit_active_armature_handoff_before_scan():
    source = PROBE.read_text(encoding="utf-8")

    assert "bpy.ops.fmodel.use_active_armature_for_animation()" in source
    assert 'state.skeleton_filter == "Hero"' in source
    assert "state.import_target_skeleton == armature.name" in source
    assert "not state.scan_completed" in source
    assert 'bpy.context.view_layer.objects.active = enemy' in source
    assert '"Step4 used active object instead of handoff target"' in source
    assert '"single_model_handoff"' in source


def test_task7_probe_asserts_auto_import_handoff_stays_on_current_step():
    source = PROBE.read_text(encoding="utf-8")

    assert "auto_import_handoff" in source
    assert "current_step == auto_import_step" in source
    assert "auto-import handoff target mismatch" in source
    assert "auto-import handoff scanned before explicit Step 3" in source


def test_task7_probe_records_project_default_indexed_plan_without_directory_scan():
    source = PROBE.read_text(encoding="utf-8")

    assert "project_default_indexed_plan" in source
    assert "directory_scan_calls" in source
    assert "Project Settings Scan" in source
