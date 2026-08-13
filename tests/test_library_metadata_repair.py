"""Pure filesystem contracts for explicit animation metadata repair."""

import importlib.util
import json
import os
import tempfile
import unittest
import uuid
from unittest import mock


class LibraryMetadataRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scanner_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "Anims", "library_scanner.py"
        )
        scanner_spec = importlib.util.spec_from_file_location("library_scanner", scanner_path)
        cls.scanner = importlib.util.module_from_spec(scanner_spec)
        scanner_spec.loader.exec_module(cls.scanner)

    def test_repair_rebuilds_missing_sidecar_from_v2_index_without_touching_blend(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "Heroes_Hero.blend")
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            original_blend = b"do not rewrite this blend"
            with open(blend_path, "wb") as handle:
                handle.write(original_blend)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0",
                    "library_name": "Heroes",
                    "bundles": [{"blend_file": "Heroes_Hero.blend", "skeletons": ["Hero"]}],
                    "catalog_entries": [{
                        "uuid": "locomotion", "path": "Hero/Locomotion", "skeleton": "Hero",
                    }],
                    "assets": [{
                        "action_name": "Run", "catalog_id": "locomotion", "skeleton": "Hero",
                        "blend_file": "Heroes_Hero.blend", "thumbnail_path": "run.png",
                    }],
                }, handle)

            result = self.scanner.repair_library_metadata(
                directory, "Heroes", lambda path: ["Run"]
            )

            self.assertEqual(result, {"sidecars_created": 1, "index_records_added": 0})
            with open(blend_path, "rb") as handle:
                self.assertEqual(handle.read(), original_blend)
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), {
                    "Hero": {"Locomotion": {"__actions__": [["Run", "run.png"]]}},
                })

    def test_repair_rejects_unsafe_declared_sidecar_before_opening_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes__library_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0",
                    "bundles": [{
                        "blend_file": "Heroes.blend",
                        "sidecar_file": "Heroes:stream_manifest.json",
                        "completion_state": "completed",
                    }],
                    "catalog_entries": [],
                    "assets": [],
                }, handle)
            loader = mock.Mock(return_value=[])

            with self.assertRaisesRegex(ValueError, "sidecar filename"):
                self.scanner.repair_library_metadata(directory, "Heroes", loader)

            loader.assert_not_called()

    def test_recreated_sidecar_includes_normalized_v2_processed_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes__library_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "bundles": [{
                        "blend_file": "Heroes_Hero.blend", "skeletons": ["Hero"],
                        "processed_sources": ["Idle.fbx", "Hero::Run.fbx"],
                    }],
                    "catalog_entries": [], "assets": [],
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes")

            self.assertFalse(os.path.exists(os.path.join(directory, "Heroes_Hero_manifest.json")))

    def test_repair_deduplicates_bundle_records_for_one_blend_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)
            with open(os.path.join(directory, "Heroes__library_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "bundles": [
                        {"blend_file": "Heroes_Hero.blend", "processed_sources": ["Hero::Run.fbx"]},
                        {"blend_file": "Heroes_Hero.blend", "processed_sources": ["Hero::Idle.fbx"]},
                    ], "catalog_entries": [], "assets": [],
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes")

            with open(os.path.join(directory, "Heroes__library_manifest.json"), "r", encoding="utf-8") as handle:
                bundles = json.load(handle)["bundles"]
            self.assertEqual(len(bundles), 1)
            self.assertEqual(bundles[0]["processed_sources"], ["Hero::Idle.fbx", "Hero::Run.fbx"])

    def test_repair_uses_metadata_missing_fallback_only_when_no_metadata_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "Loose.blend")
            with open(blend_path, "wb") as handle:
                handle.write(b"blend bytes")

            result = self.scanner.repair_library_metadata(
                directory, "Loose", lambda path: ["Idle"]
            )

            self.assertEqual(result, {"sidecars_created": 1, "index_records_added": 1})
            with open(os.path.join(directory, "Loose_manifest.json"), "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), {
                    "Unknown Skeleton": {"Metadata Missing": {"__actions__": [["Idle", ""]]}},
                })

    def test_repair_does_not_adopt_unrelated_blend_without_metadata_or_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Unrelated.blend"), "wb").close()

            result = self.scanner.repair_library_metadata(
                directory, "Heroes", lambda path: []
            )

            self.assertEqual(result, {"sidecars_created": 0, "index_records_added": 0})
            self.assertFalse(os.path.exists(os.path.join(directory, "Unrelated_manifest.json")))
            with open(os.path.join(directory, "Heroes__library_manifest.json"), encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["bundles"], [])
            self.assertEqual(repaired["assets"], [])

    def test_repair_with_usable_v2_index_ignores_unindexed_stale_bundle_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            open(os.path.join(directory, "Heroes_Stale.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)
            with open(os.path.join(directory, "Heroes_Stale_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Stale": {"Base": {"__actions__": [["Old", ""]]}}}, handle)
            with open(os.path.join(directory, "Heroes__library_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0",
                    "bundles": [{"blend_file": "Heroes_Hero.blend"}],
                    "catalog_entries": [],
                    "assets": [],
                }, handle)

            result = self.scanner.repair_library_metadata(directory, "Heroes")

            self.assertEqual(result, {"sidecars_created": 0, "index_records_added": 0})
            with open(os.path.join(directory, "Heroes__library_manifest.json"), encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual([record["blend_file"] for record in repaired["bundles"]], ["Heroes_Hero.blend"])
            self.assertNotIn("Heroes_Stale.blend", repaired["blend_files"])
            self.assertFalse(any(asset["blend_file"] == "Heroes_Stale.blend" for asset in repaired["assets"]))

    def test_repair_does_not_synthesize_index_sidecar_when_blend_actions_disagree(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "Heroes_Hero.blend")
            open(blend_path, "wb").close()
            with open(os.path.join(directory, "Heroes__library_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0",
                    "bundles": [{"blend_file": "Heroes_Hero.blend", "skeletons": ["Hero"]}],
                    "catalog_entries": [{
                        "uuid": "base", "path": "Hero/Base", "skeleton": "Hero",
                    }],
                    "assets": [{
                        "action_name": "Idle", "catalog_id": "base", "skeleton": "Hero",
                        "blend_file": "Heroes_Hero.blend", "thumbnail_path": "",
                    }],
                }, handle)

            result = self.scanner.repair_library_metadata(
                directory, "Heroes", lambda path: ["Walk"]
            )

            self.assertEqual(result["sidecars_created"], 0)
            self.assertFalse(os.path.exists(os.path.join(directory, "Heroes_Hero_manifest.json")))

    def test_repair_does_not_create_empty_sidecar_for_index_record_without_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes__library_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "bundles": [{
                        "blend_file": "Heroes_Hero.blend", "skeletons": ["Hero"],
                        "processed_sources": ["Hero::Idle.fbx"],
                    }], "catalog_entries": [], "assets": [],
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes", lambda path: [])

            self.assertFalse(os.path.exists(os.path.join(directory, "Heroes_Hero_manifest.json")))

    def test_repair_removes_unrecoverable_bundle_and_derived_records(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "library_name": "Heroes",
                    "bundles": [{
                        "blend_file": "Heroes_Hero.blend",
                        "sidecar_file": "Heroes_Hero_manifest.json",
                        "skeletons": ["Hero"], "completion_state": "completed",
                    }],
                    "catalog_entries": [{
                        "uuid": "hero", "path": "Hero/Base", "skeleton": "Hero",
                        "action_count": 1,
                    }],
                    "assets": [{
                        "action_name": "Idle", "catalog_id": "hero", "skeleton": "Hero",
                        "blend_file": "Heroes_Hero.blend", "thumbnail_path": "",
                    }],
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes", lambda path: [])

            with open(index_path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["bundles"], [])
            self.assertEqual(repaired["assets"], [])
            self.assertEqual(repaired["catalog_entries"], [])
            self.assertEqual(repaired["blend_files"], [])
            self.assertEqual(repaired["skeletons"], [])

    def test_repair_aborts_when_declared_bundle_is_temporarily_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            original = {
                "version": "2.0", "library_name": "Heroes",
                "bundles": [{
                    "blend_file": "Heroes_Hero.blend",
                    "sidecar_file": "Heroes_Hero_manifest.json",
                    "skeletons": ["Hero"], "completion_state": "completed",
                }],
                "catalog_entries": [{
                    "uuid": "hero", "path": "Hero/Base", "skeleton": "Hero",
                    "action_count": 1,
                }],
                "assets": [{
                    "action_name": "Idle", "catalog_id": "hero", "skeleton": "Hero",
                    "blend_file": "Heroes_Hero.blend", "thumbnail_path": "",
                }],
            }
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump(original, handle)

            with self.assertRaisesRegex(OSError, "declared bundle is unavailable"):
                self.scanner.repair_library_metadata(directory, "Heroes")

            with open(index_path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), original)

    def test_repair_does_not_adopt_valid_sidecar_omitted_from_usable_v2_index(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "Heroes_Hero.blend")
            sidecar_path = os.path.join(directory, "Heroes_Hero_manifest.json")
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            original_sidecar = b'{"Hero":{"Base":{"__actions__":[["Idle",""]]}}}'
            with open(blend_path, "wb") as handle:
                handle.write(b"blend bytes")
            with open(sidecar_path, "wb") as handle:
                handle.write(original_sidecar)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "library_name": "Heroes", "bundles": [],
                    "catalog_entries": [], "assets": [],
                }, handle)

            result = self.scanner.repair_library_metadata(directory, "Heroes")

            self.assertEqual(result, {"sidecars_created": 0, "index_records_added": 0})
            with open(sidecar_path, "rb") as handle:
                self.assertEqual(handle.read(), original_sidecar)
            with open(index_path, "r", encoding="utf-8") as handle:
                index = json.load(handle)
            self.assertEqual(index["bundles"], [])

    def test_repair_normalizes_verified_sidecar_processed_sources_into_record(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "__processed_sources__": ["Idle.fbx", "Hero::Run.fbx"],
                    "Hero": {"Base": {"__actions__": [["Idle", ""]]}},
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes")

            with open(os.path.join(directory, "Heroes__library_manifest.json"), "r", encoding="utf-8") as handle:
                record = json.load(handle)["bundles"][0]
            self.assertEqual(
                record["processed_sources"], ["Hero::Idle.fbx", "Hero::Run.fbx"]
            )

    def test_repair_replaces_malformed_pending_v2_record_from_verified_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "Heroes_Hero.blend")
            sidecar_path = os.path.join(directory, "Heroes_Hero_manifest.json")
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            open(blend_path, "wb").close()
            with open(sidecar_path, "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "library_name": "Heroes", "bundles": [{
                        "blend_file": "Heroes_Hero.blend", "completion_state": "pending",
                    }], "catalog_entries": [], "assets": [],
                }, handle)

            result = self.scanner.repair_library_metadata(directory, "Heroes")

            self.assertEqual(result, {"sidecars_created": 0, "index_records_added": 0})
            with open(index_path, "r", encoding="utf-8") as handle:
                record = json.load(handle)["bundles"][0]
            self.assertEqual(record, {
                "blend_file": "Heroes_Hero.blend",
                "sidecar_file": "Heroes_Hero_manifest.json",
                "skeletons": ["Hero"],
                "processed_sources": [],
                "completion_state": "completed",
            })

    def test_repair_preserves_recoverable_metadata_from_incomplete_v2_index(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "Heroes_Hero.blend")
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            open(blend_path, "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Locomotion": {"__actions__": [["Run", "run.png"]]}}}, handle)
            catalog = {
                "uuid": "locomotion", "path": "Hero/Locomotion", "skeleton": "Hero",
                "action_count": 1,
            }
            asset = {
                "action_name": "Run", "catalog_id": "locomotion", "skeleton": "Hero",
                "blend_file": "Heroes_Hero.blend", "thumbnail_path": "run.png",
            }
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "library_name": "Heroes",
                    "catalog_entries": [catalog], "assets": [asset],
                }, handle)

            result = self.scanner.repair_library_metadata(directory, "Heroes")

            self.assertEqual(result, {"sidecars_created": 0, "index_records_added": 1})
            with open(index_path, "r", encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["catalog_entries"], [catalog])
            self.assertEqual(repaired["assets"], [asset])
            self.assertEqual(repaired["catalog_entries"][0]["action_count"], 1)
            self.assertEqual(repaired["assets"][0]["thumbnail_path"], "run.png")
            self.assertEqual(len(repaired["bundles"]), 1)

    def test_repair_reconstructs_missing_asset_without_double_counting_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", "idle.png"]]}}}, handle)
            with open(os.path.join(directory, "Heroes__library_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0",
                    "catalog_entries": [{
                        "uuid": "base", "path": "Hero/Base", "skeleton": "Hero",
                        "action_count": 1,
                    }],
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes")

            with open(os.path.join(directory, "Heroes__library_manifest.json"), "r", encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["catalog_entries"][0]["action_count"], 1)
            self.assertEqual(repaired["assets"], [{
                "action_name": "Idle", "catalog_id": "base", "skeleton": "Hero",
                "blend_file": "Heroes_Hero.blend", "thumbnail_path": "idle.png",
            }])

    def test_repair_deduplicates_assets_and_recomputes_catalog_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)
            with open(os.path.join(directory, "Heroes__library_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"version": "2.0", "bundles": [{
                    "blend_file": "Heroes_Hero.blend",
                }], "catalog_entries": [
                    {"uuid": "base", "path": "Hero/Base", "skeleton": "Hero", "action_count": 99},
                ], "assets": [
                    {"action_name": "Idle", "catalog_id": "base", "skeleton": "Hero", "blend_file": "Heroes_Hero.blend"},
                    {"action_name": "Idle", "catalog_id": "base", "skeleton": "Hero", "blend_file": "Heroes_Hero.blend"},
                ]}, handle)
            self.scanner.repair_library_metadata(directory, "Heroes")
            with open(os.path.join(directory, "Heroes__library_manifest.json"), encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["catalog_entries"][0]["action_count"], 1)
            self.assertEqual(len(repaired["assets"]), 1)

    def test_repair_publishes_normalized_metadata_even_without_sidecar_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)
            with open(os.path.join(directory, "Heroes__library_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"version": "2.0", "bundles": [{
                    "blend_file": "Heroes_Hero.blend", "sidecar_file": "Heroes_Hero_manifest.json",
                    "skeletons": ["Hero"], "processed_sources": [], "completion_state": "completed",
                }], "catalog_entries": [{
                    "uuid": "base", "path": "Hero/Base", "skeleton": "Hero", "action_count": 8,
                }], "assets": [{
                    "action_name": "Idle", "catalog_id": "base", "skeleton": "Hero", "blend_file": "Heroes_Hero.blend",
                }]}, handle)
            self.scanner.repair_library_metadata(directory, "Heroes")
            with open(os.path.join(directory, "Heroes__library_manifest.json"), encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["catalog_entries"][0]["action_count"], 1)

    def test_scanning_missing_metadata_does_not_create_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "Loose.blend")
            with open(blend_path, "wb") as handle:
                handle.write(b"blend bytes")

            tree, recovery = self.scanner.load_bundle_tree(blend_path, None, ["Idle"])

            self.assertEqual(recovery, "metadata_missing")
            self.assertIn("Unknown Skeleton", tree)
            self.assertFalse(os.path.exists(os.path.join(directory, "Loose_manifest.json")))

    def test_index_and_repair_reject_library_name_traversal(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = os.path.join(parent, "library")
            os.makedirs(directory)
            escaped_index = os.path.join(parent, "escaped__library_manifest.json")
            with open(escaped_index, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "bundles": [],
                    "catalog_entries": [], "assets": [],
                }, handle)

            with self.assertRaises(ValueError):
                self.scanner.load_library_index(directory, os.path.join("..", "escaped"))
            with self.assertRaises(ValueError):
                self.scanner.repair_library_metadata(directory, os.path.join("..", "escaped"))
            self.assertTrue(os.path.isfile(escaped_index))

    def test_index_and_repair_reject_windows_unsafe_library_names(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("Heroes?", "AUX", "Heroes. ", "Heroes:stream"):
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        self.scanner.load_library_index(directory, name)
                    with self.assertRaises(ValueError):
                        self.scanner.repair_library_metadata(directory, name)

    def test_repair_rejects_unsafe_declared_bundle_filename_before_opening(self):
        with tempfile.TemporaryDirectory() as directory:
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "bundles": [{
                        "blend_file": "Heroes?.blend",
                        "sidecar_file": "Heroes?_manifest.json",
                        "completion_state": "completed",
                    }], "catalog_entries": [], "assets": [],
                }, handle)

            with self.assertRaisesRegex(ValueError, "bundle filename"):
                self.scanner.repair_library_metadata(directory, "Heroes")

    def test_existing_sidecar_is_not_authoritative_when_actions_disagree_with_blend_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            blend_path = os.path.join(directory, "Heroes_Hero.blend")
            sidecar_path = os.path.join(directory, "Heroes_Hero_manifest.json")
            open(blend_path, "wb").close()
            with open(sidecar_path, "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)

            tree, recovery = self.scanner.load_bundle_tree(
                blend_path, None, ["Walk"]
            )

            self.assertEqual(tree, {"Unknown Skeleton": {"Metadata Missing": {"__actions__": [["Walk", ""]]}}})
            self.assertEqual(recovery, "metadata_missing")

    def test_repair_preserves_ambiguous_missing_sidecar_bundle_as_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Multi.blend"), "wb").close()
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            original = {
                "version": "2.0", "library_name": "Heroes",
                "bundles": [{
                    "blend_file": "Heroes_Multi.blend",
                    "sidecar_file": "Heroes_Multi_manifest.json",
                    "skeletons": ["Hero", "Villain"],
                    "completion_state": "completed",
                }],
                "catalog_entries": [], "assets": [],
            }
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump(original, handle)

            result = self.scanner.repair_library_metadata(
                directory, "Heroes", lambda path: []
            )

            self.assertEqual(result["sidecars_created"], 0)
            with open(index_path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(len(repaired["bundles"]), 1)
            self.assertEqual(repaired["bundles"][0]["completion_state"], "unresolved")

    def test_repair_preserves_derived_metadata_for_unresolved_multi_skeleton_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Multi.blend"), "wb").close()
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            catalogs = [
                {"uuid": "hero", "path": "Hero/Base", "skeleton": "Hero", "action_count": 1},
                {"uuid": "villain", "path": "Villain/Base", "skeleton": "Villain", "action_count": 1},
                {"uuid": "villain-unused", "path": "Villain/Unused", "skeleton": "Villain", "action_count": 0},
            ]
            assets = [
                {"action_name": "Idle", "catalog_id": "hero", "skeleton": "Hero",
                 "blend_file": "Heroes_Multi.blend", "thumbnail_path": "hero.png"},
                {"action_name": "Taunt", "catalog_id": "villain", "skeleton": "Villain",
                 "blend_file": "Heroes_Multi.blend", "thumbnail_path": "villain.png"},
            ]
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "library_name": "Heroes",
                    "bundles": [{
                        "blend_file": "Heroes_Multi.blend",
                        "sidecar_file": "Heroes_Multi_manifest.json",
                        "skeletons": ["Hero", "Villain"],
                        "completion_state": "completed",
                    }],
                    "catalog_entries": catalogs, "assets": assets,
                    "blend_files": ["Heroes_Multi.blend"],
                    "skeletons": ["Hero", "Villain"],
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes", lambda path: [])

            with open(index_path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["bundles"][0]["completion_state"], "unresolved")
            self.assertEqual(repaired["catalog_entries"], catalogs)
            self.assertEqual(repaired["assets"], assets)
            self.assertEqual(repaired["blend_files"], ["Heroes_Multi.blend"])
            self.assertEqual(repaired["skeletons"], ["Hero", "Villain"])

    def test_repair_preserves_valid_library_id_when_normalizing_incomplete_v2_index(self):
        with tempfile.TemporaryDirectory() as directory:
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "library_name": "Heroes",
                    "library_id": "persistent-library-id",
                    "catalog_entries": [], "assets": [],
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes")

            with open(index_path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["library_id"], "persistent-library-id")

    def test_repair_generates_stable_library_id_for_reconstructed_v2_index(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)

            self.scanner.repair_library_metadata(directory, "Heroes")
            with open(os.path.join(directory, "Heroes__library_manifest.json"), encoding="utf-8") as handle:
                first_id = json.load(handle)["library_id"]
            self.scanner.repair_library_metadata(directory, "Heroes")
            with open(os.path.join(directory, "Heroes__library_manifest.json"), encoding="utf-8") as handle:
                second_id = json.load(handle)["library_id"]

            expected = str(uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(directory)))
            self.assertEqual(first_id, expected)
            self.assertEqual(second_id, expected)

    def test_repair_adds_stable_library_id_to_usable_v2_index(self):
        with tempfile.TemporaryDirectory() as directory:
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "library_name": "Heroes",
                    "bundles": [], "catalog_entries": [], "assets": [],
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes")

            with open(index_path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(
                repaired["library_id"],
                str(uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(directory))),
            )

    def test_repair_sets_library_path_to_repair_target_and_normalizes_catalog_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            open(os.path.join(directory, "Heroes_Hero.blend"), "wb").close()
            with open(os.path.join(directory, "Heroes_Hero_manifest.json"), "w", encoding="utf-8") as handle:
                json.dump({"Hero": {"Base": {"__actions__": [["Idle", ""]]}}}, handle)
            index_path = os.path.join(directory, "Heroes__library_manifest.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": "2.0", "library_name": "Renamed",
                    "library_path": "old/path", "bundles": [],
                    "catalog_entries": [
                        {"uuid": "same", "path": "Hero/Base", "skeleton": "Hero"},
                        {"uuid": "same", "path": "Other/Base", "skeleton": "Other"},
                    ], "assets": [],
                }, handle)

            self.scanner.repair_library_metadata(directory, "Heroes")

            with open(index_path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["library_name"], "Renamed")
            self.assertEqual(repaired["library_path"], os.path.abspath(directory))
            uuids = [item["uuid"] for item in repaired["catalog_entries"]]
            self.assertEqual(len(uuids), len(set(uuids)))


if __name__ == "__main__":
    unittest.main()
