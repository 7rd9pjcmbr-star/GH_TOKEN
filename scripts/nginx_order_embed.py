#!/usr/bin/env python3
"""Module nhúng gọi đơn + token qua nginx — chạy khi cần (on-demand).

API:
  from nginx_order_embed import NginxOrderEmbed, run_when_needed
  NginxOrderEmbed().token_realtime_pipeline()  # nginx→token→realtime orders
  NginxOrderEmbed().once()          # start → gọi /orders → stop
  NginxOrderEmbed().ensure_up()     # giữ sống nếu cần nhiều lần
  NginxOrderEmbed().call_orders()   # gọi qua proxy (cần đang up)
  NginxOrderEmbed().stop()

CLI:
  python3 scripts/nginx_order_embed.py once|token-realtime|start|stop|status|orders|test

Owned-only. Không dump-login.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

NGX_DIR = ROOT / "docker" / "nginx-order"
CONF = NGX_DIR / "nginx.conf"
MOCK_SCRIPT = NGX_DIR / "mock_orders.py"
LOG = NGX_DIR / "logs" / "order_access.log"
ERR = NGX_DIR / "logs" / "order_error.log"
PID_NGINX = NGX_DIR / "tmp" / "nginx.pid"
STATE_FILE = ROOT / "secrets" / "nginx_order_embed.state.json"
REPORTS = ROOT / "reports" / "nginx-order-embed"
CLASSIFY = ROOT / "reports" / "telegram-classify"

DEFAULT_BASE = "http://127.0.0.1:18080"
DEFAULT_UPSTREAM = "http://127.0.0.1:18081"

UPSTREAM_HEADER_MAP = {
    "X-Upstream-Addr": "$upstream_addr",
    "X-Upstream-Status": "$upstream_status",
    "X-Upstream-Response-Time": "$upstream_response_time",
    "X-Upstream-Connect-Time": "$upstream_connect_time",
    "X-Upstream-Header-Time": "$upstream_header_time",
    "X-Upstream-Bytes-Received": "$upstream_bytes_received",
    "X-Upstream-Bytes-Sent": "$upstream_bytes_sent",
    "X-Upstream-Response-Length": "$upstream_response_length",
    "X-Upstream-Cache-Status": "$upstream_cache_status",
}

EMBEDDED_VARS = list(UPSTREAM_HEADER_MAP.values())


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url: str, timeout: float = 5.0) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k: v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        headers = {k: v for k, v in (e.headers.items() if e.headers else [])}
        return e.code, headers, e.read() if e.fp else b""


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 90.0,
) -> tuple[int, dict[str, str], Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            hdrs = {k: v for k, v in resp.headers.items()}
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        hdrs = {k: v for k, v in (e.headers.items() if e.headers else [])}
        raw = e.read() if e.fp else b""
        code = e.code
    except Exception as e:  # noqa: BLE001
        return 0, {}, {"error": str(e)[:200]}
    try:
        body = json.loads(raw.decode("utf-8", errors="replace") or "null")
    except json.JSONDecodeError:
        body = {"raw": raw[:300].decode("utf-8", errors="replace")}
    return code, hdrs, body


def header_get(headers: dict[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def wait_url(url: str, tries: int = 50, delay: float = 0.1) -> bool:
    for _ in range(tries):
        try:
            http_get(url, timeout=1.0)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(delay)
    return False


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_log_line(line: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("upstream", "status", "rt", "uct", "uht", "bytes_r", "bytes_s", "len", "cache"):
        m = re.search(rf"\b{key}=([^\s]+)", line)
        if m:
            out[key] = m.group(1)
    return out


def extract_upstream_headers(headers: dict[str, str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for hk, var in UPSTREAM_HEADER_MAP.items():
        out[var] = header_get(headers, hk)
    return out


@dataclass
class NginxOrderEmbed:
    """Module chạy script nhúng gọi đơn qua nginx khi cần."""

    base: str = DEFAULT_BASE
    upstream: str = DEFAULT_UPSTREAM
    auto_stop: bool = True
    _mock: subprocess.Popen | None = field(default=None, repr=False)
    _nginx: subprocess.Popen | None = field(default=None, repr=False)

    # —— lifecycle ——————————————————————————————

    def prepare_dirs(self) -> None:
        for p in (
            NGX_DIR / "logs",
            NGX_DIR / "tmp",
            NGX_DIR / "client_body",
            NGX_DIR / "proxy",
            NGX_DIR / "fastcgi",
            NGX_DIR / "uwsgi",
            NGX_DIR / "scgi",
            REPORTS,
            CLASSIFY,
        ):
            p.mkdir(parents=True, exist_ok=True)

    def nginx_test(self) -> dict:
        r = subprocess.run(
            ["nginx", "-t", "-c", str(CONF), "-p", str(NGX_DIR) + "/"],
            capture_output=True,
            text=True,
        )
        return {
            "ok": r.returncode == 0,
            "detail": (r.stderr or r.stdout or "")[-500:],
            "conf": str(CONF),
        }

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def status(self) -> dict:
        st = load_state()
        mock_up = False
        ngx_up = False
        try:
            code, _, _ = http_get(f"{self.upstream.rstrip('/')}/health", timeout=1.0)
            mock_up = code == 200
        except Exception:  # noqa: BLE001
            mock_up = False
        try:
            code, _, body = http_get(f"{self.base.rstrip('/')}/health", timeout=1.0)
            ngx_up = code == 200 and b"nginx-order-embed" in body
        except Exception:  # noqa: BLE001
            ngx_up = False
        nginx_pid = None
        if PID_NGINX.is_file():
            try:
                nginx_pid = int(PID_NGINX.read_text().strip())
            except ValueError:
                nginx_pid = None
        return {
            "ok": True,
            "module": "nginx_order_embed",
            "running": mock_up and ngx_up,
            "mock_up": mock_up,
            "nginx_up": ngx_up,
            "base": self.base,
            "upstream": self.upstream,
            "nginx_pid": nginx_pid,
            "nginx_pid_alive": self._pid_alive(nginx_pid) if nginx_pid else False,
            "mock_pid": st.get("mock_pid"),
            "state": st,
            "embedded_vars": EMBEDDED_VARS,
            "checked_at": utc_now(),
        }

    def start_mock(self) -> dict:
        if self.status()["mock_up"]:
            return {"ok": True, "already": True, "upstream": self.upstream}
        self._mock = subprocess.Popen(
            [sys.executable, str(MOCK_SCRIPT)],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ok = wait_url(f"{self.upstream.rstrip('/')}/health")
        st = load_state()
        st["mock_pid"] = self._mock.pid
        st["mock_started_at"] = utc_now()
        save_state(st)
        return {"ok": ok, "pid": self._mock.pid, "upstream": self.upstream}

    def start_nginx(self) -> dict:
        self.prepare_dirs()
        t = self.nginx_test()
        if not t["ok"]:
            return {"ok": False, "error": "nginx -t failed", "test": t}
        if self.status()["nginx_up"]:
            return {"ok": True, "already": True, "base": self.base}
        # stop stale
        self._signal_nginx_stop()
        self._nginx = subprocess.Popen(
            ["nginx", "-c", str(CONF), "-p", str(NGX_DIR) + "/"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ok = wait_url(f"{self.base.rstrip('/')}/health")
        st = load_state()
        st["nginx_pid"] = self._nginx.pid
        st["nginx_started_at"] = utc_now()
        save_state(st)
        return {"ok": ok, "pid": self._nginx.pid, "base": self.base, "test": t}

    def start(self) -> dict:
        """Bật mock + nginx khi cần."""
        self.prepare_dirs()
        mock = self.start_mock()
        if not mock.get("ok"):
            return {"ok": False, "step": "mock", "mock": mock}
        ngx = self.start_nginx()
        if not ngx.get("ok"):
            return {"ok": False, "step": "nginx", "mock": mock, "nginx": ngx}
        st = load_state()
        st["mode"] = "up"
        save_state(st)
        return {
            "ok": True,
            "mock": mock,
            "nginx": ngx,
            "status": self.status(),
            "hint": "Gọi call_orders() / CLI orders · stop() khi xong",
        }

    def _signal_nginx_stop(self) -> None:
        if PID_NGINX.is_file():
            try:
                pid = int(PID_NGINX.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.2)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        subprocess.run(
            ["nginx", "-s", "stop", "-c", str(CONF), "-p", str(NGX_DIR) + "/"],
            capture_output=True,
            text=True,
        )

    def stop(self) -> dict:
        """Tắt nginx + mock (on-demand teardown)."""
        self._signal_nginx_stop()
        st = load_state()
        for key in ("mock_pid", "nginx_pid"):
            pid = st.get(key)
            if isinstance(pid, int):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    pass
        for proc in (self._nginx, self._mock):
            if proc and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.terminate()
                    except Exception:  # noqa: BLE001
                        pass
        time.sleep(0.2)
        st["mode"] = "down"
        st["stopped_at"] = utc_now()
        save_state(st)
        self._mock = None
        self._nginx = None
        return {"ok": True, "mode": "down", "status": self.status()}

    def ensure_up(self) -> dict:
        s = self.status()
        if s.get("running"):
            return {"ok": True, "already": True, "status": s}
        return self.start()

    # —— calls ————————————————————————————————

    def call(self, path: str = "/orders", *, ensure: bool = True) -> dict:
        if ensure:
            up = self.ensure_up()
            if not up.get("ok"):
                return {"ok": False, "error": "embed stack not up", "start": up}
        url = f"{self.base.rstrip('/')}{path}"
        try:
            code, headers, body = http_get(url)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200], "url": url}
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {"raw": body[:300].decode("utf-8", errors="replace")}
        embedded = extract_upstream_headers(headers)
        return {
            "ok": 200 <= code < 300,
            "http": code,
            "url": url,
            "via": header_get(headers, "X-Order-Via"),
            "embedded": embedded,
            "payload": payload,
            "checked_at": utc_now(),
        }

    def call_orders(self, *, ensure: bool = True) -> dict:
        return self.call("/orders", ensure=ensure)

    def call_order(self, order_id: str, *, ensure: bool = True) -> dict:
        return self.call(f"/order/{order_id}", ensure=ensure)

    def call_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        ensure: bool = True,
        timeout: float = 90.0,
    ) -> dict:
        """Gọi JSON qua nginx — nhúng $upstream_* trước khi vào module token/đơn."""
        if ensure:
            up = self.ensure_up()
            if not up.get("ok"):
                return {"ok": False, "error": "embed stack not up", "start": up, "via_nginx": False}
        url = f"{self.base.rstrip('/')}{path}"
        code, headers, body = http_json(url, method=method, payload=payload, timeout=timeout)
        embedded = extract_upstream_headers(headers)
        ok = 200 <= code < 300 and isinstance(body, dict) and body.get("ok", True) is not False
        if code == 0:
            ok = False
        return {
            "ok": ok,
            "http": code,
            "url": url,
            "method": method,
            "via": header_get(headers, "X-Order-Via"),
            "pipeline": header_get(headers, "X-Pipeline"),
            "via_nginx": True,
            "embedded": embedded,
            "payload": body,
            "checked_at": utc_now(),
        }

    def token_status(self, *, ensure: bool = True) -> dict:
        return self.call_json("/v1/token/status", method="GET", ensure=ensure)

    def token_set(self, platform: str, token: str, *, ensure: bool = True, **extra: Any) -> dict:
        payload = {"platform": platform, "token": token, **extra}
        return self.call_json("/v1/token/set", method="POST", payload=payload, ensure=ensure)

    def token_refresh(self, platform: str = "ViettelPost", *, ensure: bool = True) -> dict:
        return self.call_json(
            "/v1/token/refresh", method="POST", payload={"platform": platform}, ensure=ensure
        )

    def token_ensure(self, platforms: list[str] | None = None, *, ensure: bool = True) -> dict:
        payload: dict[str, Any] = {}
        if platforms:
            payload["platforms"] = platforms
        return self.call_json("/v1/token/ensure", method="POST", payload=payload, ensure=ensure)

    def owned_fill(self, payload: dict[str, Any], *, ensure: bool = True) -> dict:
        return self.call_json("/v1/owned/fill", method="POST", payload=payload, ensure=ensure)

    def orders_realtime(self, *, limit: int = 20, notify: bool = False, ensure: bool = True) -> dict:
        """Pipeline: nginx → access_token_rotate → danh sách đơn realtime."""
        return self.call_json(
            "/v1/orders/realtime",
            method="POST",
            payload={"limit": limit, "notify": notify},
            timeout=120.0,
            ensure=ensure,
        )

    def buucuc_scan(
        self,
        *,
        days: int = 3,
        limit: int = 10000,
        backends: list[str] | None = None,
        notify: bool = False,
        ensure: bool = True,
    ) -> dict:
        """Pipeline: nginx → scan_buucuc_orders (GHN/VTP/SPX/VNPost/Pancake remote)."""
        payload: dict[str, Any] = {"days": days, "limit": limit, "notify": notify, "pipe": True}
        if backends:
            payload["backends"] = backends
        return self.call_json(
            "/v1/buucuc/scan",
            method="POST",
            payload=payload,
            timeout=180.0,
            ensure=ensure,
        )

    def pancake_ingest(
        self,
        raw: str,
        *,
        days: int = 3,
        limit: int = 10000,
        scan: bool = True,
        notify: bool = False,
        force: bool = False,
        ensure: bool = True,
    ) -> dict:
        """Pipeline: nginx → pancake_cookie_ingest → (optional) buucuc scan."""
        return self.call_json(
            "/v1/pancake/ingest",
            method="POST",
            payload={
                "raw": raw,
                "days": days,
                "limit": limit,
                "scan": scan,
                "notify": notify,
                "force": force,
            },
            timeout=180.0,
            ensure=ensure,
        )

    def token_realtime_pipeline(self, *, limit: int = 20, notify: bool = False, auto_stop: bool | None = None) -> dict:
        """Toàn bộ: bật nginx → nạp/ensure token module → gọi đơn RT → (optional) stop."""
        stop = self.auto_stop if auto_stop is None else auto_stop
        started = self.ensure_up()
        if not started.get("ok"):
            return {
                "ok": False,
                "checked_at": utc_now(),
                "via_nginx": False,
                "start": started,
                "verdict": "❌ Không bật được nginx — không nạp token/gọi đơn RT",
            }
        try:
            ensure = self.token_ensure(ensure=False)
            realtime = self.orders_realtime(limit=limit, notify=notify, ensure=False)
            status = self.token_status(ensure=False)
            orders = self.call_orders(ensure=False)
            ok = bool(realtime.get("ok")) and bool(ensure.get("ok") or (ensure.get("payload") or {}).get("ok"))
            rt_payload = realtime.get("payload") if isinstance(realtime.get("payload"), dict) else {}
            report = {
                "ok": ok,
                "checked_at": utc_now(),
                "via_nginx": True,
                "pipeline": "client→nginx→upstream→access_token_rotate→realtime",
                "embedded": realtime.get("embedded") or ensure.get("embedded"),
                "ensure": ensure.get("payload"),
                "token_status": status.get("payload"),
                "realtime": rt_payload,
                "nginx_orders": (orders.get("payload") or {}).get("orders")
                or (rt_payload.get("nginx_mock_orders") if isinstance(rt_payload, dict) else None),
                "access_log_tail": self.last_access_log(5),
                "verdict": (
                    f"✅ Pipeline nginx→token→realtime · "
                    f"new={(rt_payload.get('cycle') or {}).get('new_count')} · "
                    f"upstream={(realtime.get('embedded') or {}).get('$upstream_addr')}"
                ),
                "policy": {"owned_only": True, "no_dump_login": True, "via_nginx_required": True},
            }
            return report
        finally:
            if stop:
                self.stop()

    def last_access_log(self, limit: int = 5) -> list[dict]:
        if not LOG.is_file():
            return []
        lines = [ln for ln in LOG.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        out = []
        for ln in lines[-limit:]:
            out.append({"line": ln, "parsed": parse_log_line(ln)})
        return out

    # —— on-demand once / test ——————————————————

    def once(self, *, order_id: str | None = "OMS-NGX-001") -> dict:
        """Chạy khi cần: start → gọi đơn → (auto) stop → báo cáo."""
        started = self.start()
        if not started.get("ok"):
            return {
                "ok": False,
                "query": "nginx_order_embed.once",
                "checked_at": utc_now(),
                "start": started,
                "verdict": "❌ Không bật được nginx embed stack",
            }
        try:
            orders = self.call_orders(ensure=False)
            one = self.call_order(order_id, ensure=False) if order_id else None
            logs = self.last_access_log(3)
            present = {k: v for k, v in (orders.get("embedded") or {}).items() if v is not None}
            ok = bool(orders.get("ok")) and len(present) >= 7
            if one is not None:
                ok = ok and bool(one.get("ok"))
            report = {
                "ok": ok,
                "query": "nginx_order_embed.once — gọi đơn qua nginx khi cần",
                "module": "nginx_order_embed",
                "checked_at": utc_now(),
                "base": self.base,
                "upstream": self.upstream,
                "orders": orders,
                "one": one,
                "access_log_tail": logs,
                "embedded_vars": EMBEDDED_VARS,
                "verdict": (
                    "✅ On-demand embed OK — đơn qua nginx + $upstream_*"
                    if ok
                    else "❌ On-demand embed thất bại"
                ),
                "safety": {
                    "local_mock_only": True,
                    "no_dump_login": True,
                    "on_demand": True,
                },
                "next_actions": [
                    "python3 scripts/nginx_order_embed.py once",
                    "python3 scripts/nginx_order_embed.py start  # giữ sống",
                    "python3 scripts/nginx_order_embed.py orders",
                    "python3 scripts/nginx_order_embed.py stop",
                    "MaMoLogic.nginxEmbed.describe()",
                ],
            }
            write_outputs(report)
            return report
        finally:
            if self.auto_stop:
                self.stop()

    def test(self) -> dict:
        """Alias kiểm thử đầy đủ (giữ tương thích nginx_order_embed_test)."""
        prev = self.auto_stop
        self.auto_stop = True
        try:
            return self.once()
        finally:
            self.auto_stop = prev

    def describe(self) -> dict:
        return {
            "ok": True,
            "module": "nginx_order_embed",
            "title": "Nhúng gọi đơn + token qua nginx (on-demand)",
            "base": self.base,
            "upstream": self.upstream,
            "when_needed": [
                "once — chạy một lần rồi tắt",
                "start/ensure_up — bật khi cần nhiều lần",
                "token-realtime — nginx→access_token_rotate→danh sách đơn RT",
                "pancake-ingest — nginx→cookie/pos_jwt→secrets→quét đơn",
                "buucuc-scan — nginx→quét bưu cục remote",
                "orders/call_orders — lấy danh sách đơn",
                "stop — tắt sau khi xong",
            ],
            "routes": [
                "POST /v1/pancake/ingest",
                "POST /v1/token/pancake-ingest",
                "POST /v1/buucuc/scan",
                "POST /v1/token/set",
                "POST /v1/orders/realtime",
                "GET /orders/buucuc",
            ],
            "flow": (
                f"client → {self.base}/v1/token/*|/v1/orders/realtime|/orders "
                f"→ upstream {self.upstream} → access_token_rotate"
            ),
            "embedded_vars": EMBEDDED_VARS,
            "paths": {
                "conf": str(CONF),
                "mock": str(MOCK_SCRIPT),
                "access_log": str(LOG),
                "state": str(STATE_FILE),
                "reports": str(REPORTS),
            },
            "cli": (
                "python3 scripts/nginx_order_embed.py "
                "once|start|stop|status|orders|token-realtime|test"
            ),
            "python": "from nginx_order_embed import NginxOrderEmbed, run_when_needed",
            "status": self.status(),
        }


def run_when_needed(*, keep_alive: bool = False, order_id: str | None = "OMS-NGX-001") -> dict:
    """Entry on-demand cho panel / import."""
    mod = NginxOrderEmbed(auto_stop=not keep_alive)
    if keep_alive:
        up = mod.ensure_up()
        if not up.get("ok"):
            return {"ok": False, "start": up}
        orders = mod.call_orders(ensure=False)
        report = {
            "ok": orders.get("ok"),
            "mode": "keep_alive",
            "orders": orders,
            "status": mod.status(),
            "checked_at": utc_now(),
            "verdict": "✅ Embed stack đang chạy — gọi orders OK" if orders.get("ok") else "❌ Gọi orders lỗi",
        }
        write_outputs(report)
        return report
    return mod.once(order_id=order_id)


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🧩 NGINX ORDER EMBED MODULE")
    L(f"Lúc: {report.get('checked_at')}")
    L(report.get("verdict") or report.get("hint") or "")
    if report.get("via_nginx") or report.get("pipeline"):
        L(f"via_nginx={report.get('via_nginx')} pipeline={report.get('pipeline')}")
    if report.get("module") or report.get("query"):
        L(f"query: {report.get('query') or report.get('module')}")
    L(f"base={report.get('base')} upstream={report.get('upstream')}")
    L("")
    emb = report.get("embedded") or {}
    if emb:
        L("nginx $upstream_*:")
        for k, v in emb.items():
            if v is not None:
                L(f"  {k} = {v}")
    if "running" in (report.get("status") or {}):
        s = report["status"]
        L(f"status: running={s.get('running')} mock={s.get('mock_up')} nginx={s.get('nginx_up')}")
    if report.get("ensure"):
        L(f"ensure: {(report.get('ensure') or {}).get('verdict') or report.get('ensure')}")
    rt = report.get("realtime") or {}
    if isinstance(rt, dict) and (rt.get("cycle") or rt.get("verdict")):
        L(f"realtime: {rt.get('verdict')}")
        c = rt.get("cycle") or {}
        L(f"  new={c.get('new_count')} blocked={c.get('blocked')}")
    orders = report.get("orders") or {}
    if orders:
        L(f"orders http={orders.get('http')} via={orders.get('via')} ok={orders.get('ok')}")
        emb2 = orders.get("embedded") or report.get("embedded_headers") or {}
        for k, v in emb2.items():
            L(f"  {k} = {v}")
        payload = orders.get("payload") or {}
        for o in (payload.get("orders") or [])[:5]:
            L(f"  · {o.get('order_id')} · {o.get('tracking_code')} · {o.get('status')} · {o.get('backend')}")
    for o in (report.get("nginx_orders") or [])[:5]:
        L(f"  · {o.get('order_id')} · {o.get('tracking_code')} · {o.get('status')} · {o.get('backend')}")
    one = report.get("one") or {}
    if one:
        L(f"one: http={one.get('http')} ok={one.get('ok')}")
    for row in report.get("access_log_tail") or []:
        L(f"log: {row.get('line')}")
    if report.get("when_needed"):
        L("When needed:")
        for w in report["when_needed"]:
            L(f"· {w}")
    if report.get("next_actions"):
        L("Next:")
        for a in report["next_actions"]:
            L(f"· {a}")
    if report.get("cli"):
        L(f"CLI: {report.get('cli')}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    CLASSIFY.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    text = format_text(report)
    paths = {
        "json": REPORTS / "nginx_order_embed.json",
        "txt": REPORTS / "nginx_order_embed.txt",
        "rt_json": CLASSIFY / "nginx_order_embed.json",
        "rt_txt": CLASSIFY / "nginx_order_embed.txt",
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Module nhúng gọi đơn+token qua nginx (on-demand)")
    ap.add_argument(
        "command",
        nargs="?",
        default="once",
        choices=[
            "once",
            "start",
            "stop",
            "status",
            "orders",
            "order",
            "token-realtime",
            "buucuc-scan",
            "pancake-ingest",
            "test",
            "describe",
        ],
    )
    ap.add_argument("--id", default="OMS-NGX-001", help="order id cho lệnh order")
    ap.add_argument("--limit", type=int, default=20, help="limit đơn realtime")
    ap.add_argument("--days", type=int, default=3, help="số ngày quét bưu cục")
    ap.add_argument("--buucuc-limit", type=int, default=10000, help="limit quét bưu cục")
    ap.add_argument("--raw-file", default="", help="file cookie/JWT cho pancake-ingest")
    ap.add_argument("--raw", default="", help="chuỗi cookie/JWT cho pancake-ingest")
    ap.add_argument("--notify", action="store_true", help="gửi Telegram sau quét")
    ap.add_argument("--no-scan", action="store_true", help="pancake-ingest chỉ nạp token, không scan")
    ap.add_argument("--force", action="store_true", help="ép nạp cả token hết hạn (không khuyến nghị)")
    ap.add_argument("--keep", action="store_true", help="once/token-realtime không auto-stop")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    args = ap.parse_args(argv)

    mod = NginxOrderEmbed(base=args.base, upstream=args.upstream, auto_stop=not args.keep)

    cmd = args.command
    if cmd == "once":
        report = mod.once(order_id=args.id)
    elif cmd == "token-realtime":
        report = mod.token_realtime_pipeline(limit=args.limit, auto_stop=not args.keep)
        report.setdefault("base", mod.base)
        report.setdefault("upstream", mod.upstream)
        write_outputs(report)
    elif cmd == "buucuc-scan":
        started = mod.ensure_up()
        try:
            report = mod.buucuc_scan(
                days=args.days,
                limit=args.buucuc_limit,
                notify=args.notify,
                ensure=False,
            )
            report["start"] = started
            report["verdict"] = (
                f"✅ buucuc-scan via nginx · count={(report.get('payload') or {}).get('orders_count')}"
                if report.get("ok")
                else "❌ buucuc-scan"
            )
            report["checked_at"] = utc_now()
            write_outputs(report)
        finally:
            if not args.keep:
                mod.stop()
    elif cmd == "pancake-ingest":
        raw = args.raw
        if args.raw_file:
            raw = Path(args.raw_file).read_text(encoding="utf-8", errors="ignore")
        if not raw.strip():
            report = {
                "ok": False,
                "error": "cần --raw hoặc --raw-file",
                "verdict": "❌ pancake-ingest thiếu cookie/JWT",
                "checked_at": utc_now(),
            }
        else:
            started = mod.ensure_up()
            try:
                report = mod.pancake_ingest(
                    raw,
                    days=args.days,
                    limit=args.buucuc_limit,
                    scan=not args.no_scan,
                    notify=args.notify,
                    force=args.force,
                    ensure=False,
                )
                report["start"] = started
                payload = report.get("payload") if isinstance(report.get("payload"), dict) else {}
                report["verdict"] = payload.get("verdict") or (
                    "✅ pancake-ingest via nginx" if report.get("ok") else "❌ pancake-ingest"
                )
                report["checked_at"] = utc_now()
                write_outputs(report)
            finally:
                if not args.keep:
                    mod.stop()
    elif cmd == "test":
        report = mod.test()
    elif cmd == "start":
        report = mod.start()
        report["verdict"] = "✅ Embed stack UP" if report.get("ok") else "❌ Start failed"
        report["checked_at"] = utc_now()
        write_outputs(report)
    elif cmd == "stop":
        report = mod.stop()
        report["verdict"] = "⏹ Embed stack DOWN"
        report["checked_at"] = utc_now()
        write_outputs(report)
    elif cmd == "status":
        report = mod.status()
        report["verdict"] = "🟢 running" if report.get("running") else "⚪ stopped"
        write_outputs(report)
    elif cmd == "orders":
        report = mod.call_orders(ensure=True)
        report["verdict"] = "✅ /orders" if report.get("ok") else "❌ /orders"
        report["checked_at"] = utc_now()
        write_outputs(report)
    elif cmd == "order":
        report = mod.call_order(args.id, ensure=True)
        report["verdict"] = f"✅ /order/{args.id}" if report.get("ok") else "❌ order"
        report["checked_at"] = utc_now()
        write_outputs(report)
    else:
        report = mod.describe()
        report["verdict"] = "Module nginx_order_embed sẵn sàng dùng khi cần"
        report["checked_at"] = utc_now()
        write_outputs(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok", True) or cmd in {"stop", "describe", "status"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
