# Architecture High-Priority Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make addon lifecycle, project-index recovery, and UEFormat animation handoff safe across reloads, failures, and prior wizard state.

**Architecture:** Four isolated safety changes: callback/dependency ownership, a truthful Blender version floor, staging-and-replace index rebuilds, and a single invalidation boundary when a handoff changes wizard inputs. Each has a focused regression test before production code changes.

**Tech Stack:** Blender Python API (`bpy` handlers/timers/RNA), SQLite, `pytest`, fake-Blender module fixtures.

## Global Constraints

- Preserve normal import, asset-library, and panel behavior.
- Write a regression test before each production change and observe it fail.
- Do not modify medium-priority audit findings in this work.
- Deploy changed runtime files to Blender 5.1 only after host verification and review.
- The addon minimum Blender version is `(5, 0, 0)`.

---

### Task 1: Lifecycle-Owned Handlers And Vendor Dependencies

**Covers:** [S2, S6]

**Files:**
- Modify: `auto_handlers.py:14-37,55-75,123-265`
- Modify: `__init__.py:24-30`
- Test: `test_auto_handlers_lifecycle.py`

**Interfaces:**
- Produces: `register_handlers()` and `unregister_handlers()` that are safe to call after module reload and leave no owned handler or timer behind.
- Produces: vendor ownership state containing only module IDs registered by this addon.

- [ ] **Step 1: Write failing lifecycle tests**
```python
def test_register_removes_stale_marked_handlers_before_appending_current_handlers():
    old = marked_stale_handler("fmodel_auto_handler")
    bpy.app.handlers.load_post[:] = [old]
    module.register_handlers()
    assert old not in bpy.app.handlers.load_post
    assert module.on_file_load_post in bpy.app.handlers.load_post

def test_unregister_cancels_owned_timers_and_inactive_timer_does_not_scan():
    module.register_handlers()
    module.unregister_handlers()
    assert timer_registry == []
    module._delayed_scan()
    assert scan_calls == []

def test_external_vendor_is_not_unregistered_when_already_registered():
    module._vendor_already_in_registry = lambda vendor: True
    module._ensure_dependency_addons()
    module._unregister_vendor_addons()
    assert vendor.unregister_calls == 0
```

- [ ] **Step 2: Run lifecycle tests to verify RED**
Run: `python -m pytest test_auto_handlers_lifecycle.py -q`
Expected: failure because stale callbacks remain, timers execute after unregister, or external vendor is recorded as owned.

- [ ] **Step 3: Implement stable ownership**
```python
_LIFECYCLE_MARKER = "fmodel_auto_handler"
_lifecycle_generation = 0
_owned_timers = set()
_vendor_registered_by_fmodel = set()

def _owned_callback(callback):
    setattr(callback, "_fmodel_owner", _LIFECYCLE_MARKER)
    return callback

def _remove_owned_handlers(handler_list):
    handler_list[:] = [callback for callback in handler_list
                       if getattr(callback, "_fmodel_owner", None) != _LIFECYCLE_MARKER]
```

Mark persistent handlers, register timers through a helper that captures the current generation, and unregister every owned timer. Record a vendor module only immediately after its `register()` succeeds; never record the already-present branch.

- [ ] **Step 4: Run lifecycle tests to verify GREEN**
Run: `python -m pytest test_auto_handlers_lifecycle.py -q`
Expected: PASS.

### Task 2: Blender Version Contract

**Covers:** [S3, S6]

**Files:**
- Modify: `__init__.py:3-11`
- Test: `test_addon_metadata.py`

**Interfaces:**
- Produces: `bl_info["blender"] == (5, 0, 0)`.

- [ ] **Step 1: Write the failing metadata test**
```python
def test_addon_requires_blender_5_for_bundled_psk_psa_support():
    namespace = runpy.run_path(ROOT / "__init__.py", run_name="metadata")
    assert namespace["bl_info"]["blender"] == (5, 0, 0)
```

- [ ] **Step 2: Run metadata test to verify RED**
Run: `python -m pytest test_addon_metadata.py -q`
Expected: FAIL because current minimum is `(3, 0, 0)`.

- [ ] **Step 3: Update metadata**
```python
"blender": (5, 0, 0),
```

- [ ] **Step 4: Run metadata test to verify GREEN**
Run: `python -m pytest test_addon_metadata.py -q`
Expected: PASS.

### Task 3: Atomic Project Index Rebuild

**Covers:** [S4, S6]

**Files:**
- Modify: `core/project_index.py:110-120`
- Test: `test_project_index.py`

**Interfaces:**
- Produces: `ProjectIndex.rebuild()` that retains the existing valid database if staging or replacement fails.

- [ ] **Step 1: Write the failing replacement-preservation test**
```python
def test_rebuild_failure_preserves_previous_ready_index(tmp_path, monkeypatch):
    index = build_ready_index(tmp_path)
    original_bytes = index.path.read_bytes()
    monkeypatch.setattr(index, "_create_schema", lambda connection: (_ for _ in ()).throw(sqlite3.Error("disk full")))
    assert index.rebuild()["state"] == "unavailable"
    assert index.path.read_bytes() == original_bytes
    assert index.status()["state"] == "ready"
```

