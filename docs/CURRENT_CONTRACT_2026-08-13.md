# FModel Tools V2 当前功能合同（2026-08-13）

> 本文是 V2 当前运行时、同步载荷和文档维护的权威摘要。
> 若历史设计文档与本文冲突，以本文、`README.md`、`README_ZH.md` 和
> `docs/FOLDER_ONLY_PIPELINE_2026-08-13.md` 为准。

## 1. 当前活动格式

V2 当前只负责：

- `.uemodel`：模型、骨架、Native FModel 材质和贴图；
- `.ueanim`：动画；
- `.uepose`：姿势。

世界场景使用 FModel 官方 USD 导出/导入路线。V2 不再注册、维护或测试 UESCENE importer。

以下格式不属于当前活动能力：

- PSK / PSKX；
- PSA；
- glTF / GLB；
- FBX 输入或导出；
- UESCENE。

旧格式源码只为迁移窗口保留，`scripts/sync_to_blender_addon.py` 不会将其复制到安装目录。

## 2. 当前动画管线

- Animation Pipeline 只有 Folder 模式；
- Step 3 直接扫描用户选择目录中的 `.ueanim/.uepose`；
- Indexed Project、SQLite 索引、后台索引 worker 和 Project Settings 索引入口不再激活；
- 旧 `.blend` 中的 Indexed Project 字段只用于安全打开和迁移，不参与当前执行；
- 严格骨架身份是默认策略；按名称兼容必须由用户显式选择，且不等同于完整 Unreal Retarget Manager。

## 3. Vendor 合同

- Vendor：`io_scene_ueformat 0.10.0`（canonical V10 基线；旧 FModel 定制版 1.2.0 的版本体系已随 V10 迁移切换）；
- 注册方式：`register_headless()`；
- V2 不重复暴露 Vendor canonical 面板和 File > Import 菜单；
- standalone UEFormat 与内置 Vendor 不应在同一 Blender 会话同时启用；
- Vendor tree manifest 必须在发布前通过 `scripts/generate_vendor_tree_manifest.py --check`；
- Windows x64 运行时只选择与 Blender Python ABI 匹配的 zstandard wheel。

## 4. JSON 边界

当前 Native UEFormat 导入不要求用户手动提供同名 JSON。二进制 UEFormat identity、material links 和 texture references 是权威来源。

JSON 仍可作为以下内部或历史状态存在：

- 旧资产兼容回退；
- Pipeline checkpoint、resume、library manifest 和导入报告；
- Shader/GameProfile 和 Blender 资产库元数据。

这些内部 JSON 不构成 FModel Native UEFormat 导入前提。

## 5. 安装载荷合同

同步脚本保留活动运行时模块，并排除：

- Indexed Project / Project Assets；
- PSK / PSA / glTF / FBX 旧格式 operator；
- PSK 专用 mesh scheme；
- 测试、文档、Git 元数据、缓存和备份。

2026-08-13 本地 dry-run 基线：

```text
151 files
3,581,323 bytes
6f38d6754df25db8c2f8d4094e497973b79cb01f8997792cdc4bf9cb85cb5cf6
```

该基线是源码同步载荷摘要，不代表已经覆盖用户 Blender 安装目录。

测试脚本统一位于 `tests/`，不会进入 Blender 安装载荷；从仓库根目录使用 `python -B -m pytest tests -q -p no:cacheprovider` 运行。

## 6. 文档治理

- 当前合同：本文、README、Folder-only 决策文档；
- 历史设计：带有旧日期、旧 UESCENE、旧 Indexed Project 或旧格式内容的迁移/路线/性能报告；
- 历史文档可以保留用于追溯，但必须在开头标记 `历史设计/已收敛`，不能作为当前实现依据。

## 7. 发布门槛

自动化测试、Vendor manifest 和 Python 语法检查通过后，仍必须在 Blender 5.2 使用真实 `.uemodel/.ueanim/.uepose` 资产完成 GUI、材质/贴图、骨架兼容和 Folder-only 打包手测，才能从 Candidate 晋级 Stable。
