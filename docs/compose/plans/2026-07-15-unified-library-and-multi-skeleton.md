# Unified Library and Multi-Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make per-skeleton animation bundles complete and recoverable under a unified v2 library index, while adding explicit resumable multi-skeleton import mappings.

**Architecture:** Preserve current per-skeleton `.blend` bundles and their same-stem tree manifests. Add a v2 double-underscore library index that records skeleton-aware assets and published bundle pairs. The builder publishes only complete pairs; the scanner resolves sidecars first, then index-based recovery, then an explicit metadata-missing fallback. Source identity flows from tree items to action operators. Multi-skeleton import configuration is serialized in the pipeline context as independent resumable tasks.

**Tech Stack:** Python 3, Blender 5.1 `bpy`, dataclasses, JSON sidecars, `unittest`, Blender background mode.

## Global Constraints

- Keep existing per-skeleton bundles; do not migrate storage to a mixed action pool.
- New builds write only `{library}__library_manifest.json`; scans remain compatible with legacy `{library}_manifest.json` and 5.1 v2 library indexes.
- Do not rewrite existing action `.blend` files during normal scans or metadata repair.
- Do not silently drop an animation `.blend` solely because its sidecar is absent.
- Do not infer a target armature when a source skeleton has multiple candidates.
- Preserve unrelated uncommitted changes and do not create a git commit unless the user explicitly requests one.

---

## File Structure

- `core/types.py`: skeleton-aware library-index dataclasses, bundle records, and multi-import task/plan/checkpoint types.
- `core/context.py`: backward-compatible serialization of multi-import state.
- `Fmodel/Pipeline/stages.py`: publish complete bundle pairs, write v2 index, and execute task-scoped imports.
- `Fmodel/Pipeline/steps/state.py`: UI PropertyGroups for source-skeleton mappings.
- `Fmodel/Pipeline/steps/runner.py`: construct and validate a multi-import plan from the wizard state.
- `ui.py`: render source-skeleton mappings and mapping validation feedback in the wizard.
- `Assets/Anims/anim_manager.py`: index parsing, fallback tree construction, unique item identity, and exact action loading.
- `Assets/ui_assets.py`: pass complete selected-item identity to preview/apply controls.
- `test_assets_ui_stability.py`: source-level and pure-Python contract regressions.
- `test_pipeline_library_contract.py`: isolated dataclass/index and file-publication contract tests, using temp directories.
- `investigation/animation-list-empty/library_build_probe.py`: extend Blender integration probe to assert paired output and v2 index.

### Task 1: Define Versioned Library and Multi-Import Types

**Covers:** [S2, S3, S6, S7, S8]

**Files:**
- Modify: `core/types.py:264-361`
- Modify: `core/context.py:36-167`
- Create: `test_pipeline_library_contract.py`

**Interfaces:**
- Produces: `BundleRecord(blend_file, manifest_file, skeletons, processed_sources, write_state)`.
- Produces: v2 `AssetLibraryManifest` with `skeletons`, `bundles`, `CatalogEntry.skeleton`, and `LibraryAsset.skeleton`.
- Produces: `MultiImportTask`, `MultiImportPlan`, `MultiImportCheckpoint` with `to_dict()` / `from_dict()`.
- Consumes: existing `ImportedAction` serialization and `PipelineContext.config` dictionary.

- [ ] **Step 1: Write failing pure-Python serialization tests**

```python
from core.types import (
    AssetLibraryManifest, BundleRecord, CatalogEntry, LibraryAsset,
    MultiImportCheckpoint, MultiImportPlan, MultiImportTask,
)


def test_v2_library_index_preserves_bundle_and_skeleton_identity():
    manifest = AssetLibraryManifest(version="2.0", library_name="AnimLibrary")
    manifest.skeletons = ["SK_A"]
    manifest.bundles = [BundleRecord(
        blend_file="AnimLibrary_SK_A.blend",
        manifest_file="AnimLibrary_SK_A_manifest.json",
        skeletons=["SK_A"],
        processed_sources=["SK_A::Walk.ueanim"],
        write_state="complete",
    )]
    manifest.catalog_entries = [CatalogEntry(path="SK_A/Locomotion", skeleton="SK_A")]
    manifest.assets = [LibraryAsset(
        action_name="Walk", blend_file="AnimLibrary_SK_A.blend", skeleton="SK_A",
    )]

    restored = AssetLibraryManifest.from_dict(manifest.to_dict())

    assert restored.version == "2.0"
    assert restored.bundles[0].manifest_file.endswith("_manifest.json")
    assert restored.assets[0].skeleton == "SK_A"


def test_multi_import_plan_round_trip_preserves_completed_task():
    plan = MultiImportPlan(tasks=[MultiImportTask(
        source_skeleton="SK_A", target_armature="Rig_A", source_refs=["SK_A/Walk"],
    )])
    checkpoint = MultiImportCheckpoint(completed_task_ids=[plan.tasks[0].task_id])

    restored_plan = MultiImportPlan.from_dict(plan.to_dict())
    restored_checkpoint = MultiImportCheckpoint.from_dict(checkpoint.to_dict())

    assert restored_plan.tasks[0].target_armature == "Rig_A"
    assert restored_checkpoint.completed_task_ids == [plan.tasks[0].task_id]
```

