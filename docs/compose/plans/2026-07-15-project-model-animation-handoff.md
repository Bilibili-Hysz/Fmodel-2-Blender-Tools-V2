# Project Model Animation Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize user-facing index terms as Open/Scan/Rebuild and automatically prepare Animation Pipeline settings after a successful UEFormat model import without navigation or scanning.

**Architecture:** The existing pure `_handoff_armature()` helper gains an optional step-advance flag, preserving manual handoff behavior by default. The UEFormat model importer calls it with the Armature returned by its own importer result after metadata assignment, not the current active object. A project-root helper fills project-default source, per-skeleton animation-library output, and library name only when a root exists.

**Tech Stack:** Blender `bpy` operators/PropertyGroups, UEFormat importer result object, existing Pipeline WizardState, pytest fake-Blender contracts, Blender 5.1 background probe.

## Global Constraints

- User-facing index workflow terminology is Open, Scan, and Rebuild; do not describe Project Settings actions as Create or Update.
- The auto handoff uses only the Armature identified from the successful UEFormat import result.
- Auto handoff must not change `current_step`, start a scan, import animations, publish a library, or mount a library.
- With Project Root, auto handoff sets Project source and `<root>/.fmodel/animation-libraries/<skeleton-name>` output/library defaults.
- Without Project Root or an imported Armature, leave existing source/output/library values untouched.
- Do not write Project Settings or Pipeline state from `Panel.draw`.

---

### Task 1: Standardize Open Scan Rebuild Terminology

**Covers:** [S1, S2, S5, S6]

**Files:**
- Modify: `README.md`
- Modify: `docs/compose/reports/fmodel-tools-next-project-index.md`
- Modify: `docs/compose/reports/single-model-animation-flow.md`
- Modify: `docs/compose/specs/2026-07-17-fmodel-tools-next-project-index-design.md`
- Modify: `docs/compose/specs/2026-07-17-single-model-animation-flow-design.md`
- Modify: `test_project_assets_ui.py`

**Interfaces:**
- Consumes current `fmodel.open_project_index`, `fmodel.scan_project_index`, and `fmodel.rebuild_project_index` UI/operator names.
- Produces user-facing workflow references using only Open/Scan/Rebuild.

- [ ] **Step 1: Write failing terminology contract**
```python
def test_project_index_user_documentation_uses_open_scan_rebuild():
    for path in USER_GUIDES:
        text = path.read_text(encoding="utf-8")
        assert "Create / Update index" not in text
        assert "create/update/rebuild actions" not in text
        assert "Open, Scan, and Rebuild" in text or "Open/Scan/Rebuild" in text
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_project_assets_ui.py -k terminology -q`
Expected: FAIL because current reports/specs say Create or Update.
- [ ] **Step 3: Update user workflow language**
```markdown
Project Settings: select root -> Open existing index or Scan project
```

```markdown
1. Set the Project Root, use **Open** to inspect an existing index, **Scan** to create or incrementally refresh it, and **Rebuild** only to replace an incompatible index.
```
- [ ] **Step 4: Run GREEN**
Run: `python -m pytest test_project_assets_ui.py -k terminology -q`
Expected: PASS.

### Task 2: Configuration-Only Import Handoff

**Covers:** [S3, S4, S5, S6]

**Files:**
- Modify: `Fmodel/Pipeline/steps/single_model_flow.py`
- Modify: `Fmodel/importers/model/operator_model_ueformat.py`
- Modify: `test_single_model_animation_flow.py`
- Create: `test_ueformat_model_animation_handoff.py`

**Interfaces:**
- Extends `_handoff_armature(wizard, armature, project_root="", advance_to_step=True) -> set[str]`.
- Produces `prepare_imported_armature_for_animation(context, armature) -> set[str]` for the UEFormat importer.
- Consumes the importer-derived `arm_obj` after its `FModel_Skeleton` metadata assignment.

