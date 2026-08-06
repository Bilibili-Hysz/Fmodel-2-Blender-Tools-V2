# FModel_Tools/Fmodel/Pipeline/manager.py
# 管线管理器 — 协调四阶段执行流程

import sys

import bpy

from ...core import (
    MultiImportPlan, PipelineContext, PipelineConfig, PipelineStage, StageStatus,
    build_multi_import_plan, restore_checkpoint, run_pending_tasks,
    armature_identity, import_settings_fingerprint,
)
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
    
    
    _PIPELINE_ORDER = [
        PipelineStage.DICTIONARY_ACQUISITION.value,
        PipelineStage.FORMAT_CONVERSION.value,
        PipelineStage.ASSET_IMPORT.value,
        PipelineStage.LIBRARY_BUILDING.value,
        PipelineStage.FINALIZATION.value,
    ]
    def create_context(self, config: dict = None) -> PipelineContext:
        """创建新的管线上下文"""
        context = PipelineContext()
        if config:
            context.config = config
        return context
    
    def run_full_pipeline(self, context: PipelineContext) -> bool:
        """运行完整的管线流程"""
        if context.multi_import_plan:
            return self._run_multi_import_pipeline(context)

        pipeline_stages = self._PIPELINE_ORDER
        
        for stage_name in pipeline_stages:
            if context.is_stage_completed(stage_name):
                context.log_info("manager", f"跳过已完成阶段: {stage_name}")
                continue
            
            success = self.run_stage(context, stage_name)
            if not success:
                context.status = StageStatus.FAILED.value
                return False
        
        context.status = StageStatus.COMPLETED.value
        return True

    def _run_multi_import_pipeline(self, context: PipelineContext) -> bool:
        """Run explicit skeleton mappings without sharing import state between tasks."""
        for stage_name in self._PIPELINE_ORDER[:2]:
            if not context.is_stage_completed(stage_name) and not self.run_stage(context, stage_name):
                context.status = StageStatus.FAILED.value
                return False

        try:
            plan = self._validated_multi_import_plan(context)
        except ValueError as exc:
            context.log_error("manager", str(exc))
            context.status = StageStatus.FAILED.value
            return False

        checkpoint = restore_checkpoint(plan, context.multi_import_checkpoint)
        if any(task.status != "completed" for task in checkpoint.plan.tasks):
            context.set_stage_status(
                PipelineStage.FINALIZATION.value, StageStatus.PENDING.value
            )

        def persist(value):
            context.multi_import_plan = value.plan.to_dict()
            context.multi_import_checkpoint = value.to_dict()
            if context.get_config("output_dir", ""):
                context.persist_checkpoint()
            else:
                context.save_to_blender_text()

        def process(task):
            context.set_config("target_armature", task.target_armature)
            context.set_config("source_skeleton", task.source_skeleton)
            context.set_config("source_subset", list(task.source_subset))
            if not self.run_stage(context, PipelineStage.ASSET_IMPORT.value):
                return False
            persist(checkpoint)
            return self.run_stage(context, PipelineStage.LIBRARY_BUILDING.value)

        persist(checkpoint)
        try:
            tasks_succeeded = run_pending_tasks(checkpoint, process, persist)
        finally:
            context.set_config("target_armature", "")
            context.set_config("source_skeleton", "")
            context.set_config("source_subset", [])
            task_error_active = sys.exc_info()[0] is not None
            try:
                if context.get_config("output_dir", ""):
                    context.persist_checkpoint()
                else:
                    context.save_to_blender_text()
            except Exception:
                if not task_error_active:
                    raise
        if not tasks_succeeded:
            context.status = StageStatus.FAILED.value
            return False
        final_stage = PipelineStage.FINALIZATION.value
        if not context.is_stage_completed(final_stage) and not self.run_stage(context, final_stage):
            context.status = StageStatus.FAILED.value
            return False
        context.status = StageStatus.COMPLETED.value
        persist(checkpoint)
        return True

    @staticmethod
    def _validated_multi_import_plan(context: PipelineContext) -> MultiImportPlan:
        plan = MultiImportPlan.from_dict(context.multi_import_plan)
        if not plan.tasks:
            raise ValueError("多骨架导入计划不包含任何任务")
        mappings = {}
        for task in plan.tasks:
            if not task.source_skeleton or not task.target_armature:
                raise ValueError("多骨架导入计划包含不完整的骨架映射")
            if task.source_skeleton in mappings:
                raise ValueError(f"多骨架导入计划包含重复源骨架: {task.source_skeleton}")
            mappings[task.source_skeleton] = task.target_armature
        available = {obj.name for obj in bpy.data.objects if obj.type == 'ARMATURE'}
        validated, unmapped = build_multi_import_plan(
            context.dictionary_manifest or {}, mappings,
            available_target_armatures=available,
            import_fingerprint=import_settings_fingerprint(context.config),
            target_armature_identities={
                obj.name: armature_identity(obj)
                for obj in bpy.data.objects if obj.type == 'ARMATURE'
            },
        )
        if unmapped:
            raise ValueError(f"多骨架导入计划缺少映射: {', '.join(unmapped)}")
        expected = [
            (task.source_skeleton, task.target_armature, list(task.source_subset))
            for task in validated.tasks
        ]
        actual = [
            (task.source_skeleton, task.target_armature, list(task.source_subset))
            for task in plan.tasks
        ]
        if actual != expected:
            raise ValueError("多骨架导入计划与当前字典清单不匹配")
        return plan
    
    def run_stage(self, context: PipelineContext, stage_name: str) -> bool:
        """运行单个阶段"""
        stage = self.stages.get(stage_name)
        if not stage:
            context.log_error("manager", f"未知阶段: {stage_name}")
            return False
        return stage.run(context)
    
    def run_from_stage(self, context: PipelineContext, start_stage: str) -> bool:
        """从指定阶段开始运行"""
        pipeline_stages = self._PIPELINE_ORDER
        
        try:
            start_index = pipeline_stages.index(start_stage)
        except ValueError:
            context.log_error("manager", f"无效的起始阶段: {start_stage}")
            return False
        
        for stage_name in pipeline_stages[start_index:]:
            if context.is_stage_completed(stage_name):
                continue
            success = self.run_stage(context, stage_name)
            if not success:
                context.status = StageStatus.FAILED.value
                return False
        
        context.status = StageStatus.COMPLETED.value
        return True
    
    def get_progress(self, context: PipelineContext) -> dict:
        """获取进度信息"""
        pipeline_stages = self._PIPELINE_ORDER
        
        completed = sum(1 for s in pipeline_stages if context.is_stage_completed(s))
        total = len(pipeline_stages)
        
        return {
            "completed_stages": completed,
            "total_stages": total,
            "percentage": (completed / total) * 100 if total > 0 else 0,
            "current_stage": context.current_stage,
            "status": context.status,
            "stage_status": dict(context.stage_status)
        }


