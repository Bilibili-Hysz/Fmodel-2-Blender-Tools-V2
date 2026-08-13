# Animation Pipeline 后台动画索引整合设计

日期：2026-08-08  
状态：Phase A–D 已实施；Phase E 等待 Blender 5.2 手动回归后收尾  
目标项目：`FModel-2-Blender-Tools-V2`

## 1. 结论

取消独立的 `Project Settings` 一级面板，但不删除 `ProjectIndex`。

项目索引应收敛为 Animation Pipeline 的内部基础设施：用户在 Animation Pipeline 中选择项目根目录和骨架，插件在准备导入计划时自动检查、创建、增量更新并查询动画索引。索引维护入口只保留在 Pipeline 的默认折叠 Advanced 区域。

最终产品边界：

- 用户管理的是“动画来源”和“项目目录”；
- 插件管理的是 SQLite Schema、扫描代次、文件指纹和失效恢复；
- Pipeline checkpoint 继续保存本次导入的精确动画路径；
- ProjectIndex 继续作为跨会话查询加速器和来源验证器；
- 不在本轮恢复材质或贴图项目索引；
- 不把 SQLite 状态伪装成实时文件系统状态。

## 2. 当前问题

### 2.1 UI 暴露了内部实现

独立 Project Settings 面板要求用户理解：

- Open；
- Scan；
- Rebuild；
- Schema v3；
- SQLite 是否 ready。

但当前真正被业务消费的只有项目根目录、骨架名称和匹配动画路径。`model_count` 没有导入选择器或查询消费者，贴图和材质也不在索引中。

### 2.2 Animation Pipeline 依赖外部面板先行操作

当前 Step 2 只显示 `fmodel_project.root_path`，不能直接编辑。Step 3 遇到索引未准备时返回：

```text
Project Settings Scan is required before preparing this import plan
```

这使一个连续工作流被拆成两个面板和三种维护操作。

### 2.3 状态存在双重归属

当前关键状态分散在：

- `scene.fmodel_project.root_path`；
- `pipeline_wizard.source_dir`；
- `pipeline_wizard.indexed_project_root`；
- `pipeline_wizard.indexed_skeleton`；
- `pipeline_wizard.indexed_selected_paths`；
- `pipeline_wizard.indexed_selection_mode`。

根目录和选择状态容易不同步，`runner.py` 需要反复比较 Project Settings 与 WizardState。

### 2.4 当前 Scan 不适合无条件自动执行

`ProjectIndex._discover_root()` 当前会为所有候选文件计算内容哈希。即使文件未变化，每次 Scan 仍可能读取全部动画文件。若直接在属性更新或面板绘制时自动扫描，大型项目会导致 UI 卡顿和无意义磁盘读取。

## 3. 目标用户流程

### 3.1 Step 2：选择动画来源

Animation Pipeline Step 2 改为：

```text
Animation Source
  [Indexed Project] [Folder]

Project Root
  D:\Exports\Game\Content\

Index
  Ready · 1,248 animations · 36 match current skeleton

Skeleton
  SK_Girl024_Skeleton
```

用户可以直接在这里修改 Project Root，不再跳转 Project Settings。

### 3.2 Step 3：准备导入计划

点击 `Prepare Import Plan` 后统一执行：

1. 规范化项目根目录；
2. 只读检查索引状态；
3. 索引缺失时自动创建；
4. Schema 不兼容时自动重建派生数据库；
5. 执行增量动画扫描；
6. 查询当前骨架全部匹配动画；
7. 生成 `DictionaryManifest`；
8. 保存精确路径与来源指纹；
9. 进入现有 Pipeline 导入和打包流程。

### 3.3 Advanced：维护与诊断

默认折叠：

```text
Advanced Index Maintenance
  Database: <root>\.fmodel\project-assets.sqlite
  Schema: v3
  Generation: 18
  Last successful scan: 2026-08-08 18:30
  State: Ready

  [Refresh Index]
  [Reset and Rescan]
```

