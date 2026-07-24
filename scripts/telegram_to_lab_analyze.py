#!/usr/bin/env python3
"""Mở hộp thoại Telegram → kéo file nghi → quarantine/lab → phân tích tĩnh.

Phòng thủ: không thực thi mẫu · không dump-login · không Acc_all login.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from telegram_captcha_pull import api, load_env, open_dialog  # noqa: E402
from telegram_inbox_scan_analyze import (  # noqa: E402
    DUMP_HINTS,
    build_report as inbox_build,
    write_outputs as inbox_write,
)
from lab_static_engine import analyze_path  # noqa: E402

INBOX = ROOT / "quarantine" / "telegram"
LAB = ROOT / "quarantine" / "lab"
REPORTS = ROOT / "reports" / "telegram-classify" / "lab-static"
REPORTS_LAB = ROOT / "reports" / "lab" / "static"

SUSPICIOUS_NAME = re.compile(
    r"(?i)(stealer|dump|acc_all|password|cookie|token|leak|assassin|valid_account|"
    r"ghn_token|results_cookies|internal_search|vnpost|proxy|socks|captcha|"
    r"cookiacc|onlylogs|\.exe$|\.dll$|\.ps1$|\.bat$|\.js$|\.vbs$)"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def analyze_file(path: Path) -> dict[str, Any]:
    """Lab static engine v2."""
    return analyze_path(path, root=ROOT, surface="tg-lab-v2")


def is_suspicious(path: Path) -> bool:
    name = path.name.lower()
    if path.is_dir():
        return False
    if name.startswith("."):
        return False
    if SUSPICIOUS_NAME.search(name):
        return True
    if any(h in name for h in DUMP_HINTS):
        return True
    # binary-ish extensions always stage for lab
    if path.suffix.lower() in {
        ".exe",
        ".dll",
        ".ps1",
        ".bat",
        ".cmd",
        ".js",
        ".vbs",
        ".jar",
        ".apk",
        ".bin",
        ".dat",
        ".xlsx",
        ".xls",
        ".csv",
        ".json",
        ".txt",
        ".zip",
        ".7z",
        ".rar",
        ".pcap",
        ".har",
    }:
        return True
    return path.stat().st_size > 0


def stage_to_lab(src: Path, dest_root: Path) -> Path:
    rel = src.relative_to(INBOX) if src.is_relative_to(INBOX) else Path(src.name)
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
        shutil.copy2(src, dest)
    return dest


def open_lab_dialog(token: str, chat_id: str) -> dict[str, Any]:
    text = (
        "🧪 Mở hộp thoại · PHÒNG THÍ NGHIỆM\n\n"
        "Gửi file nghi ngờ vào đây — bot sẽ:\n"
        "1) Kéo vào quarantine/lab\n"
        "2) Phân tích tĩnh (không chạy mẫu · không login dump)\n\n"
        "Gửi document / ảnh / text chứa mẫu cần triage."
    )
    return open_dialog(token, chat_id, text=text)


def build_report(*, wait: int = 3, open_chat: bool = True, notify: bool = True) -> dict[str, Any]:
    env = load_env()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    report: dict[str, Any] = {
        "ok": False,
        "module": "telegram_to_lab_analyze",
        "checked_at": utc_now(),
        "policy": {
            "defensive_lab": True,
            "no_execute": True,
            "no_dump_login": True,
        },
    }
    if not token or not chat:
        report["error"] = "Thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
        return report

    if open_chat:
        report["dialog"] = open_lab_dialog(token, chat)

    # Pull Telegram documents into quarantine/telegram
    inbox = inbox_build(pull=True, wait=wait)
    inbox_write(inbox)
    report["inbox_pull"] = {
        "ok": inbox.get("ok"),
        "verdict": inbox.get("verdict"),
        "pulled": (inbox.get("pull") or {}).get("downloaded")
        or (inbox.get("pull") or {}).get("files")
        or inbox.get("stats"),
    }

    LAB.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    staged: list[str] = []
    analyses: list[dict[str, Any]] = []
    for path in sorted(INBOX.rglob("*")):
        if not path.is_file():
            continue
        # skip our own lab copies loop
        if "lab" in path.parts and path.is_relative_to(LAB):
            continue
        if not is_suspicious(path):
            continue
        dest = stage_to_lab(path, LAB)
        staged.append(str(dest.relative_to(ROOT)))
        try:
            ana = analyze_file(dest)
        except OSError as e:
            ana = {"ok": False, "error": str(e), "file": {"name": dest.name}}
        analyses.append(ana)
        out_json = REPORTS / f"{dest.name}.lab.json"
        # avoid collision
        if out_json.exists():
            out_json = REPORTS / f"{dest.stem}-{hashlib.md5(str(dest).encode(), usedforsecurity=False).hexdigest()[:8]}.lab.json"
        out_json.write_text(json.dumps(ana, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        REPORTS_LAB.mkdir(parents=True, exist_ok=True)
        (REPORTS_LAB / f"{dest.name}.v2.json").write_text(json.dumps(ana, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    bands = Counter((a.get("summary") or {}).get("riskBand", "unknown") for a in analyses if a.get("ok"))
    high = [
        a
        for a in analyses
        if a.get("ok") and (a.get("summary") or {}).get("riskBand") in {"high", "critical"}
    ]
    report.update(
        {
            "ok": True,
            "lab_root": str(LAB.relative_to(ROOT)),
            "staged_n": len(staged),
            "analyzed_n": len(analyses),
            "risk_bands": dict(bands),
            "high_critical_n": len(high),
            "staged_sample": staged[:40],
            "top_risky": [
                {
                    "name": (a.get("file") or {}).get("name"),
                    "band": (a.get("summary") or {}).get("riskBand"),
                    "score": (a.get("summary") or {}).get("riskScore"),
                    "findings": [f.get("id") for f in (a.get("findings") or [])[:6]],
                    "path": (a.get("isolation") or {}).get("path"),
                }
                for a in sorted(
                    high,
                    key=lambda x: -((x.get("summary") or {}).get("riskScore") or 0),
                )[:25]
            ],
            "verdict": (
                f"✅ Lab TG · staged={len(staged)} · analyzed={len(analyses)} · "
                f"high/critical={len(high)} · bands={dict(bands)}"
            ),
            "next": [
                "Đọc reports/telegram-classify/lab-static/",
                "knowledge/experiments/EXP-02-static-analyze.md",
                "Không login dump / Acc_all / stealer",
            ],
        }
    )

    # summary outputs
    (REPORTS / "lab_batch_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (REPORTS / "lab_batch_summary.txt").write_text(format_text(report) + "\n", encoding="utf-8")
    (ROOT / "reports" / "telegram-classify" / "telegram_to_lab_analyze.txt").write_text(
        format_text(report) + "\n", encoding="utf-8"
    )
    (ROOT / "reports" / "telegram-classify" / "telegram_to_lab_analyze.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    if notify:
        try:
            api(
                token,
                "sendMessage",
                {
                    "chat_id": chat,
                    "text": format_text(report)[:3500],
                },
            )
            report["notified"] = True
        except Exception as e:  # noqa: BLE001
            report["notified"] = False
            report["notify_error"] = str(e)[:200]

    return report


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("🧪 TELEGRAM → PHÒNG THÍ NGHIỆM · PHÂN TÍCH TĨNH")
    L(f"Lúc: {report.get('checked_at')}")
    L(f"Verdict: {report.get('verdict')}")
    L("Policy: no execute · no dump-login · lab quarantine only")
    if report.get("dialog"):
        d = report["dialog"]
        L(f"Dialog: ok={d.get('ok')} mid={d.get('message_id')}")
    if report.get("inbox_pull"):
        L(f"Pull: {report['inbox_pull'].get('verdict')}")
    L(f"Lab: {report.get('lab_root')} · staged={report.get('staged_n')} · analyzed={report.get('analyzed_n')}")
    L(f"Risk bands: {report.get('risk_bands')}")
    L("")
    L("=== Top risky ===")
    for t in report.get("top_risky") or []:
        L(f"· [{t.get('band')}/{t.get('score')}] {t.get('name')}")
        L(f"  findings: {', '.join(t.get('findings') or []) or '—'}")
        L(f"  {t.get('path')}")
    L("")
    for n in report.get("next") or []:
        L(f"Next: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Telegram → lab quarantine → static analyze")
    ap.add_argument("--wait", type=int, default=3)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(
        wait=args.wait,
        open_chat=not args.no_open,
        notify=not args.no_notify,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report) if report.get("ok") else report.get("error") or report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
