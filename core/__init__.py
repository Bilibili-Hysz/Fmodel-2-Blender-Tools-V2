# FModel_Tools/core/__init__.py
# 核心共享模块 — 枚举、类型、工具函数、管线上下文

from . import enums
from . import types
from . import context
from . import utils
from . import logger
from . import i18n

# 重新导出常用内容
from .enums import (
    PipelineStage, StageStatus, SourceType, ImportMode,
    ItemType, ApplyMethod
)

from .types import (
    AnimationEntry, FolderNode, SkeletonNode,
    DictionaryManifest, ImportedAction, ImportedAssetCollection,
    CatalogEntry, LibraryAsset, BundleRecord, AssetLibraryManifest,
    MultiImportTask, MultiImportPlan, MultiImportCheckpoint,
    PipelineConfig, PipelineLog
)

from .context import PipelineContext

from .resume import (
    armature_identity, build_settings_fingerprint, import_settings_fingerprint,
    make_batch_identity, make_manifest_source, manifest_sources,
    reconcile_resume_state, reusable_import_results, source_revision,
)

from .multi_import import (
    build_multi_import_plan, restore_checkpoint, run_pending_tasks,
)

from .utils import (
    get_or_create_catalog_uuid,
    get_ueformat_bridge,
    build_model_option_kwargs,
    build_uescene_settings_kwargs,
    derive_fmodel_roots,
    parse_uescene_import_status,
    ensure_dir,
    safe_relpath,
    normalize_path,
    get_ueformat_api
)

from .library_files import (
    library_index_path,
    load_complete_v2_manifest, migrate_verified_legacy_manifest,
    get_manifest_processed_sources,
    manifest_with_bundle,
    replace_manifest_skeleton,
    publish_bundle_pair,
    publish_bundle_pair_and_index,
    publish_bundle_pair_and_library_index,
    publish_rebuild_replacement_set,
    prepare_transaction_artifact,
    prepare_library_transaction,
    persist_transaction_artifact,
    persist_transaction_replacement,
    fsync_transaction_artifacts,
    publish_library_index,
    validate_filename_component,
    safe_child_path,
    staging_chunk_path, staging_task_dir, validate_staged_blend_path,
    validate_skeleton_output_names,
    validate_generated_output_filenames,
    validate_imported_action_set,
    publish_metadata_repair,
    library_transaction_path,
    recover_library_transaction,
)

from .logger import (
    FModelLogger, LogLevel, OperationType,
    get_logger, initialize_logging,
    log_info, log_warning, log_error, log_success,
    backup_before
)


def register():
    enums.register()
    types.register()
    context.register()
    utils.register()
    logger.register()
    i18n.register()


def unregister():
    i18n.unregister()
    logger.unregister()
    utils.unregister()
    context.unregister()
    types.unregister()
    enums.unregister()
