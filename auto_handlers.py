# FModel_Tools/auto_handlers.py
# 自动处理器 — 文件加载时自动挂载资产库 + vendor依赖注册

import builtins
import inspect
import os
import sys
import threading
import time

import bpy
from bpy.app.handlers import persistent

# ============================================================
# 防抖机制
# ============================================================

_last_scan_time = 0
_scan_cooldown = 2.0
_scan_deferred_interval = 0.5
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


def _pipeline_is_active():
    """Read the shared pipeline activity flag without making Blender mandatory."""
    try:
        if __package__:
            from .core.pipeline_activity import is_pipeline_active
        else:
            from core.pipeline_activity import is_pipeline_active
        return bool(is_pipeline_active())
    except (ImportError, AttributeError, RuntimeError):
        # A partially unloaded add-on should not prevent the handler from
        # refreshing assets.  The normal registered add-on path never reaches
        # this fallback.
        return False


def _schedule_deferred_scan():
    global _pending_scan
    if not _pending_scan:
        _pending_scan = True
        _schedule_lifecycle_timer(_delayed_scan, _scan_deferred_interval)


def _debounced_scan():
    global _last_scan_time, _pending_scan
    current_time = time.time()
    with _scan_lock:
        if _pipeline_is_active():
            # Keep one timer alive while the pipeline is mutating the scene.
            # The timer will execute as soon as the activity scope closes.
            _schedule_deferred_scan()
            return
        if current_time - _last_scan_time < _scan_cooldown:
            if not _pending_scan:
                _pending_scan = True
                remaining = _scan_cooldown - (current_time - _last_scan_time)
                _schedule_lifecycle_timer(_delayed_scan, remaining)
            return
        _execute_scan()


def _delayed_scan():
    global _pending_scan
    with _scan_lock:
        if _pipeline_is_active():
            _schedule_lifecycle_timer(_delayed_scan, _scan_deferred_interval)
            return None
        _pending_scan = False
    _execute_scan()
    return None


def _execute_scan():
    global _last_scan_time
    if _pipeline_is_active():
        with _scan_lock:
            _schedule_deferred_scan()
        return
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
    # Folder-only pipeline: file loading only reinitializes the normal runtime.
    _schedule_lifecycle_timer(_init_environment, 0.8)


def _init_environment():
    try:
        if __package__:
            from .core.utils import cleanup_dynamic_libraries, mount_dynamic_library
        else:
            from core.utils import cleanup_dynamic_libraries, mount_dynamic_library
        print("[FModel Auto] 工程加载，开始初始化...")
        scene = bpy.context.scene
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


def _object_token(obj):
    """Return a stable-enough identity without retaining Blender objects."""
    as_pointer = getattr(obj, "as_pointer", None)
    if callable(as_pointer):
        try:
            pointer = as_pointer()
        except (AttributeError, RuntimeError, ReferenceError):
            pointer = 0
        if pointer:
            return ("pointer", int(pointer))
    return ("identity", id(obj))


def _update_object_cache():
    global _imported_objects_cache
    _imported_objects_cache = {_object_token(obj) for obj in bpy.data.objects}


def _depsgraph_objects(depsgraph):
    """Return updated Blender objects, or ``None`` for legacy host fallbacks."""
    updates = getattr(depsgraph, "updates", None)
    if updates is None:
        return None
    try:
        iterator = iter(updates)
    except TypeError:
        return None

    objects = []
    for update in iterator:
        obj = getattr(update, "id", None)
        if obj is not None and getattr(obj, "type", None) in {"MESH", "ARMATURE"}:
            objects.append(obj)
    return objects


def _fallback_new_objects():
    """Compatibility path for Blender-like hosts without depsgraph updates."""
    current_objects = list(bpy.data.objects)
    current_tokens = {_object_token(obj) for obj in current_objects}
    new_objects = [
        obj for obj in current_objects
        if _object_token(obj) not in _imported_objects_cache
    ]
    _imported_objects_cache.clear()
    _imported_objects_cache.update(current_tokens)
    return new_objects


def _relevant_import_object(obj):
    if getattr(obj, "type", None) not in {"MESH", "ARMATURE"}:
        return False
    try:
        keys = obj.keys()
    except (AttributeError, RuntimeError, TypeError):
        return False
    return any(
        str(key).startswith("gltf_import_path") or str(key).startswith("FModel")
        for key in keys
    )


