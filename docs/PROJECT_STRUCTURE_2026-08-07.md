# FModel 2 Blender Tools V2 项目结构

更新时间：2026-08-13
项目根目录：`F:\Blender Scripts Github\FModel-2-Blender-Tools-V2`

## 当前合同（2026-08-13）

> 本文已按当前 V2 工作树修订。当前合同以本节、`README.md`、
> `README_ZH.md` 和 `docs/FOLDER_ONLY_PIPELINE_2026-08-13.md` 为准；
> 旧的 UESCENE、Indexed Project 和旧格式入口仅作为迁移历史保留。

## 1. 当前运行时结构

```text
FModel-2-Blender-Tools-V2/
├── __init__.py                    # Blender 插件入口，版本 8.1.0
├── ui.py                          # FModel 用户界面、工具箱和 GameProfile
├── auto_handlers.py               # 生命周期、Vendor headless 注册和自动扫描
├── core/                          # 活动核心合同、导入会话、事务和恢复
│   ├── ueformat_bridge.py         # UEFormat 能力发现和兼容性桥
│   ├── ueformat_identity_reader.py
│   ├── skeleton_provenance.py
│   ├── vendor_loader.py           # 内置 Vendor 加载、缓存、锁和生命周期
│   ├── import_session.py          # 统一导入结果和 Scene 报告
│   ├── import_transaction.py      # datablock 追踪和 rollback
│   ├── library_files.py           # 资产库发布、事务和恢复
│   ├── multi_import.py            # 多任务/多骨架导入计划
│   └── context.py / types.py / resume.py
├── Fmodel/                        # FModel 业务层
│   ├── importers/                 # 当前只注册 UEFormat model/anim/pose
│   ├── Pipeline/                  # Folder-only 扫描、导入、资产库和 checkpoint
│   └── project_assets.py          # 旧资产/索引迁移兼容代码，不在启动链激活
├── Assets/                        # 动画/模型资产库 UI 和操作
├── Universal_tools/               # 材质、Shader、骨架、网格和动画工具
├── vendor/                        # 运行时 Vendor，仅保留 io_scene_ueformat
│   ├── io_scene_ueformat/         # FModel-aware UEFormat 1.2.0
│   ├── vendor_manifest.toml       # wheel/hash/capability/license 锁定清单
│   ├── vendor_tree_manifest.json  # release tree/逐文件 hash 清单
│   └── README.md                  # Vendor 来源和刷新说明
├── Shaders/                       # Shader 资源和 GameProfile 数据
├── scripts/                       # 同步、Vendor 完整性和构建辅助脚本
├── investigation/                 # 手动/真实 Blender 验证脚本，不属于运行时
├── backups/                       # 本地回滚快照，不打包到 release
└── docs/                          # 设计、迁移、Vendor 和结构文档
```

## 2. 当前激活功能

- `.uemodel` 模型、骨架、Native FModel 材质和贴图导入；
- `.ueanim` 动画导入；
- `.uepose` 姿态导入；
- Folder-only Animation Pipeline，Step 3 直接扫描 `.ueanim/.uepose`；
- 严格骨架身份校验和显式按名称兼容策略；
- 多骨架映射、断点恢复、任务事务和资产库构建；
- 动画/模型资产库、缩略图、NLA 应用和模型预览；
- 材质手动修复、Shader 管理、骨架/网格/动画工具；
- 内置 UEFormat Vendor 的 headless 注册和 Last Operation 报告；
- 世界场景继续使用 FModel 官方 USD 导出/导入路线，V2 不再维护 UESCENE。

Indexed Project、Project Root、SQLite 索引和后台索引维护不属于当前活动 UI 或执行路径。

## 3. 明确保留但不激活的迁移代码

以下内容仍在源码树中，但当前不被注册、同步脚本会排除，或仅用于旧 `.blend` 迁移：

```text
Fmodel/importers/model/operator_model_gltf.py
Fmodel/importers/model/operator_model_psk.py
Fmodel/importers/animation/operator_anim_psa.py
Fmodel/importers/export/operator_export_gltf.py
Fmodel/importers/export/operator_export_fbx.py
Universal_tools/mesh/scheme_psk_auto.py
Universal_tools/mesh/scheme_psk_mesh_fix.py
core/project_index.py
core/project_index_service.py
core/project_index_worker.py
Fmodel/Pipeline/project_index_controller.py
```

这些内容不属于当前 UEFormat-only 安装载荷，也不应被视为产品支持入口。`vendor/io_scene_psk_psa/` 已经删除。

## 4. 本地生成物处理

以下目录属于测试、构建或本地工具生成物，已清理并加入忽略规则：

```text
__pycache__/
.pytest_cache/
.ruff_cache/
_pipeline_tmp/
investigation/task7_verification/output/
```

以下目录暂时保留，不属于 release source，但可能用于恢复或本地工具：

```text
.mimocode/
backups/
```

## 5. 发布前剩余事项

1. 使用 `scripts/generate_vendor_tree_manifest.py --check` 验证 Vendor release tree；
2. 在 Blender 5.2 真实环境验证 Vendor 启动、模型材质/贴图、动画、姿态和 Folder-only 打包；
3. 保持 `io_scene_ueformat` 作为当前 headless Vendor 包名，除非另有独立迁移合同；
4. 继续将旧格式源码保留在历史窗口，但不进入同步载荷；
5. 生成只包含 UEFormat Vendor 的 release archive；
6. 保持工作树分层提交，避免把测试缓存、备份或本地工具带入 release。
