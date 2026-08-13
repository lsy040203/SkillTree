"""P0.1 fail-open Hook entry point; P3 adds sanitized event collection."""

from __future__ import annotations

def main() -> int:
    # P0.1 deliberately does not read stdin or any Plugin data. P0.2 adds the
    # reserved bootstrap path; P3 adds sanitized observation after authorization.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
