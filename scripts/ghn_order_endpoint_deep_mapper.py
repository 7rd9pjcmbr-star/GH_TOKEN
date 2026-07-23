#!/usr/bin/env python3
"""Mapper truy vấn sâu endpoint đơn hàng · online-gateway.ghn.vn

Alias: online.gateway.ghn.vn / ghn.gateway.online.vn → online-gateway.ghn.vn

- Catalog endpoint đơn (list/search/detail/fee/print/status…)
- Probe GET+POST (owned Token nếu có)
- Icon chant theo vai trò endpoint
- Không dump-login · không tạo đơn thật (create/cancel chỉ classify; --allow-mutate mới probe)

Owned-only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"

CANONICAL_HOST = "online-gateway.ghn.vn"
HOST_ALIASES = {
    "online.gateway.ghn.vn": CANONICAL_HOST,
    "ghn.gateway.online.vn": CANONICAL_HOST,
    "gateway.online.vn": CANONICAL_HOST,
    "api.ghn.vn": CANONICAL_HOST,
    "online-gateway.ghn.vn": CANONICAL_HOST,
    "dev-online-gateway.ghn.vn": "dev-online-gateway.ghn.vn",
}

ROLE_ICON = {
    "list": "monitor",
    "search": "compass",
    "detail": "hash",
    "fee": "layers",
    "print": "text",
    "create": "cube",
    "mutate": "wrench",
    "status": "spark",
    "shop": "key",
    "master": "network",
    "token": "key",
}

ORDER_ENDPOINTS: list[dict[str, Any]] = [
    {
        "id": "order.all",
        "path": "/shiip/public-api/v2/shipping-order/all",
        "methods": ("POST", "GET"),
        "role": "list",
        "purpose": "Danh sách đơn theo cửa sổ thời gian",
        "pipe": "scan_buucuc_orders.scan_ghn",
        "safe_probe": True,
    },
    {
        "id": "order.search",
        "path": "/shiip/public-api/v2/shipping-order/search",
        "methods": ("POST", "GET"),
        "role": "search",
        "purpose": "Tìm đơn theo status/time",
        "pipe": "scan_buucuc_orders.scan_ghn",
        "safe_probe": True,
    },
    {
        "id": "order.detail",
        "path": "/shiip/public-api/v2/shipping-order/detail",
        "methods": ("POST", "GET"),
        "role": "detail",
        "purpose": "Chi tiết đơn theo order_code",
        "safe_probe": True,
    },
    {
        "id": "order.detail_client",
        "path": "/shiip/public-api/v2/shipping-order/detail-by-client-code",
        "methods": ("POST", "GET"),
        "role": "detail",
        "purpose": "Chi tiết theo client_order_code",
        "safe_probe": True,
    },
    {
        "id": "order.soc",
        "path": "/shiip/public-api/v2/shipping-order/soc",
        "methods": ("POST", "GET"),
        "role": "detail",
        "purpose": "SOC / thông tin vận đơn mở rộng",
        "safe_probe": True,
    },
    {
        "id": "order.preview",
        "path": "/shiip/public-api/v2/shipping-order/preview",
        "methods": ("POST",),
        "role": "fee",
        "purpose": "Preview đơn trước tạo",
        "safe_probe": True,
    },
    {
        "id": "order.fee",
        "path": "/shiip/public-api/v2/shipping-order/fee",
        "methods": ("POST",),
        "role": "fee",
        "purpose": "Tính phí vận chuyển",
        "safe_probe": True,
    },
    {
        "id": "order.available_services",
        "path": "/shiip/public-api/v2/shipping-order/available-services",
        "methods": ("POST",),
        "role": "fee",
        "purpose": "Dịch vụ khả dụng (probe token nhẹ)",
        "pipe": "access_token_rotate.probe_token",
        "safe_probe": True,
    },
    {
        "id": "order.gen_token",
        "path": "/shiip/public-api/v2/shipping-order/gen-token",
        "methods": ("POST",),
        "role": "token",
        "purpose": "Gen token in nhãn / print",
        "safe_probe": True,
    },
    {
        "id": "order.print",
        "path": "/shiip/public-api/v2/shipping-order/print",
        "methods": ("POST", "GET"),
        "role": "print",
        "purpose": "In vận đơn",
        "safe_probe": True,
    },
    {
        "id": "order.print_label",
        "path": "/shiip/public-api/v2/shipping-order/print-label",
        "methods": ("POST", "GET"),
        "role": "print",
        "purpose": "In label",
        "safe_probe": True,
    },
    {
        "id": "order.print_a5",
        "path": "/a5/public-api/printA5",
        "methods": ("GET", "POST"),
        "role": "print",
        "purpose": "Print A5 (query token=)",
        "pipe": "ghn_cookie_ingest",
        "safe_probe": True,
    },
    {
        "id": "order.create",
        "path": "/shiip/public-api/v2/shipping-order/create",
        "methods": ("POST",),
        "role": "create",
        "purpose": "Tạo đơn (KHÔNG gọi mặc định)",
        "safe_probe": False,
    },
    {
        "id": "order.cancel",
        "path": "/shiip/public-api/v2/shipping-order/cancel",
        "methods": ("POST",),
        "role": "mutate",
        "purpose": "Hủy đơn (KHÔNG gọi mặc định)",
        "safe_probe": False,
    },
    {
        "id": "order.return",
        "path": "/shiip/public-api/v2/shipping-order/return",
        "methods": ("POST",),
        "role": "mutate",
        "purpose": "Trả hàng (KHÔNG gọi mặc định)",
        "safe_probe": False,
    },
    {
        "id": "order.update",
        "path": "/shiip/public-api/v2/shipping-order/update",
        "methods": ("POST",),
        "role": "mutate",
        "purpose": "Cập nhật đơn (KHÔNG gọi mặc định)",
        "safe_probe": False,
    },
    {
        "id": "order.update_cod",
        "path": "/shiip/public-api/v2/shipping-order/update-cod",
        "methods": ("POST",),
        "role": "mutate",
        "purpose": "Cập nhật COD (KHÔNG gọi mặc định)",
        "safe_probe": False,
    },
    {
        "id": "status.storing",
        "path": "/shiip/public-api/v2/switch-status/storing",
        "methods": ("POST",),
        "role": "status",
        "purpose": "Đổi trạng thái storing",
        "safe_probe": False,
    },
    {
        "id": "status.delivering",
        "path": "/shiip/public-api/v2/switch-status/delivering",
        "methods": ("POST",),
        "role": "status",
        "purpose": "Đổi trạng thái delivering",
        "safe_probe": False,
    },
    {
        "id": "shop.all",
        "path": "/shiip/public-api/v2/shop/all",
        "methods": ("POST", "GET"),
        "role": "shop",
        "purpose": "Danh sách shop gắn token",
        "safe_probe": True,
    },
    {
        "id": "store.all",
        "path": "/shiip/public-api/v2/store/all",
        "methods": ("POST", "GET"),
        "role": "shop",
        "purpose": "Danh sách store",
        "safe_probe": True,
    },
    {
        "id": "master.province",
        "path": "/shiip/public-api/master-data/province",
        "methods": ("GET", "POST"),
        "role": "master",
        "purpose": "Probe auth nhẹ (province)",
        "pipe": "ghn ensure / keepalive",
        "safe_probe": True,
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_host(raw: str) -> str:
    h = (raw or "").strip().lower()
    h = h.removeprefix("https://").removeprefix("http://")
    h = h.split("/")[0].split("?")[0].strip(".")
    return HOST_ALIASES.get(h, h)


def load_creds() -> dict[str, str]:
    from owned_credentials import load_env

    env = load_env(extra_files=(SECRETS / "order_session.env",))
    return {
        "token": (env.get("GHN_API_TOKEN") or env.get("GHN_TOKEN") or "").strip(),
        "shop_id": (env.get("GHN_SHOP_ID") or "").strip(),
    }


def _probe_one(
    host: str,
    path: str,
    method: str,
    *,
    token: str,
    shop_id: str,
    body: dict | None,
    timeout: float = 12,
) -> dict[str, Any]:
    import requests

    url = f"https://{host}{path}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Token"] = token
    if shop_id:
        headers["ShopId"] = shop_id
        headers["ShopID"] = shop_id

    t0 = time.time()
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, params=(body or None), timeout=timeout)
        else:
            r = requests.post(
                url, headers=headers, json=(body if body is not None else {}), timeout=timeout
            )
        elapsed_ms = int((time.time() - t0) * 1000)
        data = None
        try:
            data = r.json() if r.text else None
        except Exception:
            data = None
        msg = None
        code_field = None
        if isinstance(data, dict):
            msg = data.get("message")
            code_field = data.get("code")
        if r.status_code == 200 and code_field in (200, 0, None):
            surface = "ok"
        elif r.status_code in (401, 403) or code_field in (401, 403):
            surface = "auth_required"
        elif r.status_code == 404 or code_field == 404:
            surface = "not_found"
        elif r.status_code == 400 or code_field == 400:
            surface = "bad_request_alive"
        elif r.status_code == 405:
            surface = "method_not_allowed"
        else:
            surface = f"http_{r.status_code}"
        data_n = None
        if isinstance(data, dict):
            d = data.get("data")
            if isinstance(d, list):
                data_n = len(d)
            elif isinstance(d, dict):
                for k in ("orders", "data", "items", "list"):
                    if isinstance(d.get(k), list):
                        data_n = len(d[k])
                        break
        return {
            "method": method,
            "http": r.status_code,
            "api_code": code_field,
            "message": (msg or (r.text or "")[:100])[:140],
            "surface": surface,
            "elapsed_ms": elapsed_ms,
            "data_n": data_n,
            "url": url,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "method": method,
            "http": 0,
            "api_code": None,
            "message": str(e)[:140],
            "surface": "unreachable",
            "elapsed_ms": int((time.time() - t0) * 1000),
            "data_n": None,
            "url": url,
        }


def icon_for_endpoint(ep: dict[str, Any], probe: dict[str, Any] | None) -> dict[str, Any]:
    from realtime_icon_feedback_mapper import chant, feedback_line

    icons = ["network", ROLE_ICON.get(ep["role"], "compass")]
    surf = (probe or {}).get("surface")
    if surf == "auth_required":
        icons.extend(["key", "lock"])
    elif surf == "ok":
        icons.append("monitor")
    elif surf in {"not_found", "unreachable"}:
        icons.append("wrench")
    elif surf == "bad_request_alive":
        icons.append("text")
    icons = list(dict.fromkeys(icons))
    detail = f"{ep['id']} {ep['path']} · {surf or 'catalog'}"
    return {
        "icons": icons,
        "icon_chant": chant(icons),
        "feedback": feedback_line(icons, detail),
    }


def deep_map(
    *,
    host: str = "online.gateway.ghn.vn",
    probe: bool = True,
    allow_mutate: bool = False,
    with_token: bool = True,
) -> dict[str, Any]:
    canonical = normalize_host(host)
    creds = load_creds() if with_token else {"token": "", "shop_id": ""}
    token = creds.get("token") or ""
    shop_id = creds.get("shop_id") or ""

    now = int(datetime.now(timezone.utc).timestamp())
    window_body = {
        "from_time": now - 3 * 86400,
        "to_time": now,
        "offset": 0,
        "limit": 5,
        "status": [],
    }

    rows: list[dict[str, Any]] = []
    by_role: dict[str, int] = {}
    by_surface: dict[str, int] = {}

    for ep in ORDER_ENDPOINTS:
        by_role[ep["role"]] = by_role.get(ep["role"], 0) + 1
        do_probe = probe and (ep.get("safe_probe") or allow_mutate)
        probes: list[dict[str, Any]] = []
        best: dict[str, Any] | None = None
        if do_probe:
            body = None
            if ep["role"] in {"list", "search"}:
                body = dict(window_body)
            for method in ep["methods"]:
                pr = _probe_one(
                    canonical,
                    ep["path"],
                    method,
                    token=token,
                    shop_id=shop_id,
                    body=body if method == "POST" else None,
                )
                probes.append(pr)
                rank = {
                    "ok": 0,
                    "bad_request_alive": 1,
                    "auth_required": 2,
                    "method_not_allowed": 3,
                    "not_found": 4,
                    "unreachable": 5,
                }
                if best is None or rank.get(pr["surface"], 9) < rank.get(best["surface"], 9):
                    best = pr
        surf = (best or {}).get("surface") or "catalog_only"
        by_surface[surf] = by_surface.get(surf, 0) + 1
        ic = icon_for_endpoint(ep, best)
        rows.append(
            {
                "id": ep["id"],
                "path": ep["path"],
                "url": f"https://{canonical}{ep['path']}",
                "methods": list(ep["methods"]),
                "role": ep["role"],
                "purpose": ep["purpose"],
                "pipe": ep.get("pipe"),
                "safe_probe": ep.get("safe_probe"),
                "probed": do_probe,
                "best": best,
                "probes": probes,
                "icon": ic,
            }
        )

    list_ok = any(
        r["id"] in {"order.all", "order.search"}
        and (r.get("best") or {}).get("surface") == "ok"
        for r in rows
    )
    alive_n = sum(
        1
        for r in rows
        if (r.get("best") or {}).get("surface")
        in {"ok", "auth_required", "bad_request_alive"}
    )

    from realtime_icon_feedback_mapper import chant, feedback_line

    global_icons = ["network", "compass", "monitor", "hash"]
    if not token:
        global_icons.extend(["key", "lock"])
    elif list_ok:
        global_icons.append("spark")
    else:
        global_icons.append("wrench")
    global_icons = list(dict.fromkeys(global_icons))

    report: dict[str, Any] = {
        "ok": alive_n > 0,
        "module": "ghn_order_endpoint_deep_mapper",
        "checked_at": utc_now(),
        "query": f"Mapper truy vấn sâu endpoint đơn · {host}",
        "host_input": host,
        "host_canonical": canonical,
        "token_present": bool(token),
        "shop_id_present": bool(shop_id),
        "catalog_n": len(ORDER_ENDPOINTS),
        "probed_n": sum(1 for r in rows if r.get("probed")),
        "alive_endpoint_n": alive_n,
        "list_fetch_ready": list_ok,
        "by_role": by_role,
        "by_surface": by_surface,
        "endpoints": rows,
        "icon": {
            "icons": global_icons,
            "icon_chant": chant(global_icons),
            "feedback": feedback_line(
                global_icons,
                f"{canonical} · alive={alive_n}/{len(rows)} · list_ok={list_ok} · "
                f"token={'Y' if token else 'N'}",
            ),
        },
        "mermaid": _mermaid(canonical, rows),
        "policy": {
            "owned_only": True,
            "no_dump_login": True,
            "mutate_default_off": True,
            "allow_mutate": allow_mutate,
        },
        "next_actions": [
            "python3 scripts/ghn_order_endpoint_deep_mapper.py --host online.gateway.ghn.vn",
            "python3 scripts/ghn_cookie_ingest.py ensure",
            "python3 scripts/scan_buucuc_orders.py --backends GHN --days 3 --limit 50",
        ],
    }

    if list_ok:
        report["verdict"] = (
            f"✅ Deep map {canonical}: alive={alive_n}/{len(rows)} · "
            f"list/search OK · {chant(global_icons)}"
        )
    elif alive_n and not token:
        report["verdict"] = (
            f"⚠ Deep map {canonical}: {alive_n} endpoint sống (auth_required) · "
            f"thiếu GHN_API_TOKEN · {chant(global_icons)}"
        )
        report["ok"] = True
    elif alive_n:
        report["verdict"] = (
            f"⚠ Deep map {canonical}: alive={alive_n}/{len(rows)} · "
            f"token có nhưng list chưa OK · {chant(global_icons)}"
        )
        report["ok"] = True
    else:
        report["verdict"] = f"❌ Deep map {canonical}: không endpoint nào phản hồi"

    return report


def _mermaid(host: str, rows: list[dict[str, Any]]) -> str:
    lines = [
        "flowchart LR",
        f'  H["{host}"]',
        '  subgraph ORD["shipping-order"]',
    ]
    for r in rows:
        if "/shipping-order" not in r["path"] and not r["path"].startswith("/a5/"):
            continue
        nid = r["id"].replace(".", "_")
        surf = (r.get("best") or {}).get("surface") or "cat"
        lines.append(f'    {nid}["{r["id"]}\\n{surf}"]')
        lines.append(f"    H --> {nid}")
    lines.append("  end")
    lines.append('  subgraph SUP["support"]')
    for r in rows:
        if "/shipping-order" in r["path"] or r["path"].startswith("/a5/"):
            continue
        nid = r["id"].replace(".", "_")
        surf = (r.get("best") or {}).get("surface") or "cat"
        lines.append(f'    {nid}["{r["id"]}\\n{surf}"]')
        lines.append(f"    H --> {nid}")
    lines.append("  end")
    return "\n".join(lines)


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🔬 MAPPER · GHN ORDER ENDPOINTS (DEEP)",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"Host: {report.get('host_input')} → {report.get('host_canonical')}",
        f"Token: {report.get('token_present')} · ShopId: {report.get('shop_id_present')} · "
        f"alive={report.get('alive_endpoint_n')}/{report.get('catalog_n')} · "
        f"list_ready={report.get('list_fetch_ready')}",
    ]
    ic = report.get("icon") or {}
    lines.append(f"Chant: {ic.get('icon_chant')}")
    lines.append(f"Feedback: {ic.get('feedback')}")
    lines.append(f"by_role: {report.get('by_role')}")
    lines.append(f"by_surface: {report.get('by_surface')}")
    lines.append("")
    lines.append("=== Endpoints ===")
    for r in report.get("endpoints") or []:
        best = r.get("best") or {}
        mark = {
            "ok": "✅",
            "auth_required": "🔑",
            "bad_request_alive": "○",
            "not_found": "✖",
            "unreachable": "✖",
            "catalog_only": "·",
        }.get(best.get("surface") or "catalog_only", "·")
        lines.append(
            f"{mark} [{r.get('role')}] {r.get('id')}  {','.join(r.get('methods') or [])}  {r.get('path')}"
        )
        lines.append(f"   {r.get('purpose')}")
        if best:
            lines.append(
                f"   probe: {best.get('method')} http={best.get('http')} "
                f"api={best.get('api_code')} surface={best.get('surface')} "
                f"data_n={best.get('data_n')} · {best.get('message')}"
            )
        lines.append(f"   icon: {(r.get('icon') or {}).get('icon_chant')}")
    lines.append("")
    lines.append("Policy: owned-only · mutate off · no dump-login")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "ghn_order_endpoint_deep_mapper.json"
    tp = REPORTS / "ghn_order_endpoint_deep_mapper.txt"
    mp = REPORTS / "ghn_order_endpoint_deep_mapper.mermaid.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    mp.write_text(
        "# GHN order endpoints deep map\n\n```mermaid\n"
        + (report.get("mermaid") or "")
        + "\n```\n",
        encoding="utf-8",
    )
    return {"json": str(jp), "txt": str(tp), "mermaid": str(mp)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mapper truy vấn sâu endpoint đơn GHN gateway")
    ap.add_argument("--host", default="online.gateway.ghn.vn")
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--allow-mutate", action="store_true")
    ap.add_argument("--no-token", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = deep_map(
        host=args.host,
        probe=not args.no_probe,
        allow_mutate=args.allow_mutate,
        with_token=not args.no_token,
    )
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
