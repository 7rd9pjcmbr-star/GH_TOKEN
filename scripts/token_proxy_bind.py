#!/usr/bin/env python3
"""Rà soát repo lấy proxy → gắn 1 proxy / 1 token → nạp nginx gọi đơn GHN.

Nguồn proxy (owned):
  secrets/proxies.owned.txt
  secrets/proxy_list.txt
  quarantine/telegram/** (tên/nội dung proxy|socks)
  TELEGRAM getUpdates document proxy*

Format proxy:
  ip:port
  ip:port:user:pass
  user:pass@ip:port
  http://user:pass@host:port
  socks5://user:pass@host:port

Token nguồn: secrets/ghn_tokens.owned.txt | ghn_tokens.owned.json | GHN_API_TOKEN

Owned-only · mask proxy user/pass trong report · không dump-login.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
REPORTS = ROOT / "reports" / "telegram-classify"
INBOX = ROOT / "quarantine" / "telegram"
BIND_PATH = SECRETS / "token_proxy_bind.json"
PROXY_OWNED = SECRETS / "proxies.owned.txt"
PROXY_LIST = SECRETS / "proxy_list.txt"
PROXY_EXAMPLE = ROOT / "config" / "proxies.owned.example"

UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
# ip:port or ip:port:user:pass
IP_PORT_RE = re.compile(
    r"(?i)^\s*(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})(?::([^:\s]+):(\S+))?\s*$"
)
USERAT_RE = re.compile(
    r"(?i)^\s*([^:\s@]+):([^@\s]+)@(\d{1,3}(?:\.\d{1,3}){3}|[A-Za-z0-9.-]+):(\d{2,5})\s*$"
)
URL_RE = re.compile(r"(?i)^\s*((?:https?|socks5?|socks4)):\/\/(\S+)\s*$")
NAME_PROXY_RE = re.compile(r"(?i)(proxy|proxies|socks5?|socks4|proxy[_-]?list|proxy[_-]?pool)")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask(v: str | None, keep: int = 4) -> str | None:
    if not v:
        return None
    t = str(v).strip()
    if len(t) <= keep * 2:
        return "***"
    return f"{t[:keep]}…{t[-keep:]}"


def mask_proxy_url(url: str) -> str:
    """http://user:pass@host:port → http://***:***@host:port"""
    try:
        u = urlparse(url)
        if u.username or u.password:
            host = u.hostname or ""
            port = f":{u.port}" if u.port else ""
            return f"{u.scheme}://***:***@{host}{port}"
        return url
    except Exception:  # noqa: BLE001
        return re.sub(r"://[^/@:]+:[^/@]+@", "://***:***@", url)


def parse_proxy_line(line: str) -> dict[str, Any] | None:
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    scheme = "http"
    host = port = user = password = None

    m = URL_RE.match(raw)
    if m:
        scheme = m.group(1).lower()
        if scheme == "socks5h":
            scheme = "socks5"
        rest = m.group(2)
        # may be user:pass@host:port or host:port
        if "@" in rest:
            cred, hp = rest.rsplit("@", 1)
            if ":" in cred:
                user, password = cred.split(":", 1)
            else:
                user = cred
            if ":" in hp:
                host, port = hp.rsplit(":", 1)
            else:
                host = hp
        else:
            if ":" in rest:
                host, port = rest.rsplit(":", 1)
            else:
                host = rest
    else:
        m = USERAT_RE.match(raw)
        if m:
            user, password, host, port = m.group(1), m.group(2), m.group(3), m.group(4)
        else:
            m = IP_PORT_RE.match(raw)
            if not m:
                return None
            host, port = m.group(1), m.group(2)
            user, password = m.group(3), m.group(4)

    if not host or not port:
        return None
    try:
        port_i = int(port)
    except ValueError:
        return None
    if not (1 <= port_i <= 65535):
        return None

    if user and password:
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    elif user:
        auth = f"{quote(user, safe='')}@"
    else:
        auth = ""
    url = f"{scheme}://{auth}{host}:{port_i}"
    return {
        "scheme": scheme,
        "host": host,
        "port": port_i,
        "user": user,
        "has_auth": bool(user or password),
        "url": url,
        "url_masked": mask_proxy_url(url),
        "raw_masked": (
            f"{host}:{port_i}:***:***" if user else f"{host}:{port_i}"
        ),
    }


