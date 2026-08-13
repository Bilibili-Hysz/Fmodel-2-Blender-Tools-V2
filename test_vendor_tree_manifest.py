from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.vendor_tree_manifest import (
    VendorTreeManifestError,
    build_vendor_tree_manifest,
    load_vendor_tree_manifest,
    tree_digest,
    verify_vendor_tree,
    write_vendor_tree_manifest,
)


ROOT = Path(__file__).parent
VENDOR_ROOT = ROOT / "vendor"
TREE_MANIFEST = VENDOR_ROOT / "vendor_tree_manifest.json"


def test_bundled_vendor_tree_manifest_is_current_and_strict():
    result = verify_vendor_tree(TREE_MANIFEST, vendor_root=VENDOR_ROOT)
    manifest = load_vendor_tree_manifest(TREE_MANIFEST)

    assert result.file_count == manifest.file_count
    assert result.tree_sha256 == manifest.tree_sha256
    assert "vendor_manifest.toml" in result.checked_files
    assert "io_scene_ueformat/wheels/zstandard-0.25.0-cp313-cp313-win_amd64.whl" in result.checked_files


def test_vendor_tree_manifest_is_deterministic(tmp_path):
    vendor_root = tmp_path / "vendor"
    package_root = vendor_root / "io_scene_ueformat"
    package_root.mkdir(parents=True)
    (vendor_root / "vendor_manifest.toml").write_text(
        'vendor_id = "io_scene_ueformat"\n'
        'package_name = "io_scene_ueformat"\n'
        'version = "1.1.4"\n'
        'source_repository = "fixture"\n'
        'source_commit = "abc123"\n'
        'source_snapshot = "fixture_snapshot"\n'
        'registration_mode = "headless"\n'
        'capabilities = ["uemodel"]\n',
        encoding="utf-8",
    )
    (package_root / "__init__.py").write_text("# fixture\n", encoding="utf-8")
    (package_root / "__pycache__").mkdir()
    (package_root / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")

    first = build_vendor_tree_manifest(vendor_root)
    second = build_vendor_tree_manifest(vendor_root)

    assert first.to_dict() == second.to_dict()
    assert tree_digest(first.files) == first.tree_sha256


def test_vendor_tree_manifest_detects_tampered_file(tmp_path):
    vendor_root = tmp_path / "vendor"
    package_root = vendor_root / "io_scene_ueformat"
    package_root.mkdir(parents=True)
    (vendor_root / "vendor_manifest.toml").write_text(
        'vendor_id = "io_scene_ueformat"\n'
        'package_name = "io_scene_ueformat"\n'
        'version = "1.1.4"\n'
        'source_repository = "fixture"\n'
        'source_commit = "abc123"\n'
        'registration_mode = "headless"\n'
        'capabilities = ["uemodel"]\n',
        encoding="utf-8",
    )
    payload = package_root / "__init__.py"
    payload.write_text("original\n", encoding="utf-8")
    manifest_path = vendor_root / "vendor_tree_manifest.json"
    write_vendor_tree_manifest(build_vendor_tree_manifest(vendor_root), manifest_path)

    payload.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(VendorTreeManifestError, match="digest mismatch"):
        verify_vendor_tree(manifest_path, vendor_root=vendor_root)


def test_vendor_tree_manifest_rejects_unlisted_release_file(tmp_path):
    vendor_root = tmp_path / "vendor"
    package_root = vendor_root / "io_scene_ueformat"
    package_root.mkdir(parents=True)
    (vendor_root / "vendor_manifest.toml").write_text(
        'vendor_id = "io_scene_ueformat"\n'
        'package_name = "io_scene_ueformat"\n'
        'version = "1.1.4"\n'
        'source_repository = "fixture"\n'
        'source_commit = "abc123"\n'
        'registration_mode = "headless"\n'
        'capabilities = ["uemodel"]\n',
        encoding="utf-8",
    )
    (package_root / "__init__.py").write_text("fixture\n", encoding="utf-8")
    manifest_path = vendor_root / "vendor_tree_manifest.json"
    write_vendor_tree_manifest(build_vendor_tree_manifest(vendor_root), manifest_path)
    (vendor_root / "unexpected.txt").write_text("not listed\n", encoding="utf-8")

    with pytest.raises(VendorTreeManifestError, match="unlisted files"):
        verify_vendor_tree(manifest_path, vendor_root=vendor_root)

    assert verify_vendor_tree(
        manifest_path,
        vendor_root=vendor_root,
        strict=False,
    ).file_count == 2


def test_vendor_tree_manifest_rejects_unsafe_manifest_path(tmp_path):
    manifest_path = tmp_path / "vendor_tree_manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "vendor_id": "io_scene_ueformat",
        "package_name": "io_scene_ueformat",
        "version": "1.1.4",
        "source_repository": "fixture",
        "source_commit": "abc123",
        "registration_mode": "headless",
        "capabilities": ["uemodel"],
        "root": ".",
        "file_count": 1,
        "tree_sha256": "0" * 64,
        "files": [{"path": "../escape.txt", "size": 0, "sha256": "0" * 64}],
    }), encoding="utf-8")

    with pytest.raises(VendorTreeManifestError, match="Unsafe Vendor tree path"):
        load_vendor_tree_manifest(manifest_path)
