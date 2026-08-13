# FModel 2 Blender Tools V2 改进路线（历史记录，已收敛）

更新时间：2026-08-07（当前合同修订于 2026-08-13）
项目：`F:\Blender Scripts Github\FModel-2-Blender-Tools-V2`

> 本路线图的 UESCENE、Project Index 和旧格式条目属于历史规划，不是当前待办。当前优先级是 Blender 5.2 真实 UEFormat 资产闭环、Folder-only 性能和安装载荷洁净度。

## 当前判断

V2 的核心功能当前覆盖 UEFormat 模型、动画、姿态、材质、Folder-only 动画资产库和 Vendor runtime。世界场景由官方 USD 负责，Indexed Project 与旧格式不属于当前活动范围。当前最大的提升空间不在继续增加格式，而在：

1. 让用户知道运行时是否真正准备好；
2. 让导入、材质和场景失败变得可诊断、可恢复；
3. 将旧迁移代码和当前路径彻底分层；
4. 建立真实 Blender 5.2 回归和可重复 release 流程。

## 已落地的第一项改进

当前工具箱已经显示 UEFormat Runtime 状态：

- `pending`：等待 Vendor 加载；
- `ready`：内置 Vendor 已注册；
- `observed`：检测到已有注册状态；
- `external`：使用用户启用的外部 UEFormat；
- `failed`：Vendor 加载/注册失败。

相关文件：

- `auto_handlers.py`：`get_vendor_status()`；
- `ui.py`：`_draw_ueformat_runtime_status()`；
- `test_auto_handlers_lifecycle.py`；
- `test_ueformat_ui_contract.py`。

这解决了“按钮已经可见，但 UEFormat runtime 还没有准备好”的可见性问题。

## P0.2 已落地：Vendor 可复现 tree manifest

本轮已增加：

- `core/vendor_tree_manifest.py`：无 Blender 依赖的 manifest 生成、加载和严格校验；
- `scripts/generate_vendor_tree_manifest.py`：生成和 `--check` 验证入口；
- `vendor/vendor_tree_manifest.json`：当前 Vendor 的 33 个 release 文件、逐文件 SHA-256 和 tree SHA-256；
- `test_vendor_tree_manifest.py`：当前清单、确定性、篡改、未列文件和路径穿越合同测试；
- `vendor/vendor_manifest.toml`：增加 `source_snapshot` 和 `tree_manifest` 字段。

当前 Vendor tree digest：

```text
861b9bbec5be61339b81ea432b53cebd6ee3cfe313daf364e796cc166729d763
```

这一步只保护 Vendor 来源和 release 内容，不改变 Blender 运行时导入行为。下一步仍需把 tree manifest 接入正式 release builder，并在真实 Blender 回归前固定 Vendor snapshot。

## P0.3 基础层已落地：ImportSession 结果报告契约

已增加：

- `core/import_session.py`：统一 `running / success / partial / failed / cancelled` 状态；
- `persist_import_session()`：将最近一次结果写入 Blender Scene custom properties；
- `.ueanim` / `.uepose` 操作器：批量导入记录成功/失败文件，并在 partial 时显示 warning；
- `test_import_session.py`：状态、失败、取消、序列化和 custom-property 持久化测试；
- `core/import_transaction.py`：只追踪导入期间新增的 Blender datablock；
- `Fmodel/importers/model/operator_model_ueformat.py`：模型导入开始 transaction，成功 commit，异常时 rollback；
- `test_import_transaction.py`：预存数据保护、共享 datablock 保留、幂等 rollback 和 commit 边界测试。

模型 operator 已接入基础 rollback；下一步仍需在真实 Blender 中验证对象、材质、图片和 collection 的实际 users/link 行为，并把 model 结果写入 ImportSession。

## P0：发行前必须完成

### P0.1 真实 Blender 5.2 回归

Host-Python 合同不能替代真实 Blender 测试。需要固定测试样本覆盖：

- `.uemodel` + native FModel materials；
- 外部纹理和 embedded texture；
- `.ueanim` / `.uepose` strict skeleton；
- `.uescene` required model 成功、部分成功和失败；
- Vendor headless 注册/注销/重载；
- Blender 5.2 Python 3.13 wheel；
- Material、Image、Action Slot 和 NLA 实际行为。

### P0.2 统一导入会话和报告

当前 model、animation、scene 的报告入口不完全一致。建议新增一个轻量 `ImportSession`：

```text
ImportSession
├── operation_id
├── asset_kind
├── source_path
├── runtime_version
├── phase
├── warnings
├── errors
├── created_objects
├── created_materials
├── created_images
└── final_status
```

