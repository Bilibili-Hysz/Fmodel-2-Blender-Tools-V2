# Blender Vendor Runtime 长路径修复（2026-08-08）

## 现象

Blender 5.2 中显示：

```text
UEFormat Runtime: failed
Vendor Cache Extract Failed: 文件名或拓展名太长
```

## 原因

Vendor wheel 的最终缓存路径本身是可用的，但旧实现把完整 SHA-256 写入了临时解压目录名：

```text
.<sha256>.partial-XXXXXX
```

解压时 Windows 会把这个临时目录、wheel 内部文件路径和缓存根路径一起计算。对于
`zstandard` wheel 的 `.dist-info/licenses/...` 等路径，临时目录名可能把路径推过旧版
Windows `MAX_PATH` 边界，从而在 `ZipFile.extractall()` 阶段报“文件名或扩展名太长”。

## 修复

修改：

```text
F:\Blender Scripts Github\FModel-2-Blender-Tools-V2\core\vendor_loader.py
```

临时目录前缀从完整 hash 改为短前缀：

```python
tempfile.mkdtemp(prefix=".p-", dir=final_dir.parent)
```

完整 wheel hash 仍然保留在最终缓存目录和锁文件逻辑中，因此没有降低缓存隔离、SHA-256
校验或并发锁的安全性。

## 当前缓存

已在 Blender 5.2 的实际 Vendor cache 根目录完成 CPython 3.13 wheel 解压验证：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\config\FModel Tools\vendor-cache\io_scene_ueformat\1.1.5\cp313-win_amd64\1f830a0dac88719af0ae43b8b2d6aef487d437036468ef3c2ea59c51f9d55fd5
```

`.extracted.ok` 已成功生成。

## 用户操作

1. 完全退出 Blender 5.2；
2. 重新启动 Blender；
3. 检查 FModel 工具箱中的 `UEFormat Runtime` 是否变为 `ready`；
4. 如果仍显示旧错误，可删除以下 Vendor cache 内容后重启，让插件重新解压：

```text
C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.2\config\FModel Tools\vendor-cache
```

正常情况下不需要手动删除，因为新的 loader 会自动重建缺少 `.extracted.ok` 的最终缓存目录。
