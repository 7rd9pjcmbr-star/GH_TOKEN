#!/usr/bin/env python3
"""Mock order upstream (127.0.0.1:18081) — trả đơn mẫu cho nginx embed test."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[mock-order] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, payload: dict | list, extra: dict | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Mock-Upstream", "order-backend")
        if extra:
            for k, v in extra.items():
                self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/health", "/"}:
            self._send(200, {"ok": True, "service": "mock-order-upstream", "orders": len(ORDERS)})
            return
        if path == "/orders":
            self._send(
                200,
                {
                    "ok": True,
                    "source": "mock-order-upstream",
                    "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
        self._send(404, {"ok": False, "error": "unknown_path", "path": path})


def main() -> int:
    host = os.environ.get("MOCK_ORDER_HOST", "127.0.0.1")
    port = int(os.environ.get("MOCK_ORDER_PORT", "18081"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(json.dumps({"ok": True, "listen": f"{host}:{port}", "orders": len(ORDERS)}), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
