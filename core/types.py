# FModel_Tools/core/types.py
# 数据类型 — PipelineConfig、DictionaryManifest 等

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from .enums import (
    UEFORMAT_SOURCE_TYPE,
    UEFORMAT_IMPORT_MODE,
    SKELETON_COMPATIBILITY_STRICT,
    normalize_skeleton_compatibility_mode,
)


# ============================================================
# 动画条目
# ============================================================

@dataclass
class AnimationEntry:
    """动画条目 - 最小化的动画信息单元"""
    original_name: str = ""
    hashed_name: str = ""
    source_file: str = ""
    content_hash: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AnimationEntry':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================
# 文件夹节点
# ============================================================

@dataclass
class FolderNode:
    """文件夹节点 - 树形结构的基本单元"""
    path: str = ""
    animations: Dict[str, AnimationEntry] = field(default_factory=dict)
    subfolders: Dict[str, 'FolderNode'] = field(default_factory=dict)
    
    def add_animation(self, name: str, entry: AnimationEntry):
        self.animations[name] = entry
    
    def get_or_create_subfolder(self, name: str) -> 'FolderNode':
        if name not in self.subfolders:
            self.subfolders[name] = FolderNode(path=name)
        return self.subfolders[name]


# ============================================================
# 骨架节点
# ============================================================

@dataclass
class SkeletonNode:
    """骨架节点 - 代表一个骨架及其所有动画"""
    name: str = ""
    folders: Dict[str, FolderNode] = field(default_factory=dict)
    
    def get_or_create_folder(self, path: str) -> FolderNode:
        """获取或创建文件夹路径"""
        if not path or path == ".":
            if "" not in self.folders:
                self.folders[""] = FolderNode(path="")
            return self.folders[""]
        
        parts = path.split('/')
        current = self.folders
        for part in parts:
            if part not in current:
                current[part] = FolderNode(path=part)
            folder = current[part]
            current = folder.subfolders
        return folder
    
    def collect_all_animations(self, parent_path: str = "") -> List[Tuple[str, str, AnimationEntry]]:
        """收集所有动画条目 [(folder_path, anim_name, entry), ...]"""
        result = []
        for folder_name, folder in self.folders.items():
            current_path = f"{parent_path}/{folder_name}" if parent_path else folder_name
            for anim_name, entry in folder.animations.items():
                result.append((current_path, anim_name, entry))
            result.extend(self._collect_subfolder(folder, current_path))
        return result
    
    def _collect_subfolder(self, folder: FolderNode, parent_path: str) -> List[Tuple[str, str, AnimationEntry]]:
        result = []
        for sub_name, sub_folder in folder.subfolders.items():
            current_path = f"{parent_path}/{sub_name}"
            for anim_name, entry in sub_folder.animations.items():
                result.append((current_path, anim_name, entry))
            result.extend(self._collect_subfolder(sub_folder, current_path))
        return result


# ============================================================
# 字典清单
# ============================================================

