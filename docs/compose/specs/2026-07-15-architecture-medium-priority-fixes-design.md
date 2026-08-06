# Architecture Medium-Priority Fixes Design

## [S1] Scope

Resolve the remaining reviewed data-integrity and ownership risks in project scanning, Project Settings index reads, and dynamic Blender asset-library cleanup. Preserve normal indexed animation and library mounting workflows.

## [S2] Non-Overlapping Scan Roots

`ProjectIndex.scan()` rejects a requested root set containing an ancestor and descendant. The scan returns `failed` before publishing a new generation, preserving the prior ready generation. Equal roots remain deduplicated as before.

## [S3] Non-Mutating Index Reads

Project Settings enumeration and count paths open an existing SQLite index with a read-only URI. A missing database, rebuild window, lock, or malformed path never creates an empty SQLite file from a UI refresh.

## [S4] Selection Replacement On Success

Refreshing indexed animation choices queries and validates the new list before changing Blender collection data. A failed query preserves the user's current choices and selected flags. A successful query replaces choices with the new unselected indexed paths.

## [S5] Dynamic Asset-Library Ownership

Dynamic library cleanup removes only entries the current FModel process added. Ownership is recorded when `mount_dynamic_library()` successfully adds a new Blender preference entry, using canonical path and optional library ID. Matching a pre-existing path or ID updates it without recording ownership. Display-name prefixes are never ownership evidence.

## [S6] Validation

Tests prove overlapping roots do not alter a prior index generation; read-only UI paths do not create missing databases; failed choice refresh retains prior selections; and cleanup preserves externally configured prefix-matching libraries while removing a library FModel created during the current process.
