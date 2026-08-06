# FModel_Tools/core/utils.py
# 工具函数 — 路径处理、hash 校验、资产库挂载

import os
import uuid
import bpy

# ``core.utils`` is normally imported as part of the Blender package. A number of
# contract tests and maintenance scripts load this file directly, however, so keep
# a sibling-file fallback instead of making the bridge import package-context-only.
try:
    from .ueformat_bridge import (
        UEFormatCapabilityError,
        build_model_option_kwargs,
        build_uescene_settings_kwargs,
        derive_fmodel_roots,
        parse_uescene_import_status,
        resolve_ueformat_bridge,
    )
except (ImportError, ValueError):
    import importlib.util as _importlib_util
    import sys as _sys
    from pathlib import Path as _Path

    _bridge_path = _Path(__file__).with_name("ueformat_bridge.py")
    _bridge_spec = _importlib_util.spec_from_file_location(
        "_fmodel_ueformat_bridge_standalone", _bridge_path
    )
    if _bridge_spec is None or _bridge_spec.loader is None:
        raise ImportError(f"Unable to load UEFormat bridge: {_bridge_path}")
    _bridge_module = _importlib_util.module_from_spec(_bridge_spec)
    # Dataclasses with postponed annotations resolve their class module through
    # sys.modules, which importlib does not populate for spec-only execution.
    _sys.modules[_bridge_spec.name] = _bridge_module
    _bridge_spec.loader.exec_module(_bridge_module)
    UEFormatCapabilityError = _bridge_module.UEFormatCapabilityError
    build_model_option_kwargs = _bridge_module.build_model_option_kwargs
    build_uescene_settings_kwargs = _bridge_module.build_uescene_settings_kwargs
    derive_fmodel_roots = _bridge_module.derive_fmodel_roots
    parse_uescene_import_status = _bridge_module.parse_uescene_import_status
    resolve_ueformat_bridge = _bridge_module.resolve_ueformat_bridge


# Process-local records for preferences entries this module created.
_DYNAMIC_LIBRARY_OWNERS = []


def _library_path_identity(path: str) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.realpath(os.path.abspath(bpy.path.abspath(path))))


def _library_matches_owner(library, owner) -> bool:
    owned_library, owned_path, owned_id = owner
    if library is owned_library:
        return True
    return (
        _library_path_identity(getattr(library, "path", "")) == owned_path
        and getattr(library, "library_id", "") == owned_id
    )


def normalize_path(path: str) -> str:
    """标准化路径（统一使用正斜杠）"""
    return path.replace('\\', '/').strip('/')


def safe_relpath(path: str, start: str) -> str:
    """安全的相对路径计算（处理跨盘符情况）"""
    try:
        return os.path.relpath(path, start).replace('\\', '/')
    except ValueError:
        return normalize_path(path)


def ensure_dir(directory: str) -> bool:
    """确保目录存在"""
    if not directory:
        return False
    try:
        os.makedirs(directory, exist_ok=True)
        return True
    except Exception:
        return False


def get_blend_dir() -> str:
    """获取当前blend文件所在目录"""
    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)
    return ""


def get_or_create_catalog_uuid(asset_dir: str, catalog_path: str, sk_name: str = "") -> str:
    """基于路径生成稳定的UUID并写入资产目录配置文件
    
    Args:
        asset_dir: 资产目录路径
        catalog_path: 目录路径
        sk_name: 骨架名称
    
    Returns:
        str: 目录UUID
    """
    clean_path = normalize_path(catalog_path or "Uncategorized")
    if sk_name:
        clean_path = f"{sk_name}/{clean_path}"
    
    catalog_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, clean_path))
    catalog_name = clean_path.split('/')[-1] if '/' in clean_path else clean_path
    cats_path = os.path.join(asset_dir, "blender_assets.cats.txt")
    
    existing_lines = []
    if os.path.exists(cats_path):
        with open(cats_path, 'r', encoding='utf-8') as f:
            existing_lines = f.readlines()
    
    if not any(catalog_uuid in line for line in existing_lines):
        with open(cats_path, 'a', encoding='utf-8') as f:
            if not existing_lines:
                f.write("VERSION 1\n\n")
            f.write(f"{catalog_uuid}:{clean_path}:{catalog_name}\n")
    
    return catalog_uuid


