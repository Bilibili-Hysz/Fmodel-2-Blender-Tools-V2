# FModel_Tools/Universal_tools/material/presets/processors.py
# 贴图处理器定义 — TextureProcessor 数据类 + 五种处理器类型的构建函数

import os
from dataclasses import dataclass, field
from typing import Optional

import bpy


# ============================================================
# 数据类
# ============================================================

@dataclass
class ProcessorOutput:
    """单个处理器输出定义 — 描述一个输出连接到哪个套接字"""
    socket: Optional[str] = None   # 目标 BSDF/节点组套接字名；None 表示不连接
    via: Optional[str] = None      # 中间节点类型: "NormalMap" | "SeparateRGB" | 节点组名 | None
    label: str = ""                # UI 显示标签
    note: str = ""                 # 注释（AI 生成或手动）


@dataclass
class TextureProcessor:
    """贴图处理器 — 定义一种贴图类型的完整处理管线"""
    name: str
    type: str                           # "direct" | "normal_map" | "rgb_split" | "custom_node_group" | "ignored"
    match: list = field(default_factory=list)
    colorspace: str = "sRGB"
    outputs: dict = field(default_factory=dict)
    required: bool = False
    fallback_value: Optional[float] = None
    priority: int = 0
    tool_groups: list = field(default_factory=list)  # [{"name": "FlipNormalY", "source": "tools.blend"}]


# ============================================================
# 共享工具（从 builder.py 抽取）
# ============================================================

def _load_image_safe(path: str) -> Optional[bpy.types.Image]:
    """安全加载贴图"""
    if not path:
        return None
    abs_path = os.path.normpath(os.path.abspath(path))
    if not os.path.exists(abs_path):
        return None
    try:
        return bpy.data.images.load(abs_path, check_existing=True)
    except Exception:
        return None


def _set_colorspace(image: Optional[bpy.types.Image], colorspace: str) -> None:
    """设置贴图色彩空间"""
    if not image:
        return
    try:
        image.colorspace_settings.name = colorspace
    except TypeError:
        # Fallback for older Blender versions
        if colorspace == "Non-Color":
            image.colorspace_settings.name = "Non-Color"
        else:
            image.colorspace_settings.name = "sRGB"


def _tex_node(nodes: bpy.types.Nodes, image: bpy.types.Image, location: tuple) -> bpy.types.Node:
    """创建 TexImage 节点"""
    node = nodes.new("ShaderNodeTexImage")
    node.location = location
    node.image = image
    return node


# ============================================================
# 工具节点组链
# ============================================================

_TOOLS_BLEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "Shaders", "tools.blend",
)


def _resolve_tool_blend(source: str = "") -> str:
    """解析工具节点组来源 .blend 路径"""
    if source and os.path.isabs(source):
        return source
    if source and os.path.exists(source):
        return source
    if os.path.exists(_TOOLS_BLEND):
        return _TOOLS_BLEND
    return source or ""


def _ensure_tool_node_group(tool_name: str, source: str = "") -> Optional[bpy.types.NodeGroup]:
    """确保工具节点组已加载到 bpy.data.node_groups 中"""
    ng = bpy.data.node_groups.get(tool_name)
    if ng:
        return ng
    blend_path = _resolve_tool_blend(source)
    if not blend_path or not os.path.exists(blend_path):
        return None
    try:
        with bpy.data.libraries.load(blend_path, link=False) as (df, dt):
            if tool_name in df.node_groups:
                dt.node_groups = [tool_name]
        return bpy.data.node_groups.get(tool_name)
    except Exception:
        return None


def _insert_tool_chain(
    tex_node: bpy.types.Node,
    processor: TextureProcessor,
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    y: int,
) -> tuple[Optional[bpy.types.Node], int]:
    """在 TexImage 之后插入工具节点组链，返回链尾节点和新的 Y 坐标。

    Returns:
        (last_node, next_y): 链尾输出节点，若无工具组则返回 tex_node 自身
    """
    if not processor.tool_groups:
        return tex_node, y

    source = _resolve_tool_blend()
    prev_output = tex_node.outputs["Color"]
    prev_alpha = tex_node.outputs["Alpha"]
    current_node = tex_node
    current_y = y

    for i, tg in enumerate(processor.tool_groups):
        ng_name = tg.get("name") if isinstance(tg, dict) else str(tg)
        tg_source = tg.get("source", source) if isinstance(tg, dict) else source

        ng = _ensure_tool_node_group(ng_name, tg_source)
        if not ng:
            continue

        grp = nodes.new("ShaderNodeGroup")
        grp.node_tree = ng
        grp.location = (-300 + (i + 1) * 200, current_y)

        # 连接输入
        for socket in grp.inputs:
            sock_name = socket.name.lower().replace(" ", "_")
            if "texture_input" in sock_name or "textureinput" in sock_name:
                links.new(prev_output, socket)
            elif "alpha_input" in sock_name or "alphainput" in sock_name:
                links.new(prev_alpha, socket)
            elif "normal_input" in sock_name or "normalinput" in sock_name:
                if prev_alpha and prev_alpha != current_node.outputs[0]:
                    links.new(prev_alpha, socket)
            elif "roughness_input" in sock_name:
                links.new(prev_output, socket)
            elif "metallic_input" in sock_name or "ao_input" in sock_name:
                links.new(prev_output, socket)

        # 找下一个链的输出源
        # 优先 Texture Output / Alpha Output，其次第一个输出
        texture_out = None
        alpha_out = None
        fallback_out = None
        for socket in grp.outputs:
            sock_name = socket.name.lower().replace(" ", "_")
            if "texture_output" in sock_name or "textureoutput" in sock_name:
                texture_out = socket
            elif "alpha_output" in sock_name or "alphaoutput" in sock_name:
                alpha_out = socket
            elif fallback_out is None:
                fallback_out = socket

        prev_output = texture_out or fallback_out
        prev_alpha = alpha_out or fallback_out
        current_node = grp
        current_y -= 200

    return current_node, current_y


