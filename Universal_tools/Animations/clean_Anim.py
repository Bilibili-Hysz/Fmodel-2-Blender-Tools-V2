# FModel_Tools/Universal_tools/Animations/clean_Anim.py
# 动作清理 — 关键帧精简、空通道删除、批量清理

import bpy


def _fcurve_containers(action):
    """Return every fcurve collection of a slotted or legacy action.

    Blender 4.4 moved FCurves into per-slot channelbags and 5.2 removed the
    legacy ``Action.fcurves`` shortcut entirely.  Channelbags hang off
    ``action.layers[*].strips[*]`` (verified on 5.2); a top-level
    ``action.channelbags`` and the pre-4.4 ``action.fcurves`` are kept as
    fallbacks for other build generations.
    """
    bags = list(getattr(action, "channelbags", None) or ())
    if not bags:
        for layer in getattr(action, "layers", ()) or ():
            for strip in getattr(layer, "strips", ()) or ():
                found = getattr(strip, "channelbags", None)
                if found is None:
                    single = getattr(strip, "channelbag", None)
                    found = (single,) if single is not None else ()
                bags.extend(found)
    containers = []
    for bag in bags:
        curves = getattr(bag, "fcurves", None)
        if curves is not None:
            containers.append(curves)
    if not containers:
        curves = getattr(action, "fcurves", None)
        if curves is not None:
            containers.append(curves)
    return containers


def clean_action(action, threshold=0.001, clean_channels=True, clean_flat=True):
    """
    清理单个动作的冗余关键帧

    参数:
        action: 要清理的 bpy.types.Action（槽位动作或旧版动作）
        threshold: 精简阈值，越大清理越狠
        clean_channels: 是否删除空通道
        clean_flat: 是否精简平坦通道（保留第一帧）

    返回:
        dict: {'removed_keyframes': int, 'removed_channels': int, 'flat_channels': int}

    说明:
        清理完全通过数据 API 完成，不依赖 Dope Sheet 上下文，因此后台/
        headless 模式同样可用。阈值只用于平坦通道判定。
    """
    stats = {'removed_keyframes': 0, 'removed_channels': 0, 'flat_channels': 0}
    if not action:
        return stats

    containers = _fcurve_containers(action)
    if not containers:
        return stats

    initial_keyframe_count = sum(
        len(fc.keyframe_points)
        for curves in containers
        for fc in curves
    )

    for curves in containers:
        # 从后往前处理：删除空通道；平坦通道折叠为首帧以维持起始姿态
        for fc in reversed(list(curves)):
            keys = fc.keyframe_points
            if len(keys) == 0:
                if clean_channels:
                    curves.remove(fc)
                    stats['removed_channels'] += 1
                continue

            if clean_flat:
                values = [k.co[1] for k in keys]
                if max(values) - min(values) <= threshold:
                    key_count = len(keys)
                    for j in range(key_count - 1, 0, -1):
                        keys.remove(keys[j])
                    # removed_keyframes 由下方首尾差值统一统计，避免重复计数
                    stats['flat_channels'] += 1

    final_keyframe_count = sum(
        len(fc.keyframe_points)
        for curves in containers
        for fc in curves
    )
    stats['removed_keyframes'] += (initial_keyframe_count - final_keyframe_count)

    return stats


def clean_actions_batch(actions, threshold=0.001, clean_channels=True, clean_flat=True):
    """
    批量清理多个动作

    参数:
        actions: Action 列表
        threshold: 精简阈值
        clean_channels: 是否删除空通道
        clean_flat: 是否精简平坦通道

    返回:
        dict: {'total_actions': int, 'cleaned_actions': int, 'total_removed': int}
    """
    result = {'total_actions': len(actions), 'cleaned_actions': 0, 'total_removed': 0}

    for action in actions:
        try:
            stats = clean_action(action, threshold, clean_channels, clean_flat)
            if stats['removed_keyframes'] > 0 or stats['removed_channels'] > 0:
                result['cleaned_actions'] += 1
                result['total_removed'] += stats['removed_keyframes']
        except Exception as e:
            print(f"[FModel Clean] 清理 {action.name} 失败: {e}")
            # 使用日志系统
            import logging
            logging.getLogger(__name__).error(f"清理 {action.name} 失败: {e}")

    return result


