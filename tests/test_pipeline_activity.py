"""Pure-Python contracts for pipeline activity suppression."""

import builtins
import unittest

from core.pipeline_activity import is_pipeline_active, pipeline_activity


_STATE_KEY = "_fmodel_tools_pipeline_activity"


class PipelineActivityTests(unittest.TestCase):
    def tearDown(self):
        builtins.__dict__.pop(_STATE_KEY, None)

    def test_scope_is_nested_and_exception_safe(self):
        self.assertFalse(is_pipeline_active())
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with pipeline_activity():
                self.assertTrue(is_pipeline_active())
                with pipeline_activity():
                    self.assertTrue(is_pipeline_active())
                self.assertTrue(is_pipeline_active())
                raise RuntimeError("boom")
        self.assertFalse(is_pipeline_active())

