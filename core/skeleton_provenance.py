"""Blender-independent FModel skeleton provenance helpers.

The canonical UEFormat Blender importer stores a JSON-safe record on imported
armatures under ``fmodel_skeleton_identity``.  V2 must treat that record as
part of the import contract instead of silently treating an old armature as a
strict FModel asset.

This module intentionally has no Blender import.  It accepts ordinary Python
mappings/JSON strings and can therefore be used by checkpoint, project-index,
and contract-test code as well as by Blender operators.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


SKELETON_IDENTITY_PROPERTY = "fmodel_skeleton_identity"
SKELETON_IDENTITY_VERSION = 1
PROVENANCE_STRICT = "strict"
PROVENANCE_LEGACY = "legacy"
PROVENANCE_INVALID = "invalid"

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


class SkeletonProvenanceError(ValueError):
    """Raised when a present FModel identity is malformed."""


@dataclass(frozen=True)
class SkeletonProvenance:
    """A normalized identity plus an explicit compatibility state.

    ``legacy`` means that no identity was present.  It is deliberately not
    treated as strict: old FBX/PSK/UEFormat imports can still be used through
    the permissive path, but their identity is not eligible for strict resume
    or skeleton compatibility claims.
    """

    state: str
    identity: dict[str, Any] | None = None
    fingerprint: str = ""
    reason: str = ""

    @property
    def is_strict(self) -> bool:
        return self.state == PROVENANCE_STRICT and self.identity is not None

    @property
    def is_legacy(self) -> bool:
        return self.state == PROVENANCE_LEGACY

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "identity": self.identity,
            "fingerprint": self.fingerprint,
            "reason": self.reason,
        }


def normalize_skeleton_identity(raw: Any) -> dict[str, Any]:
    """Parse and normalize one canonical FModel identity record.

    The canonical importer emits JSON with the following base fields::

        version, skeleton_path, has_skeleton_guid, skeleton_guid,
        bone_layout_hash

    ``source_animation_path`` and ``source_pose_path`` are retained for target
    animation/pose identities.  Bytes are accepted for GUID/hash values so
    synthetic tests and future non-Blender callers can pass decoded records.
    """

    payload = _decode_mapping(raw)
    version = payload.get("version", SKELETON_IDENTITY_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version != SKELETON_IDENTITY_VERSION:
        raise SkeletonProvenanceError(
            f"unsupported FModel skeleton identity version: {version!r}"
        )

    skeleton_path = _logical_unreal_path(payload.get("skeleton_path"), "skeleton_path")

    raw_guid = payload.get("skeleton_guid")
    has_guid = payload.get("has_skeleton_guid", raw_guid is not None)
    if not isinstance(has_guid, bool):
        raise SkeletonProvenanceError("has_skeleton_guid must be a boolean")
    skeleton_guid = _hex_value(raw_guid, byte_length=16, field_name="skeleton_guid")
    if has_guid and skeleton_guid is None:
        raise SkeletonProvenanceError("has_skeleton_guid is true but skeleton_guid is missing")
    if not has_guid and skeleton_guid is not None:
        raise SkeletonProvenanceError("skeleton_guid is present while has_skeleton_guid is false")

    bone_layout_hash = _hex_value(
        payload.get("bone_layout_hash"),
        byte_length=32,
        field_name="bone_layout_hash",
    )
    if bone_layout_hash is None:
        raise SkeletonProvenanceError("bone_layout_hash is required")

    normalized: dict[str, Any] = {
        "version": SKELETON_IDENTITY_VERSION,
        "skeleton_path": skeleton_path,
        "has_skeleton_guid": has_guid,
        "skeleton_guid": skeleton_guid,
        "bone_layout_hash": bone_layout_hash,
    }
    for key in ("source_animation_path", "source_pose_path"):
        if key in payload and payload[key] is not None:
            normalized[key] = _logical_unreal_path(payload[key], key)
    if "source_animation_path" in normalized and "source_pose_path" in normalized:
        raise SkeletonProvenanceError(
            "an identity cannot contain both source_animation_path and source_pose_path"
        )
    return normalized


def skeleton_identity_fingerprint(identity: Any) -> str:
    """Return a deterministic SHA-256 fingerprint for a valid identity."""

    normalized = normalize_skeleton_identity(identity)
    payload = json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def strict_skeleton_identity_differences(source: Any, target: Any) -> tuple[str, ...]:
    """Return strict-identity differences using the native importer semantics.

    A GUID is an additional discriminator only when both records contain one.
    This deliberately matches ``io_scene_ueformat``: older UEMODELs that lack
    a GUID are not rejected solely for that omission, but path and ordered bone
    layout must always agree before a strict animation import can proceed.
    """

    source_identity = normalize_skeleton_identity(source)
    target_identity = normalize_skeleton_identity(target)
    differences: list[str] = []
    if source_identity["skeleton_path"] != target_identity["skeleton_path"]:
        differences.append("skeleton path differs")
    if (
        source_identity["has_skeleton_guid"]
        and target_identity["has_skeleton_guid"]
        and source_identity["skeleton_guid"] != target_identity["skeleton_guid"]
    ):
        differences.append("skeleton GUID differs")
    if source_identity["bone_layout_hash"] != target_identity["bone_layout_hash"]:
        differences.append("bone layout hash differs")
    return tuple(differences)


def inspect_skeleton_provenance(raw: Any) -> SkeletonProvenance:
    """Classify a property value as strict, legacy, or invalid.

    Missing/empty values intentionally become ``legacy`` rather than
    ``strict``.  This makes old imported armatures visible to callers without
    breaking the permissive workflow.
    """

    if _is_missing(raw):
        return SkeletonProvenance(
            state=PROVENANCE_LEGACY,
            reason="FModel skeleton identity is not present",
        )
    try:
        identity = normalize_skeleton_identity(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return SkeletonProvenance(
            state=PROVENANCE_INVALID,
            reason=str(error) or "FModel skeleton identity is malformed",
        )
    return SkeletonProvenance(
        state=PROVENANCE_STRICT,
        identity=identity,
        fingerprint=skeleton_identity_fingerprint(identity),
    )


def skeleton_provenance_from_owner(owner: Any) -> SkeletonProvenance:
    """Read ``fmodel_skeleton_identity`` from a Blender-like ID object."""

    if owner is None:
        return inspect_skeleton_provenance(None)
    getter = getattr(owner, "get", None)
    if not callable(getter):
        return inspect_skeleton_provenance(None)
    return inspect_skeleton_provenance(getter(SKELETON_IDENTITY_PROPERTY, None))


def provenance_record(value: SkeletonProvenance | Any) -> dict[str, Any]:
    """Return JSON-safe state data suitable for task/checkpoint records."""

    if isinstance(value, SkeletonProvenance):
        return value.to_dict()
    return inspect_skeleton_provenance(value).to_dict()


def validate_provenance_record(raw: Any) -> dict[str, Any]:
    """Validate a persisted provenance envelope used by checkpoints.

    Empty records are the explicit legacy representation.  Strict records must
    contain a normalized identity whose fingerprint matches the persisted
    fingerprint; otherwise a stale or tampered checkpoint must not be resumed.
    """

    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, Mapping):
        raise SkeletonProvenanceError("provenance record must be a JSON object")

    payload = dict(raw)
    state = payload.get("state", PROVENANCE_LEGACY)
    if state not in {PROVENANCE_STRICT, PROVENANCE_LEGACY, PROVENANCE_INVALID}:
        raise SkeletonProvenanceError(f"invalid provenance state: {state!r}")

    fingerprint = payload.get("fingerprint", "")
    reason = payload.get("reason", "")
    if not isinstance(fingerprint, str) or not isinstance(reason, str):
        raise SkeletonProvenanceError("provenance fingerprint and reason must be strings")

    identity = payload.get("identity")
    if state == PROVENANCE_STRICT:
        normalized = normalize_skeleton_identity(identity)
        expected = skeleton_identity_fingerprint(normalized)
        if fingerprint != expected:
            raise SkeletonProvenanceError("strict provenance fingerprint does not match identity")
        return {
            "state": PROVENANCE_STRICT,
            "identity": normalized,
            "fingerprint": fingerprint,
            "reason": reason,
        }

    if identity not in (None, {}):
        raise SkeletonProvenanceError(
            f"{state} provenance must not contain a skeleton identity"
        )
    if fingerprint:
        raise SkeletonProvenanceError(
            f"{state} provenance must not contain a fingerprint"
        )
    return {
        "state": state,
        "identity": None,
        "fingerprint": "",
        "reason": reason,
    }


def _decode_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as error:
            raise SkeletonProvenanceError("identity bytes must be UTF-8 JSON") from error
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SkeletonProvenanceError("identity property must contain valid JSON") from error
    if not isinstance(raw, Mapping):
        raise SkeletonProvenanceError("FModel skeleton identity must be a JSON object")
    return dict(raw)


def _logical_unreal_path(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.isspace() or value != value.strip():
        raise SkeletonProvenanceError(f"invalid {field_name}")
    if "\0" in value or value.startswith("//") or "\\" in value:
        raise SkeletonProvenanceError(f"invalid {field_name}")
    if len(value) >= 3 and value[1] == ":" and value[2] == "/" and value[0].isalpha():
        raise SkeletonProvenanceError(f"invalid {field_name}")
    if value.casefold().startswith("file:") or not value.startswith("/"):
        raise SkeletonProvenanceError(f"invalid {field_name}")
    if any(segment in ("", ".", "..") for segment in value.split("/")[1:]):
        raise SkeletonProvenanceError(f"invalid {field_name}")
    return value


def _hex_value(value: Any, *, byte_length: int, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).hex()
    if not isinstance(value, str):
        raise SkeletonProvenanceError(f"{field_name} must be hexadecimal")
    if len(value) != byte_length * 2 or _HEX_RE.fullmatch(value) is None:
        raise SkeletonProvenanceError(
            f"{field_name} must contain exactly {byte_length * 2} hexadecimal characters"
        )
    return value.lower()


def _is_missing(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


__all__ = [
    "PROVENANCE_INVALID",
    "PROVENANCE_LEGACY",
    "PROVENANCE_STRICT",
    "SKELETON_IDENTITY_PROPERTY",
    "SKELETON_IDENTITY_VERSION",
    "SkeletonProvenance",
    "SkeletonProvenanceError",
    "inspect_skeleton_provenance",
    "normalize_skeleton_identity",
    "provenance_record",
    "skeleton_identity_fingerprint",
    "strict_skeleton_identity_differences",
    "validate_provenance_record",
    "skeleton_provenance_from_owner",
]
