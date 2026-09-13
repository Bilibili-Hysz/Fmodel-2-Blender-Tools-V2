# FModel_Tools/Universal_tools/__init__.py
# 通用工具模块 — 网格、骨架、动画工具

import bpy
import importlib
from . import mesh
from . import skeleton
from . import Animations

if "bpy" in locals():
    importlib.reload(mesh)
    importlib.reload(skeleton)
    importlib.reload(Animations)


def register():
    mesh.register()
    skeleton.register()
    Animations.register()


def unregister():
    Animations.unregister()
    skeleton.unregister()
    mesh.unregister()
