#!/usr/bin/env python3
"""Rà soát đường ống dẫn đơn đặt hàng từ sàn TMDT.

Đối chiếu catalog sàn TMDT VN với kho_buucuc_pipe.db:
  sàn TMDT → pipe_source → channel → backend → ĐVVC → kho → cửa hàng

Chỉ đọc local + secrets owned. Không dump-login / không stealer login.
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
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"

# Sàn TMDT trọng điểm + tín hiệu nhận diện trong pipe
TMDT_SAN: list[dict[str, Any]] = [
    {
        "id": "shopee_spx",
        "name": "Shopee / SPX",
        "kind": "marketplace",
        "owned_env": ["SHOPEE_TOKEN", "SPX_TOKEN"],
        "match": lambda r: bool(
            re.search(r"(?i)spx|shopee", " ".join(_blob(r)))
            or str(r.get("channel") or "") == "spx_local"
            or int(r.get("spx26_n") or 0) > 0
        ),
    },
    {
        "id": "lazada",
        "name": "Lazada VN",
        "kind": "marketplace",
        "owned_env": ["LAZADA_TOKEN", "LAZADA_APP_KEY"],
        "match": lambda r: bool(re.search(r"(?i)lazada|\blex\b", " ".join(_blob(r)))),
    },
    {
        "id": "tiktokshop",
        "name": "TikTok Shop VN",
        "kind": "marketplace",
        "owned_env": ["TIKTOK_SHOP_TOKEN"],
        "match": lambda r: bool(re.search(r"(?i)tiktok|tts", " ".join(_blob(r)))),
    },
    {
        "id": "tiki",
        "name": "Tiki",
        "kind": "marketplace",
        "owned_env": ["TIKI_TOKEN"],
        "match": lambda r: bool(re.search(r"(?i)\btiki\b", " ".join(_blob(r)))),
    },
    {
        "id": "sendo",
        "name": "Sendo",
        "kind": "marketplace",
        "owned_env": ["SENDO_TOKEN"],
        "match": lambda r: bool(re.search(r"(?i)sendo", " ".join(_blob(r)))),
    },
    {
        "id": "pancake_pos",
        "name": "Pancake POS (gom đơn MXH/TMDT)",
        "kind": "pos_oms",
        "owned_env": [
            "PANCAKE_POS_ACCESS_TOKEN",
            "PANCAKE_POS_API_KEY",
            "PANCAKE_API_KEY",
        ],
        "match": lambda r: bool(
            re.search(r"(?i)pancake|remote_api|pages\.fm", " ".join(_blob(r)))
            or str(r.get("carrier") or "") == "Pancake"
            or str(r.get("buucuc") or "") == "Pancake"
            or str(r.get("channel") or "") in {"remote_api", "pancake_payload"}
        ),
    },
    {
        "id": "multi_platform_upload",
        "name": "Multi-platform Telegram upload",
        "kind": "ingest",
        "owned_env": ["TELEGRAM_BOT_TOKEN"],
        "match": lambda r: bool(
            re.search(r"(?i)multi_platform|telegram_upload", " ".join(_blob(r)))
        ),
    },
]


def _blob(r: dict[str, Any]) -> list[str]:
    return [
        str(r.get(k) or "")
        for k in (
            "channel",
            "carrier",
            "buucuc",
            "source",
            "pipe_source",
            "backend",
            "shop_name",
            "kho",
            "file",
        )
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


def env_present(keys: list[str], env: dict[str, str]) -> dict[str, bool]:
    return {k: bool((env.get(k) or "").strip()) for k in keys}


def shop_label(shop_id: Any, shop_name: Any) -> str:
    sid = str(shop_id or "").strip()
    name = str(shop_name or "").strip()
    if name and sid:
        return f"{name} [{sid}]"
    if name:
        return name
    if sid:
        return f"(shop {sid})"
    return "(không shop)"


def classify_row(r: dict[str, Any]) -> list[str]:
    """Một đơn có thể khớp nhiều sàn (vd. Pancake + SPX). Ưu tiên marketplace cụ thể trước POS."""
    hits: list[str] = []
    for san in TMDT_SAN:
        try:
            if san["match"](r):
                hits.append(san["id"])
        except Exception:  # noqa: BLE001
            continue
    return hits


def build_report() -> dict[str, Any]:
    env = load_env()
    if not PIPE_DB.is_file():
        return {"ok": False, "error": f"missing {PIPE_DB}", "checked_at": utc_now()}

    conn = sqlite3.connect(str(PIPE_DB))
    conn.row_factory = sqlite3.Row
    total = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
    # aggregate at path grain then classify
    raw = [
        dict(r)
        for r in conn.execute(
            """
            SELECT
              COALESCE(pipe_source,'(null)') AS pipe_source,
              COALESCE(channel,'(null)') AS channel,
              COALESCE(source,'(null)') AS source,
              COALESCE(backend,'(null)') AS backend,
              COALESCE(buucuc,'(null)') AS buucuc,
              COALESCE(carrier,'(none)') AS carrier,
              COALESCE(kho,'(none)') AS kho,
              shop_id,
              shop_name,
              COUNT(*) AS orders,
              SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != '' THEN 1 ELSE 0 END)
                AS with_tracking,
              SUM(CASE WHEN tracking_code LIKE '26%' THEN 1 ELSE 0 END) AS spx26_n
            FROM orders
            GROUP BY 1,2,3,4,5,6,7,8,9
            ORDER BY orders DESC
            """
        )
    ]
    # also count order-level SPX26 for accuracy
    spx26_orders = int(
        conn.execute(
            "SELECT COUNT(*) FROM orders WHERE tracking_code LIKE '26%'"
        ).fetchone()[0]
    )
    conn.close()

    # per-san buckets
    by_san: dict[str, Any] = {
        s["id"]: {
            "id": s["id"],
            "name": s["name"],
            "kind": s["kind"],
            "owned_env": s["owned_env"],
            "env_status": env_present(s["owned_env"], env),
            "orders": 0,
            "with_tracking": 0,
            "pipes": Counter(),
            "channels": Counter(),
            "backends": Counter(),
            "kho": Counter(),
            "shops": Counter(),
            "carriers": Counter(),
            "paths": [],
            "status": "missing",  # missing | live | partial
        }
        for s in TMDT_SAN
    }

    classified_orders = 0
    unclassified = 0
    branches: list[dict[str, Any]] = []

    for r in raw:
        hits = classify_row(r)
        n = int(r["orders"])
        path = (
            f"{r['pipe_source']} → {r['channel']} → {r['backend']} → "
            f"{r['carrier']}/{r['buucuc']} → kho:{r['kho']} → "
            f"{shop_label(r.get('shop_id'), r.get('shop_name'))}"
        )
        b = {
            **r,
            "orders": n,
            "with_tracking": int(r["with_tracking"] or 0),
            "tmdt_hits": hits,
            "path": path,
        }
        branches.append(b)
        if not hits:
            unclassified += n
            continue
        classified_orders += n
        # attribute full order count to each hit (overlap ok for POS+SPX)
        # but for marketplace-only stats, primary = first marketplace hit else first
        primary = next(
            (
                h
                for h in hits
                if next(s for s in TMDT_SAN if s["id"] == h)["kind"] == "marketplace"
            ),
            hits[0],
        )
        for hid in hits:
            g = by_san[hid]
            # avoid double-count POS when also SPX: still count both for presence,
            # but only add orders once per san id
            g["orders"] += n
            g["with_tracking"] += int(r["with_tracking"] or 0)
            g["pipes"][r["pipe_source"]] += n
            g["channels"][r["channel"]] += n
            g["backends"][r["backend"]] += n
            g["kho"][r["kho"]] += n
            g["shops"][shop_label(r.get("shop_id"), r.get("shop_name"))] += n
            g["carriers"][f"{r['carrier']}/{r['buucuc']}"] += n
            g["paths"].append({"path": path, "orders": n, "primary": hid == primary})

    # status
    for sid, g in by_san.items():
        has_env = any(g["env_status"].values()) if g["owned_env"] else False
        if g["orders"] > 0 and has_env:
            g["status"] = "live"
        elif g["orders"] > 0:
            g["status"] = "partial_no_owned_env"
        elif has_env:
            g["status"] = "env_ready_no_orders"
        else:
            g["status"] = "missing"

    # serialize counters
    san_list = []
    for s in TMDT_SAN:
        g = by_san[s["id"]]
        san_list.append(
            {
                "id": g["id"],
                "name": g["name"],
                "kind": g["kind"],
                "status": g["status"],
                "orders": g["orders"],
                "pct": round(100.0 * g["orders"] / total, 1) if total else 0,
                "with_tracking": g["with_tracking"],
                "owned_env": g["owned_env"],
                "env_status": g["env_status"],
                "pipes": g["pipes"].most_common(),
                "channels": g["channels"].most_common(),
                "backends": g["backends"].most_common(),
                "kho": g["kho"].most_common(8),
                "shops": g["shops"].most_common(8),
                "carriers": g["carriers"].most_common(8),
                "paths": [
                    f"{p['path']} ×{p['orders']}"
                    for p in sorted(g["paths"], key=lambda z: -z["orders"])[:8]
                ],
            }
        )

    live = [s for s in san_list if s["status"] == "live"]
    partial = [s for s in san_list if s["status"] == "partial_no_owned_env"]
    env_ready = [s for s in san_list if s["status"] == "env_ready_no_orders"]
    missing = [s for s in san_list if s["status"] == "missing"]
    marketplace = [s for s in san_list if s["kind"] == "marketplace"]

    # gaps / findings
    findings: list[str] = []
    for s in marketplace:
        if s["orders"] == 0:
            findings.append(
                f"SÓT ỐNG: {s['name']} — chưa có đơn trong pipe "
                f"(env={'có' if any(s['env_status'].values()) else 'thiếu'})"
            )
    if by_san["shopee_spx"]["orders"] > 0:
        findings.append(
            f"SPX/Shopee live={by_san['shopee_spx']['orders']} · "
            f"tracking 26*≈{spx26_orders} — ống buucuc_scan/oms_ingest→SPX→kho"
        )
    if by_san["pancake_pos"]["orders"] > 0:
        findings.append(
            f"Pancake POS gom {by_san['pancake_pos']['orders']} đơn "
            "(MXH/TMDT qua POS — không tách được sàn gốc nếu thiếu partner tag)"
        )
    if unclassified:
        findings.append(f"Không gắn sàn TMDT catalog: {unclassified} đơn")

    report: dict[str, Any] = {
        "ok": True,
        "module": "tmdt_order_pipe_audit_mapper",
        "checked_at": utc_now(),
        "policy": "owned-only · no dump-login · reports gitignored",
        "atlas": "sàn TMDT → pipe_source → channel → backend → ĐVVC → kho → cửa hàng",
        "stats": {
            "orders_total": total,
            "branches": len(branches),
            "classified_orders": classified_orders,
            "unclassified_orders": unclassified,
            "spx26_tracking": spx26_orders,
            "san_live": len(live),
            "san_partial": len(partial),
            "san_env_ready": len(env_ready),
            "san_missing": len(missing),
            "marketplace_with_orders": sum(1 for s in marketplace if s["orders"] > 0),
            "marketplace_total": len(marketplace),
        },
        "by_san": san_list,
        "live": [{"id": s["id"], "name": s["name"], "orders": s["orders"]} for s in live],
        "missing_marketplace": [
            {"id": s["id"], "name": s["name"], "env_status": s["env_status"]}
            for s in marketplace
            if s["orders"] == 0
        ],
        "findings": findings,
        "branches_tmdt_top": [
            {
                "orders": b["orders"],
                "tmdt": b["tmdt_hits"],
                "path": b["path"],
                "with_tracking": b["with_tracking"],
            }
            for b in branches
            if b["tmdt_hits"]
        ][:25],
        "verdict": (
            f"✅ Rà ống TMDT: marketplace có đơn="
            f"{sum(1 for s in marketplace if s['orders'] > 0)}/{len(marketplace)} · "
            f"live={len(live)} · missing={len(missing)} · "
            f"SPX={by_san['shopee_spx']['orders']} · Pancake={by_san['pancake_pos']['orders']} · "
            f"orders={total}"
        ),
        "next": [
            "Bật ống Lazada/TikTok/Tiki/Sendo khi có token owned tương ứng",
            "SPX: python3 scripts/pipe_kho_san_shop_mapper.py --notify",
            "Pancake multi-shop: python3 scripts/multi_shops_comprehensive_mapper.py --notify",
            "Catalog icon: python3 scripts/tmdt_vn_icon_order_mapper.py",
            "python3 scripts/tmdt_order_pipe_audit_mapper.py --notify",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"❌ {report.get('error')}"
    L: list[str] = []
    A = L.append
    st = report.get("stats") or {}
    A("🔎 RÀ SOÁT ỐNG DẪN ĐƠN TỪ SÀN TMDT")
    A(f"Lúc: {report.get('checked_at')}")
    A(f"Verdict: {report.get('verdict')}")
    A(f"Atlas: {report.get('atlas')}")
    A(
        f"Stats: total={st.get('orders_total')} · classified≈{st.get('classified_orders')} · "
        f"unclassified={st.get('unclassified_orders')} · "
        f"marketplace_live={st.get('marketplace_with_orders')}/{st.get('marketplace_total')} · "
        f"SPX26*={st.get('spx26_tracking')}"
    )
    A("")
    A("=== Theo sàn TMDT ===")
    for s in report.get("by_san") or []:
        env_ok = sum(1 for v in (s.get("env_status") or {}).values() if v)
        env_n = len(s.get("owned_env") or [])
        mark = {
            "live": "✅",
            "partial_no_owned_env": "⚠",
            "env_ready_no_orders": "🔑",
            "missing": "❌",
        }.get(s.get("status") or "", "·")
        A(
            f"  {mark} [{s.get('kind')}] {s.get('name')} · "
            f"status={s.get('status')} · đơn={s.get('orders')} ({s.get('pct')}%) · "
            f"track={s.get('with_tracking')} · env={env_ok}/{env_n}"
        )
        if s.get("orders"):
            A(f"      ống: {', '.join(f'{k}×{n}' for k,n in (s.get('pipes') or [])[:4])}")
            A(f"      channel: {', '.join(f'{k}×{n}' for k,n in (s.get('channels') or [])[:4])}")
            A(f"      → kho: {', '.join(f'{k}×{n}' for k,n in (s.get('kho') or [])[:5])}")
            A(f"      → CH: {', '.join(f'{k}×{n}' for k,n in (s.get('shops') or [])[:4])}")
            for p in (s.get("paths") or [])[:3]:
                A(f"         · {p}")
        elif s.get("status") == "missing":
            miss = [k for k, v in (s.get("env_status") or {}).items() if not v]
            if miss:
                A(f"      thiếu env: {', '.join(miss)}")
        A("")

    A("=== Sàn marketplace CHƯA có ống đơn ===")
    miss = report.get("missing_marketplace") or []
    if not miss:
        A("  (không — mọi marketplace catalog đều có tín hiệu)")
    for s in miss:
        A(f"  ❌ {s.get('name')} ({s.get('id')})")

    A("")
    A("=== Findings ===")
    for f in report.get("findings") or []:
        A(f"  · {f}")

    A("")
    A("=== Top nhánh TMDT ===")
    for i, b in enumerate((report.get("branches_tmdt_top") or [])[:15], 1):
        A(
            f"  {i}. [{b['orders']}] {','.join(b.get('tmdt') or [])} · "
            f"{b['path']} · track={b.get('with_tracking')}"
        )
    A("")
    for n in report.get("next") or []:
        A(f"Next: {n}")
    return "\n".join(L)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "tmdt_order_pipe_audit.json"
    tp = REPORTS / "tmdt_order_pipe_audit.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


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
    ap = argparse.ArgumentParser(description="Rà soát ống đơn từ sàn TMDT")
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
