#!/usr/bin/env python3
"""Truy vấn ngược đào sâu: toàn cảnh dòng chảy bưu cục → địa chỉ nhận.

Lookup van_tay / so_noi_bo / tracking / kho / buucuc / tỉnh·huyện·địa chỉ
→ lộ trình: kho → backend → bưu cục → mã VĐ → trạng thái → người nhận → địa chỉ.

Đọc kho_buucuc_pipe.db. Secrets-only. Không dump login.

Module capability mới (khuyến nghị): ``python3 -m order_pipe`` — xem
``docs/ORDER-PIPE-MODULE.md`` và package ``scripts/order_pipe/``.
Script này giữ CLI hop1–13 tương thích / engine bên dưới facade.
"""

from __future__ import annotations

import argparse
import json
import re
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
    try:
        from tracking_aship import attach_tracking_urls

        o = attach_tracking_urls(o)
    except Exception:  # noqa: BLE001
        pass
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
                "tracking_url": o.get("tracking_url"),
                "tracking_provider": o.get("tracking_provider"),
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


def reverse_by_warehouse_id(conn: sqlite3.Connection, wid: str, limit: int = 30) -> dict:
    """Truy vấn ngược từ warehouse_id (UUID) → kho → bưu cục → tỉnh nhận."""
    wid = (wid or "").strip()
    samples = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM orders WHERE warehouse_id = ? ORDER BY piped_at DESC LIMIT ?",
            (wid, limit),
        )
    ]
    kho_name = None
    if samples:
        kho_name = samples[0].get("kho") or samples[0].get("warehouse_display")
    else:
        row = conn.execute(
            "SELECT kho, warehouse_display, COUNT(*) AS n FROM orders WHERE warehouse_id = ? GROUP BY kho LIMIT 1",
            (wid,),
        ).fetchone()
        if row:
            kho_name = row[0] or row[1]

    matrix = [
        dict(r)
        for r in conn.execute(
            """
            SELECT buucuc, backend, status, COUNT(*) AS orders,
                   COUNT(DISTINCT van_tay) AS fps,
                   COUNT(DISTINCT province) AS provinces
            FROM orders WHERE warehouse_id = ?
            GROUP BY buucuc, backend, status ORDER BY orders DESC LIMIT 30
            """,
            (wid,),
        )
    ]
    dest = [
        dict(r)
        for r in conn.execute(
            """
            SELECT province, COUNT(*) AS orders,
                   COUNT(DISTINCT district) AS districts,
                   COUNT(DISTINCT buucuc) AS buucuc_n,
                   SUM(CASE WHEN phone_class='MASKED' THEN 1 ELSE 0 END) AS masked,
                   SUM(CASE WHEN phone_class='OK' THEN 1 ELSE 0 END) AS phone_ok,
                   SUM(CASE WHEN phone_class='MISSING' THEN 1 ELSE 0 END) AS phone_missing
            FROM orders WHERE warehouse_id = ? AND province IS NOT NULL AND province != ''
            GROUP BY province ORDER BY orders DESC LIMIT 25
            """,
            (wid,),
        )
    ]
    status = [
        dict(r)
        for r in conn.execute(
            """
            SELECT status, COUNT(*) AS orders,
                   SUM(CASE WHEN phone_class='MASKED' THEN 1 ELSE 0 END) AS masked
            FROM orders WHERE warehouse_id = ?
            GROUP BY status ORDER BY orders DESC
            """,
            (wid,),
        )
    ]
    phone = [
        dict(r)
        for r in conn.execute(
            """
            SELECT
              CASE
                WHEN phone_class IS NOT NULL AND phone_class != '' THEN phone_class
                WHEN receiver_phone IS NULL OR receiver_phone = '' THEN 'MISSING'
                WHEN instr(receiver_phone, '*') > 0 THEN 'MASKED'
                ELSE 'OK'
              END AS phone_class,
              COUNT(*) AS orders
            FROM orders
            WHERE warehouse_id = ?
            GROUP BY 1 ORDER BY orders DESC
            """,
            (wid,),
        )
    ]
    # Chain ngược: lấy vài van_tay mẫu để panorama
    chain_fps = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, buucuc, province, district,
                   phone_class, receiver_name, flow_path
            FROM orders WHERE warehouse_id = ? AND van_tay IS NOT NULL
            ORDER BY piped_at DESC LIMIT 8
            """,
            (wid,),
        )
    ]
    unmask = {
        "warehouse_phone": "PATH-CLEAR",
        "customer_pii": "PATH-MASK-REDACTION",
        "note": "Kho ASUMEE: SĐT kho clear; PII đơn mask — truy vấn ngược không unmask ****",
        "assist_cli": [
            "python3 scripts/crypto_decode_assist.py --unmask",
            "python3 scripts/inner_unmask_deep_mapper.py --warehouse " + wid,
        ],
    }
    return _attach_flow(
        {
            "query_type": "warehouse_id",
            "query": wid,
            "hit": bool(samples) or bool(matrix),
            "kho": kho_name,
            "orders_n": conn.execute(
                "SELECT COUNT(*) FROM orders WHERE warehouse_id = ?", (wid,)
            ).fetchone()[0],
            "buucuc_matrix": matrix,
            "destination_provinces": dest,
            "by_status": status,
            "phone_class": phone,
            "chain_fingerprints": chain_fps,
            "sample_orders": samples,
            "unmask_map": unmask,
            "path": (
                f"warehouse_id:{wid[:8]}… → kho:{kho_name or '?'} → "
                f"buucuc×{len({m.get('buucuc') for m in matrix})} → tỉnh×{len(dest)}"
            ),
        }
    )


def reverse_chain_asumee(
    conn: sqlite3.Connection,
    *,
    deep: bool = False,
    hop2: bool = False,
    hop6_live: bool = True,
    hop6_apply: bool = False,
    hop6_limit: int = 8,
    hop7_live: bool = True,
    hop7_apply: bool = False,
    hop7_limit: int = 40,
    hop8_apply: bool = False,
    hop8_probe: bool = False,
    hop8_probe_limit: int = 6,
    hop9_live: bool = False,
    hop9_apply: bool = False,
    hop9_limit: int = 40,
    hop10_apply: bool = False,
    hop11_live: bool = False,
    hop11_apply: bool = False,
    hop11_limit: int = 40,
    hop12_live: bool = False,
    hop12_apply: bool = False,
    hop12_limit: int = 40,
    hop12_probe: bool = False,
    hop13_live: bool = False,
    hop13_apply: bool = False,
    hop13_limit: int = 60,
) -> list[dict]:
    """Chuỗi truy vấn ngược đào sâu cho kho ASUMEE / UUID chính.

    deep=True: status → ward → so → tracking → address.
    hop2=True: cohort gaps → geo recover → icon → drill van_tay.
    """
    wid = "55e5f0e1-ed06-4dad-b35a-406bee25cdea"
    results = [
        reverse_by_warehouse_id(conn, wid, limit=20),
        reverse_by_kho(conn, "ASUMEE", limit=15),
    ]
    # Top tỉnh nhận từ kho → reverse province
    top_prov = [
        r[0]
        for r in conn.execute(
            """
            SELECT province FROM orders
            WHERE warehouse_id = ? AND province IS NOT NULL AND province != ''
            GROUP BY province ORDER BY COUNT(*) DESC LIMIT 3
            """,
            (wid,),
        )
    ]
    for p in top_prov:
        results.append(reverse_by_province(conn, p, limit=10))
    # Sample van_tay reverse
    vt = conn.execute(
        """
        SELECT van_tay FROM orders
        WHERE warehouse_id = ? AND van_tay IS NOT NULL
        ORDER BY piped_at DESC LIMIT 3
        """,
        (wid,),
    ).fetchall()
    for (v,) in vt:
        results.append(reverse_by_van_tay(conn, v))
    # Buucuc family trên kho
    buu = conn.execute(
        """
        SELECT buucuc FROM orders WHERE warehouse_id = ?
        GROUP BY buucuc ORDER BY COUNT(*) DESC LIMIT 2
        """,
        (wid,),
    ).fetchall()
    for (b,) in buu:
        if b:
            results.append(reverse_by_buucuc(conn, b, limit=10))

    if not deep:
        return results

    # --- Tiếp tục ngược dòng chảy (deep) ---
    results.append(reverse_flow_gaps(conn, wid))
    for st in ("delivered", "shipped", "submitted", "canceled"):
        results.append(reverse_by_status_warehouse(conn, wid, st, limit=12))

    # Top ward trong top tỉnh → address reverse
    wards = [
        r[0]
        for r in conn.execute(
            """
            SELECT ward FROM orders
            WHERE warehouse_id = ? AND ward IS NOT NULL AND ward != ''
            GROUP BY ward ORDER BY COUNT(*) DESC LIMIT 4
            """,
            (wid,),
        )
    ]
    for w in wards:
        results.append(reverse_by_address(conn, w, limit=8))
        results.append(reverse_by_ward_warehouse(conn, wid, w, limit=8))

    # Delivered samples: so_noi_bo + tracking + van_tay
    delivered = conn.execute(
        """
        SELECT van_tay, so_noi_bo, tracking_code, province, ward
        FROM orders
        WHERE warehouse_id = ? AND status = 'delivered' AND van_tay IS NOT NULL
        ORDER BY piped_at DESC LIMIT 4
        """,
        (wid,),
    ).fetchall()
    for row in delivered:
        vt, so, tr, prov, ward = row
        if so:
            results.append(reverse_by_so_noi_bo(conn, str(so)))
        if tr and str(tr) != str(so):
            results.append(reverse_by_tracking(conn, str(tr)))
        if vt:
            results.append(reverse_by_van_tay(conn, str(vt)))

    # Shipped chưa deliver — ngược từ tracking
    shipped = conn.execute(
        """
        SELECT van_tay, so_noi_bo, tracking_code
        FROM orders
        WHERE warehouse_id = ? AND status = 'shipped' AND tracking_code IS NOT NULL
        ORDER BY piped_at DESC LIMIT 3
        """,
        (wid,),
    ).fetchall()
    for vt, so, tr in shipped:
        if tr:
            results.append(reverse_by_tracking(conn, str(tr)))
        if vt:
            results.append(reverse_by_van_tay(conn, str(vt)))

    if hop2:
        results.extend(reverse_chain_asumee_hop2(conn, wid))
        results.extend(reverse_chain_asumee_hop3(conn, wid))
        results.extend(reverse_chain_asumee_hop4(conn, wid))
        results.extend(reverse_chain_asumee_hop5(conn, wid))
        results.extend(
            reverse_chain_asumee_hop6(
                conn,
                wid,
                live=hop6_live,
                apply=hop6_apply,
                limit=hop6_limit,
            )
        )
        results.extend(
            reverse_chain_asumee_hop7(
                conn,
                wid,
                live=hop7_live,
                apply=hop7_apply,
                limit=hop7_limit,
            )
        )
        results.extend(
            reverse_chain_asumee_hop8(
                conn,
                wid,
                apply=hop8_apply,
                probe=hop8_probe,
                probe_limit=hop8_probe_limit,
            )
        )
        results.extend(
            reverse_chain_asumee_hop9(
                conn,
                wid,
                live=hop9_live,
                apply=hop9_apply,
                limit=hop9_limit,
            )
        )
        results.extend(
            reverse_chain_asumee_hop10(conn, wid, apply=hop10_apply)
        )
        results.extend(
            reverse_chain_asumee_hop11(
                conn,
                wid,
                live=hop11_live,
                apply=hop11_apply,
                limit=hop11_limit,
            )
        )
        results.extend(
            reverse_chain_asumee_hop12(
                conn,
                wid,
                live=hop12_live,
                apply=hop12_apply,
                limit=hop12_limit,
                probe=hop12_probe,
            )
        )
        results.extend(
            reverse_chain_asumee_hop13(
                conn,
                wid,
                live=hop13_live,
                apply=hop13_apply,
                limit=hop13_limit,
            )
        )

    return results


def _load_pancake_env() -> dict[str, str]:
    try:
        from crypto_decode_assist import load_env_secrets

        return load_env_secrets()
    except Exception:  # noqa: BLE001
        env: dict[str, str] = {}
        p = ROOT / "secrets" / "backend_pipes.env"
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
        return env


def map_pancake_histories_to_timeline(detail: dict) -> dict:
    """Map histories[] / partner.extend_update → picked_at / delivered_at (không bịa)."""
    out: dict[str, Any] = {
        "picked_at": None,
        "delivered_at": None,
        "status": detail.get("status") or detail.get("status_name"),
        "partner_name": None,
        "signals": [],
    }
    # Direct fields if ever present
    for k in ("picked_at", "pick_at", "partner_picked_at", "picked_up_at"):
        if detail.get(k):
            out["picked_at"] = detail.get(k)
            out["signals"].append(f"field:{k}")
            break
    for k in ("delivered_at", "delivery_at", "partner_delivered_at", "first_delivery_at"):
        if detail.get(k):
            out["delivered_at"] = detail.get(k)
            out["signals"].append(f"field:{k}")
            break

    partner = detail.get("partner") if isinstance(detail.get("partner"), dict) else {}
    if partner.get("partner_name"):
        out["partner_name"] = partner.get("partner_name")
    for k in ("picked_up_at", "picked_at"):
        if partner.get(k) and not out["picked_at"]:
            out["picked_at"] = partner.get(k)
            out["signals"].append(f"partner:{k}")
    for k in ("first_delivery_at", "delivered_at"):
        if partner.get(k) and not out["delivered_at"]:
            out["delivered_at"] = partner.get(k)
            out["signals"].append(f"partner:{k}")

    # J&T / SPX extend_update timeline (richer than histories for ASUMEE)
    ext = partner.get("extend_update")
    if isinstance(ext, list):
        for ev in ext:
            if not isinstance(ev, dict):
                continue
            desc = str(ev.get("description") or ev.get("status") or "").lower()
            ts = ev.get("updated_at") or ev.get("time")
            code = ev.get("action_code")
            if not ts:
                continue
            # pick / collected
            if not out["picked_at"] and (
                code in {30901, 30101, 30001}
                or "collected" in desc
                or "picked up" in desc
                or "đã lấy" in desc
            ):
                out["picked_at"] = ts
                out["signals"].append(f"extend_update:pick:{code}")
            # delivered
            if not out["delivered_at"] and (
                code in {50101, 50001}
                or "been delivered" in desc
                or "delivered successfully" in desc
                or "giao thành công" in desc
            ):
                out["delivered_at"] = ts
                out["signals"].append(f"extend_update:deliver:{code}")

    histories = detail.get("histories")
    if not isinstance(histories, list):
        histories = []

    for h in histories:
        if not isinstance(h, dict):
            continue
        ts = h.get("updated_at") or h.get("time") or h.get("created_at") or h.get("inserted_at")
        st_obj = h.get("status")
        new_st = None
        if isinstance(st_obj, dict):
            new_st = st_obj.get("new")
        elif st_obj is not None:
            new_st = st_obj
        shopee = h.get("shopee_status")
        shopee_new = shopee.get("new") if isinstance(shopee, dict) else shopee
        label = f"{new_st}|{shopee_new}".lower()
        if ts and not out["picked_at"] and (
            str(new_st) in {"2", "8"}  # in_transit / awaiting_collection
            or (isinstance(shopee_new, str) and shopee_new.upper() in {
                "IN_TRANSIT",
                "AWAITING_COLLECTION",
            })
        ):
            # chỉ lấy mốc IN_TRANSIT đầu như proxy pick nếu chưa có extend_update
            if "in_transit" in label or str(new_st) == "2":
                out["picked_at"] = ts
                out["signals"].append(f"history:pick:{label[:40]}")
        if ts and not out["delivered_at"] and (
            str(new_st) == "3"
            or (isinstance(shopee_new, str) and shopee_new.upper() == "DELIVERED")
        ):
            out["delivered_at"] = ts
            out["signals"].append(f"history:deliver:{label[:40]}")

    return out


def extract_pancake_district(detail: dict) -> str | None:
    sa = detail.get("shipping_address") if isinstance(detail.get("shipping_address"), dict) else {}
    for k in ("district_name", "district", "county_name"):
        v = sa.get(k) or detail.get(k)
        if v and str(v).strip() and "*" not in str(v):
            return str(v).strip()
    # marketplace / full address fallback
    for k in ("marketplace_address", "full_address", "address"):
        raw = sa.get(k) or detail.get(k)
        if raw:
            hint = _district_hint_from_address(str(raw))
            if hint:
                return hint
    return None


def extract_pancake_tracking(detail: dict) -> dict:
    """Lấy mã VĐ thật từ detail (shipments / extend / tracking_link) — không dùng order id nếu khác."""
    order_id = str(detail.get("id") or detail.get("order_id") or "").strip()
    cands: list[tuple[str, str]] = []
    for k in (
        "tracking_code",
        "extend_code",
        "partner_code",
        "order_shipping_code",
        "shipping_code",
    ):
        v = detail.get(k)
        if v and str(v).strip():
            cands.append((k, str(v).strip()))
    shipments = detail.get("shipments")
    if isinstance(shipments, list):
        for sh in shipments:
            if not isinstance(sh, dict):
                continue
            for k in ("tracking_number", "tracking_code", "extend_code", "partner_id"):
                v = sh.get(k)
                if v and str(v).strip():
                    cands.append((f"shipments.{k}", str(v).strip()))
    partner = detail.get("partner") if isinstance(detail.get("partner"), dict) else {}
    for k in ("extend_code", "order_number_v2", "tracking_number", "order_number_vtp"):
        v = partner.get(k)
        if v and str(v).strip():
            cands.append((f"partner.{k}", str(v).strip()))
    link = detail.get("tracking_link") or partner.get("tracking_link")
    partner_name = partner.get("partner_name") or partner.get("delivery_name")
    best = None
    source = None
    for src, code in cands:
        if order_id and code == order_id:
            continue
        # prefer SPX-like / alphanumeric 3PL over pure long digit pancake id
        if re.fullmatch(r"26[0-9A-Za-z]{12}", code) or str(code).upper().startswith("SPX"):
            best, source = code, src
            break
        if not best:
            best, source = code, src
        elif best.isdigit() and len(best) >= 15 and not (code.isdigit() and len(code) >= 15):
            best, source = code, src
    # provider hint from partner_name
    prov = None
    pn = str(partner_name or "").upper()
    if "J&T" in pn or "JNT" in pn or "JT" == pn:
        prov = "jnt"
    elif "SPX" in pn or "SHOPEE" in pn:
        prov = "spx"
    elif "GHN" in pn:
        prov = "ghn"
    elif "VIETTEL" in pn or "VTP" in pn:
        prov = "viettelpost"
    elif best and re.fullmatch(r"26[0-9A-Za-z]{12}", str(best)):
        prov = "spx"
    elif best and str(best).upper().startswith("SPX"):
        prov = "spx"
    return {
        "tracking_code": best,
        "source": source,
        "tracking_link": link,
        "order_id": order_id or None,
        "partner_name": partner_name,
        "provider": prov,
        "candidates": [{"source": s, "code": c} for s, c in cands[:8]],
    }


def fetch_pancake_order_detail(order_id: str, *, shop_id: str | int = 714934229) -> dict:
    """GET owned Pancake detail — secrets-only."""
    import urllib.error
    import urllib.request

    env = _load_pancake_env()
    key = (env.get("PANCAKE_POS_API_KEY") or env.get("PANCAKE_API_KEY") or "").strip()
    if not key:
        return {"ok": False, "error": "missing_api_key"}
    base = f"https://pos.pages.fm/api/v1/shops/{shop_id}/orders/{order_id}"
    url = f"{base}?api_key={key}"
    try:
        with urllib.request.urlopen(url, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {"error": str(e)}
        return {"ok": False, "http": e.code, "error": body}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return {"ok": False, "http": 200, "error": "no_data", "keys": list(body)[:8] if isinstance(body, dict) else []}
    return {"ok": True, "http": 200, "data": data}


def reverse_district_backfill_plan(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False, limit: int = 80
) -> dict:
    """Offline: gợi ý huyện từ full_address → (tuỳ chọn) UPDATE district."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, status, province, ward, full_address, district
            FROM orders
            WHERE warehouse_id = ?
              AND (district IS NULL OR district = '')
              AND full_address IS NOT NULL AND full_address != ''
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, limit),
        )
    ]
    plan = []
    for r in rows:
        hint = _district_hint_from_address(str(r.get("full_address") or ""))
        if not hint:
            continue
        plan.append(
            {
                "van_tay": r.get("van_tay"),
                "so_noi_bo": r.get("so_noi_bo"),
                "status": r.get("status"),
                "province": r.get("province"),
                "ward": r.get("ward"),
                "district_new": hint,
                "action": "update_district",
            }
        )
    applied = 0
    if apply and plan:
        for p in plan:
            cur = conn.execute(
                """
                UPDATE orders SET district = ?
                WHERE van_tay = ?
                  AND (district IS NULL OR district = '')
                """,
                (p["district_new"], p["van_tay"]),
            )
            if cur.rowcount:
                applied += 1
                conn.execute(
                    "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                    (
                        utc_now(),
                        "district_backfill",
                        p["van_tay"],
                        p.get("so_noi_bo"),
                        f"hint:{p['district_new']}",
                    ),
                )
        conn.commit()
    return {
        "query_type": "district_backfill_plan",
        "query": wid,
        "hit": bool(plan) or True,
        "count": len(plan),
        "scanned": len(rows),
        "apply": apply,
        "applied": applied,
        "samples": plan[:12],
        "path": (
            f"district_backfill_plan candidates={len(plan)}/{len(rows)} "
            f"apply={apply} applied={applied}"
        ),
        "unmask_map": {
            "path_id": "PATH-MISSING-GEO",
            "action": "apply_district_hint_or_live_detail",
        },
        "next": [
            "python3 scripts/order_pipe_reverse_query.py --continue-flow --hop6-apply",
            "Live detail để lấy shipping_address.district_name nếu hint yếu",
        ],
    }


def reverse_tracking_classify(conn: sqlite3.Connection, wid: str) -> dict:
    """Phân loại mã VĐ: pancake_18digit / spx_like_26 / other."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT
              CASE
                WHEN tracking_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
                  THEN 'pancake_18digit'
                WHEN tracking_code GLOB '26*' AND length(tracking_code)=14 THEN 'spx_like_26'
                WHEN tracking_code IS NULL OR tracking_code='' THEN 'empty'
                ELSE 'other'
              END AS kind,
              COUNT(*) AS orders,
              SUM(CASE WHEN tracking_code = so_noi_bo THEN 1 ELSE 0 END) AS id_as_tracking
            FROM orders WHERE warehouse_id = ?
            GROUP BY 1 ORDER BY orders DESC
            """,
            (wid,),
        )
    ]
    return {
        "query_type": "tracking_classify",
        "query": wid,
        "hit": True,
        "count": sum(int(r.get("orders") or 0) for r in rows),
        "by_kind": rows,
        "path": f"tracking_classify kinds={len(rows)} " + ", ".join(
            f"{r['kind']}={r['orders']}" for r in rows
        ),
        "unmask_map": {
            "note": "spx_like_26 → aship URL; pancake_18digit cần detail shipments",
            "path_id": "PATH-CLEAR",
        },
        "next": [
            "Hop5 spx_like đã gắn URL; hop6 live detail để thay pancake_18digit",
        ],
    }


def reverse_pipe_events_plan(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False, limit: int = 40
) -> dict:
    """Kế hoạch emit pipe_events theo status snapshot — không bịa pick/deliver time."""
    existing = conn.execute(
        """
        SELECT COUNT(*) FROM pipe_events pe
        WHERE pe.van_tay IN (SELECT van_tay FROM orders WHERE warehouse_id = ?)
        """,
        (wid,),
    ).fetchone()[0]
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, status, tracking_code, province, district
            FROM orders WHERE warehouse_id = ?
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, limit),
        )
    ]
    plan = [
        {
            "van_tay": s.get("van_tay"),
            "so_noi_bo": s.get("so_noi_bo"),
            "event": "status_snapshot",
            "detail": f"status={s.get('status')}|trk={s.get('tracking_code')}|prov={s.get('province')}",
        }
        for s in samples
    ]
    applied = 0
    if apply and plan:
        for p in plan:
            # tránh spam: chỉ insert nếu chưa có status_snapshot gần đây cho van_tay
            has = conn.execute(
                """
                SELECT 1 FROM pipe_events
                WHERE van_tay = ? AND event = 'status_snapshot' LIMIT 1
                """,
                (p["van_tay"],),
            ).fetchone()
            if has:
                continue
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (utc_now(), p["event"], p["van_tay"], p.get("so_noi_bo"), p["detail"]),
            )
            applied += 1
        conn.commit()
    after = conn.execute(
        """
        SELECT COUNT(*) FROM pipe_events pe
        WHERE pe.van_tay IN (SELECT van_tay FROM orders WHERE warehouse_id = ?)
        """,
        (wid,),
    ).fetchone()[0]
    return {
        "query_type": "pipe_events_plan",
        "query": wid,
        "hit": True,
        "count": len(plan),
        "existing_events": existing,
        "events_after": after,
        "apply": apply,
        "applied": applied,
        "samples": plan[:10],
        "path": (
            f"pipe_events_plan planned={len(plan)} existing={existing} "
            f"apply={apply} applied={applied} after={after}"
        ),
        "unmask_map": {
            "path_id": "PATH-MISSING" if existing == 0 else "PATH-CLEAR",
            "action": "emit_status_snapshot_events_for_asumee",
        },
        "next": [
            "python3 scripts/order_pipe_reverse_query.py --continue-flow --hop6-apply",
            "Live histories → event pick/deliver khi có timestamp thật",
        ],
    }


