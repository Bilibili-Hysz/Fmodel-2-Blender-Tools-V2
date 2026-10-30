"""Pose sources survive storage without losing unrecognized wire attributes."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("pose_asset_record", Path(__file__).parents[1] / "core/pose_asset_record.py")
record_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record_module)


def record(data=b"UEFORMAT unknown future attribute"):
    return record_module.make_record(data, "source.uepose", "Source", "/Game/Source", .01, "CUE4PARSE_ADDITIVE")


def test_source_roundtrip_is_byte_exact():
    original = b"UEFORMAT\x00\xff\xfe unknown metadata"
    assert record_module.decode_record(record(original)) == original


def test_reimport_updates_same_source_identity_but_revision_changes():
    old, new = record(b"old"), record(b"new")
    assert old['asset_id'] == new['asset_id']
    assert old['source_sha256'] != new['source_sha256']


@pytest.mark.parametrize('field,value', [('source_base64', 'invalid!'), ('source_sha256', 'f'*64),
                                        ('asset_id', 'f'*64), ('scale', float('nan')),
                                        ('scale', 0), ('convention', 'ABSOLUTE_GUESS')])
def test_invalid_or_tampered_record_is_rejected(field, value):
    saved = record()
    saved[field] = value
    with pytest.raises(ValueError):
        record_module.decode_record(saved)


def test_pose_identity_distinguishes_same_named_entries():
    assert record_module.pose_entry_id('Smile', 0) != record_module.pose_entry_id('Smile', 1)


def test_large_source_uses_short_lines_and_survives_portable_roundtrip():
    import json
    data = b'opaque future metadata\x00' * 60_000
    saved = record(data)
    serialized = record_module.dumps(saved)
    assert saved['schema'] == 2
    assert max(map(len, serialized.splitlines())) < 4200
    assert record_module.decode_record(json.loads(serialized)) == data


def test_optional_pose_budget_and_record_length_are_enforced(monkeypatch):
    saved = record()
    saved['source_byte_length'] += 1
    with pytest.raises(ValueError, match='长度'):
        record_module.decode_record(saved)
    monkeypatch.setenv('FMODEL_UEFORMAT_MAX_BYTES', '4')
    with pytest.raises(ValueError, match='configured'):
        record(b'valid source exceeding configured budget')
