# Cross-skeleton UEANIM compatibility

**Status:** implemented in V2 as an explicit opt-in route; strict identity remains the default.

## Why this exists

Some Unreal projects author many animations against a shared base skeleton and use
Unreal's retargeting system at runtime. For example, a `SKEL_FM_Base` animation
may be intended for a character model whose exported skeleton is named
`SKEL_FM_Lynn_Seashell`. The UEFormat identity is correctly different, so the
normal strict Blender importer must reject that pair rather than silently bind it.

The R40 CodeWorld sample confirms that this is not merely a name difference:
`SKEL_FM_Base` has 269 exported bones while `SKEL_FM_Lynn_Seashell` has 164.
Therefore the first safe V2 feature is **not** a same-layout alias. It is an
explicit **name-based compatibility** route.

## User workflow

1. Import the target `.uemodel` and keep its armature selected.
2. In **FModel > Animation Pipeline**, select the directory or indexed project
   containing the source `.ueanim` files. Set the source skeleton filter to the
   source identity, for example `SKEL_FM_Base.SKEL_FM_Base`.
3. Complete the scan. In Step 4, map the source skeleton to the target armature
   `SKEL_FM_Lynn_Seashell` (the one-skeleton flow uses the active armature).
4. Change **Compatibility** from **Strict identity** to
   **Name-based compatibility**, then click **Import and Package**.
5. The direct `.ueanim` and `.uepose` operators expose the same choice in their
   import popup.

The choice is persisted in `PipelineContext`, each `MultiImportTask`, and its
resume/task identity. Changing it invalidates the affected import task; a strict
checkpoint cannot be reused as a permissive task or vice versa.

## Exact behavior

- `strict` (default): passes `strict_fmodel_skeleton=True` to the canonical
  `io_scene_ueformat` importer. Source/target identity path, GUID and layout
  checks remain active.
- `name_retarget` (explicit): passes `strict_fmodel_skeleton=False` only for the
  selected task. The canonical importer binds tracks by case-insensitive bone
  name, keeps matching target bones, and skips source-only tracks. It does not
  rewrite the source identity or claim that the two Unreal skeleton assets are
  identical.
- Pipeline skeleton filters use exact canonical aliases rather than arbitrary
  substring matching. Repeated package/object names such as
  `SKEL_FM_Base.SKEL_FM_Base`, rooted logical paths, and conventional
  `SK_/SKEL_/_Skeleton` display aliases are accepted; `Base` does not select
  `Base_Child`.
- Imported actions carry `fmodel_skeleton_compatibility` and
  `Pipeline_SkeletonCompatibility` metadata so downstream tools can see the
  source, target and binding mode.

## Important limitation

This is a deterministic Blender-side name binding compatibility route, not full
Unreal Retarget Manager parity. It does not yet reproduce Unreal's retarget
source/reference pose, bone translation retargeting modes, IK/virtual-bone policy,
root-motion policy, or proportion compensation. With different bone layouts,
users must inspect the result and may need a later bake/retarget pass in Blender.
Missing or ambiguous bone-name bindings are reported by the canonical importer.

The feature deliberately does **not** weaken model identity, texture/material
contracts, URI resolution, or the default strict single-skeleton import path.

## Future boundary

A future full retarget mode should be a separate explicit policy with a persisted
bone map/reference-pose record. It must not be implemented by silently turning
off strict validation globally.
