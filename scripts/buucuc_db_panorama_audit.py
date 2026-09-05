#!/usr/bin/env python3
"""Rà soát chức năng trong DB bưu cục — nhìn cấu hình toàn cảnh.

Đọc SQLite buucuc_backend.db (+ materialize lại nếu thiếu), liệt kê:
  - schema / index / bảng chức năng
  - catalog backend + secret/config
  - độ phủ cột (fill rate)
  - ma trận backend×buucuc×kho×shop×NS
  - khoảng trống cấu hình (gap)
  - ống dẫn BC-* gắn với backend DB

Chỉ read-only SQL. Secrets-only cho probe trạng thái. Không dump login.
"""

from __future__ import annotations

import argparse
import json
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

# Chức năng logic gắn với cột / truy vấn DB
FUNCTION_CATALOG = [
    {
        "id": "F-BACKEND",
        "name": "Định danh backend bưu cục/3PL",
        "columns": ["backend"],
        "sql": "SELECT backend, COUNT(*) n FROM orders GROUP BY 1 ORDER BY n DESC",
        "config_keys": ["backends.id", "backends.oms", "backends.secret"],
    },
    {
        "id": "F-BUUCUC",
        "name": "Phân họ bưu cục / carrier family",
        "columns": ["buucuc", "carrier", "tracking_code"],
        "sql": "SELECT buucuc, COUNT(*) n FROM orders GROUP BY 1 ORDER BY n DESC",
        "config_keys": ["classify_buucuc()", "carrier", "tracking_code"],
    },
    {
        "id": "F-KHO",
        "name": "Kho xuất / warehouse",
        "columns": ["kho", "warehouse_id", "warehouse_display"],
        "sql": "SELECT kho, warehouse_display, COUNT(*) n FROM orders GROUP BY 1,2 ORDER BY n DESC",
        "config_keys": ["warehouse_info.custom_id", "warehouse_info.name"],
    },
    {
        "id": "F-SHOP",
        "name": "Shop / Account POS-3PL",
        "columns": ["shop_id", "shop_name", "page_id", "pancake_shop_id"],
        "sql": "SELECT shop_id, shop_name, COUNT(*) n FROM orders GROUP BY 1,2 ORDER BY n DESC",
        "config_keys": ["shop_id", "PANCAKE_SHOP_ID", "Account ID SPX"],
    },
    {
        "id": "F-NHANSU",
        "name": "Nhân sự OMS / Order Creator",
        "columns": ["staff_creator", "staff_account", "staff_seller", "staff_care"],
        "sql": (
            "SELECT staff_creator, staff_account, staff_seller, staff_care, COUNT(*) n "
            "FROM orders GROUP BY 1,2,3,4 ORDER BY n DESC LIMIT 30"
        ),
        "config_keys": ["assigning_seller", "assigning_care", "creator", "account"],
    },
    {
        "id": "F-TRACK",
        "name": "Mã vận đơn / tracking bưu cục",
        "columns": ["tracking_code", "carrier", "status"],
        "sql": (
            "SELECT backend, COUNT(*) n, "
            "SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code!='' THEN 1 ELSE 0 END) tracked "
            "FROM orders GROUP BY 1"
        ),
        "config_keys": ["shipments[].tracking_number", "Tracking No."],
    },
    {
        "id": "F-GEO",
        "name": "Địa bàn giao (tỉnh/huyện)",
        "columns": ["province", "district"],
        "sql": "SELECT province, COUNT(*) n FROM orders WHERE province IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 20",
        "config_keys": ["shipping_address", "Receiver Province"],
    },
    {
        "id": "F-PHONE",
        "name": "SĐT khách (PII class)",
        "columns": ["phone_class", "customer_phone"],
        "sql": "SELECT phone_class, COUNT(*) n FROM orders GROUP BY 1 ORDER BY n DESC",
        "config_keys": ["customer_phone", "decode_assist mask≠ciphertext"],
    },
    {
        "id": "F-SOURCE",
        "name": "Nguồn ingest / file",
        "columns": ["source", "channel", "platform", "file"],
        "sql": "SELECT channel, source, COUNT(*) n FROM orders GROUP BY 1,2 ORDER BY n DESC",
        "config_keys": ["quarantine/telegram/*", "oms ingest"],
    },
    {
        "id": "F-PIPE-CFG",
        "name": "Cấu hình pipe backend (secrets)",
        "columns": [],
        "sql": "SELECT id, role, oms, secret, query_hint FROM backends ORDER BY id",
        "config_keys": ["secrets/backend_pipes.env", "BUUCUC_BACKENDS"],
    },
]

