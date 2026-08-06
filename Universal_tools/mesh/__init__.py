# FModel_Tools/Universal_tools/mesh/__init__.py
# 网格工具 — 形态键清理、材质分离

import bpy


class MESH_OT_CleanEmptyMorphs(bpy.types.Operator):
    """批量清理空白形态键"""
    bl_idname = "fmodel.clean_empty_morphs"
    bl_label = "批量清理空白形态键"
    bl_options = {'REGISTER', 'UNDO'}

    threshold: bpy.props.FloatProperty(name="容差", default=0.0001, min=0.0, max=0.1, precision=5)

    @classmethod
    def poll(cls, context):
        return any(o for o in context.selected_objects if o.type == 'MESH' and o.data.shape_keys)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.label(text="删除未产生形变的空 Morph", icon='INFO')
        layout.prop(self, "threshold")

    def execute(self, context):
        total = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH' or not obj.data.shape_keys:
                continue
            shape_keys = obj.data.shape_keys.key_blocks
            base = [v.co for v in shape_keys[0].data]
            to_del = []
            for i in range(1, len(shape_keys)):
                key = shape_keys[i]
                if max((v.co - base[j]).length for j, v in enumerate(key.data)) <= self.threshold:
                    to_del.append(key.name)
            if to_del:
                bpy.context.view_layer.objects.active = obj
                for name in to_del:
                    idx = obj.data.shape_keys.key_blocks.find(name)
                    if idx != -1:
                        obj.active_shape_key_index = idx
                        bpy.ops.object.shape_key_remove(all=False)
                        total += 1
        self.report({'INFO'}, f"清理完成: 删除 {total} 个无效形态键")
        return {'FINISHED'}


class MESH_OT_SeparateByMaterial(bpy.types.Operator):
    """智能按材质分离"""
    bl_idname = "fmodel.separate_by_material_smart"
    bl_label = "智能按材质分离"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o for o in context.selected_objects if o.type == 'MESH' and len(o.data.materials) > 1)

    def execute(self, context):
        total = 0
        for obj in [o for o in context.selected_objects if o.type == 'MESH']:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj
            bpy.ops.mesh.separate(type='MATERIAL')
            for o in context.selected_objects:
                if o.type != 'MESH':
                    continue
                used = {p.material_index for p in o.data.polygons}
                if not used:
                    continue
                mat = o.data.materials[list(used)[0]] if list(used)[0] < len(o.data.materials) else None
                if mat:
                    o.name = mat.name
                context.view_layer.objects.active = o
                for i in range(len(o.data.materials) - 1, -1, -1):
                    if i not in used:
                        o.active_material_index = i
                        bpy.ops.object.material_slot_remove()
                total += 1
        self.report({'INFO'}, f"分离完成: {total} 个独立部件")
        return {'FINISHED'}


_classes = (MESH_OT_CleanEmptyMorphs, MESH_OT_SeparateByMaterial)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
