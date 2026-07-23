#!/usr/bin/env python3
"""Đấu nối đường ống đơn → DB kho + bưu cục · vân tay số nội bộ.

Pipe OMS/realtime → SQLite kho_buucuc_pipe.db (+ mirror buucuc_backend.db).
Mỗi đơn nhận:
  · so_noi_bo  — số nội bộ (order_key / Customer Ref / tracking)
  · van_tay    — fingerprint SHA1 nội bộ (backend|kho|buucuc|so_noi_bo|status)
Mapper icon phản hồi nhận vân tay qua receive_fingerprint().

Secrets-only. Không dump login.
"""

from __future__ import annotations

import argparse
import hashlib
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
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
BUUCUC_DB = REPORTS / "buucuc_backend.db"
SECRETS = ROOT / "secrets"
FP_STATE = SECRETS / "order_fingerprints.state.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def so_noi_bo(rec: dict) -> str:
    """Số nội bộ: ưu tiên order_key / Customer Ref / tracking / oms_id."""
    for k in (
        "order_key",
        "Customer Reference No.",
        "customer_reference",
        "so_noi_bo",
        "tracking_code",
        "oms_id",
        "id",
        "remote_id",
    ):
        v = rec.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def van_tay(
    *,
    backend: str,
    kho: str,
    buucuc: str,
    so: str,
    status: str = "",
) -> str:
    """Vân tay số nội bộ — SHA1[:16] ổn định theo kho×bưu cục×số nội bộ."""
    raw = f"{backend}|{kho}|{buucuc}|{so}|{status or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def ensure_pipe_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kho_nodes (
          kho_id TEXT PRIMARY KEY,
          kho_name TEXT,
          warehouse_id TEXT,
          warehouse_display TEXT,
          orders INTEGER DEFAULT 0,
          buucuc_n INTEGER DEFAULT 0,
          last_piped_at TEXT
        );
        CREATE TABLE IF NOT EXISTS buucuc_nodes (
          buucuc_id TEXT PRIMARY KEY,
          buucuc TEXT,
          backend TEXT,
          orders INTEGER DEFAULT 0,
          kho_n INTEGER DEFAULT 0,
          last_piped_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
          van_tay TEXT PRIMARY KEY,
          so_noi_bo TEXT,
          oms_id TEXT,
          order_key TEXT,
          backend TEXT,
          buucuc TEXT,
          kho TEXT,
          warehouse_id TEXT,
          warehouse_display TEXT,
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
          file TEXT,
          realtime_new INTEGER DEFAULT 0,
          icon_chant TEXT,
          icon_feedback TEXT,
          created_at TEXT,
          synced_at TEXT,
          event_at TEXT,
          piped_at TEXT,
          pipe_source TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pipe_so ON orders(so_noi_bo);
        CREATE INDEX IF NOT EXISTS idx_pipe_kho ON orders(kho);
        CREATE INDEX IF NOT EXISTS idx_pipe_buu ON orders(buucuc);
        CREATE INDEX IF NOT EXISTS idx_pipe_be ON orders(backend);
        CREATE INDEX IF NOT EXISTS idx_pipe_prov ON orders(province);
        CREATE TABLE IF NOT EXISTS fingerprints (
          van_tay TEXT PRIMARY KEY,
          so_noi_bo TEXT,
          backend TEXT,
          kho TEXT,
          buucuc TEXT,
          status TEXT,
          icon_chant TEXT,
          icon_feedback TEXT,
          received_at TEXT,
          order_key TEXT,
          tracking_code TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fp_so ON fingerprints(so_noi_bo);
        CREATE TABLE IF NOT EXISTS pipe_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          at TEXT,
          event TEXT,
          van_tay TEXT,
          so_noi_bo TEXT,
          detail TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT
        );
        """
    )
    # migrate address / flow columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
    for col, typ in (
        ("ward", "TEXT"),
        ("address_detail", "TEXT"),
        ("full_address", "TEXT"),
        ("postal_code", "TEXT"),
        ("receiver_name", "TEXT"),
        ("receiver_phone", "TEXT"),
        ("sender_province", "TEXT"),
        ("sender_district", "TEXT"),
        ("sender_ward", "TEXT"),
        ("sender_address", "TEXT"),
        ("cod_amount", "TEXT"),
        ("picked_at", "TEXT"),
        ("delivered_at", "TEXT"),
        ("flow_path", "TEXT"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pipe_prov ON orders(province)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pipe_track ON orders(tracking_code)")


def _compose_flow_path(row: dict) -> str:
    """Toàn cảnh: kho → bưu cục → mã VĐ → địa chỉ nhận."""
    geo = " / ".join(
        str(x)
        for x in (
            row.get("address_detail"),
            row.get("ward"),
            row.get("district"),
            row.get("province"),
        )
        if x
    ) or row.get("full_address") or "(chưa có địa chỉ)"
    recv = row.get("receiver_name") or "(ẩn tên)"
    return (
        f"kho:{row.get('kho') or '?'} → {row.get('backend') or '?'} → "
        f"buucuc:{row.get('buucuc') or '?'} → track:{row.get('tracking_code') or '∅'} → "
        f"[{row.get('status') or '?'}] → nhận:{recv} @ {geo}"
    )


def ensure_buucuc_mirror_schema(conn: sqlite3.Connection) -> None:
    """Mirror fingerprint columns vào buucuc_backend.db (tạo nếu thiếu)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS orders (
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
        CREATE TABLE IF NOT EXISTS backends (
          id TEXT PRIMARY KEY,
          role TEXT,
          oms TEXT,
          secret TEXT,
          query_hint TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT
        );
        """
    )
    # migrate older wipe-schema DBs missing fingerprint / address cols
    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
    for col, typ in (
        ("van_tay", "TEXT"),
        ("so_noi_bo", "TEXT"),
        ("icon_chant", "TEXT"),
        ("icon_feedback", "TEXT"),
        ("piped_at", "TEXT"),
        ("ward", "TEXT"),
        ("address_detail", "TEXT"),
        ("full_address", "TEXT"),
        ("postal_code", "TEXT"),
        ("receiver_name", "TEXT"),
        ("flow_path", "TEXT"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_orders_backend ON orders(backend);
        CREATE INDEX IF NOT EXISTS idx_orders_buucuc ON orders(buucuc);
        CREATE INDEX IF NOT EXISTS idx_orders_kho ON orders(kho);
        CREATE INDEX IF NOT EXISTS idx_orders_van_tay ON orders(van_tay);
        CREATE INDEX IF NOT EXISTS idx_orders_so_noi_bo ON orders(so_noi_bo);
        CREATE INDEX IF NOT EXISTS idx_orders_province ON orders(province);
        """
    )


def enrich_row(rec: dict, *, realtime_new: bool = False, pipe_source: str = "oms") -> dict:
    from buucuc_backend_db_query import classify_buucuc, kho_key, resolve_backend
    from realtime_icon_feedback_mapper import receive_fingerprint

    buu = classify_buucuc(rec)
    backend = resolve_backend(rec, buu)
    kho = kho_key(rec)
    so = so_noi_bo(rec)
    status = str(rec.get("status") or rec.get("status_normalized") or rec.get("status_raw") or "")
    vt = van_tay(backend=backend, kho=kho, buucuc=buu, so=so or "(empty)", status=status)
    icon = receive_fingerprint(
        van_tay=vt,
        so_noi_bo=so,
        backend=backend,
        kho=kho,
        buucuc=buu,
        status=status,
        tracking=rec.get("tracking_code"),
        realtime_new=realtime_new,
    )
    row = {
        "van_tay": vt,
        "so_noi_bo": so or None,
        "oms_id": rec.get("oms_id"),
        "order_key": rec.get("order_key"),
        "backend": backend,
        "buucuc": buu,
        "kho": kho,
        "warehouse_id": str(rec.get("warehouse_id") or "") or None,
        "warehouse_display": rec.get("warehouse_display_name") or rec.get("warehouse_name"),
        "shop_id": str(rec.get("shop_id") or "") or None,
        "shop_name": rec.get("shop_name"),
        "staff_creator": str(rec.get("creator") or rec.get("staff_creator") or "") or None,
        "carrier": rec.get("carrier"),
        "tracking_code": rec.get("tracking_code"),
        "province": rec.get("province"),
        "district": rec.get("district"),
        "ward": rec.get("ward"),
        "address_detail": rec.get("address_detail"),
        "full_address": rec.get("full_address"),
        "postal_code": rec.get("postal_code"),
        "receiver_name": rec.get("receiver_name") or rec.get("customer_name"),
        "receiver_phone": (rec.get("receiver_phone") or rec.get("customer_phone") or "")[:40] or None,
        "sender_province": rec.get("sender_province"),
        "sender_district": rec.get("sender_district"),
        "sender_ward": rec.get("sender_ward"),
        "sender_address": rec.get("sender_address"),
        "cod_amount": str(rec.get("cod_amount") or "") or None,
        "phone_class": rec.get("phone_class"),
        "customer_phone": (rec.get("customer_phone") or "")[:40] or None,
        "status": status or None,
        "source": rec.get("source"),
        "channel": rec.get("channel"),
        "platform": rec.get("platform"),
        "file": rec.get("file") or rec.get("_file"),
        "page_id": rec.get("page_id"),
        "pancake_shop_id": str(rec.get("pancake_shop_id") or "") or None,
        "staff_account": str(rec.get("account") or "") or None,
        "staff_seller": str(rec.get("assigning_seller") or "") or None,
        "staff_care": str(rec.get("assigning_care") or "") or None,
        "realtime_new": 1 if realtime_new else 0,
        "icon_chant": icon.get("icon_chant"),
        "icon_feedback": icon.get("feedback"),
        "created_at": rec.get("created_at") or rec.get("order_created_at"),
        "picked_at": rec.get("picked_at"),
        "delivered_at": rec.get("delivered_at"),
        "synced_at": rec.get("synced_at"),
        "event_at": rec.get("created_at") or rec.get("synced_at") or rec.get("updated_at"),
        "piped_at": utc_now(),
        "pipe_source": pipe_source,
        "_icon": icon,
    }
    row["flow_path"] = _compose_flow_path(row)
    return row


def upsert_pipe_order(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO orders(
          van_tay, so_noi_bo, oms_id, order_key, backend, buucuc, kho,
          warehouse_id, warehouse_display, shop_id, shop_name, staff_creator,
          carrier, tracking_code, province, district, phone_class, status,
          source, channel, file, realtime_new, icon_chant, icon_feedback,
          created_at, synced_at, event_at, piped_at, pipe_source,
          ward, address_detail, full_address, postal_code, receiver_name, receiver_phone,
          sender_province, sender_district, sender_ward, sender_address,
          cod_amount, picked_at, delivered_at, flow_path
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(van_tay) DO UPDATE SET
          so_noi_bo=excluded.so_noi_bo,
          oms_id=excluded.oms_id,
          order_key=excluded.order_key,
          backend=excluded.backend,
          buucuc=excluded.buucuc,
          kho=excluded.kho,
          warehouse_id=excluded.warehouse_id,
          warehouse_display=excluded.warehouse_display,
          shop_id=excluded.shop_id,
          shop_name=excluded.shop_name,
          staff_creator=excluded.staff_creator,
          carrier=excluded.carrier,
          tracking_code=excluded.tracking_code,
          province=excluded.province,
          district=excluded.district,
          phone_class=excluded.phone_class,
          status=excluded.status,
          source=excluded.source,
          channel=excluded.channel,
          file=excluded.file,
          realtime_new=CASE WHEN excluded.realtime_new > orders.realtime_new THEN excluded.realtime_new ELSE orders.realtime_new END,
          icon_chant=excluded.icon_chant,
          icon_feedback=excluded.icon_feedback,
          created_at=COALESCE(excluded.created_at, orders.created_at),
          synced_at=COALESCE(excluded.synced_at, orders.synced_at),
          event_at=COALESCE(excluded.event_at, orders.event_at),
          piped_at=excluded.piped_at,
          pipe_source=excluded.pipe_source,
          ward=COALESCE(excluded.ward, orders.ward),
          address_detail=COALESCE(excluded.address_detail, orders.address_detail),
          full_address=COALESCE(excluded.full_address, orders.full_address),
          postal_code=COALESCE(excluded.postal_code, orders.postal_code),
          receiver_name=COALESCE(excluded.receiver_name, orders.receiver_name),
          receiver_phone=COALESCE(excluded.receiver_phone, orders.receiver_phone),
          sender_province=COALESCE(excluded.sender_province, orders.sender_province),
          sender_district=COALESCE(excluded.sender_district, orders.sender_district),
          sender_ward=COALESCE(excluded.sender_ward, orders.sender_ward),
          sender_address=COALESCE(excluded.sender_address, orders.sender_address),
          cod_amount=COALESCE(excluded.cod_amount, orders.cod_amount),
          picked_at=COALESCE(excluded.picked_at, orders.picked_at),
          delivered_at=COALESCE(excluded.delivered_at, orders.delivered_at),
          flow_path=excluded.flow_path
        """,
        (
            row["van_tay"],
            row["so_noi_bo"],
            row["oms_id"],
            row["order_key"],
            row["backend"],
            row["buucuc"],
            row["kho"],
            row["warehouse_id"],
            row["warehouse_display"],
            row["shop_id"],
            row["shop_name"],
            row["staff_creator"],
            row["carrier"],
            row["tracking_code"],
            row["province"],
            row["district"],
            row["phone_class"],
            row["status"],
            row["source"],
            row["channel"],
            row["file"],
            row["realtime_new"],
            row["icon_chant"],
            row["icon_feedback"],
            row["created_at"],
            row["synced_at"],
            row["event_at"],
            row["piped_at"],
            row["pipe_source"],
            row.get("ward"),
            row.get("address_detail"),
            row.get("full_address"),
            row.get("postal_code"),
            row.get("receiver_name"),
            row.get("receiver_phone"),
            row.get("sender_province"),
            row.get("sender_district"),
            row.get("sender_ward"),
            row.get("sender_address"),
            row.get("cod_amount"),
            row.get("picked_at"),
            row.get("delivered_at"),
            row.get("flow_path"),
        ),
    )
    conn.execute(
        """
        INSERT INTO fingerprints(
          van_tay, so_noi_bo, backend, kho, buucuc, status,
          icon_chant, icon_feedback, received_at, order_key, tracking_code
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(van_tay) DO UPDATE SET
          so_noi_bo=excluded.so_noi_bo,
          backend=excluded.backend,
          kho=excluded.kho,
          buucuc=excluded.buucuc,
          status=excluded.status,
          icon_chant=excluded.icon_chant,
          icon_feedback=excluded.icon_feedback,
          received_at=excluded.received_at,
          order_key=excluded.order_key,
          tracking_code=excluded.tracking_code
        """,
        (
            row["van_tay"],
            row["so_noi_bo"],
            row["backend"],
            row["kho"],
            row["buucuc"],
            row["status"],
            row["icon_chant"],
            row["icon_feedback"],
            row["piped_at"],
            row["order_key"],
            row["tracking_code"],
        ),
    )


def upsert_buucuc_mirror(conn: sqlite3.Connection, row: dict) -> None:
    # delete prior same van_tay or same oms_id+order_key to avoid dupes after wipe schema
    if row.get("van_tay"):
        conn.execute("DELETE FROM orders WHERE van_tay = ?", (row["van_tay"],))
    if row.get("oms_id"):
        conn.execute(
            "DELETE FROM orders WHERE oms_id = ? AND (van_tay IS NULL OR van_tay = '')",
            (row["oms_id"],),
        )
    conn.execute(
        """
        INSERT INTO orders(
          oms_id, order_key, backend, buucuc, kho, warehouse_id, warehouse_display,
          shop_id, shop_name, page_id, pancake_shop_id, staff_creator, staff_account,
          staff_seller, staff_care, carrier, tracking_code, province, district,
          phone_class, customer_phone, status, source, channel, platform, file,
          van_tay, so_noi_bo, icon_chant, icon_feedback, piped_at,
          ward, address_detail, full_address, postal_code, receiver_name, flow_path
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row["oms_id"],
            row["order_key"],
            row["backend"],
            row["buucuc"],
            row["kho"],
            row["warehouse_id"],
            row["warehouse_display"],
            row["shop_id"],
            row["shop_name"],
            row.get("page_id"),
            row.get("pancake_shop_id"),
            row["staff_creator"],
            row.get("staff_account"),
            row.get("staff_seller"),
            row.get("staff_care"),
            row["carrier"],
            row["tracking_code"],
            row["province"],
            row["district"],
            row["phone_class"],
            row.get("customer_phone") or row.get("receiver_phone"),
            row["status"],
            row["source"],
            row["channel"],
            row.get("platform"),
            row["file"],
            row["van_tay"],
            row["so_noi_bo"],
            row["icon_chant"],
            row["icon_feedback"],
            row["piped_at"],
            row.get("ward"),
            row.get("address_detail"),
            row.get("full_address"),
            row.get("postal_code"),
            row.get("receiver_name"),
            row.get("flow_path"),
        ),
    )


def refresh_nodes(conn: sqlite3.Connection) -> None:
    now = utc_now()
    conn.execute("DELETE FROM kho_nodes")
    conn.execute("DELETE FROM buucuc_nodes")
    for kho, orders, buu_n in conn.execute(
        """
        SELECT kho, COUNT(*) AS orders, COUNT(DISTINCT buucuc)
        FROM orders GROUP BY kho
        """
    ):
        kid = hashlib.sha1((kho or "(none)").encode()).hexdigest()[:12]
        conn.execute(
            "INSERT INTO kho_nodes(kho_id, kho_name, orders, buucuc_n, last_piped_at) VALUES (?,?,?,?,?)",
            (kid, kho, orders, buu_n, now),
        )
    for buu, backend, orders, kho_n in conn.execute(
        """
        SELECT buucuc, backend, COUNT(*) AS orders, COUNT(DISTINCT kho)
        FROM orders GROUP BY buucuc, backend
        """
    ):
        bid = hashlib.sha1(f"{backend}|{buu}".encode()).hexdigest()[:12]
        conn.execute(
            "INSERT INTO buucuc_nodes(buucuc_id, buucuc, backend, orders, kho_n, last_piped_at) VALUES (?,?,?,?,?,?)",
            (bid, buu, backend, orders, kho_n, now),
        )


def save_fp_state(rows: list[dict]) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now(),
        "count": len(rows),
        "fingerprints": [
            {
                "van_tay": r["van_tay"],
                "so_noi_bo": r["so_noi_bo"],
                "backend": r["backend"],
                "kho": r["kho"],
                "buucuc": r["buucuc"],
                "icon_chant": r["icon_chant"],
                "status": r["status"],
            }
            for r in rows[:500]
        ],
    }
    FP_STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_report(*, ingest_limit: int = 5000, run_cycle: bool = True, limit_rt: int = 50) -> dict:
    from buucuc_backend_db_query import BUUCUC_BACKENDS
    from oms_interconnect import ingest_local_orders, load_env
    from realtime_icon_feedback_mapper import chant, feedback_line
    from realtime_order_sync import run_cycle as rt_cycle

    env = load_env()
    cycle: dict[str, Any] = {"new_count": 0, "backends": [], "all_new_orders": [], "blocked": []}
    if run_cycle:
        cycle = rt_cycle(env, limit=max(1, limit_rt), notify=False, notify_new_only=False)

    local = ingest_local_orders(limit_per_file=max(100, ingest_limit))
    enriched: list[dict] = []
    for rec in local:
        enriched.append(enrich_row(rec, realtime_new=False, pipe_source="oms_ingest"))

    rt_new_rows = []
    for o in cycle.get("all_new_orders") or []:
        oo = dict(o)
        if not oo.get("tracking_code") and oo.get("id") and str(oo.get("_backend")) == "SPX-local":
            oo["tracking_code"] = oo.get("id")
        row = enrich_row(oo, realtime_new=True, pipe_source="realtime_cycle")
        enriched.append(row)
        rt_new_rows.append(row)

    # dedupe by van_tay — prefer realtime_new + richer fields
    dedup: dict[str, dict] = {}
    for row in enriched:
        key = row["van_tay"]
        prev = dedup.get(key)
        if prev is None:
            dedup[key] = row
            continue
        merged = dict(prev)
        if row.get("realtime_new"):
            merged["realtime_new"] = 1
        for fld in (
            "so_noi_bo",
            "oms_id",
            "order_key",
            "tracking_code",
            "shop_name",
            "staff_creator",
            "created_at",
            "synced_at",
            "event_at",
            "icon_chant",
            "icon_feedback",
            "province",
            "carrier",
        ):
            if not merged.get(fld) and row.get(fld):
                merged[fld] = row[fld]
        dedup[key] = merged
    rows = list(dedup.values())

    PIPE_DB.parent.mkdir(parents=True, exist_ok=True)
    BUUCUC_DB.parent.mkdir(parents=True, exist_ok=True)

    pipe = sqlite3.connect(str(PIPE_DB))
    ensure_pipe_schema(pipe)
    for row in rows:
        upsert_pipe_order(pipe, row)
        pipe.execute(
            "INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail) VALUES (?,?,?,?,?)",
            (
                utc_now(),
                "upsert",
                row["van_tay"],
                row["so_noi_bo"],
                f"{row['backend']}|{row['kho']}|{row['buucuc']}",
            ),
        )
    refresh_nodes(pipe)
    pipe.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES ('piped_at',?), ('orders',?), ('fingerprints',?)",
        (utc_now(), str(len(rows)), str(len(rows))),
    )
    # seed backends catalog into mirror
    mirror = sqlite3.connect(str(BUUCUC_DB))
    ensure_buucuc_mirror_schema(mirror)
    for b in BUUCUC_BACKENDS:
        mirror.execute(
            "INSERT OR REPLACE INTO backends(id, role, oms, secret, query_hint) VALUES (?,?,?,?,?)",
            (b["id"], b["role"], b["oms"], b["secret"], b["query_hint"]),
        )
    # full rebuild mirror from pipe rows to keep BC DB consistent with fingerprints
    mirror.execute("DELETE FROM orders")
    for row in rows:
        upsert_buucuc_mirror(mirror, row)
    mirror.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('piped_at', ?)", (utc_now(),))
    mirror.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('records', ?)", (str(len(rows)),))
    mirror.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('pipe', ?)", ("kho_buucuc_pipe",))
    mirror.commit()
    mirror.close()

    pipe.commit()

    # aggregates from pipe DB
    by_kho = [
        {"kho": r[0], "orders": r[1], "buucuc_n": r[2]}
        for r in pipe.execute(
            "SELECT kho_name, orders, buucuc_n FROM kho_nodes ORDER BY orders DESC"
        )
    ]
    by_buucuc = [
        {"buucuc": r[0], "backend": r[1], "orders": r[2], "kho_n": r[3]}
        for r in pipe.execute(
            "SELECT buucuc, backend, orders, kho_n FROM buucuc_nodes ORDER BY orders DESC"
        )
    ]
    kho_buucuc = [
        {"kho": r[0], "buucuc": r[1], "orders": r[2], "fps": r[3]}
        for r in pipe.execute(
            """
            SELECT kho, buucuc, COUNT(*) AS orders, COUNT(DISTINCT van_tay) AS fps
            FROM orders GROUP BY kho, buucuc ORDER BY orders DESC LIMIT 40
            """
        )
    ]
    fp_samples = [
        {
            "van_tay": r[0],
            "so_noi_bo": r[1],
            "backend": r[2],
            "kho": r[3],
            "buucuc": r[4],
            "icon_chant": r[5],
            "status": r[6],
        }
        for r in pipe.execute(
            """
            SELECT van_tay, so_noi_bo, backend, kho, buucuc, icon_chant, status
            FROM fingerprints ORDER BY received_at DESC LIMIT 24
            """
        )
    ]
    with_so = pipe.execute(
        "SELECT COUNT(*) FROM orders WHERE so_noi_bo IS NOT NULL AND so_noi_bo != ''"
    ).fetchone()[0]
    with_fp = pipe.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
    pipe.close()

    save_fp_state(rows)

    icons = ["network", "cube", "hash", "spark", "monitor"]
    top_fb = feedback_line(
        icons,
        f"pipe→DB kho+buucuc · orders={len(rows)} · van_tay={with_fp} · "
        f"so_noi_bo={with_so} · rt_new={len(rt_new_rows)} · "
        f"kho={len(by_kho)} buucuc={len(by_buucuc)}",
    )

    return {
        "ok": True,
        "query": "Đấu nối đường ống đơn → DB kho+bưu cục · mapper icon nhận vân tay số nội bộ",
        "checked_at": utc_now(),
        "cycle": {
            "checked_at": cycle.get("checked_at"),
            "new_count": cycle.get("new_count"),
            "blocked": cycle.get("blocked"),
            "backends": [
                {
                    "backend": b.get("backend"),
                    "status": b.get("status"),
                    "new": len(b.get("new_orders") or []),
                    "detail": b.get("detail"),
                }
                for b in (cycle.get("backends") or [])
            ],
        },
        "db": {
            "pipe_db": str(PIPE_DB),
            "buucuc_db": str(BUUCUC_DB),
            "orders": len(rows),
            "fingerprints": with_fp,
            "with_so_noi_bo": with_so,
            "kho_nodes": len(by_kho),
            "buucuc_nodes": len(by_buucuc),
            "fp_state": str(FP_STATE),
        },
        "by_kho": by_kho,
        "by_buucuc": by_buucuc,
        "kho_buucuc": kho_buucuc,
        "fingerprint_samples": fp_samples,
        "realtime_new_samples": [
            {
                "van_tay": r["van_tay"],
                "so_noi_bo": r["so_noi_bo"],
                "backend": r["backend"],
                "kho": r["kho"],
                "buucuc": r["buucuc"],
                "icon_chant": r["icon_chant"],
                "icon_feedback": r["icon_feedback"],
            }
            for r in rt_new_rows[:12]
        ],
        "summary": {
            "orders_piped": len(rows),
            "fingerprints": with_fp,
            "with_so_noi_bo": with_so,
            "realtime_new": len(rt_new_rows),
            "kho_nodes": len(by_kho),
            "buucuc_nodes": len(by_buucuc),
            "icon_chant": chant(icons),
            "feedback": top_fb,
        },
        "verdict": top_fb,
        "next_actions": [
            f"SQL pipe: SELECT kho, buucuc, van_tay, so_noi_bo FROM orders LIMIT 20 — {PIPE_DB}",
            f"SQL BC: SELECT van_tay, so_noi_bo, kho, buucuc FROM orders WHERE van_tay IS NOT NULL — {BUUCUC_DB}",
            "Re-pipe: python3 scripts/order_pipe_kho_buucuc_db.py",
            "Icon RT: python3 scripts/realtime_icon_feedback_mapper.py",
            "Điền secrets/backend_pipes.env để pipe live GHN/Pancake vào cùng van_tay",
        ],
        "safety": {"secrets_only": True, "no_dump_login": True},
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔌 PIPE ĐƠN → DB KHO + BƯU CỤC · VÂN TAY SỐ NỘI BỘ")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    db = report["db"]
    L(f"✨ {s.get('feedback')}")
    L(f"Chant: {s.get('icon_chant')}")
    L(
        f"piped={s['orders_piped']} van_tay={s['fingerprints']} "
        f"so_noi_bo={s['with_so_noi_bo']} rt_new={s['realtime_new']}"
    )
    L(f"DB pipe: {db['pipe_db']}")
    L(f"DB buucuc mirror: {db['buucuc_db']}")
    L(f"FP state: {db['fp_state']}")
    L("")
    cy = report["cycle"]
    L("=== Realtime cycle ===")
    L(f"· at={cy.get('checked_at')} new={cy.get('new_count')} blocked={cy.get('blocked')}")
    for b in cy.get("backends") or []:
        L(f"  - {b.get('backend')}: {b.get('status')} new={b.get('new')} · {str(b.get('detail') or '')[:80]}")
    L("")
    L("=== Kho (nodes) ===")
    for k in report["by_kho"][:12]:
        L(f"· {k['kho']}: orders={k['orders']} buucuc_n={k['buucuc_n']}")
    L("")
    L("=== Bưu cục (nodes) ===")
    for b in report["by_buucuc"][:12]:
        L(f"· {b['buucuc']} [{b['backend']}]: orders={b['orders']} kho_n={b['kho_n']}")
    L("")
    L("=== Kho × Bưu cục ===")
    for x in report["kho_buucuc"][:16]:
        L(f"· {x['kho']} × {x['buucuc']}: n={x['orders']} fp={x['fps']}")
    L("")
    L("=== Vân tay số nội bộ (mẫu — icon đã nhận) ===")
    for f in report["fingerprint_samples"][:16]:
        L(
            f"· [{f.get('van_tay')}] so={f.get('so_noi_bo')} "
            f"{f.get('backend')}/{f.get('kho')}/{f.get('buucuc')}"
        )
        L(f"  chant={f.get('icon_chant')} status={f.get('status')}")
    if report.get("realtime_new_samples"):
        L("")
        L("=== RT new (vân tay) ===")
        for r in report["realtime_new_samples"][:8]:
            L(f"· {r.get('van_tay')} so={r.get('so_noi_bo')} · {r.get('icon_feedback')}")
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
        "json": REPORTS / "order_pipe_kho_buucuc.json",
        "txt": REPORTS / "order_pipe_kho_buucuc.txt",
        "rt_json": OUT / "order_pipe_kho_buucuc.json",
        "rt_txt": OUT / "order_pipe_kho_buucuc.txt",
        "pipe_db": PIPE_DB,
        "buucuc_db": BUUCUC_DB,
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Pipe đơn → DB kho+bưu cục + vân tay số nội bộ")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-cycle", action="store_true", help="Bỏ realtime cycle (chỉ OMS ingest)")
    ap.add_argument("--limit", type=int, default=5000)
    args = ap.parse_args()
    report = build_report(ingest_limit=args.limit, run_cycle=not args.no_cycle)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
