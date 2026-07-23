#!/usr/bin/env python3
"""Upstream đơn + token (127.0.0.1:18081) — mọi thao tác đi sau nginx embed.

Endpoints:
  GET  /health
  GET  /orders · /order/<id>          — danh sách đơn mẫu (local)
  GET  /v1/token/status
  POST /v1/token/set                  — nạp access token sở hữu → secrets
  POST /v1/token/refresh              — refresh ViettelPost owned
  POST /v1/token/ensure               — probe + auto-refresh VTP
  POST /v1/orders/realtime            — ensure → danh sách đơn realtime

Owned-only. Không dump-login / Acc_all / stealer.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

ORDERS = [
    {
        "order_id": "OMS-NGX-001",
        "tracking_code": "SPXVN067431106264",
        "status": "Đang giao",
        "backend": "SPX",
        "shop_id": "shop-demo-1",
        "province": "Nam Định",
        "created_at": "2026-07-23T08:00:00Z",
    },
    {
        "order_id": "OMS-NGX-002",
        "tracking_code": "GHN1234567890",
        "status": "Đã gửi hàng",
        "backend": "GHN",
        "shop_id": "shop-demo-2",
        "province": "Hồ Chí Minh",
        "created_at": "2026-07-23T09:15:00Z",
    },
    {
        "order_id": "OMS-NGX-003",
        "tracking_code": "VTP99887766",
        "status": "Mới tạo",
        "backend": "ViettelPost",
        "shop_id": "shop-demo-1",
        "province": "Hà Nội",
        "created_at": "2026-07-23T10:00:00Z",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reject_dump_payload(payload: dict) -> str | None:
    """Chặn payload dump/stealer bulk."""
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for mark in ("acc_all", "stealer", "internal_search", "ghn_tokens", "valid_accounts", "results_cookies"):
        if mark in blob:
            return f"rejected_dump_marker:{mark}"
    # bulk array of credentials
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
        self.send_header("X-Via-Module", "access_token_rotate")
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
            self._send(
                200,
                {
                    "ok": True,
                    "service": "mock-order-upstream",
                    "role": "token+orders after nginx",
                    "orders": len(ORDERS),
                    "routes": [
                        "/orders",
                        "/v1/token/status",
                        "/v1/token/set",
                        "/v1/token/ensure",
                        "/v1/orders/realtime",
                    ],
                },
            )
            return
        if path == "/orders":
            self._send(
                200,
                {
                    "ok": True,
                    "source": "mock-order-upstream",
                    "checked_at": utc_now(),
                    "count": len(ORDERS),
                    "orders": ORDERS,
                },
            )
            return
        if path.startswith("/order/"):
            oid = path.rsplit("/", 1)[-1]
            hit = next((o for o in ORDERS if o["order_id"] == oid or o["tracking_code"] == oid), None)
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

        if path == "/v1/token/set":
            from access_token_rotate import set_access_token

            report = set_access_token(
                str(payload.get("platform") or ""),
                str(payload.get("token") or ""),
                user=(str(payload["user"]) if payload.get("user") else None),
                shop_id=(str(payload["shop_id"]) if payload.get("shop_id") else None),
                as_api_key=bool(payload.get("as_api_key")),
            )
            report["via"] = "nginx→upstream→access_token_rotate.set"
            self._send(200 if report.get("ok") else 400, report)
            return

        if path == "/v1/owned/fill":
            # Nhúng shop_id / user / extras / token sở hữu → secrets (owned-only)
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
                # chặn password-like keys
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

            token_result = None
            if plat and token and plat in TOKEN_KEYS:
                token_result = set_access_token(
                    plat,
                    token,
                    user=user,
                    shop_id=shop_id,
                    as_api_key=bool(payload.get("as_api_key")),
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
            if isinstance(plats, list):
                platforms = [str(x) for x in plats]
            else:
                platforms = None
            report = ensure_tokens(platforms=platforms, auto_refresh_vtp=True)
            report["via"] = "nginx→upstream→access_token_rotate.ensure"
            self._send(200 if report.get("ok") else 400, report)
            return

        if path == "/v1/orders/realtime":
            # Nội bộ upstream: KHÔNG gọi lại nginx (tránh đệ quy).
            from access_token_rotate import apply_realtime

            limit = int(payload.get("limit") or 20)
            notify = bool(payload.get("notify"))
            report = apply_realtime(limit=limit, notify=notify, via_nginx=False)
            report["via"] = "nginx→upstream→access_token_rotate→realtime_order_sync"
            report["nginx_mock_orders"] = ORDERS
            report["policy"] = {"owned_only": True, "no_dump_login": True, "via_nginx": True}
            if report.get("ok"):
                report["verdict"] = (
                    f"✅ realtime via nginx · new={(report.get('cycle') or {}).get('new_count')} · "
                    f"mock={len(ORDERS)}"
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
                "orders": len(ORDERS),
                "token_routes": True,
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
