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

    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'}) # type: ignore
    directory: bpy.props.StringProperty(subtype='DIR_PATH') # type: ignore

    scale_factor: bpy.props.FloatProperty(name="缩放 (Scale)", default=0.01, min=0.0001, max=100.0) # type: ignore
    rotation_only: bpy.props.BoolProperty(name="仅导入旋转", default=False) # type: ignore
    import_curves: bpy.props.BoolProperty(name="导入 Curve 辅助曲线", default=False) # type: ignore

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="动画配置 (Animation)", icon="ACTION")
        box.prop(self, "scale_factor")
        box.prop(self, "rotation_only")
        box.prop(self, "import_curves")

    def execute(self, context):
        target_arm = context.active_object
        if not target_arm or target_arm.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架本体")
            return {'CANCELLED'}

        import_paths = [os.path.join(self.directory, f.name) for f in self.files if f.name] if self.files else [self.filepath]
        if not import_paths: return {'CANCELLED'}

        try:
            UEFormatImport, _, UEAnimOptions, _ = get_ueformat_api()
        except Exception as exc:
            self.report({'ERROR'}, f"当前 UEFormat addon 不支持严格动画骨架校验: {exc}")
            return {'CANCELLED'}
        if not UEFormatImport:
            self.report({'ERROR'}, "未检测到当前 UEFormat 插件！")
            return {'CANCELLED'}
            
        try:
            options = UEAnimOptions(
                scale_factor=self.scale_factor,
                rotation_only=self.rotation_only,
                import_curves=self.import_curves,
                override_skeleton=target_arm,
                strict_fmodel_skeleton=True,
            )
        except TypeError:
            # Legacy UEFormat has no strict identity option.
            options = UEAnimOptions(
                scale_factor=self.scale_factor,
                rotation_only=self.rotation_only,
                import_curves=self.import_curves,
                override_skeleton=target_arm,
            )
        importer = UEFormatImport(options)

        success_count = 0
        for path in import_paths:
            safe_path = os.path.normpath(path)
            try:
                importer.import_file(safe_path)
                success_count += 1
            except Exception as e:
                self.report({'ERROR'}, f"导入失败 ({os.path.basename(safe_path)}): {e}")

        if success_count > 0:
            msg = f"成功加载 {success_count} 个 UEAnim。" + ("(已过滤冗余 Curve)" if not self.import_curves else "")
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

    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'}) # type: ignore
    directory: bpy.props.StringProperty(subtype='DIR_PATH') # type: ignore

    scale_factor: bpy.props.FloatProperty(name="缩放 (Scale)", default=0.01, min=0.0001, max=100.0) # type: ignore

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="姿势配置 (Pose)", icon="POSE_HLT")
        box.prop(self, "scale_factor")

    def execute(self, context):
        target_arm = context.active_object
        if not target_arm or target_arm.type != 'ARMATURE':
            self.report({'ERROR'}, "请先选中骨架本体")
            return {'CANCELLED'}

        import_paths = [os.path.join(self.directory, f.name) for f in self.files if f.name] if self.files else [self.filepath]
        if not import_paths: return {'CANCELLED'}

        try:
            UEFormatImport, _, _, UEPoseOptions = get_ueformat_api()
        except Exception as exc:
            self.report({'ERROR'}, f"当前 UEFormat addon 不支持严格姿势骨架校验: {exc}")
            return {'CANCELLED'}
        if not UEFormatImport:
            self.report({'ERROR'}, "未检测到当前 UEFormat 插件！")
            return {'CANCELLED'}
            
        try:
            options = UEPoseOptions(
                scale_factor=self.scale_factor,
                override_skeleton=target_arm,
                strict_fmodel_skeleton=True,
            )
        except TypeError:
            options = UEPoseOptions(scale_factor=self.scale_factor, override_skeleton=target_arm)
        importer = UEFormatImport(options)

        success_count = 0
        for path in import_paths:
            try:
                importer.import_file(os.path.normpath(path))
                success_count += 1
            except Exception as e:
                self.report({'ERROR'}, f"导入失败: {e}")

        if success_count > 0:
            self.report({'INFO'}, f"成功加载 {success_count} 个 UEPose 资产！")
            return {'FINISHED'}
        return {'CANCELLED'}


def register():
    bpy.utils.register_class(ANIM_OT_ImportUEAnim)
    bpy.utils.register_class(ANIM_OT_ImportUEPose)


def unregister():
    bpy.utils.unregister_class(ANIM_OT_ImportUEPose)
    bpy.utils.unregister_class(ANIM_OT_ImportUEAnim)
