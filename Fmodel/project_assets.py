"""Blender controls for the project-scoped derived asset index."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import bpy

from ..core.project_index import ProjectIndex


class FMODEL_IndexedAnimationChoice(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty(name="动画路径", default="")
    selected: bpy.props.BoolProperty(name="包含", default=False)


def _read_only_connection(index):
    return sqlite3.connect(f"{Path(index.path).resolve().as_uri()}?mode=ro", uri=True)


def _skeleton_items(settings, _context):
    root_path = settings.root_path.strip()
    if not root_path or settings.index_state != "ready":
        return []
    try:
        index = ProjectIndex(root_path, asset_scope="animation")
        if index.status()["state"] != "ready":
            return []
        with closing(_read_only_connection(index)) as connection:
            skeletons = connection.execute(
                "SELECT DISTINCT skeleton_name FROM assets "
                "WHERE asset_type = 'animation' AND skeleton_name IS NOT NULL "
                "AND skeleton_name != '' ORDER BY skeleton_name"
            ).fetchall()
    except (OSError, sqlite3.Error):
        return []
    return [(name, name, f"已索引动画: {name}") for (name,) in skeletons]


def refresh_indexed_animation_choices(settings):
    """Replace the selection list, returning ``None`` when its index query fails."""
    root_path = settings.root_path.strip()
    skeleton = settings.indexed_skeleton
    if not root_path or not skeleton or settings.index_state != "ready":
        return []
    try:
        index = ProjectIndex(root_path, asset_scope="animation")
        if index.status()["state"] != "ready":
            return None
        paths = index.read_only_animations_for_skeleton(skeleton, raise_on_error=True)
    except (OSError, sqlite3.Error):
        return None
    settings.indexed_animations.clear()
    for path in paths:
        choice = settings.indexed_animations.add()
        choice.path = path
        choice.selected = False
    return paths


def _on_indexed_skeleton_changed(settings, _context):
    refresh_indexed_animation_choices(settings)


def _clear_indexed_animation_choices(settings):
    choices = getattr(settings, "indexed_animations", None)
    if choices is not None:
        choices.clear()


def _clear_indexed_skeleton(settings):
    property_unset = getattr(settings, "property_unset", None)
    if property_unset is not None:
        property_unset("indexed_skeleton")
    else:
        settings.indexed_skeleton = ""


def _on_project_root_changed(settings, context):
    """Mirror the deprecated project root into Animation Pipeline state."""
    _clear_indexed_skeleton(settings)
    _clear_indexed_animation_choices(settings)
    wizard = getattr(getattr(context, "scene", None), "pipeline_wizard", None)
    source_dir = settings.root_path.strip()
    if wizard is not None and hasattr(wizard, "project_root"):
        if getattr(wizard, "project_root", "") != source_dir:
            wizard.project_root = source_dir
    refresh_project_index(settings)
    _sync_index_status_to_wizard(settings, wizard)
    _inspect_pipeline_index(wizard)
    if wizard is not None and getattr(wizard, "source_mode", None) == "PROJECT":
        if wizard.source_dir != source_dir:
            wizard.source_dir = source_dir
            try:
                from .Pipeline.steps.state import clear_workflow_results
            except ModuleNotFoundError:
                for name, value in (("scan_completed", False), ("import_completed", False), ("lib_completed", False)):
                    if hasattr(wizard, name):
                        setattr(wizard, name, value)
            else:
                clear_workflow_results(wizard)


class FMODEL_ProjectSettings(bpy.types.PropertyGroup):
    root_path: bpy.props.StringProperty(
        name="项目根目录", subtype='DIR_PATH', default="", update=_on_project_root_changed,
    )
    index_state: bpy.props.StringProperty(name="索引状态", default="missing")
    index_schema_version: bpy.props.IntProperty(name="索引架构版本", default=0)
    animation_count: bpy.props.IntProperty(name="已索引动画", default=0)
    model_count: bpy.props.IntProperty(name="已索引模型", default=0)
    indexed_skeleton: bpy.props.EnumProperty(
        name="已索引骨架", items=_skeleton_items,
        update=_on_indexed_skeleton_changed,
    )
    indexed_animations: bpy.props.CollectionProperty(type=FMODEL_IndexedAnimationChoice)


def _inspect_pipeline_index(wizard):
    """Use the new read-only service when Pipeline modules are available."""
    if wizard is None:
        return None
    try:
        from .Pipeline.project_index_controller import inspect_pipeline_project_index
    except (ImportError, ModuleNotFoundError):
        return None
    return inspect_pipeline_project_index(wizard)


def _sync_index_status_to_wizard(settings, wizard):
    """Copy derived index display state without making it authoritative."""
    if wizard is None:
        return
    values = {
        "project_index_state": getattr(settings, "index_state", "missing"),
        "project_index_schema_version": getattr(settings, "index_schema_version", 0),
        "project_index_animation_count": getattr(settings, "animation_count", 0),
    }
    state = values["project_index_state"]
    values["project_index_message"] = {
        "ready": "Animation index ready",
        "missing": "Animation index has not been created",
        "rebuild_required": "Animation index must be reset",
        "unavailable": "Animation index is unavailable",
    }.get(state, f"Animation index: {state}")
    for name, value in values.items():
        if hasattr(wizard, name):
            setattr(wizard, name, value)


def refresh_project_index(settings, wizard=None):
    """Read the index status and counts without creating a database."""
    root_path = settings.root_path.strip()
    if not root_path:
        settings.index_state = "missing"
        settings.index_schema_version = 0
        settings.animation_count = 0
        settings.model_count = 0
        _clear_indexed_animation_choices(settings)
        status = {"state": "missing", "schema_version": None, "rebuild_required": True}
        _sync_index_status_to_wizard(settings, wizard)
        return status

    try:
        index = ProjectIndex(root_path, asset_scope="animation")
    except OSError:
        status = {"state": "unavailable", "schema_version": None, "rebuild_required": True}
        settings.index_state = status["state"]
        settings.index_schema_version = 0
        settings.animation_count = 0
        settings.model_count = 0
        _clear_indexed_animation_choices(settings)
        _sync_index_status_to_wizard(settings, wizard)
        return status
    status = index.status()
    settings.index_state = status["state"]
    settings.index_schema_version = status["schema_version"] or 0
    settings.animation_count, settings.model_count = _asset_counts(index, status)
    if status["state"] != "ready":
        _clear_indexed_skeleton(settings)
        _clear_indexed_animation_choices(settings)
    _sync_index_status_to_wizard(settings, wizard)
    return status


def refresh_pipeline_project_index(scene):
    """Migrate the old root and refresh Pipeline index display state read-only."""
    settings = getattr(scene, "fmodel_project", None)
    wizard = getattr(scene, "pipeline_wizard", None)
    if wizard is None:
        return {"state": "missing", "schema_version": None, "rebuild_required": True}
    wizard_root = getattr(wizard, "project_root", "") or ""
    legacy_root = getattr(settings, "root_path", "") if settings is not None else ""
    if not wizard_root and legacy_root:
        wizard.project_root = legacy_root
        wizard_root = legacy_root
    elif wizard_root and settings is not None and legacy_root != wizard_root:
        settings.root_path = wizard_root
    if settings is None:
        if hasattr(wizard, "project_index_state"):
            wizard.project_index_state = "missing"
            wizard.project_index_schema_version = 0
            wizard.project_index_animation_count = 0
            wizard.project_index_message = "Animation index compatibility state is unavailable"
        return {"state": "missing", "schema_version": None, "rebuild_required": True}
    status = refresh_project_index(settings, wizard=wizard)
    _inspect_pipeline_index(wizard)
    return status


def _asset_counts(index, status):
    if status["state"] != "ready":
        return 0, 0
    try:
        with closing(_read_only_connection(index)) as connection:
            rows = connection.execute(
                "SELECT asset_type, COUNT(*) FROM assets GROUP BY asset_type"
            ).fetchall()
    except (OSError, sqlite3.Error):
        return 0, 0
    counts = dict(rows)
    return counts.get("animation", 0), counts.get("model", 0)


class _FMODEL_OT_ProjectIndexBase(bpy.types.Operator):
    @classmethod
    def poll(cls, context):
        wizard = getattr(getattr(context, "scene", None), "pipeline_wizard", None)
        return getattr(wizard, "project_index_state", "") != "scanning"

    @staticmethod
    def _settings(context):
        return context.scene.fmodel_project

    def _index(self, context):
        settings = self._settings(context)
        wizard = getattr(context.scene, "pipeline_wizard", None)
        if getattr(wizard, "project_index_state", "") == "scanning":
            self.report({'ERROR'}, "动画索引准备任务已在运行")
            return None
        root_path = getattr(wizard, "project_root", "") if wizard is not None else ""
        root_path = root_path.strip() if isinstance(root_path, str) else ""
        if not root_path:
            root_path = settings.root_path.strip()
        elif settings.root_path.strip() != root_path:
            settings.root_path = root_path
        if not root_path:
            self.report({'ERROR'}, "必须先设置项目根目录")
            return None
        try:
            return ProjectIndex(root_path, asset_scope="animation")
        except OSError as error:
            self.report({'ERROR'}, f"项目索引不可用: {error}")
            return None

    def _apply_status(self, context, settings, index, status):
        settings.index_state = status["state"]
        settings.index_schema_version = status.get("schema_version") or 0
        settings.animation_count, settings.model_count = _asset_counts(index, status)
        _clear_indexed_skeleton(settings)
        _clear_indexed_animation_choices(settings)
        wizard = getattr(context.scene, "pipeline_wizard", None)
        _sync_index_status_to_wizard(settings, wizard)
        _inspect_pipeline_index(wizard)


class FMODEL_OT_OpenProjectIndex(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.open_project_index"
    bl_label = "打开项目索引"

    def execute(self, context):
        index = self._index(context)
        if index is None:
            return {'CANCELLED'}
        try:
            status = index.status()
        except (OSError, sqlite3.Error) as error:
            self.report({'ERROR'}, f"打开项目索引失败: {error}")
            return {'CANCELLED'}
        self._apply_status(context, self._settings(context), index, status)
        if status["state"] == "missing":
            self.report({'ERROR'}, "项目索引缺失，请先扫描")
            return {'CANCELLED'}
        if status["state"] == "unavailable":
            self.report({'ERROR'}, "项目索引不可用")
            return {'CANCELLED'}
        if status["state"] != "ready":
            self.report({'ERROR'}, "需要重建项目索引")
            return {'CANCELLED'}
        self.report({'INFO'}, "项目索引已加载")
        return {'FINISHED'}


class FMODEL_OT_ScanProjectIndex(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.scan_project_index"
    bl_label = "扫描项目索引"

    def execute(self, context):
        index = self._index(context)
        if index is None:
            return {'CANCELLED'}
        try:
            result = index.scan([self._settings(context).root_path])
            status = index.status()
        except (OSError, sqlite3.Error, ValueError) as error:
            self.report({'ERROR'}, f"项目索引扫描失败: {error}")
            return {'CANCELLED'}
        self._apply_status(context, self._settings(context), index, status)
        if result["state"] != "ready":
            self.report({'ERROR'}, "项目索引扫描失败")
            return {'CANCELLED'}
        self.report({'INFO'}, "项目索引已扫描")
        return {'FINISHED'}


class FMODEL_OT_RebuildProjectIndex(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.rebuild_project_index"
    bl_label = "重置并重新扫描动画索引"

    def execute(self, context):
        index = self._index(context)
        if index is None:
            return {'CANCELLED'}
        try:
            status = index.rebuild()
            if status["state"] == "ready":
                result = index.scan([str(index.root)])
                status = index.status()
            else:
                result = {"state": "failed"}
        except (OSError, sqlite3.Error, ValueError) as error:
            self.report({'ERROR'}, f"重置动画索引失败: {error}")
            return {'CANCELLED'}
        self._apply_status(context, self._settings(context), index, status)
        if status["state"] != "ready" or result["state"] != "ready":
            self.report({'ERROR'}, "动画索引重置并重新扫描失败")
            return {'CANCELLED'}
        self.report({'INFO'}, "动画索引已重置并重新扫描")
        return {'FINISHED'}


class FMODEL_OT_RefreshIndexedAnimationChoices(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.refresh_indexed_animation_choices"
    bl_label = "刷新已索引动画"

    def execute(self, context):
        settings = self._settings(context)
        if settings.index_state != "ready":
            self.report({'ERROR'}, "需要可用的项目索引")
            return {'CANCELLED'}
        if not settings.indexed_skeleton:
            self.report({'ERROR'}, "请选择已索引的骨架")
            return {'CANCELLED'}
        paths = refresh_indexed_animation_choices(settings)
        if paths is None:
            self.report({'ERROR'}, "加载动画时项目索引查询失败")
            return {'CANCELLED'}
        if not paths:
            self.report({'WARNING'}, "所选骨架没有已索引动画")
            return {'CANCELLED'}
        self.report({'INFO'}, f"已加载 {len(paths)} 个已索引动画")
        return {'FINISHED'}


class FMODEL_OT_ApplyIndexedAnimationSelection(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.apply_indexed_animation_selection"
    bl_label = "使用已索引动画选择"

    def execute(self, context):
        settings = self._settings(context)
        if settings.index_state != "ready":
            self.report({'ERROR'}, "需要可用的项目索引")
            return {'CANCELLED'}
        if not settings.root_path.strip() or not settings.indexed_skeleton:
            self.report({'ERROR'}, "请选择已索引的骨架")
            return {'CANCELLED'}
        paths = [choice.path for choice in settings.indexed_animations if choice.selected]
        if not paths:
            self.report({'ERROR'}, "请选择至少一个已索引动画")
            return {'CANCELLED'}
        if not hasattr(context.scene, "pipeline_wizard"):
            self.report({'ERROR'}, "动画管线不可用")
            return {'CANCELLED'}
        wizard = context.scene.pipeline_wizard
        updates = (
            ("indexed_project_root", settings.root_path.strip()),
            ("indexed_skeleton", settings.indexed_skeleton),
            ("indexed_selected_paths", json.dumps(paths)),
            ("indexed_selection_mode", "EXPLICIT"),
        )
        previous = {name: getattr(wizard, name) for name, _ in updates}
        try:
            for name, value in updates:
                setattr(wizard, name, value)
        except Exception:
            for name, value in previous.items():
                try:
                    setattr(wizard, name, value)
                except Exception:
                    pass
            self.report({'ERROR'}, "无法应用已索引的动画选择")
            return {'CANCELLED'}
        from .Pipeline.steps.state import clear_workflow_results
        clear_workflow_results(wizard)
        self.report({'INFO'}, f"已为管线选择 {len(paths)} 个动画")
        return {'FINISHED'}


_classes = (
    FMODEL_IndexedAnimationChoice,
    FMODEL_ProjectSettings,
    FMODEL_OT_OpenProjectIndex,
    FMODEL_OT_ScanProjectIndex,
    FMODEL_OT_RebuildProjectIndex,
    FMODEL_OT_RefreshIndexedAnimationChoices,
    FMODEL_OT_ApplyIndexedAnimationSelection,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.fmodel_project = bpy.props.PointerProperty(type=FMODEL_ProjectSettings)


def unregister():
    del bpy.types.Scene.fmodel_project
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
