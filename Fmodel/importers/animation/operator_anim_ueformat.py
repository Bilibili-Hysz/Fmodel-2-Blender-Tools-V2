# FModel_Tools/Fmodel/importers/animation/operator_anim_ueformat.py
# UEFormat 动画导入 — 支持 UEAnim 和 UEPose

import bpy
import os
from bpy_extras.io_utils import ImportHelper

try:
    from ....core import utils as core_utils
except ImportError:
    core_utils = None

_core_get_ueformat_api = getattr(core_utils, "get_ueformat_api", None)
_core_get_ueformat_bridge = getattr(core_utils, "get_ueformat_bridge", None)

try:
    from ....core import (
        SKELETON_COMPATIBILITY_ITEMS,
        SKELETON_COMPATIBILITY_STRICT,
        SKELETON_COMPATIBILITY_NAME_RETARGET,
    )
except (ImportError, AttributeError):
    SKELETON_COMPATIBILITY_STRICT = "strict"
    SKELETON_COMPATIBILITY_NAME_RETARGET = "name_retarget"
    SKELETON_COMPATIBILITY_ITEMS = (
        ("strict", "严格身份", "要求 UEFormat 骨架身份完全一致"),
        ("name_retarget", "按名称兼容", "显式绑定同名骨骼"),
    )



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
    _enum_property = getattr(bpy.props, "EnumProperty", None)
    _compatibility_items = globals().get("SKELETON_COMPATIBILITY_ITEMS", (
        ("strict", "严格身份", "要求 UEFormat 骨架身份完全一致"),
        ("name_retarget", "按名称兼容", "显式绑定同名骨骼"),
    ))
    _compatibility_strict = globals().get("SKELETON_COMPATIBILITY_STRICT", "strict")
    if callable(_enum_property):
        compatibility_mode: _enum_property(
            name="骨架兼容策略",
            items=_compatibility_items,
            default=_compatibility_strict,
        )
    else:
        compatibility_mode = _compatibility_strict

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="动画配置", icon="ACTION")
        box.prop(self, "scale_factor")
        box.prop(self, "rotation_only")
        box.prop(self, "import_curves")
        box.prop(self, "compatibility_mode")
        if getattr(self, "compatibility_mode", "strict") == "name_retarget":
            box.label(text="按名称兼容不等同于完整的 Unreal 重定向", icon="ERROR")

    def execute(self, context):
        target_arm = context.active_object
        if not target_arm or target_arm.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架本体")
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
            self.report({'ERROR'}, f"当前 UEFormat addon 不支持严格动画骨架校验: {exc}")
            return {'CANCELLED'}
        if not UEFormatImport:
            if session is not None:
                session.fail_all(phase="resolve_bridge", message="UEFormat importer is unavailable", code="ueformat_missing")
                self._persist_session(context, session)
            self.report({'ERROR'}, "未检测到当前 UEFormat 插件！")
            return {'CANCELLED'}
            
        compatibility_mode = getattr(self, "compatibility_mode", "strict")
        if compatibility_mode not in {"strict", "name_retarget"}:
            compatibility_mode = "strict"
        strict_fmodel_skeleton = compatibility_mode == "strict"
        try:
            options = UEAnimOptions(
                scale_factor=self.scale_factor,
                rotation_only=self.rotation_only,
                import_curves=self.import_curves,
                override_skeleton=target_arm,
                strict_fmodel_skeleton=strict_fmodel_skeleton,
            )
        except TypeError as exc:
            # A current bridge must never silently bypass strict skeleton
            # validation.  Continuing here could apply an animation to an
            # incompatible armature while reporting success.
            if session is not None:
                session.fail_all(phase="build_options", message=str(exc), code="strict_skeleton_unsupported")
                self._persist_session(context, session)
            self.report({'ERROR'}, f"当前 UEFormat 插件无法启用严格骨架校验: {exc}")
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
                if compatibility_mode == "name_retarget":
                    actions_after = getattr(getattr(bpy, "data", None), "actions", None)
                    if actions_after is not None:
                        record = __import__("json").dumps({
                            "mode": compatibility_mode,
                            "source_file": os.path.normpath(safe_path),
                            "target_armature": getattr(target_arm, "name", ""),
                            "binding": "casefold_bone_name",
                            "strict_identity": False,
                        }, separators=(",", ":"), sort_keys=True)
                        for action in actions_after:
                            if id(action) not in before_ids:
                                action["fmodel_skeleton_compatibility"] = record
                                action["Pipeline_SkeletonCompatibility"] = compatibility_mode
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
            if compatibility_mode == "name_retarget":
                msg += " 已使用按骨骼名兼容模式（非完整 Unreal Retarget Manager）。"
            if session is not None and session.failure_count:
                self.report({'WARNING'}, f"{msg} 失败 {session.failure_count} 个。")
            else:
                self.report({'INFO'}, msg)
            return {'FINISHED'}
        return {'CANCELLED'}