删除含义模糊的 `Open`。原 `Scan` 改名 `Refresh Index`，原 `Rebuild` 改为 `Reset and Rescan`，并明确这是派生数据维护而不是源资产修改。

## 4. 状态所有权

### 4.1 用户持久输入归 WizardState

新增：

```python
project_root: StringProperty(subtype='DIR_PATH')
```

规则：

- `source_mode == 'PROJECT'` 时，`source_dir` 由 `project_root` 派生；
- `source_mode == 'MANUAL'` 时，`source_dir` 由 `manual_source_dir` 派生；
- UI 不直接编辑 `source_dir`；
- `project_root`、`manual_source_dir`、`skeleton_filter` 变化时清除已有 Import Plan。

### 4.2 派生 UI 状态归 WizardState，但不作为数据库真相

新增只用于显示的状态：

```python
project_index_state: EnumProperty(
    IDLE, INSPECTING, SCANNING, READY,
    NO_MATCH, REBUILD_REQUIRED, FAILED, UNAVAILABLE
)
project_index_message: StringProperty()
project_index_animation_count: IntProperty()
project_index_match_count: IntProperty()
project_index_generation: IntProperty()
project_index_last_scan_ns: StringProperty()
```

这些字段允许保存到 `.blend`，但每次加载文件后必须通过只读 inspect 刷新，不能因为 `.blend` 保存了 `READY` 就信任数据库仍然存在。

### 4.3 本次执行的确定输入继续进入 PipelineConfig

保留：

```text
indexed_project_root
indexed_skeleton
indexed_selected_paths
indexed_selection_mode
```

原因：Step 4 和恢复 checkpoint 必须重验“这一次计划使用了哪些文件”，不能只根据当前数据库重新选择。

`AUTO_PROJECT` 继续表示“使用 Prepare Import Plan 时该骨架的全部匹配动画”。`EXPLICIT` 保留为旧 `.blend` 和旧 checkpoint 兼容值，但不在新 UI 暴露。

## 5. 分层架构

### 5.1 `core/project_index.py`

继续保持 Blender-independent，职责仅限：

- SQLite Schema 生命周期；
- 扫描和原子发布；
- 动画 identity 和 provenance 读取；
- 动画查询；
- `DictionaryManifest` 构建与文件重验。

本层不允许导入 `bpy`，不管理 UI 属性，不创建线程。

### 5.2 新增 `core/project_index_service.py`

作为业务协调层，同样不导入 `bpy`。

建议数据结构：

```python
@dataclass(frozen=True)
class ProjectIndexSnapshot:
    root: str
    state: str
    schema_version: int
    generation: int | None
    animation_count: int
    matching_count: int
    last_scan_ns: int | None
    message: str

@dataclass(frozen=True)
class PreparedAnimationSelection:
    root: str
    skeleton: str
    paths: tuple[str, ...]
    manifest: dict
    generation: int
```

建议接口：

```python
class ProjectAnimationIndexService:
    def inspect(self, root, skeleton='') -> ProjectIndexSnapshot:
        ...

    def refresh(self, root, skeleton='', *, force=False, progress=None, cancelled=None):
        ...

    def prepare_selection(self, root, skeleton) -> PreparedAnimationSelection:
        ...

    def reset_and_refresh(self, root, skeleton=''):
        ...
```

服务层统一处理：

- missing → ensure_schema → scan；
- rebuild_required → rebuild → scan；
- ready → incremental scan；
- query empty → NO_MATCH；
- stale manifest → refresh once → retry once；
- 第二次仍失败 → 返回明确错误，不无限重试。

### 5.3 新增 `Fmodel/Pipeline/project_index_controller.py`

这是 Blender adapter，职责：

