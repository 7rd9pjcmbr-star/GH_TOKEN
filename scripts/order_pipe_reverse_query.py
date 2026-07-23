#!/usr/bin/env python3
"""Truy vấn ngược đào sâu: toàn cảnh dòng chảy bưu cục → địa chỉ nhận.

Lookup van_tay / so_noi_bo / tracking / kho / buucuc / tỉnh·huyện·địa chỉ
→ lộ trình: kho → backend → bưu cục → mã VĐ → trạng thái → người nhận → địa chỉ.

Đọc kho_buucuc_pipe.db. Secrets-only. Không dump login.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "realtime"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"


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


def mask_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw)
    if len(s) < 6:
        return s
    return s[:4] + "****" + s[-2:]


def build_flow_panorama(o: dict | None) -> dict | None:
    """Toàn cảnh dòng chảy từ bưu cục/kho đến địa chỉ nhận."""
    if not o:
        return None
    geo_parts = [o.get("address_detail"), o.get("ward"), o.get("district"), o.get("province")]
    geo_line = ", ".join(str(x) for x in geo_parts if x) or o.get("full_address")
    sender_geo = ", ".join(
        str(x)
        for x in (
            o.get("sender_address"),
            o.get("sender_ward"),
            o.get("sender_district"),
            o.get("sender_province"),
        )
        if x
    ) or None

    stages = [
        {
            "step": 1,
            "id": "kho",
            "label": "Kho xuất",
            "value": o.get("kho") or o.get("warehouse_display") or "(none)",
            "meta": {
                "shop": o.get("shop_name"),
                "shop_id": o.get("shop_id"),
                "staff": o.get("staff_creator"),
            },
        },
        {
            "step": 2,
            "id": "backend",
            "label": "Backend / pipe",
            "value": o.get("backend") or "?",
            "meta": {
                "channel": o.get("channel"),
                "source": o.get("source"),
                "carrier": o.get("carrier"),
            },
        },
        {
            "step": 3,
            "id": "buucuc",
            "label": "Bưu cục / 3PL",
            "value": o.get("buucuc") or "?",
            "meta": {"tracking": o.get("tracking_code"), "so_noi_bo": o.get("so_noi_bo")},
        },
        {
            "step": 4,
            "id": "van_don",
            "label": "Vận đơn",
            "value": o.get("tracking_code") or "(chưa có mã VĐ)",
            "meta": {
                "status": o.get("status"),
                "created_at": o.get("created_at"),
                "picked_at": o.get("picked_at"),
                "delivered_at": o.get("delivered_at"),
                "cod": o.get("cod_amount"),
            },
        },
        {
            "step": 5,
            "id": "nguoi_nhan",
            "label": "Người nhận",
            "value": o.get("receiver_name") or "(ẩn/thiếu)",
            "meta": {
                "phone_class": o.get("phone_class"),
                "phone_masked": mask_phone(o.get("receiver_phone")),
            },
        },
        {
            "step": 6,
            "id": "dia_chi",
            "label": "Địa chỉ nhận",
            "value": geo_line or "(chưa có địa chỉ)",
            "meta": {
                "detail": o.get("address_detail"),
                "ward": o.get("ward"),
                "district": o.get("district"),
                "province": o.get("province"),
                "postal_code": o.get("postal_code"),
                "full_address": o.get("full_address"),
            },
        },
    ]
    if sender_geo:
        stages.insert(
            1,
            {
                "step": 0,
                "id": "gui",
                "label": "Điểm gửi (sender)",
                "value": sender_geo,
                "meta": {
                    "sender_province": o.get("sender_province"),
                    "sender_district": o.get("sender_district"),
                    "sender_ward": o.get("sender_ward"),
                    "sender_address": o.get("sender_address"),
                },
            },
        )
        for i, st in enumerate(stages, 1):
            st["step"] = i

    flow_text = o.get("flow_path") or " → ".join(
        f"{s['label']}:{s['value']}"
        for s in stages
        if s["id"] in {"kho", "buucuc", "van_don", "dia_chi"}
    )
    completeness = {
        "has_tracking": bool(o.get("tracking_code")),
        "has_province": bool(o.get("province")),
        "has_district": bool(o.get("district")),
        "has_ward": bool(o.get("ward")),
        "has_address_detail": bool(o.get("address_detail") or o.get("full_address")),
        "has_receiver": bool(o.get("receiver_name")),
        "has_delivered": bool(o.get("delivered_at")),
    }
    score = sum(1 for v in completeness.values() if v)
    return {
        "van_tay": o.get("van_tay"),
        "so_noi_bo": o.get("so_noi_bo"),
        "stages": stages,
        "flow_text": flow_text,
        "completeness": completeness,
        "completeness_score": f"{score}/{len(completeness)}",
        "timeline": {
            "created_at": o.get("created_at"),
            "picked_at": o.get("picked_at"),
            "delivered_at": o.get("delivered_at"),
            "synced_at": o.get("synced_at"),
            "piped_at": o.get("piped_at"),
        },
        "icon_chant": o.get("icon_chant"),
    }


def _attach_flow(result: dict) -> dict:
    if result.get("order"):
        result["flow"] = build_flow_panorama(result["order"])
        result["path"] = (result.get("flow") or {}).get("flow_text") or result.get("path")
    flows = []
    for o in result.get("orders") or []:
        flows.append(build_flow_panorama(o))
    for o in result.get("sample_orders") or []:
        flows.append(build_flow_panorama(o))
    if flows:
        result["flows"] = [f for f in flows if f][:12]
        if not result.get("flow") and result["flows"]:
            result["flow"] = result["flows"][0]
    return result


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
    return _attach_flow(
        {
            "query_type": "van_tay",
            "query": vt,
            "hit": bool(order or fp),
            "order": order,
            "fingerprint": fp,
            "pipe_events": events,
            "path": None,
        }
    )


def reverse_by_so_noi_bo(conn: sqlite3.Connection, so: str, limit: int = 20) -> dict:
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM orders WHERE so_noi_bo = ? OR order_key = ? OR oms_id = ? LIMIT ?",
            (so, so, so, limit),
        )
    ]
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
    return _attach_flow(
        {
            "query_type": "so_noi_bo",
            "query": so,
            "hit": bool(rows),
            "count": len(rows),
            "orders": rows,
        }
    )


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
    return _attach_flow(
        {
            "query_type": "tracking",
            "query": track,
            "hit": bool(rows),
            "count": len(rows),
            "orders": rows,
        }
    )


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
    like = f"%{name}%"
    matrix = [
        dict(r)
        for r in conn.execute(
            """
            SELECT buucuc, backend, COUNT(*) AS orders, COUNT(DISTINCT van_tay) AS fps,
                   COUNT(DISTINCT province) AS provinces
            FROM orders WHERE kho LIKE ?
            GROUP BY buucuc, backend ORDER BY orders DESC LIMIT 20
            """,
            (like,),
        )
    ]
    dest = [
        dict(r)
        for r in conn.execute(
            """
            SELECT province, COUNT(*) AS orders,
                   COUNT(DISTINCT district) AS districts,
                   COUNT(DISTINCT buucuc) AS buucuc_n
            FROM orders WHERE kho LIKE ? AND province IS NOT NULL AND province != ''
            GROUP BY province ORDER BY orders DESC LIMIT 20
            """,
            (like,),
        )
    ]
    samples = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM orders WHERE kho LIKE ? ORDER BY piped_at DESC LIMIT ?",
            (like, limit),
        )
    ]
    return _attach_flow(
        {
            "query_type": "kho",
            "query": kho,
            "hit": bool(node or samples),
            "kho_node": node,
            "buucuc_matrix": matrix,
            "destination_provinces": dest,
            "sample_orders": samples,
            "path": f"kho:{name} → buucuc×{len(matrix)} → tỉnh×{len(dest)}",
        }
    )


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
            SELECT * FROM orders WHERE buucuc = ? OR buucuc LIKE ?
            ORDER BY piped_at DESC LIMIT ?
            """,
            (buu, f"%{buu}%", limit),
        )
    ]
    by_kho = [
        dict(r)
        for r in conn.execute(
            """
            SELECT kho, COUNT(*) AS orders, COUNT(DISTINCT van_tay) AS fps,
                   COUNT(DISTINCT province) AS provinces
            FROM orders WHERE buucuc = ? OR buucuc LIKE ?
            GROUP BY kho ORDER BY orders DESC
            """,
            (buu, f"%{buu}%"),
        )
    ]
    by_province = [
        dict(r)
        for r in conn.execute(
            """
            SELECT province, district, COUNT(*) AS orders
            FROM orders
            WHERE (buucuc = ? OR buucuc LIKE ?)
              AND province IS NOT NULL AND province != ''
            GROUP BY province, district ORDER BY orders DESC LIMIT 30
            """,
            (buu, f"%{buu}%"),
        )
    ]
    return _attach_flow(
        {
            "query_type": "buucuc",
            "query": buu,
            "hit": bool(nodes or samples),
            "buucuc_nodes": nodes,
            "by_kho": by_kho,
            "by_province_district": by_province,
            "sample_orders": samples,
            "path": f"buucuc:{buu} → kho×{len(by_kho)} → địa bàn×{len(by_province)} → orders",
        }
    )


