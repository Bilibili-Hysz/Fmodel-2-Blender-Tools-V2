# FModel 2 Blender Tools V2 插件改进审查（历史记录，已收敛）

审查日期：2026-08-07（当前合同修订于 2026-08-13）  
审查对象：`F:\Blender Scripts Github\FModel-2-Blender-Tools-V2`  

> 本文为历史审查。当前活动边界已收敛为 `.uemodel/.ueanim/.uepose`、Folder-only 和官方 USD 世界场景；UESCENE、Indexed Project、PSK/PSA/glTF/FBX 不再属于活动发布合同。
审查目的：在继续扩展功能前，确认当前架构是否已经清晰、发行风险在哪里，以及下一轮应该优先处理什么。

## 1. 结论

当前 V2 **可以继续作为 UEFormat-only 的开发基线**，核心方向是合理的：

- 主插件只注册 UEFormat 模型、动画和姿态入口；世界场景由官方 USD 路线负责；
- `vendor/io_scene_ueformat` 以 headless 方式注册，避免重复创建 canonical UEFormat 面板；
- `core/ueformat_bridge.py` 用能力探测代替单纯版本号判断；
- 原生 `.uemodel/.ueanim/.uepose` 已成为默认路径；
- 骨架身份、Project Index、材质 ownership 和 Vendor 生命周期已经有明确的独立模块；
- 当前 V2 Python 合同测试为 `553 passed, 98 subtests passed`；关键活动源码的 `E9/F821/F811/F822/F823/F841` 静态检查通过，源码可编译。

但是，当前还不应称为 Stable release。主要原因不是核心功能缺失，而是**导入事务、错误报告、Vendor 可复现性和真实 Blender 门禁仍不完整**。

## 2. 当前架构优点

### 2.1 格式边界已经明确

活动导入入口在 `Fmodel/importers/__init__.py` 中只包含 model、animation、scene；PSK、PSA、glTF、FBX 已不再进入主注册路径。`test_ueformat_only_phase1_contract.py` 也对这一边界进行了保护。

### 2.2 Vendor 运行时设计方向正确

`core/vendor_loader.py` 已具备：

- manifest 校验；
- wheel Python ABI / 平台匹配；
- SHA-256 校验；
- 安全解压；
- 跨进程缓存锁；
- headless register / unregister；
- 外部同名 addon 冲突保护。

这部分已经超过“把一个 addon 目录复制到插件里”的临时方案，适合继续作为正式架构。

### 2.3 Canonical importer 与 V2 orchestration 分层正确

V2 负责 UI、Project Index、Pipeline 和资产库；Vendor 负责 UEFormat 二进制协议和 Blender 数据构建；`core/ueformat_bridge.py` 负责两者之间的 capability contract。这个边界应该保持，不建议把材质解析、场景解析重新复制回 V2。

## 3. 必须优先处理的改进

### P0.1 真实 Blender 5.2 回归门禁

现有测试主要是合同测试、fake `bpy` 测试和静态行为测试，不能替代真实 Blender 运行。发行前至少要有一套可重复的 Blender 5.2 测试脚本，覆盖：

1. 加载 V2 addon；
2. headless Vendor 注册；
3. `.uemodel` 模型、骨架、Morph、材质和 embedded/external texture；
4. `.ueanim` / `.uepose` strict skeleton；
5. `.uescene` required model 成功、部分成功和失败；
6. 重复启用/禁用 addon；
7. Vendor unregister 后重新加载；
8. 保存 `.blend` 后重新打开，检查 provenance 和材质链接是否仍在。

没有这道门禁，`553 passed` 只能证明 Python 合同正确，不能证明 Blender 数据 API 和注册生命周期正确。

### P0.2 Vendor 源码可复现性

`vendor/vendor_manifest.toml` 当前记录 `source_commit = "f0deb75"`，但实际 Vendor 目录包含 canonical UEFormat 工作树中的未提交文件，例如：

- `importer/identity.py`；
- `importer/materials.py`；
- `importer/protocol.py`；
- `importer/resources.py`；
- `importer/standard_materials.py`；
- `importer/topology.py`。

这些文件并不都来自 `f0deb75` 提交本身，而是来自 canonical 仓库的 dirty working tree。当前 manifest 因而不足以唯一复现 Vendor 内容。

建议在 release 构建时生成：

- Vendor 源码 tree hash；
- Vendor 文件清单和每个文件 SHA-256；
- canonical 源仓库 commit；
- V2 headless patch commit / patch hash；
- wheel 清单和 wheel SHA-256。

