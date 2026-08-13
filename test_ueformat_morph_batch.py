from pathlib import Path
import importlib.util
from types import SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).parent
MODULE_PATH = ROOT / "vendor" / "io_scene_ueformat" / "importer" / "morph_batch.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ueformat_morph_batch", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MorphBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_offsets_are_accumulated_on_top_of_base_coordinates(self):
        deltas = [
            SimpleNamespace(vertex_index=0, position=(1.0, 0.0, 0.0)),
            SimpleNamespace(vertex_index=1, position=(0.0, 2.0, 0.0)),
        ]
        result = self.module.morph_coordinates(
            [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
            deltas,
        )
        self.assertEqual(result.tolist(), [1.0, 0.0, 0.0, 1.0, 3.0, 1.0])

    def test_repeated_vertex_deltas_are_accumulated_not_overwritten(self):
        deltas = [
            SimpleNamespace(vertex_index=0, position=(1.0, 0.0, 0.0)),
            SimpleNamespace(vertex_index=0, position=(0.0, 2.0, 0.0)),
        ]
        result = self.module.morph_coordinates([0.0, 0.0, 0.0], deltas)
        self.assertEqual(result.tolist(), [1.0, 2.0, 0.0])

    def test_invalid_vertex_index_and_shape_fail_closed(self):
        with self.assertRaises(ValueError):
            self.module.morph_coordinates(
                [0.0, 0.0, 0.0],
                [SimpleNamespace(vertex_index=1, position=(0.0, 0.0, 0.0))],
            )
        with self.assertRaises(ValueError):
            self.module.morph_coordinates(
                [0.0, 0.0],
                [],
            )
        with self.assertRaises(ValueError):
            self.module.morph_coordinates(
                [0.0, 0.0, 0.0],
                [SimpleNamespace(vertex_index=0, position=(1.0, 2.0))],
            )


if __name__ == "__main__":
    unittest.main()
