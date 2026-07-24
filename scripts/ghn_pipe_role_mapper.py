#!/usr/bin/env python3
"""Mapper đường ống dẫn + role liên quan GHN.

Gom atlas:
  · Đường ống (pipe stages): nguồn token → auth → roles → gọi đơn → scan/nginx
  · Role endpoint: list/search/detail (+ support/blocked)
  · Role host: shiip / sso-v2 / hopdongdientu / printA5
  · Trạng thái sống từ secrets/*.state + probe nhẹ

Owned-only · no dump-login · không mutate đơn.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
STATE_PATH = SECRETS / "ghn_pipe_role_mapper.state.json"

# ── Pipe stages (đường ống dẫn) ─────────────────────────────────
PIPE_STAGES: list[dict[str, Any]] = [
    {
        "id": "src.printA5",
        "stage": "source",
        "title": "printA5 URL → Token UUID",
        "role": "print",
        "host_role": "shiip_api",
        "cli": "ghn_access_token_orders.py run --printA5 <URL>",
        "module": "ghn_cookie_ingest.ingest_printA5",
        "nginx": "POST /v1/ghn/ingest",
        "secrets": ["ghn_session.raw", "GHN_API_TOKEN"],
        "next": ["auth.ensure"],
    },
    {
        "id": "src.cookie",
        "stage": "source",
        "title": "Cookie Netscape / token=UUID",
        "role": "token",
        "host_role": "shiip_api",
        "cli": "ghn_cookie_ingest.py --raw-file FILE",
        "module": "ghn_cookie_ingest",
        "nginx": "POST /v1/ghn/ingest",
        "secrets": ["ghn_session.raw", "GHN_API_TOKEN"],
        "next": ["auth.ensure"],
    },
    {
        "id": "src.frida_a11y",
        "stage": "source",
        "title": "Frida + Accessibility capture",
        "role": "token",
        "host_role": "shiip_api",
        "cli": "frida_a11y_ghn_bridge.py apply --orders",
        "module": "frida_a11y_ghn_bridge",
        "nginx": "POST /v1/ghn/frida-a11y",
        "secrets": ["frida_a11y_ghn.pending.json", "GHN_API_TOKEN"],
        "next": ["auth.ensure", "orders.fetch"],
    },
    {
        "id": "src.sso_jwt",
        "stage": "source",
        "title": "SSO-v2 JWT (hopdongdientu id_token)",
        "role": "token",
        "host_role": "sso_jwt_v2",
        "cli": "ghn_sso_jwt_bridge.py analyze|ingest",
        "module": "ghn_sso_jwt_bridge",
        "nginx": None,
        "secrets": ["ghn_sso_login.url", "GHN_SSO_ID_TOKEN", "GHN_SSO_APP_KEY"],
        "note": "id_token SSO ≠ GHN_API_TOKEN shiip",
        "next": ["auth.sso_service"],
    },
    {
        "id": "src.captcha_tg",
        "stage": "source",
        "title": "Telegram hộp thoại → captcha",
        "role": "token",
        "host_role": "sso_jwt_v2",
        "cli": "telegram_captcha_pull.py run",
        "module": "telegram_captcha_pull",
        "nginx": None,
        "secrets": ["captcha.pending.json", "captcha.pending.txt"],
        "note": "Hỗ trợ OTP/SSO — không thay Token shiip",
        "next": ["src.sso_jwt"],
    },
    {
        "id": "src.proxy_bind",
        "stage": "source",
        "title": "Telegram proxy → bind token",
        "role": "token",
        "host_role": "shiip_api",
        "cli": "telegram_proxy_pull.py && token_proxy_bind.py bind",
        "module": "token_proxy_bind",
        "nginx": "POST /v1/ghn/token-proxy-orders",
        "secrets": ["proxies.live.txt", "ghn_tokens.owned.txt"],
        "next": ["orders.proxy"],
    },
    {
        "id": "auth.ensure",
        "stage": "auth",
        "title": "Ensure GHN_API_TOKEN (probe province)",
        "role": "master",
        "host_role": "shiip_api",
        "cli": "ghn_cookie_ingest.py ensure",
        "module": "ghn_cookie_ingest.ensure_ghn_session",
        "endpoint": "/shiip/public-api/master-data/province",
        "secrets": ["GHN_API_TOKEN", "ghn_session.state.json"],
        "next": ["roles.apply", "auth.shop"],
    },
    {
        "id": "auth.shop",
        "stage": "auth",
        "title": "Resolve GHN_SHOP_ID",
        "role": "shop",
        "host_role": "shiip_api",
        "cli": "ghn_access_token_orders.py shop",
        "module": "ghn_access_token_orders.resolve_shop_id",
        "endpoint": "/shiip/public-api/v2/shop/all",
        "secrets": ["GHN_SHOP_ID"],
        "next": ["orders.fetch"],
    },
    {
        "id": "auth.sso_service",
        "stage": "auth",
        "title": "SSO staff → gen-service-token (app_key)",
        "role": "token",
        "host_role": "sso_jwt_v2",
        "cli": "ghn_sso_jwt_bridge.py ingest --url <callback>",
        "module": "ghn_sso_jwt_bridge",
        "endpoint": "/sso-v2/public-api/staff/gen-service-token",
        "secrets": ["GHN_SSO_ID_TOKEN"],
        "note": "App hopdongdientu — không feed scan shiip",
        "next": [],
    },
    {
        "id": "roles.apply",
        "stage": "roles",
        "title": "Áp fetch roles list/search/detail",
        "role": "list",
        "host_role": "shiip_api",
        "cli": "ghn_order_endpoint_deep_mapper.py apply-roles",
        "module": "ghn_order_endpoint_deep_mapper.apply_roles",
        "secrets": ["ghn_roles.state.json"],
        "fetch_roles": ["list", "search", "detail"],
        "support_roles": ["master", "shop", "fee", "print"],
        "blocked_roles": ["create", "mutate", "status", "token"],
        "next": ["orders.fetch"],
    },
    {
        "id": "orders.fetch",
        "stage": "orders",
        "title": "Access token → gọi đơn (roles)",
        "role": "list",
        "host_role": "shiip_api",
        "cli": "ghn_access_token_orders.py run --days 3 --limit 50",
        "module": "ghn_access_token_orders",
        "nginx": "POST /v1/ghn/orders",
        "endpoints": [
            "/shiip/public-api/v2/shipping-order/all",
            "/shiip/public-api/v2/shipping-order/search",
            "/shiip/public-api/v2/shipping-order/detail",
        ],
        "next": ["orders.scan", "orders.realtime"],
    },
    {
        "id": "orders.proxy",
        "stage": "orders",
        "title": "Token↔proxy → gọi đơn",
        "role": "list",
        "host_role": "shiip_api",
        "cli": "token_proxy_bind.py nginx-orders",
        "module": "token_proxy_bind",
        "nginx": "POST /v1/ghn/token-proxy-orders",
        "next": ["orders.scan"],
    },
    {
        "id": "orders.scan",
        "stage": "orders",
        "title": "Scan bưu cục / pipe GHN",
        "role": "list",
        "host_role": "shiip_api",
        "cli": "scan_buucuc_orders.py --backends GHN --days 3",
        "module": "scan_buucuc_orders.scan_ghn",
        "nginx": "POST /v1/buucuc/scan",
        "next": ["bus.oms"],
    },
    {
        "id": "orders.realtime",
        "stage": "orders",
        "title": "Realtime order sync (GHN)",
        "role": "list",
        "host_role": "shiip_api",
        "cli": "access_token_rotate.py apply-realtime --direct",
        "module": "realtime_order_sync.sync_ghn_probe",
        "nginx": "POST /v1/orders/realtime",
        "next": ["bus.oms"],
    },
    {
        "id": "bus.oms",
        "stage": "bus",
        "title": "OMS interconnect + keepalive",
        "role": "status",
        "host_role": "shiip_api",
        "cli": "ghn_pipe_interconnect.py --days 3",
        "module": "ghn_pipe_interconnect / oms_interconnect",
        "next": [],
    },
    {
        "id": "map.gateway",
        "stage": "map",
        "title": "Gateway host alias → icon role",
        "role": "master",
        "host_role": "shiip_api",
        "cli": "ghn_gateway_icon_mapper.py",
        "module": "ghn_gateway_icon_mapper",
        "next": ["roles.apply"],
    },
    {
        "id": "map.deep",
        "stage": "map",
        "title": "Deep endpoint catalog + probe",
        "role": "master",
        "host_role": "shiip_api",
        "cli": "ghn_order_endpoint_deep_mapper.py probe",
        "module": "ghn_order_endpoint_deep_mapper",
        "next": ["roles.apply"],
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_meta(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    try:
        st = path.stat()
        return {"exists": True, "bytes": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return {"exists": False}


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_env() -> dict[str, str]:
    from owned_credentials import load_env as base

    return base(extra_files=(SECRETS / "order_session.env",))


def role_catalog() -> dict[str, Any]:
    from ghn_order_endpoint_deep_mapper import (
        BLOCKED_ROLES,
        FETCH_ROLES,
        ORDER_ENDPOINTS,
        ROLE_ICON,
        SUPPORT_ROLES,
        build_fetch_plan,
    )

    plan = build_fetch_plan(host="online-gateway.ghn.vn")
    by_role: dict[str, list[dict[str, str]]] = {}
    for ep in ORDER_ENDPOINTS:
        role = str(ep.get("role") or "?")
        by_role.setdefault(role, []).append(
            {
                "id": str(ep.get("id")),
                "path": str(ep.get("path")),
                "pipe": str(ep.get("pipe") or ""),
                "purpose": str(ep.get("purpose") or ""),
            }
        )
    return {
        "fetch_roles": list(FETCH_ROLES),
        "support_roles": list(SUPPORT_ROLES),
        "blocked_roles": list(BLOCKED_ROLES),
        "role_icons": dict(ROLE_ICON),
        "by_role": {k: v for k, v in sorted(by_role.items())},
        "plan_endpoints": plan.get("endpoints") or [],
        "host": plan.get("host"),
        "base": plan.get("base"),
    }


def host_role_catalog() -> list[dict[str, Any]]:
    from ghn_gateway_icon_mapper import HOST_ICON_ROLE

    rows = []
    for host, meta in HOST_ICON_ROLE.items():
        rows.append(
            {
                "host": host,
                "role": meta.get("role"),
                "icon": meta.get("icon"),
                "call": meta.get("call"),
                "motto": meta.get("motto"),
                "base_paths": list(meta.get("base_paths") or ()),
            }
        )
    return rows


def pipe_status(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Gắn trạng thái từng stage ống dựa trên secrets/env/state."""
    env = env or load_env()
    token = (env.get("GHN_API_TOKEN") or env.get("GHN_TOKEN") or "").strip()
    shop = (env.get("GHN_SHOP_ID") or "").strip()
    sso = (env.get("GHN_SSO_ID_TOKEN") or "").strip()
    sso_app = (env.get("GHN_SSO_APP_KEY") or "").strip()

    session = _read_json(SECRETS / "ghn_session.state.json") or {}
    roles_st = _read_json(SECRETS / "ghn_roles.state.json") or {}
    sso_st = _read_json(SECRETS / "ghn_sso_jwt.state.json") or {}
    pipe_st = _read_json(SECRETS / "ghn_pipe_interconnect.state.json") or {}
    captcha = _read_json(SECRETS / "captcha.pending.json")
    captcha_txt = (SECRETS / "captcha.pending.txt").is_file()
    frida_pending = (SECRETS / "frida_a11y_ghn.pending.json").is_file()
    proxies = (SECRETS / "proxies.live.txt").is_file()

    alive = bool(session.get("alive")) if session else False
    roles_ok = bool(roles_st.get("ok"))
    fetch_roles = ((roles_st.get("plan") or {}).get("fetch_roles")) or []

    flags = {
        "src.printA5": bool(token) or (SECRETS / "ghn_session.raw").is_file(),
        "src.cookie": bool(token) or (SECRETS / "ghn_session.raw").is_file(),
        "src.frida_a11y": frida_pending or bool(token),
        "src.sso_jwt": bool(sso) or (SECRETS / "ghn_sso_login.url").is_file(),
        "src.captcha_tg": bool(captcha) or captcha_txt,
        "src.proxy_bind": proxies,
        "auth.ensure": alive and bool(token),
        "auth.shop": bool(shop),
        "auth.sso_service": bool(sso),
        "roles.apply": roles_ok and bool(fetch_roles),
        "orders.fetch": alive,  # path ready; may still auth_fail
        "orders.proxy": proxies and bool(token),
        "orders.scan": bool(pipe_st.get("ok")) or alive,
        "orders.realtime": alive,
        "bus.oms": bool(pipe_st.get("ok")),
        "map.gateway": True,
        "map.deep": roles_ok,
    }

    detail = {
        "src.printA5": f"token_set={bool(token)} session.raw={_file_meta(SECRETS / 'ghn_session.raw').get('exists')}",
        "src.sso_jwt": f"id_token_set={bool(sso)} app_key={sso_app or (sso_st.get('parsed') or {}).get('app_key') or '—'}",
        "src.captcha_tg": (
            f"pending_json={bool(captcha)} "
            f"api_error={bool(isinstance(captcha, dict) and captcha.get('status') == 'error')}"
        ),
        "auth.ensure": f"alive={alive} masked={session.get('token_masked') or ('set' if token else None)}",
        "auth.shop": f"shop_id={shop or '—'}",
        "roles.apply": f"fetch={fetch_roles} ok={roles_ok}",
        "orders.fetch": f"ready_if_alive={alive}",
        "bus.oms": f"pipe_ok={pipe_st.get('ok')} verdict={(pipe_st.get('verdict') or '')[:80]}",
    }

    out = []
    for stage in PIPE_STAGES:
        sid = stage["id"]
        ready = bool(flags.get(sid))
        out.append(
            {
                **{k: stage[k] for k in stage if k != "next"},
                "next": list(stage.get("next") or []),
                "ready": ready,
                "status": "ready" if ready else "pending",
                "detail": detail.get(sid) or "",
            }
        )
    return out


