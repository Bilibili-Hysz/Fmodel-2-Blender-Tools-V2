# FModel_Tools/Universal_tools/material/shader/panel.py
# 着色器管理器面板 + 菜单

import bpy

from ..shader_manager import (
    _load_shader_list,
    get_active_node_group,
    iter_node_group_inputs,
    _socket_is_internal,
    _get_socket_mapping,
    _get_mapped_interface_name,
)


class SHADER_MT_DefaultShaders(bpy.types.Menu):
    bl_label = "默认着色器"
    bl_idname = "SHADER_MT_default_shaders"

    def draw(self, context):
        for shader_id, info in _load_shader_list("defaults").items():
            op = self.layout.operator("fmodel.import_default_shader", text=info["label"])
            op.group_name = shader_id


class SHADER_MT_CustomShaders(bpy.types.Menu):
    bl_label = "自定义着色器"
    bl_idname = "SHADER_MT_custom_shaders"

    def draw(self, context):
        custom = _load_shader_list("custom")
        if not custom:
            self.layout.label(text="(无自定义着色器)", icon='INFO')
            return
        for shader_id, info in custom.items():
            op = self.layout.operator("fmodel.import_default_shader", text=info["label"])
            op.group_name = shader_id


class SHADER_MT_CustomShadersRemove(bpy.types.Menu):
    bl_label = "删除自定义着色器"
    bl_idname = "SHADER_MT_custom_shaders_remove"

    def draw(self, context):
        custom = _load_shader_list("custom")
        if not custom:
            self.layout.label(text="(无自定义着色器)", icon='INFO')
            return
        for shader_id, info in custom.items():
            op = self.layout.operator("fmodel.remove_custom_shader", text=info["label"])
            op.group_name = shader_id


class FMODEL_PT_ShaderManager(bpy.types.Panel):
    bl_label = "着色器管理"
    bl_idname = "FMODEL_PT_shader_manager"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'FModel'

    def draw(self, context):
        layout = self.layout
        props = context.scene.fmodel_shader_manager
        ng = get_active_node_group()

        # ── 着色器模板 ──
        box_tmpl = layout.box()
        box_tmpl.label(text="着色器模板", icon='SHADING_RENDERED')

        # 模板导入 (shaders.json)
        default_shaders = _load_shader_list("defaults")
        box_def = box_tmpl.box()
        box_def.label(text="模板导入", icon='NODE_MATERIAL')
        if default_shaders:
            box_def.menu("SHADER_MT_default_shaders", text="导入模板着色器", icon='IMPORT')
            for shader_id, info in default_shaders.items():
                box_def.label(text=f"  {info['label']}", icon='MATERIAL_DATA')
        else:
            box_def.label(text="(无可用模板)", icon='INFO')

        # 自定义导入 (Customshaders.json)
        custom_shaders = _load_shader_list("custom")
        box_cust = box_tmpl.box()
        box_cust.label(text="自定义导入", icon='FILE_TICK')
        if custom_shaders:
            row_ci = box_cust.row()
            row_ci.menu("SHADER_MT_custom_shaders", text="导入", icon='IMPORT')
            row_ci.menu("SHADER_MT_custom_shaders_remove", text="删除", icon='TRASH')
            for shader_id, info in custom_shaders.items():
                box_cust.label(text=f"  {info['label']}", icon='MATERIAL_DATA')
        else:
            box_cust.label(text="(无自定义着色器)", icon='INFO')
        if ng:
            box_cust.operator("fmodel.save_custom_shader", text="保存当前为自定义", icon='FILE_TICK').group_name = ng.name

        # ── 接口管理 ──
        box_iface = layout.box()
        box_iface.label(text="接口管理", icon='MODIFIER')
        if ng:
            inputs = [i for i in iter_node_group_inputs(ng) if not _socket_is_internal(i)]
            box_iface.label(text=f"节点组: {ng.name}", icon='NODETREE')
            if inputs:
                s1 = box_iface.split(factor=0.20)
                s1.label(text="", icon='MODIFIER')
                s2 = s1.split(factor=0.4375)
                s2.label(text="接口名")
                s3 = s2.split(factor=0.7778)
                s3.label(text="映射到")
                s3.label(text="", icon='REMOVE')

                for item in inputs:
                    stype = getattr(item, "socket_type", "NodeSocketColor")
                    type_icons = {
                        "NodeSocketColor": 'COLOR', "NodeSocketVector": 'EMPTY_ARROWS',
                        "NodeSocketFloat": 'DRIVER', "NodeSocketFloatFactor": 'PROP_CON',
                        "NodeSocketInt": 'LINENUMBERS_ON',
                    }
                    type_texts = {
                        "NodeSocketColor": "Color", "NodeSocketVector": "Vector",
                        "NodeSocketFloat": "Float", "NodeSocketFloatFactor": "Fac",
                        "NodeSocketInt": "Int",
                    }
                    ticon = type_icons.get(stype, 'NODE_SEL')
                    ttext = type_texts.get(stype, stype.rsplit("NodeSocket", 1)[-1] if "NodeSocket" in stype else stype)

                    s1 = box_iface.split(factor=0.20)
                    s1.label(text=ttext, icon=ticon)
                    s2 = s1.split(factor=0.4375)
                    op_ren = s2.operator("fmodel.rename_socket_inline", text=item.name, emboss=False)
                    op_ren.old_name = item.name
                    mapping = _get_socket_mapping(props, ng.name, item.name)
                    s3 = s2.split(factor=0.7778)
                    op_map = s3.operator("fmodel.set_socket_mapping", text=_get_mapped_interface_name(mapping))
                    op_map.socket_name = item.name
                    s3.operator("fmodel.remove_socket_inline", text="", icon='REMOVE').socket_name = item.name
            else:
                box_iface.label(text="(无可见接口)", icon='INFO')

            box_iface.separator()
            row_add = box_iface.row(align=True)
            row_add.prop(props, "new_socket_name", text="")
            row_add.prop(props, "new_socket_type", text="")
            row_add.operator("fmodel.add_socket_inline", text="", icon='ADD')
            row_add.operator("fmodel.auto_map_sockets", text="", icon='AUTO').node_group_name = ng.name
        else:
            box_iface.label(text="请在着色器编辑器中选中一个 Group 节点", icon='INFO')

        # ── 操作 ──
        box_ops = layout.box()
        box_ops.label(text="操作", icon='TOOL_SETTINGS')
        row_ops1 = box_ops.row(align=True)
        row_ops1.operator("fmodel.create_blank_shader", text="新建空白", icon='FILE_NEW')
        row_ops1.operator("fmodel.quick_register_nodegroup", text="注册为预设", icon='CHECKMARK').node_group_name = ng.name if ng else ""
        row_ops1.operator("fmodel.preview_preset", text="预览预设", icon='TEXT')
        row_ops2 = box_ops.row(align=True)
        row_ops2.operator("fmodel.export_nodegroup_preset", text="导出预设", icon='EXPORT')
        row_ops2.operator("fmodel.load_preset_shader_editor", text="加载预设", icon='FILE_TEXT')
