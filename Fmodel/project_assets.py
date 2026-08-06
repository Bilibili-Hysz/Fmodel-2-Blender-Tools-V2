"""Blender controls for the project-scoped derived asset index."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import bpy

from ..core.project_index import ProjectIndex


class FMODEL_IndexedAnimationChoice(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty(name="Animation Path", default="")
    selected: bpy.props.BoolProperty(name="Include", default=False)


def _read_only_connection(index):
    return sqlite3.connect(f"{Path(index.path).resolve().as_uri()}?mode=ro", uri=True)


def _skeleton_items(settings, _context):
    root_path = settings.root_path.strip()
    if not root_path or settings.index_state != "ready":
        return []
    try:
        index = ProjectIndex(root_path)
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
    return [(name, name, f"Indexed animations for {name}") for (name,) in skeletons]


def refresh_indexed_animation_choices(settings):
    """Replace the selection list, returning ``None`` when its index query fails."""
    root_path = settings.root_path.strip()
    skeleton = settings.indexed_skeleton
    if not root_path or not skeleton or settings.index_state != "ready":
        return []
    try:
        index = ProjectIndex(root_path)
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
    _clear_indexed_skeleton(settings)
    _clear_indexed_animation_choices(settings)
    refresh_project_index(settings)
    wizard = getattr(getattr(context, "scene", None), "pipeline_wizard", None)
    if wizard is not None and getattr(wizard, "source_mode", None) == "PROJECT":
        source_dir = settings.root_path.strip()
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
        name="Project Root", subtype='DIR_PATH', default="", update=_on_project_root_changed,
    )
    index_state: bpy.props.StringProperty(name="Index State", default="missing")
    index_schema_version: bpy.props.IntProperty(name="Index Schema Version", default=0)
    animation_count: bpy.props.IntProperty(name="Indexed Animations", default=0)
    model_count: bpy.props.IntProperty(name="Indexed Models", default=0)
    indexed_skeleton: bpy.props.EnumProperty(
        name="Indexed Skeleton", items=_skeleton_items,
        update=_on_indexed_skeleton_changed,
    )
    indexed_animations: bpy.props.CollectionProperty(type=FMODEL_IndexedAnimationChoice)


def refresh_project_index(settings):
    """Read the index status and counts without creating a database."""
    root_path = settings.root_path.strip()
    if not root_path:
        settings.index_state = "missing"
        settings.index_schema_version = 0
        settings.animation_count = 0
        settings.model_count = 0
        _clear_indexed_animation_choices(settings)
        return {"state": "missing", "schema_version": None, "rebuild_required": True}

    try:
        index = ProjectIndex(root_path)
    except OSError:
        status = {"state": "unavailable", "schema_version": None, "rebuild_required": True}
        settings.index_state = status["state"]
        settings.index_schema_version = 0
        settings.animation_count = 0
        settings.model_count = 0
        _clear_indexed_animation_choices(settings)
        return status
    status = index.status()
    settings.index_state = status["state"]
    settings.index_schema_version = status["schema_version"] or 0
    settings.animation_count, settings.model_count = _asset_counts(index, status)
    if status["state"] != "ready":
        _clear_indexed_skeleton(settings)
        _clear_indexed_animation_choices(settings)
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
    @staticmethod
    def _settings(context):
        return context.scene.fmodel_project

    def _index(self, context):
        settings = self._settings(context)
        root_path = settings.root_path.strip()
        if not root_path:
            self.report({'ERROR'}, "Project root is required")
            return None
        try:
            return ProjectIndex(root_path)
        except OSError as error:
            self.report({'ERROR'}, f"Project index unavailable: {error}")
            return None

    def _apply_status(self, settings, index, status):
        settings.index_state = status["state"]
        settings.index_schema_version = status.get("schema_version") or 0
        settings.animation_count, settings.model_count = _asset_counts(index, status)
        _clear_indexed_skeleton(settings)
        _clear_indexed_animation_choices(settings)


class FMODEL_OT_OpenProjectIndex(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.open_project_index"
    bl_label = "Open Project Index"

    def execute(self, context):
        index = self._index(context)
        if index is None:
            return {'CANCELLED'}
        try:
            status = index.status()
        except (OSError, sqlite3.Error) as error:
            self.report({'ERROR'}, f"Project index open failed: {error}")
            return {'CANCELLED'}
        self._apply_status(self._settings(context), index, status)
        if status["state"] == "missing":
            self.report({'ERROR'}, "Project index is missing; scan it")
            return {'CANCELLED'}
        if status["state"] == "unavailable":
            self.report({'ERROR'}, "Project index unavailable")
            return {'CANCELLED'}
        if status["state"] != "ready":
            self.report({'ERROR'}, "Rebuild required for project index")
            return {'CANCELLED'}
        self.report({'INFO'}, "Project index loaded")
        return {'FINISHED'}


class FMODEL_OT_ScanProjectIndex(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.scan_project_index"
    bl_label = "Scan Project Index"

    def execute(self, context):
        index = self._index(context)
        if index is None:
            return {'CANCELLED'}
        try:
            result = index.scan([self._settings(context).root_path])
            status = index.status()
        except (OSError, sqlite3.Error, ValueError) as error:
            self.report({'ERROR'}, f"Project index scan failed: {error}")
            return {'CANCELLED'}
        self._apply_status(self._settings(context), index, status)
        if result["state"] != "ready":
            self.report({'ERROR'}, "Project index scan failed")
            return {'CANCELLED'}
        self.report({'INFO'}, "Project index scanned")
        return {'FINISHED'}


class FMODEL_OT_RebuildProjectIndex(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.rebuild_project_index"
    bl_label = "Rebuild Project Index"

    def execute(self, context):
        index = self._index(context)
        if index is None:
            return {'CANCELLED'}
        try:
            status = index.rebuild()
        except (OSError, sqlite3.Error) as error:
            self.report({'ERROR'}, f"Project index rebuild failed: {error}")
            return {'CANCELLED'}
        self._apply_status(self._settings(context), index, status)
        if status["state"] != "ready":
            self.report({'ERROR'}, "Project index rebuild failed")
            return {'CANCELLED'}
        self.report({'INFO'}, "Project index rebuilt")
        return {'FINISHED'}


class FMODEL_OT_RefreshIndexedAnimationChoices(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.refresh_indexed_animation_choices"
    bl_label = "Refresh Indexed Animations"

    def execute(self, context):
        settings = self._settings(context)
        if settings.index_state != "ready":
            self.report({'ERROR'}, "A ready project index is required")
            return {'CANCELLED'}
        if not settings.indexed_skeleton:
            self.report({'ERROR'}, "Select an indexed skeleton")
            return {'CANCELLED'}
        paths = refresh_indexed_animation_choices(settings)
        if paths is None:
            self.report({'ERROR'}, "Project index query failed while loading animations")
            return {'CANCELLED'}
        if not paths:
            self.report({'WARNING'}, "No indexed animations for the selected skeleton")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Loaded {len(paths)} indexed animations")
        return {'FINISHED'}


class FMODEL_OT_ApplyIndexedAnimationSelection(_FMODEL_OT_ProjectIndexBase):
    bl_idname = "fmodel.apply_indexed_animation_selection"
    bl_label = "Use Indexed Animation Selection"

    def execute(self, context):
        settings = self._settings(context)
        if settings.index_state != "ready":
            self.report({'ERROR'}, "A ready project index is required")
            return {'CANCELLED'}
        if not settings.root_path.strip() or not settings.indexed_skeleton:
            self.report({'ERROR'}, "Select an indexed skeleton")
            return {'CANCELLED'}
        paths = [choice.path for choice in settings.indexed_animations if choice.selected]
        if not paths:
            self.report({'ERROR'}, "Select at least one indexed animation")
            return {'CANCELLED'}
        if not hasattr(context.scene, "pipeline_wizard"):
            self.report({'ERROR'}, "Animation Pipeline is unavailable")
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
            self.report({'ERROR'}, "Unable to apply indexed animation selection")
            return {'CANCELLED'}
        from .Pipeline.steps.state import clear_workflow_results
        clear_workflow_results(wizard)
        self.report({'INFO'}, f"Selected {len(paths)} animation(s) for the pipeline")
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
