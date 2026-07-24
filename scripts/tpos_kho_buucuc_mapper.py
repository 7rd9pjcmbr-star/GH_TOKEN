#!/usr/bin/env python3
"""Mapper TPOS ↔ kho · bưu cục.

Ống mục tiêu:
  TPOS OData GetViewDelivery → OMS-pipe-bus → kho → bưu cục/ĐVVC → tracking

Đối chiếu:
  - Probe owned TPOS_BASE_URL + TPOS_ACCESS_TOKEN (không dump-login)
  - Atlas kho×buucuc từ kho_buucuc_pipe.db
  - Slot đấu nối TPOS vào từng tip kho / buucuc (hiện thiếu token → blocked)

Reports gitignored · secrets-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
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
BUUCUC_DB = REPORTS / "buucuc_backend.db"

# Endpoint TPOS thường dùng cho đơn giao
TPOS_ENDPOINTS = [
    {
        "id": "odata_root",
        "path": "/odata",
        "role": "probe",
        "mapper_node": "TPOS → OMS auth",
    },
    {
        "id": "get_view_delivery",
        "path": "/odata/FastSaleOrder/ODataService.GetViewDelivery",
        "role": "delivery_view",
        "mapper_node": "TPOS delivery → kho/buucuc ingest",
    },
    {
        "id": "sale_orders",
        "path": "/odata/FastSaleOrder",
        "role": "sale_orders",
        "mapper_node": "TPOS sale → OMS-pipe-bus",
    },
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
    try:
        from owned_credentials import env_overlay_from_owned

        env = env_overlay_from_owned(env)
    except Exception:  # noqa: BLE001
        pass
    return env


def http_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 25,
) -> tuple[int, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return int(resp.status), json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return int(resp.status), {"raw": raw[:300]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            return int(e.code), json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return int(e.code), {"raw": raw[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:200]}


def probe_tpos(env: dict[str, str]) -> dict[str, Any]:
    base = (env.get("TPOS_BASE_URL") or "").rstrip("/")
    token = (env.get("TPOS_ACCESS_TOKEN") or "").strip()
    user = (env.get("TPOS_USER") or "").strip()
    shop = (env.get("TPOS_SHOP_ID") or "").strip()
    meta = {
        "base_url_set": bool(base),
        "token_set": bool(token),
        "user_set": bool(user),
        "shop_id_set": bool(shop),
        "base_host": urllib.parse.urlparse(base).netloc if base else None,
        "shop_id": shop or None,
        "user": user or None,
    }
    if not base or not token:
        return {
            "status": "missing_cred",
            "detail": "Thiếu TPOS_BASE_URL + TPOS_ACCESS_TOKEN owned",
            "http": None,
            "endpoints": [],
            **meta,
        }

    headers = {"Authorization": f"Bearer {token}"}
    endpoints: list[dict[str, Any]] = []
    best_status = "error"
    for ep in TPOS_ENDPOINTS:
        url = base + ep["path"]
        # GetViewDelivery often needs POST/params — GET probe only
        code, body = http_json(url, headers=headers)
        st = "ok"
        if code in (401, 403):
            st = "auth_fail"
        elif code == 0:
            st = "error"
        elif code == 404:
            st = "not_found"
        elif code >= 500:
            st = "error"
        elif code in (400, 405):
            st = "reachable"  # endpoint exists, method/params differ
        elif 200 <= code < 300:
            st = "ok"
        else:
            st = f"http_{code}"
        endpoints.append(
            {
                "id": ep["id"],
                "path": ep["path"],
                "role": ep["role"],
                "mapper_node": ep["mapper_node"],
                "http": code,
                "status": st,
                "body_keys": list(body.keys())[:12] if isinstance(body, dict) else type(body).__name__,
            }
        )
        if st in {"ok", "reachable"} and best_status not in {"ok"}:
            best_status = st
        if st == "auth_fail":
            best_status = "auth_fail"

    ok_n = sum(1 for e in endpoints if e["status"] in {"ok", "reachable"})
    detail = f"endpoints_ok={ok_n}/{len(endpoints)}"
    if best_status == "auth_fail":
        detail = "Bearer TPOS fail · " + detail
    return {
        "status": best_status if ok_n or best_status == "auth_fail" else "error",
        "detail": detail,
        "http": endpoints[0]["http"] if endpoints else None,
        "endpoints": endpoints,
        **meta,
    }


def load_kho_buucuc_atlas() -> dict[str, Any]:
    if not PIPE_DB.is_file():
        return {"ok": False, "error": f"missing {PIPE_DB}"}
    conn = sqlite3.connect(str(PIPE_DB))
    conn.row_factory = sqlite3.Row
    total = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])

    matrix: list[dict[str, Any]] = []
    for r in conn.execute(
        """
        SELECT
          COALESCE(kho, '(none)') AS kho,
          COALESCE(buucuc, '(none)') AS buucuc,
          COALESCE(backend, '(none)') AS backend,
          COALESCE(pipe_source, '(none)') AS pipe_source,
          COUNT(*) AS orders,
          COUNT(DISTINCT shop_id) AS shops,
          SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != '' THEN 1 ELSE 0 END)
            AS with_tracking
        FROM orders
        GROUP BY 1,2,3,4
        ORDER BY orders DESC
        """
    ):
        matrix.append(dict(r))

    by_kho: dict[str, Any] = defaultdict(
        lambda: {
            "orders": 0,
            "buucuc": Counter(),
            "backends": Counter(),
            "pipes": Counter(),
            "shops": Counter(),
        }
    )
    by_buucuc: dict[str, Any] = defaultdict(
        lambda: {
            "orders": 0,
            "kho": Counter(),
            "backends": Counter(),
            "pipes": Counter(),
        }
    )

    for r in conn.execute(
        """
        SELECT kho, buucuc, backend, pipe_source, shop_id, shop_name, COUNT(*) AS n
        FROM orders
        GROUP BY 1,2,3,4,5,6
        """
    ):
        kho = r["kho"] or "(none)"
        buu = r["buucuc"] or "(none)"
        n = int(r["n"])
        gk = by_kho[kho]
        gk["orders"] += n
        gk["buucuc"][buu] += n
        gk["backends"][r["backend"] or "(none)"] += n
        gk["pipes"][r["pipe_source"] or "(none)"] += n
        shop = (r["shop_name"] or r["shop_id"] or "(no_shop)")
        gk["shops"][str(shop)] += n

        gb = by_buucuc[buu]
        gb["orders"] += n
        gb["kho"][kho] += n
        gb["backends"][r["backend"] or "(none)"] += n
        gb["pipes"][r["pipe_source"] or "(none)"] += n

    # contracts mentioning TPOS if any
    tpos_contracts: list[dict[str, Any]] = []
    for path in (PIPE_DB, BUUCUC_DB):
        if not path.is_file():
            continue
        try:
            c2 = sqlite3.connect(str(path))
            tables = {
                x[0]
                for x in c2.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "contracts" not in tables:
                c2.close()
                continue
            c2.row_factory = sqlite3.Row
            for row in c2.execute("SELECT * FROM contracts"):
                d = dict(row)
                blob = " ".join(str(d.get(k) or "") for k in d)
                if re.search(r"(?i)tpos", blob):
                    tpos_contracts.append(
                        {
                            "source_db": path.name,
                            "backend": d.get("backend"),
                            "buucuc": d.get("buucuc"),
                            "carrier": d.get("carrier"),
                            "shop_id": d.get("shop_id"),
                            "partner_name": d.get("partner_name"),
                        }
                    )
            c2.close()
        except Exception:  # noqa: BLE001
            continue

    conn.close()
    return {
        "ok": True,
        "orders": total,
        "matrix_n": len(matrix),
        "matrix_top": matrix[:40],
        "by_kho": [
            {
                "kho": k,
                "orders": g["orders"],
                "pct": round(100.0 * g["orders"] / total, 1) if total else 0,
                "buucuc": g["buucuc"].most_common(8),
                "backends": g["backends"].most_common(),
                "pipes": g["pipes"].most_common(),
                "shops": g["shops"].most_common(6),
                "tpos_slot": (
                    "ready_to_ingest"
                    if k not in {"(none)", "(csv_no_warehouse)"}
                    else "needs_warehouse_map"
                ),
            }
            for k, g in sorted(by_kho.items(), key=lambda x: -x[1]["orders"])
        ],
        "by_buucuc": [
            {
                "buucuc": b,
                "orders": g["orders"],
                "pct": round(100.0 * g["orders"] / total, 1) if total else 0,
                "kho": g["kho"].most_common(8),
                "backends": g["backends"].most_common(),
                "pipes": g["pipes"].most_common(),
                "tpos_join": (
                    "via_OMS-pipe-bus"
                    if "OMS-pipe-bus" in dict(g["backends"])
                    else "via_" + (g["backends"].most_common(1)[0][0] if g["backends"] else "unknown")
                ),
            }
            for b, g in sorted(by_buucuc.items(), key=lambda x: -x[1]["orders"])
        ],
        "tpos_contracts": tpos_contracts,
    }


def build_pipe_plan(tpos: dict[str, Any], atlas: dict[str, Any]) -> list[dict[str, Any]]:
    """Kế hoạch đấu nối TPOS → từng lớp kho/buucuc."""
    status = tpos.get("status")
    live = status in {"ok", "reachable", "connected"}
    plans = [
        {
            "id": "P1",
            "path": "TPOS OData → OMS-pipe-bus → kho_* → buucuc_*",
            "status": "live" if live else "blocked_missing_or_auth",
            "action": (
                "Ingest GetViewDelivery vào pipe_source=tpos_odata"
                if live
                else "Điền TPOS_BASE_URL + TPOS_ACCESS_TOKEN owned rồi probe lại"
            ),
        },
        {
            "id": "P2",
            "path": "TPOS delivery.Warehouse → kho tip (ASUMEE / Kho mặc định / Kho HCM…)",
            "status": "mapped_slots",
            "slots": [
                {"kho": k["kho"], "orders_now": k["orders"], "slot": k["tpos_slot"]}
                for k in (atlas.get("by_kho") or [])[:8]
            ],
        },
        {
            "id": "P3",
            "path": "TPOS Carrier/Partner → buucuc tip (J&T/GHN/SPX/Pancake…)",
            "status": "mapped_slots",
            "slots": [
                {
                    "buucuc": b["buucuc"],
                    "orders_now": b["orders"],
                    "join": b["tpos_join"],
                }
                for b in (atlas.get("by_buucuc") or [])[:10]
            ],
        },
        {
            "id": "P4",
            "path": "TPOS tracking → tracking.aship / pipe tracking_code",
            "status": "ready_no_auth",
            "action": "Sau khi ingest: attach aship URL theo mã VĐ",
        },
    ]
    return plans


def build_report() -> dict[str, Any]:
    env = load_env()
    tpos = probe_tpos(env)
    atlas = load_kho_buucuc_atlas()
    if not atlas.get("ok"):
        return {
            "ok": False,
            "error": atlas.get("error"),
            "checked_at": utc_now(),
            "tpos": tpos,
        }

    plans = build_pipe_plan(tpos, atlas)
    tpos_orders_in_pipe = 0
    # any existing tpos-tagged rows?
    try:
        conn = sqlite3.connect(str(PIPE_DB))
        tpos_orders_in_pipe = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM orders
                WHERE lower(coalesce(backend,'')) LIKE '%tpos%'
                   OR lower(coalesce(channel,'')) LIKE '%tpos%'
                   OR lower(coalesce(source,'')) LIKE '%tpos%'
                   OR lower(coalesce(pipe_source,'')) LIKE '%tpos%'
                """
            ).fetchone()[0]
        )
        conn.close()
    except Exception:  # noqa: BLE001
        pass

    report: dict[str, Any] = {
        "ok": True,
        "module": "tpos_kho_buucuc_mapper",
        "checked_at": utc_now(),
        "policy": "owned TPOS only · no dump-login · reports gitignored",
        "atlas": "TPOS OData → OMS-pipe-bus → kho → bưu cục → tracking",
        "tpos": tpos,
        "tpos_orders_in_pipe": tpos_orders_in_pipe,
        "kho_buucuc": {
            "orders": atlas["orders"],
            "kho_n": len(atlas["by_kho"]),
            "buucuc_n": len(atlas["by_buucuc"]),
            "by_kho": atlas["by_kho"],
            "by_buucuc": atlas["by_buucuc"],
            "matrix_top": atlas["matrix_top"][:25],
            "tpos_contracts": atlas.get("tpos_contracts") or [],
        },
        "pipe_plans": plans,
        "verdict": (
            f"🗺 Mapper TPOS×kho×BC: TPOS={tpos.get('status')} · "
            f"pipe_tpos_orders={tpos_orders_in_pipe} · "
            f"kho={len(atlas['by_kho'])} · buucuc={len(atlas['by_buucuc'])} · "
            f"orders={atlas['orders']}"
        ),
        "next": [
            "Điền secrets/backend_pipes.env: TPOS_BASE_URL + TPOS_ACCESS_TOKEN (+ TPOS_SHOP_ID)",
            "python3 scripts/tpos_kho_buucuc_mapper.py --notify",
            "python3 scripts/oms_interconnect.py --once --notify",
            "Sau khi TPOS live: ingest GetViewDelivery → pipe_source=tpos_odata",
            "python3 scripts/pipe_kho_san_shop_mapper.py --notify",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"❌ {report.get('error')}"
    L: list[str] = []
    A = L.append
    tpos = report.get("tpos") or {}
    kb = report.get("kho_buucuc") or {}
    A("🗺 MAPPER TPOS ↔ KHO · BƯU CỤC")
    A(f"Lúc: {report.get('checked_at')}")
    A(f"Verdict: {report.get('verdict')}")
    A(f"Atlas: {report.get('atlas')}")
    A("")
    A("=== TPOS probe ===")
    mark = {
        "ok": "✅",
        "reachable": "✅",
        "connected": "✅",
        "missing_cred": "🔒",
        "auth_fail": "❌",
        "error": "❌",
    }.get(tpos.get("status") or "", "·")
    A(f"  {mark} status={tpos.get('status')} · {tpos.get('detail')}")
    A(
        f"  base={tpos.get('base_host') or '∅'} · token="
        f"{'SET' if tpos.get('token_set') else 'MISSING'} · "
        f"user={'SET' if tpos.get('user_set') else '∅'} · "
        f"shop={tpos.get('shop_id') or '∅'}"
    )
    A(f"  orders tagged TPOS trong pipe: {report.get('tpos_orders_in_pipe')}")
    for ep in tpos.get("endpoints") or []:
        A(
            f"      · [{ep.get('status')}] http={ep.get('http')} "
            f"{ep.get('path')} — {ep.get('mapper_node')}"
        )
    A("")
    A(
        f"=== Atlas kho×buucuc (pipe orders={kb.get('orders')} · "
        f"kho={kb.get('kho_n')} · BC={kb.get('buucuc_n')}) ==="
    )
    A("--- Theo KHO ---")
    for g in (kb.get("by_kho") or [])[:10]:
        A(
            f"  🏬 [{g['kho']}] đơn={g['orders']} ({g['pct']}%) · "
            f"TPOS_slot={g.get('tpos_slot')}"
        )
        A(f"      → BC: {', '.join(f'{k}×{n}' for k,n in (g.get('buucuc') or [])[:5])}")
        A(f"      backend: {', '.join(f'{k}×{n}' for k,n in (g.get('backends') or [])[:4])}")
    A("")
    A("--- Theo BƯU CỤC ---")
    for g in (kb.get("by_buucuc") or [])[:12]:
        A(
            f"  🏛 [{g['buucuc']}] đơn={g['orders']} ({g['pct']}%) · "
            f"join={g.get('tpos_join')}"
        )
        A(f"      ← kho: {', '.join(f'{k}×{n}' for k,n in (g.get('kho') or [])[:5])}")
    A("")
    A("=== Ma trận kho×BC×backend (top) ===")
    for m in (kb.get("matrix_top") or [])[:15]:
        A(
            f"  · [{m.get('orders')}] kho:{m.get('kho')} × BC:{m.get('buucuc')} × "
            f"{m.get('backend')}/{m.get('pipe_source')} · "
            f"shop×{m.get('shops')} track={m.get('with_tracking')}"
        )
    A("")
    A("=== Kế hoạch ống TPOS → kho/BC ===")
    for p in report.get("pipe_plans") or []:
        A(f"  ▶ {p.get('id')} [{p.get('status')}] {p.get('path')}")
        if p.get("action"):
            A(f"      → {p.get('action')}")
        for s in (p.get("slots") or [])[:5]:
            A(f"      · {s}")
    if kb.get("tpos_contracts"):
        A("")
        A("=== Contracts có TPOS ===")
        for c in kb["tpos_contracts"][:10]:
            A(f"  · {c}")
    A("")
    for n in report.get("next") or []:
        A(f"Next: {n}")
    return "\n".join(L)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "tpos_kho_buucuc_mapper.json"
    tp = REPORTS / "tpos_kho_buucuc_mapper.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    (SECRETS / "tpos_kho_buucuc_mapper.state.json").write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "tpos_status": (report.get("tpos") or {}).get("status"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
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
    ap = argparse.ArgumentParser(description="Mapper TPOS ↔ kho · bưu cục")
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
