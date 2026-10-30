"""Behavioral tests for pipeline resume identities and invalidation."""

import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from core.resume import (
    _filesystem_path,
    armature_identity,
    build_settings_fingerprint,
    import_settings_fingerprint,
    make_batch_identity,
    make_manifest_source,
    reconcile_resume_state,
    reusable_import_results,
)
from core.enums import PipelineStage, StageStatus


class FakeArmature(dict):
    def __init__(self, name, bones):
        super().__init__()
        self.name = name
        self.type = "ARMATURE"
        by_name = {}
        built = []
        for bone_name, parent_name in bones:
            bone = SimpleNamespace(name=bone_name, parent=by_name.get(parent_name))
            by_name[bone_name] = bone
            built.append(bone)
        self.data = SimpleNamespace(bones=built)


class ResumeIdentityTests(unittest.TestCase):
    def test_setting_fingerprints_are_normalized_and_scoped(self):
        base = {
            "import_scale": 0.01,
            "rotation_only": False,
            "import_curves": False,
            "clean_after_import": True,
            "clean_threshold": 0.001,
            "clean_flat_channels": True,
            "force_rebuild": False,
            "generate_thumbnails": True,
            "thumb_resolution": "256",
            "thumb_quality": "OPENGL",
            "thumb_isolate": True,
            "chunk_size": 500,
        }
        reordered = dict(reversed(list(base.items())))
        self.assertEqual(import_settings_fingerprint(base), import_settings_fingerprint(reordered))
        self.assertEqual(build_settings_fingerprint(base), build_settings_fingerprint(reordered))

        for key in ("import_scale", "rotation_only", "import_curves", "clean_after_import",
                    "clean_threshold", "clean_flat_channels"):
            changed = dict(base)
            changed[key] = not base[key] if isinstance(base[key], bool) else base[key] + 1
            self.assertNotEqual(import_settings_fingerprint(base), import_settings_fingerprint(changed), key)
            self.assertEqual(build_settings_fingerprint(base), build_settings_fingerprint(changed), key)

        for key in ("force_rebuild", "generate_thumbnails", "thumb_resolution",
                    "thumb_quality", "thumb_isolate", "chunk_size"):
            changed = dict(base)
            changed[key] = not base[key] if isinstance(base[key], bool) else f"{base[key]}-changed"
            self.assertNotEqual(build_settings_fingerprint(base), build_settings_fingerprint(changed), key)
            self.assertEqual(import_settings_fingerprint(base), import_settings_fingerprint(changed), key)

    def test_source_identity_includes_revision_and_manifest_ordinal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "walk.ueanim"
            source.write_bytes(b"v1")
            first = make_manifest_source(0, "SK", "Locomotion", "Walk", str(source))
            duplicate = make_manifest_source(1, "SK", "Locomotion", "WalkCopy", str(source))
            self.assertNotEqual(first["entry_id"], duplicate["entry_id"])

            source.write_bytes(b"version-two")
            os.utime(source, None)
            changed = make_manifest_source(0, "SK", "Locomotion", "Walk", str(source))
            self.assertNotEqual(first["revision"], changed["revision"])
            self.assertNotEqual(first["task_id"], changed["task_id"])

    @unittest.skipUnless(os.name == "nt", "Windows extended-path contract")
    def test_source_revision_tracks_file_beyond_windows_max_path(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            while len(str(parent / "long.ueanim")) <= 280:
                parent /= "animation_path_segment"
            parent.mkdir(parents=True)
            source = parent / "long.ueanim"
            source.write_bytes(b"first")

            self.assertTrue(_filesystem_path(str(source)).startswith("\\\\?\\"))
            first = make_manifest_source(0, "SK", "Moves", "Long", str(source))
            source.write_bytes(b"second-version")
            changed = make_manifest_source(0, "SK", "Moves", "Long", str(source))

            self.assertNotEqual(first["revision"], changed["revision"])

    def test_source_revision_detects_same_size_edits_with_preserved_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "Idle.ueanim")
            path.write_bytes(b"old")
            timestamp = path.stat().st_mtime_ns
            first = make_manifest_source(0, "Hero", "Base", "Idle", str(path))
            path.write_bytes(b"new")
            os.utime(path, ns=(timestamp, timestamp))
            second = make_manifest_source(0, "Hero", "Base", "Idle", str(path))
            self.assertNotEqual(first["revision"], second["revision"])

    def test_armature_identity_survives_rename_but_structure_revision_does_not(self):
        armature = FakeArmature("Rig", [("root", None), ("hand", "root")])
        first = armature_identity(armature)
        armature.name = "Renamed"
        renamed = armature_identity(armature)
        self.assertEqual(first, renamed)

        armature.data.bones.append(SimpleNamespace(name="weapon", parent=armature.data.bones[1]))
        changed = armature_identity(armature)
        self.assertEqual(first["stable_id"], changed["stable_id"])
        self.assertNotEqual(first["structure_revision"], changed["structure_revision"])

    def test_batch_identity_uses_order_and_source_revisions(self):
        sources = [{"task_id": "a"}, {"task_id": "b"}]
        identity = make_batch_identity(sources, "settings", {"stable_id": "arm", "structure_revision": "r1"})
        self.assertNotEqual(identity, make_batch_identity(list(reversed(sources)), "settings", {"stable_id": "arm", "structure_revision": "r1"}))
        self.assertNotEqual(identity, make_batch_identity(sources, "other", {"stable_id": "arm", "structure_revision": "r1"}))