- [ ] **Step 2: Run tests to verify types are absent**

Run: `python -m unittest test_pipeline_library_contract.LibraryContractTests.test_v2_library_index_preserves_bundle_and_skeleton_identity test_pipeline_library_contract.LibraryContractTests.test_multi_import_plan_round_trip_preserves_completed_task`

Expected: FAIL because the v2 fields or multi-import classes do not exist.

- [ ] **Step 3: Add dataclasses and tolerant deserializers**

Implement these exact public shapes in `core/types.py`:

```python
@dataclass
class BundleRecord:
    blend_file: str = ""
    manifest_file: str = ""
    skeletons: List[str] = field(default_factory=list)
    processed_sources: List[str] = field(default_factory=list)
    write_state: str = "complete"


@dataclass
class MultiImportTask:
    task_id: str = ""
    source_skeleton: str = ""
    target_armature: str = ""
    source_refs: List[str] = field(default_factory=list)
    status: str = "pending"


@dataclass
class MultiImportPlan:
    tasks: List[MultiImportTask] = field(default_factory=list)


@dataclass
class MultiImportCheckpoint:
    completed_task_ids: List[str] = field(default_factory=list)
    failed_task_ids: List[str] = field(default_factory=list)
```

Add `skeleton: str = ""` to `CatalogEntry` and `LibraryAsset`. Update `AssetLibraryManifest` to default to `version="2.0"`, contain `skeletons` and `bundles`, and expose tolerant `from_dict()` that accepts v1 dictionaries where these fields do not exist. `to_dict()` must emit all v2 fields.

Update `PipelineContext` with optional `multi_import_plan` and `multi_import_checkpoint` dictionaries. In `from_dict()`, filter unknown top-level keys before constructing the dataclass so legacy saved contexts and future extension fields cannot crash loading.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest test_pipeline_library_contract -v`

Expected: PASS for v1 compatibility, v2 serialization, and multi-import checkpoint round trips.

- [ ] **Step 5: Run syntax checks without committing**

Run: `python -m py_compile core/types.py core/context.py test_pipeline_library_contract.py`

Expected: exit code 0. Do not commit because the user has not requested one.

### Task 2: Publish Complete Bundle Pairs and v2 Index

**Covers:** [S1, S2, S3, S7, S8]

**Files:**
- Modify: `Fmodel/Pipeline/stages.py:919-1110`
- Modify: `test_pipeline_library_contract.py`
- Modify: `investigation/animation-list-empty/library_build_probe.py`

**Interfaces:**
- Consumes: `AssetLibraryManifest.bundles`, `BundleRecord`, and skeleton-aware asset/catalog types from Task 1.
- Produces: `_publish_bundle_pair(output_dir, blend_name, actions, chunk_manifest) -> BundleRecord` or raises a write error without publishing final names.
- Produces: `{library}__library_manifest.json` only after all successful bundle pairs are registered.

- [ ] **Step 1: Write failing output-contract tests**

Add a helper test seam around filesystem publication, then assert:

```python
def test_publish_bundle_pair_does_not_publish_blend_when_sidecar_write_fails(tmp_path, monkeypatch):
    def fail_replace(source, destination):
        if destination.endswith("_manifest.json"):
            raise OSError("disk full")
        os.replace(source, destination)

    monkeypatch.setattr(stages.os, "replace", fail_replace)

    with pytest.raises(OSError):
        stages._publish_bundle_pair(
            str(tmp_path), "Library_SK_A.blend", b"blend", {"SK_A": {}},
        )

    assert not (tmp_path / "Library_SK_A.blend").exists()
    assert not (tmp_path / "Library_SK_A_manifest.json").exists()


