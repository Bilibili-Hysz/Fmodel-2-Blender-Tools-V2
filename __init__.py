# FModel_Tools/__init__.py
# 插件入口 — 模块注册
bl_info = {
    "name": "FModel Tools Next",
    "author": "Your Name",
    "version": (8, 1, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > FModel",
    "description": "专为 UE/FModel 设计的完整资产管线工具集",
    "category": "Import-Export",
}

import bpy
import importlib

if __package__:
    from . import core
    from . import workflow_state
    from . import ui
    from . import Fmodel
    from . import Assets
    from . import Universal_tools
    from . import auto_handlers

if __package__ and "bpy" in locals():
    importlib.reload(core)
    importlib.reload(workflow_state)
    importlib.reload(ui)
    importlib.reload(Fmodel)
    importlib.reload(Assets)
    importlib.reload(Universal_tools)
    importlib.reload(auto_handlers)


def _restore_scene_state():
    """Restore saved scene state on the lifecycle timer, outside RestrictBlend."""
    Assets.Anims.anim_manager.restore_manager_backup()
    workflow_state.restore_workspaces()


def register():
    # 模块化注册
    core.register()
    workflow_state.register()
    ui.register()
    Fmodel.register()
    Assets.register()
    Universal_tools.register()

    # 注册自动触发器
    auto_handlers.register_handlers()
    auto_handlers.defer_workspace_restore(_restore_scene_state)


def unregister():
    auto_handlers.unregister_handlers()

    Universal_tools.unregister()
    Assets.unregister()
    Fmodel.unregister()
    ui.unregister()
    workflow_state.unregister()
    core.unregister()


if __name__ == "__main__":
    register()
