#!/usr/bin/env python3
"""Mapper đơn hàng thời gian thực từ backend toàn diện.

Gom:
  - OMS channel probe (oms_interconnect)
  - realtime_order_sync cycle (Pancake/GHN/TPOS/inbox)
  - Ingest normalize mọi nguồn local (CSV/JSON/SPX)
  - Map từng đơn → backend · kho · carrier · NS · phone
  - Icon feedback realtime

Secrets-only cho remote. Không dump login.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "realtime"
STATE_FILE = ROOT / "secrets" / "realtime_order_backend_mapper.state.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def phone_class(ph: str | None) -> str:
    ph = (ph or "").strip()
    if not ph:
        return "MISSING"
    if "*" in ph or set(ph) <= {"*"}:
        return "MASKED"
    digits = "".join(c for c in ph if c.isdigit())
    return "OK" if len(digits) >= 9 else "INVALID"


def resolve_backend(rec: dict) -> str:
    """Map normalized / raw order → canonical backend id."""
    ch = (rec.get("channel") or "").lower()
    src = (rec.get("source") or "").lower()
    platform = (rec.get("platform") or "").lower()
    carrier = (rec.get("carrier") or "").upper()

    if ch in {"spx_local", "pancake_payload", "inbox_csv", "direct_api", "json_flat"}:
        if ch == "spx_local" or platform == "spx" or carrier == "SPX":
            return "SPX-local"
        if "direct_api" in src or ch == "direct_api":
            return "direct_api"
        if "telegram" in src or "telegram" in platform:
            return "Telegram-upload"
        if "pancake" in src or ch == "pancake_payload":
            return "Pancake"
        if src == "sample":
            return "sample"
    if carrier and carrier not in {"(NONE)", "(NO_SHIPMENT)", "(UNKNOWN_CSV)"}:
        if "GHN" in carrier or "GIAOHANG" in carrier:
            return "GHN"
        if "VIETTEL" in carrier or carrier == "VTP":
            return "ViettelPost"
        if carrier == "SPX":
            return "SPX-local"
        return f"carrier:{carrier}"
    if "pancake" in src:
        return "Pancake"
    if "direct_api" in src:
        return "direct_api"
    if "telegram" in src:
        return "Telegram-upload"
    if src == "sample":
        return "sample"
    if rec.get("_backend"):
        return str(rec["_backend"])
    return "unknown"


def enrich_order(rec: dict, backend_status: dict[str, str]) -> dict:
    backend = resolve_backend(rec)
    phone = rec.get("customer_phone") or rec.get("bill_phone_number") or ""
    pc = rec.get("phone_class") or phone_class(str(phone))
    pipe = backend_status.get(backend) or backend_status.get(
        {
            "Pancake": "Pancake",
            "Telegram-upload": "Telegram",
            "SPX-local": "SPX-local",
            "direct_api": "direct_api",
            "GHN": "GHN",
            "ViettelPost": "ViettelPost",
            "sample": "OMS-pipe-bus",
            "unknown": "OMS-pipe-bus",
        }.get(backend, backend)
    )
    return {
        "oms_id": rec.get("oms_id") or rec.get("order_key") or rec.get("id") or rec.get("remote_id"),
        "order_key": rec.get("order_key") or rec.get("remote_id") or rec.get("id"),
        "backend": backend,
        "pipe_status": pipe or "unknown",
        "source": rec.get("source"),
        "channel": rec.get("channel"),
        "platform": rec.get("platform"),
        "shop_id": rec.get("shop_id"),
        "status": rec.get("status") or rec.get("status_normalized") or rec.get("status_raw"),
        "kho": rec.get("warehouse_name") or rec.get("kho"),
        "warehouse_id": rec.get("warehouse_id"),
        "carrier": rec.get("carrier"),
        "tracking_code": rec.get("tracking_code"),
        "province": rec.get("province"),
        "district": rec.get("district"),
        "staff_creator": rec.get("creator") or rec.get("nhan_su_creator"),
        "staff_seller": rec.get("assigning_seller") or rec.get("nhan_su_seller"),
        "staff_care": rec.get("assigning_care") or rec.get("nhan_su_care"),
        "customer_name": rec.get("customer_name"),
        "customer_phone": phone,
        "phone_class": pc,
        "file": rec.get("file") or rec.get("_file"),
        "realtime_new": bool(rec.get("_realtime_new")),
    }


def build_report(*, ingest_limit: int = 5000) -> dict[str, Any]:
    from oms_interconnect import ingest_local_orders, interconnect, load_env
    from realtime_order_sync import run_cycle

    try:
        from realtime_icon_feedback_mapper import build_from_live, chant, feedback_line
    except Exception:  # noqa: BLE001
        build_from_live = None  # type: ignore
        chant = lambda icons: " → ".join(icons)  # noqa: E731
        feedback_line = lambda icons, d="": f"Mapper gọi: {chant(icons)} — {d}"  # noqa: E731

    env = load_env()
    oms = interconnect(env, ingest=False)
    rt = run_cycle(env, limit=20, notify=False, notify_new_only=False)

    backend_status: dict[str, str] = {}
    for c in oms.get("channels") or []:
        backend_status[c.get("backend") or c.get("id")] = c.get("status") or "unknown"
        backend_status[c.get("id")] = c.get("status") or "unknown"
    for b in rt.get("backends") or []:
        # prefer live sync status when present
        backend_status[b.get("backend")] = b.get("status") or backend_status.get(b.get("backend"), "unknown")

    # Local ingest (comprehensive snapshot)
    local_recs = ingest_local_orders(limit_per_file=ingest_limit)
    mapped_local = [enrich_order(r, backend_status) for r in local_recs]

    # Realtime new orders from cycle
    rt_new_raw = rt.get("all_new_orders") or []
    mapped_rt_new = []
    for o in rt_new_raw:
        oo = dict(o)
        oo["_realtime_new"] = True
        oo["channel"] = oo.get("_backend") or "realtime"
        oo["source"] = oo.get("source") or oo.get("_backend")
        mapped_rt_new.append(enrich_order(oo, backend_status))

    all_mapped = mapped_local + mapped_rt_new

    # Aggregate by backend
    by_backend: dict[str, dict] = {}
    for m in all_mapped:
        b = m["backend"]
        bucket = by_backend.setdefault(
            b,
            {
                "backend": b,
                "pipe_status": m.get("pipe_status"),
                "orders": 0,
                "realtime_new": 0,
                "phone": Counter(),
                "status": Counter(),
                "kho": Counter(),
                "carrier": Counter(),
                "shop": Counter(),
                "province": Counter(),
                "staff_creator": Counter(),
                "with_tracking": 0,
                "with_seller": 0,
                "with_care": 0,
                "samples": [],
            },
        )
        bucket["orders"] += 1
        if m.get("realtime_new"):
            bucket["realtime_new"] += 1
        bucket["phone"][m.get("phone_class") or "?"] += 1
        bucket["status"][m.get("status") or "(null)"] += 1
        if m.get("kho"):
            bucket["kho"][str(m["kho"])[:80]] += 1
        if m.get("carrier"):
            bucket["carrier"][str(m["carrier"])[:80]] += 1
        if m.get("shop_id"):
            bucket["shop"][str(m["shop_id"])] += 1
        if m.get("province"):
            bucket["province"][str(m["province"])[:60]] += 1
        if m.get("staff_creator"):
            bucket["staff_creator"][str(m["staff_creator"])[:80]] += 1
        if m.get("tracking_code"):
            bucket["with_tracking"] += 1
        if m.get("staff_seller"):
            bucket["with_seller"] += 1
        if m.get("staff_care"):
            bucket["with_care"] += 1
        if len(bucket["samples"]) < 5:
            bucket["samples"].append(
                {
                    "oms_id": m.get("oms_id"),
                    "status": m.get("status"),
                    "phone_class": m.get("phone_class"),
                    "kho": m.get("kho"),
                    "carrier": m.get("carrier"),
                    "tracking_code": m.get("tracking_code"),
                    "realtime_new": m.get("realtime_new"),
                }
            )

    backends_out = []
    for b, bucket in sorted(by_backend.items(), key=lambda x: -x[1]["orders"]):
        backends_out.append(
            {
                "backend": b,
                "pipe_status": backend_status.get(b) or bucket.get("pipe_status"),
                "orders": bucket["orders"],
                "realtime_new": bucket["realtime_new"],
                "phone": dict(bucket["phone"]),
                "status_top": bucket["status"].most_common(8),
                "kho_top": bucket["kho"].most_common(8),
                "carrier_top": bucket["carrier"].most_common(8),
                "shop_top": bucket["shop"].most_common(8),
                "province_top": bucket["province"].most_common(8),
                "staff_creator_top": bucket["staff_creator"].most_common(5),
                "with_tracking": bucket["with_tracking"],
                "with_seller": bucket["with_seller"],
                "with_care": bucket["with_care"],
                "samples": bucket["samples"],
            }
        )

    # Channel matrix: OMS channel × order backend
    channel_matrix = []
    for c in oms.get("channels") or []:
        bid = c.get("backend") or c.get("id")
        match = next((x for x in backends_out if x["backend"] == bid or x["backend"].startswith(str(bid))), None)
        # also fuzzy
        if not match:
            for x in backends_out:
                if bid and (bid.lower() in x["backend"].lower() or x["backend"].lower() in str(bid).lower()):
                    match = x
                    break
        channel_matrix.append(
            {
                "oms_channel": bid,
                "kind": c.get("kind"),
                "pipe_status": c.get("status"),
                "detail": c.get("detail"),
                "mapped_orders": (match or {}).get("orders", 0),
                "realtime_new": (match or {}).get("realtime_new", 0),
                "phone": (match or {}).get("phone"),
            }
        )

    icons = ["spark", "monitor", "cpu", "cube", "network", "compass", "key"]
    connected_n = sum(1 for c in oms.get("channels") or [] if c.get("status") == "connected")
    fb = feedback_line(
        icons,
        f"backends={len(backends_out)} orders={len(all_mapped)} rt_new={len(mapped_rt_new)} "
        f"oms_connected={connected_n}/{(len(oms.get('channels') or []) or 1)}",
    )

    # Icon paths per backend
    icon_paths = []
    for b in backends_out[:20]:
        st = b.get("pipe_status") or "unknown"
        if st in {"connected", "alive", "ok"}:
            chant_icons = ["monitor", "cpu"]
        elif st == "missing_cred":
            chant_icons = ["key", "lock"]
        else:
            chant_icons = ["wrench"]
        if b["backend"] in {"SPX-local", "carrier:SPX"}:
            chant_icons = ["cube", "network", "compass"] + chant_icons
        elif "Pancake" in b["backend"]:
            chant_icons = ["layers"] + chant_icons
        elif b["backend"] in {"GHN", "ViettelPost"}:
            chant_icons = ["network"] + chant_icons
        icon_paths.append(
            {
                "backend": b["backend"],
                "orders": b["orders"],
                "icon_chant": chant(chant_icons),
                "feedback": feedback_line(
                    chant_icons,
                    f"{b['backend']}: pipe={st} orders={b['orders']} rt_new={b['realtime_new']} phone={b['phone']}",
                ),
            }
        )

    if build_from_live:
        try:
            icon_rt = build_from_live()
            global_fb = (icon_rt.get("global") or {}).get("feedback") or fb
            global_chant = (icon_rt.get("global") or {}).get("icon_chant") or chant(icons)
        except Exception:  # noqa: BLE001
            global_fb, global_chant = fb, chant(icons)
    else:
        global_fb, global_chant = fb, chant(icons)

    dang = [m for m in all_mapped if m.get("status") and "giao" in str(m.get("status")).lower()]
    daklak = [
        m
        for m in all_mapped
        if m.get("province")
        and (
            "đắk lắk" in str(m["province"]).lower()
            or "dak lak" in str(m["province"]).lower()
            or "daklak" in str(m["province"]).lower().replace(" ", "")
        )
    ]

    verdict = (
        f"Mapper đơn RT toàn diện: {len(all_mapped)} đơn · {len(backends_out)} backend · "
        f"realtime_new={len(mapped_rt_new)} · OMS connected {connected_n}/{len(oms.get('channels') or [])} · "
        f"Đang giao≈{len(dang)} · Đắk Lắk={len(daklak)}. {global_fb}"
    )

    report = {
        "ok": True,
        "query": "Mapper đơn hàng thời gian thực từ backend toàn diện",
        "checked_at": utc_now(),
        "summary": {
            "orders_mapped": len(all_mapped),
            "local_orders": len(mapped_local),
            "realtime_new": len(mapped_rt_new),
            "backends": len(backends_out),
            "oms_connected": connected_n,
            "oms_channels": len(oms.get("channels") or []),
            "dang_giao_approx": len(dang),
            "daklak": len(daklak),
            "phone": dict(Counter(m.get("phone_class") for m in all_mapped)),
            "icon_chant": global_chant,
            "feedback": global_fb,
        },
        "oms_channels": [
            {
                "id": c.get("id"),
                "backend": c.get("backend"),
                "status": c.get("status"),
                "detail": c.get("detail"),
                "kind": c.get("kind"),
            }
            for c in oms.get("channels") or []
        ],
        "realtime_cycle": {
            "checked_at": rt.get("checked_at"),
            "new_count": rt.get("new_count"),
            "blocked": rt.get("blocked"),
            "backends": [
                {
                    "backend": b.get("backend"),
                    "status": b.get("status"),
                    "detail": b.get("detail"),
                    "new": len(b.get("new_orders") or []),
                }
                for b in rt.get("backends") or []
            ],
        },
        "by_backend": backends_out,
        "channel_matrix": channel_matrix,
        "icon_paths": icon_paths,
        "daklak_orders": [
            {
                "oms_id": m.get("oms_id"),
                "backend": m.get("backend"),
                "kho": m.get("kho"),
                "carrier": m.get("carrier"),
                "tracking_code": m.get("tracking_code"),
                "province": m.get("province"),
                "district": m.get("district"),
                "staff_creator": m.get("staff_creator"),
                "status": m.get("status"),
            }
            for m in daklak
        ],
        "sample_orders": all_mapped[:40],
        "mermaid": """flowchart LR
  subgraph BE[Backends RT]
    PK[Pancake]
    DA[direct_api]
    TG[Telegram]
    SPX[SPX]
    GHN[GHN]
    VTP[VTP]
    TRK[tracking]
  end
  subgraph OMS
    BUS[OMS mapper]
    DG[Đang giao]
  end
  PK --> BUS
  DA --> BUS
  TG --> BUS
  SPX --> BUS
  GHN -.-> BUS
  VTP -.-> BUS
  BUS --> DG
  SPX --> TRK
  GHN -.-> TRK
