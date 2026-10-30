# FModel_Tools/Fmodel/importers/animation/operator_anim_ueformat.py
# UEFormat 动画导入 — 支持 UEAnim 和 UEPose

import bpy
from ....core.workflow import resolve_target
import os
from bpy_extras.io_utils import ImportHelper

try:
    from ....core import utils as core_utils
except ImportError:
    core_utils = None

_core_get_ueformat_api = getattr(core_utils, "get_ueformat_api", None)
_core_get_ueformat_bridge = getattr(core_utils, "get_ueformat_bridge", None)

def get_ueformat_api():
    """Resolve current UEFormat for animation while preserving the old tuple API."""

    if callable(_core_get_ueformat_bridge):
        bridge = _core_get_ueformat_bridge(require_current=True, allow_legacy=False, capability="animation")
        return bridge.as_api_tuple() if bridge is not None else (None, None, None, None)
    if callable(_core_get_ueformat_api):
        return _core_get_ueformat_api()
    return None, None, None, None


class ANIM_OT_ImportUEAnim(bpy.types.Operator, ImportHelper):
    """导入 UEAnim 动画"""
    bl_idname = "fmodel.import_anim_ueanim"
    bl_label = "导入 UEAnim 动画"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".ueanim"
    filter_glob: bpy.props.StringProperty(default="*.ueanim", options={'HIDDEN'}) # type: ignore

    @staticmethod
    def _make_session(paths):
        try:
            from ....core.import_session import ImportSession
            return ImportSession("UEAnim", source_paths=tuple(paths))
        except Exception:
            return None

    @staticmethod
    def _persist_session(context, session):
        if session is None:
            return
        try:
            from ....core.import_session import persist_import_session
            persist_import_session(context.scene, session)
        except Exception:
            pass

    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'}) # type: ignore
    directory: bpy.props.StringProperty(subtype='DIR_PATH') # type: ignore

    scale_factor: bpy.props.FloatProperty(name="缩放 (Scale)", default=0.01, min=0.0001, max=100.0) # type: ignore
    rotation_only: bpy.props.BoolProperty(name="仅导入旋转", default=False) # type: ignore
    import_curves: bpy.props.BoolProperty(name="导入 Curve 辅助曲线", default=False) # type: ignore

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="动画配置", icon="ACTION")
        box.prop(self, "scale_factor")
        box.prop(self, "rotation_only")
        box.prop(self, "import_curves")

    def execute(self, context):
        target_arm = resolve_target(context)
        if not target_arm or target_arm.type != 'ARMATURE':
            self.report({'ERROR'}, "请在顶部指定目标骨架")
            return {'CANCELLED'}

        import_paths = [os.path.join(self.directory, f.name) for f in self.files if f.name] if self.files else [self.filepath]
        if not import_paths:
            return {'CANCELLED'}
        session = self._make_session(import_paths)

        try:
            UEFormatImport, _, UEAnimOptions, _ = get_ueformat_api()
        except Exception as exc:
            if session is not None:
                session.fail_all(phase="resolve_bridge", message=str(exc), code="ueformat_capability")
                self._persist_session(context, session)
            self.report({'ERROR'}, f"当前 UEFormat addon 不支持动画导入: {exc}")
            return {'CANCELLED'}
        if not UEFormatImport:
            if session is not None:
                session.fail_all(phase="resolve_bridge", message="UEFormat importer is unavailable", code="ueformat_missing")
                self._persist_session(context, session)
            self.report({'ERROR'}, "未检测到当前 UEFormat 插件！")
            return {'CANCELLED'}
            
        try:
            options = UEAnimOptions(
                scale_factor=self.scale_factor,
                rotation_only=self.rotation_only,
                import_curves=self.import_curves,
                override_skeleton=target_arm,
            )
        except TypeError as exc:
            if session is not None:
                session.fail_all(phase="build_options", message=str(exc), code="ueanim_options_unsupported")
                self._persist_session(context, session)
            self.report({'ERROR'}, f"当前 UEFormat 插件不支持动画导入参数: {exc}")
            return {'CANCELLED'}
        importer = UEFormatImport(options)

        success_count = 0
        for path in import_paths:
            safe_path = os.path.normpath(path)
            if session is not None:
                session.set_phase("import_file")
            try:
                actions_before = getattr(getattr(bpy, "data", None), "actions", None)
                before_ids = {id(action) for action in actions_before} if actions_before is not None else set()
                importer.import_file(safe_path)
                actions_after = getattr(getattr(bpy, "data", None), "actions", None)
                if actions_after is not None:
                    record = __import__("json").dumps({
                        "mode": "ueformat",
                        "source_file": os.path.normpath(safe_path),
                        "target_armature": getattr(target_arm, "name", ""),
                        "binding": "ueformat_bone_name",
                        "strict_identity": False,
                    }, separators=(",", ":"), sort_keys=True)
                    for action in actions_after:
                        if id(action) not in before_ids:
                            action["fmodel_skeleton_compatibility"] = record
                            action["Pipeline_SkeletonCompatibility"] = "ueformat"
                success_count += 1
                if session is not None:
                    session.record_success(safe_path)
            except Exception as e:
                if session is not None:
                    session.record_failure(safe_path, phase="import_file", message=str(e), code="ueanim_import_failed")
                self.report({'ERROR'}, f"导入失败 ({os.path.basename(safe_path)}): {e}")

        if session is not None:
            session.finish()
            self._persist_session(context, session)
        if success_count > 0:
            msg = f"成功加载 {success_count} 个 UEAnim。" + ("(已过滤冗余 Curve)" if not self.import_curves else "")
            if session is not None and session.failure_count:
                self.report({'WARNING'}, f"{msg} 失败 {session.failure_count} 个。")
            else:
                self.report({'INFO'}, msg)
            return {'FINISHED'}
        return {'CANCELLED'}


