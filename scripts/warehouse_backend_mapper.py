#!/usr/bin/env python3
"""Mapper backend từng kho — gắn OMS/pipe/carrier theo warehouse.

Chỉ đọc quarantine + secrets probe (qua oms_interconnect). Không dump login.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

Q = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

BACKEND_CATALOG = [
    {"id": "Pancake-POS", "secret": "PANCAKE_POS_API_KEY", "oms": "Pancake"},
    {"id": "Pancake-file", "secret": None, "oms": "direct_api"},
    {"id": "direct_api", "secret": None, "oms": "direct_api"},
    {"id": "Telegram-upload", "secret": "TELEGRAM_BOT_TOKEN", "oms": "Telegram"},
    {"id": "GHN", "secret": "GHN_API_TOKEN", "oms": "GHN"},
    {"id": "ViettelPost", "secret": "VIETTELPOST_TOKEN", "oms": "ViettelPost"},
    {"id": "SPX-local", "secret": None, "oms": "SPX-local"},
    {"id": "Tracking-aship", "secret": None, "oms": "Tracking"},
    {"id": "TPOS", "secret": "TPOS_ACCESS_TOKEN", "oms": "TPOS"},
    {"id": "OMS-ingest-json", "secret": None, "oms": "OMS-pipe-bus"},
    {"id": "OMS-dang-giao-csv", "secret": None, "oms": "OMS-pipe-bus"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def phone_class(ph: str | None) -> str:
    ph = (ph or "").strip()
    if not ph:
        return "MISSING"
    if "*" in ph or set(ph) <= {"*"}:
        return "MASKED"
    return "OK" if len(re.sub(r"\D", "", ph)) >= 9 else "INVALID"


def read_xlsx(path: Path) -> list[dict]:
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
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))

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
    return [
        {names.get(c, f"col{c}"): cells[r].get(c, "") for c in names}
        for r in range(2, max_row + 1)
        if r in cells
    ]


def _blank_wh(key: str) -> dict:
    return {
        "kho": key,
        "warehouse_ids": Counter(),
        "addresses": Counter(),
        "shops": Counter(),
        "accounts": Counter(),
        "pages": Counter(),
        "sources": Counter(),
        "platforms": Counter(),
        "statuses": Counter(),
        "carriers": Counter(),
        "backends_hit": Counter(),
        "staff_creator": Counter(),
        "staff_seller": Counter(),
        "staff_care": Counter(),
        "phone": Counter(),
        "provinces": Counter(),
        "orders": 0,
        "files": Counter(),
        "endpoints_logical": set(),
        "sample_order_keys": [],
    }


def collect_warehouses() -> tuple[dict, int]:
    warehouses: dict[str, dict] = {}
    unmapped_dang = 0

    def ensure(key: str) -> dict:
        return warehouses.setdefault(key, _blank_wh(key))

    for jf in sorted(Q.glob("orders_detailed_*.json")):
        orders = json.loads(jf.read_text(encoding="utf-8"))
        if not isinstance(orders, list):
            continue
        for o in orders:
            p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
            wi = p.get("warehouse_info") if isinstance(p.get("warehouse_info"), dict) else {}
            wh = wi.get("custom_id") or wi.get("name") or p.get("warehouse_id") or "(none)"
            w = ensure(str(wh))
            w["orders"] += 1
            w["files"][jf.name] += 1
            if p.get("warehouse_id"):
                w["warehouse_ids"][str(p.get("warehouse_id"))] += 1
            if wi.get("address"):
                w["addresses"][str(wi.get("address"))[:120]] += 1
            shop = str(o.get("shop_id") or p.get("shop_id") or "")
            if shop:
                w["shops"][shop] += 1
            if p.get("account") not in (None, ""):
                w["accounts"][str(p.get("account"))] += 1
            if p.get("page_id"):
                w["pages"][str(p.get("page_id"))] += 1
            w["sources"][o.get("source") or "(empty)"] += 1
            w["platforms"][o.get("platform") or "(empty)"] += 1
            w["statuses"][o.get("status_normalized") or o.get("status_raw") or ""] += 1
            w["phone"][phone_class(o.get("customer_phone") or p.get("bill_phone_number"))] += 1
            for field, bucket in (
                ("creator", "staff_creator"),
                ("assigning_seller", "staff_seller"),
                ("assigning_care", "staff_care"),
            ):
                v = p.get(field)
                if isinstance(v, dict):
                    label = v.get("name") or v.get("id")
                else:
                    label = v
                if field == "creator" and not label and p.get("account") not in (None, ""):
                    label = p.get("account")
                w[bucket][str(label)[:80] if label not in (None, "") else "(null)"] += 1
            addr = p.get("shipping_address") if isinstance(p.get("shipping_address"), dict) else {}
            if addr.get("province_name") or addr.get("province"):
                w["provinces"][str(addr.get("province_name") or addr.get("province"))] += 1
            ships = p.get("shipments") or []
            if ships:
                for s in ships:
                    if isinstance(s, dict):
                        c = s.get("partner_name") or s.get("partner_id") or "UNKNOWN"
                        w["carriers"][str(c)] += 1
                        w["backends_hit"][f"carrier:{c}"] += 1
            else:
                w["carriers"]["(no_shipment)"] += 1
            w["backends_hit"]["Pancake-POS"] += 1
            w["backends_hit"]["OMS-ingest-json"] += 1
            w["endpoints_logical"].add("https://pos.pancake.vn/api/v1/shops/{shop_id}/orders")
            w["endpoints_logical"].add("https://pos.pages.fm/api/v1/shops/{shop_id}/orders")
            if len(w["sample_order_keys"]) < 5:
                w["sample_order_keys"].append(o.get("order_key"))

    for r in read_xlsx(Q / "thanhcoong.xlsx"):
        sender = (r.get("Sender Name") or "").strip()
        if not sender or sender in ("Sender Name", "Tên người gửi"):
            continue
        w = ensure(sender)
        w["orders"] += 1
        w["files"]["thanhcoong.xlsx"] += 1
        w["sources"]["thanhcoong_xlsx"] += 1
        w["platforms"]["SPX"] += 1
        tpl = (r.get("3PL Name") or r.get("Tên 3PL") or "").strip()
        if tpl and tpl not in ("3PL Name", "Tên 3PL"):
            w["carriers"][tpl] += 1
            w["backends_hit"][f"carrier:{tpl}"] += 1
            w["backends_hit"]["SPX-local"] += 1
        creator = (r.get("Order Creator") or "").strip()
        if creator and creator not in ("Order Creator", "Người tạo đơn"):
            w["staff_creator"][creator] += 1
        else:
            w["staff_creator"]["(null)"] += 1
        w["staff_seller"]["(null)"] += 1
        w["staff_care"]["(null)"] += 1
        acc = (r.get("Account ID") or "").strip()
        if acc and acc != "ID tài khoản":
            w["accounts"][acc] += 1
        prov = (r.get("Receiver Province") or r.get("Tỉnh, thành") or "").strip()
        if prov and prov not in ("Receiver Province", "Tỉnh, thành"):
            w["provinces"][prov] += 1
        w["statuses"][(r.get("Tracking Status") or r.get("Trạng thái hiện tại") or "")] += 1
        w["phone"][phone_class(r.get("Receiver Phone Number"))] += 1
        w["endpoints_logical"].add("SPX/thanhcoong.xlsx")
        w["endpoints_logical"].add(
            "https://tracking.aship.app/order?provider_code={ref}&provider=spx"
        )
        w["backends_hit"]["Tracking-aship"] += 1

    shop_to_kho: dict[str, str] = {}
    for k, w in warehouses.items():
        for shop, _ in w["shops"].most_common():
            shop_to_kho.setdefault(shop, k)

    cf = Q / "orders_detailed_Dang_giao_20260512_120712.csv"
    if cf.exists():
        with cf.open(newline="", encoding="utf-8", errors="replace") as fh:
            for r in csv.DictReader(fh):
                shop = str(r.get("shop_id") or "")
                kho = shop_to_kho.get(shop)
                if not kho:
                    kho = f"(unmapped_shop:{shop or 'empty'})"
                    unmapped_dang += 1
                w = ensure(kho)
                w["orders"] += 1
                w["files"][cf.name] += 1
                w["sources"][r.get("source") or "(empty)"] += 1
                w["platforms"][r.get("platform") or "(empty)"] += 1
                w["statuses"][r.get("status_normalized") or "Dang giao"] += 1
                w["phone"][phone_class(r.get("customer_phone"))] += 1
                if shop:
                    w["shops"][shop] += 1
                src = r.get("source") or ""
                if "pancake" in src:
                    w["backends_hit"]["Pancake-file"] += 1
                elif "direct_api" in src:
                    w["backends_hit"]["direct_api"] += 1
                elif "telegram" in src:
                    w["backends_hit"]["Telegram-upload"] += 1
                elif src == "sample":
                    w["backends_hit"]["sample"] += 1
                else:
                    w["backends_hit"][f"source:{src}"] += 1
                w["backends_hit"]["OMS-dang-giao-csv"] += 1
                w["carriers"]["(unknown_csv)"] += 1
                w["staff_creator"]["(null)"] += 1
                w["staff_seller"]["(null)"] += 1
                w["staff_care"]["(null)"] += 1

    return warehouses, unmapped_dang


def map_backends_for_warehouse(w: dict, oms_status: dict) -> list[dict]:
    hits = w["backends_hit"]
    mapped: list[dict] = []
    seen: set[str] = set()
    for hid, cnt in hits.most_common():
        if hid.startswith("carrier:"):
            carrier = hid.split(":", 1)[1]
            bid = f"carrier:{carrier}"
            cu = carrier.upper()
            if cu == "SPX":
                oms_name = "SPX-local"
            elif "GHN" in cu or "GIAOHANG" in cu:
                oms_name = "GHN"
            elif "VIETTEL" in cu or "VTP" in cu:
                oms_name = "ViettelPost"
            else:
                oms_name = None
            st = (oms_status.get(oms_name) or {}).get("status") if oms_name else "topology_only"
            hints = []
            if cu == "SPX":
                hints = ["https://tracking.aship.app/order?provider_code={ref}&provider=spx"]
            elif oms_name == "GHN":
                hints = ["GHN shiip API"]
            elif oms_name == "ViettelPost":
                hints = ["partner.viettelpost.vn"]
            mapped.append(
                {
                    "backend": bid,
                    "kind": "carrier",
                    "orders_touch": cnt,
                    "oms_channel": oms_name,
                    "pipe_status": st or "unknown",
                    "endpoint_hints": hints,
                }
            )
            seen.add(bid)
            continue
        cat = next((c for c in BACKEND_CATALOG if c["id"] == hid), None)
        oms_name = cat["oms"] if cat else None
        st = (oms_status.get(oms_name) or {}).get("status") if oms_name else "local"
        mapped.append(
            {
                "backend": hid,
                "kind": "channel",
                "orders_touch": cnt,
                "oms_channel": oms_name,
                "pipe_status": st or "unknown",
                "secret_needed": cat.get("secret") if cat else None,
                "endpoint_hints": sorted(w["endpoints_logical"])[:6],
            }
        )
        seen.add(hid)

    if any(w["shops"]) and "GHN" not in seen and not any(x.startswith("carrier:GHN") for x in seen):
        mapped.append(
            {
                "backend": "GHN",
                "kind": "suggested",
                "orders_touch": 0,
                "oms_channel": "GHN",
                "pipe_status": (oms_status.get("GHN") or {}).get("status") or "missing_cred",
                "secret_needed": "GHN_API_TOKEN",
                "note": "Gợi ý nối GHN từ kho Pancake khi có shipment",
            }
        )
    if any(w["shops"]) and "ViettelPost" not in seen:
        mapped.append(
            {
                "backend": "ViettelPost",
                "kind": "suggested",
                "orders_touch": 0,
                "oms_channel": "ViettelPost",
                "pipe_status": (oms_status.get("ViettelPost") or {}).get("status") or "missing_cred",
                "secret_needed": "VIETTELPOST_TOKEN",
                "note": "Gợi ý nối VTP nếu ĐVVC = ViettelPost",
            }
        )
    return mapped


def build_report() -> dict:
    from oms_interconnect import interconnect, load_env

    try:
        from realtime_icon_feedback_mapper import chant, feedback_line
    except Exception:  # noqa: BLE001
        chant = lambda icons: " → ".join(icons)  # noqa: E731
        feedback_line = lambda icons, d="": f"Mapper gọi: {chant(icons)} — {d}"  # noqa: E731

    warehouses, unmapped_dang = collect_warehouses()
    oms = interconnect(load_env(), ingest=False)
    oms_status = {c["backend"]: c for c in oms.get("channels") or []}

    icons = ["cube", "layers", "network", "key", "monitor", "compass"]
    fb = feedback_line(icons, f"warehouses={len(warehouses)}")

    kho_maps = []
    for key, w in sorted(warehouses.items(), key=lambda x: -x[1]["orders"]):
        backends = map_backends_for_warehouse(w, oms_status)
        live = sum(1 for b in backends if b.get("pipe_status") in {"connected", "alive", "ok"})
        missing = sum(1 for b in backends if b.get("pipe_status") == "missing_cred")
        kho_maps.append(
            {
                "kho": key,
                "orders": w["orders"],
                "warehouse_ids": w["warehouse_ids"].most_common(5),
                "addresses": w["addresses"].most_common(3),
                "shops": w["shops"].most_common(8),
                "accounts": w["accounts"].most_common(5),
                "pages": w["pages"].most_common(3),
                "sources": w["sources"].most_common(8),
                "platforms": w["platforms"].most_common(5),
                "statuses": w["statuses"].most_common(8),
                "carriers": w["carriers"].most_common(8),
                "phone": dict(w["phone"]),
                "provinces_top": w["provinces"].most_common(8),
                "staff": {
                    "creator": w["staff_creator"].most_common(5),
                    "seller": w["staff_seller"].most_common(5),
                    "care": w["staff_care"].most_common(5),
                },
                "files": w["files"].most_common(6),
                "backends": backends,
                "backends_live": live,
                "backends_missing_cred": missing,
                "endpoint_hints": sorted(w["endpoints_logical"])[:8],
                "sample_order_keys": w["sample_order_keys"],
            }
        )

    mermaid = """flowchart TB
  subgraph KHO
    HCM[Kho HCM]
    SH[Smart Homes Sender]
  end
  subgraph BE[Backends]
    PK[Pancake]
    DA[direct_api]
    TG[Telegram]
    SPX[SPX-local]
    GHN[GHN]
    VTP[ViettelPost]
    TRK[tracking.aship]
  end
  HCM --> PK
  HCM --> DA
  HCM -.-> GHN
  HCM -.-> VTP
  SH --> SPX --> TRK
  PK -.-> TRK