def reverse_pancake_detail_backfill(
    conn: sqlite3.Connection,
    wid: str,
    *,
    limit: int = 8,
    apply: bool = False,
) -> dict:
    """Live: GET detail mẫu → district / tracking / timeline candidates."""
    # Ưu tiên: no_district + timeline gap + spx-like lẫn pancake id
    targets = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, province, district,
                   picked_at, delivered_at, full_address
            FROM orders
            WHERE warehouse_id = ?
              AND so_noi_bo IS NOT NULL AND so_noi_bo != ''
              AND (
                (district IS NULL OR district = '')
                OR (status IN ('shipped','delivered')
                    AND (picked_at IS NULL OR picked_at = ''
                         OR (status='delivered' AND (delivered_at IS NULL OR delivered_at=''))))
                OR (tracking_code = so_noi_bo)
              )
            ORDER BY
              CASE WHEN status='delivered' THEN 0
                   WHEN status='shipped' THEN 1
                   ELSE 2 END,
              piped_at DESC
            LIMIT ?
            """,
            (wid, limit),
        )
    ]
    probes = []
    applied = {
        "district": 0,
        "tracking": 0,
        "picked_at": 0,
        "delivered_at": 0,
        "events": 0,
        "carrier": 0,
        "buucuc": 0,
    }
    ok_n = 0
    for t in targets:
        oid = str(t.get("so_noi_bo") or "").strip()
        # nếu so là SPX-like, thử tracking_code digit / order_key — vẫn thử so trước
        res = fetch_pancake_order_detail(oid)
        entry: dict[str, Any] = {
            "van_tay": t.get("van_tay"),
            "so_noi_bo": oid,
            "status_pipe": t.get("status"),
            "ok": res.get("ok"),
            "http": res.get("http"),
        }
        if not res.get("ok"):
            entry["error"] = res.get("error")
            # fallback: nếu so là SPX mã, không phải pancake id
            probes.append(entry)
            continue
        ok_n += 1
        detail = res["data"]
        dist = extract_pancake_district(detail)
        tr = extract_pancake_tracking(detail)
        tl = map_pancake_histories_to_timeline(detail)
        hist_n = len(detail.get("histories") or []) if isinstance(detail.get("histories"), list) else 0
        entry.update(
            {
                "district_api": dist,
                "tracking_api": tr.get("tracking_code"),
                "tracking_source": tr.get("source"),
                "tracking_link": tr.get("tracking_link"),
                "partner_name": tr.get("partner_name") or tl.get("partner_name"),
                "provider": tr.get("provider"),
                "picked_at_api": tl.get("picked_at"),
                "delivered_at_api": tl.get("delivered_at"),
                "timeline_signals": tl.get("signals"),
                "histories_n": hist_n,
                "status_api": detail.get("status") or detail.get("status_name"),
                "sa_keys": list((detail.get("shipping_address") or {}).keys())[:12]
                if isinstance(detail.get("shipping_address"), dict)
                else [],
            }
        )
        if apply:
            vt = t.get("van_tay")
            if dist and (not t.get("district")):
                cur = conn.execute(
                    """
                    UPDATE orders SET district = ?
                    WHERE van_tay = ? AND (district IS NULL OR district = '')
                    """,
                    (dist, vt),
                )
                if cur.rowcount:
                    applied["district"] += 1
            if tr.get("tracking_code") and tr["tracking_code"] != t.get("tracking_code"):
                route = map_partner_name_to_routing(
                    tr.get("partner_name") or tl.get("partner_name"),
                    provider=tr.get("provider"),
                    tracking_code=tr.get("tracking_code"),
                )
                cur = conn.execute(
                    """
                    UPDATE orders
                    SET tracking_code = ?,
                        tracking_ref = ?,
                        tracking_provider = COALESCE(?, tracking_provider),
                        tracking_url = COALESCE(?, tracking_url),
                        carrier = CASE
                          WHEN ? IS NOT NULL AND (
                            carrier IS NULL OR carrier = '' OR carrier = 'Pancake'
                          ) THEN ? ELSE carrier END,
                        buucuc = CASE
                          WHEN ? IS NOT NULL AND (
                            buucuc IS NULL OR buucuc = '' OR buucuc = 'Pancake'
                          ) THEN ? ELSE buucuc END
                    WHERE van_tay = ?
                    """,
                    (
                        tr["tracking_code"],
                        tr["tracking_code"],
                        route.get("provider") or tr.get("provider"),
                        tr.get("tracking_link"),
                        route.get("carrier"),
                        route.get("carrier"),
                        route.get("buucuc"),
                        route.get("buucuc"),
                        vt,
                    ),
                )
                if cur.rowcount:
                    applied["tracking"] += 1
                    applied["carrier"] = applied.get("carrier", 0) + (
                        1 if route.get("carrier") else 0
                    )
                    applied["buucuc"] = applied.get("buucuc", 0) + (
                        1 if route.get("buucuc") else 0
                    )
            if tl.get("picked_at") and not t.get("picked_at"):
                cur = conn.execute(
                    "UPDATE orders SET picked_at = ? WHERE van_tay = ? AND (picked_at IS NULL OR picked_at = '')",
                    (tl["picked_at"], vt),
                )
                if cur.rowcount:
                    applied["picked_at"] += 1
            if tl.get("delivered_at") and not t.get("delivered_at"):
                cur = conn.execute(
                    "UPDATE orders SET delivered_at = ? WHERE van_tay = ? AND (delivered_at IS NULL OR delivered_at = '')",
                    (tl["delivered_at"], vt),
                )
                if cur.rowcount:
                    applied["delivered_at"] += 1
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "pancake_detail_probe",
                    vt,
                    oid,
                    (
                        f"dist={dist or '∅'}|trk={tr.get('tracking_code') or '∅'}|"
                        f"prov={tr.get('provider') or '∅'}|partner={tr.get('partner_name') or '∅'}|"
                        f"pick={tl.get('picked_at') or '∅'}|del={tl.get('delivered_at') or '∅'}|"
                        f"hist={hist_n}|sig={','.join(tl.get('signals') or [])[:80]}"
                    ),
                ),
            )
            applied["events"] += 1
        probes.append(entry)
    if apply:
        conn.commit()

    with_dist = sum(1 for p in probes if p.get("district_api"))
    with_trk = sum(1 for p in probes if p.get("tracking_api"))
    with_pick = sum(1 for p in probes if p.get("picked_at_api"))
    with_del = sum(1 for p in probes if p.get("delivered_at_api"))
    return {
        "query_type": "pancake_detail_backfill",
        "query": wid,
        "hit": ok_n > 0,
        "count": len(probes),
        "ok": ok_n,
        "apply": apply,
        "applied": applied,
        "summary": {
            "with_district": with_dist,
            "with_tracking": with_trk,
            "with_picked_at": with_pick,
            "with_delivered_at": with_del,
        },
        "samples": probes,
        "path": (
            f"pancake_detail_backfill ok={ok_n}/{len(probes)} "
            f"dist={with_dist} trk={with_trk} pick={with_pick} del={with_del} "
            f"apply={apply} applied={applied}"
        ),
        "unmask_map": {
            "note": "Detail api_key vẫn MASK PII; chỉ backfill geo/tracking/timeline",
            "path_id": "PATH-CLEAR" if ok_n else "PATH-MISSING",
        },
        "next": [
            "Nếu histories không có timestamp pick/deliver → chấp nhận gap",
            "python3 scripts/order_pipe_reverse_query.py --hop6-live --hop6-apply --hop6-limit 20",
        ],
    }


def reverse_chain_asumee_hop6(
    conn: sqlite3.Connection,
    wid: str,
    *,
    live: bool = True,
    apply: bool = False,
    limit: int = 8,
) -> list[dict]:
    """Hop-6: backfill plan district/tracking/events + (tuỳ chọn) live Pancake detail."""
    out: list[dict] = []

    out.append(reverse_tracking_classify(conn, wid))

    dist_plan = reverse_district_backfill_plan(conn, wid, apply=apply, limit=80)
    out.append(dist_plan)
    for s in (dist_plan.get("samples") or [])[:4]:
        vt = s.get("van_tay")
        so = s.get("so_noi_bo")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop6_district_backfill"
            out.append(r)
        if so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "hop6_district_backfill"
            out.append(r)
        if s.get("district_new"):
            out.append(reverse_by_address(conn, str(s["district_new"]), limit=5))

    ev = reverse_pipe_events_plan(conn, wid, apply=apply, limit=max(20, limit * 3))
    out.append(ev)

    if live:
        detail = reverse_pancake_detail_backfill(conn, wid, limit=limit, apply=apply)
        out.append(detail)
        for s in (detail.get("samples") or [])[:5]:
            if not s.get("ok"):
                continue
            vt = s.get("van_tay")
            so = s.get("so_noi_bo")
            tr = s.get("tracking_api")
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = "hop6_live_detail"
                out.append(r)
            if tr:
                r = reverse_by_tracking(conn, str(tr))
                r["gap_cohort"] = "hop6_live_detail"
                out.append(r)
            elif so:
                r = reverse_by_so_noi_bo(conn, str(so))
                r["gap_cohort"] = "hop6_live_detail"
                out.append(r)
    else:
        out.append(
            {
                "query_type": "pancake_detail_backfill",
                "query": wid,
                "hit": False,
                "skipped": True,
                "path": "pancake_detail_backfill skipped (live=False)",
                "next": ["Thêm --hop6-live để GET detail owned"],
            }
        )

    # Re-count pipe_events after optional apply
    out.append(reverse_pipe_events_asumee(conn, wid))
    return out


def map_partner_name_to_routing(
    partner_name: str | None,
    *,
    provider: str | None = None,
    tracking_code: str | None = None,
) -> dict[str, str | None]:
    """Map partner_name / provider / mã VĐ → carrier + buucuc + aship provider."""
    pn = str(partner_name or "").strip()
    up = pn.upper()
    # Bỏ dấu để khớp "Giao hàng nhanh" / "Giao Hang Nhanh"
    up_ascii = (
        up.replace("À", "A")
        .replace("Á", "A")
        .replace("Ả", "A")
        .replace("Ã", "A")
        .replace("Ạ", "A")
        .replace("Ă", "A")
        .replace("Ằ", "A")
        .replace("Ắ", "A")
        .replace("Ẳ", "A")
        .replace("Ẵ", "A")
        .replace("Ặ", "A")
        .replace("Â", "A")
        .replace("Ầ", "A")
        .replace("Ấ", "A")
        .replace("Ẩ", "A")
        .replace("Ẫ", "A")
        .replace("Ậ", "A")
        .replace("È", "E")
        .replace("É", "E")
        .replace("Ẻ", "E")
        .replace("Ẽ", "E")
        .replace("Ẹ", "E")
        .replace("Ê", "E")
        .replace("Ề", "E")
        .replace("Ế", "E")
        .replace("Ể", "E")
        .replace("Ễ", "E")
        .replace("Ệ", "E")
        .replace("Ì", "I")
        .replace("Í", "I")
        .replace("Ỉ", "I")
        .replace("Ĩ", "I")
        .replace("Ị", "I")
        .replace("Ò", "O")
        .replace("Ó", "O")
        .replace("Ỏ", "O")
        .replace("Õ", "O")
        .replace("Ọ", "O")
        .replace("Ô", "O")
        .replace("Ồ", "O")
        .replace("Ố", "O")
        .replace("Ổ", "O")
        .replace("Ỗ", "O")
        .replace("Ộ", "O")
        .replace("Ơ", "O")
        .replace("Ờ", "O")
        .replace("Ớ", "O")
        .replace("Ở", "O")
        .replace("Ỡ", "O")
        .replace("Ợ", "O")
        .replace("Ù", "U")
        .replace("Ú", "U")
        .replace("Ủ", "U")
        .replace("Ũ", "U")
        .replace("Ụ", "U")
        .replace("Ư", "U")
        .replace("Ừ", "U")
        .replace("Ứ", "U")
        .replace("Ử", "U")
        .replace("Ữ", "U")
        .replace("Ự", "U")
        .replace("Ỳ", "Y")
        .replace("Ý", "Y")
        .replace("Ỷ", "Y")
        .replace("Ỹ", "Y")
        .replace("Ỵ", "Y")
        .replace("Đ", "D")
    )
    code = str(tracking_code or "").strip()
    prov = (provider or "").strip().lower() or None
    carrier = None
    buucuc = None

    if prov == "jnt" or "J&T" in up or "JNT" in up or up in {"JT", "J AND T"}:
        carrier, buucuc, prov = "J&T", "J&T", "jnt"
    elif prov == "spx" or "SPX" in up or "SHOPEE XPRESS" in up or "SHOPEE" in up:
        carrier, buucuc, prov = "Shopee Xpress", "SPX", "spx"
    elif (
        prov == "ghn"
        or "GHN" in up
        or "GIAOHANGNHANH" in up_ascii.replace(" ", "")
        or "GIAO HANG NHANH" in up_ascii
    ):
        carrier, buucuc, prov = "GHN", "GHN", "ghn"
    elif prov == "viettelpost" or "VIETTEL" in up or "VTP" in up:
        carrier, buucuc, prov = "Viettel Post", "ViettelPost", "viettelpost"
    elif "VNPOST" in up or "BUU DIEN" in up_ascii or "BƯU ĐIỆN" in up:
        carrier, buucuc, prov = "VNPost", "VNPost", "vnpost"
    elif "GHTK" in up or "GIAOHANGTIETKIEM" in up_ascii.replace(" ", ""):
        carrier, buucuc, prov = "GHTK", "GHTK", "ghtk"
    elif code.upper().startswith("SPX") or re.fullmatch(r"26[0-9A-Za-z]{12}", code):
        carrier, buucuc, prov = "Shopee Xpress", "SPX", "spx"
    elif code.upper().startswith("VNGH") or code.upper().startswith("GHN"):
        carrier, buucuc, prov = "GHN", "GHN", "ghn"
    elif pn and pn not in {"Pancake", "None"}:
        carrier = pn
        buucuc = pn

    return {"carrier": carrier, "buucuc": buucuc, "provider": prov, "partner_name": pn or None}


def reverse_carrier_buucuc_remap(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False
) -> dict:
    """Remap buucuc/carrier từ carrier/tracking_provider/tracking_code đã biết."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, status, carrier, buucuc,
                   tracking_code, tracking_provider, tracking_url
            FROM orders
            WHERE warehouse_id = ?
              AND (
                (carrier IS NOT NULL AND carrier != '' AND carrier != 'Pancake'
                 AND (buucuc IS NULL OR buucuc = '' OR buucuc = 'Pancake'
                      OR buucuc = carrier))
                OR (tracking_provider IS NOT NULL AND tracking_provider != '')
                OR (tracking_code GLOB '26*' AND length(tracking_code)=14)
                OR (upper(tracking_code) LIKE 'SPX%')
                OR (upper(tracking_code) LIKE 'VNGH%')
                OR (upper(tracking_code) LIKE 'GHN%')
                OR (carrier LIKE '%Giao%')
                OR (carrier LIKE '%hàng nhanh%')
              )
            ORDER BY piped_at DESC
            """,
            (wid,),
        )
    ]
    plan = []
    applied = 0
    by_buucuc: dict[str, int] = {}
    for r in rows:
        route = map_partner_name_to_routing(
            r.get("carrier"),
            provider=r.get("tracking_provider"),
            tracking_code=r.get("tracking_code"),
        )
        if not route.get("buucuc") and not route.get("carrier"):
            continue
        need_buu = route.get("buucuc") and (
            not r.get("buucuc")
            or r.get("buucuc") == "Pancake"
            or r.get("buucuc") != route.get("buucuc")
        )
        need_car = route.get("carrier") and (
            not r.get("carrier")
            or r.get("carrier") == "Pancake"
            or r.get("carrier") != route.get("carrier")
        )
        if not need_buu and not need_car:
            continue
        item = {
            "van_tay": r.get("van_tay"),
            "so_noi_bo": r.get("so_noi_bo"),
            "status": r.get("status"),
            "carrier_old": r.get("carrier"),
            "buucuc_old": r.get("buucuc"),
            "carrier_new": route.get("carrier") if need_car else r.get("carrier"),
            "buucuc_new": route.get("buucuc") if need_buu else r.get("buucuc"),
            "provider": route.get("provider"),
            "tracking_code": r.get("tracking_code"),
        }
        plan.append(item)
        bkey = str(item.get("buucuc_new") or "(none)")
        by_buucuc[bkey] = by_buucuc.get(bkey, 0) + 1
        if apply:
            if need_car and item.get("carrier_new"):
                conn.execute(
                    "UPDATE orders SET carrier = ? WHERE van_tay = ?",
                    (item["carrier_new"], r.get("van_tay")),
                )
            if need_buu and item.get("buucuc_new"):
                conn.execute(
                    "UPDATE orders SET buucuc = ? WHERE van_tay = ?",
                    (item["buucuc_new"], r.get("van_tay")),
                )
            if route.get("provider"):
                conn.execute(
                    """
                    UPDATE orders SET tracking_provider = COALESCE(?, tracking_provider)
                    WHERE van_tay = ?
                    """,
                    (route.get("provider"), r.get("van_tay")),
                )
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "carrier_buucuc_remap",
                    r.get("van_tay"),
                    r.get("so_noi_bo"),
                    f"{item.get('carrier_old')}→{item.get('carrier_new')}|{item.get('buucuc_old')}→{item.get('buucuc_new')}",
                ),
            )
            applied += 1
    if apply and applied:
        conn.commit()

    # Matrix after (or current)
    matrix = [
        dict(r)
        for r in conn.execute(
            """
            SELECT carrier, buucuc, COUNT(*) AS orders
            FROM orders WHERE warehouse_id = ?
            GROUP BY carrier, buucuc ORDER BY orders DESC LIMIT 20
            """,
            (wid,),
        )
    ]
    return {
        "query_type": "carrier_buucuc_remap",
        "query": wid,
        "hit": bool(plan) or True,
        "count": len(plan),
        "apply": apply,
        "applied": applied,
        "by_buucuc_new": [
            {"buucuc": k, "n": v} for k, v in sorted(by_buucuc.items(), key=lambda x: -x[1])
        ],
        "matrix": matrix,
        "samples": plan[:12],
        "path": (
            f"carrier_buucuc_remap candidates={len(plan)} apply={apply} "
            f"applied={applied} matrix×{len(matrix)}"
        ),
        "unmask_map": {
            "path_id": "PATH-CLEAR",
            "action": "remap_pancake_placeholder_to_partner_3pl",
        },
        "next": [
            "python3 scripts/order_pipe_reverse_query.py --hop7-apply --hop7-limit 100",
            "Batch detail còn lại để có partner_name trước khi remap",
        ],
    }


def reverse_batch_timeline_backfill(
    conn: sqlite3.Connection,
    wid: str,
    *,
    limit: int = 40,
    apply: bool = False,
) -> dict:
    """Hop7: batch live detail chỉ shipped/delivered thiếu pick/deliver hoặc trk=so."""
    targets = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, province, district,
                   picked_at, delivered_at, carrier, buucuc
            FROM orders
            WHERE warehouse_id = ?
              AND status IN ('shipped', 'delivered')
              AND so_noi_bo IS NOT NULL AND so_noi_bo != ''
              AND (
                picked_at IS NULL OR picked_at = ''
                OR (status = 'delivered' AND (delivered_at IS NULL OR delivered_at = ''))
                OR tracking_code = so_noi_bo
                OR carrier IS NULL OR carrier = '' OR carrier = 'Pancake'
              )
            ORDER BY
              CASE WHEN status = 'delivered' THEN 0 ELSE 1 END,
              piped_at DESC
            LIMIT ?
            """,
            (wid, limit),
        )
    ]
    # Reuse detail backfill but with explicit target list via temporary filter:
    # call fetch per target (same as detail backfill) — inline for hop7 stats
    probes = []
    applied = {
        "district": 0,
        "tracking": 0,
        "picked_at": 0,
        "delivered_at": 0,
        "events": 0,
        "carrier": 0,
        "buucuc": 0,
    }
    ok_n = 0
    partners: dict[str, int] = {}
    for t in targets:
        oid = str(t.get("so_noi_bo") or "").strip()
        res = fetch_pancake_order_detail(oid)
        entry: dict[str, Any] = {
            "van_tay": t.get("van_tay"),
            "so_noi_bo": oid,
            "status_pipe": t.get("status"),
            "ok": res.get("ok"),
        }
        if not res.get("ok"):
            entry["error"] = res.get("error")
            probes.append(entry)
            continue
        ok_n += 1
        detail = res["data"]
        dist = extract_pancake_district(detail)
        tr = extract_pancake_tracking(detail)
        tl = map_pancake_histories_to_timeline(detail)
        route = map_partner_name_to_routing(
            tr.get("partner_name") or tl.get("partner_name"),
            provider=tr.get("provider"),
            tracking_code=tr.get("tracking_code"),
        )
        pn = route.get("partner_name") or tr.get("partner_name") or "(none)"
        partners[str(pn)] = partners.get(str(pn), 0) + 1
        entry.update(
            {
                "district_api": dist,
                "tracking_api": tr.get("tracking_code"),
                "tracking_source": tr.get("source"),
                "tracking_link": tr.get("tracking_link"),
                "partner_name": pn,
                "provider": route.get("provider") or tr.get("provider"),
                "carrier_new": route.get("carrier"),
                "buucuc_new": route.get("buucuc"),
                "picked_at_api": tl.get("picked_at"),
                "delivered_at_api": tl.get("delivered_at"),
                "timeline_signals": tl.get("signals"),
            }
        )
        if apply:
            vt = t.get("van_tay")
            if dist and not t.get("district"):
                cur = conn.execute(
                    """
                    UPDATE orders SET district = ?
                    WHERE van_tay = ? AND (district IS NULL OR district = '')
                    """,
                    (dist, vt),
                )
                if cur.rowcount:
                    applied["district"] += 1
            if tr.get("tracking_code") and tr["tracking_code"] != t.get("tracking_code"):
                cur = conn.execute(
                    """
                    UPDATE orders
                    SET tracking_code = ?, tracking_ref = ?,
                        tracking_provider = COALESCE(?, tracking_provider),
                        tracking_url = COALESCE(?, tracking_url)
                    WHERE van_tay = ?
                    """,
                    (
                        tr["tracking_code"],
                        tr["tracking_code"],
                        route.get("provider") or tr.get("provider"),
                        tr.get("tracking_link"),
                        vt,
                    ),
                )
                if cur.rowcount:
                    applied["tracking"] += 1
            if route.get("carrier"):
                cur = conn.execute(
                    """
                    UPDATE orders SET carrier = ?
                    WHERE van_tay = ?
                      AND (carrier IS NULL OR carrier = '' OR carrier = 'Pancake')
                    """,
                    (route["carrier"], vt),
                )
                if cur.rowcount:
                    applied["carrier"] += 1
            if route.get("buucuc"):
                cur = conn.execute(
                    """
                    UPDATE orders SET buucuc = ?
                    WHERE van_tay = ?
                      AND (buucuc IS NULL OR buucuc = '' OR buucuc = 'Pancake')
                    """,
                    (route["buucuc"], vt),
                )
                if cur.rowcount:
                    applied["buucuc"] += 1
            if tl.get("picked_at") and not t.get("picked_at"):
                cur = conn.execute(
                    """
                    UPDATE orders SET picked_at = ?
                    WHERE van_tay = ? AND (picked_at IS NULL OR picked_at = '')
                    """,
                    (tl["picked_at"], vt),
                )
                if cur.rowcount:
                    applied["picked_at"] += 1
            if tl.get("delivered_at") and (
                t.get("status") == "delivered" or tl.get("delivered_at")
            ):
                # chỉ ghi delivered_at khi status delivered hoặc API báo deliver
                if t.get("status") == "delivered" and not t.get("delivered_at"):
                    cur = conn.execute(
                        """
                        UPDATE orders SET delivered_at = ?
                        WHERE van_tay = ? AND (delivered_at IS NULL OR delivered_at = '')
                        """,
                        (tl["delivered_at"], vt),
                    )
                    if cur.rowcount:
                        applied["delivered_at"] += 1
                elif t.get("status") == "shipped" and tl.get("delivered_at"):
                    # API đã deliver nhưng pipe còn shipped — cập nhật cả status
                    cur = conn.execute(
                        """
                        UPDATE orders
                        SET delivered_at = COALESCE(delivered_at, ?),
                            status = 'delivered'
                        WHERE van_tay = ? AND status = 'shipped'
                        """,
                        (tl["delivered_at"], vt),
                    )
                    if cur.rowcount:
                        applied["delivered_at"] += 1
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "hop7_batch_backfill",
                    vt,
                    oid,
                    (
                        f"trk={tr.get('tracking_code') or '∅'}|"
                        f"car={route.get('carrier') or '∅'}|buu={route.get('buucuc') or '∅'}|"
                        f"pick={tl.get('picked_at') or '∅'}|del={tl.get('delivered_at') or '∅'}"
                    ),
                ),
            )
            applied["events"] += 1
        probes.append(entry)
    if apply:
        conn.commit()

    remain = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ?
          AND status IN ('shipped', 'delivered')
          AND (
            picked_at IS NULL OR picked_at = ''
            OR (status = 'delivered' AND (delivered_at IS NULL OR delivered_at = ''))
          )
        """,
        (wid,),
    ).fetchone()[0]
    remain_hard = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ?
          AND (
            (status = 'delivered' AND (delivered_at IS NULL OR delivered_at = ''))
            OR (status = 'shipped' AND (picked_at IS NULL OR picked_at = ''))
          )
        """,
        (wid,),
    ).fetchone()[0]
    remain_soft_pick = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ?
          AND status = 'delivered'
          AND delivered_at IS NOT NULL AND delivered_at != ''
          AND (picked_at IS NULL OR picked_at = '')
        """,
        (wid,),
    ).fetchone()[0]

    return {
        "query_type": "batch_timeline_backfill",
        "query": wid,
        "hit": ok_n > 0,
        "count": len(probes),
        "ok": ok_n,
        "apply": apply,
        "applied": applied,
        "partners": [
            {"partner": k, "n": v} for k, v in sorted(partners.items(), key=lambda x: -x[1])
        ],
        "remaining_timeline_gaps": remain,
        "remain_hard": remain_hard,
        "remain_soft_delivered_no_pick": remain_soft_pick,
        "samples": probes[:15],
        "path": (
            f"batch_timeline_backfill ok={ok_n}/{len(probes)} "
            f"apply={apply} applied={applied} remain_gap={remain} "
            f"hard={remain_hard} soft_no_pick={remain_soft_pick}"
        ),
        "unmask_map": {
            "note": "Batch owned detail; PII vẫn MASK; timeline từ extend_update/histories",
            "path_id": "PATH-CLEAR" if ok_n else "PATH-MISSING",
        },
        "next": [
            f"Hard gap={remain_hard} (delivered∅del hoặc shipped∅pick) — SPX thường thiếu histories",
            f"Soft gap={remain_soft_pick} delivered đã có del nhưng∅pick (không bịa pick)",
            "python3 scripts/order_pipe_reverse_query.py --hop7-apply --hop7-limit 200",
        ],
    }


def reverse_chain_asumee_hop7(
    conn: sqlite3.Connection,
    wid: str,
    *,
    live: bool = True,
    apply: bool = False,
    limit: int = 40,
) -> list[dict]:
    """Hop-7: batch timeline/tracking backfill + remap carrier/buucuc theo partner."""
    out: list[dict] = []

    # Baseline gaps
    out.append(reverse_timeline_gap(conn, wid, limit=8))
    out.append(reverse_tracking_classify(conn, wid))

    if live:
        batch = reverse_batch_timeline_backfill(conn, wid, limit=limit, apply=apply)
        out.append(batch)
        for s in (batch.get("samples") or [])[:6]:
            if not s.get("ok"):
                continue
            vt = s.get("van_tay")
            tr = s.get("tracking_api")
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = "hop7_batch"
                out.append(r)
            if tr:
                r = reverse_by_tracking(conn, str(tr))
                r["gap_cohort"] = "hop7_batch"
                out.append(r)
    else:
        out.append(
            {
                "query_type": "batch_timeline_backfill",
                "query": wid,
                "hit": False,
                "skipped": True,
                "path": "batch_timeline_backfill skipped (live=False)",
            }
        )

    remap = reverse_carrier_buucuc_remap(conn, wid, apply=apply)
    out.append(remap)
    for s in (remap.get("samples") or [])[:4]:
        vt = s.get("van_tay")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop7_remap"
            out.append(r)
        buu = s.get("buucuc_new")
        if buu:
            out.append(reverse_by_buucuc(conn, str(buu), limit=6))

    # Post matrix drills for top non-Pancake buucuc
    for (buu,) in conn.execute(
        """
        SELECT buucuc FROM orders
        WHERE warehouse_id = ? AND buucuc IS NOT NULL AND buucuc != '' AND buucuc != 'Pancake'
        GROUP BY buucuc ORDER BY COUNT(*) DESC LIMIT 3
        """,
        (wid,),
    ):
        out.append(reverse_by_buucuc(conn, str(buu), limit=8))

    out.append(reverse_timeline_gap(conn, wid, limit=6))
    out.append(reverse_pipe_events_asumee(conn, wid))
    out.append(reverse_tracking_classify(conn, wid))
    return out


def reverse_hard_soft_gaps(conn: sqlite3.Connection, wid: str) -> dict:
    """Phân tách hard/soft timeline gap sau hop7."""
    hard_del = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND status = 'delivered'
          AND (delivered_at IS NULL OR delivered_at = '')
        """,
        (wid,),
    ).fetchone()[0]
    hard_ship = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND status = 'shipped'
          AND (picked_at IS NULL OR picked_at = '')
        """,
        (wid,),
    ).fetchone()[0]
    soft = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND status = 'delivered'
          AND delivered_at IS NOT NULL AND delivered_at != ''
          AND (picked_at IS NULL OR picked_at = '')
        """,
        (wid,),
    ).fetchone()[0]
    by_carrier = [
        dict(r)
        for r in conn.execute(
            """
            SELECT coalesce(carrier,'(none)') AS carrier, status, COUNT(*) AS orders
            FROM orders
            WHERE warehouse_id = ?
              AND (
                (status = 'delivered' AND (delivered_at IS NULL OR delivered_at = ''))
                OR (status = 'shipped' AND (picked_at IS NULL OR picked_at = ''))
                OR (
                  status = 'delivered'
                  AND delivered_at IS NOT NULL AND delivered_at != ''
                  AND (picked_at IS NULL OR picked_at = '')
                )
              )
            GROUP BY 1, 2 ORDER BY orders DESC LIMIT 20
            """,
            (wid,),
        )
    ]
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, carrier, buucuc,
                   picked_at, delivered_at, tracking_url, tracking_provider
            FROM orders
            WHERE warehouse_id = ?
              AND (
                (status = 'delivered' AND (delivered_at IS NULL OR delivered_at = ''))
                OR (status = 'shipped' AND (picked_at IS NULL OR picked_at = ''))
              )
            ORDER BY piped_at DESC LIMIT 12
            """,
            (wid,),
        )
    ]
    return {
        "query_type": "hard_soft_gaps",
        "query": wid,
        "hit": True,
        "count": hard_del + hard_ship + soft,
        "hard_delivered_no_at": hard_del,
        "hard_shipped_no_pick": hard_ship,
        "soft_delivered_no_pick": soft,
        "by_carrier_status": by_carrier,
        "samples": samples,
        "path": (
            f"hard_soft_gaps hard_del={hard_del} hard_ship={hard_ship} "
            f"soft_no_pick={soft}"
        ),
        "unmask_map": {
            "path_id": "PATH-MISSING" if (hard_del + hard_ship) else "PATH-CLEAR",
            "action": "accept_spx_missing_histories_or_refetch_partner_webhook",
        },
        "next": [
            "Hard SPX thường hist=0 / extend_update=∅ — không bịa timestamp",
            "Soft: đã có delivered_at, thiếu pick — giữ nguyên",
        ],
    }


def reverse_3pl_completeness(conn: sqlite3.Connection, wid: str) -> dict:
    """Ma trận 3PL: carrier × fill tracking/pick/deliver/url."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT
              coalesce(nullif(carrier,''), '(none)') AS carrier,
              coalesce(nullif(buucuc,''), '(none)') AS buucuc,
              COUNT(*) AS orders,
              SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != ''
                        AND tracking_code != so_noi_bo THEN 1 ELSE 0 END) AS trk_real,
              SUM(CASE WHEN ifnull(tracking_url,'') != '' THEN 1 ELSE 0 END) AS with_url,
              SUM(CASE WHEN ifnull(picked_at,'') != '' THEN 1 ELSE 0 END) AS with_pick,
              SUM(CASE WHEN ifnull(delivered_at,'') != '' THEN 1 ELSE 0 END) AS with_del,
              SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered,
              SUM(CASE WHEN status = 'shipped' THEN 1 ELSE 0 END) AS shipped
            FROM orders WHERE warehouse_id = ?
            GROUP BY 1, 2 ORDER BY orders DESC LIMIT 20
            """,
            (wid,),
        )
    ]
    return {
        "query_type": "three_pl_completeness",
        "query": wid,
        "hit": bool(rows),
        "count": len(rows),
        "matrix": rows,
        "path": f"three_pl_completeness carriers×{len(rows)}",
        "unmask_map": {"path_id": "PATH-CLEAR", "action": "monitor_3pl_fill_rates"},
    }


def reverse_aship_url_sync(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False, limit: int = 120
) -> dict:
    """Đồng bộ tracking_url/provider aship cho mã VĐ thật; sửa provider lệch."""
    try:
        from tracking_aship import attach_tracking_urls, build_tracking_url, resolve_provider
    except Exception as e:  # noqa: BLE001
        return {
            "query_type": "aship_url_sync",
            "query": wid,
            "hit": False,
            "error": str(e),
            "path": "aship_url_sync: module lỗi",
        }

    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, carrier, buucuc,
                   tracking_provider, tracking_url, status
            FROM orders
            WHERE warehouse_id = ?
              AND tracking_code IS NOT NULL AND tracking_code != ''
              AND tracking_code != so_noi_bo
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, max(limit, 500)),
        )
    ]
    plan = []
    applied = {"url": 0, "provider": 0}
    by_prov: dict[str, int] = {}
    for r in rows:
        route_prov = resolve_provider(
            carrier=r.get("carrier"),
            buucuc=r.get("buucuc"),
            tracking_code=r.get("tracking_code"),
        )
        # Carrier thắng tracking_provider cũ nếu lệch (vd SPX+ghn)
        desired = route_prov or (r.get("tracking_provider") or None)
        if r.get("carrier") == "Shopee Xpress" or r.get("buucuc") == "SPX":
            desired = "spx"
        elif r.get("carrier") == "J&T" or r.get("buucuc") == "J&T":
            desired = "jnt"
        elif r.get("carrier") == "GHN" or r.get("buucuc") == "GHN":
            desired = "ghn"
        url = None
        if desired and r.get("tracking_code"):
            url = build_tracking_url(
                str(r["tracking_code"]),
                provider=desired,
                tracking_code=str(r["tracking_code"]),
                carrier=r.get("carrier"),
                buucuc=r.get("buucuc"),
            )
        need_prov = desired and desired != (r.get("tracking_provider") or "")
        need_url = url and url != (r.get("tracking_url") or "")
        if not need_prov and not need_url:
            if desired:
                by_prov[desired] = by_prov.get(desired, 0) + 1
            continue
        item = {
            "van_tay": r.get("van_tay"),
            "so_noi_bo": r.get("so_noi_bo"),
            "tracking_code": r.get("tracking_code"),
            "provider_old": r.get("tracking_provider"),
            "provider_new": desired,
            "url_old": bool(r.get("tracking_url")),
            "url_new": url,
            "status": r.get("status"),
            "carrier": r.get("carrier"),
        }
        plan.append(item)
        if desired:
            by_prov[desired] = by_prov.get(desired, 0) + 1
        if apply:
            if need_prov:
                conn.execute(
                    "UPDATE orders SET tracking_provider = ? WHERE van_tay = ?",
                    (desired, r.get("van_tay")),
                )
                applied["provider"] += 1
            if need_url and url:
                conn.execute(
                    "UPDATE orders SET tracking_url = ?, tracking_ref = COALESCE(tracking_ref, ?) WHERE van_tay = ?",
                    (url, r.get("tracking_code"), r.get("van_tay")),
                )
                applied["url"] += 1
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "aship_url_sync",
                    r.get("van_tay"),
                    r.get("so_noi_bo"),
                    f"prov={r.get('tracking_provider')}→{desired}|url={'1' if url else '0'}",
                ),
            )
    if apply and (applied["url"] or applied["provider"]):
        conn.commit()

    missing_url = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ?
          AND tracking_code IS NOT NULL AND tracking_code != ''
          AND tracking_code != so_noi_bo
          AND (tracking_url IS NULL OR tracking_url = '')
        """,
        (wid,),
    ).fetchone()[0]
    with_url = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND ifnull(tracking_url,'') != ''
        """,
        (wid,),
    ).fetchone()[0]

    return {
        "query_type": "aship_url_sync",
        "query": wid,
        "hit": True,
        "count": len(plan),
        "scanned": len(rows),
        "apply": apply,
        "applied": applied,
        "with_url": with_url,
        "missing_url_real_trk": missing_url,
        "by_provider": [
            {"provider": k, "n": v} for k, v in sorted(by_prov.items(), key=lambda x: -x[1])
        ],
        "samples": plan[:12],
        "path": (
            f"aship_url_sync fix={len(plan)}/{len(rows)} apply={apply} "
            f"applied={applied} with_url={with_url} missing={missing_url}"
        ),
        "unmask_map": {
            "note": "aship URL ≠ unmask PII",
            "path_id": "PATH-CLEAR" if with_url else "PATH-MISSING",
        },
        "next": [
            "python3 scripts/order_pipe_reverse_query.py --hop8-apply",
            "python3 scripts/tracking_aship.py --probe  # nếu egress cho phép",
        ],
    }


def reverse_aship_probe_sample(
    conn: sqlite3.Connection, wid: str, *, limit: int = 6
) -> dict:
    """Probe nhẹ vài aship URL (HEAD/GET snippet) — secrets-only, không dump body."""
    try:
        from tracking_aship import probe_url
    except Exception as e:  # noqa: BLE001
        return {
            "query_type": "aship_probe",
            "query": wid,
            "hit": False,
            "error": str(e),
            "path": "aship_probe: module lỗi",
        }

    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, tracking_provider, tracking_url, carrier
            FROM orders
            WHERE warehouse_id = ?
              AND ifnull(tracking_url,'') != ''
              AND tracking_code != so_noi_bo
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, limit),
        )
    ]
    probes = []
    ok_n = 0
    for r in rows:
        url = r.get("tracking_url")
        try:
            pr = probe_url(str(url), timeout=8.0)
        except Exception as e:  # noqa: BLE001
            pr = {"ok": False, "error": str(e)}
        if pr.get("ok"):
            ok_n += 1
        probes.append(
            {
                "van_tay": r.get("van_tay"),
                "tracking_code": r.get("tracking_code"),
                "provider": r.get("tracking_provider"),
                "carrier": r.get("carrier"),
                "http": pr.get("http"),
                "ok": pr.get("ok"),
                "error": pr.get("error"),
                "snippet": (pr.get("snippet") or "")[:80],
            }
        )
    return {
        "query_type": "aship_probe",
        "query": wid,
        "hit": ok_n > 0,
        "count": len(probes),
        "ok": ok_n,
        "samples": probes,
        "path": f"aship_probe ok={ok_n}/{len(probes)}",
        "unmask_map": {"path_id": "PATH-CLEAR" if ok_n else "PATH-MISSING"},
        "next": ["Probe chỉ kiểm tra HTTP — không unmask PII"],
    }


def reverse_chain_asumee_hop8(
    conn: sqlite3.Connection,
    wid: str,
    *,
    apply: bool = False,
    probe: bool = False,
    probe_limit: int = 6,
) -> list[dict]:
    """Hop-8: aship URL sync, hard/soft gaps, ma trận 3PL, drill buucuc."""
    out: list[dict] = []

    gaps = reverse_hard_soft_gaps(conn, wid)
    out.append(gaps)
    for s in (gaps.get("samples") or [])[:4]:
        vt = s.get("van_tay")
        tr = s.get("tracking_code")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop8_hard_gap"
            out.append(r)
        if tr and tr != s.get("so_noi_bo"):
            r = reverse_by_tracking(conn, str(tr))
            r["gap_cohort"] = "hop8_hard_gap"
            out.append(r)

    out.append(reverse_3pl_completeness(conn, wid))

    sync = reverse_aship_url_sync(conn, wid, apply=apply, limit=900)
    out.append(sync)
    for s in (sync.get("samples") or [])[:5]:
        tr = s.get("tracking_code")
        if tr:
            r = reverse_by_tracking(conn, str(tr))
            r["gap_cohort"] = "hop8_aship_sync"
            out.append(r)

    # Drill top 3PL buucuc
    for (buu,) in conn.execute(
        """
        SELECT buucuc FROM orders
        WHERE warehouse_id = ? AND buucuc IN ('J&T','SPX','GHN')
        GROUP BY buucuc ORDER BY COUNT(*) DESC
        """,
        (wid,),
    ):
        out.append(reverse_by_buucuc(conn, str(buu), limit=10))

    # Sample reverse by real tracking per provider
    for prov, label in (("jnt", "J&T"), ("spx", "SPX"), ("ghn", "GHN")):
        rows = conn.execute(
            """
            SELECT tracking_code FROM orders
            WHERE warehouse_id = ? AND tracking_provider = ?
              AND tracking_code IS NOT NULL AND tracking_code != so_noi_bo
            ORDER BY piped_at DESC LIMIT 3
            """,
            (wid, prov),
        ).fetchall()
        for (tr,) in rows:
            r = reverse_by_tracking(conn, str(tr))
            r["gap_cohort"] = f"hop8_{label}"
            out.append(r)

    if probe:
        out.append(reverse_aship_probe_sample(conn, wid, limit=probe_limit))

    out.append(reverse_pipe_events_asumee(conn, wid))
    out.append(reverse_tracking_classify(conn, wid))
    return out


def reverse_pancake_id_cohort(conn: sqlite3.Connection, wid: str) -> dict:
    """Đơn còn tracking_code = so_noi_bo (chưa có mã 3PL)."""
    n = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND tracking_code = so_noi_bo
        """,
        (wid,),
    ).fetchone()[0]
    by_status = [
        dict(r)
        for r in conn.execute(
            """
            SELECT status, COUNT(*) AS orders FROM orders
            WHERE warehouse_id = ? AND tracking_code = so_noi_bo
            GROUP BY status ORDER BY orders DESC
            """,
            (wid,),
        )
    ]
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, province, ward,
                   carrier, buucuc, full_address
            FROM orders
            WHERE warehouse_id = ? AND tracking_code = so_noi_bo
            ORDER BY
              CASE status
                WHEN 'submitted' THEN 0
                WHEN 'returning' THEN 1
                WHEN 'new' THEN 2
                WHEN 'canceled' THEN 3
                ELSE 4 END,
              piped_at DESC
            LIMIT 15
            """,
            (wid,),
        )
    ]
    return {
        "query_type": "pancake_id_cohort",
        "query": wid,
        "hit": n > 0,
        "count": n,
        "by_status": by_status,
        "samples": samples,
        "path": f"pancake_id_cohort n={n} status×{len(by_status)}",
        "unmask_map": {
            "path_id": "PATH-MISSING",
            "action": "live_detail_for_submitted_returning_to_fetch_extend_code",
        },
        "next": [
            "python3 scripts/order_pipe_reverse_query.py --hop9-live --hop9-apply --hop9-limit 40",
            "Canceled/submitted thường chưa có extend_code — chấp nhận gap",
        ],
    }


def reverse_3pl_province_matrix(conn: sqlite3.Connection, wid: str) -> dict:
    """Ma trận buucuc 3PL → tỉnh nhận."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT buucuc, coalesce(nullif(province,''), '(∅)') AS province,
                   COUNT(*) AS orders,
                   SUM(CASE WHEN ifnull(picked_at,'')!='' THEN 1 ELSE 0 END) AS with_pick,
                   SUM(CASE WHEN ifnull(delivered_at,'')!='' THEN 1 ELSE 0 END) AS with_del
            FROM orders
            WHERE warehouse_id = ?
              AND buucuc IN ('J&T','SPX','GHN')
            GROUP BY buucuc, province
            ORDER BY orders DESC LIMIT 40
            """,
            (wid,),
        )
    ]
    return {
        "query_type": "three_pl_province",
        "query": wid,
        "hit": bool(rows),
        "count": len(rows),
        "matrix": rows,
        "path": f"three_pl_province rows={len(rows)}",
        "unmask_map": {"path_id": "PATH-CLEAR"},
    }


def reverse_status_flow_drill(conn: sqlite3.Connection, wid: str) -> list[dict]:
    """Drill ngược canceled / submitted / returning."""
    out: list[dict] = []
    for st in ("submitted", "returning", "canceled"):
        out.append(reverse_by_status_warehouse(conn, wid, st, limit=10))
        rows = conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code FROM orders
            WHERE warehouse_id = ? AND status = ?
            ORDER BY piped_at DESC LIMIT 3
            """,
            (wid, st),
        ).fetchall()
        for vt, so, tr in rows:
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = f"hop9_{st}"
                out.append(r)
            if tr and tr != so:
                r = reverse_by_tracking(conn, str(tr))
                r["gap_cohort"] = f"hop9_{st}"
                out.append(r)
            elif so:
                r = reverse_by_so_noi_bo(conn, str(so))
                r["gap_cohort"] = f"hop9_{st}"
                out.append(r)
    return out