- Blender Operator；
- 把 service 结果写入 WizardState；
- 管理后台任务生命周期；
- 调用 `bpy.app.timers` 在主线程提交状态；
- 插件卸载或根目录变化时丢弃过期结果。

不得把 `bpy` 对象传入工作线程。

### 5.4 `Fmodel/Pipeline/steps/runner.py`

改为只消费一个统一入口：

```python
selection = prepare_project_animation_selection(state)
```

删除对 `scene.fmodel_project` 的直接依赖，不再产生“Project Settings Scan required”错误。

### 5.5 `Fmodel/project_assets.py`

第一阶段保留注册，用于旧 `.blend` 属性兼容，但不再拥有主产品 UI。

后续职责缩减为：

- 读取旧 `fmodel_project.root_path`；
- 向 `pipeline_wizard.project_root` 做一次性迁移；
- 保留旧 Operator ID 的兼容转发；
- 兼容期结束后删除。

## 6. 扫描触发策略

### 6.1 文件加载

`load_post` 只做只读 inspect：

- 迁移旧 Project Root；
- 检查数据库是否存在和兼容；
- 更新显示计数；
- 不创建数据库；
- 不递归扫描目录；
- 不计算内容哈希。

### 6.2 Project Root 变化

采用 300–500 ms 防抖，只做 inspect。根目录变化时：

- 增加 UI 请求代次；
- 清除旧 Import Plan；
- 清除旧 indexed selection；
- 更新 `source_dir`；
- 旧后台任务即使完成也不得覆盖新根目录状态。

### 6.3 Prepare Import Plan

这是保证索引新鲜度的主要触发点：

- 自动 ensure/rebuild；
- 执行增量扫描；
- 查询当前骨架；
- 生成 manifest；
- 持久化 checkpoint。

因此索引不会因为面板重绘而运行，也不会要求用户先操作另一个面板。

### 6.4 Step 4

不重新全量扫描，只调用现有 `dictionary_manifest()` 或等价验证，确认：

- 文件仍存在；
- size/mtime/hash 与计划一致；
- 骨架匹配仍成立。

如果失效，回到 Step 3，提示重新 Prepare Import Plan。

## 7. 性能设计

### 7.1 扫描两阶段化

当前扫描应重构为：

```text
枚举候选路径
→ stat(size, mtime_ns)
→ 与 SQLite 现有记录比较
→ 只对新增/变化文件计算 SHA-256
→ 只解析新增/变化 UEFormat identity
→ 单事务发布新 generation
```

不可在“发现阶段”无条件哈希全部文件。

### 7.2 动画专用扫描范围

本设计中的 service 默认只需要：

```text
.ueanim
.uepose
```

第一阶段为保持 Schema v3 和旧索引兼容，可以继续让底层记录 `.uemodel`，但新 UI 不显示模型数量。第二阶段增加明确的扫描 scope：

```python
ProjectIndex(root, asset_scope='animation')
```

不要复用 `include_legacy` 表达动画 scope，因为两者语义不同。

### 7.3 后台任务

建议最多一个工作线程：

- 磁盘扫描本身以 I/O 为主；
- 避免多个项目扫描竞争磁盘；
- 相同 canonical root 的请求去重；
- 新的 force refresh 可以取消尚未开始的旧请求；
- 已进入 SQLite 发布事务后不响应取消，确保原子性。

`ProjectIndex` 增加可选回调：

```python
scan(roots, *, progress=None, cancelled=None)
```

取消发生在发布前时保留上一成功 generation。

## 8. 错误与恢复策略

| 状态 | UI 行为 | Prepare 行为 |
|---|---|---|
| IDLE | 尚未检查 | 自动 inspect/refresh |
| MISSING | 显示“索引将在准备计划时创建” | 自动创建并扫描 |
| REBUILD_REQUIRED | Advanced 显示旧 Schema | 自动重建派生数据库并扫描 |
| READY | 显示总数/匹配数 | 增量扫描后生成计划 |
| NO_MATCH | 显示当前骨架无动画 | 停止，不进入 Step 4 |
| UNAVAILABLE | 显示目录/数据库不可访问 | 停止并保留具体 OSError |
| FAILED | 显示最近失败原因 | 允许 Refresh 或 Reset and Rescan |

