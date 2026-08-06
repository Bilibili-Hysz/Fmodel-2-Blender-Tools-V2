# Project Index Open Scan Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Project Settings Create/Update controls with a clear Open, Scan, Rebuild lifecycle.

**Architecture:** `Open` remains a status-only operator and never creates an index. A renamed `fmodel.scan_project_index` operator calls existing `ProjectIndex.scan()`, whose schema gate creates a missing database or incrementally updates a ready database. `Rebuild` remains the only explicit destructive recovery path. No aliases for removed Create/Update operators remain.

**Tech Stack:** Blender `bpy` operators/panel, `ProjectIndex.status()`/`scan()`/`rebuild()`, pytest fake-Blender contracts, Blender 5.1 background probe.

## Global Constraints

- `Open` only reads existing index status and counts; it never creates, scans, or rebuilds a database.
- `Scan` creates a missing index then discovers assets, or incrementally updates a compatible index.
- `Rebuild` remains the explicit operation for incompatible or broken indexes and is the only operation that replaces an existing database.
- Do not retain `fmodel.create_project_index` or `fmodel.update_project_index` aliases.
- Project Settings and Animation Pipeline `Panel.draw()` do not refresh an index or write Scene/ID-backed RNA.

---

### Task 1: Rename Operators And Consolidate Scan Semantics

**Files:**
- Modify: `Fmodel/project_assets.py:169-221`
- Modify: `ui.py:93-96`
- Modify: `test_project_assets_ui.py:157-283`
- Modify: `investigation/task7_verification/blender_task7_integration.py:190-193`

**Interfaces:**
- Produces `fmodel.open_project_index`, `fmodel.scan_project_index`, and existing `fmodel.rebuild_project_index`.
- Removes `fmodel.create_project_index` and `fmodel.update_project_index`.
- Consumes `ProjectIndex.status()`, `scan([root_path])`, and `rebuild()`.

- [ ] **Step 1: Write failing lifecycle contracts**
```python
def test_open_reads_ready_index_without_scanning_or_creating(self):
    result = FMODEL_OT_OpenProjectIndex().execute(context)
    self.assertEqual(result, {"FINISHED"})
    self.assertEqual(index.calls, ["status"])

def test_scan_creates_missing_index_through_scan_then_reports_scanned(self):
    result = FMODEL_OT_ScanProjectIndex().execute(context)
    self.assertEqual(result, {"FINISHED"})
    self.assertEqual(index.calls, [("scan", ["C:/Project"]), "status"])

def test_removed_create_and_update_operator_ids_are_not_registered(source):
    assert 'bl_idname = "fmodel.create_project_index"' not in source
    assert 'bl_idname = "fmodel.update_project_index"' not in source
```

- [ ] **Step 2: Run RED**
Run: `python -m pytest test_project_assets_ui.py -k "open or scan or removed" -q`
Expected: FAIL because the old operator names and button IDs still exist.

- [ ] **Step 3: Implement Open and Scan operators**
```python
class FMODEL_OT_OpenProjectIndex(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.open_project_index"
    bl_label = "Open Project Index"
    # status(), _apply_status(), report "Project index loaded"

class FMODEL_OT_ScanProjectIndex(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.scan_project_index"
    bl_label = "Scan Project Index"
    # result = index.scan([settings.root_path]); apply status; report "Project index scanned"
```

- [ ] **Step 4: Update UI and Blender probe**
```python
row.operator("fmodel.open_project_index", text="Open", icon='FILE_FOLDER')
row.operator("fmodel.scan_project_index", text="Scan", icon='FILE_REFRESH')
row.operator("fmodel.rebuild_project_index", text="Rebuild", icon='RECOVER_LAST')

require(bpy.ops.fmodel.scan_project_index() == {"FINISHED"}, "indexed project scan failed")
```

- [ ] **Step 5: Run GREEN and full verification**
Run: `python -m pytest test_project_assets_ui.py -q`
Expected: PASS.
Run: `python -m pytest -q`
Expected: PASS.
Run: `F:\Blender\Software\blender-5.1.2-windows-x64\blender.exe --background --factory-startup --python investigation\task7_verification\blender_task7_integration.py`
Expected: successful index scan and existing single-model publication evidence.
Run: `git diff --check`
Expected: no whitespace errors.

## Self-Review

- [x] `Scan` delegates initial creation and incremental update to the existing atomic scanner.
- [x] `Open` remains strictly read-only.
- [x] Rebuild stays explicit and destructive.
- [x] Old Create/Update operator IDs are removed from source, tests, UI, and Blender probe.
