#!/usr/bin/env python3
"""Đấu nối OMS toàn diện — bus trung tâm gom mọi ống đơn hàng.

Channels (secrets-only cho remote):
  Telegram · Pancake · GHN · ViettelPost · Tracking(aship) · TPOS
  direct_api/inbox · SPX/thanhcoong local · VNPost file · pipe-bus

Không đọc dump Acc_all/Ghn để login. Không auto-login mật khẩu.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
INBOX = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "oms"
STATE_FILE = SECRETS / "oms_interconnect.state.json"
ENV_FILES = (
    SECRETS / "telegram.env",
    SECRETS / "backend_pipes.env",
    SECRETS / "pancake.env",
)
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

CHANNEL_DEFS = [
    {"id": "telegram", "backend": "Telegram", "kind": "notify+inbox", "secret_keys": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]},
    {"id": "pancake", "backend": "Pancake", "kind": "orders_api", "secret_keys": ["PANCAKE_POS_API_KEY", "PANCAKE_POS_ACCESS_TOKEN", "PANCAKE_API_KEY"]},
    {"id": "ghn", "backend": "GHN", "kind": "shipping_api", "secret_keys": ["GHN_API_TOKEN"]},
    {"id": "viettelpost", "backend": "ViettelPost", "kind": "shipping_api", "secret_keys": ["VIETTELPOST_TOKEN", "VIETTELPOST_USER", "VIETTELPOST_PASSWORD"]},
    {"id": "tracking", "backend": "Tracking", "kind": "public_track", "secret_keys": []},
    {"id": "tpos", "backend": "TPOS", "kind": "odata", "secret_keys": ["TPOS_BASE_URL", "TPOS_ACCESS_TOKEN"]},
    {"id": "direct_api", "backend": "direct_api", "kind": "local_snapshot", "secret_keys": []},
    {"id": "spx_local", "backend": "SPX-local", "kind": "xlsx_3pl", "secret_keys": []},
    {"id": "vnpost_local", "backend": "VNPost-local", "kind": "file_recon", "secret_keys": []},
    {"id": "oms_bus", "backend": "OMS-pipe-bus", "kind": "registry", "secret_keys": []},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 20,
) -> tuple[int, Any]:
    hdrs = dict(headers or {})
    data = body
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw[:200]
            return int(resp.status), parsed
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw[:200]
        return int(e.code), parsed
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def phone_class(ph: str | None) -> str:
    ph = (ph or "").strip()
    if not ph:
        return "MISSING"
    if "*" in ph or set(ph) <= {"*"}:
        return "MASKED"
    digits = re.sub(r"\D", "", ph)
    if len(digits) < 9:
        return "INVALID"
    return "OK"


def env_has_any(env: dict[str, str], keys: list[str]) -> bool:
    return any((env.get(k) or "").strip() for k in keys)


# ----- local readers -----


def read_xlsx_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            texts = [
                t.text or ""
                for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
            ]
            shared.append("".join(texts))
    sheet_name = next((n for n in z.namelist() if n.startswith("xl/worksheets/sheet")), None)
    if not sheet_name:
        return []
    sheet = ET.fromstring(z.read(sheet_name))

    def col_row(ref: str) -> tuple[int, int]:
        m = re.match(r"([A-Z]+)(\d+)", ref)
        assert m
        col_s, row = m.group(1), int(m.group(2))
        n = 0
        for ch in col_s:
            n = n * 26 + (ord(ch) - 64)
        return n, row

    cells: dict[int, dict[int, str]] = defaultdict(dict)
    max_row = 0
    for c in sheet.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
        ref = c.get("r")
        if not ref:
            continue
        col, row = col_row(ref)
        max_row = max(max_row, row)
        v = c.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
        if v is None or v.text is None:
            continue
        cells[row][col] = shared[int(v.text)] if c.get("t") == "s" else v.text
    header = cells.get(1, {})
    names = {c: header[c] for c in sorted(header)}
    rows = []
    for r in range(2, max_row + 1):
        if r not in cells:
            continue
        rows.append({names.get(c, f"col{c}"): cells[r].get(c, "") for c in names})
    return rows


def normalize_from_csv_row(r: dict, file_name: str) -> dict:
    phone = (r.get("customer_phone") or "").strip()
    shop = (r.get("shop_id") or "").strip() or None
    return {
        "oms_id": f"csv:{r.get('order_key') or r.get('id') or ''}",
        "order_key": r.get("order_key"),
        "remote_id": r.get("remote_id"),
        "source": r.get("source"),
        "platform": r.get("platform"),
        "shop_id": shop,
        "shop_name": None,
        "page_id": None,
        "pancake_shop_id": shop,
        "status": r.get("status_normalized") or r.get("status_raw"),
        "customer_name": r.get("customer_name"),
        "customer_phone": phone,
        "phone_class": phone_class(phone),
        "warehouse_id": None,
        "warehouse_name": None,
        "warehouse_display_name": None,
        "assigning_seller": None,
        "assigning_care": None,
        "creator": None,
        "account": None,
        "carrier": None,
        "tracking_code": None,
        "province": None,
        "district": None,
        "channel": "direct_api" if "direct_api" in (r.get("source") or "") else "inbox_csv",
        "file": file_name,
    }


def normalize_from_json_order(o: dict, file_name: str) -> dict:
    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    wi = p.get("warehouse_info") if isinstance(p.get("warehouse_info"), dict) else {}
    addr = p.get("shipping_address") if isinstance(p.get("shipping_address"), dict) else {}
    phone = (o.get("customer_phone") or p.get("bill_phone_number") or "").strip()
    seller = p.get("assigning_seller")
    care = p.get("assigning_care")
    creator = p.get("creator")
    shipments = p.get("shipments") or []
    tracking = None
    carrier = None
    if isinstance(shipments, list) and shipments and isinstance(shipments[0], dict):
        s0 = shipments[0]
        tracking = s0.get("tracking_number") or s0.get("extend_code") or s0.get("partner_id")
        carrier = s0.get("partner_name") or s0.get("partner_id")
    outer_shop = o.get("shop_id")
    payload_shop = p.get("shop_id")
    shop_id = outer_shop or payload_shop
    # Tên shop gần đúng: warehouse_info.name (POS) / page / shop fields
    shop_name = wi.get("name") or p.get("shop_name") or p.get("page_name") or p.get("store_name")
    page = p.get("page") if isinstance(p.get("page"), dict) else None
    if not shop_name and page:
        shop_name = page.get("name") or page.get("username")
    account = p.get("account")
    return {
        "oms_id": f"json:{o.get('order_key') or o.get('id') or ''}",
        "order_key": o.get("order_key"),
        "remote_id": o.get("remote_id") or o.get("id"),
        "source": o.get("source"),
        "platform": o.get("platform"),
        "shop_id": str(shop_id) if shop_id not in (None, "") else None,
        "shop_name": shop_name,
        "page_id": p.get("page_id") or o.get("page_id"),
        "pancake_shop_id": str(payload_shop) if payload_shop not in (None, "") else None,
        "status": o.get("status_normalized") or o.get("status_raw") or p.get("status"),
        "customer_name": o.get("customer_name") or p.get("bill_full_name"),
        "customer_phone": phone,
        "phone_class": phone_class(phone),
        "warehouse_id": p.get("warehouse_id"),
        "warehouse_name": wi.get("custom_id") or wi.get("name"),
        "warehouse_display_name": wi.get("name") or wi.get("custom_id"),
        "assigning_seller": (seller.get("name") if isinstance(seller, dict) else seller),
        "assigning_care": (care.get("name") if isinstance(care, dict) else care),
        "creator": (creator.get("name") if isinstance(creator, dict) else creator) or account,
        "account": str(account) if account not in (None, "") else None,
        "carrier": carrier,
        "tracking_code": tracking,
        "province": addr.get("province_name") or addr.get("province"),
        "district": addr.get("district_name") or addr.get("district"),
        "channel": "pancake_payload" if p else "json_flat",
        "file": file_name,
    }


def normalize_from_thanhcoong(r: dict) -> dict:
    phone = (r.get("Receiver Phone Number") or r.get("Số điện thoại người nhận") or "").strip()
    sender = (r.get("Sender Name") or r.get("Tên người gửi") or "").strip()
    account = (r.get("Account ID") or r.get("ID tài khoản") or "").strip()
    creator = (r.get("Order Creator") or r.get("Người tạo đơn") or "").strip()
    # bỏ hàng header tiếng Việt/Anh
    if sender in {"Sender Name", "Tên người gửi"} or account in {"Account ID", "ID tài khoản"}:
        return {}
    shop_name = sender or None
    return {
        "oms_id": f"spx:{r.get('Tracking No.') or r.get('Mã vận đơn') or ''}",
        "order_key": r.get("Customer Reference No.") or r.get("Tracking No."),
        "remote_id": r.get("Tracking No.") or r.get("Mã vận đơn"),
        "source": "thanhcoong_xlsx",
        "platform": "SPX",
        "shop_id": account or None,
        "shop_name": shop_name,
        "page_id": None,
        "pancake_shop_id": None,
        "status": r.get("Tracking Status") or r.get("Trạng thái hiện tại"),
        "customer_name": r.get("Receiver Name"),
        "customer_phone": phone,
        "phone_class": phone_class(phone),
        "warehouse_id": None,
        "warehouse_name": sender or None,
        "warehouse_display_name": sender or None,
        "assigning_seller": None,
        "assigning_care": None,
        "creator": creator or None,
        "account": account or None,
        "carrier": r.get("3PL Name") or r.get("Tên 3PL") or "SPX",
        "tracking_code": r.get("Tracking No.") or r.get("Mã vận đơn"),
        "province": r.get("Receiver Province") or r.get("Tỉnh, thành"),
        "district": r.get("Receiver District(old)/Ward(new)")
        or r.get("Quận, huyện (cũ) / Phường, xã (mới)"),
        "channel": "spx_local",
        "file": "thanhcoong.xlsx",
    }


def ingest_local_orders(limit_per_file: int = 5000) -> list[dict]:
    records: list[dict] = []
    if not INBOX.is_dir():
        return records
    for cf in sorted(INBOX.glob("orders_detailed_*.csv")):
        with cf.open(newline="", encoding="utf-8", errors="replace") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows[:limit_per_file]:
            records.append(normalize_from_csv_row(r, cf.name))
    for jf in sorted(INBOX.glob("orders_detailed_*.json")):
        try:
            orders = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(orders, list):
            continue
        for o in orders[:limit_per_file]:
            if isinstance(o, dict):
                records.append(normalize_from_json_order(o, jf.name))
    xlsx = INBOX / "thanhcoong.xlsx"
    if xlsx.is_file():
        for r in read_xlsx_rows(xlsx)[:limit_per_file]:
            rec = normalize_from_thanhcoong(r)
            if rec:
                records.append(rec)
    return records


# ----- channel probes -----


def probe_telegram(env: dict[str, str]) -> dict:
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return {"id": "telegram", "status": "missing_cred", "detail": "TELEGRAM_BOT_TOKEN trống", "http": None}
    code, body = http_json(f"https://api.telegram.org/bot{token}/getMe")
    ok = code == 200 and isinstance(body, dict) and body.get("ok")
    uname = ""
    if isinstance(body, dict):
        uname = ((body.get("result") or {}) if isinstance(body.get("result"), dict) else {}).get("username") or ""
    return {
        "id": "telegram",
        "status": "connected" if ok else ("auth_fail" if code in (401, 403) else "error"),
        "detail": f"@{uname}" if ok else f"http={code}",
        "http": code,
    }


def probe_pancake(env: dict[str, str]) -> dict:
    from pancake_pos_client import auth_ready, resolve_credentials

    creds = resolve_credentials()
    # also honor env already loaded into os.environ by caller
    if not auth_ready(creds):
        return {"id": "pancake", "status": "missing_cred", "detail": "Thiếu PANCAKE_* key", "http": None}
    shop = (env.get("PANCAKE_SHOP_ID") or env.get("PANCAKE_DEFAULT_SHOP_ID") or "1530618").strip()
    try:
        from pancake_pos_client import fetch_shop_orders

        data, base = fetch_shop_orders(shop, creds, page=1, page_size=1)
        n = len(data.get("data") or data.get("orders") or []) if isinstance(data, dict) else 0
        return {
            "id": "pancake",
            "status": "connected",
            "detail": f"shop={shop} base={base} sample_orders={n}",
            "http": 200,
        }
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        status = "auth_fail" if re.search(r"401|403|Unauthorized", msg, re.I) else "error"
        return {"id": "pancake", "status": status, "detail": msg[:160], "http": None}


def probe_ghn(env: dict[str, str]) -> dict:
    token = (env.get("GHN_API_TOKEN") or "").strip()
    if not token:
        return {"id": "ghn", "status": "missing_cred", "detail": "Thiếu GHN_API_TOKEN", "http": None}
    url = "https://dev-online-gateway.ghn.vn/shiip/public-api/master-data/province"
    code, _ = http_json(url, method="POST", headers={"Token": token, "Content-Type": "application/json"}, body=b"{}")
    if code in (401, 403):
        return {"id": "ghn", "status": "auth_fail", "detail": "Token GHN fail", "http": code}
    return {
        "id": "ghn",
        "status": "connected" if code == 200 else "error",
        "detail": f"province probe http={code}",
        "http": code,
    }


def probe_viettelpost(env: dict[str, str]) -> dict:
    token = (env.get("VIETTELPOST_TOKEN") or "").strip()
    user = (env.get("VIETTELPOST_USER") or "").strip()
    password = (env.get("VIETTELPOST_PASSWORD") or "").strip()
    if not token and not (user and password):
        return {
            "id": "viettelpost",
            "status": "missing_cred",
            "detail": "Thiếu VIETTELPOST_TOKEN hoặc USER/PASSWORD owned",
            "http": None,
        }
    # Prefer token ping via getPriceAll or list — use login only if user/pass and no token
    if token:
        # lightweight authenticated call — tracking with empty may 400 but proves host
        code, body = http_json(
            "https://partner.viettelpost.vn/v2/order/trackingOrder",
            method="POST",
            headers={"Token": token, "Content-Type": "application/json"},
            body=json.dumps({"orderNumber": "OMS-PING"}).encode(),
        )
        # 200/201/400/404 with JSON often means pipe reachable; 401/403 = auth fail
        if code in (401, 403):
            return {"id": "viettelpost", "status": "auth_fail", "detail": "Token VTP fail", "http": code}
        if code == 0:
            return {"id": "viettelpost", "status": "error", "detail": str(body)[:160], "http": 0}
        return {
            "id": "viettelpost",
            "status": "connected",
            "detail": f"trackingOrder probe http={code} (pipe reachable)",
            "http": code,
        }
    # login with owned user/pass
    code, body = http_json(
        "https://partner.viettelpost.vn/v2/user/Login",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"USERNAME": user, "PASSWORD": password}).encode(),
    )
    if code in (401, 403) or (
        isinstance(body, dict) and str(body.get("error") or body.get("status") or "").lower() in {"401", "403", "fail"}
    ):
        return {"id": "viettelpost", "status": "auth_fail", "detail": f"Login VTP http={code}", "http": code}
    ok = code == 200
    return {
        "id": "viettelpost",
        "status": "connected" if ok else "error",
        "detail": f"Login probe http={code}",
        "http": code,
    }


def probe_tracking(_: dict[str, str]) -> dict:
    # Public host — HEAD/GET root or known path
    url = "https://tracking.aship.app/"
    code, _ = http_json(url, method="GET")
    if code == 0:
        # try order endpoint shape
        code, _ = http_json(
            "https://tracking.aship.app/order?provider_code=OMS-PING&provider=ghn",
            method="GET",
        )
    return {
        "id": "tracking",
        "status": "connected" if code and code < 500 else "error",
        "detail": f"tracking.aship.app http={code}",
        "http": code,
    }


def probe_tpos(env: dict[str, str]) -> dict:
    base = (env.get("TPOS_BASE_URL") or "").rstrip("/")
    token = (env.get("TPOS_ACCESS_TOKEN") or "").strip()
    if not base or not token:
        return {"id": "tpos", "status": "missing_cred", "detail": "Thiếu TPOS_BASE_URL + TPOS_ACCESS_TOKEN", "http": None}
    code, _ = http_json(f"{base}/odata", headers={"Authorization": f"Bearer {token}"})
    if code in (401, 403):
        return {"id": "tpos", "status": "auth_fail", "detail": "Bearer TPOS fail", "http": code}
    return {
        "id": "tpos",
        "status": "connected" if code and code < 500 else "error",
        "detail": f"odata http={code}",
        "http": code,
    }


def probe_direct_api(_: dict[str, str]) -> dict:
    snaps = list(INBOX.glob("orders_detailed_*")) if INBOX.is_dir() else []
    if not snaps:
        return {"id": "direct_api", "status": "missing_cred", "detail": "Chưa có orders_detailed_*", "http": None}
    newest = max(snaps, key=lambda p: p.stat().st_mtime)
    age_h = (time.time() - newest.stat().st_mtime) / 3600
    return {
        "id": "direct_api",
        "status": "connected" if age_h <= 72 else "stale",
        "detail": f"file={newest.name} age_h={age_h:.1f}",
        "http": None,
    }


def probe_spx_local(_: dict[str, str]) -> dict:
    path = INBOX / "thanhcoong.xlsx"
    if not path.is_file():
        return {"id": "spx_local", "status": "missing_cred", "detail": "thiếu thanhcoong.xlsx", "http": None}
    rows = read_xlsx_rows(path)
    return {
        "id": "spx_local",
        "status": "connected",
        "detail": f"rows={len(rows)} file=thanhcoong.xlsx",
        "http": None,
    }


def probe_vnpost_local(_: dict[str, str]) -> dict:
    files = list(INBOX.glob("vnpost_ok*")) if INBOX.is_dir() else []
    if not files:
        return {"id": "vnpost_local", "status": "missing_cred", "detail": "thiếu vnpost_ok*", "http": None}
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return {
        "id": "vnpost_local",
        "status": "connected",
        "detail": f"file={newest.name} bytes={newest.stat().st_size}",
        "http": None,
    }


def probe_oms_bus(env: dict[str, str], channels: list[dict]) -> dict:
    tg = next((c for c in channels if c["id"] == "telegram"), None)
    SECRETS.mkdir(parents=True, exist_ok=True)
    writable = os.access(SECRETS, os.W_OK)
    alive = bool(tg and tg.get("status") == "connected" and writable)
    connected_n = sum(1 for c in channels if c.get("status") == "connected")
    return {
        "id": "oms_bus",
        "status": "connected" if alive else "error",
        "detail": f"channels_connected={connected_n}/{len(channels)} writable={writable}",
        "http": None,
    }


def run_channel_probes(env: dict[str, str]) -> list[dict]:
    # Ensure pancake client sees secrets
    for k, v in env.items():
        os.environ.setdefault(k, v)

    results = [
        probe_telegram(env),
        probe_pancake(env),
        probe_ghn(env),
        probe_viettelpost(env),
        probe_tracking(env),
        probe_tpos(env),
        probe_direct_api(env),
        probe_spx_local(env),
        probe_vnpost_local(env),
    ]
    results.append(probe_oms_bus(env, results))
    # enrich with channel defs
    by_id = {c["id"]: c for c in CHANNEL_DEFS}
    out = []
    for r in results:
        meta = by_id.get(r["id"], {})
        out.append(
            {
                **r,
                "backend": meta.get("backend", r["id"]),
                "kind": meta.get("kind"),
                "secret_keys": meta.get("secret_keys", []),
                "secrets_present": env_has_any(env, meta.get("secret_keys") or [])
                if meta.get("secret_keys")
                else True,
            }
        )
    return out


def summarize_orders(records: list[dict]) -> dict:
    phone = Counter(r.get("phone_class") for r in records)
    source = Counter(r.get("source") or "(empty)" for r in records)
    channel = Counter(r.get("channel") or "(empty)" for r in records)
    carrier = Counter((r.get("carrier") or "(none)") for r in records)
    province = Counter((r.get("province") or "(none)")[:60] for r in records)
    warehouse = Counter((r.get("warehouse_name") or "(none)") for r in records)
    staff_seller = sum(1 for r in records if r.get("assigning_seller"))
    staff_care = sum(1 for r in records if r.get("assigning_care"))
    with_tracking = sum(1 for r in records if r.get("tracking_code"))
    daklak = [
        {
            "oms_id": r.get("oms_id"),
            "tracking_code": r.get("tracking_code"),
            "carrier": r.get("carrier"),
            "province": r.get("province"),
            "district": r.get("district"),
            "status": r.get("status"),
            "creator": r.get("creator"),
        }
        for r in records
        if re.search(r"đắk\s*lắk|dak\s*lak|daklak", str(r.get("province") or ""), re.I)
    ]
    return {
        "total": len(records),
        "phone": dict(phone),
        "by_source_top": source.most_common(15),
        "by_channel": channel.most_common(),
        "by_carrier": carrier.most_common(15),
        "by_province_top": province.most_common(20),
        "by_warehouse": warehouse.most_common(10),
        "with_assigning_seller": staff_seller,
        "with_assigning_care": staff_care,
        "with_tracking": with_tracking,
        "daklak_orders": daklak,
        "daklak_count": len(daklak),
    }


def build_links(channels: list[dict]) -> list[dict]:
    """Logical interconnect edges OMS bus ↔ channels."""
    status = {c["id"]: c["status"] for c in channels}
    edges = [
        ("oms_bus", "telegram", "notify"),
        ("oms_bus", "direct_api", "ingest_snapshot"),
        ("oms_bus", "pancake", "pull_orders"),
        ("oms_bus", "ghn", "shipping_status"),
        ("oms_bus", "viettelpost", "shipping_status"),
        ("oms_bus", "tracking", "public_track"),
        ("oms_bus", "tpos", "delivery_view"),
        ("oms_bus", "spx_local", "ingest_3pl"),
        ("oms_bus", "vnpost_local", "recon"),
        ("pancake", "ghn", "create_waybill"),
        ("pancake", "viettelpost", "create_waybill"),
        ("ghn", "tracking", "provider_code"),
        ("viettelpost", "tracking", "provider_code"),
        ("spx_local", "tracking", "spxvn_code"),
    ]
    links = []
    for src, dst, role in edges:
        s, d = status.get(src), status.get(dst)
        live = s == "connected" and d == "connected"
        links.append(
            {
                "from": src,
                "to": dst,
                "role": role,
                "live": live,
                "from_status": s,
                "to_status": d,
            }
        )
    return links


def mermaid(channels: list[dict], links: list[dict]) -> str:
    lines = ["flowchart LR", '  subgraph OMS["OMS bus"]', "    BUS[oms_bus]", "  end"]
    for c in channels:
        if c["id"] == "oms_bus":
            continue
        flag = "OK" if c["status"] == "connected" else c["status"]
        lines.append(f'  {c["id"]}["{c["backend"]}\\n{flag}"]')
    for link in links:
        if link["from"] == "oms_bus":
            arrow = "-->" if link["live"] else "-.->"
            lines.append(f'  BUS {arrow}|{link["role"]}| {link["to"]}')
        elif link["to"] != "oms_bus" and link["from"] != "oms_bus":
            arrow = "-->" if link["live"] else "-.->"
            lines.append(f'  {link["from"]} {arrow}|{link["role"]}| {link["to"]}')
    return "\n".join(lines)


def format_report(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔗 ĐẤU NỐI OMS TOÀN DIỆN")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    L("=== Channels ===")
    for c in report["channels"]:
        mark = "✅" if c["status"] == "connected" else ("⚠️" if c["status"] == "missing_cred" else "❌")
        L(f"{mark} {c['backend']}/{c['id']}: {c['status']} · {c.get('detail','')[:120]}")
    L("")
    L("=== Links live ===")
    live = [x for x in report["links"] if x["live"]]
    dead = [x for x in report["links"] if not x["live"]]
    L(f"· live={len(live)} · pending={len(dead)}")
    for x in live:
        L(f"  ✅ {x['from']} → {x['to']} ({x['role']})")
    for x in dead[:12]:
        L(f"  ⏳ {x['from']} → {x['to']} ({x['role']}) [{x['from_status']}→{x['to_status']}]")
    L("")
    s = report["orders_summary"]
    L("=== OMS ingest ===")
    L(f"· records={s['total']} phone={s['phone']}")
    L(f"· tracking={s['with_tracking']} seller={s['with_assigning_seller']} care={s['with_assigning_care']}")
    L(f"· Đắk Lắk={s['daklak_count']}")
    for d in s.get("daklak_orders") or []:
        L(f"  - {d.get('tracking_code')} · {d.get('carrier')} · {d.get('province')}/{d.get('district')} · {d.get('status')}")
    L("")
    L("=== Next ===")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def interconnect(env: dict[str, str] | None = None, *, ingest: bool = True) -> dict:
    env = env or load_env()
    channels = run_channel_probes(env)
    records = ingest_local_orders() if ingest else []
    summary = summarize_orders(records)
    links = build_links(channels)
    live_n = sum(1 for x in links if x["live"])
    connected = [c["backend"] for c in channels if c["status"] == "connected"]
    missing = [c["backend"] for c in channels if c["status"] == "missing_cred"]

    verdict = (
        f"OMS bus: {len(connected)}/{len(channels)} channel connected · "
        f"links live {live_n}/{len(links)} · ingest {summary['total']} records · "
        f"Đắk Lắk={summary['daklak_count']} · "
        f"thiếu cred: {', '.join(missing) or 'không'}."
    )

    report = {
        "ok": True,
        "query": "Đấu nối OMS toàn diện",
        "checked_at": utc_now(),
        "channels": channels,
        "links": links,
        "orders_summary": summary,
        "sample_orders": records[:30],
        "mermaid": mermaid(channels, links),
        "safety": {
            "no_dump_login": True,
            "secrets_only_remote": True,
            "secrets_path": "secrets/backend_pipes.env",
        },
        "next_actions": [
            "Điền secrets/backend_pipes.env từ backend_pipes.env.example (owned keys only)",
            "Pancake + GHN + ViettelPost keys → mở link create_waybill / shipping_status",
            "Tracking.aship đã public — nối mã VĐ từ GHN/VTP/SPX vào monitor",
            "Refetch Pancake đủ warehouse + assigning_* + province để enrich OMS",
            "Chạy kèm: backend_pipe_keepalive.py --once && realtime_order_sync.py --once",
        ],
        "verdict": verdict,
    }

    # state
    SECRETS.mkdir(parents=True, exist_ok=True)
    state = {
        "updated_at": utc_now(),
        "channels": {c["id"]: {"status": c["status"], "detail": c.get("detail"), "checked_at": utc_now()} for c in channels},
        "links_live": live_n,
        "ingest_total": summary["total"],
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (OUT / "oms_interconnect.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    text = format_report(report)
    (OUT / "oms_interconnect.txt").write_text(text, encoding="utf-8")
    (REPORTS / "oms_interconnect.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / "oms_interconnect.txt").write_text(text, encoding="utf-8")
    (REPORTS / "oms_interconnect.mermaid.md").write_text(
        "# Đấu nối OMS toàn diện\n\n```mermaid\n" + report["mermaid"] + "\n```\n",
        encoding="utf-8",
    )
    return report


def send_telegram(env: dict[str, str], text: str) -> None:
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return
    payload = json.dumps(
        {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def main() -> int:
    ap = argparse.ArgumentParser(description="Đấu nối OMS toàn diện")
    ap.add_argument("--once", action="store_true", help="Một vòng (mặc định)")
    ap.add_argument("--notify", action="store_true", help="Gửi Telegram")
    ap.add_argument("--no-ingest", action="store_true", help="Chỉ probe channel, không ingest đơn")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    env = load_env()
    report = interconnect(env, ingest=not args.no_ingest)
    text = format_report(report)
    if args.notify:
        send_telegram(env, text)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
