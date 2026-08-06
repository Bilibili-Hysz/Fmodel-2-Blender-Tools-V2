# FModel_Tools/Universal_tools/skeleton/scheme_copy.py
# 修复方案 — 从参考骨架复制骨骼朝向

import bpy

# ==================== 1. 方案元数据 ====================
SCHEME_ID = 'COPY'
SCHEME_LABEL = "方案 2: 从源骨架复制"
SCHEME_DESC = "从另一个规范的骨架中提取形态数据"
SCHEME_ICON = 'COPYDOWN'

# ==================== 2. 专属参数定义 ====================
class COPY_Props(bpy.types.PropertyGroup): # ⭐ 修正：类名改为 COPY_Props
    ignore_non_deform: bpy.props.BoolProperty(
        name="忽略无变形骨骼",
        description="复制时，将不会复制源骨架中不参与蒙皮形变或带有 end 等名字的骨骼",
        default=True
    ) # type: ignore

# ==================== 3. 专属 UI 绘制 ====================
def draw(layout, props):
    col = layout.column(align=True)
    col.label(text="※ 先选源骨架, 再 Shift 选目标骨架", icon='INFO')
    col.prop(props, "ignore_non_deform")

# ==================== 4. 核心执行逻辑 ====================
def execute(context, props):
    obs = context.selected_objects
    if len(obs) != 2 or obs[0].type != 'ARMATURE' or obs[1].type != 'ARMATURE':
        return False, "请选中两个骨架！(先点选源骨架，再 Shift 点击目标骨架)"
        
    target = context.active_object
    source = [obj for obj in context.selected_objects if obj != target][0]
    
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode='EDIT')
    tgt_arm_data = target.data
    src_arm_data = source.data
    
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.mode_set(mode='EDIT')
    
    src_bones_info = {}
    for b in src_arm_data.edit_bones:
        if props.ignore_non_deform:
            is_end_bone = any(k in b.name.lower() for k in ['end', 'tip', 'leaf'])
            if not b.use_deform or is_end_bone:
                continue
        src_bones_info[b.name] = (b.head.copy(), b.tail.copy(), b.roll)
        
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode='EDIT')
    
    count = 0
    for tgt_bone in tgt_arm_data.edit_bones:
        if tgt_bone.name in src_bones_info:
            head, tail, roll = src_bones_info[tgt_bone.name]
            tgt_bone.head, tgt_bone.tail, tgt_bone.roll = head, tail, roll
            count += 1
            
    bpy.ops.object.mode_set(mode='OBJECT')
    return True, f"成功！提取了 {count} 根骨骼形态。"

# ⭐ 统一引用接口
SCHEME_PROPS = COPY_Props