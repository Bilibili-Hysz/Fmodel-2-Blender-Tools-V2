"""Synthetic UEFormat envelope contracts for the restricted identity reader.

Covers both wire generations: V9 (bare chunk sequence, no ObjectPath) and
V10 (attribute count, ObjectPath header field, versioned FModel envelopes).
The canonical producer fixtures under ``tests/fixtures/fmodel_v10`` anchor the
V10 layout to real bytes.
"""

import gzip
import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "fmodel_v10"


def load_reader():
    spec = importlib.util.spec_from_file_location("ueformat_identity_reader", CORE / "ueformat_identity_reader.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_optional_identity_can_be_ignored_without_ignoring_outer_corruption():
    reader = load_reader()
    for version in (9, 10):
        data = ueformat_file(reader, kind='animation', file_version=version, identity_payload=b'\x02')
        record = reader.read_ueformat_identity_bytes(data, tolerate_identity_errors=True)
        assert record.kind == 'animation' and record.identity is None and record.identity_warnings
        with unittest.TestCase().assertRaises(reader.UEFormatIdentityError):
            reader.read_ueformat_identity_bytes(data)
        with unittest.TestCase().assertRaises(reader.UEFormatIdentityError):
            reader.read_ueformat_identity_bytes(data[:-1], tolerate_identity_errors=True)


def fstring(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def section(name: str, array_size: int, payload: bytes) -> bytes:
    """V9 chunk: [name][count][byte_size][payload]."""
    return fstring(name) + struct.pack("<ii", array_size, len(payload)) + payload


def v10_attribute(name: str, payload: bytes, *, enveloped: bool = False) -> bytes:
    """V10 attribute: [name][total_size][...].

    FModel extension attributes wrap their payload in a versioned envelope
    ``[envelope_version=1][array_size=1][payload]``; standard attributes carry
    a plain payload.
    """
    if enveloped:
        body = bytes([1]) + struct.pack("<i", 1) + payload
        return fstring(name) + struct.pack("<i", len(body)) + body
    return fstring(name) + struct.pack("<i", len(payload)) + payload


def base_identity_payload(reader, *, kind="model", with_guid=True, skeleton_path="/Game/Hero/SK_Hero_Skeleton") -> bytes:
    guid = bytes.fromhex("00112233445566778899aabbccddeeff")
    layout = bytes.fromhex("0123456789abcdef" * 4)
    payload = bytearray([1])
    if kind == "animation":
        payload.extend(fstring("/Game/Hero/Animations/A_Run"))
    elif kind == "pose":
        payload.extend(fstring("/Game/Hero/Poses/P_Run"))
    payload.extend(fstring(skeleton_path))
    payload.append(1 if with_guid else 0)
    if with_guid:
        payload.extend(guid)
    payload.extend(layout)
    return bytes(payload)


def ueformat_file(reader, *, kind="model", identity=True, compressed=False, identity_payload=None, file_version=9) -> bytes:
    identifier = {
        "model": reader.MODEL_IDENTIFIER,
        "animation": reader.ANIM_IDENTIFIER,
        "pose": reader.POSE_IDENTIFIER,
    }[kind]
    resolved_identity = (
        identity_payload if identity_payload is not None
        else base_identity_payload(reader, kind=kind)
    )
    if file_version >= 10:
        attributes = []
        if kind == "animation":
            attributes.append(v10_attribute("TRACKS", struct.pack("<i", 0)))
        elif kind == "pose":
            attributes.append(v10_attribute("POSES", struct.pack("<i", 0)))
        elif kind == "model":
            attributes.append(v10_attribute("LODS", struct.pack("<i", 0)))
        if identity:
            extension = "FMODEL_SKELETON_IDENTITY" if kind == "model" else "FMODEL_TARGET_SKELETON"
            attributes.append(v10_attribute(extension, resolved_identity, enveloped=True))
        payload = struct.pack("<i", len(attributes)) + b"".join(attributes)
    else:
        archive = bytearray()
        if kind == "animation":
            archive.extend(section("TRACKS", 0, b""))
        elif kind == "pose":
            archive.extend(section("POSES", 0, b""))
        elif kind == "model":
            archive.extend(section("LODS", 0, b""))
        if identity:
            extension = "FMODEL_SKELETON_IDENTITY" if kind == "model" else "FMODEL_TARGET_SKELETON"
            archive.extend(section(extension, 1, resolved_identity))
        payload = bytes(archive)
    envelope = bytearray(reader.MAGIC)
    envelope.extend(fstring(identifier))
    envelope.append(file_version)
    envelope.extend(fstring("SyntheticAsset"))
    if file_version >= 10:
        envelope.extend(fstring("/Game/Synthetic/SyntheticAsset.SyntheticAsset"))
    envelope.append(1 if compressed else 0)
    if compressed:
        compressed_payload = gzip.compress(payload)
        envelope.extend(fstring("GZIP"))
        envelope.extend(struct.pack("<ii", len(payload), len(compressed_payload)))
        envelope.extend(compressed_payload)
    else:
        envelope.extend(payload)
    return bytes(envelope)


class UEFormatIdentityReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = load_reader()

    def test_reads_model_identity(self):
        record = self.reader.read_ueformat_identity_bytes(
            ueformat_file(self.reader, kind="model")
        )
        self.assertEqual(record.kind, "model")
        self.assertTrue(record.has_strict_identity)
        self.assertEqual(record.identity["skeleton_path"], "/Game/Hero/SK_Hero_Skeleton")
        self.assertEqual(record.identity["skeleton_guid"], "00112233445566778899aabbccddeeff")
        self.assertEqual(record.identity["bone_layout_hash"], "0123456789abcdef" * 4)

    def test_reads_animation_and_pose_target_identity(self):
        animation = self.reader.read_ueformat_identity_bytes(
            ueformat_file(self.reader, kind="animation")
        )
        pose = self.reader.read_ueformat_identity_bytes(
            ueformat_file(self.reader, kind="pose")
        )
        self.assertEqual(animation.identity["source_animation_path"], "/Game/Hero/Animations/A_Run")
        self.assertEqual(pose.identity["source_pose_path"], "/Game/Hero/Poses/P_Run")

    def test_valid_file_without_identity_is_legacy(self):
        record = self.reader.read_ueformat_identity_bytes(
            ueformat_file(self.reader, identity=False)
        )
        self.assertIsNone(record.identity)
        self.assertIsNone(record.identity_extension)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.uemodel"
            path.write_bytes(ueformat_file(self.reader, identity=False))
            provenance = self.reader.read_ueformat_provenance(path)
            self.assertEqual(provenance.state, "legacy")

    def test_file_probe_uses_streaming_for_uncompressed_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "streamed.ueanim"
            path.write_bytes(ueformat_file(self.reader, kind="animation"))
            with patch.object(
                self.reader.Path,
                "read_bytes",
                side_effect=AssertionError("file probing must not load the whole file"),
            ):
                record = self.reader.read_ueformat_identity(path)
            self.assertEqual(record.identity["skeleton_path"], "/Game/Hero/SK_Hero_Skeleton")

    @unittest.skipUnless(os.name == "nt", "Windows extended-path contract")
    def test_file_probe_reads_windows_path_beyond_max_path(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            while len(str(parent / "long.ueanim")) <= 280:
                parent /= "animation_path_segment"
            parent.mkdir(parents=True)
            path = parent / "long.ueanim"
            path.write_bytes(ueformat_file(self.reader, kind="animation"))

            self.assertTrue(self.reader._filesystem_path(path).startswith("\\\\?\\"))
            record = self.reader.read_ueformat_identity(path)

            self.assertEqual(record.kind, "animation")
            self.assertTrue(record.has_strict_identity)

    def test_reads_gzip_envelope(self):
        record = self.reader.read_ueformat_identity_bytes(
            ueformat_file(self.reader, compressed=True)
        )
        self.assertTrue(record.compressed)
        self.assertTrue(record.has_strict_identity)

    def test_reads_v10_model_identity(self):
        record = self.reader.read_ueformat_identity_bytes(
            ueformat_file(self.reader, kind="model", file_version=10)
        )
        self.assertEqual(record.kind, "model")
        self.assertEqual(record.file_version, 10)
        self.assertTrue(record.has_strict_identity)
        self.assertEqual(record.identity["skeleton_path"], "/Game/Hero/SK_Hero_Skeleton")
        self.assertEqual(record.identity["skeleton_guid"], "00112233445566778899aabbccddeeff")
        self.assertEqual(record.identity["bone_layout_hash"], "0123456789abcdef" * 4)

    def test_reads_v10_animation_and_pose_target_identity(self):
        animation = self.reader.read_ueformat_identity_bytes(
            ueformat_file(self.reader, kind="animation", file_version=10)
        )
        pose = self.reader.read_ueformat_identity_bytes(
            ueformat_file(self.reader, kind="pose", file_version=10)
        )
        self.assertEqual(animation.file_version, 10)
        self.assertEqual(animation.identity["source_animation_path"], "/Game/Hero/Animations/A_Run")
        self.assertEqual(pose.identity["source_pose_path"], "/Game/Hero/Poses/P_Run")

    def test_reads_v10_gzip_envelope(self):
        record = self.reader.read_ueformat_identity_bytes(
            ueformat_file(self.reader, file_version=10, compressed=True)
        )
        self.assertTrue(record.compressed)
        self.assertEqual(record.file_version, 10)
        self.assertTrue(record.has_strict_identity)

    def test_v10_skips_non_target_fmodel_attributes(self):
        # FMODEL_MATERIALS / FMODEL_TEXTURES share the envelope layout but are
        # not identity sections; they must be skipped by total size.
        extension = "FMODEL_SKELETON_IDENTITY"
        attributes = [
            v10_attribute("LODS", struct.pack("<i", 0)),
            v10_attribute("FMODEL_MATERIALS", b"\x01\x02\x03", enveloped=True),
            v10_attribute("FMODEL_TEXTURES", b"\x01\x02\x03\x04\x05", enveloped=True),
            v10_attribute(extension, base_identity_payload(self.reader), enveloped=True),
        ]
        payload = struct.pack("<i", len(attributes)) + b"".join(attributes)
        envelope = bytearray(self.reader.MAGIC)
        envelope.extend(fstring(self.reader.MODEL_IDENTIFIER))
        envelope.append(10)
        envelope.extend(fstring("SyntheticAsset"))
        envelope.extend(fstring("/Game/Synthetic/SyntheticAsset.SyntheticAsset"))
        envelope.append(0)
        envelope.extend(payload)
        record = self.reader.read_ueformat_identity_bytes(bytes(envelope))
        self.assertTrue(record.has_strict_identity)
        self.assertEqual(record.identity["skeleton_path"], "/Game/Hero/SK_Hero_Skeleton")

    def test_v10_rejects_unknown_identity_envelope_version(self):
        body = bytes([2]) + struct.pack("<i", 1) + base_identity_payload(self.reader)
        attribute = fstring("FMODEL_SKELETON_IDENTITY") + struct.pack("<i", len(body)) + body
        payload = struct.pack("<i", 1) + attribute
        envelope = bytearray(self.reader.MAGIC)
        envelope.extend(fstring(self.reader.MODEL_IDENTIFIER))
        envelope.append(10)
        envelope.extend(fstring("SyntheticAsset"))
        envelope.extend(fstring("/Game/Synthetic/SyntheticAsset.SyntheticAsset"))
        envelope.append(0)
        envelope.extend(payload)
        with self.assertRaises(self.reader.UEFormatIdentityError):
            self.reader.read_ueformat_identity_bytes(bytes(envelope))

    def test_v10_rejects_missing_declared_attributes(self):
        attribute = v10_attribute(
            "FMODEL_SKELETON_IDENTITY", base_identity_payload(self.reader), enveloped=True
        )
        payload = struct.pack("<i", 2) + attribute  # declares two, writes one
        envelope = bytearray(self.reader.MAGIC)
        envelope.extend(fstring(self.reader.MODEL_IDENTIFIER))
        envelope.append(10)
        envelope.extend(fstring("SyntheticAsset"))
        envelope.extend(fstring("/Game/Synthetic/SyntheticAsset.SyntheticAsset"))
        envelope.append(0)
        envelope.extend(payload)
        with self.assertRaises(self.reader.UEFormatIdentityError):
            self.reader.read_ueformat_identity_bytes(bytes(envelope))

    def test_v10_rejects_trailing_attributes(self):
        attribute = v10_attribute(
            "FMODEL_SKELETON_IDENTITY", base_identity_payload(self.reader), enveloped=True
        )
        payload = struct.pack("<i", 1) + attribute + v10_attribute("LODS", struct.pack("<i", 0))
        envelope = bytearray(self.reader.MAGIC)
        envelope.extend(fstring(self.reader.MODEL_IDENTIFIER))
        envelope.append(10)
        envelope.extend(fstring("SyntheticAsset"))
        envelope.extend(fstring("/Game/Synthetic/SyntheticAsset.SyntheticAsset"))
        envelope.append(0)
        envelope.extend(payload)
        with self.assertRaises(self.reader.UEFormatIdentityError):
            self.reader.read_ueformat_identity_bytes(bytes(envelope))

    def test_rejects_duplicate_identity_and_malformed_present_identity(self):
        first = section(
            "FMODEL_SKELETON_IDENTITY",
            1,
            base_identity_payload(self.reader),
        )
        duplicate = first
        data = bytearray(self.reader.MAGIC)
        data.extend(fstring(self.reader.MODEL_IDENTIFIER))
        data.append(9)
        data.extend(fstring("SyntheticAsset"))
        data.append(0)
        data.extend(first)
        data.extend(duplicate)
        with self.assertRaises(self.reader.UEFormatIdentityError):
            self.reader.read_ueformat_identity_bytes(bytes(data))

        malformed = bytearray(base_identity_payload(self.reader))
        malformed[0] = 2
        with self.assertRaises(self.reader.UEFormatIdentityError):
            self.reader.read_ueformat_identity_bytes(
                ueformat_file(self.reader, identity_payload=bytes(malformed))
            )

    def test_rejects_bad_envelope_and_compressed_size(self):
        with self.assertRaises(self.reader.UEFormatIdentityError):
            self.reader.read_ueformat_identity_bytes(b"NOT-UEFORMAT")

        data = bytearray(ueformat_file(self.reader, compressed=True))
        # Locate the compressed-size field by reconstructing the fixed header.
        offset = len(self.reader.MAGIC)
        for value in (self.reader.MODEL_IDENTIFIER, "SyntheticAsset", "GZIP"):
            offset += 4 + len(value.encode("utf-8"))
            if value == self.reader.MODEL_IDENTIFIER:
                offset += 1
        # The compressed flag is before the compression type.
        offset += 1
        # Recompute directly to avoid depending on string search.
        offset = len(self.reader.MAGIC) + len(fstring(self.reader.MODEL_IDENTIFIER)) + 1
        offset += len(fstring("SyntheticAsset")) + 1 + len(fstring("GZIP")) + 4
        original = struct.unpack_from("<i", data, offset)[0]
        struct.pack_into("<i", data, offset, original + 1)
        with self.assertRaises(self.reader.UEFormatIdentityError):
            self.reader.read_ueformat_identity_bytes(bytes(data))


class UEFormatV10ProducerFixtureTests(unittest.TestCase):
    """Byte-level regression against canonical FModel V10 producer output.

    The fixtures under ``tests/fixtures/fmodel_v10`` are the exact artifacts
    emitted by the canonical CUE4Parse V10 harness (FModel V10 producer gate);
    they pin the V10 attribute layout to real bytes instead of synthetic ones.
    """

    FIXTURES = {
        "fmodel_v10_none.uemodel": "7cd0de1ceea96e89962ca8326c58a0bdb10887791dc5970e58926bbe838a7987",
        "fmodel_v10_gzip.uemodel": "7f4a16c18ad38daf004271d9e336e3602142e91acb5019a0f1d2acefc1ce4054",
        "fmodel_v10_zstd.uemodel": "1a659696abb8273015e5fc7713223edfa514c1b2fb27637401c722043ec74663",
    }

    @classmethod
    def setUpClass(cls):
        cls.reader = load_reader()

    def _fixture(self, name: str) -> Path:
        path = FIXTURE_DIR / name
        if not path.is_file():
            self.skipTest(f"canonical V10 producer fixture is unavailable: {name}")
        return path

    def test_fixture_bytes_match_recorded_digests(self):
        import hashlib
        for name, expected in self.FIXTURES.items():
            path = self._fixture(name)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)

    def test_reads_canonical_v10_identity_from_all_compression_modes(self):
        for name in self.FIXTURES:
            with self.subTest(fixture=name):
                record = self.reader.read_ueformat_identity(self._fixture(name))
                self.assertEqual(record.kind, "model")
                self.assertEqual(record.file_version, 10)
                self.assertEqual(record.object_name, "SM_FModelV10Contract")
                self.assertTrue(record.has_strict_identity)
                self.assertEqual(
                    record.identity["skeleton_path"],
                    "/CodeWorld/Content/Characters/Heroes/SKEL_FM_Base.SKEL_FM_Base",
                )
                self.assertFalse(record.identity["has_skeleton_guid"])
                self.assertEqual(
                    record.identity["bone_layout_hash"],
                    "d931be2b178d85f8477bb1671eaf1344133440ff6411140c1b09b579afe7d850",
                )

    def test_canonical_v10_identity_classifies_as_strict_provenance(self):
        provenance = self.reader.read_ueformat_provenance(
            self._fixture("fmodel_v10_none.uemodel")
        )
        self.assertEqual(provenance.state, "strict")
        self.assertTrue(provenance.is_strict)
        self.assertEqual(
            provenance.fingerprint,
            self.reader._provenance_module().skeleton_identity_fingerprint(
                provenance.identity
            ),
        )

class UEFormatV10FullFixtureTests(unittest.TestCase):
    """Byte-level regression for the full-fidelity V10 model fixture.

    ``fmodel_v10_full.uemodel`` is generated deterministically by
    ``scripts/make_v10_full_fixture.py`` following the vendor deserializer
    contract (real LODS geometry + SKELETON + FMODEL_SKELETON_IDENTITY).
    Unlike the identity-only canonical fixtures above, it drives the actual
    mesh/armature creation path in ``scripts/blender_smoke_v10.py``.
    """

    FIXTURE = "fmodel_v10_full.uemodel"
    SHA256 = "5763552c33564858d0ef94990e97bcf37dd5125140e8b1c99b680ad8bcb60507"
    SKELETON_PATH = "/Game/Characters/FModelV10Full_SKEL.FModelV10Full_SKEL"
    BONE_LAYOUT_HASH = "b89e441d860d848bdd32eb8897e45c15889bb12c56952f69234e5036a58b7cc5"

    @classmethod
    def setUpClass(cls):
        cls.reader = load_reader()

    def _fixture(self) -> Path:
        path = FIXTURE_DIR / self.FIXTURE
        if not path.is_file():
            self.skipTest(f"full V10 fixture is unavailable: {path}")
        return path

    def test_fixture_bytes_match_recorded_digest(self):
        import hashlib
        self.assertEqual(
            hashlib.sha256(self._fixture().read_bytes()).hexdigest(), self.SHA256)

    def test_reads_full_fixture_identity(self):
        record = self.reader.read_ueformat_identity(self._fixture())
        self.assertEqual(record.kind, "model")
        self.assertEqual(record.file_version, 10)
        self.assertEqual(record.object_name, "SK_FModelV10Full")
        self.assertTrue(record.has_strict_identity)
        self.assertFalse(record.identity["has_skeleton_guid"])
        self.assertEqual(record.identity["skeleton_path"], self.SKELETON_PATH)
        self.assertEqual(record.identity["bone_layout_hash"], self.BONE_LAYOUT_HASH)

    def test_full_fixture_classifies_as_strict_provenance(self):
        provenance = self.reader.read_ueformat_provenance(self._fixture())
        self.assertEqual(provenance.state, "strict")
        self.assertTrue(provenance.is_strict)


if __name__ == "__main__":
    unittest.main()
