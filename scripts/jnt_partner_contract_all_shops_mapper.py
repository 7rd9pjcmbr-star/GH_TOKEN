#!/usr/bin/env python3
"""Mapper hợp đồng đối tác vận chuyển J&T trên tất cả shop (owned tokens).

Ống:
  PANCAKE_* tokens → GET /shops (ALL) → GET /shops/{id}/partners
  → partner_id=15 (J&T) → accounts[] = mã HĐ / customer code
  → báo cáo: có HĐ / rỗng / lỗi · upsert contracts backend=J&T

Không dump-login · không commit dữ liệu đơn · secrets/reports gitignored.
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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
STATE_PATH = SECRETS / "jnt_partner_contract_all_shops.state.json"
CACHE_PATH = SECRETS / "jnt_partner_accounts_all_shops.json"
BUUCUC_DB = REPORTS / "buucuc_backend.db"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"

JNT_PARTNER_ID = "15"
BASE = "https://pos.pages.fm/api/v1"


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


def is_jnt(pid: str, pname: str) -> bool:
    if str(pid) == JNT_PARTNER_ID:
        return True
    return bool(re.search(r"(?i)j\s*&?\s*t|jnt|jet\s*express", pname or ""))


def list_shops_for_token(
    label: str, tok: str, mode: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    q = f"{mode}={urllib.parse.quote(tok)}"
    st, body = http_json(f"{BASE}/shops?{q}")
    meta = {"token": label, "http": st, "shops_n": 0}
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


def probe_jnt_on_shop(
    shop: dict[str, Any], tok: str, mode: str, *, sleep_s: float = 0.05
) -> dict[str, Any]:
    sid = shop["shop_id"]
    q = f"{mode}={urllib.parse.quote(tok)}"
    st, body = http_json(f"{BASE}/shops/{sid}/partners?{q}")
    if sleep_s:
        time.sleep(sleep_s)
    row: dict[str, Any] = {
        "shop_id": sid,
        "shop_name": shop.get("shop_name"),
        "token": shop.get("token"),
        "partners_http": st,
        "jnt_found": False,
        "jnt_partner_id": None,
        "jnt_partner_name": None,
        "accounts_n": 0,
        "accounts": [],
        "status": "unknown",
    }
    if st == 404:
        row["status"] = "shop_not_found"
        row["error"] = "404"
        return row
    if st != 200:
        row["status"] = "partners_error"
        row["error"] = str(body)[:160] if not isinstance(body, dict) else json.dumps(
            body, ensure_ascii=False
        )[:160]
        return row
    partners = body.get("data") if isinstance(body, dict) else None
    if partners is None and isinstance(body, dict):
        partners = body.get("partners")
    if not isinstance(partners, list):
        row["status"] = "no_partners_payload"
        return row

    jnt = None
    for p in partners:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        pname = str(p.get("name") or "")
        if is_jnt(pid, pname):
            jnt = p
            break
    if not jnt:
        row["status"] = "jnt_not_in_catalog"
        return row

    acc = jnt.get("accounts") or []
    if not isinstance(acc, list):
        acc = []
    slim = []
    for a in acc:
        if not isinstance(a, dict):
            continue
        slim.append(
            {
                "id": a.get("id"),
                "name": a.get("name"),
                # giữ field hữu ích nếu API trả (không secret)
                "phone": a.get("phone") or a.get("phone_number"),
                "email": a.get("email"),
                "customer_code": a.get("customer_code") or a.get("cus_id") or a.get("code"),
            }
        )
    row["jnt_found"] = True
    row["jnt_partner_id"] = str(jnt.get("id") or JNT_PARTNER_ID)
    row["jnt_partner_name"] = jnt.get("name") or "J&T"
    row["accounts_n"] = len(acc)
    row["accounts"] = slim
    row["status"] = "has_contract" if slim else "jnt_empty"
    return row


def shops_from_pipe_db() -> list[dict[str, Any]]:
    """Shop IDs xuất hiện trong pipe — để ghi gap nếu token không mở được."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in (PIPE_DB, BUUCUC_DB):
        if not path.is_file():
            continue
        try:
            conn = sqlite3.connect(str(path))
            cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
            if "shop_id" not in cols:
                conn.close()
                continue
            name_col = "shop_name" if "shop_name" in cols else None
            sql = (
                f"SELECT shop_id, {name_col}, COUNT(*) FROM orders "
                f"WHERE shop_id IS NOT NULL AND shop_id != '' "
                f"GROUP BY shop_id{', ' + name_col if name_col else ''} "
                f"ORDER BY COUNT(*) DESC"
                if name_col
                else "SELECT shop_id, COUNT(*) FROM orders "
                "WHERE shop_id IS NOT NULL AND shop_id != '' "
                "GROUP BY shop_id ORDER BY COUNT(*) DESC"
            )
            for r in conn.execute(sql):
                sid = str(r[0])
                if sid in seen:
                    continue
                seen.add(sid)
                out.append(
                    {
                        "shop_id": sid,
                        "shop_name": r[1] if name_col else None,
                        "orders_n": r[2] if name_col else r[1],
                        "source": path.name,
                    }
                )
            conn.close()
        except Exception:  # noqa: BLE001
            continue
    return out


