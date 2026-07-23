"""CLI — python -m order_pipe / scripts/order_pipe_module.py."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure scripts/ on path when run as module
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def main(argv: list[str] | None = None) -> int:
    from order_pipe import (
        ASUMEE_WID,
        SAFE_STAGES,
        ReverseFlow,
        StageId,
    )
    from order_pipe.report import build_module_report, format_module_text, write_module_outputs
    from order_pipe.stages import STAGE_RUNNERS

    ap = argparse.ArgumentParser(
        description="Order Pipe module — truy vấn ngược theo capability (seed…close)"
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--run",
        action="store_true",
        help=f"Chạy pipeline mặc định an toàn: {','.join(SAFE_STAGES)}",
    )
    ap.add_argument(
        "--stages",
        help=f"Danh sách stage CSV. Known: {','.join(STAGE_RUNNERS)}",
    )
    ap.add_argument("--live", action="store_true", help="Bật live Pancake detail")
    ap.add_argument("--apply", action="store_true", help="Ghi DB (mặc định dry-run)")
    ap.add_argument("--probe", action="store_true", help="Probe aship URL (tracking/waiting)")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--warehouse", default=ASUMEE_WID)

    # Lookups
    ap.add_argument("--van-tay")
    ap.add_argument("--so")
    ap.add_argument("--tracking")
    ap.add_argument("--kho")
    ap.add_argument("--buucuc")
    ap.add_argument("--province")
    ap.add_argument("--address")
    ap.add_argument("--q", help="Auto-detect query")

    # Legacy bridge
    ap.add_argument(
        "--legacy-continue-flow",
        action="store_true",
        help="Ủy quyền sang order_pipe_reverse_query --continue-flow",
    )
    ap.add_argument("--hop13-apply", action="store_true")
    ap.add_argument("--hop12-apply", action="store_true")
    ap.add_argument("--hop7-apply", action="store_true")

    ap.add_argument(
        "--list-stages",
        action="store_true",
        help="In danh sách capability stages",
    )

    args = ap.parse_args(argv)

    if args.list_stages:
        for sid in StageId:
            print(f"{sid.value}")
        return 0

    if args.legacy_continue_flow or args.hop13_apply or args.hop12_apply or args.hop7_apply:
        import order_pipe_reverse_query as rq

        # Rebuild argv for legacy main
        legacy = ["--continue-flow"]
        if args.hop13_apply:
            legacy += ["--hop13-live", "--hop13-apply"]
        if args.hop12_apply:
            legacy += ["--hop12-live", "--hop12-apply"]
        if args.hop7_apply:
            legacy += ["--hop7-apply", f"--hop7-limit={args.limit}"]
        if args.json:
            legacy.append("--json")
        sys.argv = ["order_pipe_reverse_query.py", *legacy]
        return int(rq.main())

    rf = ReverseFlow(warehouse_id=args.warehouse)
    lookups: list[dict] = []

    if args.van_tay:
        lookups.append(rf.lookup.by_van_tay(args.van_tay))
    if args.so:
        lookups.append(rf.lookup.by_so(args.so))
    if args.tracking:
        lookups.append(rf.lookup.by_tracking(args.tracking))
    if args.kho:
        lookups.append(rf.lookup.by_kho(args.kho))
    if args.buucuc:
        lookups.append(rf.lookup.by_buucuc(args.buucuc))
    if args.province:
        lookups.append(rf.lookup.by_province(args.province))
    if args.address:
        lookups.append(rf.lookup.by_address(args.address))
    if args.q:
        lookups.extend(rf.lookup.auto(args.q))

    run_pipe = bool(args.run or args.stages)
    # Default: if only lookups, skip pipeline; if nothing, --run safe stages
    if not lookups and not run_pipe:
        run_pipe = True

    pipe_result = None
    if run_pipe:
        stages = args.stages
        pipe_result = rf.pipeline.run(
            stages=stages,
            live=bool(args.live),
            apply=bool(args.apply),
            limit=int(args.limit or 40),
            probe=bool(args.probe),
        )
    else:
        from order_pipe.pipeline import PipelineResult

        pipe_result = PipelineResult(ok=True, stages=[], results=[], meta={"skipped": True})

    report = build_module_report(rf.store, pipe_result, lookups=lookups)
    write_module_outputs(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_module_text(report))
    return 0 if pipe_result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
