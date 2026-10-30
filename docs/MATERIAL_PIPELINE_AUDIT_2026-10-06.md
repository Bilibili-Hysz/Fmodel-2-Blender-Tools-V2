# FModel → Blender 材质与贴图管线审计（2026-10-06）

## 结论

**当前管线不能认定为完整正确。** 文件定位、记录解码和普通图像加载基本可靠；真实 Blender 测试复现 4 项问题，其中 2 项会读到错误的贴图数据或颜色解释，1 项会把模型建库保存成仅骨架，另 1 项阻断合法的共享贴图导入。当前 R67 候选仍受这些消费者侧问题影响。

**Blender 中会改显示名。** `T_Same.png.001` 和 Windows 的 `T_ORIG~1.PNG.001` 已实测出现。PNG 的磁盘文件名没有被导入器改写；绑定主要依赖纹理 ID、URI、文件长度及 SHA-256，不依赖图像显示名。

本轮完成审计与复现，**未修复产品代码，也未修改 R67 编译包或安装的 Blender**。完整机器证据：[audit-summary.json](D:/~Vibe-Coding/Projects/Fmodel_Edited_UEformat/docs/current/evidence/2026-10-06/material-pipeline-audit/audit-summary.json)。

## 实测范围

- FModel `9ac22ce5` / CUE4Parse `5c158cc16e` / canonical UEFormat `960f87db`。
- 真实 Blender **5.2.1 LTS**，`9e2066aef7ef`。factory-startup、独立配置目录；测试的是实际 canonical 和 V2 Vendor 导入器，不是 bpy mock。
- 实际 R67 projection/stable-ID/V10 writer 生成 **18 个受控模型文件**，覆盖 NONE/GZIP/ZSTD、同名不同目录、线性 BaseColor、相同路径重新导出、长中文文件名、ExportRoot 和跨 Engine 挂载点。
- 两个导入器各 **13 项正向检查通过**；另外比较 **18 组实际解码像素**与 PNG 编码值，均一致。两个 importer 一致复现 MAT-01/MAT-02；实际 V2 保存函数复现 ASSET-01。
- 可读的 Blender 5.2 安装目录中，相关材质模块与本地 canonical/Vendor 文件哈希相同；安装版 `save_model_logic` 的 AST 语义摘要也相同。这不等于断言当前已打开的 Blender 会话具体选中了哪一份模块。
- 旧真实游戏审计所指的导出目录目前不可读；本轮没有执行新真实游戏导出、原始 Unreal 纹理解码或 GUI 渲染视觉比较。受控 PNG 测试不能代替这三项。

## 管线逐段检查

| 阶段 | 当前实现与结论 |
| --- | --- |
| Unreal 参数提取和主图选择 | CMaterialParams2 / lazy refs → 精确名称优先级与辅助图排除。R67 的 Base_ORM/Base_SRO/ORM 回归已保留；本轮读取选择实现。对具体游戏是否选择了原 shader 的目标图，仍需实际材质候选/UE 预览核对。 |
| 贴图编码与真实导出路径 | TextureExporter 实际返回 extension、mip/layer suffix 和 bytes；projection 使用同一队列 exporter 的预览缓存，记录真实长度与 SHA。URI resolver 重现 SavePath；交付端验证资源。不会仅按猜测的 .png 后缀拼路径。 |
| V10 绑定 | MaterialId + MapKind → stable TextureId → URI、长度、SHA。stable ID 由源身份与 URI 构造。它是定位标识，不是 Blender 图像名字。 |
| 原始参数名称 | 当前 identity-only binding 不保存 ParameterIdentity 或完整纹理 SourceIdentity；consumer 创建 FMODEL_TEXTURES.BaseColor/Normal/Orm 参数名。原 `Diffuse`、`NormalMap`、`Base_ORM` 名称不能直接从这份绑定恢复。 |
| 资源解析 | 根目录约束、URI 格式、长度/SHA 校验有效。错误文件及缺图被拒绝，创建数据回滚；不会按同名图片随便补位。自动根目录范围有 PATH-01 缺口。 |
| Blender 图像加载 | 普通外部图片和同名不同目录正确；复制保护颜色解释和外部 ownership。已有 packed image 的重用出现 MAT-01。 |
| 颜色空间和节点 | Normal/ORM 的 Non-Color、G→Roughness、B→Metallic 实测正确。linear BaseColor 有 MAT-02；ORM 红通道没有接 AO；identity bindings 固定 UV0。 |
| 资产库保存 | 实际保存函数有 ASSET-01。当前普通图片保持外部文件引用；即使正确写入网格库，也不是固定纹理快照。 |
| 资产库重新链接 | 使用 libraries.load(link=True)，没有重新运行 UEFormat 的 URI/SHA 校验。外部 PNG 改动后，旧库会读到新像素，旧材质 metadata 哈希仍保留。 |