@dataclass
class DictionaryManifest:
    """字典清单 - 阶段1的输出"""
    version: str = "1.0"
    source_type: str = UEFORMAT_SOURCE_TYPE
    source_path: str = ""
    created_at: str = ""
    skeletons: Dict[str, SkeletonNode] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
    
    def get_all_animations(self) -> List[Tuple[str, str, str, AnimationEntry]]:
        """获取所有动画 [(skeleton, folder_path, anim_name, entry), ...]"""
        result = []
        for skel_name, skel_node in self.skeletons.items():
            for folder_path, anim_name, entry in skel_node.collect_all_animations():
                result.append((skel_name, folder_path, anim_name, entry))
        return result
    
    def to_dict(self) -> dict:
        """转换为可序列化的字典"""
        return {
            "version": self.version,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "created_at": self.created_at,
            "skeletons": {
                name: self._skeleton_to_dict(skel) 
                for name, skel in self.skeletons.items()
            }
        }
    
    def _skeleton_to_dict(self, skel: SkeletonNode) -> dict:
        return {name: self._folder_to_dict(f) for name, f in skel.folders.items()}
    
    def _folder_to_dict(self, folder: FolderNode) -> dict:
        result = {}
        if folder.animations:
            result["__animations__"] = {n: e.to_dict() for n, e in folder.animations.items()}
        for sub_name, sub_folder in folder.subfolders.items():
            result[sub_name] = self._folder_to_dict(sub_folder)
        return result
    
    def save_to_file(self, filepath: str):
        """保存到JSON文件"""
        os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=4)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'DictionaryManifest':
        """从字典创建实例"""
        manifest = cls(
            version=data.get("version", "1.0"),
            source_type=data.get("source_type", UEFORMAT_SOURCE_TYPE),
            source_path=data.get("source_path", ""),
            created_at=data.get("created_at", "")
        )
        for skel_name, skel_data in data.get("skeletons", {}).items():
            skel = SkeletonNode(name=skel_name)
            skel.folders = cls._dict_to_folders(skel_data)
            manifest.skeletons[skel_name] = skel
        return manifest
    
    @classmethod
    def _dict_to_folders(cls, data: dict) -> Dict[str, FolderNode]:
        folders = {}
        for key, value in data.items():
            if isinstance(value, dict):
                folder = FolderNode(path=key)
                if "__animations__" in value:
                    for anim_name, anim_data in value["__animations__"].items():
                        folder.animations[anim_name] = AnimationEntry.from_dict(anim_data)
                subfolders = {k: v for k, v in value.items() if k != "__animations__"}
                folder.subfolders = cls._dict_to_folders(subfolders)
                folders[key] = folder
        return folders
    
    @classmethod
    def load_from_file(cls, filepath: str) -> 'DictionaryManifest':
        """从JSON文件加载"""
        with open(filepath, 'r', encoding='utf-8') as f:
            return cls.from_dict(json.load(f))


# ============================================================
# 导入动作
# ============================================================

@dataclass
class ImportedAction:
    """导入的动作"""
    action_name: str = ""
    source_manifest_ref: str = ""
    source_file: str = ""
    source_bundle: str = ""
    staged_blend: str = ""
    import_status: str = "success"
    frame_range: Tuple[int, int] = (0, 0)
    error_message: str = ""
    task_id: str = ""
    # Snapshot of the target FModel skeleton provenance, if available.
    # Empty means this record came from a legacy checkpoint or a caller that
    # does not yet provide the strict identity contract.
    skeleton_provenance: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 导入资产集合
# ============================================================

