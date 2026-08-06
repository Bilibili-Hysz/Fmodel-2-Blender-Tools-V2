# FModel_Tools/Fmodel/importers/model/operator_model_ueformat.py
# UEFormat 模型导入 — 支持 UModel 导出格式

import bpy
import os
from bpy_extras.io_utils import ImportHelper
from ....Universal_tools import material as mat_module
from ....core import log_info, log_error, log_success, backup_before, OperationType
from ...Pipeline.steps.single_model_flow import prepare_imported_armature_for_animation

try:
    # Keep the bridge API discoverable in production while retaining compatibility
    # with older hosts/tests that only expose the original tuple API.
    from ....core import utils as core_utils
except ImportError:
    core_utils = None

get_ueformat_api = getattr(core_utils, "get_ueformat_api", None)
get_ueformat_bridge = getattr(core_utils, "get_ueformat_bridge", None)
build_model_option_kwargs = getattr(core_utils, "build_model_option_kwargs", None)


class _LegacyUEFormatCapabilities:
    supports_fmodel_materials = False
    supports_fmodel_textures = False
    supports_uescene = False
    supports_strict_skeleton = False


class _LegacyUEFormatBridge:
    """Small compatibility shim for hosts exposing only the pre-V2 tuple API."""

    is_current = False
    capabilities = _LegacyUEFormatCapabilities()

    def __init__(self, import_class, model_options, anim_options, pose_options):
        self.UEFormatImport = import_class
        self.UEModelOptions = model_options
        self.UEAnimOptions = anim_options
        self.UEPoseOptions = pose_options


def _resolve_model_import_contract():
    """Resolve the current bridge, or explicitly fall back to the old tuple API."""

    resolver = globals().get("get_ueformat_bridge")
    if callable(resolver):
        # The canonical addon is required whenever the capability-aware bridge is
        # available; do not silently downgrade a real current host to legacy code.
        return resolver(require_current=True, allow_legacy=False, capability="model")

    legacy_resolver = globals().get("get_ueformat_api")
    if not callable(legacy_resolver):
        return None
    api = legacy_resolver()
    if not api or len(api) < 2:
        return None
    import_class, model_options, anim_options, pose_options = tuple(api)[:4]
    if not import_class or not model_options:
        return None
    return _LegacyUEFormatBridge(
        import_class, model_options, anim_options, pose_options
    )


def _legacy_model_option_kwargs(operator):
    return {
        "scale_factor": operator.scale_factor,
        "bone_length": operator.bone_length,
        "reorient_bones": operator.reorient_bones,
        "target_lod": operator.target_lod,
        "import_collision": operator.import_collision,
        "import_morph_targets": operator.import_morph_targets,
        "import_sockets": operator.import_sockets,
        "import_virtual_bones": operator.import_virtual_bones,
    }