def reverse_by_province(conn: sqlite3.Connection, province: str, limit: int = 30) -> dict:
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM orders
            WHERE province = ? OR province LIKE ? OR full_address LIKE ? OR address_detail LIKE ?
            ORDER BY piped_at DESC LIMIT ?
            """,
            (province, f"%{province}%", f"%{province}%", f"%{province}%", limit),
        )
    ]
    by_buucuc = [
        dict(r)
        for r in conn.execute(
            """
            SELECT buucuc, backend, COUNT(*) AS orders, COUNT(DISTINCT kho) AS kho_n
            FROM orders
            WHERE province = ? OR province LIKE ?
            GROUP BY buucuc, backend ORDER BY orders DESC
            """,
            (province, f"%{province}%"),
        )
    ]
    by_district = [
        dict(r)
        for r in conn.execute(
            """
            SELECT district, ward, COUNT(*) AS orders
            FROM orders WHERE province = ? OR province LIKE ?
            GROUP BY district, ward ORDER BY orders DESC LIMIT 25
            """,
            (province, f"%{province}%"),
        )
    ]
    return _attach_flow(
        {
            "query_type": "province",
            "query": province,
            "hit": bool(rows),
            "count": len(rows),
            "orders": rows,
            "by_buucuc": by_buucuc,
            "by_district": by_district,
            "path": f"tỉnh:{province} ← bưu cục×{len(by_buucuc)} ← huyện×{len(by_district)}",
        }
    )


def reverse_by_address(conn: sqlite3.Connection, fragment: str, limit: int = 20) -> dict:
    like = f"%{fragment}%"
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM orders
            WHERE full_address LIKE ? OR address_detail LIKE ?
               OR ward LIKE ? OR district LIKE ? OR receiver_name LIKE ?
            LIMIT ?
            """,
            (like, like, like, like, like, limit),
        )
    ]
    return _attach_flow(
        {
            "query_type": "address",
            "query": fragment,
            "hit": bool(rows),
            "count": len(rows),
            "orders": rows,
        }
    )


