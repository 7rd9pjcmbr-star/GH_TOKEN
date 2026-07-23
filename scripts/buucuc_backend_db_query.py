#!/usr/bin/env python3
"""Truy cập backend bưu cục + truy vấn DB.

- Probe pipe backend bưu cục/3PL (secrets-owned only)
- Materialize SQLite DB cục bộ từ OMS ingest (đơn × kho × bưu cục × shop × NS)
- Chạy truy vấn SQL read-only (stats + optional --sql)

Không dump login. Không ghi remote DB. Không crack.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "realtime"
DB_PATH = REPORTS / "buucuc_backend.db"
SECRETS = ROOT / "secrets"

# Backend bưu cục / 3PL catalog
BUUCUC_BACKENDS = [
    {
        "id": "GHN",
        "role": "bưu cục / hub GHN",
        "oms": "ghn",
        "secret": "GHN_API_TOKEN",
        "query_hint": "shiip order/detail + province (owned token)",
    },
    {
        "id": "ViettelPost",
        "role": "bưu cục ViettelPost",
        "oms": "viettelpost",
        "secret": "VIETTELPOST_TOKEN",
        "query_hint": "partner tracking/order (owned token)",
    },
    {
        "id": "SPX-local",
        "role": "3PL SPX (file DB)",
        "oms": "spx_local",
        "secret": None,
        "query_hint": "SELECT từ bảng orders WHERE backend='SPX-local'",
    },
    {
        "id": "VNPost-local",
        "role": "VNPost file đối soát",
        "oms": "vnpost_local",
        "secret": None,
        "query_hint": "file local — chưa có bảng đơn đầy đủ",
    },
    {
        "id": "Tracking",
        "role": "tracking.aship (mã VĐ bưu cục)",
        "oms": "tracking",
        "secret": None,
        "query_hint": "public track theo tracking_code",
    },
    {
        "id": "Pancake",
        "role": "POS → kho → tạo vận đơn bưu cục",
        "oms": "pancake",
        "secret": "PANCAKE_POS_API_KEY",
        "query_hint": "shops/{id}/orders + warehouse_info",
    },
    {
        "id": "direct_api",
        "role": "snapshot/inbox → OMS DB",
        "oms": "direct_api",
        "secret": None,
        "query_hint": "CSV/JSON inbox materialize",
    },
    {
        "id": "OMS-pipe-bus",
        "role": "registry pipe + state DB",
        "oms": "oms_bus",
        "secret": None,
        "query_hint": "secrets/*.state.json + SQLite buucuc_backend.db",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_buucuc(rec: dict) -> str:
    carrier = (rec.get("carrier") or "").strip()
    tracking = (rec.get("tracking_code") or "").strip()
    channel = (rec.get("channel") or "").lower()
    source = (rec.get("source") or "").strip()
    platform = (rec.get("platform") or "").lower()
    if carrier and carrier not in {"(none)", "(NONE)", "None"}:
        c_up = carrier.upper()
        if "SPX" in c_up:
            return "SPX"
        if "GHN" in c_up or "GIAOHANG" in c_up:
            return "GHN"
        if "VIETTEL" in c_up or c_up == "VTP":
            return "ViettelPost"
        if "VNPOST" in c_up:
            return "VNPost"
        return carrier[:40]
    if tracking.upper().startswith("SPX") or channel == "spx_local" or platform == "spx":
        return "SPX"
    if channel in {"pancake_payload", "json_flat"} and not tracking:
        return "UNASSIGNED_NO_SHIPMENT"
    if channel in {"inbox_csv", "direct_api"} or "dang_giao" in (rec.get("file") or "").lower():
        return f"UNKNOWN_DANG_GIAO/{source or channel}"[:80]
    if not tracking and not carrier:
        return "UNASSIGNED_NO_SHIPMENT"
    return "UNKNOWN"


def resolve_backend(rec: dict, buu: str) -> str:
    if buu == "SPX":
        return "SPX-local"
    if buu == "GHN":
        return "GHN"
    if buu == "ViettelPost":
        return "ViettelPost"
    if buu == "VNPost":
        return "VNPost-local"
    ch = (rec.get("channel") or "").lower()
    if ch == "pancake_payload":
        return "Pancake"
    if ch in {"inbox_csv", "direct_api"}:
        return "direct_api"
    if ch == "spx_local":
        return "SPX-local"
    return "OMS-pipe-bus"


def kho_key(rec: dict) -> str:
    return (
        (rec.get("warehouse_name") or "").strip()
        or (
            "(csv_no_warehouse)"
            if (rec.get("channel") or "") in {"inbox_csv", "direct_api"}
            else "(none)"
        )
    )


def is_readonly_sql(sql: str) -> bool:
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    s = re.sub(r"--.*?$", " ", s, flags=re.M).strip().lower()
    if not s:
        return False
    # cho phép nhiều statement chỉ nếu tất cả là SELECT/WITH/EXPLAIN/PRAGMA
    parts = [p.strip() for p in s.split(";") if p.strip()]
    if not parts:
        return False
    forbidden = re.compile(
        r"\b(insert|update|delete|drop|alter|attach|detach|create|replace|vacuum|reindex|grant|revoke)\b"
    )
    for p in parts:
        if forbidden.search(p):
            return False
        if not re.match(r"^(select|with|explain|pragma)\b", p):
            return False
    return True


def materialize_db(records: list[dict], path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=OFF")
    conn.executescript(
        """
        CREATE TABLE orders (
          oms_id TEXT,
          order_key TEXT,
          backend TEXT,
          buucuc TEXT,
          kho TEXT,
          warehouse_id TEXT,
          warehouse_display TEXT,
          shop_id TEXT,
          shop_name TEXT,
          page_id TEXT,
          pancake_shop_id TEXT,
          staff_creator TEXT,
          staff_account TEXT,
          staff_seller TEXT,
          staff_care TEXT,
          carrier TEXT,
          tracking_code TEXT,
          province TEXT,
          district TEXT,
          phone_class TEXT,
          customer_phone TEXT,
          status TEXT,
          source TEXT,
          channel TEXT,
          platform TEXT,
          file TEXT
        );
        CREATE INDEX idx_orders_backend ON orders(backend);
        CREATE INDEX idx_orders_buucuc ON orders(buucuc);
        CREATE INDEX idx_orders_kho ON orders(kho);
        CREATE INDEX idx_orders_shop ON orders(shop_id);
        CREATE TABLE backends (
          id TEXT PRIMARY KEY,
          role TEXT,
          oms TEXT,
          secret TEXT,
          query_hint TEXT
        );
        CREATE TABLE meta (
          key TEXT PRIMARY KEY,
          value TEXT
        );
        """
    )
    for b in BUUCUC_BACKENDS:
        conn.execute(
            "INSERT INTO backends(id, role, oms, secret, query_hint) VALUES (?,?,?,?,?)",
            (b["id"], b["role"], b["oms"], b["secret"], b["query_hint"]),
        )

    rows = []
    for rec in records:
        buu = classify_buucuc(rec)
        backend = resolve_backend(rec, buu)
        rows.append(
            (
                rec.get("oms_id"),
                rec.get("order_key"),
                backend,
                buu,
                kho_key(rec),
                str(rec.get("warehouse_id") or "") or None,
                rec.get("warehouse_display_name"),
                str(rec.get("shop_id") or "") or None,
                rec.get("shop_name"),
                rec.get("page_id"),
                str(rec.get("pancake_shop_id") or "") or None,
                str(rec.get("creator") or "") or None,
                str(rec.get("account") or "") or None,
                str(rec.get("assigning_seller") or "") or None,
                str(rec.get("assigning_care") or "") or None,
                rec.get("carrier"),
                rec.get("tracking_code"),
                rec.get("province"),
                rec.get("district"),
                rec.get("phone_class"),
                (rec.get("customer_phone") or "")[:40] or None,
                rec.get("status"),
                rec.get("source"),
                rec.get("channel"),
                rec.get("platform"),
                rec.get("file"),
            )
        )
    conn.executemany(
        """
        INSERT INTO orders VALUES (
          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        rows,
    )
    conn.execute(
        "INSERT INTO meta(key,value) VALUES ('materialized_at', ?), ('records', ?)",
        (utc_now(), str(len(rows))),
    )
    conn.commit()
    info = {
        "path": str(path),
        "records": len(rows),
        "tables": ["orders", "backends", "meta"],
    }
    conn.close()
    return info


