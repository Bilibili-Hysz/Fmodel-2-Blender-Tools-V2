---
feature: unified-library-multi-skeleton
status: delivered
specs: []
plans:
  - docs/compose/plans/2026-07-15-release-safety-architecture.md
branch: feature/unified-library-multi-skeleton
commits: uncommitted
---

# Unified Library and Multi-Skeleton Import - Final Report

## What Was Built

The Blender add-on now manages animation libraries as durable per-skeleton bundle pairs backed by a versioned unified v2 index. Each bundle has exact Action, skeleton, catalog, source, thumbnail, and persistent-library identity. The scanner, UI, Preview, Apply, repair, import, and pipeline stages retain that identity rather than resolving Actions by globally ambiguous names.

Multi-skeleton imports are modeled as explicit source-skeleton to target-Armature tasks. A complete mapping is required before work starts, task state is checkpointed after every meaningful transition, and retries retain task-local source and bundle provenance. The pipeline treats bundles, sidecars, indexes, catalog resources, thumbnails, staging files, and checkpoint state as recoverable publication boundaries.

## Architecture

`core/types.py`, `core/library_files.py`, and `core/context.py` define the v2 manifest, path validation, pair/index publication, recovery journals, and crash-durable external pipeline checkpoint. The external `.fmodel_pipeline_checkpoint.json` is atomically written and fsynced before action unload or staging cleanup; all entry points restore an output-matching checkpoint before Blender Text fallback.

`Fmodel/Pipeline/stages.py`, `steps/runner.py`, and `manager.py` execute isolated task scopes, preflight all generated output names, publish resources through a journaled replacement transaction, persist authoritative `source_bundle` provenance before cleanup, and finalize only after a successful mount and scan. `Assets/Anims/library_scanner.py` validates sidecar inventory against actual bundle contents, treats a usable v2 index as authoritative, performs JSON-only explicit repair, and preserves unresolved recoverable metadata.

`core/action_safety.py`, `core/action_selection.py`, `Assets/Anims/anim_manager.py`, and `Assets/Utils/utils_asset.py` enforce Blender 5 safety. They use exact datablock/provenance identity, localize external Actions before pipeline mutation, assign compatible Action slots, restore Armature/NLA/scene state, clean only operation-owned temporary data, and reject destructive NLA replacement when it cannot be preserved losslessly.

### Design Decisions

- Per-skeleton `.blend` bundles remain physical outputs; the v2 index is a management and repair authority rather than a global Action pool.
- Multi-skeleton execution requires explicit mappings because target inference can silently apply animation to the wrong Armature.
- Publication uses journaled pair/index/resource transactions because valid metadata without its matching bundle, sidecar, catalog, or thumbnail is unsafe.
- Blender 5 NLA operations reject unsupported destructive overwrite/replace cases before mutation rather than attempting lossy reconstruction of arbitrary strip types.
- Library and Action identity use persistent IDs, canonical paths, catalog/source provenance, and Blender RNA identity. Display names and bare Action names are not identity.

## Usage

1. Scan the source manifest and configure the library output directory and name.
2. For multiple source skeletons, map every source skeleton to an existing target Armature before starting import.
3. Run the import/library workflow. The pipeline resumes from the output directory's external checkpoint after interruption.
4. Use the Assets animation detail view to Preview or Apply an Action. Apply validates the active Armature and Blender 5 Action slot before changing state.
5. If metadata is missing or stale, use the explicit metadata repair operation. It writes JSON only, validates bundle inventories, preserves unresolved metadata, and never rewrites a `.blend`.

The pipeline rejects unsafe output components, Windows-reserved/overlong names, generated volume-stem collisions, incomplete task mappings, ambiguous Actions, invalid sidecars, failed mount registration, and unverified staged provenance before irreversible cleanup.

## Verification

- Explicit host contract suite: `280` tests passed across animation loader, source identity, Blender 5 action safety, repair, multi-import plans, pipeline stages, transaction durability, and dynamic mounting.
- Compilation and patch validation: `python -m compileall -q .` and `git diff --check` passed.
- Blender 5.1.2 background probe passed. It created a per-skeleton pair and v2 index, verified `v2_index` scanner authority, duplicate Action source identity handling, missing-metadata fallback, and JSON-only repair with an unchanged blend hash.
- Independent release audit: `APPROVED FOR DEPLOYMENT` with no actionable safety, crash-recovery, or Blender runtime findings.

## Journey Log

> Brief notes on what informed the final design. Not required reading.

- [lesson] A Blender Text datablock is session state, not crash-durable state; destructive pipeline boundaries require an fsynced external checkpoint.
- [lesson] Blender 5 Actions require slot/channelbag-aware handling, and Python wrapper identity is not stable ownership identity.
- [pivot] NLA replacement now refuses unsupported destructive cases before mutation because generic strip reconstruction cannot preserve transition, meta, sound, and nested strip data.
- [lesson] Resource files must join the durable journal before mutation, including thumbnail paths discovered after linked Action localization.
- [lesson] Display names are labels, not identity. Persistent IDs and canonical paths are required for mounted libraries, repair, and finalization.

## Source Materials

| File | Role | Notes |
|------|------|-------|
| `docs/compose/plans/2026-07-15-release-safety-architecture.md` | Release-safety plan | Final architecture hardening plan. |
