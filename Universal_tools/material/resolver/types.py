from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MaterialResolveInput:
    material_name: str
    material_obj: object
    texture_dir: str
    material_dir: str = ""
    metadata_path: str = ""
    style: str = "realistic"
    strict_mode: bool = False


@dataclass
class ChannelCandidate:
    channel: str
    path: str
    source: str
    score: float = 0.0
    meta: Dict[str, str] = field(default_factory=dict)


@dataclass
class ChannelResolveResult:
    channel: str
    selected_path: str = ""
    source: str = ""
    score: float = 0.0
    ambiguous: bool = False
    alternatives: List[ChannelCandidate] = field(default_factory=list)


@dataclass
class MaterialResolveResult:
    material_name: str
    style: str
    success: bool
    channels: Dict[str, ChannelResolveResult] = field(default_factory=dict)
    missing_channels: List[str] = field(default_factory=list)
    critical_missing_channels: List[str] = field(default_factory=list)
    noncritical_missing_channels: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    analyze_only: bool = False


@dataclass
class ResolveReport:
    total_materials: int = 0
    success_materials: int = 0
    failed_materials: int = 0
    ambiguous_channels: int = 0
    missing_channels: int = 0
    critical_missing_channels: int = 0
    items: List[MaterialResolveResult] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)


def make_empty_channel_result(channel: str) -> ChannelResolveResult:
    return ChannelResolveResult(channel=channel)


def get_selected_paths(result: MaterialResolveResult) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for ch, ch_result in result.channels.items():
        if ch_result.selected_path:
            out[ch] = ch_result.selected_path
    return out


def count_ambiguous(result: MaterialResolveResult) -> int:
    count = 0
    for ch_result in result.channels.values():
        if ch_result.ambiguous:
            count += 1
    return count


def first_error(result: MaterialResolveResult) -> Optional[str]:
    if result.errors:
        return result.errors[0]
    return None
