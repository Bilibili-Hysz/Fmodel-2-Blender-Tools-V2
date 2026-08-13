# FModel_Tools/Fmodel/importers/__init__.py
# UEFormat-only asset importers: model, animation and pose.

import bpy
import importlib
from . import model
from . import animation

if "bpy" in locals():
    importlib.reload(model)
    importlib.reload(animation)


def register():
    model.register()
    animation.register()


def unregister():
    animation.unregister()
    model.unregister()
