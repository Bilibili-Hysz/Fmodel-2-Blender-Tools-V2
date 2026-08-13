"""Deterministic loader for the bundled UEFormat runtime.

This module intentionally has no Blender dependency. It owns Vendor discovery,
manifest validation, compatible wheel caching, package loading, and exact
module-object lifecycle. Blender-specific policy (preferences and UI messages)
belongs in ``auto_handlers.py``.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from .vendor_tree_manifest import (
    TREE_MANIFEST_FILENAME,
    VendorTreeManifestError,
    verify_vendor_tree,
)

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Blender 4.2+ ships tomllib
    tomllib = None


MANIFEST_FILENAME = "vendor_manifest.toml"
DEFAULT_VENDOR_ID = "io_scene_ueformat"
DEFAULT_MODULE_NAME = "io_scene_ueformat"
CACHE_ENVIRONMENT_VARIABLE = "FMODEL_VENDOR_CACHE"
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
SUPPORTED_MANIFEST_SCHEMA = 1
_ALLOWED_REGISTRATION_MODES = {"headless"}
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class VendorErrorCode:
    NOT_FOUND = "VENDOR_NOT_FOUND"
    MANIFEST_INVALID = "VENDOR_MANIFEST_INVALID"
    VERSION_UNSUPPORTED = "VENDOR_VERSION_UNSUPPORTED"
    CAPABILITY_MISSING = "VENDOR_CAPABILITY_MISSING"
    EXTERNAL_CONFLICT = "VENDOR_EXTERNAL_CONFLICT"
    WHEEL_NOT_FOUND = "VENDOR_WHEEL_NOT_FOUND"
    WHEEL_HASH_MISMATCH = "VENDOR_WHEEL_HASH_MISMATCH"
    WHEEL_PLATFORM_MISMATCH = "VENDOR_WHEEL_PLATFORM_MISMATCH"
    CACHE_LOCKED = "VENDOR_CACHE_LOCKED"
    CACHE_EXTRACT_FAILED = "VENDOR_CACHE_EXTRACT_FAILED"
    TREE_MANIFEST_MISSING = "VENDOR_TREE_MANIFEST_MISSING"
    TREE_MANIFEST_INVALID = "VENDOR_TREE_MANIFEST_INVALID"
    IMPORT_FAILED = "VENDOR_IMPORT_FAILED"
    REGISTER_FAILED = "VENDOR_REGISTER_FAILED"
    UNREGISTER_FAILED = "VENDOR_UNREGISTER_FAILED"


class VendorLoadError(RuntimeError):
    """Stable, user-actionable Vendor failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        vendor_id: str = DEFAULT_VENDOR_ID,
        remediation: str = "",
        source_path: str = "",
    ) -> None:
        self.code = code
        self.vendor_id = vendor_id
        self.remediation = remediation
        self.source_path = source_path
        detail = f"[{code}] {message}"
        if remediation:
            detail += f" Remediation: {remediation}"
        super().__init__(detail)


@dataclass(frozen=True)
class WheelRuntime:
    python_tag: str
    abi_tag: str
    platform_tag: str

    @classmethod
    def current(cls) -> "WheelRuntime":
        implementation = getattr(sys.implementation, "name", "cpython")
        prefix = {"cpython": "cp", "pypy": "pp", "pyston": "pt"}.get(implementation, "py")
        python_tag = f"{prefix}{sys.version_info.major}{sys.version_info.minor}"
        abi_tag = python_tag
        if os.name == "nt":
            platform_tag = "win_amd64" if struct.calcsize("P") == 8 else "win32"
        else:
            platform_tag = "any"
        return cls(python_tag, abi_tag, platform_tag)


@dataclass(frozen=True)
class WheelSpec:
    filename: str
    python_tag: str
    abi_tag: str
    platform_tag: str
    sha256: str


