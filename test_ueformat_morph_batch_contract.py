from pathlib import Path
import unittest


ROOT = Path(__file__).parent
LOGIC = ROOT / "vendor" / "io_scene_ueformat" / "importer" / "logic.py"


class MorphBatchContractTests(unittest.TestCase):
    def test_model_import_uses_foreach_set_for_morph_coordinates(self):
        source = LOGIC.read_text(encoding="utf-8")
        self.assertIn("morph_coordinates(base_coordinates, morph.deltas)", source)
        self.assertIn('key.data.foreach_set(', source)
        self.assertIn('for delta in morph.deltas:', source)
        self.assertIn('key.data[delta.vertex_index].co += Vector(delta.position)', source)


if __name__ == "__main__":
    unittest.main()
