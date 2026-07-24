#!/usr/bin/env python3
"""Mapper hợp đồng ĐVVC → backend bưu cục (SQLite).

Ống:
  partner.accounts[] / HĐ → backend GHN|VTP|J&T|GHTK|Best|SPX
  → bảng `contracts` trong buucuc_backend.db (+ mirror kho_buucuc_pipe.db)
  → join shop_id với orders để đếm đơn gắn HĐ

Owned-only · no dump-login · mask secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
BUUCUC_DB = REPORTS / "buucuc_backend.db"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
ACCOUNTS_PATH = SECRETS / "shipping_partner_accounts_owned.json"
STATE_PATH = SECRETS / "contract_buucuc_backend_mapper.state.json"

# partner_id (Pancake) / carrier hint → backend bưu cục
PARTNER_TO_BACKEND: dict[str, dict[str, str]] = {
    "5": {
        "backend": "GHN",
        "buucuc": "GHN",
        "role": "bưu cục / hub GHN",
        "oms": "ghn",
        "secret": "GHN_API_TOKEN",
        "carrier": "GHN",
    },
    "3": {
        "backend": "ViettelPost",
        "buucuc": "ViettelPost",
        "role": "bưu cục ViettelPost",
        "oms": "viettelpost",
        "secret": "VIETTELPOST_TOKEN",
        "carrier": "VTP",
    },
    "15": {
        "backend": "J&T",
        "buucuc": "J&T",
        "role": "bưu cục J&T Express",
        "oms": "jnt",
        "secret": None,
        "carrier": "J&T",
    },
    "1": {
        "backend": "GHTK",
        "buucuc": "GHTK",
        "role": "bưu cục Giao hàng tiết kiệm",
        "oms": "ghtk",
        "secret": None,
        "carrier": "GHTK",
    },
    "16": {
        "backend": "Best",
        "buucuc": "Best",
        "role": "bưu cục Best Inc",
        "oms": "best",
        "secret": None,
        "carrier": "Best",
    },
    "19": {
        "backend": "NinjaVan",
        "buucuc": "NinjaVan",
        "role": "bưu cục Ninja Van",
        "oms": "ninjavan",
        "secret": None,
        "carrier": "NinjaVan",
    },
}

NAME_HINTS: list[tuple[str, str]] = [
    (r"(?i)j\s*&?\s*t|jnt", "15"),
    (r"(?i)\bvtp\b|viettel", "3"),
    (r"(?i)giao hàng nhanh|\bghn\b", "5"),
    (r"(?i)tiết kiệm|ghtk", "1"),
    (r"(?i)\bbest\b", "16"),
    (r"(?i)ninja", "19"),
    (r"(?i)\bspx\b|shopee\s*express", "spx"),
]

SPX_BACKEND = {
    "backend": "SPX-local",
    "buucuc": "SPX",
    "role": "3PL SPX (file DB)",
    "oms": "spx_local",
    "secret": None,
    "carrier": "SPX",
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


def load_owned_accounts() -> list[dict[str, Any]]:
    if not ACCOUNTS_PATH.is_file():
        return []
    try:
        data = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(data.get("accounts") or [])


def resolve_backend(row: dict[str, Any]) -> dict[str, str] | None:
    pid = str(row.get("partner_id") or "").strip()
    if pid in PARTNER_TO_BACKEND:
        return dict(PARTNER_TO_BACKEND[pid])
    pname = str(row.get("partner_name") or "")
    for pat, key in NAME_HINTS:
        if re.search(pat, pname):
            if key == "spx":
                return dict(SPX_BACKEND)
            return dict(PARTNER_TO_BACKEND[key])
    return None


def contract_id(row: dict[str, Any], be: dict[str, str]) -> str:
    acc = row.get("account") if isinstance(row.get("account"), dict) else {}
    raw = "|".join(
        [
            be["backend"],
            str(row.get("shop_id") or ""),
            str(acc.get("id") or ""),
            str(acc.get("name") or ""),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize_contracts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in accounts:
        be = resolve_backend(row)
        if not be:
            continue
        acc = row.get("account") if isinstance(row.get("account"), dict) else {}
        cid = contract_id(row, be)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(
            {
                "contract_id": cid,
                "backend": be["backend"],
                "buucuc": be["buucuc"],
                "carrier": be["carrier"],
                "role": be["role"],
                "oms": be["oms"],
                "secret": be.get("secret"),
                "shop_id": str(row.get("shop_id") or "") or None,
                "shop_name": row.get("shop_name"),
                "partner_id": str(row.get("partner_id") or "") or None,
                "partner_name": row.get("partner_name"),
                "account_id": str(acc.get("id") if acc.get("id") is not None else "") or None,
                "account_name": acc.get("name"),
                "token_slot": row.get("token"),
                "source": "shipping_partner_accounts_owned",
            }
        )
    # SPX env slot (không có trong Pancake partners)
    env = load_env()
    if (env.get("SPX_SHOP_ID") or "").strip():
        fake = {
            "shop_id": env.get("SPX_SHOP_ID"),
            "shop_name": "SPX env",
            "partner_id": "spx",
            "partner_name": "SPX",
            "account": {"id": env.get("SPX_SHOP_ID"), "name": env.get("SPX_USER") or "SPX_SHOP"},
            "token": "env",
        }
        be = dict(SPX_BACKEND)
        cid = contract_id(fake, be)
        if cid not in seen:
            out.append(
                {
                    "contract_id": cid,
                    "backend": be["backend"],
                    "buucuc": be["buucuc"],
                    "carrier": be["carrier"],
                    "role": be["role"],
                    "oms": be["oms"],
                    "secret": None,
                    "shop_id": str(env.get("SPX_SHOP_ID") or "") or None,
                    "shop_name": "SPX env",
                    "partner_id": "spx",
                    "partner_name": "SPX",
                    "account_id": str(env.get("SPX_SHOP_ID") or "") or None,
                    "account_name": env.get("SPX_USER") or "SPX_SHOP",
                    "token_slot": "env",
                    "source": "SPX_* env",
                }
            )
    return out


CONTRACTS_DDL = """
CREATE TABLE IF NOT EXISTS contracts (
  contract_id TEXT PRIMARY KEY,
  backend TEXT,
  buucuc TEXT,
  carrier TEXT,
  role TEXT,
  oms TEXT,
  secret TEXT,
  shop_id TEXT,
  shop_name TEXT,
  partner_id TEXT,
  partner_name TEXT,
  account_id TEXT,
  account_name TEXT,
  token_slot TEXT,
  source TEXT,
  orders_n INTEGER DEFAULT 0,
  kho_n INTEGER DEFAULT 0,
  with_tracking INTEGER DEFAULT 0,
  synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_contracts_backend ON contracts(backend);
CREATE INDEX IF NOT EXISTS idx_contracts_buucuc ON contracts(buucuc);
CREATE INDEX IF NOT EXISTS idx_contracts_shop ON contracts(shop_id);
CREATE INDEX IF NOT EXISTS idx_contracts_carrier ON contracts(carrier);
"""


def ensure_backend_rows(conn: sqlite3.Connection, contracts: list[dict[str, Any]]) -> int:
    """Đảm bảo catalog backends có hàng cho ĐVVC từ HĐ (J&T/GHTK/Best…)."""
    try:
        existing = {r[0] for r in conn.execute("SELECT id FROM backends").fetchall()}
    except sqlite3.OperationalError:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backends (
              id TEXT PRIMARY KEY,
              role TEXT,
              oms TEXT,
              secret TEXT,
              query_hint TEXT
            )
            """
        )
        existing = set()
    added = 0
    for c in contracts:
        bid = c["backend"]
        if bid in existing:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO backends(id, role, oms, secret, query_hint) VALUES (?,?,?,?,?)",
            (
                bid,
                c["role"],
                c["oms"],
                c.get("secret"),
                f"HĐ {c.get('account_name') or c.get('partner_name')} · shop {c.get('shop_id')}",
            ),
        )
        existing.add(bid)
        added += 1
    return added


