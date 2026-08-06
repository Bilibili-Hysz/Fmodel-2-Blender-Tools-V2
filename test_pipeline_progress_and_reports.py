"""Regression coverage for pipeline import progress and failure reports."""

import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).parent
STAGES = ROOT / "Fmodel" / "Pipeline" / "stages.py"


def load_asset_import_stage(globals_):
    tree = ast.parse(STAGES.read_text(encoding="utf-8"))
    definition = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AssetImportStage"
    )
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(STAGES), "exec"), globals_)
    return globals_["AssetImportStage"]


class PipelineProgressTests(unittest.TestCase):
    def test_progress_reads_the_active_blender_context_window_manager(self):
        tree = ast.parse(STAGES.read_text(encoding="utf-8"))
        definition = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "AssetImportStage"
        )
        report_progress = next(
            node for node in definition.body
            if isinstance(node, ast.FunctionDef) and node.name == "_report_progress"
        )
        window_manager_reads = [
            node for node in ast.walk(report_progress)
            if isinstance(node, ast.Attribute) and node.attr == "window_manager"
        ]

        self.assertTrue(any(
            isinstance(node.value, ast.Attribute)
            and node.value.attr == "context"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "bpy"
            for node in window_manager_reads
        ))

    def test_progress_uses_blender_window_manager_once_and_balances_lifecycle(self):
        calls = []
        window_manager = SimpleNamespace(
            progress_begin=lambda minimum, maximum: calls.append(("begin", minimum, maximum)),
            progress_update=lambda current: calls.append(("update", current)),
            progress_end=lambda: calls.append(("end",)),
        )
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=window_manager))
        stage = load_asset_import_stage({"bpy": bpy, "PipelineStageBase": object})()
        pipeline_context = SimpleNamespace(log_info=lambda *_args: None)

        stage._report_progress(pipeline_context, 0, 3, "Import")
        stage._report_progress(pipeline_context, 1, 3, "Import")
        stage._report_progress(pipeline_context, 3, 3, "Import")

        self.assertEqual(calls, [
            ("begin", 0, 3),
            ("update", 0),
            ("update", 1),
            ("update", 3),
            ("end",),
        ])

    def test_finish_progress_ends_an_active_indicator_after_an_error(self):
        calls = []
        window_manager = SimpleNamespace(
            progress_begin=lambda minimum, maximum: calls.append(("begin", minimum, maximum)),
            progress_update=lambda current: calls.append(("update", current)),
            progress_end=lambda: calls.append(("end",)),
        )
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=window_manager))
        stage = load_asset_import_stage({"bpy": bpy, "PipelineStageBase": object})()
        pipeline_context = SimpleNamespace(log_info=lambda *_args: None)

        stage._report_progress(pipeline_context, 0, 3, "Import")
        stage._finish_progress()

        self.assertEqual(calls, [("begin", 0, 3), ("update", 0), ("end",)])

    def test_progress_update_failure_still_ends_started_indicator(self):
        calls = []

        def fail_update(_current):
            calls.append(("update",))
            raise RuntimeError("window closed")

        window_manager = SimpleNamespace(
            progress_begin=lambda minimum, maximum: calls.append(("begin", minimum, maximum)),
            progress_update=fail_update,
            progress_end=lambda: calls.append(("end",)),
        )
        bpy = SimpleNamespace(context=SimpleNamespace(window_manager=window_manager))
        stage = load_asset_import_stage({"bpy": bpy, "PipelineStageBase": object})()
        pipeline_context = SimpleNamespace(log_info=lambda *_args: None)

        stage._report_progress(pipeline_context, 0, 3, "Import")

        self.assertEqual(calls, [("begin", 0, 3), ("update",), ("end",)])


class PipelineFailureReportTests(unittest.TestCase):
    def test_failure_report_is_durable_and_not_in_pipeline_temp_directory(self):
        with TemporaryDirectory() as output_dir:
            bpy = SimpleNamespace(path=SimpleNamespace(abspath=lambda value: value))
            stage = load_asset_import_stage({
                "bpy": bpy,
                "PipelineStageBase": object,
                "os": __import__("os"),
                "json": json,
                "time": __import__("time"),
            })()
            stage.get_stage_name = lambda: "AssetImport"
            collection = SimpleNamespace(failed_imports=[{"source_file": "bad.ueanim", "error": "bad"}])
            context = SimpleNamespace(get_config=lambda name, default="": output_dir if name == "output_dir" else default)

            report_path = stage._write_failure_report(context, collection)

            self.assertTrue(Path(report_path).is_file())
            self.assertEqual(Path(report_path).parent, Path(output_dir) / "reports")
            self.assertNotIn("_pipeline_tmp", report_path)
            self.assertEqual(json.loads(Path(report_path).read_text(encoding="utf-8"))["failed_count"], 1)


if __name__ == "__main__":
    unittest.main()
