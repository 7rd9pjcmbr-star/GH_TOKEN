#!/usr/bin/env python3
"""Mapper đường ống dẫn nối đến kho · sàn · cửa hàng.

Nhánh đọc:
  pipe_source → channel/sàn → backend → ĐVVC → kho → cửa hàng(shop)

Đọc kho_buucuc_pipe.db. Secrets-only · reports gitignored · không dump-login.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"

# Map channel / carrier / source → nhãn sàn
SAN_RULES: list[tuple[str, str]] = [
    (r"(?i)spx|shopee", "Sàn SPX/Shopee"),
    (r"(?i)pancake|remote_api|pos", "Sàn Pancake POS"),
    (r"(?i)asunmee|owned_upload", "Upload ASUNMEE"),
    (r"(?i)direct_api|inbox_csv|dang_giao", "Sàn/OMS CSV·direct"),
    (r"(?i)ghn", "Sàn/ĐVVC GHN"),
    (r"(?i)j\s*&?\s*t|jnt", "Sàn/ĐVVC J&T"),
    (r"(?i)vtp|viettel", "Sàn/ĐVVC VTP"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (
        SECRETS / "order_session.env",
        SECRETS / "backend_pipes.env",
        SECRETS / "telegram.env",
        ROOT / ".env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def classify_san(channel: str, carrier: str, buucuc: str, source: str, pipe: str) -> str:
    blob = " ".join(
        str(x or "") for x in (channel, carrier, buucuc, source, pipe)
    )
    for pat, label in SAN_RULES:
        if re.search(pat, blob):
            return label
    if channel and channel not in {"(null)", "(none)"}:
        return f"Sàn/channel:{channel}"
    return "Sàn:(chưa phân)"


def shop_label(shop_id: str | None, shop_name: str | None) -> str:
    sid = (shop_id or "").strip()
    name = (shop_name or "").strip()
    if name and sid:
        return f"{name} [{sid}]"
    if name:
        return name
    if sid:
        return f"(shop {sid})"
    return "(không shop)"


def kho_label(kho: str | None, warehouse_display: str | None) -> str:
    k = (kho or "").strip() or "(none)"
    wd = (warehouse_display or "").strip()
    if wd and wd != k and k in {"(none)", "(null)", "(csv_no_warehouse)"}:
        return wd
    if wd and wd != k:
        return f"{k} / {wd}"
    return k


def build_report() -> dict[str, Any]:
    if not PIPE_DB.is_file():
        return {"ok": False, "error": f"missing {PIPE_DB}", "checked_at": utc_now()}

    conn = sqlite3.connect(str(PIPE_DB))
    conn.row_factory = sqlite3.Row
    raw = [
        dict(r)
        for r in conn.execute(
            """
            SELECT
              COALESCE(pipe_source, '(null)') AS pipe_source,
              COALESCE(channel, '(null)') AS channel,
              COALESCE(source, '(null)') AS source,
              COALESCE(backend, '(null)') AS backend,
              COALESCE(buucuc, '(null)') AS buucuc,
              COALESCE(carrier, '(none)') AS carrier,
              COALESCE(kho, '(none)') AS kho,
              warehouse_display,
              shop_id,
              shop_name,
              COUNT(*) AS orders,
              SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != ''
                       THEN 1 ELSE 0 END) AS with_tracking
            FROM orders
            GROUP BY 1,2,3,4,5,6,7,8,9,10
            ORDER BY orders DESC
            """
        )
    ]
    total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()

    branches: list[dict[str, Any]] = []
    by_kho: dict[str, Any] = defaultdict(
        lambda: {
            "orders": 0,
            "shops": Counter(),
            "san": Counter(),
            "pipes": Counter(),
            "carriers": Counter(),
            "paths": [],
        }
    )
    by_san: dict[str, Any] = defaultdict(
        lambda: {
            "orders": 0,
            "kho": Counter(),
            "shops": Counter(),
            "pipes": Counter(),
            "carriers": Counter(),
            "paths": [],
        }
    )
    by_shop: dict[str, Any] = defaultdict(
        lambda: {
            "orders": 0,
            "shop_name": None,
            "kho": Counter(),
            "san": Counter(),
            "pipes": Counter(),
            "carriers": Counter(),
            "paths": [],
        }
    )

    for r in raw:
        san = classify_san(
            r["channel"], r["carrier"], r["buucuc"], r["source"], r["pipe_source"]
        )
        kho = kho_label(r.get("kho"), r.get("warehouse_display"))
        shop = shop_label(r.get("shop_id"), r.get("shop_name"))
        sid = str(r.get("shop_id") or "") or "(none)"
        path = (
            f"{r['pipe_source']} → {san} → {r['backend']} → "
            f"{r['carrier']}/{r['buucuc']} → kho:{kho} → CH:{shop}"
        )
        b = {
            **r,
            "san": san,
            "kho_tip": kho,
            "shop_tip": shop,
            "shop_key": sid,
            "path": path,
            "orders": int(r["orders"]),
            "with_tracking": int(r["with_tracking"] or 0),
        }
        branches.append(b)

        gk = by_kho[kho]
        gk["orders"] += b["orders"]
        gk["shops"][shop] += b["orders"]
        gk["san"][san] += b["orders"]
        gk["pipes"][r["pipe_source"]] += b["orders"]
        gk["carriers"][f"{r['carrier']}/{r['buucuc']}"] += b["orders"]
        gk["paths"].append(b)

        gs = by_san[san]
        gs["orders"] += b["orders"]
        gs["kho"][kho] += b["orders"]
        gs["shops"][shop] += b["orders"]
        gs["pipes"][r["pipe_source"]] += b["orders"]
        gs["carriers"][f"{r['carrier']}/{r['buucuc']}"] += b["orders"]
        gs["paths"].append(b)

        gch = by_shop[sid]
        gch["orders"] += b["orders"]
        gch["shop_name"] = r.get("shop_name") or gch["shop_name"]
        gch["kho"][kho] += b["orders"]
        gch["san"][san] += b["orders"]
        gch["pipes"][r["pipe_source"]] += b["orders"]
        gch["carriers"][f"{r['carrier']}/{r['buucuc']}"] += b["orders"]
        gch["paths"].append(b)

    # Mermaid: pipe → san → kho → shop (top edges)
    edge_san: Counter = Counter()
    edge_kho: Counter = Counter()
    edge_shop: Counter = Counter()
    for b in branches:
        edge_san[(b["pipe_source"], b["san"])] += b["orders"]
        edge_kho[(b["san"], b["kho_tip"])] += b["orders"]
        edge_shop[(b["kho_tip"], b["shop_tip"])] += b["orders"]

    def nid(s: str, prefix: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9]+", "_", s)[:40]
        return f"{prefix}_{clean}"

    ml = ["flowchart LR"]
    for (a, b), n in edge_san.most_common(12):
        ml.append(f'  {nid(a,"P")}["{a}"] -->|{n}| {nid(b,"S")}["{b}"]')
    for (a, b), n in edge_kho.most_common(12):
        ml.append(f'  {nid(a,"S")}["{a}"] -->|{n}| {nid(b,"K")}["kho:{b}"]')
    for (a, b), n in edge_shop.most_common(12):
        ml.append(f'  {nid(a,"K")}["kho:{a}"] -->|{n}| {nid(b,"C")}["{b[:36]}"]')

    report: dict[str, Any] = {
        "ok": True,
        "module": "pipe_kho_san_shop_mapper",
        "checked_at": utc_now(),
        "policy": "local pipe DB · no dump-login · reports gitignored",
        "atlas": "pipe_source → sàn → backend → ĐVVC → kho → cửa hàng",
        "stats": {
            "orders": total,
            "branches": len(branches),
            "kho_n": len(by_kho),
            "san_n": len(by_san),
            "shop_n": len(by_shop),
        },
        "by_kho": [
            {
                "kho": k,
                "orders": g["orders"],
                "pct": round(100.0 * g["orders"] / total, 1) if total else 0,
                "pipes": g["pipes"].most_common(),
                "san": g["san"].most_common(),
                "shops": g["shops"].most_common(8),
                "carriers": g["carriers"].most_common(8),
                "paths": [
                    f"{p['path']} ×{p['orders']}"
                    for p in sorted(g["paths"], key=lambda z: -z["orders"])[:6]
                ],
            }
            for k, g in sorted(by_kho.items(), key=lambda x: -x[1]["orders"])
        ],
        "by_san": [
            {
                "san": s,
                "orders": g["orders"],
                "pct": round(100.0 * g["orders"] / total, 1) if total else 0,
                "pipes": g["pipes"].most_common(),
                "kho": g["kho"].most_common(),
                "shops": g["shops"].most_common(8),
                "carriers": g["carriers"].most_common(8),
                "paths": [
                    f"{p['path']} ×{p['orders']}"
                    for p in sorted(g["paths"], key=lambda z: -z["orders"])[:6]
                ],
            }
            for s, g in sorted(by_san.items(), key=lambda x: -x[1]["orders"])
        ],
        "by_shop": [
            {
                "shop_id": sid,
                "shop_name": g.get("shop_name"),
                "orders": g["orders"],
                "pct": round(100.0 * g["orders"] / total, 1) if total else 0,
                "pipes": g["pipes"].most_common(),
                "san": g["san"].most_common(),
                "kho": g["kho"].most_common(),
                "carriers": g["carriers"].most_common(8),
                "paths": [
                    f"{p['path']} ×{p['orders']}"
                    for p in sorted(g["paths"], key=lambda z: -z["orders"])[:5]
                ],
            }
            for sid, g in sorted(by_shop.items(), key=lambda x: -x[1]["orders"])
        ],
        "branches_top": [
            {
                "orders": b["orders"],
                "path": b["path"],
                "pipe_source": b["pipe_source"],
                "san": b["san"],
                "kho": b["kho_tip"],
                "shop": b["shop_tip"],
                "with_tracking": b["with_tracking"],
            }
            for b in branches[:30]
        ],
        "mermaid": "\n".join(ml),
        "verdict": (
            f"✅ Ống→kho·sàn·CH: {len(by_kho)} kho · {len(by_san)} sàn · "
            f"{len(by_shop)} cửa hàng · {len(branches)} nhánh · orders={total}"
        ),
        "next": [
            "python3 scripts/pipe_kho_san_shop_mapper.py --notify",
            "python3 scripts/pipe_branch_mapper.py --notify",
            "Shop ngoài dashboard (1530618…): cần token đúng để nối sàn↔kho",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"❌ {report.get('error')}"
    L: list[str] = []
    A = L.append
    st = report.get("stats") or {}
    A("🗺️ ỐNG DẪN NỐI → KHO · SÀN · CỬA HÀNG")
    A(f"Lúc: {report.get('checked_at')}")
    A(f"Verdict: {report.get('verdict')}")
    A(f"Atlas: {report.get('atlas')}")
    A(
        f"Stats: orders={st.get('orders')} · nhánh={st.get('branches')} · "
        f"kho={st.get('kho_n')} · sàn={st.get('san_n')} · CH={st.get('shop_n')}"
    )
    A("")
    A("=== Theo SÀN (marketplace / channel) ===")
    for g in report.get("by_san") or []:
        A(f"  🛒 [{g['san']}] đơn={g['orders']} ({g['pct']}%)")
        A(f"      ống: {', '.join(f'{k}×{n}' for k,n in (g.get('pipes') or [])[:5])}")
        A(f"      → kho: {', '.join(f'{k}×{n}' for k,n in (g.get('kho') or [])[:6])}")
        A(f"      → CH: {', '.join(f'{k}×{n}' for k,n in (g.get('shops') or [])[:5])}")
        for p in (g.get("paths") or [])[:3]:
            A(f"         · {p}")
        A("")
    A("=== Theo KHO ===")
    for g in report.get("by_kho") or []:
        A(f"  🏬 kho [{g['kho']}] đơn={g['orders']} ({g['pct']}%)")
        A(f"      ống: {', '.join(f'{k}×{n}' for k,n in (g.get('pipes') or [])[:5])}")
        A(f"      ← sàn: {', '.join(f'{k}×{n}' for k,n in (g.get('san') or [])[:5])}")
        A(f"      → CH: {', '.join(f'{k}×{n}' for k,n in (g.get('shops') or [])[:5])}")
        A(f"      ĐVVC: {', '.join(f'{k}×{n}' for k,n in (g.get('carriers') or [])[:5])}")
        A("")
    A("=== Theo CỬA HÀNG ===")
    for g in report.get("by_shop") or []:
        name = g.get("shop_name") or g.get("shop_id")
        A(f"  🏪 {name} [{g.get('shop_id')}] đơn={g['orders']} ({g['pct']}%)")
        A(f"      ống: {', '.join(f'{k}×{n}' for k,n in (g.get('pipes') or [])[:4])}")
        A(f"      sàn: {', '.join(f'{k}×{n}' for k,n in (g.get('san') or [])[:4])}")
        A(f"      kho: {', '.join(f'{k}×{n}' for k,n in (g.get('kho') or [])[:5])}")
        for p in (g.get("paths") or [])[:2]:
            A(f"         · {p}")
        A("")
    A("=== Top nhánh đầy đủ (pipe→sàn→kho→CH) ===")
    for i, b in enumerate((report.get("branches_top") or [])[:18], 1):
        A(f"  {i}. [{b['orders']}] {b['path']} · track={b.get('with_tracking')}")
    A("")
    for n in report.get("next") or []:
        A(f"Next: {n}")
    return "\n".join(L)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "pipe_kho_san_shop_mapper.json"
    tp = REPORTS / "pipe_kho_san_shop_mapper.txt"
    mp = REPORTS / "pipe_kho_san_shop_mapper.mermaid.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = format_text(report)
    tp.write_text(text + "\n", encoding="utf-8")
    if report.get("mermaid"):
        mp.write_text(
            "```mermaid\n" + report["mermaid"] + "\n```\n", encoding="utf-8"
        )
    return {"json": str(jp), "txt": str(tp), "mermaid": str(mp)}


def notify_telegram(text: str) -> list[int]:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return []
    statuses: list[int] = []
    for i in range(0, min(len(text), 10500), 3500):
        chunk = text[i : i + 3500]
        if not chunk.strip():
            continue
        body = json.dumps({"chat_id": chat, "text": chunk}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            statuses.append(resp.status)
    return statuses


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ống dẫn nối kho · sàn · cửa hàng")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)
    report = build_report()
    paths = write_outputs(report)
    text = format_text(report)
    if args.notify:
        try:
            report["telegram"] = notify_telegram(text)
            write_outputs(report)
        except Exception as e:  # noqa: BLE001
            report["telegram_error"] = str(e)[:160]
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else text)
    print(f"\nWrote: {paths['txt']}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