def test_v2_index_lists_only_complete_bundle_pairs(tmp_path):
    # Publish one bundle through the helper, then serialize its index.
    # The index must contain the exact blend and same-stem manifest paths.
    ...
```

Use a small injectable writer wrapper so the pure test does not require `bpy.data.libraries.write`.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest test_pipeline_library_contract.LibraryContractTests.test_publish_bundle_pair_does_not_publish_blend_when_sidecar_write_fails -v`

Expected: FAIL because bundle publication is currently direct and sidecar errors are warnings.

- [ ] **Step 3: Implement pair publication and index registration**

In `LibraryBuildingStage`:

```python
def _bundle_paths(output_dir, blend_file_name):
    stem = os.path.splitext(blend_file_name)[0]
    return (
        os.path.join(output_dir, blend_file_name),
        os.path.join(output_dir, f"{stem}_manifest.json"),
    )
```

Write the Blender library to a temporary `.blend` name and JSON to a temporary manifest name. Publish both via `os.replace` only after both writes succeed. If either write fails, remove temporary files, preserve source actions for recovery, log an error containing both intended final paths, and do not append to `manifest.blend_files`, `manifest.assets`, or `manifest.bundles`.

Use a per-chunk `generated_actions`, not a list declared outside the skeleton loop. Register `CatalogEntry(..., skeleton=skel_name)` and `LibraryAsset(..., skeleton=skel_name)`. Store `source_key` as `f"{skel_name}::{relative_or_reference_key}"`.

After successful chunks, write the v2 index to:

```python
os.path.join(output_dir, f"{library_name}__library_manifest.json")
```

Do not write `{library_name}_manifest.json` for new builds. Update incremental manifest enumeration to accept only exact bundle-sidecar candidates for the active `base_name`, excluding the v2 library index.

- [ ] **Step 4: Extend the Blender probe**

Update `investigation/animation-list-empty/library_build_probe.py` to assert:

```python
assert os.path.exists(os.path.join(OUTPUT_DIR, f"ProbeLibrary_{SKELETON}.blend"))
assert os.path.exists(os.path.join(OUTPUT_DIR, f"ProbeLibrary_{SKELETON}_manifest.json"))
assert os.path.exists(os.path.join(OUTPUT_DIR, "ProbeLibrary__library_manifest.json"))
```

Load the v2 index and assert its sole `bundles[0]` maps the exact two filenames and has `write_state == "complete"`.

- [ ] **Step 5: Run focused and Blender checks**

Run: `python -m unittest test_pipeline_library_contract -v`

Expected: PASS.

Run: `& 'F:\Blender\Software\blender-5.1.2-windows-x64\blender.exe' --background --factory-startup --python "investigation\animation-list-empty\library_build_probe.py"`

Expected: `PROBE_MANIFEST_EXISTS=True` and v2 index assertions pass.

### Task 3: Add Scanner Recovery Without Silent Drops

**Covers:** [S3, S4, S7, S8]

**Files:**
- Modify: `Assets/Anims/anim_manager.py:240-481`
- Modify: `test_assets_ui_stability.py`
- Modify: `test_pipeline_library_contract.py`

**Interfaces:**
- Consumes: v2 `BundleRecord` index fields from Task 1 and complete bundle pairs from Task 2.
- Produces: `_load_library_index(abs_dir, base_name) -> dict | None`.
- Produces: `_load_bundle_tree(blend_path, index) -> tuple[dict, str]`, where source is `"sidecar"`, `"index"`, or `"fallback"`.
- Produces: visible `FOLDER` / `ANIM` records for fallback actions under `Metadata Missing`.

- [ ] **Step 1: Write scanner regression tests**

Add tests with a temporary directory and mocked `_load_manifest` / `bpy.data.libraries.load`:

```python
def test_scanner_keeps_bundle_when_sidecar_is_missing_but_v2_index_maps_it(tmp_path):
    # Create Library_SK_A.blend and Library__library_manifest.json with one
    # BundleRecord and asset mapping. The scanner must return one blend entry.
    ...


def test_scanner_creates_metadata_missing_group_when_no_sidecar_or_index(tmp_path):
    # Mock the blend to expose Walk. The result must contain an ANIM whose
    # parent folder is "Metadata Missing", not silently omit the blend.
    ...
```

