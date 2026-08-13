"""Fake-Blender contracts for asynchronous project index preparation."""

import importlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest

from core.project_index_service import (
    PreparedAnimationSelection, ProjectIndexServiceError, ProjectIndexSnapshot,
)
from core.project_index_worker import ProjectIndexWorker


def _load_controller():
    prefix = "_fmodel_async_controller_test"
    module_names = (
        prefix,
        f"{prefix}.core",
        f"{prefix}.core.project_index_service",
        f"{prefix}.core.project_index_worker",
        f"{prefix}.Fmodel",
        f"{prefix}.Fmodel.Pipeline",
        f"{prefix}.Fmodel.Pipeline.project_index_controller",
    )
    root = types.ModuleType(prefix)
    root.__path__ = []
    fmodel = types.ModuleType(f"{prefix}.Fmodel")
    fmodel.__path__ = []
    pipeline = types.ModuleType(f"{prefix}.Fmodel.Pipeline")
    pipeline.__path__ = []
    sys.modules.update({
        prefix: root,
        f"{prefix}.core": importlib.import_module("core"),
        f"{prefix}.core.project_index_service": importlib.import_module(
            "core.project_index_service"
        ),
        f"{prefix}.core.project_index_worker": importlib.import_module(
            "core.project_index_worker"
        ),
        f"{prefix}.Fmodel": fmodel,
        f"{prefix}.Fmodel.Pipeline": pipeline,
    })
    path = Path(__file__).parent / "Fmodel" / "Pipeline" / "project_index_controller.py"
    name = f"{prefix}.Fmodel.Pipeline.project_index_controller"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for module_name in module_names[:-1]:
        sys.modules.pop(module_name, None)
    return module


class FakeTimers:
    def __init__(self):
        self.callbacks = []

    def register(self, callback, **_kwargs):
        if callback not in self.callbacks:
            self.callbacks.append(callback)

    def unregister(self, callback):
        self.callbacks.remove(callback)

    def is_registered(self, callback):
        return callback in self.callbacks

    def tick(self):
        for callback in tuple(self.callbacks):
            delay = callback()
            if delay is None and callback in self.callbacks:
                self.callbacks.remove(callback)


class NamedCollection(dict):
    def get(self, name, default=None):
        return super().get(name, default)


class AsyncControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = _load_controller()
        self.saved_bpy = sys.modules.get("bpy")
        self.timers = FakeTimers()
        self.state = types.SimpleNamespace(
            source_mode="PROJECT",
            project_root="",
            skeleton_filter="Hero",
            project_index_state="missing",
            project_index_schema_version=0,
            project_index_generation=0,
            project_index_animation_count=0,
            project_index_match_count=0,
            project_index_last_scan_ns="",
            project_index_message="",
            project_index_job_id="",
            project_index_request_generation=0,
            project_index_progress=0.0,
            project_index_progress_phase="",
            project_index_progress_path="",
            project_index_cancel_requested=False,
            indexed_project_root="",
            indexed_skeleton="",
            indexed_selected_paths="[]",
            indexed_selection_mode="NONE",
            scan_completed=False,
        )
        self.scene = types.SimpleNamespace(name="Scene", pipeline_wizard=self.state)
        self.armature = types.SimpleNamespace(name="HeroRig", type="ARMATURE")
        self.bpy = types.ModuleType("bpy")
        self.bpy.app = types.SimpleNamespace(background=False, timers=self.timers)
        self.bpy.data = types.SimpleNamespace(
            scenes=NamedCollection(Scene=self.scene),
            objects=NamedCollection(HeroRig=self.armature),
        )
        sys.modules["bpy"] = self.bpy
        self.context = types.SimpleNamespace(
            scene=self.scene, active_object=self.armature
        )
        self.controller.register()

    def tearDown(self):
        self.controller.unregister()
        if self.saved_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self.saved_bpy

    @staticmethod
    def _prepared(root, skeleton, paths=None):
        paths = tuple(paths or (str(Path(root) / "Hero" / "Idle.ueanim"),))
        return PreparedAnimationSelection(
            root=str(root),
            skeleton=skeleton,
            paths=paths,
            manifest={"skeletons": {skeleton: {}}},
            snapshot=ProjectIndexSnapshot(
                root=str(root), state="ready", schema_version=3,
                generation=8, last_scan_ns=123, animation_count=len(paths),
                matching_count=len(paths), message="Animation index ready",
            ),
        )

    def _use_service(self, service_factory):
        self.controller._WORKER_FACTORY = lambda: ProjectIndexWorker(
            service_factory=service_factory
        )

    def test_headless_mode_requires_synchronous_fallback(self):
        self.bpy.app.background = True
        self.assertFalse(self.controller.can_schedule_project_prepare(self.context))
        self.assertIsNone(
            self.controller.schedule_project_prepare(
                self.context,
                self.state,
                root="C:/Project",
                skeleton="Hero",
            )
        )
        self.assertEqual(self.timers.callbacks, [])

    def test_success_is_committed_by_timer_on_calling_thread(self):
        callback_threads = []
        callback_contexts = []

        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                return self._prepared(root, skeleton)

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            main_thread = threading.get_ident()
            job = self.controller.schedule_project_prepare(
                self.context,
                self.state,
                root=directory,
                skeleton="Hero",
                on_success=lambda state, prepared, context: (
                    callback_threads.append(threading.get_ident()),
                    callback_contexts.append(context),
                    setattr(state, "scan_completed", True),
                ),
            )
            self.assertIsNotNone(job)
            self.assertEqual(self.state.project_index_state, "scanning")
            self.controller._WORKER.wait(job.job_id, timeout=2)
            self.timers.tick()

        self.assertEqual(callback_threads, [main_thread])
        self.assertIs(callback_contexts[0].active_object, self.armature)
        self.assertTrue(self.state.scan_completed)
        self.assertEqual(self.state.project_index_state, "ready")
        self.assertEqual(self.state.indexed_selection_mode, "AUTO_PROJECT")
        self.assertEqual(self.state.indexed_skeleton, "Hero")
        self.assertEqual(self.state.project_index_job_id, "")
        self.assertEqual(self.timers.callbacks, [])

    def test_progress_is_published_only_when_timer_polls(self):
        progress_sent = threading.Event()
        release = threading.Event()

        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                progress({
                    "phase": "compare", "completed": 2, "total": 4,
                    "path": str(Path(root) / "Hero" / "Run.ueanim"),
                })
                progress_sent.set()
                release.wait(2)
                return self._prepared(root, skeleton)

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            job = self.controller.schedule_project_prepare(
                self.context, self.state, root=directory, skeleton="Hero"
            )
            self.assertTrue(progress_sent.wait(2))
            self.assertEqual(self.state.project_index_progress, 0.0)
            self.timers.tick()
            self.assertEqual(self.state.project_index_progress_phase, "compare")
            self.assertEqual(self.state.project_index_progress, 0.5)
            self.assertTrue(self.state.project_index_progress_path.endswith("Run.ueanim"))
            release.set()
            self.controller._WORKER.wait(job.job_id, timeout=2)
            self.timers.tick()

    def test_changed_skeleton_discards_stale_success(self):
        started = threading.Event()
        release = threading.Event()
        completed = []

        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                started.set()
                release.wait(2)
                return self._prepared(root, skeleton)

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            job = self.controller.schedule_project_prepare(
                self.context,
                self.state,
                root=directory,
                skeleton="Hero",
                on_success=lambda *_args: completed.append(True),
            )
            self.assertTrue(started.wait(2))
            self.state.skeleton_filter = "Enemy"
            release.set()
            self.controller._WORKER.wait(job.job_id, timeout=2)
            self.timers.tick()

        self.assertEqual(completed, [])
        self.assertEqual(self.state.indexed_selection_mode, "NONE")
        self.assertEqual(self.state.project_index_state, "cancelled")
        self.assertIn("discarded", self.state.project_index_message)
        self.assertEqual(self.state.project_index_job_id, "")

    def test_explicit_selection_remains_supported(self):
        calls = []

        class Service:
            def prepare_paths(_self, root, skeleton, paths, *, progress, cancelled):
                calls.append(tuple(paths))
                return self._prepared(root, skeleton, paths)

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            paths = [str(Path(directory) / "Hero" / "Run.ueanim")]
            self.state.indexed_project_root = directory
            self.state.indexed_skeleton = "Hero"
            self.state.indexed_selected_paths = __import__("json").dumps(paths)
            self.state.indexed_selection_mode = "EXPLICIT"
            job = self.controller.schedule_project_prepare(
                self.context,
                self.state,
                selection_mode="EXPLICIT",
                root=directory,
                skeleton="Hero",
                paths=paths,
            )
            self.controller._WORKER.wait(job.job_id, timeout=2)
            self.timers.tick()

        self.assertEqual(calls, [job.key.explicit_paths])
        self.assertEqual(self.state.indexed_selection_mode, "EXPLICIT")

    def test_identical_schedule_from_same_state_reuses_job_and_callback(self):
        started = threading.Event()
        release = threading.Event()
        callbacks = []

        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                started.set()
                release.wait(2)
                return self._prepared(root, skeleton)

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            first = self.controller.schedule_project_prepare(
                self.context, self.state, root=directory, skeleton="Hero",
                on_success=lambda *_args: callbacks.append("first"),
            )
            self.assertTrue(started.wait(2))
            second = self.controller.schedule_project_prepare(
                self.context, self.state, root=directory, skeleton="Hero",
                on_success=lambda *_args: callbacks.append("duplicate"),
            )
            self.assertEqual(second.job_id, first.job_id)
            self.assertEqual(
                self.state.project_index_request_generation, 1
            )
            release.set()
            self.controller._WORKER.wait(first.job_id, timeout=2)
            self.timers.tick()

        self.assertEqual(callbacks, ["first"])

    def test_new_request_supersedes_old_callback_for_the_same_wizard(self):
        first_started = threading.Event()
        release_first = threading.Event()
        callbacks = []

        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                if skeleton == "Hero":
                    first_started.set()
                    release_first.wait(2)
                    raise ProjectIndexServiceError("cancelled", "cancelled", root=root)
                return self._prepared(root, skeleton)

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            first = self.controller.schedule_project_prepare(
                self.context, self.state, root=directory, skeleton="Hero",
                on_success=lambda *_args: callbacks.append("Hero"),
            )
            self.assertTrue(first_started.wait(2))
            self.state.skeleton_filter = "Enemy"
            second = self.controller.schedule_project_prepare(
                self.context, self.state, root=directory, skeleton="Enemy",
                on_success=lambda _state, prepared, _context: callbacks.append(
                    prepared.skeleton
                ),
            )
            release_first.set()
            self.controller._WORKER.wait(first.job_id, timeout=2)
            self.controller._WORKER.wait(second.job_id, timeout=2)
            self.timers.tick()

        self.assertEqual(callbacks, ["Enemy"])
        self.assertEqual(self.state.indexed_skeleton, "Enemy")
        self.assertEqual(self.state.project_index_state, "ready")
    def test_async_no_match_uses_structured_visible_state(self):
        callback_errors = []

        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                raise ProjectIndexServiceError(
                    "no_match", f"No indexed animations match skeleton: {skeleton}",
                    root=root,
                )

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            job = self.controller.schedule_project_prepare(
                self.context, self.state, root=directory, skeleton="Hero",
                on_error=lambda _state, error: callback_errors.append(error.code),
            )
            self.controller._WORKER.wait(job.job_id, timeout=2)
            self.timers.tick()

        self.assertEqual(callback_errors, ["no_match"])
        self.assertEqual(self.state.project_index_state, "no_match")
        self.assertIn("Hero", self.state.project_index_message)
        self.assertFalse(self.state.scan_completed)

    def test_cancel_invalidates_callback_and_clears_active_job_id(self):
        started = threading.Event()
        callbacks = []

        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                started.set()
                while not cancelled():
                    __import__("time").sleep(0.005)
                raise ProjectIndexServiceError("cancelled", "cancelled", root=root)

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            job = self.controller.schedule_project_prepare(
                self.context, self.state, root=directory, skeleton="Hero",
                on_success=lambda *_args: callbacks.append(True),
            )
            self.assertTrue(started.wait(2))
            self.assertTrue(self.controller.cancel_project_job(self.state))
            finished = self.controller._WORKER.wait(job.job_id, timeout=2)
            self.timers.tick()

        self.assertEqual(finished.status, "cancelled")
        self.assertEqual(callbacks, [])
        self.assertEqual(self.state.project_index_state, "cancelled")
        self.assertEqual(self.state.project_index_job_id, "")
        self.assertGreaterEqual(self.state.project_index_request_generation, 2)

    def test_timer_registration_failure_is_not_misreported_as_sync_fallback(self):
        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                return self._prepared(root, skeleton)

        self._use_service(Service)
        self.timers.register = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("timer unavailable")
        )
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            with self.assertRaises(ProjectIndexServiceError) as raised:
                self.controller.schedule_project_prepare(
                    self.context, self.state, root=directory, skeleton="Hero"
                )

        self.assertEqual(raised.exception.code, "schedule_failed")
        self.assertEqual(self.state.project_index_state, "failed")
        self.assertEqual(self.state.project_index_job_id, "")
    def test_loaded_blend_transient_job_metadata_is_reset(self):
        self.state.project_index_state = "scanning"
        self.state.project_index_job_id = "44"
        self.state.project_index_progress = 0.75
        self.state.project_index_progress_phase = "compare"
        self.state.project_index_progress_path = "C:/Project/Hero/Run.ueanim"
        self.state.project_index_cancel_requested = True

        self.controller.reset_project_job_state(self.state)

        self.assertEqual(self.state.project_index_state, "missing")
        self.assertEqual(self.state.project_index_job_id, "")
        self.assertEqual(self.state.project_index_progress, 0.0)
        self.assertEqual(self.state.project_index_progress_phase, "")
        self.assertEqual(self.state.project_index_progress_path, "")
        self.assertFalse(self.state.project_index_cancel_requested)

    def test_file_reload_cancel_all_removes_poll_timer_without_rna_updates(self):
        started = threading.Event()

        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                started.set()
                while not cancelled():
                    __import__("time").sleep(0.005)
                raise ProjectIndexServiceError("cancelled", "cancelled", root=root)

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            job = self.controller.schedule_project_prepare(
                self.context, self.state, root=directory, skeleton="Hero"
            )
            self.assertTrue(started.wait(2))
            callback = self.timers.callbacks[0]
            self.assertEqual(self.controller.cancel_all_project_jobs(), 1)
            self.assertEqual(self.timers.callbacks, [])
            self.assertEqual(
                self.controller._WORKER.wait(job.job_id, timeout=2).status,
                "cancelled",
            )
            self.assertIsNone(callback())
    def test_unregister_removes_timer_before_rna_can_disappear(self):
        started = threading.Event()
        release = threading.Event()

        class Service:
            def prepare_selection(_self, root, skeleton, *, progress, cancelled):
                started.set()
                release.wait(2)
                return self._prepared(root, skeleton)

        self._use_service(Service)
        with tempfile.TemporaryDirectory() as directory:
            self.state.project_root = directory
            self.controller.schedule_project_prepare(
                self.context, self.state, root=directory, skeleton="Hero"
            )
            self.assertTrue(started.wait(2))
            callback = self.timers.callbacks[0]
            self.controller.unregister()
            self.scene.pipeline_wizard = property(
                lambda _self: (_ for _ in ()).throw(AssertionError("RNA accessed"))
            )
            self.assertIsNone(callback())
            self.assertEqual(self.timers.callbacks, [])
            release.set()


if __name__ == "__main__":
    unittest.main()
