# Separate Armature Handoff Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present `Use Active Armature for Animation` as a distinct Animation Handoff action rather than a model-file import action.

**Architecture:** Keep the existing operator and behavior unchanged. Move its Toolbox draw call outside the UEFormat-specific import column into a dedicated labeled section between format selection and file import/export controls.

**Tech Stack:** Blender `bpy` panel layout, source-level pytest UI contract, Blender 5.1 background registration/draw probe.

## Global Constraints

- Do not change `fmodel.use_active_armature_for_animation` behavior, state mutation, or pipeline navigation.
- The handoff control must not be inside the UEFormat file-import branch or either import/export column.
- Label the separate section `Animation Handoff` and explain `Prepare selected Armature for Animation Pipeline`.
- Preserve all existing model and animation file-import controls.

---

### Task 1: Separate Toolbox Handoff Section

**Files:**
- Modify: `ui.py:105-132`
- Modify: `test_project_assets_ui.py`

- [ ] **Step 1: Write the failing placement contract**
```python
def test_toolbox_places_handoff_before_file_import_split(self):
    source = (ROOT / "ui.py").read_text(encoding="utf-8")
    handoff = source.index('"fmodel.use_active_armature_for_animation"')
    split = source.index('split = layout.split', source.index('class FMODEL_PT_Toolbox'))
    uemodel = source.index('"fmodel.import_model_uemodel"')
    assert handoff < split < uemodel
    assert 'layout.label(text="Animation Handoff"' in source
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_project_assets_ui.py -k "handoff" -q`
Expected: FAIL because the handoff is currently in `col_in` after the UEFormat model import button.
- [ ] **Step 3: Move only the layout call**
```python
layout.label(text="Animation Handoff", icon='ARMATURE_DATA')
layout.label(text="Prepare selected Armature for Animation Pipeline", icon='INFO')
layout.operator("fmodel.use_active_armature_for_animation", text="Use Active Armature", icon='ARMATURE_DATA')
layout.separator()
split = layout.split(factor=0.5, align=True)
```
- [ ] **Step 4: Run GREEN and full verification**
Run: `python -m pytest test_project_assets_ui.py -q`
Expected: PASS.
Run: `python -m pytest -q`
Expected: PASS.
Run: `git diff --check`
Expected: no whitespace errors.
Run: Blender 5.1.2 background installed-addon draw probe.
Expected: Toolbox panel loads and the handoff operator remains registered.
