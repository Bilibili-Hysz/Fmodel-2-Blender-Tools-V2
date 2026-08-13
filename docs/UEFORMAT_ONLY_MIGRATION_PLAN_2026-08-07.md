# FModel 2 Blender Tools：UEFormat-only 转向方案（历史设计，已收敛）

更新时间：2026-08-07（历史记录，当前合同修订于 2026-08-13）  
目标项目：`F:\Blender Scripts Github\FModel-2-Blender-Tools-V2`

> **当前状态（2026-08-13）**：本文保留迁移过程和历史决策，不能覆盖当前运行时合同。
> 当前有效边界是：`.uemodel/.ueanim/.uepose`、Folder-only 动画管线、官方 USD 世界场景；
> UESCENE producer/importer 已退役，Indexed Project 不再是活动路径，PSK/PSA/glTF/FBX 不再进入安装载荷。
> 当前实现和发布时以 `README.md`、`README_ZH.md`、`docs/FOLDER_ONLY_PIPELINE_2026-08-13.md`
> 以及 `scripts/sync_to_blender_addon.py` 为准。本文后续 Phase、旧测试门槛和删除清单属于历史记录。


## 1. 方向结论

项目从“多格式 UE/FModel 资产工具”转为 **UEFormat 原生资产工具**，产品支持边界收敛为：

- `.uemodel`：模型、骨架、材质和贴图导入；
- `.ueanim`：动画导入；
- `.uepose`：姿势导入；
- Blender 内部 `.blend` 资产库、预览、动画应用和材质后处理。

不再作为产品能力支持：

- PSK / PSKX；
- PSA；
- glTF / GLB；
- FBX 输入、FBX-match 动画路线以及其它非 UEFormat 源格式；
- 依赖用户提供的 FModel 模型 JSON、材质 JSON、动画 JSON 或 sidecar manifest 的导入路径。

本次不是简单隐藏几个按钮，而是把 **格式分支、源扫描、管线状态、Vendor 依赖、资产索引和文档** 统一到一个 UEFormat 合同上。

## 2. JSON 边界定义

“停止显式利用 JSON”按以下边界执行：

### 2.1 删除的 JSON 角色

以下文件不再作为 UEFormat 导入的输入或必要条件：

- FModel 导出的同名模型 JSON；
- FModel 导出的同名动画 JSON；
- MaterialInterface / MaterialInstance JSON；
- 旧版 `_manifest.json` 动画清单；
- PSK / glTF 导入时用来推断骨架的 JSON；
- `Universal_tools/material` 中面向外部 JSON 的材质解析链。

UEFormat 资源内的 skeleton identity、material links 和 texture references 成为模型/动画导入的权威来源；世界场景由官方 USD 路线负责，V2 不再维护 UESCENE。

### 2.2 暂时保留但不暴露为用户输入的 JSON

以下属于 V2 自己的内部状态，不是外部资产源：

- pipeline checkpoint / resume state；
- asset-library manifest；
- 导入报告；
- Project Index 的兼容导出；
- shader/game profile 预设；
- 测试 fixture 和诊断输出。

这些文件应统一放入 `.fmodel/` 或资产库内部目录，UI 不再要求用户手动准备或选择它们。后续如果需要“零 JSON 运行时”，再迁移到 SQLite、Blender custom properties 或 Blender Text 数据块；本轮不先删除内部状态格式，避免破坏断点和已有资产库。

## 3. 当前代码审计结果

### 3.1 需要移除或停止注册的入口

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\model\operator_model_gltf.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\model\operator_model_psk.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\animation\operator_anim_psa.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\export\operator_export_gltf.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\export\operator_export_fbx.py
```

对应注册汇总位于：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\__init__.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\model\__init__.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\animation\__init__.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\export\__init__.py
```

旧迁移阶段曾包含完整的旧格式 Vendor：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\vendor\io_scene_psk_psa\
```

该目录已从当前 V2 工作树删除；这里只保留历史迁移记录。

### 3.2 需要改为 UEFormat-only 的 UI

当前旧格式分支位于：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\ui.py
```

需要删除：

- `active_pipeline = GLTF`；
- `active_pipeline = PSK`；
- glTF 导入/导出按钮；
- PSK 模型和 PSA 动画按钮；
- 与旧格式相关的描述、翻译和设置字段。

保留并扩展 UEFormat 分支：

- `.uemodel`；
- `.ueanim`；
- `.uepose`；
- `.uescene`；
- native material/texture 状态和导入报告。

### 3.3 需要收敛的动画管线

