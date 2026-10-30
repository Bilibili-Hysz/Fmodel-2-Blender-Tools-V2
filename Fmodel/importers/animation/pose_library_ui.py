"""Source-backed pose library, independent of timeline animation bundles."""

import json
import os
import tempfile
import textwrap

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import pose_assets
from ....core.workflow import resolve_target, set_target


class FMODEL_PoseItem(bpy.types.PropertyGroup):
    source: bpy.props.PointerProperty(type=bpy.types.Text)
    pose_index: bpy.props.IntProperty()
    bone_count: bpy.props.IntProperty()
    curve_count: bpy.props.IntProperty()
    source_name: bpy.props.StringProperty()
    pose_name: bpy.props.StringProperty()
    convention: bpy.props.StringProperty()


def armature_only(self, obj):
    return obj.type == 'ARMATURE'


def _legacy_target_changed(self, context):
    # Keep scripts and saved scenes using the previous public property working.
    if hasattr(self.id_data, 'fmodel_workflow'):
        set_target(self.id_data, self.target)


def update_search(self, context):
    needle = self.search.casefold().strip()
    if 0 <= self.active_index < len(self.items) and needle in self.items[self.active_index].name.casefold():
        return
    self.active_index = next((i for i, item in enumerate(self.items) if needle in item.name.casefold()), -1)


class FMODEL_PoseLibrary(bpy.types.PropertyGroup):
    items: bpy.props.CollectionProperty(type=FMODEL_PoseItem)
    active_index: bpy.props.IntProperty(default=0)
    target: bpy.props.PointerProperty(name="目标骨架", type=bpy.types.Object, poll=armature_only, update=_legacy_target_changed)
    search: bpy.props.StringProperty(name="搜索姿势或来源", update=update_search)
    show_bake: bpy.props.BoolProperty(name="形态键烘焙", default=False)
    show_advanced: bpy.props.BoolProperty(name="高级设置", default=False)
    result: bpy.props.StringProperty()
    result_details: bpy.props.StringProperty()
    result_level: bpy.props.StringProperty(default='INFO')
    policy: bpy.props.EnumProperty(name="骨骼绑定", items=[
        ("ueformat", "UEFormat 原版绑定", "绑定同名骨骼，跳过未匹配骨骼"),
        ("strict", "旧设置", "按 UEFormat 原版绑定迁移"),
        ("name_retarget", "旧设置", "按 UEFormat 原版绑定迁移"),
    ], default="ueformat", options={'HIDDEN'})
    mesh_scope: bpy.props.EnumProperty(name="烘焙网格", items=[
        ("SELECTED", "选中的绑定网格", "选中要烘焙的网格，并保持骨架为活动对象"),
        ("ALL", "全部绑定网格", "场景内通过修改器绑定到目标骨架的全部网格"),
    ])


def refresh(scene):
    state = scene.fmodel_pose_library
    previous = None
    if 0 <= state.active_index < len(state.items):
        item = state.items[state.active_index]
        previous = (item.source, item.pose_index)
    state.items.clear()
    errors = []
    for text in sorted(bpy.data.texts, key=lambda t: t.name.casefold()):
        if not text.get(pose_assets.TAG):
            continue
        try:
            record, _, dto, _ = pose_assets.read_asset(text)
            for index, pose in enumerate(dto.poses):
                item = state.items.add()
                item.name = record["name"] + " / " + pose.name
                item.source_name = record["name"]
                item.pose_name = pose.name
                item.convention = record["convention"]
                item.source = text
                item.pose_index = index
                item.bone_count = len(pose.keys)
                item.curve_count = len(pose.curves)
                if (text, index) == previous:
                    state.active_index = len(state.items) - 1
        except Exception as exc:
            errors.append(text.name + ": " + str(exc))
    state.active_index = min(max(0, state.active_index), max(0, len(state.items) - 1))
    update_search(state, None)
    return errors


def selected(context):
    state = context.scene.fmodel_pose_library
    if not 0 <= state.active_index < len(state.items):
        raise ValueError("请先选择姿势条目")
    if state.search.casefold().strip() not in state.items[state.active_index].name.casefold():
        raise ValueError("当前姿势不在搜索结果中；请重新选择")
    return state, state.items[state.active_index]


