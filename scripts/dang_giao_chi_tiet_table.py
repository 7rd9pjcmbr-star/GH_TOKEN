#!/usr/bin/env python3
"""Quét đơn đang giao chi tiết từ mọi kho × mọi bưu cục → 1 bảng.

Nguồn: OMS local (CSV Đang giao + JSON đã gửi hàng + pipe DB enrich).
Secrets-only. Không dump login / không mass-login VTP.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "realtime"
DB_PATH = REPORTS / "dang_giao_chi_tiet.db"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
INBOX = ROOT / "quarantine" / "telegram"

# Trạng thái coi là đang giao / còn trên đường
IN_TRANSIT_PATTERNS = (
    r"dang\s*giao",
    r"đang\s*giao",
    r"shipping",
    r"delivering",
    r"in[_\s-]?transit",
    r"da\s*gui\s*hang",
    r"đã\s*gửi\s*hàng",
    r"out\s*for\s*delivery",
    r"picking",
    r"picked",
    r"đang\s*lấy",
    r"dang\s*lay",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_as_of(raw: str | None) -> str:
    """YYYY-MM-DD; mặc định hôm nay (UTC)."""
    if not raw:
        return today_utc()
    s = str(raw).strip()[:10]
    datetime.strptime(s, "%Y-%m-%d")  # validate
    return s


def is_in_transit(status: str | None, *, include_shipped: bool = True) -> bool:
    s = (status or "").strip().lower()
    if not s:
        return False
    pats = IN_TRANSIT_PATTERNS
    if not include_shipped:
        pats = tuple(p for p in pats if "gui" not in p and "gửi" not in p)
    return any(re.search(p, s, re.I) for p in pats)


def row_id(*parts: str) -> str:
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def load_pipe_index() -> dict[str, dict]:
    """Index pipe DB by so_noi_bo / order_key / tracking / oms_id."""
    idx: dict[str, dict] = {}
    if not PIPE_DB.is_file():
        return idx
    try:
        conn = sqlite3.connect(str(PIPE_DB))
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT * FROM orders"):
            d = {k: r[k] for k in r.keys()}
            for key in (
                d.get("so_noi_bo"),
                d.get("order_key"),
                d.get("tracking_code"),
                d.get("oms_id"),
            ):
                if key:
                    idx[str(key)] = d
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    return idx


def enrich_from_pipe(rec: dict, pipe_idx: dict[str, dict]) -> dict:
    hit = None
    for key in (
        rec.get("order_key"),
        rec.get("so_noi_bo"),
        rec.get("tracking_code"),
        rec.get("oms_id"),
        rec.get("remote_id"),
    ):
        if key and str(key) in pipe_idx:
            hit = pipe_idx[str(key)]
            break
    if not hit:
        return rec
    out = dict(rec)
    for fld in (
        "van_tay",
        "so_noi_bo",
        "kho",
        "buucuc",
        "backend",
        "carrier",
        "tracking_code",
        "province",
        "district",
        "ward",
        "address_detail",
        "full_address",
        "receiver_name",
        "shop_name",
        "staff_creator",
        "flow_path",
        "icon_chant",
        "warehouse_display",
    ):
        if not out.get(fld) and hit.get(fld):
            out[fld] = hit[fld]
    if not out.get("kho") and hit.get("kho"):
        out["kho"] = hit["kho"]
    return out


def scan_orders(
    *,
    include_shipped: bool = True,
    ingest_limit: int = 8000,
    as_of: str | None = None,
) -> list[dict]:
    from buucuc_backend_db_query import classify_buucuc, kho_key, resolve_backend
    from oms_interconnect import ingest_local_orders
    from order_pipe_kho_buucuc_db import so_noi_bo, van_tay

    ngay_dang_giao = parse_as_of(as_of)
    now = utc_now()
    pipe_idx = load_pipe_index()
    local = ingest_local_orders(limit_per_file=max(100, ingest_limit))
    rows: list[dict] = []

    for rec in local:
        status = str(rec.get("status") or rec.get("status_normalized") or rec.get("status_raw") or "")
        # CSV Đang giao file always include even if status weird
        file_dg = "dang_giao" in (rec.get("file") or "").lower()
        if not file_dg and not is_in_transit(status, include_shipped=include_shipped):
            continue

        rec = enrich_from_pipe(rec, pipe_idx)
        buu = classify_buucuc(rec)
        backend = resolve_backend(rec, buu)
        kho = (
            (rec.get("kho") or "").strip()
            or kho_key(rec)
            or (f"shop:{rec.get('shop_id')}" if rec.get("shop_id") else "(chua_gan_kho)")
        )
        so = so_noi_bo(rec) or str(rec.get("order_key") or rec.get("remote_id") or "")
        vt = rec.get("van_tay") or van_tay(
            backend=backend, kho=kho, buucuc=buu, so=so or "(empty)", status=status
        )
        geo = ", ".join(
            str(x)
            for x in (
                rec.get("address_detail"),
                rec.get("ward"),
                rec.get("district"),
                rec.get("province"),
            )
            if x
        ) or rec.get("full_address")

        # Giữ ngày gốc; cập nhật ngày đang giao = as_of (snapshot hiện tại)
        created_goc = rec.get("order_created_at") or rec.get("created_at")
        synced_goc = rec.get("synced_at")
        updated_goc = rec.get("updated_at")

        rows.append(
            {
                "row_id": row_id(vt, so, rec.get("file") or ""),
                "van_tay": vt,
                "so_noi_bo": so or None,
                "oms_id": rec.get("oms_id"),
                "order_key": rec.get("order_key"),
                "remote_id": rec.get("remote_id"),
                "backend": backend,
                "buucuc": buu,
                "kho": kho,
                "warehouse_id": str(rec.get("warehouse_id") or "") or None,
                "warehouse_display": rec.get("warehouse_display_name")
                or rec.get("warehouse_display")
                or rec.get("warehouse_name"),
                "shop_id": str(rec.get("shop_id") or "") or None,
                "shop_name": rec.get("shop_name"),
                "staff_creator": str(rec.get("creator") or rec.get("staff_creator") or "") or None,
                "staff_seller": str(rec.get("assigning_seller") or "") or None,
                "staff_care": str(rec.get("assigning_care") or "") or None,
                "carrier": rec.get("carrier"),
                "tracking_code": rec.get("tracking_code"),
                "status": status or None,
                "status_raw": rec.get("status_raw"),
                "phone_class": rec.get("phone_class"),
                "customer_name": rec.get("customer_name") or rec.get("receiver_name"),
                "customer_phone": (rec.get("customer_phone") or rec.get("receiver_phone") or "")[:40]
                or None,
                "province": rec.get("province"),
                "district": rec.get("district"),
                "ward": rec.get("ward"),
                "address_detail": rec.get("address_detail"),
                "full_address": geo or rec.get("full_address"),
                "cod_amount": str(rec.get("cod_amount") or rec.get("amount") or "") or None,
                "quantity": str(rec.get("quantity") or "") or None,
                "platform": rec.get("platform"),
                "source": rec.get("source"),
                "channel": rec.get("channel"),
                "file": rec.get("file"),
                "order_created_at": created_goc,
                "order_created_at_goc": created_goc,
                "synced_at_goc": synced_goc,
                "updated_at_goc": updated_goc,
                "synced_at": f"{ngay_dang_giao}T00:00:00Z",
                "updated_at": now,
                "ngay_dang_giao": ngay_dang_giao,
                "ngay_cap_nhat": now,
                "picked_at": rec.get("picked_at"),
                "delivered_at": rec.get("delivered_at"),
                "flow_path": rec.get("flow_path")
                or (
                    f"kho:{kho} → {backend} → buucuc:{buu} → "
                    f"track:{rec.get('tracking_code') or '∅'} → [{status}] @ {ngay_dang_giao} → "
                    f"{geo or '(chưa địa chỉ)'}"
                ),
                "icon_chant": rec.get("icon_chant"),
                "scanned_at": now,
            }
        )
        # gắn tracking.aship URL
        from tracking_aship import attach_tracking_urls

        rows[-1] = attach_tracking_urls(rows[-1])

    # dedupe by van_tay / order_key
    dedup: dict[str, dict] = {}
    for r in rows:
        key = r.get("van_tay") or r.get("order_key") or r["row_id"]
        prev = dedup.get(key)
        if prev is None:
            dedup[key] = r
            continue
        merged = dict(prev)
        for fld, val in r.items():
            if not merged.get(fld) and val:
                merged[fld] = val
        # luôn giữ ngày đang giao mới nhất
        merged["ngay_dang_giao"] = r.get("ngay_dang_giao") or merged.get("ngay_dang_giao")
        merged["ngay_cap_nhat"] = r.get("ngay_cap_nhat") or merged.get("ngay_cap_nhat")
        merged["synced_at"] = r.get("synced_at") or merged.get("synced_at")
        merged["updated_at"] = r.get("updated_at") or merged.get("updated_at")
        dedup[key] = merged
    return list(dedup.values())


def materialize(rows: list[dict]) -> dict:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(
        """
        CREATE TABLE don_dang_giao (
          row_id TEXT PRIMARY KEY,
          van_tay TEXT,
          so_noi_bo TEXT,
          oms_id TEXT,
          order_key TEXT,
          remote_id TEXT,
          backend TEXT,
          buucuc TEXT,
          kho TEXT,
          warehouse_id TEXT,
          warehouse_display TEXT,
          shop_id TEXT,
          shop_name TEXT,
          staff_creator TEXT,
          staff_seller TEXT,
          staff_care TEXT,
          carrier TEXT,
          tracking_code TEXT,
          status TEXT,
          status_raw TEXT,
          phone_class TEXT,
          customer_name TEXT,
          customer_phone TEXT,
          province TEXT,
          district TEXT,
          ward TEXT,
          address_detail TEXT,
          full_address TEXT,
          cod_amount TEXT,
          quantity TEXT,
          platform TEXT,
          source TEXT,
          channel TEXT,
          file TEXT,
          order_created_at TEXT,
          order_created_at_goc TEXT,
          synced_at_goc TEXT,
          updated_at_goc TEXT,
          synced_at TEXT,
          updated_at TEXT,
          ngay_dang_giao TEXT,
          ngay_cap_nhat TEXT,
          picked_at TEXT,
          delivered_at TEXT,
          flow_path TEXT,
          icon_chant TEXT,
          scanned_at TEXT,
          tracking_ref TEXT,
          tracking_provider TEXT,
          tracking_url TEXT
        );
        CREATE INDEX idx_dg_kho ON don_dang_giao(kho);
        CREATE INDEX idx_dg_buu ON don_dang_giao(buucuc);
        CREATE INDEX idx_dg_be ON don_dang_giao(backend);
        CREATE INDEX idx_dg_status ON don_dang_giao(status);
        CREATE INDEX idx_dg_shop ON don_dang_giao(shop_id);
        CREATE INDEX idx_dg_vt ON don_dang_giao(van_tay);
        CREATE INDEX idx_dg_prov ON don_dang_giao(province);
        CREATE INDEX idx_dg_ngay ON don_dang_giao(ngay_dang_giao);
        CREATE INDEX idx_dg_track_url ON don_dang_giao(tracking_provider);
        CREATE TABLE kho_buucuc_summary (
          kho TEXT,
          buucuc TEXT,
          backend TEXT,
          orders INTEGER,
          with_tracking INTEGER,
          with_address INTEGER,
          phone_ok INTEGER,
          phone_missing INTEGER,
          phone_masked INTEGER,
          PRIMARY KEY (kho, buucuc, backend)
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    cols = [
        "row_id",
        "van_tay",
        "so_noi_bo",
        "oms_id",
        "order_key",
        "remote_id",
        "backend",
        "buucuc",
        "kho",
        "warehouse_id",
        "warehouse_display",
        "shop_id",
        "shop_name",
        "staff_creator",
        "staff_seller",
        "staff_care",
        "carrier",
        "tracking_code",
        "status",
        "status_raw",
        "phone_class",
        "customer_name",
        "customer_phone",
        "province",
        "district",
        "ward",
        "address_detail",
        "full_address",
        "cod_amount",
        "quantity",
        "platform",
        "source",
        "channel",
        "file",
        "order_created_at",
        "order_created_at_goc",
        "synced_at_goc",
        "updated_at_goc",
        "synced_at",
        "updated_at",
        "ngay_dang_giao",
        "ngay_cap_nhat",
        "picked_at",
        "delivered_at",
        "flow_path",
        "icon_chant",
        "scanned_at",
        "tracking_ref",
        "tracking_provider",
        "tracking_url",
    ]
    conn.executemany(
        f"INSERT INTO don_dang_giao ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        [tuple(r.get(c) for c in cols) for r in rows],
    )

    # summary matrix
    conn.execute("DELETE FROM kho_buucuc_summary")
    for r in conn.execute(
        """
        SELECT kho, buucuc, backend,
               COUNT(*) AS orders,
               SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != '' THEN 1 ELSE 0 END),
               SUM(CASE WHEN full_address IS NOT NULL AND full_address != '' THEN 1 ELSE 0 END),
               SUM(CASE WHEN phone_class = 'OK' THEN 1 ELSE 0 END),
               SUM(CASE WHEN phone_class = 'MISSING' THEN 1 ELSE 0 END),
               SUM(CASE WHEN phone_class = 'MASKED' THEN 1 ELSE 0 END)
        FROM don_dang_giao
        GROUP BY kho, buucuc, backend
        """
    ):
        conn.execute(
            "INSERT INTO kho_buucuc_summary VALUES (?,?,?,?,?,?,?,?,?)",
            r,
        )

    conn.execute("INSERT INTO meta(key,value) VALUES ('scanned_at', ?)", (utc_now(),))
    conn.execute("INSERT INTO meta(key,value) VALUES ('orders', ?)", (str(len(rows)),))
    conn.execute(
        "INSERT INTO meta(key,value) VALUES ('table', ?)",
        ("don_dang_giao",),
    )
    ngay = rows[0].get("ngay_dang_giao") if rows else today_utc()
    conn.execute("INSERT INTO meta(key,value) VALUES ('ngay_dang_giao', ?)", (ngay,))
    conn.execute("INSERT INTO meta(key,value) VALUES ('ngay_cap_nhat', ?)", (utc_now(),))
    conn.commit()
    info = {
        "path": str(DB_PATH),
        "orders": len(rows),
        "table": "don_dang_giao",
        "ngay_dang_giao": ngay,
    }
    conn.close()
    return info


def build_report(
    *,
    include_shipped: bool = True,
    ingest_limit: int = 8000,
    as_of: str | None = None,
) -> dict:
    from realtime_icon_feedback_mapper import chant, feedback_line

    ngay = parse_as_of(as_of)
    rows = scan_orders(
        include_shipped=include_shipped, ingest_limit=ingest_limit, as_of=ngay
    )
    db = materialize(rows)

    by_kho: Counter = Counter()
    by_buucuc: Counter = Counter()
    by_backend: Counter = Counter()
    by_status: Counter = Counter()
    by_phone: Counter = Counter()
    matrix: dict[tuple, dict] = {}

    for r in rows:
        by_kho[r.get("kho") or "?"] += 1
        by_buucuc[r.get("buucuc") or "?"] += 1
        by_backend[r.get("backend") or "?"] += 1
        by_status[r.get("status") or "?"] += 1
        by_phone[r.get("phone_class") or "?"] += 1
        key = (r.get("kho") or "?", r.get("buucuc") or "?", r.get("backend") or "?")
        m = matrix.setdefault(
            key,
            {
                "kho": key[0],
                "buucuc": key[1],
                "backend": key[2],
                "orders": 0,
                "with_tracking": 0,
                "with_address": 0,
            },
        )
        m["orders"] += 1
        if r.get("tracking_code"):
            m["with_tracking"] += 1
        if r.get("full_address") or r.get("province"):
            m["with_address"] += 1

    matrix_out = sorted(matrix.values(), key=lambda x: -x["orders"])
    samples = []
    for r in rows[:40]:
        samples.append(
            {
                "van_tay": r.get("van_tay"),
                "so_noi_bo": r.get("so_noi_bo"),
                "kho": r.get("kho"),
                "buucuc": r.get("buucuc"),
                "backend": r.get("backend"),
                "tracking": r.get("tracking_code"),
                "status": r.get("status"),
                "shop_id": r.get("shop_id"),
                "customer_name": r.get("customer_name"),
                "phone_class": r.get("phone_class"),
                "address": r.get("full_address") or r.get("province"),
                "ngay_dang_giao": r.get("ngay_dang_giao"),
                "order_created_at_goc": r.get("order_created_at_goc"),
                "tracking_url": r.get("tracking_url"),
                "tracking_provider": r.get("tracking_provider"),
                "flow_path": r.get("flow_path"),
                "file": r.get("file"),
            }
        )

    icons = ["cube", "network", "monitor", "hash", "compass"]
    top_fb = feedback_line(
        icons,
        f"đang giao @ {ngay} · n={len(rows)} · kho={len(by_kho)} · "
        f"buucuc={len(by_buucuc)} · backend={len(by_backend)} → 1 bảng {db['table']}",
    )

    by_created_goc = Counter(
        str(r.get("order_created_at_goc") or "")[:10] or "(none)" for r in rows
    )

    return {
        "ok": True,
        "query": "Quét đơn đang giao chi tiết từ mọi kho × mọi bưu cục → 1 bảng",
        "checked_at": utc_now(),
        "ngay_dang_giao": ngay,
        "ngay_cap_nhat": utc_now(),
        "include_shipped": include_shipped,
        "db": db,
        "summary": {
            "orders": len(rows),
            "ngay_dang_giao": ngay,
            "kho_n": len(by_kho),
            "buucuc_n": len(by_buucuc),
            "backend_n": len(by_backend),
            "with_tracking": sum(1 for r in rows if r.get("tracking_code")),
            "with_address": sum(1 for r in rows if r.get("full_address") or r.get("province")),
            "phone": dict(by_phone),
            "by_status": by_status.most_common(),
            "by_kho": by_kho.most_common(),
            "by_buucuc": by_buucuc.most_common(),
            "by_backend": by_backend.most_common(),
            "by_created_goc": by_created_goc.most_common(12),
            "icon_chant": chant(icons),
            "feedback": top_fb,
        },
        "kho_buucuc_matrix": matrix_out[:60],
        "samples": samples,
        "verdict": top_fb,
        "next_actions": [
            f"Ngày đang giao đã cập nhật: {ngay}",
            f"SQL: SELECT ngay_dang_giao, COUNT(*) FROM don_dang_giao GROUP BY 1 — {DB_PATH}",
            "SQL: SELECT * FROM don_dang_giao WHERE ngay_dang_giao = date('now') LIMIT 20",
            "Đổi ngày: python3 scripts/dang_giao_chi_tiet_table.py --as-of 2026-07-23",
            "Re-scan hôm nay: python3 scripts/dang_giao_chi_tiet_table.py",
        ],
        "safety": {
            "secrets_only": True,
            "no_dump_login": True,
            "no_mass_vtp_login": True,
            "keeps_original_created_at": True,
        },
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("📦 BẢNG CHI TIẾT ĐƠN ĐANG GIAO · MỌI KHO × MỌI BƯU CỤC")
    L(f"Lúc: {report['checked_at']}")
    L(f"📅 Ngày đang giao hàng: {report.get('ngay_dang_giao')} · cập nhật: {report.get('ngay_cap_nhat')}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    db = report["db"]
    L(f"✨ {s.get('feedback')}")
    L(f"Chant: {s.get('icon_chant')}")
    L(
        f"orders={s['orders']} @ {s.get('ngay_dang_giao')} · kho={s['kho_n']} buucuc={s['buucuc_n']} "
        f"backend={s['backend_n']} track={s['with_tracking']} addr={s['with_address']}"
    )
    L(f"phone={s.get('phone')}")
    L(f"DB: {db.get('path')} · table={db.get('table')} · ngay_dang_giao={db.get('ngay_dang_giao')}")
    L(f"include_shipped={report.get('include_shipped')}")
    L("")
    L("=== Ngày tạo gốc (giữ nguyên) ===")
    for d, n in (s.get("by_created_goc") or [])[:10]:
        L(f"· created_goc {d}: {n}")
    L("")
    L("=== Theo status ===")
    for st, n in s.get("by_status") or []:
        L(f"· {st}: {n}")
    L("")
    L("=== Theo kho ===")
    for k, n in (s.get("by_kho") or [])[:12]:
        L(f"· {k}: {n}")
    L("")
    L("=== Theo bưu cục ===")
    for b, n in (s.get("by_buucuc") or [])[:12]:
        L(f"· {b}: {n}")
    L("")
    L("=== Theo backend ===")
    for b, n in s.get("by_backend") or []:
        L(f"· {b}: {n}")
    L("")
    L("=== Ma trận kho × bưu cục ===")
    for m in report.get("kho_buucuc_matrix") or []:
        L(
            f"· {m['kho']} × {m['buucuc']} [{m['backend']}]: "
            f"n={m['orders']} track={m['with_tracking']} addr={m['with_address']}"
        )
    L("")
    L("=== Mẫu đơn chi tiết ===")
    for r in (report.get("samples") or [])[:16]:
        L(
            f"· [{r.get('van_tay')}] @{r.get('ngay_dang_giao')} so={r.get('so_noi_bo')} "
            f"{r.get('kho')}/{r.get('buucuc')} track={r.get('tracking') or '∅'} "
            f"· {r.get('status')} · {r.get('phone_class')} · {r.get('address') or '∅addr'}"
        )
        if r.get("order_created_at_goc"):
            L(f"  tạo gốc={r.get('order_created_at_goc')}")
        if r.get("tracking_url"):
            L(f"  aship: {r.get('tracking_url')}")
        if r.get("flow_path"):
            L(f"  flow: {r['flow_path']}")
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
    # also export flat CSV of the detail table
    csv_path = REPORTS / "dang_giao_chi_tiet.csv"
    if DB_PATH.is_file():
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM don_dang_giao").fetchall()
        if rows:
            cols = rows[0].keys()
            with csv_path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(cols))
                w.writeheader()
                for r in rows:
                    w.writerow({k: r[k] for k in cols})
        conn.close()
    paths = {
        "json": REPORTS / "dang_giao_chi_tiet.json",
        "txt": REPORTS / "dang_giao_chi_tiet.txt",
        "csv": csv_path,
        "rt_json": OUT / "dang_giao_chi_tiet.json",
        "rt_txt": OUT / "dang_giao_chi_tiet.txt",
        "db": DB_PATH,
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Gom đơn đang giao mọi kho×bưu cục → 1 bảng chi tiết")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Chỉ status Đang giao (bỏ Đã gửi hàng)",
    )
    ap.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        help="Ngày đang giao hàng YYYY-MM-DD (mặc định hôm nay UTC)",
    )
    ap.add_argument("--limit", type=int, default=8000)
    args = ap.parse_args()
    report = build_report(
        include_shipped=not args.strict,
        ingest_limit=args.limit,
        as_of=args.as_of,
    )
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
