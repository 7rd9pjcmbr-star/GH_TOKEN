#!/usr/bin/env python3
"""Auto-backup credential — active/backup rotation cho mọi nền tảng.

Tích hợp secrets/backend_pipes.env + secrets/credential_backup.state.json.
Gọi sau login thành công / ingest V9 / Pancake cookie / GHN cookie.

CLI:
  python3 scripts/auto_backup_credential.py status
  python3 scripts/auto_backup_credential.py on-login --platform pancake_pos --token '...'
  python3 scripts/auto_backup_credential.py rotate --platform ghn_token
  python3 scripts/auto_backup_credential.py health-report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
ENV_PATH = SECRETS / "backend_pipes.env"
STATE_PATH = SECRETS / "credential_backup.state.json"
REPORTS = ROOT / "reports" / "telegram-classify"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_credential(value: str) -> str:
    return hashlib.sha256(str(value or "").encode()).hexdigest()[:16]


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"version": 1, "updated_at": utc_now(), "expiry": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("expiry", {})
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "updated_at": utc_now(), "expiry": {}}


def _save_state(state: dict[str, Any]) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(STATE_PATH, 0o600)
    except OSError:
        pass


def get_setting(key: str, default: str = "") -> str:
    """Đọc từ backend_pipes.env hoặc expiry state."""
    if key.endswith("_expires_at"):
        st = _load_state()
        return str((st.get("expiry") or {}).get(key, default) or default)
    if ENV_PATH.is_file():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return default


def set_setting(key: str, value: str) -> None:
    """Ghi env hoặc expiry metadata."""
    if key.endswith("_expires_at"):
        st = _load_state()
        st.setdefault("expiry", {})[key] = value
        _save_state(st)
        return
    from access_token_rotate import upsert_env_values

    upsert_env_values({key: value})


def send_telegram_message(text: str, *, disable_notification: bool = True) -> tuple[bool, str]:
    env: dict[str, str] = {}
    p = SECRETS / "telegram.env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False, "no_telegram"
    payload = json.dumps(
        {
            "chat_id": chat,
            "text": text[:4000],
            "disable_notification": disable_notification,
        }
    ).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            ok = json.loads(resp.read()).get("ok", False)
        return bool(ok), "sent" if ok else "api_fail"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:120]


class AutoBackupCredential:
    def __init__(
        self,
        name: str,
        active_key: str,
        backup_key: str,
        active_exp_key: str = "",
        backup_exp_key: str = "",
        default_expiry_days: int = 30,
    ):
        self.name = name
        self.active_key = active_key
        self.backup_key = backup_key
        self.active_exp_key = active_exp_key or f"{active_key}_expires_at"
        self.backup_exp_key = backup_exp_key or f"{backup_key}_expires_at"
        self.default_expiry_days = default_expiry_days

    def get_active(self) -> str:
        return str(get_setting(self.active_key, "") or "").strip()

    def get_backup(self) -> str:
        return str(get_setting(self.backup_key, "") or "").strip()

    def get_active_expiry(self) -> str:
        return str(get_setting(self.active_exp_key, "") or "").strip()

    def get_backup_expiry(self) -> str:
        return str(get_setting(self.backup_exp_key, "") or "").strip()

    def on_login_success(self, new_credential: str, expiry_days: int | None = None) -> dict[str, Any]:
        if not new_credential or not str(new_credential).strip():
            return {"ok": False, "error": "Empty credential"}

        new_credential = str(new_credential).strip()
        current_active = self.get_active()
        expiry_days = expiry_days or self.default_expiry_days
        new_expiry = datetime.now(timezone.utc) + timedelta(days=int(max(1, expiry_days)))
        new_expiry_str = new_expiry.isoformat()

        result: dict[str, Any] = {
            "ok": True,
            "name": self.name,
            "action": "backup_created",
            "timestamp": utc_now(),
            "new_credential_hash": _hash_credential(new_credential),
            "moved_to_backup": False,
            "active_updated": False,
        }

        if current_active and _hash_credential(current_active) != _hash_credential(new_credential):
            set_setting(self.backup_key, current_active)
            active_exp = self.get_active_expiry()
            if active_exp:
                set_setting(self.backup_exp_key, active_exp)
            result["moved_to_backup"] = True
            result["backup_hash"] = _hash_credential(current_active)

        set_setting(self.active_key, new_credential)
        set_setting(self.active_exp_key, new_expiry_str)
        result["active_updated"] = True
        result["active_expires_at"] = new_expiry_str

        if result["moved_to_backup"] or result["active_updated"]:
            self._send_backup_alert(result)

        return result

    def _send_backup_alert(self, result: dict[str, Any]) -> None:
        try:
            lines = [
                f"[AUTO-BACKUP] {self.name}",
                f"Time: {result.get('timestamp', '')[:19]}",
            ]
            if result.get("moved_to_backup"):
                lines.append("Active moved to backup")
            if result.get("active_updated"):
                lines.append(f"New active set (exp: {result.get('active_expires_at', '')[:10]})")
            send_telegram_message("\n".join(lines), disable_notification=True)
        except Exception:  # noqa: BLE001
            pass

    def rotate_to_backup(self) -> dict[str, Any]:
        backup = self.get_backup()
        if not backup:
            return {"ok": False, "error": "No backup credential available"}
        backup_exp = self.get_backup_expiry()
        set_setting(self.active_key, backup)
        set_setting(self.active_exp_key, backup_exp)
        set_setting(self.backup_key, "")
        set_setting(self.backup_exp_key, "")
        result = {
            "ok": True,
            "name": self.name,
            "action": "rotated_to_backup",
            "timestamp": utc_now(),
            "active_expires_at": backup_exp,
        }
        send_telegram_message(
            f"[ROTATION] {self.name} → backup\nTime: {result['timestamp'][:19]}",
            disable_notification=True,
        )
        return result

    def get_status(self) -> dict[str, Any]:
        active = self.get_active()
        backup = self.get_backup()
        active_exp = self.get_active_expiry()
        backup_exp = self.get_backup_expiry()

        def _days_until(exp_str: str) -> int | None:
            if not exp_str:
                return None
            try:
                exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                delta = exp_dt - datetime.now(timezone.utc)
                return int(delta.total_seconds() // 86400)
            except Exception:  # noqa: BLE001
                return None

        return {
            "name": self.name,
            "active_key": self.active_key,
            "backup_key": self.backup_key,
            "active_configured": bool(active),
            "active_hash": _hash_credential(active) if active else "",
            "active_expires_at": active_exp,
            "active_days_remaining": _days_until(active_exp),
            "backup_configured": bool(backup),
            "backup_hash": _hash_credential(backup) if backup else "",
            "backup_expires_at": backup_exp,
            "backup_days_remaining": _days_until(backup_exp),
        }


PLATFORMS_CREDENTIALS: dict[str, AutoBackupCredential] = {
    "pancake_pos": AutoBackupCredential(
        name="Pancake POS API Key",
        active_key="PANCAKE_POS_API_KEY",
        backup_key="PANCAKE_POS_API_KEY_BACKUP",
        default_expiry_days=90,
    ),
    "pancake_bearer": AutoBackupCredential(
        name="Pancake POS Bearer",
        active_key="PANCAKE_POS_ACCESS_TOKEN",
        backup_key="PANCAKE_POS_SECONDARY_ACCESS_TOKEN",
        default_expiry_days=30,
    ),
    "pancake_cookies": AutoBackupCredential(
        name="Pancake Cookies",
        active_key="PANCAKE_COOKIES",
        backup_key="PANCAKE_COOKIES_BACKUP",
        default_expiry_days=30,
    ),
    "ghn_token": AutoBackupCredential(
        name="GHN API Token",
        active_key="GHN_API_TOKEN",
        backup_key="GHN_API_TOKEN_BACKUP",
        default_expiry_days=365,
    ),
    "vnpost_token": AutoBackupCredential(
        name="VNPost API Token",
        active_key="VNPOST_TOKEN",
        backup_key="VNPOST_TOKEN_BACKUP",
        default_expiry_days=365,
    ),
    "viettelpost_token": AutoBackupCredential(
        name="ViettelPost API Token",
        active_key="VIETTELPOST_TOKEN",
        backup_key="VIETTELPOST_TOKEN_BACKUP",
        default_expiry_days=365,
    ),
    "shopee_token": AutoBackupCredential(
        name="Shopee API Token",
        active_key="SHOPEE_ACCESS_TOKEN",
        backup_key="SHOPEE_ACCESS_TOKEN_BACKUP",
        default_expiry_days=90,
    ),
    "spx_token": AutoBackupCredential(
        name="SPX API Token",
        active_key="SPX_TOKEN",
        backup_key="SPX_TOKEN_BACKUP",
        default_expiry_days=90,
    ),
    "jt_lendon_session": AutoBackupCredential(
        name="J&T Lendon October Session",
        active_key="JT_LENDON_OCTOBER_SESSION",
        backup_key="JT_LENDON_OCTOBER_SESSION_BACKUP",
        default_expiry_days=7,
    ),
}

# session_store platform → auto_backup platform (cookie header)
SESSION_COOKIE_PLATFORMS: dict[str, str] = {
    "Pancake": "pancake_cookies",
    "JT_Lendon": "jt_lendon_session",
    "Lendon": "jt_lendon_session",
}

# token env key prefix → auto_backup platform
TOKEN_KEY_TO_PLATFORM: dict[str, str] = {
    "PANCAKE_POS_API_KEY": "pancake_pos",
    "PANCAKE_POS_ACCESS_TOKEN": "pancake_bearer",
    "GHN_API_TOKEN": "ghn_token",
    "VIETTELPOST_TOKEN": "viettelpost_token",
    "VNPOST_TOKEN": "vnpost_token",
    "SHOPEE_ACCESS_TOKEN": "shopee_token",
    "SPX_TOKEN": "spx_token",
    "PANCAKE_COOKIES": "pancake_cookies",
    "JT_LENDON_OCTOBER_SESSION": "jt_lendon_session",
}

PLATFORM_ALIASES = {
    "pancake": "pancake_bearer",
    "pancake_api": "pancake_pos",
    "pancake_pos_api": "pancake_pos",
    "ghn": "ghn_token",
    "vtp": "viettelpost_token",
    "viettelpost": "viettelpost_token",
    "vnpost": "vnpost_token",
    "shopee": "shopee_token",
    "spx": "spx_token",
}


def _resolve_platform(platform: str) -> str:
    p = str(platform or "").lower().strip().replace("-", "_")
    return PLATFORM_ALIASES.get(p, p)


def apply_auto_backup_on_login_success(
    platform: str,
    new_credential: str,
    expiry_days: int | None = None,
) -> dict[str, Any]:
    platform = _resolve_platform(platform)
    if platform not in PLATFORMS_CREDENTIALS:
        return {
            "ok": False,
            "error": f"Unknown platform: {platform}",
            "available_platforms": list(PLATFORMS_CREDENTIALS.keys()),
        }
    return PLATFORMS_CREDENTIALS[platform].on_login_success(new_credential, expiry_days)


def resolve_credentials_env() -> dict[str, str]:
    """Trả về env với active credential; nếu active trống thì dùng backup."""
    out: dict[str, str] = {}
    for _name, mgr in PLATFORMS_CREDENTIALS.items():
        active = mgr.get_active()
        backup = mgr.get_backup()
        if active:
            out[mgr.active_key] = active
            if backup:
                out[mgr.backup_key] = backup
        elif backup:
            out[mgr.active_key] = backup
            out[mgr.backup_key] = backup
    return out


def apply_resolved_to_process_env() -> dict[str, Any]:
    """Đẩy credential resolved (active/backup) vào os.environ."""
    applied: list[str] = []
    for key, val in resolve_credentials_env().items():
        if val:
            os.environ[key] = val
            applied.append(key)
    return {"applied_count": len(applied), "keys": sorted(set(applied))}


def sync_lendon_session_file() -> dict[str, Any]:
    """Đồng bộ jt_lendon_session.json ↔ backup october_session."""
    session_path = SECRETS / "jt_lendon_session.json"
    mgr = PLATFORMS_CREDENTIALS["jt_lendon_session"]

    if session_path.is_file():
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
            for c in data.get("cookies") or []:
                if (c.get("name") or "").lower() == "october_session" and c.get("value"):
                    rep = mgr.on_login_success(str(c["value"]))
                    rep["source"] = "jt_lendon_session.json"
                    return rep
        except (OSError, json.JSONDecodeError, TypeError) as e:
            return {"ok": False, "error": str(e)[:120]}

    token = mgr.get_active() or mgr.get_backup()
    if not token:
        return {"ok": False, "error": "no_lendon_session"}

    payload = {
        "saved_at": utc_now(),
        "source": "auto_backup_credential",
        "cookies": [
            {"name": "october_session", "value": token, "domain": ".jtexpress.vn", "path": "/"},
        ],
    }
    SECRETS.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(session_path, 0o600)
    except OSError:
        pass
    return {"ok": True, "action": "restored_to_file", "path": str(session_path)}


def sync_session_store_to_backup() -> dict[str, Any]:
    """Đọc session_store.json → backup token + cookie header."""
    try:
        from session_store import cookie_header, load_store
    except ImportError as e:
        return {"ok": False, "error": str(e)[:120]}

    store = load_store()
    results: list[dict[str, Any]] = []
    for _plat, entry in (store.get("platforms") or {}).items():
        for key, val in (entry.get("tokens") or {}).items():
            plat = TOKEN_KEY_TO_PLATFORM.get(key)
            if plat and val:
                results.append(apply_auto_backup_on_login_success(plat, str(val)))

    for store_plat, backup_plat in SESSION_COOKIE_PLATFORMS.items():
        hdr = cookie_header(store_plat, store=store)
        if hdr and backup_plat in PLATFORMS_CREDENTIALS:
            results.append(apply_auto_backup_on_login_success(backup_plat, hdr))

    applied = apply_to_env_from_session_store(store)
    return {
        "ok": True,
        "backup_results": len(results),
        "session_applied": applied.get("applied_count", 0),
    }


def apply_to_env_from_session_store(store: dict[str, Any] | None = None) -> dict[str, Any]:
    """Áp token từ session_store vào os.environ (owned-only)."""
    try:
        from session_store import apply_to_env, load_store
    except ImportError as e:
        return {"ok": False, "error": str(e)[:120]}
    return apply_to_env(store if store is not None else load_store())


def bootstrap_credentials(
    *,
    from_v9: bool = True,
    session_store: bool = True,
    lendon: bool = True,
) -> dict[str, Any]:
    """Nạp credential từ V9 + session_store + lendon → backup rotation → env."""
    report: dict[str, Any] = {"ok": True, "module": "auto_backup_credential.bootstrap", "steps": {}}

    if from_v9:
        try:
            from v9_token_loader import load_v9_env

            v9_env = load_v9_env(apply_secrets=False) or {}
            report["steps"]["v9_backup"] = apply_from_v9_env(v9_env)
        except Exception as e:  # noqa: BLE001
            report["steps"]["v9_backup"] = {"ok": False, "error": str(e)[:120]}

    if session_store:
        report["steps"]["session_store"] = sync_session_store_to_backup()

    if lendon:
        report["steps"]["lendon_session"] = sync_lendon_session_file()

    report["steps"]["resolved_env"] = apply_resolved_to_process_env()
    write_status_report()
    return report


def apply_from_v9_env(env: dict[str, str]) -> list[dict[str, Any]]:
    """Backup tự động từ env V9 đã map."""
    results: list[dict[str, Any]] = []
    mapping = [
        ("pancake_pos", "PANCAKE_POS_API_KEY"),
        ("pancake_bearer", "PANCAKE_POS_ACCESS_TOKEN"),
        ("ghn_token", "GHN_API_TOKEN"),
        ("viettelpost_token", "VIETTELPOST_TOKEN"),
        ("vnpost_token", "VNPOST_TOKEN"),
        ("spx_token", "SPX_TOKEN"),
        ("shopee_token", "SHOPEE_ACCESS_TOKEN"),
    ]
    for plat, key in mapping:
        val = (env.get(key) or "").strip()
        if val:
            results.append(apply_auto_backup_on_login_success(plat, val))
    return results


def rotate_to_backup_for_platform(platform: str) -> dict[str, Any]:
    platform = _resolve_platform(platform)
    if platform not in PLATFORMS_CREDENTIALS:
        return {"ok": False, "error": f"Unknown platform: {platform}"}
    return PLATFORMS_CREDENTIALS[platform].rotate_to_backup()


def get_all_credentials_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "ok": True,
        "timestamp": utc_now(),
        "platforms": {},
        "summary": {
            "total_platforms": 0,
            "with_active": 0,
            "with_backup": 0,
            "expiring_soon": [],
        },
    }
    for platform_name, manager in PLATFORMS_CREDENTIALS.items():
        ps = manager.get_status()
        status["platforms"][platform_name] = ps
        status["summary"]["total_platforms"] += 1
        if ps.get("active_configured"):
            status["summary"]["with_active"] += 1
        if ps.get("backup_configured"):
            status["summary"]["with_backup"] += 1
        active_days = ps.get("active_days_remaining")
        if active_days is not None and active_days <= 7:
            status["summary"]["expiring_soon"].append(
                {
                    "platform": platform_name,
                    "days_remaining": active_days,
                    "expires_at": ps.get("active_expires_at"),
                }
            )
    return status


def send_credentials_health_report() -> tuple[bool, str]:
    status = get_all_credentials_status()
    lines = [
        "[CREDENTIALS HEALTH]",
        f"Time: {status['timestamp'][:19]}",
        f"Active: {status['summary']['with_active']}/{status['summary']['total_platforms']}",
        f"Backup: {status['summary']['with_backup']}/{status['summary']['total_platforms']}",
    ]
    if status["summary"]["expiring_soon"]:
        lines.append("Expiring soon:")
        for item in status["summary"]["expiring_soon"]:
            lines.append(f"  - {item['platform']}: {item['days_remaining']}d")
    for platform_name, cred_status in status["platforms"].items():
        a = "OK" if cred_status.get("active_configured") else "--"
        b = "OK" if cred_status.get("backup_configured") else "--"
        lines.append(f"  {platform_name}: active={a} backup={b}")
    return send_telegram_message("\n".join(lines), disable_notification=True)


def write_status_report() -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    rep = get_all_credentials_status()
    p = REPORTS / "auto_backup_credential.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-backup credentials")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Trạng thái tất cả platform")
    ol = sub.add_parser("on-login", help="Ghi credential sau login thành công")
    ol.add_argument("--platform", required=True)
    ol.add_argument("--token", required=True)
    ol.add_argument("--days", type=int, default=0)

    rot = sub.add_parser("rotate", help="Chuyển sang backup")
    rot.add_argument("--platform", required=True)

    sub.add_parser("health-report", help="Gửi báo cáo Telegram")
    sub.add_parser("from-v9", help="Backup từ v9_credentials.env / V9 root")
    sub.add_parser("bootstrap", help="V9 + session_store + lendon → backup → env")
    sub.add_parser("resolve", help="In env resolved (active/backup fallback)")

    args = ap.parse_args()
    cmd = args.cmd or "status"

    if cmd == "status":
        rep = get_all_credentials_status()
        write_status_report()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    if cmd == "on-login":
        rep = apply_auto_backup_on_login_success(
            args.platform,
            args.token,
            expiry_days=args.days or None,
        )
        write_status_report()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 1
    if cmd == "rotate":
        rep = rotate_to_backup_for_platform(args.platform)
        write_status_report()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 1
    if cmd == "health-report":
        ok, detail = send_credentials_health_report()
        print(json.dumps({"ok": ok, "detail": detail}))
        return 0 if ok else 1
    if cmd == "from-v9":
        from v9_token_loader import load_v9_env

        env = load_v9_env(apply_secrets=False)
        results = apply_from_v9_env(env)
        write_status_report()
        print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
        return 0
    if cmd == "bootstrap":
        rep = bootstrap_credentials()
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        return 0 if rep.get("ok") else 1
    if cmd == "resolve":
        env = resolve_credentials_env()
        masked = {k: _hash_credential(v) if v else "" for k, v in env.items()}
        print(json.dumps({"ok": True, "keys": sorted(env.keys()), "hashes": masked}, ensure_ascii=False, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
