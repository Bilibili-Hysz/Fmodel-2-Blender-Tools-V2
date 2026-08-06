"""Publication-contract tests for versioned animation library bundles."""

import ast
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).parent
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


if __name__ == "__main__":
    unittest.main()