# ============================================================
# Blender操作符
# ============================================================

class PIPELINE_OT_RunFullPipeline(bpy.types.Operator):
    """运行完整管线流程"""
    bl_idname = "pipeline.run_full"
    bl_label = "运行完整管线"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        props = getattr(context.scene, "pipeline_props", None)
        return props and props.source_dir and props.output_dir
    
    def execute(self, context):
        try:
            return self._execute(context)
        except Exception as exc:
            self.report({'ERROR'}, f"管线流程执行失败: {exc}")
            return {'CANCELLED'}

    def _execute(self, context):
        props = context.scene.pipeline_props
        
        config = PipelineConfig(
            source_dir=props.source_dir,
            output_dir=props.output_dir,
            library_name=props.library_name,
            source_type=props.source_type,
            import_mode=props.import_mode,
            unity_skip_processed=props.unity_skip_processed,
            import_scale=props.import_scale,
            rotation_only=props.rotation_only,
            generate_thumbnails=props.generate_thumbnails,
        ).to_dict()
        
        manager = PipelineManager()
        saved_context = (
            PipelineContext.load_from_checkpoint(config.get("output_dir", ""))
            or PipelineContext.load_from_blender_text(
                output_dir=config.get("output_dir", "")
            )
        )
        if saved_context:
            saved_context.apply_pipeline_config(config)
            pipeline_context = saved_context
        else:
            pipeline_context = manager.create_context(config)
        
        success = manager.run_full_pipeline(pipeline_context)
        if pipeline_context.get_config("output_dir", ""):
            pipeline_context.persist_checkpoint()
        else:
            pipeline_context.save_to_blender_text()
        
        if success:
            self.report({'INFO'}, "管线流程完成！")
        else:
            self.report({'ERROR'}, "管线流程失败，请查看日志")
        
        return {'FINISHED'} if success else {'CANCELLED'}


