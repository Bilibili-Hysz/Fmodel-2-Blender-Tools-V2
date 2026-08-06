# FModel_Tools/Universal_tools/mesh/scheme_psk_mesh_fix.py
# 修复方案 — PSK 网格拓扑修复

import bpy
from mathutils import Vector
from collections import defaultdict

SCHEME_ID = 'PSK_MESH_FIX'
SCHEME_LABEL = "方案: PSK 网格与表情撕裂修复"
SCHEME_DESC = "修复 PSK 模型在 UV 边界处的顶点重合与形态键撕裂问题"
SCHEME_ICON = 'MESH_DATA'

class PSK_MESH_FIX_Props(bpy.types.PropertyGroup): # ⭐ 修正：类名改为 PSK_MESH_FIX_Props
    sync_shape_keys: bpy.props.BoolProperty(
        name="同步重合顶点的形态键",
        description="将 UV 断片处重合顶点的形态键形变强制统一，完美修复表情撕裂",
        default=True
    ) # type: ignore
    merge_vertices: bpy.props.BoolProperty(
        name="合并重合顶点 (按距离)",
        description="物理合并重合的顶点（警告：可能会破坏原始法线，若不准备导回 UE 可开启）",
        default=False
    ) # type: ignore

# ⭐ 统一引用接口
SCHEME_PROPS = PSK_MESH_FIX_Props

def draw(layout, props):
    col = layout.column(align=True)
    col.prop(props, "sync_shape_keys")
    col.prop(props, "merge_vertices")

def execute(context, props):
    obj = context.active_object
    if not obj or obj.type != 'MESH':
        return False, "请选中一个网格对象！"
        
    fixed_count = 0
    
    # 1. 核心算法：修复形态键撕裂 (形变广播)
    if props.sync_shape_keys and obj.data.shape_keys:
        mesh = obj.data
        basis = mesh.shape_keys.key_blocks[0]
        
        pos_dict = defaultdict(list)
        for i, v in enumerate(basis.data):
            key = (round(v.co.x, 4), round(v.co.y, 4), round(v.co.z, 4))
            pos_dict[key].append(i)
            
        for sk in mesh.shape_keys.key_blocks[1:]:
            for key, indices in pos_dict.items():
                if len(indices) > 1:
                    max_disp = 0.0
                    target_co = None
                    basis_co = Vector(key)
                    
                    for idx in indices:
                        disp = (sk.data[idx].co - basis_co).length
                        if disp > max_disp:
                            max_disp = disp
                            target_co = sk.data[idx].co.copy()
                            
                    if max_disp > 0.0001:
                        for idx in indices:
                            if (sk.data[idx].co - target_co).length > 0.0001:
                                sk.data[idx].co = target_co
                                fixed_count += 1

    if props.merge_vertices:
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.remove_doubles(threshold=0.0001)
        bpy.ops.object.mode_set(mode='OBJECT')
        
    msg = "网格修复完成！"
    if fixed_count > 0:
        msg += f" 共修复了 {fixed_count} 个断层顶点的形态键撕裂。"
    return True, msg