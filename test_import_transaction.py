from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.import_transaction import ImportTransaction


class Block:
    def __init__(self, name: str, users: int = 0):
        self.name = name
        self.users = users

    def as_pointer(self):
        return id(self)


class Collection(list):
    def remove(self, block, *, do_unlink=False):
        super().remove(block)


def make_data():
    return SimpleNamespace(
        objects=Collection(),
        collections=Collection(),
        meshes=Collection(),
        materials=Collection(),
        images=Collection(),
    )


def test_transaction_removes_only_new_unlinked_datablocks():
    data = make_data()
    old_object = Block("ExistingObject")
    old_mesh = Block("ExistingMesh", users=1)
    data.objects.append(old_object)
    data.meshes.append(old_mesh)

    transaction = ImportTransaction(data, collection_names=("objects", "meshes", "materials"))
    transaction.begin()
    new_object = Block("ImportedObject")
    new_mesh = Block("ImportedMesh")
    new_material = Block("ImportedMaterial")
    data.objects.append(new_object)
    data.meshes.append(new_mesh)
    data.materials.append(new_material)

    assert transaction.created_counts() == {"objects": 1, "meshes": 1, "materials": 1}
    report = transaction.rollback()
    assert report.succeeded
    assert report.removed_count == 3
    assert data.objects == [old_object]
    assert data.meshes == [old_mesh]
    assert data.materials == []


def test_transaction_retains_new_datablocks_still_owned():
    data = make_data()
    transaction = ImportTransaction(data, collection_names=("materials",))
    transaction.begin()
    shared = Block("SharedMaterial", users=1)
    data.materials.append(shared)

    report = transaction.rollback()
    assert not report.succeeded
    assert report.retained == {"materials": 1}
    assert data.materials == [shared]


def test_transaction_is_idempotent_and_commit_is_final():
    data = make_data()
    transaction = ImportTransaction(data, collection_names=("objects",))
    transaction.begin()
    data.objects.append(Block("Imported"))
    first = transaction.rollback()
    second = transaction.rollback()
    assert first == second

    transaction.begin()
    data.objects.append(Block("Committed"))
    assert transaction.commit() == {"objects": 1}
    final = transaction.rollback()
    assert not final.succeeded
    assert "already committed" in final.errors[0]


def test_transaction_requires_begin_before_commit():
    transaction = ImportTransaction(make_data())
    with pytest.raises(RuntimeError, match="state 'new'"):
        transaction.commit()