class ANIM_OT_ImportUEPose(bpy.types.Operator, ImportHelper):
    """保存完整 UEPose 源数据；在姿势资产面板应用或烘焙"""
    bl_idname = "fmodel.import_anim_uepose"
    bl_label = "导入 UEPose 姿势"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".uepose"
    filter_glob: bpy.props.StringProperty(default="*.uepose", options={'HIDDEN'}) # type: ignore

    @staticmethod
    def _import_source(path, scale, convention):
        from .pose_assets import import_source
        return import_source(path, scale, convention)

    @staticmethod
    def _refresh_library(scene):
        from .pose_library_ui import refresh
        return refresh(scene)

    @staticmethod
    def _make_session(paths):
        try:
            from ....core.import_session import ImportSession
            return ImportSession("UEPose", source_paths=tuple(paths))
        except Exception:
            return None

    @staticmethod
    def _persist_session(context, session):
        if session is None:
            return
        try:
            from ....core.import_session import persist_import_session
            persist_import_session(context.scene, session)
        except Exception:
            pass

    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'}) # type: ignore
    directory: bpy.props.StringProperty(subtype='DIR_PATH') # type: ignore

    scale_factor: bpy.props.FloatProperty(name="缩放 (Scale)", default=0.01, min=0.0001, max=100.0) # type: ignore
    _enum_property = getattr(bpy.props, "EnumProperty", None)

    if callable(_enum_property):
        pose_convention: _enum_property(
            name="变换约定",
            items=[
                ("CUE4PARSE_ADDITIVE", "CUE4Parse 加法姿势", "按 CUE4Parse 的加法局部变换约定应用"),
                ("UNSPECIFIED", "未知，仅保存源数据", "保存完整文件，确认导出约定后再应用"),
            ],
            default="CUE4PARSE_ADDITIVE",
        )
    else:
        pose_convention = "CUE4PARSE_ADDITIVE"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="姿势配置", icon="POSE_HLT")
        box.prop(self, "scale_factor")
        box.prop(self, "pose_convention")
        box.label(text="保存完整姿势源；不改变当前角色")
        box.label(text="应用与烘焙请使用资产库中的姿势资产面板")

    def execute(self, context):
        target_arm = resolve_target(context)
        if target_arm and target_arm.type != 'ARMATURE':
            target_arm = None

        import_paths = [os.path.join(self.directory, f.name) for f in self.files if f.name] if self.files else [self.filepath]
        if not import_paths:
            return {'CANCELLED'}
        session = self._make_session(import_paths)

        try:
            UEFormatImport, _, _, UEPoseOptions = get_ueformat_api()
        except Exception as exc:
            if session is not None:
                session.fail_all(phase="resolve_bridge", message=str(exc), code="ueformat_capability")
                self._persist_session(context, session)
            self.report({'ERROR'}, f"当前 UEFormat addon 不支持姿势导入: {exc}")
            return {'CANCELLED'}
        if not UEFormatImport:
            if session is not None:
                session.fail_all(phase="resolve_bridge", message="UEFormat importer is unavailable", code="ueformat_missing")
                self._persist_session(context, session)
            self.report({'ERROR'}, "未检测到当前 UEFormat 插件！")
            return {'CANCELLED'}
            
        try:
            # Check dependency options without applying the stored pose.
            UEPoseOptions(
                scale_factor=self.scale_factor,
                override_skeleton=target_arm,
            )
        except TypeError as exc:
            if session is not None:
                session.fail_all(phase="build_options", message=str(exc), code="uepose_options_unsupported")
                self._persist_session(context, session)
            self.report({'ERROR'}, f"当前 UEFormat 插件不支持姿势导入参数: {exc}")
            return {'CANCELLED'}
        stored_poses = 0
        stored_curves = 0

        success_count = 0
        for path in import_paths:
            safe_path = os.path.normpath(path)
            if session is not None:
                session.set_phase("import_file")
            try:
                _text, pose_count, curve_count = self._import_source(
                    safe_path, self.scale_factor,
                    getattr(self, "pose_convention", "CUE4PARSE_ADDITIVE"),
                )
                stored_poses += pose_count
                stored_curves += curve_count
                if session is not None:
                    session.record_created("pose_sources")
                    session.record_created("poses", pose_count)
                    session.record_created("pose_curve_influences", curve_count)
                success_count += 1
                if session is not None:
                    session.record_success(safe_path)
            except Exception as e:
                if session is not None:
                    session.record_failure(safe_path, phase="import_file", message=str(e), code="uepose_import_failed")
                self.report({'ERROR'}, f"导入失败: {e}")

        if session is not None:
            session.finish()
            self._persist_session(context, session)
        if success_count > 0:
            self._refresh_library(context.scene)
            message = f"已保存 {success_count} 个姿势源、{stored_poses} 个姿势、{stored_curves} 个曲线影响；可在姿势资产面板应用或烘焙"
            if session is not None and session.failure_count:
                self.report({'WARNING'}, f"{message} 失败 {session.failure_count} 个。")
            else:
                self.report({'INFO'}, message)
            return {'FINISHED'}
        return {'CANCELLED'}


def register():
    bpy.utils.register_class(ANIM_OT_ImportUEAnim)
    bpy.utils.register_class(ANIM_OT_ImportUEPose)


def unregister():
    bpy.utils.unregister_class(ANIM_OT_ImportUEPose)
    bpy.utils.unregister_class(ANIM_OT_ImportUEAnim)
