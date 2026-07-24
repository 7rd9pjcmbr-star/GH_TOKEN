#!/usr/bin/env python3
"""Mapper truy vấn đường ống dẫn các nhánh (kho · BC · backend).

Nhánh = pipe_source → channel → backend → buucuc → kho
Đọc kho_buucuc_pipe.db. Local · reports gitignored · không bắt buộc commit.
"""
from __future__ import annotations

import argparse
import json
import os
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

PIPE_CATALOG = {
    "buucuc_scan": {
        "title": "Quét bưu cục remote",
        "cli": "scan_buucuc_orders.py / nginx buucuc-scan",
        "role": "writer",
    },
    "oms_ingest": {
        "title": "OMS local ingest",
        "cli": "order_pipe_kho_buucuc_db.py ← oms_interconnect",
        "role": "writer",
    },
    "asunmee_upload": {
        "title": "Upload ASUNMEE detail",
        "cli": "owned upload → pipe",
        "role": "writer",
    },
    "realtime_cycle": {
        "title": "Realtime sync cycle",
        "cli": "realtime_order_sync.py",
        "role": "writer",
    },
}


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


def path_of(b: dict[str, Any]) -> str:
    return (
        f"{b['pipe_source']} → {b['channel']} → {b['backend']} → "
        f"buucuc:{b['buucuc']} → kho:{b['kho']}"
    )


