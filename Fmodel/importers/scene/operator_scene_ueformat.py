"""V2 scene entry point backed by the canonical UEFormat UEScene operator."""

from __future__ import annotations

import json
import os

import bpy
from bpy_extras.io_utils import ImportHelper

from ....core import (
    OperationType,
    backup_before,
    log_error,
    log_info,
    log_success,
)
from ....core.utils import (
    build_uescene_settings_kwargs,
    get_ueformat_bridge,
    parse_uescene_import_status,
)


class SCENE_OT_ImportUEScene(bpy.types.Operator, ImportHelper):
    """导入 UEScene；读取和场景构建由 canonical UEFormat addon 负责。"""

    bl_idname = "fmodel.import_uescene"
    bl_label = "导入 UE Scene"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".uescene"
    filter_glob: bpy.props.StringProperty(default="*.uescene", options={"HIDDEN"})  # type: ignore

    bundle_root: bpy.props.StringProperty(  # type: ignore
        name="Bundle 根目录",
        description="可选；为空时按 Game/Content 目录规则推导",
        subtype="DIR_PATH",
        default="",
    )
    strict_required_models: bpy.props.BoolProperty(  # type: ignore
        name="严格检查 Required Models",
        description="缺失场景依赖模型时让 canonical UEFormat 报告失败",
        default=True,
    )
    flatten_hierarchy: bpy.props.BoolProperty(  # type: ignore
        name="导入后扁平化层级",
        description="沿用 canonical UEScene hierarchy 选项",
        default=True,
    )
    flatten_instances: bpy.props.BoolProperty(  # type: ignore
        name="展开场景实例",
        description="沿用 canonical UEScene instance 选项",
        default=False,
    )
    import_fmodel_materials: bpy.props.BoolProperty(  # type: ignore
        name="导入 FModel 材质",
        default=True,
    )
    build_fmodel_material_nodes: bpy.props.BoolProperty(  # type: ignore
        name="构建材质节点",
        default=True,
    )
    load_fmodel_embedded_textures: bpy.props.BoolProperty(  # type: ignore
        name="加载内嵌贴图",
        default=True,
    )
    load_fmodel_external_textures: bpy.props.BoolProperty(  # type: ignore
        name="加载外部贴图",
        default=True,
    )
    keep_fmodel_unmapped_parameters: bpy.props.BoolProperty(  # type: ignore
        name="保留未映射材质参数",
        default=True,
    )
    auto_backup: bpy.props.BoolProperty(  # type: ignore
        name="导入前自动备份",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        scene_box = layout.box()
        scene_box.label(text="UEScene 配置", icon="SCENE_DATA")
        scene_box.prop(self, "bundle_root")
        scene_box.prop(self, "strict_required_models")
        scene_box.prop(self, "flatten_hierarchy")
        scene_box.prop(self, "flatten_instances")

        material_box = layout.box()
        material_box.label(text="FModel 材质配置", icon="MATERIAL")
        material_box.prop(self, "import_fmodel_materials")
        material_box.prop(self, "build_fmodel_material_nodes")
        material_box.prop(self, "load_fmodel_embedded_textures")
        material_box.prop(self, "load_fmodel_external_textures")
        material_box.prop(self, "keep_fmodel_unmapped_parameters")
        layout.prop(self, "auto_backup")

    @staticmethod
    def _set_settings(settings, values):
        for key, value in values.items():
            if hasattr(settings, key):
                setattr(settings, key, value)

    @staticmethod
    def _status_message(status):
        failures = status.get("required_model_failures") or []
        if not failures:
            return ""
        first = failures[0]
        if isinstance(first, dict):
            detail = first.get("detail") or first.get("path") or first.get("uri")
            if detail:
                return f"；首个 required model 失败: {detail}"
        return f"；required model 失败数: {len(failures)}"

    def execute(self, context):
        safe_path = os.path.normpath(self.filepath)
        log_info(
            OperationType.PIPELINE,
            f"开始导入 UEScene: {os.path.basename(safe_path)}",
            {"file_path": safe_path, "strict_required_models": self.strict_required_models},
        )
        if self.auto_backup:
            backup_before("UEScene导入", [safe_path])

        try:
            bridge = get_ueformat_bridge(require_current=True, allow_legacy=False)
        except Exception as exc:
            log_error(OperationType.PIPELINE, f"当前 UEFormat addon 不支持 UEScene: {exc}")
            self.report({"ERROR"}, "需要 UEFormat addon 1.1.4+（含 UEScene 能力）")
            return {"CANCELLED"}
        if bridge is None or not bridge.capabilities.supports_uescene:
            log_error(OperationType.PIPELINE, "未检测到 canonical UEScene 能力")
            self.report({"ERROR"}, "未检测到 canonical UEScene importer！")
            return {"CANCELLED"}

        settings = getattr(getattr(context, "scene", None), "uf_settings", None)
        if settings is None:
            message = "canonical UEFormat addon 已发现，但 UFSettings 尚未注册"
            log_error(OperationType.PIPELINE, message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        values = build_uescene_settings_kwargs(
            scene_path=safe_path,
            bundle_root=self.bundle_root,
            strict_required_models=self.strict_required_models,
            flatten_hierarchy=self.flatten_hierarchy,
            flatten_instances=self.flatten_instances,
        )
        values.update({
            "import_fmodel_materials": self.import_fmodel_materials,
            "build_fmodel_material_nodes": self.build_fmodel_material_nodes,
            "load_fmodel_embedded_textures": self.load_fmodel_embedded_textures,
            "load_fmodel_external_textures": self.load_fmodel_external_textures,
            "keep_fmodel_unmapped_parameters": self.keep_fmodel_unmapped_parameters,
        })
        self._set_settings(settings, values)

        uf_ops = getattr(bpy.ops, "uf", None)
        scene_import = getattr(uf_ops, "import_uescene", None)
        if scene_import is None:
            message = "canonical UFImportUEScene 已发现，但 bpy.ops.uf.import_uescene 未注册"
            log_error(OperationType.PIPELINE, message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        try:
            result = scene_import(filepath=safe_path)
        except Exception as exc:
            log_error(OperationType.PIPELINE, f"UEScene 导入调用失败: {exc}", {"file": safe_path})
            self.report({"ERROR"}, f"UEScene 导入失败: {exc}")
            return {"CANCELLED"}

        raw_status = getattr(settings, "last_import_status", "")
        status = parse_uescene_import_status(raw_status)
        report_text = getattr(settings, "last_import_report", "")
        try:
            context.scene["FModel_UEFormat_Scene_Report"] = report_text
            context.scene["FModel_UEFormat_Scene_Status"] = json.dumps(status, ensure_ascii=False)
        except Exception:
            pass

        result_set = set(result or ())
        outcome = status.get("outcome", "unknown")
        failure_suffix = self._status_message(status)
        if "FINISHED" in result_set and outcome not in {"failed", "error"}:
            if outcome == "partial":
                message = f"UEScene 部分导入完成{failure_suffix}"
                self.report({"WARNING"}, message)
                log_success(OperationType.PIPELINE, message, {"file": safe_path, "outcome": outcome})
            else:
                message = "UEScene 导入成功"
                self.report({"INFO"}, message)
                log_success(OperationType.PIPELINE, message, {"file": safe_path, "outcome": outcome})
            return {"FINISHED"}

        message = f"UEScene 导入失败{failure_suffix}"
        log_error(OperationType.PIPELINE, message, {"file": safe_path, "report": report_text})
        self.report({"ERROR"}, message)
        return {"CANCELLED"}


def register():
    bpy.utils.register_class(SCENE_OT_ImportUEScene)


def unregister():
    bpy.utils.unregister_class(SCENE_OT_ImportUEScene)
