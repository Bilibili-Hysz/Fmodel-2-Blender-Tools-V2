# FModel_Tools/Universal_tools/material/shader/operators.py
# 着色器操作符 — 所有 MAT_OT_* 操作符类

import json
import os
import bpy

from ....core.i18n import t
from ..shader_manager import (
    STANDARD_INTERFACES,
    get_active_node_group,
    iter_node_group_inputs, _socket_ident, _socket_is_internal,
    _get_socket_mapping, _set_socket_mapping, _load_mapping_dict, _save_mapping_dict,
    _get_mapped_interface_name, _resolve_socket_mapping,
    _build_preset_from_mapping,
    _load_json, _save_json,
    import_node_group_from_blend, save_node_group_to_blend,
    register_custom_shader, remove_custom_shader_entry,
    _apply_node_group_to_active, _auto_map_on_import,
    list_default_shaders,
    _SHADERS_BLEND, _SHADERS_JSON, _CUSTOM_BLEND, _CUSTOM_JSON, _EXPORTS_DIR,
)
from ..presets import register_preset, unregister_preset
from ..presets.user_custom import create_custom_preset_class

# ============================================================
# 操作符: 映射接口并注册
# ============================================================

class MAT_OT_MapNodeGroupInterfaces(bpy.types.Operator):
    """使用当前接口映射注册预设"""
    bl_idname = "fmodel.map_nodegroup_interfaces"
    bl_label = "映射接口并注册预设"
    bl_options = {'REGISTER', 'UNDO'}
    node_group_name: bpy.props.StringProperty()
    preset_label: bpy.props.StringProperty(name="预设名称", default="")
    preset_category: bpy.props.EnumProperty(
        items=[("PBR","PBR",""),("NPR","NPR",""),("Hybrid","混合","")], default="Hybrid")

    def execute(self, context):
        ng = bpy.data.node_groups.get(self.node_group_name)
        if not ng:
            return {'CANCELLED'}
        smp = getattr(context.scene, "fmodel_shader_manager", None)
        socket_mapping = _resolve_socket_mapping(ng, smp)
        mapped = sum(1 for v in socket_mapping.values() if v not in ("__NONE__", "__CUSTOM__"))
        if mapped == 0:
            self.report({'WARNING'}, t("请先在接口列表中将至少一个 socket 映射到标准接口"))
            return {'CANCELLED'}
        profile = _build_preset_from_mapping(ng, socket_mapping,
            preset_label=self.preset_label or f"Custom: {ng.name}",
            preset_category=self.preset_category)
        cls = create_custom_preset_class(profile)
        unregister_preset(cls.PRESET_ID)
        register_preset(cls)
        self.report({'INFO'}, f"已注册: {cls.PRESET_LABEL} ({mapped} 接口) → 3D View N面板 材质工具中可用")
        return {'FINISHED'}


# ============================================================
# 操作符: 添加/移除自定义接口
# ============================================================

class MAT_OT_AddCustomInterface(bpy.types.Operator):
    bl_idname = "fmodel.add_custom_interface"
    bl_label = "添加自定义接口"
    bl_options = {'REGISTER', 'UNDO'}

    interface_id: bpy.props.StringProperty(name="ID", default="custom")
    label: bpy.props.StringProperty(name="标签", default="Custom")
    match_suffixes: bpy.props.StringProperty(name="后缀", default="_custom", description="逗号分隔")
    colorspace: bpy.props.EnumProperty(items=[("sRGB","sRGB",""),("Non-Color","Non-Color","")], default="Non-Color")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def execute(self, context):
        props = context.scene.fmodel_shader_manager
        item = props.custom_interfaces.add()
        item.interface_id = self.interface_id.strip() or f"custom_{len(props.custom_interfaces)}"
        item.label = self.label.strip() or item.interface_id
        item.match_suffixes = self.match_suffixes.strip()
        item.colorspace = self.colorspace
        self.report({'INFO'}, f"已添加自定义接口: {item.label}")
        return {'FINISHED'}


class MAT_OT_RemoveCustomInterface(bpy.types.Operator):
    bl_idname = "fmodel.remove_custom_interface"
    bl_label = "移除自定义接口"
    bl_options = {'REGISTER', 'UNDO'}
    index: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.fmodel_shader_manager
        if 0 <= self.index < len(props.custom_interfaces):
            name = props.custom_interfaces[self.index].label
            props.custom_interfaces.remove(self.index)
            props.active_custom_index = max(0, min(props.active_custom_index, len(props.custom_interfaces) - 1))
            self.report({'INFO'}, f"已移除: {name}")
        return {'FINISHED'}


