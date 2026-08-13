"""Lifecycle ownership contracts for host-Python auto handlers."""

import importlib.util
import os
import sys
import types
import unittest
import builtins
from unittest import mock

from core.vendor_loader import VendorErrorCode, VendorLoadError, WheelRuntime, WheelSpec, wheel_is_compatible
from core.pipeline_activity import pipeline_activity


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIFECYCLE_REGISTRY_KEY = "_fmodel_tools_next_auto_handler_lifecycle"


def _fake_ueformat_module(*, version=(1, 2, 0)):
    class UEModelOptions:
        def __init__(
            self,
            import_fmodel_materials=True,
            build_fmodel_material_nodes=True,
            load_fmodel_embedded_textures=True,
            load_fmodel_external_textures=True,
        ):
            pass

    class UEAnimOptions:
        def __init__(self, strict_fmodel_skeleton=True):
            pass

    class UEPoseOptions:
        def __init__(self, strict_fmodel_skeleton=True):
            pass

    op = types.SimpleNamespace()
    return types.SimpleNamespace(
        __name__="io_scene_ueformat",
        __file__=os.path.join(ROOT, "vendor", "io_scene_ueformat", "__init__.py"),
        bl_info={"name": "UE Format", "version": version},
        op=op,
        options=types.SimpleNamespace(
            UEModelOptions=UEModelOptions,
            UEAnimOptions=UEAnimOptions,
            UEPoseOptions=UEPoseOptions,
        ),
    )


def _fake_vendor_handle(module, *, added_paths=()):
    return types.SimpleNamespace(
        module=module,
        spec=types.SimpleNamespace(
            vendor_id="io_scene_ueformat",
            version_text="1.2.0",
        ),
        source="bundled",
        module_name="io_scene_ueformat",
        added_paths=tuple(added_paths),
        cache_root=None,
        registered=False,
        source_path=getattr(module, "__file__", ""),
    )


def _fake_vendor_loader():
    loader = types.SimpleNamespace(
        register_vendor=mock.Mock(),
        unregister_vendor=mock.Mock(),
    )

    def register(handle, *, headless=True):
        handle.module.register_headless()
        handle.registered = True

    def unregister(handle):
        handle.module.unregister()
        handle.registered = False

    loader.register_vendor.side_effect = register
    loader.unregister_vendor.side_effect = unregister
    return loader


class FakeTimers:
    def __init__(self):
        self.registered = []
        self.unregistered = []
        self.callbacks = set()

    def register(self, callback, *, first_interval):
        self.registered.append((callback, first_interval))
        self.callbacks.add(callback)

    def unregister(self, callback):
        self.unregistered.append(callback)
        self.callbacks.remove(callback)

    def fire(self, callback):
        if callback not in self.callbacks:
            return None
        self.callbacks.remove(callback)
        return callback()


class FakeApp:
    __slots__ = ("driver_namespace", "handlers", "timers")

    def __init__(self, handlers, timers):
        self.driver_namespace = {}
        self.handlers = handlers
        self.timers = timers


