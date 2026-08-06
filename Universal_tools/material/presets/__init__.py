# FModel_Tools/Universal_tools/material/presets/__init__.py
# 预设注册中心 — PRESET_REGISTRY + 注册/查找/枚举函数

from typing import Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from .base import MaterialPreset


# ============================================================
# 全局预设注册表
# ============================================================

PRESET_REGISTRY: dict[str, Type["MaterialPreset"]] = {}
"""全局预设注册表。key = PRESET_ID, value = MaterialPreset 子类"""


# ============================================================
# 注册管理
# ============================================================

def register_preset(preset_cls: Type["MaterialPreset"]) -> None:
    """注册一个材质预设。

    Args:
        preset_cls: MaterialPreset 的子类

    Raises:
        ValueError: 如果 PRESET_ID 已存在
    """
    preset_id = preset_cls.PRESET_ID
    if not preset_id:
        raise ValueError(f"预设类 {preset_cls.__name__} 必须定义 PRESET_ID")

    if preset_id in PRESET_REGISTRY:
        existing = PRESET_REGISTRY[preset_id]
        raise ValueError(
            f"PRESET_ID '{preset_id}' 已被注册（现有: {existing.__name__}，尝试: {preset_cls.__name__}）"
        )

    PRESET_REGISTRY[preset_id] = preset_cls


def unregister_preset(preset_id: str) -> None:
    """注销一个材质预设。

    Args:
        preset_id: 预设 ID
    """
    PRESET_REGISTRY.pop(preset_id, None)


def is_registered(preset_id: str) -> bool:
    """检查预设是否已注册"""
    return preset_id in PRESET_REGISTRY


# ============================================================
# 预设查找
# ============================================================

def get_preset(preset_id: str) -> Optional["MaterialPreset"]:
    """获取预设实例。

    Args:
        preset_id: 预设 ID（如 "pbr_standard"）

    Returns:
        MaterialPreset 实例，或 None
    """
    cls = PRESET_REGISTRY.get(preset_id)
    if cls is None:
        return None
    return cls()


def get_preset_class(preset_id: str) -> Optional[Type["MaterialPreset"]]:
    """获取预设类（不实例化）"""
    return PRESET_REGISTRY.get(preset_id)


# ============================================================
# UI 枚举生成
# ============================================================

def get_preset_enum_items(
    category_filter: str = "ALL",
) -> list[tuple[str, str, str, str, int]]:
    """生成 Blender EnumProperty items。

    Args:
        category_filter: 类别过滤器 "ALL" | "PBR" | "NPR" | "Hybrid"

    Returns:
        [(id, label, description, icon, index), ...]
    """
    items = []
    for i, preset_cls in enumerate(PRESET_REGISTRY.values()):
        if category_filter != "ALL" and getattr(preset_cls, "PRESET_CATEGORY", "") != category_filter:
            continue
        items.append((
            preset_cls.PRESET_ID,
            preset_cls.PRESET_LABEL,
            preset_cls.PRESET_DESC,
            "SHADING_RENDERED",  # 默认图标
            i,
        ))
    return items


def get_preset_count(category_filter: str = "ALL") -> int:
    """获取已注册预设数量"""
    if category_filter == "ALL":
        return len(PRESET_REGISTRY)
    return sum(
        1 for cls in PRESET_REGISTRY.values()
        if getattr(cls, "PRESET_CATEGORY", "") == category_filter
    )


# ============================================================
# 预设发现（用于自动扫描注册）
# ============================================================

def discover_and_register(preset_module_names: list[str]) -> int:
    """从模块列表中发现并注册预设。

    每个模块应导出 MaterialPreset 子类。
    使用约定：模块中第一个 MaterialPreset 子类即为该模块的预设。

    Args:
        preset_module_names: 模块名列表，如 ["pbr_standard", "pbr_orm"]

    Returns:
        成功注册的预设数量
    """
    import importlib

    count = 0
    for mod_name in preset_module_names:
        try:
            mod = importlib.import_module(f".{mod_name}", package=__package__)
            # 查找模块中的 MaterialPreset 子类
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and attr.__name__ != "MaterialPreset"
                    and hasattr(attr, "PRESET_ID")
                    and attr.PRESET_ID
                ):
                    try:
                        register_preset(attr)
                        count += 1
                    except ValueError:
                        # 已注册
                        pass
                    break  # 每个模块只取第一个预设类
        except ImportError as e:
            print(f"[FModel Presets] 无法导入预设模块 {mod_name}: {e}")

    return count
