# FModel_Tools/Fmodel/Pipeline/stages.py
# 管线阶段 — 字典获取、格式转换、扫描、打包

import bpy
import os
import json
import time
import re
import hashlib
import uuid
import shutil
from abc import ABC, abstractmethod

from ...core import (
    PipelineContext, PipelineStage, StageStatus, SourceType, ImportMode,
    DictionaryManifest, SkeletonNode, FolderNode, AnimationEntry,
    ImportedAssetCollection, ImportedAction, AssetLibraryManifest,
    CatalogEntry, LibraryAsset, BundleRecord, get_or_create_catalog_uuid, ensure_dir,
    manifest_with_bundle, replace_manifest_skeleton, publish_rebuild_replacement_set,
    prepare_library_transaction,
    persist_transaction_artifact,
    persist_transaction_replacement,
    publish_bundle_pair_and_library_index, validate_filename_component, staging_chunk_path,
    staging_task_dir, validate_staged_blend_path,
    load_complete_v2_manifest, get_manifest_processed_sources,
    migrate_verified_legacy_manifest,
    library_index_path, recover_library_transaction,
    validate_generated_output_filenames, validate_imported_action_set,
    validate_skeleton_output_names,
    armature_identity, import_settings_fingerprint, make_batch_identity,
    make_manifest_source, reusable_import_results,
)
from ...core.action_selection import resolve_imported_action
from ...core.action_safety import (
    animation_state,
    copy_action_for_pipeline,
    datablock_identity,
    localize_action,
    owned_actions,
    restore_animation_state,
    select_primary_action,
    supports_legacy_cleanup,
)


def cleanup_committed_staged_blends(task_tmp_dir, committed_staged_blends):
    """Remove only committed staging files belonging to one task namespace."""
    task_tmp_abs = os.path.abspath(task_tmp_dir)
    task_tmp_real = os.path.realpath(task_tmp_abs)
    removed = []
    for staged_blend in committed_staged_blends:
        staged_abs = os.path.abspath(staged_blend)
        staged_real = os.path.realpath(staged_abs)
        try:
            owned_by_task = (
                os.path.dirname(staged_abs) == task_tmp_abs
                and os.path.dirname(staged_real) == task_tmp_real
            )
        except ValueError:
            owned_by_task = False
        if not owned_by_task:
            continue
        if re.fullmatch(r".+_chunk_[0-9]{4,}\.blend", os.path.basename(staged_abs), re.IGNORECASE) is None:
            continue
        try:
            os.remove(staged_abs)
            removed.append(staged_abs)
        except FileNotFoundError:
            pass
    try:
        if os.path.isdir(task_tmp_dir) and not os.listdir(task_tmp_dir):
            os.rmdir(task_tmp_dir)
    except OSError:
        pass
    return removed


def _validated_cleanup_owner(tmp_root, owner):
    """Validate an owner independently of checkpoint-provided cleanup paths."""
    tmp_abs = os.path.abspath(tmp_root)
    tmp_real = os.path.realpath(tmp_abs)
    owner_abs = os.path.abspath(owner)
    owner_real = os.path.realpath(owner_abs)
    if (
        os.path.dirname(owner_abs) != tmp_abs
        or os.path.dirname(owner_real) != tmp_real
    ):
        raise ValueError("staging cleanup owner must be under the current _pipeline_tmp root")
    return owner_abs


def _validated_cleanup_path(owner, path):
    """Validate one direct-child staging blend without trusting its owner mapping."""
    owner_abs = os.path.abspath(owner)
    owner_real = os.path.realpath(owner_abs)
    path_abs = os.path.abspath(path)
    path_real = os.path.realpath(path_abs)
    if (
        os.path.dirname(path_abs) != owner_abs
        or os.path.dirname(path_real) != owner_real
        or re.fullmatch(
            r".+_chunk_[0-9]{4,}\.blend", os.path.basename(path_abs), re.IGNORECASE
        ) is None
    ):
        raise ValueError("staging cleanup path must be a direct-child .blend file")
    return path_abs


def queue_staged_cleanup(context, paths, task_tmp_dir):
    """Persist published staging artifacts until finalization completes."""
    current_owner = os.path.abspath(task_tmp_dir)
    pending = [os.path.abspath(path) for path in context.get_config(
        "pending_staged_cleanup", []
    ) or []]
    owners = {
        os.path.abspath(path): os.path.abspath(owner)
        for path, owner in dict(
            context.get_config("pending_staged_cleanup_owners", {}) or {}
        ).items()
    }
    for path in paths or []:
        absolute = os.path.abspath(path)
        pending.append(absolute)
        owners[absolute] = current_owner
    pending = list(dict.fromkeys(pending))
    context.set_config("pending_staged_cleanup", pending)
    context.set_config(
        "pending_staged_cleanup_owners",
        {path: owners[path] for path in pending},
    )


def retry_staged_cleanup(context, paths, task_tmp_dir):
    """Retry cleanup and retain locked paths in durable pipeline config."""
    pending = [os.path.abspath(path) for path in context.get_config(
        "pending_staged_cleanup", []
    ) or []]
    owners = {
        os.path.abspath(path): os.path.abspath(owner)
        for path, owner in dict(
            context.get_config("pending_staged_cleanup_owners", {}) or {}
        ).items()
    }
    candidates = list(dict.fromkeys(
        pending + [os.path.abspath(path) for path in paths or []]
    ))
    if not candidates:
        return
    current_owner = os.path.abspath(task_tmp_dir)
    tmp_root = os.path.dirname(current_owner)
    _validated_cleanup_owner(tmp_root, current_owner)
    for path in paths or []:
        owners.setdefault(os.path.abspath(path), current_owner)
    grouped = {}
    quarantined = list(context.get_config("quarantined_staged_cleanup", []) or [])
    for path in candidates:
        absolute = os.path.abspath(path)
        recorded_owner = owners.get(absolute, current_owner)
        recorded_parent = os.path.dirname(os.path.abspath(recorded_owner))
        if (
            os.path.basename(recorded_parent) == "_pipeline_tmp"
            and os.path.normcase(recorded_parent) != os.path.normcase(tmp_root)
        ):
            quarantined.append({
                "path": absolute,
                "owner": os.path.abspath(recorded_owner),
                "reason": "owner belongs to a different output",
            })
            continue
        owner = _validated_cleanup_owner(
            tmp_root, recorded_owner
        )
        validated_path = _validated_cleanup_path(owner, absolute)
        grouped.setdefault(owner, []).append(validated_path)
    try:
        for owner, owner_paths in grouped.items():
            cleanup_committed_staged_blends(owner, owner_paths)
    except OSError:
        quarantined_paths = {item["path"] for item in quarantined}
        remaining = [
            path for path in candidates
            if path not in quarantined_paths and os.path.exists(path)
        ]
        context.set_config("pending_staged_cleanup", remaining)
        context.set_config(
            "pending_staged_cleanup_owners",
            {path: owners.get(os.path.abspath(path), current_owner) for path in remaining},
        )
        context.set_config("quarantined_staged_cleanup", quarantined)
        raise
    quarantined_paths = {item["path"] for item in quarantined}
    remaining = [
        path for path in candidates
        if path not in quarantined_paths and os.path.exists(path)
    ]
    context.set_config("pending_staged_cleanup", remaining)
    context.set_config(
        "pending_staged_cleanup_owners",
        {path: owners.get(os.path.abspath(path), current_owner) for path in remaining},
    )
    context.set_config("quarantined_staged_cleanup", quarantined)


def track_staged_blend_references(imported_actions):
    """Track every imported action that must publish before its staging file is removed."""
    references = {}
    for imp_action in imported_actions:
        staged_blend = imp_action.get("staged_blend", "")
        if not staged_blend:
            continue
        staged_abs = os.path.abspath(staged_blend)
        staged_key = os.path.normcase(staged_abs)
        entry = references.setdefault(staged_key, {"path": staged_abs, "remaining": 0})
        entry["remaining"] += 1
    return references


def mark_staged_blend_actions_published(references, published_actions):
    """Return staging files whose final referencing action has now published."""
    ready = set()
    for imp_action in published_actions:
        staged_blend = imp_action.get("staged_blend", "")
        if not staged_blend:
            continue
        staged_key = os.path.normcase(os.path.abspath(staged_blend))
        entry = references.get(staged_key)
        if not entry or entry["remaining"] <= 0:
            continue
        entry["remaining"] -= 1
        if entry["remaining"] == 0:
            ready.add(entry["path"])
    return ready


# ============================================================
# 抽象基类
# ============================================================

class PipelineStageBase(ABC):
    """管线阶段抽象基类"""

    @staticmethod
    def _normalized_source_path(path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(path))) if path else ""

    @staticmethod
    def _source_manifest_ref(skeleton: str, folder_path: str, action_name: str) -> str:
        catalog_path = "/".join(part for part in (folder_path or "").replace("\\", "/").split("/") if part)
        return f"{skeleton}/{catalog_path or 'Uncategorized'}/{action_name}"

    @staticmethod
    def _source_skeleton(context: PipelineContext) -> str:
        return str(
            context.get_config("source_skeleton", "")
            or context.get_config("skeleton_filter", "")
            or ""
        )

    def _task_identity(self, context: PipelineContext) -> dict:
        """Return the stable identity that makes an import checkpoint reusable."""
        source_skeleton = self._source_skeleton(context)
        source_identities = [
            make_manifest_source(
                index, source_skeleton,
                "", "", path,
            )
            for index, path in enumerate(context.get_config("source_subset", [])) if path
        ]
        target_name = str(context.get_config("target_armature", "") or "")
        objects = getattr(bpy.data, "objects", None)
        target = objects.get(target_name) if target_name and objects is not None else None
        target_identity = armature_identity(target) if target and target.type == 'ARMATURE' else {
            "stable_id": target_name, "structure_revision": "missing"
        }
        fingerprint = import_settings_fingerprint(context.config)
        return {
            "source_skeleton": source_skeleton,
            "source_subset": [item["normalized_path"] for item in source_identities],
            "source_identities": source_identities,
            "target_armature": target_name,
            "target_identity": target_identity,
            "import_fingerprint": fingerprint,
            "task_id": make_batch_identity(source_identities, fingerprint, target_identity),
        }

    def _current_manifest_sources(self, context: PipelineContext) -> list[dict]:
        """Derive reusable-record identities from current scoped manifest entries."""
        manifest = DictionaryManifest.from_dict(context.dictionary_manifest or {})
        source_skeleton = self._source_skeleton(context)
        source_subset = {
            self._normalized_source_path(path)
            for path in context.get_config("source_subset", []) if path
        }
        sources = []
        for index, (skeleton, folder, name, entry) in enumerate(manifest.get_all_animations()):
            source_file = entry.source_file
            if source_skeleton and skeleton != source_skeleton:
                continue
            if source_subset and self._normalized_source_path(source_file) not in source_subset:
                continue
            sources.append(make_manifest_source(
                index, skeleton, folder, name, source_file, entry.content_hash,
            ))
        return sources

    def _staging_task_dir(self, context: PipelineContext) -> str:
        output_dir = bpy.path.abspath(context.get_config("output_dir", ""))
        return staging_task_dir(output_dir, self._task_identity(context))

    def _validate_staged_blend(self, context, staged_blend, imported_action):
        ref = imported_action.get("source_manifest_ref", "") if isinstance(imported_action, dict) else ""
        skeleton = ref.split("/", 1)[0] if isinstance(ref, str) and ref else ""
        if not skeleton:
            skeleton = self._task_identity(context)["source_skeleton"]
        return validate_staged_blend_path(
            self._staging_task_dir(context), skeleton, staged_blend
        )
    
    @abstractmethod
    def get_stage_name(self) -> str:
        pass
    
    @abstractmethod
    def validate_input(self, context: PipelineContext) -> tuple[bool, str]:
        pass
    
    @abstractmethod
    def execute(self, context: PipelineContext) -> bool:
        pass
    
    def run(self, context: PipelineContext) -> bool:
        """运行阶段（带状态管理）"""
        stage_name = self.get_stage_name()
        
        valid, error_msg = self.validate_input(context)
        if not valid:
            context.set_stage_status(stage_name, StageStatus.FAILED.value)
            context.log_error(stage_name, f"输入验证失败: {error_msg}")
            return False
        
        context.set_stage_status(stage_name, StageStatus.RUNNING.value)
        context.log_info(stage_name, f"开始执行阶段: {stage_name}")
        
        try:
            success = self.execute(context)
            if success:
                context.set_stage_status(stage_name, StageStatus.COMPLETED.value)
            else:
                context.set_stage_status(stage_name, StageStatus.FAILED.value)
            return success
        except Exception as e:
            context.set_stage_status(stage_name, StageStatus.FAILED.value)
            context.log_error(stage_name, f"异常: {str(e)}")
            return False


