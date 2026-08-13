from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skilltree.bundle import BundleValidationError, build_bundle


if __name__ == "__main__":
    try:
        manifest = build_bundle(ROOT)
    except BundleValidationError as error:
        print(f"bundle build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(manifest["bundle_hash"])
