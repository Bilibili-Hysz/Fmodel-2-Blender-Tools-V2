from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "vendor" / "io_scene_ueformat" / "importer" / "create" / "model.py"


class MorphBatchContractTests(unittest.TestCase):
    def test_model_import_uses_foreach_set_for_morph_coordinates(self):
        source = MODEL.read_text(encoding="utf-8")
        self.assertIn("morph_coordinates(base_coordinates, morph.deltas)", source)
        self.assertIn('key.data.foreach_set(', source)


if __name__ == "__main__":
    unittest.main()
