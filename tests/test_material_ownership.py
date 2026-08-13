"""Pure-Python material ownership contracts for the V2 migration."""

from core.material_ownership import (
    CANONICAL_FMODEL_PROVENANCE_KEYS,
    has_canonical_fmodel_provenance,
    should_skip_legacy_material_resolver,
)


def test_canonical_fmodel_material_link_is_protected():
    material = {
        "fmodel_material_link": '{"slot_index": 0, "material_json_uri": "Materials/M_Body.json"}',
    }
    assert has_canonical_fmodel_provenance(material)
    assert should_skip_legacy_material_resolver(material)


def test_synthetic_fmodel_materials_v2_provenance_is_protected():
    material = {
        key: f"synthetic-{key}"
        for key in CANONICAL_FMODEL_PROVENANCE_KEYS
    }
    assert should_skip_legacy_material_resolver(material)


def test_legacy_or_unowned_material_remains_eligible_for_fallback():
    material = {"name": "M_Body", "fmodel_importer_owned": False}
    assert not has_canonical_fmodel_provenance(material)
    assert not should_skip_legacy_material_resolver(material)


def test_blender_like_material_getter_is_supported():
    class FakeMaterial:
        def __init__(self):
            self.values = {"fmodel_parameter_metadata": "[]"}

        def get(self, key):
            return self.values.get(key)

    assert has_canonical_fmodel_provenance(FakeMaterial())