def build_report() -> dict[str, Any]:
    if not PIPE_DB.is_file():
        return {"ok": False, "error": f"missing {PIPE_DB}", "checked_at": utc_now()}
    conn = sqlite3.connect(str(PIPE_DB))
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT
              COALESCE(pipe_source, '(null)') AS pipe_source,
              COALESCE(channel, '(null)') AS channel,
              COALESCE(source, '(null)') AS source,
              COALESCE(backend, '(null)') AS backend,
              COALESCE(buucuc, '(null)') AS buucuc,
              COALESCE(kho, '(null)') AS kho,
              COUNT(*) AS orders,
              COUNT(DISTINCT shop_id) AS shop_n,
              SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != '' THEN 1 ELSE 0 END)
                AS with_tracking
            FROM orders
            GROUP BY 1,2,3,4,5,6
            ORDER BY orders DESC
            """
        )
    ]
    conn.close()
    for b in rows:
        b["path"] = path_of(b)

    trunks: dict[str, Any] = defaultdict(
        lambda: {
            "orders": 0,
            "branches": [],
            "buucuc": Counter(),
            "kho": Counter(),
            "backend": Counter(),
            "channel": Counter(),
        }
    )
    for b in rows:
        t = trunks[b["pipe_source"]]
        t["orders"] += b["orders"]
        t["branches"].append(b)
        t["buucuc"][b["buucuc"]] += b["orders"]
        t["kho"][b["kho"]] += b["orders"]
        t["backend"][b["backend"]] += b["orders"]
        t["channel"][b["channel"]] += b["orders"]

    by_buucuc: dict[str, Any] = defaultdict(
        lambda: {
            "orders": 0,
            "pipes": Counter(),
            "kho": Counter(),
            "backends": Counter(),
            "paths": [],
        }
    )
    by_kho: dict[str, Any] = defaultdict(
        lambda: {
            "orders": 0,
            "pipes": Counter(),
            "buucuc": Counter(),
            "backends": Counter(),
            "paths": [],
        }
    )
    for b in rows:
        g = by_buucuc[b["buucuc"]]
        g["orders"] += b["orders"]
        g["pipes"][b["pipe_source"]] += b["orders"]
        g["kho"][b["kho"]] += b["orders"]
        g["backends"][b["backend"]] += b["orders"]
        g["paths"].append(b)
        h = by_kho[b["kho"]]
        h["orders"] += b["orders"]
        h["pipes"][b["pipe_source"]] += b["orders"]
        h["buucuc"][b["buucuc"]] += b["orders"]
        h["backends"][b["backend"]] += b["orders"]
        h["paths"].append(b)

    total = sum(b["orders"] for b in rows)
    trunk_list = []
    for ps, t in sorted(trunks.items(), key=lambda x: -x[1]["orders"]):
        meta = PIPE_CATALOG.get(ps, {"title": ps, "cli": "?", "role": "?"})
        trunk_list.append(
            {
                "pipe_source": ps,
                "title": meta.get("title"),
                "cli": meta.get("cli"),
                "role": meta.get("role"),
                "orders": t["orders"],
                "pct": round(100.0 * t["orders"] / max(total, 1), 1),
                "branch_n": len(t["branches"]),
                "buucuc": t["buucuc"].most_common(),
                "kho": t["kho"].most_common(),
                "backend": t["backend"].most_common(),
                "channel": t["channel"].most_common(),
                "paths": [x["path"] + f" ×{x['orders']}" for x in t["branches"][:12]],
            }
        )

    ml = ["```mermaid", "flowchart LR"]
    for t in trunk_list:
        tid = "T" + "".join(ch if ch.isalnum() else "" for ch in t["pipe_source"])[:16]
        ml.append(f"  {tid}[{t['pipe_source']} ×{t['orders']}]")
        for buu, n in t["buucuc"][:4]:
            bid = "B" + "".join(ch if ch.isalnum() else "" for ch in buu)[:14]
            ml.append(f"  {tid} -->|{n}| {bid}[BC:{buu[:16]}]")
    ml.append("```")

    return {
        "ok": True,
        "module": "pipe_branch_mapper",
        "checked_at": utc_now(),
        "policy": "local · reports gitignored",
        "atlas": "pipe_source → channel → backend → buucuc → kho",
        "db": str(PIPE_DB),
        "stats": {
            "orders": total,
            "branch_rows": len(rows),
            "trunks_pipe_source": len(trunks),
            "buucuc_tips": len(by_buucuc),
            "kho_tips": len(by_kho),
        },
        "trunks": trunk_list,
        "branches_top": rows[:40],
        "by_buucuc": [
            {
                "buucuc": buu,
                "orders": g["orders"],
                "pipes_in": g["pipes"].most_common(),
                "kho": g["kho"].most_common(),
                "backends": g["backends"].most_common(),
                "paths": [
                    x["path"] + f" ×{x['orders']}"
                    for x in sorted(g["paths"], key=lambda z: -z["orders"])[:8]
                ],
            }
            for buu, g in sorted(by_buucuc.items(), key=lambda x: -x[1]["orders"])
        ],
        "by_kho": [
            {
                "kho": kho,
                "orders": g["orders"],
                "pipes_in": g["pipes"].most_common(),
                "buucuc": g["buucuc"].most_common(),
                "backends": g["backends"].most_common(),
                "paths": [
                    x["path"] + f" ×{x['orders']}"
                    for x in sorted(g["paths"], key=lambda z: -z["orders"])[:8]
                ],
            }
            for kho, g in sorted(by_kho.items(), key=lambda x: -x[1]["orders"])
        ],
        "mermaid": "\n".join(ml),
        "verdict": (
            f"✅ Ống→nhánh: {len(trunks)} trunk · {len(rows)} nhánh · "
            f"{len(by_buucuc)} tip BC · {len(by_kho)} tip kho · orders={total}"
        ),
    }


def format_text(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"❌ {report.get('error')}"
    L: list[str] = []
    A = L.append
    A("🗺️ Mapper truy vấn đường ống dẫn các nhánh")
    A(f"Lúc: {report.get('checked_at')}")
    A(f"Verdict: {report.get('verdict')}")
    A(f"Atlas: {report.get('atlas')}")
    A("")
    A(f"=== Trunk ống ({len(report.get('trunks') or [])}) ===")
    for t in report.get("trunks") or []:
        A(f"  🔷 {t['pipe_source']} — {t.get('title')} ({t.get('pct')}%)")
        A(f"      orders={t['orders']} · nhánh={t['branch_n']} · cli={t.get('cli')}")
        A(f"      → BC: {', '.join(f'{k}×{n}' for k, n in (t.get('buucuc') or [])[:6])}")
        A(f"      → kho: {', '.join(f'{k}×{n}' for k, n in (t.get('kho') or [])[:6])}")
        for p in (t.get("paths") or [])[:6]:
            A(f"         · {p}")
        A("")
    A("=== Nhánh theo tip BƯU CỤC ===")
    for g in report.get("by_buucuc") or []:
        A(f"  🏛 BC [{g['buucuc']}] đơn={g['orders']}")
        A(f"      ống vào: {g.get('pipes_in')}")
        for p in (g.get("paths") or [])[:4]:
            A(f"         · {p}")
    A("")
    A("=== Nhánh theo tip KHO ===")
    for g in report.get("by_kho") or []:
        A(f"  🏬 kho [{g['kho']}] đơn={g['orders']}")
        A(f"      ống vào: {g.get('pipes_in')}")
        A(f"      BC: {', '.join(f'{k}×{n}' for k, n in (g.get('buucuc') or [])[:6])}")
    A("")
    A("=== Top nhánh đầy đủ ===")
    for i, b in enumerate((report.get("branches_top") or [])[:20], 1):
        A(
            f"  {i}. [{b['orders']}] {b.get('path')} · "
            f"shop×{b.get('shop_n')} track={b.get('with_tracking')}"
        )
    A("")
    A(report.get("mermaid") or "")
    A("")
    A("Git: không commit (reports gitignored)")
    return "\n".join(L)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "pipe_branch_mapper.json"
    tp = REPORTS / "pipe_branch_mapper.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def notify_telegram(text: str) -> list[int]:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return []
    statuses = []
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
    ap = argparse.ArgumentParser(description="Mapper đường ống dẫn các nhánh")
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
