# FModel_Tools/Universal_tools/material/presets/pbr_orm.py
# PBR + ORM 预设 — 使用 ORM 打包贴图替代独立贴图
# 迁移自 resolver/builder.py _build_common_graph() 的 ORM 分支

from .base import MaterialPreset
from .processors import TextureProcessor, ProcessorOutput


class PBRORMPreset(MaterialPreset):
    """PBR + ORM 预设 — ORM 打包贴图优先

    与 pbr_standard 的区别：
    - ORM 的 R 通道映射到 AO（不需要独立 AO 贴图）
    - ORM 存在时，独立的 roughness/metallic/ao 匹配被抑制
    - roughness 降低上限（适配风格化/动画风格）
    """

    PRESET_ID = "pbr_orm"
    PRESET_LABEL = "PBR + ORM"
    PRESET_DESC = "使用ORM打包贴图的PBR管线（R=AO/G=Roughness/B=Metallic），适配风格化材质"
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
        "orm": TextureProcessor(
            name="orm",
            type="rgb_split",
            match=["_orm", "_arm", "_occlusionroughnessmetallic"],
            colorspace="Non-Color",
            priority=10,
            outputs={
                "R": ProcessorOutput(socket=None, label="AO"),
                "G": ProcessorOutput(socket="Roughness", label="Roughness"),
                "B": ProcessorOutput(socket="Metallic", label="Metallic"),
            },
        ),
        "roughness": TextureProcessor(
            name="roughness",
            type="direct",
            match=["_roughness", "_rough", "_r"],
            colorspace="Non-Color",
            priority=-10,
            fallback_value=0.5,
            outputs={"default": ProcessorOutput(socket="Roughness")},
        ),
        "metallic": TextureProcessor(
            name="metallic",
            type="direct",
            match=["_metallic", "_metal", "_m"],
            colorspace="Non-Color",
            priority=-10,
            fallback_value=0.0,
            outputs={"default": ProcessorOutput(socket="Metallic")},
        ),
        "ao": TextureProcessor(
            name="ao",
            type="direct",
            match=["_ao", "_occlusion"],
            colorspace="Non-Color",
            priority=-10,
            fallback_value=1.0,
            outputs={"default": ProcessorOutput(socket=None, note="独立AO贴图（ORM存在时跳过）")},
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
    }

    merge_strategy = {"orm_priority": "override"}
    # ORM 贴图存在时，忽略独立的 roughness/metallic/ao 贴图（通过优先级实现）

    def build_material_graph(self, material, resolved_textures):
        """构建 ORM PBR 节点图"""
        super().build_material_graph(material, resolved_textures)

        # 透明度贴图 → 设置混合模式
        if "opacity" in resolved_textures:
            material.blend_method = "HASHED"

        if not material.blend_method:
            material.blend_method = "OPAQUE"
