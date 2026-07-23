#!/usr/bin/env python3
"""Xem proxy nào dùng được thì lấy dùng → bind token → nginx gọi đơn.

Quét ứng viên:
  - secrets/proxies.owned.txt / proxy_list.txt
  - quarantine/** (tên/nội dung proxy)
  - kubernetes2 PROXY_UPSTREAMS (reverse-proxy local — chỉ dùng nếu là egress thật)
  - DIRECT (không proxy) — luôn là ứng viên dự phòng

Probe: GET https://online-gateway.ghn.vn/.../province (qua proxy nếu có).
Chỉ giữ proxy live (hoặc DIRECT nếu không có egress live).

Owned-only · không kéo free-proxy dump internet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
REPORTS = ROOT / "reports" / "telegram-classify"
LIVE_PATH = SECRETS / "proxies.live.txt"
OWNED_PATH = SECRETS / "proxies.owned.txt"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_candidates() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from token_proxy_bind import pull_telegram_proxy_docs, scan_proxy_files

    try:
        pull_telegram_proxy_docs(lookback=800)
    except Exception:  # noqa: BLE001
        pass
    proxies, sources = scan_proxy_files()
    out: list[dict[str, Any]] = []
    for p in proxies:
        out.append({**p, "kind": "egress"})
    # local nginx order gateway — NOT egress, but note as infra
    out.append(
        {
            "kind": "local_gateway",
            "scheme": "http",
            "host": "127.0.0.1",
            "port": 18080,
            "url": "http://127.0.0.1:18080",
            "url_masked": "http://127.0.0.1:18080",
            "has_auth": False,
            "source": "docker/nginx-order (order gateway, not GHN egress)",
            "skip_egress_probe": True,
        }
    )
    out.append(
        {
            "kind": "direct",
            "scheme": "direct",
            "host": None,
            "port": None,
            "url": None,
            "url_masked": "DIRECT",
            "has_auth": False,
            "source": "no_proxy",
        }
    )
    return out, sources


def probe_candidate(c: dict[str, Any], *, timeout: int = 15) -> dict[str, Any]:
    from token_proxy_bind import probe_ghn_via_proxy

    if c.get("skip_egress_probe"):
        # check local nginx health if up
        import urllib.request

        try:
            with urllib.request.urlopen("http://127.0.0.1:18080/health", timeout=3) as r:
                ok = 200 <= r.status < 300
            return {
                **c,
                "usable": ok,
                "role": "order_gateway",
                "probe": {"ok": ok, "http": 200 if ok else 0, "note": "local nginx /health"},
            }
        except Exception as e:  # noqa: BLE001
            return {
                **c,
                "usable": False,
                "role": "order_gateway",
                "probe": {"ok": False, "error": str(e)[:100]},
            }

    # For egress/direct: need a token to validate path through GHN; use dummy uuid
    # — we only care if PROXY path works (connect). Use httpbin-like via GHN without token
    # will 401 but proves TCP/TLS via proxy. Treat connect+HTTP response as proxy_usable.
    import requests

    proxy_url = c.get("url")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    try:
        r = requests.get(
            "https://online-gateway.ghn.vn/shiip/public-api/master-data/province",
            headers={"Token": "00000000-0000-0000-0000-000000000000", "Content-Type": "application/json"},
            proxies=proxies,
            timeout=timeout,
        )
        # Any HTTP response means proxy path works (401 expected for fake token)
        path_ok = r.status_code > 0
        auth_ok = False
        try:
            data = r.json() if r.text else {}
        except Exception:  # noqa: BLE001
            data = {}
        msg = data.get("message") if isinstance(data, dict) else None
        # proxy usable if we got response from GHN (not connection error)
        usable = path_ok and r.status_code in {200, 401, 403} or (
            path_ok and "ghn" in (r.text or "").lower()
        )
        # refine: connection success = status not 0
        usable = r.status_code in {200, 401, 403}
        return {
            **c,
            "usable": usable,
            "role": "egress" if proxy_url else "direct",
            "probe": {
                "ok": usable,
                "http": r.status_code,
                "message": msg,
                "note": "401/403 = đường proxy/direct tới GHN OK (token giả)",
            },
        }
    except Exception as e:  # noqa: BLE001
        return {
            **c,
            "usable": False,
            "role": "egress" if proxy_url else "direct",
            "probe": {"ok": False, "http": 0, "error": str(e)[:160]},
        }


def save_live(usable_egress: list[dict[str, Any]]) -> dict[str, str]:
    SECRETS.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# proxies.live — usable egress @ {utc_now()}",
        "# Auto-selected by proxy_usable_probe.py",
    ]
    for c in usable_egress:
        # write original-ish form without leaking if we only have url
        url = c.get("url") or ""
        if not url:
            continue
        # prefer host:port:user:pass reconstruction without re-encoding issues
        host = c.get("host")
        port = c.get("port")
        if host and port and not c.get("has_auth"):
            lines.append(f"{host}:{port}")
        else:
            lines.append(url)
    LIVE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(LIVE_PATH, 0o600)
    except OSError:
        pass
    # also sync to proxies.owned.txt for bind pipeline
    OWNED_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(OWNED_PATH, 0o600)
    except OSError:
        pass
    return {"live": str(LIVE_PATH), "owned": str(OWNED_PATH)}


def run(*, apply: bool = True, limit_tokens: int = 10, via_nginx: bool = True) -> dict[str, Any]:
    candidates, sources = collect_candidates()
    probed: list[dict[str, Any]] = []
    for c in candidates:
        probed.append(probe_candidate(c))

    usable_egress = [
        p
        for p in probed
        if p.get("usable") and p.get("role") == "egress" and p.get("url")
    ]
    direct_ok = any(p.get("usable") and p.get("role") == "direct" for p in probed)
    gateway_ok = any(p.get("usable") and p.get("role") == "order_gateway" for p in probed)

    report: dict[str, Any] = {
        "ok": False,
        "module": "proxy_usable_probe",
        "checked_at": utc_now(),
        "candidates_n": len(candidates),
        "usable_egress_n": len(usable_egress),
        "direct_ok": direct_ok,
        "gateway_ok": gateway_ok,
        "proxy_sources": sources,
        "probed": [
            {
                "kind": p.get("kind"),
                "role": p.get("role"),
                "url_masked": p.get("url_masked"),
                "source": p.get("source"),
                "usable": p.get("usable"),
                "probe": p.get("probe"),
            }
            for p in probed
        ],
        "selected": [],
        "bind": None,
        "orders": None,
        "verdict": "",
        "next": [],
        "policy": {"owned_only": True, "no_free_proxy_scrape": True},
    }

    saved = None
    if usable_egress:
        saved = save_live(usable_egress)
        report["selected"] = [p.get("url_masked") for p in usable_egress]
        report["saved"] = saved
        mode = "egress"
    elif direct_ok:
        # clear owned proxies so bind uses DIRECT
        if OWNED_PATH.is_file():
            # keep file but only header note
            OWNED_PATH.write_text(
                f"# no usable egress proxy @ {utc_now()}\n# using DIRECT\n",
                encoding="utf-8",
            )
        report["selected"] = ["DIRECT"]
        mode = "direct"
    else:
        report["verdict"] = "❌ Không có proxy/direct nào tới được GHN"
        report["next"] = [
            "Đưa proxy owned vào secrets/proxies.owned.txt",
            "python3 scripts/proxy_usable_probe.py --apply",
        ]
        write_outputs(report)
        return report

    if apply:
        from token_proxy_bind import run_bind, run_nginx_orders

        bind = run_bind(pull_telegram=False, mode="round_robin", max_tokens=limit_tokens)
        report["bind"] = {
            "ok": bind.get("ok"),
            "proxy_n": bind.get("proxy_n"),
            "token_n": bind.get("token_n"),
            "bound_n": bind.get("bound_n"),
            "direct_n": bind.get("direct_n"),
            "verdict": bind.get("verdict"),
        }
        orders = run_nginx_orders(
            days=3,
            limit=20,
            limit_tokens=limit_tokens,
            probe_only=False,
            via_nginx=via_nginx,
            keep=False,
        )
        report["orders"] = {
            "ok": orders.get("ok"),
            "via_nginx": orders.get("via_nginx"),
            "tried": orders.get("tried"),
            "alive_proxy": orders.get("alive_proxy"),
            "auth_fail": orders.get("auth_fail"),
            "proxy_fail": orders.get("proxy_fail"),
            "fetched_total": orders.get("fetched_total"),
            "verdict": orders.get("verdict"),
            "results_preview": (orders.get("results") or [])[:8],
        }

    if usable_egress:
        report["ok"] = True
        report["verdict"] = (
            f"✅ Dùng được {len(usable_egress)} egress proxy · "
            f"direct_ok={direct_ok} · gateway={gateway_ok} · "
            f"orders_alive={(report.get('orders') or {}).get('alive_proxy')} · "
            f"fetched={(report.get('orders') or {}).get('fetched_total')}"
        )
    else:
        # direct only — still "usable" path but flag gap
        report["ok"] = bool(direct_ok)
        report["verdict"] = (
            f"⚠ Không có egress proxy live trong hệ thống — dùng DIRECT · "
            f"gateway={gateway_ok} · "
            f"token_alive={(report.get('orders') or {}).get('alive_proxy')} · "
            f"auth_fail={(report.get('orders') or {}).get('auth_fail')} · "
            f"fetched={(report.get('orders') or {}).get('fetched_total')}"
        )
        report["next"] = [
            "Hệ thống Proxy SaaS (kubernetes2) chưa có list IP egress — chỉ có reverse-proxy upstream mẫu",
            "Dán proxy owned (ip:port hoặc ip:port:user:pass) vào secrets/proxies.owned.txt",
            "python3 scripts/proxy_usable_probe.py --apply",
        ]

    write_outputs(report)
    return report


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔎 PROXY USABLE · LẤY CÁI DÙNG ĐƯỢC")
    L(f"Lúc: {report.get('checked_at') or utc_now()}")
    L(report.get("verdict") or "")
    L(
        f"candidates={report.get('candidates_n')} usable_egress={report.get('usable_egress_n')} "
        f"direct_ok={report.get('direct_ok')} gateway_ok={report.get('gateway_ok')}"
    )
    L("")
    L("=== Probe ===")
    for p in report.get("probed") or []:
        mark = "✅" if p.get("usable") else "❌"
        pr = p.get("probe") or {}
        L(
            f"{mark} [{p.get('role')}/{p.get('kind')}] {p.get('url_masked')} "
            f"http={pr.get('http')} · {pr.get('message') or pr.get('error') or pr.get('note') or ''}"
        )
    if report.get("selected"):
        L("")
        L(f"Selected: {', '.join(report['selected'])}")
    if report.get("bind"):
        b = report["bind"]
        L(f"Bind: {b.get('verdict')}")
    if report.get("orders"):
        o = report["orders"]
        L(f"Orders: {o.get('verdict')}")
        for r in o.get("results_preview") or []:
            L(
                f"  · token={r.get('token_masked')} proxy={r.get('proxy_masked') or 'direct'} "
                f"probe={((r.get('probe') or {}).get('ok'))} "
                f"fetched={((r.get('orders') or {}).get('fetched'))}"
            )
    if report.get("next"):
        L("")
        L("Next:")
        for n in report["next"]:
            L(f"· {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "proxy_usable_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (REPORTS / "proxy_usable_probe.txt").write_text(format_text(report) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Probe proxy usable → lấy dùng → nginx orders")
    ap.add_argument("--apply", action="store_true", default=True)
    ap.add_argument("--no-apply", action="store_true")
    ap.add_argument("--limit-tokens", type=int, default=10)
    ap.add_argument("--direct", action="store_true", help="Orders không qua nginx")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = run(
        apply=not args.no_apply,
        limit_tokens=args.limit_tokens,
        via_nginx=not args.direct,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