def target_armature(context):
    return resolve_target(context)


def report_result(operator, context, message, warnings=(), level=None):
    state = context.scene.fmodel_pose_library
    state.result = message
    state.result_details = "\n".join(warnings)
    state.result_level = level or ('WARNING' if warnings else 'INFO')
    operator.report({state.result_level}, message + ("；" + "; ".join(warnings) if warnings else ""))


class FMODEL_UL_Poses(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        needle = data.search.casefold().strip()
        return ([self.bitflag_filter_item if not needle or needle in item.name.casefold() else 0
                 for item in getattr(data, propname)], [])

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row()
        row.label(text=item.pose_name, icon="POSE_HLT")
        origin = row.row()
        origin.alignment = 'RIGHT'
        origin.label(text=item.source_name)


class FMODEL_OT_RefreshPoses(bpy.types.Operator):
    bl_idname = "fmodel.refresh_pose_library"
    bl_label = "刷新姿势库"

    def execute(self, context):
        errors = refresh(context.scene)
        report_result(self, context, f"已列出 {len(context.scene.fmodel_pose_library.items)} 个姿势", errors)
        return {'FINISHED'}


class FMODEL_OT_CheckPose(bpy.types.Operator):
    bl_idname = "fmodel.check_pose_asset"
    bl_label = "检查匹配"
    bl_description = "检查当前源姿势与目标；不会改变角色或创建形态键"

    def execute(self, context):
        try:
            state, item = selected(context)
            result = pose_assets.inspect_pose(item.source, item.pose_index, target_armature(context), state.policy)
            report_result(self, context, f"{item.pose_name}：匹配 {result['bones']} 个骨骼、{result['curves']} 个曲线绑定",
                          result['warnings'])
            return {'FINISHED'}
        except Exception as exc:
            report_result(self, context, str(exc), level='ERROR')
            return {'CANCELLED'}


class FMODEL_OT_ApplyPose(bpy.types.Operator):
    bl_idname = "fmodel.apply_pose_asset"
    bl_label = "应用骨骼姿势"
    bl_description = "按参考姿势替换骨骼变换并应用匹配曲线；不插入关键帧，可撤销"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            state, item = selected(context)
            result = pose_assets.apply_pose(item.source, item.pose_index, target_armature(context), state.policy)
            message = f"{item.pose_name}：已应用 {result['bones']} 个骨骼、{result['curves']} 个曲线绑定"
            report_result(self, context, message, result['warnings'])
            return {'FINISHED'}
        except Exception as exc:
            report_result(self, context, str(exc), level='ERROR')
            return {'CANCELLED'}


class FMODEL_OT_BakePose(bpy.types.Operator):
    bl_idname = "fmodel.bake_pose_asset"
    bl_label = "烘焙为形态键"
    bl_description = "将此姿势的骨骼和曲线合成为每个网格的一个形态键；重新烘焙更新关联结果"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            state, item = selected(context)
            result = pose_assets.bake_pose(item.source, item.pose_index, target_armature(context),
                                          state.policy, state.mesh_scope == "SELECTED")
            report_result(self, context, f"{item.pose_name}：已生成/更新 {result['shape_keys']} 个形态键", result['warnings'])
            return {'FINISHED'}
        except Exception as exc:
            report_result(self, context, str(exc), level='ERROR')
            return {'CANCELLED'}


class FMODEL_OT_SavePoseLibrary(bpy.types.Operator, ExportHelper):
    bl_idname = "fmodel.save_pose_library"
    bl_label = "保存姿势源库"
    filename_ext = ".blend"
    filter_glob: bpy.props.StringProperty(default="*.blend", options={'HIDDEN'})

    def execute(self, context):
        temporary = None
        try:
            sources = {t for t in bpy.data.texts if t.get(pose_assets.TAG)}
            if not sources:
                raise ValueError("没有可保存的姿势源数据")
            for source in sources:
                pose_assets.read_asset(source)
            destination = os.path.abspath(bpy.path.abspath(self.filepath))
            if bpy.data.filepath and os.path.normcase(destination) == os.path.normcase(os.path.abspath(bpy.data.filepath)):
                raise ValueError("请选择独立的姿势库文件，不能覆盖当前场景")
            fd, temporary = tempfile.mkstemp(suffix=".blend", dir=os.path.dirname(destination))
            os.close(fd)
            bpy.data.libraries.write(temporary, sources, fake_user=True, compress=True)
            os.replace(temporary, destination)
            self.report({'INFO'}, f"已保存 {len(sources)} 个姿势源；无需原 UEPOSE 文件即可重新加载")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)


