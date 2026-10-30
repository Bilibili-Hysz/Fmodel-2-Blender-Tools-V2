from __future__ import annotations

from pathlib import Path
import tempfile

from scripts.sync_to_blender_addon import (
    RUNTIME_DIRECTORIES,
    RUNTIME_FILES,
    iter_runtime_files,
    sync_runtime,
)


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _make_fixture(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_FILES:
        (root / name).write_text(f"# {name}\n", encoding="utf-8")
    for directory in RUNTIME_DIRECTORIES:
        package = root / directory
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text(f"# {directory}\n", encoding="utf-8")
        (package / "runtime.txt").write_text("runtime\n", encoding="utf-8")
    (root / "vendor" / "__pycache__").mkdir()
    (root / "vendor" / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")
    (root / "Shaders" / "Backup").mkdir()
    (root / "Shaders" / "Backup" / "ignored.md").write_text("ignored", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "ignored.md").write_text("ignored", encoding="utf-8")


def test_runtime_file_selection_excludes_caches_and_backups():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory)
        _make_fixture(source)
        selected = {path.relative_to(source).as_posix() for path in iter_runtime_files(source)}

    assert "__init__.py" in selected
    assert "workflow_state.py" in selected
    assert "vendor/runtime.txt" in selected
    assert all("__pycache__" not in path for path in selected)
    assert all("Backup" not in path for path in selected)
    assert all(not path.startswith("docs/") for path in selected)


def test_retired_format_sources_are_removed_from_the_source_tree():
    retired = {
        "Fmodel/importers/animation/operator_anim_psa.py",
        "Fmodel/importers/model/operator_model_gltf.py",
        "Fmodel/importers/model/operator_model_psk.py",
        "Fmodel/importers/export/operator_export_fbx.py",
        "Fmodel/importers/export/operator_export_gltf.py",
        "Universal_tools/mesh/scheme_psk_auto.py",
        "Universal_tools/mesh/scheme_psk_mesh_fix.py",
        "Universal_tools/skeleton/scheme_auto.py",
        "Universal_tools/skeleton/scheme_copy.py",
        "Universal_tools/mesh/scheme_morph_merge.py",
        "core/project_index.py",
        "core/project_index_service.py",
        "core/project_index_worker.py",
        "Fmodel/project_assets.py",
        "Fmodel/Pipeline/project_index_controller.py",
        "Assets/Utils/incremental_preview.py",
        "Assets/Utils/link_existing_library.py",
    }
    for relative in retired:
        assert not (SOURCE_ROOT / relative).exists(), relative
    assert not (SOURCE_ROOT / "investigation").exists()
    assert not (SOURCE_ROOT / "Shaders" / "Backup").exists()


def test_sync_replaces_stale_target_and_keeps_a_backup():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source"
        addons = root / "addons"
        target = addons / "FModel_Tools"
        _make_fixture(source)
        target.mkdir(parents=True)
        (target / "stale.py").write_text("stale", encoding="utf-8")

        result = sync_runtime(source_root=source, addons_directory=addons)

        assert result.target == target.resolve()
        assert result.backup is not None
        assert result.backup.is_dir()
        assert (result.backup / "stale.py").read_text(encoding="utf-8") == "stale"
        assert not (target / "stale.py").exists()
        assert (target / "vendor" / "runtime.txt").exists()
        assert not (target / "vendor" / "__pycache__").exists()
        assert not (target / "Shaders" / "Backup").exists()
        assert result.file_count == len(RUNTIME_FILES) + len(RUNTIME_DIRECTORIES) * 2


def test_sync_refuses_a_target_outside_the_addons_directory():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source"
        addons = root / "addons"
        _make_fixture(source)
        outside = root / "outside" / "FModel_Tools"
        try:
            sync_runtime(source_root=source, addons_directory=addons, package_name="../outside")
        except ValueError:
            pass
        else:
            raise AssertionError("expected unsafe package target to be rejected")
        assert not outside.exists()