加载器可以继续只验证 wheel，但 release builder 必须验证 Vendor tree manifest，避免“manifest 版本相同但源码已经漂移”。

### P0.3 导入事务和失败回滚

当前模型导入流程位于 `Fmodel/importers/model/operator_model_ueformat.py`：

- 先执行 `backup_before`；
- 调用 Vendor importer；
- 再写 skeleton provenance、旧兼容属性、材质 fallback 标记并准备动画管线。

本轮已增加 `core/import_transaction.py`，并将模型导入包在 transaction 中：成功时 commit，Vendor 或后处理异常时只尝试删除本次创建且没有外部 owner 的 datablock。下一步不是继续扩大纯 Python 模拟，而是在真实 Blender 中验证 objects、materials、images、actions 和 collections 的 users/link 行为。当前 `ImportSession` 仍需继续接入 model 结果报告。

```text
ImportSession
├── operation_id
├── asset_kind
├── source_paths
├── vendor_version
├── phase
├── created_objects
├── created_materials
├── created_images
├── warnings
├── errors
└── final_status
```

失败时只删除本次创建且没有外部 owner 的 datablock；已有场景内容不能被误删。

### P0.4 统一结果报告

当前 model、animation、pose 和 scene 的报告方式不一致：

- model 主要依赖 `self.report` 和日志；
- animation / pose 对多文件导入只累计 `success_count`；
- scene 有更完整的 required-model 状态，但尚未与 model/animation 共用同一报告协议。

应统一为 `success / partial / failed / cancelled` 四态，并至少记录：

- 成功和失败文件列表；
- 失败阶段；
- 缺失模型、材质、贴图、骨架原因；
- Vendor 版本和来源；
- 生成对象/材质/图片数量；
- 是否使用 legacy JSON fallback。

报告需要同时进入 Blender Info/Print 区域、V2 可复制文本和结构化 JSON。这样才能诊断类似“required model(s) failed to import”或“材质导入成功但贴图为空”的问题。

## 4. P1 改进

### P1.1 Vendor 外部 addon 状态必须能力化

`auto_handlers.py:get_vendor_status()` 当前能区分 `ready / observed / external / failed / pending`，但 `external` 状态没有可靠版本和 capability 详情。用户可能看到“External UEFormat addon selected”，但实际使用的是不支持 FMODEL_MATERIALS、FMODEL_TEXTURES 或 strict skeleton 的旧 addon。

建议：

- external 状态同时显示版本和 source path；
- 通过 `core/ueformat_bridge.py` 进行 capability inspection；
- 缺能力时显示明确的缺失项；
- model/animation/scene 按 capability 分别禁用或给出可执行修复提示。

### P1.2 消除 UEFormat-only 路径中的遗留命名

`auto_handlers.py` 仍通过 `gltf_import_path` 识别“新导入对象”，而 `operator_model_ueformat.py` 也继续写入该属性。它不会马上破坏功能，但会造成：

- UEFormat 对象被旧 glTF 自动扫描逻辑误识别；
- 后续维护者误以为 glTF 仍是主路径；
- 资产库 metadata 无法表达真实来源。

建议引入统一常量，例如 `ueformat_import_path` / `fmodel_import_source_path`，迁移期同时读取旧 key，但只写新 key；Stable 后删除旧兼容读取。

### P1.3 多文件动画/姿态导入要报告 partial

`Fmodel/importers/animation/operator_anim_ueformat.py` 在多文件导入时，只要有一个成功就返回 `FINISHED`，失败文件只通过临时 `self.report` 输出。这会让用户误以为整个批次成功。

建议返回并持久化：

```text
Imported: 8
Failed: 2
Status: Partial
```

并提供“复制最近报告”入口，与 UEScene 保持一致。

### P1.4 材质选项要区分 canonical 与 legacy

模型操作器里的 `auto_link_mat` 当前实际只控制旧 JSON / 启发式材质 fallback；canonical FModel 材质由 UEFormat importer 自动处理。这个命名和默认 UI 容易让用户误解。

建议改为：

- `allow_legacy_json_material_fallback`；
- UEFormat native materials 固定为主路径；
- legacy fallback 默认关闭或明确标注“迁移兼容”；
- 在报告中记录是否触发 fallback。

### P1.5 输出对象元数据统一

当前仍有 `FModel_Skeleton`、`FModel_Skeleton_Path`、`FModel_Skeleton_Identity_Fingerprint`、`gltf_import_path` 等多套历史 key。建议建立一份 metadata schema 和版本号，所有对象、Armature、Action、Material 使用统一命名空间，例如：