def edges_from_stages(stages: list[dict[str, Any]]) -> list[dict[str, str]]:
    edges = []
    by_id = {s["id"]: s for s in stages}
    for s in stages:
        for n in s.get("next") or []:
            if n in by_id:
                edges.append({"from": s["id"], "to": n, "via": s.get("stage") or ""})
    return edges


def mermaid(stages: list[dict[str, Any]], roles: dict[str, Any]) -> str:
    lines = [
        "```mermaid",
        "flowchart LR",
        "  subgraph SRC[Nguồn token]",
        "    P5[printA5]",
        "    CK[cookie]",
        "    FA[frida+a11y]",
        "    SSO[sso-v2 JWT]",
        "    CAP[captcha TG]",
        "    PX[proxy bind]",
        "  end",
        "  subgraph AUTH[Auth]",
        "    EN[ensure Token]",
        "    SH[shop/all]",
        "    SS[gen-service-token]",
        "  end",
        "  subgraph ROLES[Fetch roles]",
        "    L[list]",
        "    S[search]",
        "    D[detail]",
        "  end",
        "  subgraph ORD[Gọi đơn]",
        "    FO[access_token_orders]",
        "    SC[scan_buucuc]",
        "    RT[realtime]",
        "  end",
        "  P5 --> EN",
        "  CK --> EN",
        "  FA --> EN",
        "  SSO --> SS",
        "  CAP -.-> SSO",
        "  PX --> FO",
        "  EN --> SH",
        "  EN --> L",
        "  L --> S --> D",
        "  D --> FO",
        "  SH --> FO",
        "  FO --> SC",
        "  FO --> RT",
        f"  %% fetch_roles={roles.get('fetch_roles')}",
        "```",
    ]
    return "\n".join(lines)


