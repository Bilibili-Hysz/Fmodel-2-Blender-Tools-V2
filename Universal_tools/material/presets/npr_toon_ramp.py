# FModel_Tools/Universal_tools/material/presets/npr_toon_ramp.py
# NPR Toon Ramp 预设 — 简化版卡通渲染（Color Ramp 直接映射）

import bpy

from .base import MaterialPreset
from .processors import TextureProcessor, ProcessorOutput
from .processors import _load_image_safe, _set_colorspace, _tex_node


class NPRToonRampPreset(MaterialPreset):
    """NPR Toon Ramp 预设 — 简化卡通渲染

    使用 Shader to RGB + Color Ramp 实现可调阶数的卡通着色：
    - 基于光照的色阶映射（可配置阶数 2-6）
    - 支持法线贴图
    - 轻量级，适合移动端/实时预览
    """

    PRESET_ID = "npr_toon_ramp"
    PRESET_LABEL = "Toon Ramp"
    PRESET_DESC = "简化卡通渲染：ColorRamp色阶映射，可调阶数2-6，支持法线贴图"
    PRESET_CATEGORY = "NPR"

    # ── 可配置参数 ──
    ramp_stops: int = 3                     # 色阶数 (2-6)
    ramp_interpolation: str = "CONSTANT"    # ColorRamp 插值模式
    shadow_tint: tuple = (0.3, 0.3, 0.4, 1.0)  # 最低阶颜色

    texture_processors = {
        "base_color": TextureProcessor(
            name="base_color",
            type="direct",
            match=["_d", "_diffuse", "_albedo", "_bc", "_basecolor"],
            colorspace="sRGB",
            required=True,
            outputs={"default": ProcessorOutput(socket="Base Color")},
        ),
        "normal": TextureProcessor(
            name="normal",
            type="normal_map",
            match=["_n", "_normal", "_nm"],
            colorspace="Non-Color",
            outputs={"default": ProcessorOutput(socket="Normal", via="NormalMap")},
        ),
    }

    # ============================================================
    # 节点图构建
    # ============================================================

    def build_material_graph(self, material, resolved_textures):
        """构建 Toon Ramp 节点图"""
        props = getattr(bpy.context.scene, "fmodel_material", None)
        if props:
            self.ramp_stops = getattr(props, "npr_ramp_stops", 3)
            self.ramp_interpolation = getattr(props, "npr_ramp_interpolation", "CONSTANT")
            self.shadow_tint = tuple(props.npr_shadow_color)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        nodes.clear()

        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (600, 0)

        diffuse = nodes.new("ShaderNodeBsdfDiffuse")
        diffuse.location = (300, 0)
        links.new(diffuse.outputs["BSDF"], output.inputs["Surface"])

        y = 0

        # ── Base Color → ShaderToRGB → ColorRamp ──
        if "base_color" in resolved_textures:
            img = _load_image_safe(resolved_textures["base_color"])
            if img:
                _set_colorspace(img, "sRGB")
                tex = _tex_node(nodes, img, (-600, y))

                shader_to_rgb = nodes.new("ShaderNodeShaderToRGB")
                shader_to_rgb.location = (-350, y)

                ramp = nodes.new("ShaderNodeValToRGB")
                ramp.location = (-100, y)
                ramp.color_ramp.interpolation = self.ramp_interpolation
                self._setup_ramp(ramp)

                links.new(tex.outputs["Color"], shader_to_rgb.inputs["Color"])
                links.new(shader_to_rgb.outputs["Color"], ramp.inputs["Fac"])
                links.new(ramp.outputs["Color"], diffuse.inputs["Color"])
                y -= 260

        # ── Normal ──
        if "normal" in resolved_textures:
            img = _load_image_safe(resolved_textures["normal"])
            if img:
                _set_colorspace(img, "Non-Color")
                n_tex = _tex_node(nodes, img, (-600, y))
                nrm = nodes.new("ShaderNodeNormalMap")
                nrm.location = (-350, y)
                links.new(n_tex.outputs["Color"], nrm.inputs["Color"])
                links.new(nrm.outputs["Normal"], diffuse.inputs["Normal"])

    def _setup_ramp(self, ramp_node):
        """设置 ColorRamp 的色阶。

        均匀分布 N 个停止点，从暗到亮线性过渡。
        """
        elements = ramp_node.color_ramp.elements
        stops = max(2, min(6, self.ramp_stops))
        # 清除现有中点
        while len(elements) > 2:
            elements.remove(elements[1])
        # 第一个和最后一个保留
        elements[0].position = 0.0
        elements[0].color = self.shadow_tint
        elements[1].position = 1.0
        elements[1].color = (1.0, 1.0, 1.0, 1.0)
        # 插入中间阶
        for i in range(1, stops - 1):
            t = i / (stops - 1)
            brightness = self.shadow_tint[0] + (1.0 - self.shadow_tint[0]) * t
            mid = elements.new(t)
            mid.color = (brightness, brightness, brightness, 1.0)

    # ============================================================
    # UI
    # ============================================================

    def draw_options(self, layout: bpy.types.UILayout, props) -> None:
        """绘制 Toon Ramp 专属选项"""
        col = layout.column(align=True)
        col.prop(props, "npr_ramp_stops", text="色阶数")
        col.prop(props, "npr_ramp_interpolation", text="插值模式")
        col.prop(props, "npr_shadow_color", text="暗部色")