def shop_order_stats(conn: sqlite3.Connection, shop_id: str | None) -> tuple[int, int, int]:
    if not shop_id:
        return 0, 0, 0
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   COUNT(DISTINCT kho) AS kho_n,
                   SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != '' THEN 1 ELSE 0 END) AS wt
            FROM orders
            WHERE shop_id = ? OR pancake_shop_id = ?
            """,
            (shop_id, shop_id),
        ).fetchone()
    except sqlite3.OperationalError:
        # pipe DB có thể thiếu pancake_shop_id
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       COUNT(DISTINCT kho) AS kho_n,
                       SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != '' THEN 1 ELSE 0 END) AS wt
                FROM orders WHERE shop_id = ?
                """,
                (shop_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0, 0, 0
    if not row:
        return 0, 0, 0
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


def upsert_contracts(db_path: Path, contracts: list[dict[str, Any]]) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(CONTRACTS_DDL)
    backends_added = ensure_backend_rows(conn, contracts)
    now = utc_now()
    for c in contracts:
        orders_n, kho_n, wt = shop_order_stats(conn, c.get("shop_id"))
        c["orders_n"] = orders_n
        c["kho_n"] = kho_n
        c["with_tracking"] = wt
        conn.execute(
            """
            INSERT INTO contracts(
              contract_id, backend, buucuc, carrier, role, oms, secret,
              shop_id, shop_name, partner_id, partner_name,
              account_id, account_name, token_slot, source,
              orders_n, kho_n, with_tracking, synced_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(contract_id) DO UPDATE SET
              backend=excluded.backend,
              buucuc=excluded.buucuc,
              carrier=excluded.carrier,
              role=excluded.role,
              oms=excluded.oms,
              secret=excluded.secret,
              shop_id=excluded.shop_id,
              shop_name=excluded.shop_name,
              partner_id=excluded.partner_id,
              partner_name=excluded.partner_name,
              account_id=excluded.account_id,
              account_name=excluded.account_name,
              token_slot=excluded.token_slot,
              source=excluded.source,
              orders_n=excluded.orders_n,
              kho_n=excluded.kho_n,
              with_tracking=excluded.with_tracking,
              synced_at=excluded.synced_at
            """,
            (
                c["contract_id"],
                c["backend"],
                c["buucuc"],
                c["carrier"],
                c["role"],
                c["oms"],
                c.get("secret"),
                c.get("shop_id"),
                c.get("shop_name"),
                c.get("partner_id"),
                c.get("partner_name"),
                c.get("account_id"),
                c.get("account_name"),
                c.get("token_slot"),
                c.get("source"),
                orders_n,
                kho_n,
                wt,
                now,
            ),
        )
    # meta
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('contracts_synced_at',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (now,),
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('contracts_n',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(len(contracts)),),
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()
    by_be = Counter(c["backend"] for c in contracts)
    info = {
        "path": str(db_path),
        "contracts": len(contracts),
        "backends_added": backends_added,
        "by_backend": dict(by_be),
        "synced_at": now,
    }
    conn.close()
    return info


def join_summary(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return {"ok": False, "error": "db missing"}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT backend, buucuc, carrier, COUNT(*) AS contracts_n,
                       COUNT(DISTINCT shop_id) AS shops_n,
                       SUM(orders_n) AS orders_linked,
                       SUM(with_tracking) AS tracking_linked
                FROM contracts
                GROUP BY backend, buucuc, carrier
                ORDER BY contracts_n DESC
                """
            ).fetchall()
        ]
        samples = [
            dict(r)
            for r in conn.execute(
                """
                SELECT contract_id, backend, buucuc, shop_id, shop_name,
                       partner_name, account_name, account_id,
                       orders_n, kho_n, with_tracking
                FROM contracts
                ORDER BY orders_n DESC, backend
                LIMIT 40
                """
            ).fetchall()
        ]
        # orders đã gắn backend từ HĐ?
        backend_ids = [r["backend"] for r in rows]
        order_hits = []
        for bid in backend_ids:
            try:
                n = conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE backend = ?", (bid,)
                ).fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
            order_hits.append({"backend": bid, "orders_in_db": n})
    except sqlite3.OperationalError as e:
        conn.close()
        return {"ok": False, "error": str(e)[:160]}
    conn.close()
    return {
        "ok": True,
        "by_backend": rows,
        "samples": samples,
        "order_hits": order_hits,
    }


def mermaid(contracts: list[dict[str, Any]], join: dict[str, Any]) -> str:
    by = Counter(c["backend"] for c in contracts)
    lines = [
        "```mermaid",
        "flowchart LR",
        "  HD[HĐ partner.accounts] --> MAP[Mapper HĐ→BC]",
    ]
    for be, n in by.most_common():
        safe = re.sub(r"[^A-Za-z0-9]", "", be) or "X"
        lines.append(f"  MAP --> {safe}[{be} ×{n}]")
        lines.append(f"  {safe} --> DB[(buucuc_backend.db contracts)]")
    hits = {h["backend"]: h.get("orders_in_db", 0) for h in (join.get("order_hits") or [])}
    if hits:
        lines.append("  DB --> ORD[orders join shop_id]")
        for be, n in list(hits.items())[:6]:
            safe = re.sub(r"[^A-Za-z0-9]", "", be) or "X"
            lines.append(f"  ORD --> O{safe}[{be} orders={n}]")
    lines.append("```")
    return "\n".join(lines)


def build_report(*, refresh_accounts: bool = False) -> dict[str, Any]:
    accounts = load_owned_accounts()
    if refresh_accounts:
        try:
            from contract_pipe_mapper import probe_pancake_partners, load_env as c_env

            live = probe_pancake_partners(c_env())
            if live.get("accounts"):
                accounts = live["accounts"]
                ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
                ACCOUNTS_PATH.write_text(
                    json.dumps(
                        {
                            "updated_at": utc_now(),
                            "source": "contract_buucuc_backend_mapper refresh",
                            "accounts": accounts,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
        except Exception as e:  # noqa: BLE001
            refresh_err = str(e)[:160]
        else:
            refresh_err = None
    else:
        refresh_err = None

    contracts = normalize_contracts(accounts)
    buu_info = upsert_contracts(BUUCUC_DB, contracts)
    pipe_info = None
    if PIPE_DB.is_file() or contracts:
        pipe_info = upsert_contracts(PIPE_DB, contracts)
    join = join_summary(BUUCUC_DB)

    mapped_n = len(contracts)
    backends_n = len({c["backend"] for c in contracts})
    linked = sum(c.get("orders_n") or 0 for c in contracts)
    unmapped = [
        {
            "shop_id": a.get("shop_id"),
            "partner_id": a.get("partner_id"),
            "partner_name": a.get("partner_name"),
        }
        for a in accounts
        if resolve_backend(a) is None
    ]

    report: dict[str, Any] = {
        "ok": True,
        "module": "contract_buucuc_backend_mapper",
        "checked_at": utc_now(),
        "policy": "owned-only · no dump-login · HĐ → backend bưu cục SQLite",
        "atlas": (
            "credential/shop → ĐVVC accounts → backend GHN|VTP|J&T|GHTK|Best|SPX "
            "→ buucuc_backend.db.contracts → join orders(shop_id)"
        ),
        "accounts_in": len(accounts),
        "contracts_mapped": mapped_n,
        "backends_n": backends_n,
        "orders_linked_sum": linked,
        "unmapped_accounts": unmapped,
        "db": {"buucuc": buu_info, "pipe": pipe_info},
        "join": join,
        "contracts": contracts,
        "mermaid": mermaid(contracts, join),
        "verdict": (
            f"✅ HĐ→backend BC: {mapped_n} hợp đồng · {backends_n} backend · "
            f"đơn join shop≈{linked} · db={BUUCUC_DB.name}"
        ),
        "next": [
            "Panel: 📜 Ống·hợp đồng / 🗄 Backend BC·DB — bảng contracts",
            "sqlite3 reports/telegram-classify/buucuc_backend.db "
            "\"SELECT backend, account_name, shop_id, orders_n FROM contracts;\"",
            "J&T vẫn thiếu account sống → chưa có hàng backend=J&T trong contracts",
        ],
    }
    if refresh_err:
        report["refresh_error"] = refresh_err
    return report


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🗺️ Mapper HĐ → backend bưu cục",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"Atlas: {report.get('atlas')}",
        "",
        "=== HĐ gắn backend ===",
    ]
    for c in report.get("contracts") or []:
        lines.append(
            f"  · [{c.get('backend')}/{c.get('buucuc')}] "
            f"shop {c.get('shop_id')} {c.get('shop_name')} → "
            f"{c.get('partner_name')}: {c.get('account_name')} "
            f"(id={c.get('account_id')}) · đơn≈{c.get('orders_n')} · "
            f"track≈{c.get('with_tracking')}"
        )
    lines.append("")
    lines.append("=== Join theo backend ===")
    for r in (report.get("join") or {}).get("by_backend") or []:
        lines.append(
            f"  · {r.get('backend')}: HĐ×{r.get('contracts_n')} · "
            f"shop×{r.get('shops_n')} · orders_linked≈{r.get('orders_linked')}"
        )
    for h in (report.get("join") or {}).get("order_hits") or []:
        lines.append(f"  · orders DB backend={h.get('backend')}: {h.get('orders_in_db')}")
    if report.get("unmapped_accounts"):
        lines.append("")
        lines.append("=== Unmapped ===")
        for u in report["unmapped_accounts"][:10]:
            lines.append(f"  · {u}")
    lines.append("")
    lines.append(report.get("mermaid") or "")
    lines.append("")
    db = report.get("db") or {}
    if db.get("buucuc"):
        lines.append(f"DB buucuc: {db['buucuc'].get('path')} · +backends={db['buucuc'].get('backends_added')}")
    if db.get("pipe"):
        lines.append(f"DB pipe: {db['pipe'].get('path')}")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "contract_buucuc_backend_mapper.json"
    tp = REPORTS / "contract_buucuc_backend_mapper.txt"
    mp = REPORTS / "contract_buucuc_backend_mapper.mermaid.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = format_text(report)
    tp.write_text(text + "\n", encoding="utf-8")
    mp.write_text((report.get("mermaid") or "") + "\n", encoding="utf-8")
    STATE_PATH.write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "contracts_n": report.get("contracts_mapped"),
                "backends_n": report.get("backends_n"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"json": str(jp), "txt": str(tp), "mermaid": str(mp)}


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
    ap = argparse.ArgumentParser(description="Mapper HĐ ĐVVC → backend bưu cục")
    ap.add_argument("--refresh", action="store_true", help="probe Pancake partners trước")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(refresh_accounts=args.refresh)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
