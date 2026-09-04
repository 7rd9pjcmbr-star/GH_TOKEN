#!/usr/bin/env python3
"""AssassinTool — phân tích file report/assassin trong quarantine Telegram.

Tập trung file có tên chứa report · assassin · final_report (và dump liên quan).
Giữ tín hiệu lấy đơn (URL/host/user/shop/platform) · che password · không login.

CLI:
  python3 scripts/assassin_tool.py
  python3 scripts/assassin_tool.py --all
  python3 scripts/assassin_tool.py path/to/report.txt
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

INBOX = ROOT / "quarantine" / "telegram"
DUMPS = INBOX / "_skipped_dumps"
REPORTS = ROOT / "reports" / "telegram-classify"
OUT_JSON = REPORTS / "assassin_tool.json"
OUT_TXT = REPORTS / "assassin_tool.txt"
EXTRACT_DIR = INBOX / "AssassinTool_extracted"

ASSASSIN_HINTS = ("assassin", "final_report", "report")
REPORT_KINDS = {"report", "dump_other", "dump_stealer", "dump_token", "dump_account", "text_blob"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_assassin_candidate(name: str) -> bool:
    n = (name or "").lower()
    if any(h in n for h in ASSASSIN_HINTS):
        return True
    from telegram_inbox_scan_analyze import REPORT_HINTS

    return any(h in n for h in REPORT_HINTS)


def find_assassin_zip() -> Path | None:
    """Locate AssassinTool.zip anywhere under repo (workspace upload)."""
    for p in sorted(ROOT.rglob("AssassinTool.zip"), key=lambda x: -x.stat().st_mtime):
        if p.is_file():
            return p
    for p in (INBOX / "AssassinTool.zip", ROOT / "AssassinTool.zip"):
        if p.is_file():
            return p
    return None


def extract_assassin_zip(src: Path | None = None, *, dest: Path | None = None) -> dict:
    """Giải nén AssassinTool.zip → quarantine/telegram/AssassinTool_extracted/."""
    src = src or find_assassin_zip()
    if not src or not src.is_file():
        return {
            "ok": False,
            "error": "Không thấy AssassinTool.zip trong workspace",
            "searched": [str(ROOT), str(INBOX)],
        }
    dest = dest or EXTRACT_DIR
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    try:
        with zipfile.ZipFile(src) as zf:
            for info in zf.infolist():
                # zip-slip safe
                target = (dest / info.filename).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    continue
                zf.extract(info, dest)
                if not info.is_dir():
                    extracted.append(info.filename)
    except zipfile.BadZipFile as e:
        return {"ok": False, "error": f"Bad zip: {e}", "source": str(src)}
    # also keep a copy/link in inbox for downstream tools
    inbox_copy = INBOX / src.name
    if src.resolve() != inbox_copy.resolve():
        shutil.copy2(src, inbox_copy)
    return {
        "ok": True,
        "source": str(src),
        "dest": str(dest),
        "files": len(extracted),
        "sample": extracted[:20],
    }


def list_assassin_files(*, all_files: bool = False) -> list[Path]:
    out: list[Path] = []
    folders = [INBOX, DUMPS]
    if EXTRACT_DIR.is_dir():
        folders.append(EXTRACT_DIR)
    for folder in folders:
        if not folder.is_dir():
            continue
        for p in folder.rglob("*"):
            if not p.is_file() or p.name.startswith("."):
                continue
            if p.suffix.lower() == ".zip" and p.name.lower() == "assassintool.zip":
                continue
            if all_files or is_assassin_candidate(p.name) or folder == EXTRACT_DIR:
                out.append(p)
    out.sort(key=lambda x: -x.stat().st_mtime)
    return out


def analyze_assassin_file(path: Path) -> dict:
    from telegram_inbox_scan_analyze import analyze_file, classify_kind

    kind = classify_kind(path.name)
    analysis = analyze_file(path, kind=kind)
    analysis["assassin_match"] = is_assassin_candidate(path.name)
    analysis["assassin_tags"] = [h for h in ASSASSIN_HINTS if h in path.name.lower()]
    return analysis


def rollup(analyses: list[dict]) -> dict:
    platforms: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    users: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    pipe_hints: list[dict] = []

    for a in analyses:
        kinds[a.get("kind") or "unknown"] += 1
        osig = a.get("order_signals") or {}
        sig = osig.get("signals") or {}
        for p in sig.get("platforms") or []:
            platforms[p] += 1
        for h in sig.get("hosts") or []:
            hosts[h] += 1
        for u in sig.get("users_top") or []:
            users[u] += 1
        for hint in osig.get("backend_hints") or []:
            pipe_hints.append(hint)

    return {
        "files": len(analyses),
        "by_kind": dict(kinds),
        "platforms": dict(platforms.most_common(12)),
        "hosts_top": [h for h, _ in hosts.most_common(12)],
        "users_top": [u for u, _ in users.most_common(12)],
        "pipe_hints": pipe_hints[:20],
    }


def icon_feedback(rollup_data: dict) -> str:
    try:
        from realtime_icon_feedback_mapper import feedback_line, map_channel

        mapped = []
        for platform, count in (rollup_data.get("platforms") or {}).items():
            mapped.append(
                map_channel(
                    {
                        "id": f"assassin:{platform}",
                        "status": "ok" if count else "stale",
                        "backend": platform,
                        "detail": f"assassin report hits={count}",
                    }
                )
            )
        if not mapped:
            mapped.append(
                map_channel(
                    {
                        "id": "assassin:empty",
                        "status": "stale",
                        "backend": "local",
                        "detail": "no assassin/report files",
                    }
                )
            )
        icons = mapped[0]["icons"]
        detail = mapped[0]["feedback"].split(" — ", 1)[-1]
        return feedback_line(icons, detail)
    except Exception as e:  # noqa: BLE001
        return f"AssassinTool · icon feedback unavailable: {e}"


def build_report(*, paths: list[Path] | None = None, all_files: bool = False) -> dict:
    candidates = paths or list_assassin_files(all_files=all_files)
    analyses = [analyze_assassin_file(p) for p in candidates]
    roll = rollup(analyses)
    verdict = (
        f"🗡 AssassinTool: {len(analyses)} file report/assassin · "
        f"platforms={list((roll.get('platforms') or {}).keys())[:6]} · "
        "che password · giữ tín hiệu lấy đơn · no dump-login"
    )
    return {
        "ok": True,
        "tool": "AssassinTool",
        "checked_at": utc_now(),
        "verdict": verdict,
        "icon_feedback": icon_feedback(roll),
        "stats": roll,
        "analyses": analyses,
        "paths": {
            "inbox": str(INBOX),
            "dumps": str(DUMPS),
            "out_json": str(OUT_JSON),
            "out_txt": str(OUT_TXT),
        },
        "safety": {
            "secrets_only": True,
            "no_dump_login": True,
            "passwords_redacted": True,
            "kept_order_related_values": True,
        },
        "next_actions": [
            "python3 scripts/telegram_inbox_scan_analyze.py — quét inbox đầy đủ",
            "python3 scripts/order_signal_extract.py quarantine/telegram/_skipped_dumps/*",
            "Panel: nút 🗡 Assassin·report",
        ],
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🗡 ASSASSIN TOOL · REPORT ANALYZER")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    if report.get("icon_feedback"):
        L(f"Icon: {report['icon_feedback']}")
    L("")
    st = report.get("stats") or {}
    L(f"Files: {st.get('files')} by_kind={st.get('by_kind')}")
    L(f"platforms={st.get('platforms')}")
    L(f"hosts_top={st.get('hosts_top')}")
    L(f"users_top={st.get('users_top')}")
    L("")
    for a in report.get("analyses") or []:
        tags = ",".join(a.get("assassin_tags") or []) or "-"
        L(f"· [{a.get('kind')}] {a.get('file')} tags={tags}")
        L(f"  {a.get('verdict')}")
        osig = a.get("order_signals") or {}
        sig = osig.get("signals") or {}
        if sig:
            L(f"  platforms={sig.get('platforms')} hosts={sig.get('hosts')[:6]}")
            if sig.get("urls_sample"):
                L(f"  urls={sig.get('urls_sample')[:4]}")
    L("")
    L("Safety: secrets-only · che password · giữ URL/user/host/shop/tracking")
    for n in report.get("next_actions") or []:
        L(f"· {n}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text(format_text(report), encoding="utf-8")
    return {"json": OUT_JSON, "txt": OUT_TXT}


def main() -> int:
    ap = argparse.ArgumentParser(description="AssassinTool — analyze report/assassin quarantine files")
    ap.add_argument("paths", nargs="*", help="Optional explicit file paths to analyze")
    ap.add_argument("--all", action="store_true", help="Include all inbox/dump files, not just assassin hints")
    ap.add_argument("--extract", action="store_true", help="Giải nén AssassinTool.zip rồi phân tích")
    ap.add_argument("--json", action="store_true", help="Print full JSON report")
    args = ap.parse_args()

    extract_result = None
    if args.extract:
        extract_result = extract_assassin_zip()
        if not extract_result.get("ok"):
            print(json.dumps(extract_result, ensure_ascii=False, indent=2) if args.json else extract_result.get("error"))
            return 1

    explicit = [Path(p) for p in args.paths] if args.paths else None
    report = build_report(paths=explicit, all_files=args.all)
    if extract_result:
        report["extract"] = extract_result
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
