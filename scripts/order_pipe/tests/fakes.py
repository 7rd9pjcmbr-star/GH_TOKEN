"""In-memory pipe DB fixtures for Order Pipe Phase B tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from order_pipe.constants import ASUMEE_WID
from order_pipe.store import PipeStore

ORDERS_DDL = """
CREATE TABLE orders (
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
  ward TEXT,
  address_detail TEXT,
  full_address TEXT,
  phone_class TEXT,
  status TEXT,
  source TEXT,
  channel TEXT,
  file TEXT,
  receiver_name TEXT,
  receiver_phone TEXT,
  picked_at TEXT,
  delivered_at TEXT,
  flow_path TEXT,
  created_at TEXT,
  synced_at TEXT,
  piped_at TEXT,
  tracking_url TEXT
);
CREATE TABLE pipe_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT,
  event TEXT,
  van_tay TEXT,
  so_noi_bo TEXT,
  detail TEXT
);
CREATE TABLE fingerprints (
  van_tay TEXT PRIMARY KEY,
  so_noi_bo TEXT,
  backend TEXT,
  kho TEXT,
  buucuc TEXT,
  status TEXT
);
CREATE TABLE kho_nodes (
  kho_id TEXT PRIMARY KEY,
  kho_name TEXT,
  warehouse_id TEXT,
  warehouse_display TEXT,
  orders INTEGER DEFAULT 0
);
CREATE TABLE buucuc_nodes (
  buucuc_id TEXT PRIMARY KEY,
  buucuc TEXT,
  backend TEXT,
  orders INTEGER DEFAULT 0
);
"""

WID = ASUMEE_WID


def _row(
    *,
    vt: str,
    so: str,
    status: str,
    tracking: str | None = None,
    kho: str = "ASUMEE",
    buucuc: str = "SPX",
    province: str | None = "Thừa Thiên Huế",
    district: str | None = "TP Huế",
    ward: str | None = "Phú Hội",
    phone_class: str = "MASKED",
    phone: str = "0901****21",
    picked_at: str | None = None,
    delivered_at: str | None = None,
    piped_at: str = "2026-09-03T00:00:00Z",
) -> tuple:
    trk = tracking if tracking is not None else so
    return (
        vt,
        so,
        so,
        so,
        "pancake",
        buucuc,
        kho,
        WID,
        "ASUMEE",
        "714934229",
        "ASUNMEE",
        "staff",
        "SPX" if buucuc == "SPX" else "Pancake",
        trk,
        province,
        district,
        ward,
        "12 Lê Lợi",
        "12 Lê Lợi, Phú Hội, TP Huế",
        phone_class,
        status,
        "pancake",
        "pos",
        None,
        "Khách A",
        phone,
        picked_at,
        delivered_at,
        f"kho:{kho} → buucuc:{buucuc} → track:{trk}",
        piped_at,
        piped_at,
        piped_at,
        None,
    )


def make_store() -> PipeStore:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(ORDERS_DDL)
    conn.execute(
        "INSERT INTO kho_nodes(kho_id, kho_name, warehouse_id, warehouse_display, orders) VALUES (?,?,?,?,?)",
        ("ASUMEE", "ASUMEE", WID, "ASUMEE", 7),
    )
    conn.execute(
        "INSERT INTO buucuc_nodes(buucuc_id, buucuc, backend, orders) VALUES (?,?,?,?)",
        ("SPX", "SPX", "pancake", 5),
    )
    conn.execute(
        "INSERT INTO buucuc_nodes(buucuc_id, buucuc, backend, orders) VALUES (?,?,?,?)",
        ("GHN", "GHN", "pancake", 2),
    )
    rows = [
        # CLEAR: delivered with both timestamps + real tracking
        _row(
            vt="fp-clear",
            so="SO-CLEAR",
            status="delivered",
            tracking="SPXVN000000000001",
            picked_at="2026-08-01T01:00:00Z",
            delivered_at="2026-08-02T01:00:00Z",
        ),
        # WAIT: submitted pancake-id
        _row(vt="fp-wait", so="SO-WAIT", status="submitted", tracking="SO-WAIT"),
        # MISSING: delivered without delivered_at
        _row(
            vt="fp-missing",
            so="SO-MISS",
            status="delivered",
            tracking="SPXVN000000000002",
            picked_at="2026-08-01T01:00:00Z",
            delivered_at="",
        ),
        # ACCEPT soft: delivered_at without picked_at
        _row(
            vt="fp-soft",
            so="SO-SOFT",
            status="delivered",
            tracking="SPXVN000000000003",
            picked_at="",
            delivered_at="2026-08-02T01:00:00Z",
        ),
        # ACCEPT commune: ward only
        _row(
            vt="fp-commune",
            so="SO-COM",
            status="shipped",
            tracking="SPXVN000000000004",
            district="",
            ward="Phú Hội",
            picked_at="2026-08-01T01:00:00Z",
        ),
        # ACCEPT canceled pancake-id
        _row(vt="fp-cancel", so="SO-CAN", status="canceled", tracking="SO-CAN", buucuc="Pancake"),
        # extra WAIT new + GHN for seed buucuc diversity
        _row(
            vt="fp-new",
            so="SO-NEW",
            status="new",
            tracking="SO-NEW",
            buucuc="GHN",
            province="Đà Nẵng",
            piped_at="2026-09-02T00:00:00Z",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO orders (
          van_tay, so_noi_bo, oms_id, order_key, backend, buucuc, kho,
          warehouse_id, warehouse_display, shop_id, shop_name, staff_creator,
          carrier, tracking_code, province, district, ward, address_detail,
          full_address, phone_class, status, source, channel, file,
          receiver_name, receiver_phone, picked_at, delivered_at, flow_path,
          created_at, synced_at, piped_at, tracking_url
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()
    return PipeStore(conn, path=Path(":memory:"))
