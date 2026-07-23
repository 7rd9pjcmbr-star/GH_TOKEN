#!/usr/bin/env python3
"""Upstream đơn + token + quét bưu cục (127.0.0.1:18081) — sau nginx embed.

Endpoints:
  GET  /health
  GET  /orders · /orders/local · /orders/buucuc · /order/<id>
  GET  /v1/token/status
  POST /v1/token/set|/refresh|/ensure
  POST /v1/owned/fill
  POST /v1/orders/realtime
  POST /v1/buucuc/scan · /v1/orders/buucuc/scan

Owned-only. Không dump-login / Acc_all / stealer. Không pad demo khi đã có scan.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

CACHE_PATH = Path(__file__).resolve().parent / "orders_local_cache.json"
BUUCUC_CACHE = Path(__file__).resolve().parent / "orders_buucuc_scan_cache.json"


def load_local_orders() -> list[dict]:
    if not CACHE_PATH.is_file():
        return []
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    orders = data.get("orders") if isinstance(data, dict) else None
    return orders if isinstance(orders, list) else []


def load_buucuc_scan_orders() -> list[dict]:
    if not BUUCUC_CACHE.is_file():
        return []
    try:
        data = json.loads(BUUCUC_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    orders = data.get("orders") if isinstance(data, dict) else None
    return orders if isinstance(orders, list) else []


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reject_dump_payload(payload: dict) -> str | None:
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for mark in ("acc_all", "stealer", "internal_search", "ghn_tokens", "valid_accounts", "results_cookies"):
        if mark in blob:
            return f"rejected_dump_marker:{mark}"
    if isinstance(payload.get("accounts"), list) and len(payload["accounts"]) > 3:
        return "rejected_bulk_accounts"
    if isinstance(payload.get("tokens"), list) and len(payload["tokens"]) > 3:
        return "rejected_bulk_tokens"
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[token-order-upstream] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, payload: dict | list, extra: dict | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Mock-Upstream", "order-token-backend")
        self.send_header("X-Via-Module", "access_token_rotate+buucuc_scan")
        if extra:
            for k, v in extra.items():
                self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/health", "/"}:
            local = load_local_orders()
            buu = load_buucuc_scan_orders()
            self._send(
                200,
                {
                    "ok": True,
                    "service": "order-upstream",
                    "role": "token+orders+buucuc-scan after nginx",
                    "orders_local": len(local),
                    "orders_buucuc_scan": len(buu),
                    "routes": [
                        "/orders",
                        "/orders/local",
                        "/orders/buucuc",
                        "/v1/orders/local",
                        "/v1/token/status",
                        "/v1/token/set",
                        "/v1/token/ensure",
                        "/v1/token/pancake-ingest",
                        "/v1/owned/fill",
                        "/v1/orders/realtime",
                        "/v1/buucuc/scan",
                        "/v1/orders/buucuc/scan",
                        "/v1/pancake/ingest",
                    ],
                },
            )
            return
        if path in {"/orders/buucuc", "/v1/orders/buucuc"}:
            buu = load_buucuc_scan_orders()
            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or ["500"])[0] or 500)
            limit = max(1, min(limit, 10000))
            self._send(
                200,
                {
                    "ok": True,
                    "source": "buucuc_remote_scan",
                    "checked_at": utc_now(),
                    "total": len(buu),
                    "count": min(limit, len(buu)),
                    "orders": buu[:limit],
                },
            )
            return
        if path == "/orders":
            local = load_local_orders()
            buu = load_buucuc_scan_orders()
            if buu:
                merged, source = buu, "buucuc_remote_scan"
            elif local:
                merged, source = local, "owned_local_exports"
            else:
                merged, source = [], "empty_no_demo"
            self._send(
                200,
                {
                    "ok": True,
                    "source": source,
                    "checked_at": utc_now(),
                    "count": len(merged),
                    "local_total": len(local),
                    "buucuc_scan_total": len(buu),
                    "orders": merged[:500],
                },
            )
            return
        if path in {"/orders/local", "/v1/orders/local"}:
            local = load_local_orders()
            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or ["200"])[0] or 200)
            limit = max(1, min(limit, 5000))
            shop = (qs.get("shop_id") or [None])[0]
            status = (qs.get("status") or [None])[0]
            platform = (qs.get("platform") or [None])[0]
            filtered = local
            if shop:
                filtered = [o for o in filtered if str(o.get("shop_id")) == str(shop)]
            if status:
                filtered = [o for o in filtered if status.lower() in str(o.get("status") or "").lower()]
            if platform:
                filtered = [o for o in filtered if platform.lower() in str(o.get("platform") or "").lower()]
            self._send(
                200,
                {
                    "ok": True,
                    "source": "owned_local_exports",
                    "checked_at": utc_now(),
                    "total": len(local),
                    "count": min(limit, len(filtered)),
                    "filtered": len(filtered),
                    "shop_id": shop,
                    "status": status,
                    "orders": filtered[:limit],
                },
            )
            return
        if path.startswith("/order/"):
            oid = path.rsplit("/", 1)[-1]
            pool = load_buucuc_scan_orders() + load_local_orders()
            hit = next(
                (
                    o
                    for o in pool
                    if str(o.get("order_id")) == oid
                    or str(o.get("tracking_code") or "") == oid
                    or str(o.get("order_key") or "") == oid
                ),
                None,
            )
            if not hit:
                self._send(404, {"ok": False, "error": "not_found", "id": oid})
                return
            self._send(200, {"ok": True, "order": hit})
            return
        if path == "/v1/token/status":
            from access_token_rotate import status

            report = status()
            report["via"] = "nginx→upstream→access_token_rotate"
            self._send(200 if report.get("ok") else 400, report)
            return
        self._send(404, {"ok": False, "error": "unknown_path", "path": path})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        payload = self._read_json()
        rejected = _reject_dump_payload(payload)
        if rejected:
            self._send(
                403,
                {
                    "ok": False,
                    "error": "dump_login_forbidden",
                    "detail": rejected,
                    "policy": {"owned_only": True, "no_dump_login": True},
                },
            )
            return

        if path in {"/v1/buucuc/scan", "/v1/orders/buucuc/scan"}:
            from scan_buucuc_orders import build_report

            days = int(payload.get("days") or 3)
            limit = int(payload.get("limit") or 10000)
            backends = payload.get("backends") if isinstance(payload.get("backends"), list) else None
            notify = bool(payload.get("notify"))
            report = build_report(
                days=days,
                limit=limit,
                backends=backends,
                pipe=bool(payload.get("pipe", True)),
                write_cache=True,
                notify=notify,
            )
            out = {k: v for k, v in report.items() if k != "orders"}
            out["orders_count"] = report.get("count")
            out["orders_preview"] = (report.get("orders") or [])[:20]
            out["via"] = "nginx→upstream→scan_buucuc_orders"
            self._send(200 if report.get("ok") else 400, out)
            return

        if path in {"/v1/pancake/ingest", "/v1/token/pancake-ingest"}:
            from pancake_cookie_ingest import ingest_and_scan

            raw = str(payload.get("raw") or payload.get("cookies") or payload.get("text") or "")
            if not raw and payload.get("pos_jwt"):
                raw = f"pos_jwt={payload.get('pos_jwt')}"
            if not raw and payload.get("token"):
                raw = str(payload.get("token"))
            report = ingest_and_scan(
                raw,
                days=int(payload.get("days") or 3),
                limit=int(payload.get("limit") or 10000),
                scan=bool(payload.get("scan", True)),
                notify=bool(payload.get("notify")),
                force=bool(payload.get("force")),
            )
            report["via"] = "nginx→upstream→pancake_cookie_ingest→scan"
            self._send(200 if report.get("ok") else 400, report)
            return

        if path == "/v1/token/set":
            from access_token_rotate import set_access_token

            report = set_access_token(
                str(payload.get("platform") or ""),
                str(payload.get("token") or ""),
                user=(str(payload["user"]) if payload.get("user") else None),
                shop_id=(str(payload["shop_id"]) if payload.get("shop_id") else None),
                as_api_key=bool(payload.get("as_api_key")),
            )
            # Optional: auto-scan after pancake token set when scan=true
            if report.get("ok") and bool(payload.get("scan")) and str(payload.get("platform") or "").lower() in {
                "pancake",
                "pos",
                "",
            }:
                from scan_buucuc_orders import build_report

                scan_report = build_report(
                    days=int(payload.get("days") or 3),
                    limit=int(payload.get("limit") or 10000),
                    backends=["Pancake"],
                    pipe=True,
                    write_cache=True,
                    notify=bool(payload.get("notify")),
                )
                report["scan"] = {
                    "count": scan_report.get("count"),
                    "verdict": scan_report.get("verdict"),
                }
            report["via"] = "nginx→upstream→access_token_rotate.set"
            self._send(200 if report.get("ok") else 400, report)
            return

        if path == "/v1/owned/fill":
            from access_token_rotate import (
                SHOP_KEYS,
                TOKEN_KEYS,
                USER_KEYS,
                normalize_platform,
                set_access_token,
                upsert_env_values,
            )

            plat = normalize_platform(str(payload.get("platform") or ""))
            updates: dict[str, str] = {}
            extras = payload.get("extras") if isinstance(payload.get("extras"), dict) else {}
            for k, v in extras.items():
                if not isinstance(k, str):
                    continue
                key = k.strip().upper()
                if not key or not isinstance(v, (str, int)):
                    continue
                if any(x in key for x in ("PASS", "SECRET", "COOKIE", "PRIVATE")):
                    continue
                updates[key] = str(v).strip()

            user = str(payload["user"]).strip() if payload.get("user") else None
            shop_id = str(payload["shop_id"]).strip() if payload.get("shop_id") else None
            token = str(payload["token"]).strip() if payload.get("token") else None

            if plat and plat in USER_KEYS and user:
                updates[USER_KEYS[plat]] = user
            if plat and plat in SHOP_KEYS and shop_id:
                updates[SHOP_KEYS[plat]] = shop_id

            if plat and token and plat in TOKEN_KEYS:
                token_result = set_access_token(
                    plat, token, user=user, shop_id=shop_id, as_api_key=bool(payload.get("as_api_key"))
                )
            elif updates:
                path_env = upsert_env_values(updates)
                token_result = {
                    "ok": True,
                    "env_file": str(path_env),
                    "updates": sorted(updates.keys()),
                    "platform": plat or None,
                }
            else:
                token_result = {"ok": False, "error": "không có field owned để fill"}

            report = {
                "ok": bool(token_result.get("ok")),
                "via": "nginx→upstream→owned.fill→secrets",
                "platform": plat or None,
                "filled_keys": sorted(updates.keys())
                + ([TOKEN_KEYS[plat]] if plat and token and plat in TOKEN_KEYS else []),
                "shop_id": shop_id,
                "user": user,
                "token_set": bool(token),
                "result": token_result,
                "policy": {"owned_only": True, "no_dump_login": True},
            }
            self._send(200 if report["ok"] else 400, report)
            return

        if path == "/v1/token/refresh":
            from access_token_rotate import normalize_platform, refresh_viettelpost

            plat = normalize_platform(str(payload.get("platform") or "ViettelPost"))
            if plat != "ViettelPost":
                self._send(
                    400,
                    {
                        "ok": False,
                        "error": f"refresh tự động hiện hỗ trợ ViettelPost; {plat} dùng /v1/token/set",
                        "via": "nginx→upstream",
                    },
                )
                return
            report = refresh_viettelpost()
            report["via"] = "nginx→upstream→access_token_rotate.refresh"
            self._send(200 if report.get("ok") else 400, report)
            return

        if path == "/v1/token/ensure":
            from access_token_rotate import ensure_tokens

            plats = payload.get("platforms")
            platforms = [str(x) for x in plats] if isinstance(plats, list) else None
            report = ensure_tokens(platforms=platforms, auto_refresh_vtp=True)
            report["via"] = "nginx→upstream→access_token_rotate.ensure"
            self._send(200 if report.get("ok") else 400, report)
            return

        if path == "/v1/orders/realtime":
            from access_token_rotate import apply_realtime

            limit = int(payload.get("limit") or 20)
            notify = bool(payload.get("notify"))
            report = apply_realtime(limit=limit, notify=notify, via_nginx=False)
            report["via"] = "nginx→upstream→access_token_rotate→realtime_order_sync"
            if payload.get("buucuc_scan"):
                from scan_buucuc_orders import build_report as buucuc_scan

                scan = buucuc_scan(
                    days=int(payload.get("days") or 3),
                    limit=int(payload.get("buucuc_limit") or 10000),
                    notify=False,
                )
                report["buucuc_scan"] = {k: v for k, v in scan.items() if k != "orders"}
                report["buucuc_scan"]["orders_count"] = scan.get("count")
            report["policy"] = {
                "owned_only": True,
                "no_dump_login": True,
                "via_nginx": True,
                "no_demo_pad": True,
            }
            if report.get("ok"):
                report["verdict"] = (
                    f"✅ realtime via nginx · new={(report.get('cycle') or {}).get('new_count')}"
                )
            self._send(200 if report.get("ok") else 400, report)
            return

        self._send(404, {"ok": False, "error": "unknown_path", "path": path})


def main() -> int:
    host = os.environ.get("MOCK_ORDER_HOST", "127.0.0.1")
    port = int(os.environ.get("MOCK_ORDER_PORT", "18081"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(
        json.dumps(
            {
                "ok": True,
                "listen": f"{host}:{port}",
                "token_routes": True,
                "buucuc_scan": True,
            }
        ),
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
