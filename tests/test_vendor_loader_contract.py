from __future__ import annotations

import hashlib
import sys
import tempfile
import types
import zipfile
from pathlib import Path

import pytest

from core.vendor_loader import (
    VendorErrorCode,
    VendorLoadError,
    WheelRuntime,
    ensure_wheel_cached,
    load_bundled_vendor,
    load_vendor_spec,
    register_vendor,
    select_wheel,
    sha256_file,
    unregister_vendor,
    wheel_is_compatible,
)


ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "vendor" / "io_scene_ueformat"
MANIFEST = ROOT / "vendor" / "vendor_manifest.toml"


def test_bundled_manifest_is_locked_and_matches_wheels():
    spec = load_vendor_spec(BUNDLED)
    assert spec.vendor_id == "io_scene_ueformat"
    assert spec.package_name == "io_scene_ueformat"
    assert spec.version == (1, 2, 0)
    assert spec.registration_mode == "headless"
    assert {wheel.filename for wheel in spec.wheels} == {
        "zstandard-0.25.0-cp311-cp311-win_amd64.whl",
        "zstandard-0.25.0-cp312-cp312-win_amd64.whl",
        "zstandard-0.25.0-cp313-cp313-win_amd64.whl",
    }
    for wheel in spec.wheels:
        assert sha256_file(spec.wheels_root / wheel.filename) == wheel.sha256


def test_wheel_selection_is_abi_and_platform_aware():
    spec = load_vendor_spec(BUNDLED)
    runtime = WheelRuntime("cp313", "cp313", "win_amd64")
    wheel = select_wheel(spec, runtime)
    assert wheel.filename.endswith("cp313-cp313-win_amd64.whl")

    cp312 = next(wheel for wheel in spec.wheels if "cp312" in wheel.filename)
    assert not wheel_is_compatible(cp312, runtime)


def test_wheel_hash_mismatch_fails_closed(tmp_path):
    spec = load_vendor_spec(BUNDLED)
    original = spec.wheels[0]
    damaged = tmp_path / original.filename
    damaged.write_bytes(b"not-the-locked-wheel")
    altered_spec = type(spec)(
        vendor_id=spec.vendor_id,
        package_name=spec.package_name,
        version=spec.version,
        package_root=spec.package_root,
        manifest_path=spec.manifest_path,
        wheels_root=tmp_path,
        capabilities=spec.capabilities,
        registration_mode=spec.registration_mode,
        wheels=(original,),
    )

    with pytest.raises(VendorLoadError) as exc_info:
        ensure_wheel_cached(altered_spec, original, cache_root=tmp_path / "cache")
    assert exc_info.value.code == VendorErrorCode.WHEEL_HASH_MISMATCH


def _make_fixture_bundle(root: Path) -> tuple[Path, str]:
    package = root / "io_scene_ueformat"
    wheels = package / "wheels"
    wheels.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "from helper import VALUE\n"
        "bl_info = {'version': (1, 1, 4)}\n"
        "registered = False\n"
        "def register_headless():\n"
        "    global registered\n"
        "    registered = True\n"
        "def unregister():\n"
        "    global registered\n"
        "    registered = False\n",
        encoding="utf-8",
    )
    wheel_path = wheels / "helper-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        archive.writestr("helper.py", "VALUE = 42\n")
    digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    (root / "vendor_manifest.toml").write_text(
        f'''schema_version = 1\nvendor_id = "io_scene_ueformat"\npackage_name = "io_scene_ueformat"\nversion = "1.1.4"\nregistration_mode = "headless"\ncapabilities = ["uemodel"]\n\n[[wheels]]\nfile = "{wheel_path.name}"\npython_tag = "py3"\nabi_tag = "none"\nplatform_tag = "any"\nsha256 = "{digest}"\n''',
        encoding="utf-8",
    )
    return package, wheel_path.name


def test_bundled_package_load_register_unregister_and_cache_hit(tmp_path):
    package, _ = _make_fixture_bundle(tmp_path)
    runtime = WheelRuntime("cp313", "cp313", "win_amd64")
    cache_root = tmp_path / "cache"

    handle = load_bundled_vendor(package, runtime=runtime, cache_root=cache_root)
    assert handle.source == "bundled"
    assert handle.module.__name__ == "io_scene_ueformat"
    assert handle.module.VALUE == 42
    assert handle.module.registered is False
    register_vendor(handle)
    register_vendor(handle)
    assert handle.registered is True
    assert handle.module.registered is True
    cache_path = handle.added_paths[0]
    assert (cache_path / ".extracted.ok").is_file()

    unregister_vendor(handle)
    assert handle.registered is False
    assert "io_scene_ueformat" not in sys.modules
    assert str(cache_path) not in sys.path

    second = load_bundled_vendor(package, runtime=runtime, cache_root=cache_root)
    assert second.added_paths[0] == cache_path
    unregister_vendor(second)