def run_query(conn: sqlite3.Connection, sql: str, limit: int = 50) -> dict:
    if not is_readonly_sql(sql):
        return {"ok": False, "error": "Chỉ cho phép SQL read-only (SELECT/WITH/PRAGMA/EXPLAIN)", "sql": sql}
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        raw = cur.fetchmany(limit + 1)
        truncated = len(raw) > limit
        rows = raw[:limit]
        return {
            "ok": True,
            "sql": sql.strip(),
            "columns": cols,
            "rows": [dict(zip(cols, r)) for r in rows],
            "row_count_returned": len(rows),
            "truncated": truncated,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "sql": sql}


DEFAULT_QUERIES: list[tuple[str, str]] = [
    (
        "by_backend",
        """
        SELECT backend, COUNT(*) AS orders,
               COUNT(DISTINCT kho) AS kho_n,
               COUNT(DISTINCT shop_id) AS shop_n,
               SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != '' THEN 1 ELSE 0 END) AS with_tracking
        FROM orders GROUP BY backend ORDER BY orders DESC
        """,
    ),
    (
        "by_buucuc",
        """
        SELECT buucuc, backend, COUNT(*) AS orders,
               COUNT(DISTINCT kho) AS kho_n,
               COUNT(DISTINCT shop_id) AS shop_n,
               COUNT(DISTINCT staff_creator) AS creator_n
        FROM orders GROUP BY buucuc, backend ORDER BY orders DESC
        """,
    ),
    (
        "kho_buucuc_shop",
        """
        SELECT kho, buucuc, shop_id, shop_name, COUNT(*) AS orders,
               COUNT(DISTINCT staff_creator) AS creators
        FROM orders
        GROUP BY kho, buucuc, shop_id, shop_name
        ORDER BY orders DESC
        LIMIT 40
        """,
    ),
    (
        "staff_by_backend",
        """
        SELECT backend, buucuc,
               staff_creator, staff_account, staff_seller, staff_care,
               COUNT(*) AS orders
        FROM orders
        WHERE COALESCE(staff_creator, staff_account, staff_seller, staff_care) IS NOT NULL
        GROUP BY backend, buucuc, staff_creator, staff_account, staff_seller, staff_care
        ORDER BY orders DESC
        LIMIT 40
        """,
    ),
    (
        "phone_by_buucuc_backend",
        """
        SELECT backend, buucuc, phone_class, COUNT(*) AS n
        FROM orders
        GROUP BY backend, buucuc, phone_class
        ORDER BY n DESC
        """,
    ),
    (
        "backends_catalog",
        "SELECT id, role, oms, secret, query_hint FROM backends ORDER BY id",
    ),
]


