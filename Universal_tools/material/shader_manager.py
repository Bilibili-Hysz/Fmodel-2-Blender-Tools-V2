# FModel_Tools/Universal_tools/material/shader_manager.py
# 着色器管理器 — 节点组扫描、接口映射管理、自定义接口、一键创建空白着色器

import json
import os
from typing import Optional, List, Dict

import bpy

from .presets import register_preset, unregister_preset, PRESET_REGISTRY
from .presets.user_custom import create_custom_preset_class
from .presets.processors import TextureProcessor, ProcessorOutput
from .channel_resolver import _CHANNEL_LABELS, _CHANNEL_SUFFIXES, _BUILTIN_COLORSPACE


# ============================================================
# 标准接口定义（派生自 channel_resolver 的权威通道数据）
# ============================================================

def _build_standard_interfaces() -> dict:
    """从 channel_resolver 构建标准接口映射，确保与材质管线通道定义一致。"""
    result = {}
    for ch_id, label in _CHANNEL_LABELS.items():
        if ch_id == "other":
            continue
        match_suffixes = _CHANNEL_SUFFIXES.get(ch_id, [])
        colorspace = _BUILTIN_COLORSPACE.get(ch_id, "sRGB")
        result[ch_id] = {
            "label": label,
            "match": match_suffixes,
            "colorspace": colorspace,
        }
    return result

STANDARD_INTERFACES = _build_standard_interfaces()


# ============================================================
# 自定义接口 PropertyGroup
# ============================================================

class CustomInterfaceItem(bpy.types.PropertyGroup):
    """单个自定义接口模板"""
    interface_id: bpy.props.StringProperty(name="ID", default="custom")
    label: bpy.props.StringProperty(name="标签", default="Custom")
    match_suffixes: bpy.props.StringProperty(
        name="匹配后缀", default="_custom",
        description="逗号分隔的文件名后缀列表，如 _d,_diffuse,_albedo",
    )
    colorspace: bpy.props.EnumProperty(
        name="色彩空间",
        items=[("sRGB", "sRGB", "颜色贴图"), ("Non-Color", "Non-Color", "数据贴图")],
        default="Non-Color",
    )

    def get_match_list(self) -> list:
        """解析逗号分隔的 suffix 字符串为列表"""
        return [s.strip() for s in self.match_suffixes.split(",") if s.strip()]

    def to_dict(self):
        return {
            "label": self.label,
            "match": self.get_match_list(),
            "colorspace": self.colorspace,
        }


class ShaderManagerProperties(bpy.types.PropertyGroup):
    """着色器管理器场景属性"""
    custom_interfaces: bpy.props.CollectionProperty(type=CustomInterfaceItem)
    active_custom_index: bpy.props.IntProperty(default=0)
    active_node_group: bpy.props.StringProperty(
        name="活动节点组",
        description="当前管理的着色器节点组",
    )
    new_socket_name: bpy.props.StringProperty(name="名称", default="NewSocket")
    new_socket_type: bpy.props.EnumProperty(
        name="类型",
        items=[
            ("NodeSocketColor", "Color", "颜色"),
            ("NodeSocketVector", "Vector", "向量"),
            ("NodeSocketFloat", "Float", "浮点"),
            ("NodeSocketFloatFactor", "Factor", "因子"),
            ("NodeSocketInt", "Int", "整数"),
        ],
        default="NodeSocketColor",
    )
    new_socket_desc: bpy.props.StringProperty(name="描述", default="", description="接口用途说明（可选）")
    socket_mappings_json: bpy.props.StringProperty(
        name="接口映射",
        default="{}",
        description="JSON字典，key=socket名, value=标准接口ID",
    )


def _get_socket_mapping(manager, ng_name: str, socket_name: str) -> str:
    """读取 socket 当前映射（按节点组隔离）"""
    data = _load_mapping_dict(manager, ng_name)
    return data.get(socket_name, "__NONE__")


def _set_socket_mapping(manager, ng_name: str, socket_name: str, value: str):
    """设置 socket 的映射值（仅在 operator execute 中调用）"""
    data = _load_mapping_dict(manager, ng_name)
    data[socket_name] = value
    _save_mapping_dict(manager, ng_name, data)


def _load_mapping_dict(manager, ng_name: str) -> dict:
    """从 StringProperty 加载映射字典。存储格式: {ng_name: {socket: mapping}}"""
    raw = getattr(manager, "socket_mappings_json", "{}")
    try:
        all_data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        all_data = {}
    if not isinstance(all_data, dict):
        all_data = {}
    # Migrate old flat format: {socket: mapping} → {ng_name: {socket: mapping}}
    if all_data and not any(isinstance(v, dict) for v in all_data.values()):
        all_data = {"__legacy__": dict(all_data)}
    return all_data.get(ng_name, {})


