#!/usr/bin/env python3
"""Mapper toàn diện: hỗ trợ giải mã × icon phản hồi × kho × bưu cục.

Xử lý vấn đề hiện tại (MASKED/MISSING phone, thiếu shipment, thiếu cred carrier)
trên mọi kho + họ bưu cục/3PL trong hệ thống vận chuyển giao hàng.

Chỉ đọc local + probe secrets-owned. Không dump login, không crack ****.
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

# Carrier / bưu cục lead icons
BUUCUC_ICON = {
    "SPX": "cube",
    "GHN": "network",
    "ViettelPost": "network",
    "VNPost": "code",
    "Tracking": "compass",
    "UNASSIGNED_NO_SHIPMENT": "wrench",
    "UNKNOWN_DANG_GIAO": "layers",
    "UNKNOWN": "chip",
    "GAP": "key",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def phone_class(ph: str | None) -> str:
    ph = (ph or "").strip()
    if not ph:
        return "MISSING"
    if "*" in ph:
        return "MASKED"
    digits = re.sub(r"\D", "", ph)
    return "OK" if len(digits) >= 9 else "INVALID"


def classify_buucuc(rec: dict) -> str:
    carrier = (rec.get("carrier") or "").strip()
    tracking = (rec.get("tracking_code") or "").strip()
    channel = (rec.get("channel") or "").lower()
    source = (rec.get("source") or "").strip()
    platform = (rec.get("platform") or "").lower()

    if carrier and carrier not in {"(none)", "(NONE)", "Tên 3PL", "None"}:
        c_up = carrier.upper()
        if "SPX" in c_up:
            return "SPX"
        if "GHN" in c_up or "GIAOHANG" in c_up:
            return "GHN"
        if "VIETTEL" in c_up or c_up == "VTP":
            return "ViettelPost"
        if "VNPOST" in c_up or "BƯU ĐIỆN" in carrier.upper():
            return "VNPost"
        return carrier[:40]
    if tracking.upper().startswith("SPX"):
        return "SPX"
    if re.match(r"(?i)^VĐ", tracking) or tracking.upper().startswith("GHN"):
        return "GHN"
    if channel == "spx_local" or platform == "spx":
        return "SPX"
    if channel in {"pancake_payload", "json_flat"} and not tracking:
        return "UNASSIGNED_NO_SHIPMENT"
    if channel in {"inbox_csv", "direct_api"} or "dang_giao" in (rec.get("file") or "").lower():
        src = source or channel or "csv"
        return f"UNKNOWN_DANG_GIAO/{src}"[:80]
    if not tracking and not carrier:
        return "UNASSIGNED_NO_SHIPMENT"
    return "UNKNOWN"


def kho_key(rec: dict) -> str:
    return (
        (rec.get("warehouse_name") or "").strip()
        or (("(csv_no_warehouse)" if (rec.get("channel") or "") in {"inbox_csv", "direct_api"} else "(none)"))
    )


def issue_icons(kind: str) -> list[str]:
    return {
        "MASKED": ["lock", "text", "key"],
        "MISSING": ["wrench", "text", "monitor"],
        "INVALID": ["wrench", "hash"],
        "ENCODED": ["text", "hash", "key"],
        "NO_SHIPMENT": ["cube", "network", "wrench"],
        "MISSING_CRED": ["key", "lock", "network"],
        "UNMAPPED_KHO": ["cube", "layers", "wrench"],
        "OK_PIPE": ["monitor", "compass", "cube"],
    }.get(kind, ["chip", "wrench"])


def build_report(ingest_limit: int = 5000) -> dict:
    from crypto_decode_assist import detect_and_decode, demo_roundtrip_assist
    from oms_interconnect import ingest_local_orders, interconnect, load_env
    from realtime_icon_feedback_mapper import (
        CHANNEL_ICON,
        chant,
        feedback_line,
        map_channel,
    )

    env = load_env()
    oms = interconnect(env, ingest=False)
    records = ingest_local_orders(limit_per_file=max(100, ingest_limit))

    # --- aggregate kho × buucuc ---
    by_kho: dict[str, dict] = {}
    by_buucuc: dict[str, dict] = {}
    matrix: dict[tuple[str, str], dict] = {}
    phone_global: Counter = Counter()
    decode_hits: list[dict] = []
    tracking_decode: list[dict] = []

    def ensure_node(store: dict, key: str) -> dict:
        return store.setdefault(
            key,
            {
                "orders": 0,
                "phone": Counter(),
                "carriers": Counter(),
                "provinces": Counter(),
                "sources": Counter(),
                "channels": Counter(),
                "staff_creator": Counter(),
                "with_tracking": 0,
                "sample_phones": [],
                "sample_tracking": [],
                "decode_samples": [],
            },
        )

    for rec in records:
        kho = kho_key(rec)
        buu = classify_buucuc(rec)
        ph = (rec.get("customer_phone") or "").strip()
        pc = rec.get("phone_class") or phone_class(ph)
        phone_global[pc] += 1

        for store, key in ((by_kho, kho), (by_buucuc, buu)):
            n = ensure_node(store, key)
            n["orders"] += 1
            n["phone"][pc] += 1
            if rec.get("carrier"):
                n["carriers"][str(rec.get("carrier"))[:40]] += 1
            if rec.get("province"):
                n["provinces"][str(rec.get("province"))[:60]] += 1
            if rec.get("source"):
                n["sources"][str(rec.get("source"))[:40]] += 1
            if rec.get("channel"):
                n["channels"][str(rec.get("channel"))[:40]] += 1
            if rec.get("creator") or rec.get("assigning_seller"):
                n["staff_creator"][str(rec.get("creator") or rec.get("assigning_seller"))[:60]] += 1
            if rec.get("tracking_code"):
                n["with_tracking"] += 1
                if len(n["sample_tracking"]) < 5:
                    n["sample_tracking"].append(str(rec.get("tracking_code"))[:40])

        mk = (kho, buu)
        m = matrix.setdefault(
            mk,
            {"kho": kho, "buucuc": buu, "orders": 0, "phone": Counter(), "with_tracking": 0},
        )
        m["orders"] += 1
        m["phone"][pc] += 1
        if rec.get("tracking_code"):
            m["with_tracking"] += 1

        # decode assist on problem phones
        if pc in {"MASKED", "INVALID"}:
            assist = detect_and_decode(ph)
            if len(decode_hits) < 60:
                hit = {
                    "kho": kho,
                    "buucuc": buu,
                    "order_key": rec.get("order_key") or rec.get("oms_id"),
                    "source": rec.get("source"),
                    "input": ph[:80],
                    "phone_class": pc,
                    "assist_kind": assist.get("kind") or assist.get("detected"),
                    "assist_ok": bool(assist.get("ok")),
                    "assist": assist.get("assist") or assist.get("explain", "")[:120],
                    "plain_text": assist.get("plain_text"),
                }
                decode_hits.append(hit)
                for store, key in ((by_kho, kho), (by_buucuc, buu)):
                    n = store[key]
                    if len(n["decode_samples"]) < 8:
                        n["decode_samples"].append(hit)
                    if len(n["sample_phones"]) < 5:
                        n["sample_phones"].append(ph[:40])
            if assist.get("ok") and assist.get("kind") in {"base64", "hex", "url", "morse", "braille"}:
                # rare encoded — keep all
                pass

        # tracking mask / encoded
        tr = (rec.get("tracking_code") or "").strip()
        if tr and ("*" in tr or re.fullmatch(r"[A-Za-z0-9+/=]{12,}", tr)):
            ta = detect_and_decode(tr)
            if len(tracking_decode) < 20 and (ta.get("kind") == "mask" or ta.get("ok")):
                tracking_decode.append(
                    {
                        "kho": kho,
                        "buucuc": buu,
                        "input": tr[:60],
                        "assist_kind": ta.get("kind") or ta.get("detected"),
                        "assist_ok": bool(ta.get("ok")),
                        "plain_text": ta.get("plain_text"),
                    }
                )

    # --- OMS channel icon maps ---
    channel_maps = [map_channel(c) for c in oms.get("channels") or []]
    blocked = [c for c in channel_maps if c["status"] in {"missing_cred", "auth_fail", "error", "stale"}]
    connected = [c for c in channel_maps if c["status"] in {"connected", "alive", "ok"}]

    # --- issues from current state ---
    issues: list[dict] = []

    def add_issue(iid: str, severity: str, kind: str, title: str, count: int, detail: str, remediation: str):
        icons = issue_icons(kind)
        issues.append(
            {
                "id": iid,
                "severity": severity,
                "kind": kind,
                "title": title,
                "count": count,
                "icons": icons,
                "icon_chant": chant(icons),
                "feedback": feedback_line(icons, detail),
                "remediation": remediation,
            }
        )

    masked_n = int(phone_global.get("MASKED") or 0)
    missing_n = int(phone_global.get("MISSING") or 0)
    invalid_n = int(phone_global.get("INVALID") or 0)
    if masked_n:
        add_issue(
            "ISSUE-MASKED",
            "P0",
            "MASKED",
            "SĐT bị mask **** — decode không khôi phục",
            masked_n,
            f"MASKED={masked_n} trên {len(records)} đơn · module giải mã: kind=mask",
            "Refetch API không PII-mask hoặc lưu AEAD nội bộ (key owned) rồi crypto_decode_assist --aes-gcm",
        )
    if missing_n:
        add_issue(
            "ISSUE-MISSING",
            "P0",
            "MISSING",
            "SĐT trống — không có ciphertext",
            missing_n,
            f"MISSING={missing_n} · bưu cục không gọi được khách",
            "Backfill từ Pancake/GHN/TPOS owned + bắt buộc customer_phone trước đẩy Đang giao",
        )
    if invalid_n:
        add_issue(
            "ISSUE-INVALID",
            "P1",
            "INVALID",
            "SĐT không đủ số / encoding lạ",
            invalid_n,
            f"INVALID={invalid_n} — chạy detect_and_decode",
            "Chạy decode assist; nếu Base64/Hex thì đổi biểu diễn; nếu không → sửa nguồn",
        )

    no_ship = by_buucuc.get("UNASSIGNED_NO_SHIPMENT", {}).get("orders") or 0
    if no_ship:
        add_issue(
            "ISSUE-NO-SHIP",
            "P0",
            "NO_SHIPMENT",
            "Đơn có kho nhưng chưa gắn bưu cục/shipment",
            no_ship,
            f"UNASSIGNED_NO_SHIPMENT={no_ship} · chủ yếu Kho HCM sample",
            "Export Pancake đủ shipments[] + carrier; nối GHN/VTP token owned",
        )

    unmapped = by_kho.get("(csv_no_warehouse)", {}).get("orders") or by_kho.get("(none)", {}).get("orders") or 0
    dang_unknown = sum(v["orders"] for k, v in by_buucuc.items() if k.startswith("UNKNOWN_DANG_GIAO"))
    if dang_unknown:
        add_issue(
            "ISSUE-UNMAPPED-CSV",
            "P0",
            "UNMAPPED_KHO",
            "Đang giao CSV thiếu kho + carrier → bưu cục UNKNOWN",
            dang_unknown,
            f"UNKNOWN_DANG_GIAO*={dang_unknown} · csv_no_warehouse/none≈{unmapped}",
            "Thêm cột warehouse + carrier + tracking vào CSV Đang giao / enrich từ OMS ingest",
        )

    for c in blocked:
        add_issue(
            f"ISSUE-CRED-{c['channel']}",
            "P0" if c["channel"] in {"pancake", "ghn", "viettelpost"} else "P1",
            "MISSING_CRED",
            f"Thiếu credential pipe {c.get('backend')}",
            1,
            c["feedback"],
            f"Điền secret owned trong secrets/backend_pipes.env cho {c.get('backend')}",
        )

    # AEAD capability proof (ephemeral)
    aead = demo_roundtrip_assist()

    # --- serialize nodes with icons ---
    def serialize_kho(key: str, n: dict) -> dict:
        icons = ["cube"]
        ph = n["phone"]
        if ph.get("MASKED") or ph.get("MISSING"):
            icons.extend(["lock", "text"])
        else:
            icons.append("monitor")
        carriers = [c for c, _ in n["carriers"].most_common(3)]
        if any("SPX" in str(c).upper() for c in carriers):
            icons.append("network")
        if n["with_tracking"]:
            icons.append("compass")
        # dedupe
        seen: set[str] = set()
        uniq = []
        for i in icons:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
        detail = (
            f"kho={key} orders={n['orders']} phone={dict(ph)} "
            f"tracking={n['with_tracking']} carriers={carriers[:3]}"
        )
        return {
            "kho": key,
            "orders": n["orders"],
            "phone": dict(ph),
            "carriers_top": n["carriers"].most_common(8),
            "provinces_top": n["provinces"].most_common(8),
            "sources_top": n["sources"].most_common(6),
            "with_tracking": n["with_tracking"],
            "staff_creator_top": n["staff_creator"].most_common(5),
            "sample_phones": n["sample_phones"],
            "sample_tracking": n["sample_tracking"],
            "decode_samples": n["decode_samples"][:5],
            "icons": uniq,
            "icon_chant": chant(uniq),
            "feedback": feedback_line(uniq, detail),
            "issues_local": [
                k
                for k, v in (
                    ("MASKED", ph.get("MASKED")),
                    ("MISSING", ph.get("MISSING")),
                    ("INVALID", ph.get("INVALID")),
                )
                if v
            ],
        }

    def serialize_buu(key: str, n: dict) -> dict:
        lead = BUUCUC_ICON.get(key.split("/")[0], BUUCUC_ICON.get(key, "network"))
        icons = [lead]
        ph = n["phone"]
        if key.startswith("UNKNOWN") or key == "UNASSIGNED_NO_SHIPMENT":
            icons.extend(["wrench", "layers"])
        elif ph.get("MASKED") or ph.get("MISSING"):
            icons.extend(["lock", "text"])
        else:
            icons.append("monitor")
        if n["with_tracking"]:
            icons.append("compass")
        seen: set[str] = set()
        uniq = []
        for i in icons:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
        # warehouses touching this buucuc
        kho_touch = Counter()
        for (kho, buu), cell in matrix.items():
            if buu == key:
                kho_touch[kho] += cell["orders"]
        detail = (
            f"buucuc={key} orders={n['orders']} kho={kho_touch.most_common(4)} "
            f"phone={dict(ph)} track={n['with_tracking']}"
        )
        return {
            "buucuc": key,
            "orders": n["orders"],
            "phone": dict(ph),
            "kho_stats": kho_touch.most_common(12),
            "kho_unique": len(kho_touch),
            "provinces_top": n["provinces"].most_common(8),
            "with_tracking": n["with_tracking"],
            "sample_tracking": n["sample_tracking"],
            "decode_samples": n["decode_samples"][:5],
            "icons": uniq,
            "icon_chant": chant(uniq),
            "feedback": feedback_line(uniq, detail),
            "real_carrier": key in {"SPX", "GHN", "ViettelPost", "VNPost"}
            or not key.startswith(("UNKNOWN", "UNASSIGNED", "GAP")),
        }

    kho_maps = [
        serialize_kho(k, v)
        for k, v in sorted(by_kho.items(), key=lambda x: -x[1]["orders"])
    ]
    buu_maps = [
        serialize_buu(k, v)
        for k, v in sorted(by_buucuc.items(), key=lambda x: -x[1]["orders"])
    ]

    matrix_rows = []
    for (kho, buu), cell in sorted(matrix.items(), key=lambda x: -x[1]["orders"]):
        ph = cell["phone"]
        icons = ["cube", BUUCUC_ICON.get(buu.split("/")[0], "network")]
        if ph.get("MASKED") or ph.get("MISSING"):
            icons.append("lock")
        elif cell["with_tracking"]:
            icons.append("compass")
        else:
            icons.append("monitor")
        seen_i: set[str] = set()
        uniq_i = []
        for i in icons:
            if i not in seen_i:
                seen_i.add(i)
                uniq_i.append(i)
        matrix_rows.append(
            {
                "kho": kho,
                "buucuc": buu,
                "orders": cell["orders"],
                "phone": dict(ph),
                "with_tracking": cell["with_tracking"],
                "icons": uniq_i,
                "icon_chant": chant(uniq_i),
                "feedback": feedback_line(
                    uniq_i,
                    f"{kho} × {buu}: n={cell['orders']} phone={dict(ph)} track={cell['with_tracking']}",
                ),
            }
        )

    # global chant: decode + icon + logistics
    global_icons = ["text", "lock", "key", "cube", "network", "compass", "monitor"]
    if blocked:
        global_icons = ["spark", "text", "lock", "key", "cube", "network", "wrench"]
    seen = set()
    g_uniq = []
    for i in global_icons:
        if i not in seen:
            seen.add(i)
            g_uniq.append(i)

    real_buu = [b for b in buu_maps if b.get("real_carrier") and b["orders"] > 0]
    gap_buu = [b for b in buu_maps if not b.get("real_carrier")]

    top_fb = feedback_line(
        g_uniq,
        f"decode+icon logistics · records={len(records)} · kho={len(kho_maps)} · "
        f"buucuc={len(buu_maps)} (real={len(real_buu)} gap={len(gap_buu)}) · "
        f"phone OK={phone_global.get('OK',0)} MASKED={masked_n} MISSING={missing_n} · "
        f"OMS {len(connected)}/{len(channel_maps)} · issues={len(issues)} · "
        f"AEAD demo={aead['decrypt_result'].get('roundtrip_ok')}",
    )

    # icon paths: each kho + each buucuc + issues
    icon_paths = []
    for k in kho_maps[:12]:
        icon_paths.append(
            {"kind": "kho", "path": f"OMS → {k['kho']} → phone/decode", "feedback": k["feedback"], "count": k["orders"]}
        )
    for b in buu_maps[:12]:
        icon_paths.append(
            {
                "kind": "buucuc",
                "path": f"OMS → {b['buucuc']} → giao hàng",
                "feedback": b["feedback"],
                "count": b["orders"],
            }
        )
    for iss in issues[:10]:
        icon_paths.append(
            {"kind": "issue", "path": iss["id"], "feedback": iss["feedback"], "count": iss["count"]}
        )

    mermaid = _mermaid(kho_maps, buu_maps, phone_global, issues)

    report = {
        "ok": True,
        "query": (
            "Sử dụng module hỗ trợ giải mã để xử lý các vấn đề hiện tại, "
            "kết hợp mapper icon nhận phản hồi ánh xạ toàn diện "
            "hệ thống vận chuyển giao hàng tất cả bưu cục + kho"
        ),
        "checked_at": utc_now(),
        "modules": [
            {"id": "crypto_decode_assist", "role": "phân loại/giải encode + AEAD owned"},
            {"id": "realtime_icon_feedback_mapper", "role": "chant / feedback icon army"},
            {"id": "oms_interconnect.ingest", "role": "gom đơn mọi nguồn local"},
        ],
        "summary": {
            "records": len(records),
            "warehouses": len(kho_maps),
            "buucuc_families": len(buu_maps),
            "real_carrier_families": len(real_buu),
            "gap_families": len(gap_buu),
            "matrix_cells": len(matrix_rows),
            "phone": dict(phone_global),
            "issues": len(issues),
            "oms_connected": len(connected),
            "oms_blocked": len(blocked),
            "aead_roundtrip_ok": aead["decrypt_result"].get("roundtrip_ok"),
            "icon_chant": chant(g_uniq),
            "feedback": top_fb,
        },
        "global": {
            "icons": g_uniq,
            "icon_chant": chant(g_uniq),
            "feedback": top_fb,
            "called": [{"name": i} for i in g_uniq],
        },
        "decode": {
            "disclaimer": (
                "encode/* chỉ đổi biểu diễn; **** không giải được; "
                "AEAD cần key owned"
            ),
            "phone_hits": decode_hits,
            "tracking_hits": tracking_decode,
            "aead_demo": {
                "roundtrip_ok": aead["decrypt_result"].get("roundtrip_ok"),
                "kind": aead["decrypt_result"].get("kind"),
                "note": aead.get("note"),
            },
            "masked_not_decryptable": True,
            "encoded_candidates": sum(1 for h in decode_hits if h.get("assist_ok")),
        },
        "issues": issues,
        "warehouses": kho_maps,
        "buucuc": buu_maps,
        "matrix_kho_buucuc": matrix_rows[:80],
        "oms_channels": channel_maps,
        "icon_paths": icon_paths,
        "mermaid": mermaid,
        "verdict": top_fb,
        "next_actions": [
            "P0 MASKED/MISSING: refetch API + AEAD nội bộ — không dùng fromBase64 trên ****",
            "P0 UNASSIGNED_NO_SHIPMENT / UNKNOWN_DANG_GIAO: gắn warehouse+shipments+carrier vào OMS",
            "P0 điền secrets owned Pancake/GHN/ViettelPost/TPOS trong backend_pipes.env",
            "Panel: 🔓 Ánh xạ giải mã×icon (kho+bưu cục) / chạy scripts/decode_icon_logistics_mapper.py",
            "CS cần SĐT: crypto_decode_assist --aes-gcm KEY NONCE CT khi đã mã hoá AEAD",
        ],
        "safety": {
            "no_dump_login": True,
            "no_password_crack": True,
            "mask_not_decryptable": True,
            "aead_requires_owned_key": True,
        },
    }
    return report


def _mermaid(kho_maps: list, buu_maps: list, phone: Counter, issues: list) -> str:
    kho_lines = []
    for i, k in enumerate(kho_maps[:6]):
        safe = re.sub(r"[^A-Za-z0-9]", "", k["kho"])[:20] or f"K{i}"
        kho_lines.append(f'    K{i}["{k["kho"][:28]}\\nn={k["orders"]}"]')
    buu_lines = []
    for i, b in enumerate(buu_maps[:8]):
        buu_lines.append(f'    B{i}["{b["buucuc"][:32]}\\nn={b["orders"]}"]')
    return f"""flowchart LR
  subgraph DEC["Module giải mã"]
    D1[detect_and_decode]
    D2[AEAD owned key]
    D3[mask ≠ ciphertext]
  end
  subgraph ICON["Icon phản hồi"]
    I1[Khối Kho]
    I2[Mạch Mạng bưu cục]
    I3[Ổ Khóa / Chìa]
  end
  subgraph KHO["Tất cả kho"]
{chr(10).join(kho_lines) or '    KX[none]'}
  end
  subgraph BUU["Tất cả bưu cục / 3PL"]
{chr(10).join(buu_lines) or '    BX[none]'}
  end
  OPS["Đơn ops\\nOK={phone.get('OK',0)} MASKED={phone.get('MASKED',0)} MISSING={phone.get('MISSING',0)}\\nissues={len(issues)}"]
  D1 --> OPS
  D2 -.-> OPS
  D3 --> I3
  I1 --> KHO --> OPS
  I2 --> BUU --> OPS
  I3 --> OPS