@dataclass
class ImportedAssetCollection:
    """导入资产集合 - 阶段2的输出"""
    version: str = "1.0"
    import_mode: str = UEFORMAT_IMPORT_MODE
    imported_actions: List[ImportedAction] = field(default_factory=list)
    failed_imports: List[Dict[str, str]] = field(default_factory=list)
    task_identity: Dict = field(default_factory=dict)
    # Collection-level snapshot used by checkpoints and library manifests.
    skeleton_provenance: Dict[str, Any] = field(default_factory=dict)
    
    def set_skeleton_provenance(self, provenance: Optional[Dict[str, Any]]) -> None:
        """Attach one normalized provenance snapshot to the collection/actions."""
        self.skeleton_provenance = dict(provenance or {})
        for action in self.imported_actions:
            if not action.skeleton_provenance:
                action.skeleton_provenance = dict(self.skeleton_provenance)

    @property
    def success_count(self) -> int:
        return len(self.imported_actions)
    
    @property
    def failed_count(self) -> int:
        return len(self.failed_imports)
    
    def to_dict(self) -> dict:
        action_payloads = []
        for action in self.imported_actions:
            payload = asdict(action)
            if not payload.get("skeleton_provenance") and self.skeleton_provenance:
                payload["skeleton_provenance"] = dict(self.skeleton_provenance)
            # Keep the old wire shape for legacy actions instead of emitting
            # an empty compatibility field.
            if not payload.get("skeleton_provenance"):
                payload.pop("skeleton_provenance", None)
            action_payloads.append(payload)
        return {
            "version": self.version,
            "import_mode": self.import_mode,
            "imported_actions": action_payloads,
            "failed_imports": self.failed_imports,
            "task_identity": self.task_identity,
            "skeleton_provenance": self.skeleton_provenance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ImportedAssetCollection':
        col = cls(
            version=data.get("version", "1.0"),
            import_mode=data.get("import_mode", UEFORMAT_IMPORT_MODE),
            failed_imports=list(data.get("failed_imports", [])),
            task_identity=dict(data.get("task_identity", {})),
            skeleton_provenance=dict(data.get("skeleton_provenance", {})),
        )
        imported = []
        for item in data.get("imported_actions", []):
            try:
                imported.append(ImportedAction(**item))
            except TypeError:
                imported.append(ImportedAction(
                    action_name=item.get("action_name", ""),
                    source_manifest_ref=item.get("source_manifest_ref", ""),
                    source_file=item.get("source_file", ""),
                    source_bundle=item.get("source_bundle", ""),
                    staged_blend=item.get("staged_blend", ""),
                    import_status=item.get("import_status", "success"),
                    frame_range=tuple(item.get("frame_range", (0, 0))),
                    error_message=item.get("error_message", ""),
                    task_id=item.get("task_id", ""),
                    skeleton_provenance=dict(item.get("skeleton_provenance", {})),
                ))
        col.imported_actions = imported
        # Old checkpoints have no provenance.  Preserve that legacy state
        # instead of manufacturing a strict identity during deserialization.
        col.set_skeleton_provenance(col.skeleton_provenance)
        return col


# ============================================================
# 目录条目
# ============================================================

@dataclass
class CatalogEntry:
    """目录条目"""
    uuid: str = ""
    path: str = ""
    skeleton: str = ""
    action_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'CatalogEntry':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================
# 资产库资产
# ============================================================

@dataclass
class LibraryAsset:
    """资产库中的资产"""
    action_name: str = ""
    catalog_id: str = ""
    skeleton: str = ""
    blend_file: str = ""
    thumbnail_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'LibraryAsset':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class BundleRecord:
    """一个骨架动画包及其同名 sidecar 清单。"""
    blend_file: str = ""
    sidecar_file: str = ""
    skeletons: List[str] = field(default_factory=list)
    processed_sources: List[str] = field(default_factory=list)
    completion_state: str = "pending"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'BundleRecord':
        data = data or {}
        return cls(
            blend_file=data.get("blend_file", data.get("blend", "")),
            sidecar_file=data.get("sidecar_file", data.get("sidecar", "")),
            skeletons=list(data.get("skeletons", [])),
            processed_sources=list(data.get("processed_sources", [])),
            completion_state=data.get("completion_state", "pending"),
        )


# ============================================================
# 资产库清单
# ============================================================

@dataclass
class AssetLibraryManifest:
    """资产库清单 - 阶段3的输出"""
    version: str = "1.0"
    library_name: str = ""
    library_id: str = ""
    library_path: str = ""
    skeletons: List[str] = field(default_factory=list)
    blend_files: List[str] = field(default_factory=list)
    catalog_entries: List[CatalogEntry] = field(default_factory=list)
    assets: List[LibraryAsset] = field(default_factory=list)
    bundles: List[BundleRecord] = field(default_factory=list)
    
    @property
    def total_assets(self) -> int:
        return len(self.assets)
    
    def to_dict(self) -> dict:
        is_v2 = self.version == "2.0"
        data = {
            "version": self.version,
            "library_name": self.library_name,
            "library_id": self.library_id,
            "library_path": self.library_path,
            "blend_files": self.blend_files,
            "catalog_entries": [
                {
                    "uuid": catalog.uuid,
                    "path": catalog.path,
                    **({"skeleton": catalog.skeleton} if is_v2 else {}),
                    "action_count": catalog.action_count,
                }
                for catalog in self.catalog_entries
            ],
            "assets": [
                {
                    "action_name": asset.action_name,
                    "catalog_id": asset.catalog_id,
                    **({"skeleton": asset.skeleton} if is_v2 else {}),
                    "blend_file": asset.blend_file,
                    "thumbnail_path": asset.thumbnail_path,
                }
                for asset in self.assets
            ],
        }
        if is_v2:
            data["skeletons"] = self.skeletons
            data["bundles"] = [b.to_dict() for b in self.bundles]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> 'AssetLibraryManifest':
        """Read both legacy v1 indexes and the v2 bundle-aware format."""
        data = data or {}
        manifest = cls(
            version=data.get("version", "1.0"),
            library_name=data.get("library_name", ""),
            library_id=data.get("library_id", ""),
            library_path=data.get("library_path", ""),
            skeletons=list(data.get("skeletons", [])),
            blend_files=list(data.get("blend_files", [])),
        )
        manifest.catalog_entries = [
            CatalogEntry.from_dict(item) for item in data.get("catalog_entries", [])
            if isinstance(item, dict)
        ]
        manifest.assets = [
            LibraryAsset.from_dict(item) for item in data.get("assets", [])
            if isinstance(item, dict)
        ]
        manifest.bundles = [
            BundleRecord.from_dict(item) for item in data.get("bundles", [])
            if isinstance(item, dict)
        ]
        return manifest
    
    def save_to_file(self, filepath: str):
        """保存到JSON文件"""
        os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=4)


