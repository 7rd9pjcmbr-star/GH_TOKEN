#!/usr/bin/env python3
"""Mapper backend của từng bưu cục.

Đọc kho_buucuc_pipe.db (+ mirror buucuc_backend.db / contracts / OMS probe):
  mỗi buucuc → backend(s) · OMS channel status · kho · shop · HĐ · pipe_source · mẫu đơn

Local view · owned secrets probe · không dump-login.
Mặc định không ghi git (reports gitignored).
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
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
BUUCUC_DB = REPORTS / "buucuc_backend.db"

# Chuẩn hoá tên bưu cục → backend catalog
BUUCUC_TO_BACKEND = {
    "GHN": "GHN",
    "J&T": "J&T",
    "JNT": "J&T",
    "SPX": "SPX-local",
    "ViettelPost": "ViettelPost",
    "VTP": "ViettelPost",
    "VNPost": "VNPost-local",
    "GHTK": "GHTK",
    "Best": "Best",
    "Pancake": "Pancake",
    "OMS-pipe-bus": "OMS-pipe-bus",
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


def resolve_primary_backend(buucuc: str, backends: Counter) -> str:
    if not backends:
        return "OMS-pipe-bus"
    # backend ghi nhiều nhất trên buucuc này
    top = backends.most_common(1)[0][0]
    mapped = BUUCUC_TO_BACKEND.get(buucuc) or BUUCUC_TO_BACKEND.get(
        buucuc.split("/")[0]
    )
    if mapped and mapped in backends:
        return mapped
    if mapped:
        return mapped
    return top


def open_db(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def load_contracts() -> dict[str, list[dict]]:
    """backend → list contracts."""
    by_be: dict[str, list[dict]] = defaultdict(list)
    for path in (BUUCUC_DB, PIPE_DB):
        conn = open_db(path)
        if not conn:
            continue
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "contracts" not in tables:
            conn.close()
            continue
        for r in conn.execute(
            "SELECT backend, buucuc, shop_id, shop_name, partner_name, "
            "account_name, account_id, orders_n FROM contracts"
        ):
            by_be[str(r["backend"])].append(dict(r))
        conn.close()
        if by_be:
            break
    return by_be


def probe_oms() -> dict[str, dict]:
    try:
        from oms_interconnect import interconnect, load_env as oms_env

        oms = interconnect(oms_env(), ingest=False)
    except Exception as e:  # noqa: BLE001
        return {"_error": {"status": "error", "detail": str(e)[:160]}}
    out: dict[str, dict] = {}
    for c in oms.get("channels") or []:
        be = c.get("backend") or c.get("id")
        out[str(be)] = {
            "id": c.get("id"),
            "backend": be,
            "status": c.get("status"),
            "detail": (c.get("detail") or "")[:120],
            "http": c.get("http"),
        }
        if c.get("id"):
            out[str(c["id"])] = out[str(be)]
    return out


def collect_buucuc_nodes(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}

    def ensure(buu: str) -> dict[str, Any]:
        return nodes.setdefault(
            buu,
            {
                "buucuc": buu,
                "orders": 0,
                "backends": Counter(),
                "kho": Counter(),
                "shops": Counter(),
                "shop_names": {},
                "statuses": Counter(),
                "carriers": Counter(),
                "pipe_sources": Counter(),
                "channels": Counter(),
                "sources": Counter(),
                "with_tracking": 0,
                "provinces": Counter(),
                "samples": [],
            },
        )

    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
    extra = []
    for c in (
        "province",
        "receiver_name",
        "receiver_phone",
        "tracking_code",
        "pipe_source",
        "channel",
        "source",
        "carrier",
        "status",
        "kho",
        "shop_id",
        "shop_name",
        "order_key",
        "cod_amount",
    ):
        if c in cols:
            extra.append(c)
    select = "buucuc, backend, " + ", ".join(extra) if extra else "buucuc, backend"
    for r in conn.execute(f"SELECT {select} FROM orders"):
        d = dict(r)
        buu = str(d.get("buucuc") or "(none)")
        n = ensure(buu)
        n["orders"] += 1
        be = str(d.get("backend") or "(none)")
        n["backends"][be] += 1
        if d.get("kho"):
            n["kho"][str(d["kho"])] += 1
        sid = str(d.get("shop_id") or "")
        if sid:
            n["shops"][sid] += 1
            if d.get("shop_name"):
                n["shop_names"][sid] = d["shop_name"]
        if d.get("status"):
            n["statuses"][str(d["status"])] += 1
        if d.get("carrier"):
            n["carriers"][str(d["carrier"])] += 1
        if d.get("pipe_source"):
            n["pipe_sources"][str(d["pipe_source"])] += 1
        if d.get("channel"):
            n["channels"][str(d["channel"])] += 1
        if d.get("source"):
            n["sources"][str(d["source"])] += 1
        if d.get("tracking_code"):
            n["with_tracking"] += 1
        if d.get("province"):
            n["provinces"][str(d["province"])] += 1
        if len(n["samples"]) < 3 and d.get("tracking_code"):
            n["samples"].append(
                {
                    "order_key": d.get("order_key"),
                    "tracking_code": d.get("tracking_code"),
                    "status": d.get("status"),
                    "kho": d.get("kho"),
                    "shop_id": d.get("shop_id"),
                    "receiver_name": d.get("receiver_name"),
                    "receiver_phone": d.get("receiver_phone"),
                    "province": d.get("province"),
                    "cod_amount": d.get("cod_amount"),
                }
            )
    return nodes


def map_backend_for_buucuc(
    node: dict[str, Any],
    oms: dict[str, dict],
    contracts_by_be: dict[str, list[dict]],
) -> dict[str, Any]:
    buu = node["buucuc"]
    backends: Counter = node["backends"]
    primary = resolve_primary_backend(buu, backends)

    # OMS lookup
    oms_hit = oms.get(primary) or oms.get(primary.lower()) or {}
    # alias
    alias = {
        "SPX-local": "SPX-local",
        "OMS-pipe-bus": "OMS-pipe-bus",
        "J&T": None,
        "GHTK": None,
        "Best": None,
        "Pancake": "Pancake",
        "GHN": "GHN",
        "ViettelPost": "ViettelPost",
        "direct_api": "direct_api",
    }
    oms_key = alias.get(primary, primary)
    if oms_key and not oms_hit:
        oms_hit = oms.get(oms_key) or oms.get(str(oms_key).lower()) or {}

    # kind
    if buu.startswith("UNKNOWN_") or buu.startswith("UNASSIGNED"):
        kind = "unassigned"
    elif primary in {"GHN", "J&T", "SPX-local", "ViettelPost", "VNPost-local", "GHTK", "Best"}:
        kind = "carrier_hub"
    elif primary == "Pancake":
        kind = "pos_hub"
    else:
        kind = "pipe_bus"

    cons = contracts_by_be.get(primary) or []
    # also match by buucuc field on contract
    for be, rows in contracts_by_be.items():
        for c in rows:
            if str(c.get("buucuc") or "") == buu and c not in cons:
                cons.append(c)

    pipe_status = oms_hit.get("status") or (
        "local_db" if primary in {"SPX-local", "VNPost-local", "direct_api", "OMS-pipe-bus"} else "unknown"
    )
    if kind == "unassigned":
        pipe_status = "unassigned_local"

    return {
        "buucuc": buu,
        "primary_backend": primary,
        "kind": kind,
        "pipe_status": pipe_status,
        "oms": {
            "channel": oms_hit.get("id") or oms_key,
            "status": oms_hit.get("status"),
            "detail": oms_hit.get("detail"),
            "http": oms_hit.get("http"),
        },
        "orders": node["orders"],
        "with_tracking": node["with_tracking"],
        "track_pct": round(100.0 * node["with_tracking"] / max(node["orders"], 1), 1),
        "backends": backends.most_common(),
        "kho_top": node["kho"].most_common(8),
        "kho_n": len(node["kho"]),
        "shops_top": [
            {
                "shop_id": sid,
                "shop_name": node["shop_names"].get(sid),
                "orders": cnt,
            }
            for sid, cnt in node["shops"].most_common(8)
        ],
        "shop_n": len(node["shops"]),
        "statuses_top": node["statuses"].most_common(6),
        "carriers_top": node["carriers"].most_common(6),
        "pipe_sources": node["pipe_sources"].most_common(),
        "channels": node["channels"].most_common(6),
        "provinces_top": node["provinces"].most_common(5),
        "contracts": [
            {
                "shop_id": c.get("shop_id"),
                "shop_name": c.get("shop_name"),
                "partner_name": c.get("partner_name"),
                "account_name": c.get("account_name"),
                "orders_n": c.get("orders_n"),
            }
            for c in cons[:8]
        ],
        "samples": node["samples"],
        "query_hint": (
            f"SELECT * FROM orders WHERE buucuc={buu!r} AND backend={primary!r} LIMIT 20"
        ),
    }


def build_report(*, prefer_pipe: bool = True) -> dict[str, Any]:
    path = PIPE_DB if prefer_pipe and PIPE_DB.is_file() else BUUCUC_DB
    conn = open_db(path)
    if not conn:
        return {
            "ok": False,
            "error": f"missing DB {PIPE_DB} / {BUUCUC_DB}",
            "checked_at": utc_now(),
        }
    nodes = collect_buucuc_nodes(conn)
    conn.close()
    oms = probe_oms()
    contracts = load_contracts()

    mapped = [
        map_backend_for_buucuc(n, oms, contracts)
        for n in sorted(nodes.values(), key=lambda x: -x["orders"])
    ]

    by_kind = Counter(m["kind"] for m in mapped)
    by_status = Counter(m["pipe_status"] or "?" for m in mapped)
    connected = [
        m
        for m in mapped
        if m["pipe_status"] in {"connected", "alive", "ok", "local_db"}
    ]
    blocked = [
        m
        for m in mapped
        if m["pipe_status"] in {"missing_cred", "auth_fail", "error", "stale"}
    ]

    report: dict[str, Any] = {
        "ok": True,
        "module": "buucuc_backend_per_hub_mapper",
        "checked_at": utc_now(),
        "policy": "local map · owned OMS probe · no dump-login · no git by default",
        "atlas": "kho_buucuc_pipe.db → từng buucuc → primary_backend + OMS + HĐ + kho/shop",
        "db": str(path),
        "stats": {
            "buucuc_n": len(mapped),
            "orders": sum(m["orders"] for m in mapped),
            "connected_hubs": len(connected),
            "blocked_hubs": len(blocked),
            "by_kind": dict(by_kind),
            "by_pipe_status": dict(by_status),
        },
        "hubs": mapped,
        "verdict": (
            f"✅ Backend từng bưu cục: {len(mapped)} hub · "
            f"connected/local={len(connected)} · blocked={len(blocked)} · "
            f"orders={sum(m['orders'] for m in mapped)} · db={Path(path).name}"
        ),
        "mermaid": _mermaid(mapped),
        "next": [
            "python3 scripts/buucuc_backend_per_hub_mapper.py --notify",
            "sqlite3 reports/telegram-classify/kho_buucuc_pipe.db "
            "\"SELECT * FROM orders WHERE buucuc='SPX' LIMIT 5;\"",
            "Panel: gắn q:bc_hub nếu cần",
        ],
    }
    return report


def _mermaid(mapped: list[dict[str, Any]]) -> str:
    lines = ["```mermaid", "flowchart LR", "  DB[(kho_buucuc_pipe)] --> MAP[Mapper backend/BC]"]
    for m in mapped[:12]:
        safe = "".join(ch if ch.isalnum() else "" for ch in m["buucuc"])[:18] or "X"
        be = "".join(ch if ch.isalnum() else "" for ch in m["primary_backend"])[:14] or "B"
        flag = "✅" if m["pipe_status"] in {"connected", "local_db", "alive", "ok"} else (
            "⚠" if m["kind"] == "unassigned" else "❌"
        )
        lines.append(
            f"  MAP --> {safe}[{flag} {m['buucuc'][:20]} ×{m['orders']}]"
        )
        lines.append(f"  {safe} --> B{safe}{be}[{m['primary_backend']}]")
    lines.append("```")
    return "\n".join(lines)


def format_text(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"❌ {report.get('error')}"
    st = report.get("stats") or {}
    lines = [
        "🏛 Mapper backend của từng bưu cục",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"Atlas: {report.get('atlas')}",
        f"DB: {report.get('db')}",
        "",
        f"=== Hubs: {st.get('buucuc_n')} · orders={st.get('orders')} ===",
        f"  kind: {st.get('by_kind')}",
        f"  pipe_status: {st.get('by_pipe_status')}",
        "",
    ]
    for m in report.get("hubs") or []:
        flag = (
            "✅"
            if m.get("pipe_status") in {"connected", "local_db", "alive", "ok"}
            else ("⚠" if m.get("kind") == "unassigned" else "❌")
        )
        lines.append(
            f"{flag} [{m.get('buucuc')}] → backend={m.get('primary_backend')} · "
            f"kind={m.get('kind')} · status={m.get('pipe_status')} · "
            f"orders={m.get('orders')} track={m.get('with_tracking')}({m.get('track_pct')}%)"
        )
        oms = m.get("oms") or {}
        if oms.get("channel") or oms.get("detail"):
            lines.append(
                f"    OMS: {oms.get('channel')} · {oms.get('status')} — {oms.get('detail') or ''}"
            )
        lines.append(
            f"    backends={m.get('backends')} · kho_n={m.get('kho_n')} · shop_n={m.get('shop_n')}"
        )
        if m.get("kho_top"):
            lines.append(
                "    kho: "
                + ", ".join(f"{k}×{n}" for k, n in (m.get("kho_top") or [])[:4])
            )
        if m.get("shops_top"):
            lines.append(
                "    shop: "
                + ", ".join(
                    f"{s.get('shop_name') or s.get('shop_id')}×{s.get('orders')}"
                    for s in (m.get("shops_top") or [])[:4]
                )
            )
        if m.get("contracts"):
            for c in m["contracts"][:3]:
                lines.append(
                    f"    HĐ: {c.get('partner_name')} {c.get('account_name')} "
                    f"(shop {c.get('shop_id')})"
                )
        if m.get("pipe_sources"):
            lines.append(f"    pipe_source: {m.get('pipe_sources')}")
        for s in (m.get("samples") or [])[:2]:
            lines.append(
                f"    mẫu: {s.get('order_key')} VĐ={s.get('tracking_code')} "
                f"nhận={s.get('receiver_name')} SĐT={s.get('receiver_phone')}"
            )
        lines.append(f"    SQL: {m.get('query_hint')}")
        lines.append("")
    lines.append(report.get("mermaid") or "")
    lines.append("")
    lines.append("Git: không commit (reports gitignored)")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "buucuc_backend_per_hub_mapper.json"
    tp = REPORTS / "buucuc_backend_per_hub_mapper.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def notify_telegram(text: str) -> int | None:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return None
    body = json.dumps({"chat_id": chat, "text": text[:3500]}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mapper backend của từng bưu cục")
    ap.add_argument("--prefer-buucuc-db", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(prefer_pipe=not args.prefer_buucuc_db)
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
