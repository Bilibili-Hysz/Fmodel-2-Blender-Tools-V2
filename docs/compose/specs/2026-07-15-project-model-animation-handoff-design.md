# Project Model Animation Handoff Design

## [S1] Goal

Unify project-index user terminology as Open, Scan, and Rebuild, and reduce manual setup between a successful UEFormat model import and Animation Pipeline configuration. The normal path prepares an editable animation configuration after import without changing the visible panel or starting a scan.

## [S2] Index Terminology

User-facing documentation, test names/assertions, probes, and current workflow descriptions use these terms:

- Open: read an existing index state and counts only.
- Scan: create a missing index as part of discovery, or incrementally refresh a compatible index.
- Rebuild: explicitly replace an incompatible or damaged index.

Historical plans may retain technical words such as `create` for generic filesystem/API actions when they do not describe the Project Settings control.

## [S3] Import Handoff

After a UEFormat model import succeeds and identifies a newly imported Armature, the importer prepares `pipeline_wizard` using that Armature. The handoff uses a nonempty `FModel_Skeleton` property or the Armature name fallback, sets the explicit import target, and preserves the current UI location. It never invokes a Pipeline scan, animation import, library publication, or mount operation.

If an import does not produce a determinable Armature, it leaves wizard configuration untouched. The handoff must operate on the Armature produced by that import, not an unrelated pre-existing active object.

## [S4] Project Defaults

When Project Root is configured, the automatic handoff sets Project Default source mode and resolves its source directory from Project Root. It sets:

```text
output_dir = <Project Root>/.fmodel/animation-libraries/<skeleton-name>
library_name = <skeleton-name>
```

When no Project Root is configured, it still sets the skeleton and target but leaves `source_dir`, `output_dir`, and `library_name` unchanged. Manual source-mode intent remains respected when the user later edits the Pipeline.

## [S5] User Flow

```text
Project Settings: select root -> Open existing index or Scan project
  -> FModel Toolbox: import UEFormat model
  -> importer prepares Animation Pipeline configuration in place
  -> user inspects model/materials and opens Animation Pipeline
  -> user confirms or edits Step 2, then explicitly clicks Scan
  -> existing animation import and library publication flow
```

## [S6] Validation

Regression coverage proves:

- Current index terminology contains no user-facing Create/Update controls or workflow instructions.
- The importer handoff uses the newly imported Armature identity and never an unrelated active object.
- Project-root handoff creates the specified source/output/library defaults and remains configuration-only.
- Missing-root or missing-Armature imports do not overwrite unrelated pipeline source/output configuration.
- Existing explicit active-Armature handoff and legacy direct Pipeline flow remain valid.
- Host tests and a Blender background integration probe pass.