- [ ] **Step 2: Run index test to verify RED**
Run: `python -m pytest test_project_index.py -k rebuild_failure_preserves -q`
Expected: FAIL because current rebuild unlinks the live database first.

- [ ] **Step 3: Stage, validate, and atomically replace**
```python
temporary_path = self.path.with_name(f"{self.path.name}.rebuild-{time_ns()}.tmp")
try:
    with self._connect_path(temporary_path) as connection:
        self._create_schema(connection)
    if not self._status_for_path(temporary_path)["state"] == "ready":
        raise sqlite3.Error("rebuilt index is invalid")
    os.replace(temporary_path, self.path)
except (OSError, sqlite3.Error):
    temporary_path.unlink(missing_ok=True)
    return self._unavailable_status()
return self.status()
```

Add the smallest private path-aware connection/status helpers needed by this code; never unlink the live path before `os.replace`.

- [ ] **Step 4: Run index tests to verify GREEN**
Run: `python -m pytest test_project_index.py -q`
Expected: PASS.

### Task 4: UEFormat Handoff Invalidates Prior Wizard Results

**Covers:** [S5, S6]

**Files:**
- Modify: `Fmodel/Pipeline/steps/single_model_flow.py:34-70`
- Test: `test_single_model_animation_flow.py`

**Interfaces:**
- Produces: `_handoff_armature(wizard, armature, project_root, advance_to_step)` that sets FModel flow and clears stale scan/import/library state when the handoff input changes.

- [ ] **Step 1: Write the failing completed-Unity handoff test**
```python
def test_imported_ueformat_handoff_switches_flow_and_invalidates_completed_plan():
    wizard = completed_wizard(flow_type="UNITY", target="OldRig")
    armature = fake_armature("NewRig", skeleton="Hero")
    assert _handoff_armature(wizard, armature, "/project", advance_to_step=False) == {"FINISHED"}
    assert wizard.flow_type == "FMODEL"
    assert wizard.import_target_skeleton == "NewRig"
    assert not wizard.scan_completed
    assert not wizard.import_completed
    assert not wizard.lib_completed
    assert wizard.multi_import_plan == "{}"
    assert wizard.multi_import_checkpoint == "{}"
```

- [ ] **Step 2: Run handoff test to verify RED**
Run: `python -m pytest test_single_model_animation_flow.py -k invalidates_completed_plan -q`
Expected: FAIL because flow and completion values remain from the old wizard.

- [ ] **Step 3: Implement one invalidation boundary**
```python
def _invalidate_handoff_results(wizard):
    wizard.scan_completed = False
    wizard.import_completed = False
    wizard.lib_completed = False
    wizard.scan_skeleton_count = 0
    wizard.scan_anim_count = 0
    wizard.scan_new_count = 0
    wizard.skeleton_mappings.clear()
    wizard.unmapped_skeletons = ""
    wizard.multi_import_plan = "{}"
    wizard.multi_import_checkpoint = "{}"
```

Compare current target/skeleton/source input before writing new values. On a changed handoff, invalidate once, set `wizard.flow_type = 'FMODEL'`, then configure target/source/output values. Preserve no-op behavior for identical handoffs.

- [ ] **Step 4: Run handoff tests to verify GREEN**
Run: `python -m pytest test_single_model_animation_flow.py test_ueformat_model_animation_handoff.py -q`
Expected: PASS.

### Task 5: Cross-Contract Verification And Deployment

**Covers:** [S6]

**Files:**
- Modify: `auto_handlers.py`, `__init__.py`, `core/project_index.py`, `Fmodel/Pipeline/steps/single_model_flow.py`
- Test: lifecycle, metadata, index, and handoff test files above

- [ ] **Step 1: Run static and aggregate verification**
Run: `python -m pytest -q`
Expected: PASS with no failures.

Run: `python -m py_compile __init__.py auto_handlers.py core/project_index.py Fmodel/Pipeline/steps/single_model_flow.py`
Expected: exit code 0.

Run: `git diff --check`
Expected: no whitespace errors.

- [ ] **Step 2: Request an independent review**
Review all files listed above against `[S2]` through `[S6]`; reject any timer that remains executable after unregister, vendor cleanup of an unowned module, pre-replacement live-index deletion, or stale UEFormat wizard completion state.

- [ ] **Step 3: Deploy and probe Blender 5.1**
```powershell
Copy-Item __init__.py,auto_handlers.py -Destination <installed-addon-root> -Force
Copy-Item core\project_index.py -Destination <installed-addon-root>\core -Force
Copy-Item Fmodel\Pipeline\steps\single_model_flow.py -Destination <installed-addon-root>\Fmodel\Pipeline\steps -Force
```

Run Blender 5.1 background Python expression that loads/registers/unregisters the addon, asserts `bl_info["blender"] == (5, 0, 0)`, then confirms no FModel-marked handler remains after unregister.