当前管线仍然包含 Unity、FBX 和旧格式兼容分支：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\core\enums.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\core\types.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\Pipeline\manager.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\Pipeline\stages.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\Pipeline\steps\state.py
```

应收敛为：

```text
SourceType.FMODEL_UEANIM
ImportMode.UEANIM_DIRECT
```

其中：

- `DictionaryAcquisitionStage` 只扫描 `.ueanim` / `.uepose`；
- `.uemodel` 由模型入口负责；世界场景由 FModel 官方 USD 导出链负责；
- 不再读取旧 `_manifest.json`；
- 不再走 Unity `.anim`、FBX batch、FBX-match、Unity 转换阶段；
- 不再创建 `conversion_manifest.json` 作为导入前提。

### 3.4 仍存在的外部 JSON fallback

目前 UEFormat 模型入口仍保留以下兼容路径：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\model\operator_model_ueformat.py
```

包括：

- 无 native identity 时读取同名 JSON 推断骨架；
- canonical material capability 不可用时调用旧 JSON 材质解析器。

UEFormat-only 版本应改为：

- native identity 缺失时报告 `legacy/invalid` 并明确失败或降级为无身份导入；
- 不自动寻找同名 JSON；
- canonical FModel material/texture capability 缺失时直接报告 Vendor 不满足要求；
- 删除 `auto_link_mat` 的旧 JSON fallback 语义，改为 native importer 状态展示。

## 4. 目标架构

```text
FModel / UEFormat exports
        │
        ├── .uemodel ───────┐
        ├── .ueanim ────────┤
        ├── .uepose ────────┤── UEFormat Contract Layer
        └── .uescene ───────┘       │
                                    ├── identity reader
                                    ├── capability bridge
                                    ├── native material/texture importer
                                    ├── model/animation/scene operators
                                    └── Project Index v4
                                             │
                                  Blender scene / asset library / .blend
```

