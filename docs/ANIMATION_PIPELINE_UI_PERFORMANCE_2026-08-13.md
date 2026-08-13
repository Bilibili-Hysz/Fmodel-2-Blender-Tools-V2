# Animation Pipeline 完成态 UI 卡顿审查（2026-08-13）

## 现象

执行 `导入并打包` 后，Blender 主界面明显卡顿；折叠 `Animation Pipeline` 面板后恢复流畅。

## 根因定位

`FModel_PT_Pipeline.draw()` 在 `current_step == 4` 且 `lib_completed == True` 时，仍然绘制完整的步骤 4 编辑器，包括：

- 全部 `skeleton_mappings` 集合行；
- 每个映射的目标骨架 PointerProperty；
- 兼容策略和状态；
- 导入参数、缩略图参数、打包参数；
- 完成后已经不可再执行的导入按钮状态计算。

当项目包含多个骨架或大量映射时，Blender 每次侧栏重绘都会重新构造这一整棵 UI。折叠面板后只绘制面板标题，因此现象会立即消失。

完成阶段还有一个次要成本：`FinalizationStage.execute()` 会同步调用 `bpy.ops.fmodel.scan_anims()`，用于刷新资产库树。它属于一次性的完成后扫描，不是持续循环；本轮先不改变该功能语义。

## 已实施修复

在 `F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\ui.py` 中增加完成态短路：

- `lib_completed == True` 时只显示完成摘要；
- 显示处理动画数、骨架数和未映射骨架；
- 保留 `重新生成缩略图` 和 `重新开始`；
- 不再绘制完整 mapping editor、导入参数和打包参数；
- 不改变导入结果、资产库文件和状态字段。

这相当于在完成后自动采用“折叠面板”的低成本 UI 路径，但仍保留完成结果和后续操作入口。

## 验证

相关回归集：

```text
101 passed, 65 subtests passed
```

新增测试：

```text
PipelineStep4UiTests.test_completed_step4_uses_compact_view_instead_of_mapping_editor
```

## 后续观察

如果手动测试仍然在完成后卡顿，则下一步应审查并将 `FinalizationStage` 的同步 `scan_anims()` 改为生命周期绑定的延迟扫描；本轮没有先改动该同步行为，以避免影响资产库刷新时序。
