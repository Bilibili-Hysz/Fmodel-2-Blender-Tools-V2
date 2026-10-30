# 动画管线“字典错误”实测诊断

日期：2026-09-27（Asia/Shanghai）

## 结论

本次发现并复现的是两个不同的扫描错误，不是 Python `KeyError`，也不是修改版 FModel 的 UEFormat V10 身份字段无法解析。

1. **筛选条件不匹配**：`Andariel_LOD0_Skeleton` 是 Blender 对象侧的名称，当前导出文件中对应的源骨架是 `Andariel_Skeleton.Andariel_Skeleton`。前者无法匹配源骨架，导致字典阶段失败。
2. **已安装版读取扫描结果的通道错误**：管线已经把扫描结果写入磁盘检查点，但扫描操作符仍通过 Blender 内部 `PipelineContext` 文本块读取。新场景没有该文本块时，加载函数提前返回 `None`，继而报“未生成字典清单”。项目工作区已包含此前的磁盘优先读取修复，Blender 5.2 安装目录却仍是旧实现。

本次仅诊断、验证并保存报告。没有修改导出器、安装目录、原始导出资产、用户检查点或现场场景；没有执行安装脚本。

## 核查对象

- 项目工作区：`G:\Blender\Blender Scripts Github\FModel-2-Blender-Tools-V2`
- 修改版 FModel 工作区：`D:\~Vibe-Coding\Projects\Fmodel_Edited_UEformat`
- 实际发布版本：`Release\LATEST_CANDIDATE.json` 指向 2026-09-13 R63，而不是部分旧 current 文档仍提到的 R62。
- 实际导出目录：R63 下的 `Output\Exports\WickedIsland`。
- 已安装插件：`C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\FModel_Tools`
- 验证程序：`G:\Blender\Software\blender-5.2.1-windows-x64\blender.exe`

## 导出格式检查

对实际导出的全部 577 个 `.ueanim` 执行当前身份读取器检查：

- 577 个均为 UEFormat V10 压缩文件。
- 577 个均读到有效 `FMODEL_TARGET_SKELETON` 身份。
- 共 31 个源骨架。
- `SKEL_Ogre.SKEL_Ogre` 对应 23 个动画。
- `Andariel_Skeleton.Andariel_Skeleton` 对应 4 个动画。

修改版 CUE4Parse 的 `Writers\UEFormat\FModelSkeletonExtensionWriter.cs:45–78` 写入扩展封装版本、身份版本、源动画路径、骨架路径、可选 GUID 和骨骼布局哈希。现有读取器与本次真实输出相符。已安装版和工作区的身份读取器 SHA-256 相同，不存在这两个副本间的格式读取实现差异。

上述结论只验证包头、压缩载荷与身份扩展；不等于对全部动画轨道语义进行了完整导入验收。

## 用户检查点证据

原文件位于 R63 的 `Output\Exports\WickedIsland\Assets\.fmodel_pipeline_checkpoint.json`，本次只读。

| 2026-09-27 时间 | 检查点内容 |
| --- | --- |
| 19:03:56 | 发现 577 个带身份文件，但筛选 `Andariel_LOD0_Skeleton` 匹配数为 0；`mismatch=577, missing=0, invalid=0`。 |
| 19:06:28 | 已成功扫描 4 个动画，字典与格式阶段均完成。 |
| 19:09:15 | 已成功扫描 23 个动画，字典与格式阶段均完成。 |

检查点最终保存的源骨架为 `SKEL_Ogre.SKEL_Ogre`，目标对象为 `SM_OgreMale_3_LOD0_Skeleton`；后续导入、构建、完成阶段尚未执行。不能将界面错误解读为这些动画没有导出，或扫描阶段从未成功。

## 已安装版具体缺陷

已安装插件内：

- `core\context.py:356–380`：`persist_checkpoint()` 只持久化磁盘 sidecar，不再创建 Blender 文本块。
- `core\context.py:560–565`：`load_from_blender_text()` 首先检查内部文本块，不存在就返回 `None`；即使磁盘文件存在也不会继续读取。
- `Fmodel\Pipeline\steps\step3_scan.py:57–59`：扫描完成后仍仅调用 `load_from_blender_text()`。
- 同文件第 14 行：收到 `None` 后报“扫描失败：未生成字典清单”。

