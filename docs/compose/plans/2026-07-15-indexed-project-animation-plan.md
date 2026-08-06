# Indexed Project Animation Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Project Default Animation Pipeline Step 3 build a validated import plan from all indexed animations for the imported skeleton, without recursively scanning Project Root.

**Architecture:** A small runner adapter derives a complete indexed selection from Project Settings root plus `WizardState.skeleton_filter` only in Project source mode. Step 3 sends that selection through the established `ProjectIndex.dictionary_manifest()` path, preserving fingerprint validation, `PipelineContext`, checkpoints, and mappings. Manual mode continues the existing directory-acquisition and format-conversion stages.

**Tech Stack:** Blender `bpy` wizard state/UI, SQLite-backed `ProjectIndex`, existing `DictionaryManifest` and `PipelineContext`, pytest fake-Blender tests, Blender 5.1 background probe.

## Global Constraints

- Project Default discovers animation candidates only from the ready project index; it never recursively scans Project Root.
- Project Default includes all indexed animations for the nonempty wizard skeleton identity.
- Every indexed candidate passes `ProjectIndex.dictionary_manifest()` validation before checkpoint/plan acceptance.
- Project Default index errors must direct users to Project Settings Scan and never fall back to directory scanning.
- Manual Directory retains current directory acquisition and format conversion stages.
- Preserve explicit indexed-selection backend behavior, current single-model target safety, checkpointing, Step 4 import, and read-only panel draws.

---

### Task 1: Project-Default Indexed Selection Adapter

**Covers:** [S1, S2, S3, S4, S6]

**Files:**
- Modify: `Fmodel/Pipeline/steps/runner.py:76-104`
- Modify: `test_pipeline_runner_resume.py`
- Modify: `test_pipeline_manager_full_entry.py`

**Interfaces:**
- Produces `_project_default_indexed_selection(state, project_settings) -> tuple[str, str, list[str]] | None`.
- Returns `(root_path, skeleton_filter, all_paths)` for ready Project Default index state.
- Raises `ValueError` with a Project Settings Scan instruction for invalid Project Default state.
- Returns `None` for Manual Directory, leaving existing stage behavior untouched.

- [ ] **Step 1: Write failing adapter tests**
```python
def test_project_default_selection_uses_every_indexed_animation_for_skeleton():
    selection = _project_default_indexed_selection(
        state(source_mode="PROJECT", skeleton_filter="Hero"),
        settings(root_path="C:/Project", index_state="ready"),
    )
    assert selection == ("C:/Project", "Hero", ["C:/Project/Hero/Idle.ueanim", "C:/Project/Hero/Run.ueanim"])

def test_project_default_missing_or_not_ready_index_requires_project_scan():
    with pytest.raises(ValueError, match="Project Settings.*Scan"):
        _project_default_indexed_selection(state(source_mode="PROJECT", skeleton_filter="Hero"), settings(index_state="missing"))
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_pipeline_runner_resume.py test_pipeline_manager_full_entry.py -k "project_default" -q`
Expected: FAIL because no automatic Project Default selection exists.
- [ ] **Step 3: Implement the minimal adapter**
```python
def _project_default_indexed_selection(state, project_settings):
    if state.source_mode != "PROJECT":
        return None
    root = getattr(project_settings, "root_path", "").strip()
    skeleton = state.skeleton_filter.strip()
    if not root or getattr(project_settings, "index_state", "") != "ready" or not skeleton:
        raise ValueError("Project Settings Scan is required before preparing this import plan")
    paths = ProjectIndex(root).animations_for_skeleton(skeleton, raise_on_error=True)
    if not paths:
        raise ValueError(f"Project Settings Scan found no animations for skeleton: {skeleton}")
    return root, skeleton, paths
```
- [ ] **Step 4: Cover stale and explicit-selection compatibility**
```python
def test_explicit_indexed_selection_remains_authoritative_when_present(): ...
def test_project_default_stale_path_is_rejected_by_dictionary_manifest(): ...
def test_manual_mode_returns_none_and_does_not_open_project_index(): ...
```
- [ ] **Step 5: Run GREEN**
Run: `python -m pytest test_pipeline_runner_resume.py test_pipeline_manager_full_entry.py -k "indexed or project_default" -q`
Expected: PASS.