建议新增或集中整理的边界模块：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\core\ueformat_contract.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\core\ueformat_identity_reader.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\core\ueformat_bridge.py
```

`ueformat_contract.py` 负责统一：

- 四类文件扩展名和资源类型；
- native asset reference；
- skeleton provenance；
- 资源合法性和能力错误；
- 不允许调用旧格式或 sidecar JSON 的约束。

这样以后新增 UEFormat 版本时，只需更新 Contract/Vendor，而不再在多个旧格式分支中补丁式适配。

## 5. 分阶段实施计划

### Phase 0：冻结和基线

目标：先保证当前版本可回退。

- 保留当前工作树和测试基线；
- 标记现有版本为多格式兼容基线；
- 不删除旧文件；
- 建立 UEFormat-only 分支或 Git tag；
- 固定当前 Vendor 1.1.5 和 `zstandard` wheels。

### Phase 1：先切 UI 和注册，不删除代码

目标：用户界面立即变成 UEFormat-only，同时保证可回滚。

- `FMODEL_CoreProperties.active_pipeline` 只保留 `UEFORMAT`；
- 移除旧格式按钮和菜单；
- `Fmodel/importers/*/__init__.py` 停止导入和注册旧格式模块；
- `auto_handlers.py` 只注册 `io_scene_ueformat`；
- 暂时保留旧源文件，但标注为待移除，不再成为运行时依赖。

### Phase 2：收敛 Pipeline 和 Project Index

目标：所有扫描、导入和断点只认识 UEFormat。

- 删除 Unity / FBX `SourceType` 和 `ImportMode`；
- 默认值统一为 `FMODEL_UEANIM` / `UEANIM_DIRECT`；
- `DictionaryAcquisitionStage` 删除 manifest/JSON fallback；
- 历史 Project Index 收录规则废止；当前 Folder-only 只扫描 `.ueanim/.uepose`；
- 建议将 `SCHEMA_VERSION` 从 3 提升到 4，强制 Rebuild，清除旧格式记录；
- 旧 `.blend` 中的 `GLTF`、`PSK`、`UNITY`、`FBX` 状态迁移为 `UEFORMAT` / `FMODEL`；
- 旧 checkpoint 对旧 source/import mode 标记为不可恢复，而不是静默复用。

### Phase 3：移除旧格式实现和 Vendor

目标：从发行包彻底去除旧格式能力。

删除或移出主分支：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\model\operator_model_gltf.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\model\operator_model_psk.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\animation\operator_anim_psa.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\export\operator_export_gltf.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Fmodel\importers\export\operator_export_fbx.py
```

`vendor/io_scene_psk_psa` 已在当前清理阶段完成删除。

同时移除：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Universal_tools\mesh\scheme_psk_auto.py
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\Universal_tools\mesh\scheme_psk_mesh_fix.py
```

`scheme_morph_merge.py` 如果仍用于 UEFormat 形态键，则保留；否则单独评估。

### Phase 4：删除外部 JSON 资产依赖

目标：用户只需要准备 FModel 新管线导出的 UEFormat 资源和贴图目录。

- `operator_model_ueformat.py` 删除同名 JSON 骨架 fallback；
- 删除 `Assets/Utils/utils_asset.py` 中面向 FModel sidecar JSON 的读取函数；
- 删除 `Universal_tools/material/auto_resolve.py`、`manifest_builder.py`、`resolver/` 等旧材质输入链；
- 默认 UI 不再显示“解析 JSON 材质”或“选择 JSON”；
- Vendor native material builder 成为唯一自动材质入口；
- `Assets/Models/operators.py` 的模型骨骼/元数据 sidecar 改存 Blender custom properties 或 Project Index；
- `Assets/Anims/library_scanner.py` 的 library sidecar 先保留为内部实现，后续再迁移 SQLite。

### Phase 5：内部状态和资产库整理

目标：用户看不到 JSON，但现有资产库不会被突然破坏。

- 所有内部 JSON 路径集中到 `.fmodel/`；
- 增加 `format_version` 和 `schema_version`；
- 旧资产库只读打开并提示迁移；
- 新资产库优先使用 SQLite + Blender custom properties；
- Pipeline checkpoint/report 保留机器可读 JSON 导出能力，但不再作为源资产输入。

### Phase 6：发布和清理

- 更新 `README.md`、`README_EN.md`、`README_ZH.md`；
- 更新 Vendor README 和适配状态；
- 删除旧格式测试，增加“不注册旧格式”负向测试；
- 生成 UEFormat-only release；
- 检查 release 中不存在：
  - `io_scene_psk_psa`；
  - `operator_model_psk.py`；
  - `operator_anim_psa.py`；
  - `operator_model_gltf.py`；
  - `operator_export_gltf.py`；
  - `operator_export_fbx.py`；
  - FModel source JSON 读取路径。

## 6. 兼容和版本策略

这是一次有意的破坏性收敛，建议：

- V2 当前版本：UEFormat-only 工程候选版；
- UEFormat-only 首个版本：升级 addon 主版本，例如 `9.0.0`；
- 不自动删除用户旧 PSK/PSA/glTF/FBX 文件；
- 旧 `.blend` 只做状态迁移，不承诺重新导入旧格式；
- 旧 JSON 资产库只读兼容一个迁移周期，之后移入 legacy 工具包；
- Vendor 只保留 `io_scene_ueformat`，继续使用 headless 注册。

## 7. 测试门槛

### 正向测试

- 无任何 sidecar JSON 的 `.uemodel` 导入；
- 无任何 sidecar JSON 的 `.ueanim` / `.uepose` 扫描和导入；
- 官方 USD 世界场景导出/导入不属于 V2 UEFormat importer 合同；
- FModel material links、embedded/external textures；
- strict skeleton identity 和多骨架任务隔离；
- headless Vendor 注册、卸载和重载；
- Folder-only 只扫描 `.ueanim/.uepose`，Indexed Project 不再作为活动路径。

### 负向测试

- `bpy.ops.fmodel.import_model_psk` 不存在；
- `bpy.ops.fmodel.import_anim_psa` 不存在；
- `bpy.ops.fmodel.import_model_gltf` 不存在；
- glTF/FBX 导出 operator 不存在；
- `io_scene_psk_psa` 不被自动加载；
- 同名 JSON 缺失不会触发 fallback，也不会伪造 strict identity；
- 旧 `UNITY` / `FBX_MATCH` checkpoint 被拒绝或要求迁移。

### 发布前命令

```powershell
python -B -m pytest -q
python -B -m compileall -q core Fmodel Universal_tools vendor auto_handlers.py
python -B -m pytest -q test_ueformat_vendor_contract.py test_ueformat_vendor_headless.py
git diff --check
```

## 8. 当前执行状态

### 已完成：Phase 1（UI 和注册收敛）

已完成以下可逆改动：

- UI 只暴露 UEFormat；
- importer registration 只注册 UEFormat model/animation/scene；
- auto handler 只加载 `io_scene_ueformat`；
- PSK-specific schemes 不再注册；
- 旧导入器源文件暂时保留，便于代码级回滚；无用的 `vendor/io_scene_psk_psa` 已清理；
- 新增 `test_ueformat_only_phase1_contract.py`。

### 尚未执行：Phase 2 及之后

以下内容仍然未执行：

- 删除 PSK/PSA/glTF/FBX 源文件；
- 删除 JSON resolver；
- 移除 Unity / FBX source 和 import mode；
- 修改 Project Index schema；
- 生成 UEFormat-only release。

下一步应先进行 Blender 5.2 手动验证；验证通过后进入 **Phase 2（Pipeline 和
Project Index 收敛）**，再进行不可逆目录清理。