def upsert_jnt_contracts(rows_with_hd: list[dict[str, Any]]) -> dict[str, Any]:
    """Ghi HĐ J&T vào buucuc_backend.db.contracts nếu DB có."""
    if not BUUCUC_DB.is_file() or not rows_with_hd:
        return {"ok": False, "skipped": True, "reason": "no db or no contracts"}
    try:
        from contract_buucuc_backend_mapper import upsert_contracts, normalize_contracts
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:160]}

    accounts = []
    for row in rows_with_hd:
        for a in row.get("accounts") or []:
            accounts.append(
                {
                    "shop_id": row["shop_id"],
                    "shop_name": row.get("shop_name"),
                    "partner_id": row.get("jnt_partner_id") or JNT_PARTNER_ID,
                    "partner_name": row.get("jnt_partner_name") or "J&T",
                    "account": {
                        "id": a.get("id"),
                        "name": a.get("name")
                        or a.get("customer_code")
                        or a.get("email")
                        or a.get("phone"),
                    },
                    "token": row.get("token"),
                }
            )
    contracts = normalize_contracts(accounts)
    # chỉ giữ J&T
    contracts = [c for c in contracts if c.get("backend") == "J&T" or c.get("carrier") == "J&T"]
    info = upsert_contracts(BUUCUC_DB, contracts)
    if PIPE_DB.is_file():
        upsert_contracts(PIPE_DB, contracts)
    return {"ok": True, "accounts": len(accounts), "db": info}


