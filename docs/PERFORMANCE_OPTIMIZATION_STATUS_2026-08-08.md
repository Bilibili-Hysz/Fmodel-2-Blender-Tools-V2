# FModel 2 Blender Tools V2 性能整改状态（历史记录，已收敛）

> 本文主要记录早期 UESCENE 性能实验。UESCENE 已从当前 V2 Vendor/importer 合同中退役；当前性能工作聚焦 Animation Pipeline 完成态、资产库 draw-time 扫描和真实 Blender 5.2 手测。当前合同见 `docs/CURRENT_CONTRACT_2026-08-13.md`。

## 本轮完成

### UEScene 组件创建追踪

`vendor/io_scene_ueformat/importer/scene_import.py` 不再在普通 UEScene 组件导入前后扫描全部 Blender datablock。

模型导入器通过 `UEFormatImportCapture` 记录本次实际创建的：

- Object
- Mesh
- Armature
- Material
- Image
- Action

失败回滚和模型移动直接使用这些创建引用。

### 同模型模板复用

同一次 UEScene 导入中，具有相同来源路径和兼容导入选项的模型会建立一个隐藏的 Source Template Collection。

后续组件通过 `Object.copy()` 复制对象包装器，但共享：

- Mesh 数据
- Armature 数据
- Material 数据
- Image 数据

复制过程中会重新映射：

- Parent
- Armature Modifier Object
- Constraint Target

这样可以避免相同 `.uemodel` 被重复解析、重复生成 Mesh、重复构建材质和重复读取纹理。

## 保守边界

当前带有 `MATO` 材质覆盖的组件仍然走独立导入路径：

```python
cache_allowed = not component.material_overrides
```

原因是现有 UEScene 导入器目前只保存材质覆盖元数据，没有完整执行逐组件材质隔离与覆盖。待材质覆盖功能正式实现后，需要将材质隔离策略纳入模板缓存设计。

严格场景替换模式仍保留一次场景级快照，用于整体事务回滚；该快照不再按组件重复执行。

## 未覆盖范围

- 不同 `target_lod` 或导入选项之间不会共享模板；
- 不同来源路径不会共享模板；
- 尚未实现跨多个 UEScene 导入操作的全局模型缓存；
- 尚未实现跨模型的全局 Image / Material Cache；
- 尚未进行 Blender 5.2 真实场景导入验证。

## 验证

当前纯 Python 测试结果：

```text
598 passed, 98 subtests passed in 16.35s
```

已执行：

- `python -m py_compile`
- `python -m compileall`
- `git diff --check`
- Vendor tree manifest 重新生成与严格校验

## 下一步

建议优先在 Blender 5.2 中验证：

1. 同一 `.uemodel` 被多个普通 StaticMeshComponent 引用；
2. 同一 `.uemodel` 被多个 ISM/HISM Component 引用；
3. 带骨架模型的 Mesh 与 Armature Modifier 是否正确指向每个组件自己的 Armature Object；
4. 严格场景导入失败后的模板 Collection 清理；
5. 材质和贴图是否保持共享且没有重复生成。

## 追加完成：单次 UEScene 资源缓存

新增 `vendor/io_scene_ueformat/importer/resource_cache.py`，在一次 UEScene materialization 生命周期内共享：

- Image datablock；
- Material datablock；
- 外部纹理内容哈希键；
- 纹理解释方式（COLOR / DATA）对应的缓存键。

该缓存是操作级缓存，不跨 Blender 操作持久化。发生组件失败回滚时会调用 `prune()` 清除已经失效的 Blender ID 引用。

这使不同 `.uemodel` 但引用同一外部纹理或等价 FModel 材质的组件可以复用 Blender Image / Material，减少重复加载、重复哈希和全局材质线性扫描。

带 MATO 材质覆盖的组件仍保持独立模型导入路径。

## 追加完成：Mesh 材质索引批量写入

新增 `vendor/io_scene_ueformat/importer/mesh_batch.py`，将 UEFormat 的材质 section 三角形索引一次转换为 Blender polygon material index 数组，再通过：

```python
mesh_data.polygons.foreach_set("material_index", indices)
```

替代逐面执行 `polygon.material_index = ...`。

该改动只改变写入方式，不改变 section 顺序、覆盖顺序或越界校验。

## 追加完成：Morph Target 坐标批量写入

新增 `vendor/io_scene_ueformat/importer/morph_batch.py`，将 Morph Delta 先在 NumPy 数组中累加，再通过 Blender Shape Key 的 `foreach_set("co", ...)` 写入。

实现使用 `numpy.add.at`，确保同一个顶点存在多个 Delta 时仍然保持累加语义，不会被高级索引赋值覆盖。

