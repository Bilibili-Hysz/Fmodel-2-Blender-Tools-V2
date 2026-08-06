---
feature: fmodel-tools-next-project-index
status: delivered
specs:
  - docs/compose/specs/2026-07-17-fmodel-tools-next-project-index-design.md
plans:
  - docs/compose/plans/2026-07-17-fmodel-tools-next-project-index.md
branch: feature/fmodel-tools-next
commits: f93aa67f988743cbfe8d39c96426356530a14130..working-tree
---

# FModel Tools Next Project Index - Final Report

## What Was Built

FModel Tools Next adds a project-first asset index ahead of the existing import pipeline. Each selected project root owns a derived SQLite database at `.fmodel/project-assets.sqlite`. The index discovers FModel animations, resolves their skeleton relationships, supports safe incremental updates, and gives users an indexed skeleton-and-animation selector before animation import.

The selector produces the existing pipeline's normal `DictionaryManifest`; it does not replace source parsing, checkpoints, bundle sidecars, or library indexes. The existing per-skeleton publication, mounting, scan, and task-scoped cleanup contracts remain the authority after selection.

## Architecture

`core/project_index.py` is a Blender-independent SQLite layer. It owns canonical project-root paths, strict schema validation, generation publication, scan-root isolation, SHA-256 fingerprinting for each supported source plus sibling JSON metadata, and indexed animation queries. A schema/object mismatch fails closed and requires rebuilding the derived index.

`Fmodel/project_assets.py` adapts this layer to Blender. It owns scene settings, Open/Scan/Rebuild index actions, selector refresh state, and `fmodel.apply_indexed_animation_selection`. The selector writes exact canonical source paths, skeleton, and project root to registered `WizardState` fields. Invalid roots, unavailable databases, stale choices, query errors, and valid empty results are reported as distinct states.

`Fmodel/Pipeline/steps/step3_scan.py` projects an indexed selection into a standard `DictionaryManifest` and persists it. `runner.py` carries the indexed selection identity through later wizard stages, regenerates and revalidates the manifest before planning, and rejects changed/deleted files before import or publication. Without indexed state, the legacy directory scanning path remains unchanged.

### Design Decisions

- The index is derived state, not an asset-library replacement, because Blender datablocks, published bundles, and resume checkpoints have separate lifecycle and trust requirements.
- File identity includes a SHA-256 hash of both the source and sibling metadata. Size and mtime alone can miss same-size timestamp-preserved changes.
- Model-to-skeleton links require explicit metadata. Unknown model formats may be recorded but never gain a guessed skeleton association.
- Indexed source identity is carried by the shared wizard config because adding it only at scan time would let Step 4 invalidate the projected manifest.

## Usage

1. Open the FModel sidebar and use **Project Settings** before the Toolbox.
2. Set the project root. Use **Open** to inspect an existing index, **Scan** to create or incrementally refresh it, and **Rebuild** only to replace an incompatible index. The database is stored at `<project-root>/.fmodel/project-assets.sqlite`.
3. In **Animation Pipeline**, choose an indexed skeleton, refresh its indexed animation choices, check one or more animations, then apply the selection.
4. Continue the existing animation wizard. Step 3 persists the selected indexed manifest; Step 4 revalidates the files and performs the existing import, bundle publication, mounting, and library scan.
5. Use the existing legacy directory source controls when intentionally running the old direct-directory workflow.

## Verification

- `python -m pytest -q`: `347 passed, 15 subtests passed`.
- `F:\Blender\Software\blender-5.1.2-windows-x64\blender.exe --background --factory-startup --python investigation\task7_verification\blender_task7_integration.py`: passed.
- The Blender probe updates a real project index, uses the registered selector/apply operator, executes registered Step 3 and Step 4 operators, publishes `IndexedWizard_Hero.blend` with `IndexedWizard__library_manifest.json`, and mounts `[FModel_Auto] IndexedWizard`.
- Host tests cover strict schema rejection, generation rollback, root isolation, SHA content and sidecar invalidation, empty/error selector states, stale indexed selections, legacy flow compatibility, and indexed Step 3 to Step 4 persistence.

## Journey Log

> Brief notes on what informed the final design. Not required reading.

- [lesson] SQLite schema checks must compare canonical database objects, not just schema version or visible table columns; generated columns, partial indexes, and triggers can otherwise evade validation.
- [lesson] Test monkeypatches on shared classmethods must be scoped. Two direct assignments leaked into the resume suite and caused aggregate-only failures.
- [pivot] Incremental scanning uses SHA-256 for source plus sibling JSON after size/mtime-only invalidation proved insufficient for preserved-stat edits.
- [lesson] A Blender probe must drive the registered selector operator, not seed wizard state directly, to prove the user-operable handoff.

## Source Materials

| File | Role | Notes |
|------|------|-------|
| `docs/compose/specs/2026-07-17-fmodel-tools-next-project-index-design.md` | Approved design | Project-first boundaries and scope |
| `docs/compose/plans/2026-07-17-fmodel-tools-next-project-index.md` | Implementation plan | Completed Next first delivery |
