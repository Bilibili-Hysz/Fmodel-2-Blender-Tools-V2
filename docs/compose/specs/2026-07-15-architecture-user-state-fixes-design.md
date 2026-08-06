# Architecture User-State And Import Correctness Fixes Design

## [S1] Scope

Resolve remaining user-triggered pipeline, import/export, model-save, and asset-tree state risks. Vendor and aggregate registration transactions remain out of scope for this iteration.

## [S2] Pipeline Input And Publication Boundaries

Applying an explicit indexed animation selection invalidates current wizard scan/import/library results after the new selection is committed. Step 4 remains unavailable until a new scan produces a matching manifest.

Optional model asset export occurs only after an individual animation import task and its library-building stage both succeed. A failed task produces no final model export artifacts.

## [S3] Import And Export Result/Context Integrity

Batch UEAnim, UEPose, and PSA import operators return `CANCELLED` when every requested file fails, while mixed-success batches remain `FINISHED` and report failures.

Model-save and individual glTF/FBX export operations snapshot and restore Blender selection and active object on success and failure. Model-save additionally removes temporary duplicated scene data on every exit path and converts operational errors into `CANCELLED` reports.

## [S4] Asset Library UI State Integrity

Every active-library index dereference requires `0 <= index < len(libraries)`; negative indexes never select the final library. Removing a library clamps the active index to a valid remaining position or zero.

Automatic animation rescans retain `is_expanded` for rebuilt nodes with stable identities; newly discovered nodes retain their existing default expansion state.

## [S5] Validation

Regression tests cover indexed-selection invalidation; failed versus successful model publication; all-failed versus partial animation batches; selection/active-object restoration after model-save and individual exports; negative library indexes; and expansion-state persistence over a rescan.
