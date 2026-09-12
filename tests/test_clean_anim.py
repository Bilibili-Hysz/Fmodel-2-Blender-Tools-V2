"""Pure-Python contracts for the Blender 5 slot-aware action cleanup.

``clean_Anim.clean_action`` must operate on channelbags reachable through
``action.layers[*].strips[*]`` (the only working path on Blender 5.2, where
the legacy ``Action.fcurves`` shortcut was removed), with fallbacks for the
older top-level ``action.channelbags`` and pre-4.4 legacy containers.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "Universal_tools" / "Animations" / "clean_Anim.py"


def load_clean_anim():
    fake_bpy = types.SimpleNamespace(
        types=types.SimpleNamespace(Operator=object),
        props=types.SimpleNamespace(
            FloatProperty=lambda **kwargs: None,
            BoolProperty=lambda **kwargs: None,
        ),
    )
    previous = sys.modules.get("bpy")
    sys.modules["bpy"] = fake_bpy
    try:
        spec = importlib.util.spec_from_file_location("clean_anim_test_module", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = previous
    return module


class FakeKey:
    def __init__(self, frame, value):
        self.co = (frame, value)


class FakeFCurve:
    def __init__(self, keys=(), path="location"):
        self.keyframe_points = [FakeKey(*key) for key in keys]
        self.data_path = path


class FakeCurves(list):
    def remove(self, fcurve):
        super().remove(fcurve)


class FakeChannelBag:
    def __init__(self, curves):
        self.fcurves = FakeCurves(curves)


class FakeStrip:
    def __init__(self, bags):
        self.channelbags = list(bags)


class FakeLayer:
    def __init__(self, strips):
        self.strips = list(strips)


class FakeAction:
    def __init__(self, layers=(), *, channelbags=None, fcurves=None):
        self.layers = list(layers)
        self.frame_range = (1, 9)
        if channelbags is not None:
            self.channelbags = list(channelbags)
        if fcurves is not None:
            self.fcurves = fcurves


def _flat(path="location"):
    return FakeFCurve([(1, 1.0), (5, 1.0005), (9, 1.0)], path)


def _varied(path="location"):
    return FakeFCurve([(1, 0.0), (5, 3.0)], path)


class CleanActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_clean_anim()

    def _bags_of(self, action):
        return [
            bag
            for layer in action.layers
            for strip in layer.strips
            for bag in strip.channelbags
        ]

    def test_layers_strips_channelbags_path_cleans_all_channels(self):
        bag = FakeChannelBag([_flat(), _varied(), FakeFCurve([])])
        action = FakeAction(layers=[FakeLayer([FakeStrip([bag])])])

        stats = self.module.clean_action(action, threshold=0.001)

        self.assertEqual(stats, {
            "removed_keyframes": 2,
            "removed_channels": 1,
            "flat_channels": 1,
        })
        self.assertEqual(len(bag.fcurves), 2)
        self.assertEqual(len(bag.fcurves[0].keyframe_points), 1)
        self.assertEqual(bag.fcurves[0].keyframe_points[0].co[1], 1.0)
        self.assertEqual(len(bag.fcurves[1].keyframe_points), 2)

    def test_multiple_slots_are_all_cleaned(self):
        first = FakeChannelBag([_flat()])
        second = FakeChannelBag([_flat("scale")])
        action = FakeAction(layers=[FakeLayer([
            FakeStrip([first]), FakeStrip([second]),
        ])])

        stats = self.module.clean_action(action, threshold=0.001)

        self.assertEqual(stats["flat_channels"], 2)
        self.assertEqual(stats["removed_keyframes"], 4)
        self.assertEqual(len(first.fcurves[0].keyframe_points), 1)
        self.assertEqual(len(second.fcurves[0].keyframe_points), 1)

    def test_top_level_channelbags_fallback_is_used(self):
        bag = FakeChannelBag([_flat()])
        action = FakeAction(channelbags=[bag])

        stats = self.module.clean_action(action, threshold=0.001)

        self.assertEqual(stats["flat_channels"], 1)
        self.assertEqual(stats["removed_keyframes"], 2)

    def test_legacy_fcurves_fallback_is_used(self):
        curves = FakeCurves([_flat(), _varied()])
        action = FakeAction(fcurves=curves)

        stats = self.module.clean_action(action, threshold=0.001)

        self.assertEqual(stats["flat_channels"], 1)
        self.assertEqual(len(curves), 2)

    def test_no_containers_reports_zero_without_error(self):
        action = FakeAction()

        self.assertEqual(
            self.module.clean_action(action, threshold=0.001),
            {"removed_keyframes": 0, "removed_channels": 0, "flat_channels": 0},
        )

    def test_clean_channels_false_keeps_empty_curves(self):
        bag = FakeChannelBag([FakeFCurve([]), _varied()])
        action = FakeAction(layers=[FakeLayer([FakeStrip([bag])])])

        stats = self.module.clean_action(action, threshold=0.001,
                                         clean_channels=False, clean_flat=False)

        self.assertEqual(stats, {
            "removed_keyframes": 0,
            "removed_channels": 0,
            "flat_channels": 0,
        })
        self.assertEqual(len(bag.fcurves), 2)

    def test_clean_flat_false_keeps_flat_keys(self):
        bag = FakeChannelBag([_flat()])
        action = FakeAction(layers=[FakeLayer([FakeStrip([bag])])])

        stats = self.module.clean_action(action, threshold=0.001,
                                         clean_channels=True, clean_flat=False)

        self.assertEqual(stats["flat_channels"], 0)
        self.assertEqual(len(bag.fcurves[0].keyframe_points), 3)

    def test_batch_aggregates_per_action_stats(self):
        good = FakeAction(layers=[FakeLayer([FakeStrip(
            [FakeChannelBag([_flat()])])])])
        empty = FakeAction()
        result = self.module.clean_actions_batch([good, empty], threshold=0.001)

        self.assertEqual(result["total_actions"], 2)
        self.assertEqual(result["cleaned_actions"], 1)
        self.assertEqual(result["total_removed"], 2)

    def test_batch_swallows_per_action_failures(self):
        class BrokenAction:
            name = "broken"

            @property
            def layers(self):
                raise RuntimeError("broken")

        result = self.module.clean_actions_batch(
            [BrokenAction(), FakeAction()], threshold=0.001)

        self.assertEqual(result["total_actions"], 2)
        self.assertEqual(result["cleaned_actions"], 0)


if __name__ == "__main__":
    unittest.main()
