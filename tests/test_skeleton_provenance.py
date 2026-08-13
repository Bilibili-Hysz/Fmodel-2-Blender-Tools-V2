"""Pure-Python contracts for canonical FModel skeleton provenance."""

import importlib.util
import json
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "skeleton_provenance.py"


def load_module():
    spec = importlib.util.spec_from_file_location("skeleton_provenance", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SkeletonProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provenance = load_module()

    def valid_identity(self):
        return {
            "version": 1,
            "skeleton_path": "/Game/Characters/Hero/SK_Hero_Skeleton",
            "has_skeleton_guid": True,
            "skeleton_guid": "00112233445566778899aabbccddeeff",
            "bone_layout_hash": "0123456789abcdef" * 4,
        }

    def test_normalize_canonical_identity(self):
        normalized = self.provenance.normalize_skeleton_identity(self.valid_identity())
        self.assertEqual(normalized["version"], 1)
        self.assertEqual(normalized["skeleton_guid"], "00112233445566778899aabbccddeeff")
        self.assertEqual(normalized["bone_layout_hash"], "0123456789abcdef" * 4)
        self.assertEqual(
            self.provenance.skeleton_identity_fingerprint(normalized),
            self.provenance.skeleton_identity_fingerprint(json.dumps(normalized)),
        )

    def test_normalize_accepts_decoded_bytes_and_target_source_path(self):
        raw = self.valid_identity()
        raw["skeleton_guid"] = bytes.fromhex(raw["skeleton_guid"])
        raw["bone_layout_hash"] = bytes.fromhex(raw["bone_layout_hash"])
        raw["source_animation_path"] = "/Game/Characters/Hero/Animations/A_Run"
        normalized = self.provenance.normalize_skeleton_identity(raw)
        self.assertEqual(normalized["skeleton_guid"], "00112233445566778899aabbccddeeff")
        self.assertEqual(normalized["source_animation_path"], raw["source_animation_path"])

    def test_missing_identity_is_legacy_not_strict(self):
        result = self.provenance.inspect_skeleton_provenance(None)
        self.assertEqual(result.state, self.provenance.PROVENANCE_LEGACY)
        self.assertFalse(result.is_strict)
        self.assertEqual(result.fingerprint, "")

    def test_owner_property_is_classified(self):
        module = self.provenance

        class Owner:
            def __init__(self, value):
                self.value = value

            def get(self, key, default=None):
                return self.value if key == module.SKELETON_IDENTITY_PROPERTY else default

        owner = Owner(json.dumps(self.valid_identity()))
        result = self.provenance.skeleton_provenance_from_owner(owner)
        self.assertEqual(result.state, self.provenance.PROVENANCE_STRICT)
        self.assertEqual(result.identity["skeleton_path"], "/Game/Characters/Hero/SK_Hero_Skeleton")

    def test_malformed_present_identity_is_invalid(self):
        raw = self.valid_identity()
        raw["bone_layout_hash"] = "bad"
        result = self.provenance.inspect_skeleton_provenance(raw)
        self.assertEqual(result.state, self.provenance.PROVENANCE_INVALID)
        self.assertIn("bone_layout_hash", result.reason)

    def test_rejects_inconsistent_guid_flag_and_paths(self):
        raw = self.valid_identity()
        raw["has_skeleton_guid"] = False
        result = self.provenance.inspect_skeleton_provenance(raw)
        self.assertEqual(result.state, self.provenance.PROVENANCE_INVALID)

        raw = self.valid_identity()
        raw["skeleton_path"] = r"C:\\Game\\Hero"
        result = self.provenance.inspect_skeleton_provenance(raw)
        self.assertEqual(result.state, self.provenance.PROVENANCE_INVALID)

    def test_rejects_two_target_source_kinds(self):
        raw = self.valid_identity()
        raw["source_animation_path"] = "/Game/Hero/Run"
        raw["source_pose_path"] = "/Game/Hero/Pose"
        result = self.provenance.inspect_skeleton_provenance(raw)
        self.assertEqual(result.state, self.provenance.PROVENANCE_INVALID)

    def test_validate_persisted_strict_record_matches_fingerprint(self):
        identity = self.valid_identity()
        fingerprint = self.provenance.skeleton_identity_fingerprint(identity)
        record = {
            "state": "strict",
            "identity": identity,
            "fingerprint": fingerprint,
            "reason": "",
        }

        normalized = self.provenance.validate_provenance_record(record)

        self.assertEqual(normalized["fingerprint"], fingerprint)
        self.assertEqual(normalized["identity"], self.provenance.normalize_skeleton_identity(identity))

    def test_validate_persisted_record_rejects_mismatched_strict_fingerprint(self):
        record = {
            "state": "strict",
            "identity": self.valid_identity(),
            "fingerprint": "f" * 64,
            "reason": "",
        }

        with self.assertRaises(self.provenance.SkeletonProvenanceError):
            self.provenance.validate_provenance_record(record)

    def test_validate_persisted_empty_record_is_legacy_compatible(self):
        self.assertEqual(self.provenance.validate_provenance_record({}), {})


if __name__ == "__main__":
    unittest.main()
