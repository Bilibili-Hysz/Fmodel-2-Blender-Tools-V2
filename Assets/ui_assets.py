# FModel_Tools/Assets/ui_assets.py
# 资产 UI — UIList 组件（资产库树、动画树、文件夹、网格）

import bpy
import os
from .Anims.anim_manager import get_custom_icon
from .Anims import ui_custom_grid


def _draw_indent(row, depth, factor=1.0):
    """用固定宽度的单级间隔绘制树节点缩进。"""
    for _ in range(depth):
        row.separator(factor=factor)


# ============================================================
# 资产库树状 UIList（供资产库设置面板使用）
# ============================================================

class ASSETS_UL_LibraryTree(bpy.types.UIList):
    """资产库树状列表 — 显示 LIBRARY、ANIM_LIB、VOLUME、MODEL"""
    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flt_flags = []
        flt_neworder = []

        # 收集被折叠的 LIBRARY 节点
        collapsed = set()
        for item in items:
            if item.item_type == 'LIBRARY' and not item.is_expanded:
                collapsed.add(item.node_id)

        # 收集被折叠的 ANIM_LIB 节点
        collapsed_anims = set()
        for item in items:
            if item.item_type == 'ANIM_LIB' and not item.is_expanded and item.volume_count > 1:
                collapsed_anims.add(item.node_id)

        for item in items:
            flag = 0
            if item.item_type == 'LIBRARY':
                flag = self.bitflag_filter_item
            elif item.item_type == 'ANIM_LIB':
                if item.parent_id not in collapsed:
                    flag = self.bitflag_filter_item
            elif item.item_type == 'MODEL':
                if item.parent_id not in collapsed:
                    flag = self.bitflag_filter_item
            elif item.depth == 2 and item.item_type not in ('FOLDER',):
                # VOLUME 条目
                if item.parent_id not in collapsed and item.parent_id not in collapsed_anims:
                    flag = self.bitflag_filter_item
            flt_flags.append(flag)
        return flt_flags, flt_neworder

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        _draw_indent(row, item.depth)

        if item.item_type == 'LIBRARY':
            expanded = item.is_expanded
            toggle_icon = 'DISCLOSURE_TRI_DOWN' if expanded else 'DISCLOSURE_TRI_RIGHT'
            op = row.operator("fmodel.toggle_folder", text="", icon=toggle_icon, emboss=False)
            op.node_id = item.node_id
            op.target_skeleton = item.skeleton

            if item.lib_type == 'DIRECTORY':
                row.label(text=item.name, icon='FILE_FOLDER')
            else:
                row.label(text=item.name, icon='FILE_BLEND')

            info = row.row(align=True)
            info.alignment = 'RIGHT'
            if item.anim_count > 0:
                info.label(text=f"{item.anim_count}动画")

        elif item.item_type == 'ANIM_LIB':
            is_multi = item.volume_count > 1
            if is_multi:
                expanded = item.is_expanded
                toggle_icon = 'DISCLOSURE_TRI_DOWN' if expanded else 'DISCLOSURE_TRI_RIGHT'
                op = row.operator("fmodel.toggle_folder", text="", icon=toggle_icon, emboss=False)
                op.node_id = item.node_id
                op.target_skeleton = item.skeleton
            else:
                row.separator(factor=0.6)

            label = item.name
            if item.anim_count > 0:
                label += f" ({item.anim_count}动作)"
            row.label(text=label, icon='ANIM_DATA')

            if is_multi:
                info = row.row(align=True)
                info.alignment = 'RIGHT'
                info.label(text=f"{item.volume_count}卷")

        elif item.depth == 2 and item.item_type not in ('FOLDER', 'ANIM'):
            row.separator(factor=0.6)
            row.label(text=item.name, icon='FILE_BLEND')

        elif item.item_type == 'MODEL':
            row.separator(factor=0.6)
            row.label(text=item.name, icon='OUTLINER_OB_MESH')


# ============================================================
# 动画树状 UIList（仅显示 FOLDER + ANIM）
# ============================================================

