# Blender 5.2 插件同步说明（2026-08-08）

## 当前同步目标

V2 已同步到：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\FModel_Tools
```

Blender 中应以 `FModel_Tools` 作为插件包名启用，而不是使用源码目录名
`FModel-2-Blender-Tools-V2`。后者包含连字符，不适合作为稳定的 Python addon package name。

## 一键同步脚本

脚本位置：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\scripts\sync_to_blender_addon.py
```

在项目根目录执行：

```powershell
python scripts\sync_to_blender_addon.py
```

默认行为：

1. 从脚本所在项目根目录读取 V2 runtime；
2. 同步到 Blender 5.2 的用户 addons 目录；
3. 只复制可运行内容；
4. 排除 `.git`、测试、文档、缓存、`__pycache__`、`.pyc` 和历史 Backup 目录；
5. 如果目标已存在，先移动到：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\.fmodel_tools_backups\
```

6. 通过临时 staging 目录复制，并在替换前执行文件数量和 SHA-256 校验。

## 常用参数

只查看将要同步的内容：

```powershell
python scripts\sync_to_blender_addon.py --dry-run
```

指定其他 Blender 版本：

```powershell
python scripts\sync_to_blender_addon.py --blender-version 5.3
```

指定其他 addons 目录：

```powershell
python scripts\sync_to_blender_addon.py --addons "D:\Blender\5.2\scripts\addons"
```

## 运行注意事项

- 如果 Blender 当前正在运行并且已经加载旧版本插件，建议同步后重启 Blender；
- 不建议使用 `--no-backup`，除非确认目标目录中的旧版本不需要保留；
- 同步脚本不会复制项目测试、文档、Git metadata 或源码备份；
- Vendor 的 `zstandard` wheels、`vendor_tree_manifest.json` 和 Shaders 运行时资源会被保留。

## Phase D 后台索引同步记录

同步时间：2026-08-08 18:21（Asia/Shanghai）

```text
status: synchronized
file_count: 167
total_bytes: 3796584
digest: afa4d1b624dad3a4d1092f4681b67465cd7384ac6c41d39d101fd490878f5aa4
```

目标：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\FModel_Tools
```

同步前备份：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\.fmodel_tools_backups\FModel_Tools_20260808_182103
```

已逐文件验证以下 Phase D 关键运行时 SHA-256 与源码一致：

- `core/project_index_worker.py`；
- `Fmodel/Pipeline/project_index_controller.py`；
- `Fmodel/Pipeline/steps/state.py`；
- `Fmodel/Pipeline/steps/step3_scan.py`；
- `Fmodel/Pipeline/__init__.py`；
- `Fmodel/project_assets.py`；
- `auto_handlers.py`；
- `ui.py`。

同步后应重启 Blender 5.2，再进行大目录进度、Cancel、文件重载和插件禁用/启用手动回归。
