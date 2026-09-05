#!/usr/bin/env python3
"""Kéo đơn nóng SamSpa 1530618 từ Pancake API (live)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SHOP = "1530618"

STATUS_MAP = {
    "0": "Mới",
    "1": "Chờ xác nhận",
    "2": "Đã xác nhận",
    "3": "Đang đóng gói",
    "4": "Đang giao",
    "5": "Hoàn thành",
    "6": "Đã hủy",
    "8": "Trả hàng",
}
URGENT = {"Mới", "Chờ xác nhận", "Đã xác nhận", "Đang đóng gói"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_token() -> str:
    from export_orders_detailed import load_env

    env = load_env()
    return env.get("PANCAKE_POS_ACCESS_TOKEN") or env.get("PANCAKE_POS_API_KEY") or ""


def fetch_hot(*, hours: int = 48, max_pages: int = 30) -> list[dict]:
    import requests

    token = load_token()
    if not token:
        return []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Cookie": f"pos_jwt={token}; pos_locale=vi",
    }
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        r = requests.get(
            f"https://pos.pancake.vn/api/v1/shops/{SHOP}/orders",
            headers=headers,
            params={"page_number": page, "page_size": 100},
            timeout=45,
        )
        if not r.ok:
            break
        data = r.json().get("data") or []
        if not data:
            break
        for row in data:
            created = row.get("inserted_at") or row.get("created_at")
            dt = None
            if created:
                try:
                    dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    dt = None
            age_h = (now - dt).total_seconds() / 3600 if dt else 9999.0
            if age_h > hours:
                continue
            sa = row.get("shipping_address") if isinstance(row.get("shipping_address"), dict) else {}
            st_code = str(row.get("status") if row.get("status") is not None else "")
            st_name = str(row.get("status_name") or STATUS_MAP.get(st_code, st_code))
            rows.append(
                {
                    "order_id": str(row.get("id") or ""),
                    "custom_id": str(row.get("custom_id") or row.get("display_id") or ""),
                    "shop_id": SHOP,
                    "shop_name": "SamSpa Shop",
                    "status": st_name,
                    "status_code": st_code,
                    "customer_name": (sa.get("full_name") or row.get("bill_full_name") or "")[:80],
                    "customer_phone": sa.get("phone_number") or row.get("bill_phone_number") or "",
                    "tracking_code": str(row.get("tracking_code") or row.get("extend_code") or ""),
                    "amount": row.get("total_price") or row.get("cod") or 0,
                    "cod": row.get("cod") or 0,
                    "order_created_at": created or "",
                    "age_hours": round(age_h, 1),
                    "province": sa.get("province_name") or "",
                    "address": (sa.get("full_address") or sa.get("address") or "")[:200],
                    "channel": row.get("page_name") or row.get("source") or "",
                }
            )
        try:
            last = data[-1].get("inserted_at") or data[-1].get("created_at")
            ldt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if ldt.tzinfo is None:
                ldt = ldt.replace(tzinfo=timezone.utc)
            if (now - ldt).total_seconds() / 3600 > hours:
                break
        except (ValueError, TypeError):
            pass
    rows.sort(key=lambda x: x.get("order_created_at") or "", reverse=True)
    return rows


def write_outputs(rows: list[dict], *, hours: int) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    all_csv = REPORTS / f"DON_NONG_SAMSPA_{hours}H_{ts}.csv"
    urgent = [r for r in rows if r.get("status") in URGENT]
    urg_csv = REPORTS / f"DON_NONG_CAN_XU_LY_{ts}.csv"
    fields = list(rows[0].keys()) if rows else []
    for path, data in ((all_csv, rows), (urg_csv, urgent)):
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(data)
    latest = REPORTS / "DON_NONG_SAMSPA_LATEST.csv"
    latest.write_bytes(all_csv.read_bytes())
    meta = REPORTS / "DON_NONG_SAMSPA.json"
    meta.write_text(
        json.dumps(
            {
                "checked_at": utc_now(),
                "hours": hours,
                "total": len(rows),
                "urgent": len(urgent),
                "by_status": dict(Counter(r.get("status") or "" for r in rows)),
                "files": {"all": str(all_csv), "urgent": str(urg_csv), "latest": str(latest)},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"all": all_csv, "urgent": urg_csv, "latest": latest, "meta": meta}


def notify_telegram(rows: list[dict], paths: dict[str, Path], *, hours: int) -> bool:
    urgent = [r for r in rows if r.get("status") in URGENT]
    env = {}
    p = ROOT / "secrets" / "telegram.env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return False
    import requests

    msg = (
        f"🔥 Đơn nóng SamSpa {SHOP} · {hours}h qua\n"
        f"· Tổng: {len(rows)} đơn\n"
        f"· Cần xử lý: {len(urgent)} (Chờ xác nhận/Mới/đóng gói)\n"
        f"· Lúc: {utc_now()}\n"
        f"⚠️ SĐT mask từ API — export Excel POS để SĐT đầy đủ."
    )
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": msg},
        timeout=30,
    )
    urg = paths.get("urgent")
    if urg and urg.is_file():
        with urg.open("rb") as fh:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat, "caption": f"DON_NONG_CAN_XU_LY · {len(urgent)} đơn"},
                files={"document": (urg.name, fh, "text/csv")},
                timeout=60,
            )
    return True


def build_report(*, hours: int = 48, notify: bool = False) -> dict:
    rows = fetch_hot(hours=hours)
    paths = write_outputs(rows, hours=hours) if rows else {}
    rep = {
        "ok": bool(rows),
        "module": "don_nong_samspa",
        "checked_at": utc_now(),
        "shop_id": SHOP,
        "hours": hours,
        "total": len(rows),
        "urgent": sum(1 for r in rows if r.get("status") in URGENT),
        "by_status": dict(Counter(r.get("status") or "" for r in rows)),
        "paths": {k: str(v) for k, v in paths.items()},
        "verdict": f"Đơn nóng {len(rows)} / {hours}h · cần xử lý {sum(1 for r in rows if r.get('status') in URGENT)}",
    }
    if notify and paths:
        rep["telegram"] = notify_telegram(rows, paths, hours=hours)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="Kéo đơn nóng SamSpa live")
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = build_report(hours=args.hours, notify=args.notify)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(rep.get("verdict"))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
