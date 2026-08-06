# FModel_Tools/Universal_tools/__init__.py
# 通用工具模块 — 网格、骨架、材质、动画工具

import bpy
import importlib
from . import mesh
from . import skeleton
from . import material
from . import Animations

if "bpy" in locals():
    importlib.reload(mesh)
    importlib.reload(skeleton)
    importlib.reload(material)
    importlib.reload(Animations)


def register():
    mesh.register()
    skeleton.register()
    material.register()
    Animations.register()


def unregister():
    Animations.unregister()
    material.unregister()
    skeleton.unregister()
    mesh.unregister()
