#!/usr/bin/env python3
"""Truy vấn ngược: vân tay / số nội bộ / tracking → đơn · kho · bưu cục · icon.

Đọc kho_buucuc_pipe.db (+ mirror buucuc_backend.db).
Không dump login. Secrets-only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "realtime"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
BUUCUC_DB = REPORTS / "buucuc_backend.db"

ORDER_COLS = (
    "van_tay",
    "so_noi_bo",
    "oms_id",
    "order_key",
    "backend",
    "buucuc",
    "kho",
    "warehouse_id",
    "warehouse_display",
    "shop_id",
    "shop_name",
    "staff_creator",
    "carrier",
    "tracking_code",
    "province",
    "district",
    "phone_class",
    "status",
    "source",
    "channel",
    "file",
    "realtime_new",
    "icon_chant",
    "icon_feedback",
    "created_at",
    "synced_at",
    "event_at",
    "piped_at",
    "pipe_source",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_pipe() -> sqlite3.Connection | None:
    if not PIPE_DB.is_file():
        return None
    conn = sqlite3.connect(str(PIPE_DB))
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    return {k: r[k] for k in r.keys()}


def reverse_by_van_tay(conn: sqlite3.Connection, vt: str) -> dict:
    order = row_to_dict(
        conn.execute("SELECT * FROM orders WHERE van_tay = ? LIMIT 1", (vt,)).fetchone()
    )
    fp = row_to_dict(
        conn.execute("SELECT * FROM fingerprints WHERE van_tay = ? LIMIT 1", (vt,)).fetchone()
    )
    events = [
        dict(r)
        for r in conn.execute(
            "SELECT at, event, so_noi_bo, detail FROM pipe_events WHERE van_tay = ? ORDER BY id DESC LIMIT 8",
            (vt,),
        )
    ]
    return {
        "query_type": "van_tay",
        "query": vt,
        "hit": bool(order or fp),
        "order": order,
        "fingerprint": fp,
        "pipe_events": events,
        "path": _path_from_order(order or fp),
    }


def reverse_by_so_noi_bo(conn: sqlite3.Connection, so: str, limit: int = 20) -> dict:
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM orders WHERE so_noi_bo = ? OR order_key = ? OR oms_id = ? LIMIT ?",
            (so, so, so, limit),
        )
    ]
    # fuzzy contains for partial số nội bộ
    if not rows and len(so) >= 4:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM orders
                WHERE so_noi_bo LIKE ? OR order_key LIKE ? OR oms_id LIKE ?
                LIMIT ?
                """,
                (f"%{so}%", f"%{so}%", f"%{so}%", limit),
            )
        ]
    return {
        "query_type": "so_noi_bo",
        "query": so,
        "hit": bool(rows),
        "count": len(rows),
        "orders": rows,
        "paths": [_path_from_order(o) for o in rows[:8]],
    }


def reverse_by_tracking(conn: sqlite3.Connection, track: str, limit: int = 20) -> dict:
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM orders WHERE tracking_code = ? LIMIT ?",
            (track, limit),
        )
    ]
    if not rows and len(track) >= 6:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM orders WHERE tracking_code LIKE ? LIMIT ?",
                (f"%{track}%", limit),
            )
        ]
    return {
        "query_type": "tracking",
        "query": track,
        "hit": bool(rows),
        "count": len(rows),
        "orders": rows,
        "paths": [_path_from_order(o) for o in rows[:8]],
    }


