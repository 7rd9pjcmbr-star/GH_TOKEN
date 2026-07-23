#!/usr/bin/env python3
"""Kiểm thử nhúng gọi đơn qua nginx — wrapper của module nginx_order_embed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nginx_order_embed import (  # noqa: E402
    NginxOrderEmbed,
    format_text,
    run_when_needed,
    write_outputs,
)


def run_test(*, base: str = "http://127.0.0.1:18080") -> dict:
    mod = NginxOrderEmbed(base=base, auto_stop=True)
    return mod.test()


def main() -> int:
    ap = argparse.ArgumentParser(description="Test nhúng gọi đơn qua nginx (on-demand module)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--base", default="http://127.0.0.1:18080")
    ap.add_argument("--once", action="store_true", help="alias run_when_needed()")
    args = ap.parse_args()
    report = run_when_needed() if args.once else run_test(base=args.base)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
