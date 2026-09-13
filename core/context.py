# FModel_Tools/core/context.py
# 管线上下文 — PipelineContext 数据载体

import json
import uuid
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime

import bpy

from .enums import (
    PipelineStage,
    StageStatus,
    normalize_ueformat_pipeline_config,
    normalize_skeleton_compatibility_mode,
)
from .types import MultiImportCheckpoint, MultiImportPlan, PipelineConfig
from .resume import (
    build_settings_fingerprint, import_settings_fingerprint, reconcile_resume_state,
)
from .library_files import staging_task_dir, validate_staged_blend_path


@dataclass
class PipelineContext:
    """管线流程上下文 - 统一数据载体
    
    这是整个管线流程的核心数据结构，在四个阶段之间传递所有必要的信息。
    
    使用示例:
        context = PipelineContext()
        context.config = {"source_dir": "/path", ...}
        context.log("stage1", "info", "开始处理")
        context.save_to_file("checkpoint.json")
    """
    
    # 基本信息
    pipeline_id: str = ""
    created_at: str = ""
    current_stage: str = PipelineStage.DICTIONARY_ACQUISITION.value
    status: str = StageStatus.PENDING.value
    
    # 各阶段数据 (使用Optional和Dict以便序列化)
    dictionary_manifest: Optional[Dict] = None
    imported_assets: Optional[Dict] = None
    # Target armature's canonical FModel skeleton provenance.  ``None`` is
    # retained for old checkpoints and is not interpreted as strict.
    skeleton_provenance: Optional[Dict] = None
    library_manifest: Optional[Dict] = None
    multi_import_plan: Optional[Dict] = None
    multi_import_checkpoint: Optional[Dict] = None
    
    # 配置
    config: Dict[str, Any] = field(default_factory=dict)
    
    # 日志
    logs: List[Dict[str, str]] = field(default_factory=list)
    
    # 阶段状态追踪
    stage_status: Dict[str, str] = field(default_factory=lambda: {
        PipelineStage.DICTIONARY_ACQUISITION.value: StageStatus.PENDING.value,
        PipelineStage.FORMAT_CONVERSION.value: StageStatus.PENDING.value,
        PipelineStage.ASSET_IMPORT.value: StageStatus.PENDING.value,
        PipelineStage.LIBRARY_BUILDING.value: StageStatus.PENDING.value,
        PipelineStage.FINALIZATION.value: StageStatus.PENDING.value,
    })
    
    def __post_init__(self):
        if not self.pipeline_id:
            self.pipeline_id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

        self.config, migration = normalize_ueformat_pipeline_config(self.config)
        if migration:
            # Do not reuse a scan/import result created by the disabled Unity or
            # FBX routes. The next run must begin with a native UEFormat scan.
            self.dictionary_manifest = None
            self.imported_assets = None
            self.skeleton_provenance = None
            self.library_manifest = None
            self.multi_import_plan = None
            self.multi_import_checkpoint = None
            self.current_stage = PipelineStage.DICTIONARY_ACQUISITION.value
            self.status = StageStatus.PENDING.value
            self.stage_status = {
                stage.value: StageStatus.PENDING.value for stage in PipelineStage
            }
            self.logs.append({
                "timestamp": datetime.now().isoformat(),
                "stage": "context",
                "level": "warning",
                "message": "旧格式管线状态已迁移为 UEFormat 原生管线，旧执行结果不会复用",
            })

        if "resume_fingerprints" not in self.config:
            self.config["resume_fingerprints"] = {
                "import": import_settings_fingerprint(self.config),
                "build": build_settings_fingerprint(self.config),
            }
        if not self.config.get("import_resume_enabled", True):
            self.config["import_checkpoint"] = {}
    
    # ============================================================
    # 日志方法
    # ============================================================
    
    def log(self, stage: str, level: str, message: str):
        """添加日志条目"""
        self.logs.append({
            "timestamp": datetime.now().isoformat(),
            "stage": stage,
            "level": level,
            "message": message
        })
    
    def log_info(self, stage: str, message: str):
        """添加信息日志"""
        self.log(stage, "info", message)
    
    def log_warning(self, stage: str, message: str):
        """添加警告日志"""
        self.log(stage, "warning", message)
    
    def log_error(self, stage: str, message: str):
        """添加错误日志"""
        self.log(stage, "error", message)
    
    # ============================================================
    # 阶段状态管理
    # ============================================================
    
    def set_stage_status(self, stage: str, status: str):
        """设置阶段状态"""
        self.stage_status[stage] = status
        self.current_stage = stage
        
        if status == StageStatus.COMPLETED.value:
            self.log_info(stage, f"阶段 {stage} 完成")
        elif status == StageStatus.FAILED.value:
            self.log_error(stage, f"阶段 {stage} 失败")
    
    def get_stage_status(self, stage: str) -> str:
        """获取阶段状态"""
        return self.stage_status.get(stage, StageStatus.PENDING.value)
    
    def is_stage_completed(self, stage: str) -> bool:
        """检查阶段是否完成"""
        return self.get_stage_status(stage) == StageStatus.COMPLETED.value
    
    def get_next_stage(self) -> Optional[str]:
        """获取下一个待执行阶段"""
        stages = [
            PipelineStage.DICTIONARY_ACQUISITION.value,
            PipelineStage.FORMAT_CONVERSION.value,
            PipelineStage.ASSET_IMPORT.value,
            PipelineStage.LIBRARY_BUILDING.value,
            PipelineStage.FINALIZATION.value,
        ]
        for stage in stages:
            if not self.is_stage_completed(stage):
                return stage
        return None
    
    # ============================================================
    # 配置访问
    # ============================================================
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self.config.get(key, default)
    
    def set_config(self, key: str, value: Any):
        """设置配置值"""
        self.config[key] = value

    def apply_pipeline_config(self, config: Dict[str, Any]) -> bool:
        """Update settings and reset scan state only when its inputs changed."""
        # The dictionary is determined by the source scan, not by options used
        # later to import or package the scanned animations.
        semantic_keys = {
            "source_dir",
            "source_type",
            "skeleton_filter",
        }
        saved_config = {
            key: value for key, value in self.config.items() if key in semantic_keys
        }
        incoming_config = {
            key: value for key, value in config.items() if key in semantic_keys
        }
        source_changed = bool(saved_config and saved_config != incoming_config)
        output_changed = not self._same_output_dir(
            self.config.get("output_dir", ""), config.get("output_dir", "")
        )
        saved_library_name = self.config.get("library_name", "")
        library_changed = bool(saved_library_name) and saved_library_name != config.get(
            "library_name", ""
        )
        if not source_changed and not output_changed and not library_changed:
            state = self.to_dict()
            state["config"].update(config)
            reconcile_resume_state(
                state,
                import_settings_fingerprint(config),
                build_settings_fingerprint(config),
                bool(config.get("import_resume_enabled", True)),
            )
            self._apply_resume_state(state)
            return False
        runtime_config = {
            key: self.config[key]
            for key in (
                "pending_staged_cleanup", "pending_staged_cleanup_owners",
                "quarantined_staged_cleanup",
            )
            if key in self.config
        }
        self.config = dict(config)
        self.config.update(runtime_config)
        if source_changed:
            self.dictionary_manifest = None
            self.imported_assets = None
            self.skeleton_provenance = None
            self.multi_import_plan = None
            self.multi_import_checkpoint = None
            self.current_stage = PipelineStage.DICTIONARY_ACQUISITION.value
            self.stage_status = {
                stage.value: StageStatus.PENDING.value for stage in PipelineStage
            }
        else:
            self._invalidate_library_state()
        self.library_manifest = None
        self.status = StageStatus.PENDING.value
        self.log_info("context", "管线配置已更改，已清除过期的执行状态")
        self.config["resume_fingerprints"] = {
            "import": import_settings_fingerprint(config),
            "build": build_settings_fingerprint(config),
        }
        if not config.get("import_resume_enabled", True):
            self.config["import_checkpoint"] = {}
        return True

    def _apply_resume_state(self, state: Dict[str, Any]):
        for key in (
            "config", "stage_status", "dictionary_manifest", "imported_assets",
            "skeleton_provenance", "library_manifest", "multi_import_plan",
            "multi_import_checkpoint",
        ):
            setattr(self, key, state.get(key))

    @staticmethod
    def _same_output_dir(left: str, right: str) -> bool:
        if not isinstance(left, str) or not isinstance(right, str):
            return False
        if not left or not right:
            return left == right
        return os.path.normcase(os.path.realpath(os.path.abspath(left))) == os.path.normcase(
            os.path.realpath(os.path.abspath(right))
        )

    def _invalidate_library_state(self):
        for plan_data in (self.multi_import_plan,):
            self._reset_task_completion(plan_data)
        if isinstance(self.multi_import_checkpoint, dict):
            self._reset_task_completion(self.multi_import_checkpoint.get("plan"))
            self.multi_import_checkpoint["current_task_index"] = 0
        for stage in (
            PipelineStage.ASSET_IMPORT.value,
            PipelineStage.LIBRARY_BUILDING.value,
            PipelineStage.FINALIZATION.value,
        ):
            self.stage_status[stage] = StageStatus.PENDING.value
        self.current_stage = PipelineStage.ASSET_IMPORT.value

    @staticmethod
    def _reset_task_completion(plan_data):
        if not isinstance(plan_data, dict):
            return
        tasks = plan_data.get("tasks", [])
        if not isinstance(tasks, list):
            return
        for task in tasks:
            if isinstance(task, dict):
                task["status"] = "pending"
                task["progress"] = 0.0
    
    # ============================================================
    # 序列化方法
    # ============================================================
    
    def to_dict(self) -> dict:
        """转换为可序列化的字典"""
        return {
            "pipeline_id": self.pipeline_id,
            "created_at": self.created_at,
            "current_stage": self.current_stage,
            "status": self.status,
            "dictionary_manifest": self.dictionary_manifest,
            "imported_assets": self.imported_assets,
            "skeleton_provenance": self.skeleton_provenance,
            "library_manifest": self.library_manifest,
            "multi_import_plan": self.multi_import_plan,
            "multi_import_checkpoint": self.multi_import_checkpoint,
            "config": self.config,
            "logs": self.logs,
            "stage_status": self.stage_status
        }
    
    def to_json(self, indent: int = 4) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
    
    def save_to_file(self, filepath: str):
        """保存到文件"""
        os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=4)

    @staticmethod
    def _checkpoint_path_for_output(output_dir: str) -> Optional[str]:
        """Return the direct-child durable checkpoint path for one library output."""
        if not isinstance(output_dir, str) or not output_dir:
            return None
        output_dir = os.path.abspath(output_dir)
        checkpoint_path = os.path.abspath(
            os.path.join(output_dir, ".fmodel_pipeline_checkpoint.json")
        )
        output_real = os.path.realpath(output_dir)
        checkpoint_real = os.path.realpath(checkpoint_path)
        try:
            contained = os.path.commonpath((output_real, checkpoint_real)) == output_real
        except ValueError:
            contained = False
        if not contained or os.path.dirname(checkpoint_path) != output_dir:
            raise ValueError("pipeline checkpoint path escapes output directory")
        return checkpoint_path

    @staticmethod
    def _fsync_directory(path: str):
        try:
            fd = os.open(path, os.O_RDONLY)
        except (OSError, AttributeError):
            return
        try:
            try:
                os.fsync(fd)
            except (OSError, AttributeError):
                pass
        finally:
            os.close(fd)

    def persist_checkpoint(self):
        """Atomically persist recoverable state before destructive pipeline cleanup."""
        output_dir = self.get_config("output_dir", "")
        checkpoint_path = self._checkpoint_path_for_output(output_dir)
        if checkpoint_path is None:
            raise ValueError("pipeline checkpoint requires an output directory")
        output_dir = os.path.dirname(checkpoint_path)
        os.makedirs(output_dir, exist_ok=True)
        payload = self.to_json(indent=2).encode("utf-8")
        fd, temporary_path = tempfile.mkstemp(
            prefix=".fmodel-pipeline-", suffix=".tmp", dir=output_dir
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, checkpoint_path)
            self._fsync_directory(output_dir)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        # 成功路径不再写入 Blender 文本：sidecar 检查点是唯一持久化通道，
        # 避免用户 .blend 被 text datablock 污染。save_to_blender_text 仅
        # 保留给显式的失败恢复调用方。

    @classmethod
    def load_from_checkpoint(cls, output_dir: str) -> Optional['PipelineContext']:
        """Load a valid durable companion for a known library output directory."""
        checkpoint_path = cls._checkpoint_path_for_output(output_dir)
        if not checkpoint_path or not os.path.isfile(checkpoint_path):
            return None
        try:
            with open(checkpoint_path, 'r', encoding='utf-8') as handle:
                context = cls._decode_external_checkpoint(json.load(handle))
            if not cls._same_output_dir(context.get_config("output_dir", ""), output_dir):
                return None
            return context
        except (AttributeError, OSError, ValueError, json.JSONDecodeError, TypeError):
            return None

    @classmethod
    def _decode_external_checkpoint(cls, data) -> 'PipelineContext':
        """Validate untrusted persisted state before constructing a context."""
        if not isinstance(data, dict):
            raise ValueError("pipeline checkpoint must be an object")
        string_fields = ("pipeline_id", "created_at")
        for key in string_fields:
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"pipeline checkpoint {key} must be a string")

        valid_stages = {stage.value for stage in PipelineStage}
        valid_statuses = {status.value for status in StageStatus}
        if data.get("current_stage", PipelineStage.DICTIONARY_ACQUISITION.value) not in valid_stages:
            raise ValueError("pipeline checkpoint has an invalid current stage")
        if data.get("status", StageStatus.PENDING.value) not in valid_statuses:
            raise ValueError("pipeline checkpoint has an invalid status")

        for key in ("dictionary_manifest", "imported_assets", "skeleton_provenance", "library_manifest"):
            if key in data and data[key] is not None and not isinstance(data[key], dict):
                raise ValueError(f"pipeline checkpoint {key} must be an object")
        if data.get("skeleton_provenance") is not None:
            try:
                from .skeleton_provenance import validate_provenance_record
                validate_provenance_record(data.get("skeleton_provenance"))
            except (ImportError, TypeError, ValueError) as error:
                raise ValueError("pipeline checkpoint skeleton_provenance is invalid") from error
        if "logs" in data and not isinstance(data["logs"], list):
            raise ValueError("pipeline checkpoint logs must be a list")

        config = data.get("config", {})
        cls._validate_checkpoint_config(config)
        normalized_config, migration = normalize_ueformat_pipeline_config(config)
        if migration:
            data = dict(data)
            data["config"] = normalized_config
            data["dictionary_manifest"] = None
            data["imported_assets"] = None
            data["skeleton_provenance"] = None
            data["library_manifest"] = None
            data["multi_import_plan"] = None
            data["multi_import_checkpoint"] = None
            data["current_stage"] = PipelineStage.DICTIONARY_ACQUISITION.value
            data["status"] = StageStatus.PENDING.value
            data["stage_status"] = {
                stage.value: StageStatus.PENDING.value for stage in PipelineStage
            }
        stage_status = data.get("stage_status", {})
        if (
            not isinstance(stage_status, dict)
            or any(key not in valid_stages for key in stage_status)
            or any(value not in valid_statuses for value in stage_status.values())
        ):
            raise ValueError("pipeline checkpoint has invalid stage status")

        plan_data = data.get("multi_import_plan")
        if plan_data is not None:
            MultiImportPlan.from_dict(plan_data, validate=True)
        checkpoint_data = data.get("multi_import_checkpoint")
        if checkpoint_data is not None:
            MultiImportCheckpoint.from_dict(checkpoint_data, validate=True)
        cls._validate_staged_checkpoint_paths(data)
        return cls.from_dict(data)

    @classmethod
    def _validate_staged_checkpoint_paths(cls, data):
        """Validate every persisted staging reference against its task namespace."""
        config = data.get("config", {})
        output_dir = config.get("output_dir", "")
        containers = []
        imported_assets = data.get("imported_assets")
        if isinstance(imported_assets, dict):
            containers.append(imported_assets)
        import_checkpoint = config.get("import_checkpoint")
        if isinstance(import_checkpoint, dict):
            containers.append(import_checkpoint)
        for container in containers:
            actions = container.get("imported_actions", [])
            if not isinstance(actions, list):
                raise ValueError("checkpoint imported actions must be a list")
            staged_actions = [
                action for action in actions
                if isinstance(action, dict) and action.get("staged_blend")
            ]
            if not staged_actions:
                continue
            task_identity = container.get("task_identity") or import_checkpoint.get(
                "task_identity", {}
            ) if isinstance(import_checkpoint, dict) else container.get("task_identity")
            task_dir = staging_task_dir(output_dir, task_identity)
            for action in actions:
                if not isinstance(action, dict):
                    raise ValueError("checkpoint imported action must be an object")
                staged_blend = action.get("staged_blend", "")
                if staged_blend:
                    ref = action.get("source_manifest_ref", "")
                    skeleton = ref.split("/", 1)[0] if isinstance(ref, str) and ref else ""
                    if not skeleton:
                        skeleton = str(task_identity.get("source_skeleton", "") or "")
                    validate_staged_blend_path(task_dir, skeleton, staged_blend)

    @staticmethod
    def _validate_checkpoint_config(config):
        if not isinstance(config, dict):
            raise ValueError("pipeline checkpoint config must be an object")
        defaults = PipelineConfig().to_dict()
        for key, value in config.items():
            if key not in defaults:
                continue
            expected = type(defaults[key])
            valid = (
                isinstance(value, (int, float)) and not isinstance(value, bool)
                if expected is float else type(value) is expected
            )
            if not valid:
                raise ValueError(f"pipeline checkpoint config {key} has an invalid type")
        if "skeleton_compatibility_mode" in config:
            try:
                normalize_skeleton_compatibility_mode(config["skeleton_compatibility_mode"])
            except ValueError as error:
                raise ValueError(
                    "pipeline checkpoint config skeleton_compatibility_mode is invalid"
                ) from error
        runtime_types = {
            "source_skeleton": str,
            "source_subset": list,
            "pending_staged_cleanup": list,
            "pending_staged_cleanup_owners": dict,
            "quarantined_staged_cleanup": list,
        }
        for key, expected in runtime_types.items():
            if key in config and not isinstance(config[key], expected):
                raise ValueError(f"pipeline checkpoint config {key} has an invalid type")
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PipelineContext':
        """从字典创建实例"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    @classmethod
    def from_json(cls, json_str: str) -> 'PipelineContext':
        """从JSON字符串创建实例"""
        return cls.from_dict(json.loads(json_str))
    
    @classmethod
    def load_from_file(cls, filepath: str) -> 'PipelineContext':
        """从文件加载"""
        with open(filepath, 'r', encoding='utf-8') as f:
            return cls._decode_external_checkpoint(json.load(f))
    
    # ============================================================
    # Blender文本块操作 (便于调试)
    # ============================================================
    
    def save_to_blender_text(self, text_name: str = "PipelineContext"):
        """保存到Blender内部文本块"""
        if text_name in bpy.data.texts:
            text_block = bpy.data.texts[text_name]
            text_block.clear()
        else:
            text_block = bpy.data.texts.new(text_name)
        text_block.write(self.to_json())
    
    @classmethod
    def load_from_blender_text(
        cls, text_name: str = "PipelineContext", output_dir: Optional[str] = None
    ) -> Optional['PipelineContext']:
        """从Blender内部文本块加载"""
        if text_name not in bpy.data.texts:
            return None
        try:
            fallback = cls._decode_external_checkpoint(
                json.loads(bpy.data.texts[text_name].as_string())
            )
            fallback_output = fallback.get_config("output_dir", "")
            if output_dir is not None and not cls._same_output_dir(
                fallback_output, output_dir
            ):
                return None
            return cls.load_from_checkpoint(output_dir or fallback_output) or fallback
        except Exception:
            return None
    
    @classmethod
    def clear_blender_text(cls, text_name: str = "PipelineContext"):
        """清除Blender内部文本块"""
        if text_name in bpy.data.texts:
            bpy.data.texts.remove(bpy.data.texts[text_name])


# ============================================================
# 注册
# ============================================================

def register():
    """Core context不需要注册Blender类"""
    pass

def unregister():
    """Core context不需要注册Blender类"""
    pass
