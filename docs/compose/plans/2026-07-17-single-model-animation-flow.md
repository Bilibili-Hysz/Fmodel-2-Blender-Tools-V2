# Single-Model Animation Flow Implementation Plan

> [!NOTE]
> This document may not reflect the current implementation.
> See the final report for up-to-date state:
> [Final Report](../reports/single-model-animation-flow.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the default real-time indexed-animation list with an explicit single-Armature handoff and predictable project-default/manual animation source flow.

**Architecture:** A dedicated operator transfers only the active Armature identity into the existing `pipeline_wizard`; it does not scan or import. The Animation Pipeline draws compact source-mode controls and retains the current scan/build stages. Project-index selector data and operators remain implemented but are removed from default panel drawing.

**Tech Stack:** Blender `bpy` PropertyGroups/operators, existing `pipeline_wizard`, existing FModel model custom properties, host pytest and Blender 5.1 background probe.

## Global Constraints

- Do not query, refresh, or render the Indexed Animation Selection list from `Panel.draw`.
- Do not delete `ProjectIndex`, selector operators, or indexed WizardState fields.
- The handoff requires an active Armature and writes only editable wizard configuration; it never starts a scan or import.
- Use `FModel_Skeleton` when present and active Armature name only as fallback.
- Project Root defaults the source directory; an explicit Manual Directory value overrides it.
- Keep direct-directory scanning, mapping, import, publication, mounting, and cleanup behavior available.

---

### Task 1: Single-Armature Handoff And Source Resolution

**Covers:** [S2, S3, S4, S6]

**Files:**
- Modify: `Fmodel/Pipeline/steps/state.py`
- Create: `Fmodel/Pipeline/steps/single_model_flow.py`
- Modify: `Fmodel/Pipeline/__init__.py` or existing pipeline registration module
- Test: `test_single_model_animation_flow.py`

**Interfaces:**
- Produces `fmodel.use_active_armature_for_animation`.
- Produces `resolve_animation_source(state, project_root) -> str`.
- Consumes `context.active_object`, `context.scene.pipeline_wizard`, and optional `context.scene.fmodel_project.root_path`.

- [ ] **Step 1: Write failing behavior tests**
```python
def test_handoff_prefills_metadata_skeleton_target_and_project_default():
    armature = FakeArmature("DisplayRig", {"FModel_Skeleton": "Hero"})
    result = use_active_armature_for_animation(context_with(armature, project_root="C:/Game"))
    assert result == {"FINISHED"}
    assert wizard.skeleton_filter == "Hero"
    assert wizard.target_armature == "DisplayRig"
    assert wizard.source_dir == "C:/Game"

def test_manual_source_overrides_project_default():
    state.source_mode = "MANUAL"; state.manual_source_dir = "C:/Anims"
    assert resolve_animation_source(state, "C:/Game") == "C:/Anims"
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_single_model_animation_flow.py -q`
Expected: FAIL because the operator/source resolver does not exist.
- [ ] **Step 3: Implement minimal state and operator**
```python
def resolve_animation_source(state, project_root):
    return state.manual_source_dir.strip() if state.source_mode == "MANUAL" else project_root.strip()

class FMODEL_OT_UseActiveArmatureForAnimation(bpy.types.Operator):
    bl_idname = "fmodel.use_active_armature_for_animation"
    def execute(self, context):
        armature = context.active_object
        if not armature or armature.type != "ARMATURE":
            self.report({"ERROR"}, "Select an Armature for animation")
            return {"CANCELLED"}
        # write only wizard settings, never invoke pipeline operators
```
- [ ] **Step 4: Cover invalid object and no-scan boundary**
```python
def test_handoff_rejects_non_armature_without_mutating_wizard(): ...
def test_handoff_never_invokes_scan_or_import_operator(): ...
```
- [ ] **Step 5: Run GREEN**
Run: `python -m pytest test_single_model_animation_flow.py -q`
Expected: PASS.

### Task 2: Compact Default UI And Legacy Preservation

**Covers:** [S1, S2, S3, S5, S6]

**Files:**
- Modify: `ui.py`
- Modify: `Assets/Models/ui.py` or `Assets/Models/operators.py`
- Modify: `test_project_assets_ui.py`
- Test: `test_single_model_animation_flow.py`

**Interfaces:**
- Consumes `fmodel.use_active_armature_for_animation`, `source_mode`, `manual_source_dir`, and `resolve_animation_source` from Task 1.
- Produces a toolbox action and compact Animation Pipeline source controls.

- [ ] **Step 1: Write failing UI contracts**
```python
def test_pipeline_default_draw_has_no_indexed_animation_choice_loop(source):
    panel = extract_class(source, "FMODEL_PT_Pipeline")
    assert "indexed_animations" not in panel
    assert "refresh_indexed_animation_choices" not in panel

def test_toolbox_exposes_explicit_use_for_animation_action(source):
    assert '"fmodel.use_active_armature_for_animation"' in source
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_project_assets_ui.py test_single_model_animation_flow.py -q`
Expected: FAIL because default UI still renders indexed choices.
- [ ] **Step 3: Implement focused UI changes**
```python
# Toolbox: add a compact Use for Animation action beside model import actions.
# Pipeline step 2: source_mode selector; show project root source read-only for PROJECT,
# manual_source_dir picker for MANUAL; retain existing source_dir/legacy controls.
# Remove the complete Indexed Animation Selection box from draw.
```
- [ ] **Step 4: Run GREEN and legacy regression**
Run: `python -m pytest test_project_assets_ui.py test_single_model_animation_flow.py test_pipeline_runner_resume.py -q`
Expected: PASS.

### Task 3: Blender Flow Verification And Documentation

**Covers:** [S1, S2, S3, S4, S5, S6]

**Files:**
- Modify: `investigation/task7_verification/blender_task7_integration.py`
- Modify: `README.md`
- Test: `test_task5_release_contract.py`, `test_single_model_animation_flow.py`

- [ ] **Step 1: Write failing probe contract**
```python
def test_blender_probe_uses_active_armature_handoff_without_indexed_choice_loop():
    source = Path("investigation/task7_verification/blender_task7_integration.py").read_text()
    assert "bpy.ops.fmodel.use_active_armature_for_animation()" in source
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_task5_release_contract.py -q`
Expected: FAIL because the probe does not drive the handoff.
- [ ] **Step 3: Extend the Blender probe and README**
```python
# Create an Armature tagged with FModel_Skeleton, invoke the handoff,
# assert no scan before Step 3, then execute the existing scan/publication path.
```
- [ ] **Step 4: Run full verification**
Run: `python -m pytest -q`
Expected: PASS.
Run: `F:\Blender\Software\blender-5.1.2-windows-x64\blender.exe --background --factory-startup --python investigation\task7_verification\blender_task7_integration.py`
Expected: handoff evidence and existing publication result.

## Self-Review

- [x] Every spec section [S1]-[S6] maps to at least one task.
- [x] The plan removes default indexed-list drawing without deleting the underlying indexed APIs.
- [x] Source resolution and handoff are separate from scanning/import, preserving the existing safety pipeline.
