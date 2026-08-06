# Indexed Project Animation Plan Design

## [S1] Goal

Use the project SQLite index as the authoritative discovery source for the default Project Default animation workflow. Animation Pipeline Step 3 prepares a validated import plan for the imported model's skeleton rather than recursively discovering the Project Root again.

## [S2] Project Default Plan Path

When `WizardState.source_mode` is `PROJECT`, Project Settings index state is `ready`, and the wizard has a nonempty skeleton identity, Step 3:

1. Queries all indexed animation paths for that skeleton.
2. Revalidates every selected path and fingerprint through `ProjectIndex.dictionary_manifest()`.
3. Builds the existing `DictionaryManifest`, `PipelineContext`, checkpoint, and skeleton mapping.
4. Marks Step 3 complete only after a nonempty, validated manifest is available.

It does not invoke the directory-acquisition or format-conversion discovery stages and does not recursively walk Project Root.

## [S3] Strict Failure Behavior

Project Default does not silently fall back to directory scanning. Step 3 returns a controlled error that directs the user to Project Settings **Scan** when:

- Project Root is absent or differs from the configured index root.
- Index state is not `ready`.
- The skeleton identity is absent.
- The index has no animations for the skeleton.
- An indexed source was removed or changed since Project Settings Scan.

The user repairs project data using Project Settings Scan, then reruns Step 3. Stale or invalid files never become import tasks.

## [S4] Manual Directory Compatibility

When source mode is `MANUAL`, Step 3 retains the existing directory acquisition and format conversion stages. Manual work is independent of Project Settings index availability and remains the legacy/direct-directory route.

## [S5] User Interface

Project Default Step 3 is labeled **Prepare Import Plan** and describes index validation rather than scanning. Manual Directory retains **Scan Animation Directory** wording. After either successful path, the pipeline preserves existing counts, mappings, Step 4 import behavior, checkpointing, resume, publication, mount, and cleanup behavior.

## [S6] Validation

Tests and Blender verification prove:

- Project Default queries all animations for the imported skeleton and does not run recursive discovery stages.
- Every indexed candidate is fingerprint-validated before the manifest/checkpoint is accepted.
- Missing/stale/not-ready/empty-index states fail with an explicit Scan instruction and do not fall back.
- Manual Directory invokes the existing directory stages unchanged.
- Existing explicit indexed-selection paths and single-model handoff target safety remain valid.
- The default panel contains no realtime indexed list and never mutates Scene RNA during draw.
