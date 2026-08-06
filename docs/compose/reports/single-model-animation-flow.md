---
feature: single-model-animation-flow
status: delivered
specs:
  - docs/compose/specs/2026-07-17-single-model-animation-flow-design.md
plans:
  - docs/compose/plans/2026-07-17-single-model-animation-flow.md
branch: master
commits: 50e47e59c16f9b7942de3b93fb85837dba72c36f..50e47e59c16f9b7942de3b93fb85837dba72c36f
---

# Single-Model Animation Flow - Final Report

## What Was Built

The default animation workflow now starts from one inspected model instead of a continuously rendered indexed-animation list. A successful UEFormat model import automatically transfers its returned Armature into editable Animation Pipeline configuration without changing panels or starting a scan. It uses a nonempty `FModel_Skeleton` custom property as the animation skeleton identity and otherwise uses the Armature name. The FModel Toolbox also exposes **Use Active Armature for Animation** as an optional explicit correction when a different Armature is intended.

Animation Pipeline Step 2 now offers a compact source choice: **Project Default** uses the configured Project Root and **Manual Directory** uses an explicit directory. Existing scanning, mapping, import, library publication, mounting, and cleanup stages continue to operate through the compatible `source_dir` field.

The Indexed Animation Selection interface is intentionally absent from the default draw path, eliminating its per-row UI work. Project indexing, selector state, and selector operators remain implemented for later on-demand/search-oriented UI work.

## Architecture

`Fmodel/Pipeline/steps/single_model_flow.py` contains `resolve_animation_source()`, the configuration-only import-result handoff, and the `fmodel.use_active_armature_for_animation` correction operator. The automatic handoff receives the Armature returned by the UEFormat importer, updates only `WizardState`, and retains the current Pipeline step. The manual operator validates the active object, then intentionally selects Step 2. Neither path starts a scan or import. Empty project/manual resolution explicitly clears `source_dir`, preventing an older directory from being scanned accidentally.

`WizardState` in `Fmodel/Pipeline/steps/state.py` owns `source_mode` and `manual_source_dir`. Its property updates maintain the legacy `source_dir` bridge outside of `Panel.draw`. `Fmodel/project_assets.py` synchronizes that bridge when Project Root changes only while the wizard is in Project Default mode; Manual Directory state is not overwritten.

`Fmodel/Pipeline/steps/runner.py` and `step4_import.py` treat a nonempty `import_target_skeleton` as explicit single-model intent. It takes precedence over a later `context.active_object` change and is validated before work begins. If the selected Armature no longer exists, execution fails with a controlled error rather than silently importing to another Armature. The legacy active-Armature behavior remains the fallback when no explicit target was set.

### Design Decisions

- The indexed backend remains available because indexing is still valuable for project discovery and future targeted search, but its large live list is not suitable for the primary panel draw path.
- UEFormat import auto-prepares configuration because its returned Armature is authoritative; the handoff remains configuration-only and never starts an implicit scan or animation import.
- The selected handoff target wins over transient active selection because changing the selected object after configuration must not redirect animation import.

## Usage

1. Optionally configure Project Settings. Project Settings uses Open, Scan, and Rebuild: use **Open** to inspect an existing index, **Scan** to create or refresh it, and **Rebuild** only to replace an incompatible index.
2. Import a UEFormat model from the FModel Toolbox. Its returned Armature automatically prepopulates Animation Pipeline configuration without changing the current panel.
3. Inspect the model and materials. If a different Armature is intended, make it active and select **Use Active Armature for Animation**.
4. Open Animation Pipeline Step 2. Confirm or edit the prefilled skeleton, target, Project Default/Manual source, and output library.
5. Explicitly continue with the existing scan, import, and library-build actions.

The default panel does not display Indexed Animation Selection. Indexed project data remains available to the addon backend and is not discarded by this workflow.

## Verification

Fresh host verification ran `python -m pytest -q` with `384 passed, 17 subtests passed in 10.69s`. `git diff --check` found no whitespace errors.

The Blender 5.1.2 background probe ran `investigation/task7_verification/blender_task7_integration.py`. It verified project-default and manual source handoff, no scan before explicit Step 3, and library publication after changing the active Armature. Probe evidence retained `IndexedWizardRig` as `handoff_target_after_active_switch` and completed publication successfully.

## Journey Log

- [lesson] Blender `Panel.draw` must be read-only for Scene-backed RNA; source compatibility synchronization belongs in property updates or explicit operators.
- [lesson] Empty or `None` `FModel_Skeleton` values are not valid identities and must fall back to the Armature name.
- [lesson] Explicit source resolution must clear an unresolved legacy `source_dir`; retaining it can scan a stale directory.
- [lesson] Saving an explicit target is insufficient unless the downstream runner and import stage consume it ahead of transient active-object state.

## Source Materials

| File | Role | Notes |
|------|------|-------|
| `docs/compose/specs/2026-07-17-single-model-animation-flow-design.md` | Initial design | Defines default UI and source-mode constraints. |
| `docs/compose/plans/2026-07-17-single-model-animation-flow.md` | Implementation plan | Three-stage TDD delivery plan. |
| `test_single_model_animation_flow.py` | Host regression suite | Handoff and source resolution coverage. |
| `test_pipeline_runner_resume.py` | Pipeline regression suite | Explicit target persistence and stale-target coverage. |
| `investigation/task7_verification/blender_task7_integration.py` | Blender integration probe | End-to-end handoff and publication verification. |
