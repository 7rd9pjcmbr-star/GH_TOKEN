#!/usr/bin/env python3
"""Monitoring + alerting — giám sát hệ thống & cảnh báo (owned-only).

Kiểm tra định kỳ các thành phần và cảnh báo khi có sự cố:
  • web tier   : GET /healthz (async aiohttp)
  • platforms  : backend_pipe_keepalive.run_once (probe token/pipe, session_risk)
  • sessions   : session_store.status_report   (nếu có — soft import)
  • pool       : account_pool.status_report    (nếu có — soft import)

Phân loại severity (ok < warn < critical), **dedup + cooldown** để không spam.
Cảnh báo qua Telegram (bot owned) bằng backend_pipe_keepalive.send_telegram —
mặc định **dry-run** (không gửi); chỉ gửi khi `--send` và có TELEGRAM_BOT_TOKEN/CHAT_ID.

Chính sách: owned-only · no dump-login · no auto-login · report mask-only.
State: secrets/monitor_alert.state.json (gitignored, chmod 600).
Override: MONITOR_STATE_PATH (test).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SECRETS = ROOT / "secrets"
DEFAULT_STATE = SECRETS / "monitor_alert.state.json"
DEFAULT_COOLDOWN_S = 900
SEV_ORDER = {"ok": 0, "warn": 1, "critical": 2}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def state_path() -> Path:
    override = os.environ.get("MONITOR_STATE_PATH")
    return Path(override) if override else DEFAULT_STATE


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.is_file():
        return {"version": 1, "updated_at": utc_now(), "components": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "components" in d:
            return d
    except Exception:  # noqa: BLE001
        pass
    return {"version": 1, "updated_at": utc_now(), "components": {}}


def save_state(state: dict[str, Any]) -> Path:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


# --------------------------------------------------------------------------- checks


async def check_web(urls: list[str], *, timeout: float = 8.0) -> list[dict[str, Any]]:
    if not urls:
        return []
    import aiohttp

    async with aiohttp.ClientSession() as session:

        async def _one(u: str) -> dict[str, Any]:
            comp = f"web:{u}"
            try:
                async with session.get(u, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                    ok = r.status == 200
                    return {
                        "component": comp,
                        "status": "ok" if ok else "critical",
                        "detail": f"http={r.status}",
                    }
            except Exception as e:  # noqa: BLE001
                return {"component": comp, "status": "critical", "detail": f"unreachable: {str(e)[:100]}"}

        return await asyncio.gather(*(_one(u) for u in urls))


def check_platforms(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    try:
        from backend_pipe_keepalive import load_env, run_once
    except Exception as e:  # noqa: BLE001
        return [{"component": "platforms", "status": "warn", "detail": f"probe unavailable: {e}"}]
    env = env or load_env()
    report = run_once(env, notify=False)  # notify handled centrally here
    out: list[dict[str, Any]] = []
    for pipe in report.get("pipes", []):
        name = pipe.get("backend") or pipe.get("name") or "pipe"
        status = pipe.get("status")
        if pipe.get("session_risk"):
            sev = "critical"
        elif status in ("alive", "connected", "ok"):
            sev = "ok"
        elif status in ("missing_cred",):
            sev = "warn"
        else:
            sev = "warn"
        out.append({"component": f"platform:{name}", "status": sev, "detail": str(pipe.get("detail") or status)[:160]})
    return out


def check_sessions() -> list[dict[str, Any]]:
    try:
        import session_store
    except Exception:  # noqa: BLE001
        return []  # module not present on this branch — skip
    rep = session_store.status_report()
    overall = rep.get("overall", "ok")
    sev = {"ok": "ok", "session": "ok", "unknown": "warn", "expiring": "warn", "expired": "critical"}.get(overall, "warn")
    return [{"component": "sessions", "status": sev, "detail": f"overall={overall} platforms={rep.get('platform_count')}"}]


def check_pool(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    try:
        import account_pool
    except Exception:  # noqa: BLE001
        return []  # module not present — skip
    rep = account_pool.status_report(env)
    t = rep.get("totals", {})
    total, eligible = int(t.get("total") or 0), int(t.get("eligible") or 0)
    if total == 0:
        sev = "warn"
    elif eligible == 0:
        sev = "critical"
    elif int(t.get("cooldown") or 0) > 0:
        sev = "warn"
    else:
        sev = "ok"
    return [{"component": "account_pool", "status": sev, "detail": f"total={total} eligible={eligible} cooldown={t.get('cooldown')}"}]


async def collect(*, env: dict[str, str] | None = None, web_urls: list[str] | None = None) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    components += await check_web(web_urls or [])
    components += check_platforms(env)
    components += check_sessions()
    components += check_pool(env)
    return components


# --------------------------------------------------------------------------- evaluate (dedup + cooldown)


def evaluate(
    components: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    cooldown_s: int = DEFAULT_COOLDOWN_S,
    now: int | None = None,
) -> dict[str, Any]:
    """Decide which components warrant an alert (state change worse, or ongoing bad past cooldown)."""
    now = now if now is not None else _now_epoch()
    comp_state = state.setdefault("components", {})
    alerts: list[dict[str, Any]] = []
    worst = "ok"
    for c in components:
        name, st = c["component"], c["status"]
        prev = comp_state.get(name, {"status": "ok", "last_alert": 0})
        prev_st = prev.get("status", "ok")
        worsened = SEV_ORDER.get(st, 0) > SEV_ORDER.get(prev_st, 0)
        due = st in ("warn", "critical") and (now - int(prev.get("last_alert") or 0) >= cooldown_s)
        if (worsened and st != "ok") or due:
            alerts.append({**c, "kind": "worsened" if worsened else "ongoing"})
            prev["last_alert"] = now
        elif st == "ok" and SEV_ORDER.get(prev_st, 0) > 0:
            alerts.append({**c, "kind": "recovered", "detail": f"recovered ({c.get('detail')})"})
        prev["status"] = st
        comp_state[name] = prev
        if SEV_ORDER.get(st, 0) > SEV_ORDER.get(worst, 0):
            worst = st
    return {"overall": worst, "alerts": alerts}


def format_alerts(overall: str, alerts: list[dict[str, Any]]) -> str:
    icon = {"ok": "✅", "warn": "⚠️", "critical": "🚨"}
    lines = [f"{icon.get(overall, 'ℹ️')} MONITOR · overall={overall} · {utc_now()}"]
    for a in alerts:
        lines.append(f"  [{a.get('kind')}] {a['component']} → {a['status']}: {a.get('detail')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- run


async def run_once_async(
    *,
    env: dict[str, str] | None = None,
    web_urls: list[str] | None = None,
    send: bool = False,
    cooldown_s: int = DEFAULT_COOLDOWN_S,
) -> dict[str, Any]:
    components = await collect(env=env, web_urls=web_urls)
    state = load_state()
    verdict = evaluate(components, state, cooldown_s=cooldown_s)
    save_state(state)

    sent = False
    would_send = bool(verdict["alerts"])
    if send and verdict["alerts"]:
        try:
            from backend_pipe_keepalive import load_env, send_telegram

            e = env or load_env()
            if e.get("TELEGRAM_BOT_TOKEN") and e.get("TELEGRAM_CHAT_ID"):
                send_telegram(e, format_alerts(verdict["overall"], verdict["alerts"]))
                sent = True
        except Exception:  # noqa: BLE001
            sent = False
    return {
        "ok": True,
        "module": "monitor_alert",
        "checked_at": utc_now(),
        "overall": verdict["overall"],
        "components": components,
        "alerts": verdict["alerts"],
        "alert_sent": sent,
        "alert_pending": would_send and not sent,
        "policy": "owned-only · dry-run unless --send + Telegram creds · mask-only",
    }


_STOP = {"flag": False}


async def run_monitor(
    *,
    interval: int = 300,
    iterations: int | None = None,
    web_urls: list[str] | None = None,
    send: bool = False,
    cooldown_s: int = DEFAULT_COOLDOWN_S,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    import signal

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: _STOP.__setitem__("flag", True))
        except (NotImplementedError, RuntimeError):
            pass
    n = 0
    last: dict[str, Any] = {}
    while not _STOP["flag"]:
        last = await run_once_async(env=env, web_urls=web_urls, send=send, cooldown_s=cooldown_s)
        n += 1
        print(
            f"[{utc_now()}] monitor #{n} overall={last['overall']} "
            f"alerts={len(last['alerts'])} sent={last['alert_sent']}",
            flush=True,
        )
        if iterations is not None and n >= iterations:
            break
        slept = 0
        while slept < int(interval) and not _STOP["flag"]:
            await asyncio.sleep(1)
            slept += 1
    return {"ok": True, "iterations": n, "last": last}


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Monitoring + alerting (owned-only, dry-run by default)")
    ap.add_argument("command", nargs="?", default="once", choices=["once", "loop", "status"])
    ap.add_argument("--web-url", action="append", help="URL /healthz để probe (lặp lại được)")
    ap.add_argument("--send", action="store_true", help="Gửi Telegram alert (mặc định dry-run)")
    ap.add_argument("--interval", type=int, default=300, help="Chu kỳ loop (giây)")
    ap.add_argument("--iterations", type=int, help="Số vòng loop (test)")
    ap.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_S, help="Cooldown giữa các alert lặp (giây)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "status":
        state = load_state()
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0

    if args.command == "loop":
        res = asyncio.run(
            run_monitor(
                interval=args.interval,
                iterations=args.iterations,
                web_urls=args.web_url or None,
                send=args.send,
                cooldown_s=args.cooldown,
            )
        )
        return 0 if res.get("ok") else 1

    # once
    rep = asyncio.run(
        run_once_async(web_urls=args.web_url or None, send=args.send, cooldown_s=args.cooldown)
    )
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(format_alerts(rep["overall"], rep["alerts"]) if rep["alerts"] else f"✅ overall={rep['overall']} (no new alerts)")
        print(f"(alert_sent={rep['alert_sent']} alert_pending={rep['alert_pending']} · {rep['policy']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
