# Vendor 内置实施状态

更新时间：2026-08-07
项目：`FModel-2-Blender-Tools-V2`

## 当前结论

Phase A（Vendor Loader 抽取）已经落地，可以作为当前开发基线：

- FModel 插件只自动加载 `io_scene_ueformat`；
- 无用的 `vendor/io_scene_psk_psa` 已从 V2 发行树移除；
- Vendor 通过 `core/vendor_loader.py` 读取锁定 manifest、选择运行时 wheel、校验 SHA-256、缓存并加载；
- 注册入口统一为 `register_vendor(handle, headless=True)`；
- Vendor 的面板和 File > Import 菜单不会由 FModel 的内置依赖创建；
- 外部同名 `io_scene_ueformat` 被检测为冲突时，内置副本 fail closed，不会注销或接管外部模块；
- 注销按 `VendorHandle` 的模块对象和来源路径进行，不依赖当前 `sys.modules` 中恰好同名的对象；
- 旧 PSK/PSA 依赖仍保留在源码树中用于回滚，但不再注册、导入或自动加载；
- UEFormat-only 管线的正常资产扫描不再使用外部 FModel JSON sidecar。

## 已实现文件

### Loader 与 manifest

- `core/vendor_loader.py`
  - `VendorSpec` / `VendorHandle` / `VendorLoadError`；
  - TOML schema、package name、registration mode、wheel filename、tag、SHA-256 校验；
  - CPython ABI 与平台匹配；
  - 用户缓存目录、锁文件、临时目录原子发布；
  - zip path traversal 防护；
  - 外部同名模块冲突检测；
  - bundled package 子模块的精确清理；
  - headless register/unregister 生命周期。

- `vendor/vendor_manifest.toml`
  - Vendor version `1.1.5`；
  - Windows x64 CPython 3.11/3.12/3.13 wheel；
  - source commit、capabilities、license inventory。

### Blender host integration

- `auto_handlers.py`
  - `_load_bundled_vendor()` 只负责连接 host 与 Loader；
  - `_ensure_dependency_addons()` 负责用户偏好、当前能力检查和所有权登记；
  - `_unregister_vendor_addons()` 优先使用 `VendorHandle`，同时兼容一次热重载窗口内的旧 tuple 状态；
  - `_vendor_cache_root()` 使用 Blender 用户 CONFIG 资源目录，而不是普通 Temp。

### UEFormat-only 边界

- `Fmodel/importers/*/__init__.py` 只注册 UEFormat model/animation/scene；
- `ui.py` 只暴露 `.uemodel`、`.ueanim`、`.uepose`、`.uescene`；
- `Fmodel/Pipeline/stages.py` 的 FModel 原生扫描只读取 `.ueanim` / `.uepose`，旧 `*_manifest.json` 仅被明确跳过。

## 合同测试

当前 host-Python 测试结果：

```text
551 passed, 98 subtests passed
```

相关重点测试：

- `test_vendor_loader_contract.py`：manifest、wheel 选择、hash、缓存、路径穿越、外部冲突、子模块注销；
- `test_auto_handlers_lifecycle.py`：headless 注册、Handle 所有权、reload 清理、外部冲突；
- `test_ueformat_vendor_headless.py`：Vendor 面板和菜单的 headless 排除；
- `test_ueformat_only_phase1_contract.py`：UEFormat-only UI/registration 边界；
- `test_pipeline_task_scope.py`：旧 JSON sidecar 不再成为正常 FModel 输入。

## 仍未执行的高风险步骤

以下步骤有意保留到 Blender 5.2 手动验证之后：

1. 将 Python package name 从 `io_scene_ueformat` 私有化为 `fmodel_ueformat_runtime`；
2. 更新所有 bridge/operator 的模块对象获取路径；
3. 删除剩余 legacy operator/source 文件；
4. 制作最终 release archive 并在真实 Blender 进程测试模型、动画、场景、材质和外部冲突；
5. 清理旧缓存和旧版本设置前，提供显式迁移/回滚说明。

## Project Index active scope

`core/project_index.py` 的默认 `ProjectIndex(root)` 已切换为 UEFormat-only：

- `.uemodel`
- `.ueanim`
- `.uepose`

旧 `.fbx` / `.psk` / `.pskx` 仍可通过 `ProjectIndex(root, include_legacy=True)` 显式建立迁移索引，
但不会进入正常 Project Settings 的默认扫描范围。

## 当前手动验证入口

在 Blender 5.2 中只安装 FModel Tools V2，不要同时启用单独安装的旧版 UEFormat 插件。至少验证：

- 启动后 FModel 面板可用，canonical UEFormat 面板不重复；
- `.uemodel` 导入包含材质和贴图；
- `.ueanim` / `.uepose` 的骨架严格匹配；
- `.uescene` 能通过 FModel 入口工作；
- 注销/重新加载后没有重复 operator、菜单或 Scene property；
- 外部旧版 `io_scene_ueformat` 已启用时，FModel 给出冲突提示且不接管外部模块；
- 用户缓存目录中能看到按 Vendor version、ABI、platform、SHA-256 分层的 wheel 缓存。

## 回滚策略

在手动验证完成前：

- 不执行 `git reset`、`git clean` 或批量删除；
- 旧导入器源文件仍保留但不接入运行时；旧 Vendor 已删除，如需恢复必须从历史版本或外部备份恢复；
- 如需回滚，只恢复 `auto_handlers.py` 的 Vendor 连接层和对应测试，不修改 Loader 的缓存目录；
- 不在同一 Blender 进程中交替加载外部旧版和内置新版 UEFormat，优先重启 Blender。