Add a source-level regression that rejects the old bare `if not os.path.exists(manifest_path): continue` behavior unless it invokes the recovery helper first.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest test_assets_ui_stability test_pipeline_library_contract -v`

Expected: FAIL for missing-sidecar recovery and metadata-missing fallback.

- [ ] **Step 3: Implement layered recovery**

Refactor `_scan_library_directory()` so it retains non-model `.blend` candidates even when their sidecar is absent. For each candidate:

1. Read its exact same-stem sidecar when present.
2. Otherwise load `{library}__library_manifest.json` first, then legacy `{library}_manifest.json`; find an asset/bundle record with the exact `blend_file` basename and construct the known skeleton/folder/action tree.
3. Otherwise enumerate `df.actions` through `bpy.data.libraries.load(blend_path, link=True)` and construct:

```python
{"Metadata Missing": {"__actions__": [[action_name, ""], ...]}}
```

Use skeleton from the index when available, otherwise `"Unknown Skeleton"`. Add one warning through `self.report({'WARNING'}, ...)` in `ANIM_OT_ScanAnims.execute()` per affected bundle, including its file name and recovery level.

Keep `_mesh.blend` handling before all animation fallback behavior. Keep volume grouping and use each actual blend path as the Action source.

- [ ] **Step 4: Run scanner suites and actual-library reproduction**

Run: `python -m unittest test_assets_ui_stability test_pipeline_library_contract -v`

Expected: PASS.

Run the existing Blender background scan of the real SilverPalace output. Expected: it produces at least an `ANIM_LIB`, a `Metadata Missing` `FOLDER`, and `ANIM` entries if no repair index exists; it must no longer produce only `{'LIBRARY': 1}`.

### Task 4: Carry Exact Source Identity Through Preview and Apply

**Covers:** [S5, S8]

**Files:**
- Modify: `Assets/Anims/anim_manager.py:118-132, 249-276, 565-825`
- Modify: `Assets/ui_assets.py:170-205`
- Modify: `test_assets_ui_stability.py`

**Interfaces:**
- Produces: `AnimTreeItem.source_key` containing a stable JSON-safe key built from library node, bundle path, skeleton, parent path, and action name.
- Produces: Preview/Apply operator properties `source_key: StringProperty`.
- Consumes: exact selected `AnimTreeItem` rather than first matching `node_id`.

- [ ] **Step 1: Write a failing duplicate-action regression test**

```python
def test_preview_lookup_requires_selected_bundle_and_skeleton():
    entries = [
        {"source_key": "A|SK_A|Locomotion|Walk", "blend_path": "A.blend", "action_name": "Walk"},
        {"source_key": "B|SK_B|Locomotion|Walk", "blend_path": "B.blend", "action_name": "Walk"},
    ]

    selected = resolve_tree_item(entries, "B|SK_B|Locomotion|Walk")

    assert selected["blend_path"] == "B.blend"
```

Add a source-level assertion that Preview and Apply resolve selected tree items by `source_key`, not by `next(... node_id ...)`.

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest test_assets_ui_stability.AssetsUiStabilityTests.test_preview_lookup_requires_selected_bundle_and_skeleton -v`

Expected: FAIL because `source_key` and exact resolver do not exist.

- [ ] **Step 3: Implement exact identity and load ordering**

Add `source_key` to `AnimTreeItem`. Populate it in `traverse_tree()` from `blend_path`, `skel`, `parent_id`, and `act_name`; include a library/bundle prefix where it is passed from the caller. Add a small resolver:

```python
def _get_tree_item_by_source_key(items, source_key):
    return next((item for item in items if item.source_key == source_key), None)
```

Pass this property from the UI buttons. Preview and Apply must:

1. Resolve the exact item by `source_key`.
2. Load/check the selected item’s `blend_path` before reusing an in-memory action.
3. Accept an in-memory action only if it is local to or linked from that selected blend; otherwise load the selected bundle and select the resulting action.
4. Report and cancel on unresolved ambiguity rather than selecting the first same-named Action.

- [ ] **Step 4: Run regressions and Blender identity probe**

Run: `python -m unittest test_assets_ui_stability -v`

Expected: PASS.

