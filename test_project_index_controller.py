"""Duck-typed WizardState contracts for the Animation Pipeline index adapter."""

import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

from core.project_index_service import (
    PreparedAnimationSelection,
    ProjectIndexServiceError,
    ProjectIndexSnapshot,
)
def _load_controller():
    prefix = "_fmodel_controller_test"
    names = (
        prefix, f"{prefix}.core", f"{prefix}.core.project_index_service",
        f"{prefix}.Fmodel", f"{prefix}.Fmodel.Pipeline",
        f"{prefix}.Fmodel.Pipeline.project_index_controller",
    )
    saved = {name: sys.modules.get(name) for name in names}
    root_package = types.ModuleType(prefix)
    root_package.__path__ = []
    fmodel = types.ModuleType(f"{prefix}.Fmodel")
    fmodel.__path__ = []
    pipeline = types.ModuleType(f"{prefix}.Fmodel.Pipeline")
    pipeline.__path__ = []
    sys.modules.update({
        prefix: root_package,
        f"{prefix}.core": importlib.import_module("core"),
        f"{prefix}.core.project_index_service": importlib.import_module("core.project_index_service"),
        f"{prefix}.Fmodel": fmodel,
        f"{prefix}.Fmodel.Pipeline": pipeline,
    })
    try:
        path = Path(__file__).parent / "Fmodel" / "Pipeline" / "project_index_controller.py"
        spec = importlib.util.spec_from_file_location(
            f"{prefix}.Fmodel.Pipeline.project_index_controller", path
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


_CONTROLLER = _load_controller()
inspect_pipeline_project_index = _CONTROLLER.inspect_pipeline_project_index
prepare_explicit_selection = _CONTROLLER.prepare_explicit_selection
prepare_project_default_selection = _CONTROLLER.prepare_project_default_selection


def snapshot(*, state="ready", matching=2):
    return ProjectIndexSnapshot(
        root="C:/Project",
        state=state,
        schema_version=3,
        generation=4,
        last_scan_ns=123456,
        animation_count=8,
        matching_count=matching,
        message="Animation index ready",
    )


def wizard(**overrides):
    state = types.SimpleNamespace(
        project_root="C:/Project",
        skeleton_filter="Hero",
        project_index_state="missing",
        project_index_schema_version=0,
        project_index_generation=0,
        project_index_animation_count=0,
        project_index_match_count=0,
        project_index_last_scan_ns="",
        project_index_message="",
        indexed_project_root="",
        indexed_skeleton="",
        indexed_selected_paths="[]",
        indexed_selection_mode="NONE",
    )
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


class ProjectIndexControllerTests(unittest.TestCase):
    def test_inspect_applies_read_only_snapshot_to_wizard(self):
        service = types.SimpleNamespace(
            inspect=lambda root, skeleton: snapshot(matching=3)
        )
        state = wizard()

        result = inspect_pipeline_project_index(state, service=service)

        self.assertEqual(result.matching_count, 3)
        self.assertEqual(state.project_index_state, "ready")
        self.assertEqual(state.project_index_generation, 4)
        self.assertEqual(state.project_index_animation_count, 8)
        self.assertEqual(state.project_index_match_count, 3)
        self.assertEqual(state.project_index_last_scan_ns, "123456")

    def test_prepare_project_default_persists_exact_paths_and_auto_mode(self):
        prepared = PreparedAnimationSelection(
            root="C:/Project",
            skeleton="Hero",
            paths=("C:/Project/Hero/Idle.ueanim", "C:/Project/Hero/Run.ueanim"),
            manifest={"skeletons": {"Hero": {}}},
            snapshot=snapshot(),
        )
        service = types.SimpleNamespace(
            prepare_selection=lambda root, skeleton: prepared
        )
        state = wizard()

        result = prepare_project_default_selection(state, service=service)

        self.assertIs(result, prepared)
        self.assertEqual(state.indexed_selection_mode, "AUTO_PROJECT")
        self.assertEqual(state.indexed_project_root, "C:/Project")
        self.assertEqual(state.indexed_skeleton, "Hero")
        self.assertEqual(json.loads(state.indexed_selected_paths), list(prepared.paths))

    def test_prepare_explicit_preserves_legacy_subset_mode(self):
        prepared = PreparedAnimationSelection(
            root="C:/Project",
            skeleton="Hero",
            paths=("C:/Project/Hero/Idle.ueanim",),
            manifest={"skeletons": {"Hero": {}}},
            snapshot=snapshot(matching=1),
        )
        service = types.SimpleNamespace(
            prepare_paths=lambda root, skeleton, paths: prepared
        )
        state = wizard()

        prepare_explicit_selection(
            state, prepared.root, prepared.skeleton, list(prepared.paths), service=service
        )

        self.assertEqual(state.indexed_selection_mode, "EXPLICIT")
        self.assertEqual(json.loads(state.indexed_selected_paths), list(prepared.paths))

    def test_no_match_error_updates_visible_state_and_is_reraised(self):
        error = ProjectIndexServiceError(
            "no_match", "No indexed animations match skeleton: Enemy"
        )

        def fail(_root, _skeleton):
            raise error

        state = wizard(skeleton_filter="Enemy")
        service = types.SimpleNamespace(prepare_selection=fail)

        with self.assertRaises(ProjectIndexServiceError) as raised:
            prepare_project_default_selection(state, service=service)

        self.assertIs(raised.exception, error)
        self.assertEqual(state.project_index_state, "no_match")
        self.assertEqual(state.project_index_match_count, 0)
        self.assertIn("Enemy", state.project_index_message)


if __name__ == "__main__":
    unittest.main()