@dataclass(frozen=True)
class VendorSpec:
    vendor_id: str
    package_name: str
    version: tuple[int, ...]
    package_root: Path
    manifest_path: Path
    wheels_root: Path
    capabilities: tuple[str, ...]
    registration_mode: str
    wheels: tuple[WheelSpec, ...]

    @property
    def version_text(self) -> str:
        return ".".join(str(part) for part in self.version)


@dataclass
class VendorHandle:
    spec: VendorSpec
    module: ModuleType
    source: str
    module_name: str
    added_paths: tuple[Path, ...] = ()
    cache_root: Path | None = None
    owns_module: bool = True
    registered: bool = False
    registration_mode: str = "headless"
    tree_sha256: str = ""
    tree_file_count: int = 0

    @property
    def source_path(self) -> str:
        return str(getattr(self.module, "__file__", self.spec.package_root))


def _as_version(value: Any) -> tuple[int, ...]:
    if isinstance(value, str):
        parts = value.split(".")
    elif isinstance(value, (tuple, list)):
        parts = value
    else:
        raise ValueError("version must be a dotted string or integer sequence")
    if not parts:
        raise ValueError("version must not be empty")
    try:
        normalized = tuple(int(part) for part in parts)
    except (TypeError, ValueError) as exc:
        raise ValueError("version contains a non-integer component") from exc
    if any(part < 0 for part in normalized):
        raise ValueError("version contains a negative component")
    return normalized


def _manifest_data(path: Path) -> dict[str, Any]:
    if tomllib is None:  # pragma: no cover
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            "Python tomllib is unavailable; cannot read the bundled Vendor manifest.",
            source_path=str(path),
        )
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            f"Unable to parse Vendor manifest: {exc}",
            source_path=str(path),
        ) from exc
    if not isinstance(data, dict):
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            "Vendor manifest root must be a TOML table.",
            source_path=str(path),
        )
    return data


