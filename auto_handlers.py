# FModel_Tools/auto_handlers.py
# 自动处理器 — 文件加载时自动挂载资产库 + vendor依赖注册

import builtins
import inspect

import bpy
import os
import sys
import time
import threading
from bpy.app.handlers import persistent

# ============================================================
# 防抖机制
# ============================================================

_last_scan_time = 0
_scan_cooldown = 2.0
_pending_scan = False
_scan_lock = threading.RLock()


def _schedule_lifecycle_timer(callback, first_interval):
    """Bind delayed work to the active add-on lifecycle."""
    generation = _lifecycle_generation

    def timer():
        _registered_timers.discard(timer)
        if (not _handlers_registered or generation != _lifecycle_state["generation"]):
            return None
        return callback()

    _registered_timers.add(timer)
    bpy.app.timers.register(timer, first_interval=first_interval)
    return timer


def _debounced_scan():
    global _last_scan_time, _pending_scan
    current_time = time.time()
    with _scan_lock:
        if current_time - _last_scan_time < _scan_cooldown:
            if not _pending_scan:
                _pending_scan = True
                remaining = _scan_cooldown - (current_time - _last_scan_time)
                _schedule_lifecycle_timer(_delayed_scan, remaining)
            return
        _execute_scan()


def _delayed_scan():
    global _pending_scan
    _pending_scan = False
    _execute_scan()
    return None


def _execute_scan():
    global _last_scan_time
    _last_scan_time = time.time()
    try:
        if hasattr(bpy.context.scene, "fmodel_anim_manager"):
            bpy.ops.fmodel.scan_anims()
            print("[FModel Auto] 资产树已自动刷新")
    except Exception as e:
        print(f"[FModel Auto] 自动扫描失败: {e}")


# ============================================================
# 文件加载处理器
# ============================================================

@persistent
def on_file_load_post(dummy):
    _schedule_lifecycle_timer(_init_environment, 0.8)


def _init_environment():
    try:
        from .core.utils import cleanup_dynamic_libraries, mount_dynamic_library
        print("[FModel Auto] 工程加载，开始初始化...")
        cleanup_dynamic_libraries()
        if hasattr(bpy.context.scene, "fmodel_assets"):
            for lib in bpy.context.scene.fmodel_assets.project_libs:
                if lib.path:
                    mount_dynamic_library(lib.name, lib.path, getattr(lib, "library_id", ""))
        if hasattr(bpy.context.scene, "fmodel_anim_manager"):
            bpy.ops.fmodel.scan_anims()
            print("[FModel Auto] 资产库已自动挂载并扫描")
    except Exception as e:
        print(f"[FModel Auto] 环境初始化失败: {e}")
    return None


# ============================================================
# 场景更新处理器
# ============================================================

_imported_objects_cache = set()
_first_run = True
_scene_update_lock = threading.RLock()


def _update_object_cache():
    global _imported_objects_cache
    _imported_objects_cache = {obj.name for obj in bpy.data.objects}


@persistent
def on_depsgraph_update_post(scene, depsgraph):
    global _first_run, _imported_objects_cache
    with _scene_update_lock:
        if _first_run:
            _first_run = False
            _update_object_cache()
            return

    current_objects = {obj.name for obj in bpy.data.objects}
    new_objects = current_objects - _imported_objects_cache

    if new_objects:
        has_relevant_import = False
        for obj_name in new_objects:
            obj = bpy.data.objects.get(obj_name)
            if obj and obj.type in ('MESH', 'ARMATURE'):
                if any(key.startswith('gltf_import_path') or key.startswith('FModel')
                       for key in obj.keys()):
                    has_relevant_import = True
                    break
        with _scene_update_lock:
            _imported_objects_cache = current_objects
        if has_relevant_import:
            print(f"[FModel Auto] 检测到新导入对象: {new_objects}")
            _debounced_scan()


# ============================================================
# 注册管理
# ============================================================

_handlers_registered = False
_LIFECYCLE_STATE_KEY = "_fmodel_tools_next_auto_handler_lifecycle"


def _get_lifecycle_state():
    """Return addon-owned process state that survives module reloads."""
    state = builtins.__dict__.get(_LIFECYCLE_STATE_KEY)
    if state is None:
        state = {
            "generation": 0,
            "registered_timers": set(),
            "vendor_registered": {},
            "added_paths": set(),
        }
        builtins.__dict__[_LIFECYCLE_STATE_KEY] = state
    return state