# ============================================================
# 阶段1: 字典获取
# ============================================================

class DictionaryAcquisitionStage(PipelineStageBase):
    """字典获取阶段 - 扫描源数据，生成标准化的字典清单"""
    
    def get_stage_name(self) -> str:
        return PipelineStage.DICTIONARY_ACQUISITION.value
    
    def validate_input(self, context: PipelineContext) -> tuple[bool, str]:
        source_dir = context.get_config("source_dir", "")
        if not source_dir:
            return False, "未指定源目录"
        abs_source = bpy.path.abspath(source_dir)
        if not os.path.exists(abs_source):
            return False, f"源目录不存在: {abs_source}"
        return True, ""
    
    def execute(self, context: PipelineContext) -> bool:
        source_type = context.get_config("source_type", SourceType.UNITY_ANIM.value)
        source_dir = bpy.path.abspath(context.get_config("source_dir", ""))
        
        handlers = {
            SourceType.UNITY_ANIM.value: self._process_unity_anims,
            SourceType.FMODEL_UEANIM.value: self._process_fmodel_manifests,
            SourceType.FBX_BATCH.value: self._process_fbx_batch,
        }
        
        handler = handlers.get(source_type)
        if not handler:
            context.log_error(self.get_stage_name(), f"不支持的源类型: {source_type}")
            return False
        
        return handler(context, source_dir)
    
    def _process_unity_anims(self, context: PipelineContext, source_dir: str) -> bool:
        """处理Unity .anim文件"""
        manifest = DictionaryManifest(
            source_type=SourceType.UNITY_ANIM.value,
            source_path=source_dir
        )
        
        processed_count = 0
        skip_processed = context.get_config("unity_skip_processed", True)
        
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for file in files:
                if not file.lower().endswith('.anim'):
                    continue
                
                file_path = os.path.join(root, file)
                original_name = os.path.splitext(file)[0]
                
                if skip_processed and re.search(r'_[a-f0-9]{8}$', original_name):
                    continue
                
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                except Exception as e:
                    context.log_warning(self.get_stage_name(), f"读取文件失败 {file_path}: {e}")
                    continue
                
                content_no_name = re.sub(r'^\s*m_Name:.*$', '', content, flags=re.MULTILINE)
                content_hash = hashlib.md5(content_no_name.encode('utf-8')).hexdigest()[:8]
                hashed_name = f"{original_name}_{content_hash}"
                
                rel_dir = os.path.relpath(root, source_dir).replace('\\', '/')
                if rel_dir == '.':
                    skel_name, catalog_path = "Generic", "Uncategorized"
                else:
                    parts = rel_dir.split('/')
                    skel_name = parts[0]
                    catalog_path = "/".join(parts[1:]) if len(parts) > 1 else "Base"
                
                if skel_name not in manifest.skeletons:
                    manifest.skeletons[skel_name] = SkeletonNode(name=skel_name)
                
                skel = manifest.skeletons[skel_name]
                folder = skel.get_or_create_folder(catalog_path)
                
                anim_entry = AnimationEntry(
                    original_name=original_name,
                    hashed_name=hashed_name,
                    source_file=file_path,
                    content_hash=content_hash
                )
                folder.add_animation(hashed_name, anim_entry)
                processed_count += 1
        
        if processed_count == 0:
            context.log_warning(self.get_stage_name(), "未找到需要处理的动画文件")
            return False
        
        context.dictionary_manifest = manifest.to_dict()
        context.log_info(self.get_stage_name(), f"成功处理 {processed_count} 个动画文件")
        return True
    
    def _process_fmodel_manifests(self, context: PipelineContext, source_dir: str) -> bool:
        """处理FModel manifest文件"""
        skeleton_filter = self._source_skeleton(context).strip()
        manifest = DictionaryManifest(
            source_type=SourceType.FMODEL_UEANIM.value,
            source_path=source_dir
        )
        
        processed_count = 0
        
        for f in os.listdir(source_dir):
            if not f.endswith("_manifest.json"):
                continue
            
            manifest_path = os.path.join(source_dir, f)
            try:
                with open(manifest_path, 'r', encoding='utf-8') as mf:
                    data = json.load(mf)
                
                for skel_name, folder_tree in data.items():
                    if skel_name == "__processed_sources__":
                        continue
                    if skeleton_filter and not self._skeleton_match(skel_name, skeleton_filter):
                        continue
                    
                    if skel_name not in manifest.skeletons:
                        manifest.skeletons[skel_name] = SkeletonNode(name=skel_name)
                    
                    self._merge_fmodel_tree(manifest.skeletons[skel_name], folder_tree, "")
                    processed_count += 1
            except Exception as e:
                context.log_warning(self.get_stage_name(), f"manifest解析失败 {manifest_path}: {e}")
                continue
        
        if processed_count == 0:
            context.log_info(self.get_stage_name(), "未找到manifest，回退为直接扫描.ueanim/.uepose")
            return self._process_fmodel_raw_files(context, source_dir)
        
        context.dictionary_manifest = manifest.to_dict()
        context.log_info(self.get_stage_name(), f"成功解析 {processed_count} 个manifest文件")
        return True

    def _process_fmodel_raw_files(self, context: PipelineContext, source_dir: str) -> bool:
        """处理FModel原始导出文件（.ueanim/.uepose + 同名json）"""
        from ...Assets.Utils.utils_asset import get_skeleton_name_from_json

        skeleton_filter = self._source_skeleton(context).strip()

        manifest = DictionaryManifest(
            source_type=SourceType.FMODEL_UEANIM.value,
            source_path=source_dir
        )

        processed_count = 0

        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for file in files:
                if not file.lower().endswith(('.ueanim', '.uepose')):
                    continue

                file_path = os.path.join(root, file)
                original_name = os.path.splitext(file)[0]

                json_path = os.path.join(root, f"{original_name}.json")
                skeleton_name = get_skeleton_name_from_json(json_path)

                if not skeleton_name:
                    rel_dir = os.path.relpath(root, source_dir).replace('\\', '/')
                    if rel_dir != '.':
                        skeleton_name = rel_dir.split('/')[0]

                if not skeleton_name:
                    skeleton_name = "Generic"

                if skeleton_filter and not self._skeleton_match(skeleton_name, skeleton_filter):
                    continue

                rel_dir = os.path.relpath(root, source_dir).replace('\\', '/')
                if rel_dir == '.':
                    folder_path = "Base"
                else:
                    parts = rel_dir.split('/')
                    folder_path = "/".join(parts[1:]) if len(parts) > 1 else "Base"

                if skeleton_name not in manifest.skeletons:
                    manifest.skeletons[skeleton_name] = SkeletonNode(name=skeleton_name)

                folder = manifest.skeletons[skeleton_name].get_or_create_folder(folder_path)
                anim_entry = AnimationEntry(
                    original_name=original_name,
                    hashed_name=original_name,
                    source_file=file_path,
                    content_hash="",
                    metadata={
                        "file_type": "ueanim" if file.lower().endswith('.ueanim') else "uepose",
                        "json_path": json_path if os.path.exists(json_path) else ""
                    }
                )
                folder.add_animation(original_name, anim_entry)
                processed_count += 1

        if processed_count == 0:
            context.log_warning(self.get_stage_name(), "未找到.ueanim/.uepose文件")
            return False

        context.dictionary_manifest = manifest.to_dict()
        context.log_info(self.get_stage_name(), f"成功扫描 {processed_count} 个FModel动画文件")
        return True

    def _skeleton_match(self, source_name: str, filter_name: str) -> bool:
        source = (source_name or "").strip().lower()
        target = (filter_name or "").strip().lower()
        if not source or not target:
            return True
        return source == target or source in target or target in source
    
    def _merge_fmodel_tree(self, skel: SkeletonNode, tree: dict, parent_path: str):
        """合并FModel树结构"""
        for key, value in tree.items():
            if key == "__actions__":
                continue
            
            current_path = f"{parent_path}/{key}" if parent_path else key
            folder = skel.get_or_create_folder(current_path)
            
            if isinstance(value, dict):
                if "__actions__" in value:
                    for action_data in value["__actions__"]:
                        if isinstance(action_data, list) and len(action_data) >= 1:
                            action_name = action_data[0]
                            anim_entry = AnimationEntry(
                                original_name=action_name,
                                hashed_name=action_name,
                                source_file="",
                                content_hash=""
                            )
                            folder.add_animation(action_name, anim_entry)
                
                subfolders = {k: v for k, v in value.items() if k != "__actions__" and isinstance(v, dict)}
                if subfolders:
                    self._merge_fmodel_tree(skel, subfolders, current_path)
    
    def _process_fbx_batch(self, context: PipelineContext, source_dir: str) -> bool:
        """处理批量FBX文件"""
        manifest = DictionaryManifest(
            source_type=SourceType.FBX_BATCH.value,
            source_path=source_dir
        )
        
        processed_count = 0
        
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for file in files:
                if not file.lower().endswith(('.fbx', '.FBX')):
                    continue
                
                file_path = os.path.join(root, file)
                original_name = os.path.splitext(file)[0]
                
                rel_dir = os.path.relpath(root, source_dir).replace('\\', '/')
                skel_name = "Generic" if rel_dir == "." else rel_dir.split('/')[0]
                catalog_path = "Uncategorized" if rel_dir == "." else rel_dir
                
                if skel_name not in manifest.skeletons:
                    manifest.skeletons[skel_name] = SkeletonNode(name=skel_name)
                
                folder = manifest.skeletons[skel_name].get_or_create_folder(catalog_path)
                anim_entry = AnimationEntry(
                    original_name=original_name,
                    hashed_name=original_name,
                    source_file=file_path,
                    content_hash=""
                )
                folder.add_animation(original_name, anim_entry)
                processed_count += 1
        
        if processed_count == 0:
            context.log_warning(self.get_stage_name(), "未找到FBX文件")
            return False
        
        context.dictionary_manifest = manifest.to_dict()
        context.log_info(self.get_stage_name(), f"成功扫描 {processed_count} 个FBX文件")
        return True


# ============================================================
# 阶段2: 格式中转 (新增 - 统一差异化环节)
# ============================================================

