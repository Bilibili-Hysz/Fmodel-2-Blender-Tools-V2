# FModel Tools V2 干净重建（2026-10-06）

## 结果

新安装包：`FModel_Tools_V2_R65_REBUILD_2026-10-06.zip`。发布目录仅保留本次 V2 ZIP 与 SOURCE.json。

当前运行时与 2026-10-01 INSTALLFIX4 完全一致，本次没有修改插件功能源码；版本仍为 **8.1.0**，内置 UEFormat **0.10.0**。构建包含已授权、尚未提交的工作树修改，按真实 dirty snapshot 记录来源。

## 清理

12 个旧 V2 ZIP / 来源清单共 12,653,505 字节已移至 `backups/2026-10-06-v2-build-cleanup/`，逐文件校验 SHA-256。永久删除动作被自动审批以 `blocked by policy` 拦截，因此采用移出发布目录的方式完成清理。清理记录保留旧文件名、字节数、哈希及归档路径。

## 检查与构建

| 项目 | 结果 |
| --- | --- |
| 全量 Python 回归 | 558 passed，51 subtests passed |
| 运行时语法检查 | 145 个 Python 文件通过 |
| Vendor 清单 | 73 个文件，源码与打包清单均严格验证通过 |
| 安装包 | 154 文件，2,077,192 字节 |
| Blender ZIP 类型 | Legacy Add-on，未误识别为嵌套 Extension |
| 隔离安装生命周期 | 安装 → 启用 → 延迟恢复 → 禁用 → 复启用通过 |
| V10 合成模型 | 网格、骨架、材质与严格身份导入通过 |
| 合成动画工作流 | 扫描、导入、建库、应用、固定目标、面板 RNA 与场景重开检查通过 |
| 合成姿势工作流 | 3 个源保存/加载、应用与 2 个网格烘焙通过 |

SHA-256：`6eb98a3e85221696d916e699f016738ff7beda6301b688dafea5825db998ee2d`，与旧 INSTALLFIX4 完全相同，确认构建结果可重复。

验证使用隔离的 Blender 5.2.1 后台进程和合成测试资产；真实游戏 GUI、贴图视觉及完整导出导入验收尚未完成，仍为 Candidate。

## 产物与证据

- 新包：`D:\~Vibe-Coding\Projects\Fmodel_Edited_UEformat\Release\FModel_Tools_V2_R65_REBUILD_2026-10-06.zip`
- 来源：同目录 `FModel_Tools_V2_R65_REBUILD_2026-10-06_SOURCE.json`
- 本仓库证据：`docs/compose/reports/2026-10-06-v2-build-validation.json`
- FModel 发布入口 `LATEST_CANDIDATE.json`、`INDEX.md` 和 workspace manifest 已更新；旧元数据保存在上述归档目录的 `release-metadata-before/`。
- 未同步用户 Blender 插件安装目录。
