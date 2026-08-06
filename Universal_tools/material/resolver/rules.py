# ⚠ DEPRECATED: 通道定义已迁移至 channel_resolver.py (_CHANNEL_LABELS, _CHANNEL_SUFFIXES, _BUILTIN_COLORSPACE)
# 旧版 resolver v1 路径保留用于向后兼容。新功能请在 channel_resolver.py 中定义。
CHANNELS = (
    "base_color",
    "normal",
    "roughness",
    "metallic",
    "ao",
    "emissive",
    "opacity",
    "orm",
)


CHANNEL_ALIASES = {
    "base_color": ["basecolor", "albedo", "diffuse", "bc", "d"],
    "normal": ["normal", "nm", "n"],
    "roughness": ["roughness", "rough", "r"],
    "metallic": ["metallic", "metal", "m"],
    "ao": ["ao", "ambientocclusion", "occlusion"],
    "emissive": ["emissive", "emission", "e"],
    "opacity": ["opacity", "alpha", "mask", "trans"],
    "orm": ["orm", "occlusionroughnessmetallic", "arm"],
}


SUFFIX_PATTERNS = {
    "base_color": ["_basecolor", "_albedo", "_diffuse", "_bc", "_d"],
    "normal": ["_normal", "_nm", "_n"],
    "roughness": ["_roughness", "_rough", "_r"],
    "metallic": ["_metallic", "_metal", "_m"],
    "ao": ["_ao", "_occlusion"],
    "emissive": ["_emissive", "_emission", "_e"],
    "opacity": ["_opacity", "_alpha", "_mask"],
    "orm": ["_orm", "_arm"],
}


TEXTURE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tga", ".tiff", ".exr", ".bmp")


FALLBACK_VALUES = {
    "roughness": 0.5,
    "metallic": 0.0,
    "ao": 1.0,
    "emissive": 0.0,
    "opacity": 1.0,
}


AMBIGUOUS_DELTA_THRESHOLD = 0.12
