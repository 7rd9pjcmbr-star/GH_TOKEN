#!/usr/bin/env python3
"""Pipeline tối ưu: credential owned → backup → fetch → KET_QUA.

Thứ tự (không đảo):
  1. backup_credential bootstrap  — V9 + session_store + Lendon → active/backup
  2. session keepalive            — duy trì token/cookie owned
  3. Telegram pull (tùy chọn)     — file/token mới từ bot
  4. Audit credential             — biết thiếu gì trước khi fetch
  5. Fetch đơn                    — local + API + buucuc scan
  6. Realtime 1 chu kỳ (tùy chọn)— đơn mới nếu có token
  7. Báo cáo KET_QUA

CLI:
  PYTHONPATH=scripts python3 scripts/owned_orders_pipeline.py
  PYTHONPATH=scripts python3 scripts/owned_orders_pipeline.py --pull --days 2
  PYTHONPATH=scripts python3 scripts/owned_orders_pipeline.py --full --pull
  PYTHONPATH=scripts python3 scripts/owned_orders_pipeline.py --credential-only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
KET_QUA = REPORTS / "KET_QUA_DON_CHIET_TIET.csv"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ket_qua_rows() -> int:
    if not KET_QUA.is_file():
        return 0
    with KET_QUA.open(encoding="utf-8") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def run_pipeline(
    *,
    pull: bool = False,
    days: int = 7,
    limit: int = 20000,
    full: bool = False,
    credential_only: bool = False,
    realtime: bool = True,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "module": "owned_orders_pipeline",
        "checked_at": utc_now(),
        "policy": "owned-only · backup_credential active/backup · no stealer dump",
        "steps": {},
        "ket_qua_before": _ket_qua_rows(),
    }

    # ── 1. Backup credential (trung tâm) ─────────────────────────────────
    try:
        from auto_backup_credential import bootstrap_credentials, get_all_credentials_status, write_status_report

        report["steps"]["backup_bootstrap"] = bootstrap_credentials()
        report["steps"]["backup_status"] = get_all_credentials_status()
        write_status_report()
    except Exception as e:  # noqa: BLE001
        report["steps"]["backup_bootstrap"] = {"ok": False, "error": str(e)[:160]}

    # ── 2. Session keepalive ─────────────────────────────────────────────
    try:
        from session_store import keepalive

        report["steps"]["session_keepalive"] = keepalive(refresh=True, probe=False)
    except Exception as e:  # noqa: BLE001
        report["steps"]["session_keepalive"] = {"ok": False, "error": str(e)[:160]}

    # ── 3. V9 env (nếu có máy V9 / file sync) ────────────────────────────
    try:
        from v9_token_loader import load_v9_env, persist_v9_env

        v9 = load_v9_env(apply_secrets=False) or {}
        if any(v9.get(k) for k in ("PANCAKE_POS_ACCESS_TOKEN", "GHN_API_TOKEN", "V9_API_URL")):
            persist_v9_env(v9)
            from auto_backup_credential import apply_from_v9_env, bootstrap_credentials

            report["steps"]["v9_backup"] = apply_from_v9_env(v9)
            bootstrap_credentials(from_v9=False)  # re-resolve sau V9
        else:
            report["steps"]["v9_backup"] = {"ok": True, "skipped": True, "detail": "no V9 tokens"}
    except Exception as e:  # noqa: BLE001
        report["steps"]["v9_backup"] = {"ok": False, "error": str(e)[:160]}

    if credential_only:
        try:
            from credential_fetch_orders import audit_credentials, load_credentials

            env = load_credentials()
            report["steps"]["audit"] = audit_credentials(env)
        except Exception as e:  # noqa: BLE001
            report["steps"]["audit"] = {"ok": False, "error": str(e)[:160]}
        report["ok"] = True
        report["verdict"] = _verdict(report, credential_only=True)
        _write_report(report)
        return report

    # ── 4. Fetch đơn ─────────────────────────────────────────────────────
    if full:
        try:
            from orders_tong_luc import run_tong_luc

            report["steps"]["tong_luc"] = run_tong_luc(days=days, limit=limit, pull=pull, skip_remote=False)
        except Exception as e:  # noqa: BLE001
            report["steps"]["tong_luc"] = {"ok": False, "error": str(e)[:160]}
    else:
        try:
            from credential_fetch_orders import fetch_orders

            report["steps"]["credential_fetch"] = fetch_orders(days=days, limit=limit, pull=pull)
        except Exception as e:  # noqa: BLE001
            report["steps"]["credential_fetch"] = {"ok": False, "error": str(e)[:160]}

    # ── 5. Realtime 1 chu kỳ (nếu có token) ──────────────────────────────
    if realtime:
        try:
            from realtime_order_sync import load_env, run_cycle

            env = load_env()
            report["steps"]["realtime_once"] = run_cycle(env, limit=min(50, limit), notify=False, notify_new_only=True)
        except Exception as e:  # noqa: BLE001
            report["steps"]["realtime_once"] = {"ok": False, "error": str(e)[:160]}

    rows = _ket_qua_rows()
    report["ket_qua_after"] = rows
    report["ket_qua_delta"] = rows - int(report.get("ket_qua_before") or 0)
    report["ok"] = rows > 0
    report["verdict"] = _verdict(report)
    _write_report(report)
    return report


def _verdict(report: dict[str, Any], *, credential_only: bool = False) -> str:
    bs = (report.get("steps") or {}).get("backup_status") or {}
    summary = bs.get("summary") or {}
    active = summary.get("with_active", 0)
    total = summary.get("total_platforms", 0)

    if credential_only:
        return f"Credential: active {active}/{total} platform · chạy lại không --credential-only để fetch đơn"

    rows = report.get("ket_qua_after", 0)
    delta = report.get("ket_qua_delta", 0)
    blocked = []
    rt = (report.get("steps") or {}).get("realtime_once") or {}
    for b in rt.get("blocked") or []:
        blocked.append(b.split(":")[0] if ":" in b else b[:40])

    parts = [f"KET_QUA={rows} đơn (+{delta})", f"backup active={active}/{total}"]
    if blocked:
        parts.append(f"API chặn: {', '.join(blocked[:4])}")
    elif active < 3:
        parts.append("thiếu Pancake/GHN token → gửi Telegram hoặc backend_pipes.env")
    return " · ".join(parts)


def _write_report(report: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "owned_orders_pipeline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Pipeline tối ưu: backup credential → fetch → KET_QUA")
    ap.add_argument("--pull", action="store_true", help="Kéo Telegram trước fetch")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--full", action="store_true", help="Dùng orders_tong_luc (quét shop×buucuc đầy đủ)")
    ap.add_argument("--credential-only", action="store_true", help="Chỉ nạp/audit credential")
    ap.add_argument("--no-realtime", action="store_true", help="Bỏ chu kỳ realtime")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = run_pipeline(
        pull=args.pull,
        days=args.days,
        limit=args.limit,
        full=args.full,
        credential_only=args.credential_only,
        realtime=not args.no_realtime,
    )

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"owned_orders_pipeline ok={rep.get('ok')}")
        print(f"  {rep.get('verdict')}")
        print(f"  KET_QUA: {KET_QUA}")
    return 0 if rep.get("ok") or args.credential_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