class AutoHandlerLifecycleTests(unittest.TestCase):
    def setUp(self):
        builtins.__dict__.pop(LIFECYCLE_REGISTRY_KEY, None)
        self.previous_modules = {
            name: sys.modules.get(name) for name in ("bpy", "bpy.app", "bpy.app.handlers")
        }
        self.timers = FakeTimers()
        self.load_post = []
        self.depsgraph_update_post = []
        bpy = types.ModuleType("bpy")
        handlers = types.ModuleType("bpy.app.handlers")
        handlers.persistent = lambda callback: callback
        handlers.load_post = self.load_post
        handlers.depsgraph_update_post = self.depsgraph_update_post
        bpy.app = FakeApp(handlers, self.timers)
        bpy.context = types.SimpleNamespace(
            scene=types.SimpleNamespace(), preferences=types.SimpleNamespace(addons={})
        )
        bpy.data = types.SimpleNamespace(objects={})
        bpy.ops = types.SimpleNamespace(fmodel=types.SimpleNamespace(scan_anims=mock.Mock()))
        bpy.types = types.SimpleNamespace()
        sys.modules["bpy"] = bpy
        sys.modules["bpy.app"] = types.ModuleType("bpy.app")
        sys.modules["bpy.app.handlers"] = handlers
        spec = importlib.util.spec_from_file_location("test_auto_handlers", os.path.join(ROOT, "auto_handlers.py"))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def tearDown(self):
        builtins.__dict__.pop(LIFECYCLE_REGISTRY_KEY, None)
        for name, module in self.previous_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    @unittest.skip("Indexed Project lifecycle retired; kept as historical compatibility coverage")
    def test_project_index_refresh_adapter_calls_compatibility_bridge(self):
        scene = types.SimpleNamespace(pipeline_wizard=object())
        refresh = mock.Mock(return_value={"state": "ready"})
        fmodel = types.ModuleType("Fmodel")
        fmodel.__path__ = []
        project_assets = types.ModuleType("Fmodel.project_assets")
        project_assets.refresh_pipeline_project_index = refresh

        with mock.patch.dict(
            sys.modules,
            {"Fmodel": fmodel, "Fmodel.project_assets": project_assets},
        ):
            result = self.module._refresh_pipeline_project_index_state(scene)

        self.assertEqual(result, {"state": "ready"})
        refresh.assert_called_once_with(scene)

    def test_init_environment_does_not_refresh_retired_pipeline_index(self):
        scene = types.SimpleNamespace(pipeline_wizard=object())
        self.module.bpy.context.scene = scene
        utilities = types.ModuleType("core.utils")
        utilities.cleanup_dynamic_libraries = mock.Mock()
        utilities.mount_dynamic_library = mock.Mock()

        with mock.patch.dict(sys.modules, {"core.utils": utilities}):
            self.assertIsNone(self.module._init_environment())

        utilities.cleanup_dynamic_libraries.assert_called_once_with()
        utilities.mount_dynamic_library.assert_not_called()
        self.module.bpy.ops.fmodel.scan_anims.assert_not_called()

    def test_depsgraph_updates_check_only_changed_objects(self):
        class GuardedObjects(list):
            allow_iteration = True

            def __iter__(self):
                if not self.allow_iteration:
                    raise AssertionError("depsgraph path must not enumerate bpy.data.objects")
                return super().__iter__()

        def fake_object(name, object_type, *keys):
            obj = types.SimpleNamespace(name=name, type=object_type)
            obj.keys = lambda: keys
            return obj

        existing = fake_object("Existing", "MESH", "FModel_Source")
        objects = GuardedObjects([existing])
        self.module.bpy.data.objects = objects
        self.module._first_run = True
        self.module.on_depsgraph_update_post(None, types.SimpleNamespace(updates=[]))

        imported = fake_object("Imported", "MESH", "FModel_Source")
        objects.allow_iteration = False
        with mock.patch.object(self.module, "_debounced_scan") as scan:
            self.module.on_depsgraph_update_post(
                None, types.SimpleNamespace(updates=[types.SimpleNamespace(id=imported)])
            )

        scan.assert_called_once_with()

    def test_depsgraph_update_for_known_object_does_not_scan(self):
        def fake_object(name, object_type, *keys):
            obj = types.SimpleNamespace(name=name, type=object_type)
            obj.keys = lambda: keys
            return obj

        existing = fake_object("Existing", "ARMATURE", "FModel_Source")
        self.module.bpy.data.objects = [existing]
        self.module._first_run = True
        self.module.on_depsgraph_update_post(None, types.SimpleNamespace(updates=[]))

        with mock.patch.object(self.module, "_debounced_scan") as scan:
            self.module.on_depsgraph_update_post(
                None, types.SimpleNamespace(updates=[types.SimpleNamespace(id=existing)])
            )

        scan.assert_not_called()

    def test_scan_is_deferred_until_pipeline_activity_finishes(self):
        self.module.bpy.context.scene.fmodel_anim_manager = object()
        self.module._last_scan_time = 0

        with pipeline_activity():
            self.module._debounced_scan()
            self.assertTrue(self.module._pending_scan)
            self.module.bpy.ops.fmodel.scan_anims.assert_not_called()

        self.module._delayed_scan()
        self.module.bpy.ops.fmodel.scan_anims.assert_called_once_with()

    def test_register_replaces_stale_callbacks_and_unregisters_owned_timers(self):
        stale = lambda _: None
        unrelated = lambda _: None
        stale._fmodel_owner = "fmodel_auto_handler"
        self.load_post.extend([stale, unrelated])
        self.module.register_handlers()
        dependency_timer = self.timers.registered[-1][0]
        self.module.on_file_load_post(None)
        load_timer = self.timers.registered[-1][0]
        self.module.unregister_handlers()
        self.assertNotIn(stale, self.load_post)
        self.assertIn(unrelated, self.load_post)
        self.assertIn(dependency_timer, self.timers.unregistered)
        self.assertIn(load_timer, self.timers.unregistered)

    def test_vendor_dependency_check_runs_on_next_timer_turn(self):
        self.module.register_handlers()
        _callback, first_interval = self.timers.registered[-1]
        self.assertLessEqual(first_interval, 0.1)


    def test_register_persists_lifecycle_state_in_builtins_registry(self):
        with self.assertRaises(AttributeError):
            self.module.bpy.app.unsupported_lifecycle_state = object()

        self.module.register_handlers()

        state = builtins.__dict__[LIFECYCLE_REGISTRY_KEY]
        self.assertEqual(state["generation"], self.module._lifecycle_generation)
        self.assertIs(state["registered_timers"], self.module._registered_timers)
        self.assertIs(state["vendor_registered"], self.module._vendor_registered)
        self.assertIs(state["added_paths"], self.module._added_paths)
        self.assertNotIn(LIFECYCLE_REGISTRY_KEY, self.module.bpy.app.driver_namespace)

    def test_stale_timer_cannot_run_after_unregister_or_reload(self):
        self.module.register_handlers()
        stale_timer = self.timers.registered[-1][0]
        self.module.unregister_handlers()
        stale_timer()
        self.module.bpy.ops.fmodel.scan_anims.assert_not_called()

        spec = importlib.util.spec_from_file_location("reloaded_auto_handlers", os.path.join(ROOT, "auto_handlers.py"))
        reloaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reloaded)
        reloaded.register_handlers()
        stale_timer()
        self.module.bpy.ops.fmodel.scan_anims.assert_not_called()

    def test_reload_register_cancels_prior_generation_timers(self):
        self.module.register_handlers()
        old_timer = self.timers.registered[-1][0]

        spec = importlib.util.spec_from_file_location(
            "reloaded_auto_handlers", os.path.join(ROOT, "auto_handlers.py")
        )
        reloaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reloaded)
        reloaded.register_handlers()

        dependency_timer = self.timers.registered[-1][0]
        self.assertIn(old_timer, self.timers.unregistered)
        self.assertEqual(reloaded._registered_timers, {dependency_timer})
        self.assertEqual(self.timers.callbacks, {dependency_timer})

    def test_reload_unregisters_prior_owned_timers_and_vendors_only(self):
        self.module.register_handlers()
        old_timer = self.timers.registered[-1][0]
        vendor_path = os.path.join(ROOT, "vendor", "io_scene_ueformat")
        vendor = types.SimpleNamespace(
            __file__=os.path.join(vendor_path, "__init__.py"), unregister=mock.Mock()
        )
        self.module._vendor_registered["io_scene_ueformat"] = (vendor, vendor_path)
        external_vendor = types.SimpleNamespace(unregister=mock.Mock())

        spec = importlib.util.spec_from_file_location(
            "reloaded_auto_handlers", os.path.join(ROOT, "auto_handlers.py")
        )
        reloaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reloaded)
        reloaded.unregister_handlers()

        self.assertIn(old_timer, self.timers.unregistered)
        vendor.unregister.assert_called_once_with()
        external_vendor.unregister.assert_not_called()

    def test_reload_unregisters_prior_owned_paths_only(self):
        owned_path = os.path.join(ROOT, "owned-vendor-parent")
        third_party_path = os.path.join(ROOT, "third-party-path")
        sys.path.insert(0, third_party_path)
        vendor = types.SimpleNamespace(
            __name__="io_scene_ueformat",
            __file__=os.path.join(ROOT, "vendor", "io_scene_ueformat", "__init__.py"),
            register_headless=mock.Mock(),
            unregister=mock.Mock(),
        )
        handle = _fake_vendor_handle(vendor, added_paths=(owned_path,))
        loader = _fake_vendor_loader()
        def load_vendor(_bundle_path):
            sys.path.insert(0, owned_path)
            return handle

        with (
            mock.patch.object(self.module.os.path, "isdir", return_value=True),
            mock.patch.object(self.module, "_load_bundled_vendor", side_effect=load_vendor),
            mock.patch.object(self.module, "_vendor_loader_module", return_value=loader),
            mock.patch.object(self.module, "_is_current_ueformat_addon", return_value=True),
            mock.patch.object(self.module, "_vendor_already_in_registry", return_value=False),
        ):
            self.module._ensure_dependency_addons()
        self.assertIn(owned_path, sys.path)

        spec = importlib.util.spec_from_file_location(
            "reloaded_auto_handlers", os.path.join(ROOT, "auto_handlers.py")
        )
        reloaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reloaded)
        with mock.patch.object(reloaded, "_vendor_loader_module", return_value=loader):
            reloaded.unregister_handlers()

        self.assertNotIn(owned_path, sys.path)
        self.assertIn(third_party_path, sys.path)
        loader.unregister_vendor.assert_called_once_with(handle)
        self.assertEqual(reloaded._added_paths, set())
        sys.path.remove(third_party_path)

    def test_register_is_idempotent_and_resets_debounce_state(self):
        self.module._pending_scan = True
        self.module.register_handlers()
        generation = self.module._lifecycle_generation
        timer = self.timers.registered[-1][0]
        self.module.register_handlers()
        self.assertFalse(self.module._pending_scan)
        self.assertEqual(generation, self.module._lifecycle_generation)
        self.assertEqual({timer}, self.timers.callbacks)

    def test_preexisting_vendor_is_observed_not_unregistered(self):
        vendor_path = os.path.join(ROOT, "vendor", "io_scene_ueformat")
        vendor = types.SimpleNamespace(
            __name__="io_scene_ueformat",
            __file__=os.path.join(vendor_path, "__init__.py"),
            register_headless=mock.Mock(),
            unregister=mock.Mock(),
        )
        handle = _fake_vendor_handle(vendor)
        loader = _fake_vendor_loader()
        with (
            mock.patch.object(self.module.os.path, "isdir", return_value=True),
            mock.patch.object(self.module, "_load_bundled_vendor", return_value=handle),
            mock.patch.object(self.module, "_vendor_loader_module", return_value=loader),
            mock.patch.object(self.module, "_is_current_ueformat_addon", return_value=True),
            mock.patch.object(self.module, "_vendor_already_in_registry", return_value=True),
        ):
            self.module._ensure_dependency_addons()
            self.module._unregister_vendor_addons()
        vendor.register_headless.assert_not_called()
        vendor.unregister.assert_not_called()
        loader.register_vendor.assert_not_called()
        loader.unregister_vendor.assert_not_called()
        self.assertIn("io_scene_ueformat", self.module._vendor_observed)
        self.assertNotIn("io_scene_ueformat", self.module._vendor_registered)

    def test_vendor_wheel_selection_accepts_pure_python_and_matching_runtime_only(self):
        def wheel(filename, python_tag, abi_tag, platform_tag):
            return WheelSpec(filename, python_tag, abi_tag, platform_tag, "0" * 64)

        runtime = WheelRuntime("cp313", "cp313", "win_amd64")
        self.assertTrue(wheel_is_compatible(
            wheel("helper-1.0-py3-none-any.whl", "py3", "none", "any"), runtime
        ))
        self.assertTrue(wheel_is_compatible(
            wheel("zstandard-0.25.0-cp313-cp313-win_amd64.whl", "cp313", "cp313", "win_amd64"),
            runtime,
        ))
        self.assertFalse(wheel_is_compatible(
            wheel("zstandard-0.25.0-cp312-cp312-win_amd64.whl", "cp312", "cp312", "win_amd64"),
            runtime,
        ))
        self.assertFalse(wheel_is_compatible(
            wheel("zstandard-0.25.0-cp313-cp313-manylinux2014_x86_64.whl", "cp313", "cp313", "manylinux2014_x86_64"),
            runtime,
        ))


    def test_bundled_ueformat_uses_headless_registration(self):
        vendor_path = os.path.join(ROOT, "vendor", "io_scene_ueformat")
        vendor = types.SimpleNamespace(
            __name__="io_scene_ueformat",
            __file__=os.path.join(vendor_path, "__init__.py"),
            register_headless=mock.Mock(),
            register=mock.Mock(),
            unregister=mock.Mock(),
        )
        handle = _fake_vendor_handle(vendor, added_paths=(os.path.join(ROOT, "cache"),))
        loader = _fake_vendor_loader()
        with (
            mock.patch.object(self.module.os.path, "isdir", return_value=True),
            mock.patch.object(self.module, "_load_bundled_vendor", return_value=handle),
            mock.patch.object(self.module, "_vendor_loader_module", return_value=loader),
            mock.patch.object(self.module, "_vendor_already_in_registry", return_value=False),
            mock.patch.object(self.module, "_is_current_ueformat_addon", return_value=True),
        ):
            self.module._ensure_dependency_addons()

        loader.register_vendor.assert_called_once_with(handle, headless=True)
        vendor.register_headless.assert_called_once_with()
        vendor.register.assert_not_called()
        self.assertIs(self.module._vendor_registered["io_scene_ueformat"], handle)

        with mock.patch.object(self.module, "_vendor_loader_module", return_value=loader):
            self.module._unregister_vendor_addons()
        loader.unregister_vendor.assert_called_once_with(handle)
        vendor.unregister.assert_called_once_with()


    def test_failed_vendor_registration_is_rolled_back(self):
        vendor_path = os.path.join(ROOT, "vendor", "io_scene_ueformat")
        vendor = types.SimpleNamespace(
            __name__="io_scene_ueformat",
            __file__=os.path.join(vendor_path, "__init__.py"),
            register_headless=mock.Mock(),
            unregister=mock.Mock(),
        )
        handle = _fake_vendor_handle(vendor, added_paths=(os.path.join(ROOT, "cache"),))
        loader = _fake_vendor_loader()
        failure = RuntimeError("simulated register failure")
        loader.register_vendor.side_effect = failure
        with (
            mock.patch.object(self.module.os.path, "isdir", return_value=True),
            mock.patch.object(self.module, "_load_bundled_vendor", return_value=handle),
            mock.patch.object(self.module, "_vendor_loader_module", return_value=loader),
            mock.patch.object(self.module, "_vendor_already_in_registry", return_value=False),
            mock.patch.object(self.module, "_is_current_ueformat_addon", return_value=True),
        ):
            self.module._ensure_dependency_addons()

        loader.register_vendor.assert_called_once_with(handle, headless=True)
        loader.unregister_vendor.assert_called_once_with(handle)
        self.assertNotIn("io_scene_ueformat", self.module._vendor_registered)


    def test_vendor_status_exposes_ready_observed_external_and_pending_states(self):
        self.assertEqual(self.module.get_vendor_status()["state"], "pending")

        vendor = types.SimpleNamespace(
            __name__="io_scene_ueformat",
            __file__=os.path.join(ROOT, "vendor", "io_scene_ueformat", "__init__.py"),
        )
        handle = _fake_vendor_handle(vendor)
        self.module._vendor_registered["io_scene_ueformat"] = handle
        self.assertEqual(self.module.get_vendor_status()["state"], "ready")
        self.assertEqual(self.module.get_vendor_status()["version"], "1.2.0")

        self.module._vendor_registered.clear()
        self.module._vendor_observed.add("io_scene_ueformat")
        self.assertEqual(self.module.get_vendor_status()["state"], "observed")

        self.module._vendor_observed.clear()
        self.module.bpy.context.preferences.addons["io_scene_ueformat"] = object()
        self.assertEqual(self.module.get_vendor_status()["state"], "external")


    def test_external_vendor_status_reports_capability_and_version(self):
        external = _fake_ueformat_module()
        previous = sys.modules.get("io_scene_ueformat")
        sys.modules["io_scene_ueformat"] = external
        self.module.bpy.context.preferences.addons["io_scene_ueformat"] = object()
        try:
            status = self.module.get_vendor_status()
        finally:
            if previous is None:
                sys.modules.pop("io_scene_ueformat", None)
            else:
                sys.modules["io_scene_ueformat"] = previous
        self.assertEqual(status["state"], "external")
        self.assertEqual(status["version"], "1.2.0")
        self.assertTrue(status["capabilities"]["current"])
        self.assertIn("current", status["message"])

        legacy = _fake_ueformat_module(version=(1, 0, 0))
        previous = sys.modules.get("io_scene_ueformat")
        sys.modules["io_scene_ueformat"] = legacy
        try:
            status = self.module.get_vendor_status()
        finally:
            if previous is None:
                sys.modules.pop("io_scene_ueformat", None)
            else:
                sys.modules["io_scene_ueformat"] = previous
        self.assertFalse(status["capabilities"]["current"])
        self.assertIn("lacks", status["message"])


    @unittest.skip("Indexed Project lifecycle retired; kept as historical compatibility coverage")
    def test_file_load_cancels_previous_project_index_job_before_refresh(self):
        state = types.SimpleNamespace(project_index_state="scanning")
        self.module.bpy.context.scene = types.SimpleNamespace(pipeline_wizard=state)
        controller = types.SimpleNamespace(
            cancel_all_project_jobs=mock.Mock(),
            reset_project_job_state=mock.Mock(),
        )
        fmodel = types.ModuleType("Fmodel")
        fmodel.__path__ = []
        pipeline = types.ModuleType("Fmodel.Pipeline")
        pipeline.__path__ = []
        pipeline.project_index_controller = controller
        with mock.patch.dict(sys.modules, {
            "Fmodel": fmodel,
            "Fmodel.Pipeline": pipeline,
            "Fmodel.Pipeline.project_index_controller": controller,
        }):
            self.module._cancel_loaded_file_project_index_jobs()

        controller.cancel_all_project_jobs.assert_called_once_with()
        controller.reset_project_job_state.assert_called_once_with(state)
    def test_current_ueformat_requires_fmodel_material_and_scene_surfaces(self):
        current = _fake_ueformat_module()
        self.assertTrue(self.module._is_current_ueformat_addon(current))

        legacy = _fake_ueformat_module(version=(1, 0, 0))
        self.assertFalse(self.module._is_current_ueformat_addon(legacy))

    def test_current_ueformat_rejects_missing_material_option_surface(self):
        current = _fake_ueformat_module()
        current.options.UEModelOptions = type("UEModelOptions", (), {"__init__": lambda self: None})
        self.assertFalse(self.module._is_current_ueformat_addon(current))

    def test_external_same_name_vendor_is_never_owned(self):
        vendor_path = os.path.join(ROOT, "vendor", "io_scene_ueformat")
        conflict = VendorLoadError(
            VendorErrorCode.EXTERNAL_CONFLICT,
            "external module already loaded",
        )
        loader = _fake_vendor_loader()
        with (
            mock.patch.object(self.module.os.path, "isdir", return_value=True),
            mock.patch.object(self.module, "_load_bundled_vendor", side_effect=conflict),
            mock.patch.object(self.module, "_vendor_loader_module", return_value=loader),
        ):
            self.module._ensure_dependency_addons()
            self.module._unregister_vendor_addons()

        loader.register_vendor.assert_not_called()
        loader.unregister_vendor.assert_not_called()
        self.assertNotIn("io_scene_ueformat", self.module._vendor_registered)
        self.assertNotIn("io_scene_ueformat", self.module._vendor_observed)

    def test_reload_after_driver_namespace_clear_cleans_prior_lifecycle(self):
        vendor_path = os.path.join(ROOT, "vendor", "io_scene_ueformat")
        owned_path = os.path.join(ROOT, "owned-vendor-parent")
        vendor = types.SimpleNamespace(
            __name__="io_scene_ueformat",
            __file__=os.path.join(vendor_path, "__init__.py"),
            register_headless=mock.Mock(),
            unregister=mock.Mock(),
        )
        handle = _fake_vendor_handle(vendor, added_paths=(owned_path,))
        loader = _fake_vendor_loader()
        self.module.register_handlers()
        module1_timer = self.timers.registered[-1][0]
        with (
            mock.patch.object(self.module.os.path, "isdir", return_value=True),
            mock.patch.object(self.module, "_load_bundled_vendor", return_value=handle),
            mock.patch.object(self.module, "_vendor_loader_module", return_value=loader),
            mock.patch.object(self.module, "_vendor_already_in_registry", return_value=False),
            mock.patch.object(self.module, "_is_current_ueformat_addon", return_value=True),
        ):
            self.module._ensure_dependency_addons()

        self.module.bpy.app.driver_namespace.clear()
        spec = importlib.util.spec_from_file_location(
            "reloaded_auto_handlers", os.path.join(ROOT, "auto_handlers.py")
        )
        module2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module2)
        module2.register_handlers()
        with mock.patch.object(module2, "_vendor_loader_module", return_value=loader):
            module2.unregister_handlers()

        self.assertIn(module1_timer, self.timers.unregistered)
        loader.unregister_vendor.assert_called_once_with(handle)
        vendor.unregister.assert_called_once_with()
        self.assertNotIn(owned_path, sys.path)

    def test_teardown_uses_original_bundled_vendor_object_after_name_substitution(self):
        vendor_path = os.path.join(ROOT, "vendor", "io_scene_ueformat")
        bundled_vendor = types.SimpleNamespace(
            __name__="io_scene_ueformat",
            __file__=os.path.join(vendor_path, "__init__.py"),
            register=mock.Mock(),
            unregister=mock.Mock(),
        )
        external_vendor = types.SimpleNamespace(
            __name__="io_scene_ueformat",
            __file__=os.path.join(ROOT, "external-vendor", "io_scene_ueformat", "__init__.py"),
            unregister=mock.Mock(),
        )
        self.module._vendor_registered["io_scene_ueformat"] = (bundled_vendor, vendor_path)
        previous_vendor = sys.modules.get("io_scene_ueformat")
        sys.modules["io_scene_ueformat"] = external_vendor
        try:
            self.module._unregister_vendor_addons()
        finally:
            if previous_vendor is None:
                sys.modules.pop("io_scene_ueformat", None)
            else:
                sys.modules["io_scene_ueformat"] = previous_vendor

        bundled_vendor.unregister.assert_called_once_with()
        external_vendor.unregister.assert_not_called()
