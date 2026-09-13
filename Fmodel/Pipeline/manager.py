# FModel_Tools/Fmodel/Pipeline/manager.py
# 管线管理器 — 协调四阶段执行流程

from ...core import PipelineContext, PipelineStage, pipeline_activity
from .stages import (
    DictionaryAcquisitionStage, FormatConversionStage,  # 新增 FormatConversionStage
    AssetImportStage, LibraryBuildingStage, FinalizationStage
)


# ============================================================
# 管线管理器
# ============================================================

class PipelineManager:
    """管线流程管理器 - 协调五个阶段的顺序执行"""
    
    def __init__(self):
        self.stages = {
            PipelineStage.DICTIONARY_ACQUISITION.value: DictionaryAcquisitionStage(),
            PipelineStage.FORMAT_CONVERSION.value: FormatConversionStage(),  # 新增阶段
            PipelineStage.ASSET_IMPORT.value: AssetImportStage(),
            PipelineStage.LIBRARY_BUILDING.value: LibraryBuildingStage(),
            PipelineStage.FINALIZATION.value: FinalizationStage(),
        }
    
    
    def run_stage(self, context: PipelineContext, stage_name: str) -> bool:
        """运行单个阶段并抑制导入期间的全库自动扫描。"""
        with pipeline_activity():
            stage = self.stages.get(stage_name)
            if not stage:
                context.log_error("manager", f"未知阶段: {stage_name}")
                return False
            return stage.run(context)

