import hashlib
import importlib.util
import json
import os
import shutil
import sys

import bpy


WORKTREE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUT = os.path.join(os.path.dirname(__file__), "output")
LIBRARY_NAME = "Task7Probe"
ADDON_PACKAGE = "task7_addon"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_file_module(name, relative_path):
    path = os.path.join(WORKTREE, *relative_path.split("/"))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_addon_package():
    package = sys.modules.get(ADDON_PACKAGE)
    if package is not None:
        return package
    spec = importlib.util.spec_from_file_location(
        ADDON_PACKAGE,
        os.path.join(WORKTREE, "__init__.py"),
        submodule_search_locations=[WORKTREE],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[ADDON_PACKAGE] = package
    return package


def load_addon_module(module_name, relative_path):
    load_addon_package()
    full_name = f"{ADDON_PACKAGE}.{module_name}"
    path = os.path.join(WORKTREE, *relative_path.split("/"))
    spec = importlib.util.spec_from_file_location(full_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def verify_project_index_projection():
    project_index = load_addon_module("core.project_index", "core/project_index.py")
    source_root = os.path.join(OUTPUT, "indexed_source")
    animation = os.path.join(source_root, "Hero", "Moves", "Indexed.ueanim")
    os.makedirs(os.path.dirname(animation), exist_ok=True)
    with open(animation, "wb") as handle:
        handle.write(b"indexed animation")

    index = project_index.ProjectIndex(source_root)
    scan_result = index.scan([source_root])
    require(scan_result["state"] == "ready", scan_result)
    manifest = index.dictionary_manifest("Hero", [animation])
    entry = manifest["skeletons"]["Hero"]["Moves"]["__animations__"]["Indexed"]
    require(entry["source_file"] == os.path.abspath(animation), entry)
    return {
        "generation": scan_result["generation"],
        "animation": entry["original_name"],
        "folder": "Moves",
    }


def verify_indexed_wizard_publication():
    """Verify active-armature handoff, then drive the Step 3/4 publication route."""
    load_addon_package()
    project_index = load_addon_module("core.project_index", "core/project_index.py")
    state_module = load_addon_module(
        "Fmodel.Pipeline.steps.state", "Fmodel/Pipeline/steps/state.py"
    )
    step3 = load_addon_module(
        "Fmodel.Pipeline.steps.step3_scan", "Fmodel/Pipeline/steps/step3_scan.py"
    )
    step4 = load_addon_module(
        "Fmodel.Pipeline.steps.step4_import", "Fmodel/Pipeline/steps/step4_import.py"
    )
    single_model_flow = load_addon_module(
        "Fmodel.Pipeline.steps.single_model_flow",
        "Fmodel/Pipeline/steps/single_model_flow.py",
    )
    asset_operators = load_addon_module(
        "Assets.Anims.operators_asset", "Assets/Anims/operators_asset.py"
    )
    animation_manager = load_addon_module(
        "Assets.Anims.anim_manager", "Assets/Anims/anim_manager.py"
    )
    project_assets = load_addon_module(
        "Fmodel.project_assets", "Fmodel/project_assets.py"
    )
    model_importer = load_addon_module(
        "Fmodel.importers.model.operator_model_ueformat",
        "Fmodel/importers/model/operator_model_ueformat.py",
    )

    source_root = os.path.join(OUTPUT, "indexed_wizard_source")
    output_dir = os.path.join(OUTPUT, "indexed_wizard_output")
    idle_animation = os.path.join(source_root, "Hero", "Moves", "IndexedWizardIdle.ueanim")
    run_animation = os.path.join(source_root, "Hero", "Moves", "IndexedWizardRun.ueanim")
    os.makedirs(os.path.dirname(idle_animation), exist_ok=True)
    with open(idle_animation, "wb") as handle:
        handle.write(b"indexed wizard idle animation")
    with open(run_animation, "wb") as handle:
        handle.write(b"indexed wizard run animation")
    index = project_index.ProjectIndex(source_root)
    require(index.scan([source_root])["state"] == "ready", "indexed source scan failed")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    state_module.register()
    bpy.types.Scene.pipeline_wizard = bpy.props.PointerProperty(type=state_module.WizardState)
    step3.register()
    step4.register()
    single_model_flow.register()
    asset_operators.register()
    animation_manager.register()
    project_assets.register()
    model_importer.register()
    armature_data = bpy.data.armatures.new("IndexedWizardRigData")
    armature = bpy.data.objects.new("IndexedWizardRig", armature_data)
    armature["FModel_Skeleton"] = "Hero"
    bpy.context.collection.objects.link(armature)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature

    state = bpy.context.scene.pipeline_wizard
    state.flow_type = "FMODEL"
    state.source_dir = source_root
    state.output_dir = output_dir
    state.library_name = "IndexedWizard"
    state.lib_generate_thumbnails = False
    project_settings = bpy.context.scene.fmodel_project
    project_settings.root_path = source_root
    manual_source = os.path.join(OUTPUT, "single_model_manual_source")
    os.makedirs(manual_source, exist_ok=True)

    state.source_mode = "PROJECT"
    require(
        bpy.ops.fmodel.use_active_armature_for_animation() == {"FINISHED"},
        "single-model project-default handoff failed",
    )
    require(state.skeleton_filter == "Hero", "handoff did not use FModel_Skeleton")
    require(state.import_target_skeleton == armature.name, "handoff target mismatch")
    require(state.source_dir == source_root, "handoff project-default source mismatch")
    require(state.current_step == 2, "handoff did not advance to Step 2")
    require(not state.scan_completed, "handoff scanned before explicit Step 3")

    state.source_mode = "MANUAL"
    state.manual_source_dir = manual_source
    require(
        bpy.ops.fmodel.use_active_armature_for_animation() == {"FINISHED"},
        "single-model manual handoff failed",
    )
    require(state.source_dir == manual_source, "handoff manual source mismatch")
    require(not state.scan_completed, "manual handoff scanned before explicit Step 3")

    state.source_mode = "PROJECT"
    require(
        bpy.ops.fmodel.use_active_armature_for_animation() == {"FINISHED"},
        "single-model project-default restore failed",
    )
    require(state.source_dir == source_root, "handoff did not restore project default")
    require(not state.scan_completed, "project handoff scanned before explicit Step 3")

    enemy_data = bpy.data.armatures.new("IndexedWizardEnemyRigData")
    enemy = bpy.data.objects.new("IndexedWizardEnemyRig", enemy_data)
    bpy.context.collection.objects.link(enemy)
    armature.select_set(False)
    enemy.select_set(True)
    bpy.context.view_layer.objects.active = enemy
    require(bpy.context.active_object == enemy, "failed to switch active armature after handoff")

    auto_import_fixture = os.path.join(OUTPUT, "AutoImportHero.uemodel")
    auto_import_metadata = os.path.splitext(auto_import_fixture)[0] + ".json"
    with open(auto_import_metadata, "w", encoding="utf-8") as handle:
        json.dump([{
            "Type": "SkeletalMesh",
            "Skeleton": {"ObjectName": "Skeleton'Hero'"},
        }], handle)
    imported_data = bpy.data.armatures.new("AutoImportHeroRigData")
    imported_armature = bpy.data.objects.new("AutoImportHeroRig", imported_data)
    bpy.context.collection.objects.link(imported_armature)
    auto_import_step = 1
    state.current_step = auto_import_step
    state.scan_completed = False
    original_get_ueformat_api = model_importer.get_ueformat_api

    class CapturedUEFormatImport:
        def __init__(self, options):
            self.options = options

        def import_file(self, path):
            require(path == auto_import_fixture, "model importer received wrong fixture path")
            return imported_armature, object()

    # Exercise the real operator and handoff using only its vendor-import boundary.
    model_importer.get_ueformat_api = lambda: (
        CapturedUEFormatImport, lambda **kwargs: kwargs, None, None
    )
    try:
        require(
            bpy.ops.fmodel.import_model_uemodel(
                filepath=auto_import_fixture,
                auto_backup=False,
                auto_link_mat=False,
            ) == {"FINISHED"},
            "controlled UEFormat model import failed",
        )
    finally:
        model_importer.get_ueformat_api = original_get_ueformat_api

    expected_auto_output = os.path.join(
        source_root, ".fmodel", "animation-libraries", "Hero"
    )
    require(imported_armature["FModel_Skeleton"] == "Hero", "auto-import metadata was not assigned")
    require(state.import_target_skeleton == imported_armature.name, "auto-import handoff target mismatch")
    require(state.import_target_skeleton != enemy.name, "auto-import handoff used active armature")
    require(state.current_step == auto_import_step, "auto-import handoff changed current step")
    require(not state.scan_completed, "auto-import handoff scanned before explicit Step 3")
    require(state.source_dir == source_root, "auto-import project-default source mismatch")
    require(state.output_dir == expected_auto_output, "auto-import project-default output mismatch")
    require(state.library_name == "Hero", "auto-import project-default library mismatch")
    auto_import_handoff = {
        "target_armature": state.import_target_skeleton,
        "unrelated_active_armature": enemy.name,
        "current_step": state.current_step,
        "scan_before_step3": state.scan_completed,
        "project_default_source": state.source_dir,
        "project_default_output": state.output_dir,
        "project_default_library": state.library_name,
    }

    enemy.select_set(False)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    require(
        bpy.ops.fmodel.use_active_armature_for_animation() == {"FINISHED"},
        "failed to restore explicit armature handoff after auto import",
    )
    state.source_dir = source_root
    state.output_dir = output_dir
    state.library_name = "IndexedWizard"

    single_model_handoff = {
        "skeleton": state.skeleton_filter,
        "target_armature": state.import_target_skeleton,
        "project_default_source": source_root,
        "manual_source": manual_source,
        "scan_before_step3": state.scan_completed,
    }
    require(
        bpy.ops.fmodel.scan_project_index() == {"FINISHED"},
        "indexed project scan failed",
    )
    # Step 3 must use the ready project index, not the manual directory stages.
    directory_scan_calls = []
    original_run_wizard_stage = step3.run_wizard_stage

    def record_wizard_stage(context, wizard_state, stage_name):
        directory_scan_calls.append(stage_name)
        return original_run_wizard_stage(context, wizard_state, stage_name)

    step3.run_wizard_stage = record_wizard_stage
    try:
        require(
            bpy.ops.pipeline.wizard_step3() == {"FINISHED"},
            "project default indexed wizard Step3 failed",
        )
        expected_paths = [idle_animation, run_animation]
        require(directory_scan_calls == [], directory_scan_calls)
        require(state.indexed_selection_mode == "AUTO_PROJECT", "Step3 did not persist automatic mode")
        require(json.loads(state.indexed_selected_paths) == expected_paths, "Step3 did not select every indexed animation")
        project_default_indexed_plan = {
            "animation_count": state.scan_anim_count,
            "selected_paths": json.loads(state.indexed_selected_paths),
            "directory_scan_calls": directory_scan_calls,
        }
        require(project_default_indexed_plan["animation_count"] == 2, project_default_indexed_plan)

        # A stale indexed file remains a Project Default error: no raw-directory fallback.
        os.remove(run_animation)
        stale_project_default_instruction = "Project Settings Scan"
        try:
            bpy.ops.pipeline.wizard_step3()
        except RuntimeError as error:
            require(
                stale_project_default_instruction in str(error),
                "stale Project Default animation did not require Project Settings Scan",
            )
        else:
            raise AssertionError("stale Project Default animation unexpectedly completed")
        require(
            bpy.ops.fmodel.scan_project_index() == {"FINISHED"},
            "Project Settings Scan did not recover stale indexed plan",
        )
        require(
            bpy.ops.pipeline.wizard_step3() == {"FINISHED"},
            "Project Default Step3 did not recover after Project Settings Scan",
        )
        require(state.scan_anim_count == 1, "rescanned Project Default plan retained stale animation")
        require(directory_scan_calls == [], directory_scan_calls)
    finally:
        step3.run_wizard_stage = original_run_wizard_stage

    core = __import__(f"{ADDON_PACKAGE}.core", fromlist=["PipelineContext"])
    manager = __import__(
        f"{ADDON_PACKAGE}.Fmodel.Pipeline.manager", fromlist=["AssetImportStage"]
    )
    saved_context = core.PipelineContext.load_from_checkpoint(output_dir)
    expected_manifest = index.dictionary_manifest("Hero", [idle_animation])
    require(saved_context is not None, "indexed wizard context was not persisted")
    require(saved_context.dictionary_manifest == expected_manifest, "Step3 did not persist indexed manifest")
    require(saved_context.config["target_armature"] == armature.name, "Step3 lost handoff target")

    imported_manifests = []
    original_execute = manager.AssetImportStage.execute

    def import_indexed_action(import_stage, pipeline_context):
        imported_manifests.append(pipeline_context.dictionary_manifest)
        expected_entry = expected_manifest["skeletons"]["Hero"]["Moves"]["__animations__"]["IndexedWizardIdle"]
        require(pipeline_context.dictionary_manifest == expected_manifest, "Step4 did not use persisted indexed manifest")
        require(pipeline_context.get_config("target_armature") == armature.name, "Step4 used active object instead of handoff target")
        action = bpy.data.actions.new("IndexedWizardIdle")
        action["Pipeline_Owned"] = True
        action["Pipeline_Source"] = expected_entry["source_file"]
        action["Pipeline_SourceRef"] = "Hero/Moves/IndexedWizard"
        pipeline_context.imported_assets = {
            "version": "1.0",
            "import_mode": pipeline_context.get_config("import_mode"),
            "task_identity": import_stage._task_identity(pipeline_context),
            "failed_imports": [],
            "imported_actions": [{
                "action_name": action.name,
                "source_file": expected_entry["source_file"],
                "source_manifest_ref": "Hero/Moves/IndexedWizard",
                "source_bundle": "",
                "staged_blend": "",
                "import_status": "success",
                "frame_range": [0, 0],
                "error_message": "",
                "task_id": import_stage._task_identity(pipeline_context)["task_id"],
            }],
        }
        return True

    manager.AssetImportStage.execute = import_indexed_action
    try:
        require(
            bpy.ops.pipeline.wizard_step4() == {"FINISHED"},
            "indexed wizard Step4 failed",
        )
    finally:
        manager.AssetImportStage.execute = original_execute

    blend_path = os.path.join(output_dir, "IndexedWizard_Hero.blend")
    index_path = os.path.join(output_dir, "IndexedWizard__library_manifest.json")
    require(os.path.isfile(blend_path), "published indexed wizard bundle missing")
    require(os.path.isfile(index_path), "published indexed wizard manifest missing")
    require(state.import_completed and state.lib_completed, "indexed wizard did not complete")
    require(imported_manifests == [expected_manifest], imported_manifests)
    mounted = [
        library for library in bpy.context.preferences.filepaths.asset_libraries
        if os.path.normcase(os.path.abspath(bpy.path.abspath(library.path)))
        == os.path.normcase(os.path.abspath(output_dir))
    ]
    require(mounted, "mounted indexed wizard library missing")
    return {
        "published_bundle": os.path.basename(blend_path),
        "manifest": os.path.basename(index_path),
        "mounted_library": mounted[0].name,
        "state": "completed",
        "single_model_handoff": single_model_handoff,
        "auto_import_handoff": auto_import_handoff,
        "project_default_indexed_plan": project_default_indexed_plan,
        "stale_project_default_instruction": stale_project_default_instruction,
        "handoff_target_after_active_switch": armature.name,
    }


def build_single_skeleton_bundle():
    load_addon_package()
    stages = __import__(f"{ADDON_PACKAGE}.Fmodel.Pipeline.stages", fromlist=["LibraryBuildingStage"])
    core = __import__(f"{ADDON_PACKAGE}.core", fromlist=["PipelineContext"])

    bpy.ops.wm.read_factory_settings(use_empty=True)
    armature_data = bpy.data.armatures.new("HeroRigData")
    armature = bpy.data.objects.new("HeroRig", armature_data)
    armature["FModel_Skeleton"] = "Hero"
    bpy.context.collection.objects.link(armature)
    action = bpy.data.actions.new("Idle")
    action["Pipeline_Source"] = os.path.join(OUTPUT, "source", "Idle.ueanim")
    action["Pipeline_SourceRef"] = "Hero/Locomotion/Idle"

    context = core.PipelineContext(
        dictionary_manifest={"skeletons": {"Hero": {}}},
        imported_assets={"imported_actions": [{
            "action_name": "Idle",
            "source_file": action["Pipeline_Source"],
            "source_manifest_ref": "Hero/Locomotion/Idle",
        }]},
        config={
            "output_dir": OUTPUT,
            "source_dir": os.path.join(OUTPUT, "source"),
            "library_name": LIBRARY_NAME,
            "chunk_size": 500,
            "force_rebuild": False,
            "generate_thumbnails": False,
            "target_armature": "HeroRig",
        },
    )
    stage = stages.LibraryBuildingStage()
    require(stage.validate_input(context) == (True, ""), "build-stage validation failed")
    require(stage.execute(context), f"build-stage execution failed: {context.logs}")

    blend_path = os.path.join(OUTPUT, f"{LIBRARY_NAME}_Hero.blend")
    sidecar_path = os.path.join(OUTPUT, f"{LIBRARY_NAME}_Hero_manifest.json")
    index_path = os.path.join(OUTPUT, f"{LIBRARY_NAME}__library_manifest.json")
    require(os.path.isfile(blend_path), "single-skeleton blend was not published")
    require(os.path.isfile(sidecar_path), "same-stem sidecar was not published")
    require(os.path.isfile(index_path), "v2 library index was not published")
    with open(sidecar_path, "r", encoding="utf-8") as handle:
        sidecar = json.load(handle)
    with open(index_path, "r", encoding="utf-8") as handle:
        index = json.load(handle)
    require(sidecar["Hero"]["Locomotion"]["__actions__"] == [["Idle", ""]], sidecar)
    require(index["version"] == "2.0", index)
    require(isinstance(index.get("library_id"), str) and index["library_id"], index)
    require(index["bundles"] == [{
        "blend_file": f"{LIBRARY_NAME}_Hero.blend",
        "sidecar_file": f"{LIBRARY_NAME}_Hero_manifest.json",
        "skeletons": ["Hero"],
        "processed_sources": ["Hero::Idle.ueanim"],
        "completion_state": "completed",
    }], index["bundles"])
    return blend_path, sidecar_path, index_path, index


def verify_scan_and_repair(blend_path, sidecar_path, index_path, index):
    scanner = load_addon_module(
        "Assets.Anims.library_scanner",
        "Assets/Anims/library_scanner.py",
    )
    tree, level = scanner.load_bundle_tree(blend_path, index, ["WrongFallback"], "v2_index")
    require(level == "v2_index", level)
    require(tree["Hero"]["Locomotion"]["__actions__"] == [["Idle", ""]], tree)

    scan_copy = os.path.join(os.path.dirname(__file__), "missing_sidecar_scan")
    os.makedirs(scan_copy, exist_ok=True)
    copied_blend = os.path.join(scan_copy, os.path.basename(blend_path))
    copied_index = os.path.join(scan_copy, os.path.basename(index_path))
    shutil.copy2(blend_path, copied_blend)
    shutil.copy2(index_path, copied_index)
    recovered_index, index_level = scanner.load_library_index(scan_copy, LIBRARY_NAME)
    tree, level = scanner.load_bundle_tree(copied_blend, recovered_index, ["WrongFallback"], index_level)
    require(level == "metadata_missing", level)
    require(tree["Hero"]["Metadata Missing"]["__actions__"] == [["WrongFallback", ""]], tree)
    require(not os.path.exists(os.path.splitext(copied_blend)[0] + "_manifest.json"), "scan wrote a sidecar")

    repair_copy = os.path.join(os.path.dirname(__file__), "repair_copy")
    os.makedirs(repair_copy, exist_ok=True)
    repair_blend = os.path.join(repair_copy, os.path.basename(blend_path))
    repair_index = os.path.join(repair_copy, os.path.basename(index_path))
    shutil.copy2(blend_path, repair_blend)
    shutil.copy2(index_path, repair_index)
    before = sha256(repair_blend)
    result = scanner.repair_library_metadata(repair_copy, LIBRARY_NAME, lambda path: ["WrongFallback"])
    after = sha256(repair_blend)
    require(result == {"sidecars_created": 1, "index_records_added": 0}, result)
    require(before == after, (before, after))
    require(os.path.isfile(os.path.splitext(repair_blend)[0] + "_manifest.json"), "repair sidecar missing")
    return before


def write_action_bundle(path, marker):
    action = bpy.data.actions.new("Walk")
    action["bundle_marker"] = marker
    bpy.data.libraries.write(path, {action}, fake_user=True)
    bpy.data.actions.remove(action)


def verify_duplicate_source_identity():
    selection = load_file_module("task7_action_selection", "core/action_selection.py")
    duplicate_dir = os.path.join(os.path.dirname(__file__), "duplicate_identity")
    os.makedirs(duplicate_dir, exist_ok=True)
    first = os.path.join(duplicate_dir, "First.blend")
    second = os.path.join(duplicate_dir, "Second.blend")
    write_action_bundle(first, "first")
    write_action_bundle(second, "second")
    with bpy.data.libraries.load(first, link=True) as (data_from, data_to):
        data_to.actions = ["Walk"]
    first_action = data_to.actions[0]
    with bpy.data.libraries.load(second, link=True) as (data_from, data_to):
        data_to.actions = ["Walk"]
    second_action = data_to.actions[0]
    selected, error = selection.resolve_imported_action(bpy, {
        "action_name": "Walk",
        "source_bundle": second,
        "source_manifest_ref": "Hero/Locomotion/Walk",
    })
    require(not error, error)
    require(selected == second_action, "selected action did not come from second bundle")
    require(os.path.normcase(os.path.abspath(selected.library.filepath)) == os.path.normcase(os.path.abspath(second)), selected.library.filepath)
    return first_action.name, second_action.name


def main():
    if os.path.isdir(OUTPUT):
        shutil.rmtree(OUTPUT)
    for name in ("missing_sidecar_scan", "repair_copy", "duplicate_identity"):
        path = os.path.join(os.path.dirname(__file__), name)
        if os.path.isdir(path):
            shutil.rmtree(path)
    os.makedirs(OUTPUT)
    indexed_result = verify_project_index_projection()
    indexed_wizard_publication = verify_indexed_wizard_publication()
    blend_path, sidecar_path, index_path, index = build_single_skeleton_bundle()
    digest = verify_scan_and_repair(blend_path, sidecar_path, index_path, index)
    duplicate_names = verify_duplicate_source_identity()
    print("TASK7_INTEGRATION_RESULT=" + json.dumps({
        "single_skeleton_pair": [os.path.basename(blend_path), os.path.basename(sidecar_path)],
        "v2_index": os.path.basename(index_path),
        "library_id": index["library_id"],
        "authoritative_scan": "v2_index",
        "missing_sidecar_scan": "metadata_missing",
        "repair_blend_sha256_before_after": digest,
        "duplicate_action_names": duplicate_names,
        "indexed_result": indexed_result,
        "indexed_wizard_publication": indexed_wizard_publication,
        "single_model_handoff": indexed_wizard_publication["single_model_handoff"],
        "auto_import_handoff": indexed_wizard_publication["auto_import_handoff"],
    }, sort_keys=True))


main()
