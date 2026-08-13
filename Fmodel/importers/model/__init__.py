# FModel_Tools/Fmodel/importers/model/__init__.py
# UEFormat 模型导入器

import bpy
import importlib
from . import operator_model_ueformat

if "bpy" in locals():
    importlib.reload(operator_model_ueformat)


def register():
    operator_model_ueformat.register()


def unregister():
    operator_model_ueformat.unregister()
