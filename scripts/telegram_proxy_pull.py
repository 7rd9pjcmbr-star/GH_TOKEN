#!/usr/bin/env python3
"""Kéo file/proxy từ hộp thoại Telegram → probe live → secrets/proxies.live.txt.

Tải document tên proxy/socks* vào quarantine/telegram/_proxies/
Probe đường tới GHN (HTTP 401/200/403 = proxy sống) → lưu live.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
INBOX = ROOT / "quarantine" / "telegram"
PROXY_DIR = INBOX / "_proxies"
REPORTS = ROOT / "reports" / "telegram-classify"
OFFSET_PATH = SECRETS / "telegram_proxy_pull.offset"

NAME_RE = re.compile(
    r"(?i)(proxy|proxies|socks5?|socks4|proxy[_-]?list|proxy[_-]?pool|ip[_-]?list)"
)
LINE_OK = re.compile(
    r"(?i)^\s*(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}(?::\S+){0,2}\s*$|"
    r"^\s*[^:\s]+:[^@\s]+@[^:\s]+:\d{2,5}\s*$|"
    r"^\s*(?:https?|socks5?|socks4)://\S+\s*$"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
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
    return env


def api(token: str, method: str, payload: dict | None = None, timeout: int = 45) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def pull(token: str, *, lookback: int = 5000) -> dict[str, Any]:
    off = 0
    for op in (
        OFFSET_PATH,
        SECRETS / "telegram.offset",
        SECRETS / "telegram_inbox.offset",
    ):
        if op.is_file():
            try:
                off = max(off, int(op.read_text().strip() or "0"))
            except ValueError:
                pass
    start = max(0, off - max(0, lookback))
    data = api(
        token,
        "getUpdates",
        {
            "offset": start,
            "timeout": 0,
            "allowed_updates": ["message", "channel_post", "edited_message"],
        },
    )
    if not data.get("ok"):
        return {"ok": False, "error": str(data)[:200], "downloaded": []}

    PROXY_DIR.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict] = []
    hits: list[dict] = []
    text_lines = 0
    max_off = start
    for upd in data.get("result") or []:
        max_off = max(max_off, int(upd["update_id"]) + 1)
        msg = upd.get("message") or upd.get("channel_post") or upd.get("edited_message") or {}
        text = msg.get("text") or msg.get("caption") or ""
        doc = msg.get("document") or {}
        name = doc.get("file_name") or ""
        if not NAME_RE.search(name) and not NAME_RE.search(text[:800]):
            continue
        hits.append(
            {
                "message_id": msg.get("message_id"),
                "document_name": name or None,
                "text_preview": text[:120],
            }
        )
        if doc.get("file_id"):
            try:
                meta = api(token, "getFile", {"file_id": doc["file_id"]})
                fpath = meta["result"]["file_path"]
                safe = re.sub(r"[^\w.\-+]", "_", name)[:180] or f"proxy_{msg.get('message_id')}.txt"
                day = datetime.now(timezone.utc).strftime("%Y%m%d")
                dest = PROXY_DIR / (safe if safe.startswith(day) else f"{day}_{safe}")
                with urllib.request.urlopen(
                    f"https://api.telegram.org/file/bot{token}/{fpath}", timeout=120
                ) as r:
                    dest.write_bytes(r.read())
                downloaded.append(
                    {"file": dest.name, "size": dest.stat().st_size, "orig": name}
                )
            except Exception as e:  # noqa: BLE001
                hits[-1]["download_error"] = str(e)[:100]
        for ln in text.splitlines():
            if LINE_OK.match(ln.strip()):
                text_lines += 1
                with (SECRETS / "proxies.telegram.txt").open("a", encoding="utf-8") as f:
                    if text_lines == 1:
                        f.write(f"\n# telegram text @ {utc_now()}\n")
                    f.write(ln.strip() + "\n")

    SECRETS.mkdir(parents=True, exist_ok=True)
    OFFSET_PATH.write_text(str(max_off), encoding="utf-8")
    return {
        "ok": True,
        "updates_n": len(data.get("result") or []),
        "hits_n": len(hits),
        "downloaded": downloaded,
        "text_proxy_lines": text_lines,
        "hits": hits[:40],
    }


def normalize(line: str, default_scheme: str | None) -> str | None:
    ln = line.strip()
    if not ln or ln.startswith("#"):
        return None
    if re.match(r"(?i)^(https?|socks5|socks4)://", ln):
        return ln
    m = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})(?::([^:]+):(\S+))?$", ln)
    if not m:
        return None
    host, port, user, pw = m.group(1), m.group(2), m.group(3), m.group(4)
    scheme = default_scheme or "http"
    if user and pw:
        return f"{scheme}://{user}:{pw}@{host}:{port}"
    return f"{scheme}://{host}:{port}"


def collect_from_files() -> list[tuple[str, str, str]]:
    cands: list[tuple[str, str, str]] = []
    if not PROXY_DIR.is_dir():
        return cands
    for p in sorted(PROXY_DIR.glob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        default = "socks5" if "socks5" in name else ("socks4" if "socks4" in name else "http")
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            # scheme from line prefix wins
            sch = default
            if ln.lower().startswith("socks5"):
                sch = "socks5"
            elif ln.lower().startswith("socks4"):
                sch = "socks4"
            elif ln.lower().startswith("http"):
                sch = "http"
            u = normalize(ln, sch)
            if u:
                cands.append((u, p.name, sch))
    # dedupe
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for u, s, sch in cands:
        if u in seen:
            continue
        seen.add(u)
        out.append((u, s, sch))
    return out


def probe_live(
    cands: list[tuple[str, str, str]],
    *,
    stop_after: int = 25,
    timeout: int = 8,
    workers: int = 32,
) -> list[dict[str, Any]]:
    import requests

    ghn = "https://online-gateway.ghn.vn/shiip/public-api/master-data/province"
    headers = {
        "Token": "00000000-0000-0000-0000-000000000000",
        "Content-Type": "application/json",
    }

    def one(url: str) -> dict[str, Any]:
        t0 = time.time()
        try:
            r = requests.get(
                ghn,
                headers=headers,
                proxies={"http": url, "https": url},
                timeout=timeout,
            )
            return {
                "ok": r.status_code in (200, 401, 403),
                "http": r.status_code,
                "ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "http": 0, "ms": int((time.time() - t0) * 1000), "error": str(e)[:80]}

    # socks5 first
    ordered = sorted(cands, key=lambda x: 0 if x[2] == "socks5" else (1 if x[2] == "http" else 2))
    live: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, u): (u, src, sch) for u, src, sch in ordered}
        for fut in as_completed(futs):
            u, src, sch = futs[fut]
            pr = fut.result()
            if pr.get("ok"):
                live.append({"url": u, "source": src, "scheme": sch, **pr})
                if len(live) >= stop_after:
                    break
    return live


def save_live(live: list[dict[str, Any]]) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    lines = [f"# telegram proxies live @ {utc_now()}", f"# n={len(live)}"]
    for x in live:
        lines.append(x["url"])
    text = "\n".join(lines) + "\n"
    for path in (
        SECRETS / "proxies.live.txt",
        SECRETS / "proxies.owned.txt",
        SECRETS / "proxies.telegram.txt",
    ):
        path.write_text(text, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def run(*, lookback: int = 5000, stop_after: int = 25, bind: bool = True) -> dict[str, Any]:
    env = load_env()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    report: dict[str, Any] = {
        "ok": False,
        "module": "telegram_proxy_pull",
        "checked_at": utc_now(),
        "pull": None,
        "candidates_n": 0,
        "live_n": 0,
        "live_preview": [],
        "bind": None,
        "verdict": "",
    }
    if not token:
        report["verdict"] = "❌ Thiếu TELEGRAM_BOT_TOKEN"
        return report

    pull_r = pull(token, lookback=lookback)
    report["pull"] = {
        "ok": pull_r.get("ok"),
        "updates_n": pull_r.get("updates_n"),
        "hits_n": pull_r.get("hits_n"),
        "downloaded": pull_r.get("downloaded"),
        "text_proxy_lines": pull_r.get("text_proxy_lines"),
    }
    cands = collect_from_files()
    report["candidates_n"] = len(cands)
    live = probe_live(cands, stop_after=stop_after) if cands else []
    report["live_n"] = len(live)
    report["live_preview"] = [
        {"scheme": x["scheme"], "url_masked": re.sub(r"://[^@]+@", "://***:***@", x["url"]), "http": x.get("http"), "ms": x.get("ms"), "source": x.get("source")}
        for x in live[:15]
    ]
    if live:
        save_live(live)
        report["ok"] = True
        report["verdict"] = (
            f"✅ Telegram proxy · downloaded={len(pull_r.get('downloaded') or [])} · "
            f"live={len(live)}/{len(cands)} → secrets/proxies.live.txt"
        )
        if bind:
            from token_proxy_bind import run_bind

            br = run_bind(pull_telegram=False, mode="round_robin", max_tokens=len(live))
            report["bind"] = {
                "ok": br.get("ok"),
                "bound_n": br.get("bound_n"),
                "proxy_n": br.get("proxy_n"),
                "verdict": br.get("verdict"),
            }
        report["next"] = [
            "python3 scripts/token_proxy_bind.py nginx-orders --limit-tokens 10",
            "python3 scripts/nginx_order_embed.py ghn-token-proxy-orders --keep",
        ]
    else:
        report["verdict"] = (
            f"⚠ Telegram: files={len(pull_r.get('downloaded') or [])} · "
            f"candidates={len(cands)} · live=0 tới GHN"
        )
        report["next"] = ["Gửi thêm file proxy socks5/http vào chat bot"]

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "telegram_proxy_pull.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (REPORTS / "telegram_proxy_pull.txt").write_text(format_text(report) + "\n", encoding="utf-8")
    return report


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("📡 TELEGRAM → LẤY PROXY LIVE")
    L(f"Lúc: {report.get('checked_at') or utc_now()}")
    L(report.get("verdict") or "")
    pull = report.get("pull") or {}
    L(
        f"Pull: updates={pull.get('updates_n')} hits={pull.get('hits_n')} "
        f"downloaded={len(pull.get('downloaded') or [])}"
    )
    for d in pull.get("downloaded") or []:
        L(f"  · {d.get('orig')} → {d.get('file')} ({d.get('size')} B)")
    L(f"candidates={report.get('candidates_n')} live={report.get('live_n')}")
    for x in report.get("live_preview") or []:
        L(f"  ✅ {x.get('scheme')} {x.get('url_masked')} http={x.get('http')} {x.get('ms')}ms")
    if report.get("bind"):
        L(f"Bind: {report['bind'].get('verdict')}")
    if report.get("next"):
        L("Next:")
        for n in report["next"]:
            L(f"· {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Telegram kéo proxy → probe live")
    ap.add_argument("--lookback", type=int, default=5000)
    ap.add_argument("--stop-after", type=int, default=25)
    ap.add_argument("--no-bind", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = run(lookback=args.lookback, stop_after=args.stop_after, bind=not args.no_bind)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