def _save_mapping_dict(manager, ng_name: str, data: dict):
    """保存映射字典到 StringProperty"""
    raw = getattr(manager, "socket_mappings_json", "{}")
    try:
        all_data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        all_data = {}
    if not isinstance(all_data, dict):
        all_data = {}
    all_data[ng_name] = data
    manager.socket_mappings_json = json.dumps(all_data, ensure_ascii=False)


def _build_socket_mapping_from_ui(manager, ng_name: str) -> dict:
    """从 UI 映射数据构建 socket_mapping 字典（name → interface_id）"""
    data = _load_mapping_dict(manager, ng_name)
    return {k: v for k, v in data.items() if v and v != "__NONE__"}


def _get_mapped_interface_name(choice: str) -> str:
    """将映射值转为可读名字"""
    if not choice or choice == "__NONE__":
        return "未映射"
    if choice == "__CUSTOM__":
        return "自定义"
    info = STANDARD_INTERFACES.get(choice)
    if info:
        return info["label"]
    if choice.startswith("custom:"):
        return choice.split(":", 1)[-1]
    return choice


def _resolve_socket_mapping(ng, manager=None) -> dict:
    """构建 socket 映射字典。优先 UI 手动映射，回退自动按名匹配。"""
    ui_map = _build_socket_mapping_from_ui(manager, ng.name) if manager else {}
    smap = {}
    for inp in iter_node_group_inputs(ng):
        if _socket_is_internal(inp):
            continue
        ident = _socket_ident(inp)
        if inp.name in ui_map:
            smap[ident] = ui_map[inp.name]
            continue
        key = inp.name.lower().strip()
        matched = None
        for std_key, std_info in STANDARD_INTERFACES.items():
            if std_info["label"].lower() == key:
                matched = std_key
                break
        smap[ident] = matched or "__CUSTOM__"
    return smap


def _auto_map_on_import(manager, ng):
    """导入时一次性自动填充映射（仅在映射字典为空时执行）"""
    data = _load_mapping_dict(manager, ng.name)
    # Already has mappings? Skip — user may have customized.
    if data:
        return
    socket_mapping = _resolve_socket_mapping(ng, manager)
    for inp in iter_node_group_inputs(ng):
        if _socket_is_internal(inp):
            continue
        ident = _socket_ident(inp)
        val = socket_mapping.get(ident, "__NONE__")
        if val not in ("__NONE__", "__CUSTOM__"):
            data[inp.name] = val
    if data:
        _save_mapping_dict(manager, ng.name, data)


# ============================================================
# 合并接口列表（标准 + 自定义）
# ============================================================

def _get_all_interfaces() -> dict:
    """合并标准接口和用户自定义接口"""
    result = dict(STANDARD_INTERFACES)
    props = bpy.context.scene.fmodel_shader_manager if hasattr(bpy.context.scene, "fmodel_shader_manager") else None
    if props:
        for item in props.custom_interfaces:
            result[item.interface_id] = item.to_dict()
    return result


# ============================================================
# 节点组选择
# ============================================================

def get_active_node_group():
    """获取当前活动的节点组。优先级: 选中Group节点 → 编辑树 → 场景属性。"""
    # 1. Selected Group node
    ng = _selected_group_node_tree()
    if ng:
        return ng
    # 2. Node editor's current tree
    ng = _node_editor_tree()
    if ng:
        return ng
    # 3. Scene property fallback
    props = bpy.context.scene.fmodel_shader_manager if hasattr(bpy.context.scene, "fmodel_shader_manager") else None
    if props and props.active_node_group:
        return bpy.data.node_groups.get(props.active_node_group)
    return None


def _selected_group_node_tree():
    """Get the node tree of the selected Group node in the node editor."""
    try:
        area = next(a for a in bpy.context.screen.areas if a.type == 'NODE_EDITOR')
    except StopIteration:
        return None
    for node in area.spaces[0].edit_tree.nodes if area.spaces[0].edit_tree else []:
        if node.type == 'GROUP' and node.select:
            return node.node_tree
    return None


def _node_editor_tree():
    """Get the node tree currently being edited in the node editor."""
    try:
        area = next(a for a in bpy.context.screen.areas if a.type == 'NODE_EDITOR')
        return area.spaces[0].edit_tree
    except (StopIteration, AttributeError, IndexError):
        return None


def select_node_group(name: str):
    """设置当前活动的节点组（写入场景属性）"""
    if hasattr(bpy.context.scene, "fmodel_shader_manager"):
        bpy.context.scene.fmodel_shader_manager.active_node_group = name


# ============================================================
# 节点组工具
# ============================================================

