"""Project Index boundary contracts for binary UEFormat provenance probing."""

import importlib.util
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).parent
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))


def load_project_index():
    path = CORE / "project_index.py"
    spec = importlib.util.spec_from_file_location("project_index_probe", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fstring(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def section(name: str, array_size: int, payload: bytes) -> bytes:
    return fstring(name) + struct.pack("<ii", array_size, len(payload)) + payload


def model_bytes(reader, identity=True) -> bytes:
    archive = bytearray(section("LODS", 0, b""))
    if identity:
        payload = bytearray([1])
        payload.extend(fstring("/Game/Hero/SK_Hero_Skeleton"))
        payload.append(0)
        payload.extend(bytes.fromhex("0123456789abcdef" * 4))
        archive.extend(section("FMODEL_SKELETON_IDENTITY", 1, bytes(payload)))
    return (
        reader.MAGIC
        + fstring(reader.MODEL_IDENTIFIER)
        + bytes([9])
        + fstring("SyntheticAsset")
        + bytes([0])
        + bytes(archive)
    )


def animation_bytes(reader) -> bytes:
    identity = bytearray([1])
    identity.extend(fstring("/Game/Hero/Animations/Run"))
    identity.extend(fstring("/Game/Hero/SK_Hero_Skeleton"))
    identity.append(0)
    identity.extend(bytes.fromhex("0123456789abcdef" * 4))
    archive = bytearray(section("TRACKS", 0, b""))
    archive.extend(section("FMODEL_TARGET_SKELETON", 1, bytes(identity)))
    return (
        reader.MAGIC
        + fstring(reader.ANIM_IDENTIFIER)
        + bytes([9])
        + fstring("SyntheticRun")
        + bytes([0])
        + bytes(archive)
    )


class ProjectIndexUEFormatProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_index = load_project_index()
        import ueformat_identity_reader as reader
        cls.reader = reader

    def test_probe_reads_strict_identity_without_creating_or_rebuilding_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            path = root / "Hero.uemodel"
            path.write_bytes(model_bytes(self.reader))
            index = self.project_index.ProjectIndex(root)

            result = index.probe_ueformat_provenance(path)

            self.assertEqual(result["state"], "strict")
            self.assertEqual(result["kind"], "model")
            self.assertEqual(result["identity"]["skeleton_path"], "/Game/Hero/SK_Hero_Skeleton")
            self.assertFalse(index.path.exists())

    def test_scan_persists_strict_provenance_columns_in_schema_v3(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            asset = root / "Characters" / "Hero.uemodel"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(model_bytes(self.reader))
            index = self.project_index.ProjectIndex(root)

            result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            connection = sqlite3.connect(index.path)
            try:
                row = connection.execute(
                    "SELECT provenance_state, skeleton_path, skeleton_guid, "
                    "bone_layout_hash, provenance_fingerprint, provenance_reason "
                    "FROM assets WHERE canonical_path = ?",
                    (str(asset.resolve()),),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row[0], "strict")
            self.assertEqual(row[1], "/Game/Hero/SK_Hero_Skeleton")
            self.assertIsNone(row[2])
            self.assertEqual(row[3], "0123456789abcdef" * 4)
            self.assertEqual(len(row[4]), 64)
            self.assertEqual(row[5], "")

    def test_scan_prefers_native_identity_over_conflicting_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            asset = root / "WrongDisplayName" / "Run.ueanim"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(animation_bytes(self.reader))
            asset.with_suffix(".json").write_text(
                '[{"Type": "AnimSequence", "Skeleton": {"ObjectName": "Skeleton\'Wrong"}}]',
                encoding="utf-8",
            )
            index = self.project_index.ProjectIndex(root)

            result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            self.assertEqual(
                index.animations_for_skeleton("SK_Hero_Skeleton"),
                [str(asset.resolve())],
            )
            self.assertEqual(index.animations_for_skeleton("Wrong"), [])
            manifest = index.dictionary_manifest("SK_Hero_Skeleton", [str(asset)])
            metadata = manifest["skeletons"]["SK_Hero_Skeleton"]["Base"]["__animations__"]["Run"]["metadata"]
            self.assertTrue(metadata["native_ueformat"])
            self.assertEqual(metadata["skeleton_path"], "/Game/Hero/SK_Hero_Skeleton")

    def test_scan_native_identity_survives_malformed_optional_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            asset = root / "Hero" / "Run.ueanim"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(animation_bytes(self.reader))
            asset.with_suffix(".json").write_text("{not valid json", encoding="utf-8")
            index = self.project_index.ProjectIndex(root)

            result = index.scan([root])

            self.assertEqual(result["state"], "ready")
            connection = sqlite3.connect(index.path)
            try:
                row = connection.execute(
                    "SELECT provenance_state, skeleton_path FROM assets WHERE canonical_path = ?",
                    (str(asset.resolve()),),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row, ("strict", "/Game/Hero/SK_Hero_Skeleton"))

    def test_native_index_does_not_depend_on_sidecar_after_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            asset = root / "Hero" / "Run.ueanim"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(animation_bytes(self.reader))
            sidecar = asset.with_suffix(".json")
            sidecar.write_text(
                '[{"Type": "AnimSequence", "Skeleton": {"ObjectName": "Skeleton\'Wrong"}}]',
                encoding="utf-8",
            )
            index = self.project_index.ProjectIndex(root)

            self.assertEqual(index.scan([root])["state"], "ready")
            sidecar.unlink()

            self.assertEqual(index.scan([root])["state"], "ready")
            manifest = index.dictionary_manifest("SK_Hero_Skeleton", [str(asset)])
            self.assertTrue(
                manifest["skeletons"]["SK_Hero_Skeleton"]["Base"]["__animations__"]["Run"]
                ["metadata"]["native_ueformat"]
            )

    def test_probe_reports_legacy_for_valid_file_without_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Legacy.uemodel"
            path.write_bytes(model_bytes(self.reader, identity=False))
            index = self.project_index.ProjectIndex(directory)

            result = index.probe_ueformat_provenance(path)

            self.assertEqual(result["state"], "legacy")
            self.assertIsNone(result["identity"])

    def test_probe_fails_closed_and_can_raise_for_malformed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Broken.uemodel"
            path.write_bytes(b"not-ueformat")
            index = self.project_index.ProjectIndex(directory)

            result = index.probe_ueformat_provenance(path)
            self.assertEqual(result["state"], "invalid")
            with self.assertRaises(ValueError):
                index.probe_ueformat_provenance(path, raise_on_error=True)


if __name__ == "__main__":
    unittest.main()