## 已复现问题

### MAT-01 / P1：通过磁盘校验后仍可能读到旧 packed image

触发条件：场景已有同一路径的旧已打包图像，FModel 重新导出该路径的 PNG，新的 .uemodel 也携带了新文件的正确 SHA。

[加载代码](<G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/vendor/io_scene_ueformat/importer/runtime_material_images.py:155>)先验证磁盘文件，然后 `images.load(check_existing=True)` 找到旧 packed datablock，再 `copy()`。验证的文件与交给 shader 的内嵌字节因此可以不是同一版本。

实测：新文件应为绿色，SHA `14ae499006cecabe8ac6ba802c2d8984e88816822aff48d5d96619818e97803f`；导入后的 image 仍为红色且 packed，内嵌 SHA 是旧的 `185f4513296f7160c891a7b482a9ab7a000d3e3dd3031398665ad364fc32da23`。foreign 图像未被修改，但它的旧内容被复制到了新材质。

普通未打包缓存图像在此测试中正确读到了新绿色版本，不能把问题扩大为所有 check_existing 缓存都必然过期。

修复方向：为已验证的外部资源解码本次文件字节，避免直接复制未经内容验证的已有 packed image；按内容和实际颜色解释缓存。**同步给 material build profile 加实现版本或验证旧缓存节点**，否则之前误标为新 SHA 的错误材质仍可在 `_find_matching_material` 中被重用。

### MAT-02 / P2：忽略线性 BaseColor 的 is_srgb

producer 写入 `is_srgb=false`，adapter 保留 `color_space=0`。但[选择颜色解释](<G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/vendor/io_scene_ueformat/importer/runtime_material_builder.py:518>)只看 semantic，BaseColor 总是 COLOR；[图像设置](<G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/vendor/io_scene_ueformat/importer/runtime_material_images.py:180>)又把 COLOR 强制设为 sRGB。

实际 PNG 文件选对了，实际 image.colorspace 却与线性声明不符。这会影响 shader 的颜色解码，不能通过重命名解决。

修复方向：统一 source color_space、用途和 Blender 颜色管理配置的决策。线性颜色、sRGB 颜色及数据图需要不同的实际解释/缓存身份；预校验、cache identity 和节点加载必须使用同一规则。

### ASSET-01 / P1：建库只保存骨架，漏掉网格、材质和图像

[保存函数](<G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/Assets/Models/operators.py:102>)只选中 armature 并 duplicate，再遍历 `dup_arm.children`。这个复制操作没有把原子网格复制到新骨架下。

实际调用保存函数，返回 True；源骨架有 1 个子网格，JSON 写 `mesh_count=1`。生成的 .blend 却只有 `AuditModel__AuditRig`，meshes/materials/images 全为空。向导 Step4 会调用同一函数，因此“保存成功”并不代表保存了角色外观。

修复方向：收集并复制完整模型层级，重定向 armature modifier/parent 到副本，保留网格材质与图像依赖；避免修改原场景共享 material 的名字。写入后必须检查实际 mesh/依赖内容，而不是只检查 .blend 或 JSON 是否存在。

### PATH-01 / P2：自动资源根过窄，合法数据无法导入

[根目录帮助函数](<G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/core/ueformat_bridge.py:333>)给 V2 设置的是 `<Exports>/<Project>`。默认 importer 对 relative URI 的推断更窄，到 `<Project>/Content`。

两个真实 Blender 条件测试：

