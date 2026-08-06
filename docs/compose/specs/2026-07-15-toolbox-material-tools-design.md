# Toolbox Material Tools Design

## [S1] Goal

Make material setup part of the model-import workflow by moving Material Tools from its own top-level FModel sidebar panel into the FModel Toolbox.

## [S2] Ownership And Placement

The standalone `FMODEL_PT_Material` panel is removed. FModel Toolbox becomes the sole material-tools UI owner.

Material Tools appear only when the UEFormat import format is active, directly after the `.uemodel` model-import action and before `.ueanim`/`.uepose` actions:

```text
Model (.uemodel)
Material Tools [collapsed]
Animation (.ueanim)
Pose (.uepose)
```

## [S3] Shared Draw Content

Extract existing Material Tools draw content into one private helper that receives a layout and Blender context. Toolbox invokes it inside the collapsed section. The helper preserves all existing texture/material directory controls, auto-resolve settings, presets, analysis/apply actions, strict/report controls, and status messages.

## [S4] Boundaries

No material PropertyGroup, operator ID, auto-resolve behavior, or import behavior changes. The Toolbox draw path only reads existing Scene material settings; no new persistent state is created during drawing.

## [S5] Validation

Tests prove the standalone panel class is absent from registration, the Toolbox invokes Material Tools only in the UEFormat branch between model and animation imports, all material operator IDs remain exposed, and host/Blender background panel draw succeeds.
