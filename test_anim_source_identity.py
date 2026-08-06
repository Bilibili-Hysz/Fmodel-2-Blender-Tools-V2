"""Pure-Python regression contracts for animation source identity."""

import os
import importlib.util
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "animation_source", os.path.join(os.path.dirname(__file__), "Assets", "Anims", "animation_source.py")
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

build_source_key = _MODULE.build_source_key
parse_source_key = _MODULE.parse_source_key
select_existing_action = _MODULE.select_existing_action


class AnimationSourceIdentityTests(unittest.TestCase):
    def test_source_key_round_trips_complete_identity(self):
        key = build_source_key(
            "Heroes",
            os.path.join("C:\\Library", "Heroes_Vol_2.blend"),
            "Hero",
            "Locomotion/Forward",
            "Walk",
        )

        self.assertEqual(parse_source_key(key), {
            "library": "Heroes",
            "blend_path": os.path.normcase(os.path.abspath(os.path.join("C:\\Library", "Heroes_Vol_2.blend"))),
            "skeleton": "Hero",
            "catalog_path": "Locomotion/Forward",
            "action": "Walk",
        })

    def test_same_walk_in_two_bundles_resolves_selected_bundle(self):
        first_key = build_source_key("Heroes", "C:/Library/Heroes_Vol_1.blend", "Hero", "Locomotion", "Walk")
        second_key = build_source_key("Heroes", "C:/Library/Heroes_Vol_2.blend", "Hero", "Locomotion", "Walk")
        candidates = [
            {"name": "Walk", "source_key": first_key, "blend_path": "C:/Library/Heroes_Vol_1.blend"},
            {"name": "Walk", "source_key": second_key, "blend_path": "C:/Library/Heroes_Vol_2.blend"},
        ]

        self.assertIs(select_existing_action(candidates, "Walk", second_key, "C:/Library/Heroes_Vol_2.blend"), candidates[1])

    def test_other_bundle_same_name_is_never_reused(self):
        selected_key = build_source_key("Heroes", "C:/Library/Heroes_Vol_2.blend", "Hero", "Locomotion", "Walk")
        candidates = [
            {"name": "Walk", "source_key": "other-source", "blend_path": "C:/Library/Heroes_Vol_1.blend"},
        ]

        self.assertIsNone(select_existing_action(candidates, "Walk", selected_key, "C:/Library/Heroes_Vol_2.blend"))

    def test_legacy_lookup_requires_one_unambiguous_same_name_action(self):
        only = {"name": "Walk", "source_key": "", "blend_path": ""}
        self.assertIs(select_existing_action([only], "Walk", "", ""), only)
        self.assertIsNone(select_existing_action([only, dict(only)], "Walk", "", ""))


if __name__ == "__main__":
    unittest.main()