""",
        "verdict": verdict,
        "next_actions": [
            "Thêm PANCAKE_POS_API_KEY / GHN_API_TOKEN / VIETTELPOST_TOKEN để realtime API kéo đơn mới",
            "Chạy loop: python3 scripts/realtime_order_backend_mapper.py && realtime_order_sync.py --loop",
            "Join tracking codes vào Đang giao để backend carrier không còn unknown",
            "Panel Telegram: Mapper RT đơn để nhận phản hồi icon + thống kê backend",
        ],
        "policy": "secrets-only remote; local ingest always; no dump login",
    }

    # state
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "updated_at": utc_now(),
                "orders_mapped": len(all_mapped),
                "realtime_new": len(mapped_rt_new),
                "backends": {b["backend"]: b["orders"] for b in backends_out},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("⏱ MAPPER ĐƠN HÀNG REALTIME — BACKEND TOÀN DIỆN")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    L(f"✨ {s.get('feedback')}")
    L(f"Chant: {s.get('icon_chant')}")
    L("")
    L("=== OMS channels ===")
    for c in report["oms_channels"]:
        L(f"· {c.get('backend')}: {c.get('status')} — {str(c.get('detail') or '')[:90]}")
    L("")
    L("=== Realtime sync cycle ===")
    rt = report["realtime_cycle"]
    L(f"· at={rt.get('checked_at')} new={rt.get('new_count')} blocked={rt.get('blocked')}")
    for b in rt.get("backends") or []:
        L(f"  - {b['backend']}: {b['status']} new={b['new']} · {str(b.get('detail'))[:80]}")
    L("")
    L("=== Đơn theo backend ===")
    for b in report["by_backend"]:
        L(
            f"▶ {b['backend']} · orders={b['orders']} rt_new={b['realtime_new']} "
            f"pipe={b.get('pipe_status')} phone={b['phone']}"
        )
        L(f"  kho={b['kho_top'][:3]} carrier={b['carrier_top'][:3]}")
        L(
            f"  tracking={b['with_tracking']} seller={b['with_seller']} care={b['with_care']} "
            f"creator={b['staff_creator_top'][:2]}"
        )
        for s in b["samples"][:2]:
            L(f"  · sample {s}")
    L("")
    L("=== Channel matrix ===")
    for row in report["channel_matrix"]:
        L(
            f"· {row['oms_channel']}: pipe={row['pipe_status']} "
            f"mapped_orders={row['mapped_orders']} rt_new={row['realtime_new']}"
        )
    L("")
    L("=== Icon paths ===")
    for p in report["icon_paths"][:12]:
        L(f"· {p['feedback']}")
    if report.get("daklak_orders"):
        L("")
        L("=== Đắk Lắk ===")
        for d in report["daklak_orders"]:
            L(f"· {d}")
    L("")
    L("Next:")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    text = format_text(report)
    paths = {
        "json": REPORTS / "realtime_order_backend_mapper.json",
        "txt": REPORTS / "realtime_order_backend_mapper.txt",
        "rt_json": OUT / "realtime_order_backend_mapper.json",
        "rt_txt": OUT / "realtime_order_backend_mapper.txt",
        "mermaid": REPORTS / "realtime_order_backend_mapper.mermaid.md",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, default=list), encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, default=list), encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    paths["mermaid"].write_text(
        "# Mapper đơn RT từ backend toàn diện\n\n```mermaid\n" + report["mermaid"] + "\n```\n",
        encoding="utf-8",
    )
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Mapper đơn hàng realtime từ backend toàn diện")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=5000, help="Max orders per local file")
    args = ap.parse_args()
    report = build_report(ingest_limit=max(100, args.limit))
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
