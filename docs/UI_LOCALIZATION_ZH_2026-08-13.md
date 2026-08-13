# FModel Tools V2 界面本地化记录（2026-08-13）

## 目标

在不改变 FModel Tools V2 的 UEFormat 导入逻辑、Folder-only 动画管线、Vendor headless 注册方式和已持久化字段的前提下，将 Blender 5.2 中实际可见的 FModel Tools UI 统一为**中文优先、英文可回退**。

## 实施范围

- 主侧栏：FModel 工具箱、UEFormat 原生管线、动画交接、材质工具、最近操作、动画管线；
- UEFormat 操作器：模型、动画、姿势、Strict identity / Name-based compatibility；
- 动画向导属性：源文件夹、源骨架、兼容策略、导入计划进度和相关属性名称；
- 动态状态：Folder-only 扫描消息、导入计划进度、运行时状态在 UI 渲染边界翻译；
- Indexed Project 的旧词条只为旧 .blend 兼容保留，不属于当前活动 UI；
- `core/i18n.py`：为中文显示词条登记 `en_US` 英文回退。

## 保持不变的边界

- operator id、Property/RNA 字段名、JSON/Checkpoint 键、协议值 `strict` / `name_retarget` 不变；
- UEFormat Vendor 仍以 headless 方式注册，不重新暴露官方独立面板；
- 资产路径、骨架名、文件名、未知异常详情等动态数据不做猜测式翻译；
- 未启用 JSON 材质回退，不改变模型/动画/姿势导入行为；
- 未修改 FModel 官方界面或导出器。

## 主要文件

- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\ui.py`
- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\core\i18n.py`
- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\core\skeleton_compatibility.py`
- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\project_assets.py`（旧兼容代码，不属于活动 UI）
- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\Pipeline\steps\state.py`
- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\Pipeline\steps\step3_scan.py`
- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\Pipeline\steps\single_model_flow.py`
- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\animation\operator_anim_ueformat.py`
- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\model\operator_model_ueformat.py`
- `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\test_ui_localization_contract.py`

## 验证与同步

- V2 全量回归：`647 passed, 19 skipped, 89 subtests passed`；
- 同步目标：`C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\FModel_Tools`；
- 当前同步 dry-run：`151` 个文件；总大小：`3,581,323` bytes；
- 当前同步载荷摘要：
  `6f38d6754df25db8c2f8d4094e497973b79cb01f8997792cdc4bf9cb85cb5cf6`；
- 本轮只执行 dry-run，未覆盖用户安装目录，也没有生成新的安装备份。

## 手动检查入口

重启 Blender 5.2 后，在 `3D View > Sidebar > FModel` 检查：

1. `FModel 工具箱`、`动画管线`、`资产库` 等面板标题；
2. UEFormat Runtime、Folder-only 扫描消息、导入计划进度和操作报告；
3. 模型/动画/姿势导入操作器的选项名称；
4. 选择 `按名称兼容` 时的中文警告；
5. 最近操作报告的复制/打开来源按钮；
6. 英文 Blender 环境下是否按 `core/i18n.py` 回退为英文。

## 剩余英文的合理范围

资产文件名、骨架路径、动态错误详情、UEFormat/Blender/FModel 等技术名称，以及 Vendor 未暴露的官方技术面板不属于 UI 术语汉化范围。