def reverse_batch_pancake_id_backfill(
    conn: sqlite3.Connection,
    wid: str,
    *,
    limit: int = 40,
    apply: bool = False,
) -> dict:
    """Live detail cho đơn tracking=so (ưu tiên submitted/returning)."""
    targets = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, province, district,
                   picked_at, delivered_at, carrier, buucuc
            FROM orders
            WHERE warehouse_id = ?
              AND tracking_code = so_noi_bo
              AND so_noi_bo IS NOT NULL AND so_noi_bo != ''
              AND status IN ('submitted', 'returning', 'new', 'canceled', 'shipped', 'delivered')
            ORDER BY
              CASE status
                WHEN 'submitted' THEN 0
                WHEN 'returning' THEN 1
                WHEN 'new' THEN 2
                WHEN 'shipped' THEN 3
                WHEN 'delivered' THEN 4
                ELSE 5 END,
              piped_at DESC
            LIMIT ?
            """,
            (wid, limit),
        )
    ]
    probes = []
    applied = {
        "tracking": 0,
        "carrier": 0,
        "buucuc": 0,
        "picked_at": 0,
        "delivered_at": 0,
        "district": 0,
        "events": 0,
        "url": 0,
    }
    partners: dict[str, int] = {}
    ok_n = 0
    got_trk = 0
    for t in targets:
        oid = str(t.get("so_noi_bo") or "").strip()
        res = fetch_pancake_order_detail(oid)
        entry: dict[str, Any] = {
            "van_tay": t.get("van_tay"),
            "so_noi_bo": oid,
            "status_pipe": t.get("status"),
            "ok": res.get("ok"),
        }
        if not res.get("ok"):
            entry["error"] = res.get("error")
            probes.append(entry)
            continue
        ok_n += 1
        detail = res["data"]
        dist = extract_pancake_district(detail)
        tr = extract_pancake_tracking(detail)
        tl = map_pancake_histories_to_timeline(detail)
        route = map_partner_name_to_routing(
            tr.get("partner_name") or tl.get("partner_name"),
            provider=tr.get("provider"),
            tracking_code=tr.get("tracking_code"),
        )
        pn = route.get("partner_name") or tr.get("partner_name") or "(none)"
        partners[str(pn)] = partners.get(str(pn), 0) + 1
        has_real = bool(
            tr.get("tracking_code") and tr["tracking_code"] != oid
        )
        if has_real:
            got_trk += 1
        entry.update(
            {
                "district_api": dist,
                "tracking_api": tr.get("tracking_code"),
                "tracking_source": tr.get("source"),
                "tracking_link": tr.get("tracking_link"),
                "partner_name": pn,
                "provider": route.get("provider") or tr.get("provider"),
                "carrier_new": route.get("carrier"),
                "buucuc_new": route.get("buucuc"),
                "picked_at_api": tl.get("picked_at"),
                "delivered_at_api": tl.get("delivered_at"),
                "has_real_tracking": has_real,
            }
        )
        if apply:
            vt = t.get("van_tay")
            if dist and not t.get("district"):
                cur = conn.execute(
                    """
                    UPDATE orders SET district = ?
                    WHERE van_tay = ? AND (district IS NULL OR district = '')
                    """,
                    (dist, vt),
                )
                if cur.rowcount:
                    applied["district"] += 1
            if has_real:
                try:
                    from tracking_aship import build_tracking_url
                except Exception:  # noqa: BLE001
                    build_tracking_url = None  # type: ignore
                prov = route.get("provider") or tr.get("provider")
                url = tr.get("tracking_link")
                if build_tracking_url and prov and tr.get("tracking_code"):
                    url = build_tracking_url(
                        str(tr["tracking_code"]),
                        provider=str(prov),
                        tracking_code=str(tr["tracking_code"]),
                    ) or url
                cur = conn.execute(
                    """
                    UPDATE orders
                    SET tracking_code = ?, tracking_ref = ?,
                        tracking_provider = COALESCE(?, tracking_provider),
                        tracking_url = COALESCE(?, tracking_url)
                    WHERE van_tay = ?
                    """,
                    (
                        tr["tracking_code"],
                        tr["tracking_code"],
                        prov,
                        url,
                        vt,
                    ),
                )
                if cur.rowcount:
                    applied["tracking"] += 1
                    if url:
                        applied["url"] += 1
            if route.get("carrier"):
                cur = conn.execute(
                    """
                    UPDATE orders SET carrier = ?
                    WHERE van_tay = ?
                      AND (carrier IS NULL OR carrier = '' OR carrier = 'Pancake')
                    """,
                    (route["carrier"], vt),
                )
                if cur.rowcount:
                    applied["carrier"] += 1
            if route.get("buucuc"):
                cur = conn.execute(
                    """
                    UPDATE orders SET buucuc = ?
                    WHERE van_tay = ?
                      AND (buucuc IS NULL OR buucuc = '' OR buucuc = 'Pancake')
                    """,
                    (route["buucuc"], vt),
                )
                if cur.rowcount:
                    applied["buucuc"] += 1
            if tl.get("picked_at") and not t.get("picked_at"):
                cur = conn.execute(
                    """
                    UPDATE orders SET picked_at = ?
                    WHERE van_tay = ? AND (picked_at IS NULL OR picked_at = '')
                    """,
                    (tl["picked_at"], vt),
                )
                if cur.rowcount:
                    applied["picked_at"] += 1
            if tl.get("delivered_at") and t.get("status") == "delivered" and not t.get(
                "delivered_at"
            ):
                cur = conn.execute(
                    """
                    UPDATE orders SET delivered_at = ?
                    WHERE van_tay = ? AND (delivered_at IS NULL OR delivered_at = '')
                    """,
                    (tl["delivered_at"], vt),
                )
                if cur.rowcount:
                    applied["delivered_at"] += 1
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "hop9_pancake_id",
                    vt,
                    oid,
                    (
                        f"real={has_real}|trk={tr.get('tracking_code') or '∅'}|"
                        f"car={route.get('carrier') or '∅'}|st={t.get('status')}"
                    ),
                ),
            )
            applied["events"] += 1
        probes.append(entry)
    if apply:
        conn.commit()

    remain = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND tracking_code = so_noi_bo
        """,
        (wid,),
    ).fetchone()[0]

    return {
        "query_type": "pancake_id_backfill",
        "query": wid,
        "hit": ok_n > 0,
        "count": len(probes),
        "ok": ok_n,
        "got_real_tracking": got_trk,
        "apply": apply,
        "applied": applied,
        "remain_pancake_id": remain,
        "partners": [
            {"partner": k, "n": v} for k, v in sorted(partners.items(), key=lambda x: -x[1])
        ],
        "samples": probes[:15],
        "path": (
            f"pancake_id_backfill ok={ok_n}/{len(probes)} real_trk={got_trk} "
            f"apply={apply} applied={applied} remain={remain}"
        ),
        "unmask_map": {
            "note": "Submitted/canceled thường chưa có extend_code",
            "path_id": "PATH-CLEAR" if got_trk else "PATH-MISSING",
        },
        "next": [
            f"Còn {remain} đơn tracking=order_id — tăng --hop9-limit hoặc chờ ship",
        ],
    }


