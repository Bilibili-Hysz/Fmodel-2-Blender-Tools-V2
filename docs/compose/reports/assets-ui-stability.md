---
feature: assets-ui-stability
status: delivered
specs: []
plans:
  - docs/compose/plans/2026-07-15-assets-ui-stability.md
branch: master
commits: uncommitted
---

# Assets UI Stability - Final Report

## What Was Built

The Assets animation browser now bounds its process-local thumbnail and manifest caches, reloads manifests when their modification time changes, and clears both caches during add-on unload. Asset and local scans increment a content revision so view-derived animation results cannot survive a same-sized rescan.

The active-armature depsgraph handler now avoids repeated skeleton work for an unchanged armature while scan operations explicitly reset its deduplication state. This ensures the active armature is evaluated against a newly rebuilt skeleton collection.

The UI uses a single indentation helper for tree rows and supplies clear preview/apply text in grid cards plus operator tooltips. Animation-grid index lookup is cached only for the current manager, scan revision, active folder, skeleton, and search text.

## Architecture

`Assets/Anims/anim_manager.py` owns scan and runtime cache state. `_PATH_EXISTS_CACHE` and `_MANIFEST_CACHE` are capped through `_remember`; `_load_manifest()` validates cached JSON against `os.path.getmtime()`, while `clear_asset_caches()` is invoked during unregister. `AnimManagerProps.tree_revision` changes whenever either scan operator rebuilds its collections.

The active-skeleton handler stores the current armature identity and resolved skeleton identifier in `_last_synced_armature`. `reset_active_skeleton_sync()` clears that key at the start of both scan paths, allowing the next depsgraph update to reconcile the current active armature with the rebuilt `skeleton_items`.

`ui.py` maintains `_VISIBLE_ANIMS_CACHE` locally and keys results on the manager identity, `tree_revision`, folder, skeleton, and normalized search filter. `Assets/ui_assets.py` keeps existing tree filtering intact and uses `_draw_indent()` only for row spacing.

### Design Decisions

We chose bounded process-local caches because thumbnail and manifest data should improve repeated scans without becoming `.blend` state or growing indefinitely.

We chose an explicit scan-lifecycle reset for active-skeleton deduplication because object identity alone cannot represent a rebuilt `skeleton_items` collection.

We chose a scan revision rather than collection length for visible-animation caching because a rescan can replace every row while retaining the same number of rows.

## Usage

No new user configuration is required. The existing Assets scan buttons continue to populate the animation tree. Repeated scans reuse unchanged manifest data automatically, refresh modified manifests on the next scan, and keep grid results aligned with the rebuilt tree.

Preview and apply controls retain their existing behavior. Grid cards now label the two actions, and icon-based tree controls expose operator descriptions through Blender tooltips.

## Verification

Ran `python -m unittest test_assets_ui_stability.py`: 6 tests passed. The suite covers cache helper presence and invalidation contracts, active-skeleton deduplication and scan reset, draw helper/action descriptions, visible-animation cache revision use, and terminal `_Vol_<number>` behavior.

Ran AST parsing across `Assets/Anims/anim_manager.py`, `Assets/Anims/operators_asset.py`, `Assets/ui_assets.py`, `ui.py`, and `test_assets_ui_stability.py`: passed.

Ran `git diff --check` over the scoped Assets/UI files: no whitespace errors. Blender runtime integration remains a residual testing gap because the automated suite is source-level and Blender-independent.

## Journey Log

- [lesson] The existing `import_curves` property and builder wiring were already present, so no duplicate configuration path was added.
- [pivot] Visible-animation caching initially used collection length; it was revised to use an explicit scan revision after identifying same-length rescan staleness.
- [lesson] Active-armature deduplication needs invalidation at scan boundaries as well as when Blender changes the active object.
- [lesson] The current skeleton-scoped ancestor filtering in `Assets/ui_assets.py` was a concurrent change and was preserved rather than replaced.

## Source Materials

| File | Role | Notes |
|------|------|-------|
| `docs/compose/plans/2026-07-15-assets-ui-stability.md` | Implementation plan | Superseded by this final state report. |
| `test_assets_ui_stability.py` | Regression harness | Blender-independent focused tests. |
| `Assets/Anims/anim_manager.py` | Runtime state and scanning | Cache, scan revision, and skeleton synchronization implementation. |
| `Assets/ui_assets.py` | Tree UI list rendering | Indentation helper and preserved filtering behavior. |
| `ui.py` | Animation grid UI | Visible-animation cache and labeled grid controls. |