工作区 `Fmodel\Pipeline\steps\step3_scan.py:71–75` 已采用：

```python
output_dir = bpy.path.abspath(state.output_dir)
pipeline_context = (
    PipelineContext.load_from_checkpoint(output_dir)
    or PipelineContext.load_from_blender_text(output_dir=output_dir)
)
```

因此这里需要部署已有修复，而不是再次重写 UEFormat 解析器。两个副本的 `bl_info` 都显示 8.1.0，仅看面板版本号不足以判断修复是否安装。

## Blender 对照复现

使用两个独立的 `--background --factory-startup --python-exit-code 1` 进程，分别注册已安装源码和工作区源码；各写入独立的新建临时目录，不覆盖用户库。没有创建 `PipelineContext` 文本块。

| 输入 / 结果 | 已安装版 | 工作区源码版 |
| --- | --- | --- |
| 筛选 `Andariel_LOD0_Skeleton` | 字典阶段失败 | 字典阶段失败（筛选仍不匹配） |
| 筛选 `SKEL_Ogre.SKEL_Ogre` | 报“未生成字典清单” | `FINISHED` |
| 磁盘清单动画数 | 23 | 23 |
| 面板状态动画数 | 0 | 23 |
| `scan_completed` | false | true |
| 字典 / 格式阶段 | completed / completed | completed / completed |

相关单元回归：

```text
python -m pytest tests/test_ueformat_identity_reader.py tests/test_pipeline_native_ueformat_scan.py tests/test_pipeline_runner_resume.py -q
60 passed, 10 subtests passed
```

完整结构化证据、原检查点摘要、源码哈希和两次 Blender 复现日志保存在 `docs\compose\reports\2026-09-27-animation-dictionary-diagnosis.json`。

## 处理建议与边界

1. 将工作区的已修复版本安装到实际使用的 Blender 插件目录，重启 Blender 后再验证；本次未执行部署。
2. 源骨架筛选使用导出文件的骨架名，例如 `Andariel_Skeleton.Andariel_Skeleton` 或 `SKEL_Ogre.SKEL_Ogre`；不要填写网格派生的 Blender Armature 对象名。
3. 可清空筛选以发现全部源骨架，但随后必须正确映射目标骨架；不能把“全部扫描成功”视为“所有动画均可严格应用于当前模型”。
4. 后续改进应把真实阶段错误显示到操作符报告中，避免一律只报“字典阶段未通过”；无可信源骨架元数据时，不应将 Blender 对象名自动当成源骨架身份。不要用模糊匹配或删除任意后缀来绕过严格兼容检查。
5. 不需要为了本次报错重新导出这批动画、删除检查点、关闭严格身份验证，或重新引入 JSON 字典依赖。

本次没有执行完整 Action 导入、资产库发布、全部动画兼容性检查或 GUI 像素验收；结论限于已复现的扫描错误及对应格式身份检查。

## 修复跟进（2026-09-27）

用户授权后已完成修复及部署，前文“仅诊断”“尚未安装”的描述记录的是部署前状态。

- 扫描操作符从磁盘检查点优先读取结果；失败时从当前阶段日志报告具体原因，例如骨架筛选不匹配，而非统一提示“字典阶段未通过”。
- 自动挂载优先采用有效 `fmodel_skeleton_identity.skeleton_path` 的末段；旧模型仅回退到显式 `FModel_Skeleton` 元数据。没有可靠源身份时筛选保持空值，扫描后需明确映射目标骨架，不再用 Blender 对象名猜测源身份。
- 全量回归：`550 passed, 51 subtests passed`。Blender 5.2.1 后台、独立临时输出复测 R63 原始导出：错误筛选报告 `mismatch=577`；`SKEL_Ogre.SKEL_Ogre` 成功扫描 23 个动画，面板状态与磁盘检查点一致。安装版也通过同样检查。
- 通过 `scripts/sync_to_blender_addon.py` 默认备份方式同步 154 个运行时文件到 Blender 5.2 用户插件目录；旧版保存在 `C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\.fmodel_tools_backups\FModel_Tools_20260927_200703`。

本次未运行完整动画 Action 导入或发布资产库；已打开的 Blender 进程需要重启才能加载安装目录的新代码。