def reverse_by_kho(conn: sqlite3.Connection, kho: str, limit: int = 30) -> dict:
    node = row_to_dict(
        conn.execute("SELECT * FROM kho_nodes WHERE kho_name = ? LIMIT 1", (kho,)).fetchone()
    )
    if not node:
        node = row_to_dict(
            conn.execute(
                "SELECT * FROM kho_nodes WHERE kho_name LIKE ? LIMIT 1", (f"%{kho}%",)
            ).fetchone()
        )
    name = (node or {}).get("kho_name") or kho
    matrix = [
        dict(r)
        for r in conn.execute(
            """
            SELECT buucuc, backend, COUNT(*) AS orders, COUNT(DISTINCT van_tay) AS fps
            FROM orders WHERE kho LIKE ?
            GROUP BY buucuc, backend ORDER BY orders DESC LIMIT 20
            """,
            (f"%{name}%" if not node else name,),
        )
    ]
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, buucuc, backend, tracking_code, status, icon_chant
            FROM orders WHERE kho LIKE ? ORDER BY piped_at DESC LIMIT ?
            """,
            (f"%{name}%", limit),
        )
    ]
    return {
        "query_type": "kho",
        "query": kho,
        "hit": bool(node or samples),
        "kho_node": node,
        "buucuc_matrix": matrix,
        "sample_orders": samples,
        "path": f"kho:{name} → buucuc×{len(matrix)} → orders",
    }


def reverse_by_buucuc(conn: sqlite3.Connection, buu: str, limit: int = 30) -> dict:
    nodes = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM buucuc_nodes WHERE buucuc = ? OR buucuc LIKE ?",
            (buu, f"%{buu}%"),
        )
    ]
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, kho, buucuc, backend, tracking_code, status, icon_chant, shop_name
            FROM orders WHERE buucuc = ? OR buucuc LIKE ?
            ORDER BY piped_at DESC LIMIT ?
            """,
            (buu, f"%{buu}%", limit),
        )
    ]
    by_kho = [
        dict(r)
        for r in conn.execute(
            """
            SELECT kho, COUNT(*) AS orders, COUNT(DISTINCT van_tay) AS fps
            FROM orders WHERE buucuc = ? OR buucuc LIKE ?
            GROUP BY kho ORDER BY orders DESC
            """,
            (buu, f"%{buu}%"),
        )
    ]
    return {
        "query_type": "buucuc",
        "query": buu,
        "hit": bool(nodes or samples),
        "buucuc_nodes": nodes,
        "by_kho": by_kho,
        "sample_orders": samples,
        "path": f"buucuc:{buu} → kho×{len(by_kho)} → orders",
    }


