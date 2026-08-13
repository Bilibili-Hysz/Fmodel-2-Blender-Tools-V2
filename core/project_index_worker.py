"""Single-threaded, Blender-independent jobs for project animation indexing."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass, field
import os
import threading
from typing import Callable, Optional

from .project_index_service import (
    PreparedAnimationSelection,
    ProjectAnimationIndexService,
    ProjectIndexServiceError,
)


_AUTO_PROJECT = "AUTO_PROJECT"
_EXPLICIT = "EXPLICIT"
_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class ProjectIndexJobKey:
    """Stable identity for requests that may share one in-flight job."""

    root: str
    skeleton: str
    selection_mode: str
    explicit_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectIndexJobProgress:
    phase: str = "queued"
    completed: int = 0
    total: Optional[int] = None
    path: str = ""

    @property
    def fraction(self) -> float:
        if not self.total or self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.completed / self.total))


@dataclass(frozen=True)
class ProjectIndexJobSnapshot:
    job_id: int
    request_generation: int
    key: ProjectIndexJobKey
    status: str
    progress: ProjectIndexJobProgress
    result: Optional[PreparedAnimationSelection] = None
    error: Optional[ProjectIndexServiceError] = None

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATES


@dataclass
class _ProjectIndexJobRecord:
    job_id: int
    request_generation: int
    key: ProjectIndexJobKey
    status: str = "queued"
    progress: ProjectIndexJobProgress = field(default_factory=ProjectIndexJobProgress)
    result: Optional[PreparedAnimationSelection] = None
    error: Optional[ProjectIndexServiceError] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: object = None


class ProjectIndexWorker:
    """Serialize index work and expose thread-safe, pollable job snapshots.

    The worker deliberately knows nothing about ``bpy``. Blender integration is
    responsible for polling snapshots and publishing them on the main thread.
    """

    def __init__(
        self,
        service_factory: Callable[[], ProjectAnimationIndexService] = ProjectAnimationIndexService,
        *,
        executor_factory: Callable[..., ThreadPoolExecutor] = ThreadPoolExecutor,
    ):
        self._service_factory = service_factory
        self._executor = executor_factory(
            max_workers=1, thread_name_prefix="FModelProjectIndex"
        )
        self._lock = threading.RLock()
        self._jobs: dict[int, _ProjectIndexJobRecord] = {}
        self._active_job_id: Optional[int] = None
        self._next_job_id = 1
        self._next_generation = 1
        self._shutdown = False

    @staticmethod
    def make_key(
        root,
        skeleton,
        *,
        selection_mode=_AUTO_PROJECT,
        explicit_paths=(),
    ) -> ProjectIndexJobKey:
        mode = str(selection_mode or "").strip().upper()
        if mode not in {_AUTO_PROJECT, _EXPLICIT}:
            raise ValueError(f"Unsupported project index selection mode: {selection_mode}")
        normalized_root = _canonical_path(root)
        normalized_skeleton = str(skeleton or "").strip()
        normalized_paths = tuple(_canonical_path(path) for path in (explicit_paths or ()))
        if mode == _AUTO_PROJECT:
            normalized_paths = ()
        return ProjectIndexJobKey(
            root=normalized_root,
            skeleton=normalized_skeleton,
            selection_mode=mode,
            explicit_paths=normalized_paths,
        )

    def schedule(
        self,
        root,
        skeleton,
        *,
        selection_mode=_AUTO_PROJECT,
        explicit_paths=(),
    ) -> ProjectIndexJobSnapshot:
        """Schedule one request, deduplicating an identical in-flight request."""
        key = self.make_key(
            root,
            skeleton,
            selection_mode=selection_mode,
            explicit_paths=explicit_paths,
        )
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Project index worker is shut down")
            active = self._jobs.get(self._active_job_id)
            if active is not None and active.status not in _TERMINAL_STATES:
                if active.key == key and not active.cancel_event.is_set():
                    return self._snapshot_locked(active)
                self._request_cancel_locked(active)

            record = _ProjectIndexJobRecord(
                job_id=self._next_job_id,
                request_generation=self._next_generation,
                key=key,
            )
            self._next_job_id += 1
            self._next_generation += 1
            self._jobs[record.job_id] = record
            self._active_job_id = record.job_id
            record.future = self._executor.submit(self._run_job, record.job_id)
            self._prune_locked()
            return self._snapshot_locked(record)

    def snapshot(self, job_id=None) -> Optional[ProjectIndexJobSnapshot]:
        with self._lock:
            target_id = self._active_job_id if job_id is None else int(job_id)
            record = self._jobs.get(target_id)
            return self._snapshot_locked(record) if record is not None else None

    def cancel(self, job_id=None) -> bool:
        """Request cooperative cancellation, including queued future removal."""
        with self._lock:
            target_id = self._active_job_id if job_id is None else int(job_id)
            record = self._jobs.get(target_id)
            if record is None or record.status in _TERMINAL_STATES:
                return False
            self._request_cancel_locked(record)
            return True

    def wait(self, job_id, timeout=None) -> Optional[ProjectIndexJobSnapshot]:
        """Testing/headless convenience: wait for a job without exposing Future."""
        with self._lock:
            record = self._jobs.get(int(job_id))
            future = record.future if record is not None else None
        if future is None:
            return None
        try:
            future.result(timeout=timeout)
        except CancelledError:
            pass
        return self.snapshot(job_id)

    def shutdown(self, *, wait=False):
        """Cancel all jobs and stop accepting work."""
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            for record in self._jobs.values():
                if record.status not in _TERMINAL_STATES:
                    self._request_cancel_locked(record)
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _run_job(self, job_id):
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            if record.cancel_event.is_set() or self._shutdown:
                self._mark_cancelled_locked(record)
                return
            record.status = "running"
            record.progress = ProjectIndexJobProgress(phase="start")

        def progress(event):
            self._update_progress(job_id, event)

        def cancelled():
            with self._lock:
                current = self._jobs.get(job_id)
                return (
                    current is None
                    or current.cancel_event.is_set()
                    or self._shutdown
                )

        try:
            service = self._service_factory()
            key = record.key
            if key.selection_mode == _EXPLICIT:
                result = service.prepare_paths(
                    key.root,
                    key.skeleton,
                    key.explicit_paths,
                    progress=progress,
                    cancelled=cancelled,
                )
            else:
                result = service.prepare_selection(
                    key.root,
                    key.skeleton,
                    progress=progress,
                    cancelled=cancelled,
                )
        except ProjectIndexServiceError as error:
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None:
                    return
                if error.code == "cancelled" or current.cancel_event.is_set():
                    self._mark_cancelled_locked(current, error)
                else:
                    current.status = "failed"
                    current.error = error
            return
        except Exception as error:  # keep worker exceptions out of Blender's timer
            structured = ProjectIndexServiceError(
                "worker_failed",
                str(error) or "Animation index worker failed",
                root=record.key.root,
                cause=error,
            )
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None:
                    return
                if current.cancel_event.is_set():
                    self._mark_cancelled_locked(current, structured)
                else:
                    current.status = "failed"
                    current.error = structured
            return

        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                return
            if current.cancel_event.is_set() or self._shutdown:
                self._mark_cancelled_locked(current)
                return
            current.status = "succeeded"
            current.result = result
            current.error = None
            if current.progress.phase != "published":
                current.progress = ProjectIndexJobProgress(
                    phase="completed", completed=1, total=1
                )

    def _update_progress(self, job_id, event):
        if not isinstance(event, dict):
            return
        with self._lock:
            record = self._jobs.get(job_id)
            if (
                record is None
                or record.status in _TERMINAL_STATES
                or record.cancel_event.is_set()
            ):
                return
            try:
                completed = max(0, int(event.get("completed", 0) or 0))
            except (TypeError, ValueError):
                completed = 0
            raw_total = event.get("total")
            try:
                total = None if raw_total is None else max(0, int(raw_total))
            except (TypeError, ValueError):
                total = None
            record.progress = ProjectIndexJobProgress(
                phase=str(event.get("phase", "running") or "running"),
                completed=completed,
                total=total,
                path=str(event.get("path", "") or ""),
            )

    def _request_cancel_locked(self, record):
        record.cancel_event.set()
        if record.status == "queued" and record.future is not None:
            if record.future.cancel():
                self._mark_cancelled_locked(record)
                return
        if record.status not in _TERMINAL_STATES:
            record.status = "cancelling"
            record.progress = ProjectIndexJobProgress(
                phase="cancelling",
                completed=record.progress.completed,
                total=record.progress.total,
                path=record.progress.path,
            )

    @staticmethod
    def _mark_cancelled_locked(record, error=None):
        record.status = "cancelled"
        record.result = None
        record.error = error or ProjectIndexServiceError(
            "cancelled", "Animation index refresh cancelled", root=record.key.root
        )
        record.progress = ProjectIndexJobProgress(
            phase="cancelled",
            completed=record.progress.completed,
            total=record.progress.total,
            path=record.progress.path,
        )

    @staticmethod
    def _snapshot_locked(record):
        return ProjectIndexJobSnapshot(
            job_id=record.job_id,
            request_generation=record.request_generation,
            key=record.key,
            status=record.status,
            progress=record.progress,
            result=record.result,
            error=record.error,
        )

    def _prune_locked(self):
        if len(self._jobs) <= 128:
            return
        removable = [
            job_id
            for job_id, record in sorted(self._jobs.items())
            if job_id != self._active_job_id and record.status in _TERMINAL_STATES
        ]
        for job_id in removable[: len(self._jobs) - 96]:
            self._jobs.pop(job_id, None)


def _canonical_path(path) -> str:
    text = os.fspath(path) if path is not None else ""
    text = str(text).strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


__all__ = [
    "ProjectIndexJobKey",
    "ProjectIndexJobProgress",
    "ProjectIndexJobSnapshot",
    "ProjectIndexWorker",
]
