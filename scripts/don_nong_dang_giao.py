#!/usr/bin/env python3
"""Đơn nóng đang giao — chỉ nguồn owned, SĐT không mask."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
KET_QUA = REPORTS / "KET_QUA_DON_CHIET_TIET.csv"
OUT_LATEST = REPORTS / "DON_NONG_DANG_GIAO_LATEST.csv"

EXPORT_FIELDS = [
    "order_key",
    "remote_id",
    "custom_id",
    "customer_name",
    "customer_phone",
    "status",
    "status_raw",
    "carrier",
    "tracking_code",
    "tracking_url",
    "province",
    "district",
    "address_detail",
    "full_address",
    "cod_amount",
    "amount",
    "order_created_at",
    "channel",
    "file",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_shipping_status(status: str | None) -> bool:
    from dang_giao_chi_tiet_table import is_in_transit

    return is_in_transit(status, include_shipped=True)


def load_rows() -> list[dict]:
    from fix_order_phones import filter_usable_rows

    rows: list[dict] = []
    if KET_QUA.is_file():
        with KET_QUA.open(encoding="utf-8", errors="replace", newline="") as fh:
            rows.extend(csv.DictReader(fh))
    else:
        dg = REPORTS / "dang_giao_chi_tiet.csv"
        if dg.is_file():
            with dg.open(encoding="utf-8", errors="replace", newline="") as fh:
                rows.extend(csv.DictReader(fh))
    usable, _ = filter_usable_rows(rows)
    return usable


def enrich_tracking(row: dict) -> dict:
    from tracking_aship import attach_tracking_urls

    out = attach_tracking_urls(dict(row))
    status = out.get("status_normalized") or out.get("status") or out.get("status_raw") or ""
    out["status"] = status
    return out


def filter_shipping(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        st = str(r.get("status_normalized") or r.get("status") or r.get("status_raw") or "")
        st_raw = str(r.get("status_raw") or "")
        if is_shipping_status(st) or is_shipping_status(st_raw):
            out.append(enrich_tracking(r))
    out.sort(key=lambda x: str(x.get("order_created_at") or ""), reverse=True)
    return out


def write_outputs(rows: list[dict]) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = REPORTS / f"DON_NONG_DANG_GIAO_{ts}.csv"
    json_path = REPORTS / f"DON_NONG_DANG_GIAO_{ts}.json"
    fields = [f for f in EXPORT_FIELDS if any(f in r for r in rows)] or EXPORT_FIELDS
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({f: r.get(f, "") for f in fields})
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_LATEST.write_bytes(csv_path.read_bytes())
    meta = REPORTS / "DON_NONG_DANG_GIAO.json"
    meta.write_text(
        json.dumps(
            {
                "checked_at": utc_now(),
                "rows": len(rows),
                "by_status": dict(Counter(r.get("status") for r in rows)),
                "by_carrier": dict(Counter(r.get("carrier") or "none" for r in rows)),
                "csv": str(csv_path),
                "latest": str(OUT_LATEST),
                "policy": "owned_only_no_mask",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"csv": csv_path, "json": json_path, "latest": OUT_LATEST, "meta": meta}


def notify_telegram(rows: list[dict], paths: dict[str, Path]) -> bool:
    import requests

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
    by_c = Counter(r.get("carrier") or "none" for r in rows)
    msg = (
        f"🚚 Đơn nóng đang giao · SĐT đầy đủ\n"
        f"· {len(rows)} đơn (file Excel bạn gửi)\n"
        f"· VNP={by_c.get('VNP',0)} · J&T={by_c.get('J&T',0)}\n"
        f"· Không mask · có link tracking\n"
        f"⚠️ Cập nhật mới: export Excel POS → gửi bot"
    )
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": msg},
        timeout=30,
    )
    latest = paths.get("latest")
    if latest and latest.is_file():
        with latest.open("rb") as fh:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat, "caption": f"DON_NONG_DANG_GIAO · {len(rows)} đơn"},
                files={"document": (latest.name, fh, "text/csv")},
                timeout=60,
            )
    return True


def build_report(*, notify: bool = False) -> dict:
    all_rows = load_rows()
    shipping = filter_shipping(all_rows)
    paths = write_outputs(shipping) if shipping else {}
    rep = {
        "ok": bool(shipping),
        "module": "don_nong_dang_giao",
        "checked_at": utc_now(),
        "source": str(KET_QUA),
        "total_owned": len(all_rows),
        "shipping_rows": len(shipping),
        "by_status": dict(Counter(r.get("status") for r in shipping)),
        "by_carrier": dict(Counter(r.get("carrier") or "none" for r in shipping)),
        "paths": {k: str(v) for k, v in paths.items()},
        "policy": "owned_only_no_mask",
        "verdict": f"Đơn nóng đang giao: {len(shipping)} đơn · SĐT đầy đủ · không mask",
    }
    if notify and paths:
        rep["telegram"] = notify_telegram(shipping, paths)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="Đơn nóng đang giao (owned, không mask)")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = build_report(notify=args.notify)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(rep.get("verdict"))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
