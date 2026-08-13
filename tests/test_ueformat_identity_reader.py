"""Synthetic UEFormat envelope contracts for the restricted identity reader."""

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


def load_reader():
    spec = importlib.util.spec_from_file_location("ueformat_identity_reader", CORE / "ueformat_identity_reader.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fstring(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def section(name: str, array_size: int, payload: bytes) -> bytes:
    return fstring(name) + struct.pack("<ii", array_size, len(payload)) + payload


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


def ueformat_file(reader, *, kind="model", identity=True, compressed=False, identity_payload=None) -> bytes:
    identifier = {
        "model": reader.MODEL_IDENTIFIER,
        "animation": reader.ANIM_IDENTIFIER,
        "pose": reader.POSE_IDENTIFIER,
    }[kind]
    archive = bytearray()
    if kind == "animation":
        archive.extend(section("TRACKS", 0, b""))
    elif kind == "pose":
        archive.extend(section("POSES", 0, b""))
    elif kind == "model":
        archive.extend(section("LODS", 0, b""))
    if identity:
        extension = "FMODEL_SKELETON_IDENTITY" if kind == "model" else "FMODEL_TARGET_SKELETON"
        archive.extend(section(
            extension,
            1,
            identity_payload if identity_payload is not None else base_identity_payload(reader, kind=kind),
        ))
    payload = bytes(archive)
    envelope = bytearray(reader.MAGIC)
    envelope.extend(fstring(identifier))
    envelope.append(9)
    envelope.extend(fstring("SyntheticAsset"))
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


if __name__ == "__main__":
    unittest.main()
