# UEPOSE 转形态键：设计适用范围与纠偏建议

日期：2026-09-15。依据：已获取的 UEFormat 官方 V10 定义、本项目当前源码、上游 Blender 创建器、CUE4Parse 导出链，以及独立 Blender 5.2.1 实测。

后续修正（2026-09-16）：V2 的 UEPOSE 入口已改为保存完整源数据，并提供姿势资产面板、骨骼应用、多网格可选烘焙及源库保存/加载。见[当前使用流程](<G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/UEPOSE_WORKFLOW_2026-09-16.md>)。下文保留修改前的评审证据；Vendor 旧 `create_pose()` 本身未修改，V2 新入口使用源记录和独立求值路径。

## 1. 判断

**当前设计存在范围失误：将 UEPOSE 烘焙为形态键可以是有用的输出方式，但不能作为通用姿势导入的唯一持久化表示。**

若明确目标是把加法面部姿势烘焙成指定网格上的表情滑块，该路线有合理性。对本项目“FModel 导入管线及动画管理”的范围，当前实现还需要支持源姿势保存、可编辑骨骼姿势、曲线关联和完整结果报告。把上游创建器的网格烘焙策略直接提升为产品唯一合同，限制了这些能力。

此前模块评审中“独立于 Action 打包是合理的”应收窄为：**姿势不必伪装成普通时间序列动画，但应有明确的姿势资产表示和管理入口，不能因不打包成动画 Action 就只剩下网格烘焙结果。**

这不是仅根据格式字段作出的判断。下文列出了可复现的实际功能损失，也保留了导出语义尚未确认的边界。

## 2. 上游为什么会走这条路线：事实与推断

### 已确认事实

- UEFormat V10 UEPose：`PoseName + Bone Keys + Curve Influences`；没有形态键顶点数组。
- Bone Key 包含骨名、位置、旋转、缩放；曲线通过 CurveIndex 引用名称表。
- UEFormat 上游 Blender `create_pose()` 将骨骼姿势应用到网格，然后调用 `modifier_apply_as_shapekey`；曲线部分匹配既有 Shape Keys，再从混合结果生成形态键。本项目继承了这条主路线。
- UEFormat README 指向的 CUE4Parse 当前导出链中，`PoseAssetConverter.TryConvert()` 明确执行 `if (!poseAsset.bAdditivePose) return false;`。它仅将加法姿势转换为 `CPoseAsset`；UEPose writer 随后写出位置、旋转、缩放及曲线影响。
- CUE4Parse writer 会忽略绝对值小于 `0.001` 的曲线影响。导出本身已有取舍，不能要求 Blender 凭文件恢复从未写出的信息。

这解释了为什么 importer 中 Scale 使用 `1 + deltaScale` 具有导出端背景。**不能未经核对就把 Scale 当成绝对缩放，改为直接赋值。**

### 不能从现有证据推出的结论

- 不能证明上游作者最初只为面部设计；该用途是从实现行为推断，未取得作者设计声明。
- 不能将上述 CUE4Parse 当前提交视为用户具体 FModel 版本的导出实现。本项目使用定制扩展，实际 fork 的变换语义仍应核验。
- UEPose 标准未提供像 UEAnim 那样的 additive/reference-pose 元数据。文件中的数值如何相对目标参考姿势应用，需要结合已知导出约定；不能声称任意 `.uepose` 都能无条件重建完整 Unreal 求值行为。