class PIPELINE_OT_RunStage(bpy.types.Operator):
    """运行单个管线阶段"""
    bl_idname = "pipeline.run_stage"
    bl_label = "运行指定阶段"
    bl_options = {'REGISTER', 'UNDO'}
    
    stage_name: bpy.props.StringProperty()
    
    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, "pipeline_props")
    
    def execute(self, context):
        try:
            return self._execute(context)
        except Exception as exc:
            self.report({'ERROR'}, f"阶段 {self.stage_name} 执行失败: {exc}")
            return {'CANCELLED'}

    def _execute(self, context):
        props = context.scene.pipeline_props
        
        pipeline_context = (
            PipelineContext.load_from_checkpoint(props.output_dir)
            or PipelineContext.load_from_blender_text(output_dir=props.output_dir)
        )
        if not pipeline_context:
            config = PipelineConfig(
                source_dir=props.source_dir,
                output_dir=props.output_dir,
                library_name=props.library_name,
                source_type=props.source_type,
                import_mode=props.import_mode,
            ).to_dict()
            pipeline_context = PipelineContext(config=config)
        
        manager = PipelineManager()
        success = manager.run_stage(pipeline_context, self.stage_name)
        if pipeline_context.get_config("output_dir", ""):
            pipeline_context.persist_checkpoint()
        else:
            pipeline_context.save_to_blender_text()
        
        if success:
            self.report({'INFO'}, f"阶段 {self.stage_name} 完成！")
        else:
            self.report({'ERROR'}, f"阶段 {self.stage_name} 失败")
        
        return {'FINISHED'} if success else {'CANCELLED'}


class PIPELINE_OT_SaveCheckpoint(bpy.types.Operator):
    """保存管线检查点"""
    bl_idname = "pipeline.save_checkpoint"
    bl_label = "保存检查点"
    
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
    
    def execute(self, context):
        pipeline_context = PipelineContext.load_from_blender_text()
        if not pipeline_context:
            self.report({'ERROR'}, "没有活动的管线上下文")
            return {'CANCELLED'}
        
        pipeline_context.save_to_file(self.filepath)
        self.report({'INFO'}, f"检查点已保存: {self.filepath}")
        return {'FINISHED'}


class PIPELINE_OT_LoadCheckpoint(bpy.types.Operator):
    """加载管线检查点"""
    bl_idname = "pipeline.load_checkpoint"
    bl_label = "加载检查点"
    
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
    
    def execute(self, context):
        try:
            pipeline_context = PipelineContext.load_from_file(self.filepath)
            pipeline_context.save_to_blender_text()
            self.report({'INFO'}, f"检查点已加载: {self.filepath}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"加载失败: {e}")
            return {'CANCELLED'}


# ============================================================
# 属性组
# ============================================================

class PipelineProperties(bpy.types.PropertyGroup):
    source_dir: bpy.props.StringProperty(name="源目录", subtype='DIR_PATH')
    output_dir: bpy.props.StringProperty(name="输出目录", subtype='DIR_PATH')
    library_name: bpy.props.StringProperty(name="资产库名称", default="Asset_Library")
    
    source_type: bpy.props.EnumProperty(
        name="源类型",
        items=[
            ("unity_anim", "Unity动画", "处理Unity .anim文件"),
            ("fmodel_ueanim", "FModel UEAnim", "处理FModel .ueanim文件"),
            ("fbx_batch", "批量FBX", "处理批量FBX文件"),
        ],
        default="unity_anim"
    )
    
    import_mode: bpy.props.EnumProperty(
        name="导入模式",
        items=[
            ("fbx_match", "匹配FBX", "匹配已导入的FBX动画"),
            ("ueanim_direct", "直接导入", "直接导入.ueanim文件"),
            ("link_existing", "链接现有", "链接现有资产库"),
        ],
        default="fbx_match"
    )
    
    unity_skip_processed: bpy.props.BoolProperty(name="跳过已处理", default=True)
    import_scale: bpy.props.FloatProperty(name="导入缩放", default=0.01, min=0.0001, precision=4)
    rotation_only: bpy.props.BoolProperty(name="仅导入旋转", default=False)
    generate_thumbnails: bpy.props.BoolProperty(name="生成缩略图", default=True)


# ============================================================
# 注册
# ============================================================

classes = (
    PipelineProperties,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pipeline_props = bpy.props.PointerProperty(type=PipelineProperties)

def unregister():
    del bpy.types.Scene.pipeline_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