1. 合法 ExportRoot URI `AuditGame/Content/Textures/...`：standalone 不传 root 时推断成功；V2 的 project root 会重复拼 AuditGame，报找不到文件。
2. 当前 RelativeToOwner URI `../../../Engine/Content/Textures/T_Same.png`：纹理确实在同一 Exports 下的 Engine 挂载点，default importer 与 V2 自动 root 均拒绝；明确指定整个 Exports 根后，同一份文件和 SHA 成功导入。

正常项目 Content 内的 `../Textures/...` 已通过，不能泛化为当前默认路径都失败。

修复方向：使用已知导出任务的完整 Exports 根，或让模型导入明确指定/核对资源根。保持 resolver 的 containment 校验，不能用扩大到磁盘根、按 basename 全盘找图等方式绕过。

## 会怎样改名

| 名称 | 实测变化 | 是否用它找图 |
| --- | --- | --- |
| PNG 的磁盘文件名 | 导入器未改写 | 使用 URI 定位 |
| Blender Image.name | 同名样本变成 T_Same.png.001/.002/.003 | 否 |
| Windows 长中文 PNG 的显示名 | T_Original_...角色服装贴图...png → T_ORIG~1.PNG.001；因为当前 Windows 加载辅助函数采用了 8.3 短路径 | 否；fmodel_source_path 保留长物理路径 |
| 原材质参数名 | 转成 FMODEL_TEXTURES.BaseColor/Normal/Orm 节点名 | 按 MapKind 接线 |
| 材质显示名 | importer 使用源材质名 + (FModel)，Blender 可加冲突后缀；建库另有模型前缀逻辑 | 当前重用按来源 metadata/内容 cache，不按显示名 |

手动把 Image.name 改成 `UserRenamed_Texture_Display`，再次导入仍绑定同一张正确图片。改显示名本身不会换图。改磁盘 PNG 文件名或目录会破坏 URI；替换文件内容会触发长度/SHA 检查。

建议后续让 image 使用长文件名作为显示名，并附上 `fmodel_texture_id`、logical URI、expected SHA 与实际颜色解释。展示友好名称，追踪时使用身份与内容校验；不要把 UI 名字当文件键。

## 资产库图片是否自足

额外的跨进程测试，直接保存一份含网格/材质/图片的库以隔离 ASSET-01：图片 packed=false；保存时 BaseColor 红色。只把源 PNG 换成绿色，第二个 factory Blender 链接旧 .blend 就显示绿色，材质中的旧 cache identity/hash 不变。

这是当前外部引用策略的实际结果。库对原 PNG 路径仍有依赖；源目录移动、删除或重导出后，旧库并不是固定快照。应明确选择复制/嵌入经验证的纹理，或维护受管理的资源根和加载时校验策略。

## 材质效果边界

当前三图 identity projection 可以定位 BaseColor/Normal/ORM。**它没有完整还原 Unreal 材质图**：已实测 ORM 的 AO 红通道未接线；所有绑定使用 UV0，未传达 shader 中的贴图 UV 通道/缩放/旋转；多层/Texture2DArray 等还需要具体材质规则。正确文件和正确两条 ORM 接线并不能证明整套 shader 的视觉一致性。

法线的 Non-Color 和 NormalMap 节点路径已检查；具体游戏的切线、绿通道约定和视觉方向，本轮没有真实资产渲染证据，不作通过结论。

## 建议修复顺序与资格门禁

1. MAT-01：fresh verified external decoding + cache profile 更新；验证 packed 旧图、plain 缓存和跨 COLOR/DATA 使用。
2. MAT-02：按线性/sRGB 声明统一解释与缓存；验证 PNG/HDR 及已有材质缓存迁移。
3. PATH-01：传递/提供完整导出根，覆盖 project/Engine/插件挂载点和旧 ExportRoot；保持范围约束。
4. ASSET-01：完整层级复制与实际库内容验收，同时明确外部纹理/自足快照策略。
5. 使用一组新的真实游戏导出，逐个核对选图参数、URI、磁盘 SHA、Blender 实际来源、颜色空间和 shader 接线，再比较 UE/FModel 与 Blender 外观。

以上 4 项仍未修复。当前可靠的结论是“普通受控文件定位与加载可通过，命名变化不决定绑定”；尚不能把 R67 配套材质/建库管线标成完整正确。