class ResumeReconciliationTests(unittest.TestCase):
    def _state(self):
        completed = StageStatus.COMPLETED.value
        return {
            "config": {
                "resume_fingerprints": {"import": "old-import", "build": "old-build"},
                "import_checkpoint": {"state": "in_progress"},
            },
            "stage_status": {stage.value: completed for stage in PipelineStage},
            "dictionary_manifest": {"scan": "keep"},
            "imported_assets": {"imported_actions": [{"task_id": "a"}], "failed_imports": []},
            "library_manifest": {"built": True},
        }

    def test_import_change_invalidates_import_and_build_but_preserves_scan(self):
        state = self._state()
        reconcile_resume_state(state, "new-import", "old-build", True)
        self.assertEqual(state["dictionary_manifest"], {"scan": "keep"})
        self.assertEqual(state["stage_status"][PipelineStage.DICTIONARY_ACQUISITION.value], StageStatus.COMPLETED.value)
        self.assertEqual(state["stage_status"][PipelineStage.ASSET_IMPORT.value], StageStatus.PENDING.value)
        self.assertIsNone(state["imported_assets"])
        self.assertIsNone(state["library_manifest"])
        self.assertEqual(state["config"]["import_checkpoint"], {})

    def test_build_change_preserves_import_and_invalidates_only_build_outputs(self):
        state = self._state()
        reconcile_resume_state(state, "old-import", "new-build", True)
        self.assertIsNotNone(state["imported_assets"])
        self.assertEqual(state["stage_status"][PipelineStage.ASSET_IMPORT.value], StageStatus.COMPLETED.value)
        self.assertEqual(state["stage_status"][PipelineStage.LIBRARY_BUILDING.value], StageStatus.PENDING.value)
        self.assertIsNone(state["library_manifest"])

    def test_build_setting_change_reopens_completed_tasks(self):
        state = self._state()
        state["multi_import_plan"] = {"tasks": [{"status": "completed", "progress": 1.0}]}
        state["multi_import_checkpoint"] = {
            "current_task_index": 1,
            "plan": {"tasks": [{"status": "completed", "progress": 1.0}]},
        }
        reconcile_resume_state(state, "old-import", "new-build", True)
        self.assertEqual(state["multi_import_plan"]["tasks"][0]["status"], "pending")
        self.assertEqual(state["multi_import_checkpoint"]["current_task_index"], 0)
        self.assertEqual(state["multi_import_checkpoint"]["plan"]["tasks"][0]["status"], "pending")
        self.assertIsNotNone(state["imported_assets"])

    def test_disabling_resume_clears_checkpoint_even_without_other_changes(self):
        state = self._state()
        reconcile_resume_state(state, "old-import", "old-build", False)
        self.assertEqual(state["config"]["import_checkpoint"], {})

    def test_only_clean_terminal_exact_coverage_is_reusable(self):
        expected = ["a", "b"]
        results = {
            "imported_actions": [{"task_id": "a"}, {"task_id": "b"}],
            "failed_imports": [],
        }
        checkpoint = {"state": "complete", "expected_task_ids": expected}
        self.assertTrue(reusable_import_results(results, checkpoint, expected))

        self.assertFalse(reusable_import_results(results, {**checkpoint, "state": "in_progress"}, expected))
        self.assertFalse(reusable_import_results({**results, "failed_imports": [{"task_id": "b"}]}, checkpoint, expected))
        self.assertFalse(reusable_import_results(results, checkpoint, ["a", "b", "c"]))
        self.assertFalse(reusable_import_results({**results, "imported_actions": [{"task_id": "a"}]}, checkpoint, expected))

    def test_reuse_requires_current_manifest_identity_coverage_not_checkpoint_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "Idle.ueanim")
            Path(source).write_bytes(b"first")
            identity = make_manifest_source(0, "Hero", "Moves", "Idle", source)
            results = {
                "imported_actions": [{
                    "task_id": identity["task_id"],
                    "source_file": source,
                    "source_manifest_ref": "Hero/Moves/Idle",
                }],
                "failed_imports": [],
            }
            checkpoint = {"state": "complete", "expected_task_ids": ["stale-id"]}

            self.assertTrue(reusable_import_results(results, checkpoint, [identity]))

            changed = make_manifest_source(0, "Hero", "Moves", "Renamed", source)
            self.assertFalse(reusable_import_results(results, checkpoint, [changed]))
            self.assertFalse(reusable_import_results(
                {**results, "failed_imports": [{"source_file": source}]}, checkpoint, [identity]
            ))


if __name__ == "__main__":
    unittest.main()
