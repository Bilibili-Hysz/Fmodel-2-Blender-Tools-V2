"""Contract that thumbnail baking uses Blender 5 slot-aware assignment."""

import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ThumbnailActionAssignmentTests(unittest.TestCase):
    def test_thumbnail_uses_shared_slot_aware_assignment(self):
        path = os.path.join(ROOT, "Assets", "Utils", "utils_asset.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("assign_action(target_arm, action)", source)
        self.assertNotIn("target_arm.animation_data.action = action", source)

    def test_thumbnail_resource_path_can_be_known_before_baking(self):
        path = os.path.join(ROOT, "Assets", "Utils", "utils_asset.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("def thumbnail_output_path(", source)

    def test_thumbnail_disables_nla_and_restores_mutated_state_in_finally(self):
        path = os.path.join(ROOT, "Assets", "Utils", "utils_asset.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("nla_mutes = disable_nla(anim_data)", source)
        finally_block = source[source.index("    finally:", source.index("def bake_thumbnail_opengl")):]
        for expected in (
            "restore_nla_mutes(nla_mutes)",
            "render.resolution_x = orig_render['resolution_x']",
            "obj.hide_render = was_hidden",
            "frame_set(orig_frame)",
            "restore_animation_state(target_arm, arm_state)",
        ):
            self.assertIn(expected, finally_block)

    def test_append_nla_does_not_clear_active_action_or_slot(self):
        path = os.path.join(ROOT, "Assets", "Anims", "anim_manager.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        append_branch = source[source.index("            else:", source.index("if method == 'REPLACE'")):source.index("        return {'FINISHED'}", source.index("if method == 'REPLACE'"))]

        self.assertNotIn("anim_data.action = None", append_branch)
        self.assertNotIn("anim_data.action_slot = None", append_branch)

    def test_anim_manager_uses_shared_slot_aware_nla_creation(self):
        path = os.path.join(ROOT, "Assets", "Anims", "anim_manager.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("append_nla_strip(", source)
        self.assertIn("track, action, target_arm, int(insert_frame), selected.action_name", source)
        self.assertIn("action, overlaps, target_arm", source)


if __name__ == "__main__":
    unittest.main()