Add and run a Blender background probe with two `.blend` bundles that each contain `Walk`; assert selecting the second source key results in an Action loaded from the second bundle.

### Task 5: Restore Explicit Multi-Skeleton Mapping and Resume

**Covers:** [S6, S7, S8, S9]

**Files:**
- Modify: `Fmodel/Pipeline/steps/state.py:12-145`
- Modify: `Fmodel/Pipeline/steps/runner.py:15-55`
- Modify: `ui.py:130-260`
- Modify: `Fmodel/Pipeline/stages.py:535-800`
- Modify: `core/context.py:139-178`
- Modify: `test_pipeline_library_contract.py`

**Interfaces:**
- Consumes: `MultiImportTask`, `MultiImportPlan`, and `MultiImportCheckpoint` from Task 1.
- Produces: `WizardSkeletonMapping(source_skeleton, target_armature, enabled)` Blender collection rows.
- Produces: `build_multi_import_plan(mappings, dictionary_manifest) -> MultiImportPlan`.
- Produces: `context.multi_import_plan` and `context.multi_import_checkpoint` persisted after each completed task.

- [ ] **Step 1: Write failing planning and checkpoint tests**

```python
def test_build_multi_import_plan_rejects_duplicate_target_guessing():
    mappings = [
        {"source_skeleton": "SK_A", "target_armature": "Rig_A", "enabled": True},
        {"source_skeleton": "SK_B", "target_armature": "", "enabled": True},
    ]

    plan, errors = build_multi_import_plan(mappings, {"skeletons": {"SK_A": {}, "SK_B": {}}})

    assert plan.tasks[0].target_armature == "Rig_A"
    assert errors == ["SK_B has no target armature"]


def test_import_checkpoint_skips_completed_task_and_runs_next():
    # Stub task runner records only SK_B when SK_A task id is checkpointed.
    ...
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest test_pipeline_library_contract.LibraryContractTests.test_build_multi_import_plan_rejects_duplicate_target_guessing -v`

Expected: FAIL because plan builder and mapping rows do not exist.

- [ ] **Step 3: Implement mapping state and wizard UI**

Add a `WizardSkeletonMapping(PropertyGroup)` with `source_skeleton`, `target_armature`, and `enabled`. Add `skeleton_mappings: CollectionProperty(type=WizardSkeletonMapping)` and `active_skeleton_mapping_index` to `WizardState`.

After dictionary scan, populate one mapping row for every discovered source skeleton. If exactly one Armature is active and only one source skeleton exists, prefill it; otherwise leave target blank. Render an editable mapping list in Step 4 with an Armature search control per row. Render clear errors for enabled rows with no target or a target that is not an Armature.

`runner.py` must build a `MultiImportPlan` from enabled valid rows. It must keep the existing single-skeleton route when `len(plan.tasks) == 1`, so established wizard workflows remain unchanged.

- [ ] **Step 4: Implement task-scoped import and persistence**

Split the UEAnim direct import loop into a helper that accepts one `MultiImportTask` and only processes that task’s source skeleton. For each task:

1. Skip if its id is in `completed_task_ids`.
2. Validate its `target_armature` exists and is an Armature.
3. Run the existing batch/retry/chunk logic only for that skeleton.
4. Append the task id to `completed_task_ids` on success, or to `failed_task_ids` and return failure on error.
5. Persist `context.multi_import_plan` and `context.multi_import_checkpoint` to the Blender text block after each task.

Use the selected mapping only. Never fall back to fuzzy matching if a mapping is blank or invalid.

- [ ] **Step 5: Run focused checks and existing suites**

Run: `python -m unittest test_pipeline_library_contract test_assets_ui_stability test_resolve_chain -v`

Expected: PASS.

Run a Blender background script that creates two Armatures, supplies two source skeleton mappings, interrupts after task one, reloads `PipelineContext`, and asserts task two remains pending while task one is skipped on resume.

### Task 6: Add Explicit Metadata Repair Operator

**Covers:** [S4, S7, S8]

**Files:**
- Modify: `Assets/Anims/anim_manager.py`
- Modify: `Assets/Anims/operators_asset.py`
- Modify: `Assets/ui_assets.py`
- Modify: `test_assets_ui_stability.py`

**Interfaces:**
- Produces: `fmodel.repair_library_metadata` operator accepting an existing library directory.
- Consumes: v2 index, legacy index, and action fallback tree data from Task 3.
- Produces: missing sidecars and a v2 index without altering `.blend` files.

