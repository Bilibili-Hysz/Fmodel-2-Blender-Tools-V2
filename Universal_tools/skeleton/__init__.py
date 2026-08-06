# FModel_Tools/Universal_tools/skeleton/__init__.py
# 骨架工具 — 骨骼整理、重命名、映射、姿态镜像

import bpy
import mathutils
import importlib

if "bpy" in locals():
    for mod in list(locals().values()):
        if hasattr(mod, 'reload'):
            importlib.reload(mod)


class SkeletonProperties(bpy.types.PropertyGroup):
    """骨架工具属性"""
    mirror_source_bone: bpy.props.StringProperty(name="源骨骼")
    mirror_target_bone: bpy.props.StringProperty(name="目标骨骼")
    mirror_plane: bpy.props.EnumProperty(
        name="镜像平面", items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")], default='X')
    mirror_apply_loc: bpy.props.BoolProperty(name="位置", default=True)
    mirror_apply_rot: bpy.props.BoolProperty(name="旋转", default=True)
    mirror_strict_axis: bpy.props.BoolProperty(name="对称轴向", default=False)
    show_mirror_adv: bpy.props.BoolProperty(name="高级", default=False)


class SKEL_OT_SortBones(bpy.types.Operator):
    """智能收纳辅助骨骼"""
    bl_idname = "fmodel.sort_bones"
    bl_label = "整理骨骼层级"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        arm_obj = context.active_object
        arm = arm_obj.data
        if not hasattr(arm, "collections"):
            return {'CANCELLED'}
        col_main = arm.collections.get("Main_Deform") or arm.collections.new("Main_Deform")
        col_hidden = arm.collections.get("Hidden_Helpers") or arm.collections.new("Hidden_Helpers")
        bound = [o for o in context.scene.objects if o.type == 'MESH' and (o.parent == arm_obj or any(m.type == 'ARMATURE' and m.object == arm_obj for m in o.modifiers))]
        vg_names = {vg.name for o in bound for vg in o.vertex_groups}

        useful = set()
        def check(bone):
            if bone.name in useful: return True
            direct = (bone.name in vg_names) and bone.use_deform if bound else len(bone.children) > 0
            child = any(check(c) for c in bone.children)
            if direct or child: useful.add(bone.name)
            return direct or child
        for root in [b for b in arm.bones if not b.parent]:
            check(root)

        moved = 0
        for bone in arm.bones:
            for c in arm.collections:
                if bone.name in c.bones: c.unassign(bone)
            (col_hidden if bone.name not in useful else col_main).assign(bone)
            if bone.name not in useful: moved += 1
        col_hidden.is_visible = False
        self.report({'INFO'}, f"已隐藏 {moved} 根辅助骨骼")
        return {'FINISHED'}


class SKEL_OT_RenameBones(bpy.types.Operator):
    """批量重命名骨骼"""
    bl_idname = "fmodel.rename_bones"
    bl_label = "批量重命名"
    bl_options = {'REGISTER', 'UNDO'}
    prefix: bpy.props.StringProperty(name="前缀", default="")
    suffix: bpy.props.StringProperty(name="后缀", default="")
    find: bpy.props.StringProperty(name="查找")
    replace: bpy.props.StringProperty(name="替换")
    mode: bpy.props.EnumProperty(items=[('PREFIX', "前后缀", ""), ('REPLACE', "查找替换", ""), ('CASE', "大小写", "")], default='PREFIX')
    case_mode: bpy.props.EnumProperty(items=[('UPPER', "大写", ""), ('LOWER', "小写", ""), ('TITLE', "首字母大写", "")], default='UPPER')
    selected_only: bpy.props.BoolProperty(name="仅选中骨骼", default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mode", expand=True)
        if self.mode == 'PREFIX':
            row = layout.row(align=True)
            row.prop(self, "prefix"); row.prop(self, "suffix")
        elif self.mode == 'REPLACE':
            row = layout.row(align=True)
            row.prop(self, "find"); row.prop(self, "replace")
        else:
            layout.prop(self, "case_mode", expand=True)
        layout.prop(self, "selected_only")

    def execute(self, context):
        arm = context.active_object.data
        if self.selected_only and context.object.mode == 'POSE':
            bones = [b.name for b in context.selected_pose_bones]
        else:
            bones = [b.name for b in arm.bones]
        renames = {}
        for name in bones:
            new_name = name
            if self.mode == 'PREFIX':
                new_name = f"{self.prefix}{name}{self.suffix}"
            elif self.mode == 'REPLACE' and self.find:
                new_name = name.replace(self.find, self.replace)
            elif self.mode == 'CASE':
                new_name = name.upper() if self.case_mode == 'UPPER' else name.lower() if self.case_mode == 'LOWER' else name.title()
            if new_name != name:
                renames[name] = new_name
        for old, new in renames.items():
            arm.bones[old].name = new
        self.report({'INFO'}, f"已重命名 {len(renames)} 根骨骼")
        return {'FINISHED'}


class SKEL_OT_FixBoneNames(bpy.types.Operator):
    """UE 标准骨骼命名"""
    bl_idname = "fmodel.fix_bone_names"
    bl_label = "UE 标准命名"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        arm = context.active_object.data
        mapping = {
            'upperarm': 'UpperArm', 'lowerarm': 'LowerArm', 'hand': 'Hand',
            'clavicle': 'Clavicle', 'spine': 'Spine', 'neck': 'Neck',
            'head': 'Head', 'thigh': 'Thigh', 'calf': 'Calf', 'foot': 'Foot',
            'ball': 'Ball', 'pelvis': 'Pelvis',
        }
        count = 0
        for bone in arm.bones:
            name = bone.name
            for key, val in mapping.items():
                if key in name.lower():
                    new_name = name.lower().replace(key, val)
                    if new_name != name:
                        bone.name = new_name
                        count += 1
                        break
        self.report({'INFO'}, f"已标准化 {count} 根骨骼")
        return {'FINISHED'}


class SKEL_OT_GrabActiveBone(bpy.types.Operator):
    """拾取当前骨骼"""
    bl_idname = "fmodel.grab_active_bone"
    bl_label = "拾取骨骼"
    bl_options = {'UNDO'}
    target_field: bpy.props.StringProperty()

    def execute(self, context):
        pb = context.active_pose_bone
        if pb:
            props = context.scene.fmodel_skeleton
            if self.target_field == 'SOURCE': props.mirror_source_bone = pb.name
            else: props.mirror_target_bone = pb.name
        return {'FINISHED'}


class SKEL_OT_MirrorPose(bpy.types.Operator):
    """执行姿态镜像"""
    bl_idname = "fmodel.mirror_pose_custom"
    bl_label = "执行镜像"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'ARMATURE' and context.object.mode == 'POSE'

    def execute(self, context):
        obj = context.object
        props = context.scene.fmodel_skeleton
        src_name, tgt_name = props.mirror_source_bone, props.mirror_target_bone
        if not src_name or not tgt_name:
            self.report({'ERROR'}, "请指定源和目标骨骼"); return {'CANCELLED'}
        if src_name not in obj.pose.bones or tgt_name not in obj.pose.bones:
            self.report({'ERROR'}, "骨骼不存在"); return {'CANCELLED'}
        src, tgt = obj.pose.bones[src_name], obj.pose.bones[tgt_name]
        axis = {'X': (1,0,0), 'Y': (0,1,0), 'Z': (0,0,1)}[props.mirror_plane]
        mirror_mat = mathutils.Matrix.Scale(-1, 4, axis)

        if props.mirror_strict_axis:
            m = mirror_mat @ src.matrix @ mirror_mat
            m_loc, m_rot, m_sca = m.decompose()
            t_loc, t_rot, t_sca = tgt.matrix.decompose()
            tgt.matrix = mathutils.Matrix.LocRotScale(
                m_loc if props.mirror_apply_loc else t_loc,
                m_rot if props.mirror_apply_rot else t_rot, t_sca)
        else:
            m_head = mirror_mat @ src.head
            m_tail = mirror_mat @ src.tail
            m_diff = m_tail - m_head
            rot_mat = tgt.matrix.to_3x3()
            if props.mirror_apply_rot and m_diff.length > 0.00001:
                t_rest_vec = (tgt.bone.tail_local - tgt.bone.head_local).normalized()
                rot_diff = t_rest_vec.rotation_difference(m_diff.normalized())
                rot_mat = rot_diff.to_matrix() @ tgt.bone.matrix_local.to_3x3()
            new_mat = rot_mat.to_4x4()
            new_mat.translation = m_head if props.mirror_apply_loc else tgt.matrix.translation
            tgt.matrix = new_mat
        context.view_layer.update()
        self.report({'INFO'}, f"镜像完成: {src_name} → {tgt_name}")
        return {'FINISHED'}


# Bone mapping operators
class SKEL_OT_GenerateBoneMapping(bpy.types.Operator):
    """生成骨骼映射模板"""
    bl_idname = "fmodel.generate_bone_mapping"
    bl_label = "生成映射"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        arm = context.active_object.data
        text = bpy.data.texts.new("BoneMapping.txt")
        text.write("# 格式: 旧名=新名\n\n")
        for bone in arm.bones:
            text.write(f"{bone.name}={bone.name}\n")
        self.report({'INFO'}, f"已生成 {len(arm.bones)} 条映射到文本编辑器")
        return {'FINISHED'}


class SKEL_OT_MapBoneNames(bpy.types.Operator):
    """应用骨骼映射"""
    bl_idname = "fmodel.map_bone_names"
    bl_label = "应用映射"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        arm = context.active_object.data
        mapping = {}
        for text in bpy.data.texts:
            for line in text.lines:
                s = line.body.strip()
                if '=' in s and not s.startswith('#'):
                    parts = s.split('=', 1)
                    if len(parts) == 2 and parts[0] and parts[1]:
                        mapping[parts[0].strip()] = parts[1].strip()
        count = 0
        for old, new in mapping.items():
            if old in arm.bones and old != new:
                arm.bones[old].name = new
                count += 1
        self.report({'INFO'}, f"已映射 {count} 根骨骼")
        return {'FINISHED'}


_classes = (
    SkeletonProperties, SKEL_OT_SortBones, SKEL_OT_RenameBones,
    SKEL_OT_FixBoneNames, SKEL_OT_GrabActiveBone, SKEL_OT_MirrorPose,
    SKEL_OT_GenerateBoneMapping, SKEL_OT_MapBoneNames,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.fmodel_skeleton = bpy.props.PointerProperty(type=SkeletonProperties)


def unregister():
    del bpy.types.Scene.fmodel_skeleton
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
