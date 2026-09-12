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


def _canonical_armature_skeleton_metadata(armature):
    """Return canonical identity metadata without requiring a JSON sidecar."""
    try:
        from ....core import skeleton_provenance_from_owner
    except (ImportError, AttributeError):
        return None
    provenance = skeleton_provenance_from_owner(armature)
    if not provenance.is_strict:
        return None
    identity = provenance.identity or {}
    skeleton_path = str(identity.get("skeleton_path") or "")
    if not skeleton_path:
        return None
    display_name = skeleton_path.rsplit("/", 1)[-1] or skeleton_path
    return {
        "display_name": display_name,
        "skeleton_path": skeleton_path,
        "fingerprint": provenance.fingerprint,
        "identity": identity,
    }


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

    @staticmethod
    def _begin_transaction():
        try:
            from ....core.import_transaction import ImportTransaction
            return ImportTransaction(getattr(bpy, "data", None)).begin()
        except Exception:
            # Compatibility/fake hosts may not expose the transaction module. The
            # canonical Blender package always does; legacy hosts remain usable.
            return None

    @staticmethod
    def _rollback_transaction(transaction):
        if transaction is None:
            return ""
        try:
            report = transaction.rollback()
            parts = [f"removed {report.removed_count} datablock(s)"]
            if report.retained_count:
                parts.append(f"retained {report.retained_count} datablock(s)")
            if report.errors:
                parts.append(f"rollback errors: {'; '.join(report.errors)}")
            return ", ".join(parts)
        except Exception as exc:
            return f"rollback raised: {exc}"

    @staticmethod
    def _make_session(path):
        try:
            from ....core.import_session import ImportSession
            return ImportSession("UEModel", source_paths=(path,))
        except Exception:
            return None

    @staticmethod
    def _persist_session(context, session) -> None:
        if session is None:
            return
        try:
            from ....core.import_session import persist_import_session
            persist_import_session(context.scene, session)
        except Exception:
            pass

    @staticmethod
    def _set_session_vendor_version(session, bridge) -> None:
        if session is None:
            return
        capabilities = getattr(bridge, "capabilities", None)
        session.vendor_version = str(getattr(capabilities, "version_text", "") or "")

    @staticmethod
    def _make_importer(bridge, options):
        """Construct the importer and inject the optional creation capture.

        The capture is a V10 additive API. Legacy tuple-resolved importers and
        test doubles do not expose it and continue through the one-argument
        constructor unchanged.
        """
        logic_module = getattr(bridge, "logic_module", None)
        capture_type = getattr(logic_module, "UEFormatImportCapture", None)
        if not callable(capture_type):
            return bridge.UEFormatImport(options), None

        capture = capture_type()
        try:
            importer = bridge.UEFormatImport(
                options,
                creation_tracker=capture,
            )
        except TypeError as error:
            if "creation_tracker" not in str(error):
                raise
            return bridge.UEFormatImport(options), None
        return importer, capture

    scale_factor: bpy.props.FloatProperty(name="模型缩放 (Scale)", default=0.01, min=0.001) # type: ignore
    target_lod: bpy.props.IntProperty(name="导入 LOD 层级", default=0, min=0) # type: ignore
    import_collision: bpy.props.BoolProperty(name="导入碰撞体", default=False) # type: ignore
    import_morph_targets: bpy.props.BoolProperty(name="导入形态键 (Morph)", default=True) # type: ignore
    import_sockets: bpy.props.BoolProperty(name="导入插槽 (Sockets)", default=True) # type: ignore
    import_virtual_bones: bpy.props.BoolProperty(name="导入虚拟骨骼", default=False) # type: ignore
    reorient_bones: bpy.props.BoolProperty(name="重定向骨骼轴向", default=False) # type: ignore
    bone_length: bpy.props.FloatProperty(name="骨骼显示长度", default=4.0, min=0.1) # type: ignore

    auto_link_mat: bpy.props.BoolProperty(
        name="启用旧版材质后处理",
        description="标准 FModel 材质由 UEFormat 导入器独立处理；此项仅控制旧版 JSON/启发式材质回退",
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
        box.label(text="模型配置", icon="OUTLINER_OB_MESH")
        box.prop(self, "scale_factor")
        box.prop(self, "target_lod")
        box.prop(self, "import_collision")
        box.prop(self, "import_morph_targets")

        box_bone = layout.box()
        box_bone.label(text="骨架配置", icon="ARMATURE_DATA")
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
        session = self._make_session(safe_path)
        if session is not None:
            session.set_phase("resolve_bridge")
        
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
            if session is not None:
                session.fail_all(phase="resolve_bridge", message=str(exc), code="ueformat_capability")
                self._persist_session(context, session)
            log_error(OperationType.IMPORT_MODEL, f"当前 UEFormat addon 能力不足: {exc}")
            self.report({'ERROR'}, "需要 UEFormat addon 0.10.0+（含 FModel 材质和纹理能力）")
            return {'CANCELLED'}
        if bridge is None:
            if session is not None:
                session.fail_all(phase="resolve_bridge", message="UEFormat importer is unavailable", code="ueformat_missing")
                self._persist_session(context, session)
            log_error(OperationType.IMPORT_MODEL, "未检测到当前 UEFormat 插件")
            self.report({'ERROR'}, "未检测到 UEFormat 插件！")
            return {'CANCELLED'}
        self._set_session_vendor_version(session, bridge)

        # Model/material imports only require the model capability.  The full
        # bridge also includes animation and pose support; tying this branch
        # to ``bridge.is_current`` would silently drop the canonical
        # FModel material/texture options when a host is model-capable but does
        # not yet expose every other capability.
        if (
            getattr(getattr(bridge, "capabilities", None), "supports_current_model", False)
            and callable(globals().get("build_model_option_kwargs"))
        ):
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
        if session is not None:
            session.set_phase("build_options")
        try:
            options = bridge.UEModelOptions(**option_kwargs)
            importer, creation_capture = self._make_importer(bridge, options)
        except Exception as exc:
            if session is not None:
                session.fail_all(phase="build_options", message=str(exc), code="ueformat_options_invalid")
                self._persist_session(context, session)
            self.report({'ERROR'}, f"UEModel 导入选项无效: {exc}")
            return {'CANCELLED'}
        
        transaction = self._begin_transaction()
        if session is not None:
            session.set_phase("import_model")
        try:
            result = importer.import_file(safe_path)
            imported_obj = result[0] if isinstance(result, tuple) else result
            if not imported_obj:
                raise ValueError("UEModel importer returned no object")
        
            arm_obj = None
            if imported_obj.type == 'ARMATURE':
                arm_obj = imported_obj
            elif imported_obj.parent and imported_obj.parent.type == 'ARMATURE':
                arm_obj = imported_obj.parent

            if arm_obj:
                canonical_metadata = _canonical_armature_skeleton_metadata(arm_obj)
                if canonical_metadata:
                    # The binary UEFormat identity is authoritative. Keep a stable
                    # display alias for old UI/pipeline code, while retaining the
                    # logical path and fingerprint for strict matching.
                    if not arm_obj.get("FModel_Skeleton", ""):
                        arm_obj["FModel_Skeleton"] = canonical_metadata["display_name"]
                    arm_obj["FModel_Skeleton_Path"] = canonical_metadata["skeleton_path"]
                    arm_obj["FModel_Skeleton_Identity_Fingerprint"] = canonical_metadata["fingerprint"]
                else:
                    # Legacy exports may not carry the identity chunk. JSON remains
                    # an optional compatibility fallback, never a native requirement.
                    try:
                        from ....Assets.Utils import utils_asset
                        json_path = os.path.splitext(safe_path)[0] + ".json"
                        ue_sk_name = utils_asset.get_skeleton_name_from_json(json_path)
                        if ue_sk_name:
                            arm_obj["FModel_Skeleton"] = ue_sk_name
                    except Exception as e:
                        print(f"[FModel] legacy skeleton JSON fallback failed: {e}")
                        log_error("IMPORT_MODEL", f"legacy skeleton JSON fallback failed: {e}")
            
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
            created_counts = (
                transaction.commit()
                if transaction is not None
                else creation_capture.counts if creation_capture is not None else {}
            )
            if session is not None:
                if created_counts:
                    for kind, count in created_counts.items():
                        session.record_created(kind, count)
                else:
                    session.record_created("objects", 1)
                    session.record_created("meshes", len(meshes))
                    if arm_obj is not None:
                        session.record_created("armatures", 1)
                session.record_success(safe_path)
                session.finish()
                self._persist_session(context, session)
            return {'FINISHED'}
        except Exception as e:
            rollback_message = self._rollback_transaction(transaction)
            details = {"file": safe_path}
            if rollback_message:
                details["rollback"] = rollback_message
            if session is not None:
                session.record_failure(
                    safe_path,
                    phase="import_model",
                    message=str(e),
                    code="uemodel_import_failed",
                    detail=details,
                )
                if rollback_message:
                    session.record_warning(rollback_message)
                session.finish()
                self._persist_session(context, session)
            log_error(OperationType.IMPORT_MODEL, f"UEModel 导入失败: {e}", details)
            message = f"UEModel 导入失败: {e}"
            if rollback_message:
                message += f"（{rollback_message}）"
            self.report({'ERROR'}, message)
            return {'CANCELLED'}


def register():
    bpy.utils.register_class(MODEL_OT_ImportUEModel)


def unregister():
    bpy.utils.unregister_class(MODEL_OT_ImportUEModel)
