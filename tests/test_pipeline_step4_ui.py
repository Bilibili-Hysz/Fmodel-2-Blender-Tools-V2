"""Fake-Blender UI contracts for pipeline Step 4 target readiness."""

import ast
from core.workflow import resolve_target
from pathlib import Path
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FakeLayout:
    def __init__(self):
        self.labels = []
        self.operators = []
        self.enabled = True
        self.scale_y = 1.0
        self.alert = False

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def box(self):
        return self

    def label(self, **kwargs):
        self.labels.append(kwargs)

    def operator(self, identifier, **kwargs):
        self.operators.append((identifier, self.enabled, kwargs))
        return types.SimpleNamespace()

    def prop(self, *_args, **_kwargs):
        pass

    def separator(self):
        pass


class PipelineStep4UiTests(unittest.TestCase):
    def _panel_class(self, objects):
        tree = ast.parse((ROOT / "ui.py").read_text(encoding="utf-8"))
        panel_node = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "FMODEL_PT_Pipeline"
        )
        namespace = {
            "bpy": types.SimpleNamespace(
                types=types.SimpleNamespace(Panel=object),
                data=types.SimpleNamespace(objects=objects),
            ),
            "t": lambda text: text,
            "resolve_target": resolve_target,
        }
        exec(compile(ast.Module(body=[next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_draw_pipeline_mapping"), panel_node], type_ignores=[]), "ui.py", "exec"), namespace)
        return namespace["FMODEL_PT_Pipeline"]

    def _draw_step4(self, objects, stored_target_name, active_object=None, lib_completed=False):
        panel = self._panel_class(objects)()
        panel.layout = FakeLayout()
        state = types.SimpleNamespace(
            current_step=4,
            flow_type="FMODEL",
            source_dir="C:/Animations",
            output_dir="C:/Output",
            scan_completed=True,
            scan_skeleton_count=1,
            scan_anim_count=1,
            scan_new_count=1,
            import_target_skeleton=stored_target_name,
            skeleton_mappings=[],
            unmapped_skeletons="",
            import_scale=1.0,
            rotation_only=False,
            import_curves=False,
            lib_generate_thumbnails=False,
            lib_thumb_resolution=256,
            lib_thumb_quality="VIEWPORT",
            lib_thumb_isolate=False,
            lib_chunk_size=20,
            lib_force_rebuild=False,
            lib_save_model=False,
            lib_model_preview=False,
            lib_completed=lib_completed,
        )
        context = types.SimpleNamespace(
            active_object=active_object,
            scene=types.SimpleNamespace(pipeline_wizard=state, objects=objects),
        )

        panel.draw(context)
        return panel.layout

    def test_single_skeleton_step4_accepts_valid_stored_target_without_active_armature(self):
        stored_target = types.SimpleNamespace(name="HeroRig", type="ARMATURE")
        layout = self._draw_step4({stored_target.name: stored_target}, stored_target.name)

        import_button = next(
            call for call in layout.operators if call[0] == "pipeline.wizard_step4"
        )
        self.assertTrue(import_button[1])
        self.assertNotIn({"text": "请选中骨架", "icon": "ERROR"}, layout.labels)

    def test_single_skeleton_step4_displays_valid_stored_target_over_active_armature(self):
        stored_target = types.SimpleNamespace(name="HeroRig", type="ARMATURE")
        active_target = types.SimpleNamespace(name="EnemyRig", type="ARMATURE")
        layout = self._draw_step4(
            {stored_target.name: stored_target, active_target.name: active_target},
            stored_target.name,
            active_target,
        )

        import_button = next(
            call for call in layout.operators if call[0] == "pipeline.wizard_step4"
        )
        self.assertTrue(import_button[1])
        self.assertIn({"text": "骨架: HeroRig", "icon": "ARMATURE_DATA"}, layout.labels)
        self.assertNotIn({"text": "骨架: EnemyRig", "icon": "ARMATURE_DATA"}, layout.labels)

    def test_single_skeleton_step4_rejects_stale_stored_target_without_active_armature(self):
        layout = self._draw_step4({}, "DeletedRig")

        import_button = next(
            call for call in layout.operators if call[0] == "pipeline.wizard_step4"
        )
        self.assertFalse(import_button[1])
        self.assertIn({"text": "请选中骨架", "icon": "ERROR"}, layout.labels)

    def test_completed_step4_uses_compact_view_instead_of_mapping_editor(self):
        stored_target = types.SimpleNamespace(name="HeroRig", type="ARMATURE")
        layout = self._draw_step4(
            {stored_target.name: stored_target},
            stored_target.name,
            lib_completed=True,
        )

        self.assertIn({"text": "导入并打包已完成", "icon": "CHECKMARK"}, layout.labels)
        self.assertNotIn(
            "pipeline.wizard_step4",
            [identifier for identifier, _enabled, _kwargs in layout.operators],
        )
        self.assertIn(
            "fmodel.open_workflow",
            [identifier for identifier, _enabled, _kwargs in layout.operators],
        )
        self.assertIn(
            "pipeline.wizard_reset",
            [identifier for identifier, _enabled, _kwargs in layout.operators],
        )


if __name__ == "__main__":
    unittest.main()