# ============================================================
# 处理器构建函数
# ============================================================

def build_processor_graph(
    material: bpy.types.Material,
    processor: TextureProcessor,
    texture_path: str,
    bsdf: bpy.types.Node,
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    node_y: int = 0,
) -> int:
    """根据处理器类型构建节点子图。

    Args:
        material: 目标材质
        processor: 贴图处理器定义
        texture_path: 解析到的贴图文件路径
        bsdf: BSDF 或输出节点组引用
        nodes: 节点树引用
        links: 链接引用
        node_y: 当前 Y 坐标起始值

    Returns:
        下一个可用的 Y 坐标
    """
    if processor.type == "ignored":
        return node_y

    if processor.type == "direct":
        return _build_direct(material, processor, texture_path, bsdf, nodes, links, node_y)
    elif processor.type == "normal_map":
        return _build_normal_map(material, processor, texture_path, bsdf, nodes, links, node_y)
    elif processor.type == "rgb_split":
        return _build_rgb_split(material, processor, texture_path, bsdf, nodes, links, node_y)
    elif processor.type == "custom_node_group":
        return _build_custom_group(material, processor, texture_path, bsdf, nodes, links, node_y)
    else:
        print(f"[FModel Preset] 未知处理器类型: {processor.type}，跳过 {processor.name}")
        return node_y


def _build_direct(
    material, processor: TextureProcessor, path: str,
    bsdf, nodes, links, y: int,
) -> int:
    """TexImage → [工具链] → socket"""
    img = _load_image_safe(path)
    if not img:
        return y
    _set_colorspace(img, processor.colorspace)

    tex = _tex_node(nodes, img, (-300, y))
    chain_node, y = _insert_tool_chain(tex, processor, nodes, links, y)
    # 从链尾取 Color 输出
    chain_output = chain_node.outputs[0] if chain_node else tex.outputs["Color"]
    for out_name, out in processor.outputs.items():
        if out.socket:
            try:
                links.new(chain_output, bsdf.inputs[out.socket])
            except (KeyError, TypeError):
                pass
    return y - 220


def _build_normal_map(
    material, processor: TextureProcessor, path: str,
    bsdf, nodes, links, y: int,
) -> int:
    """TexImage → [工具链] → NormalMap → Normal socket"""
    img = _load_image_safe(path)
    if not img:
        return y
    _set_colorspace(img, processor.colorspace)

    tex = _tex_node(nodes, img, (-300, y))
    chain_node, y = _insert_tool_chain(tex, processor, nodes, links, y)
    chain_output = chain_node.outputs[0] if chain_node else tex.outputs["Color"]

    nrm = nodes.new("ShaderNodeNormalMap")
    nrm.location = (-70, y)
    try:
        links.new(chain_output, nrm.inputs["Color"])
    except (KeyError, TypeError):
        pass

    for out_name, out in processor.outputs.items():
        if out.socket:
            try:
                links.new(nrm.outputs["Normal"], bsdf.inputs[out.socket])
            except (KeyError, TypeError):
                pass
    return y - 220


def _build_rgb_split(
    material, processor: TextureProcessor, path: str,
    bsdf, nodes, links, y: int,
) -> int:
    """TexImage → [工具链] → SeparateRGB → R/G/B sockets"""
    img = _load_image_safe(path)
    if not img:
        return y
    _set_colorspace(img, processor.colorspace)

    tex = _tex_node(nodes, img, (-300, y))
    chain_node, y = _insert_tool_chain(tex, processor, nodes, links, y)
    chain_output = chain_node.outputs[0] if chain_node else tex.outputs["Color"]

    try:
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.location = (-80, y)
        links.new(chain_output, sep.inputs["Color"])
        channel_map = {
            "R": sep.outputs["Red"],
            "G": sep.outputs["Green"],
            "B": sep.outputs["Blue"],
        }
    except (RuntimeError, KeyError):
        sep = nodes.new("ShaderNodeSeparateRGB")
        sep.location = (-80, y)
        links.new(chain_output, sep.inputs["Image"])
        channel_map = {
            "R": sep.outputs["R"],
            "G": sep.outputs["G"],
            "B": sep.outputs["B"],
        }
    for ch_name, out in processor.outputs.items():
        if ch_name not in channel_map:
            continue
        if out.socket:
            try:
                links.new(channel_map[ch_name], bsdf.inputs[out.socket])
            except (KeyError, TypeError):
                pass
    return y - 220


def _build_custom_group(
    material, processor: TextureProcessor, path: str,
    bsdf, nodes, links, y: int,
) -> int:
    """TexImage → [工具链] → 外部节点组 → outputs"""
    img = _load_image_safe(path)
    if not img:
        return y
    _set_colorspace(img, processor.colorspace)

    tex = _tex_node(nodes, img, (-300, y))
    chain_node, y = _insert_tool_chain(tex, processor, nodes, links, y)
    chain_output = chain_node.outputs[0] if chain_node else tex.outputs["Color"]

    for out_name, out in processor.outputs.items():
        if out.socket:
            try:
                links.new(chain_output, bsdf.inputs[out.socket])
            except (KeyError, TypeError):
                pass
    return y - 220
