# Project Index Open Or Create Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Project Settings `Open / Create` action load a compatible existing index and its counts before creating a missing index.

**Architecture:** `FMODEL_OT_CreateProjectIndex.execute()` first calls `ProjectIndex.status()`. A `ready` status is applied through the existing `_apply_status()` helper with no schema write. Only `missing` calls `ensure_schema()`; `rebuild_required` and `unavailable` are applied and returned as controlled errors. The panel only changes its button label and remains read-only.

**Tech Stack:** Blender `bpy` operator, `ProjectIndex.status()`/`ensure_schema()`, pytest fake-Blender tests.

## Global Constraints

- `Open / Create` reads and displays a compatible existing index without changing its SQLite file.
- `Open / Create` invokes `ensure_schema()` only for a missing index.
- Incompatible and unavailable indexes are not implicitly rebuilt or replaced; the user must use Rebuild.
- Project Settings and Animation Pipeline `Panel.draw()` do not refresh indexes or write Scene/ID-backed RNA.

---

### Task 1: Existing-Index-First Open Or Create Operator

**Files:**
- Modify: `Fmodel/project_assets.py:168-186`
- Modify: `ui.py:93-96`
- Modify: `test_project_assets_ui.py`

**Interfaces:**
- Consumes `ProjectIndex.status() -> {state, schema_version, rebuild_required}`.
- Consumes `ProjectIndex.ensure_schema() -> status` only after a `missing` status.
- Produces `INFO: Project index loaded` for an existing compatible index and `INFO: Project index created` for a newly created index.

- [ ] **Step 1: Write failing existing-index and incompatible-index tests**
```python
def test_open_or_create_loads_ready_index_without_ensuring_schema(self):
    index = FakeIndex(status={"state": "ready", "schema_version": 2, "rebuild_required": False})
    result = self.execute_create(index)
    self.assertEqual(result, {"FINISHED"})
    self.assertNotIn("ensure_schema", index.calls)
    self.assertIn(({"INFO"}, "Project index loaded"), self.reports)

def test_open_or_create_requires_rebuild_for_incompatible_index(self):
    index = FakeIndex(status={"state": "rebuild_required", "schema_version": 1, "rebuild_required": True})
    self.assertEqual(self.execute_create(index), {"CANCELLED"})
    self.assertNotIn("ensure_schema", index.calls)
```

- [ ] **Step 2: Run RED**
Run: `python -m pytest test_project_assets_ui.py -k "open_or_create" -q`
Expected: FAIL because Create always calls `ensure_schema()` and reports creation.

- [ ] **Step 3: Implement status-first control flow**
```python
status = index.status()
if status["state"] == "ready":
    self._apply_status(settings, index, status)
    self.report({"INFO"}, "Project index loaded")
    return {"FINISHED"}
if status["state"] != "missing":
    self._apply_status(settings, index, status)
    self.report({"ERROR"}, "Project index requires rebuild")
    return {"CANCELLED"}
status = index.ensure_schema()
```

- [ ] **Step 4: Rename the panel action**
```python
row.operator("fmodel.create_project_index", text="Open / Create", icon='ADD')
```

- [ ] **Step 5: Run GREEN and full regression**
Run: `python -m pytest test_project_assets_ui.py -q`
Expected: PASS.
Run: `python -m pytest -q`
Expected: PASS.
Run: `git diff --check`
Expected: no whitespace errors.

## Self-Review

- [x] Existing compatible indexes are read first and never passed to `ensure_schema()`.
- [x] Only `missing` invokes creation; incompatible/unavailable states never create or rebuild.
- [x] The panel remains a read-only dispatcher with an unambiguous label.
