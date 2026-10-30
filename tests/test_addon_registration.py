"""Restricted addon registration and lifecycle-owned deferred scene restoration.

Host-Python tests execute the actual entrypoint/restore functions extracted by
AST plus the real auto-handler timer code. The full import/registration path is
covered separately by the isolated real-Blender install smoke in the workspace.
"""
import ast
import builtins
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATE_KEY = "_fmodel_tools_next_auto_handler_lifecycle"


def function_from_source(path, name, namespace):
    node = next(node for node in ast.parse(path.read_bytes()).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    node.decorator_list = []
    tree = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace[name]


class FakeTimers:
    def __init__(self):
        self.callbacks = set()
        self.intervals = {}

    def register(self, callback, *, first_interval):
        self.callbacks.add(callback)
        self.intervals[callback] = first_interval

    def unregister(self, callback):
        self.callbacks.remove(callback)

    def fire(self, callback):
        self.unregister(callback)
        return callback()


class RestrictedData:
    def __init__(self):
        self.restricted = True
        self.reads = 0
        self.actual_scenes = [object(), object()]

    @property
    def scenes(self):
        self.reads += 1
        if self.restricted:
            raise AttributeError("'_RestrictData' object has no attribute 'scenes'")
        return self.actual_scenes


@pytest.fixture
def lifecycle(monkeypatch):
    monkeypatch.delitem(builtins.__dict__, STATE_KEY, raising=False)
    bpy = ModuleType("bpy")
    handlers = ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    handlers.load_post = []
    handlers.depsgraph_update_post = []
    bpy.app = NS(handlers=handlers, timers=FakeTimers())
    bpy.context = NS(preferences=NS(addons={}))
    bpy.data = RestrictedData()
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "bpy.app.handlers", handlers)
    def load_auto(name):
        spec = importlib.util.spec_from_file_location(name, ROOT / "auto_handlers.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._vendor_loader_module = lambda: NS(unregister_vendor=Mock())
        return module
    auto = load_auto("registration_lifecycle_auto")
    migrated = []
    restore = function_from_source(ROOT / "workflow_state.py", "restore_workspaces",
                                   {"bpy": bpy, "migrate_scene": migrated.append})
    registration_order = []
    namespace = {name: NS(register=lambda name=name: registration_order.append(name))
                 for name in ("core", "workflow_state", "ui", "Fmodel", "Assets", "Universal_tools")}
    namespace["workflow_state"].restore_workspaces = restore
    namespace["Assets"].Anims = NS(anim_manager=NS(restore_manager_backup=Mock()))
    namespace["auto_handlers"] = auto
    restore_scene = function_from_source(ROOT / "__init__.py", "_restore_scene_state", namespace)
    register = function_from_source(ROOT / "__init__.py", "register", namespace)
    state = NS(bpy=bpy, auto=auto, load_auto=load_auto, restore=restore_scene, migrated=migrated,
               register=register, registration_order=registration_order,
               manager_restore=namespace["Assets"].Anims.anim_manager.restore_manager_backup)
    yield state
    auto.unregister_handlers()


def restore_timer(state):
    return next(timer for timer in state.auto._registered_timers if any(
        cell.cell_contents is state.restore for cell in (timer.__closure__ or ())))


def test_registration_never_reads_restricted_scenes_and_later_restores(lifecycle):
    state = lifecycle
    state.register()
    assert state.bpy.data.reads == 0
    assert state.migrated == []
    state.manager_restore.assert_not_called()
    assert state.registration_order == ["core", "workflow_state", "ui", "Fmodel", "Assets", "Universal_tools"]
    assert len(state.auto._registered_timers) == 2  # Vendor load plus scene restore.
    timer = restore_timer(state)
    assert state.bpy.app.timers.intervals[timer] == 0.0
    state.bpy.data.restricted = False
    assert state.bpy.app.timers.fire(timer) is None
    assert state.migrated == state.bpy.data.actual_scenes
    assert state.bpy.data.reads == 1
    state.manager_restore.assert_called_once_with()
    assert timer not in state.auto._registered_timers


def test_disable_cancels_restore_before_restricted_data_can_be_read(lifecycle):
    state = lifecycle
    state.register()
    timer = restore_timer(state)
    state.auto.unregister_handlers()
    assert timer not in state.bpy.app.timers.callbacks
    assert timer() is None
    assert state.bpy.data.reads == 0
    assert not state.migrated


def test_reload_cancels_old_generation_restore(lifecycle):
    state = lifecycle
    state.register()
    old_timer = restore_timer(state)
    reloaded = state.load_auto("registration_lifecycle_auto_reloaded")
    reloaded.register_handlers()
    try:
        assert old_timer not in state.bpy.app.timers.callbacks
        assert old_timer() is None
        assert state.bpy.data.reads == 0
        new_timer = reloaded.defer_workspace_restore(state.restore)
        state.bpy.data.restricted = False
        assert state.bpy.app.timers.fire(new_timer) is None
        assert state.migrated == state.bpy.data.actual_scenes
    finally:
        reloaded.unregister_handlers()


def test_deferred_restore_does_not_hide_actual_migration_error(lifecycle):
    state = lifecycle
    state.auto.register_handlers()
    def fail():
        raise ValueError("genuine migration failure")
    timer = state.auto.defer_workspace_restore(fail)
    with pytest.raises(ValueError, match="genuine migration failure"):
        state.bpy.app.timers.fire(timer)
    assert timer not in state.auto._registered_timers


def test_existing_load_post_restore_is_still_persistent():
    source = ast.parse((ROOT / "workflow_state.py").read_bytes())
    restore = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "restore_workspaces")
    assert [ast.unparse(decorator) for decorator in restore.decorator_list] == ["persistent"]
    registration = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "register")
    assert "bpy.app.handlers.load_post.append(restore_workspaces)" in ast.unparse(registration)