class ANIM_OT_ImportUEPose(bpy.types.Operator, ImportHelper):
    """导入 UEPose 姿势"""
    bl_idname = "fmodel.import_anim_uepose"
    bl_label = "导入 UEPose 姿势"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".uepose"
    filter_glob: bpy.props.StringProperty(default="*.uepose", options={'HIDDEN'}) # type: ignore

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
    _compatibility_items = globals().get("SKELETON_COMPATIBILITY_ITEMS", (
        ("strict", "严格身份", "要求 UEFormat 骨架身份完全一致"),
        ("name_retarget", "按名称兼容", "显式绑定同名骨骼"),
    ))
    _compatibility_strict = globals().get("SKELETON_COMPATIBILITY_STRICT", "strict")
    if callable(_enum_property):
        compatibility_mode: _enum_property(
            name="骨架兼容策略",
            items=_compatibility_items,
            default=_compatibility_strict,
        )
    else:
        compatibility_mode = _compatibility_strict

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="姿势配置", icon="POSE_HLT")
        box.prop(self, "scale_factor")
        box.prop(self, "compatibility_mode")
        if getattr(self, "compatibility_mode", "strict") == "name_retarget":
            box.label(text="按名称兼容不等同于完整的 Unreal 重定向", icon="ERROR")

    def execute(self, context):
        target_arm = context.active_object
        if not target_arm or target_arm.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架本体")
            return {'CANCELLED'}

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
            self.report({'ERROR'}, f"当前 UEFormat addon 不支持严格姿势骨架校验: {exc}")
            return {'CANCELLED'}
        if not UEFormatImport:
            if session is not None:
                session.fail_all(phase="resolve_bridge", message="UEFormat importer is unavailable", code="ueformat_missing")
                self._persist_session(context, session)
            self.report({'ERROR'}, "未检测到当前 UEFormat 插件！")
            return {'CANCELLED'}
            
        compatibility_mode = getattr(self, "compatibility_mode", "strict")
        if compatibility_mode not in {"strict", "name_retarget"}:
            compatibility_mode = "strict"
        strict_fmodel_skeleton = compatibility_mode == "strict"
        try:
            options = UEPoseOptions(
                scale_factor=self.scale_factor,
                override_skeleton=target_arm,
                strict_fmodel_skeleton=strict_fmodel_skeleton,
            )
        except TypeError as exc:
            if session is not None:
                session.fail_all(phase="build_options", message=str(exc), code="strict_skeleton_unsupported")
                self._persist_session(context, session)
            self.report({'ERROR'}, f"当前 UEFormat 插件无法启用严格姿势骨架校验: {exc}")
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
                if compatibility_mode == "name_retarget":
                    actions_after = getattr(getattr(bpy, "data", None), "actions", None)
                    if actions_after is not None:
                        record = __import__("json").dumps({
                            "mode": compatibility_mode,
                            "source_file": os.path.normpath(safe_path),
                            "target_armature": getattr(target_arm, "name", ""),
                            "binding": "casefold_bone_name",
                            "strict_identity": False,
                        }, separators=(",", ":"), sort_keys=True)
                        for action in actions_after:
                            if id(action) not in before_ids:
                                action["fmodel_skeleton_compatibility"] = record
                                action["Pipeline_SkeletonCompatibility"] = compatibility_mode
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
            message = f"成功加载 {success_count} 个 UEPose 资产！"
            if compatibility_mode == "name_retarget":
                message += " 已使用按骨骼名兼容模式（非完整 Unreal Retarget Manager）。"
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
