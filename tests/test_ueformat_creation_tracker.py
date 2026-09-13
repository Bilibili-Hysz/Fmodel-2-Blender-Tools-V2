from pathlib import Path
import importlib.util
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "vendor" / "io_scene_ueformat" / "importer" / "creation_tracker.py"


def load_tracker_module():
    spec = importlib.util.spec_from_file_location("ueformat_creation_tracker", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeID:
    def __init__(self, pointer):
        self.pointer = pointer

    def as_pointer(self):
        return self.pointer


class CreationTrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_tracker_module()

    def test_track_deduplicates_datablocks_by_blender_pointer(self):
        capture = self.module.UEFormatImportCapture()
        first = FakeID(101)
        second_wrapper = FakeID(101)

        capture.track("objects", first)
        capture.track("objects", second_wrapper)
        capture.track("meshes", first)

        self.assertEqual(capture.items("objects"), (first,))
        self.assertEqual(capture.items("meshes"), (first,))
        self.assertTrue(capture)

    def test_counts_reflect_deduplication_and_discarded_items(self):
        capture = self.module.UEFormatImportCapture()
        object_item = FakeID(101)
        mesh_item = FakeID(202)
        capture.track("objects", object_item)
        capture.track("objects", FakeID(101))
        capture.track("meshes", mesh_item)
        self.assertEqual(capture.counts, {"objects": 1, "meshes": 1})

        capture.discard("objects", object_item)
        self.assertEqual(capture.counts, {"meshes": 1})


    def test_discard_removes_one_kind_without_affecting_other_kinds(self):
        capture = self.module.UEFormatImportCapture()
        object_item = FakeID(101)
        mesh_item = FakeID(101)
        capture.track("objects", object_item)
        capture.track("meshes", mesh_item)

        capture.discard("objects", object_item)

        self.assertEqual(capture.objects, ())
        self.assertEqual(capture.items("meshes"), (mesh_item,))
        self.assertFalse(capture.items("missing"))

    def test_python_identity_fallback_supports_objects_without_as_pointer(self):
        capture = self.module.UEFormatImportCapture()
        item = object()
        capture.track("objects", item)
        capture.track("objects", item)
        self.assertEqual(capture.objects, (item,))

    def test_discard_untracks_wrappers_invalidated_after_tracking(self):
        class DyingObject:
            def __init__(self):
                self.calls = 0

            def as_pointer(self):
                self.calls += 1
                if self.calls == 1:
                    return 4242
                raise ReferenceError("StructRNA of type Object has been removed")

        item = DyingObject()
        capture = self.module.UEFormatImportCapture()
        capture.track("objects", item)
        self.assertEqual(capture.counts, {"objects": 1})

        # The template-object flow removes the datablock before calling
        # discard, so the wrapper can no longer produce its tracked pointer.
        capture.discard("objects", item)
        self.assertEqual(capture.objects, ())
        self.assertEqual(capture.counts, {})


if __name__ == "__main__":
    unittest.main()
