#!/usr/bin/env python3
"""Xuất orders_detailed_*.csv + JSON đầy đủ từ Pancake / inbox.

Owned-only. Không dump-login.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

INBOX = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"

CSV_FIELDS = [
    "order_key",
    "remote_id",
    "custom_id",
    "source",
    "platform",
    "shop_id",
    "shop_name",
    "status_normalized",
    "status_raw",
    "customer_name",
    "customer_phone",
    "carrier",
    "tracking_code",
    "province",
    "district",
    "ward",
    "address_detail",
    "full_address",
    "cod_amount",
    "amount",
    "total_price",
    "quantity",
    "shipping_fee",
    "warehouse_id",
    "warehouse_name",
    "creator",
    "assigning_seller",
    "assigning_care",
    "order_created_at",
    "synced_at",
    "updated_at",
    "channel",
    "file",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (
        ROOT / "secrets" / "telegram.env",
        ROOT / "secrets" / "backend_pipes.env",
        ROOT / "secrets" / "pancake.env",
        ROOT / "secrets" / "order_session.env",
        ROOT / "secrets" / "owned_accounts.env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    api_settings = ROOT / "secrets" / "api_settings.local.json"
    if api_settings.is_file():
        try:
            data = json.loads(api_settings.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                mapping = {
                    "pancake_token": "PANCAKE_POS_ACCESS_TOKEN",
                    "pancake_pos_token": "PANCAKE_POS_ACCESS_TOKEN",
                    "pancake_api_key": "PANCAKE_POS_API_KEY",
                    "pancake_pos_api_key": "PANCAKE_POS_API_KEY",
                    "pancake_shop_id": "PANCAKE_SHOP_ID",
                }
                for src, dst in mapping.items():
                    val = data.get(src)
                    if val and not env.get(dst):
                        env[dst] = str(val).strip()
        except (OSError, json.JSONDecodeError):
            pass
    try:
        from owned_credentials import env_overlay_from_owned

        env = env_overlay_from_owned(env)
    except Exception:  # noqa: BLE001
        pass
    return env


def pancake_order_to_row(order: dict, *, shop_id: str, shop_name: str | None, file_name: str) -> dict:
    from oms_interconnect import normalize_from_json_order

    rec = normalize_from_json_order(
        {
            "order_key": str(order.get("id") or order.get("order_key") or ""),
            "remote_id": str(order.get("id") or ""),
            "id": order.get("id"),
            "shop_id": shop_id,
            "platform": "Pancake/POS",
            "source": "pancake_pos_api",
            "status_raw": order.get("status"),
            "status_normalized": order.get("status_name") or order.get("status"),
            "customer_name": order.get("bill_full_name"),
            "customer_phone": order.get("bill_phone_number"),
            "payload": order,
            "order_created_at": order.get("inserted_at") or order.get("created_at"),
            "synced_at": utc_now(),
            "updated_at": order.get("updated_at") or utc_now(),
        },
        file_name,
    )
    row = {f: "" for f in CSV_FIELDS}
    row.update(
        {
            "order_key": rec.get("order_key") or rec.get("remote_id") or "",
            "remote_id": rec.get("remote_id") or "",
            "custom_id": str(order.get("custom_id") or order.get("display_id") or ""),
            "source": "pancake_pos_api",
            "platform": "Pancake/POS",
            "shop_id": shop_id,
            "shop_name": shop_name or rec.get("shop_name") or "",
            "status_normalized": rec.get("status") or "",
            "status_raw": order.get("status_name") or str(order.get("status") or ""),
            "customer_name": rec.get("customer_name") or "",
            "customer_phone": rec.get("customer_phone") or "",
            "carrier": rec.get("carrier") or "",
            "tracking_code": rec.get("tracking_code") or "",
            "province": rec.get("province") or "",
            "district": rec.get("district") or "",
            "ward": rec.get("ward") or "",
            "address_detail": rec.get("address_detail") or "",
            "full_address": rec.get("full_address") or "",
            "cod_amount": str(rec.get("cod_amount") or order.get("cod") or ""),
            "amount": str(order.get("total_price") or order.get("total") or ""),
            "total_price": str(order.get("total_price") or order.get("total") or ""),
            "quantity": str(order.get("quantity") or ""),
            "shipping_fee": str(order.get("shipping_fee") or order.get("ship_fee") or ""),
            "warehouse_id": str(rec.get("warehouse_id") or ""),
            "warehouse_name": rec.get("warehouse_display_name") or rec.get("warehouse_name") or "",
            "creator": str(rec.get("creator") or ""),
            "assigning_seller": str(rec.get("assigning_seller") or ""),
            "assigning_care": str(rec.get("assigning_care") or ""),
            "order_created_at": rec.get("order_created_at") or "",
            "synced_at": utc_now(),
            "updated_at": rec.get("updated_at") or utc_now(),
            "channel": "pancake_api",
            "file": file_name,
        }
    )
    return row


def fetch_pancake_rows(env: dict[str, str], *, limit: int, days: int) -> tuple[list[dict], list[dict], str]:
    from scan_buucuc_orders import build_report

    report = build_report(days=days, limit=limit, notify=False, pipe=False, write_cache=False)
    file_name = f"orders_detailed_api_{stamp()}.csv"
    rows: list[dict] = []
    for o in report.get("orders") or []:
        if isinstance(o, dict):
            rows.append(scan_order_to_row(o, file_name))
    detail = report.get("verdict") or f"count={len(rows)}"
    attempts = [b for b in (report.get("backends") or []) if isinstance(b, dict)]
    return rows, attempts, str(detail)[:300]


def scan_order_to_row(o: dict, file_name: str) -> dict:
    row = {f: "" for f in CSV_FIELDS}
    row.update(
        {
            "order_key": str(o.get("custom_id") or o.get("order_id") or ""),
            "remote_id": str(o.get("order_id") or ""),
            "custom_id": str(o.get("custom_id") or ""),
            "source": o.get("source") or "scan_buucuc_orders",
            "platform": o.get("platform") or o.get("backend") or "",
            "shop_id": str(o.get("shop_id") or ""),
            "shop_name": str(o.get("shop_name") or ""),
            "status_normalized": str(o.get("status") or ""),
            "status_raw": str(o.get("status_raw") or o.get("status") or ""),
            "customer_name": str(o.get("customer_name") or ""),
            "customer_phone": str(o.get("customer_phone") or ""),
            "carrier": str(o.get("carrier") or o.get("backend") or ""),
            "tracking_code": str(o.get("tracking_code") or ""),
            "province": str(o.get("province") or ""),
            "district": str(o.get("district") or ""),
            "ward": str(o.get("ward") or ""),
            "address_detail": str(o.get("address") or ""),
            "full_address": str(o.get("full_address") or o.get("address") or ""),
            "cod_amount": str(o.get("cod_amount") or ""),
            "amount": str(o.get("total_price") or ""),
            "total_price": str(o.get("total_price") or ""),
            "quantity": str(o.get("quantity") or ""),
            "shipping_fee": str(o.get("shipping_fee") or ""),
            "warehouse_id": str(o.get("warehouse_id") or ""),
            "warehouse_name": str(o.get("kho") or o.get("warehouse") or ""),
            "order_created_at": str(o.get("order_created_at") or o.get("created_at") or ""),
            "synced_at": utc_now(),
            "updated_at": utc_now(),
            "channel": o.get("channel") or "remote_api",
            "file": file_name,
        }
    )
    return row


def load_cached_scan_rows() -> list[dict]:
    """Đọc đơn từ cache scan trước đó (sau pancake ingest / buucuc scan)."""
    paths = [
        ROOT / "docker/nginx-order/orders_buucuc_scan_cache.json",
        REPORTS / "scan_buucuc_orders_full.json",
        REPORTS / "scan_buucuc_orders.json",
    ]
    rows: list[dict] = []
    for p in paths:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        file_name = f"cache_{p.name}"
        for o in data.get("orders") or []:
            if isinstance(o, dict):
                rows.append(scan_order_to_row(o, file_name))
    return rows


def load_inbox_rows() -> list[dict]:
    from oms_interconnect import ingest_local_orders

    rows: list[dict] = []
    for rec in ingest_local_orders():
        row = {f: "" for f in CSV_FIELDS}
        row.update(
            {
                "order_key": str(rec.get("order_key") or rec.get("remote_id") or ""),
                "remote_id": str(rec.get("remote_id") or ""),
                "source": rec.get("source") or "inbox",
                "platform": rec.get("platform") or "",
                "shop_id": str(rec.get("shop_id") or ""),
                "shop_name": rec.get("shop_name") or "",
                "status_normalized": rec.get("status") or "",
                "status_raw": rec.get("status_raw") or rec.get("status") or "",
                "customer_name": rec.get("customer_name") or "",
                "customer_phone": rec.get("customer_phone") or "",
                "carrier": rec.get("carrier") or "",
                "tracking_code": rec.get("tracking_code") or "",
                "province": rec.get("province") or "",
                "district": rec.get("district") or "",
                "ward": rec.get("ward") or "",
                "address_detail": rec.get("address_detail") or "",
                "full_address": rec.get("full_address") or "",
                "cod_amount": str(rec.get("cod_amount") or ""),
                "order_created_at": rec.get("order_created_at") or "",
                "synced_at": rec.get("synced_at") or utc_now(),
                "updated_at": rec.get("updated_at") or utc_now(),
                "channel": rec.get("channel") or "inbox",
                "file": rec.get("file") or "",
            }
        )
        if row.get("order_key") or row.get("remote_id"):
            rows.append(row)
    return rows


def _phone_rank(raw: str) -> int:
    """Higher = better phone for dedupe merge."""
    from fix_order_phones import is_masked

    s = str(raw or "").strip()
    if not s:
        return 0
    if is_masked(s):
        return 1
    return 2


def dedupe_rows(rows: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        key = str(r.get("order_key") or r.get("remote_id") or "")
        if not key:
            continue
        prev = out.get(key)
        if prev is None:
            out[key] = r
            continue
        merged = dict(prev)
        # Prefer row with clearer (unmasked) phone
        if _phone_rank(r.get("customer_phone")) > _phone_rank(prev.get("customer_phone")):
            merged = dict(r)
            other = prev
        else:
            other = r
        for k, v in other.items():
            if v and not merged.get(k):
                merged[k] = v
            elif k == "customer_phone" and v and _phone_rank(v) > _phone_rank(merged.get(k)):
                merged[k] = v
        out[key] = merged
    return list(out.values())


def write_outputs(rows: list[dict], *, label: str = "Dang_giao") -> dict[str, Path]:
    INBOX.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    ts = stamp()
    csv_path = INBOX / f"orders_detailed_{label}_{ts}.csv"
    json_path = INBOX / f"orders_detailed_{label}_{ts}.json"
    result_csv = REPORTS / "orders_detailed_RESULT.csv"
    result_txt = REPORTS / "orders_detailed_RESULT.txt"

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    result_csv.write_bytes(csv_path.read_bytes())
    result_txt.write_text(
        "\n".join(
            [
                f"orders_detailed export · {utc_now()}",
                f"rows={len(rows)}",
                f"csv={csv_path}",
                f"json={json_path}",
                f"result={result_csv}",
            ]
        ),
        encoding="utf-8",
    )
    return {"csv": csv_path, "json": json_path, "result_csv": result_csv, "result_txt": result_txt}


def bootstrap_secrets_from_inbox() -> list[str]:
    imported: list[str] = []
    SECRETS = ROOT / "secrets"
    SECRETS.mkdir(parents=True, exist_ok=True)
    for name in ("api_settings.local.json", "pancake_storage_state.json", "order_session.env", "v9_credentials.env"):
        src = INBOX / name
        if src.is_file():
            dest = SECRETS / name
            dest.write_bytes(src.read_bytes())
            imported.append(name)
    for p in INBOX.glob("api_settings*.json"):
        if p.name == "api_settings.local.json":
            continue
        dest = SECRETS / "api_settings.local.json"
        dest.write_bytes(p.read_bytes())
        imported.append(p.name)
    return imported


def build_report(*, limit: int = 5000, days: int = 7) -> dict:
    imported = bootstrap_secrets_from_inbox()
    env = load_env()
    sources: list[str] = []
    attempts: list[dict] = []
    rows = load_inbox_rows()
    cached = load_cached_scan_rows()
    if rows:
        sources.append(f"inbox:{len(rows)}")
    if cached:
        sources.append(f"cache:{len(cached)}")

    api_rows: list[dict] = []
    api_detail = ""
    try:
        api_rows, attempts, api_detail = fetch_pancake_rows(env, limit=limit, days=days)
        if api_rows:
            sources.append(f"api_scan:{len(api_rows)}")
    except Exception as e:  # noqa: BLE001
        api_detail = str(e)[:200]

    rows = dedupe_rows(rows + cached + api_rows)
    paths = write_outputs(rows) if rows else {}

    return {
        "ok": bool(rows),
        "exported_at": utc_now(),
        "rows": len(rows),
        "sources": sources,
        "imported_secrets": imported,
        "api_detail": api_detail,
        "attempts": attempts[:20],
        "paths": {k: str(v) for k, v in paths.items()},
        "blockers": [] if rows else [
            "Thiếu PANCAKE token trong secrets/backend_pipes.env hoặc secrets/api_settings.local.json",
            "Hoặc gửi file orders_detailed_*.csv/json vào Telegram rồi chạy lại",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Xuất orders_detailed đầy đủ")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = build_report(limit=args.limit, days=args.days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
