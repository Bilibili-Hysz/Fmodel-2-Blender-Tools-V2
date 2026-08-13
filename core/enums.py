# FModel_Tools/core/enums.py
# 枚举定义 — PipelineStage、SourceType、ImportMode

from enum import Enum

from .skeleton_compatibility import (
    SKELETON_COMPATIBILITY_STRICT,
    SKELETON_COMPATIBILITY_NAME_RETARGET,
    SKELETON_COMPATIBILITY_MODES,
    SKELETON_COMPATIBILITY_ITEMS,
    skeleton_names_match,
    normalize_skeleton_compatibility_mode,
    is_name_based_compatibility,
)


class PipelineStage(Enum):
    """管线阶段枚举"""
    DICTIONARY_ACQUISITION = "dictionary_acquisition"
    FORMAT_CONVERSION = "format_conversion"  # 新增：格式中转阶段
    ASSET_IMPORT = "asset_import"
    LIBRARY_BUILDING = "library_building"
    FINALIZATION = "finalization"


class StageStatus(Enum):
    """阶段状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class SourceType(Enum):
    """数据源类型枚举"""
    UNITY_ANIM = "unity_anim"
    FMODEL_UEANIM = "fmodel_ueanim"
    FBX_BATCH = "fbx_batch"
    CUSTOM = "custom"


class ImportMode(Enum):
    """导入模式枚举"""
    FBX_MATCH = "fbx_match"
    UEANIM_DIRECT = "ueanim_direct"
    LINK_EXISTING = "link_existing"


# UEFormat-only runtime contract. Legacy enum values remain as read-only
# migration tokens until the physical cleanup phase removes the old source code.
UEFORMAT_SOURCE_TYPE = SourceType.FMODEL_UEANIM.value
UEFORMAT_IMPORT_MODE = ImportMode.UEANIM_DIRECT.value
UEFORMAT_IMPORT_MODES = frozenset({
    UEFORMAT_IMPORT_MODE,
    ImportMode.LINK_EXISTING.value,
})


def normalize_ueformat_pipeline_config(config):
    """Normalize persisted pipeline settings to the native UEFormat route.

    Old .blend/checkpoint data may contain Unity or FBX-match values. They are
    not reused as if they were equivalent: the values are replaced with the
    native route and a migration record is returned so callers can invalidate
    stale scan/import state before continuing.
    """

    normalized = dict(config or {})
    migration = {}
    source_type = normalized.get("source_type", UEFORMAT_SOURCE_TYPE)
    if source_type != UEFORMAT_SOURCE_TYPE:
        migration["source_type"] = source_type
        normalized["source_type"] = UEFORMAT_SOURCE_TYPE

    import_mode = normalized.get("import_mode", UEFORMAT_IMPORT_MODE)
    if import_mode not in UEFORMAT_IMPORT_MODES:
        migration["import_mode"] = import_mode
        normalized["import_mode"] = UEFORMAT_IMPORT_MODE

    if migration:
        previous = dict(normalized.get("ueformat_migration", {}))
        previous.update({"from": migration, "reason": "legacy format route disabled"})
        normalized["ueformat_migration"] = previous
    return normalized, migration


class ItemType(Enum):
    """树形项目类型"""
    FOLDER = "FOLDER"
    ANIM = "ANIM"


class ApplyMethod(Enum):
    """动画应用模式"""
    REPLACE = "REPLACE"
    APPEND_NLA = "APPEND_NLA"


class DisplayMode(Enum):
    """显示模式"""
    TREE = "TREE"
    DETAIL_H = "DETAIL_H"
    DETAIL_V = "DETAIL_V"


# 枚举到Blender EnumProperty的转换辅助
def enum_to_items(enum_class):
    """将枚举类转换为Blender EnumProperty的items格式"""
    return [(e.value, e.name.replace('_', ' ').title(), e.value) for e in enum_class]


def register():
    """Core enums不需要注册Blender类"""
    pass

def unregister():
    """Core enums不需要注册Blender类"""
    pass
