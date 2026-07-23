#!/usr/bin/env python3
"""
Đấu nối ống dẫn (pipe) theo từng backend — duy trì phiên / cảnh báo trước khi logout.

Chỉ đọc credential từ secrets/ hoặc biến môi trường.
KHÔNG đọc dump Acc_all / Ghn / token list.
KHÔNG auto-login bằng mật khẩu dump.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
REPORTS = ROOT / "reports" / "telegram-classify"
STATE_FILE = SECRETS / "backend_pipes.state.json"
ENV_FILES = (
    SECRETS / "telegram.env",
    SECRETS / "backend_pipes.env",
    SECRETS / "pancake.env",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()}
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    body: bytes | None = None,
) -> tuple[int, Any]:
    hdrs = dict(headers or {})
    data = body
    if method.upper() == "POST" and data is None:
        data = b"{}"
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw[:200]
            return int(resp.status), parsed
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw[:200]
        return int(e.code), parsed
    except Exception as e:  # noqa: BLE001 — báo cáo lỗi mạng
        return 0, {"error": str(e)}


@dataclass
class PipeResult:
    backend: str
    channel: str
    status: str  # alive | missing_cred | auth_fail | error | skipped
    http: int | None
    detail: str
    session_risk: bool
    checked_at: str


def probe_telegram(env: dict[str, str]) -> PipeResult:
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        return PipeResult("Telegram", "bot-api", "missing_cred", None, "TELEGRAM_BOT_TOKEN trống", True, utc_now())
    code, body = http_json(f"https://api.telegram.org/bot{token}/getMe")
    ok = code == 200 and isinstance(body, dict) and body.get("ok")
    username = ""
    if ok and isinstance(body.get("result"), dict):
        username = body["result"].get("username") or ""
    return PipeResult(
        "Telegram",
        "bot-api",
        "alive" if ok else "auth_fail",
        code,
        f"@{username}" if ok else "getMe thất bại — bot có thể bị revoke",
        not ok,
        utc_now(),
    )


def probe_pancake(env: dict[str, str]) -> PipeResult:
    api_key = (
        env.get("PANCAKE_POS_API_KEY")
        or env.get("PANCAKE_API_KEY")
        or env.get("CENTRAL_API_KEY")
        or ""
    ).strip()
    bearer = (
        env.get("PANCAKE_POS_ACCESS_TOKEN")
        or env.get("PANCAKE_POS_TOKEN")
        or env.get("PANCAKE_TOKEN")
        or ""
    ).strip()
    shop = (env.get("PANCAKE_POS_SHOP_IDS") or env.get("PANCAKE_SHOP_IDS") or "1530618").split(",")[0].strip()
    base = (env.get("PANCAKE_POS_BASE_URL") or "https://pos.pancake.vn/api/v1").rstrip("/")
    if not api_key and not bearer:
        return PipeResult(
            "Pancake",
            "pos-orders",
            "missing_cred",
            None,
            "Thiếu PANCAKE_POS_API_KEY / Bearer trong secrets — ống dẫn chưa gắn credential",
            True,
            utc_now(),
        )
    url = f"{base}/shops/{shop}/orders?limit=1&page_number=1"
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        url += f"&api_key={urllib.parse.quote(api_key)}"
    elif bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    code, body = http_json(url, headers=headers)
    if code in (401, 403):
        return PipeResult(
            "Pancake",
            "pos-orders",
            "auth_fail",
            code,
            "Auth fail — phiên/key có nguy cơ logout/hết hạn",
            True,
            utc_now(),
        )
    if code == 0:
        return PipeResult("Pancake", "pos-orders", "error", 0, str(body)[:160], True, utc_now())
    # 200 hoặc lỗi nghiệp vụ vẫn coi ống còn sống nếu không phải auth
    alive = code < 500
    detail = f"shop={shop} http={code}"
    if isinstance(body, dict) and body.get("message"):
        detail += f" msg={str(body.get('message'))[:80]}"
    return PipeResult(
        "Pancake",
        "pos-orders",
        "alive" if alive else "error",
        code,
        detail,
        not alive,
        utc_now(),
    )


def probe_ghn(env: dict[str, str]) -> PipeResult:
    token = (env.get("GHN_API_TOKEN") or "").strip()
    if not token:
        return PipeResult(
            "GHN",
            "shiip-public-api",
            "missing_cred",
            None,
            "Thiếu GHN_API_TOKEN trong secrets — không probe SSO dump",
            True,
            utc_now(),
        )
    url = "https://dev-online-gateway.ghn.vn/shiip/public-api/master-data/province"
    code, body = http_json(url, headers={"Token": token, "Content-Type": "application/json"}, method="GET")
    # GHN often expects POST; try POST empty if GET fails oddly
    if code in (404, 405, 0):
        code, body = http_json(
            url,
            method="POST",
            headers={"Token": token, "Content-Type": "application/json"},
        )
    if code in (401, 403):
        return PipeResult("GHN", "shiip-public-api", "auth_fail", code, "Token GHN hết hạn / bị thu hồi", True, utc_now())
    ok = code == 200
    return PipeResult(
        "GHN",
        "shiip-public-api",
        "alive" if ok else ("error" if code else "error"),
        code,
        f"province probe http={code}",
        not ok,
        utc_now(),
    )


def probe_tpos(env: dict[str, str]) -> PipeResult:
    base = (env.get("TPOS_BASE_URL") or "").rstrip("/")
    token = (env.get("TPOS_ACCESS_TOKEN") or "").strip()
    if not base or not token:
        return PipeResult(
            "TPOS",
            "odata",
            "missing_cred",
            None,
            "Thiếu TPOS_BASE_URL + TPOS_ACCESS_TOKEN — ống dẫn chờ credential shop của bạn",
            True,
            utc_now(),
        )
    url = f"{base}/odata"
    code, _ = http_json(url, headers={"Authorization": f"Bearer {token}"})
    if code in (401, 403):
        return PipeResult("TPOS", "odata", "auth_fail", code, "Bearer TPOS fail — nguy cơ logout", True, utc_now())
    return PipeResult(
        "TPOS",
        "odata",
        "alive" if code and code < 500 else "error",
        code,
        f"base host ok http={code}",
        code in (0,) or (code or 0) >= 500,
        utc_now(),
    )


def probe_direct_api_local(_: dict[str, str]) -> PipeResult:
    """Ống dẫn OMS local — duy trì bằng hiện diện file snapshot trong inbox."""
    inbox = ROOT / "quarantine" / "telegram"
    snaps = list(inbox.glob("orders_detailed_*")) if inbox.is_dir() else []
    if not snaps:
        return PipeResult(
            "direct_api",
            "orders_snapshot",
            "missing_cred",
            None,
            "Chưa có orders_detailed_* trong quarantine/telegram",
            True,
            utc_now(),
        )
    newest = max(snaps, key=lambda p: p.stat().st_mtime)
    age_h = (time.time() - newest.stat().st_mtime) / 3600
    risk = age_h > 72
    return PipeResult(
        "direct_api",
        "orders_snapshot",
        "alive" if not risk else "error",
        None,
        f"file={newest.name} age_h={age_h:.1f}",
        risk,
        utc_now(),
    )


def probe_oms_pipe_registry(env: dict[str, str], results: list[PipeResult]) -> PipeResult:
    """Ống dẫn trung tâm: sống khi Telegram alive (kênh báo cáo) và registry ghi được."""
    tg = next((r for r in results if r.backend == "Telegram"), None)
    SECRETS.mkdir(parents=True, exist_ok=True)
    writable = os.access(SECRETS, os.W_OK)
    alive = bool(tg and tg.status == "alive" and writable)
    return PipeResult(
        "OMS-pipe-bus",
        "registry",
        "alive" if alive else "error",
        None,
        f"telegram={tg.status if tg else 'n/a'} state_writable={writable}",
        not alive,
        utc_now(),
    )


PROBES: list[tuple[str, Callable[[dict[str, str]], PipeResult]]] = [
    ("Telegram", probe_telegram),
    ("Pancake", probe_pancake),
    ("GHN", probe_ghn),
    ("TPOS", probe_tpos),
    ("direct_api", probe_direct_api_local),
]


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"pipes": {}}
    return {"pipes": {}}


def save_state(state: dict) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_state(state: dict, results: list[PipeResult]) -> dict:
    pipes = state.setdefault("pipes", {})
    for r in results:
        prev = pipes.get(r.backend) or {}
        streak = int(prev.get("alive_streak") or 0)
        if r.status == "alive":
            streak += 1
        else:
            streak = 0
        pipes[r.backend] = {
            **asdict(r),
            "alive_streak": streak,
            "last_alive_at": r.checked_at if r.status == "alive" else prev.get("last_alive_at"),
            "logout_guard": "active" if r.status == "alive" else "alert",
        }
    return state


def format_report(results: list[PipeResult], state: dict) -> str:
    lines = [
        "🔌 PIPE BACKEND — đấu nối & chống logout",
        f"Lúc: {utc_now()}",
        "Policy: chỉ secrets/ · không dump · không auto-login mật khẩu",
        "",
    ]
    for r in results:
        st = state.get("pipes", {}).get(r.backend) or {}
        flag = "✅" if r.status == "alive" else ("⚠️" if r.status == "missing_cred" else "❌")
        lines.append(
            f"{flag} {r.backend}/{r.channel}: {r.status}"
            f" · streak={st.get('alive_streak', 0)}"
            f" · guard={st.get('logout_guard', '?')}"
        )
        lines.append(f"   {r.detail}")
    risks = [r for r in results if r.session_risk]
    lines.append("")
    if risks:
        lines.append("Cảnh báo logout/session:")
        for r in risks:
            lines.append(f"· {r.backend}: {r.detail}")
    else:
        lines.append("Không có cảnh báo logout — mọi ống đã gắn đều alive.")
    return "\n".join(lines)


def send_telegram(env: dict[str, str], text: str) -> None:
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return
    payload = json.dumps(
        {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def run_once(env: dict[str, str], notify: bool) -> dict:
    results = [fn(env) for _, fn in PROBES]
    results.append(probe_oms_pipe_registry(env, results))
    state = merge_state(load_state(), results)
    save_state(state)
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = {
        "ok": True,
        "checked_at": utc_now(),
        "pipes": [asdict(r) for r in results],
        "state": state,
        "policy": "secrets-only; no credential dumps; no password auto-login",
    }
    (REPORTS / "backend_pipe_keepalive.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    text = format_report(results, state)
    (REPORTS / "backend_pipe_keepalive.txt").write_text(text, encoding="utf-8")
    if notify:
        send_telegram(env, text)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Backend pipe keepalive (anti-logout guard)")
    ap.add_argument("--once", action="store_true", help="Một vòng rồi thoát")
    ap.add_argument("--loop", action="store_true", help="Lặp heartbeat")
    ap.add_argument("--interval", type=int, default=300, help="Giây giữa các vòng (loop)")
    ap.add_argument("--notify", action="store_true", help="Gửi báo cáo Telegram")
    ap.add_argument("--notify-on-risk", action="store_true", help="Chỉ Telegram khi có session_risk")
    args = ap.parse_args()
    env = load_env()

    def tick(force_notify: bool = False) -> dict:
        # dry run collect then decide notify
        results_preview = [fn(env) for _, fn in PROBES]
        risk = any(r.session_risk for r in results_preview)
        notify = args.notify or force_notify or (args.notify_on_risk and risk)
        return run_once(env, notify=notify)

    if args.loop:
        while True:
            rep = tick()
            print(json.dumps({"ok": True, "pipes": len(rep["pipes"]), "at": rep["checked_at"]}, ensure_ascii=False))
            time.sleep(max(30, args.interval))
    else:
        rep = tick(force_notify=args.notify)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