当宿主没有批量 RNA API 时，保留原逐 Delta fallback。

## 追加完成：UEScene 预检按 model_path 复用

`_validate_model_lod_manifests()` 现在在一次场景预检期间缓存 `read_uemodel_source_lods()` 结果。多个组件引用同一 `.uemodel` 时只解析一次预检 manifest，后续组件复用相同的 LOD 与 MATO 映射结果。

读取失败也会在本次预检中缓存为不可用状态，避免对同一损坏文件反复读取；严格的 MATO 来源歧义错误仍会立即终止预检。

## 追加完成：ImportSession 场景统计字段分离（2026-08-08）

UEScene 的 actor/component/static-mesh/light 等统计现在写入 `ImportSession.metrics`，不再混入表示 Blender datablock 创建数量的 `created_counts`。

这样可以区分：

- `created_counts`：objects、meshes、materials、images 等实际创建的 Blender 数据块；
- `metrics`：场景导入业务统计，例如 actors、deferred_instances、unresolved_models。

旧宿主若没有 `record_metric()`，Vendor 会兼容回退到 `record_created()`；当前 V2 宿主使用新字段。

## 追加完成：UEScene datablock 计数接入（2026-08-08）

`UEFormatImportCapture.counts` 现在提供当前未 discard 的追踪项计数。
`SceneImportSummary.created_datablock_counts` 会汇总：

- UEFormat 模型导入产生的 Object、Mesh、Armature、Material、Image、Action；
- UEScene actor/component/light 对象与 light data；
- 模型模板复用产生的 Object wrapper；
- ISM/HISM 的 instance controller 对象。

当 instance set 没有 transform 时，创建后被移除的模型 Object 会从 capture 中 discard，避免计数虚高。Collection 数量暂不纳入 `created_counts`。

本阶段最新纯 Python 回归：`608 passed, 98 subtests passed`；Vendor tree manifest 已重新生成并通过严格校验。

UEScene 的 `last_import_report` 现在同步显示 `Created Datablocks` 与 `Scene Metrics` 分段，使 Blender Print/报告区域无需解析 Session JSON 即可看到本次导入摘要。

## 追加完成：Project Animation Index stat-first 扫描（2026-08-08）

`core/project_index.py` 的项目扫描不再在每次 Refresh/Prepare 时无条件读取并哈希全部 strict UEFormat 文件。

当前策略：

```text
os.walk + stat
→ strict 且 size/mtime 未变化：跳过内容读取
→ 新增或 stat 变化：计算内容哈希
→ 哈希变化：重新解析 identity/provenance
→ 哈希相同：只刷新 stat/fingerprint
→ 原子发布 generation
```

Animation Pipeline 使用 `asset_scope="animation"`，因此默认只发现：

- `.ueanim`
- `.uepose`

`.uemodel` 不再进入动画索引。旧数据库中的模型行会在下一次动画 scope 扫描时移除。

为保持安全边界：

- legacy/invalid 文件继续保守哈希，以检测 JSON sidecar；
- manifest 重验发现 same-stat 内容变化后，service 会强制哈希刷新并只重试一次；
- `cancelled` 在发布前返回时保留上一 generation；
- `progress` 提供 discover/compare/published 事件，Phase D 已接入 Blender UI。

本阶段完整回归：

```text
637 passed, 95 subtests passed in 16.58s
```


## 追加完成：Animation Pipeline 后台索引 Worker（2026-08-08）

项目动画索引已从 Blender 主线程同步执行改为单工作线程：

```text
Prepare Import Plan
→ ProjectIndexWorker(max_workers=1)
→ stat-first scan / SQLite / manifest
→ thread-safe snapshot
→ bpy.app.timers 主线程提交
```

性能与响应性行为：

- 相同任务键复用进行中的 job；
- 新任务 supersede 旧任务，避免多个项目扫描同时争用磁盘；
- discover 阶段报告已发现数量，compare 阶段报告完成数/总数；
- UI 每 0.1 秒轮询一次 snapshot，不由工作线程触碰 RNA；
- cancellation 在现有文件边界与 SQLite 发布前检查；
- 后台模式和无 timer 环境保留同步 fallback；
- 文件加载和插件卸载会清理 timer/job，不保留失去消费者的回调；
- Index Maintenance 在后台 Prepare 期间被禁用，避免两个写入者并发操作同一 SQLite 派生数据库。

Phase D 后完整回归为：

```text
660 passed, 95 subtests passed in 26.09s
```

真实 Blender 大项目的墙钟时间、UI 帧响应和单个超大文件取消延迟仍属于后续手动性能验证项。
