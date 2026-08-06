# FModel_Tools/Fmodel/__init__.py
# Fmodel 模块 — 导入导出与管线

import bpy
import importlib
from . import importers
from . import Pipeline
from . import project_assets

if "bpy" in locals():
    importlib.reload(importers)
    importlib.reload(Pipeline)
    importlib.reload(project_assets)


def register():
    importers.register()
    Pipeline.register()
    project_assets.register()


def unregister():
    project_assets.unregister()
    Pipeline.unregister()
    importers.unregister()
