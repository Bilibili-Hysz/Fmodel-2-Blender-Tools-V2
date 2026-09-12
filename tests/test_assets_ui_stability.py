"""Lightweight regression checks for Assets UI code without Blender."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ANIM_MANAGER = ROOT / "Assets" / "Anims" / "anim_manager.py"
UI_ASSETS = ROOT / "Assets" / "ui_assets.py"
UI = ROOT / "ui.py"
PIPELINE_STAGES = ROOT / "Fmodel" / "Pipeline" / "stages.py"


class AssetUiStabilitySourceTests(unittest.TestCase):
    def test_anim_manager_defines_manifest_cache_helpers(self):
        source = ANIM_MANAGER.read_text(encoding="utf-8")
        self.assertIn("_MANIFEST_CACHE", source)
        self.assertIn("def _load_manifest", source)
        self.assertIn("def clear_asset_caches", source)

    def test_active_skeleton_sync_tracks_last_armature(self):
        source = ANIM_MANAGER.read_text(encoding="utf-8")
        self.assertIn("_last_synced_armature", source)
        self.assertIn("if armature_key == _last_synced_armature", source)
        self.assertIn("def reset_active_skeleton_sync", source)
        self.assertGreaterEqual(source.count("reset_active_skeleton_sync()"), 3)

    def test_asset_list_uses_shared_indentation_helper(self):
        source = UI_ASSETS.read_text(encoding="utf-8")
        self.assertIn("def _draw_indent", source)
        self.assertGreaterEqual(source.count("_draw_indent(row, item.depth"), 3)

    def test_tree_indent_uses_one_fixed_separator_per_level(self):
        source = UI_ASSETS.read_text(encoding="utf-8")
        helper_start = source.index("def _draw_indent")
        helper_end = source.index("# ============================================================", helper_start)
        helper = source[helper_start:helper_end]
        self.assertIn("for _ in range(depth):", helper)
        self.assertIn("row.separator(factor=factor)", helper)
        self.assertNotIn("factor=depth * factor", helper)

    def test_scans_select_first_folder_and_library_list_has_own_index(self):
        manager_source = ANIM_MANAGER.read_text(encoding="utf-8")
        ui_source = UI.read_text(encoding="utf-8")
        self.assertIn("active_library_index: bpy.props.IntProperty", manager_source)
        self.assertGreaterEqual(
            manager_source.count(
                "next((i for i, item in enumerate(manager.tree_items) if item.item_type == 'FOLDER'), 0)"
            ),
            2,
        )
        library_start = ui_source.index('"ASSETS_UL_LibraryTree"')
        library_end = ui_source.index("col_btns", library_start)
        self.assertIn('manager, "active_library_index"', ui_source[library_start:library_end])

    def test_animation_library_expansion_identity_uses_library_directory(self):
        source = ANIM_MANAGER.read_text(encoding="utf-8")
        anim_library_start = source.index("entry.item_type = 'ANIM_LIB'")
        anim_library_end = source.index("for skel, folder_tree", anim_library_start)
        self.assertIn("entry.blend_path = abs_path", source[anim_library_start:anim_library_end])
        self.assertNotIn("entry.blend_path = paths[0]", source[anim_library_start:anim_library_end])

    def test_animation_operators_expose_clear_descriptions(self):
        source = ANIM_MANAGER.read_text(encoding="utf-8")
        self.assertIn('bl_description = "将所选动画设为活动骨架的当前动作"', source)
        self.assertIn('bl_description = "将所选动画应用到活动骨架"', source)

    def test_visible_animation_lookup_has_input_keyed_cache(self):
        ui_source = UI.read_text(encoding="utf-8")
        manager_source = ANIM_MANAGER.read_text(encoding="utf-8")
        self.assertIn("_VISIBLE_ANIMS_CACHE", ui_source)
        self.assertIn("manager.tree_revision", ui_source)
        self.assertIn("manager.tree_revision += 1", manager_source)

    def test_volume_pattern_only_matches_numbered_volume_suffixes(self):
        source = ANIM_MANAGER.read_text(encoding="utf-8")
        self.assertIn("r'(.+)_Vol_\\d+$'", source)

    def test_ueanim_import_persists_assets_without_redraw_timer(self):
        source = PIPELINE_STAGES.read_text(encoding="utf-8")
        # The redraw_timer UI hack was removed from the import stage; guard
        # against reintroduction.  Persisting imported assets must stay part
        # of the stage's result contract on its own.
        self.assertNotIn("bpy.ops.wm.redraw_timer", source)
        self.assertIn("context.imported_assets = collection.to_dict()", source)

    def test_library_build_initializes_pending_entries_before_manifest_loading(self):
        source = PIPELINE_STAGES.read_text(encoding="utf-8")
        recovery = source.index("recover_library_transaction(manifest_path)")
        group_start = source.index("for skel_name, actions in skeleton_groups.items():")
        pending_init = source.index("pending_entries = []", group_start)
        publication = source.index("publish_rebuild_replacement_set(", group_start)
        self.assertLess(recovery, group_start)
        self.assertLess(pending_init, publication)


if __name__ == "__main__":
    unittest.main()