# The builtins mapping outlives this module when the add-on is reloaded.
_lifecycle_state = _get_lifecycle_state()
_lifecycle_generation = _lifecycle_state["generation"]
_registered_timers = _lifecycle_state["registered_timers"]
_vendor_registered = _lifecycle_state["vendor_registered"]
_vendor_observed = set()
_added_paths = _lifecycle_state["added_paths"]
_HANDLER_OWNER = "fmodel_auto_handler"


def _advance_lifecycle_generation():
    global _lifecycle_generation
    _lifecycle_generation = _lifecycle_state["generation"] + 1
    _lifecycle_state["generation"] = _lifecycle_generation


def _unregister_lifecycle_timers():
    for timer in list(_registered_timers):
        try:
            bpy.app.timers.unregister(timer)
        except (ValueError, RuntimeError):
            pass
    _registered_timers.clear()


def register_handlers():
    global _handlers_registered, _first_run, _pending_scan
    if _handlers_registered:
        return

    _unregister_lifecycle_timers()
    _advance_lifecycle_generation()
    for handlers, callback in (
        (bpy.app.handlers.load_post, on_file_load_post),
        (bpy.app.handlers.depsgraph_update_post, on_depsgraph_update_post),
    ):
        for stale_callback in list(handlers):
            if getattr(stale_callback, "_fmodel_owner", None) == _HANDLER_OWNER:
                handlers.remove(stale_callback)
        callback._fmodel_owner = _HANDLER_OWNER
        handlers.append(callback)

    with _scene_update_lock:
        _first_run = True
    with _scan_lock:
        _pending_scan = False
    _handlers_registered = True
    print("[FModel Auto] 自动触发器已注册")
    _schedule_lifecycle_timer(_ensure_dependency_addons, 5.0)


def _ensure_dependency_addons():
    prefs = bpy.context.preferences
    if prefs is None:
        print("[FModel] 无法获取用户偏好，跳过依赖检查")
        return None
    addons = prefs.addons

    bundled = {
        "io_scene_ueformat": os.path.join(os.path.dirname(__file__), "vendor", "io_scene_ueformat"),
        "io_scene_psk_psa": os.path.join(os.path.dirname(__file__), "vendor", "io_scene_psk_psa"),
    }

    for module_id, bundle_path in bundled.items():
        if module_id in addons:
            print(f"[FModel] {module_id} 已由用户安装，跳过")
            continue
        if module_id in _vendor_registered:
            print(f"[FModel] {module_id} 已通过内置副本注册，跳过")
            continue
        if not os.path.isdir(bundle_path):
            continue
        try:
            parent = os.path.dirname(bundle_path)
            if parent not in sys.path:
                sys.path.insert(0, parent)
                _added_paths.add(parent)

            _install_vendor_wheels(bundle_path)

            # 检测是否已有类残留 (Blender 启动文件缓存等)
            mod = __import__(module_id)
            if not _is_bundled_vendor_module(mod, bundle_path):
                print(f"[FModel] {module_id} 已由外部模块提供，跳过")
                continue
            if module_id == "io_scene_ueformat" and not _is_current_ueformat_addon(mod):
                print("[FModel] 内置 io_scene_ueformat 是 legacy 版本；当前 FModel 材质/UEScene 工作流需要外部 canonical addon 1.1.4+")
            if hasattr(mod, "register"):
                pre_check = _vendor_already_in_registry(mod)
                if pre_check:
                    print(f"[FModel] {module_id} 类已存在 (启动缓存), 跳过重复注册")
                    _vendor_observed.add(module_id)
                    continue
                mod.register()
                # Preserve object identity: a later same-name import may be external.
                _vendor_registered[module_id] = (mod, bundle_path)
            print(f"[FModel] {module_id} 内置副本已注册")
        except Exception as e:
            print(f"[FModel] {module_id} 内置注册失败: {e}")

    return None


def _addon_version(module):
    """Return a normalized addon version tuple for diagnostics."""
    info = getattr(module, "bl_info", {}) or {}
    value = info.get("version")
    if not isinstance(value, (tuple, list)):
        return None
    try:
        return tuple(int(part) for part in value)
    except (TypeError, ValueError):
        return None


def _ueformat_submodule(module, suffix):
    """Resolve an already-loaded or importable UEFormat submodule safely."""
    direct = getattr(module, suffix.rsplit(".", 1)[-1], None)
    if direct is not None:
        return direct
    module_name = getattr(module, "__name__", "")
    if not module_name:
        return None
    qualified_name = f"{module_name}.{suffix}"
    existing = sys.modules.get(qualified_name)
    if existing is not None:
        return existing
    try:
        return __import__(qualified_name, fromlist=["*"])
    except Exception:
        return None


