"""Lightweight regression checks for Assets UI code without Blender."""

import ast
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
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
        library_end = ui_source.index("rows=5", library_start)
        self.assertIn('manager, "active_library_index"', ui_source[library_start:library_end])

    def test_animation_library_expansion_identity_uses_library_directory(self):
        source = ANIM_MANAGER.read_text(encoding="utf-8")
        anim_library_start = source.index("entry.item_type = 'ANIM_LIB'")
        anim_library_end = source.index("for skel, folder_tree", anim_library_start)
        self.assertIn("entry.blend_path = abs_path", source[anim_library_start:anim_library_end])
        self.assertNotIn("entry.blend_path = paths[0]", source[anim_library_start:anim_library_end])

    def test_animation_operators_expose_clear_descriptions(self):
        source = ANIM_MANAGER.read_text(encoding="utf-8")
        self.assertIn('bl_description = "将所选动画设为工作目标骨架的当前动作"', source)
        self.assertIn('bl_description = "将所选动画应用到工作目标骨架"', source)

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


def load_ui_definitions(path, names, namespace):
    nodes = [node for node in ast.parse(path.read_text(encoding="utf-8")).body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class RecordingLayout:
    def __init__(self):
        self.calls = []
        self.enabled = True

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return (self, None) if name == "panel" else SimpleNamespace() if name == "operator" else self
        return call


class AssetPanelBehaviorTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "Assets" / "Anims" / "ui_custom_grid.py"
        spec = importlib.util.spec_from_file_location("asset_grid_test", path)
        self.grid = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.grid)
        self.bpy = SimpleNamespace(
            types=SimpleNamespace(Panel=object, UIList=object, Operator=object),
            props=SimpleNamespace(IntProperty=lambda **kwargs: None),
            path=SimpleNamespace(abspath=lambda value: value),
        )

    def item(self, kind="ANIM", name="Idle", **kwargs):
        values = dict(item_type=kind, name=name, node_id=name, parent_id="", skeleton="Hero",
                      depth=0, is_expanded=True, volume_count=0, blend_path="", source_key="")
        values.update(kwargs)
        return SimpleNamespace(**values)

    def manager(self, items, **kwargs):
        values = dict(tree_items=items, tree_revision=1, active_skeleton="ALL", search_filter="",
                      active_folder_index=-1, active_library_index=0, active_item_index=-1,
                      grid_columns=2, grid_row_count=2, grid_page=1)
        values.update(kwargs)
        return SimpleNamespace(**values)

    def namespace(self):
        return dict(bpy=self.bpy, os=os, t=lambda text: text, ui_custom_grid=self.grid,
                    _VISIBLE_ANIMS_CACHE={})

    def test_library_settings_exposes_unscanned_libraries_and_selected_path(self):
        namespace = load_ui_definitions(UI, {"FMODEL_PT_LibrarySettings"}, self.namespace())
        props = SimpleNamespace(project_libs=[SimpleNamespace(name="New", path="")], active_lib_index=0)
        manager = self.manager([])
        layout = RecordingLayout()
        namespace["FMODEL_PT_LibrarySettings"].draw(
            SimpleNamespace(layout=layout),
            SimpleNamespace(scene=SimpleNamespace(fmodel_assets=props, fmodel_anim_manager=manager)),
        )
        self.assertTrue(any(name == "template_list" and args[2:6] == (props, "project_libs", props, "active_lib_index")
                            for name, args, kwargs in layout.calls))
        self.assertTrue(any(name == "prop" and args[:2] == (props.project_libs[0], "path")
                            for name, args, kwargs in layout.calls))

    def test_project_selection_sync_does_not_replace_animation_folder_selection(self):
        namespace = load_ui_definitions(ANIM_MANAGER, {"_sync_manager_library_index"}, self.namespace())
        manager = self.manager([
            self.item("LIBRARY", "Same", blend_path="/first"), self.item("FOLDER", "Moves"),
            self.item("LIBRARY", "Same", blend_path="/second"),
        ], active_folder_index=1)
        props = SimpleNamespace(project_libs=[SimpleNamespace(name="Same", path="/second")], active_lib_index=0)
        namespace["_sync_manager_library_index"](
            SimpleNamespace(scene=SimpleNamespace(fmodel_assets=props, fmodel_anim_manager=manager)))
        self.assertEqual(manager.active_library_index, 2)
        self.assertEqual(manager.active_folder_index, 1)

    def test_grid_and_list_respect_global_skeleton_and_cache_changes(self):
        namespace = load_ui_definitions(UI, {"_get_visible_anims"}, self.namespace())
        manager = self.manager([self.item(parent_id="Moves")], active_skeleton="Hero")
        lookup = namespace["_get_visible_anims"]
        self.assertEqual(lookup(manager, "Moves", "Hero"), [0])
        manager.active_skeleton = "Enemy"
        self.assertEqual(lookup(manager, "Moves", "Hero"), [])
        filters = load_ui_definitions(UI_ASSETS, {"ASSETS_UL_AnimGrid"}, self.namespace())
        manager.tree_items.insert(0, self.item("FOLDER", "Moves"))
        manager.active_folder_index = 0
        ui_list = filters["ASSETS_UL_AnimGrid"]()
        ui_list.bitflag_filter_item = 1
        flags, _ = ui_list.filter_items(SimpleNamespace(scene=SimpleNamespace(fmodel_anim_manager=manager)), manager, "tree_items")
        self.assertEqual(flags, [0, 0])

    def test_local_root_actions_and_search_under_collapsed_folders_are_visible(self):
        namespace = load_ui_definitions(UI_ASSETS, {"ASSETS_UL_AnimTree"}, self.namespace())
        ui_list = namespace["ASSETS_UL_AnimTree"]()
        ui_list.bitflag_filter_item = 1
        for items, search in (
            ([self.item()], ""),
            ([self.item("FOLDER", "Moves", is_expanded=False), self.item(parent_id="Moves", depth=2)], "idle"),
            ([self.item("LIBRARY", "Lib", skeleton="", is_expanded=False),
              self.item("FOLDER", "Moves", parent_id="Lib"), self.item(parent_id="Moves", depth=2)], ""),
        ):
            with self.subTest(search=search, root=items[0].item_type):
                manager = self.manager(items, search_filter=search)
                flags, _ = ui_list.filter_items(SimpleNamespace(scene=SimpleNamespace(fmodel_anim_manager=manager)), manager, "tree_items")
                self.assertEqual(flags[-1], 1)

    def test_library_inventory_does_not_render_animation_rows_as_blank_volumes(self):
        namespace = load_ui_definitions(UI_ASSETS, {"ASSETS_UL_LibraryTree"}, self.namespace())
        ui_list = namespace["ASSETS_UL_LibraryTree"]()
        ui_list.bitflag_filter_item = 1
        manager = self.manager([self.item(depth=2)])
        flags, _ = ui_list.filter_items(None, manager, "tree_items")
        self.assertEqual(flags, [0])

    def test_page_navigation_uses_effective_page_and_stops_at_last_page(self):
        namespace = load_ui_definitions(ANIM_MANAGER, {"ANIM_OT_GridPagePrev", "ANIM_OT_GridPageNext"}, self.namespace())
        manager = self.manager([self.item(name=f"Action{i}") for i in range(12)], grid_page=99)
        context = SimpleNamespace(scene=SimpleNamespace(fmodel_anim_manager=manager))
        namespace["ANIM_OT_GridPagePrev"]().execute(context)
        self.assertEqual(manager.grid_page, 2)
        namespace["ANIM_OT_GridPageNext"]().execute(context)
        namespace["ANIM_OT_GridPageNext"]().execute(context)
        self.assertEqual(manager.grid_page, 3)

    def test_detail_rejects_negative_selection_before_loading_metadata(self):
        namespace = load_ui_definitions(UI, {"_draw_animation_detail"}, self.namespace())
        manager = self.manager([self.item()])
        layout = RecordingLayout()
        namespace["_draw_animation_detail"](layout,
            SimpleNamespace(scene=SimpleNamespace(fmodel_anim_manager=manager)))
        self.assertTrue(any(name == "label" and kwargs.get("text") == "选择一个动画"
                            for name, args, kwargs in layout.calls))


if __name__ == "__main__":
    unittest.main()
