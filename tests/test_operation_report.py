from __future__ import annotations

from pathlib import Path

from core.import_session import ImportSession
from core.operation_report import format_last_operation, load_last_operation


def test_load_last_operation_reads_shared_session_and_formats_runtime(tmp_path):
    source = tmp_path / "Hero.uemodel"
    source.write_bytes(b"model")
    session = ImportSession(
        "UEModel",
        source_paths=(source,),
        vendor_version="1.2.0",
        export_operation_id="fmodel-export-1",
        export_manifest_path=str(tmp_path / "EXPORT_MANIFEST.json"),
        import_policy="STRICT",
    )
    session.record_success(source)
    session.record_created("objects", 2)
    session.record_created("materials", 1)
    session.record_metric("material_slots", 3)
    session.record_warning("one texture was missing")
    session.finish()
    scene = {"FModel_Import_Session": session.to_json()}

    report = load_last_operation(scene)
    text = format_last_operation(
        report,
        {
            "state": "ready",
            "version": "1.2.0",
            "source": "bundled",
            "source_path": "vendor/io_scene_ueformat/__init__.py",
            "tree_sha256": "abc123",
            "message": "Bundled UEFormat runtime ready",
        },
    )

    assert report.available is True
    assert report.status == "success"
    assert report.primary_source_path == str(source)
    assert report.source_directory == str(tmp_path)
    assert report.created_counts == {"materials": 1, "objects": 2}
    assert report.export_operation_id == "fmodel-export-1"
    assert report.import_policy == "strict"
    assert "FModel Export Operation ID: fmodel-export-1" in text
    assert "Import Policy: strict" in text
    assert "Tree SHA-256: abc123" in text
    assert "one texture was missing" in text



def test_invalid_stored_session_becomes_visible_failure():
    report = load_last_operation({"FModel_Import_Session": "{not-json"})

    assert report.status == "failed"
    assert report.available is True
    assert report.parse_warning
    assert "invalid" in report.summary.lower()


def test_idle_report_is_not_available():
    report = load_last_operation({})

    assert report.status == "idle"
    assert report.available is False
    assert report.to_dict()["schema_version"] == 1