class FMODEL_OT_LoadPoseLibrary(bpy.types.Operator, ImportHelper):
    bl_idname = "fmodel.load_pose_library"
    bl_label = "加载姿势源库"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".blend"
    filter_glob: bpy.props.StringProperty(default="*.blend", options={'HIDDEN'})

    def execute(self, context):
        before = set(bpy.data.texts)
        originals = {t: t.as_string() for t in before if t.get(pose_assets.TAG) and t.library is None}
        incoming = []
        committed = False
        try:
            with bpy.data.libraries.load(bpy.path.abspath(self.filepath), link=False) as (source, target):
                target.texts = list(source.texts)
            incoming = list(set(bpy.data.texts) - before)
            records = []
            for text in incoming:
                if text.get(pose_assets.TAG):
                    record, _, _, _ = pose_assets.read_asset(text)
                    records.append(record)
            if not records:
                raise ValueError("该文件不包含有效的姿势源库")
            # Remove temporary appended records before publishing/deduplicating.
            for text in incoming:
                bpy.data.texts.remove(text)
            incoming.clear()
            for record in records:
                pose_assets.store_record(record)
            committed = True
            refresh(context.scene)
            self.report({'INFO'}, f"已加载 {len(records)} 个姿势源")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            if not committed:
                for text in list(set(bpy.data.texts) - before):
                    bpy.data.texts.remove(text)
                for text, payload in originals.items():
                    text.clear()
                    text.write(payload)


class FMODEL_OT_PoseSourceSettings(bpy.types.Operator):
    bl_idname = "fmodel.pose_source_settings"
    bl_label = "姿势源解释设置"
    bl_description = "修改所选源文件的缩放与解释约定；影响该源全部姿势，不改写原文件"
    bl_options = {'REGISTER', 'UNDO'}
    source_name: bpy.props.StringProperty(options={'HIDDEN'})
    scale: bpy.props.FloatProperty(name="单位缩放", default=.01, min=.0001, max=100)
    convention: bpy.props.EnumProperty(name="变换约定", items=[
        ('CUE4PARSE_ADDITIVE', 'CUE4Parse 加法姿势', '需与实际导出器约定相符'),
        ('UNSPECIFIED', '未知，仅保存源数据', '在确认约定前不应用或烘焙'),
    ])

    def invoke(self, context, event):
        try:
            _, item = selected(context)
            self.source_name = item.source.name
            record, _, _, _ = pose_assets.read_asset(item.source)
            self.scale = record['scale']
            self.convention = record['convention']
        except Exception as exc:
            report_result(self, context, str(exc), level='ERROR')
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            record, _, _, _ = pose_assets.read_asset(bpy.data.texts.get(self.source_name))
            record.update(scale=self.scale, convention=self.convention)
            pose_assets.store_record(record)
            refresh(context.scene)
            report_result(self, context, "已更新源姿势解释设置；已有烘焙结果需重新烘焙")
            return {'FINISHED'}
        except Exception as exc:
            report_result(self, context, str(exc), level='ERROR')
            return {'CANCELLED'}


