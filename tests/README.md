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