def reverse_chain_asumee_hop9(
    conn: sqlite3.Connection,
    wid: str,
    *,
    live: bool = False,
    apply: bool = False,
    limit: int = 40,
) -> list[dict]:
    """Hop-9: pancake-id cohort, district apply, 3PL×tỉnh, status flow, live backfill."""
    out: list[dict] = []

    cohort = reverse_pancake_id_cohort(conn, wid)
    out.append(cohort)
    for s in (cohort.get("samples") or [])[:4]:
        so = s.get("so_noi_bo")
        vt = s.get("van_tay")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop9_pancake_id"
            out.append(r)
        if so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "hop9_pancake_id"
            out.append(r)

    dist_plan = reverse_district_backfill_plan(conn, wid, apply=apply, limit=120)
    out.append(dist_plan)

    out.append(reverse_3pl_province_matrix(conn, wid))
    out.append(reverse_hard_soft_gaps(conn, wid))
    out.extend(reverse_status_flow_drill(conn, wid))

    # Top provinces from J&T → reverse
    for (prov,) in conn.execute(
        """
        SELECT province FROM orders
        WHERE warehouse_id = ? AND buucuc = 'J&T'
          AND province IS NOT NULL AND province != ''
        GROUP BY province ORDER BY COUNT(*) DESC LIMIT 4
        """,
        (wid,),
    ):
        out.append(reverse_by_province(conn, prov, limit=8))

    if live:
        batch = reverse_batch_pancake_id_backfill(
            conn, wid, limit=limit, apply=apply
        )
        out.append(batch)
        for s in (batch.get("samples") or [])[:5]:
            if not s.get("ok"):
                continue
            tr = s.get("tracking_api")
            vt = s.get("van_tay")
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = "hop9_live"
                out.append(r)
            if tr and s.get("has_real_tracking"):
                r = reverse_by_tracking(conn, str(tr))
                r["gap_cohort"] = "hop9_live"
                out.append(r)
        # sync aship after new tracking
        if apply:
            out.append(reverse_aship_url_sync(conn, wid, apply=True, limit=200))
            out.append(reverse_carrier_buucuc_remap(conn, wid, apply=True))
    else:
        out.append(
            {
                "query_type": "pancake_id_backfill",
                "query": wid,
                "hit": False,
                "skipped": True,
                "path": "pancake_id_backfill skipped (live=False)",
                "next": [
                    "python3 scripts/order_pipe_reverse_query.py --hop9-live --hop9-apply --hop9-limit 40"
                ],
            }
        )

    out.append(reverse_pancake_id_cohort(conn, wid))
    out.append(reverse_tracking_classify(conn, wid))
    out.append(reverse_pipe_events_asumee(conn, wid))
    out.append(reverse_3pl_completeness(conn, wid))
    return out


def reverse_soft_gap_accept(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False
) -> dict:
    """Đánh dấu soft-gap: delivered có delivered_at nhưng∅picked_at (SPX hist thiếu pick)."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, carrier, buucuc,
                   picked_at, delivered_at, tracking_provider, province
            FROM orders
            WHERE warehouse_id = ?
              AND status = 'delivered'
              AND delivered_at IS NOT NULL AND delivered_at != ''
              AND (picked_at IS NULL OR picked_at = '')
            ORDER BY piped_at DESC
            """,
            (wid,),
        )
    ]
    by_carrier: dict[str, int] = {}
    for r in rows:
        c = str(r.get("carrier") or "(none)")
        by_carrier[c] = by_carrier.get(c, 0) + 1
    applied = 0
    if apply and rows:
        for r in rows:
            # chỉ ghi event 1 lần / van_tay
            has = conn.execute(
                """
                SELECT 1 FROM pipe_events
                WHERE van_tay = ? AND event = 'soft_gap_accept' LIMIT 1
                """,
                (r.get("van_tay"),),
            ).fetchone()
            if has:
                continue
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "soft_gap_accept",
                    r.get("van_tay"),
                    r.get("so_noi_bo"),
                    (
                        f"PATH-ACCEPT|car={r.get('carrier')}|del={r.get('delivered_at')}|"
                        f"pick=∅|trk={r.get('tracking_code')}"
                    ),
                ),
            )
            applied += 1
        conn.commit()
    return {
        "query_type": "soft_gap_accept",
        "query": wid,
        "hit": bool(rows),
        "count": len(rows),
        "by_carrier": [
            {"carrier": k, "n": v} for k, v in sorted(by_carrier.items(), key=lambda x: -x[1])
        ],
        "apply": apply,
        "applied": applied,
        "samples": rows[:12],
        "path": (
            f"soft_gap_accept n={len(rows)} apply={apply} applied={applied} "
            f"by={by_carrier}"
        ),
        "unmask_map": {
            "path_id": "PATH-ACCEPT",
            "action": "accept_missing_pick_when_deliver_known_spx_no_histories",
            "note": "Không bịa picked_at; soft gap chủ yếu Shopee Xpress",
        },
        "next": [
            "Hard gap SPX ship/del không có timestamp — giữ PATH-MISSING",
            "Submitted pancake-id chờ ship rồi hop7 lại",
        ],
    }


def reverse_spx_marketplace_promote(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False
) -> dict:
    """Promote mã 26* (=so) → carrier/buucuc/provider/url SPX dù chưa có SPXVN."""
    try:
        from tracking_aship import build_tracking_url
    except Exception as e:  # noqa: BLE001
        return {
            "query_type": "spx_marketplace_promote",
            "query": wid,
            "hit": False,
            "error": str(e),
            "path": "spx_marketplace_promote: module lỗi",
        }

    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, carrier, buucuc,
                   tracking_provider, tracking_url
            FROM orders
            WHERE warehouse_id = ?
              AND tracking_code GLOB '26*' AND length(tracking_code) = 14
            ORDER BY piped_at DESC
            """,
            (wid,),
        )
    ]
    plan = []
    applied = {"carrier": 0, "buucuc": 0, "provider": 0, "url": 0, "events": 0}
    for r in rows:
        code = str(r.get("tracking_code") or "")
        url = build_tracking_url(code, provider="spx", tracking_code=code)
        need_car = (r.get("carrier") or "Pancake") in {"", "Pancake", None} or r.get(
            "carrier"
        ) != "Shopee Xpress"
        # only promote if not already fully SPX
        need_buu = (r.get("buucuc") or "Pancake") in {"", "Pancake", None} or r.get(
            "buucuc"
        ) != "SPX"
        need_prov = (r.get("tracking_provider") or "") != "spx"
        need_url = not r.get("tracking_url") or "provider=spx" not in str(
            r.get("tracking_url")
        )
        if not (need_car or need_buu or need_prov or need_url):
            continue
        item = {
            "van_tay": r.get("van_tay"),
            "so_noi_bo": r.get("so_noi_bo"),
            "tracking_code": code,
            "status": r.get("status"),
            "carrier_old": r.get("carrier"),
            "buucuc_old": r.get("buucuc"),
            "url": url,
        }
        plan.append(item)
        if apply:
            vt = r.get("van_tay")
            if need_car:
                conn.execute(
                    "UPDATE orders SET carrier = 'Shopee Xpress' WHERE van_tay = ?",
                    (vt,),
                )
                applied["carrier"] += 1
            if need_buu:
                conn.execute(
                    "UPDATE orders SET buucuc = 'SPX' WHERE van_tay = ?",
                    (vt,),
                )
                applied["buucuc"] += 1
            if need_prov:
                conn.execute(
                    "UPDATE orders SET tracking_provider = 'spx' WHERE van_tay = ?",
                    (vt,),
                )
                applied["provider"] += 1
            if need_url and url:
                conn.execute(
                    """
                    UPDATE orders
                    SET tracking_url = ?, tracking_ref = COALESCE(tracking_ref, ?)
                    WHERE van_tay = ?
                    """,
                    (url, code, vt),
                )
                applied["url"] += 1
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "spx_marketplace_promote",
                    vt,
                    r.get("so_noi_bo"),
                    f"26*=so|car→SPX|url=1|st={r.get('status')}",
                ),
            )
            applied["events"] += 1
    if apply and applied["events"]:
        conn.commit()
    return {
        "query_type": "spx_marketplace_promote",
        "query": wid,
        "hit": bool(plan) or True,
        "count": len(plan),
        "scanned": len(rows),
        "apply": apply,
        "applied": applied,
        "samples": plan[:12],
        "path": (
            f"spx_marketplace_promote candidates={len(plan)}/{len(rows)} "
            f"apply={apply} applied={applied}"
        ),
        "unmask_map": {
            "path_id": "PATH-CLEAR",
            "note": "Mã 26* marketplace id dùng làm ref aship spx trước khi có SPXVN",
        },
        "next": [
            "Khi ship, hop7 sẽ thay bằng SPXVN… nếu partner.extend_code khác",
        ],
    }


def reverse_flow_completeness(conn: sqlite3.Connection, wid: str) -> dict:
    """Điểm đầy đủ dòng chảy ASUMEE sau hop1–9."""
    row = dict(
        conn.execute(
            """
            SELECT
              COUNT(*) AS orders,
              SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != ''
                        AND tracking_code != so_noi_bo THEN 1 ELSE 0 END) AS trk_real,
              SUM(CASE WHEN tracking_code GLOB '26*' AND length(tracking_code)=14
                        THEN 1 ELSE 0 END) AS spx_market_id,
              SUM(CASE WHEN ifnull(tracking_url,'') != '' THEN 1 ELSE 0 END) AS with_url,
              SUM(CASE WHEN ifnull(picked_at,'') != '' THEN 1 ELSE 0 END) AS with_pick,
              SUM(CASE WHEN ifnull(delivered_at,'') != '' THEN 1 ELSE 0 END) AS with_del,
              SUM(CASE WHEN ifnull(district,'') != '' THEN 1 ELSE 0 END) AS with_district,
              SUM(CASE WHEN ifnull(province,'') != '' THEN 1 ELSE 0 END) AS with_province,
              SUM(CASE WHEN buucuc IN ('J&T','SPX','GHN') THEN 1 ELSE 0 END) AS with_3pl,
              SUM(CASE WHEN buucuc = 'Pancake' THEN 1 ELSE 0 END) AS still_pancake
            FROM orders WHERE warehouse_id = ?
            """,
            (wid,),
        ).fetchone()
    )
    n = max(int(row.get("orders") or 1), 1)
    scores = {
        "trk_real_pct": round(100 * int(row.get("trk_real") or 0) / n, 1),
        "url_pct": round(100 * int(row.get("with_url") or 0) / n, 1),
        "pick_pct": round(100 * int(row.get("with_pick") or 0) / n, 1),
        "del_pct": round(100 * int(row.get("with_del") or 0) / n, 1),
        "district_pct": round(100 * int(row.get("with_district") or 0) / n, 1),
        "three_pl_pct": round(100 * int(row.get("with_3pl") or 0) / n, 1),
    }
    return {
        "query_type": "flow_completeness",
        "query": wid,
        "hit": True,
        "count": row.get("orders"),
        "fills": row,
        "scores": scores,
        "path": (
            f"flow_completeness n={row.get('orders')} "
            f"3pl={scores['three_pl_pct']}% url={scores['url_pct']}% "
            f"pick={scores['pick_pct']}% del={scores['del_pct']}%"
        ),
        "unmask_map": {"path_id": "PATH-CLEAR"},
        "next": [
            "pancake_id submitted/canceled → chờ ship hoặc accept",
            "soft_gap SPX → PATH-ACCEPT",
        ],
    }


def reverse_chain_asumee_hop10(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False
) -> list[dict]:
    """Hop-10: soft-gap accept, promote SPX marketplace id, completeness, drill."""
    out: list[dict] = []

    out.append(reverse_flow_completeness(conn, wid))
    out.append(reverse_hard_soft_gaps(conn, wid))

    soft = reverse_soft_gap_accept(conn, wid, apply=apply)
    out.append(soft)
    for s in (soft.get("samples") or [])[:4]:
        vt = s.get("van_tay")
        tr = s.get("tracking_code")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop10_soft_accept"
            out.append(r)
        if tr:
            r = reverse_by_tracking(conn, str(tr))
            r["gap_cohort"] = "hop10_soft_accept"
            out.append(r)

    promo = reverse_spx_marketplace_promote(conn, wid, apply=apply)
    out.append(promo)
    for s in (promo.get("samples") or [])[:4]:
        tr = s.get("tracking_code")
        vt = s.get("van_tay")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop10_spx_market"
            out.append(r)
        if tr:
            r = reverse_by_tracking(conn, str(tr))
            r["gap_cohort"] = "hop10_spx_market"
            out.append(r)

    # Returning with real 3PL
    out.append(reverse_by_status_warehouse(conn, wid, "returning", limit=12))
    for (tr,) in conn.execute(
        """
        SELECT tracking_code FROM orders
        WHERE warehouse_id = ? AND status = 'returning'
          AND tracking_code IS NOT NULL AND tracking_code != so_noi_bo
        ORDER BY piped_at DESC LIMIT 4
        """,
        (wid,),
    ):
        r = reverse_by_tracking(conn, str(tr))
        r["gap_cohort"] = "hop10_returning"
        out.append(r)

    # Canceled pancake-id accept note
    canceled_n = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND status = 'canceled' AND tracking_code = so_noi_bo
        """,
        (wid,),
    ).fetchone()[0]
    out.append(
        {
            "query_type": "canceled_pancake_id",
            "query": wid,
            "hit": canceled_n > 0,
            "count": canceled_n,
            "path": f"canceled_pancake_id n={canceled_n} (thường không có 3PL)",
            "unmask_map": {
                "path_id": "PATH-ACCEPT",
                "action": "accept_canceled_without_3pl_tracking",
            },
            "next": ["Canceled giữ order_id — không ép extend_code"],
        }
    )

    out.append(reverse_3pl_completeness(conn, wid))
    out.append(reverse_flow_completeness(conn, wid))
    out.append(reverse_pipe_events_asumee(conn, wid))
    out.append(reverse_tracking_classify(conn, wid))
    out.append(reverse_hard_soft_gaps(conn, wid))
    return out


def reverse_submitted_waiting(conn: sqlite3.Connection, wid: str) -> dict:
    """Submitted pancake-id chờ ship — cohort theo tỉnh."""
    n = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND status = 'submitted' AND tracking_code = so_noi_bo
        """,
        (wid,),
    ).fetchone()[0]
    by_prov = [
        dict(r)
        for r in conn.execute(
            """
            SELECT coalesce(province,'(∅)') AS province, COUNT(*) AS orders
            FROM orders
            WHERE warehouse_id = ? AND status = 'submitted' AND tracking_code = so_noi_bo
            GROUP BY 1 ORDER BY orders DESC LIMIT 15
            """,
            (wid,),
        )
    ]
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, province, ward,
                   carrier, buucuc, created_at, piped_at
            FROM orders
            WHERE warehouse_id = ? AND status = 'submitted' AND tracking_code = so_noi_bo
            ORDER BY piped_at DESC LIMIT 12
            """,
            (wid,),
        )
    ]
    return {
        "query_type": "submitted_waiting",
        "query": wid,
        "hit": n > 0,
        "count": n,
        "by_province": by_prov,
        "samples": samples,
        "path": f"submitted_waiting pancake-id n={n} tỉnh×{len(by_prov)}",
        "unmask_map": {
            "path_id": "PATH-WAIT",
            "action": "wait_ship_then_hop7_for_extend_code",
        },
        "next": [
            "Khi status→shipped: --hop7-apply --hop7-limit 200",
            "Không ép SPXVN khi partner.extend_code còn trống",
        ],
    }


def reverse_returning_cohort(conn: sqlite3.Connection, wid: str) -> dict:
    """Returning ASUMEE — pancake-id vs SPX marketplace."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, carrier, buucuc,
                   province, ward, tracking_provider, tracking_url,
                   picked_at, delivered_at
            FROM orders
            WHERE warehouse_id = ? AND status = 'returning'
            ORDER BY piped_at DESC
            """,
            (wid,),
        )
    ]
    by_kind: dict[str, int] = {}
    for r in rows:
        tr = str(r.get("tracking_code") or "")
        so = str(r.get("so_noi_bo") or "")
        buu = str(r.get("buucuc") or "")
        if buu == "SPX" or tr.upper().startswith("SPX") or (
            tr.startswith("26") and len(tr) == 14
        ):
            kind = "spx_market_id" if tr == so else "spx"
        elif buu == "J&T" or tr.startswith("86"):
            kind = "jnt"
        elif buu == "GHN" or tr.upper().startswith(("GHN", "VNGH")):
            kind = "ghn"
        elif tr and tr == so:
            kind = "pancake_id"
        else:
            kind = "other"
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "query_type": "returning_cohort",
        "query": wid,
        "hit": bool(rows),
        "count": len(rows),
        "by_kind": [{"kind": k, "n": v} for k, v in sorted(by_kind.items(), key=lambda x: -x[1])],
        "samples": rows[:12],
        "path": f"returning_cohort n={len(rows)} kinds={by_kind}",
        "unmask_map": {
            "path_id": "PATH-CLEAR",
            "action": "monitor_returning_3pl_and_pancake_id",
        },
        "next": [
            "Returning SPX: giữ URL; pancake-id chờ extend_code",
        ],
    }


def reverse_hard_gap_refetch(
    conn: sqlite3.Connection,
    wid: str,
    *,
    limit: int = 40,
    apply: bool = False,
) -> dict:
    """Live detail chỉ hard-gap (delivered∅del / shipped∅pick) — không bịa timestamp."""
    targets = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, province, district,
                   picked_at, delivered_at, carrier, buucuc
            FROM orders
            WHERE warehouse_id = ?
              AND so_noi_bo IS NOT NULL AND so_noi_bo != ''
              AND (
                (status = 'delivered' AND (delivered_at IS NULL OR delivered_at = ''))
                OR (status = 'shipped' AND (picked_at IS NULL OR picked_at = ''))
              )
            ORDER BY
              CASE WHEN status = 'delivered' THEN 0 ELSE 1 END,
              piped_at DESC
            LIMIT ?
            """,
            (wid, limit),
        )
    ]
    probes: list[dict] = []
    applied = {
        "picked_at": 0,
        "delivered_at": 0,
        "tracking": 0,
        "district": 0,
        "carrier": 0,
        "buucuc": 0,
        "events": 0,
    }
    ok_n = 0
    got_pick = 0
    got_del = 0
    empty_hist = 0
    partners: dict[str, int] = {}
    for t in targets:
        oid = str(t.get("so_noi_bo") or "").strip()
        res = fetch_pancake_order_detail(oid)
        entry: dict[str, Any] = {
            "van_tay": t.get("van_tay"),
            "so_noi_bo": oid,
            "status_pipe": t.get("status"),
            "tracking_pipe": t.get("tracking_code"),
            "ok": res.get("ok"),
        }
        if not res.get("ok"):
            entry["error"] = res.get("error")
            probes.append(entry)
            continue
        ok_n += 1
        detail = res["data"]
        dist = extract_pancake_district(detail)
        tr = extract_pancake_tracking(detail)
        tl = map_pancake_histories_to_timeline(detail)
        route = map_partner_name_to_routing(
            tr.get("partner_name") or tl.get("partner_name"),
            provider=tr.get("provider"),
            tracking_code=tr.get("tracking_code"),
        )
        pn = route.get("partner_name") or tr.get("partner_name") or "(none)"
        partners[str(pn)] = partners.get(str(pn), 0) + 1
        hist = detail.get("histories") if isinstance(detail.get("histories"), list) else []
        partner = detail.get("partner") if isinstance(detail.get("partner"), dict) else {}
        ext = partner.get("extend_update") if isinstance(partner, dict) else None
        if not hist and not ext:
            empty_hist += 1
        if tl.get("picked_at"):
            got_pick += 1
        if tl.get("delivered_at"):
            got_del += 1
        entry.update(
            {
                "district_api": dist,
                "tracking_api": tr.get("tracking_code"),
                "partner_name": pn,
                "carrier_new": route.get("carrier"),
                "buucuc_new": route.get("buucuc"),
                "picked_at_api": tl.get("picked_at"),
                "delivered_at_api": tl.get("delivered_at"),
                "timeline_signals": tl.get("signals"),
                "hist_n": len(hist) if isinstance(hist, list) else 0,
                "extend_n": len(ext) if isinstance(ext, list) else 0,
            }
        )
        if apply:
            vt = t.get("van_tay")
            if dist and not t.get("district"):
                cur = conn.execute(
                    """
                    UPDATE orders SET district = ?
                    WHERE van_tay = ? AND (district IS NULL OR district = '')
                    """,
                    (dist, vt),
                )
                if cur.rowcount:
                    applied["district"] += 1
            if tr.get("tracking_code") and tr["tracking_code"] != t.get("tracking_code"):
                cur = conn.execute(
                    """
                    UPDATE orders
                    SET tracking_code = ?, tracking_ref = ?,
                        tracking_provider = COALESCE(?, tracking_provider)
                    WHERE van_tay = ?
                    """,
                    (
                        tr["tracking_code"],
                        tr["tracking_code"],
                        route.get("provider") or tr.get("provider"),
                        vt,
                    ),
                )
                if cur.rowcount:
                    applied["tracking"] += 1
            if route.get("carrier"):
                cur = conn.execute(
                    """
                    UPDATE orders SET carrier = ?
                    WHERE van_tay = ?
                      AND (carrier IS NULL OR carrier = '' OR carrier = 'Pancake')
                    """,
                    (route["carrier"], vt),
                )
                if cur.rowcount:
                    applied["carrier"] += 1
            if route.get("buucuc"):
                cur = conn.execute(
                    """
                    UPDATE orders SET buucuc = ?
                    WHERE van_tay = ?
                      AND (buucuc IS NULL OR buucuc = '' OR buucuc = 'Pancake')
                    """,
                    (route["buucuc"], vt),
                )
                if cur.rowcount:
                    applied["buucuc"] += 1
            if tl.get("picked_at") and not t.get("picked_at"):
                cur = conn.execute(
                    """
                    UPDATE orders SET picked_at = ?
                    WHERE van_tay = ? AND (picked_at IS NULL OR picked_at = '')
                    """,
                    (tl["picked_at"], vt),
                )
                if cur.rowcount:
                    applied["picked_at"] += 1
            if t.get("status") == "delivered" and tl.get("delivered_at") and not t.get(
                "delivered_at"
            ):
                cur = conn.execute(
                    """
                    UPDATE orders SET delivered_at = ?
                    WHERE van_tay = ? AND (delivered_at IS NULL OR delivered_at = '')
                    """,
                    (tl["delivered_at"], vt),
                )
                if cur.rowcount:
                    applied["delivered_at"] += 1
            elif t.get("status") == "shipped" and tl.get("delivered_at"):
                cur = conn.execute(
                    """
                    UPDATE orders
                    SET delivered_at = COALESCE(delivered_at, ?),
                        status = 'delivered',
                        picked_at = COALESCE(picked_at, ?)
                    WHERE van_tay = ? AND status = 'shipped'
                    """,
                    (tl["delivered_at"], tl.get("picked_at"), vt),
                )
                if cur.rowcount:
                    applied["delivered_at"] += 1
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "hop11_hard_refetch",
                    vt,
                    oid,
                    (
                        f"st={t.get('status')}|trk={tr.get('tracking_code') or '∅'}|"
                        f"pick={tl.get('picked_at') or '∅'}|del={tl.get('delivered_at') or '∅'}|"
                        f"hist={entry.get('hist_n')}|ext={entry.get('extend_n')}"
                    ),
                ),
            )
            applied["events"] += 1
        probes.append(entry)
    if apply and applied["events"]:
        conn.commit()
    remain = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ?
          AND (
            (status = 'delivered' AND (delivered_at IS NULL OR delivered_at = ''))
            OR (status = 'shipped' AND (picked_at IS NULL OR picked_at = ''))
          )
        """,
        (wid,),
    ).fetchone()[0]
    return {
        "query_type": "hard_gap_refetch",
        "query": wid,
        "hit": ok_n > 0 or bool(targets),
        "count": len(targets),
        "ok": ok_n,
        "got_pick": got_pick,
        "got_del": got_del,
        "empty_hist_or_extend": empty_hist,
        "apply": apply,
        "applied": applied,
        "remain_hard": remain,
        "partners": [
            {"partner": k, "n": v} for k, v in sorted(partners.items(), key=lambda x: -x[1])
        ],
        "samples": probes[:12],
        "path": (
            f"hard_gap_refetch ok={ok_n}/{len(targets)} pick={got_pick} del={got_del} "
            f"empty_hist={empty_hist} apply={apply} remain={remain}"
        ),
        "unmask_map": {
            "path_id": "PATH-MISSING" if remain else "PATH-CLEAR",
            "action": "refetch_hard_gap_owned_detail_no_invent",
            "note": "SPX thường hist=0/extend=∅ — không bịa pick/del",
        },
        "next": [
            "Hard còn lại → hard_gap_accept PATH-MISSING",
            "Soft gap đã PATH-ACCEPT ở hop10",
        ],
    }


def reverse_hard_gap_accept(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False
) -> dict:
    """Đánh dấu hard-gap còn lại PATH-MISSING (không bịa timestamp)."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, carrier, buucuc,
                   picked_at, delivered_at, tracking_provider, province
            FROM orders
            WHERE warehouse_id = ?
              AND (
                (status = 'delivered' AND (delivered_at IS NULL OR delivered_at = ''))
                OR (status = 'shipped' AND (picked_at IS NULL OR picked_at = ''))
              )
            ORDER BY piped_at DESC
            """,
            (wid,),
        )
    ]
    by_carrier: dict[str, int] = {}
    for r in rows:
        c = str(r.get("carrier") or "(none)")
        by_carrier[c] = by_carrier.get(c, 0) + 1
    applied = 0
    if apply and rows:
        for r in rows:
            has = conn.execute(
                """
                SELECT 1 FROM pipe_events
                WHERE van_tay = ? AND event = 'hard_gap_accept' LIMIT 1
                """,
                (r.get("van_tay"),),
            ).fetchone()
            if has:
                continue
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "hard_gap_accept",
                    r.get("van_tay"),
                    r.get("so_noi_bo"),
                    (
                        f"PATH-MISSING|st={r.get('status')}|car={r.get('carrier')}|"
                        f"pick={r.get('picked_at') or '∅'}|del={r.get('delivered_at') or '∅'}|"
                        f"trk={r.get('tracking_code')}"
                    ),
                ),
            )
            applied += 1
        conn.commit()
    return {
        "query_type": "hard_gap_accept",
        "query": wid,
        "hit": bool(rows),
        "count": len(rows),
        "by_carrier": [
            {"carrier": k, "n": v} for k, v in sorted(by_carrier.items(), key=lambda x: -x[1])
        ],
        "apply": apply,
        "applied": applied,
        "samples": rows[:12],
        "path": (
            f"hard_gap_accept n={len(rows)} apply={apply} applied={applied} "
            f"by={by_carrier}"
        ),
        "unmask_map": {
            "path_id": "PATH-MISSING",
            "action": "accept_hard_gap_no_histories_no_invent_timestamp",
            "note": "Chủ yếu SPX hist/extend trống",
        },
        "next": [
            "Chờ partner webhook / aship status nếu cần audit ngoài",
            "Submitted pancake-id → hop7 sau khi ship",
        ],
    }


