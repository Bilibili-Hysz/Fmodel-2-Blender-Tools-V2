# FModel Tools Next Project Index Implementation Plan

> [!NOTE]
> This document may not reflect the current implementation.
> See the final report for up-to-date state:
> [Final Report](../reports/fmodel-tools-next-project-index.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task.

**Goal:** Add a project-first rebuildable SQLite asset relationship index that accelerates animation discovery without changing import and publication safety contracts.

**Architecture:** `core/project_index.py` is Blender-independent and owns SQLite schema, scan generations, fingerprints, queries, and `DictionaryManifest` projection. Blender-facing project properties/operators remain separate. Indexed selections become an ordinary `DictionaryManifest`; legacy scanning remains available.

**Global constraints:** Store the index at `<project-root>/.fmodel/project-assets.sqlite`; never store `bpy` objects, Actions, checkpoints, or published state; scans publish only complete generations; metadata skeleton identity wins over directory fallback; do not directory-prune raw exports; model-to-skeleton rows require explicit JSON/manifest metadata.

### Task 1: Core SQLite Contract

**Covers:** [S3, S4, S6]

**Files:** Create `core/project_index.py`, `test_project_index.py`; modify `core/__init__.py`.

- [ ] Write a failing host-Python test proving `ProjectIndex(root).ensure_schema()` creates `<root>/.fmodel/project-assets.sqlite`, records schema version 1, and reports `rebuild_required` after a stored schema mismatch.
- [ ] Run `python -m pytest test_project_index.py -q` and observe RED.
- [ ] Implement canonical-root/path helpers, a standard-library `sqlite3` connection, `ProjectIndex.ensure_schema()`, `status()`, and `rebuild()` with `project`, `scan_roots`, `files`, `assets`, and `relations` tables.
- [ ] Re-run `python -m pytest test_project_index.py -q` and observe GREEN.

### Task 2: Generation Scanner And Queries

**Covers:** [S4, S5, S6]

**Files:** Modify `core/project_index.py`, `test_project_index.py`.

- [ ] Write RED tests for sibling JSON metadata selecting `Hero` over an `Other` directory, deletion removing an indexed animation, and failed scan retaining the preceding generation.
- [ ] Implement `scan(roots)`, file fingerprints, staged generation publication, `.ueanim`/`.uepose` discovery, explicit metadata-first skeleton resolution, `animations_for_skeleton()`, and model file registration without inferred skeleton relationships.
- [ ] Run `python -m pytest test_project_index.py -q` and observe GREEN.

### Task 3: Animation Pipeline Projection

**Covers:** [S2, S3, S5, S6]

**Files:** Modify `core/project_index.py`, `Fmodel/Pipeline/steps/step3_scan.py`; test `test_project_index.py`, `test_pipeline_task_scope.py`.

- [ ] Write RED test that a selected indexed Hero animation becomes a valid current `DictionaryManifest`, while the legacy directory scan path remains available.
- [ ] Implement `dictionary_manifest(skeleton, paths)` and opt-in handoff from indexed wizard state; do not change Pipeline checkpoint, publication, or cleanup behavior.
- [ ] Run `python -m pytest test_project_index.py test_pipeline_task_scope.py -q` and observe GREEN.

### Task 4: Project UI And Migration

**Covers:** [S1, S2, S5, S6]

**Files:** Create `Fmodel/project_assets.py`, `test_project_assets_ui.py`; modify `Fmodel/__init__.py`, `ui.py`, `Assets/Models/ui.py`, `Assets/Anims/operators_asset.py`, `__init__.py`.

- [ ] Add Project Settings as the highest FModel panel, create/update/rebuild operators, index state/counts, model Advanced material controls, and Animation Pipeline labels/selector; keep Legacy Import direct-directory controls.
- [ ] Add fake-`bpy` registration/layout tests and Blender background probe; set `bl_info` name to `FModel Tools Next` and version to `(8, 0, 0)` only after the working flow is verified.
- [ ] Run `python -m pytest -q` and the Blender integration probe before deployment.