def load_vendor_spec(bundle_path: str | Path) -> VendorSpec:
    package_root = Path(bundle_path).expanduser().resolve()
    manifest_path = package_root.parent / MANIFEST_FILENAME
    if not manifest_path.is_file():
        # Allow a manifest beside the package for standalone diagnostics.
        manifest_path = package_root / MANIFEST_FILENAME
    if not package_root.is_dir():
        raise VendorLoadError(
            VendorErrorCode.NOT_FOUND,
            "Bundled Vendor package directory does not exist.",
            source_path=str(package_root),
        )
    if not manifest_path.is_file():
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            "Bundled Vendor manifest was not found.",
            source_path=str(package_root),
        )
    if not (package_root / "__init__.py").is_file():
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            "Bundled Vendor package is missing __init__.py.",
            source_path=str(package_root),
        )

    data = _manifest_data(manifest_path)
    try:
        schema_version = data.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != SUPPORTED_MANIFEST_SCHEMA
        ):
            raise ValueError(
                f"unsupported schema_version {schema_version!r}; expected {SUPPORTED_MANIFEST_SCHEMA}"
            )

        vendor_id = str(data["vendor_id"]).strip()
        package_name = str(data.get("package_name", vendor_id)).strip()
        version = _as_version(data["version"])
        raw_capabilities = data.get("capabilities", ())
        if not isinstance(raw_capabilities, (list, tuple)):
            raise ValueError("capabilities must be an array")
        capabilities = tuple(str(item).strip() for item in raw_capabilities)
        if any(not item for item in capabilities):
            raise ValueError("capabilities must not contain empty values")
        registration_mode = str(data.get("registration_mode", "headless")).strip().lower()
        if registration_mode not in _ALLOWED_REGISTRATION_MODES:
            raise ValueError(
                f"unsupported registration_mode {registration_mode!r}; expected 'headless'"
            )
        wheel_data = data.get("wheels", ())
        if not isinstance(wheel_data, list):
            raise ValueError("wheels must be an array of tables")

        wheels = []
        seen_files = set()
        seen_targets = set()
        for item in wheel_data:
            if not isinstance(item, dict):
                raise ValueError("each wheel entry must be a table")
            filename = str(item["file"]).strip()
            if (
                not filename
                or filename != Path(filename).name
                or "/" in filename
                or "\\" in filename
                or not filename.lower().endswith(".whl")
            ):
                raise ValueError(f"wheel file must be a safe .whl basename: {filename!r}")
            if filename in seen_files:
                raise ValueError(f"duplicate wheel file: {filename!r}")
            python_tag = str(item["python_tag"]).strip()
            abi_tag = str(item["abi_tag"]).strip()
            platform_tag = str(item["platform_tag"]).strip()
            target = (python_tag, abi_tag, platform_tag)
            if not all(target):
                raise ValueError(f"wheel tags must not be empty: {filename!r}")
            if target in seen_targets:
                raise ValueError(f"duplicate wheel tags: {target!r}")
            sha256 = str(item["sha256"]).strip().lower()
            if not _SHA256_RE.fullmatch(sha256):
                raise ValueError(f"wheel sha256 must be 64 hexadecimal characters: {filename!r}")
            seen_files.add(filename)
            seen_targets.add(target)
            wheels.append(
                WheelSpec(
                    filename=filename,
                    python_tag=python_tag,
                    abi_tag=abi_tag,
                    platform_tag=platform_tag,
                    sha256=sha256,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            f"Vendor manifest has invalid fields: {exc}",
            vendor_id=str(data.get("vendor_id", DEFAULT_VENDOR_ID)),
            source_path=str(manifest_path),
        ) from exc

    if vendor_id != DEFAULT_VENDOR_ID:
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            f"Unexpected Vendor id: {vendor_id!r}.",
            vendor_id=vendor_id,
            source_path=str(manifest_path),
        )
    if package_name != DEFAULT_MODULE_NAME:
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            f"Phase-A Vendor package name must remain {DEFAULT_MODULE_NAME!r}, got {package_name!r}.",
            vendor_id=vendor_id,
            source_path=str(manifest_path),
        )
    if not wheels:
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            "Vendor manifest contains no runtime wheels.",
            vendor_id=vendor_id,
            source_path=str(manifest_path),
        )
    return VendorSpec(
        vendor_id=vendor_id,
        package_name=package_name,
        version=version,
        package_root=package_root,
        manifest_path=manifest_path,
        wheels_root=package_root / "wheels",
        capabilities=capabilities,
        registration_mode=registration_mode,
        wheels=tuple(wheels),
    )


def wheel_is_compatible(spec: WheelSpec, runtime: WheelRuntime) -> bool:
    python_tags = set(spec.python_tag.split("."))
    abi_tags = set(spec.abi_tag.split("."))
    platform_tags = set(spec.platform_tag.split("."))
    python_version = runtime.python_tag[2:] if runtime.python_tag.startswith(("cp", "pp", "pt")) else ""
    python_ok = (
        "py3" in python_tags
        or (python_version and f"py{python_version}" in python_tags)
        or runtime.python_tag in python_tags
    )
    abi_ok = "none" in abi_tags or runtime.abi_tag in abi_tags or "abi3" in abi_tags
    platform_ok = "any" in platform_tags or runtime.platform_tag in platform_tags
    return python_ok and abi_ok and platform_ok


