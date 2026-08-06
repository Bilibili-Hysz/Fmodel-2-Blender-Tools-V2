# Animation List Empty Investigation Report

## Contract

Investigate why the real SilverPalace animation library contains a `.blend` with actions but Blender's FModel animation list is empty after restart. The investigation was read-only against the real output directory; isolated Blender probes used copied or mocked data only.

## Baseline

Real output directory:

```text
AnimLibrary_SK_AlfDefault_U_Body01_Skeleton.blend
AnimLibrary_manifest.json
blender_assets.cats.txt
```

It lacks the scanner-required paired file:

```text
AnimLibrary_SK_AlfDefault_U_Body01_Skeleton_manifest.json
```

A real Blender 5.1 scan of that output created exactly one node:

```text
{'LIBRARY': 1}
```

No `ANIM_LIB`, `FOLDER`, or `ANIM` tree nodes existed, so no UI selection or list-layout change can reveal an animation.

## Confirmed Root Cause

The plugin's Assets scanner requires every animation `.blend` to have an exact same-basename companion manifest. The real library only has the aggregate `AnimLibrary_manifest.json`, which is not the format or filename the scanner reads. It therefore skips the real animation blend before tree construction.

The scanner condition is in `Assets/Anims/anim_manager.py`:

```python
manifest_name = f"{os.path.splitext(f)[0]}_manifest.json"
manifest_path = os.path.join(abs_dir, manifest_name)
if not os.path.exists(manifest_path):
    continue
```

## Causal Demonstration

1. **Actual condition**: scanning the real output produced only `LIBRARY: 1`.
2. **Reintroduction**: the same scanner was given a correctly named paired manifest containing one skeleton, folder, and action. It produced:

```text
{'LIBRARY': 1, 'ANIM_LIB': 1, 'FOLDER': 1, 'ANIM': 1}
```

This proves the missing paired manifest is sufficient to cause the empty list and sufficient restoration of that contract restores the tree.

## Ruled Out

- **Default folder selection alone**: it affects a populated detail tree, but the real scan never created any folder or animation nodes.
- **Current LibraryBuildingStage cannot write paired manifests**: an isolated Blender 5.1 probe loaded a real action and produced both `ProbeLibrary_SK_AlfDefault_U_Body01_Skeleton.blend` and its paired `_manifest.json`.
- **Asset persistence**: the real `.blend` and aggregate manifest exist; the failure is the browser's missing index/metadata sidecar, not the action blend's absence.

## Remaining Unknown

The evidence does not show why the real build at `2026-07-15 10:03` omitted its paired manifest. Current code writes it after writing the `.blend`, but that block logs a warning and does not fail the library stage if JSON writing fails. The `PipelineContext.json` supplied earlier predates this build and cannot explain that write attempt.

Possible historical causes remain unconfirmed: a previous add-on copy, an interrupted write, a filesystem failure, or external deletion. No claim is made among them.

## Minimal Repair Options

1. **Immediate data repair**: reconstruct the paired manifest from the aggregate manifest and the known catalog path, then rescan. This repairs the existing library but needs a validation script because the aggregate manifest lacks the full folder-tree structure used by the browser.
2. **Code hardening**: make the scanner fall back to `<library>_manifest.json` and inspect the `.blend` when the paired manifest is missing, reporting a clear warning instead of silently skipping the blend.
3. **Build integrity**: write the paired manifest atomically and treat its failure as a failed library build, preventing a misleading successful completion with an unbrowsable library.

Option 2 plus Option 3 is the durable code solution. Option 1 is necessary only to salvage the current output without re-importing.

## Evidence Files

- `baseline.md`
- `hypotheses.tsv`
- `library_build_probe.py`
