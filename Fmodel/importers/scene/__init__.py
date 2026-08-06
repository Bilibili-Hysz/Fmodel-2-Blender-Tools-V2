"""UEScene importer entry points."""

import bpy
import importlib

from . import operator_scene_ueformat

if "bpy" in locals():
    importlib.reload(operator_scene_ueformat)


def register():
    operator_scene_ueformat.register()


def unregister():
    operator_scene_ueformat.unregister()
