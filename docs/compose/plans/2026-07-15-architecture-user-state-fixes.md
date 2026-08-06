# Architecture User-State And Import Correctness Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent stale pipeline execution, false import success, leaked temporary data, and loss of user scene/library state.

**Architecture:** Apply invalidation at explicit selection commit, delay model publication until a task has completed, and use narrow `try/finally` context restoration around scene-mutating operations. Preserve tree state by stable node identity.

**Tech Stack:** Blender `bpy` operators/context, Pipeline task runner, pytest and fake-Blender contracts.

## Global Constraints

- Do not modify bundled vendor registration internals in this iteration.
- Do not change successful import/export output format or normal operator IDs.
- Every changed behavior requires a RED/Green regression test.
- Deploy changed runtime files to Blender 5.1 only after aggregate verification and review.

---

### Task 1: Pipeline Selection And Model Publication Boundaries

**Covers:** [S2, S5]

**Files:**
- Modify: `Fmodel/project_assets.py`, `Fmodel/Pipeline/steps/runner.py`
- Test: `test_project_assets_ui.py`, `test_pipeline_runner_resume.py`

- [ ] Add a completed-wizard indexed-selection apply test asserting results/mappings are invalidated only after selection commit.
- [ ] Add import-task tests proving `save_model_logic` is not called when import or library build fails and runs only after both pass.
- [ ] Run RED tests.
- [ ] Call `clear_workflow_results` after successful explicit-selection commit; move `prepare_task` after import/build success.
- [ ] Run focused pipeline/UI tests GREEN.

### Task 2: Import/Export Context Integrity

**Covers:** [S3, S5]

**Files:**
- Modify: `Fmodel/importers/animation/operator_anim_ueformat.py`, `Fmodel/importers/animation/operator_anim_psa.py`
- Modify: `Assets/Models/operators.py`, `Fmodel/importers/export/operator_export_gltf.py`, `Fmodel/importers/export/operator_export_fbx.py`
- Test: existing/new importer/export/model-save tests

- [ ] Add all-failed and mixed-result animation batch tests.
- [ ] Add model-save write failure cleanup/restoration test.
- [ ] Add individual glTF/FBX export selection restoration tests on success and exception.
- [ ] Run RED tests.
- [ ] Return `CANCELLED` on zero successful batch files; restore selection/active object and temporary data with `finally` blocks.
- [ ] Run focused import/export/model tests GREEN.

### Task 3: Asset Tree And Library Index State

**Covers:** [S4, S5]

**Files:**
- Modify: `Assets/Models/operators.py`, `Assets/Anims/anim_manager.py`, relevant UI/operators
- Test: `test_dynamic_library_mount.py` and/or focused asset-manager tests

- [ ] Add negative active-library-index access/poll tests.
- [ ] Add repeated-scan expansion-state test using stable node identity.
- [ ] Run RED tests.
- [ ] Guard every library index dereference and clamp after removal; snapshot expansion map before clearing rebuilt nodes and restore it during item creation.
- [ ] Run focused asset tests GREEN.

### Task 4: Integration Verification

**Covers:** [S5]

- [ ] Run `python -m pytest -q`, compile changed modules, and `git diff --check`.
- [ ] Request independent review of pipeline task order, exception restoration, and asset-tree state identity.
- [ ] Deploy runtime files to Blender 5.1 and run an installed-addon registration/importer probe.
