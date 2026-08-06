# FModel_Tools/Universal_tools/mesh/scheme_morph_merge.py
# 修复方案 — 形态键合并与去重

import bpy
import os
import re

SCHEME_ID = 'MORPH_MERGE'
SCHEME_LABEL = "方案 3: 形态键自动合并"
SCHEME_DESC = "逐个导入并合并形态键，适配 FModel 命名"
SCHEME_ICON = 'SHAPEKEY_DATA'

class MORPH_MERGE_Props(bpy.types.PropertyGroup): # ⭐ 唯一类名
    custom_path: bpy.props.StringProperty(name="手动路径", subtype='DIR_PATH') # type: ignore
    search_subfolders: bpy.props.BoolProperty(name="包含子文件夹", default=False) # type: ignore

SCHEME_PROPS = MORPH_MERGE_Props

def draw(layout, props):
    col = layout.column(align=True)
    col.prop(props, "custom_path")
    col.prop(props, "search_subfolders")

def execute(context, props):
    base_obj = context.active_object
    if not base_obj or base_obj.type != 'MESH': return False, "选中网格对象！"
    target_dir = os.path.dirname(base_obj["gltf_import_path"]) if "gltf_import_path" in base_obj else bpy.path.abspath(props.custom_path) if props.custom_path else bpy.path.abspath("//")
    base_filename = os.path.splitext(os.path.basename(base_obj.get("gltf_import_path", base_obj.name)))[0]
    
    # ⭐ 核心修复：改为 `_+` 兼容单/双下划线
    pattern = re.compile(rf"^{re.escape(base_filename)}_Morph(\d+)_+(.+)\.(gltf|glb)$", re.IGNORECASE)
    
    matched_files = []
    for root, _, files in os.walk(target_dir):
        for f in files:
            m = pattern.match(f)
            if m: matched_files.append({'path': os.path.join(root, f), 'idx': int(m.group(1)), 'name': m.group(2)})
        if not props.search_subfolders: break
    if not matched_files: return False, "未找到 Morph 文件"
    matched_files.sort(key=lambda x: x['idx'])

    # 2. 锁定底模骨架到 REST 姿态以防合并错位
    base_arm = base_obj.find_armature()
    orig_pose_pos = base_arm.data.pose_position if base_arm else 'POSE'
    if base_arm: base_arm.data.pose_position = 'REST'

    if not base_obj.data.shape_keys: 
        base_obj.shape_key_add(name="Basis")

    success_count = 0
    fail_count = 0
    
    # 3. 循环挂载形态键
    for item in matched_files:
        old_meshes = set(bpy.data.meshes)
        old_armatures = set(bpy.data.armatures)
        old_actions = set(bpy.data.actions)
        old_objs = set(context.scene.objects)
        new_objs = set()

        try:
            bpy.ops.import_scene.gltf(filepath=item['path'], bone_heuristic='TEMPERANCE')
            new_objs = set(context.scene.objects) - old_objs
            
            morph_mesh = None
            for obj in new_objs:
                if obj.type == 'MESH': morph_mesh = obj
                if obj.type == 'ARMATURE': obj.data.pose_position = 'REST' # 分身也设为 REST，确保空间一致
            
            context.view_layer.update()

            if morph_mesh:
                bpy.ops.object.select_all(action='DESELECT')
                morph_mesh.select_set(True)
                base_obj.select_set(True)
                context.view_layer.objects.active = base_obj
                
                # 5.0 API 适配
                if hasattr(bpy.ops.object, "join_shapes"): bpy.ops.object.join_shapes()
                else: bpy.ops.object.join_as_shapes()
                
                # 命名形态键
                base_obj.data.shape_keys.key_blocks[-1].name = item['name']
                success_count += 1
                
        except Exception as e:
            print(f"合并形态键 {item['name']} 失败 (可能是顶点数不匹配): {e}")
            # 使用日志系统
            import logging
            logging.getLogger(__name__).warning(f"合并形态键 {item['name']} 失败: {e}")
            fail_count += 1
            
        finally:
            # 防弹级局部清理
            for o in new_objs: 
                try: bpy.data.objects.remove(o, do_unlink=True)
                except: pass
            for m in (set(bpy.data.meshes) - old_meshes):
                if m.users == 0: 
                    try: bpy.data.meshes.remove(m)
                    except: pass
            for a in (set(bpy.data.armatures) - old_armatures):
                if a.users == 0: 
                    try: bpy.data.armatures.remove(a)
                    except: pass
            for act in (set(bpy.data.actions) - old_actions):
                if act.users == 0: 
                    try: bpy.data.actions.remove(act)
                    except: pass

    # 4. 恢复底模原始姿态
    if base_arm: base_arm.data.pose_position = orig_pose_pos 
    
    msg = f"已成功合并 {success_count} 个形态键。"
    if fail_count > 0:
        msg += f" 有 {fail_count} 个由于顶点数异常合并失败，已被系统安全丢弃。"
    return True, msg

SCHEME_PROPS = MORPH_MERGE_Props