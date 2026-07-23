#!/usr/bin/env python3
"""Đấu nối ống GHN end-to-end: gateway → roles → ensure → deep map → OMS → scan.

Owned-only · no dump-login.
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


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def interconnect_ghn(*, notify: bool = False, scan: bool = True, days: int = 3, limit: int = 50) -> dict[str, Any]:
    from realtime_icon_feedback_mapper import chant, feedback_line

    steps: dict[str, Any] = {}

    # 1) Gateway icon alias
    try:
        from ghn_gateway_icon_mapper import map_host, upsert_order_api_hosts

        gw = map_host("online.gateway.ghn.vn", probe=True)
        upsert_order_api_hosts((gw.get("mapped") or {}).get("canonical") or "online-gateway.ghn.vn")
        steps["gateway"] = {
            "ok": gw.get("ok"),
            "verdict": gw.get("verdict"),
            "canonical": (gw.get("mapped") or {}).get("canonical"),
            "icon": (gw.get("icon") or {}).get("icon_chant"),
        }
    except Exception as e:  # noqa: BLE001
        steps["gateway"] = {"ok": False, "error": str(e)[:160]}

    # 2) Apply fetch roles
    try:
        from ghn_order_endpoint_deep_mapper import apply_roles

        roles = apply_roles(host="online.gateway.ghn.vn", ensure_token=True)
        steps["roles"] = {
            "ok": roles.get("ok"),
            "verdict": roles.get("verdict"),
            "fetch_roles": (roles.get("plan") or {}).get("fetch_roles"),
            "endpoints_n": len((roles.get("plan") or {}).get("endpoints") or []),
            "token_alive": (roles.get("ghn_ensure") or {}).get("alive"),
            "token_masked": (roles.get("ghn_ensure") or {}).get("token_masked"),
        }
    except Exception as e:  # noqa: BLE001
        steps["roles"] = {"ok": False, "error": str(e)[:160]}

    # 3) Deep endpoint probe (safe)
    try:
        from ghn_order_endpoint_deep_mapper import deep_map, write_outputs

        deep = deep_map(host="online.gateway.ghn.vn", probe=True, allow_mutate=False, with_token=True)
        write_outputs(deep)
        steps["deep"] = {
            "ok": deep.get("ok"),
            "verdict": deep.get("verdict"),
            "alive": deep.get("alive_endpoint_n"),
            "list_ready": deep.get("list_fetch_ready"),
            "by_surface": deep.get("by_surface"),
        }
    except Exception as e:  # noqa: BLE001
        steps["deep"] = {"ok": False, "error": str(e)[:160]}

    # 4) Session maintain (Pancake + GHN roles)
    try:
        from token_session_maintain import maintain_once

        maint = maintain_once(notify_on_risk=notify)
        steps["maintain"] = {
            "ok": maint.get("ok"),
            "verdict": maint.get("verdict"),
            "ghn_ready": maint.get("ghn_ready"),
            "ghn_roles": ((maint.get("ghn") or {}).get("roles") or {}).get("fetch_roles"),
            "order_ready": maint.get("order_ready"),
        }
    except Exception as e:  # noqa: BLE001
        steps["maintain"] = {"ok": False, "error": str(e)[:160]}

    # 5) OMS interconnect bus
    try:
        from oms_interconnect import interconnect, load_env, send_telegram

        env = load_env()
        # overlay order_session
        ose = SECRETS / "order_session.env"
        if ose.is_file():
            for line in ose.read_text(encoding="utf-8", errors="ignore").splitlines():
                t = line.strip()
                if not t or t.startswith("#") or "=" not in t:
                    continue
                k, v = t.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        oms = interconnect(env, ingest=True)
        steps["oms"] = {
            "ok": oms.get("ok"),
            "verdict": oms.get("verdict"),
            "channels": [
                {"id": c.get("id"), "status": c.get("status"), "detail": (c.get("detail") or "")[:100]}
                for c in (oms.get("channels") or [])
                if c.get("id") in {"ghn", "pancake", "telegram", "tracking", "oms_bus"}
            ],
        }
        if notify:
            send_telegram(env, format_text({"checked_at": utc_now(), "verdict": oms.get("verdict"), "steps": steps, "icon": {}}))
    except Exception as e:  # noqa: BLE001
        steps["oms"] = {"ok": False, "error": str(e)[:160]}

    # 6) Backend keepalive
    try:
        from backend_pipe_keepalive import load_env as ka_load, run_once

        ka = run_once(ka_load(), notify=False)
        pipes = ka.get("pipes") or []
        ghn_pipe = next((p for p in pipes if (p.get("backend") or "").upper() == "GHN"), None)
        steps["keepalive"] = {
            "ok": ka.get("ok"),
            "ghn": ghn_pipe,
            "risks": ka.get("session_risk_count") or ka.get("risks"),
        }
    except Exception as e:  # noqa: BLE001
        steps["keepalive"] = {"ok": False, "error": str(e)[:160]}

    # 7) Access token → gọi đơn GHN (roles list/search)
    if scan:
        try:
            from ghn_access_token_orders import get_token_and_fetch_orders

            sg = get_token_and_fetch_orders(
                days=days, limit=limit, try_pending=True, resolve_shop=True
            )
            orders = sg.get("orders") or {}
            steps["scan"] = {
                "ok": bool(sg.get("ok")) and (orders.get("status") not in {"auth_fail", "missing_cred"}),
                "status": orders.get("status") or ("ok" if sg.get("ok") else "missing_cred"),
                "fetched": orders.get("fetched"),
                "detail": (sg.get("verdict") or orders.get("detail") or "")[:160],
                "roles": sg.get("roles") or orders.get("roles"),
                "shop_id": sg.get("shop_id"),
                "token_masked": (sg.get("token") or {}).get("token_masked"),
                "via": "ghn_access_token_orders",
            }
        except Exception as e:  # noqa: BLE001
            steps["scan"] = {"ok": False, "error": str(e)[:160]}

    token_alive = bool((steps.get("roles") or {}).get("token_alive"))
    ghn_oms = next(
        (c for c in (steps.get("oms") or {}).get("channels") or [] if c.get("id") == "ghn"),
        {},
    )
    icons = ["network", "spark", "monitor", "compass", "hash"]
    if not token_alive:
        icons.extend(["key", "lock"])
    icons = list(dict.fromkeys(icons))

    report: dict[str, Any] = {
        "ok": True,
        "module": "ghn_pipe_interconnect",
        "checked_at": utc_now(),
        "query": "Đấu nối ống GHN end-to-end",
        "steps": steps,
        "token_alive": token_alive,
        "ghn_channel_status": ghn_oms.get("status"),
        "icon": {
            "icons": icons,
            "icon_chant": chant(icons),
            "feedback": feedback_line(
                icons,
                f"GHN pipe · roles={((steps.get('roles') or {}).get('fetch_roles'))} · "
                f"token={'Y' if token_alive else 'N'} · oms={ghn_oms.get('status')}",
            ),
        },
        "policy": {"owned_only": True, "no_dump_login": True},
        "next_actions": [
            "printf '%s\\n' '<printA5 owned>' > secrets/ghn_session.raw && python3 scripts/ghn_cookie_ingest.py ensure",
            "python3 scripts/ghn_access_token_orders.py run --days 3 --limit 50",
            "python3 scripts/access_token_rotate.py refresh --platform GHN --orders --direct",
            "python3 scripts/ghn_pipe_interconnect.py --scan --days 3",
        ],
    }

    if token_alive and (steps.get("scan") or {}).get("ok"):
        report["verdict"] = (
            f"✅ Đấu nối GHN xong · token alive · scan fetched="
            f"{(steps.get('scan') or {}).get('fetched')} · {chant(icons)}"
        )
    elif token_alive:
        report["verdict"] = (
            f"⚠ Đấu nối GHN · token alive nhưng scan chưa OK "
            f"({(steps.get('scan') or {}).get('status')}) · {chant(icons)}"
        )
    else:
        report["verdict"] = (
            f"⚠ Đấu nối GHN pipe sẵn sàng (roles+OMS+gateway) · "
            f"thiếu GHN_API_TOKEN · oms_ghn={ghn_oms.get('status')} · {chant(icons)}"
        )

    _write(report)
    return report


def _write(report: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "ghn_pipe_interconnect.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (REPORTS / "ghn_pipe_interconnect.txt").write_text(format_text(report) + "\n", encoding="utf-8")
    SECRETS.mkdir(parents=True, exist_ok=True)
    (SECRETS / "ghn_pipe_interconnect.state.json").write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "token_alive": report.get("token_alive"),
                "ghn_channel_status": report.get("ghn_channel_status"),
                "roles": (report.get("steps") or {}).get("roles"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🔗 ĐẤU NỐI ỐNG GHN",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"Token alive: {report.get('token_alive')} · OMS GHN: {report.get('ghn_channel_status')}",
    ]
    ic = report.get("icon") or {}
    lines.append(f"Chant: {ic.get('icon_chant')}")
    lines.append(f"Feedback: {ic.get('feedback')}")
    lines.append("")
    steps = report.get("steps") or {}
    for name in ("gateway", "roles", "deep", "maintain", "oms", "keepalive", "scan"):
        st = steps.get(name)
        if not st:
            continue
        lines.append(f"=== {name} ===")
        if st.get("verdict"):
            lines.append(f"  {st.get('verdict')}")
        elif st.get("error"):
            lines.append(f"  error: {st.get('error')}")
        else:
            lines.append(f"  { {k: v for k, v in st.items() if k not in {'channels'}} }")
        if name == "oms":
            for c in st.get("channels") or []:
                lines.append(f"  · {c.get('id')}: {c.get('status')} — {c.get('detail')}")
        if name == "scan" and st.get("roles"):
            lines.append(f"  roles: {st.get('roles')}")
    lines.append("")
    lines.append("Policy: owned-only · no dump-login")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Đấu nối ống GHN end-to-end")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = interconnect_ghn(
        notify=args.notify,
        scan=not args.no_scan,
        days=args.days,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