目标：

- 同时写入 Blender Info/Print 区域；
- 写入结构化 report；
- 保存成功/部分成功/失败状态；
- 失败时列出缺失模型、材质、贴图和骨架原因；
- 后续支持“复制报告”和“打开报告目录”。

## P1：用户体验和诊断

### P1.1 材质/贴图诊断面板

在当前 Material Tools 中增加一次导入诊断摘要：

- 找到的材质数量；
- 创建的 Blender 材质数量；
- 重用的材质数量；
- 缺失外部纹理；
- URI 越界；
- SHA-256 不匹配；
- embedded/external 数量；
- 无法解析的材质参数。

### P1.2 UEScene 资源图

对 `.uescene` 导入报告提供资源树：

```text
Scene
├── Required Models
├── Optional Models
├── Materials
├── Textures
└── Failed References
```

这样可以直接解释 `required model(s) failed to import`，而不是只显示总失败数。

### P1.3 Project Index 状态强化

当前默认索引已经是 UEFormat-only。下一步可增加：

- 扫描进度；
- 取消扫描；
- stale 文件数量；
- 失败文件列表；
- 最近扫描时间；
- 索引 scope 显示；
- 旧索引需要重扫时的迁移提示。

## P1：可靠性和恢复

### P1.4 Model 导入事务

当前有导入前备份和失败报告，但建议把模型导入包装为事务：

1. 记录导入前 objects/materials/images；
2. 调用 Vendor；
3. 验证 armature、mesh、material slots；
4. 验证 skeleton provenance；
5. 失败时只删除本次创建的 datablocks；
6. 成功后写入 ImportSession。

### P1.5 Vendor Ready 变成真正的依赖门闩

目前 Vendor 已在下一轮 Timer 加载并且 UI 会显示状态。下一步应让导入 operator 在状态为 `pending` 时主动触发一次 ensure，或者显示明确的“等待 runtime”而不是直接报告插件缺失。

## P2：架构维护

### P2.1 拆分大文件

当前维护风险较高的文件包括：

- `Fmodel/Pipeline/stages.py`：约 2600 行；
- `ui.py`：约 1300 行；
- `Assets/Anims/anim_manager.py`：约 1600 行；
- `core/library_files.py`：约 1000 行。

建议按职责拆分：

```text
ui/
├── toolbox.py
├── pipeline_panel.py
├── asset_library_panel.py
├── material_panel.py
└── game_profile_panel.py

Fmodel/Pipeline/stages/
├── acquire.py
├── convert.py
├── import_assets.py
├── build_library.py
└── finalize.py
```

拆分应在行为冻结、真实 Blender 回归之后进行。

### P2.2 清理遗留命名

逐步替换：

- `gltf_import_path` → `fmodel_import_source_path`；
- `auto_link_mat` → `legacy_material_fallback`；
- `FBX_MATCH` / `UNITY_ANIM` 的 active 入口；
- 旧 PSK/PSA 注释和迁移 source。

不要在拆分和真实测试之前一次性删除所有兼容字段。

### P2.3 package name 私有化

`io_scene_ueformat` 仍然是公开模块名。Vendor Loader 已经具备冲突检测，但最终可以迁移到：

```text
fmodel_ueformat_runtime
```

该步骤必须在真实 Blender 验证后进行，因为它会影响 bridge、operator、submodule import 和 reload 行为。

## P2：性能

- Project Index 扫描增加目录 fingerprint 和变更摘要；
- 大批量动画导入增加 UI 级 progress/cancel；
- Material Image Repository 继续复用 image cache；
- 资产库 UI 避免在 draw 阶段反复读取 JSON/缩略图；
- UEScene 模型导入支持可控并发/分批，而不是一次性创建全部对象。

## 推荐实施顺序

```text
A. Vendor 状态 UI                         已完成
B. Project Index UEFormat-only scope      已完成
C. Material/Scene diagnostic report       下一批
D. ImportSession contract + model rollback      基础完成；真实 Blender 验证下一批
E. Blender 5.2 fixture/regression         发行门槛
F. legacy source 清理                     手动验证后
G. package name 私有化                    最后
H. 大文件拆分                             行为冻结后
```

## 当前不建议做的事情

- 不要立即重写整个 UI；
- 不要在没有 Blender 5.2 实测前删除所有 legacy source；
- 不要先做 package name 私有化再排查材质/场景问题；
- 不要把 Project Index、资产库 manifest 和 Vendor manifest 混成一个 schema；
- 不要用“测试全通过”代替真实 Blender 导入验证。







