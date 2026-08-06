"""Capability-aware bridge to the canonical io_scene_ueformat addon.

The V2 tools own orchestration, project indexing and asset-library behavior. The
UEFormat addon remains the protocol reader/importer. This module deliberately
contains no Blender imports so its discovery and path contracts can be tested
outside Blender.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable, Mapping


CURRENT_ADDON_MIN_VERSION = (1, 1, 4)


class UEFormatCapabilityError(RuntimeError):
    """Raised when the loaded addon cannot satisfy a requested V2 contract."""


@dataclass(frozen=True)
class UEFormatCapabilities:
    module_name: str
    source_path: str
    version: tuple[int, ...] | None
    has_model_api: bool
    has_animation_api: bool
    has_pose_api: bool
    supports_fmodel_materials: bool
    supports_fmodel_textures: bool
    supports_uescene: bool
    supports_strict_skeleton: bool

    @property
    def version_text(self) -> str:
        return ".".join(str(part) for part in self.version) if self.version else "unknown"

    @property
    def is_current(self) -> bool:
        return (
            self.version is not None
            and self.version >= CURRENT_ADDON_MIN_VERSION
            and self.supports_fmodel_materials
            and self.supports_fmodel_textures
            and self.supports_uescene
            and self.supports_strict_skeleton
        )

    def missing_current_features(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.version is None or self.version < CURRENT_ADDON_MIN_VERSION:
            missing.append(f"addon_version>={'.'.join(map(str, CURRENT_ADDON_MIN_VERSION))}")
        if not self.has_model_api:
            missing.append("UEModelOptions/UEFormatImport")
        if not self.has_animation_api:
            missing.append("UEAnimOptions")
        if not self.has_pose_api:
            missing.append("UEPoseOptions")
        if not self.supports_fmodel_materials:
            missing.append("FMODEL_MATERIALS_v2")
        if not self.supports_fmodel_textures:
            missing.append("FMODEL_TEXTURES_v1")
        if not self.supports_uescene:
            missing.append("UEScene")
        if not self.supports_strict_skeleton:
            missing.append("strict_fmodel_skeleton")
        return tuple(missing)


@dataclass(frozen=True)
class UEFormatBridge:
    """Resolved importer classes plus the capability contract they satisfy."""

    module: ModuleType
    logic_module: ModuleType
    options_module: ModuleType
    capabilities: UEFormatCapabilities
    UEFormatImport: type
    UEModelOptions: type
    UEAnimOptions: type
    UEPoseOptions: type

    @property
    def module_name(self) -> str:
        return self.module.__name__

    @property
    def is_current(self) -> bool:
        return self.capabilities.is_current

    def as_api_tuple(self):
        return (
            self.UEFormatImport,
            self.UEModelOptions,
            self.UEAnimOptions,
            self.UEPoseOptions,
        )


def _normalize_version(value) -> tuple[int, ...] | None:
    if not isinstance(value, (tuple, list)) or not value:
        return None
    try:
        return tuple(int(part) for part in value)
    except (TypeError, ValueError):
        return None


def _constructor_parameters(cls) -> set[str]:
    try:
        return set(inspect.signature(cls).parameters)
    except (TypeError, ValueError):
        return set()


def _import_optional(importer: Callable[[str], ModuleType], name: str):
    try:
        return importer(name)
    except (ImportError, AttributeError, ModuleNotFoundError, KeyError):
        return None


def inspect_ueformat_module(
    module: ModuleType,
    *,
    importer: Callable[[str], ModuleType] | None = None,
) -> UEFormatBridge | None:
    """Inspect a loaded addon package without importing Blender itself.

    ``importer`` is injectable so this contract can be tested with pure Python
    fake modules. In Blender it defaults to :func:`importlib.import_module`.
    """

    importer = importer or importlib.import_module
    module_name = module.__name__
    logic_module = _import_optional(importer, f"{module_name}.importer.logic")
    options_module = _import_optional(importer, f"{module_name}.options")
    if logic_module is None or options_module is None:
        return None

    import_class = getattr(logic_module, "UEFormatImport", None)
    model_options = getattr(options_module, "UEModelOptions", None)
    anim_options = getattr(options_module, "UEAnimOptions", None)
    pose_options = getattr(options_module, "UEPoseOptions", None)
    if not all((import_class, model_options, anim_options, pose_options)):
        return None

    import_helpers = _import_optional(importer, f"{module_name}.op.import_helpers")
    scene_options = _import_optional(importer, f"{module_name}.op.settings")
    model_params = _constructor_parameters(model_options)
    anim_params = _constructor_parameters(anim_options)
    pose_params = _constructor_parameters(pose_options)

    capabilities = UEFormatCapabilities(
        module_name=module_name,
        source_path=str(getattr(module, "__file__", "")),
        version=_normalize_version(getattr(module, "bl_info", {}).get("version")),
        has_model_api=True,
        has_animation_api=True,
        has_pose_api=True,
        supports_fmodel_materials={
            "import_fmodel_materials",
            "build_fmodel_material_nodes",
        }.issubset(model_params),
        supports_fmodel_textures={
            "load_fmodel_embedded_textures",
            "load_fmodel_external_textures",
        }.issubset(model_params),
        supports_uescene=bool(
            getattr(import_helpers, "UFImportUEScene", None)
            or getattr(scene_options, "UESceneOptions", None)
        ),
        supports_strict_skeleton={
            "strict_fmodel_skeleton",
        }.issubset(anim_params | pose_params),
    )
    return UEFormatBridge(
        module=module,
        logic_module=logic_module,
        options_module=options_module,
        capabilities=capabilities,
        UEFormatImport=import_class,
        UEModelOptions=model_options,
        UEAnimOptions=anim_options,
        UEPoseOptions=pose_options,
    )


def _candidate_modules(
    *,
    importer: Callable[[str], ModuleType] | None = None,
) -> Iterable[ModuleType]:
    importer = importer or importlib.import_module
    seen: set[int] = set()
    try:
        module = importer("io_scene_ueformat")
    except (ImportError, ModuleNotFoundError, KeyError):
        module = sys.modules.get("io_scene_ueformat")
    if module is not None:
        seen.add(id(module))
        yield module

    # Reload-safe fallback: V2 may have observed a package under an addon
    # namespace before the top-level module alias was installed.
    for name in sorted(sys.modules):
        if not name.endswith(".importer.logic"):
            continue
        base_name = name[: -len(".importer.logic")]
        candidate = sys.modules.get(base_name)
        if candidate is not None and id(candidate) not in seen:
            seen.add(id(candidate))
            yield candidate


def resolve_ueformat_bridge(
    *,
    require_current: bool = False,
    allow_legacy: bool = True,
    importer: Callable[[str], ModuleType] | None = None,
) -> UEFormatBridge | None:
    """Resolve the best currently loaded UEFormat addon.

    ``require_current=True`` is used by the new FModel material/UEScene path.
    Legacy callers can keep ``allow_legacy=True`` while migration is staged.
    """

    bridges: list[UEFormatBridge] = []
    for module in _candidate_modules(importer=importer):
        bridge = inspect_ueformat_module(module, importer=importer)
        if bridge is not None:
            bridges.append(bridge)

    if not bridges:
        return None

    current = [bridge for bridge in bridges if bridge.is_current]
    if current:
        return current[0]

    if require_current or not allow_legacy:
        details = "; ".join(
            f"{bridge.module_name} {bridge.capabilities.version_text}: "
            f"missing {', '.join(bridge.capabilities.missing_current_features())}"
            for bridge in bridges
        )
        raise UEFormatCapabilityError(
            "A current io_scene_ueformat addon is required for the FModel pipeline. "
            + (details or "No compatible addon was found.")
        )
    return bridges[0]


def derive_fmodel_roots(model_path: str | Path) -> tuple[Path | None, Path | None]:
    """Derive safe FModel export/bundle roots from a Game export-tree path.

    For ``<root>/<Project>/Content/.../*.uemodel`` both roots become
    ``<root>/<Project>``. If no ``Content`` ancestor exists, return ``None``
    rather than guessing a filesystem root; the importer can then use embedded
    resources or its own file-relative fallback.
    """

    path = Path(model_path).expanduser()
    if path.suffix.casefold() not in {".uemodel", ".uescene"}:
        return None, None
    for ancestor in (path.parent, *path.parents):
        if ancestor.name.casefold() == "content":
            root = ancestor.parent
            return root, root
    return None, None


def build_model_option_kwargs(
    *,
    scale_factor: float,
    bone_length: float,
    reorient_bones: bool,
    target_lod: int,
    import_collision: bool,
    import_morph_targets: bool,
    import_sockets: bool,
    import_virtual_bones: bool,
    model_path: str | Path,
) -> dict[str, object]:
    """Return V2 UI options plus current FModel material/root options."""

    export_root, bundle_root = derive_fmodel_roots(model_path)
    return {
        "scale_factor": scale_factor,
        "bone_length": bone_length,
        "reorient_bones": reorient_bones,
        "target_lod": target_lod,
        "import_collision": import_collision,
        "import_morph_targets": import_morph_targets,
        "import_sockets": import_sockets,
        "import_virtual_bones": import_virtual_bones,
        "import_fmodel_materials": True,
        "build_fmodel_material_nodes": True,
        "load_fmodel_embedded_textures": True,
        "load_fmodel_external_textures": True,
        "keep_fmodel_unmapped_parameters": True,
        "fmodel_ue_export_root": str(export_root) if export_root else "",
        "fmodel_bundle_root": str(bundle_root) if bundle_root else "",
    }