def scan_all_shops(*, sleep_s: float = 0.05, extra_shop_ids: list[str] | None = None) -> dict[str, Any]:
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
    shop_map: dict[str, dict[str, Any]] = {}  # shop_id -> shop meta (first token wins list, all tokens probed)

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
                }
            shop_map[sid]["tokens"].append({"token": label, "mode": mode, "tok": tok})
            if not shop_map[sid].get("shop_name") and s.get("shop_name"):
                shop_map[sid]["shop_name"] = s.get("shop_name")

    # api_key single shop
    if api_key and shop_api:
        if shop_api not in shop_map:
            # resolve name
            q = f"api_key={urllib.parse.quote(api_key)}"
            st, body = http_json(f"{BASE}/shops/{shop_api}?{q}")
            name = None
            if isinstance(body, dict) and body.get("success"):
                name = (body.get("shop") or body).get("name")
            shop_map[shop_api] = {
                "shop_id": shop_api,
                "shop_name": name,
                "tokens": [{"token": "api_key", "mode": "api_key", "tok": api_key}],
            }
            token_meta.append(
                {"token": "api_key", "http": st, "shops_n": 1, "shop_id": shop_api}
            )
        else:
            shop_map[shop_api]["tokens"].append(
                {"token": "api_key", "mode": "api_key", "tok": api_key}
            )

    # extra shop ids (vd. 1530618) — thử mọi token
    for sid in extra_shop_ids or []:
        sid = str(sid).strip()
        if not sid:
            continue
        if sid not in shop_map:
            shop_map[sid] = {
                "shop_id": sid,
                "shop_name": None,
                "tokens": [
                    {"token": label, "mode": mode, "tok": tok}
                    for label, tok, mode in token_slots
                ],
            }
            if api_key:
                shop_map[sid]["tokens"].append(
                    {"token": "api_key", "mode": "api_key", "tok": api_key}
                )
            shop_map[sid]["extra"] = True

    results: list[dict[str, Any]] = []
    for sid, meta in sorted(shop_map.items(), key=lambda x: x[0]):
        best: dict[str, Any] | None = None
        attempts = []
        for t in meta["tokens"]:
            probe_shop = {
                "shop_id": sid,
                "shop_name": meta.get("shop_name"),
                "token": t["token"],
            }
            row = probe_jnt_on_shop(
                probe_shop, t["tok"], t["mode"], sleep_s=sleep_s
            )
            attempts.append(
                {
                    "token": t["token"],
                    "status": row.get("status"),
                    "http": row.get("partners_http"),
                    "accounts_n": row.get("accounts_n"),
                }
            )
            if row.get("shop_name") and not meta.get("shop_name"):
                meta["shop_name"] = row.get("shop_name")
                row["shop_name"] = meta["shop_name"]
            # ưu tiên has_contract > jnt_empty > khác
            if best is None:
                best = row
            else:
                rank = {
                    "has_contract": 3,
                    "jnt_empty": 2,
                    "jnt_not_in_catalog": 1,
                }
                if rank.get(row["status"], 0) > rank.get(best["status"], 0):
                    best = row
            if row.get("status") == "has_contract":
                break  # đủ HĐ
        assert best is not None
        best["shop_name"] = best.get("shop_name") or meta.get("shop_name")
        best["attempts"] = attempts
        best["extra"] = bool(meta.get("extra"))
        results.append(best)

    has_hd = [r for r in results if r.get("status") == "has_contract"]
    empty = [r for r in results if r.get("status") == "jnt_empty"]
    not_in = [r for r in results if r.get("status") == "jnt_not_in_catalog"]
    errors = [
        r
        for r in results
        if r.get("status")
        not in {"has_contract", "jnt_empty", "jnt_not_in_catalog"}
    ]

    pipe_shops = shops_from_pipe_db()
    accessible = {r["shop_id"] for r in results if r.get("status") != "shop_not_found"}
    pipe_gaps = [
        p
        for p in pipe_shops
        if p["shop_id"] not in shop_map
        or (
            p["shop_id"] in shop_map
            and all(
                a.get("status") == "shop_not_found"
                for a in next(
                    (x.get("attempts") or [] for x in results if x["shop_id"] == p["shop_id"]),
                    [],
                )
            )
        )
    ]
    # simpler: pipe shops not successfully opened for partners
    opened_ok = {
        r["shop_id"]
        for r in results
        if r.get("status") in {"has_contract", "jnt_empty", "jnt_not_in_catalog"}
    }
    pipe_inaccessible = [p for p in pipe_shops if p["shop_id"] not in opened_ok]

    return {
        "token_meta": token_meta,
        "shops_probed": len(results),
        "results": results,
        "has_contract": has_hd,
        "jnt_empty": empty,
        "jnt_not_in_catalog": not_in,
        "errors": errors,
        "pipe_shops_n": len(pipe_shops),
        "pipe_inaccessible": pipe_inaccessible[:40],
        "accounts_flat": [
            {
                "shop_id": r["shop_id"],
                "shop_name": r.get("shop_name"),
                "partner_id": r.get("jnt_partner_id") or JNT_PARTNER_ID,
                "partner_name": r.get("jnt_partner_name") or "J&T",
                "account": a,
                "token": r.get("token"),
            }
            for r in has_hd
            for a in (r.get("accounts") or [])
        ],
    }