def reverse_commune_district_apply(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False, limit: int = 200
) -> dict:
    """Backfill district từ hint address; ward-only (mô hình xã) → PATH-ACCEPT."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, status, province, ward, full_address, district
            FROM orders
            WHERE warehouse_id = ?
              AND (district IS NULL OR district = '')
              AND full_address IS NOT NULL AND full_address != ''
            ORDER BY piped_at DESC
            LIMIT ?
            """,
            (wid, limit),
        )
    ]
    plan = []
    applied = {"district": 0, "events": 0}
    for r in rows:
        hint = _district_hint_from_address(str(r.get("full_address") or ""))
        if not hint:
            continue
        plan.append(
            {
                "van_tay": r.get("van_tay"),
                "so_noi_bo": r.get("so_noi_bo"),
                "province": r.get("province"),
                "ward": r.get("ward"),
                "hint_district": hint,
                "status": r.get("status"),
            }
        )
        if apply:
            vt = r.get("van_tay")
            cur = conn.execute(
                """
                UPDATE orders SET district = ?
                WHERE van_tay = ? AND (district IS NULL OR district = '')
                """,
                (hint, vt),
            )
            if cur.rowcount:
                applied["district"] += 1
                conn.execute(
                    "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                    (
                        utc_now(),
                        "commune_district_hint",
                        vt,
                        r.get("so_noi_bo"),
                        f"district←hint:{hint}|ward={r.get('ward') or '∅'}",
                    ),
                )
                applied["events"] += 1
    if apply and applied["events"]:
        conn.commit()

    ward_only = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ?
          AND (district IS NULL OR district = '')
          AND ward IS NOT NULL AND ward != ''
        """,
        (wid,),
    ).fetchone()[0]
    accept_applied = 0
    if apply and ward_only:
        # snapshot accept 1 lần kho (không spam per-order hàng trăm)
        has = conn.execute(
            """
            SELECT 1 FROM pipe_events
            WHERE event = 'commune_geo_accept' AND so_noi_bo = ? LIMIT 1
            """,
            (wid,),
        ).fetchone()
        if not has:
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "commune_geo_accept",
                    None,
                    wid,
                    f"PATH-ACCEPT|ward_without_district={ward_only}|vn_commune_model",
                ),
            )
            conn.commit()
            accept_applied = 1

    filled = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND district IS NOT NULL AND district != ''
        """,
        (wid,),
    ).fetchone()[0]
    return {
        "query_type": "commune_district_apply",
        "query": wid,
        "hit": True,
        "count": len(plan),
        "scanned": len(rows),
        "ward_without_district": ward_only,
        "with_district": filled,
        "apply": apply,
        "applied": applied,
        "commune_accept_event": accept_applied,
        "samples": plan[:12],
        "path": (
            f"commune_district_apply hints={len(plan)}/{len(rows)} "
            f"apply={apply} applied={applied} ward_only={ward_only}"
        ),
        "unmask_map": {
            "path_id": "PATH-ACCEPT",
            "action": "hint_district_or_accept_ward_only_commune_model",
            "note": "Pancake district_name thường null sau cải cách xã/phường",
        },
        "next": [
            "Ward-only là chuẩn mới — không ép huyện nếu address không có Huyện/Quận",
        ],
    }


def reverse_chain_asumee_hop11(
    conn: sqlite3.Connection,
    wid: str,
    *,
    live: bool = False,
    apply: bool = False,
    limit: int = 40,
) -> list[dict]:
    """Hop-11: hard-gap refetch/accept, submitted/returning, commune geo."""
    out: list[dict] = []

    out.append(reverse_flow_completeness(conn, wid))
    out.append(reverse_hard_soft_gaps(conn, wid))
    out.append(reverse_submitted_waiting(conn, wid))
    wait = out[-1]
    for s in (wait.get("samples") or [])[:3]:
        vt = s.get("van_tay")
        so = s.get("so_noi_bo")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop11_submitted_wait"
            out.append(r)
        if so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "hop11_submitted_wait"
            out.append(r)
        prov = s.get("province")
        if prov:
            out.append(reverse_by_province(conn, str(prov), limit=6))

    ret = reverse_returning_cohort(conn, wid)
    out.append(ret)
    for s in (ret.get("samples") or [])[:4]:
        vt = s.get("van_tay")
        tr = s.get("tracking_code")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop11_returning"
            out.append(r)
        if tr and tr != s.get("so_noi_bo"):
            r = reverse_by_tracking(conn, str(tr))
            r["gap_cohort"] = "hop11_returning"
            out.append(r)

    if live:
        refetch = reverse_hard_gap_refetch(conn, wid, limit=limit, apply=apply)
        out.append(refetch)
        for s in (refetch.get("samples") or [])[:6]:
            if not s.get("ok"):
                continue
            vt = s.get("van_tay")
            tr = s.get("tracking_api") or s.get("tracking_pipe")
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = "hop11_hard_refetch"
                out.append(r)
            if tr:
                r = reverse_by_tracking(conn, str(tr))
                r["gap_cohort"] = "hop11_hard_refetch"
                out.append(r)
    else:
        out.append(
            {
                "query_type": "hard_gap_refetch",
                "query": wid,
                "hit": False,
                "skipped": True,
                "path": "hard_gap_refetch skipped (live=False)",
                "next": [
                    "python3 scripts/order_pipe_reverse_query.py --continue-flow --hop11-live --hop11-apply"
                ],
            }
        )

    hard_acc = reverse_hard_gap_accept(conn, wid, apply=apply)
    out.append(hard_acc)
    for s in (hard_acc.get("samples") or [])[:4]:
        vt = s.get("van_tay")
        tr = s.get("tracking_code")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop11_hard_accept"
            out.append(r)
        if tr:
            r = reverse_by_tracking(conn, str(tr))
            r["gap_cohort"] = "hop11_hard_accept"
            out.append(r)

    geo = reverse_commune_district_apply(conn, wid, apply=apply, limit=200)
    out.append(geo)
    for s in (geo.get("samples") or [])[:4]:
        hint = s.get("hint_district")
        vt = s.get("van_tay")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop11_commune_geo"
            out.append(r)
        if hint:
            out.append(reverse_by_address(conn, str(hint), limit=5))

    out.append(reverse_district_recover(conn, wid))
    out.append(reverse_3pl_completeness(conn, wid))
    out.append(reverse_flow_completeness(conn, wid))
    out.append(reverse_pipe_events_asumee(conn, wid))
    out.append(reverse_hard_soft_gaps(conn, wid))
    return out


def reverse_open_path_scorecard(conn: sqlite3.Connection, wid: str) -> dict:
    """Tồn dư đường mở sau hop1–11."""
    row = dict(
        conn.execute(
            """
            SELECT
              SUM(CASE WHEN status='submitted' AND tracking_code=so_noi_bo THEN 1 ELSE 0 END) AS submitted_wait,
              SUM(CASE WHEN status='new' AND tracking_code=so_noi_bo THEN 1 ELSE 0 END) AS new_wait,
              SUM(CASE WHEN status='returning' AND tracking_code=so_noi_bo THEN 1 ELSE 0 END) AS returning_pancake_id,
              SUM(CASE WHEN status='returning' AND tracking_code!=so_noi_bo THEN 1 ELSE 0 END) AS returning_real_trk,
              SUM(CASE WHEN status='canceled' AND tracking_code=so_noi_bo THEN 1 ELSE 0 END) AS canceled_pancake_id,
              SUM(CASE WHEN status='delivered' AND ifnull(delivered_at,'')='' THEN 1 ELSE 0 END) AS hard_del,
              SUM(CASE WHEN status='shipped' AND ifnull(picked_at,'')='' THEN 1 ELSE 0 END) AS hard_ship,
              SUM(CASE WHEN status='delivered' AND ifnull(delivered_at,'')!='' AND ifnull(picked_at,'')='' THEN 1 ELSE 0 END) AS soft_del,
              SUM(CASE WHEN ifnull(district,'')='' AND ifnull(ward,'')!='' THEN 1 ELSE 0 END) AS ward_only,
              COUNT(*) AS orders
            FROM orders WHERE warehouse_id = ?
            """,
            (wid,),
        ).fetchone()
    )
    paths = {
        "PATH-WAIT": int(row.get("submitted_wait") or 0) + int(row.get("new_wait") or 0),
        "PATH-MISSING": int(row.get("hard_del") or 0) + int(row.get("hard_ship") or 0),
        "PATH-ACCEPT-soft": int(row.get("soft_del") or 0),
        "PATH-ACCEPT-commune": int(row.get("ward_only") or 0),
        "PATH-ACCEPT-canceled": int(row.get("canceled_pancake_id") or 0),
        "returning_need_trk": int(row.get("returning_pancake_id") or 0),
        "returning_real_trk": int(row.get("returning_real_trk") or 0),
    }
    return {
        "query_type": "open_path_scorecard",
        "query": wid,
        "hit": True,
        "count": row.get("orders"),
        "fills": row,
        "paths": paths,
        "path": (
            f"open_path_scorecard wait={paths['PATH-WAIT']} "
            f"hard={paths['PATH-MISSING']} soft={paths['PATH-ACCEPT-soft']} "
            f"returning_id={paths['returning_need_trk']} "
            f"returning_trk={paths['returning_real_trk']}"
        ),
        "unmask_map": {"path_id": "PATH-CLEAR"},
        "next": [
            "Returning pancake-id → hop12 live backfill (thường đã có extend_code)",
            "Submitted wait → hop7 sau khi ship",
        ],
    }


def reverse_waiting_live_backfill(
    conn: sqlite3.Connection,
    wid: str,
    *,
    limit: int = 40,
    apply: bool = False,
) -> dict:
    """Live detail ưu tiên returning/new/submitted — lấy extend_code + timeline.

    Returning: ghi tracking/pick; delivered_at = mốc giao lần đầu (giữ status=returning).
    """
    targets = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, province, district,
                   picked_at, delivered_at, carrier, buucuc
            FROM orders
            WHERE warehouse_id = ?
              AND tracking_code = so_noi_bo
              AND so_noi_bo IS NOT NULL AND so_noi_bo != ''
              AND status IN ('returning', 'new', 'submitted')
            ORDER BY
              CASE status
                WHEN 'returning' THEN 0
                WHEN 'new' THEN 1
                WHEN 'submitted' THEN 2
                ELSE 3 END,
              piped_at DESC
            LIMIT ?
            """,
            (wid, limit),
        )
    ]
    probes: list[dict] = []
    applied = {
        "tracking": 0,
        "carrier": 0,
        "buucuc": 0,
        "picked_at": 0,
        "delivered_at": 0,
        "district": 0,
        "url": 0,
        "events": 0,
    }
    partners: dict[str, int] = {}
    ok_n = 0
    got_trk = 0
    for t in targets:
        oid = str(t.get("so_noi_bo") or "").strip()
        res = fetch_pancake_order_detail(oid)
        entry: dict[str, Any] = {
            "van_tay": t.get("van_tay"),
            "so_noi_bo": oid,
            "status_pipe": t.get("status"),
            "ok": res.get("ok"),
        }
        if not res.get("ok"):
            entry["error"] = res.get("error")
            probes.append(entry)
            continue
        ok_n += 1
        detail = res["data"]
        dist = extract_pancake_district(detail)
        tr = extract_pancake_tracking(detail)
        tl = map_pancake_histories_to_timeline(detail)
        route = map_partner_name_to_routing(
            tr.get("partner_name") or tl.get("partner_name"),
            provider=tr.get("provider"),
            tracking_code=tr.get("tracking_code"),
        )
        pn = route.get("partner_name") or tr.get("partner_name") or "(none)"
        partners[str(pn)] = partners.get(str(pn), 0) + 1
        has_real = bool(tr.get("tracking_code") and tr["tracking_code"] != oid)
        if has_real:
            got_trk += 1
        entry.update(
            {
                "district_api": dist,
                "tracking_api": tr.get("tracking_code"),
                "tracking_source": tr.get("source"),
                "partner_name": pn,
                "provider": route.get("provider") or tr.get("provider"),
                "carrier_new": route.get("carrier"),
                "buucuc_new": route.get("buucuc"),
                "picked_at_api": tl.get("picked_at"),
                "delivered_at_api": tl.get("delivered_at"),
                "has_real_tracking": has_real,
                "status_api": detail.get("status"),
            }
        )
        if apply:
            vt = t.get("van_tay")
            if dist and not t.get("district"):
                cur = conn.execute(
                    """
                    UPDATE orders SET district = ?
                    WHERE van_tay = ? AND (district IS NULL OR district = '')
                    """,
                    (dist, vt),
                )
                if cur.rowcount:
                    applied["district"] += 1
            if has_real:
                try:
                    from tracking_aship import build_tracking_url
                except Exception:  # noqa: BLE001
                    build_tracking_url = None  # type: ignore
                prov = route.get("provider") or tr.get("provider")
                url = tr.get("tracking_link")
                if build_tracking_url and prov and tr.get("tracking_code"):
                    url = (
                        build_tracking_url(
                            str(tr["tracking_code"]),
                            provider=str(prov),
                            tracking_code=str(tr["tracking_code"]),
                        )
                        or url
                    )
                cur = conn.execute(
                    """
                    UPDATE orders
                    SET tracking_code = ?, tracking_ref = ?,
                        tracking_provider = COALESCE(?, tracking_provider),
                        tracking_url = COALESCE(?, tracking_url)
                    WHERE van_tay = ?
                    """,
                    (tr["tracking_code"], tr["tracking_code"], prov, url, vt),
                )
                if cur.rowcount:
                    applied["tracking"] += 1
                if url:
                    conn.execute(
                        "UPDATE orders SET tracking_url = ?, tracking_provider = COALESCE(?, tracking_provider) WHERE van_tay = ?",
                        (url, prov, vt),
                    )
                    applied["url"] += 1
            if route.get("carrier"):
                conn.execute(
                    "UPDATE orders SET carrier = ? WHERE van_tay = ?",
                    (route["carrier"], vt),
                )
                applied["carrier"] += 1
            if route.get("buucuc"):
                conn.execute(
                    "UPDATE orders SET buucuc = ? WHERE van_tay = ?",
                    (route["buucuc"], vt),
                )
                applied["buucuc"] += 1
            if tl.get("picked_at") and not t.get("picked_at"):
                cur = conn.execute(
                    """
                    UPDATE orders SET picked_at = ?
                    WHERE van_tay = ? AND (picked_at IS NULL OR picked_at = '')
                    """,
                    (tl["picked_at"], vt),
                )
                if cur.rowcount:
                    applied["picked_at"] += 1
            # delivered_at: delivered OR returning (first delivery before return)
            if tl.get("delivered_at") and not t.get("delivered_at"):
                if t.get("status") in {"delivered", "returning"}:
                    cur = conn.execute(
                        """
                        UPDATE orders SET delivered_at = ?
                        WHERE van_tay = ? AND (delivered_at IS NULL OR delivered_at = '')
                        """,
                        (tl["delivered_at"], vt),
                    )
                    if cur.rowcount:
                        applied["delivered_at"] += 1
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "hop12_waiting_backfill",
                    vt,
                    oid,
                    (
                        f"st={t.get('status')}|real={int(has_real)}|"
                        f"trk={tr.get('tracking_code') or '∅'}|"
                        f"car={route.get('carrier') or '∅'}|buu={route.get('buucuc') or '∅'}|"
                        f"pick={tl.get('picked_at') or '∅'}|del={tl.get('delivered_at') or '∅'}"
                    ),
                ),
            )
            applied["events"] += 1
        probes.append(entry)
    if apply and applied["events"]:
        conn.commit()
    remain = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND tracking_code = so_noi_bo
          AND status IN ('returning', 'new', 'submitted')
        """,
        (wid,),
    ).fetchone()[0]
    return {
        "query_type": "waiting_live_backfill",
        "query": wid,
        "hit": ok_n > 0 or bool(targets),
        "count": len(targets),
        "ok": ok_n,
        "got_real_tracking": got_trk,
        "apply": apply,
        "applied": applied,
        "remain_waiting_id": remain,
        "partners": [
            {"partner": k, "n": v} for k, v in sorted(partners.items(), key=lambda x: -x[1])
        ],
        "samples": probes[:12],
        "path": (
            f"waiting_live_backfill ok={ok_n}/{len(targets)} real_trk={got_trk} "
            f"apply={apply} applied={applied} remain={remain}"
        ),
        "unmask_map": {
            "path_id": "PATH-CLEAR" if got_trk else "PATH-WAIT",
            "action": "live_extend_code_for_returning_submitted",
            "note": "Returning giữ status; delivered_at = giao lần đầu nếu API có",
        },
        "next": [
            "Submitted còn pancake-id → chờ ship",
            "Hard SPX gap giữ PATH-MISSING",
        ],
    }


def reverse_hard_gap_aship_probe(
    conn: sqlite3.Connection, wid: str, *, limit: int = 8
) -> dict:
    """Probe aship URL của hard-gap — audit HTTP, không ghi timestamp."""
    try:
        from tracking_aship import probe_url
    except Exception as e:  # noqa: BLE001
        return {
            "query_type": "hard_gap_aship_probe",
            "query": wid,
            "hit": False,
            "error": str(e),
            "path": "hard_gap_aship_probe: module lỗi",
        }
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, tracking_url, status, carrier
            FROM orders
            WHERE warehouse_id = ?
              AND ifnull(tracking_url,'') != ''
              AND (
                (status = 'delivered' AND (delivered_at IS NULL OR delivered_at = ''))
                OR (status = 'shipped' AND (picked_at IS NULL OR picked_at = ''))
              )
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, limit),
        )
    ]
    probes = []
    ok_n = 0
    for r in rows:
        try:
            pr = probe_url(str(r.get("tracking_url")), timeout=8.0)
        except Exception as e:  # noqa: BLE001
            pr = {"ok": False, "error": str(e)}
        if pr.get("ok"):
            ok_n += 1
        probes.append(
            {
                "van_tay": r.get("van_tay"),
                "so_noi_bo": r.get("so_noi_bo"),
                "tracking_code": r.get("tracking_code"),
                "status": r.get("status"),
                "carrier": r.get("carrier"),
                "http": pr.get("http"),
                "ok": pr.get("ok"),
                "error": pr.get("error"),
                "snippet": (pr.get("snippet") or "")[:60],
            }
        )
    return {
        "query_type": "hard_gap_aship_probe",
        "query": wid,
        "hit": ok_n > 0,
        "count": len(probes),
        "ok": ok_n,
        "samples": probes,
        "path": f"hard_gap_aship_probe ok={ok_n}/{len(probes)}",
        "unmask_map": {
            "path_id": "PATH-MISSING",
            "action": "aship_http_audit_only_no_invent_timestamp",
            "note": "HTTP 200 ≠ có pick/del trong Pancake hist",
        },
        "next": ["Không map aship HTML → picked_at"],
    }


def reverse_chain_asumee_hop12(
    conn: sqlite3.Connection,
    wid: str,
    *,
    live: bool = False,
    apply: bool = False,
    limit: int = 40,
    probe: bool = False,
) -> list[dict]:
    """Hop-12: open-path scorecard, waiting live backfill, hard aship probe."""
    out: list[dict] = []

    score = reverse_open_path_scorecard(conn, wid)
    out.append(score)
    out.append(reverse_returning_cohort(conn, wid))
    out.append(reverse_submitted_waiting(conn, wid))

    if live:
        batch = reverse_waiting_live_backfill(conn, wid, limit=limit, apply=apply)
        out.append(batch)
        for s in (batch.get("samples") or [])[:8]:
            if not s.get("ok"):
                continue
            vt = s.get("van_tay")
            tr = s.get("tracking_api")
            so = s.get("so_noi_bo")
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = "hop12_waiting"
                out.append(r)
            if tr and s.get("has_real_tracking"):
                r = reverse_by_tracking(conn, str(tr))
                r["gap_cohort"] = "hop12_waiting"
                out.append(r)
            elif so:
                r = reverse_by_so_noi_bo(conn, str(so))
                r["gap_cohort"] = "hop12_waiting"
                out.append(r)
        if apply:
            sync = reverse_aship_url_sync(conn, wid, apply=True, limit=200)
            out.append(sync)
            out.append(reverse_carrier_buucuc_remap(conn, wid, apply=True))
    else:
        out.append(
            {
                "query_type": "waiting_live_backfill",
                "query": wid,
                "hit": False,
                "skipped": True,
                "path": "waiting_live_backfill skipped (live=False)",
                "next": [
                    "python3 scripts/order_pipe_reverse_query.py --continue-flow --hop12-live --hop12-apply"
                ],
            }
        )

    if probe:
        out.append(reverse_hard_gap_aship_probe(conn, wid, limit=8))

    out.append(reverse_returning_cohort(conn, wid))
    out.append(reverse_open_path_scorecard(conn, wid))
    out.append(reverse_3pl_completeness(conn, wid))
    out.append(reverse_flow_completeness(conn, wid))
    out.append(reverse_pipe_events_asumee(conn, wid))
    out.append(reverse_hard_soft_gaps(conn, wid))
    out.append(reverse_tracking_classify(conn, wid))
    return out


def reverse_flow_closure(conn: sqlite3.Connection, wid: str) -> dict:
    """Sổ đóng/mở đường sau hop1–12."""
    score = reverse_open_path_scorecard(conn, wid)
    fills = score.get("fills") or {}
    flow = reverse_flow_completeness(conn, wid)
    closed = {
        "returning_real_trk": int(fills.get("returning_real_trk") or 0),
        "hard_gap_accept_events": conn.execute(
            "SELECT COUNT(*) FROM pipe_events WHERE event='hard_gap_accept'"
        ).fetchone()[0],
        "soft_gap_accept_events": conn.execute(
            "SELECT COUNT(*) FROM pipe_events WHERE event='soft_gap_accept'"
        ).fetchone()[0],
        "commune_geo_accept": conn.execute(
            "SELECT COUNT(*) FROM pipe_events WHERE event='commune_geo_accept'"
        ).fetchone()[0],
        "canceled_pancake_id": int(fills.get("canceled_pancake_id") or 0),
        "trk_real": (flow.get("fills") or {}).get("trk_real"),
        "with_3pl": (flow.get("fills") or {}).get("with_3pl"),
    }
    open_paths = {
        "submitted_wait": int(fills.get("submitted_wait") or 0),
        "new_wait": int(fills.get("new_wait") or 0),
        "returning_need_trk": int(fills.get("returning_pancake_id") or 0),
        "hard_del": int(fills.get("hard_del") or 0),
        "hard_ship": int(fills.get("hard_ship") or 0),
        "soft_del": int(fills.get("soft_del") or 0),
        "ward_only": int(fills.get("ward_only") or 0),
    }
    return {
        "query_type": "flow_closure",
        "query": wid,
        "hit": True,
        "count": fills.get("orders"),
        "closed": closed,
        "open": open_paths,
        "scores": flow.get("scores"),
        "path": (
            f"flow_closure closed_returning={closed['returning_real_trk']} "
            f"open_wait={open_paths['submitted_wait']+open_paths['new_wait']} "
            f"hard={open_paths['hard_del']+open_paths['hard_ship']} "
            f"soft={open_paths['soft_del']}"
        ),
        "unmask_map": {"path_id": "PATH-CLEAR"},
        "next": [
            "PATH-WAIT submitted/new → ship rồi hop7",
            "PATH-MISSING hard SPX giữ nguyên",
            "PATH-ACCEPT soft/commune/canceled giữ nguyên",
        ],
    }