# ============================================================
# 多骨架导入计划
# ============================================================

@dataclass
class MultiImportTask:
    """一个独立的源骨架导入任务。"""
    source_skeleton: str = ""
    target_armature: str = ""
    compatibility_mode: str = SKELETON_COMPATIBILITY_STRICT
    source_subset: List[str] = field(default_factory=list)
    source_identities: List[Dict[str, str]] = field(default_factory=list)
    target_identity: Dict[str, str] = field(default_factory=dict)
    target_skeleton_provenance: Dict[str, Any] = field(default_factory=dict)
    import_fingerprint: str = ""
    task_id: str = ""
    progress: float = 0.0
    status: str = "pending"

    def __post_init__(self):
        self.compatibility_mode = normalize_skeleton_compatibility_mode(
            self.compatibility_mode
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict, validate: bool = False) -> 'MultiImportTask':
        if validate:
            if not isinstance(data, dict):
                raise ValueError("multi-import task must be an object")
            if not isinstance(data.get("source_skeleton", ""), str):
                raise ValueError("multi-import task source_skeleton must be a string")
            if not isinstance(data.get("target_armature", ""), str):
                raise ValueError("multi-import task target_armature must be a string")
            if not isinstance(data.get("compatibility_mode", SKELETON_COMPATIBILITY_STRICT), str):
                raise ValueError("multi-import task compatibility_mode must be a string")
            try:
                normalize_skeleton_compatibility_mode(
                    data.get("compatibility_mode", SKELETON_COMPATIBILITY_STRICT)
                )
            except ValueError as error:
                raise ValueError("multi-import task compatibility_mode is invalid") from error
            subset = data.get("source_subset", [])
            if not isinstance(subset, list) or not all(isinstance(item, str) for item in subset):
                raise ValueError("multi-import task source_subset must be a string list")
            identities = data.get("source_identities", [])
            if not isinstance(identities, list) or not all(isinstance(item, dict) for item in identities):
                raise ValueError("multi-import task source_identities must be an object list")
            provenance = data.get("target_skeleton_provenance", {})
            if not isinstance(provenance, dict):
                raise ValueError("multi-import task target_skeleton_provenance must be an object")
            try:
                from .skeleton_provenance import validate_provenance_record
                validate_provenance_record(provenance)
            except (ImportError, TypeError, ValueError) as error:
                raise ValueError(
                    "multi-import task target_skeleton_provenance is invalid"
                ) from error
            progress = data.get("progress", 0.0)
            if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not 0 <= progress <= 1:
                raise ValueError("multi-import task progress must be between zero and one")
            if data.get("status", "pending") not in {
                "pending", "running", "completed", "failed", "skipped",
            }:
                raise ValueError("multi-import task has an invalid status")
        return cls(**{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class MultiImportPlan:
    """由多个独立骨架任务组成的可检查点导入计划。"""
    version: str = "1.0"
    tasks: List[MultiImportTask] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"version": self.version, "tasks": [task.to_dict() for task in self.tasks]}

    @classmethod
    def from_dict(cls, data: dict, validate: bool = False) -> 'MultiImportPlan':
        if validate and not isinstance(data, dict):
            raise ValueError("multi-import plan must be an object")
        data = data or {}
        if validate and data.get("version", "1.0") != "1.0":
            raise ValueError("multi-import plan has an unsupported version")
        tasks = data.get("tasks", [])
        if validate and not isinstance(tasks, list):
            raise ValueError("multi-import plan tasks must be a list")
        return cls(
            version=data.get("version", "1.0"),
            tasks=[MultiImportTask.from_dict(task, validate=validate) for task in tasks
                   if validate or isinstance(task, dict)],
        )


@dataclass
class MultiImportCheckpoint:
    """计划执行进度，在每个任务完成后持久化。"""
    version: str = "1.0"
    plan: MultiImportPlan = field(default_factory=MultiImportPlan)
    current_task_index: int = 0

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "plan": self.plan.to_dict(),
            "current_task_index": self.current_task_index,
        }

    @classmethod
    def from_dict(cls, data: dict, validate: bool = False) -> 'MultiImportCheckpoint':
        if validate and not isinstance(data, dict):
            raise ValueError("multi-import checkpoint must be an object")
        data = data or {}
        if validate and data.get("version", "1.0") != "1.0":
            raise ValueError("multi-import checkpoint has an unsupported version")
        plan = MultiImportPlan.from_dict(data.get("plan", {}), validate=validate)
        current_task_index = data.get("current_task_index", 0)
        if validate and (
            isinstance(current_task_index, bool)
            or not isinstance(current_task_index, int)
            or not 0 <= current_task_index <= len(plan.tasks)
        ):
            raise ValueError("multi-import checkpoint has an invalid task index")
        return cls(
            version=data.get("version", "1.0"),
            plan=plan,
            current_task_index=current_task_index,
        )


