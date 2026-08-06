# Persist Imported Assets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist imported action metadata after every successful UEAnim import so the library-building stage receives its required input.

**Architecture:** Keep the fix inside `AssetImportStage._import_ueanim_direct()`. The completed collection is serialized after the batch loop, independent of Blender UI redraw success. A source-level regression test guards the required control-flow placement.

**Tech Stack:** Python 3, Blender `bpy`, `unittest`, AST parsing.

## Global Constraints

- Do not alter asset-library output naming, manifest format, or import behavior.
- Preserve existing uncommitted pipeline improvements.
- Deploy only to `C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.1\scripts\addons\FModel-2-Blender-Tools`.
- Do not modify the legacy `FModel_Tools` installation.
- Stop Blender before copying add-on source files so loaded modules cannot conflict with deployment.

---

### Task 1: Persist UEAnim Import Results

**Files:**
- Modify: `Fmodel/Pipeline/stages.py:826-844`
- Modify: `test_assets_ui_stability.py`

**Interfaces:**
- Consumes: `ImportedAssetCollection.to_dict()` after the UEAnim batch loop.
- Produces: `PipelineContext.imported_assets` for `LibraryBuildingStage.validate_input()`.

- [ ] **Step 1: Write the failing regression test**

```python
def test_ueanim_import_persists_assets_outside_redraw_exception():
    source = PIPELINE_STAGES.read_text(encoding="utf-8")
    redraw_block = source[source.index("bpy.ops.wm.redraw_timer"):source.index("if resume_enabled:", source.index("bpy.ops.wm.redraw_timer"))]
    self.assertNotIn("context.imported_assets = collection.to_dict()", redraw_block)
    self.assertIn("context.imported_assets = collection.to_dict()", source)
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: failure because the installed-bug-compatible code only saves `imported_assets` inside the redraw exception handler.

- [ ] **Step 3: Make the minimal implementation change**

```python
try:
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
except Exception:
    pass

if resume_enabled:
    context.set_config("import_checkpoint", {})
context.imported_assets = collection.to_dict()
```

- [ ] **Step 4: Run focused tests and syntax verification**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: all tests pass.

Run: `python -c "import ast; ast.parse(open('Fmodel/Pipeline/stages.py', encoding='utf-8').read()); print('pipeline syntax passed')"`

Expected: `pipeline syntax passed`.

- [ ] **Step 5: Deploy verified source after Blender exits**

Run: `robocopy <repository> <target-addon> /E /PURGE ...`

Expected: source tree matches repository while excluding `.git`, `.mimocode`, Python caches, test files, and compose documentation.
