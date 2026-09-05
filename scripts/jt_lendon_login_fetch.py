#!/usr/bin/env python3
"""Login Lendon → lấy đơn ngay (Playwright CDP / form / cookie sync)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

CHROME_PROFILE = Path.home() / ".config/google-chrome"
CDP_PORT = int(os.environ.get("JT_LENDON_CDP_PORT", "9333"))
REPORTS = ROOT / "reports" / "telegram-classify"


def _chrome_running() -> bool:
    try:
        r = subprocess.run(["pgrep", "-f", "google-chrome/chrome"], capture_output=True, text=True)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _start_chrome_debug() -> None:
    if _chrome_running():
        return
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":1")
    subprocess.Popen(
        [
            "google-chrome",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={CHROME_PROFILE}",
            "--password-store=basic",
            "--no-first-run",
            "https://lendon.jtexpress.vn/order",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    for _ in range(30):
        time.sleep(1)
        try:
            import urllib.request

            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2)
            return
        except Exception:
            pass


def _fetch_via_cdp() -> dict:
    from playwright.sync_api import sync_playwright
    from jt_lendon_fetch import import_cookie_paste, load_lendon_env, run_fetch

    env = load_lendon_env()
    user = (env.get("JT_LENDON_USER") or env.get("JT_LENDON_LOGIN") or "").strip()
    password = (env.get("JT_LENDON_PASSWORD") or "").strip()

    _start_chrome_debug()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://lendon.jtexpress.vn/order", wait_until="domcontentloaded", timeout=60000)

        # Đã login?
        if "login" in (page.title() or "").lower() or page.locator("input[name='login']").count():
            if user and password:
                page.goto("https://lendon.jtexpress.vn/home-page", wait_until="domcontentloaded", timeout=60000)
                page.locator("#form-login input[name='login']").fill(user, timeout=8000)
                page.locator("#form-login input[name='password']").fill(password, timeout=8000)
                page.locator("#form-login button:has-text('Đăng nhập')").click(timeout=8000)
                page.wait_for_timeout(2500)
                page.goto("https://lendon.jtexpress.vn/order", wait_until="networkidle", timeout=60000)
            else:
                # Chờ user login trên Chrome (tối đa 90s)
                for _ in range(45):
                    page.goto("https://lendon.jtexpress.vn/order", wait_until="domcontentloaded", timeout=30000)
                    if "login" not in (page.title() or "").lower():
                        break
                    time.sleep(2)

        login = "login" in (page.title() or "").lower()
        cookies = context.cookies("https://lendon.jtexpress.vn")
        lendon = [c for c in cookies if c["name"] in ("october_session", "XSRF-TOKEN", "xsrf-token")]
        rep = {
            "ok": False,
            "login_page": login,
            "cookies": [c["name"] for c in lendon],
            "cdp_port": CDP_PORT,
        }
        if lendon and not login:
            payload = json.dumps(
                [{"name": c["name"], "value": c["value"], "domain": c.get("domain", ""), "path": c.get("path", "/")} for c in lendon]
            )
            imp = import_cookie_paste(payload, source="cdp_live")
            rep["import"] = {"ok": imp.get("ok"), "names": imp.get("names")}
            fetch = run_fetch(apply=True)
            rep.update(fetch)
            rep["ok"] = bool(fetch.get("ok"))
        browser.close()
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "jt_lendon_login_fetch.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return rep


def main() -> int:
    rep = _fetch_via_cdp()
    if rep.get("ok"):
        print(f"OK lendon orders={rep.get('orders_mapped')} ket_qua={rep.get('ket_qua_rows')}")
        return 0
    print(f"FAIL login_page={rep.get('login_page')} cookies={rep.get('cookies')} err={rep.get('error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
