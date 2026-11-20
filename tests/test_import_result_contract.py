"""The import operator must not report success when nothing was created.

A structurally valid .uemodel whose LODS array is empty parses cleanly and then has
nothing to build, so import_file() returns None and the operator used to discard that
value and publish "Status: Success" with zero objects. Pose imports legitimately
create no object, so the guard has to distinguish the two cases.
"""

from __future__ import annotations

import ast
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "io_scene_ueformat"
HELPERS = VENDOR / "op" / "import_helpers.py"


def _load_guard():
    """Extract _require_created_asset and run it against stand-in option classes."""
    tree = ast.parse(HELPERS.read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "_require_created_asset"
    )

    class UEModelOptions:
        pass

    class UEAnimOptions:
        pass

    class UEPoseOptions:
        pass

    namespace = {
        "UEModelOptions": UEModelOptions,
        "UEAnimOptions": UEAnimOptions,
        "UEPoseOptions": UEPoseOptions,
        "UEFormatOptions": object,
        "Path": Path,
        "type": type,
    }
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(HELPERS), "exec"), namespace)
    return (
        namespace["_require_created_asset"],
        UEModelOptions,
        UEAnimOptions,
        UEPoseOptions,
    )


class _Object:
    name = "CreatedObject"


class CreatedAssetGuardTests(unittest.TestCase):
    def setUp(self):
        (
            self.guard,
            self.UEModelOptions,
            self.UEAnimOptions,
            self.UEPoseOptions,
        ) = _load_guard()
        self.path = Path("C:/exports/SK_Example.uemodel")

    def test_model_import_that_created_nothing_fails(self):
        with self.assertRaises(ValueError) as caught:
            self.guard(self.UEModelOptions, None, self.path)
        message = str(caught.exception)
        # The report must name the file and the reason, not just "failed".
        assert "SK_Example.uemodel" in message
        assert "LOD" in message

    def test_animation_import_that_created_nothing_fails(self):
        with self.assertRaises(ValueError):
            self.guard(self.UEAnimOptions, None, self.path)

    def test_pose_import_may_create_no_object(self):
        # A pose mutates the target armature in place; None is its success value.
        assert self.guard(self.UEPoseOptions, None, self.path) is None

    def test_import_that_created_an_asset_passes(self):
        for options_class in (self.UEModelOptions, self.UEAnimOptions, self.UEPoseOptions):
            assert self.guard(options_class, _Object(), self.path) is None

    def test_operator_validates_the_return_value(self):
        """The operator must call the guard, not drop import_file()'s result."""
        source = HELPERS.read_text(encoding="utf-8")
        assert "_require_created_asset(self.options_class, created, path)" in source
        assert "import_file(path)\n" not in source.split("created = UEFormatImport")[0][-200:]


if __name__ == "__main__":
    unittest.main()
