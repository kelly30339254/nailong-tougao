"""Validate release metadata before building distributable packages."""
from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import APP_VERSION  # noqa: E402
from app.announcements import validate_release_announcement  # noqa: E402


def main() -> int:
    announcements = ROOT / "app" / "data" / "announcements.json"
    try:
        validate_release_announcement(APP_VERSION, announcements)
    except ValueError as exc:
        print(f"[ERROR] release announcement validation failed: {exc}", file=sys.stderr)
        return 1

    ref_type = os.environ.get("GITHUB_REF_TYPE", "").strip()
    ref_name = os.environ.get("GITHUB_REF_NAME", "").strip()
    if ref_type == "tag" and ref_name != f"v{APP_VERSION}":
        print(
            f"[ERROR] Git tag {ref_name!r} does not match APP_VERSION "
            f"v{APP_VERSION}",
            file=sys.stderr,
        )
        return 1

    print(f"[OK] release metadata v{APP_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