def probe_backends() -> list[dict]:
    from oms_interconnect import interconnect, load_env
    from realtime_icon_feedback_mapper import CHANNEL_ICON, chant, feedback_line, map_channel

    env = load_env()
    oms = interconnect(env, ingest=False)
    ch_by_id = {c.get("id"): c for c in oms.get("channels") or []}
    ch_by_be = {c.get("backend"): c for c in oms.get("channels") or []}
    results = []
    for b in BUUCUC_BACKENDS:
        ch = ch_by_id.get(b["oms"]) or ch_by_be.get(b["id"]) or {}
        status = (ch.get("status") or ("local_db" if not b["secret"] else "unknown")).lower()
        if not ch and b["secret"] is None:
            status = "local_db"
        mapped = map_channel(
            {
                "id": b["oms"],
                "backend": b["id"],
                "status": status if status != "local_db" else "connected",
                "detail": ch.get("detail") or b["query_hint"],
            }
        ) if ch or b["secret"] is None else {
            "status": "missing_cred",
            "icons": [CHANNEL_ICON.get(b["oms"], "network"), "key", "lock"],
            "icon_chant": chant([CHANNEL_ICON.get(b["oms"], "network"), "key", "lock"]),
            "feedback": feedback_line(
                [CHANNEL_ICON.get(b["oms"], "network"), "key", "lock"],
                f"{b['id']}: missing_cred · thiếu {b['secret']}",
            ),
        }
        if not ch and b["secret"] is None:
            icons = [CHANNEL_ICON.get(b["oms"], "code"), "monitor"]
            mapped = {
                "status": "local_db",
                "icons": icons,
                "icon_chant": chant(icons),
                "feedback": feedback_line(icons, f"{b['id']}: local_db · {b['query_hint']}"),
            }
        results.append(
            {
                "backend": b["id"],
                "role": b["role"],
                "oms": b["oms"],
                "secret": b["secret"],
                "status": mapped.get("status") or status,
                "http": ch.get("http"),
                "detail": ch.get("detail") or b["query_hint"],
                "icons": mapped.get("icons"),
                "icon_chant": mapped.get("icon_chant"),
                "feedback": mapped.get("feedback"),
                "db_access": "sqlite:orders WHERE backend=?" if b["id"] != "VNPost-local" else "file_only",
            }
        )
    return results


