# Default Animation Folder Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the detail animation list selects a valid folder after every scan and is unaffected by selecting entries in the library-settings list.

**Architecture:** `AnimManagerProps` receives a separate `active_library_index` for `ASSETS_UL_LibraryTree`. Both scan operators select the first `FOLDER` in their rebuilt tree for `active_folder_index`, so detail filtering always has a valid folder and skeleton immediately after scanning.

**Tech Stack:** Python 3, Blender `bpy` PropertyGroup/UIList API, Blender background mode, `unittest`.

## Global Constraints

- Preserve existing library tree data, folder hierarchy, and action filtering rules.
- Do not change root-level action behavior, recursive folder display, or search behavior in this fix.
- Keep library-settings selection independent from detail folder selection.
- Deploy only to `C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.1\scripts\addons\FModel-2-Blender-Tools` after Blender exits.

---

### Task 1: Select a Valid Detail Folder After Scan

**Files:**
- Modify: `Assets/Anims/anim_manager.py:221-224, 375-478, 487-525`
- Modify: `ui.py:473-477`
- Modify: `test_assets_ui_stability.py`

**Interfaces:**
- Consumes: rebuilt `manager.tree_items` containing `AnimTreeItem.item_type` values.
- Produces: `manager.active_folder_index` pointing to the first `FOLDER`; `manager.active_library_index` for the library settings list.

- [ ] **Step 1: Write the failing source-level regression test**

```python
def test_scans_select_first_folder_and_library_list_has_own_index(self):
    manager_source = ANIM_MANAGER.read_text(encoding="utf-8")
    ui_source = UI.read_text(encoding="utf-8")
    self.assertIn("active_library_index: bpy.props.IntProperty", manager_source)
    self.assertGreaterEqual(manager_source.count("next((i for i, item in enumerate(manager.tree_items) if item.item_type == 'FOLDER'), 0)"), 2)
    library_list = ui_source[ui_source.index('"ASSETS_UL_LibraryTree"'):ui_source.index("col_btns", ui_source.index('"ASSETS_UL_LibraryTree"'))]
    self.assertIn('manager, "active_library_index"', library_list)
```

- [ ] **Step 2: Run the regression test and confirm it fails**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: failure because `active_library_index` and first-folder selection are absent.

- [ ] **Step 3: Implement the focused selection fix**

```python
active_library_index: bpy.props.IntProperty(default=0)

# After each scan finishes rebuilding manager.tree_items:
manager.active_folder_index = next(
    (i for i, item in enumerate(manager.tree_items) if item.item_type == 'FOLDER'),
    0,
)
```

Use `manager.active_library_index` only for the `ASSETS_UL_LibraryTree` `template_list` active property.

- [ ] **Step 4: Run regression, syntax, and Blender-background reproduction checks**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: all tests pass.

Run: `python -c "import ast; [ast.parse(open(p, encoding='utf-8').read()) for p in ('Assets/Anims/anim_manager.py', 'ui.py', 'test_assets_ui_stability.py')]; print('animation selection syntax passed')"`

Expected: `animation selection syntax passed`.

Run: Blender 5.1 background script that registers the deployed add-on, creates `LIBRARY → FOLDER → ANIM`, applies the selection expression, and confirms the folder selection yields `['Walk']`.

Expected: selected type is `FOLDER`; visible list is `['Walk']`.

- [ ] **Step 5: Deploy verified files after Blender exits**

Run: copy `Assets/Anims/anim_manager.py` and `ui.py` to the installed `FModel-2-Blender-Tools` directory after making per-file backups.

Expected: deployed files match repository SHA-256 and pass AST parsing.
