#!/usr/bin/env python3
"""Lấy access token GHN (owned) → gọi đơn hàng GHN.

Luồng:
  1) resolve/ensure GHN_API_TOKEN (env / printA5 / cookie pending owned)
  2) resolve GHN_SHOP_ID qua /v2/shop/all (nếu thiếu)
  3) gọi đơn theo fetch roles: shipping-order/all + search (+ detail khi có mã)

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
GHN_BASE = "https://online-gateway.ghn.vn/shiip/public-api"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mask(token: str | None) -> str | None:
    if not token:
        return None
    t = token.strip()
    if len(t) <= 10:
        return "***"
    return f"{t[:4]}…{t[-4:]}"


def load_env() -> dict[str, str]:
    from owned_credentials import env_overlay_from_owned, load_env as base_load

    return env_overlay_from_owned(
        base_load(extra_files=(SECRETS / "order_session.env",))
    )


def resolve_access_token(*, try_pending: bool = True) -> dict[str, Any]:
    """Lấy / duy trì access token GHN (header Token)."""
    from ghn_cookie_ingest import ensure_ghn_session

    ensure = ensure_ghn_session(try_pending=try_pending)
    env = load_env()
    token = (env.get("GHN_API_TOKEN") or env.get("GHN_TOKEN") or "").strip()
    shop = (env.get("GHN_SHOP_ID") or "").strip() or None
    return {
        "ok": bool(ensure.get("alive") and token),
        "alive": bool(ensure.get("alive")),
        "token": token or None,
        "token_masked": _mask(token) if token else ensure.get("token_masked"),
        "shop_id": shop,
        "ensure": {
            "ok": ensure.get("ok"),
            "alive": ensure.get("alive"),
            "reingested": ensure.get("reingested"),
            "verdict": ensure.get("verdict"),
            "need": ensure.get("need"),
            "roles": ensure.get("roles"),
        },
        "checked_at": utc_now(),
    }


def resolve_shop_id(token: str, *, shop_id: str | None = None, persist: bool = True) -> dict[str, Any]:
    """Lấy ShopId từ env hoặc /v2/shop/all."""
    import requests

    out: dict[str, Any] = {
        "ok": False,
        "shop_id": (shop_id or "").strip() or None,
        "shops_n": 0,
        "http": None,
        "detail": "",
    }
    if out["shop_id"]:
        out["ok"] = True
        out["detail"] = "from_env"
        return out

    headers = {"Token": token, "Content-Type": "application/json"}
    try:
        r = requests.post(f"{GHN_BASE}/v2/shop/all", headers=headers, json={}, timeout=25)
        out["http"] = r.status_code
        data = r.json() if r.text else {}
    except Exception as e:  # noqa: BLE001
        out["detail"] = str(e)[:160]
        return out

    shops: list[Any] = []
    if isinstance(data, dict):
        d = data.get("data")
        if isinstance(d, list):
            shops = d
        elif isinstance(d, dict):
            for k in ("shops", "data", "items", "list"):
                if isinstance(d.get(k), list):
                    shops = d[k]
                    break
    out["shops_n"] = len(shops)
    if r.status_code in (401, 403):
        out["detail"] = f"auth http={r.status_code}"
        return out
    if not shops:
        out["detail"] = f"shop/all empty · code={data.get('code') if isinstance(data, dict) else None}"
        return out

    first = shops[0] if isinstance(shops[0], dict) else {}
    sid = first.get("_id") or first.get("id") or first.get("shop_id") or first.get("ShopID")
    if sid is None:
        out["detail"] = "shop row thiếu id"
        return out
    out["shop_id"] = str(sid)
    out["ok"] = True
    out["detail"] = "from_shop_all"
    if persist:
        try:
            from access_token_rotate import upsert_env_values

            upsert_env_values({"GHN_SHOP_ID": str(sid)})
            from order_session_env import export_session_env

            export_session_env()
        except Exception as e:  # noqa: BLE001
            out["persist_error"] = str(e)[:120]
    return out


def fetch_orders(
    *,
    token: str,
    shop_id: str | None = None,
    days: int = 3,
    limit: int = 50,
) -> dict[str, Any]:
    """Gọi đơn GHN theo roles (list/search)."""
    from scan_buucuc_orders import scan_ghn

    env = {
        "GHN_API_TOKEN": token,
        "GHN_TOKEN": token,
    }
    if shop_id:
        env["GHN_SHOP_ID"] = str(shop_id)
    # merge rest of env for any extras
    env = {**load_env(), **env}
    result = scan_ghn(env, days=days, limit=limit)
    status = result.get("status")
    fetched = int(result.get("fetched") or 0)
    return {
        "ok": status not in {"auth_fail", "missing_cred", "error"} or fetched > 0,
        "status": status,
        "fetched": fetched,
        "orders": result.get("orders") or [],
        "orders_preview": (result.get("orders") or [])[:10],
        "roles": result.get("roles"),
        "attempts": (result.get("attempts") or [])[:12],
        "detail": result.get("detail"),
        "backend": "GHN",
    }


def get_token_and_fetch_orders(
    *,
    days: int = 3,
    limit: int = 50,
    try_pending: bool = True,
    resolve_shop: bool = True,
) -> dict[str, Any]:
    """Đổi/lấy access token → gọi đơn hàng GHN."""
    report: dict[str, Any] = {
        "ok": False,
        "module": "ghn_access_token_orders",
        "checked_at": utc_now(),
        "token": None,
        "shop_id": None,
        "orders": None,
        "verdict": "",
        "next": [],
        "policy": {"owned_only": True, "no_dump_login": True},
    }

    tok = resolve_access_token(try_pending=try_pending)
    report["token"] = {
        "ok": tok.get("ok"),
        "alive": tok.get("alive"),
        "token_masked": tok.get("token_masked"),
        "ensure": tok.get("ensure"),
    }
    token = tok.get("token") or ""
    if not token or not tok.get("alive"):
        report["verdict"] = (
            "❌ Chưa có GHN_API_TOKEN sống — đặt printA5/cookie owned vào "
            "secrets/ghn_session.raw rồi chạy lại"
        )
        report["next"] = [
            "echo 'https://online-gateway.ghn.vn/.../printA5?token=UUID' > secrets/ghn_session.raw",
            "python3 scripts/ghn_access_token_orders.py run --days 3 --limit 50",
            "hoặc: python3 scripts/access_token_rotate.py set --platform GHN --token <OWNED> --direct",
        ]
        return report

    shop_id = tok.get("shop_id")
    shop_info: dict[str, Any] = {"ok": bool(shop_id), "shop_id": shop_id, "detail": "from_env"}
    if resolve_shop:
        shop_info = resolve_shop_id(token, shop_id=shop_id, persist=True)
    report["shop"] = shop_info
    shop_id = shop_info.get("shop_id") or shop_id
    report["shop_id"] = shop_id

    # apply roles before fetch
    try:
        from ghn_order_endpoint_deep_mapper import apply_roles

        roles = apply_roles(host="online-gateway.ghn.vn", ensure_token=False)
        report["roles"] = {
            "ok": roles.get("ok"),
            "fetch_roles": (roles.get("plan") or {}).get("fetch_roles"),
            "verdict": roles.get("verdict"),
        }
    except Exception as e:  # noqa: BLE001
        report["roles"] = {"ok": False, "error": str(e)[:120]}

    orders = fetch_orders(token=token, shop_id=shop_id, days=days, limit=limit)
    report["orders"] = {
        "ok": orders.get("ok"),
        "status": orders.get("status"),
        "fetched": orders.get("fetched"),
        "detail": orders.get("detail"),
        "roles": orders.get("roles"),
        "attempts": orders.get("attempts"),
        "preview": orders.get("orders_preview"),
    }
    # full orders kept for callers that need them (scan/realtime)
    report["order_rows"] = orders.get("orders") or []

    status = orders.get("status")
    fetched = int(orders.get("fetched") or 0)
    if status == "auth_fail":
        report["verdict"] = f"❌ Token GHN auth_fail · {_mask(token)} · {orders.get('detail')}"
        report["next"] = [
            "Cập nhật printA5/cookie owned mới → secrets/ghn_session.raw",
            "python3 scripts/ghn_cookie_ingest.py ensure",
        ]
        return report

    # Token đã sống: coi pipeline lấy token→gọi API là OK (kể cả cửa sổ đơn rỗng)
    report["ok"] = True
    if fetched > 0 or status in {"ok", "partial", "empty"}:
        report["verdict"] = (
            f"✅ Access token GHN → gọi đơn · fetched={fetched} · "
            f"shop={shop_id or '—'} · token={_mask(token)} · days={days}"
        )
    else:
        report["verdict"] = (
            f"⚠ Token sống · gọi đơn status={status} · "
            f"{orders.get('detail') or ''} · shop={shop_id or '—'}"
        )
    report["next"] = [
        "python3 scripts/ghn_access_token_orders.py run --days 3 --limit 50",
        "python3 scripts/scan_buucuc_orders.py --backends GHN --days 3 --limit 50",
        "python3 scripts/access_token_rotate.py apply-realtime --direct",
    ]
    return report


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("📦 GHN ACCESS TOKEN → GỌI ĐƠN")
    L(f"Lúc: {report.get('checked_at') or utc_now()}")
    L(report.get("verdict") or "")
    tok = report.get("token") or {}
    if tok:
        L(f"token: alive={tok.get('alive')} masked={tok.get('token_masked')}")
    if report.get("shop_id") or report.get("shop"):
        shop = report.get("shop") or {}
        L(f"shop_id: {report.get('shop_id')} · {shop.get('detail')} · shops_n={shop.get('shops_n')}")
    roles = report.get("roles") or {}
    if roles:
        L(f"roles: {roles.get('fetch_roles')} · {roles.get('verdict') or roles.get('error') or ''}")
    orders = report.get("orders") or {}
    if orders:
        L(f"orders: status={orders.get('status')} fetched={orders.get('fetched')} · {orders.get('detail') or ''}")
        for a in (orders.get("attempts") or [])[:6]:
            L(
                f"  · {a.get('role') or a.get('endpoint_id') or '?'} "
                f"http={a.get('http')} offset={a.get('offset')}"
            )
        for o in (orders.get("preview") or [])[:5]:
            if isinstance(o, dict):
                L(
                    f"  · {o.get('order_id') or o.get('tracking_code') or o.get('id')} "
                    f"· {o.get('status') or ''}"
                )
    if report.get("next"):
        L("")
        L("Next:")
        for n in report["next"]:
            L(f"· {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    # strip heavy rows from json report
    slim = {k: v for k, v in report.items() if k != "order_rows"}
    jp = REPORTS / "ghn_access_token_orders.json"
    tp = REPORTS / "ghn_access_token_orders.txt"
    jp.write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tp.write_text(format_text(report), encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lấy access token GHN → gọi đơn hàng")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ensure", help="Ensure/resolve GHN_API_TOKEN only")
    p_shop = sub.add_parser("shop", help="Resolve GHN_SHOP_ID từ token")
    p_shop.add_argument("--token", default="", help="Override token (owned)")

    p_ord = sub.add_parser("orders", help="Gọi đơn với token hiện tại")
    p_ord.add_argument("--days", type=int, default=3)
    p_ord.add_argument("--limit", type=int, default=50)

    p_run = sub.add_parser("run", help="ensure token → shop → gọi đơn")
    p_run.add_argument("--days", type=int, default=3)
    p_run.add_argument("--limit", type=int, default=50)
    p_run.add_argument("--no-pending", action="store_true")
    p_run.add_argument("--no-shop", action="store_true")

    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "ensure":
        report = resolve_access_token(try_pending=True)
        report["module"] = "ghn_access_token_orders.ensure"
        report["verdict"] = (
            f"✅ GHN token alive · {report.get('token_masked')}"
            if report.get("ok")
            else (report.get("ensure") or {}).get("verdict")
            or "❌ Thiếu GHN_API_TOKEN sống"
        )
        # don't leak raw token
        report.pop("token", None)
    elif args.cmd == "shop":
        env = load_env()
        token = (args.token or env.get("GHN_API_TOKEN") or "").strip()
        if not token:
            report = {"ok": False, "error": "Thiếu token", "checked_at": utc_now()}
        else:
            report = resolve_shop_id(token, shop_id=(env.get("GHN_SHOP_ID") or None), persist=True)
            report["checked_at"] = utc_now()
            report["token_masked"] = _mask(token)
            report["verdict"] = (
                f"✅ shop_id={report.get('shop_id')}" if report.get("ok") else f"❌ {report.get('detail')}"
            )
    elif args.cmd == "orders":
        env = load_env()
        token = (env.get("GHN_API_TOKEN") or "").strip()
        if not token:
            report = {
                "ok": False,
                "verdict": "❌ Thiếu GHN_API_TOKEN",
                "checked_at": utc_now(),
            }
        else:
            orders = fetch_orders(
                token=token,
                shop_id=(env.get("GHN_SHOP_ID") or None),
                days=args.days,
                limit=args.limit,
            )
            report = {
                "ok": orders.get("ok") or orders.get("status") not in {"auth_fail", "missing_cred"},
                "module": "ghn_access_token_orders.orders",
                "checked_at": utc_now(),
                "orders": {
                    "status": orders.get("status"),
                    "fetched": orders.get("fetched"),
                    "detail": orders.get("detail"),
                    "preview": orders.get("orders_preview"),
                    "attempts": orders.get("attempts"),
                },
                "verdict": (
                    f"✅ fetched={orders.get('fetched')}"
                    if (orders.get("fetched") or 0) > 0
                    else f"status={orders.get('status')} · {orders.get('detail')}"
                ),
            }
    else:
        report = get_token_and_fetch_orders(
            days=args.days,
            limit=args.limit,
            try_pending=not args.no_pending,
            resolve_shop=not args.no_shop,
        )

    write_outputs(report)
    if args.json:
        slim = {k: v for k, v in report.items() if k not in {"order_rows", "token"} or k == "token"}
        if isinstance(slim.get("token"), str):
            slim["token"] = _mask(slim["token"])
        print(json.dumps(slim, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report) if "verdict" in report else json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
