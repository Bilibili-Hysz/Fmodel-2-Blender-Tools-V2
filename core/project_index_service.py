"""Automatic, Blender-independent coordination for the project animation index."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable, Optional

from .project_index import ProjectIndex


@dataclass(frozen=True)
class ProjectIndexSnapshot:
    root: str
    state: str
    schema_version: int
    generation: Optional[int]
    last_scan_ns: Optional[int]
    animation_count: int
    matching_count: int
    message: str


@dataclass(frozen=True)
class PreparedAnimationSelection:
    root: str
    skeleton: str
    paths: tuple[str, ...]
    manifest: dict
    snapshot: ProjectIndexSnapshot


class ProjectIndexServiceError(ValueError):
    """Structured failure raised at the Pipeline/index boundary."""

    def __init__(self, code: str, message: str, *, root: str = "", cause=None):
        super().__init__(message)
        self.code = code
        self.root = root
        self.cause = cause


class ProjectAnimationIndexService:
    """Inspect, repair, refresh and query one project-scoped animation index."""

    def __init__(
        self, index_factory: Callable[..., ProjectIndex] = ProjectIndex,
        *, asset_scope: str = "animation",
    ):
        self._index_factory = index_factory
        self.asset_scope = asset_scope

    def _new_index(self, root):
        return self._index_factory(root, asset_scope=self.asset_scope)

    def inspect(self, root, skeleton="") -> ProjectIndexSnapshot:
        """Read status and counts without creating or scanning the database."""
        normalized = self._normalize_root(root, require_directory=False)
        if not normalized:
            return ProjectIndexSnapshot(
                root="",
                state="missing",
                schema_version=0,
                generation=None,
                last_scan_ns=None,
                animation_count=0,
                matching_count=0,
                message="Project Root is required",
            )
        if not Path(normalized).is_dir():
            return ProjectIndexSnapshot(
                root=normalized,
                state="unavailable",
                schema_version=0,
                generation=None,
                last_scan_ns=None,
                animation_count=0,
                matching_count=0,
                message="Project Root is not an accessible directory",
            )
        try:
            summary = self._new_index(normalized).summary(
                self._normalize_skeleton(skeleton), raise_on_error=True
            )
        except (OSError, TypeError, ValueError, sqlite3.Error) as error:
            return ProjectIndexSnapshot(
                root=normalized,
                state="unavailable",
                schema_version=0,
                generation=None,
                last_scan_ns=None,
                animation_count=0,
                matching_count=0,
                message=str(error) or "Animation index is unavailable",
            )
        return self._snapshot(normalized, summary)

    def refresh(
        self, root, skeleton="", *, force=False, progress=None, cancelled=None
    ) -> ProjectIndexSnapshot:
        """Ensure a compatible schema, incrementally scan, and return its snapshot."""
        normalized = self._normalize_root(root, require_directory=True)
        skeleton = self._normalize_skeleton(skeleton)
        try:
            index = self._new_index(normalized)
            status = index.status()
            if status["state"] == "missing":
                status = index.ensure_schema()
            elif status["state"] == "rebuild_required":
                status = index.rebuild()
            if status["state"] != "ready":
                raise ProjectIndexServiceError(
                    "index_unavailable",
                    f"Animation index cannot be prepared: {status['state']}",
                    root=normalized,
                )
            scan_result = index.scan(
                [normalized], force=force, progress=progress, cancelled=cancelled
            )
            if scan_result.get("state") == "cancelled":
                raise ProjectIndexServiceError(
                    "cancelled", "Animation index refresh cancelled", root=normalized
                )
            if scan_result.get("state") != "ready":
                raise ProjectIndexServiceError(
                    "refresh_failed",
                    "Animation index refresh failed",
                    root=normalized,
                )
            summary = index.summary(skeleton, raise_on_error=True)
        except ProjectIndexServiceError:
            raise
        except (OSError, TypeError, ValueError, sqlite3.Error) as error:
            raise ProjectIndexServiceError(
                "refresh_failed",
                str(error) or "Animation index refresh failed",
                root=normalized,
                cause=error,
            ) from error
        if summary["state"] != "ready":
            raise ProjectIndexServiceError(
                "refresh_failed",
                f"Animation index is not ready after refresh: {summary['state']}",
                root=normalized,
            )
        return self._snapshot(normalized, summary)

    def reset_and_refresh(
        self, root, skeleton="", *, progress=None, cancelled=None
    ) -> ProjectIndexSnapshot:
        """Replace the derived database and immediately repopulate it."""
        normalized = self._normalize_root(root, require_directory=True)
        skeleton = self._normalize_skeleton(skeleton)
        try:
            index = self._new_index(normalized)
            status = index.rebuild()
            if status["state"] != "ready":
                raise ProjectIndexServiceError(
                    "reset_failed",
                    "Animation index reset failed",
                    root=normalized,
                )
            scan_result = index.scan(
                [normalized], progress=progress, cancelled=cancelled
            )
            if scan_result.get("state") == "cancelled":
                raise ProjectIndexServiceError(
                    "cancelled", "Animation index rescan cancelled", root=normalized
                )
            if scan_result.get("state") != "ready":
                raise ProjectIndexServiceError(
                    "reset_failed",
                    "Animation index rescan failed",
                    root=normalized,
                )
            summary = index.summary(skeleton, raise_on_error=True)
        except ProjectIndexServiceError:
            raise
        except (OSError, TypeError, ValueError, sqlite3.Error) as error:
            raise ProjectIndexServiceError(
                "reset_failed",
                str(error) or "Animation index reset failed",
                root=normalized,
                cause=error,
            ) from error
        return self._snapshot(normalized, summary)

    def prepare_selection(
        self, root, skeleton, *, progress=None, cancelled=None
    ) -> PreparedAnimationSelection:
        """Refresh and prepare every indexed animation matching one skeleton."""
        skeleton = self._require_skeleton(skeleton)
        return self._prepare(
            root, skeleton, explicit_paths=None,
            progress=progress, cancelled=cancelled,
        )

    def prepare_paths(
        self, root, skeleton, paths: Iterable[str], *, progress=None, cancelled=None
    ) -> PreparedAnimationSelection:
        """Refresh and validate an exact legacy/explicit indexed selection."""
        skeleton = self._require_skeleton(skeleton)
        selected = self._normalize_paths(paths)
        return self._prepare(
            root, skeleton, explicit_paths=selected,
            progress=progress, cancelled=cancelled,
        )

    def _prepare(self, root, skeleton, explicit_paths, *, progress, cancelled):
        normalized = self._normalize_root(root, require_directory=True)
        snapshot = self.refresh(
            normalized, skeleton, progress=progress, cancelled=cancelled
        )
        try:
            index = self._new_index(normalized)
            paths = explicit_paths
            if paths is None:
                paths = tuple(index.animations_for_skeleton(skeleton, raise_on_error=True))
        except (OSError, TypeError, ValueError, sqlite3.Error) as error:
            raise ProjectIndexServiceError(
                "query_failed",
                str(error) or "Animation index query failed",
                root=normalized,
                cause=error,
            ) from error
        if not paths:
            raise ProjectIndexServiceError(
                "no_match",
                f"No indexed animations match skeleton: {skeleton}",
                root=normalized,
            )
        try:
            manifest = index.dictionary_manifest(skeleton, list(paths))
        except (OSError, TypeError, ValueError, sqlite3.Error):
            # A file may have changed between scan and manifest construction.
            # Refresh and retry once; never loop indefinitely.
            snapshot = self.refresh(
                normalized, skeleton, force=True,
                progress=progress, cancelled=cancelled,
            )
            try:
                index = self._new_index(normalized)
                if explicit_paths is None:
                    paths = tuple(
                        index.animations_for_skeleton(skeleton, raise_on_error=True)
                    )
            except (OSError, TypeError, ValueError, sqlite3.Error) as error:
                raise ProjectIndexServiceError(
                    "query_failed",
                    str(error) or "Animation index query failed",
                    root=normalized,
                    cause=error,
                ) from error
            if not paths:
                raise ProjectIndexServiceError(
                    "no_match",
                    f"No indexed animations match skeleton: {skeleton}",
                    root=normalized,
                )
            try:
                manifest = index.dictionary_manifest(skeleton, list(paths))
            except (OSError, TypeError, ValueError, sqlite3.Error) as error:
                raise ProjectIndexServiceError(
                    "selection_invalid",
                    "Indexed animation selection is stale, missing, or incompatible",
                    root=normalized,
                    cause=error,
                ) from error
        return PreparedAnimationSelection(
            root=normalized,
            skeleton=skeleton,
            paths=tuple(paths),
            manifest=manifest,
            snapshot=snapshot,
        )

    @staticmethod
    def _normalize_root(root, *, require_directory):
        if not isinstance(root, (str, Path)):
            if require_directory:
                raise ProjectIndexServiceError("root_required", "Project Root is required")
            return ""
        raw = str(root).strip()
        if not raw:
            if require_directory:
                raise ProjectIndexServiceError("root_required", "Project Root is required")
            return ""
        try:
            normalized = str(Path(raw).expanduser().resolve())
        except (OSError, RuntimeError, ValueError) as error:
            if require_directory:
                raise ProjectIndexServiceError(
                    "root_unavailable", "Project Root cannot be resolved", root=raw, cause=error
                ) from error
            return raw
        if require_directory and not Path(normalized).is_dir():
            raise ProjectIndexServiceError(
                "root_unavailable",
                "Project Root is not an accessible directory",
                root=normalized,
            )
        return normalized

    @staticmethod
    def _normalize_skeleton(skeleton):
        return skeleton.strip() if isinstance(skeleton, str) else ""

    @classmethod
    def _require_skeleton(cls, skeleton):
        normalized = cls._normalize_skeleton(skeleton)
        if not normalized:
            raise ProjectIndexServiceError(
                "skeleton_required", "Skeleton Filter is required"
            )
        return normalized

    @staticmethod
    def _normalize_paths(paths):
        if isinstance(paths, (str, bytes)) or not isinstance(paths, Iterable):
            raise ProjectIndexServiceError(
                "selection_invalid", "Indexed animation paths must be a sequence"
            )
        items = tuple(paths)
        if not items or any(
            not isinstance(path, (str, Path)) or not str(path).strip()
            for path in items
        ):
            raise ProjectIndexServiceError(
                "selection_invalid", "Indexed animation paths must be non-empty"
            )
        normalized = tuple(
            str(Path(str(path).strip()).expanduser().resolve()) for path in items
        )
        if len(normalized) != len(set(normalized)):
            raise ProjectIndexServiceError(
                "selection_invalid", "Indexed animation paths must be unique"
            )
        return normalized

    @staticmethod
    def _snapshot(root, summary):
        state = summary.get("state", "unavailable")
        message = {
            "ready": "Animation index ready",
            "missing": "Animation index will be created when the import plan is prepared",
            "rebuild_required": "Animation index will be reset when the import plan is prepared",
            "unavailable": "Animation index is unavailable",
        }.get(state, f"Animation index: {state}")
        return ProjectIndexSnapshot(
            root=root,
            state=state,
            schema_version=summary.get("schema_version") or 0,
            generation=summary.get("generation"),
            last_scan_ns=summary.get("last_scan_ns"),
            animation_count=summary.get("animation_count", 0),
            matching_count=summary.get("matching_count", 0),
            message=message,
        )


__all__ = [
    "PreparedAnimationSelection",
    "ProjectAnimationIndexService",
    "ProjectIndexServiceError",
    "ProjectIndexSnapshot",
]