不得吞掉所有异常后只显示“Scan required”。至少区分：

- root 不存在；
- root 不是目录；
- `.fmodel` 无写权限；
- SQLite 不可读；
- Schema 不兼容；
- UEFormat 文件损坏；
- 当前骨架没有匹配动画；
- 已保存的计划文件过期。

## 9. 旧数据兼容

### 9.1 `.blend` 属性迁移

加载时：

```python
if not wizard.project_root and scene.fmodel_project.root_path:
    wizard.project_root = scene.fmodel_project.root_path
```

第一兼容阶段可双向同步 root，但必须用 guard 防止 update callback 递归。第二阶段只保留旧 → 新迁移。

### 9.2 旧操作符

旧 ID 暂时保留：

```text
fmodel.open_project_index
fmodel.scan_project_index
fmodel.rebuild_project_index
```

行为改为转发新 controller：

- Open → inspect；
- Scan → refresh；
- Rebuild → reset_and_refresh。

不再在主 UI 绘制，避免旧脚本立即失效。

### 9.3 旧 checkpoint

`EXPLICIT` 和已有 `indexed_selected_paths` 继续由 `_indexed_selection_from_state()` 读取。新 UI 不创建 EXPLICIT，但旧 checkpoint 可继续恢复和验证。

### 9.4 SQLite

第一阶段不升级 Schema，继续使用 v3，避免 UI 重构与数据库迁移同时发生。只有在动画 scope 和扫描性能改造确实需要新字段时才引入 v4。

## 10. UI 决策

### 保留

- `source_mode`；
- Project Root；
- Manual Animation Directory；
- Skeleton Filter；
- Output Directory；
- Library Name；
- Prepare Import Plan；
- 动画数量和匹配数量；
- Advanced 的 Refresh/Reset。

### 删除或隐藏

- 独立 `FMODEL_PT_ProjectSettings` 注册；
- Models count；
- Schema 作为主界面信息；
- Open 按钮；
- “Project Settings Scan required”文案；
- 新 UI 中的 EXPLICIT indexed selector；
- 用户对 `source_dir` 的直接编辑。

## 11. 实施阶段

### Phase A：状态归并与 UI 合并

1. WizardState 新增 `project_root` 和 index display fields；
2. Step 2 直接绘制 Project Root；
3. `_sync_animation_source()` 不再读取 `scene.fmodel_project`；
4. load_post 增加旧 root 迁移与只读 inspect；
5. 从 `ui._classes` 移除独立 Project Settings Panel；
6. Advanced 内复用旧维护 Operator；
7. 保持同步扫描和 Schema v3，先稳定行为。

### Phase B：服务层与自动准备

1. 新增 `core/project_index_service.py`；
2. Step 3 自动 ensure/refresh/query/manifest；
3. runner 删除对 `fmodel_project` 的直接引用；
4. 统一错误状态；
5. stale 时只自动刷新并重试一次。

### Phase C：扫描性能

1. `_discover_root()` 改为 stat-only；
2. hash/parse 只处理变化文件；
3. 增加 progress/cancel；
4. 增加 animation-only scope；
5. 大项目性能基准测试。

### Phase D：后台任务

1. 新增 controller 单工作线程；
2. `bpy.app.timers` 主线程发布结果；
3. 请求代次和根目录校验；
4. 插件卸载清理；
5. UI 进度、取消与重复请求去重。

### Phase E：兼容代码收尾

实际 Blender 回归通过一个发行候选后：

1. 删除旧 Project Settings Panel 类；
2. 删除只服务旧 UI 的 count/status 属性；
3. 评估停止默认模型索引；
4. 将旧 Operator 标记 deprecated 或删除；
5. 更新 README 和迁移说明。