# ============================================================
# 操作符: 一键创建空白着色器 + 材质
# ============================================================

# ── Path constants, _load_shader_list, _ensure_data_dirs, SHADER_MT_* menus
#     (shared with shader_manager.py + shader/panel.py; defined there)







class MAT_OT_CreateBlankShader(bpy.types.Operator):
    """从 shaders.blend 导入默认模板或创建全新空白着色器"""
    bl_idname = "fmodel.create_blank_shader"
    bl_label = "新建着色器"
    bl_options = {'REGISTER', 'UNDO'}
    template: bpy.props.EnumProperty(
        name="模板",
        description="从默认模板库导入，选「空白」则为空节点组",
        items=lambda s, c: [("", "空白 (空节点组)", "")] + [
            (d["name"], d["label"], d.get("description", "")) for d in list_default_shaders()
        ],
    )
    assign_to_active: bpy.props.BoolProperty(name="应用到选中物体", default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def execute(self, context):
        if self.template:
            if not import_node_group_from_blend(_SHADERS_BLEND, self.template):
                self.report({'ERROR'}, f"导入失败: {self.template}")
                return {'CANCELLED'}
            ng = bpy.data.node_groups.get(self.template)
            name = self.template
        else:
            name = "FModel_NewShader"
            ng = bpy.data.node_groups.new(name=name, type="ShaderNodeTree")
            ng.use_fake_user = True
            ng.interface.new_socket(name="Shader", socket_type="NodeSocketShader", in_out="OUTPUT",
                                     description="着色器输出")
            nn = ng.nodes; nn.clear()
            nn.new("NodeGroupInput").location = (-200, 0)
            nn.new("NodeGroupOutput").location = (200, 0)

        if ng and self.assign_to_active:
            _apply_node_group_to_active(context, ng, name)

        self.report({'INFO'}, f"已创建: {name}")
        return {'FINISHED'}


# ============================================================
# 操作符: 管理节点组接口（增删改查 socket）
# ============================================================
# 操作符: 添加/移除自定义接口
# ============================================================
# 操作符: 导出/导入预设 JSON
# ============================================================

class MAT_OT_ExportNodeGroupPreset(bpy.types.Operator):
    """将当前映射导出为 preset JSON 文件"""
    bl_idname = "fmodel.export_nodegroup_preset"
    bl_label = "导出预设JSON"
    bl_options = {'REGISTER', 'UNDO'}

    node_group_name: bpy.props.StringProperty()
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')

    def invoke(self, context, event):
        ng = bpy.data.node_groups.get(self.node_group_name)
        if ng:
            self.filepath = f"//{ng.name}_preset.json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        ng = bpy.data.node_groups.get(self.node_group_name)
        if not ng:
            return {'CANCELLED'}

        smp = getattr(context.scene, "fmodel_shader_manager", None)
        socket_mapping = _resolve_socket_mapping(ng, smp)

        profile = _build_preset_from_mapping(ng, socket_mapping, preset_label=ng.name)
        abs_path = bpy.path.abspath(self.filepath)
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        self.report({'INFO'}, f"已导出: {abs_path}")
        return {'FINISHED'}


class MAT_OT_LoadPresetShaderEditor(bpy.types.Operator):
    """在着色器编辑器中加载预设 JSON"""
    bl_idname = "fmodel.load_preset_shader_editor"
    bl_label = "加载预设JSON"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from ..presets.user_custom import load_preset_from_json
        if not self.filepath.endswith('.json'):
            self.report({'ERROR'}, t("请选择 .json 文件"))
            return {'CANCELLED'}
        try:
            cls = load_preset_from_json(self.filepath)
            unregister_preset(cls.PRESET_ID)
            register_preset(cls)
            # 尝试恢复 socket_mappings 到面板
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                socket_mappings = raw.get("socket_mappings", {})
                if socket_mappings and hasattr(context.scene, "fmodel_shader_manager"):
                    smp = context.scene.fmodel_shader_manager
                    ng = get_active_node_group()
                    ng_name = ng.name if ng else "__legacy__"
                    data = _load_mapping_dict(smp, ng_name)
                    data.update(socket_mappings)
                    _save_mapping_dict(smp, ng_name, data)
            except Exception:
                pass
            self.report({'INFO'}, f"已加载: {cls.PRESET_LABEL}")
        except Exception as e:
            self.report({'ERROR'}, str(e))
        return {'FINISHED'}


# ============================================================
# 操作符: 直接注册节点组为预设（使用自动匹配）
# ============================================================

class MAT_OT_QuickRegisterNodeGroup(bpy.types.Operator):
    """快速注册：自动按 socket 名匹配标准接口并注册为预设"""
    bl_idname = "fmodel.quick_register_nodegroup"
    bl_label = "注册"
    bl_options = {'REGISTER', 'UNDO'}

    node_group_name: bpy.props.StringProperty()

    def execute(self, context):
        ng = bpy.data.node_groups.get(self.node_group_name)
        if not ng:
            return {'CANCELLED'}

        smp = getattr(context.scene, "fmodel_shader_manager", None)
        socket_mapping = _resolve_socket_mapping(ng, smp)

        mapped = sum(1 for v in socket_mapping.values() if v not in ("__NONE__", "__CUSTOM__"))
        profile = _build_preset_from_mapping(ng, socket_mapping, preset_label=ng.name)
        cls = create_custom_preset_class(profile)
        unregister_preset(cls.PRESET_ID)
        register_preset(cls)
        self.report({'INFO'}, f"已注册: {cls.PRESET_LABEL} ({mapped} 接口)")
        return {'FINISHED'}


class MAT_OT_PreviewPreset(bpy.types.Operator):
    """预览当前节点组的预设JSON"""
    bl_idname = "fmodel.preview_preset"
    bl_label = "预览预设"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_node_group() is not None

    def execute(self, context):
        ng = get_active_node_group()
        if not ng:
            return {'CANCELLED'}
        smp = getattr(context.scene, "fmodel_shader_manager", None)
        socket_mapping = _resolve_socket_mapping(ng, smp)
        profile = _build_preset_from_mapping(ng, socket_mapping, preset_label=ng.name)
        preset_json = json.dumps(profile, indent=2, ensure_ascii=False)
        text_name = f"[FModel] {ng.name}_preset"
        text_block = bpy.data.texts.get(text_name)
        if not text_block:
            text_block = bpy.data.texts.new(text_name)
        text_block.clear()
        text_block.write(preset_json)
        self.report({'INFO'}, f"已写入文本编辑器: {text_name}")
        return {'FINISHED'}


def _get_mapping_enum_items(self, context):
    """从 STANDARD_INTERFACES 动态生成接口映射选项"""
    items = [("__NONE__", "(未映射)", "跳过此 socket")]
    for ch_id, info in STANDARD_INTERFACES.items():
        items.append((ch_id, info["label"], info["label"]))
    items.append(("__CUSTOM__", "自定义", "基于socket名自动生成"))
    return items


class MAT_OT_SetSocketMapping(bpy.types.Operator):
    """设置接口的标准映射"""
    bl_idname = "fmodel.set_socket_mapping"
    bl_label = "选择映射"
    bl_options = {'REGISTER', 'UNDO'}
    bl_property = "mapping"

    socket_name: bpy.props.StringProperty(options={'HIDDEN'})
    mapping: bpy.props.EnumProperty(
        name="映射到",
        items=_get_mapping_enum_items,
    )

    def execute(self, context):
        smp = context.scene.fmodel_shader_manager
        ng = get_active_node_group()
        if ng:
            _set_socket_mapping(smp, ng.name, self.socket_name, self.mapping)
        return {'FINISHED'}

    def invoke(self, context, event):
        smp = context.scene.fmodel_shader_manager
        ng = get_active_node_group()
        if ng:
            self.mapping = _get_socket_mapping(smp, ng.name, self.socket_name)
        return context.window_manager.invoke_props_dialog(self, width=240)


class MAT_OT_EditSocketDetail(bpy.types.Operator):
    """编辑接口的完整配置：映射、色彩空间、匹配后缀"""
    bl_idname = "fmodel.edit_socket_detail"
    bl_label = "接口详情"
    bl_options = {'REGISTER', 'UNDO'}

    socket_name: bpy.props.StringProperty(options={'HIDDEN'})
    mapping: bpy.props.EnumProperty(
        name="标准接口",
        items=[
            ("__NONE__", "(未映射)", ""),
            ("base_color", "Base Color", "基础颜色贴图"),
            ("normal", "Normal", "法线贴图"),
            ("roughness", "Roughness", "粗糙度"),
            ("metallic", "Metallic", "金属度"),
            ("ao", "AO", "环境遮蔽"),
            ("emission", "Emission", "自发光"),
            ("alpha", "Alpha", "透明度"),
            ("__CUSTOM__", "自定义", "基于socket名"),
        ],
    )
    colorspace: bpy.props.EnumProperty(
        name="色彩空间",
        items=[("sRGB", "sRGB", "颜色贴图"), ("Non-Color", "Non-Color", "数据贴图")],
    )

    def invoke(self, context, event):
        smp = context.scene.fmodel_shader_manager
        ng = get_active_node_group()
        if ng:
            self.mapping = _get_socket_mapping(smp, ng.name, self.socket_name)
        # 根据 socket 类型推断默认 colorspace
        if ng:
            for inp in iter_node_group_inputs(ng):
                if inp.name == self.socket_name:
                    stype = getattr(inp, "socket_type", "")
                    self.colorspace = "Non-Color" if stype != "NodeSocketColor" else "sRGB"
                    break
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"接口: {self.socket_name}", icon='NODE_SOCKET_RGBA')
        layout.separator()
        layout.prop(self, "mapping")
        # 根据当前映射推荐色彩空间
        if self.mapping and self.mapping != "__NONE__":
            std = STANDARD_INTERFACES.get(self.mapping)
            if std:
                recommended = std.get("colorspace", "sRGB")
                layout.label(text=f"推荐色彩空间: {recommended}", icon='COLOR')
        layout.prop(self, "colorspace")
        layout.separator()
        layout.label(text=t("映射和色彩空间将写入预设JSON"), icon='INFO')

    def execute(self, context):
        smp = context.scene.fmodel_shader_manager
        ng = get_active_node_group()
        if ng:
            _set_socket_mapping(smp, ng.name, self.socket_name, self.mapping)
        self.report({'INFO'}, f"已更新: {self.socket_name}")
        return {'FINISHED'}


