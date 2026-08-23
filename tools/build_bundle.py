from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skilltree.bundle import BundleValidationError, build_bundle, validate_bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build and validate the offline SkillTree Plugin Bundle")
    parser.add_argument("--output-dir", type=Path, help="optional new directory receiving a staged Plugin Bundle")
    parser.add_argument("--json-out", type=Path, help="optional JSON build report path")
    args = parser.parse_args()
    try:
        manifest = build_bundle(ROOT)
        if args.output_dir:
            destination = args.output_dir.resolve()
            if destination == (ROOT / "plugins" / "skilltree").resolve() or destination.exists():
                raise BundleValidationError("output directory must be a new path outside the source Plugin")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="skilltree-release-", dir=destination.parent) as temporary:
                staged = Path(temporary) / "skilltree"
                shutil.copytree(
                    ROOT / "plugins" / "skilltree",
                    staged,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
                staged.rename(destination)
            manifest = validate_bundle(destination)
    except BundleValidationError as error:
        print(f"bundle build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    report = {"schema_version": "skilltree-release-build/v1", "ok": True, "bundle_hash": manifest["bundle_hash"]}
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(manifest["bundle_hash"])
