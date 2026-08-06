# Unified Library and Multi-Skeleton Design

## [S1] Goal

Keep per-skeleton animation bundles as the physical storage boundary while restoring unified library management, recoverability, and explicit multi-skeleton import selection. A completed library must never contain a published animation `.blend` that the add-on silently cannot browse.

## [S2] Bundle Contract

Each generated animation bundle has an exact pair:

```text
{library}_{skeleton}.blend
{library}_{skeleton}_manifest.json
{library}_{skeleton}_Vol_N.blend
{library}_{skeleton}_Vol_N_manifest.json
```

The sidecar remains the canonical browser tree data: skeleton, catalog hierarchy, direct action records, thumbnails, and processed-source keys. The builder writes temporary files, publishes the pair only after both writes succeed, and only then includes the pair in the library index. A sidecar failure marks the bundle/build as failed and reports its path; it must not be downgraded to a warning.

## [S3] Unified Library Index

New builds write `{library}__library_manifest.json`, schema version 2. The index records explicit `skeletons`, skeleton-bearing catalog and asset records, and one bundle record per published pair with its `.blend`, sidecar, skeletons, processed sources, and completion state.

The index is a library-level recovery and management index, not a replacement for the bundle sidecar. Readers support the current `{library}_manifest.json` v1 and the 5.1 v2 index as read-only legacy formats. Writers only emit the v2 double-underscore filename.

## [S4] Scanner Recovery

The scanner prefers each bundle's same-stem sidecar. If it is absent, it attempts recovery from the v2 library index. If the index cannot reconstruct a normal hierarchy, it enumerates the bundle's actions read-only and exposes them in an explicit metadata-missing group. The scan reports the affected bundle and recovery level; no animation `.blend` is silently skipped solely because metadata is absent.

Model bundles retain their existing `_mesh.blend` priority and are not interpreted as animation fallback candidates.

## [S5] Action Identity

Every animation tree item and Preview/Apply operator must retain an unambiguous source identity comprising library, bundle blend path, skeleton, catalog path, and action name. Loading must resolve the selected bundle before considering a matching in-memory action. Same-named actions from another bundle or skeleton must not be reused.

Existing libraries with insufficient metadata preserve current behavior where unambiguous; ambiguous fallback records must report rather than select an arbitrary action.

## [S6] Multi-Skeleton Import

Restore explicit source-skeleton-to-target-armature mapping in the pipeline UI and serialized configuration. A generated import plan contains independent tasks with source skeleton, target armature, source subset, progress, and status. It is checkpointed after every task so a failed or interrupted task resumes independently.

The user selects mappings; no multiple-candidate target is implicitly chosen. Unmapped source skeletons are reported before import and do not enter library construction. Single-skeleton mode remains the natural default and uses the existing active-armature path.

## [S7] Compatibility and Migration

Existing per-skeleton bundles remain readable. The new scanner accepts their sidecars unchanged. Existing v1 aggregate manifests and legacy 5.1 v2 indexes are accepted when possible, but they are never overwritten during a scan.

A repair operation may rebuild missing sidecars/index records from existing bundles. It does not rewrite action `.blend` files. The initial implementation must keep repair separate from ordinary scanning and require explicit user invocation.

## [S8] Validation

Automated coverage must prove:

- Every published bundle has an exact sidecar and index entry.
- A sidecar write failure does not publish or index its bundle.
- Scanner output includes valid sidecar bundles, v2-index-recovered bundles, and an explicit metadata-missing fallback rather than silently dropping a blend.
- Same action names across two skeleton/bundles resolve to the selected bundle.
- Multi-skeleton configuration serializes, restores, validates mappings, and resumes a partially completed task plan.
- Existing single-skeleton import and legacy library scans remain supported.

Blender background-mode integration tests validate writing, scanning, preview/apply identity, and checkpoint round trips; Blender-independent tests cover serializable types and output-contract logic.

## [S9] Non-Goals

This change does not convert current libraries to a mixed action pool, rewrite existing `.blend` files, or remove current per-skeleton bundles. It does not infer an armature mapping when more than one candidate exists.
