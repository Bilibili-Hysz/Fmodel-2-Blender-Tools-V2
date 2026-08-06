import os

import bpy

from .rules import FALLBACK_VALUES


def ensure_material_nodes(material):
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (500, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return nodes, links, bsdf, output


def load_image_safe(path):
    if not path:
        return None
    abs_path = os.path.normpath(os.path.abspath(path))
    if not os.path.exists(abs_path):
        return None
    try:
        return bpy.data.images.load(abs_path, check_existing=True)
    except Exception:
        return None


def set_colorspace(image, channel):
    if not image:
        return
    if channel in ("base_color",):
        image.colorspace_settings.name = "sRGB"
    else:
        image.colorspace_settings.name = "Non-Color"


def _tex_node(nodes, image, x, y):
    node = nodes.new("ShaderNodeTexImage")
    node.location = (x, y)
    node.image = image
    return node


def _build_common_graph(material, resolved_channels, anime=False):
    nodes, links, bsdf, _ = ensure_material_nodes(material)

    y = 260
    if resolved_channels.get("base_color"):
        img = load_image_safe(resolved_channels["base_color"])
        if img:
            set_colorspace(img, "base_color")
            tex = _tex_node(nodes, img, -300, y)
            links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

    y -= 220
    if resolved_channels.get("normal"):
        img = load_image_safe(resolved_channels["normal"])
        if img:
            set_colorspace(img, "normal")
            tex = _tex_node(nodes, img, -300, y)
            nrm = nodes.new("ShaderNodeNormalMap")
            nrm.location = (-70, y)
            links.new(tex.outputs["Color"], nrm.inputs["Color"])
            links.new(nrm.outputs["Normal"], bsdf.inputs["Normal"])

    if resolved_channels.get("orm"):
        img = load_image_safe(resolved_channels["orm"])
        if img:
            set_colorspace(img, "orm")
            tex = _tex_node(nodes, img, -300, -220)
            try:
                sep = nodes.new("ShaderNodeSeparateColor")
                sep.location = (-80, -220)
                links.new(tex.outputs["Color"], sep.inputs["Color"])
                links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
                links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
            except RuntimeError:
                sep = nodes.new("ShaderNodeSeparateRGB")
                sep.location = (-80, -220)
                links.new(tex.outputs["Color"], sep.inputs["Image"])
                links.new(sep.outputs["G"], bsdf.inputs["Roughness"])
                links.new(sep.outputs["B"], bsdf.inputs["Metallic"])
    else:
        if resolved_channels.get("roughness"):
            img = load_image_safe(resolved_channels["roughness"])
            if img:
                set_colorspace(img, "roughness")
                tex = _tex_node(nodes, img, -300, -220)
                links.new(tex.outputs["Color"], bsdf.inputs["Roughness"])
        else:
            bsdf.inputs["Roughness"].default_value = FALLBACK_VALUES["roughness"]

        if resolved_channels.get("metallic"):
            img = load_image_safe(resolved_channels["metallic"])
            if img:
                set_colorspace(img, "metallic")
                tex = _tex_node(nodes, img, -300, -420)
                links.new(tex.outputs["Color"], bsdf.inputs["Metallic"])
        else:
            bsdf.inputs["Metallic"].default_value = FALLBACK_VALUES["metallic"]

    if resolved_channels.get("emissive"):
        img = load_image_safe(resolved_channels["emissive"])
        if img:
            set_colorspace(img, "emissive")
            tex = _tex_node(nodes, img, -300, -620)
            links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])

    if resolved_channels.get("opacity"):
        img = load_image_safe(resolved_channels["opacity"])
        if img:
            set_colorspace(img, "opacity")
            tex = _tex_node(nodes, img, -300, -820)
            links.new(tex.outputs["Color"], bsdf.inputs["Alpha"])
            material.blend_method = "HASHED"

    if anime:
        bsdf.inputs["Roughness"].default_value = min(0.35, bsdf.inputs["Roughness"].default_value)


def build_graph_realistic(material, resolved_channels):
    _build_common_graph(material, resolved_channels, anime=False)


def build_graph_anime(material, resolved_channels):
    _build_common_graph(material, resolved_channels, anime=True)