@persistent
def on_depsgraph_update_post(scene, depsgraph):
    global _first_run, _imported_objects_cache
    with _scene_update_lock:
        if _first_run:
            _first_run = False
            _update_object_cache()
            return

        updated_objects = _depsgraph_objects(depsgraph)
        if updated_objects is None:
            # Older Blender-compatible test hosts may not expose updates.
            # Keep the old behavior only on that path, not on every update.
            new_objects = _fallback_new_objects()
        else:
            new_objects = []
            for obj in updated_objects:
                token = _object_token(obj)
                if token not in _imported_objects_cache:
                    new_objects.append(obj)
                _imported_objects_cache.add(token)

    relevant_objects = [obj for obj in new_objects if _relevant_import_object(obj)]
    if relevant_objects:
        names = {getattr(obj, "name", "<unnamed>") for obj in relevant_objects}
        print(f"[FModel Auto] 检测到新导入对象: {names}")
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
            "vendor_errors": {},
            "added_paths": set(),
        }
        builtins.__dict__[_LIFECYCLE_STATE_KEY] = state
    return state


# The builtins mapping outlives this module when the add-on is reloaded.
_lifecycle_state = _get_lifecycle_state()
_lifecycle_generation = _lifecycle_state["generation"]
_registered_timers = _lifecycle_state["registered_timers"]
_vendor_registered = _lifecycle_state["vendor_registered"]
_vendor_errors = _lifecycle_state.setdefault("vendor_errors", {})
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
    # Vendor loading is deferred to Blender's next timer turn so registration
    # does not block the add-on enable callback, but it must not leave a
    # multi-second window where the UEFormat buttons are already visible while
    # their runtime is unavailable.
    _schedule_lifecycle_timer(_ensure_dependency_addons, 0.0)


def _vendor_loader_module():
    """Load the Blender-independent Vendor Loader in package and test contexts."""
    if __package__:
        from .core import vendor_loader
    else:
        from core import vendor_loader
    return vendor_loader


def _load_bundled_vendor(bundle_path):
    loader = _vendor_loader_module()
    return loader.load_bundled_vendor(
        bundle_path,
        cache_root=_vendor_cache_root(),
        verify_tree=True,
    )


def _vendor_cache_root():
    """Return Blender's user-scoped Vendor cache when the API is available."""
    user_resource = getattr(getattr(bpy, "utils", None), "user_resource", None)
    if callable(user_resource):
        try:
            return user_resource(
                "CONFIG",
                path="FModel Tools/vendor-cache",
                create=True,
            )
        except (OSError, RuntimeError, TypeError):
            pass
    return None


def _ensure_dependency_addons():
    prefs = bpy.context.preferences
    if prefs is None:
        print("[FModel] 无法获取用户偏好，跳过依赖检查")
        return None
    addons = prefs.addons

    # UEFormat is the only bundled asset-format dependency. Legacy PSK/PSA
    # support is intentionally no longer auto-loaded in the UEFormat-only line.
    bundled = {
        "io_scene_ueformat": os.path.join(
            os.path.dirname(__file__), "vendor", "io_scene_ueformat"
        ),
    }

    for module_id, bundle_path in bundled.items():
        if module_id in addons:
            _vendor_errors.pop(module_id, None)
            print(f"[FModel] {module_id} 已由用户安装，跳过内置副本")
            continue
        if module_id in _vendor_registered:
            print(f"[FModel] {module_id} 已通过内置副本注册，跳过")
            continue
        if not os.path.isdir(bundle_path):
            continue
        handle = None
        loader = None
        try:
            loader = _vendor_loader_module()
            handle = _load_bundled_vendor(bundle_path)
            mod = handle.module

            if not _is_current_ueformat_addon(mod):
                raise loader.VendorLoadError(
                    loader.VendorErrorCode.CAPABILITY_MISSING,
                    "Bundled io_scene_ueformat does not satisfy the current FModel contract.",
                    vendor_id=handle.spec.vendor_id,
                    source_path=handle.source_path,
                    remediation="Refresh the bundled UEFormat runtime instead of enabling a legacy copy.",
                )

            pre_check = _vendor_already_in_registry(mod)
            if pre_check:
                # The class table may survive an addon reload. Do not claim
                # ownership of a pre-existing registration we did not create.
                _vendor_observed.add(module_id)
                _vendor_errors.pop(module_id, None)
                print(f"[FModel] {module_id} 类已存在 (启动缓存), 跳过重复注册")
                continue

            loader.register_vendor(handle, headless=True)
            _vendor_registered[module_id] = handle
            _vendor_errors.pop(module_id, None)
            _added_paths.update(str(path) for path in handle.added_paths)
            print("[FModel] io_scene_ueformat 以 headless 模式注册（不创建 canonical 面板）")
            print(
                f"[FModel] {module_id} 内置副本已注册 "
                f"(version {handle.spec.version_text}, cache={handle.cache_root})"
            )
        except Exception as exc:
            # Loading/registering may fail after importing the package or
            # adding its wheel cache path. Roll back only the handle created
            # by this attempt; external modules are never owned here.
            if handle is not None and loader is not None:
                try:
                    loader.unregister_vendor(handle)
                except Exception as cleanup_exc:
                    print(f"[FModel] {module_id} 失败回滚异常: {cleanup_exc}")
            _vendor_errors[module_id] = str(exc)
            print(f"[FModel] {module_id} 内置注册失败: {exc}")

    return None

