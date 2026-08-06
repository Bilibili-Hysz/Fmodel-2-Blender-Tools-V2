# Release Safety Architecture Implementation Plan

> [!NOTE]
> This document may not reflect the current implementation.
> See the final report for up-to-date state:
> [Final Report](../reports/unified-library-multi-skeleton.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining crash-recovery and Blender 5 NLA data-loss release blockers without deploying the isolated unified-library worktree.

**Architecture:** Persist pipeline checkpoints through an atomic, fsynced external companion before destructive cleanup, while preserving Blender Text as a session copy. Replace destructive NLA rollback/reconstruction with failure-safe mutations that retain original strips, use slot-aware NLA assignment, and restore only changes made by the operation. Treat thumbnail/catalog resource publication as a journaled transaction whose changed artifacts are fsynced before index publication and whose terminal cleanup cannot leave a stale recoverable journal.

**Tech Stack:** Blender 5.1 Python API, Python standard library filesystem APIs, `unittest`, existing fake-`bpy` contracts, Blender 5.1.2 background probe.

## Global Constraints

- Work only in `F:\Blender Scripts Github\worktrees\fmodel-unified-library`.
- Do not deploy, commit, push, merge, or modify real game-library assets.
- Preserve user-owned Actions, Armature animation data, NLA strips, and scene state on success and all error paths.
- Persist durable state before removing Actions or staging files.
- Use TDD: add a focused failing regression, observe its expected failure, implement the smallest fix, then run the focused and root release suites.
- All resource/journal paths must remain validated under the selected library directory.

---

### Task 1: Durable Pipeline Checkpoints

**Covers:** [S1]

**Files:**
- Modify: `core/context.py`
- Modify: `Fmodel/Pipeline/stages.py`
- Test: `test_pipeline_task_scope.py`
- Test: `test_pipeline_runner_resume.py`

**Interfaces:**
- Consumes: `PipelineContext.save_to_blender_text()` and task-owned staging cleanup ordering.
- Produces: `PipelineContext.persist_checkpoint()` which atomically writes and fsyncs a checkpoint companion before destructive cleanup.

- [ ] **Step 1: Write the failing test**

```python
def test_published_provenance_is_external_checkpointed_before_staging_delete(self):
    events = []
    context.persist_checkpoint = lambda: events.append("checkpoint")
    stage.retry_staged_cleanup = lambda *_: events.append("cleanup")
    stage.execute(context)
    self.assertLess(events.index("checkpoint"), events.index("cleanup"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_pipeline_task_scope.TaskScopedImportTests.test_published_provenance_is_external_checkpointed_before_staging_delete -v`

Expected: FAIL because no external durable checkpoint is created before cleanup.

- [ ] **Step 3: Write minimal implementation**

```python
def persist_checkpoint(self):
    payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
    write_json_transaction(self._checkpoint_path(), payload)
    self.save_to_blender_text()
```

Call `context.persist_checkpoint()` immediately after staged/provenance state changes and before removing imported Actions or staged files. Make restore prefer a valid external checkpoint and otherwise retain the existing Blender Text fallback.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest test_pipeline_task_scope test_pipeline_runner_resume -v`

Expected: PASS.

### Task 2: Non-Destructive Blender 5 NLA Transactions

**Covers:** [S2]

**Files:**
- Modify: `core/action_safety.py`
- Modify: `Assets/Anims/anim_manager.py`
- Test: `test_blender5_action_safety.py`
- Test: `test_thumbnail_action_assignment.py`

**Interfaces:**
- Consumes: `assign_action()`, `replace_action()`, NLA tracks/strips and Blender 5 Action slots.
- Produces: `append_nla_strip()` and `overwrite_nla_strip()` that preserve arbitrary strip types and set the compatible slot on newly-created strips.

- [ ] **Step 1: Write failing tests**

```python
def test_overwrite_preserves_non_action_strip_when_creation_fails(self):
    track = FakeTrack([FakeTransitionStrip("Blend")])
    with self.assertRaises(RuntimeError):
        overwrite_nla_strip(track, action, 1, 10)
    self.assertEqual(track.strips[0].name, "Blend")

def test_nla_strip_uses_resolved_blender5_slot(self):
    strip = append_nla_strip(track, action_with_slot, target, 1)
    self.assertIs(strip.action_slot, action_with_slot.slots[0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest test_blender5_action_safety.Blender5ActionSafetyTests.test_overwrite_preserves_non_action_strip_when_creation_fails test_blender5_action_safety.Blender5ActionSafetyTests.test_nla_strip_uses_resolved_blender5_slot -v`

Expected: FAIL because the existing operation clears/rebuilds strips or omits slot assignment.

- [ ] **Step 3: Write minimal implementation**

```python
def overwrite_nla_strip(track, action, target, frame_start):
    overlaps = tuple(_overlapping_strips(track, frame_start, action.frame_range[1]))
    for strip in overlaps:
        track.strips.remove(strip)
    try:
        strip = track.strips.new(action.name, frame_start, action)
        strip.action_slot = resolve_action_slot(action, target)
        return strip
    except Exception:
        _restore_removed_strips_without_reconstructing_unknown_types(track, overlaps)
        raise
```

Do not claim rollback support for unsupported strip types: reject overwrite before mutation when affected strips cannot be preserved by the Blender API. For created action strips, assign the resolved compatible Action slot before returning.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest test_blender5_action_safety test_thumbnail_action_assignment test_anim_loader_contract -v`

Expected: PASS.

### Task 3: Durable Resource Commit and Terminal Journal Cleanup

**Covers:** [S3]

**Files:**
- Modify: `core/library_files.py`
- Modify: `Fmodel/Pipeline/stages.py`
- Test: `test_pipeline_library_contract.py`
- Test: `test_pipeline_task_scope.py`

**Interfaces:**
- Consumes: prepared resource transaction journal, artifact paths, index publication.
- Produces: `fsync_transaction_artifacts()` and terminal journal state handling used before index publication and after rollback.

- [ ] **Step 1: Write failing tests**

```python
def test_resource_artifacts_sync_before_index_published(self):
    events = []
    publish_rebuild_replacement_set(..., sync_file=lambda p: events.append(p))
    self.assertLess(events.index(thumbnail_path), events.index(index_path))

def test_rollback_terminal_state_does_not_reference_deleted_backups(self):
    recover_library_transaction(library_dir)
    self.assertFalse(os.path.exists(journal_path))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest test_pipeline_library_contract.PipelineLibraryContractTests.test_resource_artifacts_sync_before_index_published test_pipeline_library_contract.PipelineLibraryContractTests.test_rollback_terminal_state_does_not_reference_deleted_backups -v`

Expected: FAIL because artifact updates are not synced before index commit or journal cleanup order is unsafe.

- [ ] **Step 3: Write minimal implementation**

```python
for artifact in transaction["artifacts"]:
    if os.path.isfile(artifact["path"]):
        _fsync_file(artifact["path"])
        _fsync_directory(os.path.dirname(artifact["path"]))
publish_library_index(...)
```

Record and fsync a terminal `rolled_back` journal state before cleanup, then remove the journal before best-effort backup disposal. Recovery must treat that terminal state as cleanup-only and never attempt restoration from removed backups.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest test_pipeline_library_contract test_pipeline_task_scope -v`

Expected: PASS.

### Task 4: Staged Action Rejection Cleanup

**Covers:** [S4]

**Files:**
- Modify: `core/action_selection.py`
- Test: `test_library_action_provenance.py`
- Test: `test_pipeline_task_scope.py`

**Interfaces:**
- Consumes: newly returned `data_to.actions` Action and source/source-reference validation.
- Produces: no leaked appended Action after any validation failure.

- [ ] **Step 1: Write failing test**

```python
def test_staged_source_ref_mismatch_removes_loaded_action(self):
    action, error = resolve_imported_action(...)
    self.assertIsNone(action)
    self.assertEqual(fake_bpy.data.actions, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_library_action_provenance.LibraryActionProvenanceTests.test_staged_source_ref_mismatch_removes_loaded_action -v`

Expected: FAIL because the appended Action remains loaded.

- [ ] **Step 3: Write minimal implementation**

```python
def reject_loaded_action(action, message):
    try:
        bpy.data.actions.remove(action)
    except Exception:
        pass
    return None, message
```

Use this helper on every post-load provenance validation or tagging error.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest test_library_action_provenance test_pipeline_task_scope -v`

Expected: PASS.

### Task 5: Release Verification

**Covers:** [S1, S2, S3, S4]

**Files:**
- Test: all explicit root `test_*.py` contract modules
- Test: `investigation/task7_verification/blender_task7_integration.py`

- [ ] **Step 1: Run the complete host release suite**

Run: `python -m unittest -v test_anim_loader_contract test_anim_source_identity test_blender5_action_safety test_library_action_provenance test_library_metadata_repair test_multi_import_plan test_pipeline_fbx_match_subset test_pipeline_library_contract test_pipeline_manager_full_entry test_pipeline_runner_resume test_pipeline_task_scope test_resolve_chain test_thumbnail_action_assignment`

Expected: all tests pass.

- [ ] **Step 2: Run compilation and Blender verification**

Run: `python -m compileall -q .; git diff --check; & "F:\Blender\Software\blender-5.1.2-windows-x64\blender.exe" --background --factory-startup --python "investigation\task7_verification\blender_task7_integration.py"`

Expected: exit code 0 and `TASK7_INTEGRATION_RESULT` output.

- [ ] **Step 3: Run independent release audit**

Dispatch a read-only reviewer focused on crash recovery, NLA state ownership, Blender 5 slots, and resource publication durability. Require `APPROVED FOR DEPLOYMENT` or actionable findings.
