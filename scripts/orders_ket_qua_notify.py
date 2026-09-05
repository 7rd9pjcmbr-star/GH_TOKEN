#!/usr/bin/env python3
"""Trạng thái KET_QUA + thông báo Telegram khi có đơn hoặc còn blocker."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
KET_QUA = REPORTS / "KET_QUA_DON_CHI_TIET.csv"
STATUS = REPORTS / "KET_QUA_DON_CHI_TIET.txt"
STATE = ROOT / "secrets" / "ket_qua_notify.state.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def count_rows() -> int:
    if not KET_QUA.is_file() or KET_QUA.stat().st_size == 0:
        return 0
    with KET_QUA.open(encoding="utf-8", errors="replace") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def load_flex_report() -> dict:
    p = REPORTS / "flex_local_ingest.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def blockers_text() -> list[str]:
    lines: list[str] = []
    flex = load_flex_report()
    jt = flex.get("jt_parsed_audit") or {}
    if jt.get("credential_only"):
        lines.append(
            f"jt_parsed_data.json: {jt.get('entries', 0)} bản ghi credential J&T — không có mã vận đơn/đơn"
        )
    if not (ROOT / "secrets" / "api_settings.local.json").is_file():
        lines.append("Thiếu api_settings.local.json (Pancake token SamSpa/ASUNMEE)")
    env = ROOT / "secrets" / "backend_pipes.env"
    if env.is_file():
        blob = env.read_text(encoding="utf-8", errors="replace")
        if "PANCAKE_POS_ACCESS_TOKEN=" in blob and not any(
            ln.split("=", 1)[1].strip()
            for ln in blob.splitlines()
            if ln.startswith("PANCAKE_POS_ACCESS_TOKEN=")
        ):
            lines.append("Thiếu PANCAKE_POS_ACCESS_TOKEN trong backend_pipes.env")
    inbox = ROOT / "quarantine" / "telegram"
    if not list(inbox.glob("orders_detailed_*.csv")) and not list(inbox.glob("orders_detailed_*.json")):
        lines.append("Chưa có orders_detailed_*.csv/json trong inbox")
    if not (inbox / "thanhcoong.xlsx").is_file():
        lines.append("Chưa có thanhcoong.xlsx (SPX export)")
    return lines


def write_status(rows: int) -> None:
    blockers = blockers_text()
    STATUS.write_text(
        "\n".join(
            [
                f"KET QUA DON CHI TIET · {utc_now()}",
                f"rows={rows}",
                f"file={KET_QUA.resolve()}",
                "",
                "=== Blockers ===" if blockers else "=== OK ===",
                *([f"· {b}" for b in blockers] if blockers else ["· (none)"]),
                "",
                "=== Cách có dòng đơn chi tiết ===",
                "1) Windows: chạy quarantine/telegram/V9_Windows_Sync/RUN_ME.bat (đẩy token + CSV lên Telegram)",
                "2) Gửi bot Telegram: api_settings.local.json hoặc orders_detailed_*.csv/json",
                "3) Upload Cursor: file CSV/XLSX đơn → uploads/ rồi bash scripts/flex_orders_run.sh",
            ]
        ),
        encoding="utf-8",
    )


def maybe_notify(rows: int, *, force: bool = False) -> dict:
    state: dict = {}
    if STATE.is_file():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}

    sent = False
    err = ""
    prev_rows = int(state.get("last_rows") or 0)
    should = force or (rows > 0 and rows != prev_rows) or (rows == 0 and not state.get("blocker_sent"))

    if should:
        try:
            from auto_backup_credential import send_telegram_message

            if rows > 0:
                msg = f"✅ KET_QUA_DON_CHI_TIET.csv · {rows} đơn chi tiết\n{KET_QUA}"
            else:
                msg = (
                    "⏳ Pipeline sẵn sàng · KET_QUA=0\n"
                    "Worker đang pull Telegram — gửi billcode/phone hoặc file đơn vào bot sẽ tự tra."
                )
            ok, detail = send_telegram_message(msg, disable_notification=rows == 0)
            sent = ok
            if not ok:
                err = detail[:120]
            if rows == 0:
                state["blocker_sent"] = True
        except Exception as e:  # noqa: BLE001
            err = str(e)[:120]

    state.update({"last_rows": rows, "updated_at": utc_now(), "last_notify_sent": sent})
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"rows": rows, "notified": sent, "error": err or None}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="KET_QUA status + Telegram notify")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = count_rows()
    write_status(rows)
    rep = maybe_notify(rows, force=args.force)
    rep["status_file"] = str(STATUS)
    rep["csv"] = str(KET_QUA) if KET_QUA.is_file() else None

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"KET_QUA rows={rows} notified={rep.get('notified')}")
    return 0 if rows > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
