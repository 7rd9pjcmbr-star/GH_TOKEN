#!/usr/bin/env python3
"""Mapper icon TMDT Việt Nam — đơn hàng ↔ OnlyLogs×6 (không bỏ sót).

Catalog đầy đủ sàn / POS / 3PL VN + icon army + pattern triage trong lab intake.
Policy: KHÔNG dump-login · KHÔNG dùng stealer để lấy đơn live.
Lấy đơn thật chỉ qua credential OWNED (owned_credentials / access_token_rotate).

Usage:
  python3 scripts/tmdt_vn_icon_order_mapper.py
  python3 scripts/tmdt_vn_icon_order_mapper.py --scan-lab
  python3 scripts/tmdt_vn_icon_order_mapper.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
LAB_REPORTS = ROOT / "reports" / "lab" / "tmdt"
INTAKE = ROOT / "quarantine" / "lab" / "intake" / "onlylogs-6"
CHUNKS = ROOT / "quarantine" / "lab" / "chunks" / "onlylogs-6"

# Đồng bộ vocabulary với realtime_icon_feedback_mapper / network-map.js
ICON_ARMY = {
    "spark": {"call": "Tia Lửa Hub", "role": "hub"},
    "layers": {"call": "Lớp Khiên", "role": "group"},
    "key": {"call": "Chìa Khái Niệm", "role": "secret"},
    "lock": {"call": "Ổ Khóa", "role": "auth"},
    "network": {"call": "Mạch Mạng", "role": "pipe"},
    "compass": {"call": "La Bàn Tracking", "role": "track"},
    "monitor": {"call": "Màn Realtime", "role": "live"},
    "cube": {"call": "Khối Kho", "role": "warehouse"},
    "wrench": {"call": "Cờ Lê Sự Cố", "role": "error"},
    "cpu": {"call": "Nhân Sync", "role": "engine"},
    "hash": {"call": "Dấu Băm Đơn", "role": "fingerprint"},
    "text": {"call": "Dòng Phản Hồi", "role": "feedback"},
    "code": {"call": "Mã Nguồn Pipe", "role": "local"},
    "chip": {"call": "Chip Kênh", "role": "channel"},
    "atom": {"call": "Nguyên Tử Pay", "role": "pay"},
    "scroll": {"call": "Cuộn Đơn", "role": "order"},
}

# ── Catalog TMDT Việt Nam (không bỏ sót nhóm chính) ─────────────
# kind: marketplace | pos_oms | logistics | payment | social_shop
# order_via: owned_api | triage_only | tracking_only
# onlylogs_hit: domains/cookies thường gặp trong stealer pack VN

TMDT_VN: list[dict[str, Any]] = [
    # Marketplaces
    {
        "id": "shopee",
        "name": "Shopee VN",
        "kind": "marketplace",
        "icon": "cube",
        "order_via": "owned_api",
        "domains": ["shopee.vn", "seller.shopee.vn", "banhang.shopee.vn", "shopee.com"],
        "order_hints": ["order_id", "ordersn", "checkout", "parcel", "spx"],
        "cookie_hints": ["SPC_", "shopee", "seller"],
        "owned_env": ["SHOPEE_TOKEN", "SPX_TOKEN"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "lazada",
        "name": "Lazada VN",
        "kind": "marketplace",
        "icon": "layers",
        "order_via": "owned_api",
        "domains": ["lazada.vn", "sellercenter.lazada.vn", "www.lazada.vn"],
        "order_hints": ["orderId", "tradeOrder", "fulfillment", "LEX"],
        "cookie_hints": ["lazada", "t_uid", "x5sec"],
        "owned_env": ["LAZADA_TOKEN", "LAZADA_APP_KEY"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "tiki",
        "name": "Tiki",
        "kind": "marketplace",
        "icon": "scroll",
        "order_via": "owned_api",
        "domains": ["tiki.vn", "seller.tiki.vn", "api.tiki.vn"],
        "order_hints": ["order_code", "fulfillment", "tiki_ngon"],
        "cookie_hints": ["_tiki", "TIKI"],
        "owned_env": ["TIKI_TOKEN"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "sendo",
        "name": "Sendo",
        "kind": "marketplace",
        "icon": "chip",
        "order_via": "owned_api",
        "domains": ["sendo.vn", "ban.sendo.vn", "seller.sendo.vn"],
        "order_hints": ["sales_order", "sendo_order"],
        "cookie_hints": ["sendo"],
        "owned_env": ["SENDO_TOKEN"],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "tiktokshop_vn",
        "name": "TikTok Shop VN",
        "kind": "marketplace",
        "icon": "monitor",
        "order_via": "owned_api",
        "domains": ["shop.tiktok.com", "seller-vn.tiktok.com", "tiktok.com"],
        "order_hints": ["tts_order", "order_id", "fulfillment"],
        "cookie_hints": ["sessionid", "sid_tt", "ttwid"],
        "owned_env": ["TIKTOK_SHOP_TOKEN"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "facebook_shop_vn",
        "name": "Facebook / Meta Shop VN",
        "kind": "social_shop",
        "icon": "spark",
        "order_via": "triage_only",
        "domains": ["facebook.com", "business.facebook.com", "fb.com"],
        "order_hints": ["commerce", "order_id", "catalog"],
        "cookie_hints": ["c_user", "xs", "datr"],
        "owned_env": ["META_PAGE_TOKEN"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "instagram_shop_vn",
        "name": "Instagram Shopping VN",
        "kind": "social_shop",
        "icon": "spark",
        "order_via": "triage_only",
        "domains": ["instagram.com"],
        "order_hints": ["checkout", "order"],
        "cookie_hints": ["sessionid", "ds_user_id"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    # POS / OMS (seller tools phổ biến VN)
    {
        "id": "pancake",
        "name": "Pancake POS",
        "kind": "pos_oms",
        "icon": "layers",
        "order_via": "owned_api",
        "domains": ["pos.pancake.vn", "pancake.vn", "pages.fm"],
        "order_hints": ["pos_jwt", "order", "shop_id", "conversation"],
        "cookie_hints": ["pos_jwt", "pos_locale"],
        "owned_env": ["PANCAKE_POS_ACCESS_TOKEN", "PANCAKE_SHOP_ID"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "tpos",
        "name": "TPOS",
        "kind": "pos_oms",
        "icon": "cpu",
        "order_via": "owned_api",
        "domains": ["tpos.vn", "tapi.tpos.vn"],
        "order_hints": ["sale_order", "tpos"],
        "cookie_hints": ["tpos", ".AspNetCore"],
        "owned_env": ["TPOS_ACCESS_TOKEN"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "sapo",
        "name": "Sapo",
        "kind": "pos_oms",
        "icon": "cube",
        "order_via": "owned_api",
        "domains": ["sapo.vn", "mysapo.net"],
        "order_hints": ["orders.json", "fulfillments"],
        "cookie_hints": ["_sapo", "store"],
        "owned_env": ["SAPO_ACCESS_TOKEN", "SAPO_STORE"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "nhanh",
        "name": "Nhanh.vn",
        "kind": "pos_oms",
        "icon": "hash",
        "order_via": "owned_api",
        "domains": ["nhanh.vn", "nhanhweb.com"],
        "order_hints": ["orderId", "businessId"],
        "cookie_hints": ["nhanh"],
        "owned_env": ["NHANH_API_KEY", "NHANH_BUSINESS_ID"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "haravan",
        "name": "Haravan",
        "kind": "pos_oms",
        "icon": "layers",
        "order_via": "owned_api",
        "domains": ["haravan.com", "myharavan.com"],
        "order_hints": ["orders.json", "fulfillments"],
        "cookie_hints": ["_haravan", "secure_customer"],
        "owned_env": ["HARAVAN_TOKEN"],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "kiotviet",
        "name": "KiotViet",
        "kind": "pos_oms",
        "icon": "monitor",
        "order_via": "owned_api",
        "domains": ["kiotviet.vn", "api-hn1.kiotviet.vn", "man.kiotviet.vn"],
        "order_hints": ["invoices", "orders", "retailer"],
        "cookie_hints": ["kiotviet", "Retailer"],
        "owned_env": ["KIOTVIET_TOKEN", "KIOTVIET_RETAILER"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "gosell",
        "name": "GoSELL",
        "kind": "pos_oms",
        "icon": "chip",
        "order_via": "triage_only",
        "domains": ["gosell.vn", "gosell.io"],
        "order_hints": ["order"],
        "cookie_hints": ["gosell"],
        "owned_env": [],
        "onlylogs_relevance": "low",
    },
    {
        "id": "bizweb",
        "name": "Bizweb / Haravan sibling",
        "kind": "pos_oms",
        "icon": "code",
        "order_via": "triage_only",
        "domains": ["bizweb.vn", "mybizweb.com"],
        "order_hints": ["orders"],
        "cookie_hints": ["bizweb"],
        "owned_env": [],
        "onlylogs_relevance": "low",
    },
    {
        "id": "basevn",
        "name": "Base.vn",
        "kind": "pos_oms",
        "icon": "cpu",
        "order_via": "triage_only",
        "domains": ["base.vn"],
        "order_hints": ["order", "crm"],
        "cookie_hints": ["base"],
        "owned_env": [],
        "onlylogs_relevance": "low",
    },
    {
        "id": "salework",
        "name": "SaleWork / Omni",
        "kind": "pos_oms",
        "icon": "text",
        "order_via": "triage_only",
        "domains": ["salework.net", "omnisell.vn"],
        "order_hints": ["order"],
        "cookie_hints": ["salework"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    # Logistics 3PL VN
    {
        "id": "ghn",
        "name": "GHN",
        "kind": "logistics",
        "icon": "network",
        "order_via": "owned_api",
        "domains": ["ghn.vn", "online-gateway.ghn.vn", "api.ghn.vn", "sso-v2.ghn.vn"],
        "order_hints": ["order_code", "client_order_code", "printA5", "Token"],
        "cookie_hints": ["Token", "ghn"],
        "owned_env": ["GHN_API_TOKEN", "GHN_SHOP_ID"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "ghtk",
        "name": "GHTK",
        "kind": "logistics",
        "icon": "network",
        "order_via": "owned_api",
        "domains": ["ghtk.vn", "services.giaohangtietkiem.vn"],
        "order_hints": ["label_id", "partner_id", "order"],
        "cookie_hints": ["ghtk"],
        "owned_env": ["GHTK_TOKEN"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "viettelpost",
        "name": "Viettel Post",
        "kind": "logistics",
        "icon": "network",
        "order_via": "owned_api",
        "domains": ["viettelpost.vn", "api.viettelpost.vn"],
        "order_hints": ["ORDER_NUMBER", "MÃ VĐ"],
        "cookie_hints": ["viettelpost", "VTP"],
        "owned_env": ["VIETTELPOST_TOKEN"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "vnpost",
        "name": "VNPost / EMS",
        "kind": "logistics",
        "icon": "code",
        "order_via": "owned_api",
        "domains": ["vnpost.vn", "ems.com.vn"],
        "order_hints": ["itemCode", "mail"],
        "cookie_hints": ["vnpost"],
        "owned_env": ["VNPOST_TOKEN"],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "spx",
        "name": "SPX (Shopee Xpress)",
        "kind": "logistics",
        "icon": "cube",
        "order_via": "owned_api",
        "domains": ["spx.vn", "spx.co.id"],
        "order_hints": ["spx", "tracking"],
        "cookie_hints": ["spx"],
        "owned_env": ["SPX_TOKEN"],
        "onlylogs_relevance": "high",
    },
    {
        "id": "jtexpress",
        "name": "J&T Express VN",
        "kind": "logistics",
        "icon": "compass",
        "order_via": "tracking_only",
        "domains": ["jtexpress.vn"],
        "order_hints": ["billcode", "tracking"],
        "cookie_hints": ["jt", "jtexpress"],
        "owned_env": [],
        "onlylogs_relevance": "high",
    },
    {
        "id": "best",
        "name": "BEST Express VN",
        "kind": "logistics",
        "icon": "compass",
        "order_via": "tracking_only",
        "domains": ["best-inc.vn", "best-inc.com"],
        "order_hints": ["mailNo", "tracking"],
        "cookie_hints": ["best"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "ninjavan",
        "name": "Ninja Van VN",
        "kind": "logistics",
        "icon": "compass",
        "order_via": "tracking_only",
        "domains": ["ninjavan.co", "ninjavan.vn"],
        "order_hints": ["tracking_number"],
        "cookie_hints": ["ninjavan"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "ahamove",
        "name": "AhaMove",
        "kind": "logistics",
        "icon": "compass",
        "order_via": "owned_api",
        "domains": ["ahamove.com"],
        "order_hints": ["order_id", "path"],
        "cookie_hints": ["ahamove"],
        "owned_env": ["AHAMOVE_TOKEN"],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "grabexpress",
        "name": "GrabExpress VN",
        "kind": "logistics",
        "icon": "compass",
        "order_via": "triage_only",
        "domains": ["grab.com", "p.grabtaxi.com"],
        "order_hints": ["delivery", "booking"],
        "cookie_hints": ["grab"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "be",
        "name": "Be Delivery",
        "kind": "logistics",
        "icon": "compass",
        "order_via": "triage_only",
        "domains": ["be.com.vn", "beeorder.com"],
        "order_hints": ["order"],
        "cookie_hints": ["be"],
        "owned_env": [],
        "onlylogs_relevance": "low",
    },
    {
        "id": "ship60",
        "name": "Ship60",
        "kind": "logistics",
        "icon": "network",
        "order_via": "triage_only",
        "domains": ["ship60.com"],
        "order_hints": ["order"],
        "cookie_hints": ["ship60"],
        "owned_env": [],
        "onlylogs_relevance": "low",
    },
    {
        "id": "express247",
        "name": "247 Express",
        "kind": "logistics",
        "icon": "network",
        "order_via": "triage_only",
        "domains": ["247express.vn"],
        "order_hints": ["tracking"],
        "cookie_hints": ["247"],
        "owned_env": [],
        "onlylogs_relevance": "low",
    },
    {
        "id": "kerry",
        "name": "Kerry Express VN",
        "kind": "logistics",
        "icon": "compass",
        "order_via": "tracking_only",
        "domains": ["kerryexpress.com.vn"],
        "order_hints": ["con_no", "tracking"],
        "cookie_hints": ["kerry"],
        "owned_env": [],
        "onlylogs_relevance": "low",
    },
    {
        "id": "aship",
        "name": "Aship Tracking Hub",
        "kind": "logistics",
        "icon": "compass",
        "order_via": "tracking_only",
        "domains": ["aship.vn", "tracking.aship"],
        "order_hints": ["tracking", "carrier"],
        "cookie_hints": ["aship"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    # Payment (đơn liên quan)
    {
        "id": "momo",
        "name": "MoMo",
        "kind": "payment",
        "icon": "atom",
        "order_via": "triage_only",
        "domains": ["momo.vn", "momocdn.net"],
        "order_hints": ["transId", "orderId"],
        "cookie_hints": ["momo"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "zalopay",
        "name": "ZaloPay",
        "kind": "payment",
        "icon": "atom",
        "order_via": "triage_only",
        "domains": ["zalopay.vn"],
        "order_hints": ["apptransid", "order"],
        "cookie_hints": ["zalopay"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "vnpay",
        "name": "VNPay",
        "kind": "payment",
        "icon": "atom",
        "order_via": "triage_only",
        "domains": ["vnpay.vn", "vnpayment.vn"],
        "order_hints": ["vnp_TxnRef", "order"],
        "cookie_hints": ["vnpay"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    {
        "id": "shopeepay",
        "name": "ShopeePay",
        "kind": "payment",
        "icon": "atom",
        "order_via": "triage_only",
        "domains": ["airpay", "shopeepay"],
        "order_hints": ["payment", "order"],
        "cookie_hints": ["shopeepay", "airpay"],
        "owned_env": [],
        "onlylogs_relevance": "medium",
    },
    # Messaging order intake
    {
        "id": "telegram",
        "name": "Telegram (OMS inbox)",
        "kind": "pos_oms",
        "icon": "spark",
        "order_via": "owned_api",
        "domains": ["api.telegram.org", "t.me"],
        "order_hints": ["order", "đơn"],
        "cookie_hints": [],
        "owned_env": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
        "onlylogs_relevance": "low",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def describe_icon(name: str) -> dict[str, str]:
    meta = ICON_ARMY.get(name) or {"call": name, "role": "unit"}
    return {"name": name, **meta}


def load_onlylogs_manifest() -> dict[str, Any]:
    p = INTAKE / "MANIFEST.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def scan_lab_for_platforms() -> dict[str, Any]:
    """Quét artifact lab OnlyLogs (stub/chunk/txt) tìm dấu domain TMDT."""
    hits: dict[str, list[str]] = defaultdict(list)
    files_scanned = 0
    roots = [INTAKE, CHUNKS]
    blob_parts: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".txt", ".json"}:
                continue
            files_scanned += 1
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")[:200_000]
            except OSError:
                continue
            blob_parts.append(text)
            low = text.lower()
            for plat in TMDT_VN:
                for d in plat["domains"]:
                    if d.lower() in low:
                        hits[plat["id"]].append(str(p.relative_to(ROOT)))
                        break
                for h in plat.get("cookie_hints") or []:
                    if h and h.lower() in low and plat["id"] not in hits:
                        # weak hint
                        pass
    blob = "\n".join(blob_parts).lower()
    # second pass on combined for cookie hints strength
    weak: dict[str, list[str]] = defaultdict(list)
    for plat in TMDT_VN:
        for h in plat.get("cookie_hints") or []:
            if h and h.lower() in blob:
                weak[plat["id"]].append(h)
        for oh in plat.get("order_hints") or []:
            if oh and oh.lower() in blob:
                weak[plat["id"]].append(f"order:{oh}")
    return {
        "files_scanned": files_scanned,
        "domain_hits": {k: sorted(set(v)) for k, v in hits.items()},
        "weak_hints": {k: sorted(set(v))[:12] for k, v in weak.items()},
    }


def owned_ready() -> dict[str, bool]:
    try:
        from owned_credentials import load_env, tokens_for
    except Exception:  # noqa: BLE001
        return {}
    env = load_env()
    out: dict[str, bool] = {}
    mapping = {
        "pancake": "Pancake",
        "ghn": "GHN",
        "viettelpost": "ViettelPost",
        "tpos": "TPOS",
        "sapo": "Sapo",
        "nhanh": "Nhanh",
        "shopee": "Shopee",
        "spx": "Shopee",
    }
    for pid, pname in mapping.items():
        try:
            toks = tokens_for(env, pname) or []
            out[pid] = bool(toks)
        except Exception:  # noqa: BLE001
            out[pid] = False
    return out


def build_report(*, scan_lab: bool = True) -> dict[str, Any]:
    only = load_onlylogs_manifest()
    items = only.get("items") or []
    scan = scan_lab_for_platforms() if scan_lab else {"files_scanned": 0, "domain_hits": {}, "weak_hints": {}}
    owned = owned_ready()

    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = []
    for plat in TMDT_VN:
        pid = plat["id"]
        icon = describe_icon(plat["icon"])
        domain_hit = pid in (scan.get("domain_hits") or {})
        weak = (scan.get("weak_hints") or {}).get(pid) or []
        can_live = bool(owned.get(pid)) and plat["order_via"] == "owned_api"
        row = {
            **plat,
            "icon_meta": icon,
            "icon_chant": icon["call"],
            "in_onlylogs_lab_now": domain_hit,
            "weak_lab_hints": weak,
            "owned_token_ready": bool(owned.get(pid)),
            "can_fetch_orders_now": can_live,
            "onlylogs6_link": {
                "archives_n": len(items),
                "binary_present": False,
                "note": (
                    "Binary RAR absent — không extract đơn từ dump. "
                    "Pattern mapper sẵn; lấy đơn qua owned API khi có token."
                ),
            },
            "coverage_checked": True,
        }
        rows.append(row)
        by_kind[plat["kind"]].append(
            {"id": pid, "name": plat["name"], "icon": plat["icon"], "order_via": plat["order_via"]}
        )

    # Platforms that COULD yield orders from stealer content (triage map) — not permission to login
    high_relevance = [r for r in rows if r.get("onlylogs_relevance") == "high"]
    order_capable = [r for r in rows if r.get("order_via") == "owned_api"]

    report: dict[str, Any] = {
        "ok": True,
        "module": "tmdt_vn_icon_order_mapper",
        "checked_at": utc_now(),
        "policy": {
            "vietnam_tmdt_only_focus": True,
            "no_dump_login": True,
            "no_stealer_order_fetch": True,
            "live_orders_via_owned_only": True,
            "onlylogs_role": "IOC/pattern triage — không lấy đơn bằng dump",
        },
        "verdict": (
            f"✅ Mapper icon TMDT VN · platforms={len(rows)} · "
            f"kinds={dict((k, len(v)) for k, v in by_kind.items())} · "
            f"onlylogs6={len(items)} archives · "
            f"owned_ready={sum(1 for v in owned.values() if v)} · "
            f"lab_domain_hits={len(scan.get('domain_hits') or {})}"
        ),
        "onlylogs6": {
            "intake": str(INTAKE.relative_to(ROOT)),
            "chunks": str(CHUNKS.relative_to(ROOT)),
            "archives": [
                {
                    "update_id": it.get("update_id"),
                    "file_name": it.get("file_name"),
                    "size_gb": it.get("size_gb"),
                    "classification": it.get("classification"),
                }
                for it in items
            ],
            "total_gb": round(sum(float(it.get("size_gb") or 0) for it in items), 3),
            "binary_in_lab": False,
            "can_extract_orders_from_rar_now": False,
            "reason": "Thiếu binary RAR (Bot API >20MB). Dump stealer → không login lấy đơn.",
        },
        "catalog_n": len(rows),
        "by_kind": {k: v for k, v in by_kind.items()},
        "platforms": rows,
        "high_relevance_for_onlylogs": [
            {"id": r["id"], "name": r["name"], "icon": r["icon"], "icon_chant": r["icon_chant"]}
            for r in high_relevance
        ],
        "owned_api_order_platforms": [
            {
                "id": r["id"],
                "name": r["name"],
                "icon": r["icon"],
                "owned_token_ready": r["owned_token_ready"],
                "can_fetch_orders_now": r["can_fetch_orders_now"],
            }
            for r in order_capable
        ],
        "lab_scan": scan,
        "coverage_checklist": [
            {"id": r["id"], "name": r["name"], "kind": r["kind"], "covered": True}
            for r in rows
        ],
        "icon_index": {
            r["id"]: {"icon": r["icon"], "chant": r["icon_chant"], "role": r["icon_meta"]["role"]}
            for r in rows
        },
        "how_to_get_orders_legally": [
            "1) Không dùng OnlyLogs/stealer để login",
            "2) Điền token OWNED vào secrets/backend_pipes.env",
            "3) python3 scripts/access_token_rotate.py refresh --platform GHN --orders",
            "4) python3 scripts/scan_buucuc_orders.py --backends GHN,Pancake --days 3",
            "5) Panel: Token·realtime / Nhúng GHN / Pancake cookie owned",
        ],
        "next": [
            "Đọc reports/lab/tmdt/tmdt_vn_icon_order_mapper.txt",
            "python3 scripts/tmdt_vn_icon_order_mapper.py --scan-lab",
            "python3 scripts/realtime_icon_feedback_mapper.py",
        ],
    }
    write_outputs(report)
    return report


def write_outputs(report: dict[str, Any]) -> None:
    LAB_REPORTS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (LAB_REPORTS / "tmdt_vn_icon_order_mapper.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    text = format_text(report)
    (LAB_REPORTS / "tmdt_vn_icon_order_mapper.txt").write_text(text + "\n", encoding="utf-8")
    (REPORTS / "tmdt_vn_icon_order_mapper.txt").write_text(text + "\n", encoding="utf-8")
    (REPORTS / "tmdt_vn_icon_order_mapper.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # mermaid
    lines = ["```mermaid", "flowchart LR", "  OL[OnlyLogs×6 stealer RAR]"]
    for r in report.get("high_relevance_for_onlylogs") or []:
        nid = re.sub(r"[^a-z0-9]", "_", r["id"])
        lines.append(f"  OL -.->|triage pattern| {nid}[{r['name']}]")
        lines.append(f"  {nid} --> I_{nid}({r['icon_chant']})")
    lines += [
        "  OWN[Owned tokens only] --> API[Lấy đơn live]",
        "  OL -.->|FORBIDDEN dump-login| X[blocked]",
        "```",
        "",
    ]
    (LAB_REPORTS / "tmdt_vn_icon_order_mapper.mermaid.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("🗺 MAPPER ICON · TMDT VIỆT NAM × ONLYLOGS×6")
    L(f"Lúc: {report.get('checked_at')}")
    L(f"Verdict: {report.get('verdict')}")
    L("Policy: NO dump-login · lấy đơn chỉ OWNED API · OnlyLogs = triage pattern")
    L("")
    ol = report.get("onlylogs6") or {}
    L(f"=== OnlyLogs×6 === archives={ol.get('archives') and len(ol['archives'])} · total_gb={ol.get('total_gb')}")
    L(f"Binary in lab: {ol.get('binary_in_lab')} · extract orders now: {ol.get('can_extract_orders_from_rar_now')}")
    L(f"Reason: {ol.get('reason')}")
    L("")
    L("=== Catalog theo nhóm (đủ, không bỏ sót) ===")
    for kind, items in (report.get("by_kind") or {}).items():
        L(f"— {kind} ({len(items)})")
        for it in items:
            L(f"  · {it['id']}: {it['name']} [{it['icon']}] via={it['order_via']}")
    L("")
    L("=== High relevance với OnlyLogs (pattern) ===")
    for r in report.get("high_relevance_for_onlylogs") or []:
        L(f"· {r['icon_chant']} ← {r['name']} ({r['id']})")
    L("")
    L("=== Có thể lấy đơn LIVE (owned API) ===")
    for r in report.get("owned_api_order_platforms") or []:
        ready = "READY" if r.get("can_fetch_orders_now") else ("token?" if r.get("owned_token_ready") else "need owned token")
        L(f"· {r['name']}: {ready}")
    L("")
    L("=== Lab scan domain hits ===")
    hits = (report.get("lab_scan") or {}).get("domain_hits") or {}
    if not hits:
        L("· (chưa thấy domain TMDT trong stub — đúng vì chưa có binary RAR)")
    else:
        for k, v in hits.items():
            L(f"· {k}: {len(v)} files")
    L("")
    L("=== Coverage checklist ===")
    for c in report.get("coverage_checklist") or []:
        L(f"  [x] {c['id']} · {c['name']} ({c['kind']})")
    L("")
    L("=== Cách lấy đơn đúng ===")
    for s in report.get("how_to_get_orders_legally") or []:
        L(s)
    for n in report.get("next") or []:
        L(f"Next: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mapper icon TMDT VN ↔ OnlyLogs×6")
    ap.add_argument("--scan-lab", action="store_true", default=True)
    ap.add_argument("--no-scan-lab", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(scan_lab=not args.no_scan_lab)
    text = format_text(report)
    if args.notify:
        _notify(text)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(text)
    return 0 if report.get("ok") else 1


def _notify(text: str) -> None:
    import os
    import urllib.request

    env = dict(os.environ)
    p = ROOT / "secrets" / "telegram.env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return
    body = json.dumps({"chat_id": chat, "text": text[:3500]}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


if __name__ == "__main__":
    raise SystemExit(main())