def iter_node_group_inputs(ng) -> list:
    """兼容 Blender 5.0 的输入 socket 迭代。
    Blender 5.0 中 ng.inputs 属性存在但返回空，需要用 interface.items_tree。"""
    # 优先使用 Blender 5.0 的 interface 体系
    if hasattr(ng, "interface") and hasattr(ng.interface, "items_tree"):
        items = []
        for item in ng.interface.items_tree:
            if getattr(item, "in_out", "") == "INPUT":
                items.append(item)
        if items:
            return items
    # Fallback: 旧版 Blender 的 inputs 属性
    if hasattr(ng, "inputs"):
        result = list(ng.inputs)
        if result:
            return result
    return []


def _socket_hidden(item) -> bool:
    """安全检测 interface item 是否隐藏（兼容 Blender 5.0）"""
    return bool(getattr(item, "hide", False))


def _socket_ident(item) -> str:
    """安全获取 interface item 的标识符（兼容 Blender 5.0）"""
    return str(getattr(item, "identifier", item.name) if hasattr(item, "name") else "")


def _socket_is_internal(item) -> bool:
    """检测是否为内部 socket（以 __ 开头或隐藏）"""
    if _socket_hidden(item):
        return True
    ident = _socket_ident(item)
    return bool(ident.startswith("__"))


def count_node_group_inputs(ng) -> int:
    return len([i for i in iter_node_group_inputs(ng) if not _socket_is_internal(i)])


# ============================================================
# 着色器列表加载
# ============================================================

_SHADERS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "Shaders"
)
_SHADERS_JSON = os.path.join(_SHADERS_DIR, "shaders.json")
_CUSTOM_JSON = os.path.join(_SHADERS_DIR, "Customshaders.json")
_SHADERS_BLEND = os.path.join(_SHADERS_DIR, "shaders.blend")
_CUSTOM_BLEND = os.path.join(_SHADERS_DIR, "Customshaders.blend")
_EXPORTS_DIR = os.path.join(_SHADERS_DIR, "CustomExported")
_BACKUP_DIR = os.path.join(_SHADERS_DIR, "Backup")


