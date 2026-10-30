"""Skeleton labels for library discovery and legacy import-setting migration.

Animation import and application use upstream UEFormat bone-name binding.
Skeleton labels select library content; they never authorize its application.
"""

from __future__ import annotations

SKELETON_COMPATIBILITY_UEFORMAT = "ueformat"
SKELETON_COMPATIBILITY_STRICT = "strict"
SKELETON_COMPATIBILITY_NAME_RETARGET = "name_retarget"
SKELETON_COMPATIBILITY_MODES = frozenset({
    SKELETON_COMPATIBILITY_UEFORMAT,
    SKELETON_COMPATIBILITY_STRICT,
    SKELETON_COMPATIBILITY_NAME_RETARGET,
})

# Blender EnumProperty-compatible items.  Keep the stored values stable and
# human-readable because they are persisted in PipelineContext/checkpoints.
SKELETON_COMPATIBILITY_ITEMS = (
    (
        SKELETON_COMPATIBILITY_UEFORMAT,
        "UEFormat 原版绑定",
        "绑定同名骨骼，跳过目标中不存在的轨道",
    ),
)


def _canonical_skeleton_aliases(value: object | None) -> frozenset[str]:
    """Return safe display aliases used by pipeline skeleton filters.

    FModel/UE object names are sometimes serialized as a repeated package and
    object name (for example ``SKEL_FM_Base.SKEL_FM_Base``), while the native
    UEFormat identity reader exposes the leaf name (``SKEL_FM_Base``).  Only
    that exact repeated-name form is collapsed.  Conventional Unreal ``SK_`` /
    ``SKEL_`` prefixes and ``_Skeleton`` suffixes are exposed as additional
    exact aliases so a UI filter such as ``Hero`` can match
    ``SK_Hero_Skeleton``.  Arbitrary substring matching is deliberately not
    allowed because ``Base`` must not select ``Base_Child``.
    """

    if value is None:
        return frozenset()
    text = str(value).strip().replace("\\", "/")
    if not text:
        return frozenset()
    text = text.rsplit("/", 1)[-1]
    if "." in text:
        package_name, object_name = text.rsplit(".", 1)
        if package_name.casefold() == object_name.casefold():
            text = package_name

    aliases = {text.casefold()}
    changed = True
    while changed:
        changed = False
        for alias in tuple(aliases):
            for prefix in ("skel_", "sk_"):
                if alias.startswith(prefix) and len(alias) > len(prefix):
                    before = len(aliases)
                    aliases.add(alias[len(prefix):])
                    changed |= len(aliases) > before
            for suffix in ("_skeleton", "_skel"):
                if alias.endswith(suffix) and len(alias) > len(suffix):
                    before = len(aliases)
                    aliases.add(alias[:-len(suffix)])
                    changed |= len(aliases) > before
    return frozenset(aliases)


def skeleton_names_match(source_name: object | None, filter_name: object | None) -> bool:
    """Match exact skeleton names plus the documented repeated-name alias.

    Empty values retain the pipeline's historical unfiltered behavior.  A
    non-empty filter otherwise matches only case-insensitively equal canonical
    aliases; it never performs a substring/fuzzy match.
    """

    source = _canonical_skeleton_aliases(source_name)
    target = _canonical_skeleton_aliases(filter_name)
    if not source or not target:
        return True
    return bool(source & target)


def normalize_skeleton_compatibility_mode(value: object | None) -> str:
    """Migrate old presets to the single upstream UEFormat import behavior."""

    mode = SKELETON_COMPATIBILITY_UEFORMAT if value in (None, "") else value
    if not isinstance(mode, str) or mode not in SKELETON_COMPATIBILITY_MODES:
        raise ValueError(
            "unsupported skeleton compatibility mode: "
            f"{mode!r}; expected one of {sorted(SKELETON_COMPATIBILITY_MODES)}"
        )
    return SKELETON_COMPATIBILITY_UEFORMAT


def is_name_based_compatibility(mode: object | None) -> bool:
    """Compatibility helper for callers persisting older import settings."""

    return normalize_skeleton_compatibility_mode(mode) == SKELETON_COMPATIBILITY_UEFORMAT


def validate_library_action(action, target, policy=None):
    """Validate the target object without comparing skeletons or channels.

    ``policy`` is accepted for saved scenes using the retired strict setting.
    Blender keeps unmatched action channels inert, as with an upstream import.
    """
    if target is None or getattr(target, "type", "") != "ARMATURE":
        raise ValueError("请选择目标骨架")
    return ""


__all__ = [
    "SKELETON_COMPATIBILITY_UEFORMAT",
    "SKELETON_COMPATIBILITY_STRICT",
    "SKELETON_COMPATIBILITY_NAME_RETARGET",
    "SKELETON_COMPATIBILITY_MODES",
    "SKELETON_COMPATIBILITY_ITEMS",
    "skeleton_names_match",
    "normalize_skeleton_compatibility_mode",
    "is_name_based_compatibility",
    "validate_library_action",
]