# ============================================================
# 操作符: 内联接口管理
# ============================================================

class MAT_OT_SelectNodeGroup(bpy.types.Operator):
    """选中节点组以管理其接口"""
    bl_idname = "fmodel.select_nodegroup"
    bl_label = "选中"
    bl_options = {'REGISTER', 'UNDO'}
    node_group_name: bpy.props.StringProperty()

    def execute(self, context):
        props = context.scene.fmodel_shader_manager
        if props.active_node_group == self.node_group_name:
            props.active_node_group = ""
        else:
            props.active_node_group = self.node_group_name
        return {'FINISHED'}


class MAT_OT_AddSocketInline(bpy.types.Operator):
    """向活动节点组添加新接口"""
    bl_idname = "fmodel.add_socket_inline"
    bl_label = "添加接口"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.fmodel_shader_manager
        ng = get_active_node_group()
        if not ng:
            return {'CANCELLED'}
        name = props.new_socket_name.strip()
        if not name:
            self.report({'WARNING'}, t("请输入接口名称"))
            return {'CANCELLED'}
        if not hasattr(ng, "interface"):
            self.report({'WARNING'}, t("当前节点组不支持接口操作"))
            return {'CANCELLED'}
        ng.interface.new_socket(
            name=name, socket_type=props.new_socket_type, in_out="INPUT",
            description=props.new_socket_desc or f"{name} 输入",
        )
        self.report({'INFO'}, f"已添加: {name}")
        props.new_socket_name = "NewSocket"
        props.new_socket_desc = ""
        return {'FINISHED'}


