# FModel_Tools/Universal_tools/material/presets/pbr_standard.py
# PBR Standard 预设 — Principled BSDF 标准 PBR 管线
# 迁移自 resolver/rules.py 和 resolver/builder.py _build_common_graph(anime=False)

from .base import MaterialPreset
from .processors import TextureProcessor, ProcessorOutput


class PBRStandardPreset(MaterialPreset):
    """标准 PBR 预设 — 基于 Principled BSDF

    通道定义完全兼容现有 resolver/rules.py 的 CHANNELS/CHANNEL_ALIASES/SUFFIX_PATTERNS。
    build_material_graph() 输出与现有 build_graph_realistic() 完全一致。
    """

    PRESET_ID = "pbr_standard"
    PRESET_LABEL = "Standard PBR"
    PRESET_DESC = "基于Principled BSDF的标准PBR管线，8通道"
    PRESET_CATEGORY = "PBR"

    texture_processors = {
        "base_color": TextureProcessor(
            name="base_color",
            type="direct",
            match=["_basecolor", "_albedo", "_diffuse", "_bc", "_d"],
            colorspace="sRGB",
            required=True,
            outputs={"default": ProcessorOutput(socket="Base Color")},
        ),
        "normal": TextureProcessor(
            name="normal",
            type="normal_map",
            match=["_normal", "_nm", "_n"],
            colorspace="Non-Color",
            required=True,
            outputs={"default": ProcessorOutput(socket="Normal", via="NormalMap")},
        ),
        "roughness": TextureProcessor(
            name="roughness",
            type="direct",
            match=["_roughness", "_rough", "_r"],
            colorspace="Non-Color",
            fallback_value=0.5,
            outputs={"default": ProcessorOutput(socket="Roughness")},
        ),
        "metallic": TextureProcessor(
            name="metallic",
            type="direct",
            match=["_metallic", "_metal", "_m"],
            colorspace="Non-Color",
            fallback_value=0.0,
            outputs={"default": ProcessorOutput(socket="Metallic")},
        ),
        "ao": TextureProcessor(
            name="ao",
            type="direct",
            match=["_ao", "_occlusion"],
            colorspace="Non-Color",
            fallback_value=1.0,
            outputs={"default": ProcessorOutput(socket=None, note="单独AO贴图，可选连接")},
        ),
        "emissive": TextureProcessor(
            name="emissive",
            type="direct",
            match=["_emissive", "_emission", "_e"],
            colorspace="sRGB",
            outputs={"default": ProcessorOutput(socket="Emission Color")},
        ),
        "opacity": TextureProcessor(
            name="opacity",
            type="direct",
            match=["_opacity", "_alpha", "_mask"],
            colorspace="Non-Color",
            outputs={"default": ProcessorOutput(socket="Alpha")},
        ),
        "orm": TextureProcessor(
            name="orm",
            type="rgb_split",
            match=["_orm", "_arm"],
            colorspace="Non-Color",
            priority=10,
            outputs={
                "G": ProcessorOutput(socket="Roughness", label="Roughness"),
                "B": ProcessorOutput(socket="Metallic", label="Metallic"),
            },
        ),
    }

    merge_strategy = {"orm_priority": "separate_only"}
    # ORM 贴图存在时优先使用 ORM 的 G/B 通道
    # 但独立的 roughness/metallic 贴图仍然可用

    def build_material_graph(self, material, resolved_textures):
        """构建 PBR 节点图，与 build_graph_realistic() 输出一致"""
        super().build_material_graph(material, resolved_textures)

        # 透明度贴图 → 设置混合模式
        if "opacity" in resolved_textures:
            material.blend_method = "HASHED"

        # 确保 blend_method 回退到默认
        if not material.blend_method:
            material.blend_method = "OPAQUE"
