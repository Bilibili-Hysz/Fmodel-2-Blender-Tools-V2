import os
from typing import Dict, List

from .builder import build_graph_anime, build_graph_realistic
from .rules import CHANNELS
from .scanners import extract_metadata_texture_hints, load_sidecar_metadata, scan_texture_index
from .scoring import pick_best_candidate, score_candidate
from .types import (
    ChannelCandidate,
    ChannelResolveResult,
    MaterialResolveInput,
    MaterialResolveResult,
)


def _normalize(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum() or c in ("_", "-"))


def _collect_candidates(inp: MaterialResolveInput) -> Dict[str, List[ChannelCandidate]]:
    out: Dict[str, List[ChannelCandidate]] = {ch: [] for ch in CHANNELS}

    tex_index = scan_texture_index(inp.texture_dir)
    metadata = load_sidecar_metadata(inp.metadata_path)
    hints = extract_metadata_texture_hints(metadata)

    for ch, hinted in hints.items():
        if ch not in out:
            continue
        cand = ChannelCandidate(channel=ch, path=hinted, source="metadata")
        cand.score = score_candidate(inp.material_name, ch, hinted, {"context_dir": inp.texture_dir, "source": "metadata"})
        out[ch].append(cand)

    mat_token = _normalize(inp.material_name)
    for token, paths in tex_index.items():
        if mat_token and mat_token not in token and token not in mat_token:
            continue
        for path in paths:
            for ch in CHANNELS:
                cand = ChannelCandidate(channel=ch, path=path, source="rule")
                cand.score = score_candidate(inp.material_name, ch, path, {"context_dir": inp.texture_dir, "source": "rule"})
                if cand.score > 0.25:
                    out[ch].append(cand)

    return out


def resolve_material(input_data: MaterialResolveInput, style="realistic", analyze_only=False) -> MaterialResolveResult:
    result = MaterialResolveResult(material_name=input_data.material_name, style=style, success=False, analyze_only=analyze_only)

    try:
        cands = _collect_candidates(input_data)
        resolved_paths: Dict[str, str] = {}

        for ch in CHANNELS:
            picked = pick_best_candidate(cands[ch])
            ch_result = ChannelResolveResult(channel=ch)
            if picked:
                ch_result.selected_path = picked.path
                ch_result.source = picked.source
                ch_result.score = picked.score
                ch_result.alternatives = sorted(cands[ch], key=lambda x: x.score, reverse=True)[:5]
                ch_result.ambiguous = picked.meta.get("ambiguous") == "1"
                resolved_paths[ch] = picked.path
            else:
                result.missing_channels.append(ch)
            result.channels[ch] = ch_result

        if not analyze_only and input_data.material_obj:
            if style == "anime":
                build_graph_anime(input_data.material_obj, resolved_paths)
            else:
                build_graph_realistic(input_data.material_obj, resolved_paths)

        critical_set = {"base_color", "normal"}
        result.critical_missing_channels = [ch for ch in result.missing_channels if ch in critical_set]
        result.noncritical_missing_channels = [ch for ch in result.missing_channels if ch not in critical_set]

        if result.missing_channels:
            result.warnings.append("missing:" + ",".join(result.missing_channels))
        if result.critical_missing_channels:
            result.warnings.append("critical_missing:" + ",".join(result.critical_missing_channels))

        ambiguous = [ch for ch, v in result.channels.items() if v.ambiguous]
        if ambiguous:
            result.warnings.append("ambiguous:" + ",".join(ambiguous))

        if input_data.strict_mode:
            if result.critical_missing_channels:
                result.errors.append("strict_missing:" + ",".join(result.critical_missing_channels))
            if ambiguous:
                result.errors.append("strict_ambiguous:" + ",".join(ambiguous))

        result.success = len(result.errors) == 0
        return result
    except Exception as e:
        result.errors.append(str(e))
        result.success = False
        return result


def resolve_objects_materials(objects, tex_dir, mat_dir, style, analyze_only=False):
    results = []
    for obj in objects:
        if not obj or obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            if not slot.material:
                continue
            mat = slot.material
            meta_path = ""
            if mat_dir:
                meta_path = os.path.join(mat_dir, f"{mat.name}.json")
            inp = MaterialResolveInput(
                material_name=mat.name,
                material_obj=mat,
                texture_dir=tex_dir,
                material_dir=mat_dir,
                metadata_path=meta_path,
                style=style,
                strict_mode=False,
            )
            results.append(resolve_material(inp, style=style, analyze_only=analyze_only))
    return results


def resolve_objects_materials_with_options(objects, tex_dir, mat_dir, style, analyze_only=False, strict_mode=False):
    results = []
    for obj in objects:
        if not obj or obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            if not slot.material:
                continue
            mat = slot.material
            meta_path = ""
            if mat_dir:
                meta_path = os.path.join(mat_dir, f"{mat.name}.json")
            inp = MaterialResolveInput(
                material_name=mat.name,
                material_obj=mat,
                texture_dir=tex_dir,
                material_dir=mat_dir,
                metadata_path=meta_path,
                style=style,
                strict_mode=strict_mode,
            )
            results.append(resolve_material(inp, style=style, analyze_only=analyze_only))
    return results
