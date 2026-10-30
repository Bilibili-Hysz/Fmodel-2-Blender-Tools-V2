# UEFormat 上游完整格式依据与本项目对照

获取日期：2026-09-15。来源由用户指定为 [h4lfheart/UEFormat](https://github.com/h4lfheart/UEFormat)。本次已实际 clone 仓库，并以 Git 对象原始字节保存格式资料。

固定基线：[d858fe38b2b5b8c63ac4b7481b4386ebfabbf935](https://github.com/h4lfheart/UEFormat/tree/d858fe38b2b5b8c63ac4b7481b4386ebfabbf935)，获取时为 `main` 的 HEAD，提交时间 `2026-08-14T19:44:59Z`。文件格式版本为 **10 / AttributeFormatRestructure**；这是文件版本，不是插件版本。

## 1. 已获取的完整资料

| 上游定义 | 本地原文 | 覆盖内容 |
|---|---|---|
| Generic | [generic.md][generic] | 基础类型、文件头、压缩头、属性容器 |
| UEModel | [uemodel.md][model] | LOD、几何体、骨架、Socket、虚拟骨骼、材质分段、权重、Morph、凸碰撞 |
| UEAnim | [ueanim.md][anim] | 时间元数据、加法动画/参考姿势元数据、骨骼轨道、浮点曲线 |
| UEPose | [uepose.md][pose] | 命名姿势、骨骼变换、曲线名称表、曲线影响 |

以上是该提交 README 列出的全部 Format Specification 文档。另保存了 [format-sources.zip][archive]：包含 **90 个原始文本文件**，覆盖上述文档、Blender 插件源码和 Unreal 插件自有源码，包括当前解析器、DTO、版本枚举和 legacy 解析器。保留上游目录结构，未把第三方压缩库、编译产物、wheel 或图片混入格式参考资料。

[SOURCE_MANIFEST.json][manifest] 逐文件记录上游路径、固定提交 URL、Git blob SHA-1、SHA-256 和字节数，并记录压缩包摘要。所有内容均校验 Git blob 身份。压缩包作为离线参考资料，不替换本项目运行时 Vendor。

“完整”指该固定提交公开的全部格式文档及对应插件实现，包括其保留的历史兼容路径；不表示已收集整个 Git 历史中曾存在的全部格式，也不包含其他仓库自行定义的扩展协议。

## 2. 通用二进制结构

V10 文件按顺序包含：

```text
Magic[8] = "UEFORMAT"
Identifier: FString = "UEMODEL" | "UEANIM" | "UEPOSE"
FileVersion: byte
ObjectName: FString
ObjectPath: FString                  # V10 才加入文件头
IsCompressed: bool
if IsCompressed:
    CompressionFormat: FString      # "GZIP" 或 "ZSTD"
    UncompressedSize: int32
    CompressedSize: int32
Body                                # 解压后为一个 FDataAttributeSet
```

| 类型 | 定义 |
|---|---|
| `FString` | int32 字节长度，随后 UTF-8 字节；规范不要求结尾 NUL，不等同于 Unreal 通用 FString 的全部编码分支 |
| `TArray<T>` | int32 Count，随后 Count 个 T |
| `FVector` | float32 X、Y、Z |
| `FQuat` | float32 X、Y、Z、W；不能直接按 Blender W、X、Y、Z 顺序解释 |
| `FColor` | byte R、G、B、A |
| `FMeshUVFloat` | float32 U、V |
| `FDataAttributeSet` | int32 Count，随后 Count 个属性 |
| `FDataAttribute` | FString Name、int32 ByteSize、ByteSize 字节的 Data |

属性顺序无关；可选属性可能不存在；未知属性按 ByteSize 跳过。Data 可以是结构体、带计数数组或另一个属性集合。V10 **属性头没有 legacy 的 ArraySize 字段**，数组计数在 Data 内。

当前 Windows/常用 x86 实现按小端解释数值；上游 Generic 文档没有单独声明跨平台端序合同，Blender reader 的若干 `struct.unpack` 使用本机格式。应区分文档明确保证与具体实现行为。

依据：原文 Generic、`importer/archive/reader.py`、`importer/import_context.py` 及 Unreal `Private/Archive/UEFormatReader.cpp`。

## 3. UEModel：全部标准属性与结构体

| 层级 | 属性 | 数据类型 |
|---|---|---|
| 根 | `LODS` | TArray&lt;UEModelLOD&gt; |
| 根 | `SKELETON` | FDataAttributeSet |
| 根 | `COLLISION` | TArray&lt;FConvexMeshCollision&gt; |
| LOD | `VERTICES` | TArray&lt;FVector&gt; |
| LOD | `NORMALS` | TArray&lt;FNormal&gt; |
| LOD | `TANGENTS` | TArray&lt;FVector&gt; |
| LOD | `TEXCOORDS` | TArray&lt;FTexCoordEntry&gt; |
| LOD | `INDICES` | TArray&lt;uint32&gt; |
| LOD | `VERTEXCOLORS` | TArray&lt;FVertexColor&gt; |
| LOD | `MATERIALS` | TArray&lt;FMaterial&gt; |
| LOD | `WEIGHTS` | TArray&lt;FWeight&gt; |
| LOD | `MORPHTARGETS` | TArray&lt;FMorphTarget&gt; |
| Skeleton | `METADATA` | FString，解析为 skeleton path |
| Skeleton | `BONES` | TArray&lt;FBone&gt; |
| Skeleton | `SOCKETS` | TArray&lt;FSocket&gt; |
| Skeleton | `VIRTUALBONES` | TArray&lt;FVirtualBone&gt; |

以下按线上的字段顺序列出，完整类型声明见上游原文：

| 结构体 | 字段顺序 |
|---|---|
| UEModelLOD | Name、Attributes |
| FNormal | BinormalSign、Normal |
| FTexCoordEntry | Name、UVs |
| FVertexColor | Name、Data |
| FMaterial | MaterialName、MaterialPath、FirstIndex、NumFaces |
| FWeight | **ushort** Bone、int VertexIndex、float Weight |
| FMorphTarget | MorphName、MorphData 数组 |
| FMorphData | PositionDelta、TangentZDelta、uint VertexIndex |
| FBone | BoneName、ParentIndex、Position、Orientation、**Scale** |
| FSocket | SocketName、BoneName、RelativeLocation、RelativeRotation、RelativeScale |
| FVirtualBone | SourceBoneName、TargetBoneName、VirtualBoneName |
| FConvexMeshCollision | Name、VertexData 数组、int IndexData 数组 |

标准 `MATERIALS` 表达材质名称、路径和索引分段，**没有定义 PBR 参数、图片数据或纹理引用绑定表**。这些能力必须另外追溯扩展定义。Skeleton 是 UEMODEL 的数据块，上游没有单独的 `.ueskeleton` 标准文件。

## 4. UEAnim：全部标准属性与结构体

根属性：`METADATA: FAnimMetadata`、`TRACKS: TArray<FTrack>`、`CURVES: TArray<FCurve>`。

| 结构体 | 字段顺序 |
|---|---|
| FAnimMetadata | int NumFrames、float FramesPerSecond、FString RefPosePath、byte AdditiveAnimType、byte RefPoseType、int RefFrameIndex |
| FTrack | BoneName、PositionKeys 数组、RotationKeys 数组、ScaleKeys 数组 |
| FCurve | CurveName、FFloatKey 数组 |
| FVectorKey | int Frame、FVector Value |
| FQuatKey | int Frame、FQuat Value |
| FFloatKey | int Frame、float Value |

`EAdditiveAnimationType`：None=0、LocalSpaceBase=1、RotationOffsetMeshSpace=2。

`EAdditiveBasePoseType`：None=0、RefPose=1、AnimScaled=2、AnimFrame=3、LocalAnimFrame=4。DTO 中的 MAX 是枚举哨兵，不能当作普通动画语义。

Frame 是与 FramesPerSecond 一起解释的源帧序号。格式本身没有规定必须把源帧号直接用作当前 Blender 场景帧号。上游 Blender 创建器直接使用 `key.frame`；本项目增加了帧率换算与时间 provenance，因此两者的时间应用行为不同。

Float 曲线按名称存储，格式不把它限定为形态键曲线。上游 Blender 导入器将匹配到的曲线应用到 Shape Keys，是消费端策略。

## 5. UEPose：全部标准属性与结构体

根属性：`POSES: TArray<FPoseData>`、`CURVES: TArray<FString>`。

| 结构体 | 字段顺序 |
|---|---|
| FPoseData | PoseName、FPoseKey 数组、FPoseCurveInfluence 数组 |
| FPoseKey | BoneName、Location、Rotation、Scale |
| FPoseCurveInfluence | int CurveIndex、float Influence |

CurveIndex 指向根 CURVES 名称表。UEPOSE 的定义是**命名骨骼姿势与曲线影响**，并非文件里直接保存形态键顶点数据。Blender `create_pose()` 通过骨架变形生成形态键；其他消费端可以采取不同表示。

该提交具有 Blender UEPOSE 解析与创建路径；Unreal 插件没有对应的 Pose reader/factory，不能把“上游格式已定义”写成“两种宿主插件都已实现”。

后续已完成[UEPOSE 设计专项复核](<G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/UEPOSE_DESIGN_REVIEW_2026-09-15.md>)：CUE4Parse 当前转换器只接受加法姿势，为 importer 的 delta 处理提供了背景；但该约定没有完整写在 UEPOSE wire metadata 中，也不能代替用户实际 FModel fork 的验证。形态键适合作为可选烘焙输出，唯一输出设计会丢失姿势可编辑性及组合关系。

## 6. V0–V9 历史兼容定义

当前四份文档主要描述 V10。历史差异必须读取归档中的 `importer/legacy/`、版本枚举和 `create/space.py`，不能将 V10 字段布局套用到所有版本。

| 值 | 上游枚举 | 需要关注的差异 |
|---|---|---|
| 0 | BeforeCustomVersionWasAdded | 最早布局 |
| 1 | SerializeBinormalSign | 法线加入 BinormalSign；此前为三个浮点分量 |
| 2 | AddMultipleVertexColors | 多个命名顶点色集合；此前为单组颜色 |
| 3 | AddConvexCollisionGeom | 凸碰撞能力节点 |
| 4 | LevelOfDetailFormatRestructure | 从扁平模型区块改为根 LODS/SKELETON 等区块 |
| 5 | SerializeVirtualBones | 虚拟骨骼能力节点 |
| 6 | SerializeMaterialPath | 材质记录加入路径 |
| 7 | SerializeAssetMetadata | 动画等资源元数据能力节点；旧动画在 body 开头直接存帧数/FPS |
| 8 | PreserveOriginalTransforms | 保留 Unreal 原始变换；Blender 端按版本执行坐标变换 |
| 9 | AddPoseExport | 姿势导出能力节点 |
| 10 | AttributeFormatRestructure | 属性集合结构、文件头 ObjectPath、骨骼 Scale、命名 UV 等当前布局 |

关键旧布局：

- 旧文件头没有 ObjectPath。
- 旧 body 区块为 `Name: FString / ArraySize: int32 / ByteSize: int32 / Data`，读取到区块流末尾；不是 V10 的根 Count。
- V4–V9 的 LOD 为 `Name / LOD ByteSize / 子区块流`；V10 为 `Name / 属性集合`。
- 旧 UV 集合没有 V10 的逐通道 Name。
- 旧 Bone 不序列化 Scale，解析器填单位缩放；V10 明确读取 Scale。
- 上游旧 Weight reader 使用 signed short；V10 文档和读取函数采用 unsigned ushort。
- legacy 动画及姿势继续使用各自的区块结构；具体字段以冻结 reader 为准。

版本枚举中的能力节点不意味着每个旧 parser 都严格拒绝“版本尚未引入但文件中出现”的区块。不能仅靠枚举名推导额外的拒绝规则。

## 7. 对本项目评审的校准

### 标准、扩展与宿主策略

| 层 | 依据 | 当前项目情况 |
|---|---|---|
| UEFormat 标准 | 本次固定提交的 docs、DTO、reader、legacy | 三种活动格式与 V10 基础结构一致 |
| FModel 定制扩展 | 本项目 Vendor 的 runtime / extensions / fmodel 相关代码；还需以对应导出端验证布局 | `FMODEL_MATERIALS`、`FMODEL_TEXTURES`、`FMODEL_PBR_INTERFACES`、`FMODEL_SKELETON_IDENTITY`、`FMODEL_TARGET_SKELETON` 不在上述上游标准定义中 |
| Blender 实现策略 | create/model、anim、pose、space | 单位与坐标变换、Action/形态键选择、帧率换算、材质节点生成 |
| V2 管理策略 | Pipeline、Assets、core | 扫描、严格身份门槛、恢复、分卷、Link/NLA、缩略图及库元数据 |

标准骨架路径、文件 ObjectPath 和本项目严格身份中使用的 GUID/layout hash 不能混为一谈。“严格身份默认”是本项目 FModel 流程的约束，不是上游标准文件必须携带 `FMODEL_*` 的要求。标准文件如何进入 V2 的兼容模式，应作为明确的产品合同评审。

### 已做的源码对照

[LOCAL_COMPARISON.json][comparison] 记录本次 AST/属性分支比对结果及本地被比较文件的 SHA-256，便于后续源码变化时复核。

- 版本枚举、四个 DTO 文件（model/anim/pose/skeleton）以及坐标转换 `create/space.py`，与上游 **AST 相同**。
- 五个 V10 分派器覆盖了上游全部 21 个属性分支；没有缺失上游属性名称。统计为 Model 根 3、LOD 9、Skeleton 4、Anim 3、Pose 2，同名属性在不同层级分别计数。
- 本项目增加了边界校验、FModel 属性处理、事务、时间换算等逻辑，所以不是对上游原包的逐字复制。
- 分支存在和 DTO 相同只证明结构覆盖，**不能证明字段被完整保留、正确应用或可无损回写**。本次未把静态比对写成完整二进制兼容性认证。

可直接在线检查固定提交的[版本枚举](https://github.com/h4lfheart/UEFormat/blob/d858fe38b2b5b8c63ac4b7481b4386ebfabbf935/plugins/blender/io_scene_ueformat/importer/version.py)、[V10 解析器](https://github.com/h4lfheart/UEFormat/tree/d858fe38b2b5b8c63ac4b7481b4386ebfabbf935/plugins/blender/io_scene_ueformat/importer/deserialize)、[legacy 解析器](https://github.com/h4lfheart/UEFormat/tree/d858fe38b2b5b8c63ac4b7481b4386ebfabbf935/plugins/blender/io_scene_ueformat/importer/legacy)、[Blender 创建器](https://github.com/h4lfheart/UEFormat/tree/d858fe38b2b5b8c63ac4b7481b4386ebfabbf935/plugins/blender/io_scene_ueformat/importer/create)和 [Unreal 解析器](https://github.com/h4lfheart/UEFormat/tree/d858fe38b2b5b8c63ac4b7481b4386ebfabbf935/plugins/unreal/UEFormat/Source/UEFormat/Private/Deserialize)。

### 补充的功能设计边界

1. **加法动画**：标准已经定义 additive/reference pose 元数据。本项目和本次上游 Blender 创建器没有根据这些字段执行完整的参考姿势求值。应明确支持范围、需要的参考资源与无法解析时的提示；不能把“读到了元数据”等同于完整加法动画支持。此处为静态调用检查，未用游戏加法动画样本验证视觉结果。
2. **模型数据保真**：本项目和上游 Blender reader 都没有把 TANGENTS 写入最终切线数据，NORMALS 读取后丢弃 BinormalSign，UV 通道名称也未进入 DTO。它们是已定义的标准字段；应明确哪些用于 Blender 重建、哪些需要保留，而非因 importer 暂未使用就视为格式不存在。
3. **曲线用途**：UEANIM 的浮点曲线不只有形态键一种可能用途。资产管理应记录未知/未应用曲线，避免无声丢弃。此前 F03 的打包关系问题仍成立。
4. **姿势副作用**：此前 F16 是 Blender 创建器的场景恢复问题，与 UEPOSE 二进制定义无关。上游创建器本身也在结束时清零骨骼；本项目应明确要保留还是改变这一行为。
5. **端间差异**：文档将 Morph VertexIndex 写为 uint，Blender/Unreal parser 实际读取 signed int；Unreal 权重读取 ushort 后转入 short。一般小索引不受影响，但不能据此声称整个取值域完全一致。
6. **坐标与缩放**：`create/space.py` 在版本 ≥8 做位置/四元数镜像及 UV 翻转。该文件与本项目 AST 相同；其中 Socket Scale 也走位置缩放路径，属于应另行验证的实现语义，不能当作 wire format 要求。

此前 17 项问题主要针对管理层交接及真实 Blender 行为，本次未因上游定义而撤销它们。评审中的“格式本身”“FModel 扩展”“Blender 创建策略”应按本文件重新归属。上游插件可作为实现参照，但其局限不能自动成为本项目的正确性标准。

[generic]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/references/ueformat-upstream/d858fe38b2b5/docs/generic.md>
[model]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/references/ueformat-upstream/d858fe38b2b5/docs/uemodel.md>
[anim]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/references/ueformat-upstream/d858fe38b2b5/docs/ueanim.md>
[pose]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/references/ueformat-upstream/d858fe38b2b5/docs/uepose.md>
[archive]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/references/ueformat-upstream/d858fe38b2b5/format-sources.zip>
[manifest]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/references/ueformat-upstream/d858fe38b2b5/SOURCE_MANIFEST.json>
[comparison]: <G:/Blender/Blender Scripts Github/FModel-2-Blender-Tools-V2/docs/references/ueformat-upstream/d858fe38b2b5/LOCAL_COMPARISON.json>