def reverse_by_icon_chant(conn: sqlite3.Connection, fragment: str, limit: int = 20) -> dict:
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, kho, buucuc, backend, icon_chant, status
            FROM orders WHERE icon_chant LIKE ? OR icon_feedback LIKE ?
            LIMIT ?
            """,
            (f"%{fragment}%", f"%{fragment}%", limit),
        )
    ]
    return {
        "query_type": "icon",
        "query": fragment,
        "hit": bool(rows),
        "count": len(rows),
        "orders": rows,
    }


def _path_from_order(o: dict | None) -> str:
    if not o:
        return "(no hit)"
    return (
        f"van_tay={o.get('van_tay')} ← so_noi_bo={o.get('so_noi_bo')} ← "
        f"track={o.get('tracking_code') or '∅'} ← "
        f"{o.get('backend')} ← kho:{o.get('kho')} ← buucuc:{o.get('buucuc')} ← "
        f"icon:{o.get('icon_chant') or '∅'}"
    )


def auto_detect_queries(conn: sqlite3.Connection) -> list[dict]:
    """Demo truy vấn ngược từ mẫu thật trong DB (SPX + 1 kho + 1 van_tay)."""
    demos: list[dict] = []
    spx = conn.execute(
        "SELECT van_tay, so_noi_bo, tracking_code FROM orders WHERE buucuc = 'SPX' LIMIT 1"
    ).fetchone()
    if spx:
        demos.append(reverse_by_van_tay(conn, spx["van_tay"]))
        demos.append(reverse_by_so_noi_bo(conn, spx["so_noi_bo"]))
        if spx["tracking_code"]:
            demos.append(reverse_by_tracking(conn, spx["tracking_code"]))
        demos.append(reverse_by_buucuc(conn, "SPX", limit=8))
        demos.append(reverse_by_kho(conn, "Smart Homes", limit=8))

    pancake = conn.execute(
        "SELECT van_tay, so_noi_bo FROM orders WHERE backend = 'Pancake' LIMIT 1"
    ).fetchone()
    if pancake:
        demos.append(reverse_by_van_tay(conn, pancake["van_tay"]))
        demos.append(reverse_by_kho(conn, "Kho HCM", limit=8))

    demos.append(reverse_by_icon_chant(conn, "Dấu Băm Đơn", limit=8))
    return demos


def ensure_pipe_or_build() -> sqlite3.Connection:
    conn = open_pipe()
    if conn is not None:
        n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        if n > 0:
            return conn
        conn.close()
    # materialize via pipe if empty
    from order_pipe_kho_buucuc_db import build_report, write_outputs

    write_outputs(build_report(run_cycle=False))
    conn = open_pipe()
    if conn is None:
        raise RuntimeError(f"Không mở được pipe DB: {PIPE_DB}")
    return conn


def build_report(
    *,
    van_tay: str | None = None,
    so_noi_bo: str | None = None,
    tracking: str | None = None,
    kho: str | None = None,
    buucuc: str | None = None,
    icon: str | None = None,
    q: str | None = None,
) -> dict:
    from realtime_icon_feedback_mapper import chant, feedback_line, receive_fingerprint

    conn = ensure_pipe_or_build()
    results: list[dict] = []

    # free-form q: auto-route
    if q:
        qq = q.strip()
        if len(qq) == 16 and all(c in "0123456789abcdef" for c in qq.lower()):
            results.append(reverse_by_van_tay(conn, qq.lower()))
        elif qq.upper().startswith("SPX") or qq.upper().startswith("GHN"):
            results.append(reverse_by_tracking(conn, qq))
        elif "kho" in qq.lower() or "smart" in qq.lower() or "hcm" in qq.lower():
            results.append(reverse_by_kho(conn, qq))
        elif qq.upper() in {"SPX", "GHN", "VIETTELPOST", "VNPOST"} or "DANG_GIAO" in qq.upper() or "UNASSIGNED" in qq.upper():
            results.append(reverse_by_buucuc(conn, qq))
        else:
            # try so_noi_bo then tracking then van_tay fragment
            r_so = reverse_by_so_noi_bo(conn, qq)
            if r_so["hit"]:
                results.append(r_so)
            else:
                r_tr = reverse_by_tracking(conn, qq)
                if r_tr["hit"]:
                    results.append(r_tr)
                else:
                    results.append(r_so)

    if van_tay:
        results.append(reverse_by_van_tay(conn, van_tay.strip().lower()))
    if so_noi_bo:
        results.append(reverse_by_so_noi_bo(conn, so_noi_bo.strip()))
    if tracking:
        results.append(reverse_by_tracking(conn, tracking.strip()))
    if kho:
        results.append(reverse_by_kho(conn, kho.strip()))
    if buucuc:
        results.append(reverse_by_buucuc(conn, buucuc.strip()))
    if icon:
        results.append(reverse_by_icon_chant(conn, icon.strip()))

    demo_mode = not any([van_tay, so_noi_bo, tracking, kho, buucuc, icon, q])
    if demo_mode:
        results = auto_detect_queries(conn)

    hits = sum(1 for r in results if r.get("hit"))
    total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    total_fp = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]

    # icon phản hồi cho mỗi hit van_tay
    icon_echo: list[dict] = []
    for r in results:
        o = r.get("order")
        if o and o.get("van_tay"):
            icon_echo.append(
                receive_fingerprint(
                    van_tay=o["van_tay"],
                    so_noi_bo=o.get("so_noi_bo"),
                    backend=o.get("backend"),
                    kho=o.get("kho"),
                    buucuc=o.get("buucuc"),
                    status=o.get("status"),
                    tracking=o.get("tracking_code"),
                )
            )
        for oo in (r.get("orders") or r.get("sample_orders") or [])[:3]:
            if oo.get("van_tay"):
                icon_echo.append(
                    receive_fingerprint(
                        van_tay=oo["van_tay"],
                        so_noi_bo=oo.get("so_noi_bo"),
                        backend=oo.get("backend"),
                        kho=oo.get("kho"),
                        buucuc=oo.get("buucuc"),
                        status=oo.get("status"),
                        tracking=oo.get("tracking_code"),
                    )
                )

    icons = ["hash", "compass", "cube", "network", "text"]
    top_fb = feedback_line(
        icons,
        f"truy vấn ngược · queries={len(results)} hit={hits} · "
        f"db_orders={total_orders} van_tay={total_fp} · demo={demo_mode}",
    )

    # index stats for reverse capability
    index_stats = {
        "by_backend": [
            {"backend": r[0], "n": r[1]}
            for r in conn.execute(
                "SELECT backend, COUNT(*) AS cnt FROM orders GROUP BY backend ORDER BY cnt DESC"
            )
        ],
        "by_kho": [
            {"kho": r[0], "n": r[1]}
            for r in conn.execute(
                "SELECT kho, COUNT(*) AS cnt FROM orders GROUP BY kho ORDER BY cnt DESC"
            )
        ],
        "by_buucuc": [
            {"buucuc": r[0], "n": r[1]}
            for r in conn.execute(
                "SELECT buucuc, COUNT(*) AS cnt FROM orders GROUP BY buucuc ORDER BY cnt DESC LIMIT 12"
            )
        ],
    }
    conn.close()

    return {
        "ok": True,
        "query": "Truy vấn ngược van_tay/so_noi_bo/tracking → kho×bưu cục×icon",
        "checked_at": utc_now(),
        "demo_mode": demo_mode,
        "db": {"pipe_db": str(PIPE_DB), "orders": total_orders, "fingerprints": total_fp},
        "summary": {
            "queries": len(results),
            "hits": hits,
            "icon_echo": len(icon_echo),
            "icon_chant": chant(icons),
            "feedback": top_fb,
        },
        "results": results,
        "icon_echo": icon_echo[:16],
        "index_stats": index_stats,
        "verdict": top_fb,
        "next_actions": [
            "python3 scripts/order_pipe_reverse_query.py --so SAPO-1990252568_664140",
            "python3 scripts/order_pipe_reverse_query.py --tracking SPXVN067431106264",
            "python3 scripts/order_pipe_reverse_query.py --van-tay 790ee41984baea83",
            "python3 scripts/order_pipe_reverse_query.py --kho 'Kho HCM'",
            "python3 scripts/order_pipe_reverse_query.py --buucuc SPX",
            "python3 scripts/order_pipe_reverse_query.py --q <van_tay|so|track|kho|buucuc>",
        ],
        "safety": {"secrets_only": True, "no_dump_login": True},
    }


def _short_order(o: dict) -> str:
    return (
        f"[{o.get('van_tay')}] so={o.get('so_noi_bo')} track={o.get('tracking_code') or '∅'} "
        f"· {o.get('backend')}/{o.get('kho')}/{o.get('buucuc')} · {o.get('status')}"
    )


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔎 TRUY VẤN NGƯỢC · VÂN TAY / SỐ NỘI BỘ → KHO × BƯU CỤC")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    db = report["db"]
    L(f"✨ {s.get('feedback')}")
    L(f"Chant: {s.get('icon_chant')}")
    L(f"queries={s['queries']} hits={s['hits']} icon_echo={s['icon_echo']} · demo={report.get('demo_mode')}")
    L(f"DB: {db['pipe_db']} · orders={db['orders']} van_tay={db['fingerprints']}")
    L("")
    L("=== Index (để truy ngược) ===")
    for b in (report.get("index_stats") or {}).get("by_backend") or []:
        L(f"· backend {b['backend']}: {b['n']}")
    for k in (report.get("index_stats") or {}).get("by_kho") or []:
        L(f"· kho {k['kho']}: {k['n']}")
    for b in ((report.get("index_stats") or {}).get("by_buucuc") or [])[:8]:
        L(f"· buucuc {b['buucuc']}: {b['n']}")
    L("")
    L("=== Kết quả truy vấn ngược ===")
    for r in report.get("results") or []:
        mark = "✅" if r.get("hit") else "○"
        L(f"{mark} [{r.get('query_type')}] q={r.get('query')}")
        if r.get("path"):
            L(f"  path: {r['path']}")
        if r.get("order"):
            L(f"  {_short_order(r['order'])}")
            if r["order"].get("icon_chant"):
                L(f"  icon: {r['order']['icon_chant']}")
        for p in (r.get("paths") or [])[:4]:
            L(f"  ← {p}")
        for o in (r.get("orders") or r.get("sample_orders") or [])[:5]:
            L(f"  · {_short_order(o)}")
        if r.get("buucuc_matrix"):
            for m in r["buucuc_matrix"][:5]:
                L(f"  · buu {m.get('buucuc')} [{m.get('backend')}]: n={m.get('orders')} fp={m.get('fps')}")
        if r.get("by_kho"):
            for m in r["by_kho"][:5]:
                L(f"  · kho {m.get('kho')}: n={m.get('orders')} fp={m.get('fps')}")
        if r.get("pipe_events"):
            for e in r["pipe_events"][:3]:
                L(f"  evt {e.get('at')}: {e.get('event')} · {e.get('detail')}")
    if report.get("icon_echo"):
        L("")
        L("=== Icon nhận lại (echo) ===")
        for f in report["icon_echo"][:10]:
            L(f"· {f.get('icon_chant')} — van_tay={f.get('van_tay')} so={f.get('so_noi_bo')}")
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
        "json": REPORTS / "order_pipe_reverse_query.json",
        "txt": REPORTS / "order_pipe_reverse_query.txt",
        "rt_json": OUT / "order_pipe_reverse_query.json",
        "rt_txt": OUT / "order_pipe_reverse_query.txt",
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Truy vấn ngược pipe kho+bưu cục")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--van-tay", dest="van_tay")
    ap.add_argument("--so", dest="so_noi_bo", help="Số nội bộ / order_key")
    ap.add_argument("--tracking")
    ap.add_argument("--kho")
    ap.add_argument("--buucuc")
    ap.add_argument("--icon", help="Fragment icon chant")
    ap.add_argument("--q", help="Auto-detect query (van_tay|so|track|kho|buucuc)")
    args = ap.parse_args()
    report = build_report(
        van_tay=args.van_tay,
        so_noi_bo=args.so_noi_bo,
        tracking=args.tracking,
        kho=args.kho,
        buucuc=args.buucuc,
        icon=args.icon,
        q=args.q,
    )
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
