# 功能实现审计（2026-11-20）

> 范围：V2 主机 137 个 Python 文件 / 29,201 行 + 内置 vendor 72 文件。
> 结论：**核心逻辑坚实，找到 1 个真实缺陷**（静默成功误报），其余为可接受的边缘行为。

## 1. 审计方法

不做纯静态阅读，而是**构造输入并观察实际行为**：

| 手段 | 目标 |
| --- | --- |
| 随机模糊（13,500+ 载荷） | 解码器是否会以未声明异常崩溃 |
| 结构感知模糊（逐字节变异 / 全截断扫描） | 合法头部之后深层字段的健壮性 |
| 真实 Blender 5.2.3 无头执行 | 操作符、UI 状态、资源泄漏 |
| 对抗性用例 | 路径逃逸、事务回滚、批量失败隔离、贴图缺失 |

## 2. 修复记录（2026-11-20）

上面那条缺陷已修复，两个仓库各自提交：

| 仓库 | 提交 | 内容 |
| --- | --- | --- |
| canonical add-on | `c0e9aa6` | `_require_created_asset` 守卫 + 操作符读取返回值 |
| V2 主机 | `8645105` | vendor 同步 + 树清单重建 + 回归测试 |

**修复语义**：`import_file` 返回 `None` 时按资产类型区分——

- `UEPoseOptions`：**合法**（姿势就地应用，不建对象）→ 仍然成功
- `UEModelOptions` / `UEAnimOptions`：**异常**（解析成功但无内容可建）→ 走失败路径并报告原因

**验证**：

- 真实 Blender 5.2.3 实测：零几何 `.uemodel` → `FAILED`，报告含 `LOD` 原因；正常模型 → `SUCCESS`
- 部署副本（`%APPDATA%` 下的 addons 目录）同样验证通过
- 反向对照：移除守卫调用 → 新测试变红；恢复 → 变绿
- 新增 `tests/test_import_result_contract.py`（5 项）

**连带发现并修复**：`scripts/audit_pipeline_failure_boundaries.py` 用固定名单
从 AST 提取操作符代码，新增的模块级辅助函数不在名单内，导致审计脚本 `NameError`。
已改为**自动解析 execute() 的依赖闭包**，以后操作符再加辅助函数不会重蹈覆辙。
反向对照确认审计仍能检出真实缺陷（注入 false-success → `FINDINGS` + `falseSuccessCount: 3`）。

## 2. 已确认的缺陷

### 缺陷：零几何文件被报告为导入成功（中等严重）

**位置**：`vendor/io_scene_ueformat/op/import_helpers.py` 的 `UFImportBase.execute`

**现象**（真实 Blender 5.2.3 实测）：

```text
输入：结构合法但 LODS 为空的 .uemodel
operator    : ['FINISHED']
objects     : 0
UI status   : SUCCESS  (Model import: 1 file(s))
```

**根因**：`create_model` 在 `dto.lods` 为空时遍历零次，返回 `None`
（`importer/create/model.py:97,236`）。`execute` 只把**异常**当作失败，
从不检查 `import_file` 的返回值：

```python
try:
    UEFormatImport(options, creation_tracker=capture).import_file(path)  # 返回值被丢弃
except Exception as error:
    ...  # 只有异常才算失败
...
_set_last_import_report(..., "Status: Success", ...)   # 无条件报成功
```

**重要**：这是**独立安装**时的路径。V2 主机的
`Fmodel/importers/model/operator_model_ueformat.py:299` 有守卫
（`if not imported_obj: raise ValueError(...)`），所以经 V2 导入不受影响。

**为什么值得修**：这与项目纲领「进程活着不等于程序启动了」直接冲突。用户拿到
`Status: Success` 却看不到任何对象，唯一的解释线索是一条不含诊断的报告。

**建议**：`execute` 检查返回值；当 `UEModelOptions`/`UEAnimOptions` 返回 `None` 时按失败处理。
注意 `UEPoseOptions` 合法返回 `None`（姿势就地应用、不建对象），必须排除。

## 3. 已验证正确、无需修改的项

| 检查项 | 证据 |
| --- | --- |
| 材质/贴图解码器 | 13,500+ 随机载荷全部以声明的 `FModelMaterialDecodeError` 拒绝，零未声明崩溃 |
| 逐字节变异 + 全截断扫描 | 124 字节合法载荷的每个字节位变异、每个截断前缀，均干净拒绝 |
| 路径逃逸防护 | `..` 前缀、反斜杠、盘符、绝对路径全部被 `resolve + relative_to` 拦截 |
| 贴图缺失 | 警告并继续（材质保留），符合「linking is auxiliary」设计 |
| 贴图损坏/截断 | **失败关闭**，且对象/材质/图像零泄漏 |
| 事务回滚 | 只删本次创建的数据块；共享块保留；幂等性有测试锁定 |
| 批量失败隔离 | 中间任务失败不污染其余；断点续跑不重跑已完成任务 |
| 导入会话 | `ImportSession` 序列化/生命周期 250+ 用例零异常 |
| 身份读取器 | 3000 个随机载荷零误接受、零未声明异常 |

## 4. 我一开始怀疑、但经验证不是缺陷的项

审计中我提出过几个假设，逐一证伪，记录在此以免后人重复：

| 假设 | 实际 |
| --- | --- |
| 重复调用 `rollback()` 返回陈旧报告 = bug | **是显式设计**，`test_import_transaction.py:72` 断言 `first == second` |
| `decode_texture_payload` 泄漏裸 `ValueError` | 上层统一 `except ValueError`，且 `FModelMaterialDecodeError` 本就继承 `ValueError`；仅是消息精度问题 |
| manifest 遍历顺序不稳定导致 entry_id 漂移 | 扫描器全程 `sorted()`，顺序确定；且 ID 由 ordinal+name+path 派生 |
| `target_lod` 越界静默导入错误 LOD | 探针在同一会话累积对象造成 `.001` 后缀假象；`min()` 已钳制上界，UI 声明 `min=0` |

## 5. 观察到的设计取舍（非缺陷，但值得知道）

- **资源上限默认关闭**：`FMODEL_UEFORMAT_MAX_*` 环境变量未设置时不设上限。
  超大/恶意文件只受各读取器自身的结构校验保护。
- **33 处静默 `except Exception: pass`**：分布在清理、回滚、诊断持久化路径。
  属 best-effort 语义，但若掩盖真实失败，现场不会有任何日志。
- **`import_file` 返回值是多义的**：`None` 既可能是 UEPose 正常、也可能是 UEModel 无 LOD，
  调用方必须自行区分——这正是缺陷 1 的根源。

## 6. 未覆盖

- GUI 交互（全部为 `--background`）；面板绘制断言的是契约而非像素。
- 真实游戏资产（夹具为合成，最大 16,900 顶点）。
- Blender 4.x。
- 并发导入（同一会话内两个导入同时进行）。
