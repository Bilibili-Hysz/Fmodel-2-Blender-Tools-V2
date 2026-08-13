"""Generate or verify the bundled UEFormat Vendor tree manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.vendor_tree_manifest import (  # noqa: E402
    TREE_MANIFEST_FILENAME,
    VendorTreeManifestError,
    build_vendor_tree_manifest,
    verify_vendor_tree,
    write_vendor_tree_manifest,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or verify the reproducible FModel Tools Vendor tree manifest."
    )
    parser.add_argument(
        "--vendor-root",
        type=Path,
        default=PROJECT_ROOT / "vendor",
        help="Vendor root directory (default: <project>/vendor)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Manifest path (default: <vendor-root>/vendor_tree_manifest.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the existing manifest instead of regenerating it.",
    )
    parser.add_argument(
        "--allow-extra-files",
        action="store_true",
        help="Do not fail verification when non-excluded files are not listed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    vendor_root = args.vendor_root.expanduser().resolve()
    manifest_path = (args.manifest or (vendor_root / TREE_MANIFEST_FILENAME)).expanduser().resolve()

    try:
        if args.check:
            result = verify_vendor_tree(
                manifest_path,
                vendor_root=vendor_root,
                strict=not args.allow_extra_files,
            )
            print(json.dumps({
                "status": "verified",
                "manifest": str(result.manifest_path),
                "vendor_root": str(result.vendor_root),
                "file_count": result.file_count,
                "tree_sha256": result.tree_sha256,
            }, ensure_ascii=False, sort_keys=True))
            return 0

        manifest = build_vendor_tree_manifest(vendor_root)
        write_vendor_tree_manifest(manifest, manifest_path)
        result = verify_vendor_tree(manifest_path, vendor_root=vendor_root)
        print(json.dumps({
            "status": "generated",
            "manifest": str(result.manifest_path),
            "vendor_root": str(result.vendor_root),
            "file_count": result.file_count,
            "tree_sha256": result.tree_sha256,
            "source_commit": manifest.source_commit,
            "source_snapshot": manifest.source_snapshot,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except VendorTreeManifestError as exc:
        print(f"Vendor tree manifest failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
