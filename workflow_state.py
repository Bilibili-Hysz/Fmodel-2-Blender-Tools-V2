"""Blender persistence and explicit navigation for the shared work context."""

import bpy
from bpy.app.handlers import persistent

from .core.workflow import migrate_scene, set_target, synchronize_target


def _target_changed(self, context):
    self.initialized = True
    synchronize_target(self.id_data, self.target)


class FMODEL_WorkflowState(bpy.types.PropertyGroup):
    initialized: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    target: bpy.props.PointerProperty(
        name="目标骨架", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE' and self.id_data.objects.get(obj.name) == obj,
        update=_target_changed,
        description="动画与姿势的固定目标；选择其他场景对象不会改变此目标",
    )
    asset_kind: bpy.props.EnumProperty(name="资产类型", items=[
        ('MODEL', '模型', '浏览当前库的模型'),
        ('ANIMATION', '动画', '浏览已扫描的动画资产'),
        ('POSE', '姿势', '浏览当前场景保存的姿势源资产'),
    ], default='ANIMATION')
    show_builder: bpy.props.BoolProperty(name="批量建立动画库", default=False)


class FMODEL_OT_SetWorkTarget(bpy.types.Operator):
    bl_idname = "fmodel.set_work_target"
    bl_label = "使用选中骨架"
    bl_description = "将当前选中的骨架固定为动画、姿势和单骨架建库的目标"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        set_target(context.scene, context.active_object)
        self.report({'INFO'}, "工作目标：" + context.active_object.name)
        return {'FINISHED'}


class FMODEL_OT_OpenWorkflow(bpy.types.Operator):
    bl_idname = "fmodel.open_workflow"
    bl_label = "切换工作区"
    destination: bpy.props.EnumProperty(items=[
        ('BUILD', '批量建库', ''), ('ASSETS', '浏览动画资产', ''),
        ('EDIT_BUILD', '调整构建设置', ''),
    ])

    def execute(self, context):
        workspace = context.scene.fmodel_workflow
        if self.destination in {'BUILD', 'EDIT_BUILD'}:
            workspace.show_builder = True
            state = context.scene.pipeline_wizard
            state.current_step = max(2, state.current_step)
            if self.destination == 'EDIT_BUILD':
                state.lib_completed = False
                state.import_completed = False
                state.current_step = 4
        else:
            workspace.show_builder = False
            workspace.asset_kind = 'ANIMATION'
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


@persistent
def restore_workspaces(_unused=None):
    for scene in bpy.data.scenes:
        migrate_scene(scene)


CLASSES = (FMODEL_WorkflowState, FMODEL_OT_SetWorkTarget, FMODEL_OT_OpenWorkflow)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.fmodel_workflow = bpy.props.PointerProperty(type=FMODEL_WorkflowState)
    if restore_workspaces not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(restore_workspaces)


def unregister():
    if restore_workspaces in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(restore_workspaces)
    del bpy.types.Scene.fmodel_workflow
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
