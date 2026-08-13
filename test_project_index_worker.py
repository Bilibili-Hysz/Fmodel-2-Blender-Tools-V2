"""Concurrency contracts for the Blender-independent project index worker."""

from pathlib import Path
import tempfile
import threading
import time
import unittest

from core.project_index_service import (
    PreparedAnimationSelection,
    ProjectIndexServiceError,
    ProjectIndexSnapshot,
)
from core.project_index_worker import ProjectIndexWorker


def _prepared(root, skeleton, paths=None):
    paths = tuple(paths or (str(Path(root) / f"{skeleton}.ueanim"),))
    return PreparedAnimationSelection(
        root=str(root),
        skeleton=skeleton,
        paths=paths,
        manifest={"skeletons": {skeleton: {}}},
        snapshot=ProjectIndexSnapshot(
            root=str(root),
            state="ready",
            schema_version=3,
            generation=1,
            last_scan_ns=1,
            animation_count=len(paths),
            matching_count=len(paths),
            message="Animation index ready",
        ),
    )


class ProjectIndexWorkerTests(unittest.TestCase):
    def test_identical_inflight_request_is_deduplicated(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        class Service:
            def prepare_selection(self, root, skeleton, *, progress, cancelled):
                calls.append((root, skeleton))
                started.set()
                self_outer.assertTrue(release.wait(2))
                return _prepared(root, skeleton)

        self_outer = self
        worker = ProjectIndexWorker(service_factory=Service)
        self.addCleanup(worker.shutdown)
        with tempfile.TemporaryDirectory() as directory:
            first = worker.schedule(directory, "Hero")
            self.assertTrue(started.wait(2))
            duplicate = worker.schedule(directory, "Hero")
            self.assertEqual(duplicate.job_id, first.job_id)
            self.assertEqual(duplicate.request_generation, first.request_generation)
            release.set()
            finished = worker.wait(first.job_id, timeout=2)

        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(len(calls), 1)

    def test_new_request_cooperatively_supersedes_running_job(self):
        first_started = threading.Event()

        class Service:
            def prepare_selection(self, root, skeleton, *, progress, cancelled):
                if skeleton == "Hero":
                    first_started.set()
                    deadline = time.monotonic() + 2
                    while not cancelled() and time.monotonic() < deadline:
                        time.sleep(0.005)
                    raise ProjectIndexServiceError("cancelled", "cancelled", root=root)
                return _prepared(root, skeleton)

        worker = ProjectIndexWorker(service_factory=Service)
        self.addCleanup(worker.shutdown)
        with tempfile.TemporaryDirectory() as directory:
            first = worker.schedule(directory, "Hero")
            self.assertTrue(first_started.wait(2))
            second = worker.schedule(directory, "Enemy")
            first_done = worker.wait(first.job_id, timeout=2)
            second_done = worker.wait(second.job_id, timeout=2)

        self.assertEqual(first_done.status, "cancelled")
        self.assertEqual(second_done.status, "succeeded")
        self.assertEqual(second_done.result.skeleton, "Enemy")
        self.assertGreater(second.request_generation, first.request_generation)

    def test_superseding_a_queued_request_cancels_its_future(self):
        release_first = threading.Event()
        first_started = threading.Event()
        executed = []

        class Service:
            def prepare_selection(self, root, skeleton, *, progress, cancelled):
                executed.append(skeleton)
                if skeleton == "First":
                    first_started.set()
                    release_first.wait(2)  # deliberately ignores cooperative cancellation
                    raise ProjectIndexServiceError("cancelled", "cancelled", root=root)
                return _prepared(root, skeleton)

        worker = ProjectIndexWorker(service_factory=Service)
        self.addCleanup(worker.shutdown)
        with tempfile.TemporaryDirectory() as directory:
            first = worker.schedule(directory, "First")
            self.assertTrue(first_started.wait(2))
            queued = worker.schedule(directory, "Queued")
            newest = worker.schedule(directory, "Newest")
            queued_snapshot = worker.snapshot(queued.job_id)
            self.assertEqual(queued_snapshot.status, "cancelled")
            release_first.set()
            worker.wait(first.job_id, timeout=2)
            newest_done = worker.wait(newest.job_id, timeout=2)

        self.assertEqual(newest_done.status, "succeeded")
        self.assertNotIn("Queued", executed)
        self.assertEqual(executed, ["First", "Newest"])

    def test_progress_is_published_as_an_immutable_snapshot(self):
        progress_sent = threading.Event()
        release = threading.Event()

        class Service:
            def prepare_selection(self, root, skeleton, *, progress, cancelled):
                progress({
                    "phase": "compare", "completed": 3, "total": 10,
                    "path": str(Path(root) / "Hero" / "Run.ueanim"),
                })
                progress_sent.set()
                release.wait(2)
                return _prepared(root, skeleton)

        worker = ProjectIndexWorker(service_factory=Service)
        self.addCleanup(worker.shutdown)
        with tempfile.TemporaryDirectory() as directory:
            job = worker.schedule(directory, "Hero")
            self.assertTrue(progress_sent.wait(2))
            snapshot = worker.snapshot(job.job_id)
            self.assertEqual(snapshot.progress.phase, "compare")
            self.assertEqual(snapshot.progress.completed, 3)
            self.assertEqual(snapshot.progress.total, 10)
            self.assertAlmostEqual(snapshot.progress.fraction, 0.3)
            self.assertTrue(snapshot.progress.path.endswith("Run.ueanim"))
            release.set()
            self.assertEqual(worker.wait(job.job_id, 2).status, "succeeded")

    def test_explicit_job_uses_exact_paths(self):
        calls = []

        class Service:
            def prepare_paths(self, root, skeleton, paths, *, progress, cancelled):
                calls.append(tuple(paths))
                return _prepared(root, skeleton, paths)

        worker = ProjectIndexWorker(service_factory=Service)
        self.addCleanup(worker.shutdown)
        with tempfile.TemporaryDirectory() as directory:
            selected = [
                str(Path(directory) / "Hero" / "Idle.ueanim"),
                str(Path(directory) / "Hero" / "Run.ueanim"),
            ]
            job = worker.schedule(
                directory,
                "Hero",
                selection_mode="EXPLICIT",
                explicit_paths=selected,
            )
            finished = worker.wait(job.job_id, timeout=2)

        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(calls, [finished.key.explicit_paths])
        self.assertEqual(finished.result.paths, finished.key.explicit_paths)

    def test_shutdown_rejects_new_jobs(self):
        worker = ProjectIndexWorker()
        worker.shutdown()
        with self.assertRaises(RuntimeError):
            worker.schedule("C:/Project", "Hero")


if __name__ == "__main__":
    unittest.main()
