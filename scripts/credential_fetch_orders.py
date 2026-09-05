#!/usr/bin/env python3
"""Đọc credential (owned) → lấy đơn từ mọi backend có token → KET_QUA.

Nguồn credential (theo thứ tự):
  auto_backup_credential (active/backup) · session_store · secrets/*.env
  v9_credentials.env · Telegram pull · inbox bridge · Chrome/Lendon sync

Không login bằng jt_parsed / stealer dump.

CLI:
  PYTHONPATH=scripts python3 scripts/credential_fetch_orders.py
  PYTHONPATH=scripts python3 scripts/credential_fetch_orders.py --audit
  PYTHONPATH=scripts python3 scripts/credential_fetch_orders.py --pull --days 14
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
INBOX = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"
KET_QUA = REPORTS / "KET_QUA_DON_CHIET_TIET.csv"

ENV_FILES = (
    SECRETS / "telegram.env",
    SECRETS / "backend_pipes.env",
    SECRETS / "jt_api.env",
    SECRETS / "jt_lendon.env",
    SECRETS / "order_session.env",
    SECRETS / "owned_accounts.env",
    SECRETS / "v9_credentials.env",
    SECRETS / "proxy.env",
    SECRETS / "warehouse.env",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask(val: str) -> str:
    v = (val or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:4]}…{v[-4:]}(len={len(v)})"


def load_credentials() -> dict[str, str]:
    """Gom biến môi trường từ secrets + backup credential + shell."""
    from export_orders_detailed import bootstrap_secrets_from_inbox, load_env

    bootstrap_secrets_from_inbox()
    env = load_env()

    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    # auto_backup: active + backup fallback (token, cookie, session)
    try:
        from auto_backup_credential import resolve_credentials_env

        for key, val in resolve_credentials_env().items():
            if val and not env.get(key):
                env[key] = val
    except Exception:  # noqa: BLE001
        pass

    for key in (
        "PANCAKE_POS_ACCESS_TOKEN",
        "PANCAKE_POS_API_KEY",
        "PANCAKE_COOKIES",
        "PANCAKE_SHOP_ID",
        "GHN_API_TOKEN",
        "JT_LENDON_USER",
        "JT_LENDON_PASSWORD",
        "JT_LENDON_OCTOBER_SESSION",
        "JT_API_ACCOUNT",
        "JT_PRIVATE_KEY",
        "JT_PASSWORD",
        "VIETTELPOST_TOKEN",
        "SPX_TOKEN",
        "VNPOST_TOKEN",
    ):
        val = os.environ.get(key, "").strip()
        if val:
            env[key] = val

    try:
        from v9_token_loader import load_v9_env

        for k, v in (load_v9_env(apply_secrets=False) or {}).items():
            if v and k not in env:
                env[k] = v
    except Exception:  # noqa: BLE001
        pass

    return env


def audit_credentials(env: dict[str, str]) -> dict[str, Any]:
    """Trạng thái credential theo backend (masked)."""
    from auto_backup_credential import get_all_credentials_status
    from pancake_pos_client import auth_ready, resolve_credentials

    pancake = resolve_credentials(
        api_key=env.get("PANCAKE_POS_API_KEY", ""),
        access_token=env.get("PANCAKE_POS_ACCESS_TOKEN", ""),
    )
    lendon_session = (SECRETS / "jt_lendon_session.json").is_file()
    lendon_october = bool(env.get("JT_LENDON_OCTOBER_SESSION"))
    api_settings = (SECRETS / "api_settings.local.json").is_file()
    backup_status = get_all_credentials_status()

    backends = {
        "telegram": {
            "ready": bool(env.get("TELEGRAM_BOT_TOKEN")),
            "user": env.get("TELEGRAM_BOT_USERNAME", ""),
            "token": mask(env.get("TELEGRAM_BOT_TOKEN", "")),
        },
        "pancake": {
            "ready": auth_ready(pancake),
            "shop_id": env.get("PANCAKE_SHOP_ID", ""),
            "api_key": mask(env.get("PANCAKE_POS_API_KEY", "")),
            "bearer": mask(env.get("PANCAKE_POS_ACCESS_TOKEN", "")),
            "api_settings_file": api_settings,
        },
        "ghn": {
            "ready": bool(env.get("GHN_API_TOKEN")),
            "token": mask(env.get("GHN_API_TOKEN", "")),
        },
        "jt_lendon": {
            "ready": bool(
                (env.get("JT_LENDON_USER") and env.get("JT_LENDON_PASSWORD"))
                or lendon_session
                or lendon_october
            ),
            "user": env.get("JT_LENDON_USER", ""),
            "session_file": lendon_session,
            "october_session": mask(env.get("JT_LENDON_OCTOBER_SESSION", "")),
        },
        "jt_open_api": {
            "ready": bool(
                env.get("JT_API_ACCOUNT") and env.get("JT_PRIVATE_KEY") and env.get("JT_CUSTOMER_CODE")
            ),
            "account": mask(env.get("JT_API_ACCOUNT", "")),
            "customer_code": env.get("JT_CUSTOMER_CODE", ""),
        },
        "viettelpost": {"ready": bool(env.get("VIETTELPOST_TOKEN")), "token": mask(env.get("VIETTELPOST_TOKEN", ""))},
        "spx": {"ready": bool(env.get("SPX_TOKEN") or env.get("SHOPEE_ACCESS_TOKEN"))},
        "vnpost": {"ready": bool(env.get("VNPOST_TOKEN"))},
        "local_inbox": {
            "ready": bool(list(INBOX.glob("orders*.xlsx")) + list(INBOX.glob("orders_detailed_*.csv"))),
            "files": len(list(INBOX.glob("orders*"))),
        },
    }
    ready_n = sum(1 for b in backends.values() if b.get("ready"))
    return {
        "checked_at": utc_now(),
        "backends": backends,
        "backup": {
            "active": backup_status.get("summary", {}).get("with_active", 0),
            "backup": backup_status.get("summary", {}).get("with_backup", 0),
            "total": backup_status.get("summary", {}).get("total_platforms", 0),
        },
        "ready_count": ready_n,
        "total_backends": len(backends),
    }


def import_runtime_credentials(*, pull: bool = False, wait: int = 8) -> dict[str, Any]:
    """Kéo credential/file mới từ backup + Telegram + bridge + Chrome Lendon."""
    steps: dict[str, Any] = {}

    try:
        from auto_backup_credential import bootstrap_credentials

        steps["credential_backup"] = bootstrap_credentials()
    except Exception as e:  # noqa: BLE001
        steps["credential_backup"] = {"ok": False, "error": str(e)[:120]}

    try:
        from uploads_inbox_bridge import bridge_uploads

        steps["uploads_bridge"] = bridge_uploads()
    except Exception as e:  # noqa: BLE001
        steps["uploads_bridge"] = {"ok": False, "error": str(e)[:120]}

    if pull:
        try:
            from telegram_inbox_today_mapper import pull_telegram_inbox, load_env as tg_env

            token = tg_env().get("TELEGRAM_BOT_TOKEN") or tg_env().get("BOT_TOKEN") or ""
            chat = tg_env().get("TELEGRAM_CHAT_ID") or tg_env().get("CHAT_ID")
            steps["telegram_pull"] = pull_telegram_inbox(token, chat_id=chat or None, wait=wait)
        except Exception as e:  # noqa: BLE001
            steps["telegram_pull"] = {"ok": False, "error": str(e)[:120]}

    try:
        from jt_lendon_fetch import import_lendon_files_from_inbox

        steps["jt_lendon_import"] = {"imported": import_lendon_files_from_inbox()}
    except Exception as e:  # noqa: BLE001
        steps["jt_lendon_import"] = {"error": str(e)[:120]}

    try:
        from jt_lendon_chrome_sync import sync_and_fetch

        steps["chrome_lendon_sync"] = sync_and_fetch()
    except Exception as e:  # noqa: BLE001
        steps["chrome_lendon_sync"] = {"ok": False, "error": str(e)[:120]}

    try:
        from export_orders_detailed import bootstrap_secrets_from_inbox

        steps["secrets_imported"] = bootstrap_secrets_from_inbox()
    except Exception as e:  # noqa: BLE001
        steps["secrets_imported"] = {"error": str(e)[:120]}

    return steps


def _ket_qua_rows() -> int:
    if not KET_QUA.is_file() or KET_QUA.stat().st_size == 0:
        return 0
    with KET_QUA.open(encoding="utf-8", errors="replace") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def _write_ket_qua_from_result() -> int:
    src = REPORTS / "orders_detailed_RESULT.csv"
    if src.is_file() and src.stat().st_size > 0:
        REPORTS.mkdir(parents=True, exist_ok=True)
        KET_QUA.write_bytes(src.read_bytes())
        return _ket_qua_rows()
    return _ket_qua_rows()


def fetch_orders(*, days: int = 7, limit: int = 10000, pull: bool = False) -> dict[str, Any]:
    """Đọc credential → fetch từng backend → ghi KET_QUA."""
    report: dict[str, Any] = {
        "ok": False,
        "module": "credential_fetch_orders",
        "checked_at": utc_now(),
        "steps": {},
        "ket_qua_rows_before": _ket_qua_rows(),
    }

    report["steps"]["import"] = import_runtime_credentials(pull=pull)
    env = load_credentials()
    report["credentials"] = audit_credentials(env)

    # 1) Local files (xlsx/csv/json) — không cần API token
    try:
        from flex_local_ingest import run_flex_ingest

        report["steps"]["flex_local"] = run_flex_ingest()
    except Exception as e:  # noqa: BLE001
        report["steps"]["flex_local"] = {"ok": False, "error": str(e)[:120]}

    # 2) J&T Lendon portal
    try:
        from jt_lendon_fetch import run_fetch as lendon_fetch

        report["steps"]["jt_lendon"] = lendon_fetch(apply=True)
    except Exception as e:  # noqa: BLE001
        report["steps"]["jt_lendon"] = {"ok": False, "error": str(e)[:120]}

    # 3) J&T Open API (tracking refs)
    try:
        from jt_express_fetch import run_fetch as jt_api_fetch

        report["steps"]["jt_open_api"] = jt_api_fetch(apply=True)
    except Exception as e:  # noqa: BLE001
        report["steps"]["jt_open_api"] = {"ok": False, "error": str(e)[:120]}

    # 4) Pancake / buucuc scan / inbox merge
    try:
        from export_orders_detailed import build_report

        report["steps"]["api_export"] = build_report(days=days, limit=limit)
    except Exception as e:  # noqa: BLE001
        report["steps"]["api_export"] = {"ok": False, "error": str(e)[:120]}

    # 5) Enrich + KET_QUA chuẩn
    try:
        from dang_giao_chi_tiet_table import build_report as dg_build, write_outputs as dg_write

        dg = dg_build(include_shipped=True, include_all=True, ingest_limit=limit)
        dg_write(dg)
        report["steps"]["dang_giao"] = {"ok": True, "orders": (dg.get("summary") or {}).get("orders", 0)}
    except Exception as e:  # noqa: BLE001
        report["steps"]["dang_giao"] = {"ok": False, "error": str(e)[:120]}

    ket_rows = _write_ket_qua_from_result()
    report["ket_qua"] = {"rows": ket_rows, "path": str(KET_QUA)}
    report["ket_qua_rows_after"] = ket_rows
    report["ok"] = ket_rows > 0

    new_rows = ket_rows - int(report.get("ket_qua_rows_before") or 0)
    ready = [k for k, v in (report.get("credentials") or {}).get("backends", {}).items() if v.get("ready")]
    report["verdict"] = (
        f"KET_QUA={ket_rows} đơn (+{new_rows}) · cred ready: {', '.join(ready) or 'local only'}"
        if ket_rows
        else f"Chưa có đơn · cred ready: {', '.join(ready) or 'none'} — cần token Pancake/GHN hoặc file orders*.xlsx"
    )

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "credential_fetch_orders.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Đọc credential → lấy đơn → KET_QUA")
    ap.add_argument("--audit", action="store_true", help="Chỉ rà credential, không fetch")
    ap.add_argument("--pull", action="store_true", help="Kéo Telegram trước khi fetch")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=10000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.audit:
        env = load_credentials()
        rep = audit_credentials(env)
        rep["ket_qua_rows"] = _ket_qua_rows()
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(f"credential audit ready={rep.get('ready_count')}/{rep.get('total_backends')} ket_qua={rep.get('ket_qua_rows')}")
            for name, st in (rep.get("backends") or {}).items():
                mark = "OK" if st.get("ready") else "—"
                print(f"  [{mark}] {name}")
        return 0

    rep = fetch_orders(days=args.days, limit=args.limit, pull=args.pull)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"credential_fetch_orders ok={rep.get('ok')}")
        print(f"  {rep.get('verdict')}")
        print(f"  KET_QUA: {rep.get('ket_qua', {}).get('path')}")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
