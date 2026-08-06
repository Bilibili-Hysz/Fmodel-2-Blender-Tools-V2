# Assets UI Stability Implementation Plan

> [!NOTE]
> This document may not reflect the current implementation.
> See the final report for up-to-date state:
> [Final Report](../reports/assets-ui-stability.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining Assets UI stability and performance improvements without changing the user-facing asset-library workflow.

**Architecture:** Keep state local to `Assets.Anims.anim_manager`: bound thumbnail-path and manifest caches, and avoid repeated work in the depsgraph handler. Keep UI-only changes in `Assets.ui_assets` and `ui.py`. Cache invalidation is driven by file modification times or the exact view inputs so stale library data is never reused after a rescan or a changed filter.

**Tech Stack:** Blender Python API (`bpy`), Blender PropertyGroup/UIList/Panel APIs, Python standard library (`os`, `json`).

## Global Constraints

- Do not change the existing asset-library, animation-application, or model-import file formats.
- Preserve current UI layout and interaction semantics unless an explicitly listed stability fix requires a change.
- Do not add external dependencies.
- Keep cache data process-local; it must never be persisted into `.blend` files.
- Retain the existing user modifications in `Fmodel/Pipeline/stages.py` and only modify Assets/UI files for this plan.
- Verify Python syntax for every edited source file with `python -c "import ast; ..."`.

---

### Task 1: Bound Thumbnail and Manifest Caches

**Covers:** P2-1, P2-2

**Files:**
- Modify: `Assets/Anims/anim_manager.py:26-76, 268-322, 1140-1175`
- Test: `test_assets_ui_stability.py`

**Interfaces:**
- Consumes: thumbnail paths, `_scan_library_directory(abs_dir, lib_name)` calls, Blender add-on register lifecycle.
- Produces: `_load_manifest(manifest_path) -> dict | None`, `clear_asset_caches() -> None`; both icon and manifest caches are bounded and cleared on add-on unload.

- [ ] **Step 1: Write failing tests for file-cache behavior**

```python
def test_manifest_cache_reloads_when_mtime_changes(tmp_path):
    manifest = tmp_path / "Library_manifest.json"
    manifest.write_text('{"A": {}}', encoding="utf-8")
    assert module._load_manifest(str(manifest)) == {"A": {}}
    manifest.write_text('{"B": {}}', encoding="utf-8")
    os.utime(manifest, None)
    assert module._load_manifest(str(manifest)) == {"B": {}}

def test_clear_asset_caches_discards_path_and_manifest_entries():
    module._PATH_EXISTS_CACHE["x"] = True
    module._MANIFEST_CACHE["y"] = (1, {})
    module.clear_asset_caches()
    assert module._PATH_EXISTS_CACHE == {}
    assert module._MANIFEST_CACHE == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: FAIL because `_load_manifest`, `_MANIFEST_CACHE`, and `clear_asset_caches` do not exist.

- [ ] **Step 3: Add bounded cache helpers**

```python
_CACHE_LIMIT = 512
_MANIFEST_CACHE = {}

def _remember(cache, key, value):
    cache[key] = value
    while len(cache) > _CACHE_LIMIT:
        cache.pop(next(iter(cache)))

def _load_manifest(manifest_path):
    try:
        modified = os.path.getmtime(manifest_path)
    except OSError:
        return None
    cached = _MANIFEST_CACHE.get(manifest_path)
    if cached and cached[0] == modified:
        return cached[1]
    try:
        with open(manifest_path, "r", encoding="utf-8") as manifest_file:
            data = json.load(manifest_file)
    except (OSError, json.JSONDecodeError):
        return None
    _remember(_MANIFEST_CACHE, manifest_path, (modified, data))
    return data
```

Use `_remember()` for `_PATH_EXISTS_CACHE`, replace direct manifest file reads with `_load_manifest()`, and call `clear_asset_caches()` from `unregister()` after icon cleanup.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: PASS.

- [ ] **Step 5: Run syntax verification**

Run: `python -c "import ast; ast.parse(open('Assets/Anims/anim_manager.py', encoding='utf-8').read()); print('syntax ok')"`

Expected: `syntax ok`.

### Task 2: Avoid Redundant Active-Skeleton Synchronization

**Covers:** P2-4

**Files:**
- Modify: `Assets/Anims/anim_manager.py:948-970`
- Test: `test_assets_ui_stability.py`

**Interfaces:**
- Consumes: `bpy.app.handlers.depsgraph_update_post`, active armature object, `AnimManagerProps.active_skeleton`.
- Produces: `auto_sync_active_skeleton(scene, depsgraph)` avoids assigning the enum unless the active armature identity changed.

- [ ] **Step 1: Write a failing test for unchanged active object**

```python
def test_active_skeleton_sync_skips_unchanged_object(fake_bpy):
    module._last_synced_armature = "Rig"
    fake_bpy.context.active_object.name = "Rig"
    module.auto_sync_active_skeleton(fake_bpy.context.scene, None)
    assert fake_bpy.context.scene.fmodel_anim_manager.assignment_count == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: FAIL because the current handler always evaluates and can assign on every depsgraph update.

- [ ] **Step 3: Cache the processed armature identity**

```python
_last_synced_armature = None

@persistent
def auto_sync_active_skeleton(scene, depsgraph):
    global _last_synced_armature
    obj = bpy.context.active_object
    if not obj or obj.type != "ARMATURE":
        _last_synced_armature = None
        return
    if obj.name == _last_synced_armature:
        return
    _last_synced_armature = obj.name
    # Existing manager/skeleton lookup and assignment follows.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: PASS.

- [ ] **Step 5: Run syntax verification**

Run: `python -c "import ast; ast.parse(open('Assets/Anims/anim_manager.py', encoding='utf-8').read()); print('syntax ok')"`

Expected: `syntax ok`.

### Task 3: Reduce UI Draw Work and Clarify Action Controls

**Covers:** P2-3, P3-1

**Files:**
- Modify: `Assets/ui_assets.py:50-53, 165-191, 233-241, 269-283`
- Modify: `ui.py:844-865`
- Test: `test_assets_ui_stability.py`

**Interfaces:**
- Consumes: `AnimTreeItem.depth`, existing animation operators.
- Produces: single-call indentation helper and descriptive operator labels/tooltips for preview, replace, and NLA application.

- [ ] **Step 1: Write a failing test for indentation helper**

```python
def test_draw_indent_emits_one_separator_per_depth(fake_layout):
    module._draw_indent(fake_layout, 3)
    assert fake_layout.separators == [3.0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: FAIL because `_draw_indent` does not exist.

- [ ] **Step 3: Add minimal drawing helper and descriptive labels**

```python
def _draw_indent(row, depth, factor=1.0):
    if depth:
        row.separator(factor=depth * factor)
```

Replace repeated separator loops with `_draw_indent()`. Use concise visible labels in the card grid (`预览`, `应用`) and set `bl_description` on `ANIM_OT_PreviewAnim` and `ANIM_OT_ApplyAnim` so icon-only list controls expose meaningful tooltips without changing their density.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: PASS.

- [ ] **Step 5: Run syntax verification**

Run: `python -c "import ast; [ast.parse(open(p, encoding='utf-8').read()) for p in ('Assets/ui_assets.py', 'ui.py', 'Assets/Anims/anim_manager.py')]; print('syntax ok')"`

Expected: `syntax ok`.

### Task 4: Cache Visible Animation Indices by View Inputs

**Covers:** P3-3

**Files:**
- Modify: `ui.py:834-841`
- Test: `test_assets_ui_stability.py`

**Interfaces:**
- Consumes: `manager.tree_items`, active folder ID, active skeleton, search text.
- Produces: `_get_visible_anims(manager, active_folder_id, active_skel)` returns cached indices only when all view inputs and item count match.

- [ ] **Step 1: Write a failing test for identical view inputs**

```python
def test_visible_animation_indices_are_reused_for_same_view(manager):
    first = ui._get_visible_anims(manager, "Root", "Rig")
    manager.iteration_count = 0
    second = ui._get_visible_anims(manager, "Root", "Rig")
    assert second == first
    assert manager.iteration_count == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: FAIL because the function always iterates through the collection.

- [ ] **Step 3: Add a module-local input-keyed cache**

```python
_VISIBLE_ANIMS_CACHE = {}

def _get_visible_anims(manager, active_folder_id, active_skel):
    search = manager.search_filter.lower().strip()
    key = (id(manager), len(manager.tree_items), active_folder_id, active_skel, search)
    cached = _VISIBLE_ANIMS_CACHE.get(key)
    if cached is not None:
        return cached
    visible = [
        i for i, item in enumerate(manager.tree_items)
        if item.item_type == "ANIM" and item.parent_id == active_folder_id
        and item.skeleton == active_skel
        and (not search or search in item.name.lower())
    ]
    _VISIBLE_ANIMS_CACHE.clear()
    _VISIBLE_ANIMS_CACHE[key] = visible
    return visible
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: PASS.

- [ ] **Step 5: Run syntax verification**

Run: `python -c "import ast; ast.parse(open('ui.py', encoding='utf-8').read()); print('syntax ok')"`

Expected: `syntax ok`.

### Task 5: Keep Existing Localization Coverage Intact

**Covers:** P3-2

**Files:**
- Modify: `Assets/Anims/operators_asset.py:1-155`
- Test: `test_assets_ui_stability.py`

**Interfaces:**
- Consumes: `core.i18n.t`, existing asset-build dialog labels.
- Produces: asset-build dialog labels pass through the current translation system.

- [ ] **Step 1: Write a failing source-level test for i18n import**

```python
def test_asset_builder_imports_the_translation_helper():
    source = Path("Assets/Anims/operators_asset.py").read_text(encoding="utf-8")
    assert "from ...core.i18n import t" in source
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: FAIL because `operators_asset.py` does not import `t`.

- [ ] **Step 3: Use the current translation helper for new dialog labels**

```python
from ...core.i18n import t

box_dir.label(text=t("资产路径配置"), icon="FILE_FOLDER")
box_flt.label(text=t("安全与匹配机制"), icon="FILTER")
```

Only replace text that has an existing translation key; leave unmatched operational copy unchanged to avoid introducing a partial or misleading translation layer.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: PASS.

- [ ] **Step 5: Run syntax verification**

Run: `python -c "import ast; ast.parse(open('Assets/Anims/operators_asset.py', encoding='utf-8').read()); print('syntax ok')"`

Expected: `syntax ok`.

### Task 6: Verify Existing Volume-Name Handling Before Changing It

**Covers:** P3-4

**Files:**
- Test: `test_assets_ui_stability.py`

**Interfaces:**
- Consumes: `_get_base_name(filename)` and per-volume manifest pairing in `_scan_library_directory()`.
- Produces: a regression test establishing that files with `_Vol_` in the base name are not misgrouped when their own manifest is present.

- [ ] **Step 1: Write a regression test for literal `_Vol_` asset names**

```python
def test_scan_preserves_literal_vol_in_library_name(tmp_path):
    write_library_asset(tmp_path, "Combat_Volume.blend")
    entries, _ = module._scan_library_directory(str(tmp_path), "Library")
    assert entries[0][0] == "Combat_Volume"
```

- [ ] **Step 2: Run the test to verify it fails only if behavior is incorrect**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: PASS if current regex behavior is already correct; otherwise FAIL with the expected incorrect base name.

- [ ] **Step 3: Implement only if the regression test fails**

```python
def _get_base_name(filename):
    name = os.path.splitext(filename)[0]
    match = _VOL_PATTERN.match(name)
    return match.group(1) if match else name
```

No code change is required when the test passes; the existing end-anchored regex correctly distinguishes `Combat_Volume` from a true `*_Vol_2` volume suffix.

- [ ] **Step 4: Run the full focused test suite**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: PASS.

- [ ] **Step 5: Run final syntax verification**

Run: `python -c "import ast; [ast.parse(open(p, encoding='utf-8').read()) for p in ('Assets/Anims/anim_manager.py', 'Assets/Anims/operators_asset.py', 'Assets/ui_assets.py', 'ui.py')]; print('all source syntax checks passed')"`

Expected: `all source syntax checks passed`.
