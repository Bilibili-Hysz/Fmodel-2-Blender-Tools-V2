# Bundled Vendor Plugins

These third-party Blender plug-ins are shipped as runtime dependencies of
**FModel-2-Blender-Tools V2**. They are not the user-facing FModel panels.

## `io_scene_ueformat`

- **Bundled version:** `0.10.0`
- **Supported formats:** `.uemodel`, `.ueanim`, `.uepose`
- **Upstream family:** `h4lfheart/UEFormat` Blender add-on
- **FModel snapshot:** canonical addon branch `fmodel/material-links-v1`,
  based on commit `6d812dd38ed79d2ce33c5d6dc33e31b2ab71f45f` plus the working-tree
  overlay synchronized on **2026-10-31**. The overlay restores upstream animation
  binding while preserving the FModel material and texture improvements.
- **Runtime identity:** 72 UEFormat files, tree SHA-256
  `0d5ca81be020b050f689aacb11986285dd4d4f8cca1c7bd25471b8b9aacad26e`.
- **License:** see the canonical addon `blender_manifest.toml` declaration and the
  repository licensing files.

The snapshot includes the FModel-aware importer surfaces used by V2:

- FModel material-link and texture provenance;
- external and embedded texture resolution;
- upstream bone-name binding for animation and pose imports;
- optional FModel skeleton provenance for library grouping and discovery;
- FModel export-root and bundle-root options;
- material-slot/topology compatibility fixes for sparse or remapped LOD slots.

### Headless registration

V2 automatically registers the bundled UEFormat dependency through
`io_scene_ueformat.register_headless()`. Headless mode still registers the
operators, file handlers, settings property, and import implementation, but does
not register the canonical UEFormat panels or the **File > Import** menu entries.
This leaves V2's FModel UI as the only user-facing panel.

When the same add-on is installed and enabled by itself, calling its normal
`register()` keeps the canonical UEFormat panels and menu entries. The headless
behavior is therefore opt-in and does not change the standalone add-on contract.

### Bundled compression runtime

The importer depends on `zstandard`. The `wheels/` directory carries Windows
x64 wheels for CPython 3.11, 3.12, and 3.13. `auto_handlers.py` selects only
the wheel compatible with the running Blender Python before importing the vendor;
other interpreter builds are ignored rather than placed on `sys.path`.

## Refresh procedure

1. Run the workspace `scripts/sync_ueformat_runtime.py --sync-to-v2` command
   with an explicit recovery backup root. It replaces the Vendor package
   atomically from the canonical standalone runtime.
2. Confirm that the canonical package still exposes `register_headless()` and
   that V2's headless loader remains the only registration path used by the
   host application.
3. Update `vendor_manifest.toml`, this document, and the tree manifest together.
4. Re-run `test_ueformat_vendor_contract.py`,
   `test_ueformat_vendor_headless.py`, and the full test suite.

The vendor is intentionally kept as source files rather than a nested Git
repository, so the V2 release can be inspected and packaged as one unit.

### Reproducible tree manifest

`vendor_tree_manifest.json` records the complete release Vendor root (including
`vendor_manifest.toml`, the UEFormat source package, and locked wheels) using
per-file size/SHA-256 records plus a canonical `tree_sha256`. Generated Python
caches are excluded and must not be packaged.

Generate or verify it from the V2 project root:

```powershell
python scripts/generate_vendor_tree_manifest.py
python scripts/generate_vendor_tree_manifest.py --check
```

The manifest deliberately records both the canonical source commit and whether
the Vendor is a working-tree snapshot. The commit field alone is not treated as
a complete source identity.
