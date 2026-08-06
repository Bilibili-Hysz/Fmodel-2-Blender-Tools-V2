# FModel_Tools/Universal_tools/mesh/scheme_psk_auto.py
# 修复方案 — PSK 模型自动修复

import bpy
from mathutils import Vector

SCHEME_ID = 'PSK_AUTO'
SCHEME_LABEL = "方案: PSK 骨骼自动修复"
SCHEME_DESC = "专为 PSK 格式设计的骨骼清理与修直方案"
SCHEME_ICON = 'MESH_DATA'

# ⭐ 修正：类名改为 PSK_AUTO_Props
class PSK_AUTO_Props(bpy.types.PropertyGroup):
    delete_end_bones: bpy.props.BoolProperty(name="清理无贡献骨骼", default=True) # type: ignore
    ignore_end_bones: bpy.props.BoolProperty(name="忽略 End 骨骼", default=True) # type: ignore
    ignore_ik_bones: bpy.props.BoolProperty(name="忽略 IK/辅助骨骼", default=True) # type: ignore
    use_adaptive_length: bpy.props.BoolProperty(name="自适应骨骼长度", default=True) # type: ignore
    connect_bones: bpy.props.BoolProperty(name="强制物理相连", default=True) # type: ignore

# ⭐ 统一引用接口
SCHEME_PROPS = PSK_AUTO_Props

def draw(layout, props):
    col = layout.column(align=True)
    col.prop(props, "delete_end_bones")
    col.prop(props, "ignore_end_bones")
    col.prop(props, "ignore_ik_bones")
    col.prop(props, "use_adaptive_length")
    col.prop(props, "connect_bones")

def execute(context, props):
    arm_obj = context.active_object
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return False, "请选中骨架！"
        
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    arm = arm_obj.data
    deleted_count = 0
    
    # 1. 物理清理无贡献骨骼
    if props.delete_end_bones:
        useless_bones = [b for b in arm.edit_bones if len(b.children) == 0 and not b.use_deform]
        deleted_count = len(useless_bones)
        for b in useless_bones: arm.edit_bones.remove(b)

    new_tails = {}
    ik_keywords = ['ik', 'vb_', 'weapon', 'socket', 'dummy', 'camera']
    
    # 2. 计算新尾部坐标
    for bone in arm.edit_bones:
        name_lower = bone.name.lower()
        
        if props.ignore_end_bones and 'end' in name_lower: 
            continue
        if props.ignore_ik_bones and any(k in name_lower for k in ik_keywords):
            continue
            
        orig_length = max(0.01, bone.length)
        
        # 判定是否为主 Root
        is_main_root = (bone.parent is None) or ('root' in name_lower and 'ik' not in name_lower and 'foot' not in name_lower)
        
        if is_main_root:
            new_tails[bone.name] = bone.head + Vector((0, 0, orig_length))
            continue
            
        # 常规子骨骼朝向计算
        if bone.children:
            valid_children = list(bone.children)
            if props.ignore_ik_bones:
                valid_children = [c for c in valid_children if not any(k in c.name.lower() for k in ik_keywords)]
            if props.ignore_end_bones:
                valid_children = [c for c in valid_children if 'end' not in c.name.lower()]
            
            deform_children = [c for c in valid_children if c.use_deform]
            if deform_children: valid_children = deform_children
            
            if valid_children:
                center = sum((child.head for child in valid_children), Vector()) / len(valid_children)
                direction = center - bone.head
                if direction.length > 0.001:
                    new_tails[bone.name] = center if props.use_adaptive_length else bone.head + direction.normalized() * orig_length
                else:
                    new_tails[bone.name] = bone.head + Vector((0, orig_length if not props.use_adaptive_length else 0.1, 0))
                continue

        # 处理末梢骨骼
        if bone.parent:
            direction = bone.head - bone.parent.head
            if direction.length > 0.001:
                ext_len = direction.length if props.use_adaptive_length else orig_length
                new_tails[bone.name] = bone.head + direction.normalized() * max(0.01, ext_len)
            else:
                new_tails[bone.name] = bone.head + Vector((0, 0, orig_length))
        else:
            new_tails[bone.name] = bone.head + Vector((0, 0, orig_length))
                
    # 3. 应用计算并强制重置 Roll
    for b_name, n_tail in new_tails.items():
        if b_name in arm.edit_bones:
            eb = arm.edit_bones[b_name]
            eb.tail = n_tail
            eb.roll = 0 

    # 4. 物理连接 (核心：枢纽骨骼豁免，防止漂移)
    if getattr(props, "connect_bones", True):
        protected_bones = ['pelvis', 'hip', 'root', 'ik', 'vb_']
        for bone in arm.edit_bones:
            if bone.parent and (bone.head - bone.parent.tail).length < 0.001:
                name_lower = bone.name.lower()
                parent_lower = bone.parent.name.lower()
                is_protected = any(k in name_lower for k in protected_bones)
                is_parent_root = 'root' in parent_lower or bone.parent.parent is None
                if not is_protected and not is_parent_root:
                    bone.use_connect = True

    bpy.ops.object.mode_set(mode='OBJECT')
    msg = "PSK 骨骼修复完成！"
    if props.delete_end_bones: msg += f"清理了 {deleted_count} 根骨骼。"
    return True, msg