def reverse_wait_path_accept(
    conn: sqlite3.Connection, wid: str, *, apply: bool = False
) -> dict:
    """Đánh dấu PATH-WAIT cho submitted/new pancake-id (không ép extend_code)."""
    by_status = [
        dict(r)
        for r in conn.execute(
            """
            SELECT status, COUNT(*) AS orders,
              SUM(CASE WHEN carrier='Pancake' THEN 1 ELSE 0 END) AS still_pancake,
              SUM(CASE WHEN buucuc='SPX' THEN 1 ELSE 0 END) AS already_spx
            FROM orders
            WHERE warehouse_id = ?
              AND status IN ('submitted', 'new')
              AND tracking_code = so_noi_bo
            GROUP BY status ORDER BY orders DESC
            """,
            (wid,),
        )
    ]
    n = sum(int(r.get("orders") or 0) for r in by_status)
    by_prov = [
        dict(r)
        for r in conn.execute(
            """
            SELECT coalesce(province,'(∅)') AS province, COUNT(*) AS orders
            FROM orders
            WHERE warehouse_id = ?
              AND status IN ('submitted', 'new')
              AND tracking_code = so_noi_bo
            GROUP BY 1 ORDER BY orders DESC LIMIT 12
            """,
            (wid,),
        )
    ]
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, status, province, ward, carrier, buucuc, created_at
            FROM orders
            WHERE warehouse_id = ?
              AND status IN ('submitted', 'new')
              AND tracking_code = so_noi_bo
            ORDER BY piped_at DESC LIMIT 12
            """,
            (wid,),
        )
    ]
    applied = 0
    if apply and n:
        has = conn.execute(
            """
            SELECT 1 FROM pipe_events
            WHERE event = 'wait_path_accept' AND so_noi_bo = ? LIMIT 1
            """,
            (wid,),
        ).fetchone()
        if not has:
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "wait_path_accept",
                    None,
                    wid,
                    f"PATH-WAIT|submitted+new={n}|by={by_status}",
                ),
            )
            conn.commit()
            applied = 1
    return {
        "query_type": "wait_path_accept",
        "query": wid,
        "hit": n > 0,
        "count": n,
        "by_status": by_status,
        "by_province": by_prov,
        "samples": samples,
        "apply": apply,
        "applied": applied,
        "path": f"wait_path_accept n={n} apply={apply} applied={applied}",
        "unmask_map": {
            "path_id": "PATH-WAIT",
            "action": "accept_submitted_new_without_extend_code",
            "note": "Pancake submitted thường partner=∅ / extend=∅ đến khi ship",
        },
        "next": [
            "Khi status→shipped: --hop7-apply --hop7-limit 200",
            "Không bịa SPXVN khi extend_code trống",
        ],
    }


def reverse_submitted_confirm_scan(
    conn: sqlite3.Connection,
    wid: str,
    *,
    limit: int = 60,
    apply: bool = False,
) -> dict:
    """Live xác nhận submitted/new Pancake — tìm rare extend/partner; không bịa VĐ."""
    targets = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, status, carrier, buucuc, province, district,
                   picked_at, delivered_at, tracking_code
            FROM orders
            WHERE warehouse_id = ?
              AND status IN ('submitted', 'new')
              AND tracking_code = so_noi_bo
              AND (carrier IS NULL OR carrier = '' OR carrier = 'Pancake'
                   OR buucuc IS NULL OR buucuc = '' OR buucuc = 'Pancake')
            ORDER BY
              CASE status WHEN 'new' THEN 0 ELSE 1 END,
              piped_at DESC
            LIMIT ?
            """,
            (wid, limit),
        )
    ]
    probes: list[dict] = []
    partners: dict[str, int] = {}
    applied = {"carrier": 0, "buucuc": 0, "tracking": 0, "url": 0, "events": 0}
    ok_n = 0
    got_trk = 0
    got_partner = 0
    for t in targets:
        oid = str(t.get("so_noi_bo") or "").strip()
        res = fetch_pancake_order_detail(oid)
        entry: dict[str, Any] = {
            "van_tay": t.get("van_tay"),
            "so_noi_bo": oid,
            "status_pipe": t.get("status"),
            "ok": res.get("ok"),
        }
        if not res.get("ok"):
            entry["error"] = res.get("error")
            probes.append(entry)
            continue
        ok_n += 1
        detail = res["data"]
        partner = detail.get("partner") if isinstance(detail.get("partner"), dict) else {}
        pn = partner.get("partner_name") or "(none)"
        partners[str(pn)] = partners.get(str(pn), 0) + 1
        tr = extract_pancake_tracking(detail)
        route = map_partner_name_to_routing(
            partner.get("partner_name"),
            provider=tr.get("provider"),
            tracking_code=tr.get("tracking_code") or oid,
        )
        has_real = bool(tr.get("tracking_code") and tr["tracking_code"] != oid)
        if has_real:
            got_trk += 1
        if pn != "(none)":
            got_partner += 1
        entry.update(
            {
                "partner_name": pn,
                "status_api": detail.get("status"),
                "extend_code": partner.get("extend_code"),
                "tracking_api": tr.get("tracking_code"),
                "has_real_tracking": has_real,
                "carrier_new": route.get("carrier"),
                "buucuc_new": route.get("buucuc"),
                "hist_n": len(detail.get("histories") or [])
                if isinstance(detail.get("histories"), list)
                else 0,
            }
        )
        if apply and (has_real or route.get("carrier")):
            vt = t.get("van_tay")
            if has_real:
                try:
                    from tracking_aship import build_tracking_url
                except Exception:  # noqa: BLE001
                    build_tracking_url = None  # type: ignore
                prov = route.get("provider") or tr.get("provider")
                url = None
                if build_tracking_url and prov:
                    url = build_tracking_url(
                        str(tr["tracking_code"]),
                        provider=str(prov),
                        tracking_code=str(tr["tracking_code"]),
                    )
                conn.execute(
                    """
                    UPDATE orders
                    SET tracking_code = ?, tracking_ref = ?,
                        tracking_provider = COALESCE(?, tracking_provider),
                        tracking_url = COALESCE(?, tracking_url)
                    WHERE van_tay = ?
                    """,
                    (tr["tracking_code"], tr["tracking_code"], prov, url, vt),
                )
                applied["tracking"] += 1
                if url:
                    conn.execute(
                        "UPDATE orders SET tracking_url = ? WHERE van_tay = ?",
                        (url, vt),
                    )
                    applied["url"] += 1
            if route.get("carrier"):
                conn.execute(
                    "UPDATE orders SET carrier = ? WHERE van_tay = ?",
                    (route["carrier"], vt),
                )
                applied["carrier"] += 1
            if route.get("buucuc"):
                conn.execute(
                    "UPDATE orders SET buucuc = ? WHERE van_tay = ?",
                    (route["buucuc"], vt),
                )
                applied["buucuc"] += 1
            conn.execute(
                "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
                (
                    utc_now(),
                    "hop13_submitted_confirm",
                    vt,
                    oid,
                    (
                        f"st={t.get('status')}|partner={pn}|real={int(has_real)}|"
                        f"trk={tr.get('tracking_code') or '∅'}|"
                        f"car={route.get('carrier') or '∅'}"
                    ),
                ),
            )
            applied["events"] += 1
        probes.append(entry)
    if apply and applied["events"]:
        conn.commit()
    return {
        "query_type": "submitted_confirm_scan",
        "query": wid,
        "hit": ok_n > 0 or bool(targets),
        "count": len(targets),
        "ok": ok_n,
        "got_real_tracking": got_trk,
        "got_partner": got_partner,
        "apply": apply,
        "applied": applied,
        "partners": [
            {"partner": k, "n": v} for k, v in sorted(partners.items(), key=lambda x: -x[1])
        ],
        "samples": probes[:12],
        "path": (
            f"submitted_confirm_scan ok={ok_n}/{len(targets)} "
            f"real={got_trk} partner={got_partner} apply={apply}"
        ),
        "unmask_map": {
            "path_id": "PATH-WAIT" if got_trk == 0 else "PATH-CLEAR",
            "action": "confirm_no_extend_or_rare_promote",
            "note": "Phần lớn submitted Pancake partner=∅ đến khi ship",
        },
        "next": [
            "real_trk=0 → giữ PATH-WAIT",
            "Nếu sau này có extend → hop7/hop12 lại",
        ],
    }


def reverse_chain_asumee_hop13(
    conn: sqlite3.Connection,
    wid: str,
    *,
    live: bool = False,
    apply: bool = False,
    limit: int = 60,
) -> list[dict]:
    """Hop-13: flow closure, confirm submitted wait, PATH-WAIT accept."""
    out: list[dict] = []

    out.append(reverse_flow_closure(conn, wid))
    out.append(reverse_open_path_scorecard(conn, wid))
    out.append(reverse_submitted_waiting(conn, wid))

    wait = reverse_wait_path_accept(conn, wid, apply=apply)
    out.append(wait)
    for s in (wait.get("samples") or [])[:4]:
        vt = s.get("van_tay")
        so = s.get("so_noi_bo")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop13_wait"
            out.append(r)
        if so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "hop13_wait"
            out.append(r)
        prov = s.get("province")
        if prov:
            out.append(reverse_by_province(conn, str(prov), limit=5))

    if live:
        scan = reverse_submitted_confirm_scan(conn, wid, limit=limit, apply=apply)
        out.append(scan)
        for s in (scan.get("samples") or [])[:6]:
            if not s.get("ok"):
                continue
            vt = s.get("van_tay")
            tr = s.get("tracking_api")
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = "hop13_confirm"
                out.append(r)
            if tr and s.get("has_real_tracking"):
                r = reverse_by_tracking(conn, str(tr))
                r["gap_cohort"] = "hop13_confirm"
                out.append(r)
        if apply and int(scan.get("got_real_tracking") or 0) > 0:
            out.append(reverse_aship_url_sync(conn, wid, apply=True, limit=100))
            out.append(reverse_carrier_buucuc_remap(conn, wid, apply=True))
    else:
        out.append(
            {
                "query_type": "submitted_confirm_scan",
                "query": wid,
                "hit": False,
                "skipped": True,
                "path": "submitted_confirm_scan skipped (live=False)",
                "next": [
                    "python3 scripts/order_pipe_reverse_query.py --continue-flow --hop13-live --hop13-apply"
                ],
            }
        )

    out.append(reverse_returning_cohort(conn, wid))
    out.append(reverse_hard_soft_gaps(conn, wid))
    out.append(reverse_flow_closure(conn, wid))
    out.append(reverse_flow_completeness(conn, wid))
    out.append(reverse_3pl_completeness(conn, wid))
    out.append(reverse_pipe_events_asumee(conn, wid))
    return out


def reverse_chain_asumee_hop5(conn: sqlite3.Connection, wid: str) -> list[dict]:
    """Hop-5 ngược dòng: recover huyện, SPX-like URL, timeline trống, pipe_events."""
    out: list[dict] = []

    # District recover từ full_address (ward có, district trống)
    dist = reverse_district_recover(conn, wid)
    out.append(dist)
    for s in (dist.get("samples") or [])[:5]:
        hint = s.get("hint_district")
        vt = s.get("van_tay")
        so = s.get("so_noi_bo")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop5_district_recover"
            out.append(r)
        if so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "hop5_district_recover"
            out.append(r)
        if hint:
            out.append(reverse_by_address(conn, str(hint), limit=6))

    out.append(reverse_gap_cohort(conn, wid, "no_district"))
    out.append(reverse_gap_cohort(conn, wid, "delivered_no_timeline"))

    # SPX-like tracking (26**********) → aship URL + reverse drills
    spx = reverse_spx_like_tracking(conn, wid, limit=14)
    out.append(spx)
    for tn in (spx.get("_drill_tracking") or [])[:8]:
        r = reverse_by_tracking(conn, tn)
        r["gap_cohort"] = "hop5_spx_like"
        out.append(r)

    # Timeline gaps: shipped/delivered thiếu pick/deliver
    tl = reverse_timeline_gap(conn, wid, limit=12)
    out.append(tl)
    for s in (tl.get("samples") or [])[:4]:
        vt = s.get("van_tay")
        so = s.get("so_noi_bo")
        tr = s.get("tracking_code")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop5_timeline"
            out.append(r)
        if so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "hop5_timeline"
            out.append(r)
        if tr and str(tr) != str(so):
            r = reverse_by_tracking(conn, str(tr))
            r["gap_cohort"] = "hop5_timeline"
            out.append(r)

    # pipe_events cho ASUMEE (thường 0)
    out.append(reverse_pipe_events_asumee(conn, wid))

    # Canceled + SPX-like → reverse
    canceled_spx = conn.execute(
        """
        SELECT van_tay, so_noi_bo, tracking_code FROM orders
        WHERE warehouse_id = ? AND status = 'canceled'
          AND tracking_code GLOB '26*' AND length(tracking_code) = 14
        ORDER BY piped_at DESC LIMIT 4
        """,
        (wid,),
    ).fetchall()
    for vt, so, tr in canceled_spx:
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop5_canceled_spx"
            out.append(r)
        if tr:
            r = reverse_by_tracking(conn, str(tr))
            r["gap_cohort"] = "hop5_canceled_spx"
            out.append(r)
        elif so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "hop5_canceled_spx"
            out.append(r)

    return out