- [ ] **Step 1: Write failing repair tests**

```python
def test_repair_rebuilds_sidecar_without_changing_blend_bytes(tmp_path):
    blend = tmp_path / "Library_SK_A.blend"
    blend.write_bytes(b"unchanged")
    before = blend.read_bytes()

    result = repair_library_metadata(str(tmp_path), indexed_assets=[...])

    assert result.repaired_files == ["Library_SK_A_manifest.json"]
    assert blend.read_bytes() == before
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m unittest test_assets_ui_stability.AssetsUiStabilityTests.test_repair_rebuilds_sidecar_without_changing_blend_bytes -v`

Expected: FAIL because repair API/operator does not exist.

- [ ] **Step 3: Implement explicit repair only**

Implement repair using v2 index mappings first. If only action fallback data exists, write a sidecar with `Unknown Skeleton/Metadata Missing` and leave the original blend untouched. Write a new v2 index after successful metadata files are written. Report every repaired and unrecoverable bundle in the operator output; do not run repair automatically during scan.

Add a button to the library management panel labeled `Repair Library Metadata`, with a confirmation dialog explaining that only JSON metadata will be written.

- [ ] **Step 4: Run repair and full verification**

Run: `python -m unittest test_pipeline_library_contract test_assets_ui_stability test_resolve_chain -v`

Expected: PASS.

Run Blender background probe against a copied version of the real SilverPalace library. Expected: it writes a missing sidecar/index in the copy only; scanning the copy creates folders/actions; SHA-256 of the copied `.blend` before and after repair matches.

### Task 7: Review, Deploy, and Verify the Installed Add-on

**Covers:** [S1, S2, S3, S4, S5, S6, S7, S8, S9]

**Files:**
- Modify: `docs/compose/reports/2026-07-15-unified-library-and-multi-skeleton-report.md`

**Interfaces:**
- Consumes: all completed implementation tasks and their verification evidence.
- Produces: a final report of output format, compatibility behavior, known migration outcomes, and deployment checks.

- [ ] **Step 1: Run static and unit verification**

Run: `python -m py_compile core/types.py core/context.py Fmodel/Pipeline/stages.py Fmodel/Pipeline/steps/state.py Fmodel/Pipeline/steps/runner.py Assets/Anims/anim_manager.py Assets/ui_assets.py ui.py`

Expected: exit code 0.

Run: `python -m unittest test_pipeline_library_contract test_assets_ui_stability test_resolve_chain -v`

Expected: all tests pass.

- [ ] **Step 2: Run Blender integration matrix**

Run isolated background Blender scripts for:

```text
single skeleton build -> exact pair + v2 index -> scan
two skeleton build -> two pairs + one v2 index -> scan
missing sidecar -> index recovery -> scan
missing sidecar/index -> Metadata Missing fallback -> scan
same action name in two bundles -> exact preview/apply source
two mapping tasks -> checkpoint -> resume
```

Expected: every expected tree/action/task assertion passes; the real library remains read-only.

- [ ] **Step 3: Perform a focused code review**

Review changes against the specification: pair write failure cannot publish index entries, legacy reading has no writer side effects, model bundles retain priority, fallback never chooses an arbitrary same-named action, and unmapped source skeletons cannot enter the import loop.

- [ ] **Step 4: Deploy only after Blender is closed**

Confirm no interactive `blender.exe` process is running. Copy only changed add-on files into:

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.1\scripts\addons\FModel-2-Blender-Tools
```

Create timestamped backups beside each replaced installed file. Verify deployed file hashes match repository hashes. Do not overwrite `FModel_Tools`.

- [ ] **Step 5: Write final report without committing**

Create `docs/compose/reports/2026-07-15-unified-library-and-multi-skeleton-report.md` with executed commands, test results, generated file examples, legacy compatibility results, migration notes, deployed paths, and residual risks. Do not commit unless the user explicitly requests it.

## Plan Self-Review

- Spec coverage: S1-S9 are all covered by Tasks 1-7.
- Type consistency: bundle/index and multi-import type names are established in Task 1 before later tasks consume them.
- Scope: the plan retains per-skeleton storage and defers mixed-pool migration, matching the approved design.
- No placeholder tasks: all implementation steps name target paths, functions, data shapes, and verification commands.
