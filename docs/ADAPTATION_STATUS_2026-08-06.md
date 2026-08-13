# FModel-2-Blender-Tools V2 适配状态（历史记录，已收敛）

更新时间：2026-08-07（当前合同修订于 2026-08-13）

> 本文记录早期 Vendor/Project Index 适配过程。当前 Vendor 为 1.2.0，V2 活动格式为 `.uemodel/.ueanim/.uepose`；UESCENE、Indexed Project 和旧格式不再是活动路径。当前实现以 `docs/CURRENT_CONTRACT_2026-08-13.md` 为准。

## 适配目标

V2 只适配当前 canonical UEFormat Blender addon，不修改原始
`FModel-2-Blender-Tools`，也不复制或依赖真实游戏素材。当前 canonical addon 版本为
`io_scene_ueformat 1.2.0`，支持 `.uemodel`、`.ueanim`、`.uepose`、
FModel material/texture provenance 和 strict FModel skeleton identity。Vendor 当前与
本地 FModel UEFormat fork 的 Blender package 快照同步；V2 只在根包和 op 注册层增加
headless 适配，不改 canonical importer 的资源协议。

## UEFormat-only Phase 1

已完成第一阶段的可逆收敛，运行时不再向用户暴露旧格式入口：

- `ui.py` 的 `active_pipeline` 只保留 `UEFORMAT`；
- Toolbox 当前只显示 `.uemodel/.ueanim/.uepose`；世界场景使用官方 USD 路线；
- `Fmodel/importers` 只导入和注册 UEFormat model/animation/scene；
- 旧 glTF/PSK/PSA/FBX 模块暂时保留在源树，但不再注册；
- `auto_handlers.py` 只自动加载 `io_scene_ueformat`，不再加载 `io_scene_psk_psa`；
- root scheme 注册移除 PSK 专用修复方案，保留通用 skeleton/morph 工具；
- 旧 `.blend` 中残留的格式测试值会由 UEFormat-only Toolbox 按 native 入口绘制，而不会重新显示旧按钮。

本阶段没有物理删除旧文件、没有删除 JSON resolver，也没有修改 Project Index schema，便于
Blender 5.2 手动验证失败时快速回滚。

## 本轮已完成

### Skeleton provenance

`core/skeleton_provenance.py` 是 Blender-independent 的身份边界，负责：

- 解析 `fmodel_skeleton_identity` JSON 属性；
- 校验 version、Unreal logical path、GUID 和 `bone_layout_hash`；
- 生成稳定 SHA-256 identity fingerprint；
- 将身份明确分类为 `strict`、`legacy` 或 `invalid`；
- 兼容 canonical importer 的 animation/pose source path 字段。

没有身份属性的旧模型不会被伪装成 strict 资产，而是标记为 `legacy`。存在但格式错误
的身份标记为 `invalid`，由上层决定是否拒绝或走兼容路径。

### Pipeline / checkpoint

以下记录现在携带目标骨架 provenance：

- `PipelineContext.skeleton_provenance`；
- `ImportedAssetCollection.skeleton_provenance`；
- `ImportedAction.skeleton_provenance`（有值时才写入旧动作记录）；
- `PipelineStageBase._task_identity()['target_skeleton_provenance']`；
- `MultiImportTask.target_skeleton_provenance`。

provenance fingerprint 参与单骨架和多骨架任务的 batch identity，因此骨架 identity 变化
会使旧导入结果/断点失效，而不会复用到另一套骨架上。为兼容旧 vendor 或测试替身，
collection 的 provenance 注入采用 capability check，不强迫 legacy collection constructor
接受新参数。

### UEFormat identity reader

新增 `core/ueformat_identity_reader.py`，这是一个受限的纯 Python reader，只处理：

- UEFormat envelope magic、identifier、version 和 compression envelope；
- `GZIP`，以及运行时可用时的 `compression.zstd` / `zstandard`；
- 顶层 section bounds、identity chunk 唯一性和安全尺寸上限；
- `FMODEL_SKELETON_IDENTITY`；
- `FMODEL_TARGET_SKELETON`。

它不会解析 mesh、材质、纹理、骨骼拓扑或动画曲线。没有 identity extension 的合法文件返回
`identity=None`，由 provenance 层分类为 `legacy`。

`core/project_index.py` 新增只读方法 `probe_ueformat_provenance()`。该方法可以检查真实
`.uemodel/.ueanim/.uepose` 的二进制 identity，但不会创建、重建或修改 SQLite 数据库。

### Native animation scan / JSON boundary

`DictionaryAcquisitionStage` 的原生扫描现在按以下顺序工作：

```text
.ueanim/.uepose binary identity
    ↓ strict identity 成功
资源内 skeleton_path → display alias + provenance metadata
    ↓ 没有 identity / legacy 文件
同名 JSON（可选兼容回退）
    ↓ 仍然没有结果
相对目录名 / Generic（仅动画目录发现）
```

原生资源不会因为同名 JSON 缺失、冲突或损坏而失去 canonical skeleton identity。
`native_ueformat`、`skeleton_path`、`skeleton_display_name`、format identifier、
identity fingerprint 和 `json_path` 都会写入 V2 自己的 `DictionaryManifest` 元数据。
这里的 `skeleton_display_name` 仅用于 UI/目录分组；严格兼容性应使用
`fmodel_skeleton_identity` 和 fingerprint。

