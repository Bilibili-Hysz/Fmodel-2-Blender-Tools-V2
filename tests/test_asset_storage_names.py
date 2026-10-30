"""Display labels do not collide with physical volumes or escape directories."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.library_files import (
    storage_component, library_bundle_base, library_index_path,
    safe_child_path, validate_generated_output_filenames,
    staging_chunk_path, validate_staged_blend_path,
)


def test_labels_get_distinct_portable_volume_names(tmp_path):
    labels = ['SK_Hero', 'SK_Hero_Vol_2', 'sk_hero', 'CON', '英雄' * 300]
    validate_generated_output_filenames('Library', labels)
    bases = [library_bundle_base('Library', label) for label in labels]
    names = [base + suffix for base in bases for suffix in ('.blend', '_Vol_2.blend')]
    assert len(set(name.casefold() for name in names)) == len(names)
    for name in names:
        assert len(name) < 255
        assert Path(safe_child_path(str(tmp_path), name)).parent == tmp_path


def test_existing_library_keeps_legacy_bundle_namespace():
    manifest = SimpleNamespace(bundles=[SimpleNamespace(skeletons=['Hero'], blend_file='Library_Hero_Vol_2.blend')])
    assert library_bundle_base('Library', 'Hero', manifest) == 'Library_Hero'
    assert library_bundle_base('Library', 'Hero_Vol_2', manifest) != 'Library_Hero_Vol_2'


def test_staging_accepts_encoded_and_legacy_paths_but_rejects_escape(tmp_path):
    encoded = staging_chunk_path(str(tmp_path), 'CON', 1)
    assert validate_staged_blend_path(str(tmp_path), 'CON', encoded) == encoded
    legacy = str(tmp_path / 'Hero_chunk_0001.blend')
    assert validate_staged_blend_path(str(tmp_path), 'Hero', legacy) == legacy
    old_target_label = str(tmp_path / 'GuardRig_chunk_0001.blend')
    assert validate_staged_blend_path(str(tmp_path), 'SourceSkeleton', old_target_label) == old_target_label
    with pytest.raises(ValueError):
        validate_staged_blend_path(str(tmp_path), 'Hero', str(tmp_path.parent / 'Hero_chunk_0001.blend'))


def test_library_display_label_is_encoded_without_path_escape(tmp_path):
    path = library_index_path(str(tmp_path), '../../CON')
    assert Path(path).parent == tmp_path
    assert storage_component('../../CON') in Path(path).name