def select_wheel(spec: VendorSpec, runtime: WheelRuntime | None = None) -> WheelSpec:
    runtime = runtime or WheelRuntime.current()
    compatible = [wheel for wheel in spec.wheels if wheel_is_compatible(wheel, runtime)]
    if not compatible:
        raise VendorLoadError(
            VendorErrorCode.WHEEL_PLATFORM_MISMATCH,
            f"No compatible Vendor wheel for {runtime.python_tag}/{runtime.abi_tag}/{runtime.platform_tag}.",
            vendor_id=spec.vendor_id,
            source_path=str(spec.wheels_root),
            remediation="Use a supported Blender Python/platform or add a matching locked wheel.",
        )
    # Prefer an exact Python/ABI match over abi3/py3 fallbacks.
    compatible.sort(
        key=lambda wheel: (
            wheel.python_tag == runtime.python_tag,
            wheel.abi_tag == runtime.abi_tag,
            wheel.platform_tag == runtime.platform_tag,
        ),
        reverse=True,
    )
    return compatible[0]


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _default_cache_root() -> Path:
    override = os.environ.get(CACHE_ENVIRONMENT_VARIABLE, "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "FModel Tools" / "vendor-cache"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "fmodel-tools" / "vendor-cache"


def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in zf.infolist():
        member_path = Path(member.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise VendorLoadError(
                VendorErrorCode.CACHE_EXTRACT_FAILED,
                f"Unsafe wheel member path: {member.filename!r}",
            )
        target = (destination / member_path).resolve()
        try:
            target.relative_to(destination)
        except ValueError as exc:
            raise VendorLoadError(
                VendorErrorCode.CACHE_EXTRACT_FAILED,
                f"Wheel member escapes cache directory: {member.filename!r}",
            ) from exc
    zf.extractall(destination)


def _acquire_lock(lock_path: Path, *, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii", "replace"))
            return descriptor
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise VendorLoadError(
                    VendorErrorCode.CACHE_LOCKED,
                    f"Timed out waiting for Vendor cache lock: {lock_path}",
                    source_path=str(lock_path),
                    remediation="Close other Blender instances using this FModel cache and retry.",
                )
            time.sleep(0.05)


def _release_lock(lock_path: Path, descriptor: int) -> None:
    try:
        os.close(descriptor)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def ensure_wheel_cached(
    spec: VendorSpec,
    wheel: WheelSpec,
    *,
    cache_root: str | Path | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> Path:
    wheel_path = spec.wheels_root / wheel.filename
    if not wheel_path.is_file():
        raise VendorLoadError(
            VendorErrorCode.WHEEL_NOT_FOUND,
            f"Vendor wheel was not found: {wheel.filename}",
            vendor_id=spec.vendor_id,
            source_path=str(wheel_path),
        )
    actual_hash = sha256_file(wheel_path)
    if actual_hash.casefold() != wheel.sha256.casefold():
        raise VendorLoadError(
            VendorErrorCode.WHEEL_HASH_MISMATCH,
            f"SHA-256 mismatch for {wheel.filename}: expected {wheel.sha256}, got {actual_hash}.",
            vendor_id=spec.vendor_id,
            source_path=str(wheel_path),
            remediation="Refresh the locked Vendor wheel instead of bypassing the hash check.",
        )

    root = Path(cache_root).expanduser() if cache_root else _default_cache_root()
    final_dir = (
        root
        / spec.vendor_id
        / spec.version_text
        / f"{wheel.python_tag}-{wheel.platform_tag}"
        / wheel.sha256
    )
    marker = final_dir / ".extracted.ok"
    if marker.is_file():
        return final_dir

    lock_path = final_dir.parent / f".{wheel.sha256}.lock"
    descriptor = _acquire_lock(lock_path, timeout=lock_timeout)
    try:
        if marker.is_file():
            return final_dir
        if final_dir.exists():
            shutil.rmtree(final_dir, ignore_errors=True)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        # Keep the staging directory name deliberately short. On Windows the
        # temporary directory path is combined with every wheel member name;
        # embedding the full SHA-256 here can push an otherwise valid cache
        # extraction beyond the legacy MAX_PATH boundary. The wheel hash is
        # already part of ``final_dir`` and the lock protects this short name.
        partial_dir = Path(tempfile.mkdtemp(prefix=".p-", dir=final_dir.parent))
        try:
            with zipfile.ZipFile(wheel_path, "r") as archive:
                _safe_extract(archive, partial_dir)
            marker_tmp = partial_dir / ".extracted.ok.tmp"
            marker_tmp.write_text(
                f"vendor_id={spec.vendor_id}\nversion={spec.version_text}\nwheel={wheel.filename}\nsha256={wheel.sha256}\n",
                encoding="utf-8",
            )
            marker_tmp.replace(partial_dir / ".extracted.ok")
            try:
                partial_dir.replace(final_dir)
            except FileExistsError:
                if not marker.is_file():
                    raise
                shutil.rmtree(partial_dir, ignore_errors=True)
        except VendorLoadError:
            shutil.rmtree(partial_dir, ignore_errors=True)
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            shutil.rmtree(partial_dir, ignore_errors=True)
            raise VendorLoadError(
                VendorErrorCode.CACHE_EXTRACT_FAILED,
                f"Unable to extract Vendor wheel {wheel.filename}: {exc}",
                vendor_id=spec.vendor_id,
                source_path=str(wheel_path),
            ) from exc
        return final_dir
    finally:
        _release_lock(lock_path, descriptor)


def _module_is_within(module: ModuleType, root: Path) -> bool:
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return False
    try:
        Path(module_file).resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _remove_loaded_package_modules(
    module_name: str,
    package_root: Path,
    package_module: ModuleType | None = None,
) -> None:
    """Remove only submodules that came from this bundled package root."""
    prefix = f"{module_name}."
    for name, module in list(sys.modules.items()):
        if name != module_name and not name.startswith(prefix):
            continue
        if module is None:
            continue
        if module is package_module or _module_is_within(module, package_root):
            sys.modules.pop(name, None)


def load_bundled_vendor(
    bundle_path: str | Path,
    *,
    runtime: WheelRuntime | None = None,
    cache_root: str | Path | None = None,
    module_name: str = DEFAULT_MODULE_NAME,
    verify_tree: bool = False,
) -> VendorHandle:
    spec = load_vendor_spec(bundle_path)
    tree_verification = None
    if verify_tree:
        tree_manifest_path = spec.manifest_path.parent / TREE_MANIFEST_FILENAME
        if not tree_manifest_path.is_file():
            raise VendorLoadError(
                VendorErrorCode.TREE_MANIFEST_MISSING,
                "Bundled Vendor tree manifest was not found.",
                vendor_id=spec.vendor_id,
                source_path=str(tree_manifest_path),
                remediation="Regenerate vendor_tree_manifest.json before packaging the release.",
            )
        try:
            tree_verification = verify_vendor_tree(
                tree_manifest_path,
                vendor_root=spec.manifest_path.parent,
            )
        except VendorTreeManifestError as exc:
            raise VendorLoadError(
                VendorErrorCode.TREE_MANIFEST_INVALID,
                f"Bundled Vendor tree verification failed: {exc}",
                vendor_id=spec.vendor_id,
                source_path=str(tree_manifest_path),
                remediation="Refresh the locked Vendor snapshot and regenerate its tree manifest.",
            ) from exc
    if module_name != spec.package_name:
        raise VendorLoadError(
            VendorErrorCode.MANIFEST_INVALID,
            f"Manifest package name {spec.package_name!r} does not match requested module {module_name!r}.",
            vendor_id=spec.vendor_id,
            source_path=str(spec.manifest_path),
        )
    existing = sys.modules.get(module_name)
    if existing is not None:
        if _module_is_within(existing, spec.package_root):
            return VendorHandle(
                spec=spec,
                module=existing,
                source="bundled",
                module_name=module_name,
                owns_module=False,
                tree_sha256=getattr(tree_verification, "tree_sha256", ""),
                tree_file_count=getattr(tree_verification, "file_count", 0),
            )
        raise VendorLoadError(
            VendorErrorCode.EXTERNAL_CONFLICT,
            f"Module {module_name!r} is already loaded from another source.",
            vendor_id=spec.vendor_id,
            source_path=str(getattr(existing, "__file__", "")),
            remediation="Disable the external UEFormat addon or restart Blender before loading the bundled runtime.",
        )

    wheel = select_wheel(spec, runtime)
    wheel_cache = ensure_wheel_cached(spec, wheel, cache_root=cache_root)
    wheel_cache_text = str(wheel_cache)
    added_paths = ()
    if wheel_cache_text not in sys.path:
        sys.path.insert(0, wheel_cache_text)
        added_paths = (wheel_cache,)

    init_path = spec.package_root / "__init__.py"
    module_spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=[str(spec.package_root)],
    )
    if module_spec is None or module_spec.loader is None:
        for path in added_paths:
            path_text = str(path)
            if path_text in sys.path:
                sys.path.remove(path_text)
        raise VendorLoadError(
            VendorErrorCode.IMPORT_FAILED,
            "Unable to create an import spec for the bundled UEFormat package.",
            vendor_id=spec.vendor_id,
            source_path=str(init_path),
        )
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    try:
        module_spec.loader.exec_module(module)
    except Exception as exc:
        _remove_loaded_package_modules(module_name, spec.package_root, module)
        for path in added_paths:
            path_text = str(path)
            if path_text in sys.path:
                sys.path.remove(path_text)
        if isinstance(exc, VendorLoadError):
            raise
        raise VendorLoadError(
            VendorErrorCode.IMPORT_FAILED,
            f"Unable to import bundled UEFormat package: {exc}",
            vendor_id=spec.vendor_id,
            source_path=str(init_path),
            remediation="Check the selected Blender Python ABI and bundled wheel cache.",
        ) from exc
    return VendorHandle(
        spec=spec,
        module=module,
        source="bundled",
        module_name=module_name,
        added_paths=added_paths,
        cache_root=wheel_cache,
        owns_module=True,
        registration_mode=spec.registration_mode,
        tree_sha256=getattr(tree_verification, "tree_sha256", ""),
        tree_file_count=getattr(tree_verification, "file_count", 0),
    )


def make_external_vendor_handle(
    module: ModuleType,
    *,
    bundle_path: str | Path,
) -> VendorHandle:
    spec = load_vendor_spec(bundle_path)
    return VendorHandle(
        spec=spec,
        module=module,
        source="external",
        module_name=getattr(module, "__name__", DEFAULT_MODULE_NAME),
        registration_mode="external",
        owns_module=False,
    )


def register_vendor(handle: VendorHandle, *, headless: bool = True) -> None:
    if handle.registered:
        return
    function_name = "register_headless" if headless else "register"
    function = getattr(handle.module, function_name, None)
    if not callable(function):
        if headless and callable(getattr(handle.module, "register", None)):
            function_name = "register"
            function = handle.module.register
        else:
            raise VendorLoadError(
                VendorErrorCode.REGISTER_FAILED,
                f"Vendor does not expose callable {function_name}().",
                vendor_id=handle.spec.vendor_id,
                source_path=handle.source_path,
            )
    try:
        function()
    except Exception as exc:
        # A Blender register() function can fail after registering a subset of
        # classes. Best-effort rollback keeps a failed Vendor from poisoning
        # the next addon reload.
        cleanup = getattr(handle.module, "unregister", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        raise VendorLoadError(
            VendorErrorCode.REGISTER_FAILED,
            f"Vendor registration failed: {exc}",
            vendor_id=handle.spec.vendor_id,
            source_path=handle.source_path,
        ) from exc
    handle.registered = True
    handle.registration_mode = "headless" if function_name == "register_headless" else "normal"


def unregister_vendor(handle: VendorHandle) -> None:
    if handle.registered:
        function = getattr(handle.module, "unregister", None)
        if not callable(function):
            raise VendorLoadError(
                VendorErrorCode.UNREGISTER_FAILED,
                "Vendor does not expose callable unregister().",
                vendor_id=handle.spec.vendor_id,
                source_path=handle.source_path,
            )
        try:
            function()
        except Exception as exc:
            raise VendorLoadError(
                VendorErrorCode.UNREGISTER_FAILED,
                f"Vendor unregistration failed: {exc}",
                vendor_id=handle.spec.vendor_id,
                source_path=handle.source_path,
            ) from exc
        handle.registered = False

    if handle.source == "bundled" and handle.owns_module:
        _remove_loaded_package_modules(
            handle.module_name, handle.spec.package_root, handle.module
        )
        for path in handle.added_paths:
            path_text = str(path)
            while path_text in sys.path:
                sys.path.remove(path_text)