Project Index 同样先读取二进制 identity，再尝试旧 JSON；对于 strict 原生资源，JSON
不会覆盖 binary skeleton path，也不会因为 malformed sidecar 阻断扫描。strict 资源发布后，索引
的 freshness hash 只依赖二进制资源本身；同名 JSON 被修改或删除不会让原生条目失效。只要目录
中存在 strict `.ueanim/.uepose`，管线还会优先跳过旧的 `_manifest.json`，直接以原生资源建立
可导入的清单；只有 manifest-only 的 legacy 目录才继续使用旧 manifest 解析。

## Project Index 决策

在完成 binary identity reader 和 synthetic contract 后，Project Index 已安全升级到
`SCHEMA_VERSION = 3`。升级采用现有的 **rebuild-only** 语义：

- 新建或显式 Rebuild 的数据库使用 schema v3；
- 旧 v2 数据库只会报告 `rebuild_required`，不会自动 ALTER、覆盖或删除；
- `scan()` 在 schema 不兼容时失败并保留旧数据库；
- 用户必须通过现有 Project Settings **Rebuild** 流程生成新的派生索引。

`assets` 表现在记录：

```text
provenance_state
skeleton_path
skeleton_guid
bone_layout_hash
provenance_fingerprint
provenance_reason
```

严格身份来自真实 UEFormat binary extension；没有 identity 的合法文件记录为 `legacy`，
格式错误或不支持的文件记录为 `invalid`。FBX/PSK 等不携带 canonical FModel identity 的
资源记录为 legacy，并保留明确 reason。原生 binary identity 优先于同名 JSON；JSON 只作为
旧资源 fallback，不再是新 `.uemodel/.ueanim/.uepose` 的硬性依赖。

当前 schema v3 仍然不把 Blender 对象、Action、checkpoint 或发布库状态写进 SQLite。
它只保存可由源文件重新扫描得到的派生 provenance。

canonical UEFormat importer 自身负责原生 mesh/material/texture 处理；V2 的
`Universal_tools/material` JSON resolver 仍保留给 legacy 资源和手动材质修复。这里的
“JSON 去依赖”仅针对 native identity、Project Index 和 FModel 原始动画扫描，不代表删除
V2 自己的 checkpoint/report/library/preset JSON，也不代表删除 legacy 材质输入。

### Vendor 与 headless 注册

Vendor 已更新为 `io_scene_ueformat 1.1.5`，包括 FModel material links、external/embedded
textures、strict skeleton 和 sparse LOD material-slot 修复。V2 启动时优先调用
`io_scene_ueformat.register_headless()`：运算符、file handlers、`UFSettings` 和导入实现
仍会注册，但 canonical UEFormat 的三个面板以及 **File > Import** 菜单不会创建。普通
独立安装调用 `register()` 时仍保留原始面板和菜单行为。

Vendor 还携带 Windows x64 CPython 3.11/3.12/3.13 的 `zstandard` wheels。
`auto_handlers._wheel_is_compatible()` 只把与当前 Blender Python 匹配的 wheel 放入
`sys.path`，避免在错误解释器上加载二进制扩展。

下一阶段重点是 Blender 5.2 GUI、真实 `.uemodel/.ueanim/.uepose` 和真实材质/贴图导入
的手动验证；手动验证通过后再进入 Phase 2，移除 Unity/FBX source 分支并将 Project Index
收敛到 UEFormat 四种资源。

## 当前边界

- 纯 Python 合同测试和 fake Blender 测试已经覆盖；
- 尚未声称 Blender 5.2 GUI、canonical addon 真实注册、真实 `.uemodel/.ueanim/.uepose`
  导入已经通过；
- 真实素材缺失时继续使用 synthetic fixture；
- 原始 V1 项目和 canonical FModel 源码保持只读；
- 不自动执行数据库 rebuild、删除、git reset 或清理用户文件。

## 验证命令

在 V2 根目录执行：

```powershell
pytest -q
python -B -m compileall -q core Fmodel Universal_tools vendor/io_scene_ueformat auto_handlers.py
git diff --check
```

本轮新增合同测试：

```text
test_skeleton_provenance.py
test_ueformat_identity_reader.py
test_project_index_ueformat_probe.py
test_pipeline_native_ueformat_scan.py
test_ueformat_model_animation_handoff.py
test_ueformat_vendor_contract.py
test_ueformat_vendor_headless.py
test_ueformat_only_phase1_contract.py
test_multi_import_plan.py::MultiImportPlanTests::test_target_skeleton_provenance_roundtrips_and_invalidates_task_id
```

UEFormat-only Phase 1 后的完整回归结果：`535 passed, 98 subtests passed`；包含新增的
Vendor version/headless/wheel contract。独立的 UEFormat FModel fork 测试结果为
`309 passed, 2 skipped, 251 subtests passed`（存在一个测试主动构造重复 ZIP 成员的
预期 warning）。V2 的 vendor/core/Fmodel 编译检查与 `git diff --check` 均已通过。
