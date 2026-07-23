#!/usr/bin/env python3
"""Mở rộng đơn theo thời gian thực — gom sync + timeline + DB.

Chạy realtime cycle (Pancake/inbox/SPX/VNPost/GHN/TPOS), mở rộng đơn OMS,
xếp theo thời gian (create/deliver/seen), cập nhật SQLite RT + báo cáo.

Secrets-only remote. Không dump login.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "realtime"
DB_PATH = REPORTS / "realtime_orders_expand.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # already ISO-ish
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return s[:32]


def classify_buucuc(rec: dict) -> str:
    carrier = (rec.get("carrier") or "").strip()
    tracking = (rec.get("tracking_code") or "").strip()
    channel = (rec.get("channel") or "").lower()
    platform = (rec.get("platform") or "").lower()
    source = (rec.get("source") or "").strip()
    if carrier and "SPX" in carrier.upper():
        return "SPX"
    if tracking.upper().startswith("SPX") or channel == "spx_local" or platform == "spx":
        return "SPX"
    if carrier:
        c = carrier.upper()
        if "GHN" in c:
            return "GHN"
        if "VIETTEL" in c:
            return "ViettelPost"
        return carrier[:40]
    if channel in {"pancake_payload", "json_flat"} and not tracking:
        return "UNASSIGNED_NO_SHIPMENT"
    if channel in {"inbox_csv", "direct_api"}:
        return f"UNKNOWN_DANG_GIAO/{source or channel}"[:80]
    return "UNKNOWN"


def resolve_backend(rec: dict, buu: str) -> str:
    if rec.get("_backend"):
        return str(rec["_backend"])
    if buu == "SPX":
        return "SPX-local"
    ch = (rec.get("channel") or "").lower()
    if ch == "pancake_payload":
        return "Pancake"
    if ch in {"inbox_csv", "direct_api"}:
        return "direct_api"
    if ch == "spx_local":
        return "SPX-local"
    return "OMS-pipe-bus"


def materialize_rt_db(rows: list[dict]) -> dict:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(
        """
        CREATE TABLE orders_rt (
          oms_id TEXT,
          order_key TEXT,
          backend TEXT,
          buucuc TEXT,
          kho TEXT,
          shop_id TEXT,
          shop_name TEXT,
          staff_creator TEXT,
          carrier TEXT,
          tracking_code TEXT,
          province TEXT,
          district TEXT,
          phone_class TEXT,
          status TEXT,
          source TEXT,
          channel TEXT,
          realtime_new INTEGER,
          created_at TEXT,
          picked_at TEXT,
          delivered_at TEXT,
          seen_at TEXT,
          time_bucket TEXT,
          file TEXT
        );
        CREATE INDEX idx_rt_backend ON orders_rt(backend);
        CREATE INDEX idx_rt_bucket ON orders_rt(time_bucket);
        CREATE INDEX idx_rt_created ON orders_rt(created_at);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    for r in rows:
        conn.execute(
            """
            INSERT INTO orders_rt VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                r.get("oms_id"),
                r.get("order_key"),
                r.get("backend"),
                r.get("buucuc"),
                r.get("kho"),
                r.get("shop_id"),
                r.get("shop_name"),
                r.get("staff_creator"),
                r.get("carrier"),
                r.get("tracking_code"),
                r.get("province"),
                r.get("district"),
                r.get("phone_class"),
                r.get("status"),
                r.get("source"),
                r.get("channel"),
                1 if r.get("realtime_new") else 0,
                r.get("created_at"),
                r.get("picked_at"),
                r.get("delivered_at"),
                r.get("seen_at"),
                r.get("time_bucket"),
                r.get("file"),
            ),
        )
    conn.execute(
        "INSERT INTO meta(key,value) VALUES ('materialized_at',?), ('records',?)",
        (utc_now(), str(len(rows))),
    )
    conn.commit()
    conn.close()
    return {"path": str(DB_PATH), "records": len(rows)}


def time_bucket(iso: str | None) -> str:
    if not iso:
        return "(no_time)"
    # YYYY-MM-DD or hour
    m = re.match(r"(\d{4}-\d{2}-\d{2})", iso)
    if m:
        return m.group(1)
    return "(no_time)"


def expand_record(rec: dict, *, realtime_new: bool = False, seen_at: str | None = None) -> dict:
    buu = classify_buucuc(rec)
    backend = resolve_backend(rec, buu)
    created = parse_ts(rec.get("created_at") or rec.get("order_created_at") or rec.get("Create Time"))
    delivered = parse_ts(rec.get("delivered_at") or rec.get("Delivered Time"))
    picked = parse_ts(rec.get("picked_at") or rec.get("Actual Pickup/Drop Off Time"))
    seen = seen_at or (utc_now() if realtime_new else None)
    anchor = created or delivered or seen
    return {
        "oms_id": rec.get("oms_id") or rec.get("order_key") or rec.get("id"),
        "order_key": rec.get("order_key") or rec.get("id") or rec.get("remote_id"),
        "backend": backend,
        "buucuc": buu,
        "kho": rec.get("warehouse_name") or rec.get("kho"),
        "shop_id": str(rec.get("shop_id") or "") or None,
        "shop_name": rec.get("shop_name"),
        "staff_creator": rec.get("creator") or rec.get("staff_creator"),
        "carrier": rec.get("carrier"),
        "tracking_code": rec.get("tracking_code"),
        "province": rec.get("province"),
        "district": rec.get("district"),
        "phone_class": rec.get("phone_class"),
        "status": rec.get("status") or rec.get("status_normalized") or rec.get("status_raw"),
        "source": rec.get("source"),
        "channel": rec.get("channel"),
        "realtime_new": realtime_new,
        "created_at": created,
        "picked_at": picked,
        "delivered_at": delivered,
        "seen_at": seen,
        "time_bucket": time_bucket(anchor),
        "file": rec.get("file") or rec.get("_file"),
    }


def build_report(*, limit: int = 50, ingest_limit: int = 5000) -> dict:
    from oms_interconnect import ingest_local_orders, load_env
    from realtime_icon_feedback_mapper import chant, feedback_line
    from realtime_order_sync import run_cycle

    env = load_env()
    cycle = run_cycle(env, limit=max(1, limit), notify=False, notify_new_only=False)

    local = ingest_local_orders(limit_per_file=max(100, ingest_limit))
    expanded: list[dict] = []
    for rec in local:
        # SPX times already may be absent on normalize — keep None
        expanded.append(expand_record(rec, realtime_new=False))

    rt_new = []
    for o in cycle.get("all_new_orders") or []:
        oo = dict(o)
        # map SPX-like fields
        if not oo.get("tracking_code") and oo.get("id") and str(oo.get("_backend")) == "SPX-local":
            oo["tracking_code"] = oo.get("id")
        if not oo.get("warehouse_name"):
            oo["warehouse_name"] = oo.get("shop_name") or oo.get("Sender Name")
        row = expand_record(oo, realtime_new=True, seen_at=cycle.get("checked_at"))
        expanded.append(row)
        rt_new.append(row)

    # Enrich SPX rows with times from xlsx via second pass on new SPX only
    # (local ingest SPX lacks times — re-read from cycle new or from xlsx map)
    try:
        from oms_interconnect import normalize_from_thanhcoong, read_xlsx_rows

        xlsx = ROOT / "quarantine" / "telegram" / "thanhcoong.xlsx"
        if xlsx.is_file():
            by_track: dict[str, dict] = {}
            for r in read_xlsx_rows(xlsx):
                rec = normalize_from_thanhcoong(r)
                if not rec or not rec.get("tracking_code"):
                    continue
                by_track[str(rec["tracking_code"])] = {
                    "created_at": parse_ts(r.get("Create Time")),
                    "picked_at": parse_ts(r.get("Actual Pickup/Drop Off Time")),
                    "delivered_at": parse_ts(r.get("Delivered Time")),
                }
            for row in expanded:
                if row.get("tracking_code") in by_track:
                    t = by_track[row["tracking_code"]]
                    row["created_at"] = row.get("created_at") or t.get("created_at")
                    row["picked_at"] = row.get("picked_at") or t.get("picked_at")
                    row["delivered_at"] = row.get("delivered_at") or t.get("delivered_at")
                    row["time_bucket"] = time_bucket(
                        row.get("created_at") or row.get("delivered_at") or row.get("seen_at")
                    )
    except Exception:  # noqa: BLE001
        pass

    # Deduplicate: prefer realtime_new + richer timestamps
    dedup: dict[str, dict] = {}
    for row in expanded:
        key = (
            str(row.get("tracking_code") or "")
            or str(row.get("oms_id") or "")
            or str(row.get("order_key") or "")
        )
        if not key:
            key = f"anon:{len(dedup)}"
        prev = dedup.get(key)
        if prev is None:
            dedup[key] = row
            continue
        # merge flags/times
        merged = dict(prev)
        if row.get("realtime_new"):
            merged["realtime_new"] = True
            merged["seen_at"] = row.get("seen_at") or merged.get("seen_at")
        for fld in ("created_at", "picked_at", "delivered_at", "shop_name", "staff_creator", "province", "district"):
            if not merged.get(fld) and row.get(fld):
                merged[fld] = row[fld]
        merged["time_bucket"] = time_bucket(
            merged.get("created_at") or merged.get("delivered_at") or merged.get("seen_at")
        )
        dedup[key] = merged
    expanded = list(dedup.values())
    rt_new = [r for r in expanded if r.get("realtime_new")]

    db_info = materialize_rt_db(expanded)

    # aggregations
    by_backend: dict[str, dict] = {}
    by_bucket: Counter = Counter()
    by_buucuc: Counter = Counter()
    timeline: dict[str, dict] = {}

    for row in expanded:
        b = row["backend"]
        bucket = by_backend.setdefault(
            b,
            {
                "backend": b,
                "orders": 0,
                "realtime_new": 0,
                "with_created_at": 0,
                "with_delivered_at": 0,
                "with_tracking": 0,
                "phone": Counter(),
                "status": Counter(),
                "buckets": Counter(),
                "samples": [],
            },
        )
        bucket["orders"] += 1
        if row.get("realtime_new"):
            bucket["realtime_new"] += 1
        if row.get("created_at"):
            bucket["with_created_at"] += 1
        if row.get("delivered_at"):
            bucket["with_delivered_at"] += 1
        if row.get("tracking_code"):
            bucket["with_tracking"] += 1
        bucket["phone"][row.get("phone_class") or "?"] += 1
        bucket["status"][str(row.get("status") or "?")[:40]] += 1
        bucket["buckets"][row.get("time_bucket") or "(no_time)"] += 1
        if len(bucket["samples"]) < 5:
            bucket["samples"].append(
                {
                    "order_key": row.get("order_key"),
                    "tracking": row.get("tracking_code"),
                    "created_at": row.get("created_at"),
                    "delivered_at": row.get("delivered_at"),
                    "realtime_new": row.get("realtime_new"),
                    "status": row.get("status"),
                    "province": row.get("province"),
                }
            )

        by_bucket[row.get("time_bucket") or "(no_time)"] += 1
        by_buucuc[row.get("buucuc") or "?"] += 1

        day = row.get("time_bucket") or "(no_time)"
        tl = timeline.setdefault(
            day,
            {"day": day, "orders": 0, "backends": Counter(), "buucuc": Counter(), "realtime_new": 0},
        )
        tl["orders"] += 1
        tl["backends"][b] += 1
        tl["buucuc"][row.get("buucuc") or "?"] += 1
        if row.get("realtime_new"):
            tl["realtime_new"] += 1

    backends_out = []
    for b, bucket in sorted(by_backend.items(), key=lambda x: -x[1]["orders"]):
        backends_out.append(
            {
                "backend": b,
                "orders": bucket["orders"],
                "realtime_new": bucket["realtime_new"],
                "with_created_at": bucket["with_created_at"],
                "with_delivered_at": bucket["with_delivered_at"],
                "with_tracking": bucket["with_tracking"],
                "phone": dict(bucket["phone"]),
                "status_top": bucket["status"].most_common(8),
                "buckets_top": bucket["buckets"].most_common(10),
                "samples": bucket["samples"],
            }
        )

    timeline_out = []
    for day, tl in sorted(timeline.items(), key=lambda x: x[0]):
        timeline_out.append(
            {
                "day": day,
                "orders": tl["orders"],
                "realtime_new": tl["realtime_new"],
                "backends": tl["backends"].most_common(),
                "buucuc": tl["buucuc"].most_common(8),
            }
        )

    icons = ["cpu", "monitor", "cube", "network", "hash", "spark"]
    new_n = int(cycle.get("new_count") or 0)
    top_fb = feedback_line(
        icons,
        f"mở rộng đơn realtime · total={len(expanded)} · cycle_new={new_n} · "
        f"backends={len(backends_out)} · days={len(timeline_out)} · "
        f"db={db_info['path']}",
    )

    return {
        "ok": True,
        "query": "Tiếp tục mở rộng đơn theo thời gian thực",
        "checked_at": utc_now(),
        "cycle": {
            "checked_at": cycle.get("checked_at"),
            "new_count": new_n,
            "blocked": cycle.get("blocked"),
            "backends": cycle.get("backends"),
        },
        "db": db_info,
        "summary": {
            "expanded_orders": len(expanded),
            "realtime_new": len(rt_new),
            "backends": len(backends_out),
            "time_buckets": len(timeline_out),
            "with_created_at": sum(1 for r in expanded if r.get("created_at")),
            "with_delivered_at": sum(1 for r in expanded if r.get("delivered_at")),
            "icon_chant": chant(icons),
            "feedback": top_fb,
        },
        "by_backend": backends_out,
        "by_buucuc": by_buucuc.most_common(),
        "timeline": timeline_out,
        "realtime_new_samples": rt_new[:20],
        "verdict": top_fb,
        "next_actions": [
            "Chạy loop: python3 scripts/realtime_order_sync.py --loop --interval 60",
            "Mở rộng lại: python3 scripts/realtime_order_expand.py",
            "SQL: sqlite3 reports/telegram-classify/realtime_orders_expand.db "
            "\"SELECT time_bucket, backend, COUNT(*) FROM orders_rt GROUP BY 1,2\"",
            "Điền Pancake/GHN token để cycle_new > 0 từ API (SPX file đã vào realtime)",
        ],
        "safety": {"secrets_only": True, "no_dump_login": True},
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("⏱ MỞ RỘNG ĐƠN · THỜI GIAN THỰC")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    L(f"✨ {s.get('feedback')}")
    L(f"DB: {report['db'].get('path')} · rows={report['db'].get('records')}")
    L(
        f"expanded={s['expanded_orders']} realtime_new={s['realtime_new']} "
        f"created_at={s['with_created_at']} delivered_at={s['with_delivered_at']}"
    )
    L("")
    L("=== Realtime cycle ===")
    cy = report["cycle"]
    L(f"· at={cy.get('checked_at')} new={cy.get('new_count')} blocked={cy.get('blocked')}")
    for b in cy.get("backends") or []:
        L(
            f"  - {b.get('backend')}: {b.get('status')} "
            f"new={len(b.get('new_orders') or [])} · {str(b.get('detail') or '')[:90]}"
        )
    L("")
    L("=== Đơn theo backend (mở rộng) ===")
    for b in report["by_backend"]:
        L(
            f"▶ {b['backend']}: n={b['orders']} rt_new={b['realtime_new']} "
            f"created={b['with_created_at']} delivered={b['with_delivered_at']} track={b['with_tracking']}"
        )
        L(f"  phone={b['phone']} status={b['status_top'][:3]}")
        L(f"  buckets={b['buckets_top'][:5]}")
        for sm in b["samples"][:2]:
            L(f"  · {sm}")
    L("")
    L("=== Timeline theo ngày ===")
    for t in report["timeline"][:20]:
        L(
            f"· {t['day']}: n={t['orders']} rt_new={t['realtime_new']} "
            f"backends={t['backends'][:4]} buucuc={t['buucuc'][:3]}"
        )
    L("")
    L("=== Bưu cục ===")
    for buu, n in report["by_buucuc"][:12]:
        L(f"· {buu}: {n}")
    if report.get("realtime_new_samples"):
        L("")
        L("=== Đơn realtime mới (mẫu) ===")
        for r in report["realtime_new_samples"][:12]:
            L(
                f"· [{r.get('backend')}] {r.get('tracking_code') or r.get('order_key')} "
                f"created={r.get('created_at')} delivered={r.get('delivered_at')} "
                f"status={r.get('status')} · {r.get('province')}"
            )
    L("")
    L("Next:")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    text = format_text(report)
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=list)
    paths = {
        "json": REPORTS / "realtime_order_expand.json",
        "txt": REPORTS / "realtime_order_expand.txt",
        "rt_json": OUT / "realtime_order_expand.json",
        "rt_txt": OUT / "realtime_order_expand.txt",
        "db": DB_PATH,
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Mở rộng đơn theo thời gian thực")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=50, help="Pancake page limit")
    ap.add_argument("--ingest-limit", type=int, default=5000)
    args = ap.parse_args()
    report = build_report(limit=max(1, args.limit), ingest_limit=max(100, args.ingest_limit))
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