def build_report(ingest_limit: int = 5000, extra_sql: list[str] | None = None) -> dict:
    from oms_interconnect import ingest_local_orders
    from realtime_icon_feedback_mapper import chant, feedback_line

    records = ingest_local_orders(limit_per_file=max(100, ingest_limit))
    db_info = materialize_db(records, DB_PATH)
    backends = probe_backends()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    query_results = {}
    for name, sql in DEFAULT_QUERIES:
        query_results[name] = run_query(conn, sql, limit=80)

    custom = []
    for sql in extra_sql or []:
        custom.append(run_query(conn, sql, limit=100))

    # quick scalar stats
    stats = {}
    for key, sql in (
        ("orders", "SELECT COUNT(*) FROM orders"),
        ("backends_in_db", "SELECT COUNT(DISTINCT backend) FROM orders"),
        ("buucuc_in_db", "SELECT COUNT(DISTINCT buucuc) FROM orders"),
        ("kho_in_db", "SELECT COUNT(DISTINCT kho) FROM orders"),
        ("shops_in_db", "SELECT COUNT(DISTINCT shop_id) FROM orders"),
        ("with_tracking", "SELECT COUNT(*) FROM orders WHERE tracking_code IS NOT NULL AND tracking_code != ''"),
        ("with_staff", "SELECT COUNT(*) FROM orders WHERE staff_creator IS NOT NULL OR staff_seller IS NOT NULL"),
    ):
        stats[key] = conn.execute(sql).fetchone()[0]
    conn.close()

    connected = [b for b in backends if b["status"] in {"connected", "alive", "ok", "local_db"}]
    blocked = [b for b in backends if b["status"] in {"missing_cred", "auth_fail", "error", "stale"}]
    icons = ["network", "cpu", "cube", "key", "monitor"]
    top_fb = feedback_line(
        icons,
        f"backend bưu cục DB · sqlite={db_info['path']} · rows={stats['orders']} · "
        f"backends_live={len(connected)}/{len(backends)} · blocked={len(blocked)} · "
        f"buucuc={stats['buucuc_in_db']} kho={stats['kho_in_db']} shop={stats['shops_in_db']}",
    )

    return {
        "ok": True,
        "query": "Truy cập backend bưu cục truy vấn db",
        "checked_at": utc_now(),
        "db": db_info,
        "stats": stats,
        "backends": backends,
        "queries": query_results,
        "custom_queries": custom,
        "summary": {
            "sqlite": db_info["path"],
            "orders": stats["orders"],
            "backends_catalog": len(backends),
            "backends_reachable": len(connected),
            "backends_blocked": len(blocked),
            "icon_chant": chant(icons),
            "feedback": top_fb,
        },
        "verdict": top_fb,
        "how_to_query": [
            f"sqlite3 {DB_PATH} \"SELECT backend, buucuc, COUNT(*) FROM orders GROUP BY 1,2;\"",
            "python3 scripts/buucuc_backend_db_query.py --sql \"SELECT * FROM orders WHERE buucuc='SPX' LIMIT 10\"",
            "Panel Telegram: 🗄 Backend BC·DB",
        ],
        "next_actions": [
            "Điền GHN_API_TOKEN / VIETTELPOST_TOKEN / PANCAKE_* owned để probe remote ngoài local DB",
            "CSV Đang giao thiếu kho/carrier → backend=direct_api, buucuc=UNKNOWN_* trong DB",
            "SPX-local đã query được từ SQLite (96 đơn Smart Homes)",
            "Không dùng dump Acc_all/Ghn để login backend bưu cục",
        ],
        "safety": {
            "readonly_sql_only": True,
            "no_dump_login": True,
            "secrets_only_remote": True,
            "local_sqlite": str(DB_PATH),
        },
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🗄 BACKEND BƯU CỤC · TRUY VẤN DB")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    L(f"✨ {s.get('feedback')}")
    L(f"DB: {report['db'].get('path')} · rows={report['stats'].get('orders')}")
    L("")
    L("=== Backend bưu cục (access) ===")
    for b in report["backends"]:
        mark = "✅" if b["status"] in {"connected", "alive", "ok", "local_db"} else "⚠️"
        L(f"{mark} {b['backend']} · {b['status']} · {b['role']}")
        L(f"   {b.get('icon_chant')}")
        L(f"   {b.get('feedback')}")
        L(f"   db_access={b.get('db_access')} · detail={str(b.get('detail') or '')[:90]}")
    L("")
    L("=== SQL: by_backend ===")
    for r in (report["queries"].get("by_backend") or {}).get("rows") or []:
        L(f"· {r.get('backend')}: orders={r.get('orders')} kho={r.get('kho_n')} shop={r.get('shop_n')} track={r.get('with_tracking')}")
    L("")
    L("=== SQL: by_buucuc ===")
    for r in ((report["queries"].get("by_buucuc") or {}).get("rows") or [])[:12]:
        L(
            f"· {r.get('buucuc')} @ {r.get('backend')}: n={r.get('orders')} "
            f"kho={r.get('kho_n')} shop={r.get('shop_n')} creators={r.get('creator_n')}"
        )
    L("")
    L("=== SQL: kho × buucuc × shop (top) ===")
    for r in ((report["queries"].get("kho_buucuc_shop") or {}).get("rows") or [])[:12]:
        L(
            f"· {r.get('kho')} × {r.get('buucuc')} · shop={r.get('shop_name')}[{r.get('shop_id')}] "
            f"n={r.get('orders')} creators={r.get('creators')}"
        )
    L("")
    L("=== SQL: nhân sự theo backend ===")
    for r in ((report["queries"].get("staff_by_backend") or {}).get("rows") or [])[:10]:
        L(
            f"· {r.get('backend')}/{r.get('buucuc')}: creator={r.get('staff_creator')} "
            f"account={r.get('staff_account')} seller={r.get('staff_seller')} n={r.get('orders')}"
        )
    if report.get("custom_queries"):
        L("")
        L("=== Custom SQL ===")
        for q in report["custom_queries"]:
            if not q.get("ok"):
                L(f"· ERR {q.get('error')}")
                continue
            L(f"· {q.get('sql')[:100]} → {q.get('row_count_returned')} rows")
            for row in (q.get("rows") or [])[:5]:
                L(f"  {row}")
    L("")
    L("How to query:")
    for h in report["how_to_query"]:
        L(f"· {h}")
    L("")
    L("Next:")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    text = format_text(report)
    # strip huge phone fields already truncated; still keep report lean for custom
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=list)
    paths = {
        "json": REPORTS / "buucuc_backend_db_query.json",
        "txt": REPORTS / "buucuc_backend_db_query.txt",
        "rt_json": OUT / "buucuc_backend_db_query.json",
        "rt_txt": OUT / "buucuc_backend_db_query.txt",
        "db": DB_PATH,
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Truy cập backend bưu cục + truy vấn SQLite DB")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--sql", action="append", help="Thêm câu SQL read-only (lặp được)")
    ap.add_argument("--db-only", action="store_true", help="Chỉ materialize DB, không probe OMS")
    args = ap.parse_args()

    if args.db_only:
        from oms_interconnect import ingest_local_orders

        records = ingest_local_orders(limit_per_file=max(100, args.limit))
        info = materialize_db(records, DB_PATH)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    report = build_report(ingest_limit=max(100, args.limit), extra_sql=args.sql)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