def test_cache_extraction_uses_short_staging_path_for_long_windows_paths(tmp_path, monkeypatch):
    package, _ = _make_fixture_bundle(tmp_path)
    wheel = next((package / "wheels").glob("*.whl"))
    long_member = (
        "zstandard-0.25.0.dist-info/licenses/"
        + ("x" * 35)
        + "/LICENSE"
    )
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("helper.py", "VALUE = 42\n")
        archive.writestr(long_member, "license\n")

    manifest_path = tmp_path / "vendor_manifest.toml"
    manifest = manifest_path.read_text(encoding="utf-8")
    spec_before = load_vendor_spec(package)
    old_hash = spec_before.wheels[0].sha256
    new_hash = sha256_file(wheel)
    manifest_path.write_text(manifest.replace(old_hash, new_hash), encoding="utf-8")
    spec = load_vendor_spec(package)

    import core.vendor_loader as vendor_loader

    prefixes = []
    original_mkdtemp = vendor_loader.tempfile.mkdtemp

    def capture_prefix(*args, **kwargs):
        prefixes.append(kwargs.get("prefix", ""))
        return original_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(vendor_loader.tempfile, "mkdtemp", capture_prefix)
    cache_root = tmp_path / ("cache-" + ("x" * 60))
    cached = ensure_wheel_cached(spec, spec.wheels[0], cache_root=cache_root)
    assert (cached / ".extracted.ok").is_file()
    assert (cached / long_member).is_file()
    assert prefixes == [".p-"]


def test_bundled_load_can_require_tree_manifest(tmp_path):
    package, _ = _make_fixture_bundle(tmp_path)
    with pytest.raises(VendorLoadError) as exc_info:
        load_bundled_vendor(
            package,
            runtime=WheelRuntime("cp313", "cp313", "win_amd64"),
            cache_root=tmp_path / "cache",
            verify_tree=True,
        )
    assert exc_info.value.code == VendorErrorCode.TREE_MANIFEST_MISSING


def test_bundled_load_rejects_existing_external_module(tmp_path):
    package, _ = _make_fixture_bundle(tmp_path)
    external = types.ModuleType("io_scene_ueformat")
    external.__file__ = str(tmp_path / "external" / "__init__.py")
    previous = sys.modules.get("io_scene_ueformat")
    sys.modules["io_scene_ueformat"] = external
    try:
        with pytest.raises(VendorLoadError) as exc_info:
            load_bundled_vendor(
                package,
                runtime=WheelRuntime("cp313", "cp313", "win_amd64"),
                cache_root=tmp_path / "cache",
            )
        assert exc_info.value.code == VendorErrorCode.EXTERNAL_CONFLICT
    finally:
        if previous is None:
            sys.modules.pop("io_scene_ueformat", None)
        else:
            sys.modules["io_scene_ueformat"] = previous


def test_cache_rejects_zip_path_traversal(tmp_path):
    package, _ = _make_fixture_bundle(tmp_path)
    wheel = next((package / "wheels").glob("*.whl"))
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("../escape.py", "bad = True")
    spec = load_vendor_spec(package)
    locked = spec.wheels[0]
    # Refresh the test manifest's hash after intentionally changing the wheel.
    manifest = (tmp_path / "vendor_manifest.toml").read_text(encoding="utf-8")
    old_hash = locked.sha256
    new_hash = sha256_file(wheel)
    (tmp_path / "vendor_manifest.toml").write_text(manifest.replace(old_hash, new_hash), encoding="utf-8")
    spec = load_vendor_spec(package)
    with pytest.raises(VendorLoadError) as exc_info:
        ensure_wheel_cached(spec, spec.wheels[0], cache_root=tmp_path / "cache")
    assert exc_info.value.code == VendorErrorCode.CACHE_EXTRACT_FAILED


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version = 1", "schema_version = 99"),
        ("registration_mode = \"headless\"", "registration_mode = \"normal\""),
    ],
)
def test_manifest_rejects_unsupported_runtime_contract(tmp_path, field, replacement):
    package, _ = _make_fixture_bundle(tmp_path)
    manifest_path = tmp_path / "vendor_manifest.toml"
    manifest = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(manifest.replace(field, replacement), encoding="utf-8")

    with pytest.raises(VendorLoadError) as exc_info:
        load_vendor_spec(package)
    assert exc_info.value.code == VendorErrorCode.MANIFEST_INVALID