- [ ] **Step 1: Write failing pure handoff tests**
```python
def test_auto_handoff_sets_project_library_defaults_without_changing_step():
    result = _handoff_armature(wizard, armature, "C:/Game", advance_to_step=False)
    assert result == {"FINISHED"}
    assert wizard.current_step == 1
    assert wizard.source_dir == "C:/Game"
    assert wizard.output_dir == "C:/Game/.fmodel/animation-libraries/Hero"
    assert wizard.library_name == "Hero"

def test_auto_handoff_without_project_root_preserves_existing_paths():
    _handoff_armature(wizard, armature, "", advance_to_step=False)
    assert wizard.source_dir == "C:/ExistingSource"
    assert wizard.output_dir == "C:/ExistingOutput"
    assert wizard.library_name == "ExistingLibrary"
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_single_model_animation_flow.py -k "auto_handoff" -q`
Expected: FAIL because the helper always changes source and Step 2 and has no project library defaults.
- [ ] **Step 3: Implement minimal helper extension**
```python
def _handoff_armature(wizard, armature, project_root="", advance_to_step=True):
    # validate and set skeleton_filter/import_target_skeleton
    if project_root.strip():
        skeleton = ...
        wizard.source_mode = "PROJECT"
        wizard.source_dir = project_root.strip()
        wizard.output_dir = os.path.join(project_root.strip(), ".fmodel", "animation-libraries", skeleton)
        wizard.library_name = skeleton
    if advance_to_step:
        wizard.current_step = 2
```
- [ ] **Step 4: Write failing importer-result boundary test**
```python
def test_successful_import_handoffs_returned_armature_not_active_object():
    context.active_object = unrelated_armature
    importer.return_value = (imported_armature, model_data)
    assert operator.execute(context) == {"FINISHED"}
    assert wizard.import_target_skeleton == imported_armature.name
    assert wizard.current_step == original_step
    assert wizard.scan_completed is False
```
- [ ] **Step 5: Integrate after imported metadata assignment**
```python
# operator_model_ueformat.py, after arm_obj metadata assignment
prepare_imported_armature_for_animation(context, arm_obj)
```

`prepare_imported_armature_for_animation` obtains `pipeline_wizard` and `fmodel_project.root_path` safely, delegates to `_handoff_armature(..., advance_to_step=False)`, and logs non-fatal failures without cancelling the successful model import.

- [ ] **Step 6: Run GREEN and regression**
Run: `python -m pytest test_single_model_animation_flow.py test_ueformat_model_animation_handoff.py -q`
Expected: PASS.
Run: `python -m pytest -q`
Expected: PASS.

### Task 3: Real Blender Verification And Documentation

**Covers:** [S2, S3, S4, S5, S6]

**Files:**
- Modify: `investigation/task7_verification/blender_task7_integration.py`
- Modify: `README.md`
- Modify: `test_task7_single_model_probe_contract.py`

- [ ] **Step 1: Write failing probe contract**
```python
def test_task7_probe_asserts_auto_import_handoff_stays_on_current_step():
    source = Path(PROBE).read_text(encoding="utf-8")
    assert "auto_import_handoff" in source
    assert "current_step ==" in source
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_task7_single_model_probe_contract.py -q`
Expected: FAIL because the probe has no auto-import handoff evidence.
- [ ] **Step 3: Extend the Blender probe and user guide**
```python
# Import a controlled UEFormat model fixture, retain a different active Armature,
# then assert imported target, unchanged step, untouched scan state, and project library defaults.
```
- [ ] **Step 4: Run full verification**
Run: `python -m pytest -q`
Expected: PASS.
Run: `F:\Blender\Software\blender-5.1.2-windows-x64\blender.exe --background --factory-startup --python investigation\task7_verification\blender_task7_integration.py`
Expected: auto-import handoff evidence plus existing publication evidence.
Run: `git diff --check`
Expected: no whitespace errors.

## Self-Review

- [x] All spec sections [S1]-[S6] map to one or more implementation tasks.
- [x] Import integration uses the returned Armature rather than active selection.
- [x] No-root and no-Armature paths preserve existing user configuration.
- [x] Scan remains the user-confirmed operation after auto prefill.
