from pathlib import Path
import importlib.util
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "vendor" / "io_scene_ueformat" / "importer" / "mesh_batch.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ueformat_mesh_batch", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Section:
    def __init__(self, first_index, num_faces):
        self.first_index = first_index
        self.num_faces = num_faces


class MeshBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_material_indices_expand_triangle_ranges_to_polygons(self):
        sections = [Section(0, 2), Section(6, 1), Section(9, 1)]
        self.assertEqual(
            self.module.material_indices_for_sections(sections, 4),
            [0, 0, 1, 2],
        )

    def test_later_overlapping_sections_preserve_existing_assignment_order(self):
        sections = [Section(0, 3), Section(3, 2)]
        self.assertEqual(
            self.module.material_indices_for_sections(sections, 3),
            [0, 1, 1],
        )

    def test_invalid_ranges_fail_before_foreach_set(self):
        with self.assertRaises(ValueError):
            self.module.material_indices_for_sections([Section(1, 1)], 1)
        with self.assertRaises(ValueError):
            self.module.material_indices_for_sections([Section(0, 2)], 1)
        with self.assertRaises(ValueError):
            self.module.material_indices_for_sections([Section(-3, 1)], 1)


if __name__ == "__main__":
    unittest.main()