## 12. 测试矩阵

### 纯 Python

- missing index 自动创建；
- incompatible schema 自动重建；
- scan failure 保留上一 generation；
- unchanged file 不重新 hash/parse；
- changed file 重新发布；
- added/deleted animation 正确更新；
- strict UEFormat 无 JSON 仍可索引；
- skeleton query 只返回匹配动画；
- stale selection refresh 一次后成功；
- 第二次仍 stale 时失败；
- animation-only scope 不记录模型；
- cancel 不发布半成品 generation。

### Fake Blender

- root 变化清除工作流结果；
- source_mode 正确派生 source_dir；
- load_post 只 inspect、不 scan；
- 旧 `fmodel_project.root_path` 自动迁移；
- UI draw 不写数据库；
- 过期后台结果不覆盖新 root；
- unregister 后 timer 不更新属性；
- 旧 operator 正确转发。

### Blender 5.2 手动回归

- 新 `.blend` 设置 Project Root；
- 旧 `.blend` 打开后 root 保留；
- 缺失数据库时 Prepare 自动完成；
- 新增 UEANIM 后 Refresh 可发现；
- 删除已计划 UEANIM 后 Step 4 拒绝；
- 无匹配动画时显示 NO_MATCH；
- 大目录扫描期间 Blender UI 可响应；
- 禁用/启用插件无残留 timer/thread；
- 保存并重开 `.blend` 后状态只读刷新正确。

## 13. 验收标准

设计完成后的产品行为必须满足：

1. 正常用户不需要看到或操作独立 Project Settings；
2. 用户可在 Animation Pipeline 内完成全部配置；
3. 缺失或旧版索引可自动恢复；
4. UI draw 和 load_post 不执行递归扫描；
5. Prepare Import Plan 使用最新增量扫描结果；
6. Step 4 仍严格验证精确输入文件；
7. 旧 `.blend` 和旧 checkpoint 在兼容期内可继续使用；
8. 索引故障有可诊断信息和 Advanced 修复入口；
9. 未变化动画不重复 hash/parse；
10. 不恢复当前没有业务消费者的贴图/材质项目索引。

## 14. 最终目录目标

```text
core/
  project_index.py              # SQLite 与扫描原语
  project_index_service.py      # 自动恢复、查询、准备选择

Fmodel/Pipeline/
  project_index_controller.py   # Blender 任务、状态和 Operators
  steps/
    state.py                    # Pipeline 用户输入与显示状态
    step3_scan.py               # Prepare Import Plan 入口
    runner.py                   # 消费已准备选择

Fmodel/
  project_assets.py             # 兼容桥，后续删除

ui.py                           # Animation Pipeline 内置索引 UI
```

本设计优先减少用户认知负担，同时保留现有 ProjectIndex 的事务、指纹和 checkpoint 安全边界。实施时应先完成同步、可测试的 UI/服务整合，再进行线程化，避免一次同时改变状态归属、数据库行为和并发模型。

## 15. Phase A 实施记录（2026-08-08）

已完成：

- `WizardState.project_root` 接管 Animation Pipeline 的项目目录输入；
- 新增 Pipeline 索引状态、Schema、动画计数和消息显示字段；
- `source_dir` 根据 Indexed Project / Folder 自动派生；
- 模型导入和手动 Armature handoff 会写入新的 `project_root`；
- 旧 `scene.fmodel_project.root_path` 与新状态保持兼容同步；
- `load_post` 执行旧属性迁移与只读索引刷新，不创建或扫描数据库；
- Animation Pipeline Step 2 直接显示 Project Root 和索引状态；
- Refresh Index / Reset and Rescan 收入默认折叠的 Index Maintenance；
- 独立 `FMODEL_PT_ProjectSettings` 不再注册为顶层面板；
- Reset 操作现在会重建空数据库后立即同步扫描，避免旧 Rebuild 语义误导；
- README 中英文说明已更新；
- 旧 PropertyGroup、Operator ID、SQLite v3 和 checkpoint 字段仍然保留。