class ASSETS_UL_AnimTree(bpy.types.UIList):
    """动画树状列表 — 仅显示 FOLDER 和 ANIM，过滤 LIBRARY/MODEL/ANIM_LIB"""
    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        manager = context.scene.fmodel_anim_manager
        active_skel = manager.active_skeleton
        search_str = manager.search_filter.lower().strip()

        flt_flags = []
        flt_neworder = []

        collapsed = set()
        for item in items:
            if ((item.item_type == 'ANIM_LIB' and item.volume_count > 1
                 and not item.is_expanded)
                    or (item.item_type == 'FOLDER' and not item.is_expanded)):
                collapsed.add((item.skeleton, item.node_id))

        # Folder IDs can be shared by multiple skeletons, so resolve ancestors
        # using the current skeleton first and fall back to library-level nodes.
        by_identity = {(item.skeleton, item.node_id): item for item in items}

        def has_collapsed_ancestor(item):
            parent_id = item.parent_id
            while parent_id:
                parent = by_identity.get((item.skeleton, parent_id))
                if parent is None:
                    parent = by_identity.get(("", parent_id))
                if parent is None:
                    return False
                if parent.item_type == 'LIBRARY' and not parent.is_expanded:
                    return True
                if parent.item_type == 'ANIM_LIB' and parent.volume_count > 1 and not parent.is_expanded:
                    return True
                if parent.item_type == 'FOLDER' and not parent.is_expanded:
                    return True
                parent_id = parent.parent_id
            return False

        for item in items:
            flag = 0

            # 跳过 LIBRARY/MODEL/ANIM_LIB（这些是库设置用的层级节点）
            if item.item_type in ('LIBRARY', 'MODEL', 'ANIM_LIB'):
                flag = 0

            elif item.item_type == 'FOLDER':
                # 骨架过滤
                if active_skel not in ("ALL", "NONE") and item.skeleton != active_skel:
                    flag = 0
                elif has_collapsed_ancestor(item):
                    flag = 0
                elif (item.skeleton, item.parent_id) in collapsed:
                    flag = 0
                else:
                    flag = self.bitflag_filter_item

            elif item.item_type == 'ANIM':
                # depth 2+ 的 ANIM 是实际动画条目
                if item.depth < 2:
                    flag = 0
                elif has_collapsed_ancestor(item):
                    flag = 0
                elif active_skel not in ("ALL", "NONE") and item.skeleton != active_skel:
                    flag = 0
                else:
                    if search_str:
                        if search_str in item.name.lower():
                            flag = self.bitflag_filter_item
                    else:
                        flag = self.bitflag_filter_item

            flt_flags.append(flag)
        return flt_flags, flt_neworder

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        _draw_indent(row, item.depth, factor=1.2)

        if item.item_type == 'FOLDER':
            icon_toggle = 'DISCLOSURE_TRI_DOWN' if item.is_expanded else 'DISCLOSURE_TRI_RIGHT'
            op = row.operator("fmodel.toggle_folder", text="", icon=icon_toggle, emboss=False)
            op.node_id = item.node_id
            op.target_skeleton = item.skeleton
            row.label(text=item.name, icon='FILE_FOLDER')
        else:
            row.separator(factor=0.6)
            icon_id = get_custom_icon(item.thumb_path, item.blend_path)
            if icon_id != 0:
                row.label(text=item.name, icon_value=icon_id)
            else:
                row.label(text=item.name, icon='ANIM_DATA')

            row_ops = row.row(align=True)
            op_preview = row_ops.operator("fmodel.preview_anim", text="", icon='RESTRICT_VIEW_OFF', emboss=False)
            op_preview.target_node_id = item.node_id
            op_preview.target_skeleton = item.skeleton
            op_preview.source_key = item.source_key
            op_apply = row_ops.operator("fmodel.apply_anim", text="", icon='IMPORT', emboss=False)
            op_apply.target_node_id = item.node_id
            op_apply.target_skeleton = item.skeleton
            op_apply.source_key = item.source_key
            op_apply.apply_method_override = 'REPLACE'
            op_nla = row_ops.operator("fmodel.apply_anim", text="", icon='NLA', emboss=False)
            op_nla.target_node_id = item.node_id
            op_nla.target_skeleton = item.skeleton
            op_nla.source_key = item.source_key
            op_nla.apply_method_override = 'APPEND_NLA'


