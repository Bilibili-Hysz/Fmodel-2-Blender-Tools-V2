# FModel_Tools/Universal_tools/skeleton/scheme_auto.py
# 修复方案 — 自动骨骼朝向修正

import bpy
from mathutils import Vector

SCHEME_ID = 'AUTO'
SCHEME_LABEL = "方案 1: 自动计算修复"
SCHEME_DESC = "自动清理无用节点，Root朝上，计算骨骼朝向并重置基准轴"
SCHEME_ICON = 'AUTO'

class AUTO_Props(bpy.types.PropertyGroup): # ⭐ 唯一类名
    delete_end_bones: bpy.props.BoolProperty(name="清理无贡献骨骼", default=True) # type: ignore
    ignore_end_bones: bpy.props.BoolProperty(name="忽略 End 骨骼", default=True) # type: ignore
    use_adaptive_length: bpy.props.BoolProperty(name="自适应骨骼长度", default=True) # type: ignore

SCHEME_PROPS = AUTO_Props # ⭐ 统一引用接口

def draw(layout, props):
    col = layout.column(align=True)
    col.prop(props, "delete_end_bones")
    col.prop(props, "ignore_end_bones")
    col.prop(props, "use_adaptive_length")

def execute(context, props):
    arm_obj = context.active_object
    if not arm_obj or arm_obj.type != 'ARMATURE': return False, "请选中骨架！"
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    arm = arm_obj.data
    deleted_count = 0
    
    if props.delete_end_bones:
        useless_bones = [b for b in arm.edit_bones if len(b.children) == 0 and not b.use_deform]
        deleted_count = len(useless_bones)
        for b in useless_bones: arm.edit_bones.remove(b)

    new_tails = {}
    for bone in arm.edit_bones:
        name_lower = bone.name.lower()
        if props.ignore_end_bones and 'end' in name_lower: continue
        orig_length = max(0.01, bone.length)
        
        # ⭐ 同步 Root 逻辑：没有父级或名字含 root 的主骨骼强制指向 Z 轴
        is_main_root = (bone.parent is None) or ('root' in name_lower and 'ik' not in name_lower and 'foot' not in name_lower)
        if is_main_root:
            new_tails[bone.name] = bone.head + Vector((0, 0, orig_length))
            continue
            
        if bone.children:
            valid_children = [c for c in bone.children if c.use_deform and 'end' not in c.name.lower()] if props.ignore_end_bones else [c for c in bone.children if c.use_deform]
            if not valid_children: valid_children = list(bone.children)
            if valid_children:
                center = sum((child.head for child in valid_children), Vector()) / len(valid_children)
                direction = center - bone.head
                if direction.length > 0.001:
                    new_tails[bone.name] = center if props.use_adaptive_length else bone.head + direction.normalized() * orig_length
                else:
                    new_tails[bone.name] = bone.head + Vector((0, orig_length if not props.use_adaptive_length else 0.1, 0))
                continue

        if bone.parent:
            direction = bone.head - bone.parent.head
            if direction.length > 0.001:
                new_tails[bone.name] = bone.head + direction.normalized() * max(0.01, (direction.length if props.use_adaptive_length else orig_length))
            else:
                new_tails[bone.name] = bone.head + Vector((0, 0, orig_length))
        else:
            new_tails[bone.name] = bone.head + Vector((0, 0, orig_length))
                
    for b_name, n_tail in new_tails.items():
        if b_name in arm.edit_bones:
            eb = arm.edit_bones[b_name]
            eb.tail = n_tail
            eb.roll = 0 
    bpy.ops.object.mode_set(mode='OBJECT')
    return True, f"修复完成！清理了 {deleted_count} 根骨骼。"