class FormatConversionStage(PipelineStageBase):
    """格式中转阶段 - 根据源类型执行差异化的格式转换"""
    
    def get_stage_name(self) -> str:
        return PipelineStage.FORMAT_CONVERSION.value
    
    def validate_input(self, context: PipelineContext) -> tuple[bool, str]:
        if not context.dictionary_manifest:
            return False, "缺少字典清单数据"
        return True, ""
    
    def execute(self, context: PipelineContext) -> bool:
        source_type = context.get_config("source_type", SourceType.UNITY_ANIM.value)
        
        handlers = {
            SourceType.FMODEL_UEANIM.value: self._convert_ueformat,
            SourceType.UNITY_ANIM.value: self._convert_unity,
            SourceType.FBX_BATCH.value: self._convert_fbx,
        }
        
        handler = handlers.get(source_type, self._convert_default)
        return handler(context)
    
    def _convert_ueformat(self, context: PipelineContext) -> bool:
        """UEFormat格式中转 - 直接使用，无需转换"""
        context.log_info(self.get_stage_name(), "UEFormat格式无需中转，直接使用")
        return True
    
    def _convert_unity(self, context: PipelineContext) -> bool:
        """Unity格式中转 - 重命名.anim文件并准备FBX导入"""
        manifest = DictionaryManifest.from_dict(context.dictionary_manifest)
        source_dir = bpy.path.abspath(context.get_config("source_dir", ""))
        output_dir = bpy.path.abspath(context.get_config("output_dir", ""))
        
        if not output_dir:
            output_dir = os.path.join(source_dir, "_converted")
            ensure_dir(output_dir)
        
        converted_count = 0
        conversion_manifest = {"converted_files": []}
        
        for skel_name, skel_node in manifest.skeletons.items():
            for folder_path, folder_node in skel_node.folders.items():
                for anim_name, anim_entry in folder_node.animations.items():
                    source_file = anim_entry.source_file
                    if not os.path.exists(source_file):
                        continue
                    
                    try:
                        # 重命名文件（添加hash后缀）
                        new_name = anim_entry.hashed_name
                        new_path = os.path.join(
                            output_dir,
                            skel_name,
                            folder_path if folder_path else "",
                            f"{new_name}.anim"
                        )
                        ensure_dir(os.path.dirname(new_path))
                        shutil.copy2(source_file, new_path)
                        
                        conversion_manifest["converted_files"].append({
                            "original": source_file,
                            "converted": new_path,
                            "skel_name": skel_name,
                            "folder_path": folder_path,
                            "anim_name": anim_name
                        })
                        converted_count += 1
                    except Exception as e:
                        context.log_warning(self.get_stage_name(), f"转换失败: {anim_name} - {e}")
        
        # 保存转换清单
        manifest_path = os.path.join(output_dir, "conversion_manifest.json")
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(conversion_manifest, f, ensure_ascii=False, indent=2)
        
        context.conversion_manifest = conversion_manifest
        context.log_info(self.get_stage_name(), f"成功转换 {converted_count} 个文件")
        return converted_count > 0
    
    def _convert_fbx(self, context: PipelineContext) -> bool:
        """FBX格式中转 - 直接使用，无需转换"""
        context.log_info(self.get_stage_name(), "FBX格式无需中转，直接使用")
        return True
    
    def _convert_default(self, context: PipelineContext) -> bool:
        """默认中转处理"""
        context.log_info(self.get_stage_name(), "未知格式，跳过中转")
        return True


# ============================================================
# 阶段3: 资源导入
# ============================================================