class ASSETS_UL_FolderTree(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        manager = context.scene.fmodel_anim_manager
        active_skel = manager.active_skeleton
        search_str = manager.search_filter.lower().strip()
        
        # 搜索模式下：收集匹配的动画/文件夹及其所有祖先。
        # Use skeleton-scoped IDs because different skeletons can share paths.
        visible_ids = set()
        if search_str:
            for item in items:
                if item.item_type not in ('FOLDER', 'ANIM'):
                    continue
                if active_skel not in ("ALL", "NONE") and item.skeleton != active_skel:
                    continue
                if search_str in item.name.lower():
                    if item.item_type == 'FOLDER':
                        visible_ids.add((item.skeleton, item.node_id))
                    # 向上追溯祖先
                    curr = item.parent_id
                    while curr:
                        visible_ids.add((item.skeleton, curr))
                        curr = curr.rsplit('/', 1)[0] if '/' in curr else ""

        flt_flags = []
        flt_neworder = []
        by_identity = {(item.skeleton, item.node_id): item for item in items}

        def has_collapsed_folder_ancestor(item):
            parent_id = item.parent_id
            while parent_id:
                parent = by_identity.get((item.skeleton, parent_id))
                if parent is None:
                    parent = by_identity.get(("", parent_id))
                if parent is None:
                    return False
                if parent.item_type == 'FOLDER' and not parent.is_expanded:
                    return True
                parent_id = parent.parent_id
            return False

        for item in items:
            flag = 0
            if item.item_type != 'FOLDER':
                flt_flags.append(flag)
                continue

            # 骨架过滤
            if active_skel not in ("ALL", "NONE") and item.skeleton != active_skel:
                flt_flags.append(flag)
                continue

            if search_str:
                # 搜索模式：只显示匹配项及其祖先
                if (item.skeleton, item.node_id) in visible_ids:
                    flag = self.bitflag_filter_item
            else:
                # 正常浏览模式：折叠树
                if not has_collapsed_folder_ancestor(item):
                    flag = self.bitflag_filter_item

            flt_flags.append(flag)
        return flt_flags, flt_neworder

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        _draw_indent(row, item.depth, factor=1.2)
            
        icon_toggle = 'TRIA_DOWN' if item.is_expanded else 'TRIA_RIGHT'
        op = row.operator("fmodel.toggle_folder", text="", icon=icon_toggle, emboss=False)
        op.node_id = item.node_id
        op.target_skeleton = item.skeleton
        row.label(text=item.name, icon='FILE_FOLDER')


class ASSETS_UL_AnimGrid(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        manager = context.scene.fmodel_anim_manager
        search_str = manager.search_filter.lower().strip()
        active_folder_id = ""
        active_skel = ""
        
        if manager.tree_items and manager.active_folder_index < len(manager.tree_items):
            sel_item = manager.tree_items[manager.active_folder_index]
            if sel_item.item_type == 'FOLDER':
                active_folder_id = sel_item.node_id
                active_skel = sel_item.skeleton

        flt_flags = []
        flt_neworder = []
        for item in items:
            flag = 0
            if item.item_type == 'ANIM': 
                if item.parent_id == active_folder_id and item.skeleton == active_skel:
                    if not search_str or search_str in item.name.lower():
                        flag = self.bitflag_filter_item
            flt_flags.append(flag)
        return flt_flags, flt_neworder

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        icon_id = get_custom_icon(item.thumb_path, item.blend_path)
        if icon_id != 0: row.label(text=item.name, icon_value=icon_id)
        else: row.label(text=item.name, icon='ANIM_DATA')
        
        row_ops = row.row(align=True)
        op_preview = row_ops.operator("fmodel.preview_anim", text="", icon='RESTRICT_VIEW_OFF', emboss=False)
        op_preview.target_node_id = item.node_id
        op_preview.target_skeleton = item.skeleton
        op_preview.source_key = item.source_key
        op_apply = row_ops.operator("fmodel.apply_anim", text="", icon='IMPORT', emboss=False)
        op_apply.target_node_id = item.node_id
        op_apply.target_skeleton = item.skeleton
        op_apply.source_key = item.source_key
        op_apply.apply_method_override = 'REPLACE'
        op_nla = row_ops.operator("fmodel.apply_anim", text="", icon='NLA', emboss=False)
        op_nla.target_node_id = item.node_id
        op_nla.target_skeleton = item.skeleton
        op_nla.source_key = item.source_key
        op_nla.apply_method_override = 'APPEND_NLA'


# ============================================================
# 注册（面板已集成到 FModel 主面板 ui.py）
# ============================================================

def register():
    bpy.utils.register_class(ASSETS_UL_LibraryTree)
    bpy.utils.register_class(ASSETS_UL_AnimTree)
    bpy.utils.register_class(ASSETS_UL_FolderTree)
    bpy.utils.register_class(ASSETS_UL_AnimGrid)

def unregister():
    bpy.utils.unregister_class(ASSETS_UL_AnimGrid)
    bpy.utils.unregister_class(ASSETS_UL_FolderTree)
    bpy.utils.unregister_class(ASSETS_UL_AnimTree)
    bpy.utils.unregister_class(ASSETS_UL_LibraryTree)
