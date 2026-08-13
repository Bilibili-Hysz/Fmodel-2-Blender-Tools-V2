"""Fake-Blender contract for native UEFormat animation discovery without JSON."""

import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _fstring(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def _section(name: str, array_size: int, payload: bytes) -> bytes:
    return _fstring(name) + struct.pack("<ii", array_size, len(payload)) + payload


def _native_ueanim_bytes(reader, skeleton_path="/Game/Hero/SK_Hero_Skeleton") -> bytes:
    layout_hash = bytes.fromhex("0123456789abcdef" * 4)
    identity = bytearray([1])
    identity.extend(_fstring("/Game/Hero/Animations/Run"))
    identity.extend(_fstring(skeleton_path))
    identity.append(0)
    identity.extend(layout_hash)

    archive = bytearray(_section("TRACKS", 0, b""))
    archive.extend(_section("FMODEL_TARGET_SKELETON", 1, bytes(identity)))
    return (
        reader.MAGIC
        + _fstring(reader.ANIM_IDENTIFIER)
        + bytes([9])
        + _fstring("NativeRun")
        + bytes([0])
        + bytes(archive)
    )


def _legacy_ueanim_bytes(reader) -> bytes:
    archive = _section("TRACKS", 0, b"")
    return (
        reader.MAGIC
        + _fstring(reader.ANIM_IDENTIFIER)
        + bytes([9])
        + _fstring("LegacyRun")
        + bytes([0])
        + archive
    )


class _FakeContext:
    def __init__(self, config):
        self.config = dict(config)
        self.dictionary_manifest = {}
        self.logs = []

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def log_info(self, stage, message):
        self.logs.append(("info", stage, message))

    def log_warning(self, stage, message):
        self.logs.append(("warning", stage, message))

    def log_error(self, stage, message):
        self.logs.append(("error", stage, message))


class NativeUEFormatScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = (
            "bpy", "addon", "addon.core", "addon.Fmodel", "addon.Fmodel.Pipeline",
            "addon.core.action_selection", "addon.Assets", "addon.Assets.Utils",
            "addon.Assets.Utils.utils_asset",
        )
        cls.saved_modules = {name: sys.modules.get(name) for name in names}

        bpy = types.ModuleType("bpy")
        bpy.path = types.SimpleNamespace(abspath=lambda value: value)
        bpy.data = types.SimpleNamespace()
        sys.modules["bpy"] = bpy

        import core

        addon = types.ModuleType("addon")
        addon.__path__ = []
        fmodel = types.ModuleType("addon.Fmodel")
        fmodel.__path__ = []
        pipeline = types.ModuleType("addon.Fmodel.Pipeline")
        pipeline.__path__ = []
        core_module = types.ModuleType("addon.core")
        core_module.__dict__.update(core.__dict__)
        sys.modules.update({
            "addon": addon,
            "addon.core": core_module,
            "addon.Fmodel": fmodel,
            "addon.Fmodel.Pipeline": pipeline,
        })

        selection_path = ROOT / "core" / "action_selection.py"
        selection_spec = importlib.util.spec_from_file_location(
            "addon.core.action_selection", selection_path
        )
        selection = importlib.util.module_from_spec(selection_spec)
        sys.modules[selection_spec.name] = selection
        selection_spec.loader.exec_module(selection)

        # The raw scanner imports this compatibility helper lazily.  Make any
        # invocation visible: strict native identity must not ask it for JSON.
        assets = types.ModuleType("addon.Assets")
        assets.__path__ = []
        utils = types.ModuleType("addon.Assets.Utils")
        utils.__path__ = []
        utils_asset = types.ModuleType("addon.Assets.Utils.utils_asset")
        utils_asset.get_skeleton_name_from_json = lambda path: (_ for _ in ()).throw(
            AssertionError(f"native scan unexpectedly parsed JSON: {path}")
        )
        sys.modules.update({
            "addon.Assets": assets,
            "addon.Assets.Utils": utils,
            "addon.Assets.Utils.utils_asset": utils_asset,
        })

        stages_path = ROOT / "Fmodel" / "Pipeline" / "stages.py"
        stages_spec = importlib.util.spec_from_file_location(
            "addon.Fmodel.Pipeline.stages", stages_path
        )
        cls.stages = importlib.util.module_from_spec(stages_spec)
        sys.modules[stages_spec.name] = cls.stages
        stages_spec.loader.exec_module(cls.stages)

        import core.ueformat_identity_reader as reader
        cls.reader = reader
        cls.core = core

    @classmethod
    def tearDownClass(cls):
        for name, module in cls.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_native_identity_wins_when_a_legacy_manifest_is_present(self):
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory)
            asset_dir = source_root / "Hero"
            asset_dir.mkdir()
            asset_path = asset_dir / "Run.ueanim"
            asset_path.write_bytes(_native_ueanim_bytes(self.reader))
            (source_root / "legacy_manifest.json").write_text(
                '{"WrongSkeleton": {"Moves": {"__actions__": [["Run", ""]]}}}',
                encoding="utf-8",
            )

            context = _FakeContext({"source_skeleton": "Hero"})
            stage = self.stages.DictionaryAcquisitionStage()

            self.assertTrue(stage._process_fmodel_manifests(context, str(source_root)))

            manifest = self.core.DictionaryManifest.from_dict(context.dictionary_manifest)
            skeleton, _folder, _name, entry = manifest.get_all_animations()[0]
            self.assertEqual(skeleton, "SK_Hero_Skeleton")
            self.assertTrue(entry.metadata["native_ueformat"])
            self.assertEqual(entry.source_file, str(asset_path))

    def test_raw_scan_uses_native_identity_without_sidecar_or_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory)
            asset_dir = source_root / "Hero"
            asset_dir.mkdir()
            asset_path = asset_dir / "Run.ueanim"
            asset_path.write_bytes(_native_ueanim_bytes(self.reader))

            context = _FakeContext({
                "source_skeleton": "Hero",
                "skeleton_filter": "Enemy",  # explicit source_skeleton wins
            })
            stage = self.stages.DictionaryAcquisitionStage()

            self.assertTrue(stage._process_fmodel_manifests(context, str(source_root)))

            manifest = self.core.DictionaryManifest.from_dict(context.dictionary_manifest)
            entries = manifest.get_all_animations()
            self.assertEqual(len(entries), 1)
            skeleton, folder, name, entry = entries[0]
            self.assertEqual(skeleton, "SK_Hero_Skeleton")
            self.assertEqual(folder, "Hero")
            self.assertEqual(name, "Run")
            self.assertEqual(entry.source_file, str(asset_path))
            self.assertFalse((asset_path.with_suffix(".json")).exists())

            metadata = entry.metadata
            self.assertTrue(metadata["native_ueformat"])
            self.assertEqual(metadata["json_path"], "")
            self.assertEqual(metadata["skeleton_path"], "/Game/Hero/SK_Hero_Skeleton")
            self.assertEqual(metadata["skeleton_display_name"], "SK_Hero_Skeleton")
            self.assertEqual(metadata["format_identifier"], "UEANIM")
            self.assertEqual(metadata["format_object_name"], "NativeRun")
            self.assertEqual(
                metadata["fmodel_skeleton_identity_fingerprint"],
                self.core.skeleton_identity_fingerprint(metadata["fmodel_skeleton_identity"]),
            )

    def test_legacy_files_are_not_grouped_by_directory_name(self):
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory)
            asset_dir = source_root / "Content" / "Animations"
            asset_dir.mkdir(parents=True)
            (asset_dir / "Legacy.ueanim").write_bytes(_legacy_ueanim_bytes(self.reader))

            context = _FakeContext({"source_skeleton": "Content"})
            stage = self.stages.DictionaryAcquisitionStage()

            self.assertFalse(stage._process_fmodel_raw_files(context, str(source_root)))
            self.assertFalse(context.dictionary_manifest)
            message = context.logs[-1][2]
            self.assertIn("发现 1 个 UEANIM/UEPOSE 文件，但 0 个包含", message)
            self.assertIn("missing=1, invalid=0", message)

    def test_missing_identity_and_invalid_files_have_precise_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory)
            for index in range(3):
                (source_root / f"Legacy{index}.ueanim").write_bytes(
                    _legacy_ueanim_bytes(self.reader)
                )
            (source_root / "Broken.uepose").write_bytes(b"not-ueformat")

            context = _FakeContext({"source_skeleton": "SK_Hero_Skeleton"})
            stage = self.stages.DictionaryAcquisitionStage()

            self.assertFalse(stage._process_fmodel_raw_files(context, str(source_root)))
            message = context.logs[-1][2]
            self.assertIn("missing=3, invalid=1", message)

    def test_strict_identity_filter_mismatch_is_not_reported_as_no_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory)
            (source_root / "Run.ueanim").write_bytes(_native_ueanim_bytes(self.reader))

            context = _FakeContext({"source_skeleton": "SK_Enemy_Skeleton"})
            stage = self.stages.DictionaryAcquisitionStage()

            self.assertFalse(stage._process_fmodel_raw_files(context, str(source_root)))
            message = context.logs[-1][2]
            self.assertIn("1 个带内嵌身份", message)
            self.assertIn("mismatch=1, missing=0, invalid=0", message)

    def test_empty_tree_reports_no_animation_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            context = _FakeContext({})
            stage = self.stages.DictionaryAcquisitionStage()

            self.assertFalse(stage._process_fmodel_raw_files(context, directory))
            self.assertEqual(context.logs[-1][2], "未发现 UEANIM/UEPOSE 文件")


if __name__ == "__main__":
    unittest.main()
