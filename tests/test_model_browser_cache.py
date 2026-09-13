"""Contracts for the model-browser scan cache (draw-chain must not hit disk).

``Assets/Models/operators._scan_models`` runs from three draw chains, so it
caches its listing keyed by (model_dir, dir mtime) and only rescans when the
directory content changes or the cache is invalidated.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "Assets" / "Models" / "operators.py"


def load_operators():
    fake_bpy = types.SimpleNamespace(
        types=types.SimpleNamespace(Operator=object, Menu=object),
        props=types.SimpleNamespace(
            StringProperty=lambda **kwargs: None,
            IntProperty=lambda **kwargs: None,
            FloatProperty=lambda **kwargs: None,
            BoolProperty=lambda **kwargs: None,
            EnumProperty=lambda **kwargs: None,
            CollectionProperty=lambda **kwargs: None,
        ),
        utils=types.SimpleNamespace(register_class=lambda cls: None,
                                    unregister_class=lambda cls: None),
    )
    previous = sys.modules.get("bpy")
    sys.modules["bpy"] = fake_bpy
    try:
        spec = importlib.util.spec_from_file_location(
            "model_operators_test_module", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = previous
    return module


class FakeScene:
    def __init__(self):
        self.fmodel_assets = None


class FakeContext:
    def __init__(self):
        self.scene = FakeScene()


class ModelScanCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_operators()

    def setUp(self):
        self.module._invalidate_model_scan_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.model_dir = self._tmp.name
        self.context = FakeContext()
        # get_model_dir falls back to the single library entry; keep the
        # scene free of libraries so the fallback below is exercised.
        self._original_get_model_dir = self.module.get_model_dir
        self.module.get_model_dir = lambda context: self.model_dir

    def tearDown(self):
        self.module.get_model_dir = self._original_get_model_dir
        self._tmp.cleanup()

    def _add_model(self, name, *, bone_count=2, mesh_count=1):
        Path(self.model_dir, f"{name}_mesh.blend").write_bytes(b"blend")
        Path(self.model_dir, f"{name}.json").write_text(
            json.dumps({"bone_count": bone_count, "mesh_count": mesh_count}),
            encoding="utf-8",
        )

    def test_scan_returns_sorted_models_with_meta(self):
        self._add_model("Beta")
        self._add_model("Alpha", bone_count=3, mesh_count=2)

        models = self.module._scan_models(self.context)

        self.assertEqual([m["name"] for m in models], ["Alpha", "Beta"])
        self.assertEqual(models[0]["meta"]["bone_count"], 3)
        self.assertIn("preview", models[0])

    def test_second_scan_is_served_from_cache_without_disk_access(self):
        self._add_model("Hero")
        self.module._scan_models(self.context)

        original_listdir = self.module.os.listdir

        def forbidden(*args, **kwargs):
            raise AssertionError("draw-chain rescan touched the directory")

        self.module.os.listdir = forbidden
        try:
            models = self.module._scan_models(self.context)
        finally:
            self.module.os.listdir = original_listdir

        self.assertEqual([m["name"] for m in models], ["Hero"])

    def test_directory_change_invalidates_the_cache(self):
        self._add_model("Hero")
        first = self.module._scan_models(self.context)
        self.assertEqual(len(first), 1)

        self._add_model("Newer")
        stat = os.stat(self.model_dir)
        os.utime(self.model_dir, (stat.st_atime, stat.st_mtime + 5))

        second = self.module._scan_models(self.context)
        self.assertEqual([m["name"] for m in second], ["Hero", "Newer"])

    def test_invalidate_forces_a_rescan(self):
        self._add_model("Hero")
        self.module._scan_models(self.context)
        self._add_model("Extra")

        self.module._invalidate_model_scan_cache()
        models = self.module._scan_models(self.context)

        self.assertEqual(len(models), 2)

    def test_missing_directory_returns_empty_and_drops_cache(self):
        self.module.get_model_dir = lambda context: self.model_dir
        self.module._scan_models(self.context)
        self.module.get_model_dir = lambda context: os.path.join(
            self.model_dir, "missing")
        self._add_model("Hero")

        self.assertEqual(self.module._scan_models(self.context), [])
        self.assertIsNone(self.module._MODEL_SCAN_CACHE["key"])

    def test_refresh_operator_clears_the_cache(self):
        self._add_model("Hero")
        self.module._scan_models(self.context)
        self._add_model("Extra")

        operator = self.module.MODEL_OT_RefreshModelBrowser()
        operator.report = lambda *args: None
        self.assertEqual(operator.execute(self.context), {"FINISHED"})

        models = self.module._scan_models(self.context)
        self.assertEqual(len(models), 2)


if __name__ == "__main__":
    unittest.main()