class MAT_OT_RemoveSocketInline(bpy.types.Operator):
    """从活动节点组移除接口"""
    bl_idname = "fmodel.remove_socket_inline"
    bl_label = "移除接口"
    bl_options = {'REGISTER', 'UNDO'}
    socket_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return get_active_node_group() is not None

    def execute(self, context):
        ng = get_active_node_group()
        if not ng:
            return {'CANCELLED'}
        for item in getattr(getattr(ng, "interface", None), "items_tree", []):
            if getattr(item, "name", "") == self.socket_name and getattr(item, "in_out", "") == "INPUT":
                for node in ng.nodes:
                    if node.type == "GROUP_INPUT":
                        for output in node.outputs:
                            if output.name == self.socket_name:
                                for link in list(output.links):
                                    ng.links.remove(link)
                ng.interface.remove(item)
                self.report({'INFO'}, f"已移除: {self.socket_name}")
                return {'FINISHED'}
        self.report({'WARNING'}, f"未找到接口: {self.socket_name}")
        return {'CANCELLED'}


class MAT_OT_RenameSocketInline(bpy.types.Operator):
    """重命名活动节点组的接口"""
    bl_idname = "fmodel.rename_socket_inline"
    bl_label = "重命名接口"
    bl_options = {'REGISTER', 'UNDO'}
    old_name: bpy.props.StringProperty()
    new_name: bpy.props.StringProperty(name="新名称")

    def invoke(self, context, event):
        self.new_name = self.old_name
        return context.window_manager.invoke_props_dialog(self, width=280)

    def execute(self, context):
        ng = get_active_node_group()
        if not ng:
            return {'CANCELLED'}
        name = self.new_name.strip()
        if not name or name == self.old_name:
            return {'CANCELLED'}
        for item in getattr(getattr(ng, "interface", None), "items_tree", []):
            if getattr(item, "name", "") == self.old_name and getattr(item, "in_out", "") == "INPUT":
                item.name = name
                self.report({'INFO'}, f"已重命名: {self.old_name} → {name}")
                return {'FINISHED'}
        return {'CANCELLED'}