def build_report(*, apply: bool = True, probe_gateway: bool = True) -> dict[str, Any]:
    env = load_env()
    roles = role_catalog()
    hosts = host_role_catalog()
    stages = pipe_status(env)
    edges = edges_from_stages(PIPE_STAGES)

    applied = None
    if apply:
        try:
            from ghn_order_endpoint_deep_mapper import apply_roles

            applied = apply_roles(host="online-gateway.ghn.vn", ensure_token=False)
            # refresh roles snapshot after apply
            roles = role_catalog()
            stages = pipe_status(env)
        except Exception as e:  # noqa: BLE001
            applied = {"ok": False, "error": str(e)[:160]}

    gateway = None
    if probe_gateway:
        try:
            from ghn_gateway_icon_mapper import map_host

            gateway = map_host("online-gateway.ghn.vn", probe=True)
        except Exception as e:  # noqa: BLE001
            gateway = {"ok": False, "error": str(e)[:160]}

    ready_n = sum(1 for s in stages if s.get("ready"))
    fetch_ready = any(s["id"] == "roles.apply" and s.get("ready") for s in stages)
    auth_ready = any(s["id"] == "auth.ensure" and s.get("ready") for s in stages)

    report: dict[str, Any] = {
        "ok": fetch_ready,
        "module": "ghn_pipe_role_mapper",
        "checked_at": utc_now(),
        "policy": {"owned_only": True, "no_dump_login": True, "mutate_blocked": True},
        "summary": {
            "stages_n": len(stages),
            "stages_ready": ready_n,
            "auth_ready": auth_ready,
            "roles_ready": fetch_ready,
            "fetch_roles": roles.get("fetch_roles"),
            "support_roles": roles.get("support_roles"),
            "blocked_roles": roles.get("blocked_roles"),
        },
        "pipes": stages,
        "edges": edges,
        "roles": {
            "fetch": roles.get("fetch_roles"),
            "support": roles.get("support_roles"),
            "blocked": roles.get("blocked_roles"),
            "icons": roles.get("role_icons"),
            "by_role": {
                k: [{"id": x["id"], "path": x["path"]} for x in v[:8]]
                for k, v in (roles.get("by_role") or {}).items()
            },
            "plan_endpoints": roles.get("plan_endpoints"),
            "host": roles.get("host"),
        },
        "hosts": hosts,
        "roles_applied": {
            "ok": (applied or {}).get("ok"),
            "verdict": (applied or {}).get("verdict"),
            "fetch_roles": ((applied or {}).get("plan") or {}).get("fetch_roles"),
            "error": (applied or {}).get("error"),
        }
        if applied is not None
        else None,
        "gateway": {
            "ok": (gateway or {}).get("ok"),
            "verdict": (gateway or {}).get("verdict"),
            "canonical": ((gateway or {}).get("mapped") or {}).get("canonical"),
            "role": ((gateway or {}).get("mapped") or {}).get("role"),
        }
        if gateway is not None
        else None,
        "mermaid": mermaid(stages, roles),
        "next": [],
        "verdict": "",
    }

    if auth_ready and fetch_ready:
        report["verdict"] = (
            f"✅ GHN ống+role sẵn · fetch={roles.get('fetch_roles')} · "
            f"stages_ready={ready_n}/{len(stages)} · auth=alive"
        )
        report["next"] = [
            "python3 scripts/ghn_access_token_orders.py run --days 3 --limit 50",
            "python3 scripts/ghn_pipe_interconnect.py --days 3",
        ]
    elif fetch_ready and not auth_ready:
        report["ok"] = True  # roles atlas OK even without live token
        report["verdict"] = (
            f"⚠ Role đã áp {roles.get('fetch_roles')} · chưa có GHN_API_TOKEN sống — "
            f"stages_ready={ready_n}/{len(stages)}"
        )
        report["next"] = [
            "printA5/Frida owned → python3 scripts/ghn_access_token_orders.py run --printA5 '<URL>'",
            "hoặc: python3 scripts/frida_a11y_ghn_bridge.py apply --orders",
        ]
    else:
        report["verdict"] = f"❌ Mapper ống/role chưa sẵn · stages_ready={ready_n}/{len(stages)}"
        report["next"] = [
            "python3 scripts/ghn_pipe_role_mapper.py --apply",
            "python3 scripts/ghn_order_endpoint_deep_mapper.py apply-roles",
        ]

    write_outputs(report)
    return report


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "ghn_pipe_role_mapper.json"
    tp = REPORTS / "ghn_pipe_role_mapper.txt"
    mp = REPORTS / "ghn_pipe_role_mapper.mermaid.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    mp.write_text((report.get("mermaid") or "") + "\n", encoding="utf-8")
    slim = {
        "updated_at": report.get("checked_at"),
        "ok": report.get("ok"),
        "verdict": report.get("verdict"),
        "summary": report.get("summary"),
        "fetch_roles": (report.get("roles") or {}).get("fetch"),
    }
    STATE_PATH.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp), "mermaid": str(mp)}


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("🗺 GHN · MAPPER ĐƯỜNG ỐNG + ROLE")
    L(f"Lúc: {report.get('checked_at')}")
    L(f"Verdict: {report.get('verdict')}")
    s = report.get("summary") or {}
    L(
        f"stages_ready={s.get('stages_ready')}/{s.get('stages_n')} · "
        f"auth={s.get('auth_ready')} · roles={s.get('roles_ready')} · "
        f"fetch={s.get('fetch_roles')}"
    )
    L("")
    L("=== Đường ống ===")
    for p in report.get("pipes") or []:
        mark = "✅" if p.get("ready") else "·"
        L(
            f"{mark} [{p.get('stage')}] {p.get('id')} · role={p.get('role')} · "
            f"{p.get('title')}"
        )
        if p.get("detail"):
            L(f"    {p.get('detail')}")
        if p.get("cli"):
            L(f"    cli: {p.get('cli')}")
    L("")
    L("=== Role endpoint ===")
    roles = report.get("roles") or {}
    L(f"fetch: {roles.get('fetch')}")
    L(f"support: {roles.get('support')}")
    L(f"blocked: {roles.get('blocked')}")
    for ep in roles.get("plan_endpoints") or []:
        L(f"  · [{ep.get('role')}] {ep.get('method')} {ep.get('path')}")
    L("")
    L("=== Host role ===")
    for h in report.get("hosts") or []:
        L(f"  · {h.get('host')} → {h.get('role')} · {h.get('call')}")
    ra = report.get("roles_applied") or {}
    if ra:
        L("")
        L(f"roles_applied: {ra.get('verdict') or ra.get('error')}")
    gw = report.get("gateway") or {}
    if gw:
        L(f"gateway: {gw.get('verdict') or gw.get('error')}")
    L("")
    L("Policy: owned-only · mutate blocked · SSO id_token ≠ GHN_API_TOKEN")
    for n in report.get("next") or []:
        L(f"Next: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mapper đường ống dẫn + role GHN")
    ap.add_argument("--apply", action="store_true", help="Áp fetch roles list/search/detail")
    ap.add_argument("--no-apply", action="store_true")
    ap.add_argument("--no-gateway", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--mermaid", action="store_true", help="In mermaid only")
    args = ap.parse_args(argv)

    apply = bool(args.apply) or not args.no_apply
    if args.no_apply:
        apply = False

    report = build_report(apply=apply, probe_gateway=not args.no_gateway)
    if args.mermaid:
        print(report.get("mermaid") or "")
        return 0 if report.get("ok") else 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
