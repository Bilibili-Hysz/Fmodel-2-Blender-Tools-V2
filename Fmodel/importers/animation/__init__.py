# FModel_Tools/Fmodel/importers/animation/__init__.py
# UEFormat 动画导入器：UEAnim / UEPose

import bpy
import importlib
from . import operator_anim_ueformat

if "bpy" in locals():
    importlib.reload(operator_anim_ueformat)


def register():
    operator_anim_ueformat.register()


def unregister():
    operator_anim_ueformat.unregister()
