from .types import (
    MaterialResolveInput,
    ChannelCandidate,
    ChannelResolveResult,
    MaterialResolveResult,
    ResolveReport,
)
from .engine import resolve_material, resolve_objects_materials, resolve_objects_materials_with_options
from .report import format_summary, export_report_json

__all__ = [
    "MaterialResolveInput",
    "ChannelCandidate",
    "ChannelResolveResult",
    "MaterialResolveResult",
    "ResolveReport",
    "resolve_material",
    "resolve_objects_materials",
    "resolve_objects_materials_with_options",
    "format_summary",
    "export_report_json",
]
