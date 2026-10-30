# FModel_Tools/Assets/Anims/ui_custom_grid.py
# 翻页网格 — 卡片布局与分页渲染（操作符由 anim_manager.py 注册）

import math
import os


def in_library_scope(manager, item):
    if getattr(manager, "library_scope", "ALL") == 'ALL':
        return True
    index = getattr(manager, "active_library_index", -1)
    if not 0 <= index < len(manager.tree_items):
        return False
    root = manager.tree_items[index]
    if root.item_type != 'LIBRARY':
        by_identity = {(node.skeleton, node.node_id): node for node in manager.tree_items}
        root = next((node for node in ancestor_items(root, by_identity)
                     if node.item_type == 'LIBRARY'), None)
        if root is None:
            return False
    path = getattr(item, "blend_path", "")
    root_path = getattr(root, "blend_path", "")
    if not path or not root_path:
        return False
    path, root_path = (os.path.normcase(os.path.abspath(p)) for p in (path, root_path))
    if root_path.lower().endswith('.blend'):
        return path == root_path
    try:
        return os.path.commonpath((path, root_path)) == root_path
    except ValueError:
        return False


def ancestor_items(item, by_identity):
    seen = set()
    parent_id = item.parent_id
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = by_identity.get((item.skeleton, parent_id)) or by_identity.get(("", parent_id))
        if parent is None:
            break
        yield parent
        parent_id = parent.parent_id


def selected_folder(manager):
    index = manager.active_folder_index
    if 0 <= index < len(manager.tree_items):
        item = manager.tree_items[index]
        if (item.item_type == 'FOLDER'
                and manager.active_skeleton in ("", "ALL", "NONE", item.skeleton)):
            return item.node_id, item.skeleton
    return "", ""


def visible_animation_indices(manager, folder_id="", skeleton=""):
    search = manager.search_filter.lower().strip()
    by_identity = {(item.skeleton, item.node_id): item for item in manager.tree_items}
    visible = []
    for index, item in enumerate(manager.tree_items):
        if (item.item_type != 'ANIM' or not in_library_scope(manager, item)
                or manager.active_skeleton not in ("", "ALL", "NONE", item.skeleton)):
            continue
        if search:
            if (search not in item.name.lower() and not any(
                    parent.item_type == 'FOLDER' and search in parent.name.lower()
                    for parent in ancestor_items(item, by_identity))):
                continue
        elif folder_id and (item.parent_id != folder_id or item.skeleton != skeleton):
            continue
        visible.append(index)
    return visible


def change_grid_page(manager, step):
    indices = visible_animation_indices(manager, *selected_folder(manager))
    pages = max(1, math.ceil(len(indices) / max(1, manager.grid_columns * manager.grid_row_count)))
    page = min(max(1, manager.grid_page), pages)
    manager.grid_page = min(max(1, page + step), pages)


class FModelCustomGrid:
    @staticmethod
    def draw(layout, context, manager, valid_indices, draw_callback, empty_callback):
        cols = manager.grid_columns
        rows = manager.grid_row_count
        items_per_page = cols * rows

        total_items = len(valid_indices)
        total_pages = max(1, math.ceil(total_items / max(1, items_per_page)))

        safe_page = manager.grid_page
        if safe_page > total_pages: safe_page = total_pages
        if safe_page < 1: safe_page = 1

        main_box = layout.box()

        # 顶部导航
        head_row = main_box.row(align=True)
        head_row.label(text=f" {total_items} 个", icon='ASSET_MANAGER')

        if total_pages > 1:
            nav = head_row.row(align=True)
            previous = nav.row(align=True)
            previous.enabled = safe_page > 1
            previous.operator("fmodel.grid_page_prev", text="", icon='TRIA_LEFT')
            nav.label(text=f" {safe_page}/{total_pages} ")
            following = nav.row(align=True)
            following.enabled = safe_page < total_pages
            following.operator("fmodel.grid_page_next", text="", icon='TRIA_RIGHT')

        main_box.separator()

        if total_items == 0:
            main_box.label(text="当前目录没有匹配的动画", icon='INFO')
            return

        grid_col = main_box.column(align=True)
        start_idx = (safe_page - 1) * items_per_page
        end_idx = start_idx + items_per_page

        current_row = None
        for i in range(start_idx, end_idx):
            if (i - start_idx) % cols == 0:
                current_row = grid_col.row(align=True)

            cell = current_row.column(align=True)
            if i < total_items:
                item_idx = valid_indices[i]
                item = manager.tree_items[item_idx]
                is_active = (manager.active_item_index == item_idx)
                draw_callback(context, cell, item, item_idx, is_active, manager)
            else:
                empty_callback(context, cell, manager)


def register():
    pass  # 操作符由 anim_manager.py 统一注册


def unregister():
    pass