验证：

```text
python -m compileall -q .
git diff --check
python -m pytest -q
617 passed, 97 subtests passed
```

已同步到：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\FModel_Tools
```

同步备份：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\.fmodel_tools_backups\FModel_Tools_20260808_170447
```

Phase B–C 的记录保留如下；Phase D 已在本文第 18 节完成单工作线程、请求代次、任务去重和主线程状态提交。

## 16. Phase B 实施记录（2026-08-08）

已完成：

- 新增 `core/project_index_service.py`，提供 inspect、refresh、reset_and_refresh、prepare_selection 和 prepare_paths；
- 新增 `ProjectIndex.summary()` 只读摘要接口，统一返回 generation、最后扫描时间、动画总数和当前骨架匹配数；
- 新增 `Fmodel/Pipeline/project_index_controller.py`，负责把 service snapshot 和错误映射到 WizardState；
- Prepare Import Plan 会自动创建缺失 SQLite 索引；
- Schema 不兼容时会自动重建派生数据库后继续扫描；
- 每次 Project 模式 Prepare 都执行增量刷新，再查询当前骨架全部动画；
- 旧 EXPLICIT 路径选择仍可按精确子集刷新和验证；
- manifest 构建遇到扫描后竞态时只刷新并重试一次，不无限循环；
- NO_MATCH、root unavailable、refresh failed 和 stale selection 使用独立错误状态；
- `step3_scan.py` 和 `runner.py` 不再读取 `scene.fmodel_project`；
- Step 4 继续重验 checkpoint 中的精确路径；
- UI 显示 animation total、matching count 和 generation；
- 旧 Project Settings PropertyGroup 仅剩迁移和维护兼容职责。

新增测试：

```text
test_project_index_service.py
test_project_index_controller.py
```

完整验证：

```text
python -m compileall -q .
git diff --check
python -m pytest -q
627 passed, 95 subtests passed
```

Phase B 已同步到 Blender 5.2：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\FModel_Tools
```

同步备份：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\.fmodel_tools_backups\FModel_Tools_20260808_172719
```

## 17. Phase C 实施记录（2026-08-08）

已完成：

- `ProjectIndex.scan()` 改为 stat-first；
- 未变化的 strict `.ueanim/.uepose/.uemodel` 不再重复计算 SHA-256；
- 新文件、size/mtime 变化文件和 force scan 才进入哈希阶段；
- 内容哈希未变化但 stat 变化时，只更新 files/assets fingerprint 和 generation，不重复解析 provenance；
- legacy/invalid 记录仍保守计算组合哈希，以继续检测可选 JSON sidecar 变化；
- same-size/same-mtime 的 strict 文件由 manifest 重验发现，service 会执行一次 `force=True` 刷新并重试；
- 新增 `asset_scope=all|animation|model`；
- Animation Pipeline、Project maintenance 和 Step 4 统一使用 animation scope；
- animation scope 会清除旧数据库中不再属于当前 scope 的模型记录；
- 新增 `progress` callback，阶段包括 discover、compare、published；
- 新增 `cancelled` callback，发布事务前取消会保留上一成功 generation；
- progress callback 是观察性的，其异常不会破坏扫描；
- ProjectAnimationIndexService 转发 force/progress/cancelled，并将取消映射为结构化错误。

性能边界：

- 当前未引入线程，Prepare Import Plan 仍同步执行；
- strict 原生 UEFormat 在第二次无变化扫描中跳过文件内容读取；
- invalid/legacy 文件为了 sidecar 正确性仍会重新哈希；
- 扫描仍会递归执行 `os.walk` 和 `stat`；
- Phase D 已在后续完成 UI 响应、任务进度显示和取消按钮。

