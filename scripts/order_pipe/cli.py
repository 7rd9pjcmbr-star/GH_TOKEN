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
    ap.add_argument(
        "--fetch-orders",
        action="store_true",
        help="Lấy đơn realtime + re-pipe + scan buucuc (owned secrets)",
    )
    ap.add_argument(
        "--unmask-assist",
        action="store_true",
        help="Hỗ trợ giải mã unmask (ASUNMEE live + path + Frida/AEAD owned)",
    )
    ap.add_argument(
        "--session-audit",
        action="store_true",
        help="Rà soát key lấy đơn/login (mask only)",
    )
    ap.add_argument(
        "--session-ensure",
        action="store_true",
        help="Gom env + duy trì phiên (export/ensure/keepalive/ttl)",
    )
    ap.add_argument(
        "--session-maintain",
        action="store_true",
        help="Duy trì token TTL + heartbeat (chống hết hạn)",
    )
    ap.add_argument(
        "--sample-limit",
        type=int,
        default=20,
        help="Số đơn mẫu ASUNMEE live cho --unmask-assist",
    )

    args = ap.parse_args(argv)

    if args.list_stages:
        for sid in StageId:
            print(f"{sid.value}")
        return 0

    if args.session_audit or args.session_ensure or args.session_maintain:
        if args.session_maintain:
            from token_session_maintain import format_text as fmt_m
            from token_session_maintain import maintain_once

            report = maintain_once(notify_on_risk=False)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            else:
                print(fmt_m(report))
            return 0 if report.get("ok", True) else 1

        import order_session_env as ose

        if args.session_ensure:
            report = ose.ensure_session(via_nginx=False)
        else:
            report = ose.audit()
            ose.write_audit_outputs(report)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            print(ose.format_text(report))
        return 0 if report.get("ok", True) else 1

    # --- Fetch + unmask ops (có thể chạy độc lập) ---
    if args.fetch_orders or args.unmask_assist:
        from order_pipe import ReverseFlow
        from order_pipe.report import write_module_outputs
        from order_pipe.unmask import (
            resolve_key_file,
            run_asunmee_live,
            run_inner_warehouse,
            run_unmask_assist,
        )
        from order_pipe.fetch import fetch_realtime, repipe, scan_buucuc

        payload: dict = {
            "ok": True,
            "module": "order_pipe.fetch_unmask",
            "query": "Lấy đơn + hỗ trợ unmask",
            "fetch": None,
            "unmask": None,
            "safety": {
                "secrets_only": True,
                "no_dump_login": True,
                "mask_not_decryptable": True,
                "no_invent_timestamps": True,
            },
        }
        if args.fetch_orders:
            fr = fetch_realtime(limit=max(int(args.limit or 40), 80))
            rp = repipe(limit=8000)
            sc = scan_buucuc(days=3, limit=5000)
            rf = ReverseFlow(warehouse_id=args.warehouse)
            payload["fetch"] = {
                "realtime": {"ok": fr.get("ok"), "exit": fr.get("exit")},
                "repipe": {"ok": rp.get("ok"), "exit": rp.get("exit")},
                "scan": {"ok": sc.get("ok"), "exit": sc.get("exit")},
                "asumee_stats": rf.stats(),
                "tails": {
                    "realtime": fr.get("stdout_tail"),
                    "repipe": rp.get("stdout_tail"),
                    "scan": sc.get("stdout_tail"),
                },
            }
            payload["ok"] = bool(fr.get("ok") and rp.get("ok") and sc.get("ok"))
        if args.unmask_assist:
            kf = resolve_key_file()
            asu = run_asunmee_live(sample_limit=int(args.sample_limit or 20))
            um = run_unmask_assist(key_file=kf)
            inn = run_inner_warehouse(warehouse_id=args.warehouse, key_file=kf)
            # scrub heavy samples for console
            live = asu.get("live") or {}
            payload["unmask"] = {
                "asunmee_verdict": asu.get("verdict"),
                "live_ok": live.get("ok") if isinstance(live, dict) else None,
                "detail_unmasks": live.get("detail_unmasks") if isinstance(live, dict) else asu.get("detail_unmasks"),
                "total_entries": live.get("total_entries") if isinstance(live, dict) else None,
                "unmask_verdict": um.get("verdict"),
                "by_path": um.get("by_path"),
                "frida": {
                    "ok": (um.get("frida_a11y_aes") or {}).get("ok"),
                    "verdict": (um.get("frida_a11y_aes") or {}).get("verdict"),
                },
                "inner_verdict": inn.get("verdict"),
                "inner_warehouse": inn.get("warehouse_lookup"),
                "key_present": bool(kf),
                "policy": um.get("policy") or asu.get("policy"),
            }
        # write compact report
        from datetime import datetime, timezone
        from pathlib import Path as P
        import json as _json

        payload["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out_dir = P("reports/telegram-classify")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "order_pipe_fetch_unmask.json").write_text(
            _json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        lines = [
            "📦 ORDER PIPE · FETCH + UNMASK ASSIST",
            f"Lúc: {payload['checked_at']}",
            f"ok={payload['ok']}",
        ]
        if payload.get("fetch"):
            st = payload["fetch"].get("asumee_stats") or {}
            lines.append(
                f"Fetch ASUMEE n={st.get('orders')} trk_real={st.get('trk_real')} "
                f"wait={int(st.get('wait_submitted') or 0)+int(st.get('wait_new') or 0)}"
            )
            lines.append(
                f"realtime={payload['fetch']['realtime']} repipe={payload['fetch']['repipe']} "
                f"scan={payload['fetch']['scan']}"
            )
        if payload.get("unmask"):
            u = payload["unmask"]
            lines.append(f"ASUNMEE: {u.get('asunmee_verdict')}")
            lines.append(
                f"live_ok={u.get('live_ok')} detail_unmasks={u.get('detail_unmasks')} "
                f"shop_orders≈{u.get('total_entries')} key={u.get('key_present')}"
            )
            lines.append(f"Unmask: {u.get('unmask_verdict')}")
            lines.append(f"by_path={u.get('by_path')} frida={u.get('frida')}")
            lines.append(f"Inner: {u.get('inner_verdict')}")
            lines.append("Policy: **** = redaction — không AES-unmask; Frida AES ≠ Pancake PII")
        text = "\n".join(lines) + "\n"
        (out_dir / "order_pipe_fetch_unmask.txt").write_text(text, encoding="utf-8")
        if args.json:
            print(_json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            print(text)
        return 0 if payload.get("ok") else 1

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
