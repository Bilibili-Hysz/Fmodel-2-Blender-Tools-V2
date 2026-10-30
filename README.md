# FModel Tools Next

Current sidebar workflow: fixed target and library → import/build → model/animation/pose browser → save and manage → utilities → diagnostics. The builder has three visible stages. See [工作流架构与面板说明](docs/WORKFLOW_ARCHITECTURE_2026-09-16.md).

FModel Tools Next is a Blender add-on for project-first UE/FModel asset workflows.

## Animation Workflow

1. Open **Animation Pipeline**; the active source mode is **Folder**.
2. In Step 2, select the animation folder, output directory, library name, and optional skeleton filter.
3. Step 3 scans `.ueanim` Actions and lists `.uepose` resources separately. Pose import preserves the complete source file in the Pose Assets library. Apply bone poses or optionally bake combined bone/curve deformation to shape keys there; poses remain separate from timed Action packaging.
4. Importing a model can prepare Animation Pipeline for its returned Armature, including skeleton, target Armature, output path, and library defaults.
5. Step 4 imports animations, builds the library, and generates thumbnails.

Scan counts distinguish new, source-unchanged, source-changed, and version-unverified animations. Published bundles retain source-content, import-settings, and target-skeleton versions. Automatic updates require the skeleton's complete prior source set and a reliable source-to-Action mapping. They preserve each existing bundle path and Action name so saved Link references remain valid; new sources use new volumes. Incomplete selections, shared bundles, missing legacy mappings, renamed Actions, and changed target-skeleton versions fail without replacing the old library. Use a new library when changing the target skeleton. Back up before choosing explicit Force Rebuild: it retains scoped replacement semantics and can change bundle paths, requiring existing links to be repointed.

After publishing an update, reload the linked library or reopen the scene to read the new data; appended local copies are not updated automatically.

Animation import follows upstream UEFormat: bind the first case-insensitive bone-name match and skip absent tracks. Library preview and application do not compare skeleton labels, identity, or track coverage. Skeleton labels only group and filter library content; standard animations without FModel identity are supported.

Indexed Project, Project Root, the SQLite animation index, and background index-maintenance controls are retired from the active UI and execution path. Legacy fields and source modules remain only as migration/reference data for older .blend files; they are not registered or invoked by normal startup.

### Canonical FModel skeleton provenance

When the canonical UEFormat importer creates an armature, V2 preserves its
`fmodel_skeleton_identity` contract in pipeline task/checkpoint records. A
strict identity contains the skeleton path, optional GUID, and bone-layout
hash. Armatures without that property remain **legacy** rather than being
classified as strict; malformed present properties are reported as **invalid**.
The identity fingerprint participates in import task reuse and multi-skeleton
checkpoint matching.

The active Folder scanner uses optional binary skeleton provenance for grouping. Standard resources without identity use a supplied search label or Unspecified; directory names are not guessed as skeleton identity. It does not create or read a SQLite Project Index.

Legacy dictionary imports remain available for existing exports, but new work should begin with direct Folder selection.

See `README_EN.md` and `README_ZH.md` for the full feature reference and export guidance.

## Native UEFormat and JSON boundary

V2 treats the canonical UEFormat binary resource as the authoritative source for
model/animation skeleton identity. For current `.uemodel`, `.ueanim`, and
`.uepose` exports, V2 can discover the skeleton path, GUID/layout identity, and
provenance fingerprint directly from the resource; same-name FModel JSON is not
required for that path. The Folder scanner and pipeline checkpoint use the
binary identity first and consult JSON only as an optional legacy fallback.

JSON is intentionally retained in bounded roles:

- legacy exports and legacy model/skeleton compatibility;
- V2-owned checkpoints, reports, library manifests, and other internal state.

The canonical UEFormat importer remains responsible for native mesh/material/
texture import when its current capability bridge is available. The former
`Universal_tools/material` JSON resolver and the custom shader node-group manager
have been retired; canonical materials are built entirely by the vendor importer.

A native binary resource may therefore be tested without a sidecar JSON. Keep
textures and their original relative paths available when the selected importer
requires external texture files.

## User-visible reports

The Toolbox now keeps a persistent **Last Operation** section. It reports the
latest UEFormat import status, source, created Datablocks and actionable
failures, and provides **Copy Report** and **Open Source** actions. The copied
report also includes the active runtime path and verified Vendor tree digest.

Choose one Blender installation mode: use the V2 bundled runtime, or install the
standalone `io_scene_ueformat` addon. Do not enable both in one session.

## Bundled UEFormat Vendor

V2 bundles the FModel-aware `io_scene_ueformat` **0.10.0** implementation. It
supports `.uemodel`, `.ueanim`, and `.uepose`, including FModel
material/texture provenance, optional skeleton provenance, and upstream animation binding.

When V2 starts, the bundled dependency is registered through
`io_scene_ueformat.register_headless()`: operators, file handlers, settings, and
import logic remain available, while the canonical UEFormat panels and
**File > Import** entries are suppressed. This avoids duplicate panels; a
standalone UEFormat installation still uses normal `register()` and keeps its own
UI.

The vendor carries compatible Windows x64 `zstandard` wheels for CPython 3.11,
3.12, and 3.13. The auto-handler selects the wheel matching the running Blender
Python. See `vendor/README.md` and
`docs/ADAPTATION_STATUS_2026-08-06.md` for the refresh contract and current
verification boundary.

`vendor/vendor_tree_manifest.json` locks the complete Vendor release tree. Verify it
with `python scripts/generate_vendor_tree_manifest.py --check` before packaging.

## Development and tests

The complete automated suite is kept under `tests/` and is not part of the
installable Blender runtime. Run it from the repository root with:

```powershell
python -B -m pytest tests -q -p no:cacheprovider
```

The `tests/conftest.py` bootstrap resolves the repository root for source
contracts, so individual test files can also be invoked through the `tests/`
path.