class AssetImportStage(PipelineStageBase):
    """资源导入阶段 - 根据字典将资源导入Blender"""
    
    def get_stage_name(self) -> str:
        return PipelineStage.ASSET_IMPORT.value
    
    def validate_input(self, context: PipelineContext) -> tuple[bool, str]:
        if not context.dictionary_manifest:
            return False, "缺少字典清单数据"
        return True, ""

    def _report_progress(self, context, current: int, total: int, stage_name: str):
        """Update Blender's active window-manager progress indicator."""
        if total <= 0:
            return
        percent = min(100, int(current * 100 / total))
        context.log_info(stage_name, f"导入动画: {current}/{total} ({percent}%)")
        try:
            window_manager = bpy.context.window_manager
            if not getattr(self, "_progress_active", False):
                window_manager.progress_begin(0, total)
                self._progress_active = True
            window_manager.progress_update(min(current, total))
            if current >= total:
                window_manager.progress_end()
                self._progress_active = False
        except Exception:
            if getattr(self, "_progress_active", False):
                try:
                    window_manager.progress_end()
                except Exception:
                    pass
            self._progress_active = False

    def _finish_progress(self):
        """Close an active Blender progress indicator after an incomplete import."""
        if not getattr(self, "_progress_active", False):
            return
        try:
            bpy.context.window_manager.progress_end()
        except Exception:
            pass
        finally:
            self._progress_active = False

    def _write_failure_report(self, context, collection):
        """Persist import failures outside disposable pipeline staging files."""
        if not collection.failed_imports:
            return ""
        output_dir = bpy.path.abspath(context.get_config("output_dir", ""))
        if not output_dir:
            return ""
        reports_dir = os.path.join(output_dir, "reports")
        try:
            os.makedirs(reports_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            report_path = os.path.join(reports_dir, f"import_failures_{timestamp}.json")
            payload = {
                "stage": self.get_stage_name(),
                "failed_count": len(collection.failed_imports),
                "failures": collection.failed_imports,
            }
            with open(report_path, "w", encoding="utf-8") as report_file:
                json.dump(payload, report_file, ensure_ascii=False, indent=2)
            return report_path
        except Exception:
            return ""
    
    def execute(self, context: PipelineContext) -> bool:
        import_mode = context.get_config("import_mode", ImportMode.FBX_MATCH.value)
        task_identity = self._task_identity(context)

        persisted = context.imported_assets or {}
        persisted_checkpoint = context.get_config("import_checkpoint", {})
        current_manifest_sources = self._current_manifest_sources(context)
        if (
            import_mode in (
                ImportMode.FBX_MATCH.value,
                ImportMode.UEANIM_DIRECT.value,
                ImportMode.LINK_EXISTING.value,
            )
            and persisted.get("import_mode") == import_mode
            and persisted.get("task_identity") == task_identity
            and persisted.get("imported_actions")
            and reusable_import_results(
                persisted, persisted_checkpoint, current_manifest_sources,
            )
        ):
            resolved = [
                resolve_imported_action(
                    bpy, item,
                    staged_validator=lambda path, item=item: self._validate_staged_blend(context, path, item),
                )[0]
                for item in persisted["imported_actions"]
            ]
            if all(resolved):
                context.log_info(self.get_stage_name(), "复用当前任务已持久化的导入结果")
                return True
            if import_mode == ImportMode.LINK_EXISTING.value:
                context.log_warning(
                    self.get_stage_name(),
                    "已持久化动作不完整，将重新解析且保留现有外部动作",
                )
            context.imported_assets = None
        
        handlers = {
            ImportMode.FBX_MATCH.value: self._import_fbx_match,
            ImportMode.UEANIM_DIRECT.value: self._import_ueanim_direct,
            ImportMode.LINK_EXISTING.value: self._import_link_existing,
        }
        
        handler = handlers.get(import_mode)
        if not handler:
            context.log_error(self.get_stage_name(), f"不支持的导入模式: {import_mode}")
            return False
        
        try:
            return handler(context)
        finally:
            self._finish_progress()

    def _import_fbx_match(self, context: PipelineContext) -> bool:
        """匹配已导入的FBX动画"""
        manifest = DictionaryManifest.from_dict(context.dictionary_manifest)
        task_identity = self._task_identity(context)
        collection = ImportedAssetCollection(
            import_mode=ImportMode.FBX_MATCH.value,
            task_identity=task_identity,
        )

        all_actions = list(bpy.data.actions)
        source_skeleton = self._source_skeleton(context)
        source_subset = set(task_identity["source_subset"])
        task_id_by_ref = {
            self._source_manifest_ref(item["skeleton"], item["folder"], item["name"]): item["task_id"]
            for item in self._current_manifest_sources(context)
        }
        scoped_task = bool(source_skeleton or source_subset)
        claimed_actions = set()
        matched_count = 0
        
        for skel, folder_path, anim_name, anim_entry in manifest.get_all_animations():
            if source_skeleton and skel != source_skeleton:
                continue
            source_file = anim_entry.source_file
            normalized_source = self._normalized_source_path(source_file)
            if source_subset and normalized_source not in source_subset:
                continue
            hashed_name = anim_entry.hashed_name
            
            verified_candidates = []
            untagged_candidates = []
            expected_ref = self._source_manifest_ref(skel, folder_path, anim_name)
            for action in all_actions:
                if datablock_identity(action) in claimed_actions:
                    continue
                if scoped_task:
                    action_source = action.get("Pipeline_Source", "")
                    action_ref = action.get("Pipeline_SourceRef", "")
                    if not action_source and not action_ref:
                        if action.name == hashed_name:
                            untagged_candidates.append(action)
                        continue
                    if normalized_source and self._normalized_source_path(action_source) != normalized_source:
                        continue
                    if action_ref and action_ref != expected_ref:
                        continue
                    verified_candidates.append(action)
                elif hashed_name.lower() in action.name.lower():
                    verified_candidates.append(action)

            candidates = verified_candidates or untagged_candidates
            target_action = candidates[0] if len(candidates) == 1 else None
            
            if target_action:
                claimed_actions.add(datablock_identity(target_action))
                target_action = copy_action_for_pipeline(target_action)
                target_action["Pipeline_Owned"] = True
                target_action["Pipeline_Source"] = source_file
                target_action["Pipeline_SourceRef"] = expected_ref
                imported = ImportedAction(
                    action_name=target_action.name,
                    source_manifest_ref=expected_ref,
                    source_file=source_file,
                    import_status="success",
                    frame_range=tuple(target_action.frame_range) if target_action.frame_range else (0, 0),
                    task_id=task_id_by_ref.get(expected_ref, ""),
                )
                collection.imported_actions.append(imported)
                matched_count += 1
            else:
                collection.failed_imports.append({
                    "source_file": anim_entry.source_file,
                    "error": "ambiguous matching actions" if candidates else "未找到匹配的动作",
                    "expected_name": hashed_name
                })
        
        context.imported_assets = collection.to_dict()
        expected_task_ids = [
            item["task_id"] for item in task_identity.get("source_identities", [])
        ]
        if matched_count > 0 and not collection.failed_imports:
            context.set_config("import_checkpoint", {
                "state": "complete",
                "expected_task_ids": expected_task_ids,
                "failed_imports": [],
            })
        context.log_info(self.get_stage_name(), f"成功匹配 {matched_count} 个动作")
        return matched_count > 0 and not collection.failed_imports
    
    def _import_ueanim_direct(self, context: PipelineContext) -> bool:
        """直接导入.ueanim文件"""
        try:
            from ..importers.animation.operator_anim_ueformat import get_ueformat_api
            UEFormatImport, _, UEAnimOptions, _ = get_ueformat_api()
            if not UEFormatImport:
                context.log_error(self.get_stage_name(), "无法加载UEFormat API")
                return False
        except Exception as e:
            context.log_error(self.get_stage_name(), f"导入UEFormat模块失败: {e}")
            return False
        
        manifest = DictionaryManifest.from_dict(context.dictionary_manifest)
        collection = ImportedAssetCollection(
            import_mode=ImportMode.UEANIM_DIRECT.value,
            task_identity=self._task_identity(context),
        )
        
        target_arm = None
        target_arm_name = context.get_config("target_armature", "")
        if target_arm_name:
            obj = bpy.data.objects.get(target_arm_name)
            if obj and obj.type == 'ARMATURE':
                target_arm = obj
            else:
                context.log_error(
                    self.get_stage_name(), f"目标骨架不存在或不是骨架对象: {target_arm_name}"
                )
                context.imported_assets = collection.to_dict()
                return False
        else:
            configured_skeleton = self._source_skeleton(context)
            if configured_skeleton:
                exact = []
                fuzzy = []
                for obj in bpy.data.objects:
                    if obj.type != 'ARMATURE':
                        continue
                    skeleton_name = str(obj.get("FModel_Skeleton", obj.name))
                    if skeleton_name == configured_skeleton:
                        exact.append(obj)
                    elif (configured_skeleton.lower() in skeleton_name.lower()
                          or skeleton_name.lower() in configured_skeleton.lower()):
                        fuzzy.append(obj)
                candidates = exact or fuzzy
                if len(candidates) == 1:
                    target_arm = candidates[0]
                    context.log_info(
                        self.get_stage_name(),
                        f"通过骨架名匹配到目标骨架: {target_arm.name}",
                    )
                elif len(candidates) > 1:
                    context.log_error(
                        self.get_stage_name(), f"骨架映射不唯一: {configured_skeleton}"
                    )
                    context.imported_assets = collection.to_dict()
                    return False

        target_skeleton_name = ""
        if target_arm:
            target_skeleton_metadata = str(target_arm.get("FModel_Skeleton", "") or "")
            source_skeleton = self._source_skeleton(context)
            if target_skeleton_metadata and source_skeleton and target_skeleton_metadata != source_skeleton:
                context.log_error(
                    self.get_stage_name(),
                    f"目标骨架元数据不匹配: 任务={source_skeleton}, 目标={target_skeleton_metadata}",
                )
                context.imported_assets = collection.to_dict()
                return False
            target_skeleton_name = target_skeleton_metadata or target_arm.name

        try:
            options = UEAnimOptions(
                scale_factor=context.get_config("import_scale", 0.01),
                rotation_only=context.get_config("rotation_only", False),
                import_curves=context.get_config("import_curves", False),
                override_skeleton=target_arm,
                strict_fmodel_skeleton=True,
            )
        except TypeError:
            # Legacy UEFormat has no strict identity option.
            options = UEAnimOptions(
                scale_factor=context.get_config("import_scale", 0.01),
                rotation_only=context.get_config("rotation_only", False),
                import_curves=context.get_config("import_curves", False),
            )
        importer = UEFormatImport(options)
        
        valid_entries = []
        source_skeleton = self._source_skeleton(context)
        task_identity = self._task_identity(context)
        source_subset = set(task_identity["source_subset"])
        for manifest_index, (skel, folder_path, anim_name, anim_entry) in enumerate(manifest.get_all_animations()):
            if source_skeleton and skel != source_skeleton:
                continue
            source_file = anim_entry.source_file
            if not source_file or not os.path.exists(source_file):
                continue
            if not source_file.lower().endswith('.ueanim'):
                continue
            if source_subset and self._normalized_source_path(source_file) not in source_subset:
                continue
            source_identity = make_manifest_source(
                manifest_index, skel, folder_path, anim_name, source_file,
                anim_entry.content_hash,
            )
            valid_entries.append((skel, folder_path, anim_name, source_file, source_identity))

        if not valid_entries:
            context.log_warning(self.get_stage_name(), "没有可导入的.ueanim文件")
            context.imported_assets = collection.to_dict()
            return False

        resume_enabled = bool(context.get_config("import_resume_enabled", True))
        checkpoint = context.get_config("import_checkpoint", {}) if resume_enabled else {}
        if not isinstance(checkpoint, dict):
            checkpoint = {}
        expected_task_ids = [entry[4]["task_id"] for entry in valid_entries]
        batch_identity = make_batch_identity(
            [entry[4] for entry in valid_entries],
            import_settings_fingerprint(context.config),
            task_identity["target_identity"],
        )
        if checkpoint and (
            checkpoint.get("task_identity") != task_identity
            or checkpoint.get("batch_identity") != batch_identity
        ):
            # Checkpoints contain staged actions for one target task only.
            checkpoint = {}
            context.set_config("import_checkpoint", {})
            context.log_warning(self.get_stage_name(), "导入断点任务不匹配，已重置断点状态")

        batch_size = max(1, int(context.get_config("import_batch_size", 300)))
        max_cycles = max(1, int(context.get_config("max_import_cycles", 200)))
        max_consecutive_failures = max(20, int(context.get_config("max_consecutive_failures", 120)))
        large_threshold = max(1, int(context.get_config("large_import_threshold", 2000)))
        if len(valid_entries) >= large_threshold and batch_size > 120:
            old_size = batch_size
            batch_size = 120
            context.log_warning(
                self.get_stage_name(),
                f"检测到巨量导入({len(valid_entries)}). 自动降批次: {old_size} -> {batch_size}"
            )

        min_batch = max(10, batch_size // 4)
        max_batch = max(batch_size, batch_size * 4)

        imported_count = 0
        cycle_count = 0
        invocation_cycle_count = 0
        consecutive_failures = 0
        staging_validation_failed = False
        old_actions = set(bpy.data.actions)
        auxiliary_actions = {}
        action_by_imported = {}

        if resume_enabled and checkpoint:
            try:
                cursor = int(checkpoint.get("cursor", 0))
                cycle_count = int(checkpoint.get("cycle_count", 0))
                staged = checkpoint.get("imported_actions", [])
                fails = checkpoint.get("failed_imports", [])
                pending_sources = list(checkpoint.get("pending_sources", []))
                if pending_sources:
                    pending_ids = [item.get("entry_id") for item in pending_sources]
                    entries_by_id = {entry[4]["entry_id"]: entry for entry in valid_entries}
                    valid_entries = [
                        entries_by_id[entry_id] for entry_id in pending_ids
                        if entry_id in entries_by_id
                    ]
                    cursor = 0
                if staged:
                    staged_col = ImportedAssetCollection.from_dict({
                        "import_mode": ImportMode.UEANIM_DIRECT.value,
                        "imported_actions": staged,
                        "failed_imports": fails,
                    })
                    collection.imported_actions.extend(staged_col.imported_actions)
                    collection.failed_imports.extend(staged_col.failed_imports)
                    for imported in collection.imported_actions:
                        action_by_imported[id(imported)] = next(
                            (candidate for candidate in bpy.data.actions
                             if candidate.name == imported.action_name
                             and (not imported.source_file or candidate.get("Pipeline_Source", "") == imported.source_file)
                             and (not imported.source_manifest_ref or candidate.get("Pipeline_SourceRef", "") == imported.source_manifest_ref)),
                            None,
                        )
                context.log_info(self.get_stage_name(), f"断点恢复: cursor={cursor}, staged={len(collection.imported_actions)}")
            except Exception as e:
                cursor = 0
                context.log_warning(self.get_stage_name(), f"断点恢复解析失败，回退全量: {e}")
        else:
            cursor = 0

        def _persist_checkpoint(next_cursor):
            pending = valid_entries[next_cursor:]
            pending_sources = [entry[4] for entry in pending]
            pending_entry_ids = {item["entry_id"] for item in pending_sources}
            failed_paths = {
                self._normalized_source_path(failure["source_file"])
                for failure in collection.failed_imports if failure.get("source_file")
            }
            for entry in valid_entries[:next_cursor]:
                if entry[4]["normalized_path"] in failed_paths and entry[4]["entry_id"] not in pending_entry_ids:
                    pending_sources.append(entry[4])
            context.set_config("import_checkpoint", {
                "state": "in_progress",
                "task_identity": task_identity,
                "batch_identity": batch_identity,
                "expected_task_ids": expected_task_ids,
                "cursor": next_cursor,
                "cycle_count": cycle_count,
                "pending_sources": pending_sources,
                "imported_actions": [a.__dict__ for a in collection.imported_actions],
                "failed_imports": collection.failed_imports,
            })
            context.imported_assets = collection.to_dict()
            # Persist outside Blender before later unloading the imported Actions.
            save_checkpoint = getattr(context, "persist_checkpoint", None)
            if save_checkpoint:
                save_checkpoint()

        def _flush_actions_to_temp_blend(skel_name: str, next_cursor: int):
            nonlocal staging_validation_failed
            if not collection.imported_actions:
                return False
            chunk_actions = []
            chunk_entries = []
            for ia in collection.imported_actions:
                if ia.import_status != "success":
                    continue
                act = action_by_imported.get(id(ia))
                if act:
                    chunk_actions.append(act)
                    chunk_entries.append(ia)
                    chunk_actions.extend(auxiliary_actions.get(datablock_identity(act), []))
            if not chunk_actions:
                return False

            if context.get_config("clean_after_import", False):
                from ...Universal_tools.Animations.clean_Anim import clean_action
                for action in chunk_actions:
                    if not supports_legacy_cleanup(action):
                        context.log_warning(
                            self.get_stage_name(),
                            f"跳过不支持Blender 5 slots的旧式动作清理: {action.name}",
                        )
                        continue
                    clean_action(
                        action,
                        threshold=float(context.get_config("clean_threshold", 0.001)),
                        clean_channels=True,
                        clean_flat=bool(context.get_config("clean_flat_channels", True)),
                    )

            output_dir = bpy.path.abspath(context.get_config("output_dir", ""))
            if not output_dir:
                return False
            os.makedirs(output_dir, exist_ok=True)
            temp_dir = self._staging_task_dir(context)
            os.makedirs(temp_dir, exist_ok=True)
            chunk_index = cycle_count
            temp_blend = staging_chunk_path(temp_dir, skel_name, chunk_index)
            normalized_temp_blend = self._normalized_source_path(temp_blend)

            for ia in chunk_entries:
                action = action_by_imported[id(ia)]
                action["Pipeline_RequestedActionName"] = ia.action_name
                action["Pipeline_StagedBlend"] = normalized_temp_blend
                action["Pipeline_Source"] = ia.source_file
                action["Pipeline_SourceRef"] = ia.source_manifest_ref

            bpy.data.libraries.write(filepath=temp_blend, datablocks=set(chunk_actions), fake_user=True)
            context.log_info(
                self.get_stage_name(),
                f"动作分块落盘: {os.path.basename(temp_blend)} ({len(chunk_actions)} actions)"
            )

            requested_names = [ia.action_name for ia in chunk_entries]
            validation_actions = []
            try:
                with bpy.data.libraries.load(temp_blend, link=False) as (data_from, data_to):
                    available_names = list(data_from.actions)
                    if any(name not in available_names for name in requested_names):
                        raise ValueError("暂存动作包缺少预期动作")
                    data_to.actions = list(requested_names)
                returned_actions = list(data_to.actions)
                validation_actions = [action for action in returned_actions if action is not None]
                if len(returned_actions) != len(requested_names) or len(validation_actions) != len(requested_names):
                    raise ValueError("暂存动作包返回动作数量不匹配")
                for ia, action in zip(chunk_entries, returned_actions):
                    if action.get("Pipeline_RequestedActionName", "") != ia.action_name:
                        raise ValueError(f"暂存动作请求名称不匹配: {ia.action_name}")
                    if self._normalized_source_path(
                        action.get("Pipeline_StagedBlend", "")
                    ) != normalized_temp_blend:
                        raise ValueError(f"暂存动作包路径不匹配: {ia.action_name}")
                    if action.get("Pipeline_SourceRef", "") != ia.source_manifest_ref:
                        raise ValueError(f"暂存动作任务不匹配: {ia.action_name}")
            except Exception as exc:
                staging_validation_failed = True
                context.log_error(self.get_stage_name(), f"验证暂存动作包失败 {temp_blend}: {exc}")
                try:
                    os.remove(temp_blend)
                except OSError:
                    pass
                return False
            finally:
                for action in validation_actions:
                    try:
                        bpy.data.actions.remove(action)
                    except Exception:
                        pass

            staged_entries = []
            for ia in chunk_entries:
                staged_entries.append(ImportedAction(
                    action_name=ia.action_name,
                    source_manifest_ref=ia.source_manifest_ref,
                    source_file=ia.source_file,
                    staged_blend=temp_blend,
                    import_status="staged",
                    frame_range=ia.frame_range,
                    task_id=ia.task_id,
                ))

            flushed_entry_ids = {id(ia) for ia in chunk_entries}
            collection.imported_actions = [
                ia for ia in collection.imported_actions
                if id(ia) not in flushed_entry_ids
            ]
            collection.imported_actions.extend(staged_entries)

            # The staging bundle must be represented in the durable checkpoint
            # before its source actions can be removed from Blender memory.
            _persist_checkpoint(next_cursor)
            context.imported_assets = collection.to_dict()

            for act in chunk_actions:
                try:
                    bpy.data.actions.remove(act)
                except Exception:
                    pass
            return True

        total_entries = len(valid_entries)
        context.log_info(
            self.get_stage_name(),
            f"开始导入 {total_entries} 个动画，批次大小={batch_size}，目标骨架={target_skeleton_name or '未指定'}"
        )
        self._report_progress(context, 0, total_entries, self.get_stage_name())
        aborted_by_failures = False
        self._report_progress(context, 0, total_entries, self.get_stage_name())
        while cursor < total_entries:
            batch = valid_entries[cursor:cursor + batch_size]
            cycle_count += 1
            invocation_cycle_count += 1
            if invocation_cycle_count > max_cycles:
                context.log_error(self.get_stage_name(), f"超过最大导入循环次数: {max_cycles}")
                _persist_checkpoint(cursor)
                break

            cycle_start = time.perf_counter()
            batch_success = 0
            batch_failed = 0
            processed_in_batch = 0

            for skel, folder_path, anim_name, source_file, source_identity in batch:
                processed_in_batch += 1
                target_state = animation_state(target_arm)
                try:
                    importer.import_file(os.path.normpath(source_file))
                    new_actions = owned_actions(list(bpy.data.actions), old_actions)
                    action = None
                    auxiliary = []

                    if new_actions:
                        action, auxiliary = select_primary_action(new_actions)
                    if action:
                        source_ref = self._source_manifest_ref(skel, folder_path, anim_name)
                        action["Pipeline_Source"] = source_file
                        action["Pipeline_SourceRef"] = source_ref
                        if target_skeleton_name:
                            action["Pipeline_Skeleton"] = target_skeleton_name
                        imported = ImportedAction(
                            action_name=action.name,
                            source_manifest_ref=source_ref,
                            source_file=source_file,
                            import_status="success",
                            frame_range=tuple(action.frame_range) if action.frame_range else (0, 0),
                            task_id=source_identity["task_id"],
                        )
                        collection.imported_actions.append(imported)
                        action_by_imported[id(imported)] = action
                        auxiliary_actions[datablock_identity(action)] = auxiliary
                        collection.failed_imports = [
                            failure for failure in collection.failed_imports
                            if failure.get("source_file") != source_file
                        ]
                        old_actions = set(bpy.data.actions)
                        imported_count += 1
                        batch_success += 1
                        consecutive_failures = 0
                    else:
                        for extra in new_actions:
                            try:
                                bpy.data.actions.remove(extra)
                            except Exception:
                                pass
                        batch_failed += 1
                        consecutive_failures += 1
                        collection.failed_imports.append({
                            "source_file": source_file,
                            "error": f"导入后缺少唯一骨架主动作: {len(new_actions)}",
                        })
                        old_actions = set(bpy.data.actions)
                except Exception as e:
                    partial_actions = owned_actions(list(bpy.data.actions), old_actions)
                    for partial in partial_actions:
                        try:
                            bpy.data.actions.remove(partial)
                        except Exception:
                            pass
                    old_actions = set(bpy.data.actions)
                    collection.failed_imports.append({"source_file": source_file, "error": str(e)})
                    batch_failed += 1
                    consecutive_failures += 1
                finally:
                    restore_animation_state(target_arm, target_state)

                if consecutive_failures >= max_consecutive_failures:
                    context.log_error(
                        self.get_stage_name(),
                        f"连续失败达到阈值({max_consecutive_failures})，提前中止导入"
                    )
                    aborted_by_failures = True
                    break

            if consecutive_failures >= max_consecutive_failures:
                # Preserve successes before the failing source in this batch.
                next_cursor = cursor + processed_in_batch
                if not _flush_actions_to_temp_blend(
                    target_skeleton_name or "Generic", next_cursor
                ):
                    _persist_checkpoint(next_cursor)
                break

            # 内存保护：每批次导入后落地并卸载动作，防止巨量动作常驻内存
            next_cursor = cursor + len(batch)
            if not _flush_actions_to_temp_blend(
                target_skeleton_name or "Generic", next_cursor
            ):
                _persist_checkpoint(next_cursor)

            cycle_cost = time.perf_counter() - cycle_start
            fail_ratio = (batch_failed / len(batch)) if batch else 0.0

            context.log_info(
                self.get_stage_name(),
                (
                    f"批次 {cycle_count}: "
                    f"范围[{cursor}-{cursor + len(batch) - 1}] "
                    f"成功 {batch_success} 失败 {batch_failed} "
                    f"耗时 {cycle_cost:.2f}s "
                    f"batch={batch_size}"
                )
            )

            # 自适应批次大小：慢批/高失败率降速，快批低失败率增速
            if fail_ratio > 0.25 or cycle_cost > 8.0:
                batch_size = max(min_batch, int(batch_size * 0.75))
            elif fail_ratio < 0.05 and cycle_cost < 2.5:
                batch_size = min(max_batch, int(batch_size * 1.2))

            cursor += len(batch)
            self._report_progress(context, cursor, total_entries, self.get_stage_name())

            # 让Blender主线程有机会处理UI与事件，降低卡死感
            try:
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            except Exception:
                pass
        resumed_count = 0
        if resume_enabled and cursor >= total_entries and not aborted_by_failures and not collection.failed_imports:
            valid_resumed = []
            for imported_action in collection.to_dict()["imported_actions"]:
                action, error = resolve_imported_action(
                    bpy, imported_action,
                    staged_validator=lambda path, item=imported_action: self._validate_staged_blend(context, path, item),
                )
                if not action:
                    context.log_error(self.get_stage_name(), error)
                    continue
                valid_resumed.append(imported_action)
            resumed_count = len(valid_resumed)
            if resumed_count != len(collection.imported_actions):
                context.imported_assets = collection.to_dict()
                self._finish_progress()
                return False
            context.set_config("import_checkpoint", {
                "state": "complete",
                "task_identity": task_identity,
                "batch_identity": batch_identity,
                "expected_task_ids": expected_task_ids,
                "cursor": total_entries,
                "cycle_count": cycle_count,
                "pending_sources": [],
                "imported_actions": [a.__dict__ for a in collection.imported_actions],
                "failed_imports": [],
            })
        if cursor >= total_entries and not aborted_by_failures:
            self._report_progress(context, total_entries, total_entries, self.get_stage_name())
        else:
            self._finish_progress()
        if collection.failed_imports:
            report_path = self._write_failure_report(context, collection)
            if report_path:
                context.log_warning(self.get_stage_name(), f"失败报告已导出: {report_path}")
        context.imported_assets = collection.to_dict()
        context.log_info(
            self.get_stage_name(),
            f"成功导入 {imported_count} 个动画，累计失败 {len(collection.failed_imports)}"
        )
        if staging_validation_failed or aborted_by_failures or cursor < total_entries or collection.failed_imports:
            return False
        return imported_count + resumed_count > 0
    
    def _import_link_existing(self, context: PipelineContext) -> bool:
        """链接现有资产库"""
        collection = ImportedAssetCollection(
            import_mode=ImportMode.LINK_EXISTING.value,
            task_identity=self._task_identity(context),
        )

        source_skeleton = self._source_skeleton(context)

        def sidecar_action_refs(sidecar):
            skeletons = [
                name for name, tree in sidecar.items()
                if name != "__processed_sources__" and isinstance(name, str)
                and name and isinstance(tree, dict)
            ]
            selected_skeletons = [source_skeleton] if source_skeleton else skeletons
            if source_skeleton and source_skeleton not in skeletons:
                return [], {}

            refs = {}

            def collect_actions(tree, folders):
                entries = tree.get("__actions__", [])
                if not isinstance(entries, list):
                    raise ValueError("库包动作目录格式无效")
                catalog_path = "/".join(folders) or "Uncategorized"
                for entry in entries:
                    if not isinstance(entry, (list, tuple)) or not entry or not isinstance(entry[0], str) or not entry[0]:
                        raise ValueError("库包动作元数据无效")
                    action_name = entry[0]
                    ref = f"{skeleton}/{catalog_path}/{action_name}"
                    if action_name in refs:
                        raise ValueError(f"库包动作名称不唯一: {action_name}")
                    refs[action_name] = ref
                for name in sorted(tree):
                    if name == "__actions__":
                        continue
                    child = tree[name]
                    if not isinstance(name, str) or not name or not isinstance(child, dict):
                        raise ValueError("库包目录格式无效")
                    collect_actions(child, folders + [name])

            for skeleton in selected_skeletons:
                collect_actions(sidecar[skeleton], [])
            return selected_skeletons, refs
        
        source_dir = bpy.path.abspath(context.get_config("source_dir", ""))
        if not source_dir or not os.path.exists(source_dir):
            context.log_error(self.get_stage_name(), "源目录无效")
            return False
        
        blend_files = [f for f in os.listdir(source_dir) if f.endswith('.blend')]
        if not blend_files:
            context.log_warning(self.get_stage_name(), "未找到blend文件")
            return False
        
        source_subset = set(self._task_identity(context)["source_subset"])
        scoped_task = bool(source_skeleton or source_subset)
        source_dir = self._normalized_source_path(context.get_config("source_dir", ""))
        imported_count = 0
        task_source_failed = False
        for blend_file in blend_files:
            blend_path = os.path.join(source_dir, blend_file)
            existing_actions = {id(action) for action in bpy.data.actions}
            staged_actions = []
            sidecar_path = os.path.splitext(blend_path)[0] + "_manifest.json"
            declared_in_scope = not source_skeleton
            try:
                with open(sidecar_path, "r", encoding="utf-8") as handle:
                    sidecar = json.load(handle)
                # A malformed bundle can still be ignored when its declared
                # skeleton is outside this task. In-scope bundles continue
                # through the strict sidecar validation below.
                if source_skeleton and isinstance(sidecar, dict):
                    declared_skeletons = {
                        name for name, tree in sidecar.items()
                        if name != "__processed_sources__" and isinstance(name, str)
                    }
                    if source_skeleton not in declared_skeletons:
                        continue
                    declared_in_scope = True
                skeletons, action_refs = sidecar_action_refs(sidecar)
                processed_sources = sidecar.get("__processed_sources__", [])
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                if not declared_in_scope and source_skeleton not in os.path.splitext(blend_file)[0]:
                    continue
                task_source_failed = True
                collection.failed_imports.append({
                    "source_file": blend_path,
                    "error": f"无法安全确认库包骨架元数据: {exc}",
                })
                continue
            if scoped_task:
                if source_skeleton and source_skeleton not in skeletons:
                    continue
                if source_subset:
                    if not isinstance(processed_sources, list):
                        task_source_failed = True
                        collection.failed_imports.append({
                            "source_file": blend_path,
                            "error": "库包缺少可验证的源文件元数据",
                        })
                        continue
                    scoped_sources = set()
                    for source in processed_sources:
                        if not isinstance(source, str):
                            continue
                        source_path = source.split("::", 1)[-1]
                        if os.path.isabs(source_path):
                            scoped_sources.add(self._normalized_source_path(source_path))
                        elif source_dir:
                            scoped_sources.add(self._normalized_source_path(os.path.join(source_dir, source_path)))
                    # Linking is bundle-granular. A partial overlap would link
                    # actions belonging to another task, so only accept a fully
                    # provable subset.
                    if not scoped_sources or not scoped_sources.issubset(source_subset):
                        continue
            try:
                normalized_bundle = self._normalized_source_path(blend_path)
                existing_local = {
                    action.name: action for action in bpy.data.actions
                    if action.get("Pipeline_SourceBundle", "") == normalized_bundle
                    and action.name in action_refs
                }
                for action_name, action in existing_local.items():
                    staged_actions.append(ImportedAction(
                        action_name=action.name,
                        source_manifest_ref=action_refs[action_name],
                        source_file=blend_path,
                        source_bundle=blend_path,
                        import_status="success",
                        frame_range=tuple(action.frame_range) if action.frame_range else (0, 0),
                    ))
                if set(existing_local) == set(action_refs):
                    loaded_actions = []
                else:
                    with bpy.data.libraries.load(blend_path, link=True) as (data_from, data_to):
                        data_to.actions = [
                            action for action in data_from.actions
                            if getattr(action, "name", action) in action_refs
                            and getattr(action, "name", action) not in existing_local
                        ]
                    loaded_actions = data_to.actions
                loaded_names = [
                    getattr(action, "name", action) for action in loaded_actions if action
                ]
                missing_names = {
                    name for name in action_refs
                    if name not in existing_local and loaded_names.count(name) != 1
                }
                if missing_names:
                    task_source_failed = True
                    collection.failed_imports.append({
                        "source_file": blend_path,
                        "error": f"库包动作未完整加载: {', '.join(sorted(missing_names))}",
                    })
                    continue
                for action in loaded_actions:
                    if action:
                        source_manifest_ref = action_refs.get(action.name)
                        if not source_manifest_ref:
                            task_source_failed = True
                            collection.failed_imports.append({
                                "source_file": blend_path,
                                "error": f"库包动作缺少可验证的目录元数据: {action.name}",
                            })
                            continue
                        local_action = copy_action_for_pipeline(action)
                        local_action["Pipeline_Owned"] = True
                        local_action["Pipeline_SourceBundle"] = self._normalized_source_path(blend_path)
                        local_action["Pipeline_SourceRef"] = source_manifest_ref
                        imported = ImportedAction(
                            action_name=local_action.name,
                            source_manifest_ref=source_manifest_ref,
                            source_file=blend_path,
                            source_bundle=blend_path,
                            import_status="success",
                            frame_range=tuple(local_action.frame_range) if local_action.frame_range else (0, 0)
                        )
                        staged_actions.append(imported)
            except Exception as e:
                for action in list(bpy.data.actions):
                    if id(action) not in existing_actions:
                        try:
                            bpy.data.actions.remove(action)
                        except (ReferenceError, RuntimeError, ValueError):
                            pass
                task_source_failed = True
                collection.failed_imports.append({"source_file": blend_path, "error": str(e)})
            else:
                collection.imported_actions.extend(staged_actions)
                imported_count += len(staged_actions)
        
        context.imported_assets = collection.to_dict()
        context.log_info(self.get_stage_name(), f"成功链接 {imported_count} 个动作")
        return imported_count > 0 and not task_source_failed


# ============================================================
# 阶段3: 建立资产库
# ============================================================

class LibraryBuildingStage(PipelineStageBase):
    """建立资产库阶段 - 标记资产、分配目录、生成清单、保存库文件"""
    
    def get_stage_name(self) -> str:
        return PipelineStage.LIBRARY_BUILDING.value
    
    def validate_input(self, context: PipelineContext) -> tuple[bool, str]:
        if not context.imported_assets:
            return False, "缺少导入资产数据"
        if not context.dictionary_manifest:
            return False, "缺少字典清单数据"
        return True, ""

    def _bundle_slot(self, filename: str, base_name: str):
        """Return the exact v2 volume slot named by one bundle artifact."""
        if filename in (f"{base_name}.blend", f"{base_name}_manifest.json"):
            return 1
        matched = re.fullmatch(
            rf"{re.escape(base_name)}_Vol_([1-9][0-9]*)(?:\.blend|_manifest\.json)",
            filename,
        )
        return int(matched.group(1)) if matched else None

    def _occupied_bundle_slots(self, output_dir: str, base_name: str):
        """Treat either half of a bundle pair as occupied until it is recovered."""
        return {
            slot for filename in os.listdir(output_dir)
            if (slot := self._bundle_slot(filename, base_name)) is not None
        }

    def _plan_bundle_slots(self, occupied_slots, chunk_count: int):
        """Allocate immutable output slots before any temporary bundle is written."""
        next_slot = max(occupied_slots, default=0) + 1
        return list(range(next_slot, next_slot + chunk_count))

    def _remove_owned_force_rebuild_bundles(self, output_dir, base_name, skeleton_name):
        """Remove only complete, exact, single-skeleton legacy pairs."""
        owned = self._owned_force_rebuild_bundle_files(output_dir, base_name, skeleton_name)
        for filename in owned:
            os.remove(os.path.join(output_dir, filename))
        return owned

    def _owned_force_rebuild_bundle_files(self, output_dir, base_name, skeleton_name):
        """Identify exact exclusive pairs; callers may schedule, never guess removal."""
        owned = []
        for filename in os.listdir(output_dir):
            if self._bundle_slot(filename, base_name) is None or not filename.endswith(".blend"):
                continue
            stem = os.path.splitext(filename)[0]
            sidecar_name = f"{stem}_manifest.json"
            sidecar_path = os.path.join(output_dir, sidecar_name)
            if not os.path.isfile(sidecar_path):
                continue
            try:
                with open(sidecar_path, "r", encoding="utf-8") as handle:
                    sidecar = json.load(handle)
                skeletons = set(sidecar) - {"__processed_sources__"}
            except (OSError, ValueError, TypeError):
                continue
            if skeletons == {skeleton_name}:
                owned.extend((filename, sidecar_name))
        return owned

    def _validate_published_bundle_actions(self, blend_path, expected_action_names):
        """Prove the physical bundle contains every action before it gains coverage."""
        with bpy.data.libraries.load(blend_path, link=False) as (data_from, _):
            actual_action_names = set(data_from.actions)
        expected_action_names = set(expected_action_names)
        if actual_action_names != expected_action_names:
            missing = sorted(expected_action_names - actual_action_names)
            unexpected = sorted(actual_action_names - expected_action_names)
            raise ValueError(
                f"published bundle action inventory mismatch: missing={missing}, unexpected={unexpected}"
            )
    
    def execute(self, context: PipelineContext) -> bool:
        from ...Assets.Utils import utils_asset
        
        output_dir = bpy.path.abspath(context.get_config("output_dir", ""))
        library_name = context.get_config("library_name", "Asset_Library")
        imported_actions = context.imported_assets.get("imported_actions", [])

        try:
            validate_imported_action_set(imported_actions)
            validate_generated_output_filenames(
                library_name,
                (item["source_manifest_ref"].split("/", 1)[0] for item in imported_actions),
            )
        except (KeyError, ValueError) as exc:
            context.log_error(self.get_stage_name(), str(exc))
            return False
        
        if not output_dir:
            context.log_error(self.get_stage_name(), "未指定输出目录")
            return False
        
        if not ensure_dir(output_dir):
            context.log_error(self.get_stage_name(), "创建输出目录失败")
            return False
        
        try:
            manifest_path = library_index_path(output_dir, library_name)
        except ValueError as exc:
            context.log_error(self.get_stage_name(), str(exc))
            return False
        try:
            retry_staged_cleanup(
                context,
                context.get_config("pending_staged_cleanup", []),
                self._staging_task_dir(context),
            )
        except OSError as exc:
            context.log_warning(self.get_stage_name(), f"重试清理暂存动作包失败: {exc}")
        try:
            # Resolve pending cleanup before deriving the authoritative bundle set.
            recover_library_transaction(manifest_path)
            manifest = load_complete_v2_manifest(manifest_path, output_dir)
        except Exception as e:
            context.log_error(self.get_stage_name(), f"恢复或读取已有v2资产库索引失败: {e}")
            return False
        authoritative_v2_manifest = manifest
        migrated_legacy_inventory = False
        if manifest is None:
            manifest = AssetLibraryManifest(
                version="2.0", library_name=library_name,
                library_id=str(uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(output_dir))),
                library_path=output_dir
            )
            try:
                def load_action_names(blend_path):
                    with bpy.data.libraries.load(blend_path, link=True) as (data_from, _):
                        return list(data_from.actions)

                manifest = migrate_verified_legacy_manifest(
                    output_dir, manifest, load_action_names
                )
            except (OSError, ValueError) as exc:
                context.log_error(self.get_stage_name(), f"迁移旧版资产库失败: {exc}")
                return False
            migrated_legacy_inventory = bool(manifest.bundles)
            if migrated_legacy_inventory:
                authoritative_v2_manifest = manifest
        else:
            manifest.library_name = library_name
            if not manifest.library_id:
                manifest.library_id = str(uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(output_dir)))
            manifest.library_path = output_dir
        force_rebuild = bool(context.get_config("force_rebuild", False))
        source_dir = bpy.path.abspath(context.get_config("source_dir", ""))
        recovered_provenance = []
        if authoritative_v2_manifest is not None and not force_rebuild:
            for imported_action in imported_actions:
                if imported_action.get("source_bundle"):
                    continue
                source_key = self._source_key_for_imported(imported_action, source_dir)
                destination_bundle = self._published_bundle_for_source(
                    output_dir, authoritative_v2_manifest, source_key
                )
                if destination_bundle:
                    recovered_provenance.append((imported_action, destination_bundle))
        if recovered_provenance:
            self._persist_published_provenance(context, recovered_provenance)
        # Reload only the completed pairs named by the recovered v2 index.
        self._reload_actions_from_blends(output_dir, manifest, imported_actions)
        try:
            # Every build mode must validate the complete imported set before
            # deriving or writing any replacement bundle.
            validate_imported_action_set(imported_actions)
        except ValueError as exc:
            context.log_error(self.get_stage_name(), str(exc))
            return False
        resolved_actions = {}
        for imported_action in imported_actions:
            action, selection_error = resolve_imported_action(
                bpy, imported_action,
                staged_validator=lambda path, item=imported_action: self._validate_staged_blend(context, path, item),
            )
            if not action:
                context.log_error(self.get_stage_name(), selection_error)
                return False
            resolved_actions[id(imported_action)] = action
        staged_blend_references = track_staged_blend_references(imported_actions)
        print(f"[FModel DEBUG] LibraryBuilding: output_dir={output_dir}, actions={len(imported_actions)}")
        chunk_size = max(1, int(context.get_config("chunk_size", 500)))
        generate_thumbnails = bool(context.get_config("generate_thumbnails", True))
        thumb_resolution = str(context.get_config("thumb_resolution", "256"))
        
        skeleton_groups = {}
        build_failed = False

        for imp_action in imported_actions:
            ref = imp_action.get("source_manifest_ref", "")
            parts = ref.split("/") if isinstance(ref, str) else []
            if len(parts) < 3 or any(not part for part in parts):
                context.log_error(self.get_stage_name(), "导入动作缺少有效的源清单引用")
                build_failed = True
                continue
            try:
                validate_filename_component(parts[0], "source skeleton")
            except ValueError as exc:
                context.log_error(self.get_stage_name(), str(exc))
                build_failed = True
                continue
            skel_name = parts[0]
            if skel_name not in skeleton_groups:
                skeleton_groups[skel_name] = []
            skeleton_groups[skel_name].append(imp_action)

        try:
            validate_skeleton_output_names(skeleton_groups)
        except ValueError as exc:
            context.log_error(self.get_stage_name(), str(exc))
            return False
        if force_rebuild:
            shared_bundles = [
                bundle.blend_file for skeleton in skeleton_groups for bundle in manifest.bundles
                if skeleton in bundle.skeletons and len(bundle.skeletons) > 1
            ]
            if shared_bundles:
                context.log_error(
                    self.get_stage_name(),
                    "拒绝重建共享旧动画包；请先执行完整库重建: "
                    + ", ".join(sorted(set(shared_bundles))),
                )
                return False
        
        success_count = 0
        obsolete_files = set()
        pending_rebuild_pairs = []
        pending_rebuild_actions = []
        pending_force_action_cleanup = []
        pending_rebuild_provenance = []
        pending_rebuild_artifact_paths = set()
        prebuild_artifact_paths = []
        prebuild_artifact_keys = set()
        catalog_path = os.path.join(output_dir, "blender_assets.cats.txt")
        prebuild_artifact_paths.append(catalog_path)
        prebuild_artifact_keys.add(os.path.normcase(os.path.abspath(catalog_path)))
        thumbnail_paths = []
        if generate_thumbnails:
            for skel_name, actions in skeleton_groups.items():
                for imp_action in actions:
                    action = resolved_actions[id(imp_action)]
                    thumb_paths = os.path.join(
                        output_dir,
                        utils_asset.thumbnail_output_path(action, skel_name).replace("/", os.sep),
                    )
                    thumbnail_paths.append(thumb_paths)
        if force_rebuild:
            thumbnail_root = os.path.join(output_dir, ".thumbnails")
            if os.path.isdir(thumbnail_root):
                for root, _, filenames in os.walk(thumbnail_root):
                    for filename in filenames:
                        thumbnail_paths.append(os.path.join(root, filename))
        for artifact_path in thumbnail_paths:
            artifact_key = os.path.normcase(os.path.abspath(artifact_path))
            if artifact_key not in prebuild_artifact_keys:
                prebuild_artifact_paths.append(artifact_path)
                prebuild_artifact_keys.add(artifact_key)
        try:
            planned_volume_indices = {}
            for skel_name, actions in skeleton_groups.items():
                base_name = f"{library_name}_{skel_name}"
                chunk_count = max(1, (len(actions) + chunk_size - 1) // chunk_size)
                occupied_slots = self._occupied_bundle_slots(output_dir, base_name)
                planned_volume_indices[skel_name] = self._plan_bundle_slots(
                    occupied_slots, chunk_count
                )
            validate_generated_output_filenames(
                library_name,
                skeleton_groups,
                volume_indices=planned_volume_indices,
            )
        except ValueError as exc:
            context.log_error(self.get_stage_name(), str(exc))
            return False
        try:
            prepare_library_transaction(manifest_path, prebuild_artifact_paths)
        except Exception as exc:
            context.log_error(self.get_stage_name(), f"准备资产库事务失败: {exc}")
            return False
        
        for skel_name, actions in skeleton_groups.items():
            base_name = f"{library_name}_{skel_name}"
            existing_sources = set()
            pending_entries = []
            manifest_for_skeleton = manifest

            if authoritative_v2_manifest is not None:
                existing_sources = get_manifest_processed_sources(
                    authoritative_v2_manifest, skel_name
                )

            if force_rebuild:
                obsolete_files.update(
                    self._owned_force_rebuild_bundle_files(output_dir, base_name, skel_name)
                )
                obsolete_files.update(
                    path for bundle in manifest.bundles
                    if bundle.skeletons == [skel_name]
                    for path in (bundle.blend_file, bundle.sidecar_file)
                )
                manifest_for_skeleton = replace_manifest_skeleton(manifest, skel_name)
            for imp_action in actions:
                action_name = imp_action["action_name"]
                action = resolved_actions[id(imp_action)]
                if imp_action.get("source_bundle"):
                    action = localize_action(action)

                source_ref = imp_action.get("source_manifest_ref", "") or action_name
                source_path = action.get("Pipeline_Source", "")
                source_key = source_ref
                if source_path and source_dir:
                    try:
                        source_key = os.path.relpath(source_path, source_dir).replace('\\', '/')
                    except Exception:
                        source_key = source_ref
                source_key = f"{skel_name}::{source_key}"
                if not force_rebuild and source_key in existing_sources:
                    destination_bundle = self._published_bundle_for_source(
                        output_dir, authoritative_v2_manifest, source_key
                    )
                    if not destination_bundle:
                        context.log_error(
                            self.get_stage_name(),
                            f"无法唯一解析已发布动作包: {source_key}",
                        )
                        build_failed = True
                        continue
                    pending_rebuild_provenance.append((imp_action, destination_bundle))
                    pending_rebuild_actions.append(imp_action)
                    continue

                pending_entries.append((imp_action, action, source_key))

            if not pending_entries:
                continue

            chunks = [pending_entries[i:i + chunk_size] for i in range(0, len(pending_entries), chunk_size)]

            for chunk_idx, chunk_entries in enumerate(chunks):
                current_vol = planned_volume_indices[skel_name][chunk_idx]
                vol_suffix = f"_Vol_{current_vol}" if current_vol > 1 else ""
                blend_file_name = f"{base_name}{vol_suffix}.blend"
                blend_path = os.path.join(output_dir, blend_file_name)
                sidecar_file_name = f"{base_name}{vol_suffix}_manifest.json"
                sidecar_path = os.path.join(output_dir, sidecar_file_name)
                temp_blend_path = os.path.join(
                    output_dir, f".{base_name}{vol_suffix}.{time.time_ns()}.blend.tmp"
                )

                chunk_manifest = {"__processed_sources__": [], skel_name: {}}
                target_armature = context.get_config("target_armature", "")
                arm_obj = bpy.data.objects.get(target_armature) if target_armature else None
                if arm_obj is not None and arm_obj.type != 'ARMATURE':
                    arm_obj = None
                if not target_armature:
                    arm_obj = self._find_skeleton_object(skel_name, context_logger=context)
                generated_actions = []
                chunk_catalog_entries = []
                chunk_assets = []

                for imp_action, action, source_key in chunk_entries:
                    ref = imp_action.get("source_manifest_ref", "")
                    parts = ref.split("/") if ref else []
                    catalog_path = "/".join(parts[1:-1]) if len(parts) > 2 else "Uncategorized"
                    rel_thumb_path = ""

                    if generate_thumbnails:
                        thumb_path = os.path.join(
                            output_dir,
                            utils_asset.thumbnail_output_path(action, skel_name).replace("/", os.sep),
                        )
                        thumb_key = os.path.normcase(os.path.abspath(thumb_path))
                        if thumb_key not in prebuild_artifact_keys and thumb_key not in pending_rebuild_artifact_paths:
                            pending_rebuild_artifact_paths.add(thumb_key)
                            persist_transaction_artifact(manifest_path, thumb_path)

                    if not action.asset_data:
                        action.asset_mark()
                    if not action.asset_data:
                        context.log_error(
                            self.get_stage_name(),
                            f"无法将动作标记为资产: {action.name}",
                        )
                        build_failed = True
                        continue

                    catalog_uuid = get_or_create_catalog_uuid(output_dir, catalog_path, skel_name)
                    action.asset_data.catalog_id = catalog_uuid
                    action["SA_Group"] = skel_name
                    action["SA_Folder"] = catalog_path

                    if generate_thumbnails and arm_obj:
                        try:
                            rel_thumb_path = utils_asset.bake_thumbnail_opengl(
                                arm_obj, action, output_dir, skel_name,
                                resolution=thumb_resolution,
                                quality=context.get_config("thumb_quality", "OPENGL"),
                                isolate=context.get_config("thumb_isolate", True),
                            )
                        except Exception as e:
                            rel_thumb_path = ""
                            import traceback
                            traceback.print_exc()

                    catalog_entry = next(
                        (c for c in chunk_catalog_entries
                         if c.path == f"{skel_name}/{catalog_path}"),
                        None
                    )
                    if not catalog_entry:
                        catalog_entry = CatalogEntry(
                            uuid=catalog_uuid,
                            path=f"{skel_name}/{catalog_path}",
                            skeleton=skel_name,
                            action_count=0,
                        )
                        chunk_catalog_entries.append(catalog_entry)
                    catalog_entry.action_count += 1

                    chunk_assets.append(LibraryAsset(
                        action_name=action.name,
                        catalog_id=catalog_uuid,
                        skeleton=skel_name,
                        blend_file=blend_file_name,
                        thumbnail_path=rel_thumb_path
                    ))

                    current_level = chunk_manifest[skel_name]
                    for part in [p for p in catalog_path.split('/') if p]:
                        if part not in current_level:
                            current_level[part] = {}
                        current_level = current_level[part]
                    if "__actions__" not in current_level:
                        current_level["__actions__"] = []
                    current_level["__actions__"].append([action.name, rel_thumb_path])
                    chunk_manifest["__processed_sources__"].append(source_key)

                    generated_actions.append(action)
                    if force_rebuild and action.get("Pipeline_Owned"):
                        pending_force_action_cleanup.append(action)

                if not generated_actions:
                    continue

                try:
                    bpy.data.libraries.write(
                        filepath=temp_blend_path, datablocks=set(generated_actions), fake_user=True
                    )
                    self._validate_published_bundle_actions(
                        temp_blend_path, [action.name for action in generated_actions]
                    )
                    persist_transaction_replacement(
                        manifest_path, temp_blend_path, blend_path, sidecar_path, chunk_manifest
                    )
                    bundle_record = BundleRecord(
                        blend_file=blend_file_name,
                        sidecar_file=sidecar_file_name,
                        skeletons=[skel_name],
                        processed_sources=chunk_manifest["__processed_sources__"],
                        completion_state="completed",
                    )
                    candidate_manifest = manifest_with_bundle(
                        manifest_for_skeleton,
                        skel_name,
                        chunk_catalog_entries,
                        chunk_assets,
                        bundle_record,
                        blend_file_name,
                    )
                    # Keep old pairs untouched until the complete transaction commits.
                    pending_rebuild_pairs.append(
                        (temp_blend_path, blend_path, sidecar_path, chunk_manifest)
                    )
                    destination_bundle = os.path.normcase(os.path.abspath(blend_path))
                    pending_rebuild_provenance.extend(
                        (imp_action, destination_bundle)
                        for imp_action, _, _ in chunk_entries
                    )
                    manifest = candidate_manifest
                    manifest_for_skeleton = candidate_manifest
                    context.library_manifest = manifest.to_dict()
                    chunk_actions = [imp_action for imp_action, _, _ in chunk_entries]
                    pending_rebuild_actions.extend(chunk_actions)
                    # The temporary bundle and resume state must survive before
                    # unloading the Actions that were just written to it.
                    context.persist_checkpoint()
                except Exception as e:
                    try:
                        if os.path.exists(temp_blend_path):
                            os.remove(temp_blend_path)
                    except OSError:
                        pass
                    context.log_error(
                        self.get_stage_name(),
                        f"保存动画包失败 {blend_path} / {sidecar_path}: {e}"
                    )
                    build_failed = True
                    continue

                for act in generated_actions:
                    if force_rebuild:
                        continue
                    try:
                        bpy.data.actions.remove(act)
                    except Exception as e:
                        context.log_warning(
                            self.get_stage_name(),
                            f"清理动作失败 {getattr(act, 'name', '<unknown>')}: {e}",
                        )
                success_count += len(generated_actions)

        if build_failed:
            for temporary_blend, _, _, _ in pending_rebuild_pairs:
                try:
                    if os.path.exists(temporary_blend):
                        os.remove(temporary_blend)
                except OSError:
                    pass
            try:
                recover_library_transaction(manifest_path)
            except OSError as exc:
                context.log_error(self.get_stage_name(), f"回滚资产库事务失败，保留恢复日志: {exc}")
        if not build_failed and pending_rebuild_pairs:
            try:
                indexed_files = {
                    name for bundle in manifest.bundles
                    for name in (bundle.blend_file, bundle.sidecar_file)
                }
                obsolete_cleanup_paths = [
                    os.path.join(output_dir, filename)
                    for filename in obsolete_files - indexed_files
                ]
                publish_rebuild_replacement_set(
                    manifest_path, pending_rebuild_pairs, manifest, obsolete_cleanup_paths, ()
                )
            except Exception as exc:
                context.log_error(self.get_stage_name(), f"发布重建动画包失败: {exc}")
                build_failed = True
            else:
                self._persist_published_provenance(context, pending_rebuild_provenance)
                if force_rebuild:
                    removed_ids = set()
                    for action in pending_force_action_cleanup:
                        identity = datablock_identity(action)
                        if identity in removed_ids or not action.get("Pipeline_Owned"):
                            continue
                        try:
                            bpy.data.actions.remove(action)
                            removed_ids.add(identity)
                        except (ReferenceError, RuntimeError, ValueError) as exc:
                            context.log_warning(
                                self.get_stage_name(),
                                f"提交后清理动作失败 {getattr(action, 'name', '<unknown>')}: {exc}",
                            )
        elif not build_failed:
            if migrated_legacy_inventory:
                try:
                    publish_rebuild_replacement_set(manifest_path, [], manifest)
                except Exception as exc:
                    context.log_error(self.get_stage_name(), f"发布旧版资产库迁移失败: {exc}")
                    build_failed = True
                else:
                    self._persist_published_provenance(context, pending_rebuild_provenance)
            else:
                # A no-op incremental run still prepared a journal and backups.
                recover_library_transaction(manifest_path)
        if not build_failed:
            committed_staged_blends = mark_staged_blend_actions_published(
                staged_blend_references, pending_rebuild_actions
            )
            queue_staged_cleanup(
                context, committed_staged_blends, self._staging_task_dir(context)
            )
        context.library_manifest = manifest.to_dict()
        context.log_info(self.get_stage_name(), f"成功构建资产库，包含 {success_count} 个动作")
        return bool(imported_actions) and not build_failed

    def _persist_published_provenance(self, context, published_actions):
        """Checkpoint destination provenance before staging can be deleted."""
        for imported_action, destination_bundle in published_actions:
            imported_action["source_bundle"] = self._normalized_source_path(destination_bundle)
        context.persist_checkpoint()

    def _published_bundle_for_source(self, output_dir: str, manifest, source_key: str) -> str:
        """Resolve one processed source to its authoritative completed bundle."""
        if manifest is None:
            return ""
        matches = [
            self._normalized_source_path(os.path.join(output_dir, bundle.blend_file))
            for bundle in manifest.bundles
            if source_key in (bundle.processed_sources or [])
            and bundle.completion_state == "completed"
        ]
        return matches[0] if len(matches) == 1 else ""

    def _source_key_for_imported(self, imported_action, source_dir: str) -> str:
        """Recreate the processed-source key without requiring a live action."""
        source_ref = imported_action.get("source_manifest_ref", "")
        skeleton = source_ref.split("/", 1)[0] if source_ref else ""
        source_value = source_ref
        source_file = imported_action.get("source_file", "")
        if source_file and source_dir:
            try:
                source_value = os.path.relpath(source_file, source_dir).replace('\\', '/')
            except (OSError, ValueError):
                pass
        return f"{skeleton}::{source_value}" if skeleton and source_value else ""

    def _reload_actions_from_blends(self, output_dir: str, manifest, imported_actions=None):
        """从已提交的v2库包恢复动作到 bpy.data，用于重烘焙。"""
        loaded_by_bundle = {}
        imported_actions = imported_actions or []
        if not os.path.isdir(output_dir):
            return loaded_by_bundle
        for bundle in sorted(manifest.bundles, key=lambda item: item.blend_file):
            blend_path = os.path.join(output_dir, bundle.blend_file)
            normalized_bundle = self._normalized_source_path(blend_path)
            provenance = {
                item.get("action_name", ""): item.get("source_manifest_ref", "")
                for item in imported_actions
                if self._normalized_source_path(item.get("source_bundle", "")) == normalized_bundle
            }
            try:
                existing = {}
                duplicate_actions = []
                for action in list(bpy.data.actions):
                    requested_name = action.get("Pipeline_RequestedActionName", "") or action.name
                    if self._normalized_source_path(
                        action.get("Pipeline_SourceBundle", "")
                    ) != normalized_bundle:
                        continue
                    expected_ref = provenance.get(requested_name, "")
                    if expected_ref and action.get("Pipeline_SourceRef", "") != expected_ref:
                        duplicate_actions.append(action)
                        continue
                    if requested_name in existing:
                        duplicate_actions.append(action)
                    else:
                        existing[requested_name] = action
                for action in duplicate_actions:
                    bpy.data.actions.remove(action)

                if provenance and set(existing) == set(provenance):
                    loaded_by_bundle[normalized_bundle] = existing
                    continue

                with bpy.data.libraries.load(blend_path, link=False) as (df, dt):
                    requested_names = list(df.actions)
                    missing_names = [name for name in requested_names if name not in existing]
                    dt.actions = missing_names
                returned_actions = list(dt.actions)
                if len(returned_actions) != len(missing_names):
                    continue
                bundle_actions = dict(existing)
                for requested_name, action in zip(missing_names, returned_actions):
                    if action is None:
                        continue
                    action["Pipeline_RequestedActionName"] = requested_name
                    action["Pipeline_SourceBundle"] = normalized_bundle
                    if provenance.get(requested_name):
                        action["Pipeline_SourceRef"] = provenance[requested_name]
                    bundle_actions[requested_name] = action
                loaded_by_bundle[normalized_bundle] = bundle_actions
            except Exception:
                pass

        selected_actions = {
            datablock_identity(action)
            for bundle_actions in loaded_by_bundle.values()
            for action in bundle_actions.values()
        }
        staged_identities = {
            (
                item.get("action_name", ""),
                item.get("source_manifest_ref", ""),
                self._normalized_source_path(item.get("staged_blend", "")),
            )
            for item in imported_actions
            if item.get("source_bundle") and item.get("staged_blend")
        }
        for action in list(bpy.data.actions):
            identity = (
                action.get("Pipeline_RequestedActionName", "") or action.name,
                action.get("Pipeline_SourceRef", ""),
                self._normalized_source_path(action.get("Pipeline_StagedBlend", "")),
            )
            if datablock_identity(action) not in selected_actions and identity in staged_identities:
                try:
                    bpy.data.actions.remove(action)
                except (RuntimeError, ValueError):
                    pass
        return loaded_by_bundle

    def _find_skeleton_object(self, skeleton_name: str, context_logger=None):
        """查找与骨架名匹配的骨架对象。

        精确匹配优先；仅接受唯一的模糊匹配。
        """
        candidates = []
        for obj in bpy.data.objects:
            if obj.type != 'ARMATURE':
                continue
            sk = obj.get("FModel_Skeleton", obj.name)
            # 精确匹配：直接返回
            if sk == skeleton_name:
                return obj
            # 模糊匹配候选
            if sk.lower() in skeleton_name.lower() or skeleton_name.lower() in sk.lower():
                candidates.append((obj, sk))
        
        if len(candidates) == 1:
            obj, matched_name = candidates[0]
            if context_logger:
                context_logger.log_info(
                    "LibraryBuilding",
                    f"模糊匹配骨架: '{skeleton_name}' → '{matched_name}' (对象: {obj.name})"
                )
            return obj
        elif len(candidates) > 1:
            if context_logger:
                names = [f"'{c[1]}'({c[0].name})" for c in candidates]
                context_logger.log_warning(
                    "LibraryBuilding",
                    f"骨架'{skeleton_name}'有多个模糊匹配: {', '.join(names)}，跳过缩略图生成"
                )
        return None


# ============================================================
# 阶段4: 完成
# ============================================================

class FinalizationStage(PipelineStageBase):
    """完成阶段 - 动态挂载资产库、扫描动画、更新UI"""
    
    def get_stage_name(self) -> str:
        return PipelineStage.FINALIZATION.value
    
    def validate_input(self, context: PipelineContext) -> tuple[bool, str]:
        if not context.library_manifest:
            return False, "缺少资产库清单数据"
        return True, ""
    
    def execute(self, context: PipelineContext) -> bool:
        from ...core.utils import mount_dynamic_library
        
        manifest_data = context.library_manifest
        library_name = manifest_data.get("library_name", "")
        library_id = manifest_data.get("library_id", "")
        library_path = manifest_data.get("library_path", "")

        if not library_path or not os.path.exists(library_path):
            context.log_error(self.get_stage_name(), "资产库路径无效")
            return False
        
        try:
            if not mount_dynamic_library(library_name, library_path, library_id):
                context.log_error(self.get_stage_name(), "挂载资产库失败")
                context.status = StageStatus.FAILED.value
                return False
            context.log_info(self.get_stage_name(), f"已挂载资产库: {library_name}")
        except Exception as e:
            context.log_error(self.get_stage_name(), f"挂载资产库失败: {e}")
            return False
        
        scene = bpy.context.scene if bpy.context else None
        if scene and hasattr(scene, "fmodel_assets"):
            props = scene.fmodel_assets
            normalized_library_path = os.path.normcase(os.path.abspath(library_path))
            linked_library = next((
                lib for lib in props.project_libs
                if library_id and getattr(lib, "library_id", "") == library_id
            ), None)
            if linked_library is None:
                linked_library = next((
                    lib for lib in props.project_libs
                    if getattr(lib, "path", "")
                    and os.path.normcase(os.path.abspath(bpy.path.abspath(lib.path)))
                    == normalized_library_path
                ), None)
            if linked_library is None:
                new_lib = props.project_libs.add()
                linked_library = new_lib
            linked_library.name = library_name
            linked_library.path = library_path
            if library_id:
                linked_library.library_id = library_id
        
        try:
            if hasattr(bpy.ops.fmodel, "scan_anims"):
                scan_result = bpy.ops.fmodel.scan_anims()
                if "FINISHED" not in scan_result:
                    context.log_error(
                        self.get_stage_name(), f"动画扫描未完成: {scan_result}"
                    )
                    context.status = StageStatus.FAILED.value
                    return False
                context.log_info(self.get_stage_name(), "动画扫描完成")
            else:
                context.log_error(self.get_stage_name(), "scan_anims 操作符未注册")
                context.status = StageStatus.FAILED.value
                return False
        except Exception as e:
            context.log_error(self.get_stage_name(), f"动画扫描失败: {e}")
            context.status = StageStatus.FAILED.value
            return False
        
        context.status = StageStatus.COMPLETED.value
        try:
            retry_staged_cleanup(
                context,
                context.get_config("pending_staged_cleanup", []),
                self._staging_task_dir(context),
            )
        except OSError as exc:
            context.log_warning(self.get_stage_name(), f"清理暂存动作包失败: {exc}")
        context.log_info(self.get_stage_name(), "管线流程完成")
        return True


# ============================================================
# 注册
# ============================================================

def register():
    pass

def unregister():
    pass