def reverse_by_icon_chant(conn: sqlite3.Connection, fragment: str, limit: int = 20) -> dict:
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM orders WHERE icon_chant LIKE ? OR icon_feedback LIKE ?
            LIMIT ?
            """,
            (f"%{fragment}%", f"%{fragment}%", limit),
        )
    ]
    return _attach_flow(
        {
            "query_type": "icon",
            "query": fragment,
            "hit": bool(rows),
            "count": len(rows),
            "orders": rows,
        }
    )


def auto_detect_queries(conn: sqlite3.Connection) -> list[dict]:
    demos: list[dict] = []
    spx = conn.execute(
        "SELECT van_tay, so_noi_bo, tracking_code, province FROM orders WHERE buucuc = 'SPX' LIMIT 1"
    ).fetchone()
    if spx:
        demos.append(reverse_by_van_tay(conn, spx["van_tay"]))
        demos.append(reverse_by_so_noi_bo(conn, spx["so_noi_bo"]))
        if spx["tracking_code"]:
            demos.append(reverse_by_tracking(conn, spx["tracking_code"]))
        demos.append(reverse_by_buucuc(conn, "SPX", limit=6))
        demos.append(reverse_by_kho(conn, "Smart Homes", limit=6))
        if spx["province"]:
            demos.append(reverse_by_province(conn, spx["province"], limit=8))

    pancake = conn.execute(
        "SELECT van_tay, province FROM orders WHERE backend = 'Pancake' AND province IS NOT NULL LIMIT 1"
    ).fetchone()
    if pancake:
        demos.append(reverse_by_van_tay(conn, pancake["van_tay"]))
        demos.append(reverse_by_kho(conn, "Kho HCM", limit=6))
        if pancake["province"]:
            demos.append(reverse_by_province(conn, pancake["province"], limit=8))

    addr = conn.execute(
        "SELECT address_detail FROM orders WHERE address_detail IS NOT NULL AND address_detail != '' LIMIT 1"
    ).fetchone()
    if addr and addr["address_detail"]:
        frag = str(addr["address_detail"])[:18]
        demos.append(reverse_by_address(conn, frag, limit=5))

    demos.append(reverse_by_icon_chant(conn, "Dấu Băm Đơn", limit=5))
    return demos


def ensure_pipe_or_build() -> sqlite3.Connection:
    conn = open_pipe()
    if conn is not None:
        n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        with_addr = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE buucuc='SPX' AND (full_address IS NOT NULL OR address_detail IS NOT NULL)"
        ).fetchone()[0]
        if n > 0 and with_addr > 0:
            return conn
        conn.close()
    from order_pipe_kho_buucuc_db import build_report, write_outputs

    write_outputs(build_report(run_cycle=False))
    conn = open_pipe()
    if conn is None:
        raise RuntimeError(f"Không mở được pipe DB: {PIPE_DB}")
    return conn


def build_flow_matrix(conn: sqlite3.Connection) -> dict:
    """Ma trận toàn cảnh: bưu cục → tỉnh nhận."""
    buu_to_prov = [
        dict(r)
        for r in conn.execute(
            """
            SELECT buucuc, province, COUNT(*) AS orders
            FROM orders
            WHERE province IS NOT NULL AND province != ''
            GROUP BY buucuc, province
            ORDER BY orders DESC
            LIMIT 40
            """
        )
    ]
    kho_to_prov = [
        dict(r)
        for r in conn.execute(
            """
            SELECT kho, province, COUNT(*) AS orders
            FROM orders
            WHERE province IS NOT NULL AND province != ''
            GROUP BY kho, province
            ORDER BY orders DESC
            LIMIT 40
            """
        )
    ]
    row = conn.execute(
        """
        SELECT
          SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != '' THEN 1 ELSE 0 END),
          SUM(CASE WHEN province IS NOT NULL AND province != '' THEN 1 ELSE 0 END),
          SUM(CASE WHEN district IS NOT NULL AND district != '' THEN 1 ELSE 0 END),
          SUM(CASE WHEN ward IS NOT NULL AND ward != '' THEN 1 ELSE 0 END),
          SUM(CASE WHEN address_detail IS NOT NULL AND address_detail != '' THEN 1 ELSE 0 END),
          SUM(CASE WHEN full_address IS NOT NULL AND full_address != '' THEN 1 ELSE 0 END),
          SUM(CASE WHEN receiver_name IS NOT NULL AND receiver_name != '' THEN 1 ELSE 0 END),
          SUM(CASE WHEN delivered_at IS NOT NULL AND delivered_at != '' THEN 1 ELSE 0 END),
          COUNT(*)
        FROM orders
        """
    ).fetchone()
    fill = {
        "with_tracking": row[0],
        "with_province": row[1],
        "with_district": row[2],
        "with_ward": row[3],
        "with_detail": row[4],
        "with_full_address": row[5],
        "with_receiver": row[6],
        "with_delivered": row[7],
        "total": row[8],
    }
    return {
        "buucuc_to_province": buu_to_prov,
        "kho_to_province": kho_to_prov,
        "address_fill": fill,
    }


def build_report(
    *,
    van_tay: str | None = None,
    so_noi_bo: str | None = None,
    tracking: str | None = None,
    kho: str | None = None,
    buucuc: str | None = None,
    province: str | None = None,
    address: str | None = None,
    icon: str | None = None,
    q: str | None = None,
) -> dict:
    from realtime_icon_feedback_mapper import chant, feedback_line, receive_fingerprint

    conn = ensure_pipe_or_build()
    results: list[dict] = []

    if q:
        qq = q.strip()
        if len(qq) == 16 and all(c in "0123456789abcdef" for c in qq.lower()):
            results.append(reverse_by_van_tay(conn, qq.lower()))
        elif qq.upper().startswith(("SPX", "GHN", "VTP", "VN")):
            results.append(reverse_by_tracking(conn, qq))
        elif re.search(r"kho|smart|hcm", qq, re.I):
            results.append(reverse_by_kho(conn, qq))
        elif qq.upper() in {"SPX", "GHN", "VIETTELPOST", "VNPOST"} or "DANG_GIAO" in qq.upper() or "UNASSIGNED" in qq.upper():
            results.append(reverse_by_buucuc(conn, qq))
        elif re.search(
            r"tỉnh|thành|nam định|sơn la|nghệ an|hà nội|hải|đắk|dak",
            qq,
            re.I,
        ):
            results.append(reverse_by_province(conn, qq))
        else:
            r_so = reverse_by_so_noi_bo(conn, qq)
            if r_so["hit"]:
                results.append(r_so)
            else:
                r_addr = reverse_by_address(conn, qq)
                if r_addr["hit"]:
                    results.append(r_addr)
                else:
                    r_tr = reverse_by_tracking(conn, qq)
                    results.append(r_tr if r_tr["hit"] else r_so)

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
    if province:
        results.append(reverse_by_province(conn, province.strip()))
    if address:
        results.append(reverse_by_address(conn, address.strip()))
    if icon:
        results.append(reverse_by_icon_chant(conn, icon.strip()))

    demo_mode = not any([van_tay, so_noi_bo, tracking, kho, buucuc, province, address, icon, q])
    if demo_mode:
        results = auto_detect_queries(conn)

    hits = sum(1 for r in results if r.get("hit"))
    total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    total_fp = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
    flow_matrix = build_flow_matrix(conn)

    icon_echo: list[dict] = []
    panorama_samples: list[dict] = []
    for r in results:
        fl = r.get("flow")
        if fl:
            panorama_samples.append(fl)
        for fl in r.get("flows") or []:
            if fl and fl.get("van_tay") not in {p.get("van_tay") for p in panorama_samples}:
                panorama_samples.append(fl)
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

    icons = ["hash", "compass", "cube", "network", "monitor", "text"]
    fill = flow_matrix["address_fill"]
    top_fb = feedback_line(
        icons,
        f"truy vấn ngược đào sâu · queries={len(results)} hit={hits} · "
        f"addr province={fill['with_province']}/{fill['total']} "
        f"detail={fill['with_detail']} · buucuc→tỉnh={len(flow_matrix['buucuc_to_province'])}",
    )

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
        "by_province": [
            {"province": r[0], "n": r[1]}
            for r in conn.execute(
                """
                SELECT province, COUNT(*) AS cnt FROM orders
                WHERE province IS NOT NULL AND province != ''
                GROUP BY province ORDER BY cnt DESC LIMIT 15
                """
            )
        ],
    }
    conn.close()

    return {
        "ok": True,
        "query": "Truy vấn ngược đào sâu — toàn cảnh dòng chảy bưu cục → địa chỉ nhận",
        "checked_at": utc_now(),
        "demo_mode": demo_mode,
        "db": {"pipe_db": str(PIPE_DB), "orders": total_orders, "fingerprints": total_fp},
        "summary": {
            "queries": len(results),
            "hits": hits,
            "panorama_samples": len(panorama_samples),
            "icon_echo": len(icon_echo),
            "address_fill": fill,
            "icon_chant": chant(icons),
            "feedback": top_fb,
        },
        "flow_matrix": flow_matrix,
        "results": results,
        "panorama_samples": panorama_samples[:12],
        "icon_echo": icon_echo[:16],
        "index_stats": index_stats,
        "verdict": top_fb,
        "next_actions": [
            "python3 scripts/order_pipe_reverse_query.py --so SAPO-1990252568_664140",
            "python3 scripts/order_pipe_reverse_query.py --tracking SPXVN067431106264",
            "python3 scripts/order_pipe_reverse_query.py --buucuc SPX",
            "python3 scripts/order_pipe_reverse_query.py --province 'Nam Định'",
            "python3 scripts/order_pipe_reverse_query.py --address 'Hải Hậu'",
            "python3 scripts/order_pipe_kho_buucuc_db.py   # re-pipe nếu thiếu địa chỉ",
        ],
        "safety": {"secrets_only": True, "no_dump_login": True, "phone_masked_in_report": True},
    }


def _short_order(o: dict) -> str:
    geo = o.get("province") or ""
    if o.get("district"):
        geo = f"{o.get('district')}, {geo}" if geo else str(o.get("district"))
    return (
        f"[{o.get('van_tay')}] so={o.get('so_noi_bo')} track={o.get('tracking_code') or '∅'} "
        f"· {o.get('backend')}/{o.get('kho')}/{o.get('buucuc')} → {geo or '∅addr'} · {o.get('status')}"
    )


def _fmt_flow(flow: dict | None, L) -> None:
    if not flow:
        return
    L(f"  🌊 FLOW {flow.get('completeness_score')} · {flow.get('flow_text')}")
    for st in flow.get("stages") or []:
        L(f"    {st['step']}. {st['label']}: {st['value']}")
    tl = flow.get("timeline") or {}
    if any(tl.get(k) for k in ("created_at", "picked_at", "delivered_at")):
        L(
            f"    ⏱ create={tl.get('created_at') or '∅'} · "
            f"pick={tl.get('picked_at') or '∅'} · deliver={tl.get('delivered_at') or '∅'}"
        )


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔎 TRUY VẤN NGƯỢC ĐÀO SÂU · BƯU CỤC → ĐỊA CHỈ NHẬN")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    db = report["db"]
    fill = s.get("address_fill") or {}
    L(f"✨ {s.get('feedback')}")
    L(f"Chant: {s.get('icon_chant')}")
    L(
        f"queries={s['queries']} hits={s['hits']} panorama={s.get('panorama_samples')} · "
        f"demo={report.get('demo_mode')}"
    )
    L(f"DB: {db['pipe_db']} · orders={db['orders']} van_tay={db['fingerprints']}")
    L(
        f"Fill địa chỉ: province={fill.get('with_province')}/{fill.get('total')} "
        f"district={fill.get('with_district')} ward={fill.get('with_ward')} "
        f"detail={fill.get('with_detail')} receiver={fill.get('with_receiver')} "
        f"tracking={fill.get('with_tracking')} delivered={fill.get('with_delivered')}"
    )
    L("")
    L("=== Ma trận bưu cục → tỉnh nhận ===")
    for m in (report.get("flow_matrix") or {}).get("buucuc_to_province") or []:
        L(f"· {m.get('buucuc')} → {m.get('province')}: {m.get('orders')}")
    L("")
    L("=== Ma trận kho → tỉnh nhận ===")
    for m in ((report.get("flow_matrix") or {}).get("kho_to_province") or [])[:12]:
        L(f"· {m.get('kho')} → {m.get('province')}: {m.get('orders')}")
    L("")
    L("=== Index ===")
    for b in (report.get("index_stats") or {}).get("by_backend") or []:
        L(f"· backend {b['backend']}: {b['n']}")
    for p in (report.get("index_stats") or {}).get("by_province") or []:
        L(f"· tỉnh {p['province']}: {p['n']}")
    L("")
    L("=== Kết quả + toàn cảnh dòng chảy ===")
    for r in report.get("results") or []:
        mark = "✅" if r.get("hit") else "○"
        L(f"{mark} [{r.get('query_type')}] q={r.get('query')}")
        if r.get("path"):
            L(f"  path: {r['path']}")
        _fmt_flow(r.get("flow"), L)
        if r.get("order") and not r.get("flow"):
            L(f"  {_short_order(r['order'])}")
        for o in (r.get("orders") or r.get("sample_orders") or [])[:4]:
            L(f"  · {_short_order(o)}")
        for fl in (r.get("flows") or [])[1:3]:
            _fmt_flow(fl, L)
        if r.get("buucuc_matrix"):
            for m in r["buucuc_matrix"][:5]:
                L(
                    f"  · buu {m.get('buucuc')} [{m.get('backend')}]: "
                    f"n={m.get('orders')} tỉnh={m.get('provinces')}"
                )
        if r.get("destination_provinces"):
            for m in r["destination_provinces"][:6]:
                L(f"  · đến {m.get('province')}: n={m.get('orders')} huyện={m.get('districts')}")
        if r.get("by_province_district"):
            for m in r["by_province_district"][:8]:
                L(f"  · địa bàn {m.get('province')}/{m.get('district')}: n={m.get('orders')}")
        if r.get("by_buucuc"):
            for m in r["by_buucuc"][:6]:
                L(f"  · từ buu {m.get('buucuc')}: n={m.get('orders')}")
        if r.get("by_district"):
            for m in r["by_district"][:6]:
                L(f"  · huyện {m.get('district')} / {m.get('ward')}: n={m.get('orders')}")
        if r.get("by_kho"):
            for m in r["by_kho"][:5]:
                L(f"  · kho {m.get('kho')}: n={m.get('orders')} tỉnh={m.get('provinces')}")
    if report.get("panorama_samples"):
        L("")
        L("=== Panorama mẫu (bưu cục → địa chỉ) ===")
        for fl in report["panorama_samples"][:6]:
            L(f"· van_tay={fl.get('van_tay')} so={fl.get('so_noi_bo')} [{fl.get('completeness_score')}]")
            _fmt_flow(fl, L)
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
    ap = argparse.ArgumentParser(description="Truy vấn ngược đào sâu — bưu cục → địa chỉ nhận")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--van-tay", dest="van_tay")
    ap.add_argument("--so", dest="so_noi_bo", help="Số nội bộ / order_key")
    ap.add_argument("--tracking")
    ap.add_argument("--kho")
    ap.add_argument("--buucuc")
    ap.add_argument("--province", help="Tỉnh/thành nhận")
    ap.add_argument("--address", help="Fragment địa chỉ / ward / huyện / tên nhận")
    ap.add_argument("--icon", help="Fragment icon chant")
    ap.add_argument("--q", help="Auto-detect query")
    args = ap.parse_args()
    report = build_report(
        van_tay=args.van_tay,
        so_noi_bo=args.so_noi_bo,
        tracking=args.tracking,
        kho=args.kho,
        buucuc=args.buucuc,
        province=args.province,
        address=args.address,
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