def mount_dynamic_library(lib_name: str, lib_path: str, library_id: str = "") -> bool:
    """动态挂载资产库到Blender
    
    Args:
        lib_name: 库名称
        lib_path: 库路径
    
    Returns:
        bool: 是否成功
    """
    prefs = bpy.context.preferences
    target_name = f"[FModel_Auto] {lib_name}"
    abs_target_path = os.path.normpath(os.path.abspath(bpy.path.abspath(lib_path)))
    target_identity = _library_path_identity(abs_target_path)
    
    # Display names are labels, not identities: same-name libraries may live at
    # different paths. A persistent ID may intentionally follow a moved library.
    for lib in prefs.filepaths.asset_libraries:
        lib_path_identity = _library_path_identity(getattr(lib, "path", ""))
        same_id = library_id and getattr(lib, "library_id", "") == library_id
        if lib_path_identity == target_identity or same_id:
            lib.name = target_name
            lib.path = abs_target_path
            return True
    
    # 添加新库
    try:
        previous_count = len(prefs.filepaths.asset_libraries)
        bpy.ops.preferences.asset_library_add(directory=abs_target_path)
        if len(prefs.filepaths.asset_libraries) <= previous_count:
            return False
        added = prefs.filepaths.asset_libraries[-1]
        added_identity = _library_path_identity(getattr(added, "path", ""))
        if added_identity != target_identity:
            return False
        added.name = target_name
        if library_id and hasattr(added, "library_id"):
            added.library_id = library_id
        _DYNAMIC_LIBRARY_OWNERS.append((added, target_identity, library_id))
        return True
    except Exception:
        return False


def cleanup_dynamic_libraries():
    """清理动态挂载的资产库"""
    prefs = bpy.context.preferences
    libs = prefs.filepaths.asset_libraries

    retained_owners = []
    for owner in _DYNAMIC_LIBRARY_OWNERS:
        for i in range(len(libs) - 1, -1, -1):
            if not _library_matches_owner(libs[i], owner):
                continue
            try:
                bpy.ops.preferences.asset_library_remove(index=i)
            except Exception:
                retained_owners.append(owner)
            break
        else:
            # The entry was already removed outside this cleanup call.
            continue
    _DYNAMIC_LIBRARY_OWNERS[:] = retained_owners


def deep_purge_orphans():
    """深度清理孤立数据"""
    for _ in range(3):
        for data_collections in (
            bpy.data.meshes, bpy.data.materials, bpy.data.textures,
            bpy.data.images, bpy.data.armatures, bpy.data.actions, bpy.data.node_groups
        ):
            for block in list(data_collections):
                if block.users == 0:
                    try:
                        data_collections.remove(block)
                    except Exception:
                        pass


def safe_invert_matrix(m):
    """安全的矩阵反转"""
    from mathutils import Matrix
    try:
        return m.inverted()
    except ValueError:
        return Matrix()


def get_ueformat_bridge(
    *,
    require_current: bool = False,
    allow_legacy: bool = True,
    capability: str = "full",
):
    """Resolve the UEFormat addon through the capability-aware bridge."""

    try:
        return resolve_ueformat_bridge(
            require_current=require_current,
            allow_legacy=allow_legacy,
            capability=capability,
        )
    except UEFormatCapabilityError:
        if require_current:
            raise
        return None


def get_ueformat_api(*, require_current: bool = False, allow_legacy: bool = True):
    """Backward-compatible tuple API backed by the capability-aware bridge."""

    bridge = get_ueformat_bridge(
        require_current=require_current,
        allow_legacy=allow_legacy,
    )
    return bridge.as_api_tuple() if bridge is not None else (None, None, None, None)

def register():
    """Core utils不需要注册Blender类"""
    pass

def unregister():
    """Core utils不需要注册Blender类"""
    pass
