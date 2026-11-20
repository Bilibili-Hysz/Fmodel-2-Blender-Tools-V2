# Blender 5.x 兼容性核查（2026-11-20）

> 结论：**V2 插件与本机可用的三个 Blender 5.x 版本完全兼容**，未发现 5.x 专属缺陷。
> 已识别的唯一断点是未来 Blender 升级 Python ABI 时的 fail-closed 行为（见 §4）。

## 1. 核查范围

| Blender | 版本 | Python | ABI | numpy |
| --- | --- | --- | --- | --- |
| `blender-5.2.2-npr-port` | 5.2.2 LTS | 3.13.13 | cp313 | 2.3.4 |
| `blender-5.2.3-windows-x64` | 5.2.3 LTS | 3.13.13 | cp313 | 2.3.4 |
| `blender-5.3.0-beta` | 5.3.0 Beta | 3.13.13 | cp313 | 2.3.4 |

插件声明 `blender_version_min = "4.2.0"`（vendor 包）与 `bl_info.blender = (5, 0, 0)`（V2 主机），
两者都被上述版本满足。

## 2. 实测结果

每个版本独立跑四组检查，全部 PASS：

| 检查 | 5.2.2 | 5.2.3 | 5.3.0 Beta |
| --- | --- | --- | --- |
| 链路门禁：17 项断言 + 3 个样本导入（V10/V9、材质、贴图、动画、姿势、zstd） | PASS | PASS | PASS |
| 从 `io_scene_ueformat.zip` 装进干净实例并导入 | PASS | PASS | PASS |
| V2 主机注册 + 14 个面板全部绘制成功 | PASS | PASS | PASS |
| V2 内置 vendor runtime 状态 `ready` | PASS | PASS | PASS |

补充项，同样三版全过：

- **工作流验收**（`scripts/blender_workflow_smoke.py`）：固定目标骨架、三步建库、面板模式解析、重载存活。
- **姿势资产管线**（`scripts/blender_pose_asset_smoke.py`）：3 源 → 库 → 应用/烘焙。
- **动画清理**（`Universal_tools/Animations`）：在真实 5.x 槽位 Action 上遍历 channelbag。
- **V10 全量导入**（`scripts/blender_smoke_v10.py`）：网格 + 骨架 + 材质 + 身份一致。

## 3. 5.x 关键 API 变更的处置

| 变更 | 影响点 | 现状 |
| --- | --- | --- |
| 4.1 移除 `Mesh.use_auto_smooth` | `vendor/importer/create/model.py:128` | **已有版本保护** `if bpy.app.version < (4, 1, 0)`，5.x 不执行 |
| 4.4 FCurve 迁入 per-slot channelbag | `vendor/importer/create/anim.py` | **已分支**：`< (5,0,0)` 用 `action.fcurves`，否则走 `action_ensure_channelbag_for_slot` |
| 5.2 彻底移除 `Action.fcurves` | `Universal_tools/Animations/clean_Anim.py`、`utils_anim.py` | **已用 `getattr`/`hasattr` 防御**，实测 5.x 走 channelbag 路径正确 |
| 4.4 `AnimationData.action_slot` | `anim.py:126` | 已按 `>= (4, 4, 0)` 分支 |
| `bgl` / `blend_method` / `calc_normals` 等 | 全树 | **未使用** |

静态扫描 137 个文件，未发现无保护的已移除 API 调用。

## 4. 已识别的唯一断点：Python ABI

vendor 内置三个 zstandard wheel（cp311 / cp312 / cp313，均 win_amd64）。当前三个 5.x 版本
都是 **cp313**，选择正确并能成功解压（15 文件 + 标记）。

实测未来场景：

```text
cp311 -> zstandard-0.25.0-cp311-cp311-win_amd64.whl
cp312 -> zstandard-0.25.0-cp312-cp312-win_amd64.whl
cp313 -> zstandard-0.25.0-cp313-cp313-win_amd64.whl
cp314 -> FAIL-CLOSED VENDOR_WHEEL_PLATFORM_MISMATCH
cp315 -> FAIL-CLOSED VENDOR_WHEEL_PLATFORM_MISMATCH
```

**这是正确设计**（宁可明确报错也不静默降级），但意味着 Blender 一旦升级到 Python 3.14，
V2 与 standalone 插件会在 vendor 加载阶段明确失败。届时需要补 cp314+/win_arm64 wheel，
或改为依赖 Blender 自带的 zstandard（当前 5.2.3/5.3.0 均自带 0.25.0，但 V2 选择走 vendor）。

**这不是当前的问题**，是升级前必须记住的前置条件。

## 5. 未覆盖的边界

- **Blender 4.x 未纳入本轮实测**（本机有 4.4.1 / 4.4.3 / 4.5.11 LTS，但用户问的是 5.x）。
  `blender_version_min = 4.2.0` 的声明在 5.x 上成立，4.x 的实际行为未验证。
- **GUI 交互未测**：全部验证为无头（`--background`），面板绘制用 recording layout 断言
  绘制契约而非像素外观。真实 GUI 点击、拖拽、Blender 原生主题下的视觉表现仍需人工确认。
- **真实游戏资产未测**：所有夹具是合成的（最大 16,900 顶点）。真实 Unreal 包解码与
  复杂 shader 视觉一致性仍在 V2 合同里标记为"需人工"。
- **5.3.0 是 Beta**：通过不等于正式版无回归。

## 6. 复现命令

```powershell
# 链路门禁（指定 Blender）
python -B scripts/verify_blender_import_chain.py --blender "<blender.exe>"

# V2 自带三套验收
& "<blender.exe>" --background --factory-startup --python-exit-code 1 --python "scripts/blender_workflow_smoke.py"
& "<blender.exe>" --background --factory-startup --python-exit-code 1 --python "scripts/blender_pose_asset_smoke.py"
& "<blender.exe>" --background --factory-startup --python-exit-code 1 --python "scripts/blender_smoke_v10.py"

# 纯 Python 契约（无需 Blender）
python -m pytest -q
```