def test_manifest_rejects_unsafe_or_malformed_wheel_metadata(tmp_path):
    package, _ = _make_fixture_bundle(tmp_path)
    manifest_path = tmp_path / "vendor_manifest.toml"
    manifest = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        manifest.replace('file = "helper-1.0-py3-none-any.whl"', 'file = "../helper.whl"'),
        encoding="utf-8",
    )

    with pytest.raises(VendorLoadError) as exc_info:
        load_vendor_spec(package)
    assert exc_info.value.code == VendorErrorCode.MANIFEST_INVALID

    manifest_path.write_text(
        manifest.replace(
            'sha256 = "',
            'sha256 = "not-a-sha256-',
        ),
        encoding="utf-8",
    )
    with pytest.raises(VendorLoadError) as exc_info:
        load_vendor_spec(package)
    assert exc_info.value.code == VendorErrorCode.MANIFEST_INVALID


def test_unregister_removes_only_bundled_package_submodules(tmp_path):
    package, _ = _make_fixture_bundle(tmp_path)
    handle = load_bundled_vendor(
        package,
        runtime=WheelRuntime("cp313", "cp313", "win_amd64"),
        cache_root=tmp_path / "cache",
    )
    register_vendor(handle)

    bundled_submodule = types.ModuleType("io_scene_ueformat.importer.fake")
    bundled_submodule.__file__ = str(package / "importer" / "fake.py")
    external_submodule = types.ModuleType("io_scene_ueformat.external")
    external_submodule.__file__ = str(tmp_path / "external" / "io_scene_ueformat" / "external.py")
    sys.modules[bundled_submodule.__name__] = bundled_submodule
    sys.modules[external_submodule.__name__] = external_submodule
    try:
        unregister_vendor(handle)
        assert bundled_submodule.__name__ not in sys.modules
        assert sys.modules.get(external_submodule.__name__) is external_submodule
    finally:
        sys.modules.pop(external_submodule.__name__, None)
        sys.modules.pop(bundled_submodule.__name__, None)



def test_cached_path_ownership_is_not_removed_when_preexisting(tmp_path):
    package, _ = _make_fixture_bundle(tmp_path)
    runtime = WheelRuntime("cp313", "cp313", "win_amd64")
    cache_root = tmp_path / "cache"

    first = load_bundled_vendor(package, runtime=runtime, cache_root=cache_root)
    cache_path = first.added_paths[0]
    unregister_vendor(first)

    sys.path.insert(0, str(cache_path))
    try:
        second = load_bundled_vendor(package, runtime=runtime, cache_root=cache_root)
        assert second.added_paths == ()
        unregister_vendor(second)
        assert str(cache_path) in sys.path
    finally:
        while str(cache_path) in sys.path:
            sys.path.remove(str(cache_path))


def test_register_failure_rolls_back_partial_vendor_registration(tmp_path):
    package, _ = _make_fixture_bundle(tmp_path)
    init_path = package / "__init__.py"
    init_path.write_text(
        "bl_info = {'version': (1, 1, 4)}\n"
        "registered = False\n"
        "cleaned = False\n"
        "def register_headless():\n"
        "    global registered\n"
        "    registered = True\n"
        "    raise RuntimeError('partial registration')\n"
        "def unregister():\n"
        "    global registered, cleaned\n"
        "    registered = False\n"
        "    cleaned = True\n",
        encoding="utf-8",
    )
    handle = load_bundled_vendor(
        package,
        runtime=WheelRuntime("cp313", "cp313", "win_amd64"),
        cache_root=tmp_path / "cache",
    )

    with pytest.raises(VendorLoadError) as exc_info:
        register_vendor(handle)
    assert exc_info.value.code == VendorErrorCode.REGISTER_FAILED
    assert handle.registered is False
    assert handle.module.registered is False
    assert handle.module.cleaned is True
    unregister_vendor(handle)
