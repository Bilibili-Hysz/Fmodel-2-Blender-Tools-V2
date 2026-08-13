"""Pure-Python contracts for explicit multi-skeleton import plans."""

import importlib
import sys
import types
import unittest
import tempfile
from pathlib import Path


class MultiImportPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._bpy_module = sys.modules.get("bpy")
        cls._core_modules = {
            name: module for name, module in sys.modules.items()
            if name == "core" or name.startswith("core.")
        }
        sys.modules["bpy"] = types.ModuleType("bpy")
        for name in cls._core_modules:
            sys.modules.pop(name, None)
        cls.addClassCleanup(cls._restore_modules)

        cls.multi_import = importlib.import_module("core.multi_import")
        cls.types = importlib.import_module("core.types")

    @classmethod
    def _restore_modules(cls):
        for name in tuple(sys.modules):
            if name == "core" or name.startswith("core."):
                sys.modules.pop(name, None)
        sys.modules.update(cls._core_modules)
        if cls._bpy_module is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = cls._bpy_module

    @staticmethod
    def _manifest():
        return {
            "skeletons": {
                "Hero": {"Locomotion": {"__animations__": {
                    "Idle": {"source_file": "C:/Anims/Hero/Idle.ueanim"},
                    "Run": {"source_file": "C:/Anims/Hero/Run.ueanim"},
                }}},
                "Enemy": {"Combat": {"__animations__": {
                    "Attack": {"source_file": "C:/Anims/Enemy/Attack.ueanim"},
                }}},
            }
        }

    def test_plan_serializes_and_restores_explicit_mapping_and_subsets(self):
        plan, unmapped = self.multi_import.build_multi_import_plan(
            self._manifest(),
            {"Hero": "HeroRig", "Enemy": "EnemyRig"},
            available_target_armatures={"HeroRig", "EnemyRig"},
        )

        restored = self.types.MultiImportPlan.from_dict(plan.to_dict())

        self.assertEqual(unmapped, [])
        self.assertEqual(
            [(task.source_skeleton, task.target_armature) for task in restored.tasks],
            [("Hero", "HeroRig"), ("Enemy", "EnemyRig")],
        )
        self.assertEqual(restored.tasks[0].source_subset, [
            "C:/Anims/Hero/Idle.ueanim", "C:/Anims/Hero/Run.ueanim",
        ])

    def test_multi_skeleton_plan_reports_unmapped_without_inference(self):
        plan, unmapped = self.multi_import.build_multi_import_plan(
            self._manifest(),
            {"Hero": "HeroRig"},
            default_target_armature="ActiveRig",
            available_target_armatures={"HeroRig", "ActiveRig"},
        )

        self.assertEqual([task.source_skeleton for task in plan.tasks], ["Hero"])
        self.assertEqual(unmapped, ["Enemy"])

    def test_multi_skeleton_plan_can_require_complete_mapping(self):
        with self.assertRaisesRegex(ValueError, "Enemy"):
            self.multi_import.build_multi_import_plan(
                self._manifest(),
                {"Hero": "HeroRig"},
                default_target_armature="ActiveRig",
                available_target_armatures={"HeroRig", "ActiveRig"},
                require_complete=True,
            )

    def test_single_skeleton_defaults_to_existing_active_armature_path(self):
        manifest = self._manifest()
        manifest["skeletons"].pop("Enemy")

        plan, unmapped = self.multi_import.build_multi_import_plan(
            manifest, {}, default_target_armature="ActiveRig",
            available_target_armatures={"ActiveRig"},
        )

        self.assertEqual(unmapped, [])
        self.assertEqual(plan.tasks[0].target_armature, "ActiveRig")

    def test_plan_rejects_mapping_to_missing_or_non_armature_target(self):
        with self.assertRaisesRegex(ValueError, "EnemyRig"):
            self.multi_import.build_multi_import_plan(
                self._manifest(),
                {"Hero": "HeroRig", "Enemy": "EnemyRig"},
                available_target_armatures={"HeroRig"},
            )

    def test_resume_persists_running_before_each_completion(self):
        checkpoint = self.types.MultiImportCheckpoint(
            plan=self.types.MultiImportPlan(tasks=[
                self.types.MultiImportTask(source_skeleton="Hero", status="completed", progress=1.0),
                self.types.MultiImportTask(source_skeleton="Enemy"),
                self.types.MultiImportTask(source_skeleton="NPC"),
            ]),
            current_task_index=1,
        )
        processed = []
        snapshots = []

        success = self.multi_import.run_pending_tasks(
            checkpoint,
            lambda task: processed.append(task.source_skeleton) or True,
            lambda value: snapshots.append(value.to_dict()),
        )

        self.assertTrue(success)
        self.assertEqual(processed, ["Enemy", "NPC"])
        self.assertEqual([
            [task["status"] for task in snapshot["plan"]["tasks"]]
            for snapshot in snapshots
        ], [
            ["completed", "running", "pending"],
            ["completed", "completed", "pending"],
            ["completed", "completed", "running"],
            ["completed", "completed", "completed"],
        ])
        self.assertEqual([task.status for task in checkpoint.plan.tasks], [
            "completed", "completed", "completed",
        ])
        self.assertEqual(checkpoint.current_task_index, 3)

    def test_restore_checkpoint_preserves_only_matching_completed_tasks(self):
        old_plan, _ = self.multi_import.build_multi_import_plan(
            self._manifest(),
            {"Hero": "HeroRig", "Enemy": "EnemyRig"},
        )
        old_plan.tasks[0].status = "completed"
        old_plan.tasks[0].progress = 1.0
        old_checkpoint = self.types.MultiImportCheckpoint(plan=old_plan, current_task_index=1)
        new_plan, _ = self.multi_import.build_multi_import_plan(
            self._manifest(),
            {"Hero": "HeroRig", "Enemy": "ChangedRig"},
        )

        restored = self.multi_import.restore_checkpoint(new_plan, old_checkpoint.to_dict())

        self.assertEqual(restored.plan.tasks[0].status, "completed")
        self.assertEqual(restored.plan.tasks[1].status, "pending")
        self.assertEqual(restored.current_task_index, 1)

    def test_source_revision_change_invalidates_only_affected_completed_task(self):
        with tempfile.TemporaryDirectory() as directory:
            hero = Path(directory) / "Hero.ueanim"
            enemy = Path(directory) / "Enemy.ueanim"
            hero.write_bytes(b"hero-v1")
            enemy.write_bytes(b"enemy-v1")
            manifest = {"skeletons": {
                "Hero": {"Moves": {"__animations__": {"Idle": {"source_file": str(hero)}}}},
                "Enemy": {"Moves": {"__animations__": {"Idle": {"source_file": str(enemy)}}}},
            }}
            old_plan, _ = self.multi_import.build_multi_import_plan(
                manifest, {"Hero": "HeroRig", "Enemy": "EnemyRig"},
                import_fingerprint="settings",
            )
            for task in old_plan.tasks:
                task.status = "completed"
                task.progress = 1.0
            checkpoint = self.types.MultiImportCheckpoint(plan=old_plan, current_task_index=2)

            enemy.write_bytes(b"enemy-version-two")
            new_plan, _ = self.multi_import.build_multi_import_plan(
                manifest, {"Hero": "HeroRig", "Enemy": "EnemyRig"},
                import_fingerprint="settings",
            )
            restored = self.multi_import.restore_checkpoint(new_plan, checkpoint.to_dict())

            self.assertEqual([task.status for task in restored.plan.tasks], ["completed", "pending"])

    def test_target_skeleton_provenance_roundtrips_and_invalidates_task_id(self):
        manifest = self._manifest()
        strict_v1 = {
            "state": "strict",
            "fingerprint": "a" * 64,
            "identity": {"skeleton_path": "/Game/Hero/Skeleton"},
        }
        strict_v2 = {**strict_v1, "fingerprint": "b" * 64}
        first, _ = self.multi_import.build_multi_import_plan(
            manifest,
            {"Hero": "HeroRig", "Enemy": "EnemyRig"},
            target_armature_provenances={
                "HeroRig": strict_v1,
                "EnemyRig": strict_v1,
            },
        )
        restored = self.types.MultiImportPlan.from_dict(first.to_dict())
        changed, _ = self.multi_import.build_multi_import_plan(
            manifest,
            {"Hero": "HeroRig", "Enemy": "EnemyRig"},
            target_armature_provenances={
                "HeroRig": strict_v2,
                "EnemyRig": strict_v1,
            },
        )

        self.assertEqual(restored.tasks[0].target_skeleton_provenance, strict_v1)
        self.assertNotEqual(first.tasks[0].task_id, changed.tasks[0].task_id)

    def test_restore_does_not_reuse_completed_task_after_provenance_change(self):
        manifest = self._manifest()
        strict_v1 = {
            "state": "strict",
            "fingerprint": "a" * 64,
            "identity": {"skeleton_path": "/Game/Hero/Skeleton"},
        }
        strict_v2 = {**strict_v1, "fingerprint": "b" * 64}
        old_plan, _ = self.multi_import.build_multi_import_plan(
            manifest,
            {"Hero": "HeroRig", "Enemy": "EnemyRig"},
            target_armature_provenances={
                "HeroRig": strict_v1,
                "EnemyRig": strict_v1,
            },
        )
        old_plan.tasks[0].status = "completed"
        old_plan.tasks[0].progress = 1.0
        checkpoint = self.types.MultiImportCheckpoint(plan=old_plan)

        new_plan, _ = self.multi_import.build_multi_import_plan(
            manifest,
            {"Hero": "HeroRig", "Enemy": "EnemyRig"},
            target_armature_provenances={
                "HeroRig": strict_v2,
                "EnemyRig": strict_v1,
            },
        )
        restored = self.multi_import.restore_checkpoint(new_plan, checkpoint.to_dict())

        self.assertEqual(restored.plan.tasks[0].status, "pending")
        self.assertEqual(restored.plan.tasks[0].progress, 0.0)

    def test_legacy_checkpoint_cannot_cross_into_strict_target_provenance(self):
        manifest = self._manifest()
        legacy_plan, _ = self.multi_import.build_multi_import_plan(
            manifest,
            {"Hero": "HeroRig", "Enemy": "EnemyRig"},
        )
        legacy_plan.tasks[0].status = "completed"
        legacy_plan.tasks[0].progress = 1.0
        checkpoint = self.types.MultiImportCheckpoint(plan=legacy_plan)
        strict = {
            "state": "strict",
            "fingerprint": "a" * 64,
            "identity": {"skeleton_path": "/Game/Hero/Skeleton"},
        }

        strict_plan, _ = self.multi_import.build_multi_import_plan(
            manifest,
            {"Hero": "HeroRig", "Enemy": "EnemyRig"},
            target_armature_provenances={
                "HeroRig": strict,
                "EnemyRig": strict,
            },
        )
        restored = self.multi_import.restore_checkpoint(strict_plan, checkpoint.to_dict())

        self.assertEqual(restored.plan.tasks[0].status, "pending")

    def test_validate_rejects_malformed_persisted_provenance(self):
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.types.MultiImportTask.from_dict(
                {
                    "source_skeleton": "Hero",
                    "target_armature": "HeroRig",
                    "target_skeleton_provenance": {
                        "state": "strict",
                        "identity": {"skeleton_path": "/Game/Hero/Skeleton"},
                        "fingerprint": "f" * 64,
                    },
                },
                validate=True,
            )

    def test_target_rename_keeps_task_id_but_structural_revision_changes_it(self):
        manifest = self._manifest()
        first, _ = self.multi_import.build_multi_import_plan(
            manifest, {"Hero": "OldName", "Enemy": "EnemyRig"},
            target_armature_identities={
                "OldName": {"stable_id": "hero-id", "structure_revision": "bones-v1"},
                "EnemyRig": {"stable_id": "enemy-id", "structure_revision": "bones-v1"},
            },
        )
        renamed, _ = self.multi_import.build_multi_import_plan(
            manifest, {"Hero": "NewName", "Enemy": "EnemyRig"},
            target_armature_identities={
                "NewName": {"stable_id": "hero-id", "structure_revision": "bones-v1"},
                "EnemyRig": {"stable_id": "enemy-id", "structure_revision": "bones-v1"},
            },
        )
        changed, _ = self.multi_import.build_multi_import_plan(
            manifest, {"Hero": "NewName", "Enemy": "EnemyRig"},
            target_armature_identities={
                "NewName": {"stable_id": "hero-id", "structure_revision": "bones-v2"},
                "EnemyRig": {"stable_id": "enemy-id", "structure_revision": "bones-v1"},
            },
        )

        self.assertEqual(first.tasks[0].task_id, renamed.tasks[0].task_id)
        self.assertNotEqual(first.tasks[0].task_id, changed.tasks[0].task_id)

    def test_failed_task_persists_running_before_failed(self):
        checkpoint = self.types.MultiImportCheckpoint(
            plan=self.types.MultiImportPlan(tasks=[
                self.types.MultiImportTask(source_skeleton="Hero"),
                self.types.MultiImportTask(source_skeleton="Enemy"),
            ])
        )
        snapshots = []

        success = self.multi_import.run_pending_tasks(
            checkpoint,
            lambda task: False,
            lambda value: snapshots.append(value.to_dict()),
        )

        self.assertFalse(success)
        self.assertEqual([
            snapshot["plan"]["tasks"][0]["status"] for snapshot in snapshots
        ], ["running", "failed"])
        self.assertEqual(checkpoint.plan.tasks[0].status, "failed")
        self.assertEqual(checkpoint.plan.tasks[1].status, "pending")

    def test_failure_checkpoint_error_does_not_mask_original_task_error(self):
        checkpoint = self.types.MultiImportCheckpoint(
            plan=self.types.MultiImportPlan(tasks=[
                self.types.MultiImportTask(source_skeleton="Hero"),
            ])
        )
        persisted_statuses = []

        def persist(value):
            status = value.plan.tasks[0].status
            persisted_statuses.append(status)
            if status == "failed":
                raise OSError("checkpoint unavailable")

        with self.assertRaisesRegex(ValueError, "task exploded"):
            self.multi_import.run_pending_tasks(
                checkpoint,
                lambda task: (_ for _ in ()).throw(ValueError("task exploded")),
                persist,
            )

        self.assertEqual(checkpoint.plan.tasks[0].status, "failed")
        self.assertEqual(persisted_statuses, ["running", "failed"])

if __name__ == "__main__":
    unittest.main()