def _load_shader_list(section: str) -> dict:
    """读取着色器列表: 'defaults' 读 shaders.json, 'custom' 读 Customshaders.json"""
    json_path = _SHADERS_JSON if section == "defaults" else _CUSTOM_JSON
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get(section, {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _ensure_data_dirs():
    """确保目录存在"""
    for d in (_EXPORTS_DIR, _BACKUP_DIR):
        os.makedirs(d, exist_ok=True)


# ============================================================
# 从映射构建 preset
# ============================================================

def _build_preset_from_mapping(ng, socket_mapping: dict, preset_label="", preset_category="Hybrid") -> dict:
    """从映射构建 preset。socket_mapping key 可以是 identifier 或 name。"""
    processors = {}
    socket_map = {}  # socket名 → 标准接口ID
    for inp in iter_node_group_inputs(ng):
        if _socket_is_internal(inp):
            continue
        ident = _socket_ident(inp)
        ikey = socket_mapping.get(inp.name, "__NONE__")
        if ikey == "__NONE__":
            ikey = socket_mapping.get(ident, socket_mapping.get(inp.name, "__NONE__"))
        if ikey == "__NONE__":
            continue

        all_ifaces = _get_all_interfaces()

        if ikey.startswith("custom:"):
            cid = ikey.split(":", 1)[1]
            std = all_ifaces.get(cid)
        elif ikey != "__CUSTOM__":
            std = all_ifaces.get(ikey)
        else:
            std = None

        if ikey == "__CUSTOM__" or (std is None and ikey != "__CUSTOM__"):
            proc_name = inp.name.lower().replace(" ", "_")
            match = [f"_{proc_name}"]
            colorspace = "sRGB" if getattr(inp, "type", "") == "NodeSocketColor" else "Non-Color"
            socket_map[inp.name] = "__CUSTOM__"
        else:
            proc_name = ikey.split(":", 1)[-1] if ":" in ikey else ikey
            match = std.get("match", [f"_{proc_name}"])
            colorspace = std.get("colorspace", "Non-Color")
            socket_map[inp.name] = ikey

        processors[proc_name] = TextureProcessor(
            name=proc_name, type="direct", match=match,
            colorspace=colorspace,
            outputs={"default": ProcessorOutput(socket=inp.name)},
        )

    preset_id = f"custom_{ng.name.lower().replace(' ', '_').replace('.', '_')}"
    blend_path = bpy.data.filepath or "//untitled.blend"
    return {
        "preset_id": preset_id,
        "preset_label": preset_label or f"Custom: {ng.name}",
        "preset_category": preset_category,
        "version": "2.0",
        "node_group_name": ng.name,
        "socket_mappings": socket_map,
        "texture_processors": {
            n: {"type": p.type, "match": p.match, "colorspace": p.colorspace,
                "socket": list(p.outputs.values())[0].socket if p.outputs else None,
                "required": p.required, "fallback_value": p.fallback_value, "priority": p.priority,
                "outputs": {on: {"socket": o.socket, "via": o.via, "label": o.label} for on, o in p.outputs.items()}}
            for n, p in processors.items()
        },
        "node_group_source": f"{blend_path}\\NodeTree\\{ng.name}",
    }


# ============================================================
# 工具函数: JSON读写、节点组导入导出、自定义着色器注册
# ============================================================

def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_default_shaders() -> list[dict]:
    """列出默认节点组（仅 JSON 索引中的，不扫描整个 blend）"""
    cfg = _load_json(_SHADERS_JSON)
    defaults = cfg.get("defaults", {})
    return [{"name": name, **info} for name, info in defaults.items()]


def list_custom_shaders() -> list[dict]:
    """列出用户自定义节点组"""
    cfg = _load_json(_CUSTOM_JSON)
    customs = cfg.get("custom", {})
    return [{"name": name, **info} for name, info in customs.items()]


def import_node_group_from_blend(blend_path: str, group_name: str, append: bool = True) -> bool:
    """从 .blend 导入节点组到当前场景。已存在则跳过。"""
    if not os.path.exists(blend_path):
        return False
    existing = bpy.data.node_groups.get(group_name)
    if existing:
        select_node_group(group_name)
        return True
    try:
        with bpy.data.libraries.load(blend_path, link=not append) as (df, dt):
            if group_name in df.node_groups:
                dt.node_groups = [group_name]
        ng = bpy.data.node_groups.get(group_name)
        if ng:
            ng.use_fake_user = True
            select_node_group(group_name)
            return True
    except Exception:
        pass
    return False


def save_node_group_to_blend(blend_path: str, group_names: list[str]) -> bool:
    """将指定节点组合并保存到 .blend（读取已有 + 新增，一并写入）"""
    if not group_names:
        return False
    datablocks = set()
    for name in group_names:
        ng = bpy.data.node_groups.get(name)
        if ng:
            datablocks.add(ng)
            for node in ng.nodes:
                if node.type == "GROUP" and node.node_tree:
                    datablocks.add(node.node_tree)
    if not datablocks:
        return False
    try:
        current_names = {ng.name for ng in datablocks}
        if os.path.exists(blend_path):
            with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):
                missing = [n for n in data_from.node_groups if n not in current_names and bpy.data.node_groups.get(n) is None]
                if missing:
                    data_to.node_groups = missing
            for name in missing:
                ng = bpy.data.node_groups.get(name)
                if ng:
                    datablocks.add(ng)
        bpy.data.libraries.write(filepath=blend_path, datablocks=datablocks, fake_user=True)
        return True
    except Exception:
        return False


def register_custom_shader(group_name: str, label: str, category: str = "Hybrid"):
    ng = bpy.data.node_groups.get(group_name)
    if not ng:
        return False
    save_node_group_to_blend(_CUSTOM_BLEND, [group_name])
    cfg = _load_json(_CUSTOM_JSON)
    if "custom" not in cfg:
        cfg["custom"] = {}
    interfaces = {}
    for inp in iter_node_group_inputs(ng):
        if not _socket_is_internal(inp):
            interfaces[inp.name] = None
    cfg["custom"][group_name] = {
        "label": label or group_name,
        "description": f"用户自定义着色器: {group_name}",
        "category": category,
        "interfaces": interfaces,
    }
    _save_json(_CUSTOM_JSON, cfg)
    return True


def remove_custom_shader_entry(group_name: str):
    cfg = _load_json(_CUSTOM_JSON)
    cfg.get("custom", {}).pop(group_name, None)
    _save_json(_CUSTOM_JSON, cfg)


def _apply_node_group_to_active(context, ng, name_hint: str = ""):
    """将节点组应用到选中物体的当前材质。无材质则新建。"""
    obj = context.active_object
    if not obj or obj.type not in ("MESH", "CURVE", "SURFACE"):
        return
    mat = obj.active_material
    if not mat:
        mat = bpy.data.materials.new(name=name_hint or ng.name)
        obj.data.materials.clear()
        obj.data.materials.append(mat)
    mat.use_nodes = True
    mn = mat.node_tree.nodes; ml = mat.node_tree.links
    mn.clear()
    mo = mn.new("ShaderNodeOutputMaterial"); mo.location = (400, 0)
    gn = mn.new("ShaderNodeGroup"); gn.location = (0, 0); gn.node_tree = ng
    if "Shader" in gn.outputs:
        ml.new(gn.outputs["Shader"], mo.inputs["Surface"])
    select_node_group(ng.name)
