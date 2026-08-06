"""Material ownership contracts shared by canonical and legacy import paths."""

from __future__ import annotations

from collections.abc import Mapping


# These keys are written by the canonical io_scene_ueformat FModel material
# builder. A legacy JSON resolver must never replace a material carrying any of
# these records, because doing so would discard source-slot/URI provenance.
CANONICAL_FMODEL_PROVENANCE_KEYS = (
    "fmodel_material_link",
    "fmodel_material_build_profile",
    "fmodel_material_cache_identity",
    "fmodel_parameter_metadata",
)


def _metadata_value(material, key):
    if isinstance(material, Mapping):
        return material.get(key)
    getter = getattr(material, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except (AttributeError, KeyError, TypeError, RuntimeError):
            return None
    return None


def has_canonical_fmodel_provenance(material) -> bool:
    """Return whether canonical FModel material metadata is present."""

    return any(
        value not in (None, "", [], {}, ())
        for key in CANONICAL_FMODEL_PROVENANCE_KEYS
        for value in (_metadata_value(material, key),)
    )


def should_skip_legacy_material_resolver(material) -> bool:
    """Return whether legacy JSON/heuristic rebuilding must not touch material."""

    return has_canonical_fmodel_provenance(material)