def get_vendor_status():
    """Return a UI-safe snapshot of the bundled UEFormat runtime state."""
    module_id = "io_scene_ueformat"
    entry = _vendor_registered.get(module_id)
    if entry is not None:
        if isinstance(entry, tuple):
            module, _bundle_path = entry
            return {
                "state": "ready",
                "module_id": module_id,
                "source": "bundled",
                "version": _addon_version(module),
                "message": "UEFormat runtime ready",
            }
        handle = entry
        version = getattr(getattr(handle, "spec", None), "version_text", "")
        capabilities = tuple(getattr(getattr(handle, "spec", None), "capabilities", ()) or ())
        return {
            "state": "ready",
            "module_id": module_id,
            "source": getattr(handle, "source", "bundled"),
            "source_path": getattr(handle, "source_path", ""),
            "version": version,
            "capabilities": capabilities,
            "tree_sha256": str(getattr(handle, "tree_sha256", "") or ""),
            "tree_file_count": int(getattr(handle, "tree_file_count", 0) or 0),
            "message": "Bundled UEFormat runtime ready",
        }

    if module_id in _vendor_observed:
        return {
            "state": "observed",
            "module_id": module_id,
            "source": "existing",
            "version": "",
            "message": "UEFormat runtime already registered",
        }

    try:
        prefs = getattr(bpy.context, "preferences", None)
        addons = getattr(prefs, "addons", {}) if prefs is not None else {}
        if module_id in addons:
            module = sys.modules.get(module_id)
            version = _addon_version(module)
            version_text = ".".join(str(part) for part in version) if version else ""
            is_current = _is_current_ueformat_addon(module) if module is not None else None
            if is_current is True:
                message = "External current UEFormat addon overrides the bundled V2 runtime"
                remediation = "Use either FModel Tools V2 or the standalone addon, not both enabled together."
            elif is_current is False:
                message = "External UEFormat addon lacks the current FModel capabilities"
                remediation = "Disable the external addon so FModel Tools V2 can load its verified bundled runtime."
            else:
                message = "External UEFormat addon selected; capability inspection is pending"
                remediation = "Restart Blender after disabling the external addon if V2 imports are unavailable."
            return {
                "state": "external",
                "module_id": module_id,
                "source": "external",
                "source_path": str(getattr(module, "__file__", "")) if module is not None else "",
                "version": version_text,
                "capabilities": {"current": is_current},
                "message": message,
                "remediation": remediation,
            }
    except Exception:
        pass

    error = _vendor_errors.get(module_id, "")
    if error:
        return {
            "state": "failed",
            "module_id": module_id,
            "source": "bundled",
            "version": "",
            "message": error,
            "remediation": "Verify the bundled Vendor tree and cache, then restart Blender.",
        }
    return {
        "state": "pending",
        "module_id": module_id,
        "source": "bundled",
        "version": "",
        "message": "UEFormat runtime is loading",
    }

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
    if version is None or version < (1, 2, 0):
        return False
    if not (
        getattr(module, "__file__", None)
        and getattr(module, "bl_info", {}).get("name")
    ):
        return False

    # The version number alone is not enough: a same-name legacy addon can be
    # repackaged with a newer version. Check the concrete model/material and
    # strict skeleton surfaces that the V2 bridge relies on.
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
    "io_scene_ueformat": ["UFImportUEModel", "UFImportUEAnim", "UFImportUEPose"],
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
    _vendor_errors.clear()
    print("[FModel Auto] 自动触发器已注销")


def _unregister_vendor_addons():
    loader = _vendor_loader_module()
    for module_id, entry in sorted(_vendor_registered.items()):
        try:
            # Accept the old tuple shape during one reload window so a Blender
            # session upgraded in place can still clean up its previous owner.
            if isinstance(entry, tuple):
                mod, bundle_path = entry
                if not _is_bundled_vendor_module(mod, bundle_path):
                    print(f"[FModel] {module_id} 内置副本来源无效，跳过注销")
                    continue
                if hasattr(mod, "unregister"):
                    mod.unregister()
                print(f"[FModel] {module_id} 内置副本已注销")
                continue

            handle = entry
            loader.unregister_vendor(handle)
            print(f"[FModel] {module_id} 内置副本已注销")
        except Exception as exc:
            print(f"[FModel] {module_id} 内置副本注销失败: {exc}")