def reverse_chain_asumee_hop4(conn: sqlite3.Connection, wid: str) -> list[dict]:
    """Hop-4 ngược dòng: returning/new drill, attach tracking URL, submitted thiếu tỉnh."""
    out: list[dict] = []

    # Returning / new deep
    for st in ("returning", "new"):
        out.append(reverse_by_status_warehouse(conn, wid, st, limit=12))
        rows = conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code FROM orders
            WHERE warehouse_id = ? AND status = ? AND van_tay IS NOT NULL
            ORDER BY piped_at DESC LIMIT 4
            """,
            (wid, st),
        ).fetchall()
        for vt, so, tr in rows:
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = f"hop4_{st}"
                out.append(r)
            if so:
                r = reverse_by_so_noi_bo(conn, str(so))
                r["gap_cohort"] = f"hop4_{st}"
                out.append(r)
            if tr and str(tr) != str(so):
                r = reverse_by_tracking(conn, str(tr))
                r["gap_cohort"] = f"hop4_{st}"
                out.append(r)

    # Submitted thiếu province + gap cohort
    gap_sub = reverse_gap_cohort(conn, wid, "submitted_no_province")
    out.append(gap_sub)
    for s in (gap_sub.get("sample_orders") or [])[:4]:
        vt = s.get("van_tay")
        so = s.get("so_noi_bo")
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop4_submitted_no_province"
            out.append(r)
        if so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "hop4_submitted_no_province"
            out.append(r)

    # Tracking URL attach batch (aship) + reverse drills
    attach = reverse_tracking_url_attach(conn, wid, limit=12)
    out.append(attach)
    drills = list(attach.get("_drill_tracking") or [])
    # Fallback: khi with_url=0 (Pancake order_id = tracking) vẫn drill tracking_code
    if not drills:
        for a in (attach.get("samples") or [])[:8]:
            tn = str(a.get("tracking_code") or a.get("so_noi_bo") or "").strip()
            if tn and tn not in drills:
                drills.append(tn)
    for tn in drills[:8]:
        r = reverse_by_tracking(conn, tn)
        r["gap_cohort"] = "hop4_tracking_url"
        out.append(r)

    # Timeline: newest submitted
    newest = conn.execute(
        """
        SELECT van_tay, so_noi_bo FROM orders
        WHERE warehouse_id = ? AND status = 'submitted'
        ORDER BY piped_at DESC LIMIT 5
        """,
        (wid,),
    ).fetchall()
    for vt, so in newest:
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "hop4_submitted_newest"
            out.append(r)
        if so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "hop4_submitted_newest"
            out.append(r)

    # Provinces of returning
    for (prov,) in conn.execute(
        """
        SELECT province FROM orders
        WHERE warehouse_id = ? AND status = 'returning'
          AND province IS NOT NULL AND province != ''
        GROUP BY province ORDER BY COUNT(*) DESC LIMIT 4
        """,
        (wid,),
    ):
        out.append(reverse_by_province(conn, prov, limit=8))

    return out


def reverse_tracking_url_attach(conn: sqlite3.Connection, wid: str, limit: int = 12) -> dict:
    """Gắn tracking URL (aship) cho mẫu ASUMEE rồi ánh xạ ngược."""
    try:
        from tracking_aship import attach_tracking_urls
    except Exception as e:  # noqa: BLE001
        return {
            "query_type": "tracking_url_attach",
            "query": wid,
            "hit": False,
            "error": str(e),
            "path": "tracking_url_attach: module lỗi",
        }

    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM orders
            WHERE warehouse_id = ?
              AND status IN ('shipped', 'delivered', 'returning')
              AND tracking_code IS NOT NULL AND tracking_code != ''
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, limit),
        )
    ]
    attached = []
    providers: dict[str, int] = {}
    for o in rows:
        try:
            a = attach_tracking_urls(dict(o))
        except Exception:  # noqa: BLE001
            a = dict(o)
        url = a.get("tracking_url")
        prov = a.get("tracking_provider") or "(none)"
        providers[prov] = providers.get(prov, 0) + 1
        attached.append(
            {
                "van_tay": a.get("van_tay"),
                "so_noi_bo": a.get("so_noi_bo"),
                "tracking_code": a.get("tracking_code"),
                "status": a.get("status"),
                "province": a.get("province"),
                "buucuc": a.get("buucuc"),
                "carrier": a.get("carrier"),
                "tracking_provider": a.get("tracking_provider"),
                "tracking_url": url,
                "has_url": bool(url),
            }
        )
    # Drill: ưu tiên mã có URL; fallback order_id/tracking_code (Pancake)
    drills: list[str] = []
    for a in attached:
        if a.get("has_url") and a.get("tracking_code") and a["tracking_code"] not in drills:
            drills.append(str(a["tracking_code"]))
        if len(drills) >= 4:
            break
    if not drills:
        for a in attached:
            tn = str(a.get("tracking_code") or a.get("so_noi_bo") or "").strip()
            if tn and tn not in drills:
                drills.append(tn)
            if len(drills) >= 6:
                break

    result = {
        "query_type": "tracking_url_attach",
        "query": wid,
        "hit": bool(attached),
        "count": len(attached),
        "providers": [
            {"provider": k, "n": v}
            for k, v in sorted(providers.items(), key=lambda x: -x[1])
        ],
        "with_url": sum(1 for a in attached if a.get("has_url")),
        "id_as_tracking": sum(
            1
            for a in attached
            if a.get("tracking_code")
            and a.get("so_noi_bo")
            and str(a["tracking_code"]) == str(a["so_noi_bo"])
        ),
        "samples": attached[:10],
        "path": (
            f"tracking_url_attach n={len(attached)} with_url="
            f"{sum(1 for a in attached if a.get('has_url'))} "
            f"id_as_tracking="
            f"{sum(1 for a in attached if a.get('tracking_code') and a.get('so_noi_bo') and str(a['tracking_code']) == str(a['so_noi_bo']))} "
            f"providers={list(providers)}"
        ),
        "unmask_map": {
            "note": "URL tracking ≠ unmask PII; carrier Pancake thường không có deep link 3PL",
            "path_id": "PATH-CLEAR" if any(a.get("has_url") for a in attached) else "PATH-MISSING",
        },
        "next": [
            "Nếu with_url=0: mã VĐ đang là order_id Pancake — cần mã GHN/SPX/VTP thật",
            "Pipe lại shipments.tracking từ detail API nếu có",
        ],
    }
    result["_drill_tracking"] = drills
    return result


def reverse_chain_asumee_hop3(conn: sqlite3.Connection, wid: str) -> list[dict]:
    """Hop-3 ngược dòng: Huế sâu, SĐT OK hiếm, returning/new, cluster mask trùng."""
    out: list[dict] = []

    # Huế — từ geo_recover
    out.append(reverse_by_province(conn, "Huế", limit=15))
    out.append(reverse_by_address(conn, "Huế", limit=12))
    out.append(reverse_by_address(conn, "A Lưới", limit=8))
    hue_wards = [
        r[0]
        for r in conn.execute(
            """
            SELECT ward FROM orders
            WHERE warehouse_id = ?
              AND (province LIKE '%Huế%' OR full_address LIKE '%Huế%' OR full_address LIKE '%Hue%')
              AND ward IS NOT NULL AND ward != ''
            GROUP BY ward ORDER BY COUNT(*) DESC LIMIT 5
            """,
            (wid,),
        )
    ]
    for w in hue_wards:
        out.append(reverse_by_ward_warehouse(conn, wid, w, limit=8))

    # Status returning / new
    for st in ("returning", "new"):
        out.append(reverse_by_status_warehouse(conn, wid, st, limit=10))

    # SĐT OK hiếm — contrast unmask (mask display in report)
    out.append(reverse_ok_phone_contrast(conn, wid))
    ok_rows = conn.execute(
        """
        SELECT van_tay, so_noi_bo FROM orders
        WHERE warehouse_id = ?
          AND receiver_phone IS NOT NULL AND receiver_phone != ''
          AND instr(receiver_phone, '*') = 0
        ORDER BY piped_at DESC LIMIT 5
        """,
        (wid,),
    ).fetchall()
    for vt, so in ok_rows:
        if vt:
            r = reverse_by_van_tay(conn, str(vt))
            r["gap_cohort"] = "phone_ok_clear"
            out.append(r)
        if so:
            r = reverse_by_so_noi_bo(conn, str(so))
            r["gap_cohort"] = "phone_ok_clear"
            out.append(r)

    # Cluster mask trùng (cảnh báo: mask ngắn → collision giả)
    out.append(reverse_mask_phone_clusters(conn, wid))
    top_masks = [
        r[0]
        for r in conn.execute(
            """
            SELECT receiver_phone FROM orders
            WHERE warehouse_id = ? AND receiver_phone LIKE '%*%'
            GROUP BY receiver_phone HAVING COUNT(*) >= 5
            ORDER BY COUNT(*) DESC LIMIT 3
            """,
            (wid,),
        )
    ]
    for ph in top_masks:
        # Không dùng phone làm address query — dùng so mẫu trong cluster
        sos = conn.execute(
            """
            SELECT so_noi_bo, van_tay FROM orders
            WHERE warehouse_id = ? AND receiver_phone = ?
            ORDER BY piped_at DESC LIMIT 2
            """,
            (wid, ph),
        ).fetchall()
        for so, vt in sos:
            if so:
                r = reverse_by_so_noi_bo(conn, str(so))
                r["gap_cohort"] = f"mask_cluster:{ph}"
                out.append(r)
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = f"mask_cluster:{ph}"
                out.append(r)

    return out


def reverse_ok_phone_contrast(conn: sqlite3.Connection, wid: str) -> dict:
    """Đối chiếu đơn SĐT CLEAR (hiếm) vs MASK — hỗ trợ unmask path."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, status, province, ward, receiver_name, receiver_phone,
                   tracking_code, flow_path
            FROM orders
            WHERE warehouse_id = ?
              AND receiver_phone IS NOT NULL AND receiver_phone != ''
              AND instr(receiver_phone, '*') = 0
            ORDER BY piped_at DESC LIMIT 20
            """,
            (wid,),
        )
    ]
    samples = []
    for r in rows:
        samples.append(
            {
                **r,
                "receiver_phone_masked_report": mask_phone(r.get("receiver_phone")),
                "phone_class": "OK",
                "kho": "ASUMEE",
                "warehouse_id": wid,
                "backend": "OMS-pipe-bus",
                "buucuc": "Pancake",
                # strip raw phone from panorama payload for safety in JSON samples list
                "receiver_phone": mask_phone(r.get("receiver_phone")),
            }
        )
    return _attach_flow(
        {
            "query_type": "phone_ok_contrast",
            "query": wid,
            "hit": bool(samples),
            "count": len(samples),
            "sample_orders": samples,
            "unmask_map": {
                "path_id": "PATH-CLEAR",
                "note": "SĐT CLEAR hiếm trên ASUMEE api_key — phần lớn vẫn MASK; không lộ raw trong report",
                "action": "use_clear_phone_for_ops_callback",
            },
            "path": f"phone_ok_contrast n={len(samples)} ← ASUMEE (PATH-CLEAR)",
        }
    )


def reverse_mask_phone_clusters(conn: sqlite3.Connection, wid: str) -> dict:
    """Cluster SĐT mask trùng — cảnh báo collision do redaction ngắn (0****01)."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT receiver_phone AS mask_display,
                   COUNT(*) AS orders,
                   COUNT(DISTINCT status) AS statuses,
                   COUNT(DISTINCT province) AS provinces,
                   COUNT(DISTINCT van_tay) AS fingerprints
            FROM orders
            WHERE warehouse_id = ? AND receiver_phone LIKE '%*%'
            GROUP BY receiver_phone
            HAVING COUNT(*) >= 4
            ORDER BY orders DESC
            LIMIT 15
            """,
            (wid,),
        )
    ]
    return {
        "query_type": "mask_phone_clusters",
        "query": wid,
        "hit": bool(rows),
        "count": len(rows),
        "clusters": rows,
        "warning": (
            "Mask ngắn (vd 0****01) tạo collision giả — không gộp khách theo mask_display"
        ),
        "unmask_map": {
            "path_id": "PATH-MASK-REDACTION",
            "action": "fetch_unmasked_from_source_api_before_dedupe",
        },
        "path": f"mask_clusters×{len(rows)} ← ASUMEE (collision risk)",
    }


def reverse_chain_asumee_hop2(conn: sqlite3.Connection, wid: str) -> list[dict]:
    """Hop-2 ngược dòng: cohort thiếu tỉnh/địa chỉ/SĐT → van_tay/so → recover geo → icon."""
    out: list[dict] = []
    out.append(reverse_gap_cohort(conn, wid, "no_province"))
    out.append(reverse_gap_cohort(conn, wid, "no_address"))
    out.append(reverse_gap_cohort(conn, wid, "canceled_missing_phone"))
    out.append(reverse_gap_cohort(conn, wid, "mask_phone_delivered"))
    out.append(reverse_geo_recover(conn, wid))
    out.append(reverse_by_icon_chant(conn, "Khối Kho", limit=10))
    out.append(reverse_by_icon_chant(conn, "Tia Lửa", limit=8))

    # Drill van_tay/so từ no_province + canceled missing
    for sql, label in (
        (
            """
            SELECT van_tay, so_noi_bo FROM orders
            WHERE warehouse_id = ? AND (province IS NULL OR province = '')
              AND van_tay IS NOT NULL
            ORDER BY piped_at DESC LIMIT 4
            """,
            "no_province",
        ),
        (
            """
            SELECT van_tay, so_noi_bo FROM orders
            WHERE warehouse_id = ? AND status = 'canceled'
              AND (receiver_phone IS NULL OR receiver_phone = '')
              AND van_tay IS NOT NULL
            ORDER BY piped_at DESC LIMIT 3
            """,
            "canceled_missing",
        ),
        (
            """
            SELECT van_tay, so_noi_bo FROM orders
            WHERE warehouse_id = ? AND status = 'delivered'
              AND receiver_phone LIKE '%*%'
            ORDER BY piped_at DESC LIMIT 3
            """,
            "delivered_mask",
        ),
    ):
        rows = conn.execute(sql, (wid,)).fetchall()
        for vt, so in rows:
            if vt:
                r = reverse_by_van_tay(conn, str(vt))
                r["gap_cohort"] = label
                out.append(r)
            if so:
                r = reverse_by_so_noi_bo(conn, str(so))
                r["gap_cohort"] = label
                out.append(r)

    # Address fragments suy ra từ full_address khi thiếu province (vd Huế)
    hints = conn.execute(
        """
        SELECT full_address FROM orders
        WHERE warehouse_id = ?
          AND (province IS NULL OR province = '')
          AND full_address IS NOT NULL AND full_address != ''
        ORDER BY piped_at DESC LIMIT 8
        """,
        (wid,),
    ).fetchall()
    seen_frag: set[str] = set()
    for (addr,) in hints:
        frag = _geo_hint_from_address(str(addr))
        if frag and frag not in seen_frag:
            seen_frag.add(frag)
            out.append(reverse_by_address(conn, frag, limit=6))
            out.append(reverse_by_province(conn, frag, limit=6))
    return out


def _geo_hint_from_address(addr: str) -> str | None:
    """Lấy gợi ý tỉnh/thành từ full_address khi province trống."""
    if not addr:
        return None
    # Patterns: "Việt Nam, Huế, …" / ", Ninh Bình," / tỉnh names
    parts = [p.strip() for p in re.split(r"[,/|]", addr) if p.strip()]
    skip = {"việt nam", "vietnam", "vn"}
    for p in parts[:4]:
        low = p.lower()
        if low in skip:
            continue
        if p.startswith("Xã ") or p.startswith("Phường ") or p.startswith("Thị "):
            continue
        if "*" in p:
            continue
        if len(p) >= 3:
            return p
    return None


def reverse_gap_cohort(conn: sqlite3.Connection, wid: str, kind: str) -> dict:
    """Cohort lỗ hổng dòng chảy + mẫu đơn + unmask path."""
    where_map = {
        "no_province": "(province IS NULL OR province = '')",
        "no_address": "(full_address IS NULL OR full_address = '')",
        "canceled_missing_phone": (
            "status = 'canceled' AND (receiver_phone IS NULL OR receiver_phone = '')"
        ),
        "mask_phone_delivered": "status = 'delivered' AND receiver_phone LIKE '%*%'",
        "submitted_no_province": (
            "lower(coalesce(status,'')) IN ('submitted','pending','new','confirmed') "
            "AND (province IS NULL OR province = '')"
        ),
        "no_district": "(district IS NULL OR district = '')",
        "delivered_no_timeline": (
            "status = 'delivered' AND (delivered_at IS NULL OR delivered_at = '')"
        ),
    }
    where = where_map.get(kind, "1=0")
    if kind not in where_map:
        return {
            "query_type": "gap_cohort",
            "query": kind,
            "hit": False,
            "error": f"unknown gap: {kind}",
            "path": f"gap:{kind} unknown",
        }
    n = conn.execute(
        f"SELECT COUNT(*) FROM orders WHERE warehouse_id = ? AND {where}", (wid,)
    ).fetchone()[0]
    samples = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT van_tay, so_noi_bo, tracking_code, status, province, ward,
                   full_address, receiver_name, receiver_phone, flow_path
            FROM orders WHERE warehouse_id = ? AND {where}
            ORDER BY piped_at DESC LIMIT 10
            """,
            (wid,),
        )
    ]
    by_status = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT status, COUNT(*) AS orders FROM orders
            WHERE warehouse_id = ? AND {where}
            GROUP BY status ORDER BY orders DESC
            """,
            (wid,),
        )
    ]
    unmask = {
        "no_province": {
            "path_id": "PATH-MISSING-GEO",
            "action": "recover_province_from_full_address_or_refetch",
        },
        "no_address": {
            "path_id": "PATH-MISSING",
            "action": "backfill_shipping_address_from_pancake",
        },
        "canceled_missing_phone": {
            "path_id": "PATH-MISSING",
            "action": "backfill_or_accept_canceled_without_phone",
        },
        "mask_phone_delivered": {
            "path_id": "PATH-MASK-REDACTION",
            "action": "fetch_unmasked_from_source_api",
        },
        "submitted_no_province": {
            "path_id": "PATH-MISSING-GEO",
            "action": "recover_province_from_full_address_before_ship",
        },
        "no_district": {
            "path_id": "PATH-MISSING-GEO",
            "action": "backfill_district_from_full_address_or_pancake",
        },
        "delivered_no_timeline": {
            "path_id": "PATH-MISSING",
            "action": "map_pancake_status_history_to_delivered_at",
        },
    }.get(kind, {"path_id": "PATH-UNKNOWN", "action": "inspect"})
    return _attach_flow(
        {
            "query_type": "gap_cohort",
            "query": kind,
            "hit": n > 0,
            "warehouse_id": wid,
            "count": n,
            "by_status": by_status,
            "sample_orders": [
                # wrap as minimal order dicts for panorama when enough fields
                {
                    **s,
                    "kho": "ASUMEE",
                    "warehouse_id": wid,
                    "backend": "OMS-pipe-bus",
                    "buucuc": "Pancake",
                }
                for s in samples
            ],
            "unmask_map": unmask,
            "path": f"gap:{kind} n={n} ← ASUMEE ← status×{len(by_status)}",
        }
    )


def _district_hint_from_address(addr: str) -> str | None:
    """Gợi ý huyện/quận từ full_address khi district trống."""
    if not addr:
        return None
    # Ưu tiên token rõ: Huyện / Quận / Thị xã / Thành phố (trừ tỉnh đứng sau)
    m = re.search(
        r"(Huyện\s+[^,;/|]+|Quận\s+[^,;/|]+|Thị\s+xã\s+[^,;/|]+|"
        r"Thành\s+phố\s+[^,;/|]+|Thành\s+Phố\s+[^,;/|]+)",
        addr,
        flags=re.IGNORECASE,
    )
    if m:
        hint = re.sub(r"\s+", " ", m.group(1)).strip(" .,")
        # Cắt đuôi "tỉnh …" nếu regex nuốt quá dài từ marketplace text
        hint = re.split(r"\s+tỉnh\s+", hint, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        if "*" in hint:
            return None
        if len(hint) >= 5:
            # Chuẩn hoá viết hoa nhẹ
            if hint.lower().startswith("huyện ") and not hint.startswith("Huyện"):
                hint = "Huyện " + hint[6:]
            elif hint.lower().startswith("quận ") and not hint.startswith("Quận"):
                hint = "Quận " + hint[5:]
            return hint
    parts = [p.strip() for p in re.split(r"[,/|]", addr) if p.strip()]
    for p in parts:
        low = p.lower()
        if low.startswith(("huyện ", "quận ", "thị xã ", "thành phố ")):
            if "*" in p:
                continue
            return p
    return None


def reverse_district_recover(conn: sqlite3.Connection, wid: str) -> dict:
    """Truy vấn ngược: thiếu district nhưng còn full_address / ward → gợi ý huyện."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, status, province, ward, full_address,
                   receiver_phone, tracking_code
            FROM orders
            WHERE warehouse_id = ?
              AND (district IS NULL OR district = '')
              AND full_address IS NOT NULL AND full_address != ''
            ORDER BY piped_at DESC LIMIT 40
            """,
            (wid,),
        )
    ]
    recovered = []
    for r in rows:
        hint = _district_hint_from_address(str(r.get("full_address") or ""))
        recovered.append(
            {
                "van_tay": r.get("van_tay"),
                "so_noi_bo": r.get("so_noi_bo"),
                "status": r.get("status"),
                "province": r.get("province"),
                "ward": r.get("ward"),
                "hint_district": hint,
                "full_address": (r.get("full_address") or "")[:120],
                "phone_class": (
                    "MASKED"
                    if r.get("receiver_phone") and "*" in str(r.get("receiver_phone"))
                    else ("MISSING" if not r.get("receiver_phone") else "OK")
                ),
            }
        )
    with_hint = [x for x in recovered if x.get("hint_district")]
    by_hint: dict[str, int] = {}
    for x in with_hint:
        h = str(x.get("hint_district"))
        by_hint[h] = by_hint.get(h, 0) + 1
    n_no_dist = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ? AND (district IS NULL OR district = '')
        """,
        (wid,),
    ).fetchone()[0]
    n_ward_only = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ?
          AND (district IS NULL OR district = '')
          AND ward IS NOT NULL AND ward != ''
        """,
        (wid,),
    ).fetchone()[0]
    return {
        "query_type": "district_recover",
        "query": wid,
        "hit": bool(with_hint) or n_no_dist > 0,
        "count": n_no_dist,
        "ward_without_district": n_ward_only,
        "sample_scanned": len(recovered),
        "recovered_hints": len(with_hint),
        "by_hint_district": [
            {"hint": k, "orders": v}
            for k, v in sorted(by_hint.items(), key=lambda kv: -kv[1])
        ][:15],
        "samples": with_hint[:10] or recovered[:8],
        "path": (
            f"district_recover: no_district={n_no_dist} ward_only={n_ward_only} "
            f"hints={len(with_hint)}/{len(recovered)}"
        ),
        "unmask_map": {
            "note": "Recover huyện từ address; PII vẫn MASK nếu có *",
            "path_id": "PATH-MISSING-GEO",
            "action": "backfill_district_from_full_address_or_pancake",
        },
        "next": [
            "Pipe lại shipping_address.district từ Pancake detail",
            "Ward có · district trống là pattern ASUMEE chính",
        ],
    }


def reverse_spx_like_tracking(conn: sqlite3.Connection, wid: str, limit: int = 14) -> dict:
    """Gắn aship URL cho mã SPX-like (26XXXXXXXXXXXX) dù buucuc=Pancake."""
    try:
        from tracking_aship import attach_tracking_urls, build_tracking_url
    except Exception as e:  # noqa: BLE001
        return {
            "query_type": "spx_like_tracking",
            "query": wid,
            "hit": False,
            "error": str(e),
            "path": "spx_like_tracking: module lỗi",
        }

    n = conn.execute(
        """
        SELECT COUNT(*) FROM orders
        WHERE warehouse_id = ?
          AND tracking_code GLOB '26*' AND length(tracking_code) = 14
        """,
        (wid,),
    ).fetchone()[0]
    by_status = [
        dict(r)
        for r in conn.execute(
            """
            SELECT status, COUNT(*) AS orders FROM orders
            WHERE warehouse_id = ?
              AND tracking_code GLOB '26*' AND length(tracking_code) = 14
            GROUP BY status ORDER BY orders DESC
            """,
            (wid,),
        )
    ]
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM orders
            WHERE warehouse_id = ?
              AND tracking_code GLOB '26*' AND length(tracking_code) = 14
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, limit),
        )
    ]
    attached = []
    drills: list[str] = []
    for o in rows:
        a = attach_tracking_urls(dict(o))
        # Force SPX URL nếu resolver chưa bắt (phiên bản cũ)
        if not a.get("tracking_url") and a.get("tracking_code"):
            a["tracking_provider"] = "spx"
            a["tracking_url"] = build_tracking_url(
                str(a["tracking_code"]), provider="spx", tracking_code=str(a["tracking_code"])
            )
        attached.append(
            {
                "van_tay": a.get("van_tay"),
                "so_noi_bo": a.get("so_noi_bo"),
                "tracking_code": a.get("tracking_code"),
                "status": a.get("status"),
                "province": a.get("province"),
                "buucuc": a.get("buucuc"),
                "carrier": a.get("carrier"),
                "tracking_provider": a.get("tracking_provider"),
                "tracking_url": a.get("tracking_url"),
                "has_url": bool(a.get("tracking_url")),
            }
        )
        tn = str(a.get("tracking_code") or "").strip()
        if tn and tn not in drills:
            drills.append(tn)

    return {
        "query_type": "spx_like_tracking",
        "query": wid,
        "hit": n > 0,
        "count": n,
        "by_status": by_status,
        "with_url": sum(1 for a in attached if a.get("has_url")),
        "samples": attached[:12],
        "path": (
            f"spx_like_tracking n={n} sample={len(attached)} "
            f"with_url={sum(1 for a in attached if a.get('has_url'))}"
        ),
        "unmask_map": {
            "note": "SPX URL ≠ unmask PII; mã 26* suy ra provider=spx",
            "path_id": "PATH-CLEAR" if any(a.get("has_url") for a in attached) else "PATH-MISSING",
        },
        "next": [
            "Probe aship URL nhẹ nếu egress cho phép",
            "Map buucuc/carrier=SPX khi pipe lại shipments",
        ],
        "_drill_tracking": drills[:8],
    }


def reverse_timeline_gap(conn: sqlite3.Connection, wid: str, limit: int = 12) -> dict:
    """Shipped/delivered nhưng thiếu picked_at / delivered_at."""
    row = dict(
        conn.execute(
            """
            SELECT
              SUM(CASE WHEN status='shipped'
                        AND (picked_at IS NULL OR picked_at='') THEN 1 ELSE 0 END)
                AS shipped_no_pick,
              SUM(CASE WHEN status='delivered'
                        AND (picked_at IS NULL OR picked_at='') THEN 1 ELSE 0 END)
                AS delivered_no_pick,
              SUM(CASE WHEN status='delivered'
                        AND (delivered_at IS NULL OR delivered_at='') THEN 1 ELSE 0 END)
                AS delivered_no_at,
              SUM(CASE WHEN status='shipped' THEN 1 ELSE 0 END) AS shipped,
              SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) AS delivered
            FROM orders WHERE warehouse_id = ?
            """,
            (wid,),
        ).fetchone()
    )
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, tracking_code, status, province, ward,
                   picked_at, delivered_at, created_at, buucuc
            FROM orders
            WHERE warehouse_id = ?
              AND status IN ('shipped', 'delivered')
              AND (picked_at IS NULL OR picked_at = ''
                   OR (status='delivered' AND (delivered_at IS NULL OR delivered_at='')))
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, limit),
        )
    ]
    return {
        "query_type": "timeline_gap",
        "query": wid,
        "hit": (
            (row.get("shipped_no_pick") or 0) > 0
            or (row.get("delivered_no_pick") or 0) > 0
            or (row.get("delivered_no_at") or 0) > 0
        ),
        "count": (row.get("shipped_no_pick") or 0)
        + (row.get("delivered_no_at") or 0),
        "gaps": row,
        "samples": samples,
        "path": (
            f"timeline_gap: shipped_no_pick={row.get('shipped_no_pick')}/"
            f"{row.get('shipped')} delivered_no_pick={row.get('delivered_no_pick')}/"
            f"{row.get('delivered')} delivered_no_at={row.get('delivered_no_at')}/"
            f"{row.get('delivered')}"
        ),
        "unmask_map": {
            "path_id": "PATH-MISSING",
            "action": "map_pancake_status_history_to_picked_delivered_at",
        },
        "next": [
            "Pipe status history / last_partner_update từ Pancake detail",
            "Không suy diễn delivered_at từ piped_at",
        ],
    }


def reverse_pipe_events_asumee(conn: sqlite3.Connection, wid: str) -> dict:
    """Đếm pipe_events gắn van_tay ASUMEE — thường 0 (thiếu event bus)."""
    n = conn.execute(
        """
        SELECT COUNT(*) FROM pipe_events pe
        WHERE pe.van_tay IN (
          SELECT van_tay FROM orders WHERE warehouse_id = ?
        )
        """,
        (wid,),
    ).fetchone()[0]
    by_event = [
        dict(r)
        for r in conn.execute(
            """
            SELECT pe.event, COUNT(*) AS n FROM pipe_events pe
            WHERE pe.van_tay IN (
              SELECT van_tay FROM orders WHERE warehouse_id = ?
            )
            GROUP BY pe.event ORDER BY n DESC LIMIT 12
            """,
            (wid,),
        )
    ]
    recent = [
        dict(r)
        for r in conn.execute(
            """
            SELECT pe.at, pe.event, pe.van_tay, pe.so_noi_bo,
                   substr(pe.detail, 1, 80) AS detail
            FROM pipe_events pe
            WHERE pe.van_tay IN (
              SELECT van_tay FROM orders WHERE warehouse_id = ?
            )
            ORDER BY pe.id DESC LIMIT 8
            """,
            (wid,),
        )
    ]
    return {
        "query_type": "pipe_events",
        "query": wid,
        "hit": True,
        "count": n,
        "by_event": by_event,
        "samples": recent,
        "path": f"pipe_events ASUMEE n={n} event_types={len(by_event)}",
        "unmask_map": {
            "path_id": "PATH-MISSING" if n == 0 else "PATH-CLEAR",
            "action": "emit_pipe_events_on_status_change_for_asumee",
        },
        "next": [
            "Bật pipe_events khi sync ASUMEE (status/pick/deliver)",
            "Không có event → không truy ngược timeline từ bus",
        ],
    }


def reverse_geo_recover(conn: sqlite3.Connection, wid: str) -> dict:
    """Truy vấn ngược: đơn thiếu province nhưng còn full_address → gợi ý geo."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, status, full_address, receiver_name, receiver_phone
            FROM orders
            WHERE warehouse_id = ?
              AND (province IS NULL OR province = '')
              AND full_address IS NOT NULL AND full_address != ''
            ORDER BY piped_at DESC LIMIT 25
            """,
            (wid,),
        )
    ]
    recovered = []
    for r in rows:
        hint = _geo_hint_from_address(str(r.get("full_address") or ""))
        recovered.append(
            {
                "van_tay": r.get("van_tay"),
                "so_noi_bo": r.get("so_noi_bo"),
                "status": r.get("status"),
                "hint_province": hint,
                "full_address": (r.get("full_address") or "")[:120],
                "receiver_phone": r.get("receiver_phone"),
                "phone_class": (
                    "MASKED"
                    if r.get("receiver_phone") and "*" in str(r.get("receiver_phone"))
                    else ("MISSING" if not r.get("receiver_phone") else "OK")
                ),
            }
        )
    by_hint: dict[str, int] = {}
    for x in recovered:
        h = x.get("hint_province") or "(none)"
        by_hint[h] = by_hint.get(h, 0) + 1
    return {
        "query_type": "geo_recover",
        "query": wid,
        "hit": bool(recovered),
        "count": len(recovered),
        "by_hint_province": [
            {"hint": k, "orders": v}
            for k, v in sorted(by_hint.items(), key=lambda kv: -kv[1])
        ],
        "samples": recovered[:12],
        "path": f"geo_recover: {len(recovered)} đơn thiếu province → hint×{len(by_hint)}",
        "unmask_map": {
            "note": "Recover geo từ address text; PII vẫn MASK nếu có *",
            "path_id": "PATH-MASK-REDACTION",
        },
    }


def reverse_flow_gaps(conn: sqlite3.Connection, wid: str) -> dict:
    """Phân tích lỗ hổng dòng chảy ngược (thiếu tỉnh/huyện/SĐT/pick)."""
    row = dict(
        conn.execute(
            """
            SELECT
              COUNT(*) AS orders,
              SUM(CASE WHEN province IS NULL OR province='' THEN 1 ELSE 0 END) AS no_province,
              SUM(CASE WHEN district IS NULL OR district='' THEN 1 ELSE 0 END) AS no_district,
              SUM(CASE WHEN ward IS NULL OR ward='' THEN 1 ELSE 0 END) AS no_ward,
              SUM(CASE WHEN full_address IS NULL OR full_address='' THEN 1 ELSE 0 END) AS no_address,
              SUM(CASE WHEN receiver_phone IS NULL OR receiver_phone='' THEN 1 ELSE 0 END) AS no_phone,
              SUM(CASE WHEN receiver_phone LIKE '%*%' THEN 1 ELSE 0 END) AS mask_phone,
              SUM(CASE WHEN picked_at IS NULL THEN 1 ELSE 0 END) AS no_picked,
              SUM(CASE WHEN delivered_at IS NULL THEN 1 ELSE 0 END) AS no_delivered_at,
              SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) AS status_delivered,
              SUM(CASE WHEN status='shipped' THEN 1 ELSE 0 END) AS status_shipped
            FROM orders WHERE warehouse_id = ?
            """,
            (wid,),
        ).fetchone()
    )
    return {
        "query_type": "flow_gaps",
        "query": wid,
        "hit": True,
        "gaps": row,
        "unmask_map": {
            "mask_phone": row.get("mask_phone"),
            "path_id": "PATH-MASK-REDACTION",
            "action": "fetch_unmasked_from_source_api",
        },
        "path": (
            f"gaps ASUMEE: no_prov={row.get('no_province')} no_dist={row.get('no_district')} "
            f"mask_phone={row.get('mask_phone')} shipped={row.get('status_shipped')} "
            f"delivered_status={row.get('status_delivered')} (picked_at/delivered_at trống)"
        ),
        "next": [
            "Bổ sung district khi pipe (ward có, district thiếu)",
            "Picked/delivered_at chưa map từ Pancake status history",
            "SĐT MASK → --unmask / --asunmee --live",
        ],
    }


def reverse_by_status_warehouse(
    conn: sqlite3.Connection, wid: str, status: str, limit: int = 15
) -> dict:
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM orders
            WHERE warehouse_id = ? AND status = ?
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, status, limit),
        )
    ]
    dest = [
        dict(r)
        for r in conn.execute(
            """
            SELECT province, ward, COUNT(*) AS orders
            FROM orders WHERE warehouse_id = ? AND status = ?
              AND province IS NOT NULL AND province != ''
            GROUP BY province, ward ORDER BY orders DESC LIMIT 20
            """,
            (wid, status),
        )
    ]
    phone = [
        dict(r)
        for r in conn.execute(
            """
            SELECT
              CASE
                WHEN receiver_phone IS NULL OR receiver_phone = '' THEN 'MISSING'
                WHEN instr(receiver_phone, '*') > 0 THEN 'MASKED'
                ELSE 'OK'
              END AS phone_class,
              COUNT(*) AS orders
            FROM orders WHERE warehouse_id = ? AND status = ?
            GROUP BY 1
            """,
            (wid, status),
        )
    ]
    return _attach_flow(
        {
            "query_type": "status_warehouse",
            "query": f"{status}@{wid[:8]}",
            "hit": bool(samples),
            "status": status,
            "warehouse_id": wid,
            "count": conn.execute(
                "SELECT COUNT(*) FROM orders WHERE warehouse_id=? AND status=?",
                (wid, status),
            ).fetchone()[0],
            "destination_wards": dest,
            "phone_class": phone,
            "sample_orders": samples,
            "path": f"status:{status} ← kho:ASUMEE ← tỉnh/ward×{len(dest)} ← đơn×{len(samples)}",
        }
    )


def reverse_by_ward_warehouse(
    conn: sqlite3.Connection, wid: str, ward: str, limit: int = 12
) -> dict:
    samples = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM orders
            WHERE warehouse_id = ? AND ward LIKE ?
            ORDER BY piped_at DESC LIMIT ?
            """,
            (wid, f"%{ward}%", limit),
        )
    ]
    by_status = [
        dict(r)
        for r in conn.execute(
            """
            SELECT status, COUNT(*) AS orders FROM orders
            WHERE warehouse_id = ? AND ward LIKE ?
            GROUP BY status ORDER BY orders DESC
            """,
            (wid, f"%{ward}%"),
        )
    ]
    return _attach_flow(
        {
            "query_type": "ward_warehouse",
            "query": ward,
            "hit": bool(samples),
            "warehouse_id": wid,
            "by_status": by_status,
            "sample_orders": samples,
            "path": f"ward:{ward} ← ASUMEE ← status×{len(by_status)}",
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
    warehouse_id: str | None = None,
    continue_asumee: bool = False,
    continue_flow: bool = False,
    hop6_live: bool = True,
    hop6_apply: bool = False,
    hop6_limit: int = 8,
    hop7_live: bool = True,
    hop7_apply: bool = False,
    hop7_limit: int = 40,
    hop8_apply: bool = False,
    hop8_probe: bool = False,
    hop8_probe_limit: int = 6,
    hop9_live: bool = False,
    hop9_apply: bool = False,
    hop9_limit: int = 40,
    hop10_apply: bool = False,
    hop11_live: bool = False,
    hop11_apply: bool = False,
    hop11_limit: int = 40,
    hop12_live: bool = False,
    hop12_apply: bool = False,
    hop12_limit: int = 40,
    hop12_probe: bool = False,
    hop13_live: bool = False,
    hop13_apply: bool = False,
    hop13_limit: int = 60,
) -> dict:
    from realtime_icon_feedback_mapper import chant, feedback_line, receive_fingerprint

    conn = ensure_pipe_or_build()
    results: list[dict] = []

    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.I,
    )

    # «Tiếp tục ngược dòng chảy» → deep + hop2 (gaps cohort / geo recover / icon)
    if continue_flow or continue_asumee:
        results.extend(
            reverse_chain_asumee(
                conn,
                deep=True,
                hop2=True,
                hop6_live=hop6_live,
                hop6_apply=hop6_apply,
                hop6_limit=hop6_limit,
                hop7_live=hop7_live,
                hop7_apply=hop7_apply,
                hop7_limit=hop7_limit,
                hop8_apply=hop8_apply,
                hop8_probe=hop8_probe,
                hop8_probe_limit=hop8_probe_limit,
                hop9_live=hop9_live,
                hop9_apply=hop9_apply,
                hop9_limit=hop9_limit,
                hop10_apply=hop10_apply,
                hop11_live=hop11_live,
                hop11_apply=hop11_apply,
                hop11_limit=hop11_limit,
                hop12_live=hop12_live,
                hop12_apply=hop12_apply,
                hop12_limit=hop12_limit,
                hop12_probe=hop12_probe,
                hop13_live=hop13_live,
                hop13_apply=hop13_apply,
                hop13_limit=hop13_limit,
            )
        )

    if q:
        qq = q.strip()
        if uuid_re.match(qq):
            results.append(reverse_by_warehouse_id(conn, qq))
        elif len(qq) == 16 and all(c in "0123456789abcdef" for c in qq.lower()):
            results.append(reverse_by_van_tay(conn, qq.lower()))
        elif qq.upper().startswith(("SPX", "GHN", "VTP", "VN")):
            results.append(reverse_by_tracking(conn, qq))
        elif re.search(r"kho|smart|hcm|asumee|asunmee", qq, re.I):
            results.append(reverse_by_kho(conn, qq))
        elif qq.upper() in {"SPX", "GHN", "VIETTELPOST", "VNPOST"} or "DANG_GIAO" in qq.upper() or "UNASSIGNED" in qq.upper():
            results.append(reverse_by_buucuc(conn, qq))
        elif re.search(
            r"tỉnh|thành|nam định|sơn la|nghệ an|hà nội|hải|đắk|dak|bắc ninh",
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

    if warehouse_id:
        results.append(reverse_by_warehouse_id(conn, warehouse_id.strip()))
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

    demo_mode = not any(
        [
            van_tay,
            so_noi_bo,
            tracking,
            kho,
            buucuc,
            province,
            address,
            icon,
            q,
            warehouse_id,
            continue_asumee,
            continue_flow,
        ]
    )
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
            "python3 scripts/order_pipe_reverse_query.py --continue-flow --hop13-live --hop13-apply",
            "python3 scripts/order_pipe_reverse_query.py --hop7-apply --hop7-limit 200",
            "python3 scripts/order_pipe_reverse_query.py --continue-flow --hop12-live --hop12-apply --hop12-probe",
            "python3 scripts/order_pipe_reverse_query.py --continue-flow --hop11-live --hop11-apply",
            "python3 scripts/order_pipe_reverse_query.py --continue-asumee",
            "python3 scripts/order_pipe_reverse_query.py --kho ASUMEE",
            "python3 scripts/crypto_decode_assist.py --unmask",
            "python3 scripts/inner_unmask_deep_mapper.py --warehouse 55e5f0e1-ed06-4dad-b35a-406bee25cdea",
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
        if st.get("id") == "van_don" and (st.get("meta") or {}).get("tracking_url"):
            L(f"       aship: {st['meta']['tracking_url']}")
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
        if r.get("by_status"):
            for m in r["by_status"][:8]:
                L(f"  · status {m.get('status')}: n={m.get('orders')} masked={m.get('masked')}")
        if r.get("phone_class"):
            for m in r["phone_class"]:
                L(f"  · phone_class {m.get('phone_class')}: n={m.get('orders')}")
        if r.get("unmask_map"):
            L(f"  · unmask_map={r.get('unmask_map')}")
        if r.get("chain_fingerprints"):
            for fp in r["chain_fingerprints"][:5]:
                L(
                    f"  · chain van_tay={fp.get('van_tay')} so={fp.get('so_noi_bo')} "
                    f"→ {fp.get('buucuc')} → {fp.get('province')} [{fp.get('status')}] "
                    f"phone={fp.get('phone_class')}"
                )
        if r.get("gaps"):
            L(f"  · gaps={r.get('gaps')}")
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("destination_wards"):
            for m in r["destination_wards"][:8]:
                L(
                    f"  · ward {m.get('province')}/{m.get('ward')}: n={m.get('orders')}"
                )
        if r.get("by_hint_province"):
            for m in r["by_hint_province"][:8]:
                L(f"  · geo_hint {m.get('hint')}: n={m.get('orders')}")
        if r.get("clusters"):
            L(f"  · warning={r.get('warning')}")
            for c in r["clusters"][:8]:
                L(
                    f"  · cluster mask={c.get('mask_display')!r} n={c.get('orders')} "
                    f"st={c.get('statuses')} tỉnh={c.get('provinces')} fp={c.get('fingerprints')}"
                )
        if r.get("samples") and r.get("query_type") == "geo_recover":
            for s in r["samples"][:5]:
                L(
                    f"  · recover van_tay={s.get('van_tay')} hint={s.get('hint_province')!r} "
                    f"phone={s.get('phone_class')} addr={(s.get('full_address') or '')[:50]!r}"
                )
        if r.get("query_type") == "tracking_url_attach":
            L(
                f"  · with_url={r.get('with_url')} id_as_tracking={r.get('id_as_tracking')} "
                f"providers={r.get('providers')}"
            )
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · trk={s.get('tracking_code')} st={s.get('status')} "
                    f"url={'(yes)' if s.get('has_url') else '∅'} "
                    f"prov={s.get('tracking_provider') or s.get('province') or '∅'} "
                    f"buu={s.get('buucuc')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "district_recover":
            L(
                f"  · no_district={r.get('count')} ward_only={r.get('ward_without_district')} "
                f"hints={r.get('recovered_hints')}/{r.get('sample_scanned')}"
            )
            for m in (r.get("by_hint_district") or [])[:8]:
                L(f"  · district_hint {m.get('hint')}: n={m.get('orders')}")
            for s in (r.get("samples") or [])[:5]:
                L(
                    f"  · recover van_tay={s.get('van_tay')} dist={s.get('hint_district')!r} "
                    f"ward={s.get('ward')!r} prov={s.get('province')!r}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "spx_like_tracking":
            L(f"  · with_url={r.get('with_url')} by_status={r.get('by_status')}")
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · trk={s.get('tracking_code')} st={s.get('status')} "
                    f"url={'(yes)' if s.get('has_url') else '∅'} "
                    f"prov={s.get('tracking_provider') or '∅'} "
                    f"buu={s.get('buucuc')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "timeline_gap":
            L(f"  · gaps={r.get('gaps')}")
            for s in (r.get("samples") or [])[:5]:
                L(
                    f"  · {s.get('van_tay')} st={s.get('status')} "
                    f"pick={s.get('picked_at') or '∅'} deliver={s.get('delivered_at') or '∅'} "
                    f"trk={s.get('tracking_code')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "pipe_events":
            L(f"  · events={r.get('count')} by_event={r.get('by_event')}")
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "district_backfill_plan":
            L(
                f"  · candidates={r.get('count')}/{r.get('scanned')} "
                f"apply={r.get('apply')} applied={r.get('applied')}"
            )
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · {s.get('van_tay')} → district={s.get('district_new')!r} "
                    f"ward={s.get('ward')!r} st={s.get('status')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "tracking_classify":
            for m in r.get("by_kind") or []:
                L(
                    f"  · kind {m.get('kind')}: n={m.get('orders')} "
                    f"id_as_tracking={m.get('id_as_tracking')}"
                )
        if r.get("query_type") == "pipe_events_plan":
            L(
                f"  · planned={r.get('count')} existing={r.get('existing_events')} "
                f"apply={r.get('apply')} applied={r.get('applied')} after={r.get('events_after')}"
            )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "pancake_detail_backfill":
            if r.get("skipped"):
                L("  · skipped live detail")
            else:
                L(
                    f"  · ok={r.get('ok')}/{r.get('count')} summary={r.get('summary')} "
                    f"apply={r.get('apply')} applied={r.get('applied')}"
                )
                for s in (r.get("samples") or [])[:8]:
                    if not s.get("ok"):
                        L(
                            f"  · so={s.get('so_noi_bo')} FAIL err={str(s.get('error'))[:60]}"
                        )
                        continue
                    L(
                        f"  · so={s.get('so_noi_bo')} st={s.get('status_api') or s.get('status_pipe')} "
                        f"dist={s.get('district_api') or '∅'} "
                        f"trk={s.get('tracking_api') or '∅'}@{s.get('tracking_source') or '∅'} "
                        f"partner={s.get('partner_name') or '∅'}/{s.get('provider') or '∅'} "
                        f"pick={s.get('picked_at_api') or '∅'} del={s.get('delivered_at_api') or '∅'} "
                        f"hist={s.get('histories_n')} sig={s.get('timeline_signals')}"
                    )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "batch_timeline_backfill":
            if r.get("skipped"):
                L("  · skipped batch timeline")
            else:
                L(
                    f"  · ok={r.get('ok')}/{r.get('count')} apply={r.get('apply')} "
                    f"applied={r.get('applied')} remain_gap={r.get('remaining_timeline_gaps')} "
                    f"hard={r.get('remain_hard')} soft_no_pick={r.get('remain_soft_delivered_no_pick')} "
                    f"partners={r.get('partners')}"
                )
                for s in (r.get("samples") or [])[:8]:
                    if not s.get("ok"):
                        L(f"  · so={s.get('so_noi_bo')} FAIL err={str(s.get('error'))[:60]}")
                        continue
                    L(
                        f"  · so={s.get('so_noi_bo')} st={s.get('status_pipe')} "
                        f"trk={s.get('tracking_api') or '∅'} "
                        f"car={s.get('carrier_new') or '∅'}/{s.get('buucuc_new') or '∅'} "
                        f"pick={s.get('picked_at_api') or '∅'} del={s.get('delivered_at_api') or '∅'}"
                    )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "carrier_buucuc_remap":
            L(
                f"  · candidates={r.get('count')} apply={r.get('apply')} "
                f"applied={r.get('applied')} by_buucuc={r.get('by_buucuc_new')}"
            )
            for m in (r.get("matrix") or [])[:10]:
                L(
                    f"  · matrix car={m.get('carrier')} buu={m.get('buucuc')}: n={m.get('orders')}"
                )
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · {s.get('van_tay')} {s.get('carrier_old')}→{s.get('carrier_new')} "
                    f"{s.get('buucuc_old')}→{s.get('buucuc_new')} trk={s.get('tracking_code')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "hard_soft_gaps":
            L(
                f"  · hard_del={r.get('hard_delivered_no_at')} "
                f"hard_ship={r.get('hard_shipped_no_pick')} "
                f"soft_no_pick={r.get('soft_delivered_no_pick')}"
            )
            for m in (r.get("by_carrier_status") or [])[:10]:
                L(
                    f"  · {m.get('carrier')} / {m.get('status')}: n={m.get('orders')}"
                )
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · {s.get('so_noi_bo')} st={s.get('status')} car={s.get('carrier')} "
                    f"pick={s.get('picked_at') or '∅'} del={s.get('delivered_at') or '∅'} "
                    f"trk={s.get('tracking_code')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "three_pl_completeness":
            for m in (r.get("matrix") or [])[:10]:
                L(
                    f"  · {m.get('carrier')}/{m.get('buucuc')}: n={m.get('orders')} "
                    f"trk={m.get('trk_real')} url={m.get('with_url')} "
                    f"pick={m.get('with_pick')} del={m.get('with_del')} "
                    f"ship={m.get('shipped')} done={m.get('delivered')}"
                )
        if r.get("query_type") == "aship_url_sync":
            L(
                f"  · fix={r.get('count')}/{r.get('scanned')} apply={r.get('apply')} "
                f"applied={r.get('applied')} with_url={r.get('with_url')} "
                f"missing={r.get('missing_url_real_trk')} by_prov={r.get('by_provider')}"
            )
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · trk={s.get('tracking_code')} "
                    f"prov={s.get('provider_old')}→{s.get('provider_new')} "
                    f"url={'yes' if s.get('url_new') else '∅'} car={s.get('carrier')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "aship_probe":
            L(f"  · ok={r.get('ok')}/{r.get('count')}")
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · trk={s.get('tracking_code')} prov={s.get('provider')} "
                    f"http={s.get('http')} ok={s.get('ok')} "
                    f"err={str(s.get('error') or '')[:40]}"
                )
        if r.get("query_type") == "pancake_id_cohort":
            L(f"  · by_status={r.get('by_status')}")
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · so={s.get('so_noi_bo')} st={s.get('status')} "
                    f"car={s.get('carrier')} prov={s.get('province') or '∅'}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "pancake_id_backfill":
            if r.get("skipped"):
                L("  · skipped live pancake-id backfill")
            else:
                L(
                    f"  · ok={r.get('ok')}/{r.get('count')} real_trk={r.get('got_real_tracking')} "
                    f"apply={r.get('apply')} applied={r.get('applied')} "
                    f"remain={r.get('remain_pancake_id')} partners={r.get('partners')}"
                )
                for s in (r.get("samples") or [])[:8]:
                    if not s.get("ok"):
                        L(f"  · so={s.get('so_noi_bo')} FAIL err={str(s.get('error'))[:50]}")
                        continue
                    L(
                        f"  · so={s.get('so_noi_bo')} st={s.get('status_pipe')} "
                        f"real={s.get('has_real_tracking')} trk={s.get('tracking_api') or '∅'} "
                        f"car={s.get('carrier_new') or '∅'}/{s.get('buucuc_new') or '∅'}"
                    )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "three_pl_province":
            for m in (r.get("matrix") or [])[:12]:
                L(
                    f"  · {m.get('buucuc')} → {m.get('province')}: n={m.get('orders')} "
                    f"pick={m.get('with_pick')} del={m.get('with_del')}"
                )
        if r.get("query_type") == "soft_gap_accept":
            L(
                f"  · soft={r.get('count')} apply={r.get('apply')} "
                f"applied={r.get('applied')} by={r.get('by_carrier')}"
            )
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · {s.get('so_noi_bo')} car={s.get('carrier')} "
                    f"del={s.get('delivered_at') or '∅'} pick=∅ trk={s.get('tracking_code')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "spx_marketplace_promote":
            L(
                f"  · candidates={r.get('count')}/{r.get('scanned')} apply={r.get('apply')} "
                f"applied={r.get('applied')}"
            )
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · {s.get('so_noi_bo')} st={s.get('status')} "
                    f"{s.get('carrier_old')}→SPX {s.get('buucuc_old')}→SPX "
                    f"trk={s.get('tracking_code')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "flow_completeness":
            sc = r.get("scores") or {}
            fills = r.get("fills") or {}
            L(
                f"  · scores 3pl={sc.get('three_pl_pct')}% url={sc.get('url_pct')}% "
                f"pick={sc.get('pick_pct')}% del={sc.get('del_pct')}% "
                f"trk_real={sc.get('trk_real_pct')}% dist={sc.get('district_pct')}%"
            )
            L(
                f"  · fills trk_real={fills.get('trk_real')} spx26={fills.get('spx_market_id')} "
                f"url={fills.get('with_url')} pick={fills.get('with_pick')} "
                f"del={fills.get('with_del')} 3pl={fills.get('with_3pl')} "
                f"pancake={fills.get('still_pancake')}"
            )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "canceled_pancake_id":
            L(f"  · canceled_pancake_id n={r.get('count')}")
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "submitted_waiting":
            L(f"  · waiting={r.get('count')} by_prov={r.get('by_province')}")
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · so={s.get('so_noi_bo')} prov={s.get('province') or '∅'} "
                    f"ward={s.get('ward') or '∅'}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "returning_cohort":
            L(f"  · returning={r.get('count')} by_kind={r.get('by_kind')}")
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · so={s.get('so_noi_bo')} car={s.get('carrier')}/{s.get('buucuc')} "
                    f"trk={s.get('tracking_code')} prov={s.get('province') or '∅'}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "hard_gap_refetch":
            if r.get("skipped"):
                L("  · skipped live hard-gap refetch")
            else:
                L(
                    f"  · ok={r.get('ok')}/{r.get('count')} pick={r.get('got_pick')} "
                    f"del={r.get('got_del')} empty={r.get('empty_hist_or_extend')} "
                    f"apply={r.get('apply')} applied={r.get('applied')} "
                    f"remain={r.get('remain_hard')} partners={r.get('partners')}"
                )
                for s in (r.get("samples") or [])[:8]:
                    if not s.get("ok"):
                        L(f"  · so={s.get('so_noi_bo')} FAIL err={str(s.get('error'))[:50]}")
                        continue
                    L(
                        f"  · so={s.get('so_noi_bo')} st={s.get('status_pipe')} "
                        f"pick={s.get('picked_at_api') or '∅'} del={s.get('delivered_at_api') or '∅'} "
                        f"hist={s.get('hist_n')} ext={s.get('extend_n')} "
                        f"trk={s.get('tracking_api') or s.get('tracking_pipe')}"
                    )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "hard_gap_accept":
            L(
                f"  · hard={r.get('count')} apply={r.get('apply')} "
                f"applied={r.get('applied')} by={r.get('by_carrier')}"
            )
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · {s.get('so_noi_bo')} st={s.get('status')} car={s.get('carrier')} "
                    f"pick={s.get('picked_at') or '∅'} del={s.get('delivered_at') or '∅'} "
                    f"trk={s.get('tracking_code')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "commune_district_apply":
            L(
                f"  · hints={r.get('count')}/{r.get('scanned')} apply={r.get('apply')} "
                f"applied={r.get('applied')} ward_only={r.get('ward_without_district')} "
                f"with_district={r.get('with_district')}"
            )
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · {s.get('so_noi_bo')} hint={s.get('hint_district')!r} "
                    f"ward={s.get('ward')!r} prov={s.get('province')!r}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "open_path_scorecard":
            L(f"  · paths={r.get('paths')}")
            fills = r.get("fills") or {}
            L(
                f"  · wait submitted={fills.get('submitted_wait')} new={fills.get('new_wait')} "
                f"returning_id={fills.get('returning_pancake_id')} "
                f"returning_trk={fills.get('returning_real_trk')} "
                f"hard={fills.get('hard_del')}+{fills.get('hard_ship')} "
                f"soft={fills.get('soft_del')}"
            )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "waiting_live_backfill":
            if r.get("skipped"):
                L("  · skipped live waiting backfill")
            else:
                L(
                    f"  · ok={r.get('ok')}/{r.get('count')} real_trk={r.get('got_real_tracking')} "
                    f"apply={r.get('apply')} applied={r.get('applied')} "
                    f"remain={r.get('remain_waiting_id')} partners={r.get('partners')}"
                )
                for s in (r.get("samples") or [])[:8]:
                    if not s.get("ok"):
                        L(f"  · so={s.get('so_noi_bo')} FAIL err={str(s.get('error'))[:50]}")
                        continue
                    L(
                        f"  · so={s.get('so_noi_bo')} st={s.get('status_pipe')} "
                        f"real={s.get('has_real_tracking')} trk={s.get('tracking_api') or '∅'} "
                        f"car={s.get('carrier_new') or '∅'}/{s.get('buucuc_new') or '∅'} "
                        f"pick={s.get('picked_at_api') or '∅'} del={s.get('delivered_at_api') or '∅'}"
                    )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "hard_gap_aship_probe":
            L(f"  · ok={r.get('ok')}/{r.get('count')}")
            for s in (r.get("samples") or [])[:6]:
                L(
                    f"  · trk={s.get('tracking_code')} st={s.get('status')} "
                    f"http={s.get('http')} ok={s.get('ok')}"
                )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "flow_closure":
            L(f"  · closed={r.get('closed')}")
            L(f"  · open={r.get('open')}")
            L(f"  · scores={r.get('scores')}")
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "wait_path_accept":
            L(
                f"  · wait={r.get('count')} apply={r.get('apply')} "
                f"applied={r.get('applied')} by_status={r.get('by_status')}"
            )
            for m in (r.get("by_province") or [])[:8]:
                L(f"  · tỉnh {m.get('province')}: n={m.get('orders')}")
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("query_type") == "submitted_confirm_scan":
            if r.get("skipped"):
                L("  · skipped live submitted confirm scan")
            else:
                L(
                    f"  · ok={r.get('ok')}/{r.get('count')} real={r.get('got_real_tracking')} "
                    f"partner={r.get('got_partner')} apply={r.get('apply')} "
                    f"applied={r.get('applied')} partners={r.get('partners')}"
                )
                for s in (r.get("samples") or [])[:8]:
                    if not s.get("ok"):
                        L(f"  · so={s.get('so_noi_bo')} FAIL err={str(s.get('error'))[:50]}")
                        continue
                    L(
                        f"  · so={s.get('so_noi_bo')} st={s.get('status_pipe')} "
                        f"partner={s.get('partner_name')} real={s.get('has_real_tracking')} "
                        f"trk={s.get('tracking_api') or '∅'} ext={s.get('extend_code') or '∅'}"
                    )
            for n in r.get("next") or []:
                L(f"    → {n}")
        if r.get("gap_cohort"):
            L(f"  · gap_cohort={r.get('gap_cohort')}")
        if r.get("count") and r.get("query_type") in {
            "status_warehouse",
            "ward_warehouse",
            "flow_gaps",
            "gap_cohort",
            "geo_recover",
            "phone_ok_contrast",
            "mask_phone_clusters",
            "tracking_url_attach",
            "district_recover",
            "spx_like_tracking",
            "timeline_gap",
            "pipe_events",
            "district_backfill_plan",
            "tracking_classify",
            "pipe_events_plan",
            "pancake_detail_backfill",
            "batch_timeline_backfill",
            "carrier_buucuc_remap",
            "hard_soft_gaps",
            "three_pl_completeness",
            "aship_url_sync",
            "aship_probe",
            "pancake_id_cohort",
            "pancake_id_backfill",
            "three_pl_province",
            "soft_gap_accept",
            "spx_marketplace_promote",
            "flow_completeness",
            "canceled_pancake_id",
            "submitted_waiting",
            "returning_cohort",
            "hard_gap_refetch",
            "hard_gap_accept",
            "commune_district_apply",
            "open_path_scorecard",
            "waiting_live_backfill",
            "hard_gap_aship_probe",
            "flow_closure",
            "wait_path_accept",
            "submitted_confirm_scan",
        }:
            L(f"  · count={r.get('count')} status={r.get('status')}")
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


def scrub_phones_in_obj(obj: Any, depth: int = 0) -> Any:
    """Che SĐT đầy đủ trong report (kể cả PATH-CLEAR) — chỉ giữ mask_phone()."""
    if depth > 12:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in {
                "receiver_phone",
                "customer_phone",
                "bill_phone_number",
                "phone",
            } and isinstance(v, str) and v and "*" not in v and len(re.sub(r"\D", "", v)) >= 9:
                out[k] = mask_phone(v)
                out[f"{k}_scrubbed"] = True
            else:
                out[k] = scrub_phones_in_obj(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [scrub_phones_in_obj(x, depth + 1) for x in obj]
    return obj


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    scrubbed = scrub_phones_in_obj(report)
    text = format_text(scrubbed)
    payload = json.dumps(scrubbed, ensure_ascii=False, indent=2, default=list)
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
    ap.add_argument("--warehouse", dest="warehouse_id", help="UUID warehouse_id (vd ASUMEE)")
    ap.add_argument(
        "--continue-asumee",
        action="store_true",
        help="Chuỗi truy vấn ngược đào sâu kho ASUMEE (warehouse→kho→tỉnh→van_tay→buucuc)",
    )
    ap.add_argument(
        "--continue-flow",
        action="store_true",
        help="Tiếp tục ngược dòng chảy ASUMEE deep+hop2…hop13",
    )
    ap.add_argument(
        "--hop6-live",
        action="store_true",
        help="Hop6: GET Pancake detail owned",
    )
    ap.add_argument(
        "--hop6-offline",
        action="store_true",
        help="Hop6: không gọi live detail",
    )
    ap.add_argument(
        "--hop6-apply",
        action="store_true",
        help="Hop6: ghi district/timeline/tracking/pipe_events vào DB (mặc định dry-run)",
    )
    ap.add_argument(
        "--hop6-limit",
        type=int,
        default=8,
        help="Hop6: số đơn detail live (default 8)",
    )
    ap.add_argument(
        "--hop7-apply",
        action="store_true",
        help="Hop7: batch backfill timeline/tracking + remap carrier/buucuc",
    )
    ap.add_argument(
        "--hop7-offline",
        action="store_true",
        help="Hop7: bỏ live batch detail",
    )
    ap.add_argument(
        "--hop7-limit",
        type=int,
        default=40,
        help="Hop7: số đơn batch detail (default 40)",
    )
    ap.add_argument(
        "--hop8-apply",
        action="store_true",
        help="Hop8: sync aship URL/provider + drill 3PL (ghi DB)",
    )
    ap.add_argument(
        "--hop8-probe",
        action="store_true",
        help="Hop8: probe nhẹ vài aship URL",
    )
    ap.add_argument(
        "--hop8-probe-limit",
        type=int,
        default=6,
        help="Hop8: số URL probe (default 6)",
    )
    ap.add_argument(
        "--hop9-live",
        action="store_true",
        help="Hop9: GET detail cho đơn tracking=order_id",
    )
    ap.add_argument(
        "--hop9-apply",
        action="store_true",
        help="Hop9: ghi tracking/district/carrier từ detail + district hints",
    )
    ap.add_argument(
        "--hop9-limit",
        type=int,
        default=40,
        help="Hop9: số đơn pancake-id live (default 40)",
    )
    ap.add_argument(
        "--hop10-apply",
        action="store_true",
        help="Hop10: soft-gap PATH-ACCEPT + promote SPX marketplace 26*",
    )
    ap.add_argument(
        "--hop11-live",
        action="store_true",
        help="Hop11: GET detail hard-gap (shipped∅pick / delivered∅del)",
    )
    ap.add_argument(
        "--hop11-apply",
        action="store_true",
        help="Hop11: ghi timeline/district + hard_gap_accept + commune hint",
    )
    ap.add_argument(
        "--hop11-limit",
        type=int,
        default=40,
        help="Hop11: số hard-gap live refetch (default 40)",
    )
    ap.add_argument(
        "--hop12-live",
        action="store_true",
        help="Hop12: GET detail returning/new/submitted chờ extend_code",
    )
    ap.add_argument(
        "--hop12-apply",
        action="store_true",
        help="Hop12: ghi tracking/timeline từ waiting live backfill",
    )
    ap.add_argument(
        "--hop12-limit",
        type=int,
        default=40,
        help="Hop12: số đơn waiting live (default 40)",
    )
    ap.add_argument(
        "--hop12-probe",
        action="store_true",
        help="Hop12: probe aship URL hard-gap (HTTP audit)",
    )
    ap.add_argument(
        "--hop13-live",
        action="store_true",
        help="Hop13: live confirm submitted/new chưa có extend_code",
    )
    ap.add_argument(
        "--hop13-apply",
        action="store_true",
        help="Hop13: ghi PATH-WAIT accept + rare partner/tracking nếu có",
    )
    ap.add_argument(
        "--hop13-limit",
        type=int,
        default=60,
        help="Hop13: số đơn submitted confirm scan (default 60)",
    )
    ap.add_argument("--buucuc")
    ap.add_argument("--province", help="Tỉnh/thành nhận")
    ap.add_argument("--address", help="Fragment địa chỉ / ward / huyện / tên nhận")
    ap.add_argument("--icon", help="Fragment icon chant")
    ap.add_argument("--q", help="Auto-detect query")
    args = ap.parse_args()

    continue_flow = bool(args.continue_flow)
    continue_asumee = bool(args.continue_asumee)
    hop6_apply = bool(args.hop6_apply)
    hop7_apply = bool(args.hop7_apply) or hop6_apply
    hop8_apply = bool(args.hop8_apply)
    hop8_probe = bool(args.hop8_probe)
    hop9_live = bool(args.hop9_live)
    hop9_apply = bool(args.hop9_apply)
    hop10_apply = bool(args.hop10_apply)
    hop11_live = bool(args.hop11_live)
    hop11_apply = bool(args.hop11_apply)
    hop12_live = bool(args.hop12_live)
    hop12_apply = bool(args.hop12_apply)
    hop12_probe = bool(args.hop12_probe)
    hop13_live = bool(args.hop13_live)
    hop13_apply = bool(args.hop13_apply)

    # Bật continue-flow nếu chỉ truyền cờ hop*
    if (
        args.hop6_live
        or args.hop6_apply
        or args.hop6_offline
        or args.hop7_apply
        or args.hop7_offline
        or args.hop8_apply
        or args.hop8_probe
        or args.hop9_live
        or args.hop9_apply
        or args.hop10_apply
        or args.hop11_live
        or args.hop11_apply
        or args.hop12_live
        or args.hop12_apply
        or args.hop12_probe
        or args.hop13_live
        or args.hop13_apply
    ) and not (continue_flow or continue_asumee):
        continue_flow = True

    # Live defaults: hop6/hop7 offline trừ khi user xin live/apply batch
    hop6_live = bool(args.hop6_live) or (hop6_apply and not args.hop6_offline)
    hop7_live = bool(hop7_apply) and not args.hop7_offline
    if args.hop6_offline:
        hop6_live = False
    if args.hop7_offline:
        hop7_live = False
    # hop11/hop12/hop13: apply alone also implies live
    if hop11_apply and not args.hop11_live:
        hop11_live = True
    if hop12_apply and not args.hop12_live:
        hop12_live = True
    if hop13_apply and not args.hop13_live:
        hop13_live = True

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
        warehouse_id=args.warehouse_id,
        continue_asumee=continue_asumee,
        continue_flow=continue_flow,
        hop6_live=hop6_live,
        hop6_apply=hop6_apply,
        hop6_limit=int(args.hop6_limit or 8),
        hop7_live=hop7_live,
        hop7_apply=hop7_apply,
        hop7_limit=int(args.hop7_limit or 40),
        hop8_apply=hop8_apply,
        hop8_probe=hop8_probe,
        hop8_probe_limit=int(args.hop8_probe_limit or 6),
        hop9_live=hop9_live,
        hop9_apply=hop9_apply,
        hop9_limit=int(args.hop9_limit or 40),
        hop10_apply=hop10_apply,
        hop11_live=hop11_live,
        hop11_apply=hop11_apply,
        hop11_limit=int(args.hop11_limit or 40),
        hop12_live=hop12_live,
        hop12_apply=hop12_apply,
        hop12_limit=int(args.hop12_limit or 40),
        hop12_probe=hop12_probe,
        hop13_live=hop13_live,
        hop13_apply=hop13_apply,
        hop13_limit=int(args.hop13_limit or 60),
    )
    write_outputs(report)
    if args.json:
        # Scrub before print too
        print(json.dumps(scrub_phones_in_obj(report), ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(scrub_phones_in_obj(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
