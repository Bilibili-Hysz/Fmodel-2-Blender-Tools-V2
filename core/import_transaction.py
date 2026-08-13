"""Best-effort Blender datablock transaction for importer rollback.

The transaction intentionally accepts a generic ``bpy.data``-like object so its
ownership and rollback rules can be tested without launching Blender. It only
removes datablocks created after ``begin()`` and never purges pre-existing data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


DEFAULT_DATA_COLLECTIONS = (
    "objects",
    "collections",
    "actions",
    "meshes",
    "armatures",
    "materials",
    "images",
    "textures",
    "node_groups",
    "curves",
    "cameras",
    "lights",
    "shape_keys",
)


@dataclass(frozen=True, slots=True)
class ImportRollbackReport:
    removed: dict[str, int]
    retained: dict[str, int]
    errors: tuple[str, ...] = ()

    @property
    def removed_count(self) -> int:
        return sum(self.removed.values())

    @property
    def retained_count(self) -> int:
        return sum(self.retained.values())

    @property
    def succeeded(self) -> bool:
        return not self.errors and not self.retained


class ImportTransaction:
    """Track and remove only datablocks created during one import operation."""

    def __init__(
        self,
        data: Any,
        *,
        collection_names: Iterable[str] = DEFAULT_DATA_COLLECTIONS,
    ) -> None:
        self.data = data
        self.collection_names = tuple(dict.fromkeys(collection_names))
        self._snapshots: dict[str, set[int]] = {}
        self.state = "new"
        self.last_rollback: ImportRollbackReport | None = None

    @staticmethod
    def _identity(block: Any) -> int:
        pointer = getattr(block, "as_pointer", None)
        if callable(pointer):
            try:
                return int(pointer())
            except (TypeError, ValueError, RuntimeError):
                pass
        return id(block)

    def _collection(self, name: str):
        if self.data is None:
            return None
        return getattr(self.data, name, None)

    @staticmethod
    def _blocks(collection) -> list[Any]:
        try:
            return list(collection)
        except TypeError:
            return []

    def begin(self) -> "ImportTransaction":
        if self.state not in {"new", "rolled_back", "committed"}:
            raise RuntimeError(f"Cannot begin transaction in state {self.state!r}")
        self._snapshots.clear()
        for name in self.collection_names:
            collection = self._collection(name)
            if collection is not None:
                self._snapshots[name] = {
                    self._identity(block) for block in self._blocks(collection)
                }
        self.state = "active"
        self.last_rollback = None
        return self

    def _created_blocks(self, name: str) -> list[Any]:
        collection = self._collection(name)
        if collection is None:
            return []
        snapshot = self._snapshots.get(name, set())
        return [
            block for block in self._blocks(collection)
            if self._identity(block) not in snapshot
        ]

    def created_counts(self) -> dict[str, int]:
        if self.state == "new":
            return {}
        return {
            name: len(self._created_blocks(name))
            for name in self.collection_names
            if self._created_blocks(name)
        }

    @staticmethod
    def _remove(collection, block) -> None:
        remover = getattr(collection, "remove", None)
        if not callable(remover):
            raise RuntimeError("datablock collection does not expose remove()")
        try:
            remover(block, do_unlink=True)
        except TypeError:
            remover(block)

    @staticmethod
    def _users(block) -> int:
        users = getattr(block, "users", 0)
        try:
            return int(users)
        except (TypeError, ValueError):
            return 0

    def commit(self) -> dict[str, int]:
        if self.state != "active":
            raise RuntimeError(f"Cannot commit transaction in state {self.state!r}")
        created = self.created_counts()
        self.state = "committed"
        return created

    def rollback(self) -> ImportRollbackReport:
        if self.state == "new":
            report = ImportRollbackReport({}, {}, ("transaction was never started",))
            self.last_rollback = report
            return report
        if self.state == "committed":
            report = ImportRollbackReport({}, {}, ("transaction was already committed",))
            self.last_rollback = report
            return report
        if self.state == "rolled_back" and self.last_rollback is not None:
            return self.last_rollback

        removed: dict[str, int] = {}
        retained: dict[str, int] = {}
        errors: list[str] = []
        # Objects must be unlinked before their data blocks; collections are
        # retried after object removal so newly-created children can disappear.
        ordered_names = tuple(dict.fromkeys(("objects", "collections", *self.collection_names)))
        for _ in range(3):
            changed = False
            for name in ordered_names:
                collection = self._collection(name)
                if collection is None:
                    continue
                for block in self._created_blocks(name):
                    if name not in {"objects", "collections"} and self._users(block) > 0:
                        retained[name] = retained.get(name, 0) + 1
                        continue
                    try:
                        self._remove(collection, block)
                    except Exception as exc:
                        errors.append(f"{name}:{getattr(block, 'name', '<unnamed>')}: {exc}")
                        continue
                    removed[name] = removed.get(name, 0) + 1
                    changed = True
            if not changed:
                break

        # Recompute retained blocks after all removal passes. A block may have
        # become unused only after its owning object/collection was removed.
        retained = {}
        for name in self.collection_names:
            count = len(self._created_blocks(name))
            if count:
                retained[name] = count
        self.state = "rolled_back"
        report = ImportRollbackReport(removed, retained, tuple(errors))
        self.last_rollback = report
        return report
