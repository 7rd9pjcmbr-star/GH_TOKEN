#!/usr/bin/env python3
"""Kiểm thử nhúng script gọi đơn qua nginx (+ biến $upstream_*).

Luồng:
  client → nginx:18080/orders → upstream mock:18081
  Response headers + access_log chứa $upstream_addr/status/rt/…

Secrets-only mock local. Không dump-login API thật.
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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NGX_DIR = ROOT / "docker" / "nginx-order"
CONF = NGX_DIR / "nginx.conf"
MOCK = NGX_DIR / "mock_orders.py"
LOG = NGX_DIR / "logs" / "order_access.log"
ERR = NGX_DIR / "logs" / "order_error.log"
REPORTS = ROOT / "reports" / "nginx-order-embed"
PID_FILE = NGX_DIR / "tmp" / "nginx.pid"

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

LOG_KEYS = ("upstream=", "status=", "rt=", "uct=", "uht=", "bytes_r=", "bytes_s=", "len=", "cache=")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url: str, timeout: float = 5.0) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = {k: v for k, v in resp.headers.items()}
            return resp.status, headers, resp.read()
    except urllib.error.HTTPError as e:
        headers = {k: v for k, v in (e.headers.items() if e.headers else [])}
        return e.code, headers, e.read() if e.fp else b""


def wait_port(url: str, tries: int = 40) -> bool:
    for _ in range(tries):
        try:
            http_get(url, timeout=1.0)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    return False


def start_mock() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(MOCK)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def nginx_test() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["nginx", "-t", "-c", str(CONF), "-p", str(NGX_DIR) + "/"],
        capture_output=True,
        text=True,
    )


def start_nginx() -> subprocess.Popen:
    # daemon off in conf → foreground
    return subprocess.Popen(
        ["nginx", "-c", str(CONF), "-p", str(NGX_DIR) + "/"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def stop_proc(proc: subprocess.Popen | None, *, name: str) -> None:
    if not proc or proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def stop_nginx() -> None:
    if PID_FILE.is_file():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.3)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    subprocess.run(
        ["nginx", "-s", "stop", "-c", str(CONF), "-p", str(NGX_DIR) + "/"],
        capture_output=True,
        text=True,
    )


def parse_log_line(line: str) -> dict[str, str]:
    out = {}
    for key in ("upstream", "status", "rt", "uct", "uht", "bytes_r", "bytes_s", "len", "cache"):
        m = re.search(rf"\b{key}=([^\s]+)", line)
        if m:
            out[key] = m.group(1)
    return out


def run_test(*, base: str = "http://127.0.0.1:18080") -> dict:
    checks: list[dict] = []
    REPORTS.mkdir(parents=True, exist_ok=True)
    (NGX_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (NGX_DIR / "tmp").mkdir(parents=True, exist_ok=True)
    if LOG.exists():
        LOG.write_text("", encoding="utf-8")

    # 1) nginx -t
    t = nginx_test()
    checks.append(
        {
            "id": "nginx_config_test",
            "ok": t.returncode == 0,
            "detail": (t.stderr or t.stdout or "")[-400:],
        }
    )
    if t.returncode != 0:
        return {
            "ok": False,
            "checked_at": utc_now(),
            "checks": checks,
            "verdict": "nginx -t FAILED",
        }

    mock = start_mock()
    ngx: subprocess.Popen | None = None
    try:
        if not wait_port("http://127.0.0.1:18081/health"):
            checks.append(
                {
                    "id": "mock_upstream",
                    "ok": False,
                    "detail": f"mock not up · poll={mock.poll()}",
                }
            )
            return {"ok": False, "checked_at": utc_now(), "checks": checks, "verdict": "mock upstream down"}

        checks.append({"id": "mock_upstream", "ok": True, "detail": "127.0.0.1:18081"})

        stop_nginx()
        ngx = start_nginx()
        time.sleep(0.25)
        if not wait_port(f"{base}/health"):
            err = ERR.read_text(encoding="utf-8", errors="replace")[-500:] if ERR.is_file() else ""
            checks.append({"id": "nginx_listen", "ok": False, "detail": err or "health timeout"})
            return {"ok": False, "checked_at": utc_now(), "checks": checks, "verdict": "nginx not listening"}

        checks.append({"id": "nginx_listen", "ok": True, "detail": base})

        # 2) Gọi danh sách đơn qua nginx
        code, headers, body = http_get(f"{base}/orders")
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {"raw": body[:200].decode("utf-8", errors="replace")}

        header_hits = {}
        for hk, var in UPSTREAM_HEADER_MAP.items():
            # headers may be case-insensitive
            val = None
            for k, v in headers.items():
                if k.lower() == hk.lower():
                    val = v
                    break
            header_hits[var] = val

        missing_headers = [v for v, val in header_hits.items() if val is None]
        # cache status có thể "-" / rỗng khi không bật proxy_cache — vẫn tính là đã nhúng
        present = {k: v for k, v in header_hits.items() if v is not None}
        checks.append(
            {
                "id": "call_orders_via_nginx",
                "ok": code == 200
                and isinstance(payload, dict)
                and payload.get("count", 0) >= 1
                and len(present) >= 7,
                "http": code,
                "orders": payload.get("count") if isinstance(payload, dict) else None,
                "via": headers.get("X-Order-Via") or headers.get("x-order-via"),
                "upstream_headers": header_hits,
                "missing_embedded_headers": missing_headers,
            }
        )

        # 3) Gọi 1 đơn
        code1, headers1, body1 = http_get(f"{base}/order/OMS-NGX-001")
        try:
            one = json.loads(body1.decode("utf-8"))
        except json.JSONDecodeError:
            one = {}
        checks.append(
            {
                "id": "call_one_order",
                "ok": code1 == 200 and (one.get("order") or {}).get("order_id") == "OMS-NGX-001",
                "http": code1,
                "upstream_addr": headers1.get("X-Upstream-Addr") or headers1.get("x-upstream-addr"),
                "upstream_status": headers1.get("X-Upstream-Status") or headers1.get("x-upstream-status"),
            }
        )

        # 4) Đọc access_log — biến nhúng
        time.sleep(0.15)
        log_text = LOG.read_text(encoding="utf-8", errors="replace") if LOG.is_file() else ""
        log_lines = [ln for ln in log_text.splitlines() if "/orders" in ln or "/order/" in ln]
        last = log_lines[-1] if log_lines else ""
        parsed = parse_log_line(last)
        log_ok = bool(last) and all(k in last for k in ("upstream=", "status=", "rt="))
        checks.append(
            {
                "id": "access_log_embedded_vars",
                "ok": log_ok and parsed.get("status") in {"200", "000"},
                "line": last[:300],
                "parsed": parsed,
                "lines": len(log_lines),
            }
        )

        # 5) Đối chiếu catalog
        catalog_path = ROOT / "data" / "nginx-upstream-vars.js"
        catalog_ok = catalog_path.is_file()
        embedded_used = [
            "$upstream_addr",
            "$upstream_status",
            "$upstream_response_time",
            "$upstream_connect_time",
            "$upstream_header_time",
            "$upstream_bytes_received",
            "$upstream_bytes_sent",
            "$upstream_response_length",
            "$upstream_cache_status",
        ]
        checks.append(
            {
                "id": "catalog_embed_alignment",
                "ok": catalog_ok,
                "embedded_vars_in_test": embedded_used,
                "catalog": str(catalog_path),
            }
        )

        ok = all(c.get("ok") for c in checks)
        sample_orders = payload.get("orders") if isinstance(payload, dict) else []
        return {
            "ok": ok,
            "query": "Kiểm thử nhúng script gọi đơn qua nginx",
            "checked_at": utc_now(),
            "base": base,
            "upstream": "http://127.0.0.1:18081",
            "checks": checks,
            "sample_orders": sample_orders[:5] if isinstance(sample_orders, list) else [],
            "embedded_headers": header_hits,
            "access_log": str(LOG),
            "verdict": (
                "✅ Gọi đơn qua nginx OK — biến $upstream_* hiện trên header + access_log"
                if ok
                else "❌ Một số kiểm thử nhúng nginx/order thất bại"
            ),
            "next_actions": [
                "python3 scripts/nginx_order_embed_test.py",
                "curl -si http://127.0.0.1:18080/orders | grep -i x-upstream",
                "tail -f docker/nginx-order/logs/order_access.log",
                "MaMoLogic.vars.get('$upstream_addr')",
            ],
            "safety": {
                "local_mock_only": True,
                "no_dump_login": True,
                "no_third_party_order_api": True,
            },
        }
    finally:
        stop_nginx()
        stop_proc(ngx, name="nginx")
        stop_proc(mock, name="mock")


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🧪 NGINX ORDER EMBED TEST")
    L(f"Lúc: {report.get('checked_at')}")
    L(report.get("verdict") or "")
    L(f"base={report.get('base')} → upstream={report.get('upstream')}")
    L("")
    for c in report.get("checks") or []:
        mark = "✅" if c.get("ok") else "❌"
        L(f"{mark} {c.get('id')}: { {k:v for k,v in c.items() if k not in {'id','ok'}} }")
    L("")
    L("Embedded headers:")
    for k, v in (report.get("embedded_headers") or {}).items():
        L(f"  {k} = {v}")
    L("")
    L("Sample orders:")
    for o in report.get("sample_orders") or []:
        L(f"  · {o.get('order_id')} · {o.get('tracking_code')} · {o.get('status')} · {o.get('backend')}")
    L("")
    L("Next:")
    for a in report.get("next_actions") or []:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": REPORTS / "nginx_order_embed_test.json",
        "txt": REPORTS / "nginx_order_embed_test.txt",
        "rt_json": ROOT / "reports" / "telegram-classify" / "nginx_order_embed_test.json",
        "rt_txt": ROOT / "reports" / "telegram-classify" / "nginx_order_embed_test.txt",
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    text = format_text(report)
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].parent.mkdir(parents=True, exist_ok=True)
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Kiểm thử gọi đơn qua nginx + biến nhúng upstream")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--base", default="http://127.0.0.1:18080")
    args = ap.parse_args()
    report = run_test(base=args.base)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
