# FModel_Tools/Fmodel/__init__.py
# Fmodel 模块 — UEFormat 导入与 Folder-only 动画管线

import bpy
import importlib
from . import importers
from . import Pipeline

if "bpy" in locals():
    importlib.reload(importers)
    importlib.reload(Pipeline)


def register():
    importers.register()
    Pipeline.register()


def unregister():
    Pipeline.unregister()
    importers.unregister()