class MAT_OT_ImportDefaultShader(bpy.types.Operator):
    """从 shaders.blend 导入节点组模板并自动创建/赋给当前物体材质"""
    bl_idname = "fmodel.import_default_shader"
    bl_label = "导入默认着色器"
    bl_options = {'REGISTER', 'UNDO'}

    group_name: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type in ("MESH", "CURVE", "SURFACE")

    def execute(self, context):
        if not self.group_name:
            return {'CANCELLED'}
        imported = import_node_group_from_blend(_SHADERS_BLEND, self.group_name)
        source = "default"
        if not imported:
            imported = import_node_group_from_blend(_CUSTOM_BLEND, self.group_name)
            source = "custom"
        if not imported:
            self.report({'ERROR'}, f"导入失败: {self.group_name}")
            return {'CANCELLED'}
        ng = bpy.data.node_groups.get(self.group_name)
        if not ng:
            return {'CANCELLED'}
        _apply_node_group_to_active(context, ng, self.group_name)
        # Auto-populate interface mapping on first import (only if mapping is empty)
        smp = getattr(context.scene, "fmodel_shader_manager", None)
        if smp and (source in ("default", "custom")):
            _auto_map_on_import(smp, ng)
        self.report({'INFO'}, f"已导入: {self.group_name}")
        return {'FINISHED'}


class MAT_OT_SaveCustomShader(bpy.types.Operator):
    """将当前节点组保存为自定义着色器"""
    bl_idname = "fmodel.save_custom_shader"
    bl_label = "保存为自定义"
    bl_options = {'REGISTER', 'UNDO'}

    group_name: bpy.props.StringProperty(options={'HIDDEN'})
    label: bpy.props.StringProperty(name="名称")

    def invoke(self, context, event):
        ng = bpy.data.node_groups.get(self.group_name)
        if ng:
            self.label = ng.name
        return context.window_manager.invoke_props_dialog(self, width=300)

    def execute(self, context):
        if not self.label.strip():
            return {'CANCELLED'}
        if register_custom_shader(self.group_name, self.label.strip()):
            self.report({'INFO'}, f"已保存: {self.label}")
        else:
            self.report({'ERROR'}, t("保存失败"))
        return {'FINISHED'}


class MAT_OT_RemoveCustomShader(bpy.types.Operator):
    """从索引中移除自定义着色器"""
    bl_idname = "fmodel.remove_custom_shader"
    bl_label = "移除"
    bl_options = {'REGISTER', 'UNDO'}

    group_name: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        remove_custom_shader_entry(self.group_name)
        self.report({'INFO'}, f"已移除: {self.group_name}")
        return {'FINISHED'}


