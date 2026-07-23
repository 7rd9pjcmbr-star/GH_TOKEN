#!/usr/bin/env python3
"""Duy trì token / phiên — cảnh báo trước hết hạn, heartbeat, refresh khi được.

- Pancake JWT: đọc exp · heartbeat /shops · bắt pos_jwt mới từ Set-Cookie nếu server gia hạn
- ViettelPost: auto-refresh USER+PASSWORD owned khi auth_fail / sắp hết
- GHN: probe Token (không tự tạo — cần owned token)
- Loop: --loop --interval 1800 --warn-days 7 --notify-on-risk

Owned-only · no dump-login · mask trong report.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
REPORTS = ROOT / "reports" / "telegram-classify"
STATE_PATH = SECRETS / "token_session_maintain.state.json"
SESSION_ENV = SECRETS / "order_session.env"

DEFAULT_WARN_DAYS = 7
DEFAULT_CRITICAL_DAYS = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mask(tok: str | None) -> str | None:
    from owned_credentials import mask_secret

    return mask_secret(tok)


def decode_jwt(token: str) -> dict[str, Any] | None:
    parts = (token or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        pad = "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode("utf-8"))
    except Exception:
        return None


def jwt_ttl(token: str) -> dict[str, Any]:
    pl = decode_jwt(token)
    if not pl:
        return {"has_exp": False, "expired": None, "days_left": None}
    exp = pl.get("exp")
    if not isinstance(exp, (int, float)):
        return {
            "has_exp": False,
            "expired": None,
            "days_left": None,
            "name": pl.get("name") or pl.get("fb_name"),
            "uid": pl.get("uid"),
        }
    now = int(datetime.now(timezone.utc).timestamp())
    left = int(exp) - now
    return {
        "has_exp": True,
        "exp": int(exp),
        "exp_iso": datetime.fromtimestamp(int(exp), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expired": left <= 0,
        "seconds_left": left,
        "days_left": round(left / 86400, 2),
        "name": pl.get("name") or pl.get("fb_name"),
        "uid": pl.get("uid"),
        "session_id": pl.get("session_id"),
    }


def load_env() -> dict[str, str]:
    from owned_credentials import load_env as base_load

    return base_load(extra_files=(SECRETS / "mapper_icon_aes.env", SESSION_ENV))


def _risk_level(days_left: float | None, *, warn: float, critical: float) -> str:
    if days_left is None:
        return "unknown"
    if days_left <= 0:
        return "expired"
    if days_left <= critical:
        return "critical"
    if days_left <= warn:
        return "warn"
    return "ok"


def pancake_heartbeat(env: dict[str, str]) -> dict[str, Any]:
    """Probe /shops bằng api_key + bearer; bắt Set-Cookie pos_jwt nếu server gia hạn."""
    import requests
    from access_token_rotate import upsert_env_values

    api_key = (env.get("PANCAKE_POS_API_KEY") or env.get("PANCAKE_API_KEY") or "").strip()
    primary = (env.get("PANCAKE_POS_ACCESS_TOKEN") or "").strip()
    secondary = (env.get("PANCAKE_POS_SECONDARY_ACCESS_TOKEN") or "").strip()

    out: dict[str, Any] = {
        "api_key": {"present": bool(api_key), "alive": None},
        "primary": {"present": bool(primary), "alive": None, "renewed": False},
        "secondary": {"present": bool(secondary), "alive": None, "renewed": False},
        "renewals": [],
    }

    def touch(label: str, token: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Cookie": f"pos_jwt={token}; pos_locale=vi",
        }
        try:
            r = requests.get("https://pos.pancake.vn/api/v1/shops", headers=headers, timeout=25)
        except Exception as e:  # noqa: BLE001
            return {"alive": False, "http": 0, "error": str(e)[:140], "renewed": False}
        renewed = False
        new_tok = None
        raw_sc = r.headers.get("Set-Cookie") or ""
        for c in r.cookies:
            if c.name == "pos_jwt" and c.value and c.value.count(".") == 2 and c.value != token:
                new_tok = c.value
                break
        if not new_tok and "pos_jwt=" in raw_sc:
            m = re.search(r"pos_jwt=([^;,\s]+)", raw_sc)
            if m and m.group(1).count(".") == 2 and m.group(1) != token:
                new_tok = m.group(1)
        if new_tok:
            ttl_old = jwt_ttl(token)
            ttl_new = jwt_ttl(new_tok)
            # only accept if longer-lived or not expired
            if ttl_new.get("expired") is False and (
                (ttl_new.get("seconds_left") or 0) > (ttl_old.get("seconds_left") or 0)
            ):
                key = (
                    "PANCAKE_POS_ACCESS_TOKEN"
                    if label == "primary"
                    else "PANCAKE_POS_SECONDARY_ACCESS_TOKEN"
                )
                upsert_env_values({key: new_tok})
                renewed = True
                out["renewals"].append(
                    {
                        "label": label,
                        "old_exp": ttl_old.get("exp_iso"),
                        "new_exp": ttl_new.get("exp_iso"),
                        "masked": _mask(new_tok),
                    }
                )
        shops = None
        try:
            data = r.json() if r.text else {}
            shops = data.get("shops") if isinstance(data, dict) else None
        except Exception:
            data = {}
        alive = r.status_code == 200 and isinstance(shops, list)
        return {
            "alive": alive,
            "http": r.status_code,
            "shops_n": len(shops) if isinstance(shops, list) else None,
            "renewed": renewed,
            "ttl": jwt_ttl(new_tok or token),
            "message": None if alive else (data.get("message") if isinstance(data, dict) else r.text[:80]),
        }

    if api_key:
        try:
            r = requests.get(
                "https://pos.pancake.vn/api/v1/shops",
                params={"api_key": api_key},
                headers={"Accept": "application/json"},
                timeout=25,
            )
            data = r.json() if r.text else {}
            shops = data.get("shops") if isinstance(data, dict) else None
            out["api_key"]["alive"] = r.status_code == 200 and isinstance(shops, list)
            out["api_key"]["http"] = r.status_code
            out["api_key"]["shops_n"] = len(shops) if isinstance(shops, list) else None
            out["api_key"]["note"] = "api_key không theo JWT exp — ưu tiên lấy đơn"
        except Exception as e:  # noqa: BLE001
            out["api_key"]["alive"] = False
            out["api_key"]["error"] = str(e)[:140]

    if primary:
        out["primary"].update(touch("primary", primary))
    if secondary:
        out["secondary"].update(touch("secondary", secondary))

    if out["renewals"]:
        try:
            from order_session_env import export_session_env

            export_session_env()
        except Exception as e:  # noqa: BLE001
            out["session_export_error"] = str(e)[:120]

    return out


def maintain_once(
    *,
    warn_days: float = DEFAULT_WARN_DAYS,
    critical_days: float = DEFAULT_CRITICAL_DAYS,
    auto_refresh_vtp: bool = True,
    notify_on_risk: bool = False,
) -> dict[str, Any]:
    from access_token_rotate import ensure_tokens

    env = load_env()
    primary = (env.get("PANCAKE_POS_ACCESS_TOKEN") or "").strip()
    secondary = (env.get("PANCAKE_POS_SECONDARY_ACCESS_TOKEN") or "").strip()

    ttl_primary = jwt_ttl(primary) if primary else {"has_exp": False, "days_left": None}
    ttl_secondary = jwt_ttl(secondary) if secondary else {"has_exp": False, "days_left": None}

    heartbeat = pancake_heartbeat(env)
    # reload if renewed
    if heartbeat.get("renewals"):
        env = load_env()
        primary = (env.get("PANCAKE_POS_ACCESS_TOKEN") or "").strip()
        secondary = (env.get("PANCAKE_POS_SECONDARY_ACCESS_TOKEN") or "").strip()
        ttl_primary = jwt_ttl(primary) if primary else ttl_primary
        ttl_secondary = jwt_ttl(secondary) if secondary else ttl_secondary

    ensure = ensure_tokens(auto_refresh_vtp=auto_refresh_vtp)

    risks: list[dict[str, Any]] = []
    for label, ttl, alive in (
        ("pancake_primary", ttl_primary, (heartbeat.get("primary") or {}).get("alive")),
        ("pancake_secondary", ttl_secondary, (heartbeat.get("secondary") or {}).get("alive")),
    ):
        level = _risk_level(ttl.get("days_left"), warn=warn_days, critical=critical_days)
        if alive is False:
            level = "auth_fail"
        if level in {"warn", "critical", "expired", "auth_fail"}:
            risks.append(
                {
                    "key": label,
                    "level": level,
                    "days_left": ttl.get("days_left"),
                    "exp_iso": ttl.get("exp_iso"),
                    "name": ttl.get("name"),
                    "need": "Gửi pos_jwt còn hạn → pancake_cookie_ingest"
                    if level in {"warn", "critical", "expired"}
                    else "Kiểm tra revoke / đăng nhập lại",
                }
            )

    if not (env.get("GHN_API_TOKEN") or "").strip():
        risks.append({"key": "ghn", "level": "missing", "need": "GHN_API_TOKEN owned"})
    if not (env.get("VIETTELPOST_TOKEN") or "").strip() and not (
        (env.get("VIETTELPOST_USER") or "").strip() and (env.get("VIETTELPOST_PASSWORD") or "").strip()
    ):
        risks.append({"key": "viettelpost", "level": "missing", "need": "VIETTELPOST_TOKEN hoặc USER+PASSWORD"})

    # Prefer api_key path for order continuity
    api_alive = (heartbeat.get("api_key") or {}).get("alive")
    primary_alive = (heartbeat.get("primary") or {}).get("alive")
    order_ready = bool(api_alive or primary_alive)

    report: dict[str, Any] = {
        "ok": order_ready and not any(r["level"] in {"expired", "auth_fail", "critical"} for r in risks if r["key"].startswith("pancake")),
        "module": "token_session_maintain",
        "checked_at": utc_now(),
        "ttl": {
            "primary": {
                "name": ttl_primary.get("name"),
                "exp_iso": ttl_primary.get("exp_iso"),
                "days_left": ttl_primary.get("days_left"),
                "level": _risk_level(ttl_primary.get("days_left"), warn=warn_days, critical=critical_days),
                "alive": primary_alive,
            },
            "secondary": {
                "name": ttl_secondary.get("name"),
                "exp_iso": ttl_secondary.get("exp_iso"),
                "days_left": ttl_secondary.get("days_left"),
                "level": _risk_level(ttl_secondary.get("days_left"), warn=warn_days, critical=critical_days),
                "alive": (heartbeat.get("secondary") or {}).get("alive"),
            },
            "warn_days": warn_days,
            "critical_days": critical_days,
        },
        "heartbeat": {
            "api_key_alive": api_alive,
            "primary_renewed": (heartbeat.get("primary") or {}).get("renewed"),
            "secondary_renewed": (heartbeat.get("secondary") or {}).get("renewed"),
            "renewals": heartbeat.get("renewals") or [],
            "api_key_shops": (heartbeat.get("api_key") or {}).get("shops_n"),
            "primary_shops": (heartbeat.get("primary") or {}).get("shops_n"),
        },
        "ensure": {
            "ok": ensure.get("ok"),
            "ready": ensure.get("ready_platforms"),
            "refreshed": ensure.get("refreshed"),
            "verdict": ensure.get("verdict"),
        },
        "order_ready": order_ready,
        "risks": risks,
        "policy": {"owned_only": True, "no_dump_login": True},
    }

    if order_ready and not risks:
        report["verdict"] = (
            f"✅ Phiên ổn · primary={ttl_primary.get('days_left')}d · "
            f"secondary={ttl_secondary.get('days_left')}d · api_key={'OK' if api_alive else '—'}"
        )
    elif order_ready:
        report["verdict"] = (
            f"⚠ Duy trì OK lấy đơn (api_key/bearer) · risks={len(risks)} · "
            f"primary={ttl_primary.get('days_left')}d · secondary={ttl_secondary.get('days_left')}d"
        )
        report["ok"] = True  # still can fetch via api_key
    else:
        report["verdict"] = f"❌ Phiên rủi ro — không lấy đơn được · risks={len(risks)}"

    # next actions for near-expiry
    next_actions = [
        "python3 scripts/token_session_maintain.py once",
        "python3 scripts/token_session_maintain.py --loop --interval 1800 --notify-on-risk",
        "python3 scripts/order_session_env.py ensure",
    ]
    if any(r["level"] in {"warn", "critical", "expired"} for r in risks if "pancake" in r["key"]):
        next_actions.insert(
            0,
            "python3 scripts/pancake_cookie_ingest.py --raw-file <pos_jwt_con_han> --no-scan",
        )
    report["next_actions"] = next_actions

    _write_outputs(report)
    _save_state(report)

    if notify_on_risk and risks:
        _notify_telegram(env, report)

    return report


def _save_state(report: dict[str, Any]) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    state = {
        "updated_at": report.get("checked_at"),
        "ttl": report.get("ttl"),
        "order_ready": report.get("order_ready"),
        "risks": report.get("risks"),
        "renewals": (report.get("heartbeat") or {}).get("renewals"),
        "verdict": report.get("verdict"),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(STATE_PATH, 0o600)
    except OSError:
        pass


def _write_outputs(report: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "token_session_maintain.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (REPORTS / "token_session_maintain.txt").write_text(format_text(report) + "\n", encoding="utf-8")


def _notify_telegram(env: dict[str, str], report: dict[str, Any]) -> None:
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return
    import urllib.parse
    import urllib.request

    text = format_text(report)[:3500]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST"), timeout=20)
    except Exception:
        pass


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🛡 TOKEN SESSION MAINTAIN",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"order_ready={report.get('order_ready')}",
    ]
    ttl = report.get("ttl") or {}
    for k in ("primary", "secondary"):
        row = ttl.get(k) or {}
        if row.get("exp_iso") or row.get("name"):
            lines.append(
                f"· {k}: {row.get('name')} · exp={row.get('exp_iso')} · "
                f"left={row.get('days_left')}d · level={row.get('level')} · alive={row.get('alive')}"
            )
    hb = report.get("heartbeat") or {}
    lines.append(
        f"Heartbeat: api_key={hb.get('api_key_alive')} shops={hb.get('api_key_shops')} · "
        f"renewed_primary={hb.get('primary_renewed')} secondary={hb.get('secondary_renewed')}"
    )
    ens = report.get("ensure") or {}
    if ens:
        lines.append(f"Ensure: {ens.get('verdict')} · refreshed={ens.get('refreshed')}")
    for r in report.get("risks") or []:
        lines.append(
            f"⚠ {r.get('key')} [{r.get('level')}] days_left={r.get('days_left')} · need={r.get('need')}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Duy trì token phiên — chống hết hạn / revoke sớm")
    ap.add_argument("command", nargs="?", default="once", choices=["once", "status", "loop"])
    ap.add_argument("--loop", action="store_true", help="Lặp duy trì (alias command=loop)")
    ap.add_argument("--interval", type=int, default=1800, help="Giây giữa các vòng (mặc định 30p)")
    ap.add_argument("--warn-days", type=float, default=DEFAULT_WARN_DAYS)
    ap.add_argument("--critical-days", type=float, default=DEFAULT_CRITICAL_DAYS)
    ap.add_argument("--notify-on-risk", action="store_true")
    ap.add_argument("--no-vtp-refresh", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cmd = "loop" if args.loop else args.command

    if cmd == "status":
        if STATE_PATH.is_file():
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            print(json.dumps(state, ensure_ascii=False, indent=2) if args.json else format_text({"checked_at": state.get("updated_at"), "verdict": state.get("verdict"), "order_ready": state.get("order_ready"), "ttl": state.get("ttl"), "heartbeat": {"renewals": state.get("renewals"), "api_key_alive": None}, "ensure": {}, "risks": state.get("risks") or []}))
            return 0
        print("Chưa có state — chạy: python3 scripts/token_session_maintain.py once")
        return 1

    def tick() -> dict[str, Any]:
        return maintain_once(
            warn_days=args.warn_days,
            critical_days=args.critical_days,
            auto_refresh_vtp=not args.no_vtp_refresh,
            notify_on_risk=args.notify_on_risk,
        )

    if cmd == "loop":
        print(f"🛡 token_session_maintain loop interval={args.interval}s warn={args.warn_days}d", flush=True)
        while True:
            rep = tick()
            print(format_text(rep) if not args.json else json.dumps(rep, ensure_ascii=False), flush=True)
            time.sleep(max(60, args.interval))
    else:
        rep = tick()
        print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else format_text(rep))
        return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
