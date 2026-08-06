# FModel-2-Blender-Tools Next Project Index Design

> [!NOTE]
> This document may not reflect the current implementation.
> See the final report for up-to-date state:
> [Final Report](../reports/fmodel-tools-next-project-index.md)

## [S1] Product Identity

The next-generation product is **FModel-2-Blender-Tools Next** (short form: **FModel Tools Next**). It evolves the addon from a directory importer into a project asset discovery, relationship, import, and animation-management system. Direct-directory import remains an Advanced/Legacy migration path.

## [S2] Project-First Flow

The FModel sidebar starts with Project Settings: select a project root. Project Settings uses Open, Scan, and Rebuild: use **Open** to inspect an existing asset index, **Scan** to create or incrementally refresh it, and **Rebuild** only to replace an incompatible index. Project Assets exposes map/scene, model, material, texture, skeleton, and animation relationships. Model Import remains the normal importer with material tools under Advanced. Asset Pipeline is renamed Animation Pipeline; it selects indexed skeleton animations, then constructs the existing per-run `DictionaryManifest` and reuses the current safe Pipeline.

## [S3] Persistent Index Boundary

The derived SQLite index is `<project-root>/.fmodel/project-assets.sqlite`. It stores canonical paths, file fingerprints, parse status, diagnostics, and discovered relations. It never stores `bpy` objects, Action identities, checkpoints, or published-library state. It is rebuildable and fails closed when unreadable, schema-incompatible, or stale.

Existing boundaries stay separate: SQLite is cross-session project discovery; `DictionaryManifest` is one selected animation run; Pipeline checkpoints are resumable execution state; Library v2 index and sidecars describe published bundles.

## [S4] Schema And Scan Contract

Schema versioning includes `project`, `scan_roots`, `files`, `assets`, and `relations` tables. Relations support `map_uses_model`, `model_uses_material`, `material_uses_texture`, `model_uses_skeleton`, and `skeleton_uses_animation`.

Scans are generation-based. A complete scan publishes only after transactional completion; cancelled or failed scans retain the preceding successful generation. Updates use canonical path, size, mtime nanoseconds where available, and optional content hash to reparse changed files and remove deleted records. Metadata-declared FModel skeleton identity outranks directory fallback; raw directories must not be pruned merely by their names.

## [S5] First Delivery Scope

First delivery implements project settings, SQLite lifecycle, schema/version checks, generation scans, incremental invalidation, file records, and skeleton-to-animation queries. The indexed animation selector projects its selection to a normal `DictionaryManifest` before using current task mapping, staging, publication, mounting, scanning, and cleanup.

Models are only related to skeletons when an existing JSON or manifest explicitly declares that relationship. Unknown model formats register a file identity but do not infer a skeleton. Full material/texture browsing, map graph UX, automated repair, and legacy-path removal are later deliveries.

## [S6] Validation And Non-Goals

Tests prove project-root isolation, schema rejection/rebuild, generation rollback, incremental updates, metadata-first skeleton selection, manifest projection, and legacy directory-flow compatibility. Blender probes validate registration and indexed handoff through library publication. SQLite does not replace source parsing, pipeline authority, or physical per-skeleton bundle contracts.
