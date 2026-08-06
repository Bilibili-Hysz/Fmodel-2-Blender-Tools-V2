# Toolbox Material Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the complete Material Tools UI into a collapsed UEFormat model-import section in FModel Toolbox.

**Architecture:** Extract the former panel's draw body to `_draw_material_tools(layout, context)`. Toolbox creates a closed box after `.uemodel` and calls the helper. Remove the standalone panel class and its registration entry, leaving properties/operators unchanged.

**Tech Stack:** Blender `bpy` panel layout and registration, existing material PropertyGroup/operators, pytest UI source contracts, Blender 5.1 background draw probe.

## Global Constraints

- Material Tools exist only in FModel Toolbox under active UEFormat import mode.
- The collapsed Material Tools region follows `.uemodel` and precedes `.ueanim`.
- Keep all existing material controls/operators/properties and automatic import resolution behavior unchanged.
- Do not duplicate material draw code or write Scene RNA from `Panel.draw`.

---

### Task 1: Integrate Material Tools Into Toolbox

**Covers:** [S1, S2, S3, S4, S5]

**Files:**
- Modify: `ui.py:105-132,349-416, registration tuple`
- Modify: `test_project_assets_ui.py`

- [ ] **Step 1: Write failing layout/registration contracts**
```python
def test_material_tools_are_toolbox_only_and_follow_model_import(self):
    source = (ROOT / "ui.py").read_text(encoding="utf-8")
    toolbox = source[source.index("class FMODEL_PT_Toolbox"):source.index("class FMODEL_PT_Pipeline")]
    assert "FMODEL_PT_Material" not in source
    assert "_draw_material_tools" in toolbox
    assert toolbox.index('"fmodel.import_model_uemodel"') < toolbox.index("_draw_material_tools") < toolbox.index('"fmodel.import_anim_ueanim"')
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_project_assets_ui.py -k material_toolbox -q`
Expected: FAIL because Material Tools are a separate panel.
- [ ] **Step 3: Extract and move draw body**
```python
def _draw_material_tools(layout, context):
    props = context.scene.fmodel_material
    # move existing Material Tools body unchanged

# UEFORMAT Toolbox branch
col_in.operator("fmodel.import_model_uemodel", ...)
material_box = col_in.box()
material_box.label(text="Material Tools", icon='MATERIAL')
_draw_material_tools(material_box, context)
col_in.operator("fmodel.import_anim_ueanim", ...)
```

Use a Blender collapsible panel helper available to the existing layout API, default closed, and call `_draw_material_tools` only when expanded.

- [ ] **Step 4: Remove standalone panel registration**
```python
# Delete FMODEL_PT_Material class and remove it from the UI class registration tuple.
```
- [ ] **Step 5: Run GREEN and full verification**
Run: `python -m pytest test_project_assets_ui.py -q`
Expected: PASS.
Run: `python -m pytest -q`
Expected: PASS.
Run: `git diff --check`
Expected: no whitespace errors.
Run: Blender 5.1.2 installed-addon Toolbox draw probe.
Expected: Toolbox draws with UEFormat active and material settings present.
