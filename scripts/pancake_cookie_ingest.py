#!/usr/bin/env python3
"""Nhúng nạp cookie/token Pancake → secrets → (optional) quét bưu cục.

Luôn đi qua nginx embed khi gọi từ panel/CLI:
  POST /v1/pancake/ingest
  POST /v1/token/pancake-ingest

Owned-only. Không dump-login.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "reports" / "telegram-classify"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64json(seg: str) -> dict[str, Any]:
    pad = "=" * (-len(seg) % 4)
    return json.loads(base64.urlsafe_b64decode(seg + pad).decode("utf-8"))


def decode_jwt(token: str) -> dict[str, Any] | None:
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        return _b64json(parts[1])
    except Exception:
        # try common paste corruption fix (iss/facebook segment)
        fixed = token.replace(
            "HZaMmx1WDNObGMzTnBiMjRpT201MWJHd3NJbVpp",
            "aHR0cHM6Ly93d3cuZmFjZWJvb2suY29t",
            1,
        )
        try:
            return _b64json(fixed.split(".")[1])
        except Exception:
            return None


def extract_from_text(raw: str) -> dict[str, Any]:
    """Parse Netscape cookie lines / bare JWT / key=value."""
    text = (raw or "").strip()
    found: dict[str, str] = {}
    jwts: list[str] = []

    # Netscape cookie: domain flag path secure expiry name value
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t+", line)
        if len(parts) >= 7 and "pancake" in parts[0].lower():
            name = parts[5].strip()
            value = parts[6].strip()
            found[name] = value
            if value.startswith("eyJ"):
                jwts.append(value)
            continue
        # name=value
        if "=" in line and not line.startswith("eyJ"):
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and v:
                found[k] = v
                if v.startswith("eyJ"):
                    jwts.append(v)
        if line.startswith("eyJ") and line.count(".") == 2:
            jwts.append(line.split()[0])

    # bare JWT anywhere
    for m in re.finditer(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", text):
        jwts.append(m.group(0))

    pos_jwt = found.get("pos_jwt") or ""
    token_cookie = found.get("token") or ""
    nested = None
    outer_payload = None
    for cand in [pos_jwt, token_cookie, *jwts]:
        if not cand:
            continue
        pl = decode_jwt(cand)
        if not pl:
            continue
        if pl.get("session_id") is not None or pl.get("application") == 1:
            # looks like pos_jwt
            if not pos_jwt:
                pos_jwt = cand
            outer_payload = outer_payload or pl
        if pl.get("accessToken") and isinstance(pl.get("accessToken"), str):
            nested = pl["accessToken"]
            outer_payload = pl
            if not token_cookie:
                token_cookie = cand

    # Prefer explicit pos_jwt; else nested accessToken if pos_jwt-shaped; else token cookie
    chosen = pos_jwt or nested or token_cookie or (jwts[0] if jwts else "")
    chosen_payload = decode_jwt(chosen) if chosen else None

    return {
        "pos_jwt": pos_jwt or None,
        "token_cookie": token_cookie or None,
        "nested_access_token": nested,
        "chosen_token": chosen or None,
        "chosen_payload": chosen_payload,
        "outer_payload": outer_payload,
        "pos_locale": found.get("pos_locale"),
        "pos_country": found.get("pos_country"),
        "cookies_found": sorted(found.keys()),
        "jwt_count": len(set(jwts)),
    }


def token_expiry_info(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"has_exp": False, "expired": None}
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return {"has_exp": False, "expired": None, "payload_keys": list(payload.keys())}
    now = int(datetime.now(timezone.utc).timestamp())
    return {
        "has_exp": True,
        "exp": int(exp),
        "exp_iso": datetime.fromtimestamp(int(exp), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now": now,
        "expired": now > int(exp),
        "name": payload.get("name") or payload.get("fb_name"),
        "uid": payload.get("uid"),
        "fb_id": payload.get("fb_id") or payload.get("userID") or payload.get("id"),
        "session_id": payload.get("session_id"),
    }


def probe_shops(token: str) -> dict[str, Any]:
    import requests

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Cookie": f"pos_jwt={token}; pos_locale=vi",
    }
    try:
        r = requests.get("https://pos.pancake.vn/api/v1/shops", headers=headers, timeout=25)
        data = r.json() if r.text else {}
        shops = data.get("shops") if isinstance(data, dict) else None
        return {
            "http": r.status_code,
            "success": bool(isinstance(shops, list)),
            "message": data.get("message") if isinstance(data, dict) else None,
            "error_code": data.get("error_code") if isinstance(data, dict) else None,
            "shops": [{"id": s.get("id"), "name": s.get("name")} for s in (shops or [])][:50]
            if isinstance(shops, list)
            else [],
            "shops_n": len(shops) if isinstance(shops, list) else 0,
        }
    except Exception as e:  # noqa: BLE001
        return {"http": 0, "success": False, "message": str(e)[:160], "shops": [], "shops_n": 0}


def apply_token(token: str, *, probe: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    from access_token_rotate import set_access_token, upsert_env_values

    shop_ids = [str(s["id"]) for s in (probe.get("shops") or []) if s.get("id")]
    set_report = set_access_token(
        "Pancake",
        token,
        user=str(meta.get("name") or "") or None,
        shop_id=shop_ids[0] if shop_ids else None,
    )
    extras = {
        "PANCAKE_POS_ACCESS_TOKEN": token,
        "PANCAKE_POS_SHOP_IDS": ",".join(shop_ids[:50]),
        "PANCAKE_SECONDARY_SHOP_IDS": ",".join(shop_ids[1:50]),
    }
    if shop_ids:
        extras["PANCAKE_SHOP_ID"] = shop_ids[0]
    if meta.get("name"):
        extras["PANCAKE_USER"] = str(meta["name"])
    if meta.get("fb_id"):
        extras["PANCAKE_PAGE_ID"] = str(meta["fb_id"])  # best-effort; may be fb user id
    if meta.get("uid"):
        extras["PANCAKE_ACCOUNT_UID"] = str(meta["uid"])
    upsert_env_values(extras)
    return {"set": set_report, "shop_ids": shop_ids, "extras_keys": sorted(extras.keys())}


def ingest_and_scan(
    raw: str,
    *,
    days: int = 3,
    limit: int = 10000,
    scan: bool = True,
    notify: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Parse cookie/JWT → validate → set secrets → optional buucuc scan."""
    extracted = extract_from_text(raw)
    token = extracted.get("chosen_token") or ""
    payload = extracted.get("chosen_payload")
    exp_info = token_expiry_info(payload)

    report: dict[str, Any] = {
        "ok": False,
        "module": "pancake_cookie_ingest",
        "checked_at": utc_now(),
        "via": "nginx→upstream→pancake_cookie_ingest",
        "extracted": {
            "cookies_found": extracted.get("cookies_found"),
            "jwt_count": extracted.get("jwt_count"),
            "has_pos_jwt": bool(extracted.get("pos_jwt")),
            "has_token_cookie": bool(extracted.get("token_cookie")),
            "has_nested": bool(extracted.get("nested_access_token")),
            "chosen_is_pos_jwt_shape": bool(
                payload and (payload.get("session_id") is not None or payload.get("application") == 1)
            ),
        },
        "expiry": exp_info,
        "probe": None,
        "apply": None,
        "scan": None,
        "policy": {"owned_only": True, "via_nginx_required": True, "no_dump_login": True},
    }

    if not token:
        report["error"] = "Không tìm thấy JWT/pos_jwt trong payload"
        report["need"] = "Gửi cookie pos.pancake.vn pos_jwt=eyJ... (còn hạn)"
        report["verdict"] = "❌ Thiếu token — chưa nhúng được"
        return report

    if exp_info.get("expired") and not force:
        report["error"] = "Token hết hạn"
        report["need"] = "Gửi pos_jwt còn hạn"
        report["verdict"] = (
            f"❌ Token hết hạn (exp={exp_info.get('exp_iso')}) — "
            f"account={exp_info.get('name')} — không ghi đè / không quét"
        )
        write_outputs(report)
        return report

    probe = probe_shops(token)
    report["probe"] = {k: v for k, v in probe.items() if k != "shops"}
    report["shops"] = probe.get("shops") or []

    if not probe.get("success"):
        report["error"] = probe.get("message") or "probe_failed"
        report["error_code"] = probe.get("error_code")
        report["verdict"] = (
            f"❌ API từ chối token (http={probe.get('http')} · {probe.get('message')}) — "
            f"account={exp_info.get('name')} — cần pos_jwt hợp lệ"
        )
        write_outputs(report)
        return report

    apply = apply_token(token, probe=probe, meta=exp_info)
    report["apply"] = apply
    report["ok"] = True

    if scan:
        from scan_buucuc_orders import build_report

        scan_report = build_report(
            days=days,
            limit=limit,
            backends=["Pancake"],
            pipe=True,
            write_cache=True,
            notify=notify,
        )
        report["scan"] = {
            "count": scan_report.get("count"),
            "verdict": scan_report.get("verdict"),
            "by_shop": scan_report.get("by_shop") or scan_report.get("by_buucuc"),
            "by_day": scan_report.get("by_day"),
            "blockers": scan_report.get("blockers"),
            "shops_n": len(probe.get("shops") or []),
        }
        report["orders_count"] = scan_report.get("count")
        report["orders_preview"] = (scan_report.get("orders") or [])[:10]
        report["verdict"] = (
            f"✅ Đã nhúng nginx→token→scan · account={exp_info.get('name')} · "
            f"shops={probe.get('shops_n')} · orders={scan_report.get('count')}/{limit} / {days}d"
        )
    else:
        report["verdict"] = (
            f"✅ Đã nhúng token qua nginx · account={exp_info.get('name')} · "
            f"shops={probe.get('shops_n')} · (chưa scan)"
        )

    write_outputs(report)
    return report


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    OUT.mkdir(parents=True, exist_ok=True)
    jp = OUT / "pancake_cookie_ingest.json"
    tp = OUT / "pancake_cookie_ingest.txt"
    slim = {k: v for k, v in report.items() if k != "orders_preview"}
    jp.write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🍪 PANCAKE COOKIE INGEST (nginx embed)",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
    ]
    exp = report.get("expiry") or {}
    if exp:
        lines.append(
            f"Account: {exp.get('name')} · uid={exp.get('uid')} · fb={exp.get('fb_id')} · "
            f"exp={exp.get('exp_iso')} · expired={exp.get('expired')}"
        )
    probe = report.get("probe") or {}
    if probe:
        lines.append(f"Probe: http={probe.get('http')} success={probe.get('success')} shops={probe.get('shops_n')}")
    scan = report.get("scan") or {}
    if scan:
        lines.append(f"Scan: count={scan.get('count')} · {scan.get('verdict')}")
    if report.get("need"):
        lines.append(f"Need: {report.get('need')}")
    if report.get("error"):
        lines.append(f"Error: {report.get('error')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Nhúng cookie/token Pancake → scan (qua module ingest)")
    ap.add_argument("--raw-file", help="File chứa cookie Netscape / JWT")
    ap.add_argument("--raw", help="Chuỗi cookie/JWT trực tiếp")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--limit", type=int, default=10000)
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raw = args.raw or ""
    if args.raw_file:
        raw = Path(args.raw_file).read_text(encoding="utf-8", errors="ignore")
    if not raw.strip():
        print("Cần --raw hoặc --raw-file", file=sys.stderr)
        return 2
    report = ingest_and_scan(
        raw,
        days=args.days,
        limit=args.limit,
        scan=not args.no_scan,
        notify=args.notify,
        force=args.force,
    )
    if args.json:
        print(json.dumps({k: v for k, v in report.items()}, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