def _constructor_fields(cls):
    if cls is None:
        return set()
    try:
        return set(inspect.signature(cls).parameters)
    except (TypeError, ValueError):
        return set()


def _is_current_ueformat_addon(module) -> bool:
    """Require the current FModel-aware UEFormat surface for new workflows."""
    version = _addon_version(module)
    if version is None or version < (1, 1, 4):
        return False
    if not (
        getattr(module, "__file__", None)
        and getattr(module, "bl_info", {}).get("name")
    ):
        return False

    # The version number alone is not enough: a same-name legacy addon can be
    # repackaged with a newer version. Check the concrete model/material/scene
    # surfaces that the V2 bridge relies on before calling it current.
    op_module = _ueformat_submodule(module, "op")
    if getattr(op_module, "UFImportUEScene", None) is None:
        return False

    options_module = _ueformat_submodule(module, "options")
    model_options = getattr(options_module, "UEModelOptions", None)
    anim_options = getattr(options_module, "UEAnimOptions", None)
    pose_options = getattr(options_module, "UEPoseOptions", None)
    model_fields = _constructor_fields(model_options)
    skeleton_fields = _constructor_fields(anim_options) | _constructor_fields(pose_options)
    return (
        {
            "import_fmodel_materials",
            "build_fmodel_material_nodes",
            "load_fmodel_embedded_textures",
            "load_fmodel_external_textures",
        }.issubset(model_fields)
        and "strict_fmodel_skeleton" in skeleton_fields
    )

_KNOWN_VENDOR_CLASSES = {
    "io_scene_psk_psa": ["PSK_OT_import", "PSA_OT_export"],
    "io_scene_ueformat": ["UFImportUEModel", "UFImportUEAnim", "UFImportUEPose", "UFImportUEScene"],
}


def _is_bundled_vendor_module(mod, bundle_path) -> bool:
    module_file = getattr(mod, "__file__", None)
    if not module_file:
        return False
    try:
        resolved_module_file = os.path.normcase(os.path.realpath(module_file))
        resolved_bundle_path = os.path.normcase(os.path.realpath(bundle_path))
        return os.path.commonpath((resolved_module_file, resolved_bundle_path)) == resolved_bundle_path
    except (OSError, ValueError):
        return False


def _vendor_already_in_registry(mod) -> bool:
    """检查 vendor addon 的已知类是否已在 Blender 类型表中 (防止重复注册噪音)"""
    for module_id, class_names in _KNOWN_VENDOR_CLASSES.items():
        if mod.__name__ == module_id:
            return any(hasattr(bpy.types, cn) for cn in class_names)
    return False


def _install_vendor_wheels(bundle_path):
    import sys, zipfile, tempfile
    wheels_dir = os.path.join(bundle_path, "wheels")
    if not os.path.isdir(wheels_dir):
        return
    cache_dir = os.path.join(tempfile.gettempdir(), "fmodel_wheel_cache")
    for entry in sorted(os.listdir(wheels_dir)):
        if not entry.endswith(".whl"):
            continue
        wheel_path = os.path.join(wheels_dir, entry)
        wheel_name = entry.rsplit(".", 1)[0]
        extract_dir = os.path.join(cache_dir, wheel_name)
        if not os.path.isdir(extract_dir):
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(wheel_path, 'r') as zf:
                zf.extractall(extract_dir)
        if extract_dir not in sys.path:
            sys.path.insert(0, extract_dir)
            _added_paths.add(extract_dir)


def unregister_handlers():
    global _handlers_registered, _pending_scan
    _handlers_registered = False
    _advance_lifecycle_generation()
    with _scan_lock:
        _pending_scan = False
    _unregister_lifecycle_timers()
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.depsgraph_update_post):
        for callback in list(handlers):
            if getattr(callback, "_fmodel_owner", None) == _HANDLER_OWNER:
                handlers.remove(callback)

    _unregister_vendor_addons()

    for path in sorted(_added_paths, reverse=True):
        if path in sys.path:
            sys.path.remove(path)
    _added_paths.clear()
    _vendor_registered.clear()
    _vendor_observed.clear()
    print("[FModel Auto] 自动触发器已注销")


def _unregister_vendor_addons():
    for module_id, (mod, bundle_path) in sorted(_vendor_registered.items()):
        try:
            if not _is_bundled_vendor_module(mod, bundle_path):
                print(f"[FModel] {module_id} 内置副本来源无效，跳过注销")
                continue
            if hasattr(mod, "unregister"):
                mod.unregister()
            print(f"[FModel] {module_id} 内置副本已注销")
        except Exception as e:
            print(f"[FModel] {module_id} 内置副本注销失败: {e}")
