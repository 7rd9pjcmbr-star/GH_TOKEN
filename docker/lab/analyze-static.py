#!/usr/bin/env python3
"""Docker lab analyze entry — lab_static_v2 compatible.

Accepts /quarantine/* paths. Static-only. No execution.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Inline minimal bootstrap if engine not mounted
ROOT_CANDIDATES = [Path("/lab"), Path("/workspace"), Path(__file__).resolve().parent]

def main() -> int:
    if len(sys.argv) < 2:
        print("usage: analyze-static.py <file>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1]).resolve()
    allowed = str(path).startswith("/quarantine/")
    if not allowed:
        print("refusing path outside /quarantine", file=sys.stderr)
        return 3
    # Prefer copied engine next to this file
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from lab_static_engine import analyze_path, write_report
    except ImportError:
        # fallback: copy-free minimal
        print(json.dumps({"ok": False, "error": "lab_static_engine missing in image"}))
        return 4
    report = analyze_path(path, surface="docker-lab-v2")
    out = Path("/reports") / "lab" / "static" / f"{path.name}.v2.json"
    write_report(report, out)
    # legacy mirror
    write_report(report, Path("/reports") / f"{path.name}.report.json")
    print(json.dumps(report, indent=2))
    print(f"\n# wrote {out}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