"""


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔓✨ MAPPER GIẢI MÃ × ICON · KHO + BƯU CỤC TOÀN DIỆN")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    L(f"✨ {s.get('feedback')}")
    L(f"Chant: {s.get('icon_chant')}")
    L("")
    L("=== Vấn đề hiện tại (decode + icon) ===")
    for iss in report["issues"]:
        L(f"▶ [{iss['severity']}] {iss['id']} · {iss['title']} · n={iss['count']}")
        L(f"  {iss['icon_chant']}")
        L(f"  {iss['feedback']}")
        L(f"  → {iss['remediation']}")
    L("")
    L("=== Module giải mã ===")
    d = report["decode"]
    L(f"· {d['disclaimer']}")
    L(
        f"· AEAD demo roundtrip={d['aead_demo'].get('roundtrip_ok')} · "
        f"encoded_candidates={d.get('encoded_candidates')} · "
        f"phone_hits={len(d.get('phone_hits') or [])}"
    )
    for h in (d.get("phone_hits") or [])[:8]:
        L(
            f"  - [{h.get('kho')}|{h.get('buucuc')}] {h.get('input')!r} "
            f"→ {h.get('assist_kind')} ok={h.get('assist_ok')} · {str(h.get('assist') or '')[:70]}"
        )
    L("")
    L("=== Tất cả kho ===")
    for k in report["warehouses"]:
        L(f"▶ {k['kho']} · orders={k['orders']} phone={k['phone']} track={k['with_tracking']}")
        L(f"  {k['icon_chant']}")
        L(f"  {k['feedback']}")
        if k.get("issues_local"):
            L(f"  issues={k['issues_local']}")
    L("")
    L("=== Tất cả bưu cục / 3PL ===")
    for b in report["buucuc"]:
        mark = "✅" if b.get("real_carrier") else "⚠️"
        L(
            f"{mark} {b['buucuc']} · orders={b['orders']} kho_unique={b['kho_unique']} "
            f"phone={b['phone']} track={b['with_tracking']}"
        )
        L(f"  {b['icon_chant']}")
        L(f"  kho={b['kho_stats'][:4]}")
    L("")
    L("=== Ma trận kho × bưu cục (top) ===")
    for row in report["matrix_kho_buucuc"][:16]:
        L(f"· {row['kho']} × {row['buucuc']}: n={row['orders']} phone={row['phone']}")
        L(f"  {row['feedback']}")
    L("")
    L("=== OMS channels (icon) ===")
    for c in report["oms_channels"]:
        mark = "✅" if c["status"] in {"connected", "alive", "ok"} else "⚠️"
        L(f"{mark} {c['icon_chant']}")
        L(f"   {c['feedback']}")
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
        "json": REPORTS / "decode_icon_logistics_mapper.json",
        "txt": REPORTS / "decode_icon_logistics_mapper.txt",
        "rt_json": OUT / "decode_icon_logistics_mapper.json",
        "rt_txt": OUT / "decode_icon_logistics_mapper.txt",
        "mermaid": REPORTS / "decode_icon_logistics_mapper.mermaid.md",
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    paths["mermaid"].write_text(
        "# Mapper giải mã × icon · kho + bưu cục\n\n```mermaid\n" + report["mermaid"] + "\n```\n",
        encoding="utf-8",
    )
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Mapper giải mã × icon × kho × bưu cục")
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
