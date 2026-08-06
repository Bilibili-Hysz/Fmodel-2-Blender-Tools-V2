# Single-Model Animation Flow Design

> [!NOTE]
> This document may not reflect the current implementation.
> See the final report for up-to-date state:
> [Final Report](../reports/single-model-animation-flow.md)

## [S1] Goal

Make the normal single-model path smooth from FModel model import through animation library publication, while removing the current Indexed Animation Selection list from the default Animation Pipeline UI. The indexed project database remains available for project setup and future on-demand search work, but panel drawing must not enumerate or render its animation rows.

## [S2] User Flow

```text
Project Settings (optional Open/Scan/Rebuild index lifecycle)
  -> FModel Toolbox imports one model
  -> user inspects the active Armature and material result
  -> explicit Use for Animation action
  -> Animation Pipeline opens with skeleton, target Armature, and source defaulted
  -> user chooses Project Default or Manual source directory
  -> existing scan, mapping, import, library build, mount, and cleanup
```

The explicit handoff never starts a scan or import. It only confirms the current Armature and prepares editable wizard configuration.

## [S3] Source Directory Selection

Animation Pipeline has two sources:

- Project Default: use `fmodel_project.root_path` as the initial source directory when it is configured.
- Manual Directory: expose the existing `source_dir` picker; its selected directory overrides the project default.

The default is a convenience value, not a trusted index result. The existing scanning stage remains responsible for validating and scanning the chosen directory. The UI does not recursively scan, query a large animation list, or mutate persistent project-index state from `Panel.draw`.

## [S4] Armature Handoff

Add an explicit `fmodel.use_active_armature_for_animation` operator. It requires an active Armature, derives the source skeleton from its `FModel_Skeleton` custom property when present (otherwise its object name), and writes editable `pipeline_wizard` values:

- `source_skeleton` / skeleton filter
- target Armature selection
- source directory default according to the selected source mode
- Animation Pipeline current step

The user can change all prefilled values before scanning. A non-Armature active object returns a controlled operator error and leaves the wizard unchanged.

## [S5] UI Boundaries

Remove the Indexed Animation Selection box, refresh button, skeleton EnumProperty, checkbox list, and apply-selection button from default Animation Pipeline drawing. Do not remove `ProjectIndex`, selector operators, or indexed WizardState fields; they remain non-default capability for later search-oriented UI work.

Show a compact single-model handoff action in the FModel Toolbox and a concise source-mode control in Animation Pipeline step 2. Material tools remain part of the existing model workflow and are not expanded by this change.

## [S6] Validation

Tests must prove:

- Animation Pipeline UI no longer renders or queries Indexed Animation Selection during draw.
- The active-Armature handoff sets only wizard configuration and never invokes scan/import operators.
- Skeleton metadata takes precedence over Armature display name; fallback name works when metadata is absent.
- Invalid active objects fail without changing wizard state.
- Project Default and Manual Directory resolve source directory predictably, and legacy direct-directory behavior remains available.
- Host suite and Blender background probe pass; panel drawing remains read-only for Scene RNA.
