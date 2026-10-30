# R68 材质管线与模型库修复 — 2026-10-06

## 交付

- V2 安装包：`Release/FModel_Tools_V2_R68_2026-10-06.zip`，SHA-256 `e59de71e6eaac03d0b56c4084d3fda6a9d66fc578469f47319784f1c440e20d0`。
- 整合候选包：`Release/FModel_Edited_UEformat_Release_2026-10-06_NEWPIPELINE-CANDIDATE-R68_Windows-x64.zip`，SHA-256 `aa98252df92dffa4de9d30534621cc2075885aaea5ab7acb4ae5724ad586720b`。
- 独立 UEFormat 安装包：整合候选包内 `io_scene_ueformat.zip`，SHA-256 `eb9de7f1d95f2012617240effd1e5ced08b59058b660f190adfc87597d3eb1dd`。
- FModel.exe 精确复用 R67 已审计编译，SHA-256 `f096c643711e065f38f9eb7d9ff88a187c2f82ead1e28b466961f84bac11be3c`。本轮没有重新编译 FModel/CUE4Parse。
- canonical addon 提交 `921cd07c1d2d059b193d0491000067fc50e37dab`；V2 保留用户原有工作区修改，以工作区快照打包，未提交或重置其原有修改。

## 修复结果

| 项目 | 现在的行为 |
| --- | --- |
| 同路径旧 packed image | 校验文件后新建导入器自有图片，避免复制旧内嵌内容；原有用户图片保持原状。 |
| 颜色解释 | 颜色用途结合源标记选择 sRGB 或 Linear Rec.709/Linear；法线、ORM 等数据用途使用 Non-Color。缓存区分有效解释。 |
| 旧材质缓存 | build profile 增加 implementation_version=2；重新导入时重建旧版本材质。 |
| 中文长文件名 | 从原始长路径设置 Image.name，Windows 加载仍保留短路径兼容。稳定 ID、URI、SHA 和原路径单独记录。 |
| 资源根 | `<Exports>/<Project>/Content` 推导完整 `<Exports>`，允许同一导出树中的 Engine/插件挂载；继续拒绝越界和错误长度/SHA。 |
| 模型建库 | 复制完整后代层级及通过 Armature modifier 绑定的未父子关联网格；重绑 parent、modifier、constraint 和对象 driver 引用。 |
| 材质与图片依赖 | 复制材质及嵌套节点组，在副本中内嵌验证过的图片；源场景材质名、图片与选择/active 保持原状。 |
| 成功判定 | 写入临时库后检查集合、对象、网格、材质、图片的实际库目录，成功后发布；异常清理自有副本和临时文件。 |

中文长名实测显示为：

`T_Original_Very_Long_BaseColor_Texture_Chinese_角色服装贴图_Test_1234567890.png`

不再显示为 `T_ORIG~1.PNG.001`。同名 datablock 的 `.001` 后缀仍可能由 Blender 正常添加，查找和绑定不依赖显示名。

## 验证

- canonical 当前 V10 回归：80 passed。
- V2 全部测试：559 passed，51 subtests passed。
- 真实隔离 Blender 5.2.1：canonical 44 + Vendor 44 = 88 项断言全部通过。
- NONE/GZIP/ZSTD 来自 R67 实际 projection/stable-ID/writer 的受控 PNG 测试文件；覆盖同名不同资源、ORM 通道、法线、旧材质迁移、旧 packed image、长中文名、Content/ExportRoot/Engine 跨挂载。
- 模型库实际重新 link：网格/嵌套层级/骨架 modifier/材质/图片完整，嵌套节点组图片也为副本。外部 PNG 改动后库内保留原已验证像素。
- 贴图在导入后被修改会拒绝保存，保留原库。注入发布失败后无临时 datablock 或文件残留，选择与 active 恢复。
- R68 V2 ZIP 在独立 Blender 用户目录执行安装、启用、延迟恢复、保存状态恢复、禁用、再次启用，全部通过。
- ZIP CRC、包与目录逐文件 SHA、当前指针与源码锁以 `r68-closeout-verification.json` 为最终验收记录。

证据：`docs/current/evidence/2026-10-06/material-r68/`。

## 使用与边界

安装新的 V2 ZIP 并重启 Blender，然后重新导入受影响模型，重新保存曾经遗漏依赖的模型库。旧文件中已错误缓存或缺失的内容不会因安装 ZIP 自动修复。

模型库采用内嵌贴图快照；若 FModel 贴图内容在导入后改变，先重新导入再保存。FILE/GENERATED 图片受支持，MOVIE/SEQUENCE/TILED 图片明确报错。

本轮未同步用户已安装插件或修改打开的 Blender 会话；没有实际 Unreal 包解码、真实游戏导出或最终游戏 shader 视觉一致性验收。V10 wire、MapKind、extension envelope、URI 和骨架身份协议保持不变。