# ============================================================
# 管线配置
# ============================================================

@dataclass
class PipelineConfig:
    """管线配置"""
    source_dir: str = ""
    output_dir: str = ""
    library_name: str = "Asset_Library"
    source_type: str = UEFORMAT_SOURCE_TYPE
    import_mode: str = UEFORMAT_IMPORT_MODE
    
    # Unity动画特有配置
    unity_skip_processed: bool = True
    
    # 导入选项
    import_scale: float = 0.01
    rotation_only: bool = False
    import_curves: bool = False
    target_armature: str = ""
    skeleton_filter: str = ""
    skeleton_compatibility_mode: str = SKELETON_COMPATIBILITY_STRICT
    import_batch_size: int = 300
    max_import_cycles: int = 200
    large_import_threshold: int = 2000
    max_consecutive_failures: int = 120
    import_resume_enabled: bool = True
    clean_after_import: bool = False
    clean_threshold: float = 0.001
    clean_flat_channels: bool = True
    
    # 资产库选项
    chunk_size: int = 500
    force_rebuild: bool = False
    generate_thumbnails: bool = True
    thumb_resolution: str = "256"
    thumb_quality: str = "OPENGL"
    thumb_isolate: bool = True
    
    def __post_init__(self):
        self.skeleton_compatibility_mode = normalize_skeleton_compatibility_mode(
            self.skeleton_compatibility_mode
        )

    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PipelineConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================
# 管线日志
# ============================================================

@dataclass
class PipelineLog:
    """管线日志"""
    timestamp: str = ""
    stage: str = ""
    level: str = "info"
    message: str = ""
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


# ============================================================
# 注册
# ============================================================

def register():
    """Core types不需要注册Blender类"""
    pass

def unregister():
    """Core types不需要注册Blender类"""
    pass
