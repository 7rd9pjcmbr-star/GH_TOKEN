#!/usr/bin/env python3
"""Truy cập kho — thống kê nhân sự + tên shop theo từng kho × bưu cục.

Đọc OMS ingest (đã enrich shop_name / account / creator). Icon feedback.
Không dump login.
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
OUT = REPORTS / "realtime"

# Catalog tên shop đã biết (id → tên hiển thị) — bổ sung từ warehouse_info / SPX sender
KNOWN_SHOP_NAMES: dict[str, str] = {
    "1530618": "Pancake shop 1530618 (Sam Spa / Kho HCM)",
    "9999999": "Pancake shop 9999999 (sample alias → Sam Spa)",
    "4851972": "Pancake shop 4851972 (Sam Spa / Kho HCM)",
    "1658780215": "Smart Homes - Gia Dụng Mọi Nhà (SPX Account)",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_buucuc(rec: dict) -> str:
    carrier = (rec.get("carrier") or "").strip()
    tracking = (rec.get("tracking_code") or "").strip()
    channel = (rec.get("channel") or "").lower()
    source = (rec.get("source") or "").strip()
    platform = (rec.get("platform") or "").lower()

    if carrier and carrier not in {"(none)", "(NONE)", "None"}:
        c_up = carrier.upper()
        if "SPX" in c_up:
            return "SPX"
        if "GHN" in c_up or "GIAOHANG" in c_up:
            return "GHN"
        if "VIETTEL" in c_up or c_up == "VTP":
            return "ViettelPost"
        if "VNPOST" in c_up:
            return "VNPost"
        return carrier[:40]
    if tracking.upper().startswith("SPX"):
        return "SPX"
    if channel == "spx_local" or platform == "spx":
        return "SPX"
    if channel in {"pancake_payload", "json_flat"} and not tracking:
        return "UNASSIGNED_NO_SHIPMENT"
    if channel in {"inbox_csv", "direct_api"} or "dang_giao" in (rec.get("file") or "").lower():
        return f"UNKNOWN_DANG_GIAO/{source or channel}"[:80]
    if not tracking and not carrier:
        return "UNASSIGNED_NO_SHIPMENT"
    return "UNKNOWN"


def kho_key(rec: dict) -> str:
    return (
        (rec.get("warehouse_name") or "").strip()
        or (
            "(csv_no_warehouse)"
            if (rec.get("channel") or "") in {"inbox_csv", "direct_api"}
            else "(none)"
        )
    )


def resolve_shop_name(rec: dict) -> str:
    sid = str(rec.get("shop_id") or "").strip()
    name = (rec.get("shop_name") or "").strip()
    display = (rec.get("warehouse_display_name") or "").strip()
    if name:
        return name
    if display and sid:
        return f"{display} [shop:{sid}]"
    if sid in KNOWN_SHOP_NAMES:
        return KNOWN_SHOP_NAMES[sid]
    if sid:
        return f"(unnamed shop {sid})"
    return "(no_shop)"


def staff_label(rec: dict) -> dict[str, str | None]:
    return {
        "creator": (str(rec.get("creator")).strip() if rec.get("creator") not in (None, "") else None),
        "account": (str(rec.get("account")).strip() if rec.get("account") not in (None, "") else None),
        "seller": (
            str(rec.get("assigning_seller")).strip()
            if rec.get("assigning_seller") not in (None, "")
            else None
        ),
        "care": (
            str(rec.get("assigning_care")).strip()
            if rec.get("assigning_care") not in (None, "")
            else None
        ),
    }


def _blank_cell() -> dict:
    return {
        "orders": 0,
        "shops": Counter(),  # shop_id → n
        "shop_names": Counter(),  # display name → n
        "shop_id_to_name": {},
        "staff_creator": Counter(),
        "staff_account": Counter(),
        "staff_seller": Counter(),
        "staff_care": Counter(),
        "pages": Counter(),
        "pancake_shops": Counter(),
        "phone": Counter(),
        "sources": Counter(),
    }


def build_report(ingest_limit: int = 5000) -> dict:
    from oms_interconnect import ingest_local_orders
    from realtime_icon_feedback_mapper import chant, feedback_line

    records = ingest_local_orders(limit_per_file=max(100, ingest_limit))

    by_kho: dict[str, dict] = {}
    by_buucuc: dict[str, dict] = {}
    matrix: dict[tuple[str, str], dict] = {}
    shop_catalog: dict[str, dict] = {}
    staff_catalog: Counter = Counter()

    def touch(store: dict, key: str) -> dict:
        if key not in store:
            store[key] = {
                "orders": 0,
                "shops": Counter(),
                "shop_names": Counter(),
                "shop_id_to_name": {},
                "staff_creator": Counter(),
                "staff_account": Counter(),
                "staff_seller": Counter(),
                "staff_care": Counter(),
                "pages": Counter(),
                "pancake_shops": Counter(),
                "buucuc_touch": Counter(),
                "kho_touch": Counter(),
                "warehouse_ids": Counter(),
                "warehouse_display": Counter(),
                "phone": Counter(),
                "with_seller": 0,
                "with_care": 0,
                "with_creator": 0,
            }
        return store[key]

    for rec in records:
        kho = kho_key(rec)
        buu = classify_buucuc(rec)
        sid = str(rec.get("shop_id") or "").strip() or "(no_shop)"
        sname = resolve_shop_name(rec)
        st = staff_label(rec)
        pc = rec.get("phone_class") or "UNKNOWN"

        for store, key, peer_key, peer_field in (
            (by_kho, kho, buu, "buucuc_touch"),
            (by_buucuc, buu, kho, "kho_touch"),
        ):
            n = touch(store, key)
            n["orders"] += 1
            n["shops"][sid] += 1
            n["shop_names"][sname] += 1
            n["shop_id_to_name"][sid] = sname
            n[peer_field][peer_key] += 1
            n["phone"][pc] += 1
            if rec.get("warehouse_id"):
                n["warehouse_ids"][str(rec["warehouse_id"])] += 1
            if rec.get("warehouse_display_name"):
                n["warehouse_display"][str(rec["warehouse_display_name"])] += 1
            if rec.get("page_id"):
                n["pages"][str(rec["page_id"])] += 1
            if rec.get("pancake_shop_id"):
                n["pancake_shops"][str(rec["pancake_shop_id"])] += 1
            if st["creator"]:
                n["staff_creator"][st["creator"]] += 1
                n["with_creator"] += 1
                staff_catalog[f"creator:{st['creator']}"] += 1
            if st["account"]:
                n["staff_account"][st["account"]] += 1
                staff_catalog[f"account:{st['account']}"] += 1
            if st["seller"]:
                n["staff_seller"][st["seller"]] += 1
                n["with_seller"] += 1
                staff_catalog[f"seller:{st['seller']}"] += 1
            if st["care"]:
                n["staff_care"][st["care"]] += 1
                n["with_care"] += 1
                staff_catalog[f"care:{st['care']}"] += 1

        cell = matrix.setdefault((kho, buu), _blank_cell())
        cell["orders"] += 1
        cell["shops"][sid] += 1
        cell["shop_names"][sname] += 1
        cell["shop_id_to_name"][sid] = sname
        cell["phone"][pc] += 1
        if rec.get("source"):
            cell["sources"][str(rec["source"])[:40]] += 1
        if rec.get("page_id"):
            cell["pages"][str(rec["page_id"])] += 1
        if rec.get("pancake_shop_id"):
            cell["pancake_shops"][str(rec["pancake_shop_id"])] += 1
        if st["creator"]:
            cell["staff_creator"][st["creator"]] += 1
        if st["account"]:
            cell["staff_account"][st["account"]] += 1
        if st["seller"]:
            cell["staff_seller"][st["seller"]] += 1
        if st["care"]:
            cell["staff_care"][st["care"]] += 1

        # shop catalog
        cat = shop_catalog.setdefault(
            sid,
            {
                "shop_id": sid,
                "shop_name": sname,
                "orders": 0,
                "kho": Counter(),
                "buucuc": Counter(),
                "staff_creator": Counter(),
                "staff_account": Counter(),
                "pages": Counter(),
                "pancake_shop_ids": Counter(),
            },
        )
        cat["orders"] += 1
        cat["shop_name"] = sname
        cat["kho"][kho] += 1
        cat["buucuc"][buu] += 1
        if st["creator"]:
            cat["staff_creator"][st["creator"]] += 1
        if st["account"]:
            cat["staff_account"][st["account"]] += 1
        if rec.get("page_id"):
            cat["pages"][str(rec["page_id"])] += 1
        if rec.get("pancake_shop_id"):
            cat["pancake_shop_ids"][str(rec["pancake_shop_id"])] += 1

    def ser_kho(key: str, n: dict) -> dict:
        icons = ["cube", "layers", "key"]
        if n["with_seller"] or n["with_care"]:
            icons.append("monitor")
        else:
            icons.append("wrench")
        detail = (
            f"kho={key} orders={n['orders']} shops={len(n['shops'])} "
            f"creator_assigned={n['with_creator']} seller={n['with_seller']} care={n['with_care']}"
        )
        return {
            "kho": key,
            "orders": n["orders"],
            "warehouse_ids": n["warehouse_ids"].most_common(5),
            "warehouse_display_names": n["warehouse_display"].most_common(5),
            "shops": [
                {"shop_id": sid, "shop_name": n["shop_id_to_name"].get(sid, sid), "orders": c}
                for sid, c in n["shops"].most_common(20)
            ],
            "shop_names_top": n["shop_names"].most_common(12),
            "nhan_su": {
                "creator": n["staff_creator"].most_common(15),
                "account": n["staff_account"].most_common(15),
                "seller": n["staff_seller"].most_common(15),
                "care": n["staff_care"].most_common(15),
                "creator_assigned": n["with_creator"],
                "seller_assigned": n["with_seller"],
                "care_assigned": n["with_care"],
            },
            "buucuc_touch": n["buucuc_touch"].most_common(12),
            "pages": n["pages"].most_common(8),
            "pancake_shops": n["pancake_shops"].most_common(8),
            "phone": dict(n["phone"]),
            "icons": icons,
            "icon_chant": chant(icons),
            "feedback": feedback_line(icons, detail),
        }

    def ser_buu(key: str, n: dict) -> dict:
        icons = ["network", "layers", "key"]
        if key == "SPX":
            icons = ["cube", "layers", "key", "compass"]
        detail = (
            f"buucuc={key} orders={n['orders']} shops={len(n['shops'])} "
            f"kho={n['kho_touch'].most_common(4)}"
        )
        return {
            "buucuc": key,
            "orders": n["orders"],
            "shops": [
                {"shop_id": sid, "shop_name": n["shop_id_to_name"].get(sid, sid), "orders": c}
                for sid, c in n["shops"].most_common(20)
            ],
            "shop_names_top": n["shop_names"].most_common(12),
            "nhan_su": {
                "creator": n["staff_creator"].most_common(15),
                "account": n["staff_account"].most_common(15),
                "seller": n["staff_seller"].most_common(15),
                "care": n["staff_care"].most_common(15),
                "creator_assigned": n["with_creator"],
                "seller_assigned": n["with_seller"],
                "care_assigned": n["with_care"],
            },
            "kho_touch": n["kho_touch"].most_common(12),
            "icons": icons,
            "icon_chant": chant(icons),
            "feedback": feedback_line(icons, detail),
        }

    def ser_cell(kho: str, buu: str, cell: dict) -> dict:
        icons = ["cube", "network", "layers"]
        ns_creator = cell["staff_creator"].most_common(10)
        ns_seller = cell["staff_seller"].most_common(10)
        if not ns_creator and not ns_seller:
            icons.append("wrench")
        else:
            icons.append("key")
        shops = [
            {"shop_id": sid, "shop_name": cell["shop_id_to_name"].get(sid, sid), "orders": c}
            for sid, c in cell["shops"].most_common(15)
        ]
        detail = (
            f"{kho} × {buu}: n={cell['orders']} shops={len(shops)} "
            f"creator={ns_creator[:2]} seller={ns_seller[:2]}"
        )
        return {
            "kho": kho,
            "buucuc": buu,
            "orders": cell["orders"],
            "shops": shops,
            "shop_names_top": cell["shop_names"].most_common(10),
            "nhan_su": {
                "creator": ns_creator,
                "account": cell["staff_account"].most_common(10),
                "seller": ns_seller,
                "care": cell["staff_care"].most_common(10),
            },
            "pages": cell["pages"].most_common(5),
            "pancake_shops": cell["pancake_shops"].most_common(5),
            "phone": dict(cell["phone"]),
            "sources": cell["sources"].most_common(6),
            "icons": icons,
            "icon_chant": chant(icons),
            "feedback": feedback_line(icons, detail),
        }

    kho_maps = [ser_kho(k, v) for k, v in sorted(by_kho.items(), key=lambda x: -x[1]["orders"])]
    buu_maps = [ser_buu(k, v) for k, v in sorted(by_buucuc.items(), key=lambda x: -x[1]["orders"])]
    matrix_rows = [
        ser_cell(k, b, c)
        for (k, b), c in sorted(matrix.items(), key=lambda x: -x[1]["orders"])
    ]
    shops = [
        {
            "shop_id": sid,
            "shop_name": cat["shop_name"],
            "orders": cat["orders"],
            "kho_top": cat["kho"].most_common(8),
            "buucuc_top": cat["buucuc"].most_common(8),
            "staff_creator": cat["staff_creator"].most_common(8),
            "staff_account": cat["staff_account"].most_common(8),
            "pages": cat["pages"].most_common(5),
            "pancake_shop_ids": cat["pancake_shop_ids"].most_common(5),
        }
        for sid, cat in sorted(shop_catalog.items(), key=lambda x: -x[1]["orders"])
    ]

    global_icons = ["cube", "network", "layers", "key", "monitor"]
    named_shops = sum(1 for s in shops if not str(s["shop_name"]).startswith("("))
    staff_with_name = sum(1 for k, _ in staff_catalog.most_common() if not k.endswith(":(null)"))
    top_fb = feedback_line(
        global_icons,
        f"truy cập kho · records={len(records)} · kho={len(kho_maps)} · "
        f"buucuc={len(buu_maps)} · shops={len(shops)} (named≈{named_shops}) · "
        f"staff_keys={len(staff_catalog)} · matrix={len(matrix_rows)}",
    )

    gaps = []
    for row in matrix_rows:
        ns = row["nhan_su"]
        if not ns["creator"] and not ns["seller"] and not ns["care"]:
            gaps.append(
                {
                    "kho": row["kho"],
                    "buucuc": row["buucuc"],
                    "orders": row["orders"],
                    "gap": "thiếu nhân sự (creator/seller/care)",
                    "shops": [s["shop_name"] for s in row["shops"][:5]],
                }
            )
        elif all(str(s["shop_name"]).startswith("(") for s in row["shops"]):
            gaps.append(
                {
                    "kho": row["kho"],
                    "buucuc": row["buucuc"],
                    "orders": row["orders"],
                    "gap": "shop chỉ có id — chưa có tên hiển thị",
                    "shops": [s["shop_id"] for s in row["shops"][:5]],
                }
            )

    mermaid = _mermaid(kho_maps, matrix_rows)

    return {
        "ok": True,
        "query": "Truy cập các kho thống kê nhân sự và tên shop của từng kho+bưu cục",
        "checked_at": utc_now(),
        "summary": {
            "records": len(records),
            "warehouses": len(kho_maps),
            "buucuc_families": len(buu_maps),
            "shops": len(shops),
            "named_shops": named_shops,
            "matrix_cells": len(matrix_rows),
            "staff_keys": len(staff_catalog),
            "gaps": len(gaps),
            "icon_chant": chant(global_icons),
            "feedback": top_fb,
        },
        "warehouses": kho_maps,
        "buucuc": buu_maps,
        "matrix_kho_buucuc": matrix_rows,
        "shop_catalog": shops,
        "staff_catalog_top": staff_catalog.most_common(40),
        "gaps": gaps[:30],
        "icon_feedback": top_fb,
        "mermaid": mermaid,
        "verdict": top_fb,
        "next_actions": [
            "Kho HCM: seller/care đang null — refetch Pancake assigning_* theo shop Sam Spa",
            "CSV Đang giao: bổ sung warehouse + shop_name + nhân sự (hiện csv_no_warehouse)",
            "Map shop_id 1530618/9999999/4851972 → tên POS chính thức khi có API key owned",
            "SPX Account 1658780215 = Smart Homes — gắn Order Creator vào NS OMS",
        ],
    }


def _mermaid(kho_maps: list, matrix_rows: list) -> str:
    lines = ["flowchart TB", '  subgraph KHO["Kho"]']
    for i, k in enumerate(kho_maps[:6]):
        shops = ", ".join(s["shop_name"][:20] for s in k["shops"][:2]) or "no shop"
        lines.append(f'    K{i}["{k["kho"][:24]}\\n{shops}"]')
    lines.append("  end")
    lines.append('  subgraph CELL["Kho × Bưu cục"]')
    for i, row in enumerate(matrix_rows[:8]):
        ns = row["nhan_su"]["creator"][:1] or row["nhan_su"]["account"][:1] or [["(null)", 0]]
        lines.append(
            f'    C{i}["{row["kho"][:16]}×{row["buucuc"][:18]}\\n'
            f'n={row["orders"]} NS={ns[0][0][:24]}"]'
        )
    lines.append("  end")
    lines.append("  KHO --> CELL")
    return "\n".join(lines)


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🏬 TRUY CẬP KHO · NHÂN SỰ + TÊN SHOP × BƯU CỤC")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    L(f"✨ {s.get('feedback')}")
    L(f"Chant: {s.get('icon_chant')}")
    L("")
    L("=== Catalog shop ===")
    for sh in report["shop_catalog"][:12]:
        L(f"▶ shop_id={sh['shop_id']} · {sh['shop_name']} · orders={sh['orders']}")
        L(f"  kho={sh['kho_top'][:3]}")
        L(f"  buucuc={sh['buucuc_top'][:3]}")
        L(f"  NS creator={sh['staff_creator'][:3]} account={sh['staff_account'][:3]}")
    L("")
    L("=== Từng kho ===")
    for k in report["warehouses"]:
        L(f"▶ {k['kho']} · orders={k['orders']}")
        L(f"  display={k['warehouse_display_names']}")
        L(f"  shops:")
        for sh in k["shops"][:6]:
            L(f"    · {sh['shop_id']}: {sh['shop_name']} (n={sh['orders']})")
        ns = k["nhan_su"]
        L(
            f"  NS creator={ns['creator'][:4]} | account={ns['account'][:3]} | "
            f"seller={ns['seller'][:3] or '[(null)]'} | care={ns['care'][:3] or '[(null)]'}"
        )
        L(f"  assigned creator={ns['creator_assigned']} seller={ns['seller_assigned']} care={ns['care_assigned']}")
        L(f"  buucuc={k['buucuc_touch'][:5]}")
        L(f"  {k['icon_chant']}")
    L("")
    L("=== Từng bưu cục ===")
    for b in report["buucuc"]:
        L(f"▶ {b['buucuc']} · orders={b['orders']}")
        for sh in b["shops"][:5]:
            L(f"  · shop {sh['shop_id']}: {sh['shop_name']} (n={sh['orders']})")
        ns = b["nhan_su"]
        L(f"  NS creator={ns['creator'][:4]} seller={ns['seller'][:2]} care={ns['care'][:2]}")
        L(f"  kho={b['kho_touch'][:4]}")
    L("")
    L("=== Ma trận kho × bưu cục (shop + NS) ===")
    for row in report["matrix_kho_buucuc"]:
        L(f"▶ {row['kho']} × {row['buucuc']} · n={row['orders']} phone={row['phone']}")
        L(
            "  shops: "
            + "; ".join(f"{s['shop_name']}[{s['shop_id']}]={s['orders']}" for s in row["shops"][:5])
        )
        ns = row["nhan_su"]
        L(
            f"  NS creator={ns['creator'][:3] or '[(null)]'} · "
            f"account={ns['account'][:3] or '[(null)]'} · "
            f"seller={ns['seller'][:2] or '[(null)]'} · care={ns['care'][:2] or '[(null)]'}"
        )
        L(f"  {row['feedback']}")
    if report.get("gaps"):
        L("")
        L("=== Gap NS / tên shop ===")
        for g in report["gaps"][:12]:
            L(f"· {g['kho']} × {g['buucuc']}: {g['gap']} (n={g['orders']}) shops={g['shops']}")
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
        "json": REPORTS / "kho_buucuc_staff_shop.json",
        "txt": REPORTS / "kho_buucuc_staff_shop.txt",
        "rt_json": OUT / "kho_buucuc_staff_shop.json",
        "rt_txt": OUT / "kho_buucuc_staff_shop.txt",
        "mermaid": REPORTS / "kho_buucuc_staff_shop.mermaid.md",
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    paths["mermaid"].write_text(
        "# Kho × bưu cục · nhân sự + shop\n\n```mermaid\n" + report["mermaid"] + "\n```\n",
        encoding="utf-8",
    )
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Thống kê NS + shop theo kho × bưu cục")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=5000)
    args = ap.parse_args()
    report = build_report(ingest_limit=max(100, args.limit))
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