def is_action_used(action):
    """
    检查动作是否被使用

    返回:
        bool: True 如果动作被任何对象、NLA 或形态键使用
    """
    # 检查对象动画数据
    for obj in bpy.data.objects:
        if obj.animation_data and obj.animation_data.action == action:
            return True

    # 检查 NLA 轨道
    for obj in bpy.data.objects:
        if obj.animation_data and obj.animation_data.nla_tracks:
            for track in obj.animation_data.nla_tracks:
                for strip in track.strips:
                    if strip.action == action:
                        return True

    # 检查形态键
    for key in bpy.data.shape_keys:
        if key.animation_data and key.animation_data.action == action:
            return True

    return False


def get_action_stats(action):
    """
    获取动作统计信息

    返回:
        dict: {'fcurves': int, 'keyframes': int, 'frame_range': tuple}
    """
    if not action:
        return {'fcurves': 0, 'keyframes': 0, 'frame_range': (0, 0)}

    containers = _fcurve_containers(action)
    fcurve_count = sum(len(curves) for curves in containers)
    keyframe_count = sum(
        len(fc.keyframe_points)
        for curves in containers
        for fc in curves
    )

    return {
        'fcurves': fcurve_count,
        'keyframes': keyframe_count,
        'frame_range': action.frame_range if action.frame_range else (0, 0)
    }


# ============================================================
# 操作符
# ============================================================

class ANIM_OT_CleanAction(bpy.types.Operator):
    """清理当前动作的冗余关键帧"""
    bl_idname = "fmodel.clean_action"
    bl_label = "清理动作"
    bl_options = {'REGISTER', 'UNDO'}

    threshold: bpy.props.FloatProperty(
        name="精简阈值",
        default=0.001,
        min=0.0,
        max=1.0,
        precision=4
    )
    clean_channels: bpy.props.BoolProperty(
        name="删除空通道",
        default=True
    )
    clean_flat: bpy.props.BoolProperty(
        name="精简平坦通道",
        default=True
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.animation_data and obj.animation_data.action

    def execute(self, context):
        action = context.active_object.animation_data.action
        stats = clean_action(action, self.threshold, self.clean_channels, self.clean_flat)

        self.report({'INFO'},
            f"清理完成: 移除 {stats['removed_keyframes']} 个关键帧, "
            f"{stats['removed_channels']} 个空通道, "
            f"{stats['flat_channels']} 个平坦通道"
        )
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class ANIM_OT_CleanAllActions(bpy.types.Operator):
    """批量清理所有动作"""
    bl_idname = "fmodel.clean_all_actions"
    bl_label = "批量清理动作"
    bl_options = {'REGISTER', 'UNDO'}

    threshold: bpy.props.FloatProperty(
        name="精简阈值",
        default=0.001,
        min=0.0,
        max=1.0,
        precision=4
    )
    clean_channels: bpy.props.BoolProperty(
        name="删除空通道",
        default=True
    )
    clean_flat: bpy.props.BoolProperty(
        name="精简平坦通道",
        default=True
    )
    include_unused: bpy.props.BoolProperty(
        name="包含未使用动作",
        default=False
    )

    @classmethod
    def poll(cls, context):
        return len(bpy.data.actions) > 0

    def execute(self, context):
        if self.include_unused:
            actions = list(bpy.data.actions)
        else:
            actions = [a for a in bpy.data.actions if is_action_used(a)]

        result = clean_actions_batch(actions, self.threshold, self.clean_channels, self.clean_flat)

        self.report({'INFO'},
            f"批量清理完成: {result['cleaned_actions']}/{result['total_actions']} 个动作, "
            f"共移除 {result['total_removed']} 个关键帧"
        )
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


# ============================================================
# 注册
# ============================================================

classes = (
    ANIM_OT_CleanAction,
    ANIM_OT_CleanAllActions,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