class MAT_OT_AutoMapSockets(bpy.types.Operator):
    """智能映射：仅填充未映射接口，不覆盖已有映射"""
    bl_idname = "fmodel.auto_map_sockets"
    bl_label = "智能映射"
    bl_options = {'REGISTER', 'UNDO'}

    node_group_name: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        ng = bpy.data.node_groups.get(self.node_group_name)
        if not ng:
            return {'CANCELLED'}
        smp = context.scene.fmodel_shader_manager
        socket_mapping = _resolve_socket_mapping(ng, smp)
        existing = _load_mapping_dict(smp, ng.name)
        mapped = 0
        for inp in iter_node_group_inputs(ng):
            if _socket_is_internal(inp):
                continue
            # Skip already-mapped sockets (preserve user edits)
            if existing.get(inp.name, "__NONE__") not in ("__NONE__",):
                continue
            ident = _socket_ident(inp)
            val = socket_mapping.get(ident, "__NONE__")
            if val not in ("__NONE__", "__CUSTOM__"):
                _set_socket_mapping(smp, ng.name, inp.name, val)
                mapped += 1
        self.report({'INFO'}, f"智能映射完成: {mapped} 个新接口已匹配")
        return {'FINISHED'}


class MAT_OT_AddDefaultShader(bpy.types.Operator):
    """将当前节点组添加为默认模板并保存到 shaders.blend + shaders.json"""
    bl_idname = "fmodel.add_default_shader"
    bl_label = "添加为默认模板"
    bl_options = {'REGISTER', 'UNDO'}

    group_name: bpy.props.StringProperty(options={'HIDDEN'})
    label: bpy.props.StringProperty(name="模板名称")
    category: bpy.props.EnumProperty(
        items=[("PBR","PBR",""),("NPR","NPR",""),("Hybrid","混合","")], default="Hybrid")

    def invoke(self, context, event):
        ng = bpy.data.node_groups.get(self.group_name)
        if ng:
            self.label = ng.name
        return context.window_manager.invoke_props_dialog(self, width=300)

    def execute(self, context):
        ng = bpy.data.node_groups.get(self.group_name)
        if not ng:
            return {'CANCELLED'}
        smp = getattr(context.scene, "fmodel_shader_manager", None)
        socket_mapping = _resolve_socket_mapping(ng, smp)
        profile = _build_preset_from_mapping(ng, socket_mapping, preset_label=self.label, preset_category=self.category)
        # 保存到 shaders.blend
        save_node_group_to_blend(_SHADERS_BLEND, [self.group_name])
        # 写入 shaders.json
        cfg = _load_json(_SHADERS_JSON)
        if "defaults" not in cfg:
            cfg["defaults"] = {}
        interfaces = {}
        for inp in iter_node_group_inputs(ng):
            if not _socket_is_internal(inp):
                mapped = socket_mapping.get(_socket_ident(inp), None)
                interfaces[inp.name] = mapped if mapped not in (None, "__NONE__", "__CUSTOM__") else None
        cfg["defaults"][self.group_name] = {
            "label": self.label.strip() or ng.name,
            "description": profile.get("preset_desc", f"默认模板: {ng.name}"),
            "category": self.category,
            "interfaces": interfaces,
        }
        _save_json(_SHADERS_JSON, cfg)
        self.report({'INFO'}, f"已添加默认模板: {self.label}")
        return {'FINISHED'}


class MAT_OT_ExportPresetToShaders(bpy.types.Operator):
    """将当前节点组的预设 JSON 导出到 Shaders 文件夹并打开目录"""
    bl_idname = "fmodel.export_preset_to_shaders"
    bl_label = "导出预设到 Shaders"
    bl_options = {'REGISTER', 'UNDO'}

    node_group_name: bpy.props.StringProperty()

    def execute(self, context):
        import subprocess, sys
        ng = bpy.data.node_groups.get(self.node_group_name)
        if not ng:
            return {'CANCELLED'}
        smp = getattr(context.scene, "fmodel_shader_manager", None)
        socket_mapping = _resolve_socket_mapping(ng, smp)
        profile = _build_preset_from_mapping(ng, socket_mapping, preset_label=ng.name)
        os.makedirs(_EXPORTS_DIR, exist_ok=True)
        filepath = os.path.join(_EXPORTS_DIR, f"{ng.name}_preset.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        self.report({'INFO'}, f"已导出到 CustomExported/: {os.path.basename(filepath)}")
        try:
            if sys.platform == 'win32':
                os.startfile(_EXPORTS_DIR)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', _EXPORTS_DIR])
            else:
                subprocess.Popen(['xdg-open', _EXPORTS_DIR])
        except Exception:
            pass
        return {'FINISHED'}