固定依据：[UEFormat 格式基线][format-reference]；[CUE4Parse Converter](https://github.com/FabianFG/CUE4Parse/blob/6253cb0f919d24bb80e17108ccf3d2a94a1b98c3/CUE4Parse-Conversion/Legacy/PoseAssetConverter.cs)；[UEPose Writer](https://github.com/FabianFG/CUE4Parse/blob/6253cb0f919d24bb80e17108ccf3d2a94a1b98c3/CUE4Parse-Conversion/Writers/UEFormat/UEPose.cs)。4 个导出端文件已按 Git blob 校验并保存于[来源清单][exporter-manifest]所指的离线包。

## 3. 实际检查结果

使用独立 `--background --factory-startup` 进程构造骨架、网格、Pose DTO 和标准 V10 UEPOSE 文件。未操作用户编辑场景。[观察脚本][probe]、[机器可读结果][evidence]。

| 场景 | 实际结果 | 设计含义 |
|---|---|---|
| 骨架有 3 个骨骼，没有网格，导入骨骼姿势 | `AssertionError` | 姿势消费被强制依赖网格，无法作为独立骨架姿势导入 |
| 同一骨架下有 2 个受其驱动的网格 | 仅第一个子网格获得 `BonePose`；第二个没有 Shape Keys | 多部件角色不完整，选中哪个网格也不能明确控制目标 |
| 姿势仅作用于有效但无权重的 helper 骨骼 | 函数正常返回，只有 Basis，没有 Action | 网格无变形不等于源姿势无信息；用于挂点或其他控制的变换没有留下 |
| 一个姿势同时含骨骼变换和 Smile=0.75 曲线 | 输出 `Expression` 与 `curve_Expression` 两个独立形态键；该标准样例的网格及 Key datablock 没有记录源组合关系 | 一个源姿势不再对应一个完整可应用操作；拆分后缺少联动控制及来源映射 |
| 标准 UEPOSE 只含未匹配到形态键的曲线 | 实际 V2 operator 返回 `FINISHED`，报告“成功加载 1 个”；场景只有 Basis，没有 Action | 以“未抛异常”为文件成功条件，掩盖了零有效输出 |

前四组主要直接验证 Vendor 创建器；最后一组走真实 V2 operator、wire parser、DTO 和创建器，显式使用按名称兼容模式。Curve-only 文件是标准布局的合成输入，**不声称上述 CUE4Parse exporter 一定会生成它**；该 exporter 还检查骨骼轨道数量。

此前已验证的 F16 仍存在：成功生成形态键后，角色原有骨骼姿势被清零。重复导入会产生自动后缀，不会按源姿势身份更新旧结果。

定位：

- [网格选择函数][mesh-selection]只返回第一个直接子 Mesh，不按绑定 modifier 全量查找，也不提供目标网格选择。
- [UEPOSE 创建入口][pose-create]断言必须存在 Mesh。
- [权重过滤][weight-filter]会跳过不能影响目标网格的骨骼。
- [曲线合成][curve-bake]只处理匹配到的 Shape Keys，并另建输出。
- [operator 成功统计][success-count]在 importer 返回后直接计为成功，没有检查生成姿势、骨骼、曲线或形态键数量。

## 4. 烘焙不能替代骨骼姿势表示的原因

烘焙保留的是“某个网格在某次求值后的顶点结果”。骨名、局部变换、未影响顶点的控制、曲线原始用途及参考姿势关系不再由结果本身表达。源文件仍在磁盘不等于资产管理层已经保存了可用的姿势结构。

形态键还绑定特定顶点数量和顺序；换 LOD、换拓扑或拆分角色部件后，不能像骨骼姿势那样按骨名重新应用。两者的混合行为也不等价：二维点从 `(1,0)` 经骨骼旋转 90° 到 `(0,1)`，形态键权重 0.5 给出 `(0.5,0.5)`，而旋转 45° 给出约 `(0.707,0.707)`。这是插值表示的差别，不是调整导入比例即可修复的问题。

对表情制作，这些取舍可能是用户主动需要的；对通用姿势库，它们应该是可选择的派生输出约束。

## 5. 建议调整后的功能设计

### 先保留姿势数据，再选择如何应用

建议建立独立的 `PoseAsset` 数据层，至少保存：

- 源文件标识、内容版本、ObjectPath、命名姿势及其稳定条目标识；
- 每个姿势的骨骼局部变换、曲线名称/影响及未映射项；
- 源坐标系、单位、导出器/约定版本，以及已确认的 additive/参考姿势解释；
- 已有 FModel 扩展身份（若有），以及应用/烘焙目标的版本；
- 派生 Action、形态键与源条目的关联。

原始数值与规范化/求值后的数值应可区分，防止重复坐标转换或把加法 delta 误当绝对姿势。未知语义允许导入并保存源结构，应用前明确显示缺少的解释条件。

### 两类派生输出

| 输出 | 用途 | 必须满足的合同 |
|---|---|---|
| 骨骼姿势资产 | 姿势预览、应用、混合、编辑和角色复用 | 不要求网格；解析加法与参考姿势关系；保留曲线；应用可撤销、预览可恢复 |
| 网格形态键烘焙 | 表情滑块、固定拓扑的形变库 | 用户指定目标网格；处理骨骼与曲线组合；记录损失及来源；可按源身份更新 |

Blender 侧可用单帧 Action/Pose Asset 表达已经正确求值的骨骼姿势；但它是源姿势的派生表示，不能直接把所有原始 delta 写进单帧 Action 就宣布转换完成。对象骨骼通道和 Key 曲线属于不同 ID，需显式使用合适的 slots 或关联 Action，并记录一键应用时的组合关系。

本项目的资产管理应增加 `kind=pose` 的发现、来源、搜索、缩略图与应用语义。UEPOSE 可以继续通过独立导入器处理，无需硬塞进“带 FPS 和时长的动画片段”流程；但应能在资产层管理，而不是扫描后只能去网格数据里寻找结果。

### 烘焙结果要可核验

返回结构化结果，至少区分：读取到的姿势数、生成的姿势资产数、每个目标网格生成/更新的形态键数、跳过的骨骼/曲线及原因、警告与失败。零有效输出不能报告“成功加载姿势资产”。

单个源姿势的骨骼与曲线贡献应提供一次完整应用；若确实拆成多个派生通道，必须保存关联并提供统一控制。使用临时求值目标计算最终形变，明确 modifier 顺序、已有形态键和约束的处理，成功/失败均恢复原场景状态。

## 6. 建议实施顺序与验收

1. **先纠正结果报告和状态副作用**：零输出/部分输出、目标网格范围、F16 的姿势恢复。避免继续生成用户误以为完整的资产。
2. **保存源姿势与曲线结构**：提供明确返回类型和来源关联；验证保存、重新打开文件后数据仍可用。
3. **实现骨骼姿势应用与资产浏览**：先以用户实际 FModel exporter 和真实样本校准加法、参考姿势、缩放及 reorient 语义。
4. **完善可选烘焙**：多网格、组合姿势、重复导入更新、LOD/拓扑变化的失效规则。

验收样例至少覆盖：骨架无网格、辅助骨骼、双网格角色、骨骼与曲线混合、未识别曲线、已有角色姿势、重复导入、不同 LOD、保存后重载。应用正确性需以真实 UE/FModel 样本比较；不能仅验证“有形态键”或“有 Action”。

本次仅完成设计审查及验证材料，没有改动导入行为、当前产品合同或 Vendor。建议修正的是唯一输出与信息保留的设计；不能在未确认导出语义时直接删掉原烘焙路线。

[format-reference]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/UEFORMAT_UPSTREAM_FORMAT_REFERENCE_2026-09-15.md>
[exporter-manifest]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/references/uepose-exporter/6253cb0f919d/SOURCE_MANIFEST.json>
[probe]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/compose/reports/2026-09-15-uepose-design-probe.py>
[evidence]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/compose/reports/2026-09-15-uepose-design-evidence.json>
[mesh-selection]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/vendor/io_scene_ueformat/importer/utils.py:77>
[pose-create]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/vendor/io_scene_ueformat/importer/create/pose.py:39>
[weight-filter]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/vendor/io_scene_ueformat/importer/create/pose.py:115>
[curve-bake]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/vendor/io_scene_ueformat/importer/create/pose.py:150>
[success-count]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/Fmodel/importers/animation/operator_anim_ueformat.py:319>