```text
fmodel.meta.version
fmodel.source.kind
fmodel.source.path
fmodel.skeleton.identity
fmodel.import.operation_id
```

旧 key 只读兼容，不再继续增加散落字段。

## 5. P2 可维护性

当前单文件复杂度仍然偏高：

| 文件 | 行数 | 风险 |
| --- | ---: | --- |
| `Fmodel/Pipeline/stages.py` | 2608 | 导入、状态、恢复和 UI 事务耦合 |
| `Assets/Anims/anim_manager.py` | 1641 | 资产索引、预览、UI 和缓存耦合 |
| `ui.py` | 1380 | 所有面板、操作器和 GameProfile 混在一个文件 |
| `core/library_files.py` | 1096 | 文件格式、迁移和修复逻辑集中 |
| `core/project_index.py` | 795 | 扫描、持久化、迁移和 UI 状态耦合 |

建议顺序：先完成 P0 真实回归，再拆分为：

```text
ui/
├── toolbox.py
├── project_panel.py
├── asset_library_panel.py
├── material_panel.py
└── game_profile_panel.py

core/import/
├── session.py
├── transaction.py
├── report.py
└── metadata.py

Fmodel/Pipeline/stages/
├── acquire.py
├── import_assets.py
├── build_library.py
└── finalize.py
```

不要在真实 Blender 门禁之前进行大规模拆分，否则出现问题时无法判断是行为回归还是结构迁移错误。

## 6. 遗留代码处理原则

当前主注册路径已经 UEFormat-only，但以下 legacy 源码仍然存在：

- `Fmodel/importers/animation/operator_anim_psa.py`；
- `Fmodel/importers/model/operator_model_gltf.py`；
- `Fmodel/importers/model/operator_model_psk.py`；
- `Fmodel/importers/export/operator_export_fbx.py`；
- `Fmodel/importers/export/operator_export_gltf.py`；
- `Fmodel/importers/export/__init__.py`。

它们目前是迁移窗口中的回滚材料，不应立即与活动代码混放。安全做法：

1. 先把 legacy 文件移入明确的 `legacy/` 或只读归档目录；
2. 增加“legacy 不可注册”的合同测试；
3. 完成一次 Stable release 和真实用户迁移后再删除；
4. 不要在未确认 `.blend` / checkpoint / 用户脚本兼容前直接物理删除。

## 7. 下一轮建议

### 推荐立即实施

1. 增加 Vendor tree manifest / release verification；
2. 增加统一 `ImportSession` 和结构化报告；
3. 先给 model operator 增加事务回滚；
4. 修正多文件 animation/pose 的 partial 结果；
5. 增加 external Vendor capability 状态；
6. 通过真实 Blender 5.2 harness。

### 暂缓

- 继续增加新格式；
- 把 USD、glTF、PSK 重新并入主路径；
- 大规模 UI 重写；
- 在 dirty worktree 上做无证据的清理性 Git reset；
- 直接升级 Vendor 依赖而不重新生成 hash 和真实 Blender 证据。

## 8. 本轮已完成的第一批改进

在审查文档之后，本轮继续落地了两组低风险基础设施：

1. Vendor tree manifest：`vendor/vendor_tree_manifest.json` 记录 33 个 release 文件、逐文件 SHA-256 和 tree SHA-256；`scripts/generate_vendor_tree_manifest.py --check` 可在打包前严格验证；Vendor loader 支持在 V2 自动加载时要求 tree manifest。
2. ImportSession 基础层：`core/import_session.py` 定义统一结果状态、失败记录、序列化和 Scene custom-property 持久化；`.ueanim` / `.uepose` 批量导入已经记录 partial 结果。
3. Model transaction：`core/import_transaction.py` 和 `Fmodel/importers/model/operator_model_ueformat.py` 已接入基础 commit/rollback。

本轮验证结果：

```text
V2 pytest: 568 passed, 98 subtests passed
Vendor tree: 33 files verified
Critical Ruff checks: passed
compileall: passed
```

模型 datablock rollback 基础层已经完成；真实 Blender users/link 回归和 model ImportSession 接入仍是下一批 P0 工作。

## 9. 当前实施边界

本轮没有执行 Git reset、删除或提交操作。V2 原有 dirty entries 仍保持原样；新增改动集中在 Vendor manifest、ImportSession、动画/姿态 partial 报告、测试和文档。

P0.2 Vendor 可复现清单、P0.3 ImportSession 基础层和模型 transaction 基础层已经完成；下一步应在真实 Blender 5.2 中验证 rollback，再补齐 model/scene 的统一报告。





