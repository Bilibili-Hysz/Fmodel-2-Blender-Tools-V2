# Folder-only Animation Pipeline Decision

> Decision date: 2026-08-13 (Asia/Shanghai)
> V2 addon version: 8.1.0

## Decision

The active Animation Pipeline uses Folder mode only. Users select one animation folder, an output directory, a library name, and optional skeleton filters. Step 3 directly scans .ueanim / .uepose files in that folder, then the existing import/package flow continues.

Indexed Project is no longer a supported active source mode. It was not useful enough in real testing to justify the SQLite database, background worker, progress/cancel surface, and migration complexity.

## Runtime boundary

- WizardState.source_mode exposes only MANUAL (Folder).
- manual_source_dir is the only user-facing animation source.
- source_dir is an internal normalized checkpoint value.
- project_root, indexed-selection fields, project_assets.py, and project_index_controller.py are migration/reference material only.
- Project index modules are not imported or registered by normal addon startup and are excluded from the installed runtime payload.
- The legacy top-level Project Settings class is unregistered and renders only a retirement notice if loaded by source-level tooling.
- No FModel executable, UEFormat binary protocol, mesh/material/animation output, or USD exporter is changed by this decision.

## Compatibility

Explicit old PROJECT state can still be read by resolve_animation_source() for migration-oriented callers. A current MANUAL state with an empty manual_source_dir resolves to an empty path and never reuses stale project data. This prevents old .blend values from silently selecting an unintended directory.

## Verification

The Folder-only targeted V2 suite currently passes:

    151 passed, 19 skipped, 59 subtests passed

The final V2 full suite passes:

    646 passed, 19 skipped, 89 subtests passed

The 19 skipped tests are historical Indexed Project execution/lifecycle contracts; they remain visible as retired history rather than silently being deleted.

## Follow-up

1. Sync the V2 source to the Blender 5.2 addon using scripts/sync_to_blender_addon.py.
2. Verify source/target runtime trees are byte-identical and confirm the five retired index modules are absent from the installed payload.
3. Perform manual Blender GUI testing with a real animation folder.
4. Do not reintroduce Project Index behavior unless a new requirement and separate contract justify it.
