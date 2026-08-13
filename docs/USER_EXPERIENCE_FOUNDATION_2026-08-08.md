# User-facing UEFormat runtime and Last Operation

Updated: 2026-08-08

## Installation policy

FModel Tools V2 owns a verified, headless UEFormat runtime. Users should enable
**either** FModel Tools V2 **or** the standalone `io_scene_ueformat` addon. Do
not enable both in the same Blender session. The current source trees are kept
byte-for-byte identical, but duplicate module ownership and UI registration are
still ambiguous in Blender.

## Last Operation

The Toolbox contains a persistent Last Operation section backed by the shared
`FModel_Import_Session` scene property. It shows status, source, created
Datablocks and the first actionable failure. Copy Report includes the complete
session, canonical UEFormat scene report, runtime source path and Vendor tree
digest. Open Source opens the primary asset folder.

Legacy UEScene status/report properties remain readable when no shared session
was persisted.

## Runtime parity

The aggregate workspace owns the parity helper:

```powershell
python -B D:\~Vibe-Coding\Projects\Fmodel_Edited_UEformat\scripts\sync_ueformat_runtime.py
```

The release preflight invokes the same strict comparison when the V2 source is
available. `--sync` copies V2 Vendor files to the standalone source without
deleting target-only files, then fails if any missing, extra or mismatched file
remains.

## Compatibility fallback

UEScene uses explicit creation tracking and operation-scoped resource caching
only when the active importer advertises those constructor hooks. Older or
external importers use snapshot ownership, preserving model import and rollback
behavior without disabling the optimized bundled path.