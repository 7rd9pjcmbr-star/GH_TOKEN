#!/usr/bin/env python3
"""Rà soát hộp thoại Telegram — mọi nội dung liên quan GHN (mask only).

Kéo getUpdates (text + document) + quét quarantine/telegram.
Owned-only · không dump-login · không in raw token.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

INBOX = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
OUT_JSON = REPORTS / "telegram_ghn_audit.json"
OUT_TXT = REPORTS / "telegram_ghn_audit.txt"
OFFSET_PATH = SECRETS / "telegram_ghn_scan.offset"

GHN_RE = re.compile(
    r"(?i)(ghn\.vn|api\.ghn|shiip|printA5|GHN_API_TOKEN|GHN_TOKEN|GHN_SHOP|"
    r"online-gateway\.ghn|dev-online-gateway\.ghn|giao\s*hang\s*nhanh|"
    r"giaohangnhanh|\bghn\b)"
)
UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
PRINT_RE = re.compile(r"(?i)https?://[^\s\"'<>]*ghn\.vn[^\s\"'<>]*")
TOKEN_KV = re.compile(
    r"(?i)\b(GHN_API_TOKEN|GHN_TOKEN|Token)\s*[:=]\s*([0-9a-f-]{36}|[A-Za-z0-9_\-\.]{16,})"
)
REJECT_HINT = re.compile(
    r"(?i)(hjSession|_ga\b|Acc_all|stealer|ghn_tokens|results_cookies|dump)"
)
USER_PASS_RE = re.compile(
    r"(?i)(https?://[^\s:]+|/Home/Login|/ssoLogin)?[:\s]*([0-9a-zA-Z._%+\-]{3,80}):([^\s:]{3,80})(?::([0-9a-fA-F\-]{16,}))?"
)
DUMP_NAME_RE = re.compile(
    r"(?i)(ghn\.txt|ghn_tokens|acc_all|stealer|results_cookies|valid_accounts|internal_search)"
)


def redact_preview(line: str) -> str:
    """Mask passwords/tokens in dump-like lines before report."""
    shown = UUID_RE.sub(lambda m: mask(m.group(1)), line)
    shown = mask_url(shown)

    # url:user:pass  OR host/path:user:pass
    shown = re.sub(
        r"(https?://[^\s:]+|(?:sso(?:-v2)?\.)?ghn\.vn[^\s:]*)"
        r":([^:\s]{2,80}):([^:\s]{2,200})",
        lambda m: f"{m.group(1)}:{mask(m.group(2), keep=2)}:***",
        shown,
        flags=re.I,
    )
    # user:pass:uuid-token
    shown = re.sub(
        r"\b([^:\s]{2,80}):([^:\s]{2,80}):([0-9a-fA-F]{8}-[0-9a-fA-F\-]{27})\b",
        lambda m: f"{mask(m.group(1), keep=2)}:***:{mask(m.group(3))}",
        shown,
    )
    # email:pass:token
    shown = re.sub(
        r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}):([^:\s]{2,80}):([^\s]{8,})\b",
        lambda m: f"{mask(m.group(1), keep=3)}:***:{mask(m.group(3))}",
        shown,
    )
    # leftover phone:password (no token)
    shown = re.sub(
        r"\b(0\d{8,11}):([^:\s]{3,80})\b",
        lambda m: f"{mask(m.group(1), keep=2)}:***",
        shown,
    )
    return shown[:220]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask(v: str, keep: int = 4) -> str:
    v = (v or "").strip()
    if len(v) <= keep * 2:
        return "***"
    return f"{v[:keep]}…{v[-keep:]}(len={len(v)})"


def mask_url(u: str) -> str:
    return re.sub(
        r"(?i)(token=)([0-9a-f-]{36})",
        lambda m: m.group(1) + mask(m.group(2)),
        u,
    )


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for p in (
        SECRETS / "telegram.env",
        SECRETS / "backend_pipes.env",
        SECRETS / "order_session.env",
    ):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def _read_offset() -> int:
    best = 0
    for op in (
        OFFSET_PATH,
        SECRETS / "telegram.offset",
        SECRETS / "telegram_inbox.offset",
        SECRETS / "telegram_inbox_scan.offset",
    ):
        if not op.is_file():
            continue
        try:
            best = max(best, int(op.read_text(encoding="utf-8").strip() or "0"))
        except ValueError:
            pass
    return best


def pull_ghn_messages(token: str, *, lookback: int = 200) -> tuple[dict[str, Any], list[dict]]:
    bot_off = _read_offset()
    start = max(0, bot_off - max(0, lookback))
    payload = {
        "offset": start,
        "timeout": 0,
        "allowed_updates": ["message", "channel_post", "edited_message"],
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getUpdates",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200], "start_offset": start}, []

    if not data.get("ok"):
        return {"ok": False, "error": str(data)[:200], "start_offset": start}, []

    messages: list[dict] = []
    max_off = start
    for upd in data.get("result") or []:
        max_off = max(max_off, int(upd["update_id"]) + 1)
        msg = upd.get("message") or upd.get("channel_post") or upd.get("edited_message") or {}
        text = msg.get("text") or msg.get("caption") or ""
        doc = msg.get("document") or {}
        name = doc.get("file_name") or ""
        blob = f"{text}\n{name}"
        if not GHN_RE.search(blob):
            continue
        urls = PRINT_RE.findall(text)
        uuids = UUID_RE.findall(text)
        kv = [(a, mask(b)) for a, b in TOKEN_KV.findall(text)]
        messages.append(
            {
                "update_id": upd.get("update_id"),
                "message_id": msg.get("message_id"),
                "chat_id": str((msg.get("chat") or {}).get("id") or ""),
                "date": msg.get("date"),
                "from": (msg.get("from") or {}).get("username")
                or (msg.get("from") or {}).get("first_name"),
                "has_document": bool(doc),
                "document_name": name or None,
                "text_preview": text[:240] + ("…" if len(text) > 240 else ""),
                "urls_masked": [mask_url(u) for u in urls[:10]],
                "uuids_masked": [mask(u) for u in uuids[:20]],
                "token_kv_masked": kv[:10],
                "looks_dump": bool(REJECT_HINT.search(blob)),
            }
        )

    SECRETS.mkdir(parents=True, exist_ok=True)
    OFFSET_PATH.write_text(str(max_off), encoding="utf-8")
    pull = {
        "ok": True,
        "n": len(data.get("result") or []),
        "start_offset": start,
        "saved_offset": max_off,
        "ghn_messages": len(messages),
    }
    return pull, messages


def scan_inbox_files() -> tuple[list[dict], list[dict]]:
    file_hits: list[dict] = []
    name_hits: list[dict] = []
    if not INBOX.is_dir():
        return file_hits, name_hits

    for p in INBOX.rglob("*"):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = str(p.relative_to(INBOX))
        mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
        if GHN_RE.search(rel) or GHN_RE.search(p.name):
            name_hits.append({"path": rel, "size": p.stat().st_size, "mtime": mtime})

        if p.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".mp4",
            ".zip",
            ".rar",
            ".7z",
            ".db",
            ".sqlite",
        }:
            continue

        size = p.stat().st_size
        name_hint = bool(GHN_RE.search(rel) or GHN_RE.search(p.name))
        if size > 2_000_000 and not name_hint:
            continue
        try:
            text = p.read_bytes()[:400_000].decode("utf-8", errors="ignore")
        except Exception:
            continue
        if not GHN_RE.search(text) and not name_hint:
            continue

        urls = PRINT_RE.findall(text)[:15]
        uuids = UUID_RE.findall(text)[:30]
        lines: list[dict] = []
        for i, line in enumerate(text.splitlines()[:8000], 1):
            if not GHN_RE.search(line):
                continue
            shown = redact_preview(line)
            lines.append({"n": i, "preview": shown})
            if len(lines) >= 12:
                break

        looks_dump = bool(
            REJECT_HINT.search(rel)
            or REJECT_HINT.search(text[:2000])
            or DUMP_NAME_RE.search(p.name)
            or "username:password:token" in text[:500].lower()
            or "/Home/Login:" in text[:2000]
        )
        file_hits.append(
            {
                "path": rel,
                "size": size,
                "mtime": mtime,
                "in_dumps": "_skipped_dumps" in rel,
                "urls_n": len(PRINT_RE.findall(text)),
                "uuids_n": len(UUID_RE.findall(text)),
                "urls_masked": [
                    mask_url(u)
                    for u in urls
                    if "Login" not in u and ":/" in u and u.count(":") <= 1
                ][:10],
                "sample_uuids_masked": [mask(u) for u in uuids[:8]],
                "ghn_lines": lines,
                "looks_dump": looks_dump,
                "blocked_for_login": looks_dump,
            }
        )
    return file_hits, name_hits


def quarantine_dump_files(file_hits: list[dict]) -> list[dict]:
    """Move dump-like GHN files from inbox root → _skipped_dumps (no login)."""
    dumps = INBOX / "_skipped_dumps"
    dumps.mkdir(parents=True, exist_ok=True)
    moved: list[dict] = []
    for f in file_hits:
        if not f.get("looks_dump") or f.get("in_dumps"):
            continue
        src = INBOX / f["path"]
        if not src.is_file() or src.parent == dumps:
            continue
        # only move top-level inbox dumps
        if src.parent != INBOX:
            continue
        dest = dumps / src.name
        if dest.exists():
            dest = dumps / f"{src.stem}_{int(datetime.now(timezone.utc).timestamp())}{src.suffix}"
        try:
            src.rename(dest)
            moved.append({"from": f["path"], "to": str(dest.relative_to(INBOX))})
            f["path"] = str(dest.relative_to(INBOX))
            f["in_dumps"] = True
        except OSError as e:
            moved.append({"from": f["path"], "error": str(e)[:120]})
    return moved


def load_audit_db_ghn() -> list[dict]:
    db = REPORTS / "telegram_inbox_secrets_audit.db"
    if not db.is_file():
        return []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    out: list[dict] = []
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if "findings" not in tables:
            return []
        rows = con.execute(
            """
            SELECT * FROM findings
            WHERE UPPER(COALESCE(platform,'')) LIKE '%GHN%'
               OR LOWER(COALESCE(path,'')) LIKE '%ghn%'
               OR UPPER(COALESCE(key,'')) LIKE '%GHN%'
               OR LOWER(COALESCE(value_masked,'')) LIKE '%ghn%'
            LIMIT 500
            """
        ).fetchall()
        out = [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        out = [{"error": str(e)[:160]}]
    finally:
        con.close()
    return out


def build_report(*, lookback: int = 200, run_secrets_audit: bool = False) -> dict[str, Any]:
    env = load_env()
    bot = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    pull: dict[str, Any] = {"ok": False, "error": "missing TELEGRAM_BOT_TOKEN"}
    messages: list[dict] = []
    if bot:
        pull, messages = pull_ghn_messages(bot, lookback=lookback)

    if run_secrets_audit:
        try:
            from telegram_inbox_secrets_audit import build_report as audit_build, write_outputs

            write_outputs(audit_build())
        except Exception as e:  # noqa: BLE001
            pull["secrets_audit_error"] = str(e)[:160]

    file_hits, name_hits = scan_inbox_files()
    moved = quarantine_dump_files(file_hits)
    audit_ghn = load_audit_db_ghn()

    report: dict[str, Any] = {
        "ok": True,
        "module": "telegram_ghn_audit",
        "checked_at": utc_now(),
        "query": "Rà soát hộp thoại Telegram — mọi liên quan GHN",
        "pull": pull,
        "quarantined_dumps": moved,
        "messages_ghn_n": len(messages),
        "messages_ghn": messages[:100],
        "inbox_files_ghn_n": len(file_hits),
        "inbox_files_ghn": file_hits[:100],
        "inbox_name_hits": name_hits[:50],
        "audit_db_ghn_n": len(audit_ghn) if audit_ghn and "error" not in (audit_ghn[0] or {}) else 0,
        "audit_db_ghn_sample": [
            {
                k: r.get(k)
                for k in (
                    "path",
                    "platform",
                    "kind",
                    "key",
                    "value_masked",
                    "source",
                )
                if k in r
            }
            for r in (audit_ghn[:40] if isinstance(audit_ghn, list) else [])
            if "error" not in r
        ],
        "summary": {
            "chat_messages_with_ghn": len(messages),
            "files_with_ghn_content": len(file_hits),
            "files_named_ghn": len(name_hits),
            "dump_like_files": sum(
                1 for f in file_hits if f.get("looks_dump") or f.get("in_dumps")
            ),
            "quarantined": len(moved),
            "printA5_urls_seen": sum(
                1
                for f in file_hits
                for u in f.get("urls_masked") or []
                if "printa5" in u.lower()
            )
            + sum(
                1
                for m in messages
                for u in m.get("urls_masked") or []
                if "printa5" in u.lower()
            ),
            "usable_api_token_candidates": 0,
        },
        "policy": {
            "owned_only": True,
            "no_dump_login": True,
            "mask_only": True,
            "dump_files_blocked": True,
        },
        "next_actions": [
            "python3 scripts/telegram_ghn_audit.py",
            "printf '%s\\n' '<printA5 owned>' > secrets/ghn_session.raw && python3 scripts/ghn_cookie_ingest.py ensure",
            "python3 scripts/token_session_maintain.py once",
        ],
    }
    s = report["summary"]
    if messages or file_hits or name_hits:
        report["verdict"] = (
            f"GHN trong Telegram: msgs={s['chat_messages_with_ghn']} · "
            f"files={s['files_with_ghn_content']} · named={s['files_named_ghn']} · "
            f"dump_like={s['dump_like_files']} · quarantined={s['quarantined']} · "
            f"printA5≈{s['printA5_urls_seen']} · "
            f"API token usable từ dump=0 (blocked)"
        )
    else:
        report["verdict"] = (
            "Không thấy nội dung GHN trong getUpdates gần đây / inbox quét được "
            "(bot chỉ thấy tin gửi cho bot; lịch sử cũ có thể đã hết buffer)"
        )
    return report


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "📦 TELEGRAM · RÀ SOÁT GHN",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
    ]
    pull = report.get("pull") or {}
    lines.append(
        f"Pull: ok={pull.get('ok')} updates={pull.get('n')} "
        f"start={pull.get('start_offset')} err={pull.get('error')}"
    )
    moved = report.get("quarantined_dumps") or []
    if moved:
        lines.append(f"Quarantine dumps: {len(moved)}")
        for m in moved[:10]:
            lines.append(f"  → {m}")
    lines.append("")
    lines.append("=== Tin nhắn chat có GHN ===")
    msgs = report.get("messages_ghn") or []
    if not msgs:
        lines.append("(không có trong buffer getUpdates)")
    for m in msgs[:40]:
        lines.append(
            f"· msg={m.get('message_id')} chat={m.get('chat_id')} "
            f"from={m.get('from')} doc={m.get('document_name')}"
        )
        lines.append(f"  preview: {redact_preview(str(m.get('text_preview') or ''))}")
        if m.get("urls_masked"):
            lines.append(f"  urls: {m['urls_masked'][:3]}")
        if m.get("uuids_masked"):
            lines.append(f"  uuids: {m['uuids_masked'][:5]}")
        if m.get("looks_dump"):
            lines.append("  ⚠ looks_dump — không login")
    lines.append("")
    lines.append("=== File inbox có GHN ===")
    files = report.get("inbox_files_ghn") or []
    if not files:
        lines.append("(không có)")
    for f in files[:40]:
        flag = "BLOCK_DUMP" if f.get("blocked_for_login") else "review"
        lines.append(
            f"· [{flag}] {f.get('path')} size={f.get('size')} dumps={f.get('in_dumps')} "
            f"urls={f.get('urls_n')} uuids={f.get('uuids_n')}"
        )
        for row in (f.get("ghn_lines") or [])[:3]:
            lines.append(f"  L{row.get('n')}: {row.get('preview')}")
        if f.get("urls_masked"):
            lines.append(f"  urls: {f['urls_masked'][:2]}")
    sample = report.get("audit_db_ghn_sample") or []
    if sample:
        lines.append("")
        lines.append(f"=== Secrets audit DB (GHN) n≈{report.get('audit_db_ghn_n')} ===")
        for r in sample[:20]:
            lines.append(
                f"· {r.get('path')} [{r.get('platform')}/{r.get('kind')}] "
                f"{r.get('key')}={r.get('value_masked')}"
            )
    lines.append("")
    lines.append("Policy: owned-only · mask · no dump-login · dump → _skipped_dumps")
    lines.append(
        "Next: printA5 / GHN_API_TOKEN owned còn hạn → secrets/ghn_session.raw + ensure"
    )
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    OUT_TXT.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(OUT_JSON), "txt": str(OUT_TXT)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rà soát Telegram hộp thoại — GHN")
    ap.add_argument("--lookback", type=int, default=300, help="update_id lookback")
    ap.add_argument(
        "--secrets-audit",
        action="store_true",
        help="Chạy lại telegram_inbox_secrets_audit trước khi lọc GHN",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(lookback=args.lookback, run_secrets_audit=args.secrets_audit)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
