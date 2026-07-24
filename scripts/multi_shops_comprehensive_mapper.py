#!/usr/bin/env python3
"""Mapper toàn diện multi-shops · ALL shops (owned tokens).

Ống:
  PANCAKE primary/secondary/api_key → GET /shops (ALL)
  → GET /shops/{id}/partners (toàn bộ ĐVVC + accounts[])
  → join pipe DB (orders/kho/carrier/status) + contracts DB
  → atlas shop × partner × pipe · Telegram notify

Secrets-only · không dump-login · không commit dữ liệu đơn.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
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
STATE_PATH = SECRETS / "multi_shops_comprehensive.state.json"
CACHE_PATH = SECRETS / "multi_shops_partners_cache.json"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
BUUCUC_DB = REPORTS / "buucuc_backend.db"
BASE = "https://pos.pages.fm/api/v1"

# ĐVVC trọng điểm (ưu tiên hiển thị)
FOCUS_PARTNERS = {
    "15": "J&T",
    "5": "GHN",
    "42": "SPX",
    "3": "VTP",
    "17": "VNPost",
    "1": "GHTK",
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


def http_json(url: str, timeout: int = 30) -> tuple[int, Any]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        try:
            return e.code, json.loads(raw.decode() or "null")
        except Exception:
            return e.code, {"raw": raw[:200].decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:200]}


def split_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in re.split(r"[,;\s]+", raw) if x.strip()]


def list_shops_for_token(
    label: str, tok: str, mode: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    q = f"{mode}={urllib.parse.quote(tok)}"
    st, body = http_json(f"{BASE}/shops?{q}")
    meta: dict[str, Any] = {"token": label, "http": st, "shops_n": 0}
    shops: list[dict[str, Any]] = []
    if not isinstance(body, dict):
        meta["error"] = "non-dict body"
        return shops, meta
    raw = body.get("shops") or []
    if not isinstance(raw, list):
        meta["error"] = "no shops list"
        return shops, meta
    for s in raw:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        if not sid:
            continue
        shops.append(
            {
                "shop_id": sid,
                "shop_name": s.get("name"),
                "token": label,
                "mode": mode,
            }
        )
    meta["shops_n"] = len(shops)
    return shops, meta


def slim_account(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "phone": a.get("phone") or a.get("phone_number"),
        "email": a.get("email"),
        "customer_code": a.get("customer_code")
        or a.get("cus_id")
        or a.get("code"),
    }


def probe_partners(
    shop_id: str,
    shop_name: str | None,
    tok: str,
    mode: str,
    token_label: str,
    *,
    sleep_s: float = 0.05,
) -> dict[str, Any]:
    q = f"{mode}={urllib.parse.quote(tok)}"
    st, body = http_json(f"{BASE}/shops/{shop_id}/partners?{q}")
    if sleep_s:
        time.sleep(sleep_s)
    row: dict[str, Any] = {
        "shop_id": shop_id,
        "shop_name": shop_name,
        "token": token_label,
        "partners_http": st,
        "partners_n": 0,
        "partners_with_accounts": 0,
        "accounts_total": 0,
        "partners": [],
        "focus": {},
        "status": "unknown",
    }
    if st == 404:
        row["status"] = "shop_not_found"
        row["error"] = "404"
        return row
    if st != 200:
        row["status"] = "partners_error"
        row["error"] = (
            str(body)[:160]
            if not isinstance(body, dict)
            else json.dumps(body, ensure_ascii=False)[:160]
        )
        return row

    partners = body.get("data") if isinstance(body, dict) else None
    if partners is None and isinstance(body, dict):
        partners = body.get("partners")
    if not isinstance(partners, list):
        row["status"] = "no_partners_payload"
        return row

    slim_partners: list[dict[str, Any]] = []
    focus: dict[str, Any] = {}
    with_acc = 0
    acc_total = 0
    for p in partners:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        pname = str(p.get("name") or "")
        acc = p.get("accounts") or []
        if not isinstance(acc, list):
            acc = []
        slim_acc = [slim_account(a) for a in acc if isinstance(a, dict)]
        if slim_acc:
            with_acc += 1
            acc_total += len(slim_acc)
        entry = {
            "id": pid,
            "name": pname,
            "accounts_n": len(slim_acc),
            "accounts": slim_acc,
        }
        slim_partners.append(entry)
        if pid in FOCUS_PARTNERS or any(
            re.search(rf"(?i){re.escape(v)}", pname)
            for v in ("J&T", "JNT", "GHN", "SPX", "Shopee", "Viettel", "VN.?Post", "GHTK")
        ):
            key = FOCUS_PARTNERS.get(pid) or pname[:24]
            focus[key] = {
                "partner_id": pid,
                "partner_name": pname,
                "accounts_n": len(slim_acc),
                "has_contract": bool(slim_acc),
            }

    row["partners_n"] = len(slim_partners)
    row["partners_with_accounts"] = with_acc
    row["accounts_total"] = acc_total
    # chỉ giữ partners có HĐ + focus (tránh blob quá lớn); full id/name vẫn trong partners_brief
    row["partners"] = [p for p in slim_partners if p["accounts_n"] > 0]
    row["partners_brief"] = [
        {"id": p["id"], "name": p["name"], "accounts_n": p["accounts_n"]}
        for p in slim_partners
    ]
    row["focus"] = focus
    row["status"] = "ok" if slim_partners else "empty_partners"
    return row


def pipe_shop_stats() -> dict[str, dict[str, Any]]:
    """shop_id → thống kê từ kho_buucuc_pipe.db (+ fallback buucuc_backend.db)."""
    out: dict[str, dict[str, Any]] = {}

    def ingest(path: Path) -> None:
        if not path.is_file():
            return
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
            if "shop_id" not in cols:
                conn.close()
                return
            name_sel = "shop_name" if "shop_name" in cols else "NULL AS shop_name"
            for r in conn.execute(
                f"""
                SELECT shop_id, {name_sel}, COUNT(*) AS n
                FROM orders
                WHERE shop_id IS NOT NULL AND TRIM(CAST(shop_id AS TEXT)) != ''
                GROUP BY shop_id
                ORDER BY n DESC
                """
            ):
                sid = str(r["shop_id"])
                node = out.setdefault(
                    sid,
                    {
                        "shop_id": sid,
                        "shop_name": r["shop_name"],
                        "orders_n": 0,
                        "sources": [],
                        "by_carrier": Counter(),
                        "by_kho": Counter(),
                        "by_status": Counter(),
                        "by_buucuc": Counter(),
                        "by_backend": Counter(),
                        "phone": Counter(),
                        "with_tracking": 0,
                    },
                )
                node["orders_n"] = max(int(node["orders_n"]), int(r["n"]))
                if r["shop_name"] and not node.get("shop_name"):
                    node["shop_name"] = r["shop_name"]
                if path.name not in node["sources"]:
                    node["sources"].append(path.name)

            # breakdown chỉ từ pipe DB (đầy đủ cột)
            if path == PIPE_DB:
                carrier_col = "carrier" if "carrier" in cols else None
                kho_col = "kho" if "kho" in cols else None
                status_col = "status" if "status" in cols else None
                buucuc_col = "buucuc" if "buucuc" in cols else None
                backend_col = "backend" if "backend" in cols else None
                phone_col = "phone_class" if "phone_class" in cols else None
                track_col = "tracking_code" if "tracking_code" in cols else None

                for r in conn.execute(
                    "SELECT * FROM orders WHERE shop_id IS NOT NULL "
                    "AND TRIM(CAST(shop_id AS TEXT)) != ''"
                ):
                    d = dict(r)
                    sid = str(d.get("shop_id"))
                    node = out.setdefault(
                        sid,
                        {
                            "shop_id": sid,
                            "shop_name": d.get("shop_name"),
                            "orders_n": 0,
                            "sources": [path.name],
                            "by_carrier": Counter(),
                            "by_kho": Counter(),
                            "by_status": Counter(),
                            "by_buucuc": Counter(),
                            "by_backend": Counter(),
                            "phone": Counter(),
                            "with_tracking": 0,
                        },
                    )
                    if carrier_col:
                        node["by_carrier"][str(d.get(carrier_col) or "(none)")] += 1
                    if kho_col:
                        node["by_kho"][str(d.get(kho_col) or "(none)")] += 1
                    if status_col:
                        node["by_status"][str(d.get(status_col) or "(none)")] += 1
                    if buucuc_col:
                        node["by_buucuc"][str(d.get(buucuc_col) or "(none)")] += 1
                    if backend_col:
                        node["by_backend"][str(d.get(backend_col) or "(none)")] += 1
                    if phone_col:
                        node["phone"][str(d.get(phone_col) or "MISSING")] += 1
                    if track_col and str(d.get(track_col) or "").strip():
                        node["with_tracking"] += 1
            conn.close()
        except Exception:  # noqa: BLE001
            return

    ingest(PIPE_DB)
    ingest(BUUCUC_DB)
    # serialize counters
    for node in out.values():
        for k in ("by_carrier", "by_kho", "by_status", "by_buucuc", "by_backend", "phone"):
            c = node.get(k) or Counter()
            if isinstance(c, Counter):
                node[k] = c.most_common(12)
    return out


def contracts_by_shop() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in (BUUCUC_DB, PIPE_DB):
        if not path.is_file():
            continue
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "contracts" not in tables:
                conn.close()
                continue
            cols = {r[1] for r in conn.execute("PRAGMA table_info(contracts)").fetchall()}
            if "shop_id" not in cols:
                conn.close()
                continue
            for r in conn.execute(
                "SELECT * FROM contracts WHERE shop_id IS NOT NULL AND shop_id != ''"
            ):
                d = dict(r)
                sid = str(d.get("shop_id"))
                slim = {
                    "backend": d.get("backend"),
                    "carrier": d.get("carrier") or d.get("buucuc"),
                    "partner_id": d.get("partner_id"),
                    "partner_name": d.get("partner_name"),
                    "account_id": d.get("account_id"),
                    "account_name": d.get("account_name"),
                    "source_db": path.name,
                }
                key = (
                    slim["backend"],
                    slim["partner_id"],
                    slim["account_id"],
                    slim["account_name"],
                )
                existing = {
                    (
                        x.get("backend"),
                        x.get("partner_id"),
                        x.get("account_id"),
                        x.get("account_name"),
                    )
                    for x in out[sid]
                }
                if key not in existing:
                    out[sid].append(slim)
            conn.close()
        except Exception:  # noqa: BLE001
            continue
    return dict(out)


def collect_shop_map() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    env = load_env()
    token_slots: list[tuple[str, str, str]] = []
    if (env.get("PANCAKE_POS_ACCESS_TOKEN") or "").strip():
        token_slots.append(
            ("primary", env["PANCAKE_POS_ACCESS_TOKEN"], "access_token")
        )
    if (env.get("PANCAKE_POS_SECONDARY_ACCESS_TOKEN") or "").strip():
        token_slots.append(
            (
                "secondary",
                env["PANCAKE_POS_SECONDARY_ACCESS_TOKEN"],
                "access_token",
            )
        )
    api_key = (env.get("PANCAKE_POS_API_KEY") or env.get("PANCAKE_API_KEY") or "").strip()
    shop_api = (env.get("PANCAKE_SHOP_ID") or "").strip()

    token_meta: list[dict[str, Any]] = []
    shop_map: dict[str, dict[str, Any]] = {}

    for label, tok, mode in token_slots:
        shops, meta = list_shops_for_token(label, tok, mode)
        token_meta.append(meta)
        for s in shops:
            sid = s["shop_id"]
            if sid not in shop_map:
                shop_map[sid] = {
                    "shop_id": sid,
                    "shop_name": s.get("shop_name"),
                    "tokens": [],
                    "listed_on": [],
                }
            shop_map[sid]["tokens"].append(
                {"token": label, "mode": mode, "tok": tok}
            )
            shop_map[sid]["listed_on"].append(label)
            if s.get("shop_name") and not shop_map[sid].get("shop_name"):
                shop_map[sid]["shop_name"] = s.get("shop_name")

    if api_key and shop_api:
        if shop_api not in shop_map:
            q = f"api_key={urllib.parse.quote(api_key)}"
            st, body = http_json(f"{BASE}/shops/{shop_api}?{q}")
            name = None
            if isinstance(body, dict):
                name = (body.get("shop") or body).get("name")
            shop_map[shop_api] = {
                "shop_id": shop_api,
                "shop_name": name,
                "tokens": [{"token": "api_key", "mode": "api_key", "tok": api_key}],
                "listed_on": ["api_key"],
            }
            token_meta.append(
                {"token": "api_key", "http": st, "shops_n": 1, "shop_id": shop_api}
            )
        else:
            shop_map[shop_api]["tokens"].append(
                {"token": "api_key", "mode": "api_key", "tok": api_key}
            )

    # env shop id lists + known pipe extras
    extras = set(
        split_ids(env.get("PANCAKE_POS_SHOP_IDS"))
        + split_ids(env.get("PANCAKE_SECONDARY_SHOP_IDS"))
        + ["1530618", "4851972", "9999999"]
    )
    for sid in extras:
        if sid in shop_map:
            continue
        tokens = [
            {"token": label, "mode": mode, "tok": tok}
            for label, tok, mode in token_slots
        ]
        if api_key:
            tokens.append({"token": "api_key", "mode": "api_key", "tok": api_key})
        shop_map[sid] = {
            "shop_id": sid,
            "shop_name": None,
            "tokens": tokens,
            "listed_on": [],
            "extra": True,
        }

    # pipe shops chưa có
    for sid, st in pipe_shop_stats().items():
        if sid in shop_map:
            if st.get("shop_name") and not shop_map[sid].get("shop_name"):
                shop_map[sid]["shop_name"] = st.get("shop_name")
            continue
        tokens = [
            {"token": label, "mode": mode, "tok": tok}
            for label, tok, mode in token_slots
        ]
        if api_key:
            tokens.append({"token": "api_key", "mode": "api_key", "tok": api_key})
        shop_map[sid] = {
            "shop_id": sid,
            "shop_name": st.get("shop_name"),
            "tokens": tokens,
            "listed_on": [],
            "extra": True,
            "from_pipe": True,
        }

    return shop_map, token_meta


def scan_all(*, sleep_s: float = 0.05) -> dict[str, Any]:
    shop_map, token_meta = collect_shop_map()
    pipe = pipe_shop_stats()
    contracts = contracts_by_shop()

    results: list[dict[str, Any]] = []
    for sid, meta in sorted(
        shop_map.items(),
        key=lambda x: (-(pipe.get(x[0], {}).get("orders_n") or 0), x[0]),
    ):
        best: dict[str, Any] | None = None
        attempts: list[dict[str, Any]] = []
        for t in meta.get("tokens") or []:
            row = probe_partners(
                sid,
                meta.get("shop_name"),
                t["tok"],
                t["mode"],
                t["token"],
                sleep_s=sleep_s,
            )
            attempts.append(
                {
                    "token": t["token"],
                    "status": row.get("status"),
                    "http": row.get("partners_http"),
                    "partners_n": row.get("partners_n"),
                    "accounts_total": row.get("accounts_total"),
                }
            )
            if row.get("shop_name") and not meta.get("shop_name"):
                meta["shop_name"] = row.get("shop_name")
            if best is None:
                best = row
            else:
                rank = {"ok": 3, "empty_partners": 2, "shop_not_found": 0}
                if rank.get(row["status"], 1) > rank.get(best["status"], 1):
                    best = row
                elif (
                    row.get("status") == best.get("status")
                    and (row.get("accounts_total") or 0)
                    > (best.get("accounts_total") or 0)
                ):
                    best = row
            if row.get("status") == "ok" and (row.get("accounts_total") or 0) > 0:
                break
        assert best is not None
        best["shop_name"] = best.get("shop_name") or meta.get("shop_name")
        best["attempts"] = attempts
        best["listed_on"] = meta.get("listed_on") or []
        best["extra"] = bool(meta.get("extra"))
        best["from_pipe"] = bool(meta.get("from_pipe"))

        pst = pipe.get(sid) or {}
        best["pipe"] = {
            "orders_n": pst.get("orders_n") or 0,
            "with_tracking": pst.get("with_tracking") or 0,
            "by_carrier": pst.get("by_carrier") or [],
            "by_kho": pst.get("by_kho") or [],
            "by_status": pst.get("by_status") or [],
            "by_buucuc": pst.get("by_buucuc") or [],
            "by_backend": pst.get("by_backend") or [],
            "phone": pst.get("phone") or [],
            "sources": pst.get("sources") or [],
        }
        best["contracts_db"] = contracts.get(sid) or []
        results.append(best)

    ok = [r for r in results if r.get("status") == "ok"]
    with_hd = [r for r in ok if (r.get("accounts_total") or 0) > 0]
    empty_hd = [r for r in ok if (r.get("accounts_total") or 0) == 0]
    errors = [r for r in results if r.get("status") not in {"ok", "empty_partners"}]

    # shop × focus partner matrix
    matrix: list[dict[str, Any]] = []
    for r in results:
        focus = r.get("focus") or {}
        matrix.append(
            {
                "shop_id": r["shop_id"],
                "shop_name": r.get("shop_name"),
                "orders_n": (r.get("pipe") or {}).get("orders_n") or 0,
                "api": r.get("status"),
                "J&T": (focus.get("J&T") or {}).get("accounts_n", 0),
                "GHN": (focus.get("GHN") or {}).get("accounts_n", 0),
                "SPX": (focus.get("SPX") or {}).get("accounts_n", 0),
                "VTP": (focus.get("VTP") or {}).get("accounts_n", 0),
                "VNPost": (focus.get("VNPost") or {}).get("accounts_n", 0),
                "GHTK": (focus.get("GHTK") or {}).get("accounts_n", 0),
                "partners_with_hd": r.get("partners_with_accounts") or 0,
                "accounts_total": r.get("accounts_total") or 0,
            }
        )

    layers = [
        {
            "id": "L1-TOKEN",
            "title": "Owned Pancake tokens → /shops ALL",
            "n": len(token_meta),
        },
        {
            "id": "L2-SHOP",
            "title": "Shop atlas (live + pipe + env extras)",
            "n": len(results),
        },
        {
            "id": "L3-PARTNER",
            "title": "ĐVVC /partners + accounts[] (HĐ)",
            "n": sum(r.get("partners_n") or 0 for r in ok),
        },
        {
            "id": "L4-PIPE",
            "title": "Pipe DB orders × shop × carrier/kho",
            "n": sum((r.get("pipe") or {}).get("orders_n") or 0 for r in results),
        },
        {
            "id": "L5-CONTRACT",
            "title": "contracts table (backend HĐ đã upsert)",
            "n": sum(len(r.get("contracts_db") or []) for r in results),
        },
        {
            "id": "L6-GAP",
            "title": "Shop pipe không mở được /partners",
            "n": len(
                [
                    r
                    for r in results
                    if (r.get("pipe") or {}).get("orders_n")
                    and r.get("status") == "shop_not_found"
                ]
            ),
        },
    ]

    return {
        "token_meta": token_meta,
        "results": results,
        "ok": ok,
        "with_hd": with_hd,
        "empty_hd": empty_hd,
        "errors": errors,
        "matrix": matrix,
        "layers": layers,
        "pipe_shops_n": len(pipe),
    }


def build_report(*, sleep_s: float = 0.05) -> dict[str, Any]:
    scan = scan_all(sleep_s=sleep_s)

    # cache partners (gitignored) — không chứa raw token
    SECRETS.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(
            {
                "updated_at": utc_now(),
                "source": "multi_shops_comprehensive_mapper",
                "shops": [
                    {
                        "shop_id": r["shop_id"],
                        "shop_name": r.get("shop_name"),
                        "status": r.get("status"),
                        "token": r.get("token"),
                        "partners_with_accounts": r.get("partners_with_accounts"),
                        "accounts_total": r.get("accounts_total"),
                        "focus": r.get("focus"),
                        "partners": r.get("partners"),
                        "pipe_orders": (r.get("pipe") or {}).get("orders_n"),
                    }
                    for r in scan["results"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    n = len(scan["results"])
    n_ok = len(scan["ok"])
    n_hd = len(scan["with_hd"])
    n_empty = len(scan["empty_hd"])
    n_err = len(scan["errors"])
    orders = sum((r.get("pipe") or {}).get("orders_n") or 0 for r in scan["results"])
    acc_total = sum(r.get("accounts_total") or 0 for r in scan["results"])

    report: dict[str, Any] = {
        "ok": True,
        "module": "multi_shops_comprehensive_mapper",
        "checked_at": utc_now(),
        "policy": "owned Pancake tokens only · no dump-login · secrets/reports gitignored",
        "atlas": (
            "MULTI SHOPS ALL → /shops → /partners (mọi ĐVVC) → "
            "accounts[] HĐ → join pipe DB + contracts"
        ),
        "token_meta": scan["token_meta"],
        "layers": scan["layers"],
        "stats": {
            "shops_total": n,
            "shops_api_ok": n_ok,
            "shops_with_any_hd": n_hd,
            "shops_partners_empty_hd": n_empty,
            "shops_error": n_err,
            "accounts_total": acc_total,
            "pipe_shops": scan["pipe_shops_n"],
            "pipe_orders_joined": orders,
        },
        "matrix": scan["matrix"],
        "shops": [
            {
                "shop_id": r["shop_id"],
                "shop_name": r.get("shop_name"),
                "status": r.get("status"),
                "token": r.get("token"),
                "listed_on": r.get("listed_on"),
                "extra": r.get("extra"),
                "partners_n": r.get("partners_n"),
                "partners_with_accounts": r.get("partners_with_accounts"),
                "accounts_total": r.get("accounts_total"),
                "focus": r.get("focus"),
                "partners_hd": r.get("partners"),
                "pipe": r.get("pipe"),
                "contracts_db_n": len(r.get("contracts_db") or []),
                "contracts_db": r.get("contracts_db"),
                "attempts": r.get("attempts"),
                "error": r.get("error"),
            }
            for r in scan["results"]
        ],
        "with_hd": [
            {
                "shop_id": r["shop_id"],
                "shop_name": r.get("shop_name"),
                "token": r.get("token"),
                "accounts_total": r.get("accounts_total"),
                "focus": r.get("focus"),
                "partners_hd": r.get("partners"),
            }
            for r in scan["with_hd"]
        ],
        "empty_hd": [
            {
                "shop_id": r["shop_id"],
                "shop_name": r.get("shop_name"),
                "token": r.get("token"),
                "partners_n": r.get("partners_n"),
                "orders_n": (r.get("pipe") or {}).get("orders_n") or 0,
            }
            for r in scan["empty_hd"]
        ],
        "errors": [
            {
                "shop_id": r["shop_id"],
                "shop_name": r.get("shop_name"),
                "status": r.get("status"),
                "error": r.get("error"),
                "orders_n": (r.get("pipe") or {}).get("orders_n") or 0,
                "attempts": r.get("attempts"),
            }
            for r in scan["errors"]
        ],
        "verdict": (
            f"✅ Mapper toàn diện multi-shops ALL: shops={n} · "
            f"API ok={n_ok} · có HĐ ĐVVC={n_hd}/{acc_total} accounts · "
            f"rỗng HĐ={n_empty} · lỗi={n_err} · pipe_orders={orders}"
        ),
        "next": [
            "Shop có HĐ: dùng accounts[] làm mã hợp đồng ĐVVC tương ứng",
            "Shop empty_hd nhưng pipe có đơn: bật ĐVVC trên icon đúng shop",
            "Shop pipe + shop_not_found: cần token thuộc shop (vd. 1530618)",
            "python3 scripts/multi_shops_comprehensive_mapper.py --notify",
            "python3 scripts/jnt_partner_contract_all_shops_mapper.py --notify",
            "python3 scripts/comprehensive_order_mapper.py",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    st = report.get("stats") or {}
    lines = [
        "🗺️ MAPPER TOÀN DIỆN · MULTI SHOPS ALL",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"Atlas: {report.get('atlas')}",
        "",
        "=== Lớp ===",
    ]
    for layer in report.get("layers") or []:
        lines.append(f"▶ {layer.get('id')} · {layer.get('title')} · n={layer.get('n')}")

    lines.append("")
    lines.append("=== Token / shops ===")
    for t in report.get("token_meta") or []:
        lines.append(
            f"  · {t.get('token')}: http={t.get('http')} shops={t.get('shops_n')}"
            + (f" shop_id={t.get('shop_id')}" if t.get("shop_id") else "")
        )
    lines.append(
        f"  Total={st.get('shops_total')} · API ok={st.get('shops_api_ok')} · "
        f"có HĐ={st.get('shops_with_any_hd')} · accounts={st.get('accounts_total')} · "
        f"empty_hd={st.get('shops_partners_empty_hd')} · err={st.get('shops_error')} · "
        f"pipe_orders={st.get('pipe_orders_joined')}"
    )

    lines.append("")
    lines.append("=== Ma trận shop × HĐ ĐVVC (accounts_n) ===")
    lines.append(
        "  shop_id | orders | api | J&T | GHN | SPX | VTP | VNPost | GHTK | hd_partners"
    )
    for m in report.get("matrix") or []:
        lines.append(
            f"  {m.get('shop_id')} | {m.get('orders_n')} | {m.get('api')} | "
            f"{m.get('J&T')} | {m.get('GHN')} | {m.get('SPX')} | {m.get('VTP')} | "
            f"{m.get('VNPost')} | {m.get('GHTK')} | {m.get('partners_with_hd')}"
            f" · {str(m.get('shop_name') or '')[:40]}"
        )

    lines.append("")
    lines.append("=== Shop CÓ hợp đồng ĐVVC (accounts[]) ===")
    if not report.get("with_hd"):
        lines.append("  (chưa shop nào có accounts[] trên token hiện có)")
    for r in report.get("with_hd") or []:
        lines.append(
            f"  ✅ {r.get('shop_id')} {r.get('shop_name')} · token={r.get('token')} · "
            f"accounts={r.get('accounts_total')}"
        )
        for p in (r.get("partners_hd") or [])[:12]:
            lines.append(
                f"      · [{p.get('id')}] {p.get('name')} ×{p.get('accounts_n')}"
            )
            for a in (p.get("accounts") or [])[:4]:
                lines.append(
                    f"          id={a.get('id')} name={a.get('name')} "
                    f"code={a.get('customer_code')}"
                )

    lines.append("")
    lines.append("=== Shop mở được /partners nhưng HĐ rỗng ===")
    for r in (report.get("empty_hd") or [])[:40]:
        lines.append(
            f"  ⚠ {r.get('shop_id')} {r.get('shop_name')} · "
            f"partners={r.get('partners_n')} · pipe_orders={r.get('orders_n')} · "
            f"token={r.get('token')}"
        )

    lines.append("")
    lines.append("=== Lỗi / không mở được ===")
    for r in (report.get("errors") or [])[:30]:
        lines.append(
            f"  ❌ {r.get('shop_id')} {r.get('shop_name')}: {r.get('status')} "
            f"pipe={r.get('orders_n')} {(r.get('error') or '')[:80]}"
        )

    lines.append("")
    lines.append("=== Chi tiết pipe / shop (top) ===")
    shops_sorted = sorted(
        report.get("shops") or [],
        key=lambda x: -((x.get("pipe") or {}).get("orders_n") or 0),
    )
    for r in shops_sorted[:12]:
        pipe = r.get("pipe") or {}
        lines.append(
            f"  · {r.get('shop_id')} {r.get('shop_name')} · "
            f"orders={pipe.get('orders_n')} track={pipe.get('with_tracking')} · "
            f"api={r.get('status')} hd={r.get('accounts_total')}"
        )
        carriers = pipe.get("by_carrier") or []
        if carriers:
            top = ", ".join(f"{c}×{n}" for c, n in carriers[:5])
            lines.append(f"      carrier: {top}")
        khos = pipe.get("by_kho") or []
        if khos:
            top = ", ".join(f"{c}×{n}" for c, n in khos[:4])
            lines.append(f"      kho: {top}")

    lines.append("")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "multi_shops_comprehensive.json"
    tp = REPORTS / "multi_shops_comprehensive.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = format_text(report)
    tp.write_text(text + "\n", encoding="utf-8")
    STATE_PATH.write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "stats": report.get("stats"),
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
    ap = argparse.ArgumentParser(
        description="Mapper toàn diện multi-shops ALL (owned Pancake)"
    )
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    report = build_report(sleep_s=args.sleep)
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
