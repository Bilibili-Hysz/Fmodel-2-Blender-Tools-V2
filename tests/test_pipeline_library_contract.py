"""Publication-contract tests for versioned animation library bundles."""

import ast
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
STAGES = ROOT / "Fmodel" / "Pipeline" / "stages.py"


def load_stage_methods(names):
    tree = ast.parse(STAGES.read_text(encoding="utf-8"))
    stage = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LibraryBuildingStage")
    methods = [node for node in stage.body if isinstance(node, ast.FunctionDef) and node.name in names]
    module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="LibraryBuildingStage", bases=[], keywords=[], decorator_list=[], body=methods
    )], type_ignores=[]))
    globals_ = {"os": __import__("os"), "re": __import__("re"), "json": json}
    exec(compile(module, str(STAGES), "exec"), globals_)
    return globals_["LibraryBuildingStage"]()


class LibraryBundleContractTests(unittest.TestCase):
    def setUp(self):
        self.stage = load_stage_methods({
            "_bundle_slot", "_occupied_bundle_slots", "_plan_bundle_slots",
            "_remove_owned_force_rebuild_bundles", "_owned_force_rebuild_bundle_files",
            "_validate_published_bundle_actions",
        })

    def test_volume_plan_keeps_sidecar_only_slot_occupied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Lib_SK_A.blend").touch()
            (root / "Lib_SK_A_Vol_2_manifest.json").write_text("{}", encoding="utf-8")

            occupied = self.stage._occupied_bundle_slots(directory, "Lib_SK_A")
            planned = self.stage._plan_bundle_slots(occupied, 2)

            self.assertEqual(occupied, {1, 2})
            self.assertEqual(planned, [3, 4])

    def test_volume_plan_keeps_blend_only_slot_occupied(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Lib_SK_A_Vol_2.blend").touch()

            occupied = self.stage._occupied_bundle_slots(directory, "Lib_SK_A")

            self.assertEqual(occupied, {2})
            self.assertEqual(self.stage._plan_bundle_slots(occupied, 1), [3])

    def test_force_rebuild_removes_only_exact_exclusive_skeleton_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._bundle(root, "Lib_SK_A", {"SK_A": {}})
            self._bundle(root, "Lib_SK_A_Vol_2", {"SK_A": {}})
            self._bundle(root, "Lib_SK_A_Extra", {"SK_A": {}})
            self._bundle(root, "Lib", {"SK_A": {}, "SK_B": {}})
            self._bundle(root, "Lib_SK_A_Vol_3", {"SK_A": {}, "SK_B": {}})

            self.stage._remove_owned_force_rebuild_bundles(directory, "Lib_SK_A", "SK_A")

            self.assertFalse((root / "Lib_SK_A.blend").exists())
            self.assertFalse((root / "Lib_SK_A_Vol_2.blend").exists())
            self.assertTrue((root / "Lib_SK_A_Extra.blend").exists())
            self.assertTrue((root / "Lib.blend").exists())
            self.assertTrue((root / "Lib_SK_A_Vol_3.blend").exists())

    def test_inventory_validation_rejects_missing_planned_action(self):
        class LibraryLoad:
            def __enter__(self):
                return SimpleNamespace(actions=["Walk"]), None

            def __exit__(self, *_args):
                return False

        self.stage._validate_published_bundle_actions.__func__.__globals__["bpy"] = SimpleNamespace(
            data=SimpleNamespace(libraries=SimpleNamespace(load=lambda *_args, **_kwargs: LibraryLoad()))
        )

        with self.assertRaisesRegex(ValueError, "missing=.*Run"):
            self.stage._validate_published_bundle_actions("bundle.blend", ["Walk", "Run"])

    @staticmethod
    def _bundle(root, stem, manifest):
        (root / f"{stem}.blend").touch()
        (root / f"{stem}_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class IncrementalLibraryTests(unittest.TestCase):
    def setUp(self):
        from core import library_files
        from core.types import AssetLibraryManifest, BundleRecord, LibraryAsset
        self.files = library_files
        self.old = {"content_hash": "old", "settings": "settings", "target": "rig"}
        self.manifest = AssetLibraryManifest(
            version="2.0", skeletons=["Hero"],
            assets=[LibraryAsset(action_name="Idle", skeleton="Hero", blend_file="Hero.blend")],
            bundles=[BundleRecord(
                blend_file="Hero.blend", skeletons=["Hero"],
                processed_sources=["Hero::Idle.ueanim"], completion_state="completed",
            )],
        )
        self.manifest.bundles[0].source_versions = {"Hero::Idle.ueanim": self.old}

    def test_source_versions_roundtrip_without_losing_legacy_support(self):
        from core.types import AssetLibraryManifest, BundleRecord
        restored = AssetLibraryManifest.from_dict(self.manifest.to_dict())
        self.assertEqual(restored.bundles[0].source_versions, {"Hero::Idle.ueanim": self.old})
        self.assertEqual(BundleRecord.from_dict({}).source_versions, {})

    def test_unchanged_and_new_sources_do_not_require_replacement(self):
        current = {"Hero::Idle.ueanim": self.old, "Hero::Run.ueanim": self.old}
        plan = self.files.plan_source_updates(self.manifest, "Hero", current)
        self.assertEqual(plan["unchanged"], ["Hero::Idle.ueanim"])
        self.assertEqual(plan["new"], ["Hero::Run.ueanim"])
        self.assertFalse(plan["rebuild"])

    def test_content_settings_and_target_changes_require_replacement(self):
        for field in self.old:
            with self.subTest(field=field):
                current = {"Hero::Idle.ueanim": {**self.old, field: "changed"}}
                plan = self.files.plan_source_updates(self.manifest, "Hero", current)
                self.assertEqual(plan["changed"], ["Hero::Idle.ueanim"])
                self.assertTrue(plan["rebuild"])

    def test_incomplete_source_selection_cannot_drop_existing_assets(self):
        from core.types import LibraryAsset
        bundle = self.manifest.bundles[0]
        bundle.processed_sources.append("Hero::Walk.ueanim")
        self.manifest.assets.append(LibraryAsset(
            action_name="Walk", skeleton="Hero", blend_file="Hero.blend",
        ))
        with self.assertRaisesRegex(ValueError, "完整"):
            self.files.plan_source_updates(self.manifest, "Hero", {
                "Hero::Idle.ueanim": {**self.old, "content_hash": "changed"},
            })

    def test_legacy_versions_are_unverified_not_unchanged(self):
        self.manifest.bundles[0].source_versions = {}
        plan = self.files.plan_source_updates(self.manifest, "Hero", {"Hero::Idle.ueanim": self.old})
        self.assertEqual(plan["unverified"], ["Hero::Idle.ueanim"])
        self.assertTrue(plan["rebuild"])

    def test_shared_bundle_cannot_be_replaced_by_a_single_skeleton(self):
        self.manifest.bundles[0].skeletons.append("Enemy")
        with self.assertRaisesRegex(ValueError, "共享"):
            self.files.plan_source_updates(self.manifest, "Hero", {
                "Hero::Idle.ueanim": {**self.old, "content_hash": "changed"},
            })

    def test_duplicate_published_source_is_ambiguous(self):
        import copy
        duplicate = copy.deepcopy(self.manifest.bundles[0])
        duplicate.blend_file = "Other.blend"
        self.manifest.bundles.append(duplicate)
        with self.assertRaisesRegex(ValueError, "重复"):
            self.files.plan_source_updates(self.manifest, "Hero", {"Hero::Idle.ueanim": self.old})

    def test_scan_counts_compare_source_content_not_just_paths(self):
        from core.resume import source_content_hash
        from core.types import AnimationEntry, DictionaryManifest, SkeletonNode
        with tempfile.TemporaryDirectory() as directory:
            idle = Path(directory, "Idle.ueanim")
            idle.write_bytes(b"old")
            self.old["content_hash"] = source_content_hash(str(idle))
            skeleton = SkeletonNode(name="Hero")
            folder = skeleton.get_or_create_folder("Base")
            folder.add_animation("Idle", AnimationEntry(source_file=str(idle)))
            fresh = Path(directory, "Run.ueanim")
            fresh.write_bytes(b"new")
            folder.add_animation("Run", AnimationEntry(source_file=str(fresh)))
            scan = DictionaryManifest(source_path=directory, skeletons={"Hero": skeleton})
            self.assertEqual(self.files.library_scan_counts(scan, self.manifest), {
                "new": 1, "unchanged": 1, "changed": 0, "unverified": 0,
            })
            idle.write_bytes(b"changed")
            self.assertEqual(self.files.library_scan_counts(scan, self.manifest)["changed"], 1)
            self.manifest.bundles[0].source_versions = {}
            self.assertEqual(self.files.library_scan_counts(scan, self.manifest)["unverified"], 1)


if __name__ == "__main__":
    unittest.main()
