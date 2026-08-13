# FModel Tools Next

FModel Tools Next is a Blender add-on for project-first UE/FModel asset workflows.

## Animation Workflow

1. Open **Animation Pipeline**; the active source mode is **Folder**.
2. In Step 2, select the animation folder, output directory, library name, and optional skeleton filter.
3. Step 3 directly scans .ueanim / .uepose files in the selected folder.
4. Importing a model can prepare Animation Pipeline for its returned Armature, including skeleton, target Armature, output path, and library defaults.
5. Step 4 imports animations, builds the library, and generates thumbnails.

Indexed Project, Project Root, the SQLite animation index, and background index-maintenance controls are retired from the active UI and execution path. Legacy fields and source modules remain only as migration/reference data for older .blend files; they are not registered or invoked by normal startup.

### Canonical FModel skeleton provenance

When the canonical UEFormat importer creates an armature, V2 preserves its
`fmodel_skeleton_identity` contract in pipeline task/checkpoint records. A
strict identity contains the skeleton path, optional GUID, and bone-layout
hash. Armatures without that property remain **legacy** rather than being
classified as strict; malformed present properties are reported as **invalid**.
The identity fingerprint participates in import task reuse and multi-skeleton
checkpoint matching.

The active Folder scanner uses the binary identity first and consults JSON only as an optional legacy fallback. It does not create or read a SQLite Project Index.

Legacy dictionary imports remain available for existing exports, but new work should begin with direct Folder selection.

See `README_EN.md` and `README_ZH.md` for the full feature reference and export guidance.

## Native UEFormat and JSON boundary

V2 treats the canonical UEFormat binary resource as the authoritative source for
model/animation skeleton identity. For current `.uemodel`, `.ueanim`, and
`.uepose` exports, V2 can discover the skeleton path, GUID/layout identity, and
provenance fingerprint directly from the resource; same-name FModel JSON is not
required for that path. The Folder scanner and pipeline checkpoint use the
binary identity first and consult JSON only as an optional legacy fallback.

JSON is intentionally retained in three bounded roles:

- legacy exports and legacy model/skeleton compatibility;
- optional old FModel dictionary/manifests and material resolver inputs;
- V2-owned checkpoints, reports, library manifests, presets, and other internal
  state.

The canonical UEFormat importer remains responsible for native mesh/material/
texture import when its current capability bridge is available. The older
`Universal_tools/material` JSON resolver is still useful for legacy assets or
manual material repair; removing it would not improve native UEFormat imports.

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

V2 bundles the FModel-aware `io_scene_ueformat` **1.2.0** implementation. It
supports `.uemodel`, `.ueanim`, and `.uepose`, including FModel
material/texture provenance and strict skeleton identity.

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