class MODEL_OT_ImportUEModel(bpy.types.Operator, ImportHelper):
    """导入 UEModel 模型"""
    bl_idname = "fmodel.import_model_uemodel"
    bl_label = "导入 UEModel"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".uemodel"
    filter_glob: bpy.props.StringProperty(default="*.uemodel", options={'HIDDEN'}) # type: ignore

    scale_factor: bpy.props.FloatProperty(name="模型缩放 (Scale)", default=0.01, min=0.001) # type: ignore
    target_lod: bpy.props.IntProperty(name="导入 LOD 层级", default=0, min=0) # type: ignore
    import_collision: bpy.props.BoolProperty(name="导入碰撞体", default=False) # type: ignore
    import_morph_targets: bpy.props.BoolProperty(name="导入形态键 (Morph)", default=True) # type: ignore
    import_sockets: bpy.props.BoolProperty(name="导入插槽 (Sockets)", default=True) # type: ignore
    import_virtual_bones: bpy.props.BoolProperty(name="导入虚拟骨骼", default=False) # type: ignore
    reorient_bones: bpy.props.BoolProperty(name="重定向骨骼轴向", default=False) # type: ignore
    bone_length: bpy.props.FloatProperty(name="骨骼显示长度", default=4.0, min=0.1) # type: ignore

    auto_link_mat: bpy.props.BoolProperty(
        name="启用 Legacy 材质后处理",
        description="canonical FModel 材质由 UEFormat importer 独立处理；此项只控制旧 JSON/启发式材质 fallback",
        default=True,
    ) # type: ignore
    
    auto_backup: bpy.props.BoolProperty(
        name="导入前自动备份",
        description="在导入前自动备份当前工程",
        default=True
    ) # type: ignore

    def draw(self, context):
        layout = self.layout
        
        box = layout.box()
        box.label(text="模型配置 (Model)", icon="OUTLINER_OB_MESH")
        box.prop(self, "scale_factor")
        box.prop(self, "target_lod")
        box.prop(self, "import_collision")
        box.prop(self, "import_morph_targets")

        box_bone = layout.box()
        box_bone.label(text="骨架配置 (Skeleton)", icon="ARMATURE_DATA")
        box_bone.prop(self, "bone_length")
        box_bone.prop(self, "reorient_bones")
        box_bone.prop(self, "import_sockets")
        box_bone.prop(self, "import_virtual_bones")

        box_mat = layout.box()
        box_mat.label(text="材质后处理", icon="MATERIAL")
        box_mat.prop(self, "auto_link_mat")
        box_mat.prop(self, "auto_backup")

    def execute(self, context):
        safe_path = os.path.normpath(self.filepath)
        
        # 记录操作开始
        log_info(
            OperationType.IMPORT_MODEL,
            f"开始导入 UEModel: {os.path.basename(safe_path)}",
            {
                "file_path": safe_path,
                "scale": self.scale_factor,
                "lod": self.target_lod,
                "morph": self.import_morph_targets
            }
        )
        
        # 操作前备份
        if self.auto_backup:
            backup_before("UEModel导入", [safe_path])
        
        try:
            bridge = _resolve_model_import_contract()
        except Exception as exc:
            log_error(OperationType.IMPORT_MODEL, f"当前 UEFormat addon 能力不足: {exc}")
            self.report({'ERROR'}, "需要 UEFormat addon 1.1.4+（含 FModel 材质和纹理能力）")
            return {'CANCELLED'}
        if bridge is None:
            log_error(OperationType.IMPORT_MODEL, "未检测到当前 UEFormat 插件")
            self.report({'ERROR'}, "未检测到 UEFormat 插件！")
            return {'CANCELLED'}

        if bridge.is_current and callable(globals().get("build_model_option_kwargs")):
            option_kwargs = build_model_option_kwargs(
                scale_factor=self.scale_factor,
                bone_length=self.bone_length,
                reorient_bones=self.reorient_bones,
                target_lod=self.target_lod,
                import_collision=self.import_collision,
                import_morph_targets=self.import_morph_targets,
                import_sockets=self.import_sockets,
                import_virtual_bones=self.import_virtual_bones,
                model_path=safe_path,
            )
        else:
            option_kwargs = _legacy_model_option_kwargs(self)
        options = bridge.UEModelOptions(**option_kwargs)
        importer = bridge.UEFormatImport(options)
        
        try:
            result = importer.import_file(safe_path)
        except Exception as e:
            log_error(OperationType.IMPORT_MODEL, f"UEModel 导入失败: {e}", {"file": safe_path})
            self.report({'ERROR'}, f"UEModel 导入失败: {e}")
            return {'CANCELLED'}
            
        imported_obj = result[0] if isinstance(result, tuple) else result
        if not imported_obj:
            log_error(OperationType.IMPORT_MODEL, "导入返回空对象")
            return {'CANCELLED'}
        
        arm_obj = None
        if imported_obj.type == 'ARMATURE':
            arm_obj = imported_obj
        elif imported_obj.parent and imported_obj.parent.type == 'ARMATURE':
            arm_obj = imported_obj.parent

        if arm_obj:
            try:
                from ....Assets.Utils import utils_asset 
                json_path = os.path.splitext(safe_path)[0] + ".json"
                ue_sk_name = utils_asset.get_skeleton_name_from_json(json_path)
                if ue_sk_name:
                    arm_obj["FModel_Skeleton"] = ue_sk_name
            except Exception as e:
                print(f"[FModel] 骨架真实属性写入失败: {e}")
                # 使用日志系统
                log_error("IMPORT_MODEL", f"骨架真实属性写入失败: {e}")
            
        meshes = []
        if imported_obj.type == 'MESH': meshes.append(imported_obj)
        elif imported_obj.type == 'ARMATURE':
            meshes.extend([child for child in imported_obj.children if child.type == 'MESH'])
                    
        for obj in meshes: obj["gltf_import_path"] = safe_path

        # Use the importer result, never context.active_object, for auto setup.
        prepare_imported_armature_for_animation(context, arm_obj)
            
        if self.auto_link_mat and not bridge.capabilities.supports_fmodel_materials:
            try:
                mat_module.auto_resolve_materials_for_import(meshes, safe_path)
            except Exception as e:
                print(f"[FModel] legacy JSON 材质解析失败: {e}")
            log_success(
                OperationType.IMPORT_MODEL,
                f"UEModel 导入成功",
                {"object": imported_obj.name, "meshes": len(meshes)}
            )
            self.report({'INFO'}, "UEModel 导入成功！")
        elif self.auto_link_mat and bridge.capabilities.supports_fmodel_materials:
            log_success(
                OperationType.IMPORT_MODEL,
                "UEModel 导入成功（canonical FModel 材质已由 UEFormat importer 处理）",
                {"object": imported_obj.name, "meshes": len(meshes)},
            )
            self.report({'INFO'}, "UEModel 导入成功（FModel 材质已处理）")
        else:
            log_success(
                OperationType.IMPORT_MODEL,
                f"UEModel 导入成功",
                {"object": imported_obj.name}
            )
            self.report({'INFO'}, "UEModel 模型导入成功！")
            
        return {'FINISHED'}


def register():
    bpy.utils.register_class(MODEL_OT_ImportUEModel)


def unregister():
    bpy.utils.unregister_class(MODEL_OT_ImportUEModel)