def build_report(
    *,
    sleep_s: float = 0.05,
    extra_shops: list[str] | None = None,
    upsert_db: bool = True,
) -> dict[str, Any]:
    extras = list(extra_shops or [])
    # luôn thử shop hay được nhắc
    for sid in ("1530618",):
        if sid not in extras:
            extras.append(sid)

    scan = scan_all_shops(sleep_s=sleep_s, extra_shop_ids=extras)
    db_info = None
    if upsert_db and scan.get("has_contract"):
        db_info = upsert_jnt_contracts(scan["has_contract"])

    # cache secrets (gitignored)
    SECRETS.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(
            {
                "updated_at": utc_now(),
                "source": "jnt_partner_contract_all_shops_mapper",
                "shops_probed": scan["shops_probed"],
                "has_contract_n": len(scan["has_contract"]),
                "accounts": scan["accounts_flat"],
                "jnt_empty": [
                    {"shop_id": r["shop_id"], "shop_name": r.get("shop_name")}
                    for r in scan["jnt_empty"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    n_hd = len(scan["has_contract"])
    n_acc = len(scan["accounts_flat"])
    n_empty = len(scan["jnt_empty"])
    n_err = len(scan["errors"])

    report: dict[str, Any] = {
        "ok": True,
        "module": "jnt_partner_contract_all_shops_mapper",
        "checked_at": utc_now(),
        "policy": "owned Pancake tokens only · no dump-login · secrets gitignored",
        "atlas": (
            "ALL shops trên primary/secondary/api_key → /partners id=15 J&T → "
            "accounts[] = HĐ · so với shop trong pipe DB"
        ),
        "partner": {"id": JNT_PARTNER_ID, "name": "J&T"},
        "token_meta": scan["token_meta"],
        "stats": {
            "shops_probed": scan["shops_probed"],
            "has_contract_shops": n_hd,
            "accounts_n": n_acc,
            "jnt_empty_shops": n_empty,
            "jnt_not_in_catalog": len(scan["jnt_not_in_catalog"]),
            "errors": n_err,
            "pipe_shops": scan["pipe_shops_n"],
            "pipe_inaccessible": len(scan["pipe_inaccessible"]),
        },
        "has_contract": [
            {
                "shop_id": r["shop_id"],
                "shop_name": r.get("shop_name"),
                "token": r.get("token"),
                "accounts_n": r.get("accounts_n"),
                "accounts": r.get("accounts"),
            }
            for r in scan["has_contract"]
        ],
        "jnt_empty": [
            {"shop_id": r["shop_id"], "shop_name": r.get("shop_name"), "token": r.get("token")}
            for r in scan["jnt_empty"]
        ],
        "jnt_not_in_catalog": [
            {"shop_id": r["shop_id"], "shop_name": r.get("shop_name")}
            for r in scan["jnt_not_in_catalog"]
        ],
        "errors": [
            {
                "shop_id": r["shop_id"],
                "shop_name": r.get("shop_name"),
                "status": r.get("status"),
                "error": r.get("error"),
                "attempts": r.get("attempts"),
            }
            for r in scan["errors"]
        ],
        "pipe_inaccessible_sample": scan["pipe_inaccessible"][:20],
        "all_shops": [
            {
                "shop_id": r["shop_id"],
                "shop_name": r.get("shop_name"),
                "status": r.get("status"),
                "accounts_n": r.get("accounts_n"),
                "token": r.get("token"),
            }
            for r in scan["results"]
        ],
        "db_upsert": db_info,
        "verdict": (
            f"✅ J&T HĐ all-shops: probed={scan['shops_probed']} · "
            f"có HĐ={n_hd} shop / {n_acc} accounts · rỗng={n_empty} · "
            f"lỗi={n_err} · pipe không mở={len(scan['pipe_inaccessible'])}"
        ),
        "next": [
            "Shop có HĐ: dùng accounts[].name / id làm mã HĐ J&T",
            "Shop jnt_empty: bật J&T trên icon ĐVVC của đúng shop",
            "Shop pipe inaccessible: cần token thuộc shop đó (vd. 1530618)",
            "python3 scripts/jnt_partner_contract_all_shops_mapper.py --notify",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    st = report.get("stats") or {}
    lines = [
        "🗺️ Mapper HĐ đối tác J&T · tất cả shop",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"Atlas: {report.get('atlas')}",
        "",
        "=== Token / shops ===",
    ]
    for t in report.get("token_meta") or []:
        lines.append(
            f"  · {t.get('token')}: http={t.get('http')} shops={t.get('shops_n')}"
            + (f" shop_id={t.get('shop_id')}" if t.get("shop_id") else "")
        )
    lines.append(
        f"  Probed={st.get('shops_probed')} · có HĐ={st.get('has_contract_shops')} · "
        f"accounts={st.get('accounts_n')} · empty={st.get('jnt_empty_shops')} · "
        f"not_in_catalog={st.get('jnt_not_in_catalog')} · err={st.get('errors')}"
    )
    lines.append("")
    lines.append("=== Shop CÓ hợp đồng J&T ===")
    if not report.get("has_contract"):
        lines.append("  (chưa có shop nào gắn accounts[] J&T trên token hiện có)")
    for r in report.get("has_contract") or []:
        lines.append(
            f"  ✅ {r.get('shop_id')} {r.get('shop_name')} · token={r.get('token')} · "
            f"accounts={r.get('accounts_n')}"
        )
        for a in (r.get("accounts") or [])[:8]:
            lines.append(
                f"      · id={a.get('id')} name={a.get('name')} "
                f"code={a.get('customer_code')} phone={a.get('phone')} email={a.get('email')}"
            )
    lines.append("")
    lines.append("=== J&T có trong catalog nhưng accounts rỗng ===")
    for r in (report.get("jnt_empty") or [])[:30]:
        lines.append(f"  ⚠ {r.get('shop_id')} {r.get('shop_name')} · {r.get('token')}")
    if len(report.get("jnt_empty") or []) > 30:
        lines.append(f"  … +{len(report['jnt_empty']) - 30}")
    lines.append("")
    lines.append("=== Không thấy J&T trong /partners ===")
    for r in (report.get("jnt_not_in_catalog") or [])[:15]:
        lines.append(f"  · {r.get('shop_id')} {r.get('shop_name')}")
    lines.append("")
    lines.append("=== Lỗi / không mở được ===")
    for r in (report.get("errors") or [])[:20]:
        lines.append(
            f"  ❌ {r.get('shop_id')} {r.get('shop_name')}: {r.get('status')} "
            f"{(r.get('error') or '')[:80]}"
        )
    if report.get("pipe_inaccessible_sample"):
        lines.append("")
        lines.append("=== Shop trong pipe DB nhưng token không mở partners ===")
        for p in report["pipe_inaccessible_sample"][:15]:
            lines.append(
                f"  · {p.get('shop_id')} {p.get('shop_name')} orders≈{p.get('orders_n')}"
            )
    if report.get("db_upsert"):
        lines.append("")
        lines.append(f"DB upsert: {report.get('db_upsert')}")
    lines.append("")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "jnt_partner_contract_all_shops.json"
    tp = REPORTS / "jnt_partner_contract_all_shops.txt"
    # báo cáo không chứa token; accounts chỉ id/name
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
    ap = argparse.ArgumentParser(
        description="Mapper HĐ J&T trên tất cả shop Pancake (owned)"
    )
    ap.add_argument("--extra-shop", action="append", default=[], help="thêm shop_id thử")
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--no-upsert", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    report = build_report(
        sleep_s=args.sleep,
        extra_shops=args.extra_shop,
        upsert_db=not args.no_upsert,
    )
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