# Ánh xạ pipe BC-* (từ mapper bưu cục) → backend DB
PIPE_OVERLAY = [
    {"pipe": "BC-01", "backend": "GHN", "priority": "P0", "need": "GHN_API_TOKEN"},
    {"pipe": "BC-02", "backend": "GHN", "priority": "P3", "need": "portal topology only"},
    {"pipe": "BC-03", "backend": "Tracking", "priority": "P1", "need": "tracking_code in DB"},
    {"pipe": "BC-04", "backend": "SPX-local", "priority": "P1", "need": "thanhcoong.xlsx"},
    {"pipe": "BC-05", "backend": "ViettelPost", "priority": "P0", "need": "VIETTELPOST_TOKEN"},
    {"pipe": "BC-06", "backend": "VNPost-local", "priority": "P2", "need": "vnpost file"},
    {"pipe": "KNS-BC-01", "backend": "Pancake", "priority": "P0", "need": "PANCAKE_* + warehouse"},
    {"pipe": "OMS-BUS", "backend": "OMS-pipe-bus", "priority": "P1", "need": "state json + sqlite"},
    {"pipe": "DIRECT", "backend": "direct_api", "priority": "P0", "need": "CSV columns kho/carrier"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_db() -> Path:
    if DB_PATH.is_file() and DB_PATH.stat().st_size > 0:
        return DB_PATH
    from buucuc_backend_db_query import materialize_db
    from oms_interconnect import ingest_local_orders

    records = ingest_local_orders(limit_per_file=5000)
    materialize_db(records, DB_PATH)
    return DB_PATH


def q(conn: sqlite3.Connection, sql: str, limit: int = 100) -> list[dict]:
    cur = conn.execute(sql)
    cols = [d[0] for d in (cur.description or [])]
    return [dict(zip(cols, r)) for r in cur.fetchmany(limit)]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


def column_fill(conn: sqlite3.Connection) -> list[dict]:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    total = int(scalar(conn, "SELECT COUNT(*) FROM orders") or 0) or 1
    out = []
    for c in cols:
        filled = int(
            scalar(
                conn,
                f"SELECT COUNT(*) FROM orders WHERE {c} IS NOT NULL AND TRIM(CAST({c} AS TEXT)) != ''",
            )
            or 0
        )
        out.append(
            {
                "column": c,
                "filled": filled,
                "total": total,
                "fill_pct": round(100.0 * filled / total, 1),
                "empty": total - filled,
            }
        )
    return sorted(out, key=lambda x: x["fill_pct"])


def schema_inventory(conn: sqlite3.Connection) -> dict:
    tables = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        info = [
            {"cid": r[0], "name": r[1], "type": r[2], "notnull": r[3], "pk": r[5]}
            for r in conn.execute(f"PRAGMA table_info({name})").fetchall()
        ]
        indexes = [
            {"name": r[1], "unique": r[2], "sql": r[4]}
            for r in conn.execute(
                "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name=?", (name,)
            ).fetchall()
        ]
        n = int(scalar(conn, f"SELECT COUNT(*) FROM {name}") or 0)
        tables[name] = {"columns": info, "indexes": indexes, "rows": n}
    return tables


def audit_functions(conn: sqlite3.Connection) -> list[dict]:
    results = []
    for fn in FUNCTION_CATALOG:
        try:
            rows = q(conn, fn["sql"], limit=40) if fn["sql"] else []
            ok = True
            err = None
        except Exception as e:  # noqa: BLE001
            rows, ok, err = [], False, str(e)
        # coverage: avg fill of related columns
        fills = []
        for c in fn["columns"]:
            total = int(scalar(conn, "SELECT COUNT(*) FROM orders") or 0) or 1
            filled = int(
                scalar(
                    conn,
                    f"SELECT COUNT(*) FROM orders WHERE {c} IS NOT NULL AND TRIM(CAST({c} AS TEXT)) != ''",
                )
                or 0
            )
            fills.append(round(100.0 * filled / total, 1))
        avg_fill = round(sum(fills) / len(fills), 1) if fills else None
        status = "ok"
        if not ok:
            status = "error"
        elif avg_fill is not None and avg_fill < 5:
            status = "empty"
        elif avg_fill is not None and avg_fill < 40:
            status = "partial"
        results.append(
            {
                "id": fn["id"],
                "name": fn["name"],
                "columns": fn["columns"],
                "config_keys": fn["config_keys"],
                "status": status,
                "fill_pct_avg": avg_fill,
                "sample_rows": rows[:12],
                "error": err,
            }
        )
    return results


def panorama_matrices(conn: sqlite3.Connection) -> dict:
    return {
        "backend_buucuc": q(
            conn,
            """
            SELECT backend, buucuc, COUNT(*) orders,
                   COUNT(DISTINCT kho) kho_n,
                   COUNT(DISTINCT shop_id) shop_n,
                   SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code!='' THEN 1 ELSE 0 END) tracked,
                   SUM(CASE WHEN staff_creator IS NOT NULL THEN 1 ELSE 0 END) with_creator
            FROM orders GROUP BY 1,2 ORDER BY orders DESC
            """,
        ),
        "kho_shop": q(
            conn,
            """
            SELECT kho, shop_id, shop_name, warehouse_display, COUNT(*) orders
            FROM orders GROUP BY 1,2,3,4 ORDER BY orders DESC LIMIT 30
            """,
        ),
        "config_ready": q(
            conn,
            """
            SELECT b.id AS backend, b.role, b.secret, b.query_hint,
                   COALESCE(o.orders, 0) AS orders_in_db,
                   COALESCE(o.tracked, 0) AS tracked,
                   COALESCE(o.creators, 0) AS with_creator
            FROM backends b
            LEFT JOIN (
              SELECT backend,
                     COUNT(*) orders,
                     SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code!='' THEN 1 ELSE 0 END) tracked,
                     SUM(CASE WHEN staff_creator IS NOT NULL THEN 1 ELSE 0 END) creators
              FROM orders GROUP BY backend
            ) o ON o.backend = b.id
            ORDER BY orders_in_db DESC, b.id
            """,
        ),
    }


def build_gaps(functions: list[dict], matrices: dict, backends_live: list[dict]) -> list[dict]:
    gaps = []
    for fn in functions:
        if fn["status"] in {"empty", "partial", "error"}:
            gaps.append(
                {
                    "kind": "function",
                    "id": fn["id"],
                    "severity": "P0" if fn["status"] == "empty" else "P1",
                    "detail": f"{fn['name']}: status={fn['status']} fill≈{fn['fill_pct_avg']}%",
                    "fix": f"Bổ sung dữ liệu cho cột {fn['columns']} / config {fn['config_keys'][:3]}",
                }
            )
    for row in matrices.get("config_ready") or []:
        if int(row.get("orders_in_db") or 0) == 0 and row.get("secret"):
            gaps.append(
                {
                    "kind": "backend_empty",
                    "id": row["backend"],
                    "severity": "P0",
                    "detail": f"Backend {row['backend']} có trong catalog nhưng 0 đơn trong DB · secret={row['secret']}",
                    "fix": f"Điền {row['secret']} owned + sync đơn vào OMS/SQLite",
                }
            )
        elif int(row.get("orders_in_db") or 0) == 0:
            gaps.append(
                {
                    "kind": "backend_empty",
                    "id": row["backend"],
                    "severity": "P2",
                    "detail": f"Backend {row['backend']}: 0 đơn (local/file) · {row.get('query_hint')}",
                    "fix": "Ingest file/carrier tương ứng hoặc chấp nhận topology-only",
                }
            )
    for b in backends_live:
        if b.get("status") in {"missing_cred", "auth_fail"}:
            gaps.append(
                {
                    "kind": "cred",
                    "id": b.get("backend"),
                    "severity": "P0",
                    "detail": b.get("feedback") or b.get("detail"),
                    "fix": f"secrets/backend_pipes.env ← {b.get('secret')}",
                }
            )
    # UNKNOWN / UNASSIGNED concentration
    for row in matrices.get("backend_buucuc") or []:
        buu = str(row.get("buucuc") or "")
        if buu.startswith("UNKNOWN") or buu == "UNASSIGNED_NO_SHIPMENT":
            gaps.append(
                {
                    "kind": "classification",
                    "id": f"{row.get('backend')}/{buu}",
                    "severity": "P0",
                    "detail": f"{row.get('orders')} đơn chưa gắn bưu cục thật (kho={row.get('kho_n')} shop={row.get('shop_n')})",
                    "fix": "Thêm warehouse+carrier+tracking vào nguồn / shipments[]",
                }
            )
    return gaps


def build_report(refresh_db: bool = False) -> dict:
    from realtime_icon_feedback_mapper import chant, feedback_line

    if refresh_db or not DB_PATH.is_file():
        from buucuc_backend_db_query import materialize_db, probe_backends
        from oms_interconnect import ingest_local_orders

        records = ingest_local_orders(limit_per_file=5000)
        materialize_db(records, DB_PATH)
        backends_live = probe_backends()
    else:
        ensure_db()
        try:
            from buucuc_backend_db_query import probe_backends

            backends_live = probe_backends()
        except Exception as e:  # noqa: BLE001
            backends_live = [{"backend": "?", "status": "error", "detail": str(e)}]

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    schema = schema_inventory(conn)
    fills = column_fill(conn)
    functions = audit_functions(conn)
    matrices = panorama_matrices(conn)
    meta = {r["key"]: r["value"] for r in q(conn, "SELECT key, value FROM meta")}
    backends_cfg = q(conn, "SELECT * FROM backends ORDER BY id")

    # pipe overlay status vs DB
    orders_by_backend = {
        r["backend"]: r for r in q(conn, "SELECT backend, COUNT(*) orders FROM orders GROUP BY 1")
    }
    live_by_id = {b.get("backend"): b for b in backends_live}
    pipe_panorama = []
    for p in PIPE_OVERLAY:
        live = live_by_id.get(p["backend"]) or {}
        odb = orders_by_backend.get(p["backend"]) or {"orders": 0}
        pipe_panorama.append(
            {
                **p,
                "orders_in_db": odb.get("orders") or 0,
                "backend_status": live.get("status") or "unknown",
                "icon_chant": live.get("icon_chant"),
                "ready": bool(odb.get("orders"))
                or live.get("status") in {"connected", "alive", "ok", "local_db"},
            }
        )

    gaps = build_gaps(functions, matrices, backends_live)
    conn.close()

    fn_ok = sum(1 for f in functions if f["status"] == "ok")
    fn_partial = sum(1 for f in functions if f["status"] == "partial")
    fn_empty = sum(1 for f in functions if f["status"] == "empty")
    icons = ["cpu", "network", "cube", "layers", "key", "monitor"]
    top_fb = feedback_line(
        icons,
        f"rà soát DB bưu cục · tables={len(schema)} · functions {fn_ok}ok/{fn_partial}partial/{fn_empty}empty · "
        f"orders={schema.get('orders', {}).get('rows')} · gaps={len(gaps)} · "
        f"pipes={len(pipe_panorama)}",
    )

    mermaid = _mermaid(matrices.get("config_ready") or [], functions)

    return {
        "ok": True,
        "query": "Rà soát chức năng trong db bưu cục để nhìn cấu hình toàn cảnh",
        "checked_at": utc_now(),
        "db_path": str(DB_PATH),
        "meta": meta,
        "schema": schema,
        "column_fill": fills,
        "functions": functions,
        "backends_config": backends_cfg,
        "backends_live": backends_live,
        "matrices": matrices,
        "pipe_panorama": pipe_panorama,
        "gaps": gaps,
        "summary": {
            "tables": list(schema.keys()),
            "orders": schema.get("orders", {}).get("rows"),
            "backends_catalog": len(backends_cfg),
            "functions_ok": fn_ok,
            "functions_partial": fn_partial,
            "functions_empty": fn_empty,
            "gaps": len(gaps),
            "pipes": len(pipe_panorama),
            "icon_chant": chant(icons),
            "feedback": top_fb,
        },
        "verdict": top_fb,
        "mermaid": mermaid,
        "next_actions": [
            "P0: điền secrets GHN/ViettelPost/Pancake → backend remote có dữ liệu ngoài local DB",
            "P0: giảm UNKNOWN/UNASSIGNED — bổ sung carrier+tracking+kho vào CSV/JSON",
            "P1: seller/care đang fill≈0% — refetch assigning_* Pancake",
            "Dùng panel 🔎 Rà soát DB BC hoặc scripts/buucuc_db_panorama_audit.py",
        ],
        "safety": {"readonly": True, "no_dump_login": True, "secrets_only_probe": True},
    }


def _mermaid(config_ready: list, functions: list) -> str:
    lines = [
        "flowchart TB",
        '  subgraph DB["SQLite buucuc_backend.db"]',
        "    ORD[orders]",
        "    BE[backends]",
        "    META[meta]",
        "  end",
        '  subgraph FN["Chức năng"]',
    ]
    for i, f in enumerate(functions):
        mark = {"ok": "OK", "partial": "PART", "empty": "EMPTY", "error": "ERR"}.get(f["status"], "?")
        lines.append(f'    F{i}["{f["id"]} {mark}"]')
    lines.append("  end")
    lines.append('  subgraph CFG["Backend config"]')
    for i, b in enumerate(config_ready[:8]):
        lines.append(f'    B{i}["{b.get("backend")}\\nn={b.get("orders_in_db")}"]')
    lines.append("  end")
    lines.append("  BE --> CFG")
    lines.append("  ORD --> FN")
    return "\n".join(lines)


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔎 RÀ SOÁT DB BƯU CỤC · CẤU HÌNH TOÀN CẢNH")
    L(f"Lúc: {report['checked_at']}")
    L(f"DB: {report['db_path']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    L(f"✨ {s.get('feedback')}")
    L(f"Chant: {s.get('icon_chant')}")
    L(f"meta: {report.get('meta')}")
    L("")
    L("=== Schema ===")
    for tname, t in report["schema"].items():
        cols = ", ".join(c["name"] for c in t["columns"])
        idxs = ", ".join(i["name"] for i in t["indexes"]) or "(none)"
        L(f"▶ {tname} · rows={t['rows']}")
        L(f"  cols: {cols}")
        L(f"  idx: {idxs}")
    L("")
    L("=== Fill rate cột (thấp → cao) ===")
    for c in report["column_fill"]:
        bar = "█" * int(c["fill_pct"] // 10) + "░" * (10 - int(c["fill_pct"] // 10))
        L(f"· {c['column']:<22} {bar} {c['fill_pct']:5.1f}%  filled={c['filled']}/{c['total']}")
    L("")
    L("=== Chức năng DB ===")
    for f in report["functions"]:
        mark = {"ok": "✅", "partial": "🟡", "empty": "⚠️", "error": "❌"}.get(f["status"], "·")
        L(f"{mark} {f['id']} · {f['name']} · status={f['status']} fill≈{f['fill_pct_avg']}")
        L(f"  cols={f['columns']} · config={f['config_keys'][:4]}")
        for row in (f.get("sample_rows") or [])[:3]:
            L(f"  · {row}")
    L("")
    L("=== Cấu hình backend (catalog × DB × live) ===")
    for row in report["matrices"]["config_ready"]:
        live = next((b for b in report["backends_live"] if b.get("backend") == row["backend"]), {})
        L(
            f"▶ {row['backend']}: db_orders={row['orders_in_db']} tracked={row['tracked']} "
            f"creator={row['with_creator']} · live={live.get('status')} · secret={row.get('secret')}"
        )
        L(f"  role={row.get('role')} · hint={str(row.get('query_hint') or '')[:80]}")
    L("")
    L("=== Pipe toàn cảnh (BC-* → backend DB) ===")
    for p in report["pipe_panorama"]:
        mark = "✅" if p.get("ready") else "⚠️"
        L(
            f"{mark} {p['pipe']} → {p['backend']} [{p['priority']}] "
            f"db={p['orders_in_db']} status={p['backend_status']} need={p['need']}"
        )
    L("")
    L("=== Ma trận backend × bưu cục ===")
    for r in report["matrices"]["backend_buucuc"][:12]:
        L(
            f"· {r['backend']} / {r['buucuc']}: n={r['orders']} kho={r['kho_n']} "
            f"shop={r['shop_n']} track={r['tracked']} creator={r['with_creator']}"
        )
    L("")
    L("=== Gap cấu hình ===")
    for g in report["gaps"][:18]:
        L(f"▶ [{g['severity']}] {g['kind']}:{g['id']}")
        L(f"  {g['detail']}")
        L(f"  → {g['fix']}")
    L("")
    L("Next:")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    # slim json: drop very wide sample duplication if needed — keep full for audit
    text = format_text(report)
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=list)
    paths = {
        "json": REPORTS / "buucuc_db_panorama_audit.json",
        "txt": REPORTS / "buucuc_db_panorama_audit.txt",
        "rt_json": OUT / "buucuc_db_panorama_audit.json",
        "rt_txt": OUT / "buucuc_db_panorama_audit.txt",
        "mermaid": REPORTS / "buucuc_db_panorama_audit.mermaid.md",
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    paths["mermaid"].write_text(
        "# Rà soát DB bưu cục · cấu hình toàn cảnh\n\n```mermaid\n"
        + report["mermaid"]
        + "\n```\n",
        encoding="utf-8",
    )
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Rà soát chức năng DB bưu cục — cấu hình toàn cảnh")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--refresh-db", action="store_true", help="Materialize lại SQLite trước khi audit")
    args = ap.parse_args()
    report = build_report(refresh_db=args.refresh_db)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
