#!/usr/bin/env python3
"""Mapper icon — host GHN gateway (ghn.gateway.online.vn → online-gateway.ghn.vn).

Ánh xạ alias host → canonical GHN shiip API + quân đội icon (network/key/lock).
Owned-only · không dump-login.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"

# Alias người dùng / biến thể → host chuẩn GHN API
GHN_GATEWAY_ALIASES: dict[str, str] = {
    "ghn.gateway.online.vn": "online-gateway.ghn.vn",
    "online.gateway.ghn.vn": "online-gateway.ghn.vn",
    "gateway.online.vn": "online-gateway.ghn.vn",
    "gateway.ghn.vn": "online-gateway.ghn.vn",
    "api.ghn.vn": "online-gateway.ghn.vn",
    "online-gateway.ghn.vn": "online-gateway.ghn.vn",
    "dev-online-gateway.ghn.vn": "dev-online-gateway.ghn.vn",
    "sso.ghn.vn": "sso.ghn.vn",
    "sso-v2.ghn.vn": "sso-v2.ghn.vn",
    "hopdongdientu.ghn.vn": "hopdongdientu.ghn.vn",
    "5sao.ghn.vn": "5sao.ghn.vn",
}

# Vai trò host → icon lead (NETWORK_MAP / realtime_icon_feedback)
HOST_ICON_ROLE: dict[str, dict[str, str]] = {
    "online-gateway.ghn.vn": {
        "platform": "GHN",
        "role": "shiip_api",
        "icon": "network",
        "call": "Mạch Mạng",
        "motto": "GHN shiip public-api / printA5",
        "base_paths": (
            "/shiip/public-api",
            "/a5/public-api/printA5",
        ),
    },
    "dev-online-gateway.ghn.vn": {
        "platform": "GHN",
        "role": "shiip_api_dev",
        "icon": "network",
        "call": "Mạch Mạng Dev",
        "motto": "GHN shiip sandbox",
        "base_paths": ("/shiip/public-api",),
    },
    "sso.ghn.vn": {
        "platform": "GHN",
        "role": "sso_portal",
        "icon": "lock",
        "call": "Ổ Khóa SSO",
        "motto": "portal login — không dùng dump",
        "base_paths": ("/v2/ssoLogin",),
    },
    "sso-v2.ghn.vn": {
        "platform": "GHN",
        "role": "sso_jwt_v2",
        "icon": "lock",
        "call": "Ổ Khóa SSO-v2",
        "motto": "JWT login /sso/jwt/login → id_token (hopdongdientu)",
        "base_paths": (
            "/sso/jwt/login",
            "/sso/jwt/logout",
            "/auth-callback",
        ),
    },
    "hopdongdientu.ghn.vn": {
        "platform": "GHN",
        "role": "econtract",
        "icon": "lock",
        "call": "Hợp Đồng Điện Tử",
        "motto": "redirect_uri authorize nhận id_token SSO",
        "base_paths": ("/authorize",),
    },
    "5sao.ghn.vn": {
        "platform": "GHN",
        "role": "staff_portal",
        "icon": "lock",
        "call": "Ổ Khóa 5sao",
        "motto": "nhân sự bưu cục — topology_only",
        "base_paths": ("/Home/Login",),
    },
}

PROBE_PATH = "/shiip/public-api/master-data/province"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_host(raw: str) -> str:
    h = (raw or "").strip().lower()
    h = re.sub(r"^https?://", "", h)
    h = h.split("/")[0].split("?")[0].strip(".")
    return h


def resolve_alias(host: str) -> dict[str, Any]:
    h = normalize_host(host)
    canonical = GHN_GATEWAY_ALIASES.get(h)
    if not canonical:
        # fuzzy: contains gateway + ghn / online
        if "ghn" in h and ("gateway" in h or "shiip" in h or "online" in h):
            canonical = "online-gateway.ghn.vn"
        else:
            canonical = h
    meta = dict(HOST_ICON_ROLE.get(canonical) or {
        "platform": "GHN" if "ghn" in canonical else "unknown",
        "role": "unknown_host",
        "icon": "wrench",
        "call": "Cờ Lê Sự Cố",
        "motto": "host chưa trong atlas",
        "base_paths": (),
    })
    return {
        "input": host,
        "normalized": h,
        "canonical": canonical,
        "is_alias": h != canonical and h in GHN_GATEWAY_ALIASES,
        "alias_of": canonical if h != canonical else None,
        **meta,
    }


def dns_lookup(host: str) -> dict[str, Any]:
    try:
        ips = sorted({i[4][0] for i in socket.getaddrinfo(host, 443)})
        return {"ok": True, "ips": ips[:8]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120], "ips": []}


def probe_shiip(host: str, *, token: str | None = None) -> dict[str, Any]:
    import requests

    url = f"https://{host}{PROBE_PATH}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Token"] = token
    try:
        r = requests.get(url, headers=headers, timeout=15)
        body = r.json() if r.text and r.headers.get("content-type", "").startswith("application/json") else None
        msg = body.get("message") if isinstance(body, dict) else (r.text or "")[:80]
        code = body.get("code") if isinstance(body, dict) else None
        # 401 with token message = host alive but auth needed/fail
        alive = r.status_code in (200, 401, 403) or code in (200, 401, 403)
        return {
            "ok": r.status_code == 200 and code in (200, 0, None),
            "alive_host": alive,
            "http": r.status_code,
            "api_code": code,
            "message": msg,
            "url": url,
            "auth": "ok" if r.status_code == 200 and code == 200 else (
                "fail" if r.status_code in (401, 403) or code in (401, 403) else "unknown"
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "alive_host": False,
            "http": 0,
            "message": str(e)[:160],
            "url": url,
            "auth": "unreachable",
        }


def load_ghn_token() -> str:
    from owned_credentials import load_env

    env = load_env(extra_files=(SECRETS / "order_session.env", SECRETS / "mapper_icon_aes.env"))
    return (env.get("GHN_API_TOKEN") or env.get("GHN_TOKEN") or "").strip()


def icon_feedback(mapped: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    from realtime_icon_feedback_mapper import chant, feedback_line

    icons = [mapped.get("icon") or "network"]
    if probe.get("auth") == "fail" or not load_ghn_token():
        icons.append("key")
    if probe.get("auth") == "fail":
        icons.append("lock")
    if probe.get("alive_host"):
        icons.append("monitor")
    if not probe.get("alive_host"):
        icons.append("wrench")
    icons = list(dict.fromkeys(icons))
    detail = (
        f"{mapped.get('canonical')} · role={mapped.get('role')} · "
        f"dns/http={probe.get('http')} auth={probe.get('auth')}"
    )
    return {
        "icons": icons,
        "icon_chant": chant(icons),
        "feedback": feedback_line(icons, detail),
        "channel": "ghn",
        "status": (
            "alive" if probe.get("ok") else (
                "auth_fail" if probe.get("auth") == "fail" else (
                    "error" if not probe.get("alive_host") else "missing_cred"
                )
            )
        ),
    }


def map_host(host: str = "ghn.gateway.online.vn", *, probe: bool = True) -> dict[str, Any]:
    mapped = resolve_alias(host)
    dns = dns_lookup(mapped["normalized"])
    dns_can = dns_lookup(mapped["canonical"]) if mapped["canonical"] != mapped["normalized"] else dns
    token = load_ghn_token()
    probe_raw = {"skipped": True}
    probe_can: dict[str, Any] = {"skipped": True}
    if probe:
        # only probe if DNS ok — else mark unreachable
        if dns.get("ok"):
            probe_raw = probe_shiip(mapped["normalized"], token=token or None)
        else:
            probe_raw = {
                "ok": False,
                "alive_host": False,
                "http": 0,
                "message": dns.get("error") or "NXDOMAIN",
                "auth": "unreachable",
                "url": f"https://{mapped['normalized']}{PROBE_PATH}",
            }
        if mapped["canonical"] != mapped["normalized"]:
            if dns_can.get("ok"):
                probe_can = probe_shiip(mapped["canonical"], token=token or None)
            else:
                probe_can = {
                    "ok": False,
                    "alive_host": False,
                    "http": 0,
                    "message": dns_can.get("error") or "NXDOMAIN",
                    "auth": "unreachable",
                }

    fb = icon_feedback(mapped, probe_can if probe_can.get("alive_host") else probe_raw)

    paths = []
    for bp in mapped.get("base_paths") or ():
        paths.append(f"https://{mapped['canonical']}{bp}")

    report = {
        "ok": bool(dns_can.get("ok") or dns.get("ok")),
        "module": "ghn_gateway_icon_mapper",
        "checked_at": utc_now(),
        "query": f"Mapper icon · {host}",
        "mapped": mapped,
        "dns": {"input": dns, "canonical": dns_can},
        "probe": {"input": probe_raw, "canonical": probe_can},
        "token_present": bool(token),
        "icon": fb,
        "urls": paths,
        "aliases_catalog": sorted(GHN_GATEWAY_ALIASES.keys()),
        "policy": {"owned_only": True, "no_dump_login": True},
        "next_actions": [
            "python3 scripts/ghn_gateway_icon_mapper.py --host ghn.gateway.online.vn",
            "python3 scripts/ghn_cookie_ingest.py ensure",
            "printf '%s\\n' 'https://online-gateway.ghn.vn/a5/public-api/printA5?token=<owned>' > secrets/ghn_session.raw",
        ],
    }

    if mapped["normalized"] != mapped["canonical"] and not dns.get("ok") and dns_can.get("ok"):
        report["verdict"] = (
            f"↪ Alias `{mapped['normalized']}` NXDOMAIN → canonical "
            f"`{mapped['canonical']}` OK · icon={fb['icon_chant']} · "
            f"auth={probe_can.get('auth')} token={'set' if token else 'missing'}"
        )
        report["ok"] = True
    elif dns_can.get("ok") or dns.get("ok"):
        report["verdict"] = (
            f"✅ Host map `{mapped['canonical']}` · {fb['icon_chant']} · "
            f"auth={((probe_can if probe_can.get('alive_host') else probe_raw) or {}).get('auth')}"
        )
    else:
        report["verdict"] = (
            f"❌ Host `{mapped['normalized']}` không DNS · canonical "
            f"`{mapped['canonical']}` cũng fail · {fb['icon_chant']}"
        )
    return report


def upsert_order_api_hosts(canonical: str) -> None:
    """Ghi canonical vào ORDER_API_HOSTS (meta) nếu chưa có."""
    from access_token_rotate import upsert_env_values
    from owned_credentials import load_env

    env = load_env(extra_files=(SECRETS / "order_session.env",))
    cur = (env.get("ORDER_API_HOSTS") or "").strip()
    parts = [p.strip() for p in cur.split(",") if p.strip()]
    if canonical not in parts:
        parts.append(canonical)
        upsert_env_values({"ORDER_API_HOSTS": ",".join(parts[:24])})
        try:
            from order_session_env import export_session_env

            export_session_env()
        except Exception:
            pass


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🗺 MAPPER ICON · GHN GATEWAY",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
    ]
    m = report.get("mapped") or {}
    lines.append(
        f"Map: {m.get('normalized')} → {m.get('canonical')} "
        f"(alias={m.get('is_alias')}) · platform={m.get('platform')} · role={m.get('role')}"
    )
    lines.append(
        f"Icon: {m.get('icon')} · {m.get('call')} — {m.get('motto')}"
    )
    ic = report.get("icon") or {}
    lines.append(f"Chant: {ic.get('icon_chant')}")
    lines.append(f"Feedback: {ic.get('feedback')}")
    dns = report.get("dns") or {}
    lines.append(f"DNS input: {dns.get('input')}")
    lines.append(f"DNS canonical: {dns.get('canonical')}")
    pr = report.get("probe") or {}
    lines.append(f"Probe input: {pr.get('input')}")
    lines.append(f"Probe canonical: {pr.get('canonical')}")
    lines.append(f"GHN_API_TOKEN present: {report.get('token_present')}")
    if report.get("urls"):
        lines.append("URLs:")
        for u in report["urls"]:
            lines.append(f"  · {u}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "ghn_gateway_icon_mapper.json"
    tp = REPORTS / "ghn_gateway_icon_mapper.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mapper icon GHN gateway host")
    ap.add_argument("--host", default="ghn.gateway.online.vn")
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--register-hosts", action="store_true", help="Ghi canonical vào ORDER_API_HOSTS")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = map_host(args.host, probe=not args.no_probe)
    if args.register_hosts and (report.get("mapped") or {}).get("canonical"):
        upsert_order_api_hosts(report["mapped"]["canonical"])
        report["registered_order_api_hosts"] = True
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
