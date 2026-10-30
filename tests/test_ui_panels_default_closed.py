"""Source contracts for FModel View3D sidebar panel defaults."""

import ast
from pathlib import Path
import unittest


UI_PATH = Path(__file__).resolve().parents[1] / "ui.py"


def _literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _class_assignments(class_node):
    return {
        target.id: statement.value
        for statement in class_node.body
        if isinstance(statement, ast.Assign)
        for target in statement.targets
        if isinstance(target, ast.Name)
    }


class FModelSidebarPanelDefaultsTests(unittest.TestCase):
    def test_daily_workspace_open_and_secondary_panels_closed(self):
        tree = ast.parse(UI_PATH.read_text(encoding="utf-8"))
        scoped_panels = []

        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not node.name.startswith("FMODEL_PT_"):
                continue
            assignments = _class_assignments(node)
            if (
                _literal_string(assignments.get("bl_space_type")) == "VIEW_3D"
                and _literal_string(assignments.get("bl_region_type")) == "UI"
                and _literal_string(assignments.get("bl_category")) == "FModel"
            ):
                scoped_panels.append((node.name, assignments.get("bl_options")))

        self.assertTrue(scoped_panels, "expected at least one FModel View3D sidebar panel")
        for class_name, options in scoped_panels:
            with self.subTest(panel=class_name):
                if class_name in {"FMODEL_PT_WorkContext", "FMODEL_PT_AssetLibrary", "FMODEL_PT_Pipeline"}:
                    self.assertIsNone(options)
                    continue
                self.assertIsInstance(options, (ast.Set, ast.Tuple, ast.List))
                self.assertIn(
                    "HIDE_HEADER" if class_name in {
                        "FMODEL_PT_ModelAssets", "FMODEL_PT_AnimationAssets", "FMODEL_PT_Pipeline"
                    } else "DEFAULT_CLOSED",
                    {_literal_string(element) for element in options.elts},
                )


if __name__ == "__main__":
    unittest.main()
