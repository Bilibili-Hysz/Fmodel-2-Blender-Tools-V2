# UEModel ImportSession 接入状态（2026-08-08）

> 历史记录：本文只描述 UEModel 会话接入。UESCENE 不属于当前 V2 活动 importer；当前合同见 `docs/CURRENT_CONTRACT_2026-08-13.md`。

## 已完成

`Fmodel/importers/model/operator_model_ueformat.py` 已接入 `core/import_session.py`。

模型导入操作现在记录：

- `UEModel` asset kind；
- source path；
- 当前导入 phase；
- Vendor capability version；
- 成功/失败状态；
- 创建的 Blender datablock 数量；
- rollback 结果和异常详情。

结果会写入当前 Blender Scene：

```text
FModel_Import_Session
FModel_Import_Status
FModel_Import_Summary
FModel_Import_Operation_Id
```

## 状态路径

```text
resolve_bridge
    ├── ueformat_capability / ueformat_missing
build_options
    ├── ueformat_options_invalid
import_model
    ├── success
    └── uemodel_import_failed
```

失败时会先执行已有 `ImportTransaction` rollback，再将 rollback 摘要写入 ImportSession failure detail 和 warning。

## 兼容边界

ImportSession 使用 lazy import。旧测试宿主或不提供 `core.import_session` 的兼容环境仍然可以继续运行模型操作器，只是不持久化结构化报告。正式 V2 Blender 环境会使用完整 ImportSession。

## 当前未覆盖

- UEScene 仍使用自己的详细 scene report，并未完全合并到 ImportSession；
- Model operator 的真实 Blender 5.2 users/link rollback 仍需人工验证；
- 材质、贴图、骨架的更细粒度统计依赖真实 Vendor 返回的 datablock transaction。
