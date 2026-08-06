import json
import os

from .types import ResolveReport


def build_report(items):
    rep = ResolveReport()
    rep.items = items
    rep.total_materials = len(items)
    rep.success_materials = sum(1 for i in items if i.success)
    rep.failed_materials = rep.total_materials - rep.success_materials
    rep.ambiguous_channels = sum(
        1 for i in items for ch in i.channels.values() if ch.ambiguous
    )
    rep.missing_channels = sum(len(i.missing_channels) for i in items)
    rep.critical_missing_channels = sum(len(i.critical_missing_channels) for i in items)
    rep.summary = {
        "total": rep.total_materials,
        "success": rep.success_materials,
        "failed": rep.failed_materials,
        "ambiguous_channels": rep.ambiguous_channels,
        "missing_channels": rep.missing_channels,
        "critical_missing_channels": rep.critical_missing_channels,
    }
    return rep


def format_summary(results):
    rep = build_report(results)
    s = rep.summary
    return (
        f"materials={s['total']} "
        f"success={s['success']} "
        f"failed={s['failed']} "
        f"ambiguous={s['ambiguous_channels']} "
        f"missing={s['missing_channels']} "
        f"critical_missing={s['critical_missing_channels']}"
    )


def export_report_json(results, output_path):
    rep = build_report(results)
    data = {
        "summary": rep.summary,
        "items": [],
    }
    for item in rep.items:
        data["items"].append({
            "material": item.material_name,
            "style": item.style,
            "success": item.success,
            "missing": item.missing_channels,
            "warnings": item.warnings,
            "errors": item.errors,
            "channels": {
                ch: {
                    "path": cr.selected_path,
                    "source": cr.source,
                    "score": cr.score,
                    "ambiguous": cr.ambiguous,
                    "alternatives": [
                        {
                            "path": alt.path,
                            "source": alt.source,
                            "score": alt.score,
                        }
                        for alt in cr.alternatives[:5]
                    ],
                }
                for ch, cr in item.channels.items()
            },
            "missing_detail": {
                "all": item.missing_channels,
                "critical": item.critical_missing_channels,
                "noncritical": item.noncritical_missing_channels,
            },
        })

    abs_path = os.path.normpath(os.path.abspath(output_path))
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return abs_path