def scan_proxy_files() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Scan owned/known locations for proxy lines."""
    sources: list[Path] = []
    for p in (PROXY_OWNED, PROXY_LIST, SECRETS / "proxies.txt", SECRETS / "proxy.txt"):
        if p.is_file():
            sources.append(p)
    if INBOX.is_dir():
        for p in INBOX.rglob("*"):
            if not p.is_file() or p.suffix.lower() in {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".mp4",
                ".zip",
                ".xlsx",
                ".xls",
                ".db",
            }:
                continue
            if NAME_PROXY_RE.search(p.name) or NAME_PROXY_RE.search(str(p.relative_to(INBOX))):
                sources.append(p)

    found: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sources:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            meta.append({"path": str(path), "error": str(e)[:80]})
            continue
        n = 0
        for line in text.splitlines():
            prox = parse_proxy_line(line)
            if not prox:
                continue
            key = prox["url"]
            if key in seen:
                continue
            seen.add(key)
            prox["source"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            found.append(prox)
            n += 1
        meta.append(
            {
                "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                "parsed": n,
                "size": path.stat().st_size,
            }
        )
    return found, meta


def pull_telegram_proxy_docs(*, lookback: int = 500) -> dict[str, Any]:
    env = dict(os.environ)
    for p in (SECRETS / "telegram.env",):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"'))
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return {"ok": False, "error": "missing TELEGRAM_BOT_TOKEN", "downloaded": []}

    off = 0
    for op in (
        SECRETS / "telegram.offset",
        SECRETS / "telegram_inbox.offset",
        SECRETS / "telegram_ghn_scan.offset",
    ):
        if op.is_file():
            try:
                off = max(off, int(op.read_text().strip() or "0"))
            except ValueError:
                pass
    start = max(0, off - max(0, lookback))
    payload = {
        "offset": start,
        "timeout": 0,
        "allowed_updates": ["message", "channel_post"],
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getUpdates",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:160], "downloaded": []}

    if not data.get("ok"):
        return {"ok": False, "error": str(data)[:160], "downloaded": []}

    INBOX.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict] = []
    for upd in data.get("result") or []:
        msg = upd.get("message") or upd.get("channel_post") or {}
        doc = msg.get("document") or {}
        name = doc.get("file_name") or ""
        text = msg.get("text") or msg.get("caption") or ""
        if not NAME_PROXY_RE.search(name) and not NAME_PROXY_RE.search(text):
            continue
        if not doc.get("file_id"):
            # paste text proxies → secrets/proxies.owned.txt append candidates
            if text and any(parse_proxy_line(ln) for ln in text.splitlines()[:50]):
                PROXY_OWNED.parent.mkdir(parents=True, exist_ok=True)
                with PROXY_OWNED.open("a", encoding="utf-8") as f:
                    f.write(f"\n# from telegram msg {msg.get('message_id')} @ {utc_now()}\n")
                    for ln in text.splitlines():
                        if parse_proxy_line(ln):
                            f.write(ln.strip() + "\n")
                downloaded.append({"type": "text_append", "to": str(PROXY_OWNED.name)})
            continue
        # download named proxy file
        try:
            meta = urllib.request.urlopen(
                urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/getFile",
                    data=json.dumps({"file_id": doc["file_id"]}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=30,
            )
            meta_j = json.loads(meta.read().decode())
            fpath = meta_j["result"]["file_path"]
            dest = INBOX / re.sub(r"[^\w.\-+]", "_", name)[:180]
            with urllib.request.urlopen(
                f"https://api.telegram.org/file/bot{token}/{fpath}", timeout=120
            ) as r:
                dest.write_bytes(r.read())
            downloaded.append({"file": dest.name, "size": dest.stat().st_size, "orig": name})
        except Exception as e:  # noqa: BLE001
            downloaded.append({"orig": name, "error": str(e)[:100]})
    return {
        "ok": True,
        "updates_n": len(data.get("result") or []),
        "downloaded": downloaded,
    }


def load_tokens() -> list[dict[str, str]]:
    """Load GHN tokens (owned pool / env)."""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    pool = SECRETS / "ghn_tokens.owned.json"
    if pool.is_file():
        try:
            data = json.loads(pool.read_text(encoding="utf-8"))
            for t in data.get("tokens") or []:
                tok = (t.get("token") or "").strip().lower()
                if tok and UUID_RE.fullmatch(tok) and tok not in seen:
                    seen.add(tok)
                    rows.append({"token": tok, "user": t.get("user") or "", "source": "pool"})
        except json.JSONDecodeError:
            pass

    owned_txt = SECRETS / "ghn_tokens.owned.txt"
    if owned_txt.is_file():
        for line in owned_txt.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            m = UUID_RE.fullmatch(t) or UUID_RE.search(t)
            if not m:
                continue
            tok = (m.group(1) if m.lastindex else m.group(0)).lower()
            if tok in seen:
                continue
            seen.add(tok)
            rows.append({"token": tok, "user": "", "source": "owned_txt"})

    # quarantine owned-claimed multi file
    claim = SECRETS / "OWNED_CLAIM_GHN"
    claimed = claim.is_file() and claim.read_text(encoding="utf-8", errors="ignore").strip().lower() in {
        "1",
        "true",
        "yes",
        "i-own-this",
        "owned",
    }
    if claimed:
        q = INBOX / "_skipped_dumps" / "ghn_tokens_20260422_051037.txt"
        if q.is_file():
            from ghn_tokens_owned_maintain import parse_owned_file

            for r in parse_owned_file(q):
                tok = r["token"].lower()
                if tok in seen:
                    continue
                seen.add(tok)
                rows.append({"token": tok, "user": r.get("user") or "", "source": "claimed_file"})

    # env single
    for p in (SECRETS / "backend_pipes.env", SECRETS / "order_session.env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("GHN_API_TOKEN=") or line.startswith("GHN_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').lower()
                if UUID_RE.fullmatch(tok) and tok not in seen:
                    seen.add(tok)
                    rows.append({"token": tok, "user": "", "source": "env"})
    return rows


def bind_token_proxy(
    tokens: list[dict[str, str]],
    proxies: list[dict[str, Any]],
    *,
    mode: str = "round_robin",
) -> list[dict[str, Any]]:
    """Gắn 1 proxy / 1 token (round-robin hoặc zip)."""
    binds: list[dict[str, Any]] = []
    if not tokens:
        return binds
    for i, tok in enumerate(tokens):
        prox = None
        if proxies:
            if mode == "zip":
                prox = proxies[i] if i < len(proxies) else proxies[i % len(proxies)]
            else:
                prox = proxies[i % len(proxies)]
        binds.append(
            {
                "index": i,
                "token_masked": mask(tok["token"]),
                "token": tok["token"],
                "user_masked": mask(tok.get("user"), 2) if tok.get("user") else None,
                "token_source": tok.get("source"),
                "proxy": (
                    {
                        "url": prox["url"],
                        "url_masked": prox["url_masked"],
                        "scheme": prox["scheme"],
                        "host": prox["host"],
                        "port": prox["port"],
                        "has_auth": prox["has_auth"],
                        "source": prox.get("source"),
                    }
                    if prox
                    else None
                ),
                "proxy_masked": prox["url_masked"] if prox else None,
                "direct": prox is None,
            }
        )
    return binds


def save_bind(binds: list[dict[str, Any]], *, meta: dict[str, Any]) -> Path:
    SECRETS.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now(),
        "module": "token_proxy_bind",
        "count": len(binds),
        "with_proxy": sum(1 for b in binds if b.get("proxy")),
        "direct": sum(1 for b in binds if b.get("direct")),
        "meta": meta,
        "bindings": binds,
        "policy": {"owned_only": True, "mask_proxy_auth": True},
    }
    BIND_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(BIND_PATH, 0o600)
    except OSError:
        pass
    return BIND_PATH


def load_bind() -> dict[str, Any]:
    if not BIND_PATH.is_file():
        return {"bindings": []}
    try:
        return json.loads(BIND_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"bindings": []}


def ensure_proxy_example() -> None:
    if PROXY_EXAMPLE.is_file() or PROXY_OWNED.is_file():
        return
    SECRETS.mkdir(parents=True, exist_ok=True)
    PROXY_EXAMPLE.write_text(
        "# Owned egress proxies — copy → secrets/proxies.owned.txt\n"
        "# Formats:\n"
        "# 1.2.3.4:8080\n"
        "# 1.2.3.4:8080:user:pass\n"
        "# user:pass@1.2.3.4:8080\n"
        "# http://user:pass@1.2.3.4:8080\n"
        "# socks5://user:pass@1.2.3.4:1080\n",
        encoding="utf-8",
    )


def requests_proxies_dict(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    # requests: socks needs PySocks; http/https fine
    return {"http": proxy_url, "https": proxy_url}


def probe_ghn_via_proxy(token: str, proxy_url: str | None, *, timeout: int = 20) -> dict[str, Any]:
    import requests

    headers = {"Token": token, "Content-Type": "application/json"}
    url = "https://online-gateway.ghn.vn/shiip/public-api/master-data/province"
    proxies = requests_proxies_dict(proxy_url)
    try:
        r = requests.get(url, headers=headers, proxies=proxies, timeout=timeout)
        data = r.json() if r.text else {}
        ok = r.status_code == 200 and isinstance(data, dict) and data.get("code") == 200
        return {
            "ok": ok,
            "http": r.status_code,
            "code": data.get("code") if isinstance(data, dict) else None,
            "message": (data.get("message") if isinstance(data, dict) else None) or None,
            "proxy_masked": mask_proxy_url(proxy_url) if proxy_url else None,
            "direct": not bool(proxy_url),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "http": 0,
            "error": str(e)[:160],
            "proxy_masked": mask_proxy_url(proxy_url) if proxy_url else None,
            "direct": not bool(proxy_url),
        }


def fetch_orders_via_proxy(
    token: str,
    proxy_url: str | None,
    *,
    shop_id: str | None = None,
    days: int = 3,
    limit: int = 20,
) -> dict[str, Any]:
    """Gọi đơn GHN qua proxy (roles all/search)."""
    import requests
    from datetime import timedelta

    headers = {"Token": token, "Content-Type": "application/json"}
    if shop_id:
        headers["ShopId"] = str(shop_id)
        headers["ShopID"] = str(shop_id)
    proxies = requests_proxies_dict(proxy_url)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, days))
    body = {
        "from_time": int(start.timestamp()),
        "to_time": int(end.timestamp()),
        "offset": 0,
        "limit": min(200, limit),
    }
    urls = [
        "https://online-gateway.ghn.vn/shiip/public-api/v2/shipping-order/all",
        "https://online-gateway.ghn.vn/shiip/public-api/v2/shipping-order/search",
    ]
    attempts = []
    orders: list[dict] = []
    for url in urls:
        try:
            r = requests.post(url, headers=headers, json=body, proxies=proxies, timeout=30)
            data = r.json() if r.text else {}
            attempts.append(
                {
                    "url": url.split("/")[-1],
                    "http": r.status_code,
                    "code": data.get("code") if isinstance(data, dict) else None,
                }
            )
            if r.status_code in (401, 403):
                return {
                    "ok": False,
                    "status": "auth_fail",
                    "fetched": 0,
                    "orders": [],
                    "attempts": attempts,
                    "proxy_masked": mask_proxy_url(proxy_url) if proxy_url else None,
                }
            rows = []
            if isinstance(data, dict):
                d = data.get("data")
                if isinstance(d, list):
                    rows = d
                elif isinstance(d, dict):
                    for k in ("orders", "data", "items", "list"):
                        if isinstance(d.get(k), list):
                            rows = d[k]
                            break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("order_code") or row.get("client_order_code") or "")
                if not code:
                    continue
                orders.append(
                    {
                        "order_id": code,
                        "tracking_code": str(row.get("order_code") or code),
                        "status": row.get("status"),
                        "backend": "GHN",
                    }
                )
                if len(orders) >= limit:
                    break
            if orders:
                break
        except Exception as e:  # noqa: BLE001
            attempts.append({"url": url.split("/")[-1], "error": str(e)[:100]})
    return {
        "ok": True,
        "status": "ok" if orders else "empty",
        "fetched": len(orders),
        "orders": orders[:limit],
        "attempts": attempts,
        "proxy_masked": mask_proxy_url(proxy_url) if proxy_url else None,
    }


def run_bind(
    *,
    pull_telegram: bool = True,
    mode: str = "round_robin",
    max_tokens: int = 0,
) -> dict[str, Any]:
    ensure_proxy_example()
    pull = {"ok": False, "skipped": True}
    if pull_telegram:
        pull = pull_telegram_proxy_docs()

    proxies, src_meta = scan_proxy_files()
    tokens = load_tokens()
    if max_tokens > 0:
        tokens = tokens[:max_tokens]

    binds = bind_token_proxy(tokens, proxies, mode=mode)
    meta = {
        "proxy_sources": src_meta,
        "proxy_n": len(proxies),
        "token_n": len(tokens),
        "mode": mode,
        "telegram_pull": {
            "ok": pull.get("ok"),
            "downloaded": len(pull.get("downloaded") or []),
            "error": pull.get("error"),
        },
    }
    path = save_bind(binds, meta=meta)

    report: dict[str, Any] = {
        "ok": bool(proxies) and bool(tokens),
        "module": "token_proxy_bind",
        "checked_at": utc_now(),
        "bind_file": str(path),
        "proxy_n": len(proxies),
        "token_n": len(tokens),
        "bound_n": sum(1 for b in binds if b.get("proxy")),
        "direct_n": sum(1 for b in binds if b.get("direct")),
        "proxy_sources": src_meta,
        "proxy_preview": [
            {"url_masked": p["url_masked"], "scheme": p["scheme"], "source": p.get("source")}
            for p in proxies[:10]
        ],
        "bind_preview": [
            {
                "token_masked": b["token_masked"],
                "proxy_masked": b.get("proxy_masked"),
                "direct": b.get("direct"),
                "token_source": b.get("token_source"),
            }
            for b in binds[:12]
        ],
        "telegram_pull": pull,
        "verdict": "",
        "next": [],
        "policy": {"owned_only": True, "mask_proxy_auth": True},
    }

    if not proxies and not tokens:
        report["verdict"] = (
            "❌ Repo không có egress proxy + chưa có token — "
            "điền secrets/proxies.owned.txt và ghn_tokens owned"
        )
    elif not proxies:
        report["verdict"] = (
            f"⚠ Có {len(tokens)} token nhưng 0 proxy trong repo — "
            "copy list proxy owned → secrets/proxies.owned.txt rồi chạy lại"
        )
        report["ok"] = False
        report["next"] = [
            "python3 scripts/proxy_saas_windows_audit.py  # cửa sổ ProxyFlow/Proxy Pool",
            "Export Proxy Pool (kubernetes2 ScanToolmanus V3) → secrets/proxies.owned.txt",
            "printf '%s\\n' 'ip:port' 'ip:port:user:pass' >> secrets/proxies.owned.txt",
            "python3 scripts/token_proxy_bind.py bind",
            "python3 scripts/token_proxy_bind.py nginx-orders --limit-tokens 5",
        ]
    elif not tokens:
        report["verdict"] = (
            f"⚠ Có {len(proxies)} proxy nhưng 0 token — "
            "python3 scripts/ghn_tokens_owned_maintain.py --i-own-this"
        )
        report["ok"] = False
    else:
        report["verdict"] = (
            f"✅ Bind {report['bound_n']} token↔proxy "
            f"(tokens={len(tokens)} proxies={len(proxies)} mode={mode}) → {path.name}"
        )
        report["next"] = [
            "python3 scripts/token_proxy_bind.py nginx-orders --days 3 --limit 20",
            "python3 scripts/nginx_order_embed.py ghn-token-proxy-orders --keep",
        ]
    write_outputs(report)
    return report


def run_nginx_orders(
    *,
    days: int = 3,
    limit: int = 20,
    limit_tokens: int = 10,
    probe_only: bool = False,
    via_nginx: bool = True,
    keep: bool = False,
) -> dict[str, Any]:
    """Nạp bind → (nginx) → gọi đơn từng token+proxy."""
    data = load_bind()
    binds = data.get("bindings") or []
    if not binds:
        # auto bind first
        br = run_bind(pull_telegram=True)
        data = load_bind()
        binds = data.get("bindings") or []
        auto = br
    else:
        auto = None

    binds = binds[: max(1, limit_tokens)] if limit_tokens else binds
    report: dict[str, Any] = {
        "ok": False,
        "module": "token_proxy_bind.nginx_orders",
        "checked_at": utc_now(),
        "via_nginx": via_nginx,
        "auto_bind": auto.get("verdict") if isinstance(auto, dict) else None,
        "tried": 0,
        "alive_proxy": 0,
        "auth_fail": 0,
        "proxy_fail": 0,
        "fetched_total": 0,
        "results": [],
        "verdict": "",
        "policy": {"owned_only": True},
    }

    def _work() -> dict[str, Any]:
        results = []
        alive = auth_fail = proxy_fail = fetched = 0
        for b in binds:
            token = b.get("token") or ""
            prox = (b.get("proxy") or {}).get("url") if b.get("proxy") else None
            if not token:
                continue
            probe = probe_ghn_via_proxy(token, prox)
            entry: dict[str, Any] = {
                "token_masked": b.get("token_masked") or mask(token),
                "proxy_masked": b.get("proxy_masked"),
                "direct": b.get("direct"),
                "probe": {
                    "ok": probe.get("ok"),
                    "http": probe.get("http"),
                    "message": probe.get("message") or probe.get("error"),
                },
            }
            if probe.get("ok"):
                alive += 1
                if not probe_only:
                    orders = fetch_orders_via_proxy(
                        token, prox, days=days, limit=limit
                    )
                    entry["orders"] = {
                        "status": orders.get("status"),
                        "fetched": orders.get("fetched"),
                        "attempts": orders.get("attempts"),
                        "preview": (orders.get("orders") or [])[:5],
                    }
                    fetched += int(orders.get("fetched") or 0)
            else:
                msg = str(probe.get("message") or probe.get("error") or "")
                if probe.get("http") in (401, 403) or "not valid" in msg.lower() or "unauthorized" in msg.lower():
                    auth_fail += 1
                else:
                    proxy_fail += 1
            results.append(entry)
        return {
            "tried": len(results),
            "alive_proxy": alive,
            "auth_fail": auth_fail,
            "proxy_fail": proxy_fail,
            "fetched_total": fetched,
            "results": results,
        }

    if via_nginx:
        try:
            from nginx_order_embed import NginxOrderEmbed

            mod = NginxOrderEmbed(auto_stop=not keep)
            started = mod.ensure_up()
            if not started.get("ok"):
                report["verdict"] = "❌ Không bật được nginx embed"
                report["start"] = started
                # fallback direct
                work = _work()
                report.update(work)
                report["via_nginx"] = False
                report["fallback_direct"] = True
            else:
                try:
                    res = mod.call_json(
                        "/v1/ghn/token-proxy-orders",
                        method="POST",
                        payload={
                            "days": days,
                            "limit": limit,
                            "limit_tokens": limit_tokens,
                            "probe_only": probe_only,
                        },
                        timeout=300.0,
                        ensure=False,
                    )
                    payload = res.get("payload") if isinstance(res.get("payload"), dict) else {}
                    if payload:
                        report.update(
                            {
                                "ok": bool(payload.get("ok")),
                                "tried": payload.get("tried"),
                                "alive_proxy": payload.get("alive_proxy"),
                                "auth_fail": payload.get("auth_fail"),
                                "proxy_fail": payload.get("proxy_fail"),
                                "fetched_total": payload.get("fetched_total"),
                                "results": payload.get("results") or [],
                                "verdict": payload.get("verdict"),
                                "via_nginx": True,
                                "embedded": res.get("embedded"),
                                "http": res.get("http"),
                            }
                        )
                    else:
                        work = _work()
                        report.update(work)
                        report["via_nginx"] = False
                        report["nginx_error"] = res.get("error") or res
                finally:
                    if not keep:
                        mod.stop()
        except Exception as e:  # noqa: BLE001
            work = _work()
            report.update(work)
            report["via_nginx"] = False
            report["nginx_exception"] = str(e)[:160]
    else:
        work = _work()
        report.update(work)

    if not report.get("verdict"):
        if report.get("alive_proxy"):
            report["ok"] = True
            report["verdict"] = (
                f"✅ Token+proxy → nginx/orders · alive={report.get('alive_proxy')}/"
                f"{report.get('tried')} · fetched={report.get('fetched_total')} · "
                f"auth_fail={report.get('auth_fail')} proxy_fail={report.get('proxy_fail')}"
            )
        elif report.get("tried") and not report.get("proxy_n", 1):
            report["verdict"] = (
                f"⚠ Thử {report.get('tried')} token · 0 proxy sống/gắn · "
                f"auth_fail={report.get('auth_fail')} proxy_fail={report.get('proxy_fail')}"
            )
        else:
            report["verdict"] = (
                f"⚠ Token+proxy orders · tried={report.get('tried')} · "
                f"alive={report.get('alive_proxy')} · auth_fail={report.get('auth_fail')} · "
                f"proxy_fail={report.get('proxy_fail')} · fetched={report.get('fetched_total')}"
            )
            # still ok=False if nothing alive
            report["ok"] = bool(report.get("alive_proxy"))

    if report.get("bound_n") is None:
        report["proxy_n"] = sum(1 for b in binds if b.get("proxy"))
        report["token_n"] = len(binds)

    write_outputs(report)
    return report


def run_nginx_orders_direct(
    *,
    days: int = 3,
    limit: int = 20,
    limit_tokens: int = 10,
    probe_only: bool = False,
) -> dict[str, Any]:
    """Upstream handler entry (không đệ quy nginx)."""
    return run_nginx_orders(
        days=days,
        limit=limit,
        limit_tokens=limit_tokens,
        probe_only=probe_only,
        via_nginx=False,
        keep=False,
    )


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔌 TOKEN ↔ PROXY → NGINX GỌI ĐƠN")
    L(f"Lúc: {report.get('checked_at') or utc_now()}")
    L(report.get("verdict") or "")
    L(
        f"proxies={report.get('proxy_n')} tokens={report.get('token_n')} "
        f"bound={report.get('bound_n')} direct={report.get('direct_n')} "
        f"via_nginx={report.get('via_nginx')}"
    )
    if report.get("proxy_sources") is not None:
        L("Proxy sources:")
        for s in report.get("proxy_sources") or []:
            L(f"  · {s.get('path')} parsed={s.get('parsed')} err={s.get('error')}")
    for p in report.get("proxy_preview") or []:
        L(f"  proxy {p.get('scheme')} {p.get('url_masked')} ← {p.get('source')}")
    for b in report.get("bind_preview") or []:
        L(
            f"  bind token={b.get('token_masked')} → "
            f"{b.get('proxy_masked') or ('DIRECT' if b.get('direct') else '?')}"
        )
    if report.get("tried") is not None:
        L(
            f"orders: tried={report.get('tried')} alive={report.get('alive_proxy')} "
            f"auth_fail={report.get('auth_fail')} proxy_fail={report.get('proxy_fail')} "
            f"fetched={report.get('fetched_total')}"
        )
        for r in (report.get("results") or [])[:10]:
            L(
                f"  · {r.get('token_masked')} proxy={r.get('proxy_masked') or 'direct'} "
                f"probe={((r.get('probe') or {}).get('ok'))} "
                f"http={((r.get('probe') or {}).get('http'))} "
                f"orders={((r.get('orders') or {}).get('fetched'))}"
            )
    if report.get("next"):
        L("")
        L("Next:")
        for n in report["next"]:
            L(f"· {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    # strip raw tokens/urls with auth from report file
    slim = json.loads(json.dumps(report, ensure_ascii=False, default=str))
    for b in slim.get("bind_preview") or []:
        b.pop("token", None)
    for r in slim.get("results") or []:
        r.pop("token", None)
        if isinstance(r.get("proxy"), dict):
            r["proxy"].pop("url", None)
    (REPORTS / "token_proxy_bind.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORTS / "token_proxy_bind.txt").write_text(format_text(report) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rà soát proxy → gắn token → nginx gọi đơn")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Chỉ quét proxy trong repo/telegram")
    p_scan.add_argument("--no-telegram", action="store_true")

    p_bind = sub.add_parser("bind", help="Quét proxy + gắn vào mỗi token")
    p_bind.add_argument("--no-telegram", action="store_true")
    p_bind.add_argument("--mode", choices=["round_robin", "zip"], default="round_robin")
    p_bind.add_argument("--max-tokens", type=int, default=0)

    p_ord = sub.add_parser("nginx-orders", help="Nạp nginx → gọi đơn theo token+proxy")
    p_ord.add_argument("--days", type=int, default=3)
    p_ord.add_argument("--limit", type=int, default=20)
    p_ord.add_argument("--limit-tokens", type=int, default=10)
    p_ord.add_argument("--probe-only", action="store_true")
    p_ord.add_argument("--direct", action="store_true", help="Bỏ qua nginx")
    p_ord.add_argument("--keep", action="store_true")

    sub.add_parser("status", help="Xem bind hiện tại")

    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "scan":
        ensure_proxy_example()
        pull = {"skipped": True}
        if not args.no_telegram:
            pull = pull_telegram_proxy_docs()
        proxies, meta = scan_proxy_files()
        report = {
            "ok": bool(proxies),
            "checked_at": utc_now(),
            "proxy_n": len(proxies),
            "proxy_sources": meta,
            "proxy_preview": [
                {"url_masked": p["url_masked"], "scheme": p["scheme"], "source": p.get("source")}
                for p in proxies[:20]
            ],
            "telegram_pull": pull,
            "verdict": (
                f"✅ Tìm thấy {len(proxies)} proxy"
                if proxies
                else "❌ Không có egress proxy trong repo — điền secrets/proxies.owned.txt"
            ),
            "next": [
                "cp secrets/proxies.owned.example secrets/proxies.owned.txt  # rồi điền proxy",
                "python3 scripts/token_proxy_bind.py bind",
            ],
        }
        write_outputs(report)
    elif args.cmd == "bind":
        report = run_bind(
            pull_telegram=not args.no_telegram,
            mode=args.mode,
            max_tokens=args.max_tokens,
        )
    elif args.cmd == "nginx-orders":
        report = run_nginx_orders(
            days=args.days,
            limit=args.limit,
            limit_tokens=args.limit_tokens,
            probe_only=args.probe_only,
            via_nginx=not args.direct,
            keep=args.keep,
        )
    else:
        data = load_bind()
        binds = data.get("bindings") or []
        report = {
            "ok": bool(binds),
            "checked_at": utc_now(),
            "bind_file": str(BIND_PATH),
            "count": len(binds),
            "with_proxy": sum(1 for b in binds if b.get("proxy")),
            "updated_at": data.get("updated_at"),
            "bind_preview": [
                {
                    "token_masked": mask(b.get("token")),
                    "proxy_masked": (b.get("proxy") or {}).get("url_masked"),
                    "direct": b.get("direct"),
                }
                for b in binds[:15]
            ],
            "verdict": (
                f"✅ Bind file: {len(binds)} rows · with_proxy="
                f"{sum(1 for b in binds if b.get('proxy'))}"
                if binds
                else "⚠ Chưa bind — chạy: python3 scripts/token_proxy_bind.py bind"
            ),
        }
        write_outputs(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