"""
    named = [k for k in kho_maps if not str(k["kho"]).startswith("(unmapped")]
    verdict = (
        f"Mapper backend từng kho: {len(kho_maps)} kho-node · "
        f"chính: {', '.join(k['kho'] for k in named[:3]) or 'n/a'} · "
        f"Kho HCM backends chủ yếu Pancake/OMS (GHN/VTP suggested missing_cred) · "
        f"Smart Homes → SPX-local+Tracking connected. {fb}"
    )
    return {
        "ok": True,
        "query": "Mapper backend từng kho",
        "checked_at": utc_now(),
        "summary": {
            "warehouses": len(kho_maps),
            "total_order_touches": sum(k["orders"] for k in kho_maps),
            "unmapped_dang_giao_rows": unmapped_dang,
            "oms_channels": {k: v.get("status") for k, v in oms_status.items()},
            "icon_chant": chant(icons),
            "feedback": fb,
        },
        "warehouses": kho_maps,
        "mermaid": mermaid,
        "verdict": verdict,
        "next_actions": [
            "Điền PANCAKE_POS_API_KEY + GHN_API_TOKEN để mở backend live cho Kho HCM",
            "Gắn warehouse_id vào CSV Đang giao để bỏ bucket unmapped_shop",
            "Giữ SPX-local làm backend đã map đủ cho kho Smart Homes",
        ],
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🗄 MAPPER BACKEND TỪNG KHO")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    if s.get("feedback"):
        L(f"✨ {s['feedback']}")
        L(f"Chant: {s.get('icon_chant')}")
        L("")
    L(f"OMS: {s.get('oms_channels')}")
    L("")
    for k in report["warehouses"]:
        if k["orders"] == 0:
            continue
        L(
            f"▶ {k['kho']} · orders={k['orders']} · live_be={k['backends_live']} "
            f"missing_cred={k['backends_missing_cred']}"
        )
        if k["warehouse_ids"]:
            L(f"  id={k['warehouse_ids']}")
        if k["addresses"]:
            L(f"  addr={k['addresses']}")
        L(f"  shops={k['shops'][:5]} accounts={k['accounts'][:3]}")
        L(f"  carriers={k['carriers'][:5]} phone={k['phone']}")
        L(f"  staff creator={k['staff']['creator'][:3]} seller={k['staff']['seller'][:2]}")
        L("  backends:")
        for b in k["backends"][:12]:
            L(
                f"    - [{b.get('kind')}] {b['backend']}: pipe={b.get('pipe_status')} "
                f"touch={b.get('orders_touch')} oms={b.get('oms_channel')}"
            )
            if b.get("secret_needed"):
                L(f"      secret={b['secret_needed']}")
            if b.get("note"):
                L(f"      note={b['note']}")
        if k["endpoint_hints"]:
            L(f"  endpoints: {k['endpoint_hints'][:4]}")
    L("")
    L("Next:")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = format_text(report)
    paths = {
        "json": REPORTS / "warehouse_backend_mapper.json",
        "txt": REPORTS / "warehouse_backend_mapper.txt",
        "mermaid": REPORTS / "warehouse_backend_mapper.mermaid.md",
    }
    paths["json"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=list), encoding="utf-8"
    )
    paths["txt"].write_text(text, encoding="utf-8")
    paths["mermaid"].write_text(
        "# Mapper backend từng kho\n\n```mermaid\n" + report["mermaid"] + "\n```\n",
        encoding="utf-8",
    )
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Mapper backend từng kho")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = build_report()
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