class FMODEL_PT_PoseLibrary(bpy.types.Panel):
    bl_label = "姿势资产"
    bl_idname = "FMODEL_PT_pose_library"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'FModel'
    bl_parent_id = "FMODEL_PT_asset_library"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        return context.scene.fmodel_workflow.asset_kind == 'POSE'

    def draw(self, context):
        layout = self.layout
        state = context.scene.fmodel_pose_library
        layout.label(text="当前场景的姿势源资产", icon='FILE_BLEND')
        row = layout.row(align=True)
        row.operator("fmodel.import_anim_uepose", text="导入 UEPOSE", icon="IMPORT")
        row.operator("fmodel.refresh_pose_library", text="", icon="FILE_REFRESH")
        row = layout.row(align=True)
        row.operator("fmodel.load_pose_library", text="加载源库")
        layout.prop(state, "search", text="", icon='VIEWZOOM')
        layout.template_list("FMODEL_UL_Poses", "", state, "items", state, "active_index", rows=5)
        item = state.items[state.active_index] if 0 <= state.active_index < len(state.items) else None
        if not item:
            layout.label(text="没有匹配的姿势" if state.items else "先导入 UEPOSE 或加载姿势源库", icon='INFO')
            return
        details = layout.box()
        details.label(text=item.pose_name, icon='POSE_HLT')
        details.label(text="来源：" + item.source_name)
        details.label(text=f"{item.bone_count} 个骨骼 · {item.curve_count} 条曲线")
        target = target_armature(context)
        details.label(text="当前目标：" + (target.name if target else '未选择骨架'), icon='ARMATURE_DATA')
        controls = details.column()
        controls.enabled = bool(target and item.source and item.convention != 'UNSPECIFIED')
        row = controls.row(align=True)
        row.operator('fmodel.check_pose_asset', text='检查匹配', icon='VIEWZOOM')
        row = controls.row()
        row.scale_y = 1.25
        row.operator("fmodel.apply_pose_asset", text="应用到骨架", icon="POSE_HLT")
        details.label(text="替换为参考姿势上的结果；不插入关键帧")
        if item.convention == 'UNSPECIFIED':
            details.label(text="请在高级设置中确认变换约定", icon='INFO')
        layout.prop(state, 'show_bake', icon='TRIA_DOWN' if state.show_bake else 'TRIA_RIGHT', emboss=False)
        if state.show_bake:
            bake = layout.box()
            bake.prop(state, "mesh_scope")
            meshes = pose_assets.bound_meshes(target, state.mesh_scope == 'SELECTED') if target else []
            bake.label(text=f"目标网格：{len(meshes)} 个")
            for mesh in meshes[:3]:
                bake.label(text=mesh.name, icon='MESH_DATA')
            if len(meshes) > 3:
                bake.label(text=f"另有 {len(meshes) - 3} 个网格")
            row = bake.row()
            row.enabled = bool(meshes and item.source and item.convention != 'UNSPECIFIED')
            row.operator("fmodel.bake_pose_asset", icon="SHAPEKEY_DATA")
            bake.label(text="骨骼与曲线合成；再次烘焙更新结果")
        layout.prop(state, 'show_advanced', icon='TRIA_DOWN' if state.show_advanced else 'TRIA_RIGHT', emboss=False)
        if state.show_advanced:
            layout.operator('fmodel.pose_source_settings', icon='PREFERENCES')
            layout.label(text="源数据随场景保存，可单独保存源库")
        if state.result:
            result = layout.box()
            result.label(text='上次操作结果', icon='ERROR' if state.result_level == 'ERROR' else 'INFO')
            width = max(16, int(context.region.width / 14))
            for line in textwrap.wrap(state.result, width):
                result.label(text=line)
            for warning in state.result_details.splitlines():
                for line in textwrap.wrap(warning, width):
                    result.label(text=line)


CLASSES = (FMODEL_PoseItem, FMODEL_PoseLibrary, FMODEL_UL_Poses,
           FMODEL_OT_RefreshPoses, FMODEL_OT_CheckPose, FMODEL_OT_ApplyPose, FMODEL_OT_BakePose,
           FMODEL_OT_PoseSourceSettings,
           FMODEL_OT_SavePoseLibrary, FMODEL_OT_LoadPoseLibrary, FMODEL_PT_PoseLibrary)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.fmodel_pose_library = bpy.props.PointerProperty(type=FMODEL_PoseLibrary)


def unregister():
    del bpy.types.Scene.fmodel_pose_library
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