新增回归覆盖：

- unchanged strict scan 的 `_content_hash_pair` 调用次数为 0；
- force scan 会哈希但不重复解析 unchanged provenance；
- stat-only 变化更新 fingerprint 而不重新解析；
- cancelled scan 不发布新 generation；
- progress 最终报告 published；
- animation scope 排除并移除 model rows；
- manifest 竞态恢复的第二次 scan 必须使用 force；
- 默认 service 数据库只保留 animation asset type。

完整验证：

```text
python -m compileall -q .
git diff --check
python -m pytest -q
637 passed, 95 subtests passed
```

Phase C 已同步到 Blender 5.2，备份为：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\.fmodel_tools_backups\FModel_Tools_20260808_174053
```


## 18. Phase D 实施记录（2026-08-08）

已完成后台动画索引架构：

- 新增 `core/project_index_worker.py`；
- 使用 `ThreadPoolExecutor(max_workers=1)` 串行执行磁盘扫描、SQLite 更新、骨架查询和 manifest 构建；
- 任务键包含 canonical root、skeleton、selection mode 和 explicit paths；
- 相同进行中任务去重，不同新请求会 supersede 并协作取消旧请求；
- queued future 可直接取消，running job 通过 Phase C 的 `cancelled()` 边界在文件阶段之间退出；
- Worker 完全不导入或访问 `bpy`。

Blender Controller 已扩展为：

```text
后台 Worker
→ 线程安全 snapshot/progress
→ bpy.app.timers 轮询
→ 主线程验证 scene / request generation / root / skeleton
→ 主线程写 WizardState、PipelineContext 和 checkpoint
```

具体保护：

- `project_index_job_id` 和 `project_index_request_generation` 防止旧请求提交；
- root、skeleton、source mode 或 explicit paths 变化时，旧结果会取消或丢弃；
- 活动 Armature 只保存名称，timer 提交时重新解析，不跨线程持有 Blender RNA；
- timer callback 只在 Blender 主线程更新状态；
- 插件 unregister 会先取消任务并注销 timer，再注销 Wizard RNA；
- `.blend` 文件加载会取消上一文件的任务，并清理保存文件中可能残留的 transient job 状态；
- transient job/progress 字段使用 `SKIP_SAVE`；
- Blender background、Fake Blender 或 timer 不可用时保留同步准备路径。

Step 3 和 UI 已完成：

- Project 模式点击 **Prepare Import Plan** 后立即返回，Blender UI 保持响应；
- 显示 queued / discover / compare / published 等阶段、百分比和当前文件；
- 提供 **Cancel** 按钮；
- 活动任务期间禁用重复 Prepare 和 Index Maintenance 的 Refresh/Reset；
- 异步完成后复用与同步路径相同的主线程 finalizer，生成相同的 `PipelineContext`、`DictionaryManifest`、checkpoint 和 skeleton mappings；
- 旧 `EXPLICIT` 选择继续兼容。

新增回归：

```text
test_project_index_worker.py
test_project_index_async_controller.py
```

并扩展：

```text
test_pipeline_manager_full_entry.py
test_project_assets_ui.py
test_auto_handlers_lifecycle.py
```

当前完整纯 Python / Fake Blender 回归：

```text
660 passed, 95 subtests passed in 26.09s
```

当前剩余边界：

- 单个超大文件正在计算 SHA-256 时，取消会在该文件处理结束后生效；
- 手动 **Refresh Index** / **Reset and Rescan** 仍是同步维护操作，但后台 Prepare 运行时已禁止并发调用；
- Blender 5.2 GUI 的真实大目录响应、进度刷新、取消、禁用/启用插件仍需用户手动回归；
- Phase E 仅在一个 Release Candidate 通过真实 Blender 回归后执行，不提前删除兼容 PropertyGroup、旧 Operator ID 或 checkpoint 字段。
