from __future__ import annotations

import json

import pytest

from core.import_session import ImportSession, ImportStatus, persist_import_session


def test_import_session_tracks_success_and_created_counts():
    session = ImportSession(
        "UEModel",
        source_paths=("C:/exports/hero.uemodel",),
        vendor_version="1.2.0",
    )
    session.set_phase("import_model")
    session.record_success("C:/exports/hero.uemodel")
    session.record_created("objects", 2)
    session.record_created("materials", 3)
    assert session.finish() is ImportStatus.SUCCESS
    assert session.summary == "UEModel: success; 1 succeeded, 0 failed"
    data = session.to_dict()
    assert data["status"] == "success"
    assert data["created_counts"] == {"materials": 3, "objects": 2}
    assert data["schema_version"] == 1
    assert data["started_at_utc"].endswith("Z")
    assert data["finished_at_utc"].endswith("Z")
    assert json.loads(session.to_json())["vendor_version"] == "1.2.0"


def test_import_session_carries_optional_export_identity_and_policy():
    session = ImportSession(
        "UEModel",
        source_paths=("Hero.uemodel",),
        export_operation_id="fmodel-op-1",
        export_manifest_path="C:/Export/EXPORT_MANIFEST.json",
        import_policy="DIAGNOSTIC",
    )
    session.record_success("Hero.uemodel")
    session.finish()

    data = session.to_dict()
    assert data["export_operation_id"] == "fmodel-op-1"
    assert data["export_manifest_path"].endswith("EXPORT_MANIFEST.json")
    assert data["import_policy"] == "diagnostic"


def test_import_session_keeps_metrics_separate_from_datablock_counts():
    session = ImportSession("UEModel")
    session.record_created("objects", 2)
    session.record_metric("actors", 3)
    session.record_metric("unresolved_models", 1)

    data = json.loads(session.to_json())
    assert data["created_counts"] == {"objects": 2}
    assert data["metrics"] == {"actors": 3, "unresolved_models": 1}


def test_import_session_distinguishes_partial_and_failed():
    partial = ImportSession("UEAnim", source_paths=("good", "bad"))
    partial.record_success("good")
    partial.record_failure("bad", phase="decode", message="invalid archive", code="decode_failed")
    assert partial.finish() is ImportStatus.PARTIAL
    assert partial.summary == "UEAnim: partial; 1 succeeded, 1 failed"
    assert partial.failures[0].detail == {}

    failed = ImportSession("UEPose", source_paths=("bad",))
    failed.record_failure("bad", phase="resolve_bridge", message="missing Vendor")
    assert failed.finish() is ImportStatus.FAILED
    assert failed.summary == "UEPose: failed; 0 succeeded, 1 failed"


def test_import_session_cancel_and_persist():
    session = ImportSession("UEAnim")
    session.cancel("user cancelled")
    assert session.finish() is ImportStatus.CANCELLED
    assert session.summary == "UEAnim: cancelled: user cancelled"

    scene = {}
    persist_import_session(scene, session)
    assert scene["FModel_Import_Status"] == "cancelled"
    assert scene["FModel_Import_Summary"] == session.summary
    assert json.loads(scene["FModel_Import_Session"])["operation_id"] == session.operation_id


def test_import_session_validates_created_counts_and_deduplicates():
    session = ImportSession("UEModel")
    session.record_success("same")
    session.record_success("same")
    session.record_warning("missing texture")
    session.record_warning("missing texture")
    assert session.success_count == 1
    assert session.warnings == ["missing texture"]
    with pytest.raises(ValueError, match="negative"):
        session.record_created("objects", -1)