def test_anim_manager_register_defers_backup_without_reading_restricted_context():
    namespace = {"classes": (), "bpy": NS(
        context=NS(), data=RestrictedData(), types=NS(Scene=NS()),
        props=NS(PointerProperty=lambda **_: object()),
        app=NS(handlers=NS(depsgraph_update_post=[]))),
        "AnimManagerProps": object(), "auto_sync_active_skeleton": Mock(),
        "_manager_backup": {"skeletons": ["SavedSkeleton"], "tree": []}}
    register = function_from_source(ROOT / "Assets/Anims/anim_manager.py", "register", namespace)
    register()
    assert namespace["_manager_backup"]["skeletons"] == ["SavedSkeleton"]
    assert hasattr(namespace["bpy"].types.Scene, "fmodel_anim_manager")
    assert namespace["auto_sync_active_skeleton"] in namespace["bpy"].app.handlers.depsgraph_update_post


def test_deferred_manager_backup_restores_saved_rows():
    class Rows(list):
        def add(self):
            row = NS()
            self.append(row)
            return row
    manager = NS(skeleton_items=Rows([NS(name="OldSkeleton")]),
                 tree_items=Rows([NS(name="OldAnimation", node_id="old-node")]))
    namespace = {"bpy": NS(context=NS(scene=NS(fmodel_anim_manager=manager))),
                 "_manager_backup": {"skeletons": ["SavedSkeleton"],
                                     "tree": [{"name": "SavedAnimation", "node_id": "saved-node"}]}}
    restore = function_from_source(ROOT / "Assets/Anims/anim_manager.py", "restore_manager_backup", namespace)
    assert restore() is None
    assert [row.name for row in manager.skeleton_items] == ["SavedSkeleton"]
    assert manager.tree_items[0].node_id == "saved-node"


def test_deferred_backup_is_consumed_and_never_overwrites_later_user_changes():
    class Rows(list):
        def add(self):
            row = NS()
            self.append(row)
            return row
    manager = NS(skeleton_items=Rows(), tree_items=Rows())
    namespace = {"bpy": NS(context=NS(scene=NS(fmodel_anim_manager=manager))),
                 "_manager_backup": {"skeletons": ["SavedSkeleton"], "tree": []}}
    restore = function_from_source(ROOT / "Assets/Anims/anim_manager.py", "restore_manager_backup", namespace)
    assert restore() is None
    assert namespace["_manager_backup"] is None
    manager.skeleton_items.add().name = "LaterUserChoice"
    assert restore() is None
    assert [row.name for row in manager.skeleton_items] == ["SavedSkeleton", "LaterUserChoice"]
