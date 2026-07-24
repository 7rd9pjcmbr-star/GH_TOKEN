#!/usr/bin/env python3
"""Bật / kết nối HĐ J&T (partner_id=15) trên tất cả shop owned.

Cần mã khách hàng J&T (owned) — Pancake không cho bật ĐVVC không có HĐ:
  JNT_CUSTOMER_CODE=...   (bắt buộc)
  JNT_PASSWORD=...        (nếu J&T cấp)
  JNT_PHONE=...           (tuỳ form)

Thử các endpoint/payload phổ biến rồi verify GET /partners → accounts[].

Owned-only · không bịa mã HĐ · secrets gitignored.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
STATE_PATH = SECRETS / "jnt_partner_enable_all_shops.state.json"
BASE = "https://pos.pages.fm/api/v1"
JNT_ID = 15


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


def http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    form: dict | None = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "null"
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"raw": raw[:300]}
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        try:
            return e.code, json.loads(raw.decode() or "null")
        except Exception:
            return e.code, {"raw": raw[:300].decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:200]}


def token_slots(env: dict[str, str]) -> list[tuple[str, str, str]]:
    out = []
    if (env.get("PANCAKE_POS_ACCESS_TOKEN") or "").strip():
        out.append(("primary", env["PANCAKE_POS_ACCESS_TOKEN"], "access_token"))
    if (env.get("PANCAKE_POS_SECONDARY_ACCESS_TOKEN") or "").strip():
        out.append(
            ("secondary", env["PANCAKE_POS_SECONDARY_ACCESS_TOKEN"], "access_token")
        )
    api = (env.get("PANCAKE_POS_API_KEY") or env.get("PANCAKE_API_KEY") or "").strip()
    shop = (env.get("PANCAKE_SHOP_ID") or "").strip()
    if api and shop:
        out.append(("api_key", api, "api_key"))
    return out


def list_shops(label: str, tok: str, mode: str) -> list[dict[str, Any]]:
    q = f"{mode}={urllib.parse.quote(tok)}"
    st, body = http_json(f"{BASE}/shops?{q}")
    shops = []
    if mode == "api_key":
        # single shop from env handled by caller
        return shops
    for s in (body.get("shops") if isinstance(body, dict) else None) or []:
        if isinstance(s, dict) and s.get("id"):
            shops.append(
                {"shop_id": str(s["id"]), "shop_name": s.get("name"), "token": label, "tok": tok, "mode": mode}
            )
    return shops


def get_jnt_accounts(shop_id: str, tok: str, mode: str) -> dict[str, Any]:
    q = f"{mode}={urllib.parse.quote(tok)}"
    st, body = http_json(f"{BASE}/shops/{shop_id}/partners?{q}")
    if st != 200 or not isinstance(body, dict):
        return {"http": st, "accounts": [], "ok": False}
    for p in body.get("data") or []:
        if str(p.get("id")) == str(JNT_ID):
            acc = p.get("accounts") or []
            return {
                "http": st,
                "accounts": acc if isinstance(acc, list) else [],
                "ok": True,
                "partner_name": p.get("name"),
            }
    return {"http": st, "accounts": [], "ok": True, "partner_name": None}


def attempt_enable(
    shop_id: str,
    tok: str,
    mode: str,
    *,
    customer_code: str,
    password: str | None,
    phone: str | None,
) -> list[dict[str, Any]]:
    """Thử nhiều path/payload — dừng khi accounts[] có phần tử."""
    q = f"{mode}={urllib.parse.quote(tok)}"
    code = customer_code.strip()
    attempts: list[dict[str, Any]] = []

    payloads: list[tuple[str, str, dict | None, dict | None]] = [
        # (path, method, json_body, form_body)
        (
            f"/shops/{shop_id}/partners/{JNT_ID}/accounts",
            "POST",
            {"name": code, "customer_id": code, "customer_code": code},
            None,
        ),
        (
            f"/shops/{shop_id}/partners/{JNT_ID}/accounts",
            "POST",
            {"customer_id": code, "password": password or ""},
            None,
        ),
        (
            f"/shops/{shop_id}/partners/{JNT_ID}/login",
            "POST",
            {"customer_id": code, "password": password or "", "phone": phone or ""},
            None,
        ),
        (
            f"/shops/{shop_id}/partners/{JNT_ID}/connect",
            "POST",
            {"customer_id": code, "password": password or ""},
            None,
        ),
        (
            f"/shops/{shop_id}/partners/connect",
            "POST",
            {"partner_id": JNT_ID, "customer_id": code, "password": password or ""},
            None,
        ),
        (
            f"/shops/{shop_id}/partners",
            "POST",
            {"id": JNT_ID, "customer_id": code, "password": password or ""},
            None,
        ),
        (
            f"/shops/{shop_id}/partners/{JNT_ID}/accounts",
            "POST",
            None,
            {
                "customer_id": code,
                "password": password or "",
                "phone": phone or "",
                "name": code,
            },
        ),
        (
            f"/shops/{shop_id}/partners/{JNT_ID}/login",
            "POST",
            None,
            {"customer_id": code, "password": password or ""},
        ),
    ]

    for path, method, jbody, form in payloads:
        url = f"{BASE}{path}?{q}"
        st, body = http_json(url, method=method, body=jbody, form=form)
        attempts.append(
            {
                "path": path,
                "method": method,
                "http": st,
                "body_keys": list(jbody.keys()) if jbody else list((form or {}).keys()),
                "response": body if not isinstance(body, dict) else {
                    k: body.get(k)
                    for k in ("success", "message", "error", "errors", "data")
                    if k in body
                }
                or {"raw": str(body)[:160]},
            }
        )
        # verify
        time.sleep(0.15)
        verify = get_jnt_accounts(shop_id, tok, mode)
        if verify.get("accounts"):
            attempts[-1]["verified_accounts"] = [
                {"id": a.get("id"), "name": a.get("name")}
                for a in verify["accounts"]
                if isinstance(a, dict)
            ]
            attempts[-1]["enabled"] = True
            break
        attempts[-1]["enabled"] = False
    return attempts


def build_report(
    *,
    customer_code: str | None = None,
    password: str | None = None,
    phone: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    env = load_env()
    code = (customer_code or env.get("JNT_CUSTOMER_CODE") or env.get("JT_CUSTOMER_CODE") or "").strip()
    pw = (password or env.get("JNT_PASSWORD") or env.get("JT_PASSWORD") or "").strip() or None
    ph = (phone or env.get("JNT_PHONE") or env.get("JT_PHONE") or "").strip() or None

    slots = token_slots(env)
    shops: dict[str, dict[str, Any]] = {}
    for label, tok, mode in slots:
        if mode == "api_key":
            sid = (env.get("PANCAKE_SHOP_ID") or "").strip()
            if sid:
                shops.setdefault(
                    sid,
                    {
                        "shop_id": sid,
                        "shop_name": None,
                        "token": label,
                        "tok": tok,
                        "mode": mode,
                    },
                )
            continue
        for s in list_shops(label, tok, mode):
            shops.setdefault(s["shop_id"], s)

    if not code:
        return {
            "ok": False,
            "module": "jnt_partner_enable_all_shops",
            "checked_at": utc_now(),
            "blocked": True,
            "reason": "Thiếu JNT_CUSTOMER_CODE (mã khách hàng J&T owned)",
            "shops_ready": [
                {"shop_id": s["shop_id"], "shop_name": s.get("shop_name"), "token": s.get("token")}
                for s in shops.values()
            ],
            "how": [
                "Lấy mã KH từ hợp đồng J&T Express (sau khi ký HĐ).",
                "Đặt vào secrets/backend_pipes.env: JNT_CUSTOMER_CODE=... (JNT_PASSWORD=... nếu có)",
                "Chạy: python3 scripts/jnt_partner_enable_all_shops.py --notify",
                "Hoặc: --customer-code '<mã>' --password '<mk>'",
            ],
            "verdict": (
                f"❌ Chưa bật được J&T — thiếu mã KH. "
                f"Có {len(shops)} shop owned sẵn sàng kết nối khi có mã."
            ),
            "policy": "owned-only · không bịa mã HĐ / không dump-login",
        }

    results = []
    enabled_n = 0
    for sid, s in sorted(shops.items()):
        before = get_jnt_accounts(sid, s["tok"], s["mode"])
        row: dict[str, Any] = {
            "shop_id": sid,
            "shop_name": s.get("shop_name"),
            "token": s.get("token"),
            "before_accounts_n": len(before.get("accounts") or []),
            "before_accounts": [
                {"id": a.get("id"), "name": a.get("name")}
                for a in (before.get("accounts") or [])
                if isinstance(a, dict)
            ],
        }
        if row["before_accounts_n"] > 0:
            row["status"] = "already_enabled"
            row["after_accounts"] = row["before_accounts"]
            enabled_n += 1
            results.append(row)
            continue
        if dry_run:
            row["status"] = "dry_run_skip"
            results.append(row)
            continue
        attempts = attempt_enable(
            sid,
            s["tok"],
            s["mode"],
            customer_code=code,
            password=pw,
            phone=ph,
        )
        row["attempts"] = attempts
        after = get_jnt_accounts(sid, s["tok"], s["mode"])
        row["after_accounts_n"] = len(after.get("accounts") or [])
        row["after_accounts"] = [
            {"id": a.get("id"), "name": a.get("name")}
            for a in (after.get("accounts") or [])
            if isinstance(a, dict)
        ]
        if row["after_accounts_n"] > 0:
            row["status"] = "enabled"
            enabled_n += 1
        else:
            row["status"] = "failed"
        results.append(row)
        time.sleep(0.1)

    ok_any = any(r.get("status") in {"enabled", "already_enabled"} for r in results)
    report = {
        "ok": ok_any,
        "module": "jnt_partner_enable_all_shops",
        "checked_at": utc_now(),
        "blocked": False,
        "customer_code_set": True,
        "customer_code_len": len(code),
        "password_set": bool(pw),
        "shops_n": len(results),
        "enabled_n": enabled_n,
        "failed_n": sum(1 for r in results if r.get("status") == "failed"),
        "results": results,
        "verdict": (
            f"{'✅' if ok_any else '❌'} Bật J&T: enabled/already={enabled_n}/{len(results)} · "
            f"failed={sum(1 for r in results if r.get('status')=='failed')}"
        ),
        "policy": "owned-only · mã KH từ env · không commit secrets",
        "next": [
            "python3 scripts/jnt_partner_contract_all_shops_mapper.py --notify",
            "Kiểm tra icon ĐVVC trên POS nếu API path chưa khớp UI",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🔌 Bật HĐ J&T trên tất cả shop",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        "",
    ]
    if report.get("blocked"):
        lines.append(f"Blocked: {report.get('reason')}")
        lines.append(f"Shop sẵn sàng: {len(report.get('shops_ready') or [])}")
        for s in (report.get("shops_ready") or [])[:15]:
            lines.append(f"  · {s.get('shop_id')} {s.get('shop_name')} ({s.get('token')})")
        lines.append("")
        lines.append("Cách bật:")
        for h in report.get("how") or []:
            lines.append(f"  · {h}")
        return "\n".join(lines)

    lines.append(
        f"Mã KH set=yes (len={report.get('customer_code_len')}) · "
        f"password_set={report.get('password_set')}"
    )
    lines.append("")
    for r in report.get("results") or []:
        flag = {
            "enabled": "✅",
            "already_enabled": "✅",
            "failed": "❌",
            "dry_run_skip": "⏸",
        }.get(r.get("status") or "", "·")
        lines.append(
            f"  {flag} {r.get('shop_id')} {r.get('shop_name')}: {r.get('status')} · "
            f"before={r.get('before_accounts_n')} after={r.get('after_accounts_n', r.get('before_accounts_n'))}"
        )
        for a in (r.get("after_accounts") or r.get("before_accounts") or [])[:5]:
            lines.append(f"      · id={a.get('id')} name={a.get('name')}")
        if r.get("status") == "failed" and r.get("attempts"):
            last = r["attempts"][-1]
            lines.append(
                f"      last try {last.get('path')} → http={last.get('http')} "
                f"{str(last.get('response'))[:100]}"
            )
    lines.append("")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "jnt_partner_enable_all_shops.json"
    tp = REPORTS / "jnt_partner_enable_all_shops.txt"
    # never write customer code into report
    safe = {k: v for k, v in report.items()}
    jp.write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    STATE_PATH.write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "blocked": report.get("blocked"),
                "enabled_n": report.get("enabled_n"),
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
    ap = argparse.ArgumentParser(description="Bật HĐ J&T trên tất cả shop owned")
    ap.add_argument("--customer-code", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--phone", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    report = build_report(
        customer_code=args.customer_code,
        password=args.password,
        phone=args.phone,
        dry_run=args.dry_run,
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
    return 0 if report.get("ok") or report.get("blocked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
