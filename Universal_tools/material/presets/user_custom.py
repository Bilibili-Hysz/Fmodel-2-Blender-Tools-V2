# FModel_Tools/Universal_tools/material/presets/user_custom.py
# 自定义预设加载器 — 从 JSON 方案文件动态创建 MaterialPreset

import json
import os
from typing import Optional, Type, TYPE_CHECKING

from .base import MaterialPreset
from .processors import TextureProcessor, ProcessorOutput

if TYPE_CHECKING:
    from . import MaterialPreset as MaterialPresetType


# ============================================================
# JSON 加载与验证
# ============================================================

def load_preset_from_json(filepath: str) -> Type["MaterialPresetType"]:
    """从 JSON 文件加载并创建自定义预设类。

    Args:
        filepath: 预设方案 JSON 文件路径

    Returns:
        动态创建的 MaterialPreset 子类

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: JSON 格式无效
    """
    abs_path = os.path.normpath(os.path.abspath(filepath))
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"预设方案文件不存在: {abs_path}")

    with open(abs_path, "r", encoding="utf-8") as f:
        profile = json.load(f)

    return create_custom_preset_class(profile, source_path=abs_path)


def create_custom_preset_class(
    profile: dict,
    source_path: str = "",
) -> Type["MaterialPresetType"]:
    """从方案字典创建自定义预设类。

    使用类工厂模式：根据 JSON 中的 texture_processors 定义，
    动态创建一个 MaterialPreset 子类。

    Args:
        profile: 预设方案字典（符合 texture_processors schema）
        source_path: 方案文件路径（用于 node_group_source 的相对路径解析）

    Returns:
        动态创建的 MaterialPreset 子类（可直接传给 register_preset）

    Raises:
        ValueError: 方案缺少必要字段
    """
    _validate_profile(profile)

    preset_id = profile["preset_id"]
    preset_label = profile.get("preset_label", preset_id)
    preset_desc = profile.get("preset_desc", "")
    preset_category = profile.get("preset_category", "Hybrid")
    node_group_source = profile.get("node_group_source")
    merge_strategy = profile.get("merge_strategy", {})
    ai_metadata = profile.get("ai_metadata", {})

    # 解析 texture_processors
    texture_processors = {}
    for proc_name, proc_data in profile["texture_processors"].items():
        outputs = {}
        for out_name, out_data in proc_data.get("outputs", {}).items():
            outputs[out_name] = ProcessorOutput(
                socket=out_data.get("socket"),
                via=out_data.get("via"),
                label=out_data.get("label", ""),
                note=out_data.get("note", ""),
            )

        texture_processors[proc_name] = TextureProcessor(
            name=proc_name,
            type=proc_data.get("type", "direct"),
            match=proc_data.get("match", []),
            colorspace=proc_data.get("colorspace", "sRGB"),
            required=proc_data.get("required", False),
            fallback_value=proc_data.get("fallback_value"),
            priority=proc_data.get("priority", 0),
            outputs=outputs,
            tool_groups=proc_data.get("tool_groups", []),
        )

    # 动态创建类
    custom_cls = type(
        f"CustomPreset_{preset_id}",
        (MaterialPreset,),
        {
            "PRESET_ID": preset_id,
            "PRESET_LABEL": preset_label,
            "PRESET_DESC": preset_desc,
            "PRESET_CATEGORY": preset_category,
            "texture_processors": texture_processors,
            "merge_strategy": merge_strategy,
            "node_group_source": _resolve_node_group_path(node_group_source, source_path),
            "ai_metadata": ai_metadata,
        },
    )

    return custom_cls


def _validate_profile(profile: dict) -> None:
    """验证方案 JSON 的必要字段"""
    if not isinstance(profile, dict):
        raise ValueError("方案必须是 JSON 对象")

    if not profile.get("preset_id"):
        raise ValueError("方案缺少 preset_id")

    if "texture_processors" not in profile:
        raise ValueError("方案缺少 texture_processors")

    processors = profile["texture_processors"]
    if not isinstance(processors, dict) or len(processors) == 0:
        raise ValueError("texture_processors 必须是非空对象")

    for name, proc in processors.items():
        if not isinstance(proc, dict):
            raise ValueError(f"处理器 '{name}' 必须是对象")
        if "type" not in proc:
            raise ValueError(f"处理器 '{name}' 缺少 type 字段")
        valid_types = {"direct", "normal_map", "rgb_split", "custom_node_group", "ignored"}
        if proc["type"] not in valid_types:
            raise ValueError(
                f"处理器 '{name}' 的 type '{proc['type']}' 无效，"
                f"有效值: {', '.join(sorted(valid_types))}"
            )


def _resolve_node_group_path(
    node_group_source: Optional[str],
    source_path: str,
) -> Optional[str]:
    """解析节点组来源路径。

    如果 node_group_source 是相对路径，相对于 source_path 解析。
    格式: "path/to/file.blend\\NodeTree\\GroupName"
    """
    if not node_group_source:
        return None

    # 已经是绝对路径或包含 \\ 分隔符？
    if os.path.isabs(node_group_source) or "\\" in node_group_source:
        return node_group_source

    # 相对路径：相对于 source_path
    if source_path:
        base_dir = os.path.dirname(source_path)
        resolved = os.path.normpath(os.path.join(base_dir, node_group_source))
        return resolved

    return node_group_source


# ============================================================
# 工具：导出预设方案 JSON
# ============================================================

def export_preset_to_json(preset: MaterialPreset, filepath: str) -> str:
    """将预设导出为 JSON 方案文件。

    Args:
        preset: 任意 MaterialPreset 实例
        filepath: 输出文件路径

    Returns:
        写入的绝对路径
    """
    abs_path = os.path.normpath(os.path.abspath(filepath))
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)

    data = preset.to_dict()
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return abs_path
