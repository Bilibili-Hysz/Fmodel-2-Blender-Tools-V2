# V2 测试目录

所有 V2 自动化测试统一位于本目录，根目录不再散落 `test_*.py` 文件。

从仓库根目录执行：

```powershell
python -B -m pytest tests -q -p no:cacheprovider
```

如果只运行一个测试文件：

```powershell
python -B -m pytest tests/test_sync_to_blender_addon.py -q -p no:cacheprovider
```

`tests/conftest.py` 会将仓库根目录加入 `sys.path`；测试中的源码路径也统一从
`Path(__file__).resolve().parents[1]` 或等价的仓库根路径解析。

Windows 上若旧 pytest 临时目录发生权限错误，可使用全新的临时路径，不必清理旧目录或修改权限：

```powershell
python -B -m pytest tests -q -p no:cacheprovider --basetemp (Join-Path $env:TEMP ('fmodel-p0-' + [guid]::NewGuid().ToString('N')))
```

不要将 `--basetemp` 指向已有工作目录：pytest 会清理该目录。以上命令每次生成新的随机路径。
这些测试包含纯 Python 和模拟 Blender 合同测试，不等同于真实 Blender GUI、资产导入效果或场景验证。

UEPOSE 姿势源库、骨骼应用和可选烘焙的真实 Blender 回归（仅使用独立临时场景）：

```powershell
& 'G:\Blender\Software\blender-5.2.1-windows-x64\blender.exe' --background --factory-startup --python-exit-code 1 --python scripts/blender_pose_asset_smoke.py
```

脚本的损坏库用例会主动报告一次错误；最终必须出现 `POSE_ACCEPTANCE_PASSED` 且进程退出码为 0。
# 工作流与面板重构验收（2026-09-16）

`test_workflow_context.py` 覆盖固定目标、清空/删除、旧工程迁移、重命名、多骨架映射隔离。
`scripts/blender_workflow_smoke.py` 在 factory-startup 环境验证相对路径扫描、建库、应用、面板 RNA、保存重载与注册注销。
运行时必须添加 `--python-exit-code 1`，脚本只写入新建临时目录。面板检查不代替 GUI 视觉验收。
