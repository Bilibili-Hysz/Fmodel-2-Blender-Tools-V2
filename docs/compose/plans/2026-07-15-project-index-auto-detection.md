# Project Index Auto-Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect and display the selected Project Root's existing SQLite asset-index status immediately without scanning, creating, or updating the database.

**Architecture:** Reuse `refresh_project_index(settings)` from the safe `root_path` property update callback in `Fmodel/project_assets.py`. The callback remains the only new write path for persisted Project Settings fields; Project Settings and Pipeline `Panel.draw()` continue to consume those values read-only.

**Tech Stack:** Blender `bpy` property update callbacks, `ProjectIndex.status()`, SQLite read-only status/count queries, pytest fake-Blender tests.

## Global Constraints

- Selecting a Project Root reads existing `<root>/.fmodel/project-assets.sqlite` status and counts without scanning or changing the database.
- The auto-detection path must report `ready`, `missing`, `rebuild_required`, or `unavailable` through existing Project Settings fields.
- Project Settings and Animation Pipeline `Panel.draw()` must not call `refresh_project_index()` or write Scene/ID-backed RNA.
- Keep existing Project Default source synchronization and Manual Directory preservation unchanged.

---

### Task 1: Refresh Existing Index During Project Root Update

**Files:**
- Modify: `Fmodel/project_assets.py:74-80`
- Modify: `test_project_assets_ui.py:101-134`

**Interfaces:**
- Consumes existing `refresh_project_index(settings) -> dict`.
- Produces current `index_state`, `index_schema_version`, `animation_count`, and `model_count` immediately after a Project Root update.

- [ ] **Step 1: Write the failing callback regression test**
```python
def test_project_root_update_refreshes_existing_index_status(self):
    settings = types.SimpleNamespace(
        root_path="C:/IndexedProject",
        indexed_skeleton="Hero",
        indexed_animations=[],
    )
    self.module.refresh_project_index = mock_refresh = Mock(return_value={"state": "ready"})

    self.module._on_project_root_changed(settings, types.SimpleNamespace(scene=types.SimpleNamespace()))

    mock_refresh.assert_called_once_with(settings)
```

- [ ] **Step 2: Run RED**
Run: `python -m pytest test_project_assets_ui.py::ProjectAssetsUiTests::test_project_root_update_refreshes_existing_index_status -q`
Expected: FAIL because the callback does not call `refresh_project_index`.

- [ ] **Step 3: Add the minimal callback call**
```python
def _on_project_root_changed(settings, context):
    _clear_indexed_skeleton(settings)
    _clear_indexed_animation_choices(settings)
    refresh_project_index(settings)
    wizard = getattr(getattr(context, "scene", None), "pipeline_wizard", None)
    if wizard is not None and getattr(wizard, "source_mode", None) == "PROJECT":
        wizard.source_dir = settings.root_path.strip()
```

- [ ] **Step 4: Preserve draw and source-mode contracts**
```python
def test_project_root_update_keeps_manual_wizard_source_untouched(self):
    ...

def test_panel_draw_source_never_refreshes_the_project_index(self):
    ...
```

- [ ] **Step 5: Run GREEN and full regression**
Run: `python -m pytest test_project_assets_ui.py -q`
Expected: PASS.
Run: `python -m pytest -q`
Expected: PASS.

## Self-Review

- [x] The plan uses the existing read-only status routine instead of adding a scanner.
- [x] The only new invocation occurs in the `root_path` update callback, not a panel draw method.
- [x] Existing Project/Manual source synchronization remains covered.
