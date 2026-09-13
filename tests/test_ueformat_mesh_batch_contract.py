from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "vendor" / "io_scene_ueformat" / "importer" / "create" / "model.py"


class MeshBatchContractTests(unittest.TestCase):
    def test_model_import_uses_foreach_set_for_material_indices(self):
        source = MODEL.read_text(encoding="utf-8")
        self.assertIn("material_indices_for_sections(lod.materials, len(mesh_data.polygons))", source)
        self.assertIn('"material_index",', source)
        self.assertNotIn("mesh_data.polygons[face_index].material_index = i", source)


if __name__ == "__main__":
    unittest.main()
