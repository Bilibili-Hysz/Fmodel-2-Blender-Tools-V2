# Architecture High-Priority Fixes Design

## [S1] Scope

Resolve the high-priority runtime correctness risks identified in the architecture audit without changing normal import, asset-library, or UI workflows. The work covers addon lifecycle ownership, supported Blender version, project-index rebuild safety, and UEFormat model-to-animation handoff state consistency.

## [S2] Addon Lifecycle Ownership

`auto_handlers` owns only callbacks and vendor addons it registers in its current lifecycle. Handler registration removes stale callbacks by a stable addon marker before adding current callbacks, so module reload cannot accumulate old function objects.

Every scheduled timer has a stable callable registered through lifecycle helpers. Unregistration removes registered timers and callbacks before vendor cleanup. Timer bodies verify that their lifecycle generation remains active before scanning, mounting libraries, or changing Blender preferences.

Vendor addon state distinguishes an already-observed external registration from a module successfully registered by FModel. Only modules successfully registered by FModel are unregistered by FModel.

## [S3] Compatibility Contract

The addon advertises Blender 5.0 as its minimum version. This matches the bundled PSK/PSA dependency floor and avoids promising unsupported Blender 3.x installations.

## [S4] Atomic Project Index Rebuild

Rebuild creates and validates a temporary SQLite index beside the live database. Only after schema validation succeeds does it atomically replace the live database. Any creation, schema, or replacement failure preserves the prior index file and returns unavailable status.

## [S5] UEFormat Handoff Invalidation

Successful `.uemodel` automatic handoff selects the FModel animation workflow, records the imported armature identity, and fills Project Default values when a project root exists. If the selected target, skeleton, source mode, or source directory changes, previous scan/import/library results, mappings, and incompatible saved plan state are cleared before the new workflow can proceed.

Manual active-armature handoff uses the same input invalidation boundary before advancing to Step 2.

## [S6] Validation

Tests cover stale handler/timer removal and vendor ownership; addon compatibility metadata; rebuild failure preserving the prior SQLite database; and UEFormat handoff changing a previously completed Unity wizard into a clean FModel wizard for the newly imported armature. Existing host suite and Blender 5.1 installed-addon registration probes remain required.