### Task 2: Step 3 Indexed Plan Execution And UI

**Covers:** [S2, S3, S4, S5, S6]

**Files:**
- Modify: `Fmodel/Pipeline/steps/step3_scan.py:29-90`
- Modify: `ui.py:210-226`
- Modify: `test_pipeline_manager_full_entry.py`
- Modify: `test_project_assets_ui.py`

**Interfaces:**
- Consumes `_project_default_indexed_selection` from Task 1 and existing `_indexed_selection_from_state` / `ProjectIndex.dictionary_manifest` paths.
- Produces a valid manifest/checkpoint and Step 3 counts for either indexed Project Default or legacy Manual Directory source.

- [ ] **Step 1: Write failing Step 3 behavior tests**
```python
def test_project_default_step3_builds_manifest_without_directory_stages():
    assert operator.execute(project_context) == {"FINISHED"}
    assert context.dictionary_manifest is not None
    assert directory_stage_calls == []

def test_project_default_step3_reports_scan_instruction_for_stale_indexed_file():
    assert operator.execute(project_context) == {"CANCELLED"}
    assert "Project Settings" in reports[-1][1]
    assert "Scan" in reports[-1][1]
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_pipeline_manager_full_entry.py -k "project_default_step3" -q`
Expected: FAIL because Project Default currently runs directory stages without explicit indexed selection.
- [ ] **Step 3: Use automatic selection before legacy stages**
```python
explicit = _indexed_selection_from_state(state)
selection = explicit or _project_default_indexed_selection(
    state, getattr(context.scene, "fmodel_project", None)
)
if selection:
    root, skeleton, paths = selection
    manifest_data = ProjectIndex(root).dictionary_manifest(skeleton, paths)
    # persist existing config/context/checkpoint path
else:
    # unchanged Manual Directory stage 1 + stage 2 path
```
- [ ] **Step 4: Render source-specific Step 3 labels**
```python
if state.source_mode == 'PROJECT':
    row.operator("pipeline.wizard_step3", text="Prepare Import Plan", icon='CHECKMARK')
    layout.label(text="Validates indexed animations for this skeleton", icon='FILE_CACHE')
else:
    row.operator("pipeline.wizard_step3", text="Scan Animation Directory", icon='VIEWZOOM')
```
- [ ] **Step 5: Run GREEN and UI contracts**
Run: `python -m pytest test_pipeline_manager_full_entry.py test_project_assets_ui.py -q`
Expected: PASS.

### Task 3: Blender Plan Verification And Documentation

**Covers:** [S1, S2, S3, S4, S5, S6]

**Files:**
- Modify: `investigation/task7_verification/blender_task7_integration.py`
- Modify: `test_task7_single_model_probe_contract.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing probe source contract**
```python
def test_task7_probe_records_project_default_indexed_plan_without_directory_scan():
    source = Path(PROBE).read_text(encoding="utf-8")
    assert "project_default_indexed_plan" in source
    assert "directory_scan_calls" in source
```
- [ ] **Step 2: Run RED**
Run: `python -m pytest test_task7_single_model_probe_contract.py -q`
Expected: FAIL because no automatic indexed-plan evidence exists.
- [ ] **Step 3: Extend probe and README**
```python
# Scan Project Settings once, auto-handoff a model, run Step 3,
# assert all skeleton animations are planned and directory-stage call count is zero.
# Delete or alter an indexed source, assert Project Settings Scan instruction, then rescan and recover.
```
- [ ] **Step 4: Run full verification**
Run: `python -m pytest -q`
Expected: PASS.
Run: `F:\Blender\Software\blender-5.1.2-windows-x64\blender.exe --background --factory-startup --python investigation\task7_verification\blender_task7_integration.py`
Expected: indexed-plan evidence and existing publication result.
Run: `git diff --check`
Expected: no whitespace errors.

## Self-Review

- [x] Project Default uses the existing validation-first manifest builder rather than a new scan engine.
- [x] Manual directory stages remain unchanged and isolated.
- [x] Project index failure paths are strict and instruct users to Scan.
- [x] Explicit indexed selection remains supported for future targeted search UI.
