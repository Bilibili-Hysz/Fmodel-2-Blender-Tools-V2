# FModel 2 Blender Tools

专为 **UE/FModel** 设计的 Blender 5.0 全流程资产管线工具集。

---

## 功能模块概览

### 1. 动画管线（Animation Pipeline）

4 步引导式导入流程，一键完成动画导入 → 资产库打包 → 缩略图生成。

| 步骤 | 功能 |
|------|------|
| 流程 | 选择 FModel 流程 |
| 文件夹 | 选择动画文件夹、输出目录、骨架过滤（支持滴管拾取骨架名） |
| 扫描 | 扫描 `.ueanim` 动画字典，自动发现骨架和动作 |
| 导入/打包 | 设置导入参数 → 一键导入动画 → 构建资产库 → 生成 WebP 缩略图 |

缩略图支持：
- OpenGL 视口快照（快速）和 Render 渲染（高质量）两种模式
- 仅渲染骨架子级（排除场景其他物体）
- 确定性 Hash 命名——同动作重烘焙自动覆盖旧缩略图
- 完成后可单独"重新生成缩略图"，无需重新导入

当前 Animation Pipeline 只使用 Folder 模式：步骤 2 选择动画文件夹、输出目录和库名称，步骤 3 直接扫描该文件夹中的 .ueanim / .uepose。Indexed Project、Project Root、SQLite 动画索引和后台索引维护已从活跃 UI/执行路径移除；旧状态字段仅作为打开历史 .blend 的兼容数据保留。

---

### 2. 材质（Materials）

标准 FModel 材质与纹理由 UEFormat 导入器（vendor）在导入时直接构建：
Principled 节点图 + BaseColor / Normal / ORM 纹理链，无需任何 JSON 或
手动节点组操作。外部纹理保持 `Content/Textures/...` 相对布局即可被自动解析。

旧版 JSON 材质解析回退与自定义着色器节点组管理器已随 V1 遗留清理一并退役；
`Shaders/Tools Nodes/` 下的独立编辑器工具脚本（DecodeORM 等）保留供手动使用。

---

### 3. 资产库（Asset Library）

**动画资产**  
- 树/列表/卡片三种视图模式
- 骨架过滤 + 名称搜索
- 缩略图预览 + 详情面板（帧数/时长/骨架/源文件）
- 一键预览/应用动画到骨架
- NLA 部署模式（替换/追加/覆盖）

**模型资产**  
- 保存角色模型到资产库（带预览图）
- 卡片网格浏览，预览图直显
- 一键导入模型到场景

---

### 5. 拓展工具（Extension Tools）

| 工具 | 功能 |
|------|------|
| 网格工具 | 清理空白形态键 / 按材质分离 |
| 骨架/姿态 | 整理骨骼层级 / 批量重命名 / UE标准命名 / 骨骼映射 / 姿态镜像 |
| 动画工具 | 清理动作关键帧 / 批量清理 |

---

### 6. GameProfile 管理器

在着色器编辑器中管理 GameProfile 预设（JSON），支持：
- 创建/删除/编辑预设
- UE 参数名 → 标准通道的映射配置
- 自动保存至 `Shaders/GameProfiles/`

---

## FModel 导出建议

### 原生 UEFormat（推荐）
当前 canonical UEFormat 导出资源已经包含模型/动画所需的骨架身份信息。对于
`.uemodel`、`.ueanim` 和 `.uepose`：

```
✅ 原生二进制资源
✅ 保持 UE 原始目录层级，便于扫描和定位外部贴图
⭕ 同名 JSON：不是骨架识别和动画清单生成的必要条件
⭕ 字典 manifest JSON：不是 Folder 扫描的必要条件
```

V2 会优先读取资源内的 skeleton path、GUID、bone-layout hash 和 provenance fingerprint。UEFormat-only 默认路径不再向用户暴露 JSON fallback；动画管线直接扫描用户选择的 Folder，不创建或依赖 Project Index。原生资源可以在没有同名 JSON 的情况下完成扫描和导入；FBX/PSK/PSKX 不属于当前活动管线。

### 材质与贴图
canonical UEFormat importer 可在具备当前 FModel 材质/纹理能力的情况下直接负责
原生模型的材质导入。`Universal_tools/material` 的 JSON 解析器仍然保留，用于：

- 旧项目迁移期间的材质修复；
- 报告和诊断用途；
- 不再作为 UEFormat-only 默认导入路径。

外部贴图仍应保留（`.png` / `.tga` / `.dds` 等），并尽量保持
`Content/Textures/...` 的相对目录结构。

### 仍然保留的 JSON

JSON 没有被全局删除，而是划分为明确的边界：

- V2 自己的 checkpoint、report、library manifest、shader preset 等内部状态；
- 迁移窗口内旧项目的只读兼容输入。


### 原生资源与 JSON 的边界

严格身份匹配使用 `skeleton_path` / identity fingerprint，不使用 UI 显示名称作为唯一
依据。原生资源通常显示为 `SK_Hero_Skeleton`，旧 JSON 可能显示为 `Hero`；两者属于
兼容别名，不能据此判断资源是否为同一套骨架。

### 推荐目录策略
- 导出模式：优先选择 canonical UEFormat 原生资源；需要 legacy 材质解析时再附带 JSON
- 贴图格式：PNG（保留 Alpha 通道用于 WebP 缩略图）
- 目录结构：**保留 UE 原始层级**（不放平）

---

## 用户可见报告

工具箱现在保留默认展开的 **Last Operation**：显示最近一次 UEFormat 导入的
状态、源文件、创建的 Datablock 和首个可操作失败，并提供 **Copy Report** 与
**Open Source**。复制的报告同时包含实际 runtime 路径和已验证 Vendor tree digest。

Blender 安装方式应二选一：使用 V2 内置 runtime，或单独安装 standalone
`io_scene_ueformat`；不要在同一 Blender 会话中同时启用。

## 内置 UEFormat Vendor

Release 内置 FModel 适配版 `io_scene_ueformat` **0.10.0**，支持
`.uemodel`、`.ueanim` 和 `.uepose`，并包含 V2 所需的 FModel
材质/贴图和 strict skeleton 能力。

V2 会以 **headless 模式**注册该依赖：运算符、文件处理器、设置属性和导入逻辑
仍然有效，但不会创建 canonical UEFormat 面板，也不会追加 **File > Import**
菜单项，从而避免与 V2 面板重复。单独安装 UEFormat 插件时仍使用普通注册流程，
canonical 面板保持不变。

Vendor 同时内置 Windows x64 的 CPython 3.11/3.12/3.13 `zstandard` wheel，
启动时按 Blender 实际 Python 版本选择。

`vendor/vendor_tree_manifest.json` 锁定完整 Vendor release tree，打包前可运行：

```powershell
python scripts/generate_vendor_tree_manifest.py --check
```

## 安装

将 `FModel_Tools/` 文件夹放入 Blender addons 目录：

```
%APPDATA%\Blender Foundation\Blender\5.0\scripts\addons\
```

在 Blender 偏好设置中启用 **FModel Tools** 插件。

---

## 需求

- Blender 5.0+
- 内置 Vendor 依赖：`io_scene_ueformat` 0.10.0（headless 注册）
- UEFormat-only 版本已停用 PSK、PSA、glTF 和 FBX 支持

---

## 国际化

- 默认中文界面
- 在 Blender 偏好设置中切换语言为 English 后，所有面板、按钮、描述自动切换英文（309 条翻译）
