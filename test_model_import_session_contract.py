from pathlib import Path
import unittest


ROOT = Path(__file__).parent
MODULE = ROOT / "Fmodel" / "importers" / "model" / "operator_model_ueformat.py"


class ModelImportSessionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MODULE.read_text(encoding="utf-8")

    def test_model_operator_creates_and_persists_import_session(self):
        self.assertIn('ImportSession("UEModel"', self.source)
        self.assertIn("persist_import_session", self.source)
        self.assertIn('session.record_success(safe_path)', self.source)
        self.assertIn('session.record_failure(', self.source)
        self.assertIn('session.finish()', self.source)

    def test_model_operator_records_transaction_datablock_counts(self):
        self.assertIn('session.record_created(kind, count)', self.source)
        self.assertIn('session.record_created("objects", 1)', self.source)
        self.assertIn('session.record_created("meshes", len(meshes))', self.source)

    def test_model_operator_reports_bridge_and_option_failures(self):
        self.assertIn('code="ueformat_capability"', self.source)
        self.assertIn('code="ueformat_missing"', self.source)
        self.assertIn('code="ueformat_options_invalid"', self.source)


if __name__ == "__main__":
    unittest.main()
