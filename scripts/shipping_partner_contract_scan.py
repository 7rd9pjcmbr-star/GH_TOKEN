#!/usr/bin/env python3
"""Rà soát mã hợp đồng / đối tác vận chuyển trong data local (inventory-only).

Quét secrets + quarantine + reports tìm:
  · hopdongdientu / SSO app_key + client_id
  · shop_id / partner_id / 3PL owned trong env
  · prefix OMS trên mã đơn (TPO/PKE/ZLS/…)
  · pattern số HĐ (HĐ-… / CONTRACT-…)

Không dump-login. Không gọi API shop bằng token dump.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "telegram-classify"

FIELD_HINTS = re.compile(
    r"(?i)("
    r"h[oọ]p[_\s-]?[dđ][oồ]ng|hopdong|contract|agreement|"
    r"m[aã][_\s-]?h[dđ]|mahd|so[_\s-]?h[dđ]|contract[_\s_-]?(code|id|no|number)|"
    r"partner[_\s_-]?(id|code|contract|name)|doi[_\s-]?tac|đ[oố]i[_\s-]?t[aá]c|"
    r"client[_\s_-]?(id|code|contract)|shop[_\s_-]?id|carrier|van[_\s-]?chuyen|"
    r"3pl|fulfillment|shipping[_\s_-]?partner|service[_\s_-]?id|app[_\s_-]?key|client_id"
    r")"
)
VALUE_HINTS = re.compile(
    r"(?i)(\bH[ĐD]\s*[A-Z0-9/_-]{3,}\b|\bHD[ĐD]?[-_/]?\d{3,}\b|\bCONTRACT[-_]?\d+\b|\bPARTNER[-_]?\d+\b)"
)
UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
DUMP_NAME = re.compile(
    r"(?i)acc_all|stealer|onlylogs|ghn_tokens|internal_search|cookiacc|passwords?|results_cookies"
)
OMS_PREFIX_MEANING = {
    "TPO": "TPOS/OMS",
    "PKE": "Pancake",
    "ZLS": "ZLS OMS",
    "KMS": "KMS/Kiot?",
    "NVS": "NVS OMS",
    "MIS": "MIS",
    "CMCT": "CMCT",
    "VTP": "ViettelPost",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(
    findings: list[dict[str, Any]],
    *,
    kind: str,
    source: str | Path,
    key: str,
    value: Any,
    note: str = "",
    confidence: str = "medium",
) -> None:
    v = str(value)
    if len(v) > 48 and (v.startswith("eyJ") or re.fullmatch(r"[0-9a-fA-F-]{36}", v)):
        masked = f"{v[:10]}…{v[-6:]}(len={len(v)})"
    elif len(v) > 80:
        masked = v[:60] + "…"
    else:
        masked = v
    findings.append(
        {
            "kind": kind,
            "source": str(source),
            "key": key,
            "value_masked": masked,
            "value_len": len(v),
            "note": note,
            "confidence": confidence,
        }
    )


def scan_env(findings: list[dict[str, Any]]) -> None:
    for ef in (
        ROOT / ".env",
        ROOT / "secrets" / "order_session.env",
        ROOT / "secrets" / "telegram.env",
        ROOT / "secrets" / "backend_pipes.env",
    ):
        if not ef.is_file():
            continue
        for ln in ef.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not ln.strip() or ln.strip().startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if FIELD_HINTS.search(k):
                add(
                    findings,
                    kind="env_key",
                    source=ef,
                    key=k,
                    value=v,
                    note="env field match",
                    confidence="high",
                )


def scan_1k5_prefixes() -> dict[str, int]:
    csv1k5 = ROOT / "quarantine" / "telegram" / "20260724_1k5_orders_normalized.csv"
    pref: Counter[str] = Counter()
    if not csv1k5.exists():
        return {}
    for r in csv.DictReader(csv1k5.open(encoding="utf-8")):
        c = (r.get("order_code") or "").strip()
        m = re.match(r"^([A-Za-z]+)", c)
        if m:
            pref[m.group(1)] += 1
    return dict(pref)


def known_econtract(findings: list[dict[str, Any]]) -> None:
    known = {
        "hopdong_app_key_portal247": "e2b09bde-9b9d-41aa-bc89-7289eaff48ea",
        "hopdong_app_key_homepage_alt": "64046186-c1d1-4628-b2b7-1d1b6383c603",
        "user_sso_app_key": "8eabb4d5-19b4-478d-8d20-bc82452d50c7",
        "user_sso_client_id": "289326883003350022",
    }
    pending = ROOT / "secrets" / "ghn_sso_auth_code.pending"
    if pending.is_file():
        known["sso_auth_code_pending"] = pending.read_text(encoding="utf-8").strip()
    for k, v in known.items():
        if v:
            add(
                findings,
                kind="ghn_econtract_identity",
                source="known/staged",
                key=k,
                value=v,
                note="SSO/hopdongdientu identity",
                confidence="high",
            )


def extract_order_shop_ids() -> dict[str, list]:
    counts: dict[str, Counter] = {
        "shop_id": Counter(),
        "shop_partner_id": Counter(),
        "service_partner": Counter(),
    }
    for p in (ROOT / "quarantine" / "telegram").glob("orders_detailed*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = data if isinstance(data, list) else []
        if isinstance(data, dict) and not rows:
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    rows = v
                    break
        for row in rows:
            if not isinstance(row, dict):
                continue
            for f in counts:
                if row.get(f) not in (None, ""):
                    counts[f][str(row[f])] += 1
    return {k: v.most_common(50) for k, v in counts.items()}


def build_report() -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    scan_env(findings)
    known_econtract(findings)
    prefixes = scan_1k5_prefixes()
    if prefixes:
        add(
            findings,
            kind="1k5_oms_prefix",
            source="quarantine/telegram/20260724_1k5_orders_normalized.csv",
            key="order_code_prefixes",
            value=prefixes,
            note="OMS/partner prefixes — not GHN contract numbers",
            confidence="medium",
        )
    order_ids = extract_order_shop_ids()
    report: dict[str, Any] = {
        "ok": True,
        "module": "shipping_partner_contract_scan",
        "checked_at": utc_now(),
        "policy": "inventory-only · no dump-login · secrets masked",
        "1k5_oms_prefixes": prefixes,
        "order_export_partner_fields": order_ids,
        "findings": findings,
        "partner_inventory": {
            "ghn_econtract_sso": {
                "portal247_app_key": "e2b09bde-9b9d-41aa-bc89-7289eaff48ea",
                "user_app_key": "8eabb4d5-19b4-478d-8d20-bc82452d50c7",
                "client_id": "289326883003350022",
            },
            "oms_prefix_meaning": {
                k: OMS_PREFIX_MEANING.get(k, "?") for k in prefixes
            },
        },
        "verdict": (
            "⚠ Không có số hợp đồng đối tác vận chuyển rõ trong data local. "
            "Có SSO/hopdong app_key+client_id, shop_id 3PL owned, và prefix OMS trong 1k5."
        ),
        "next": [
            "Login owned hopdongdientu → export danh sách HĐ",
            "Hoặc gửi PDF/ảnh hợp đồng đối tác vận chuyển",
            "Token/printA5 shop GHN owned để lấy client_id/contract từ API shop",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "📜 Rà soát mã hợp đồng đối tác vận chuyển",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        "",
        "=== E-contract / SSO ===",
    ]
    inv = (report.get("partner_inventory") or {}).get("ghn_econtract_sso") or {}
    for k, v in inv.items():
        lines.append(f"  · {k}: {v}")
    lines.append("")
    lines.append("=== 1k5 OMS prefixes ===")
    meaning = (report.get("partner_inventory") or {}).get("oms_prefix_meaning") or {}
    for k, n in sorted(
        (report.get("1k5_oms_prefixes") or {}).items(), key=lambda x: -x[1]
    ):
        lines.append(f"  · {k} ×{n} → {meaning.get(k, '?')}")
    lines.append("")
    lines.append("=== Order export shop_id ===")
    for sid, n in ((report.get("order_export_partner_fields") or {}).get("shop_id") or [])[
        :10
    ]:
        lines.append(f"  · shop_id={sid} ×{n}")
    lines.append("")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "shipping_partner_contract_scan.json"
    tp = REPORTS / "shipping_partner_contract_scan.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan local data for shipping partner contract codes")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)
    report = build_report()
    paths = write_outputs(report)
    text = format_text(report)
    if args.notify:
        try:
            import os
            import urllib.request

            env = dict(os.environ)
            for path in (
                ROOT / "secrets" / "telegram.env",
                ROOT / "secrets" / "order_session.env",
            ):
                if not path.is_file():
                    continue
                for line in path.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.split("=", 1)
                        env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            token = env.get("TELEGRAM_BOT_TOKEN") or ""
            chat = env.get("TELEGRAM_CHAT_ID") or ""
            if token and chat:
                body = json.dumps({"chat_id": chat, "text": text[:3500]}).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    report["telegram"] = resp.status
        except Exception as e:  # noqa: BLE001
            report["telegram_error"] = str(e)[:160]
            write_outputs(report)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else text)
    print(f"\nWrote: {paths['txt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
